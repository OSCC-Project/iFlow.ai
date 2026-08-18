"""
对比实验执行器 — 笛卡尔积展开 → 批量执行 → 结果汇总
"""
import time, uuid, json, os, itertools
from typing import Optional

class ExperimentRunner:
    """批量实验调度"""

    def __init__(self, api_flow_run, storage_dir: str = "./server/experiments"):
        self.run_flow = api_flow_run  # 回调: compose+run
        self.storage_dir = storage_dir
        os.makedirs(storage_dir, exist_ok=True)
        self._experiments: dict[str, dict] = {}

    def create(self, design: str, variables: dict) -> dict:
        """创建实验 — 展开笛卡尔积"""
        exp_id = str(uuid.uuid4())[:8]
        keys = list(variables.keys())
        values = [v.split(",") for v in variables.values()]

        combos = []
        for combo in itertools.product(*values):
            config = dict(zip(keys, [c.strip() for c in combo]))
            combos.append({"id": str(uuid.uuid4())[:6], "config": config, "status": "pending"})

        exp = {
            "id": exp_id, "design": design, "variables": variables,
            "combos": combos, "total": len(combos), "completed": 0,
            "status": "created", "created_at": time.time(), "results": [],
        }
        self._experiments[exp_id] = exp
        return exp

    def run_one(self, exp_id: str, combo_id: str) -> dict:
        """执行一个组合 (同步)"""
        exp = self._experiments.get(exp_id)
        if not exp: return {"error": "experiment not found"}

        combo = next((c for c in exp["combos"] if c["id"] == combo_id), None)
        if not combo: return {"error": "combo not found"}

        combo["status"] = "running"
        try:
            # _run_id 注入组合 ID: WS 进度按组合推送 (前端 /ws/exp_{exp_id}_{combo_id})
            run_config = dict(combo["config"])
            run_config["_run_id"] = f"exp_{exp_id}_{combo_id}"
            result = self.run_flow(exp["design"], run_config)
            combo["status"] = "done"
            combo["result"] = result
            exp["completed"] += 1
            exp["results"].append({"combo_id": combo_id, "config": combo["config"], "result": result})
            return combo
        except Exception as e:
            combo["status"] = "failed"
            combo["error"] = str(e)
            return combo

    def run_all(self, exp_id: str) -> dict:
        """顺序执行所有组合 (Phase 1: 简单实现)"""
        exp = self._experiments.get(exp_id)
        if not exp: return {"error": "not found"}

        exp["status"] = "running"
        for combo in exp["combos"]:
            self.run_one(exp_id, combo["id"])

        exp["status"] = "done"
        exp["finished_at"] = time.time()

        # 生成汇总表
        summary = self._build_summary(exp)
        summary_path = os.path.join(self.storage_dir, f"{exp_id}.json")
        with open(summary_path, "w") as f:
            json.dump({"experiment": exp, "summary": summary}, f, indent=2, default=str)

        return {"experiment": exp, "summary": summary}

    def _build_summary(self, exp: dict) -> dict:
        """构建对比总表"""
        rows = []
        for r in exp.get("results", []):
            row = {"combo": r["config"]}
            if "result" in r:
                metrics = self._extract_metrics(r["result"])
                row.update(metrics)
            rows.append(row)
        return {"rows": rows, "columns": list(rows[0].keys()) if rows else []}

    def _extract_metrics(self, result: dict) -> dict:
        """从 flow run 结果中提取关键指标"""
        metrics = {}
        # 工具列: Sheet 3 工具替换维度的口径标注 (sky130=ieda, nangate45/asap7=openroad)
        metrics["tool"] = result.get("tool", "")
        for step in result.get("results", []):
            m = step.get("metrics", {})
            if m.get("wns") is not None: metrics["wns_ns"] = m["wns"]
            if m.get("area") is not None: metrics["area_mm2"] = m["area"]
            if m.get("power") is not None: metrics["power_mw"] = m["power"]
            if step["step"] == "verible_lint":
                metrics["lint_violations"] = step.get("violations", 0)
            if step["step"] == "idrc_drc":
                # None = DRC 未运行 (如实展示, 不填 0)
                metrics["drc_violations"] = step.get("metrics", {}).get("drc")
            metrics[f'{step["step"]}_duration'] = step.get("duration", 0)
        return metrics

    def get(self, exp_id: str) -> Optional[dict]:
        return self._experiments.get(exp_id)

    def list_all(self) -> list:
        return list(self._experiments.values())


# 全局单例
experiment_runner = ExperimentRunner(api_flow_run=None)  # 注入时机在 api.py 中
