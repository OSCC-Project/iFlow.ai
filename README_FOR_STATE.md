# Adapter → State 接口说明书

## 一句话

Adapter 调用 EDA 工具，返回 `SnapshotPackage`，调用 `receiver.submit_snapshot(pkg)` 或 `adapter.run_and_submit(receiver, ...)` 入库。结束。

## 快速开始

```python
from adapter.adapter import Adapter
from adapter.contract import SimError
from state import SnapshotReceiver

adapter = Adapter("adapter/config.yaml", "adapter/metric_define.yaml")
receiver = SnapshotReceiver()

# 方式一：一行搞定（推荐）
run_id = adapter.run_and_submit(
    receiver,
    design_type="digital",
    circuit_name="GCD",
    params={"TOP_MODULE": "gcd", "CLK_PERIOD": 1.5}
)

# 方式二：分步控制
result = adapter.run("digital", "GCD", {
    "TOP_MODULE": "gcd",
    "VERILOG_SRC": "/path/to/gcd.v",
    "CLK_PERIOD": 1.5,
})

if isinstance(result, SimError):
    print(f"失败: [{result.type}] {result.likely_cause}")
else:
    receiver.submit_snapshot(result)  # 一行入库
```

## 四个后端

| design_type | EDA 工具 | 实际可用？ | 说明 |
|-------------|----------|-----------|------|
| `"digital"` | Yosys + iSTA | ✅ | RTL to netlist + STA |
| `"ieda"` | iEDA 全流程 | ⚠️ 需安装 iEDA | floorplan → placement → CTS → routing |
| `"analog"` | ngspice | ⚠️ 需安装 ngspice | 模拟电路仿真 (spicelib) |
| `"primetime"` | PrimeTime | ⚠️ 需 license | 商业 STA 工具 |

## SnapshotPackage 结构

```python
SnapshotPackage
├── header: SnapshotHeader
│   ├── snapshot_id: str        # 唯一快照 ID (snap_xxxxxxxxxxxx)
│   ├── run_id: str             # 本次运行 ID (UUID)
│   ├── parent_snapshot_id: str # 父快照 ID，溯源链
│   ├── tool: str               # "digital" | "ieda" | "analog" | "primetime"
│   ├── stage: str              # 流程阶段
│   ├── snapshot_type: str      # FULL | CHECKPOINT | INCREMENTAL | RECOVERY | PREDICTION
│   ├── observation_level: str  # artifact | metric | object | execution
│   └── timestamp: str          # ISO 时间戳
│
├── capability: Capability     # Adapter 能力声明
│   ├── artifact: bool          # 是否提供产物文件
│   ├── metric: bool            # 是否提供指标
│   ├── object: bool            # 是否提供设计对象 (cell/net 级)
│   ├── execution: bool         # 是否提供执行轨迹
│   └── extras: dict            # EDA 特有能力
│
├── observation_context: ObservationContext  # 执行环境
│   ├── operation: str          # 当前操作
│   ├── command: str            # 完整命令
│   ├── parameters: dict        # 命令参数
│   ├── trigger: str            # 触发条件
│   └── duration_ms: float      # 耗时
│
├── digital_twin: DigitalTwin   # 当前设计状态
│   ├── metadata: dict          # {design, technology, flow}
│   ├── objects: [DesignObject] # 统一 Schema 设计对象
│   ├── metrics: {source: {name: value}}  # 指标
│   ├── constraints: dict       # 设计约束
│   └── extensions: dict        # EDA 特有扩展
│
├── artifact_manifest: [ArtifactInfo]  # 产物清单
│   └── 每个: artifact_id, logical_name, source_uri,
│            size, checksum, producer, stage, depends_on
│
└── execution_trace: [ExecutionTraceEntry]  # 工具内部轨迹 (可选)
    └── 每个: operation, iteration, metrics, checkpoint
```

## 不同 Adapter 的能力差异

State Tool 通过 `capability` 字段判断快照可信度：

| Adapter | artifact | metric | object | execution |
|---------|----------|--------|--------|-----------|
| Yosys (digital) | ✅ | ✅ | ❌ | ❌ |
| iEDA | ✅ | ✅ | ✅ | ❌ |
| ngspice (analog) | ✅ | ✅ | ❌ | ❌ + waveform |
| PrimeTime (商业) | ✅ | ✅ | ❌ | ❌ |

> 示例：OpenROAD 可以给 object=true, execution=true；Innovus 只能给 metric=true。
> State Tool 读 capability 就知道当前快照的信息丰富程度，不需要猜。

## State 侧 API

```python
from state import StateStore, SnapshotReceiver

store = StateStore()                         # 初始化（自动建 SQLite）
receiver = SnapshotReceiver(store)

# 提交
receiver.submit_snapshot(pkg)                # 直接提交 SnapshotPackage
receiver.receive(result, label="exp1")       # 自动判断成功/失败

# 查询
store.list_all(limit=50)                     # 列出所有 run
store.latest("GCD")                          # GCD 最新快照
store.get("snap_xxxx")                       # 按 ID 查
store.stats()                                # 统计信息
```

## SQLite 表结构

```
state.db
├── runs              (snapshot_id, run_id, tool, observation_level, snapshot_type, ...)
├── capabilities      (adapter, artifact, metric, object, execution, extras)
├── observation_contexts (operation, command, parameters, trigger, duration_ms)
├── designs           (circuit_name, technology, flow)
├── metrics           (source, metric_name, value)
├── constraints       (key, value)
├── artifacts         (artifact_id, logical_name, source_uri, size, checksum, depends_on)
└── execution_traces  (operation, iteration, metrics, checkpoint)
```

## 验证

```bash
cd /home/xu/ic_agent_os
python3 test.py        # 自动化测试
python3 demo.py        # 完整演示（初始化 → Yosys → SnapshotPackage → State 入库）
python3 run_all.py     # 调所有可用工具
python3 state.py list  # 查看已入库的快照
python3 state.py stats # 统计
```
