"""
Agent Decision 规则引擎 — 实现 Excel Sheet 3 的四维决策逻辑
截断(Truncation) + 跳过(Skip) + 强度(Intensity) + 工具替换(Substitution)
"""
from dataclasses import dataclass, field
from typing import Optional

# ============================================================
# 决策维度 1: 截断点 (场景 → 阶段终点)
# ============================================================
TRUNCATION = {
    "experience":  ["verible_lint", "verilator_lint", "icarus_sim"],
    "course":      ["verible_lint", "verilator_lint", "icarus_sim"],
    "competition": ["verible_lint", "verilator_lint", "yosys_synth", "ista_sta"],
    "research":    ["verible_lint", "verilator_lint", "sby_check",
                    "yosys_synth", "ieda_floorplan", "ieda_place", "ieda_cts",
                    "ieda_route", "ista_sta", "idrc_drc", "gds_export"],
    "tapeout":     ["verible_lint", "verilator_lint", "sby_check",
                    "yosys_synth", "ieda_floorplan", "ieda_place", "ieda_cts",
                    "ieda_route", "ista_sta", "idrc_drc", "netgen_lvs", "gds_export"],
}

# ============================================================
# 决策维度 2: 跳过规则 (设计特征 → 不适用步骤)
# ============================================================
SKIP_RULES = [
    {"condition": lambda d: d.get("clock_domains", 1) < 2, "skip": ["cdc_check"]},
    {"condition": lambda d: d.get("reset_domains", 1) < 2, "skip": ["rdc_check"]},
    {"condition": lambda d: not d.get("has_upf"),         "skip": ["upf_check", "low_power_check"]},
    {"condition": lambda d: d.get("is_comb", False),       "skip": ["ieda_cts", "opt_hold"]},
    {"condition": lambda d: not d.get("has_dft_config"),   "skip": ["dft_insert", "atpg"]},
    {"condition": lambda d: d.get("gates", 99999) < 5000, "skip": ["ir_drop"]},
]

# ============================================================
# 决策维度 3: 强度参数 (场景 → 执行深度)
# ============================================================
INTENSITY = {
    "experience":  {"sta_corners": 0,  "coverage": 0,    "drc_depth": "none",       "routing_iters": 1, "synth_strategy": "default"},
    "course":      {"sta_corners": 0,  "coverage": 0.6,  "drc_depth": "none",       "routing_iters": 1, "synth_strategy": "default"},
    "competition": {"sta_corners": 2,  "coverage": 0,    "drc_depth": "full",       "routing_iters": 1, "synth_strategy": "area_then_speed"},
    "research":    {"sta_corners": 1,  "coverage": 0,    "drc_depth": "full",       "routing_iters": 3, "synth_strategy": "default"},
    "tapeout":     {"sta_corners": 3,  "coverage": 0.95, "drc_depth": "full+dfm",   "routing_iters": 10,"synth_strategy": "multi_strategy"},
}

# ============================================================
# 决策维度 4: 工具替换 (可用资源 → 工具选择)
# ============================================================
TOOL_MATRIX = {
    "sim":       {"open": "icarus",      "commercial": "vcs"},
    "synth":     {"open": "yosys",       "commercial": "design_compiler"},
    "sta":       {"open": "ista",        "commercial": "primetime"},
    "physical":  {"open": "ieda",        "commercial": "icc2"},
    "drc":       {"open": "idrc",        "commercial": "calibre"},
    "lvs":       {"open": "netgen",      "commercial": "calibre"},
}


