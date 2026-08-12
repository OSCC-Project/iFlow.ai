# adapter 主类，实现接口逻辑：选后端 → 调工具 → 解析指标 → 返回结果
#   ┌─────────────────────────────────────────────────────────┐
#   │                    adapter.py（调度层）                  │
#   │  1. 获取规则   → MetricDefine                            │
#   │  2. 提取指标   → MetricParser                            │
#   │  3. 诊断错误   → ErrorDiagnosis                          │
#   │  4. 构建快照   → SnapshotBuilder                         │
#   └─────────────────────────────────────────────────────────┘
import os
from typing import Union, Optional

import yaml

from .runner import (
    BackendRegistry,
    create_backend,
    BackendExecutionError,
    BackendNotFoundError,
)
from .analog_runner import AnalogRunner
from .digital_runner import DigitalRunner
from .commercial_runner import PrimeTimeRunner
from .ieda_runner import IEDARunner
from .openroad_runner import OpenROADRunner
from .opensta_runner import OpenSTARunner
from .gds_runner import GDSRunner
from .contract import StructuredMetrics, SimError, SnapshotPackage
from .MetricDefine import MetricDefine
from .MetricParser import MetricParser
from .ErrorDiagnosis import ErrorDiagnosis
from .snapshot_builder import SnapshotBuilder


def load_yaml(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ============================================================
# 注册所有后端
# ============================================================
BackendRegistry.register("primetime", PrimeTimeRunner)
BackendRegistry.register("analog", AnalogRunner)
BackendRegistry.register("digital", DigitalRunner)
BackendRegistry.register("ieda", IEDARunner)
BackendRegistry.register("openroad", OpenROADRunner)
BackendRegistry.register("opensta", OpenSTARunner)
BackendRegistry.register("gds", GDSRunner)


class Adapter:
    """统一 EDA 工具调度器

    根据 design_type 选择后端、执行仿真、提取指标、构建结构化快照。
    """

    def __init__(self, config_path: str, metric_define_path: str):
        self.config = load_yaml(config_path)
        self.metric_define = MetricDefine(metric_define_path)

        # 初始化时创建所有后端实例（缓存）
        self.backends: dict = {}
        for name, cfg in self.config.get("backend", {}).items():
            self.backends[name] = create_backend(name, cfg)

    def run(
        self,
        design_type: str,
        circuit_name: str,
        params: dict,
        analyses: Optional[list] = None,
        parent_snapshot_id: str = "",
        observation_level: str = "1",
        snapshot_type: str = "CHECKPOINT",
    ) -> Union[SnapshotPackage, SimError]:
        """执行 EDA 工具调用，返回结构化快照或错误信息。

        Args:
            design_type: 后端类型 ("analog" | "digital" | "primetime" | "ieda")
            circuit_name: 电路名称（如 "TwoStageAmp", "GCD"）
            params: 参数字典
            analyses: 分析类型列表
            parent_snapshot_id: 父快照 ID
            observation_level: artifact | metric | object | execution
            snapshot_type: FULL | CHECKPOINT | INCREMENTAL | RECOVERY | PREDICTION

        Returns:
            - 成功: SnapshotPackage
            - 失败: SimError
        """
        # 1. 根据 design_type 从缓存里取对应的后端
        backend = self.backends.get(design_type)
        if not backend:
            return SimError(
                type="backend_error",
                likely_cause=f"未知设计类型: {design_type}",
                raw_log="",
            )

        try:
            # 2. 执行后端
            raw = backend.execute(circuit_name, params, analyses)

            # 2a. 执行结果分类 (反馈 Issue 1)
            rc = raw.get("returncode", 0)
            synth_ok = raw.get("synth_success", None)  # None=后端无此字段
            sta_ok = raw.get("sta_success", None)
            # FAILED: 综合明确失败 → 不提交
            if synth_ok is False:
                return SimError(type="execution_fail",
                    likely_cause="综合失败, 未生成有效网表",
                    raw_log=raw.get("stderr", "")[:500])
            # FAILED: 综合未标记但 returncode 非零且无网表
            netlist = raw.get("netlist_path", "")
            if rc != 0 and (not netlist or not os.path.exists(netlist)):
                return SimError(type="execution_fail",
                    likely_cause=f"工具执行失败 (rc={rc}), 无网表产出",
                    raw_log=raw.get("stderr", "")[:500])

            # 3. 获取指标提取规则
            rules = self.metric_define.get_circuit_metrics(circuit_name)
            if not rules:
                return SimError(
                    type="metric_error",
                    likely_cause=f"未找到电路 {circuit_name} 的指标规则",
                    raw_log="",
                )

            # 4. 提取指标
            parser = MetricParser(rules, raw)
            metrics = parser.extract()

            # 5. 构建结构化快照
            snapshot = SnapshotBuilder().build(
                raw=raw,
                metrics=metrics,
                design_type=design_type,
                circuit_name=circuit_name,
                parent_snapshot_id=parent_snapshot_id,
                observation_level=observation_level,
                snapshot_type=snapshot_type,
            )
            return snapshot

        except BackendExecutionError as e:
            # 失败时诊断错误
            diagnosis = ErrorDiagnosis(
                raw_log=str(e), context={"design_type": design_type,
                                         "circuit_name": circuit_name}
            )
            return diagnosis.diagnose()

        except BackendNotFoundError as e:
            return SimError(
                type="backend_not_found",
                likely_cause=str(e),
                raw_log="",
            )

    def run_and_submit(self, receiver, design_type: str, circuit_name: str,
                       params: dict, analyses: list = None,
                       **kwargs) -> Optional[str]:
        """执行 EDA 调用并自动提交到 State。

        Args:
            receiver: SnapshotReceiver 实例
            其余参数同 Adapter.run()

        Returns:
            成功: run_id (str)；失败: None
        """
        result = self.run(design_type, circuit_name, params, analyses, **kwargs)
        return receiver.receive(result)

    def register_backend(self, name: str, backend_class):
        """动态注册新后端（用于插件场景）"""
        BackendRegistry.register(name, backend_class)
        cfg = self.config.get("backend", {}).get(name, {})
        self.backends[name] = create_backend(name, cfg)
