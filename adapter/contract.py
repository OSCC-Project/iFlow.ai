# contract.py —— Adapter ↔ State Tool 统一契约 v1.0
# 对齐: Unified Contract v1.0 (2026-07)
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional, Union

# ============================================================
# 1. 类型别名
# ============================================================
StructuredMetrics = Dict[str, Dict[str, float]]


@dataclass
class SimError:
    """仿真/分析错误信息"""
    type: str
    likely_cause: str
    raw_log: str = ""


# ============================================================
# 2. SnapshotPackage 组件
# ============================================================

# ── 2a. SnapshotHeader ──
@dataclass
class SnapshotHeader:
    """快照元信息头 (双方必填)"""
    snapshot_id: str = ""
    run_id: str = ""
    parent_snapshot_id: str = ""
    timestamp: str = ""
    tool: str = ""                  # iEDA, OpenROAD, Yosys, ngspice, Innovus, ...
    tool_version: str = ""
    adapter_version: str = ""
    design_name: str = ""           # v1.0 新增: 设计名
    design_type: str = ""           # v1.0 新增: digital / analog / mixed_signal / rf
    stage: str = ""
    step: int = 0
    schema_version: str = "1.0"     # v1.0 新增: 契约版本
    snapshot_type: str = "CHECKPOINT"
    observation_level: str = "1"    # "0"=artifact | "1"=metric | "2"=object | "3"=execution


# ── 2b. CapabilityDecl ──
@dataclass
class Capability:
    """Adapter 能力声明 (每个 SnapshotPackage 自带)"""
    adapter: str = ""
    artifact: bool = True
    metric: bool = True
    object_delta: bool = False      # v1.0: 原 object, 改名对齐
    execution_trace: bool = False   # v1.0: 原 execution, 改名对齐
    waveform: bool = False          # v1.0 新增: ngspice 特有
    extras: Dict[str, bool] = field(default_factory=dict)


# ── 2c. TracePoint (合并原 ExecutionTraceEntry) ──
@dataclass
class TracePoint:                   # v1.0: 合并统一
    operation: str = ""
    iteration: int = 0
    command: str = ""               # v1.0 新增
    parameters: Dict[str, Any] = field(default_factory=dict)   # v1.0 新增
    duration_ms: float = 0.0        # v1.0 新增
    trigger: str = ""               # v1.0 新增
    metrics_snapshot: Dict[str, float] = field(default_factory=dict)  # v1.0: 原 metrics
    checkpoint: str = ""
    timestamp: str = ""


# ── 2d. ObservationContext ──
@dataclass
class ObservationContext:
    """执行环境 (observation_level="2" 以上必填)"""
    stage: str = ""                 # v1.0 新增
    operation: str = ""
    command: str = ""
    parameters: Dict[str, Any] = field(default_factory=dict)
    duration_ms: float = 0.0
    trigger: str = "scheduled_checkpoint"
    work_dir: str = ""
    trace: List[TracePoint] = field(default_factory=list)           # v1.0 新增
    metrics_snapshot: Dict[str, float] = field(default_factory=dict)  # v1.0 新增


# ── 2e. DigitalTwin ──
@dataclass
class DesignInfo:                   # v1.0 新增: 结构化设计信息
    name: str = ""
    technology: str = ""
    top: str = ""


@dataclass
class DesignObject:
    """统一设计对象 Schema (泛型, 通过 type + properties 区分)"""
    id: str
    type: str                       # cell / module / device / net / pin / instance
    master: str = ""                # v1.0 新增: 标准单元类型 (仅 cell)
    properties: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DigitalTwin:
    """当前设计状态"""
    design: DesignInfo = field(default_factory=DesignInfo)    # v1.0 新增
    metadata: Dict[str, str] = field(default_factory=dict)
    objects: List[DesignObject] = field(default_factory=list)
    metrics: Dict[str, Dict[str, float]] = field(default_factory=dict)
    constraints: Dict[str, Any] = field(default_factory=dict)
    extensions: Dict[str, Any] = field(default_factory=dict)


# ── 2f. ArtifactEntry ──
@dataclass
class ArtifactInfo:                 # alias: ArtifactEntry
    artifact_id: str = ""
    type: str = "file"
    logical_name: str = ""
    source_uri: str = ""
    size: int = 0
    checksum: str = ""
    producer: str = ""
    stage: str = ""
    depends_on: List[str] = field(default_factory=list)
    metadata: Dict[str, str] = field(default_factory=dict)


# ── 2g. SnapshotPackage ──
@dataclass
class SnapshotPackage:
    """EDA 快照 —— Adapter 核心输出"""
    header: SnapshotHeader
    capability: Capability = field(default_factory=Capability)
    observation_context: ObservationContext = field(default_factory=ObservationContext)
    digital_twin: DigitalTwin = field(default_factory=DigitalTwin)
    artifact_manifest: List[ArtifactInfo] = field(default_factory=list)
    execution_trace: List[TracePoint] = field(default_factory=list)
    optimizer_hints: Optional[Dict] = None    # v1.0 新增


# ============================================================
# 3. 旧的 ExecutionTraceEntry → 改为 TracePoint alias
# ============================================================
ExecutionTraceEntry = TracePoint  # backward compat


# ============================================================
# 4. 调用接口签名 (文档用途)
# ============================================================
def run(
    design_type: str,
    circuit_name: str,
    params: dict,
    analyses: Optional[List[str]] = None,
) -> Union[SnapshotPackage, SimError]:
    """统一 EDA 工具调用接口。"""
    pass


# ============================================================
# 5. 观测级别常量
# ============================================================
OBS_LEVEL_ARTIFACT = "0"
OBS_LEVEL_METRIC = "1"
OBS_LEVEL_OBJECT = "2"
OBS_LEVEL_EXECUTION = "3"
