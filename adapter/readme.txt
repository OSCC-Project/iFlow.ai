# IC-Agent-OS Adapter 模块

Adapter 模块是 IC-Agent-OS 的核心组件，将不同 EDA 工具（Yosys、iEDA、ngspice、PrimeTime）封装为统一接口，让上层模块用同一个 `adapter.run()` 调用所有工具。

## 目录结构

```
ic_agent_os/
├── state.py                # State 模块：SnapshotPackage 入库 + 查询
├── demo.py                 # 完整演示（Adapter → State）
├── test.py                 # 自动化测试
├── run_all.py              # 批量调用所有后端
│
└── adapter/
    ├── adapter.py          # 调度层：选后端 → 执行 → 解析 → 返回
    ├── runner.py           # 抽象层：Backend 基类 + 注册表 + 工厂
    ├── analog_runner.py    # 实现层：ngspice 仿真器
    ├── digital_runner.py   # 实现层：Yosys 综合 + iSTA/OpenSTA
    ├── ieda_runner.py      # 实现层：iEDA 物理设计全流程
    ├── commercial_runner.py# 实现层：商业 EDA 工具（PrimeTime）
    ├── contract.py         # 契约层：SnapshotPackage / SimError / DigitalTwin
    ├── MetricDefine.py     # 加载并校验 metric_define.yaml
    ├── MetricParser.py     # 按规则从原始数据提取指标
    ├── ErrorDiagnosis.py   # 诊断仿真失败原因
    ├── snapshot_builder.py # 构建完整 SnapshotPackage
    ├── mcp_server.py       # 将 Adapter 封装为 MCP Server
    ├── optimizer.py        # Optuna 优化示例（仅演示）
    ├── config.yaml         # 工具路径、超时、并行数配置
    ├── metric_define.yaml  # 指标提取规则配置
    └── requirements.txt    # Python 依赖
```

## 安装

```bash
cd ic_agent_os
pip install pyyaml pydantic mcp jinja2
```

## 快速开始

```python
from adapter.adapter import Adapter
from adapter.contract import SimError

adapter = Adapter("adapter/config.yaml", "adapter/metric_define.yaml")

# Yosys 综合
result = adapter.run("digital", "GCD", {
    "TOP_MODULE": "gcd",
    "VERILOG_SRC": "/path/to/gcd.v",
    "CLK_PERIOD": 2.0,
})

if isinstance(result, SimError):
    print(f"失败: {result.type} - {result.likely_cause}")
else:
    # result 是 SnapshotPackage dataclass
    metrics = result.digital_twin.metrics
    print(f"WNS: {metrics.get('sta', {}).get('wns')}")
    print(f"面积: {metrics.get('sta', {}).get('area')}")
```

## 四个后端

| design_type | EDA 工具 | 说明 |
|-------------|----------|------|
| `"digital"` | Yosys + iSTA/OpenSTA | RTL 综合 + STA 时序分析 |
| `"ieda"` | iEDA 全流程 | floorplan → placement → CTS → routing（需安装 iEDA）|
| `"analog"` | ngspice | 模拟电路仿真（需安装 ngspice >= 42）|
| `"primetime"` | PrimeTime | 商业 STA 工具（需 license）|

## 模块状态

| 模块 | 状态 |
|------|------|
| adapter.py | ✅ 完整 |
| analog_runner.py | ✅ 完整 |
| digital_runner.py | ✅ 完整 |
| ieda_runner.py | ✅ 完整 |
| commercial_runner.py | ✅ 完整 |
| contract.py | ✅ 完整 |
| MetricDefine.py | ✅ 完整 |
| MetricParser.py | ✅ 完整 |
| ErrorDiagnosis.py | ✅ 完整 |
| snapshot_builder.py | ✅ 完整 |
| mcp_server.py | ✅ 完整 |

## 启动 MCP Server

```bash
cd /home/xu/ic_agent_os
python -m adapter.mcp_server
```

LLM 可通过 MCP 协议调用 `run_adapter` 工具。

## 依赖要求

- Python >= 3.10
- pyyaml, pydantic, mcp, jinja2（pip install）
- Yosys（综合，推荐 >= 0.27，已在工具链中可用）
- ngspice >= 42（模拟仿真，可选）
- iEDA（数字物理设计，可选）
- PrimeTime（商业 STA，需 license，可选）

## 许可证

本项目采用 MIT 许可证。
