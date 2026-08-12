# optimizer.py —— 优化器示例
# 使用 Optuna 通过 Adapter 统一接口驱动 EDA 工具进行设计空间探索。
#
# 用法：
#   cd /home/xu/ic_agent_os
#   python -m adapter.optimizer
#
# 注意：此文件属于 Adapter 模块的"消费者"示例，不参与 Adapter 核心逻辑。
import os
import sys

# 确保项目根目录在 sys.path 中
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from adapter.adapter import Adapter
from adapter.contract import SnapshotPackage, SimError


def optimize_analog_circuit():
    """模拟电路优化示例：用 Optuna 调优 TwoStageAmp 的晶体管尺寸。

    需要 ngspice 可用。
    """
    try:
        import optuna
    except ImportError:
        print("optuna 未安装，跳过优化示例。安装: pip install optuna")
        return

    config_path = os.path.join(os.path.dirname(__file__), "config.yaml")
    metric_path = os.path.join(os.path.dirname(__file__), "metric_define.yaml")

    adapter = Adapter(config_path, metric_path)

    def objective(trial):
        # 设计变量：晶体管宽度
        params = {
            "M1_W": trial.suggest_float("M1_W", 1.0, 100.0),
            "M1_L": trial.suggest_float("M1_L", 0.18, 2.0),
            "M2_W": trial.suggest_float("M2_W", 1.0, 100.0),
            "M2_L": trial.suggest_float("M2_L", 0.18, 2.0),
            "supply_voltage": 1.8,
        }

        result = adapter.run("analog", "TwoStageAmp", params, ["ac", "dc"])

        if isinstance(result, SimError):
            # 仿真失败 → 返回惩罚值
            return float("-inf")

        # 从 SnapshotPackage 中取指标
        metrics = result.digital_twin.metrics
        ac = metrics.get("ac", {})
        dc = metrics.get("dc", {})

        gain_db = ac.get("gain_db", float("-inf"))
        pm_deg = ac.get("pm_deg", 0)
        power_mw = dc.get("power_mw", float("inf"))

        # 多目标聚合：增益高 + 相位裕度好 + 功耗低
        if pm_deg < 45:  # 稳定性约束
            return float("-inf")

        score = gain_db / power_mw
        return score

    print("开始模拟电路优化...")
    study = optuna.create_study(direction="maximize")
    study.optimize(objective, n_trials=50)

    print(f"最佳参数: {study.best_params}")
    print(f"最佳得分 (gain/power): {study.best_value:.2f}")


def optimize_digital_circuit():
    """数字电路优化示例：用 Optuna 调节时钟周期和利用率。

    需要 Yosys 和 iEDA 可用。
    """
    try:
        import optuna
    except ImportError:
        print("optuna 未安装，跳过优化示例。安装: pip install optuna")
        return

    config_path = os.path.join(os.path.dirname(__file__), "config.yaml")
    metric_path = os.path.join(os.path.dirname(__file__), "metric_define.yaml")

    adapter = Adapter(config_path, metric_path)

    def objective(trial):
        params = {
            "CLK_PERIOD": trial.suggest_float("CLK_PERIOD", 0.5, 5.0),
            "UTILIZATION": trial.suggest_float("UTILIZATION", 0.3, 0.9),
            "TOP_MODULE": "gcd",
        }

        result = adapter.run("ieda", "GCD", params)

        if isinstance(result, SimError):
            return float("-inf")

        metrics = result.digital_twin.metrics
        sta = metrics.get("sta", {})

        wns = sta.get("wns", float("-inf"))
        tns = sta.get("tns", float("-inf"))
        area = sta.get("total_area", float("inf"))

        if wns < -0.5:  # 时序违规太严重
            return float("-inf")

        # 同时优化面积和时序
        score = -area - abs(wns) * 100
        return score

    print("开始数字电路优化...")
    study = optuna.create_study(direction="maximize")
    study.optimize(objective, n_trials=30)

    print(f"最佳参数: {study.best_params}")
    print(f"最佳得分: {study.best_value:.2f}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="IC-Agent-OS Optimizer")
    parser.add_argument(
        "--mode", choices=["analog", "digital"], default="analog",
        help="优化模式 (default: analog)"
    )
    args = parser.parse_args()

    if args.mode == "analog":
        optimize_analog_circuit()
    else:
        optimize_digital_circuit()