@dataclass
class DesignProfile:
    """从 RTL 代码提取的设计特征"""
    clock_domains: int = 1
    reset_domains: int = 1
    has_upf: bool = False
    has_dft_config: bool = False
    is_comb: bool = False
    gates: int = 10000
    top_module: str = "top"

    @classmethod
    def from_code(cls, code: str) -> "DesignProfile":
        """从 Verilog 代码中提取设计特征"""
        profile = cls()
        # 数时钟域
        clocks = set()
        for line in code.split("\n"):
            if "posedge" in line or "negedge" in line:
                import re
                found = re.findall(r"(?:posedge|negedge)\s+(\w+)", line)
                clocks.update(found)
        profile.clock_domains = max(len(clocks), 1)
        # 检查 UPF
        profile.has_upf = "upf" in code.lower() or "power" in code.lower()
        # 是否纯组合
        profile.is_comb = "posedge" not in code and "negedge" not in code
        # 估算门数 (粗糙: 按行数估算)
        profile.gates = max(len([l for l in code.split("\n") if l.strip() and not l.strip().startswith("//")]) * 10, 1)
        # 提取 top module
        m = re.search(r"module\s+(\w+)", code) if 're' in dir() else None
        if not m:
            import re
            m = re.search(r"module\s+(\w+)", code)
        if m:
            profile.top_module = m.group(1)
        return profile


def decide_flow(scene: str, design: DesignProfile,
                available_tools: str = "open") -> dict:
    """
    Agent 决策主入口: 输入场景+设计特征 → 输出裁剪后的 Flow
    """
    # Step 1: 截断
    full_steps = TRUNCATION.get(scene, TRUNCATION["competition"])

    # Step 2: 跳过
    to_skip = set()
    for rule in SKIP_RULES:
        if rule["condition"](design.__dict__ if isinstance(design, DesignProfile) else design):
            to_skip.update(rule["skip"])
    steps = [s for s in full_steps if s not in to_skip]

    # Step 3: 强度
    intensity = INTENSITY.get(scene, INTENSITY["competition"])

    # Step 4: 工具
    tools = {k: v.get(available_tools, v["open"])
             for k, v in TOOL_MATRIX.items()}

    return {
        "steps": steps,
        "skipped": list(to_skip),
        "intensity": intensity,
        "tools": tools,
        "scene": scene,
        "design": {
            "clock_domains": design.clock_domains,
            "gates": design.gates,
            "top_module": design.top_module,
        },
    }


# ============================================================
# Exception Handling (Sheet 4 卡住信号表)
# ============================================================
STUCK_SIGNALS = [
    # (stage, signal_condition, severity, action, backtrack, max_retries, escalation)
    ("yosys_synth",  lambda m: m.get("wns", 0) < -2, "BLOCKING",
     "WNS 差距过大, 检查 SDC/约束/综合配置", "rtl_or_sdc", 2,
     "降频或换更快的库"),
    ("ieda_place",   lambda m: m.get("hpwl_ratio", 1) > 1.5, "WARNING",
     "HPWL 偏大, 放宽 die 面积", "floorplan", 3,
     "接受面积膨胀"),
    ("ieda_route",   lambda m: m.get("drc", 0) > 1000, "BLOCKING",
     "DRC 过多, 分析类型并针对性修复", "routing_params", 3,
     "退到 placement, 降低 density"),
    ("ieda_route",   lambda m: m.get("drc_trend", "down") == "flat", "BLOCKING",
     "DRC 连续不降, 退到 placement", "placement", 2,
     "退到 floorplan, 增大 die"),
    ("ista_sta",     lambda m: m.get("wns", 0) < 0, "BLOCKING",
     "时序违例, 分析关键路径", "synth_strategy", 2,
     "降频到可收敛值"),
]

STOP_LOSS = [
    ("drc_5_rounds_no_improve", "DRC 连续 5 轮无改善 → 检查 LEF/规则映射, 或换 PDK"),
    ("wns_3_rounds_worse",       "WNS 连续 3 轮恶化 → 降频 20% 或换更快的库"),
    ("area_50pct_over",          "面积超过预算 50% → 降低利用率或增大 die"),
    ("time_5x_expected",         "单轮耗时超过预期 5 倍 → 检查约束/工具是否卡死"),
]
