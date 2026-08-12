# tool_registry.py —— EDA 工具能力注册中心
"""
每个 EDA 工具在此注册其:
  - 能执行哪些阶段 (synthesis / floorplan / placement / CTS / routing / STA / simulation / ...)
  - 输入/输出产物格式
  - 观测能力等级 (artifact / metric / object / execution)
  - QoS 属性 (速度 / 质量 / 开源与否)
  - 与其他工具的兼容性
  - 推荐场景 & 限制条件

这是 Flow Composer 做工具选择和替换的"知识库"。
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class ArtifactSpec:
    """产物规格"""
    name: str                # 逻辑名 (netlist / def / sdc / lef / lib / gds / report)
    format: str              # 文件格式 (verilog / def / lef / liberty / gds2 / json / tcl)
    required: bool = True    # 是否必须
    description: str = ""


@dataclass
class StageCapability:
    """工具在某个阶段的能力声明"""
    stage: str                                    # synthesis / floorplan / placement / CTS / routing / STA / DRC / simulation / ...
    inputs: List[ArtifactSpec] = field(default_factory=list)
    outputs: List[ArtifactSpec] = field(default_factory=list)
    parameters: Dict[str, str] = field(default_factory=dict)  # 关键参数 & 描述
    quality: str = "medium"                       # low / medium / high
    speed: str = "medium"                         # fast / medium / slow
    notes: str = ""


@dataclass
class ToolInfo:
    """EDA 工具完整信息"""
    name: str                                     # 工具名 (Yosys / iEDA / OpenROAD / ngspice / PrimeTime / ...)
    adapter: str                                  # 对应 adapter 的 design_type
    description: str
    is_open_source: bool = True
    license_required: bool = False

    # 观测能力
    observation: Dict[str, bool] = field(default_factory=lambda: {
        "artifact": True, "metric": True, "object": False, "execution": False,
    })

    # 该工具能执行的所有阶段
    stages: List[StageCapability] = field(default_factory=list)

    # 兼容性
    compatible_upstream: List[str] = field(default_factory=list)    # 能接收哪些上游工具输出
    compatible_downstream: List[str] = field(default_factory=list)  # 输出可被哪些下游工具消费

    # 推荐
    recommended_for: List[str] = field(default_factory=list)        # 推荐使用场景
    limitations: List[str] = field(default_factory=list)            # 已知限制
    extras: Dict[str, str] = field(default_factory=dict)            # 扩展属性


# ╔══════════════════════════════════════════════════════════════╗
# ║  工具注册表                                                  ║
# ╚══════════════════════════════════════════════════════════════╝

TOOL_REGISTRY: Dict[str, ToolInfo] = {}


def register(tool: ToolInfo):
    """注册一个工具"""
    TOOL_REGISTRY[tool.name] = tool


def get_tool(name: str) -> Optional[ToolInfo]:
    return TOOL_REGISTRY.get(name)


def get_tools_for_stage(stage: str) -> List[ToolInfo]:
    """获取能执行某个阶段的所有工具"""
    return [t for t in TOOL_REGISTRY.values()
            if any(s.stage == stage for s in t.stages)]


def get_all_tools() -> Dict[str, ToolInfo]:
    return dict(TOOL_REGISTRY)


# ╔══════════════════════════════════════════════════════════════╗
# ║  注册所有已知 EDA 工具                                       ║
# ╚══════════════════════════════════════════════════════════════╝

def _init_registry():
    """初始化工具注册表 —— 这是系统的'EDA 知识库'"""

    # ─── Yosys ───
    register(ToolInfo(
        name="Yosys",
        adapter="digital",
        description="开源 RTL 综合工具，将 Verilog RTL 转换为门级网表",
        is_open_source=True,
        observation={"artifact": True, "metric": True, "object": False, "execution": False},
        stages=[
            StageCapability(
                stage="synthesis",
                inputs=[
                    ArtifactSpec("rtl", "verilog", True, "RTL 源码 (.v)"),
                    ArtifactSpec("liberty", "liberty", False, "标准单元库 (.lib)"),
                ],
                outputs=[
                    ArtifactSpec("netlist", "verilog", True, "门级网表 (.v)"),
                    ArtifactSpec("synth_report", "text", False, "综合报告"),
                ],
                parameters={"TOP_MODULE": "顶层模块名", "CLK_PERIOD": "时钟周期(ns)",
                           "VERILOG_SRC": "RTL 路径", "LIBERTY_PATH": "工艺库路径(可选)"},
                quality="medium", speed="fast",
                notes="无 liberty 文件时无法做工艺映射(abc)，但仍输出 techmap 后的网表",
            ),
        ],
        compatible_downstream=["iEDA", "OpenSTA", "OpenROAD", "PrimeTime"],
        recommended_for=["原型验证", "教学", "快速迭代", "开源流程"],
        limitations=["无 liberty 时不做 abc 工艺映射", "不直接输出 DEF"],
    ))

    # ─── iEDA ───
    register(ToolInfo(
        name="iEDA",
        adapter="ieda",
        description="开源数字 IC 物理设计全流程平台 (floorplan → GDS)",
        is_open_source=True,
        observation={"artifact": True, "metric": True, "object": True, "execution": False},
        stages=[
            StageCapability(stage="floorplan", quality="medium", speed="medium",
                inputs=[ArtifactSpec("netlist", "verilog"), ArtifactSpec("lef", "lef"),
                        ArtifactSpec("lib", "liberty"), ArtifactSpec("sdc", "sdc")],
                outputs=[ArtifactSpec("def", "def")]),
            StageCapability(stage="tapcell", quality="medium", speed="fast",
                inputs=[ArtifactSpec("def", "def")], outputs=[ArtifactSpec("def", "def")]),
            StageCapability(stage="pdn", quality="medium", speed="medium",
                inputs=[ArtifactSpec("def", "def")], outputs=[ArtifactSpec("def", "def")]),
            StageCapability(stage="gplace", quality="medium", speed="fast",
                inputs=[ArtifactSpec("def", "def")], outputs=[ArtifactSpec("def", "def")]),
            StageCapability(stage="resize", quality="medium", speed="medium",
                inputs=[ArtifactSpec("def", "def")], outputs=[ArtifactSpec("def", "def")],
                notes="含 pre-CTS STA"),
            StageCapability(stage="dplace", quality="medium", speed="fast",
                inputs=[ArtifactSpec("def", "def")], outputs=[ArtifactSpec("def", "def")]),
            StageCapability(stage="cts", quality="medium", speed="medium",
                inputs=[ArtifactSpec("def", "def")], outputs=[ArtifactSpec("def", "def")],
                notes="含 post-CTS STA"),
            StageCapability(stage="groute", quality="medium", speed="slow",
                inputs=[ArtifactSpec("def", "def")], outputs=[ArtifactSpec("def", "def")]),
            StageCapability(stage="droute", quality="medium", speed="slow",
                inputs=[ArtifactSpec("def", "def")], outputs=[ArtifactSpec("def", "def")],
                notes="含 post-route STA (signoff)"),
            StageCapability(stage="filler", quality="medium", speed="fast",
                inputs=[ArtifactSpec("def", "def")], outputs=[ArtifactSpec("def", "def")]),
            StageCapability(stage="gds", quality="medium", speed="medium",
                inputs=[ArtifactSpec("def", "def")], outputs=[ArtifactSpec("gds", "gds2")]),
            StageCapability(stage="DRC", quality="medium", speed="slow",
                inputs=[ArtifactSpec("gds", "gds2")], outputs=[ArtifactSpec("drc_report", "text")]),
        ],
        compatible_upstream=["Yosys", "Design Compiler"],
        compatible_downstream=["OpenSTA", "Magic", "KLayout"],
        recommended_for=["开源全流程", "sky130/ASAP7 工艺", "学术研究"],
        limitations=["仅支持 sky130 和 ASAP7 PDK", "部分高级优化不如商业工具"],
    ))

    # ─── OpenROAD ───
    register(ToolInfo(
        name="OpenROAD",
        adapter="openroad",
        description="开源数字 IC 物理设计平台，支持从 RTL 到 GDS 的全流程",
        is_open_source=True,
        observation={"artifact": True, "metric": True, "object": True, "execution": True},
        stages=[
            StageCapability(stage="floorplan", quality="high", speed="fast",
                inputs=[ArtifactSpec("netlist", "verilog"), ArtifactSpec("lef", "lef"),
                        ArtifactSpec("lib", "liberty"), ArtifactSpec("sdc", "sdc")],
                outputs=[ArtifactSpec("def", "def"), ArtifactSpec("odb", "odb")]),
            StageCapability(stage="tapcell", quality="high", speed="fast",
                inputs=[ArtifactSpec("def", "def")], outputs=[ArtifactSpec("def", "def")]),
            StageCapability(stage="pdn", quality="high", speed="medium",
                inputs=[ArtifactSpec("def", "def")], outputs=[ArtifactSpec("def", "def")]),
            StageCapability(stage="gplace", quality="high", speed="fast",
                inputs=[ArtifactSpec("def", "def")], outputs=[ArtifactSpec("def", "def")],
                notes="支持逐迭代 HPWL (execution trace)"),
            StageCapability(stage="resize", quality="high", speed="medium",
                inputs=[ArtifactSpec("def", "def")], outputs=[ArtifactSpec("def", "def")],
                notes="门级缩放 + 首次 STA (pre-CTS)"),
            StageCapability(stage="dplace", quality="high", speed="fast",
                inputs=[ArtifactSpec("def", "def")], outputs=[ArtifactSpec("def", "def")]),
            StageCapability(stage="cts", quality="high", speed="medium",
                inputs=[ArtifactSpec("def", "def")], outputs=[ArtifactSpec("def", "def")],
                notes="时钟树综合 + 第二次 STA (post-CTS)"),
            StageCapability(stage="groute", quality="high", speed="slow",
                inputs=[ArtifactSpec("def", "def")], outputs=[ArtifactSpec("def", "def")]),
            StageCapability(stage="droute", quality="high", speed="slow",
                inputs=[ArtifactSpec("def", "def")], outputs=[ArtifactSpec("def", "def")],
                notes="详细布线 + 第三次 STA (signoff post-route)"),
            StageCapability(stage="filler", quality="high", speed="fast",
                inputs=[ArtifactSpec("def", "def")], outputs=[ArtifactSpec("def", "def")]),
            StageCapability(stage="gds", quality="high", speed="medium",
                inputs=[ArtifactSpec("def", "def")], outputs=[ArtifactSpec("gds", "gds2")]),
            StageCapability(stage="DRC", quality="high", speed="slow",
                inputs=[ArtifactSpec("gds", "gds2")], outputs=[ArtifactSpec("drc_report", "text")]),
        ],
        compatible_upstream=["Yosys", "Design Compiler"],
        compatible_downstream=["KLayout", "Magic"],
        recommended_for=["高质量开源流程", "需要迭代过程可见", "研究级优化"],
        limitations=["社区版本功能持续演进中", "某些 corner case 不如商业工具稳定"],
    ))

    # ─── OpenSTA ───
    register(ToolInfo(
        name="OpenSTA",
        adapter="opensta",
        description="开源静态时序分析引擎",
        is_open_source=True,
        observation={"artifact": True, "metric": True, "object": False, "execution": False},
        stages=[
            StageCapability(stage="STA",
                inputs=[ArtifactSpec("netlist", "verilog"), ArtifactSpec("liberty", "liberty"),
                        ArtifactSpec("sdc", "sdc"), ArtifactSpec("spef", "spef", False)],
                outputs=[ArtifactSpec("timing_report", "text")],
                parameters={"CLK_PERIOD": "时钟周期"},
                quality="high", speed="fast",
                notes="兼容 SDC 标准，可被 iEDA/OpenROAD 的 STA 步骤调用或独立运行"),
        ],
        compatible_upstream=["Yosys", "iEDA", "OpenROAD"],
        compatible_downstream=["任何报告解析器"],
        recommended_for=["时序签核", "与 iEDA 搭配的独立 STA 验证", "ICCAD 评估对齐"],
        limitations=["不包含物理优化", "需要完整的 liberty + sdc + spef"],
    ))

    # ─── PrimeTime ───
    register(ToolInfo(
        name="PrimeTime",
        adapter="primetime",
        description="Synopsys 黄金标准静态时序分析工具 (商业)",
        is_open_source=False, license_required=True,
        observation={"artifact": True, "metric": True, "object": False, "execution": False},
        stages=[
            StageCapability(stage="STA",
                inputs=[ArtifactSpec("netlist", "verilog"), ArtifactSpec("liberty", "liberty"),
                        ArtifactSpec("sdc", "sdc"), ArtifactSpec("spef", "spef")],
                outputs=[ArtifactSpec("timing_report", "text")],
                quality="highest", speed="medium",
                notes="业界黄金标准，支持最完整的时序分析功能"),
        ],
        compatible_upstream=["Design Compiler", "Yosys", "iEDA", "OpenROAD"],
        compatible_downstream=["任何报告解析器"],
        recommended_for=["tape-out 签核", "商业项目最终验证"],
        limitations=["需要商业 license", "闭源", "价格昂贵"],
    ))

    # ─── ngspice ───
    register(ToolInfo(
        name="ngspice",
        adapter="analog",
        description="开源模拟电路仿真器",
        is_open_source=True,
        observation={"artifact": True, "metric": True, "object": False, "execution": False,
                     "extras": {"waveform": True}},
        stages=[
            StageCapability(stage="simulation",
                inputs=[ArtifactSpec("netlist", "spice", True, "SPICE 网表 (.sp/.cir)")],
                outputs=[ArtifactSpec("raw_data", "raw", False, "仿真原始数据"),
                         ArtifactSpec("log", "text", False, "仿真日志")],
                parameters={"temperature": "温度(℃)", "vdd": "电源电压(V)"},
                quality="medium", speed="slow",
                notes="支持 AC/DC/TRAN 多种分析类型"),
        ],
        compatible_downstream=["任何波形查看器"],
        recommended_for=["模拟电路仿真", "学术研究", "教学"],
        limitations=["仿真速度较慢", "大电路收敛困难"],
    ))

    # ─── Design Compiler ───
    register(ToolInfo(
        name="Design Compiler",
        adapter="design_compiler",
        description="Synopsys 商业 RTL 综合工具，业界黄金标准",
        is_open_source=False, license_required=True,
        observation={"artifact": True, "metric": True, "object": False, "execution": False},
        stages=[
            StageCapability(stage="synthesis",
                inputs=[ArtifactSpec("rtl", "verilog"), ArtifactSpec("liberty", "liberty"),
                        ArtifactSpec("sdc", "sdc")],
                outputs=[ArtifactSpec("netlist", "verilog"), ArtifactSpec("sdc", "sdc"),
                         ArtifactSpec("ddc", "ddc", False, "Design Compiler 数据库")],
                quality="highest", speed="medium",
                notes="支持最完整的综合优化，输出可用于 ICC2/Innovus"),
        ],
        compatible_downstream=["ICC2", "Innovus", "iEDA", "OpenROAD", "PrimeTime"],
        recommended_for=["商业项目", "追求极致 PPA", "高性能芯片"],
        limitations=["需要商业 license", "价格昂贵", "学习曲线陡峭"],
    ))

    # ─── Innovus ───
    register(ToolInfo(
        name="Innovus",
        adapter="innovus",
        description="Cadence 商业数字物理实现平台",
        is_open_source=False, license_required=True,
        observation={"artifact": True, "metric": True, "object": False, "execution": False},
        stages=[
            StageCapability(stage="floorplan",
                inputs=[ArtifactSpec("netlist", "verilog"), ArtifactSpec("lef", "lef"),
                        ArtifactSpec("lib", "liberty"), ArtifactSpec("sdc", "sdc")],
                outputs=[ArtifactSpec("def", "def")], quality="highest", speed="medium"),
            StageCapability(stage="placement",
                inputs=[ArtifactSpec("def", "def"), ArtifactSpec("netlist", "verilog")],
                outputs=[ArtifactSpec("def", "def")], quality="highest", speed="fast"),
            StageCapability(stage="CTS",
                inputs=[ArtifactSpec("def", "def")],
                outputs=[ArtifactSpec("def", "def")], quality="highest", speed="medium"),
            StageCapability(stage="routing",
                inputs=[ArtifactSpec("def", "def")],
                outputs=[ArtifactSpec("def", "def"), ArtifactSpec("gds", "gds2")],
                quality="highest", speed="slow"),
        ],
        compatible_upstream=["Design Compiler", "Genus"],
        compatible_downstream=["PrimeTime", "Calibre"],
        recommended_for=["高性能商业芯片", "先进制程", "追求极限 PPA"],
        limitations=["需要商业 license", "价格非常昂贵", "只提供 checkpoint + metrics，无执行轨迹"],
    ))

    # ─── Calibre ───
    register(ToolInfo(
        name="Calibre",
        adapter="calibre",
        description="Mentor/Siemens 黄金标准 DRC/LVS 工具 (商业)",
        is_open_source=False, license_required=True,
        observation={"artifact": True, "metric": True, "object": False, "execution": False},
        stages=[
            StageCapability(stage="DRC",
                inputs=[ArtifactSpec("gds", "gds2"), ArtifactSpec("rule_deck", "calibre")],
                outputs=[ArtifactSpec("drc_report", "text")],
                quality="highest", speed="slow", notes="业界黄金标准"),
        ],
        compatible_upstream=["任何输出 GDS 的工具"],
        recommended_for=["tape-out 签核", "foundry 官方验证"],
        limitations=["需要商业 license 和 foundry rule deck"],
    ))

_init_registry()
