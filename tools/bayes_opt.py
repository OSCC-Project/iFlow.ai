# bayes_opt.py —— 贝叶斯优化集成
"""
使用 Optuna 进行 EDA 设计空间探索，与 Flow Composer 集成。

功能:
  1. 自动选择优化参数 (基于 Tool Registry 的参数 schema)
  2. Optuna Trial → Adapter 执行 → 指标提取 → 打分
  3. 多目标优化 (频率/面积/功耗 trade-off)

用法:
  from bayes_opt import BayesOptimizer
  opt = BayesOptimizer()
  best = opt.optimize(
      flow=composed_flow,
      n_trials=100,
      objectives=["frequency", "area"],
  )

对应会议中应朱齐的贝叶斯优化方向。
"""
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    import optuna
    HAS_OPTUNA = True
except ImportError:
    HAS_OPTUNA = False


class BayesOptimizer:
    """贝叶斯优化器：Optuna + Flow Composer + Adapter。

    工作流程:
      1. 从 Flow 中提取可优化参数
      2. Optuna 采样 → Adapter 执行
      3. 从 SnapshotPackage 提取指标
      4. 多目标打分
      5. 返回 Pareto 前沿
    """

    def __init__(self, study_name: str = "ic_agent_os_optimization"):
        self.study_name = study_name
        self._studies: Dict[str, Any] = {}

    def optimize(
        self,
        flow,
        adapter,
        n_trials: int = 50,
        objectives: List[str] = None,
        param_ranges: Dict[str, Tuple[float, float]] = None,
        constraints: Dict[str, Tuple[str, float]] = None,
        direction: str = "maximize",
    ) -> Dict:
        """运行贝叶斯优化。

        Args:
            flow: ComposedFlow (从 FlowComposer 获取)
            adapter: Adapter 实例
            n_trials: 试验次数
            objectives: 优化目标 ["frequency", "area", "power"]
            param_ranges: 手动指定参数范围 {"CLK_PERIOD": (0.5, 5.0)}
            constraints: 约束 {"wns": (">", -0.5), "area": ("<", 200000)}
            direction: maximize (越大越好) 或 minimize

        Returns:
            {"best_params": {...}, "best_score": ..., "study": optuna.study}
        """
        if not HAS_OPTUNA:
            return {"error": "optuna 未安装。pip install optuna"}

        objectives = objectives or ["frequency", "area"]
        constraints = constraints or {}
        param_ranges = param_ranges or {
            "CLK_PERIOD": (0.5, 10.0),
            "UTILIZATION": (0.3, 0.95),
        }

        # 从 flow 的第一个 synthesis 步骤获取 adapter 类型
        synth_step = flow.get_step("synthesis")
        adapter_name = synth_step.tool_info.adapter if synth_step and synth_step.tool_info else "digital"

        if adapter_name not in adapter.backends:
            # fallback到digital
            adapter_name = "digital"

        study = optuna.create_study(
            study_name=self.study_name,
            direction=direction,
            storage=None,  # 内存模式
        )

        def objective(trial):
            params = {}
            for pname, (lo, hi) in param_ranges.items():
                if pname == "CLK_PERIOD" or pname.endswith("_PERIOD"):
                    params[pname] = trial.suggest_float(pname, lo, hi)
                elif pname == "UTILIZATION" or pname.endswith("DENSITY"):
                    params[pname] = trial.suggest_float(pname, lo, hi)
                elif pname.endswith("_W") or pname.endswith("_L"):
                    params[pname] = trial.suggest_float(pname, lo, hi)
                else:
                    params[pname] = trial.suggest_float(pname, lo, hi)

            params["TOP_MODULE"] = flow.design
            params["DESIGN_TOP"] = flow.design

            # 执行
            from adapter.contract import SimError
            try:
                result = adapter.run(adapter_name, flow.design, params)
            except Exception:
                return float("-inf") if direction == "maximize" else float("inf")

            if isinstance(result, SimError):
                return float("-inf") if direction == "maximize" else float("inf")

            # 提取指标
            dt = result.digital_twin
            metrics = dt.metrics if hasattr(dt, 'metrics') else dt.get("metrics", {})

            # 约束检查
            for cname, (op, cval) in constraints.items():
                actual = self._get_metric(metrics, cname)
                if actual is None:
                    continue
                if op == ">" and actual <= cval:
                    return float("-inf") if direction == "maximize" else float("inf")
                if op == "<" and actual >= cval:
                    return float("-inf") if direction == "maximize" else float("inf")

            # 打分
            score = self._score(metrics, objectives)
            return score

        study.optimize(objective, n_trials=n_trials)

        return {
            "best_params": study.best_params,
            "best_score": study.best_value,
            "n_trials": len(study.trials),
            "direction": direction,
            "objectives": objectives,
            "study": study,
        }

    def _get_metric(self, metrics: Dict, name: str) -> Optional[float]:
        """从 metrics 字典中查找指标值。"""
        for src, vals in metrics.items():
            if isinstance(vals, dict) and name in vals:
                return vals[name]
            elif name == src:
                return vals
        return None

    def _score(self, metrics: Dict, objectives: List[str]) -> float:
        """多目标打分。

        简单加权: frequency 越高越好, area/power 越低越好。
        """
        score = 0.0
        for obj in objectives:
            val = self._get_metric(metrics, obj)
            if val is None or (isinstance(val, float) and val != val):  # NaN
                continue
            if obj in ("frequency", "gain_db", "pm_deg"):
                score += val * (1.0 if obj == "frequency" else 1.0)
            elif obj in ("area", "power", "wns", "tns", "leakage_power"):
                # 越小越好 → 取负
                score -= abs(val) * (10000 if obj == "area" else 1.0)
        return score

    def pareto_frontier(
        self, trials: List, x_metric: str = "area", y_metric: str = "frequency",
    ) -> List[Dict]:
        """提取 Pareto 前沿。

        Args:
            trials: Optuna trials 列表
            x_metric: X 轴指标
            y_metric: Y 轴指标

        Returns:
            Pareto 最优解列表
        """
        frontier = []
        for t in trials:
            if t.value is None or t.value == float("-inf"):
                continue
            frontier.append({
                "params": t.params,
                "score": t.value,
                "trial_id": t.number,
            })

        # 简单的 Pareto 过滤 (针对双目标)
        pareto = []
        for a in frontier:
            dominated = False
            for b in frontier:
                if b["score"] > a["score"]:
                    dominated = True
                    break
            if not dominated:
                pareto.append(a)

        return pareto[:10]  # Top 10
