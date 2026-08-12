# mcp_server.py —— Adapter MCP Server（S6 合规）
# 通过 FastMCP 将 Adapter + FlowComposer + 全部工具暴露给 LLM
import json
import sys
from dataclasses import asdict
from pathlib import Path

# 确保项目根在 sys.path
_project_root = str(Path(__file__).parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from mcp.server.fastmcp import FastMCP

# ── Adapter ──
from adapter.adapter import Adapter
from adapter.contract import SnapshotPackage, SimError

BASE_DIR = Path(__file__).parent
adapter = Adapter(str(BASE_DIR / "config.yaml"), str(BASE_DIR / "metric_define.yaml"))

# ── Flow Composer ──
from composer.flow_composer import FlowComposer
from composer.nl_interface import NLInterface
from composer.format_bridge import FormatBridge
from tools.sandbox import Sandbox

composer = FlowComposer()
nli = NLInterface()
bridge = FormatBridge()
sandbox = Sandbox()

# ── MCP Server ──
app = FastMCP("ic-agent-os-mcp")


# ═══════════════════════════════════════════════════════════
# 1. Adapter.run() — 执行 EDA 工具
# ═══════════════════════════════════════════════════════════
@app.tool()
async def run_eda_tool(
    design_type: str,
    circuit_name: str,
    params: dict,
    analyses: list = None,
    observation_level: str = "metric",
    snapshot_type: str = "CHECKPOINT",
) -> dict:
    """统一 EDA 工具调用接口。

    Args:
        design_type: digital | ieda | analog | primetime | openroad
        circuit_name: 电路名称
        params: 参数字典
        analyses: 分析类型（模拟侧用）
        observation_level: artifact | metric | object | execution
        snapshot_type: FULL | CHECKPOINT | INCREMENTAL | RECOVERY | PREDICTION
    """
    result = adapter.run(
        design_type, circuit_name, params, analyses,
        observation_level=observation_level, snapshot_type=snapshot_type,
    )
    if isinstance(result, (SnapshotPackage, SimError)):
        return asdict(result)
    return result


# ═══════════════════════════════════════════════════════════
# 2. Flow Composer — S4 渐进演进 + S6 原生集成
# ═══════════════════════════════════════════════════════════
@app.tool()
async def compose_flow(
    design: str,
    technology: str = "sky130",
    requirements: list = None,
    goals: dict = None,
    fast_mode: bool = False,
) -> dict:
    """根据用户需求自动生成 IC 设计 Flow。

    支持自动选择开源/商业工具链，任意步骤可替换。
    """
    flow = composer.compose(
        design=design, technology=technology,
        requirements=requirements or ["开源"],
        goals=goals or {}, fast_mode=fast_mode,
    )
    return {
        "name": flow.name, "description": flow.description,
        "design": flow.design, "technology": flow.technology,
        "steps": [{"stage": s.stage, "primary_tool": s.primary_tool,
                    "alternatives": s.alternatives, "reason": s.reason}
                   for s in flow.steps],
        "warnings": flow.warnings,
        "recommendations": flow.recommendations,
        "summary": flow.summary(),
    }


@app.tool()
async def swap_flow_tool(step: str, new_tool: str) -> dict:
    """替换 Flow 中某步骤的工具。"""
    current = composer.compose("gcd", "sky130", ["开源"])
    swapped = composer.swap_tool(current, step, new_tool)
    if not swapped:
        return {"error": f"无法替换 {step} → {new_tool}"}
    return {
        "name": swapped.name,
        "steps": [{"stage": s.stage, "primary_tool": s.primary_tool}
                   for s in swapped.steps],
        "warnings": swapped.warnings,
    }


@app.tool()
async def list_tool_alternatives(stage: str) -> dict:
    """列出某阶段所有可选工具。"""
    flow = composer.compose("gcd", "sky130", ["开源"])
    return {"stage": stage, "alternatives": composer.list_alternatives(flow, stage)}


# ═══════════════════════════════════════════════════════════
# 3. NL Interface — 自然语言解析
# ═══════════════════════════════════════════════════════════
@app.tool()
async def parse_requirements(text: str) -> dict:
    """中文/英文需求 → 结构化设计参数。"""
    return nli.parse(text)


# ═══════════════════════════════════════════════════════════
# 4. Format Bridge — 格式兼容性检查
# ═══════════════════════════════════════════════════════════
@app.tool()
async def check_format(tool_a: str, tool_b: str, stage_a: str, stage_b: str) -> dict:
    """检查两工具间产物格式兼��性。"""
    from tool_registry import get_tool
    ta, tb = get_tool(tool_a), get_tool(tool_b)
    if not ta or not tb:
        return {"compatible": False, "error": "工具未找到"}
    ok, details = bridge.check_compatibility(ta, tb, stage_a, stage_b)
    return {"compatible": ok, "details": details}


# ═══════════════════════════════════════════════════════════
# 5. Sandbox
# ═══════════════════════════════════════════════════════════
@app.tool()
async def run_sandbox_flow(
    design: str = "gcd", technology: str = "sky130",
    requirements: list = None, fast_mode: bool = True,
) -> dict:
    """沙箱中执行 Flow 并返回每步结果。"""
    flow = composer.compose(design, technology, requirements or ["开源"], fast_mode=fast_mode)
    run = sandbox.run_flow(flow, adapter, {
        "VERILOG_SRC": f"./verilog/{design}.v", "TOP_MODULE": design,
    })
    return {
        "run_id": run.run_id, "total_duration_ms": run.total_duration_ms,
        "steps": [{"stage": s.stage, "tool": s.tool, "success": s.success,
                    "metrics": s.metrics, "error": s.error}
                   for s in run.steps],
    }


# ═══════════════════════════════════════════════════════════
# 6. 启动
# ═══════════════════════════════════════════════════════════
if __name__ == "__main__":
    app.run(transport="stdio")
