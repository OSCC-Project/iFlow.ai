#!/usr/bin/env python3
"""
param_bridge.py —— 用户目标 → 工具参数桥接
══════════════════════════════════════════════════════════
用户说 "frequency=200MHz" → 自动变成 Yosys 的 CLK_PERIOD=5.0ns
用户说 "area_max=100000" → 自动变成 OpenROAD 的 CORE_AREA

用法:
  from param_bridge import goal_to_params
  params = goal_to_params(goals={"frequency":200}, stage="synthesis")
  # → {"CLK_PERIOD": 5.0}
══════════════════════════════════════════════════════════"""
from typing import Dict


# ═══════════════════════════════════════════════════════════
# 核心映射表: goal 字段 → (stage → tool_param_name, conversion_fn)
# ═══════════════════════════════════════════════════════════
GOAL_TO_PARAM = {
    # ────── 频率 ──────
    "frequency": [
        # synthesis: clockspeed → CLK_PERIOD (单位: ns)
        ("synthesis", "CLK_PERIOD", lambda freq: round(1000.0 / freq, 1)),
        # STA
        ("STA", "CLK_PERIOD", lambda freq: round(1000.0 / freq, 1)),
        # 物理实现也需要 CLK_PERIOD (供 OpenROAD Tcl 生成时钟约束)
        ("floorplan", "CLK_PERIOD", lambda freq: round(1000.0 / freq, 1)),
    ],
    # ────── 面积 ──────
    "area_max": [
        # floorplan: 面积上限 → CORE_AREA (粗略估算: sqrt(area)*0.8)
        ("floorplan", "CORE_AREA",
         lambda a: f"10 10 {int((a**0.5)*0.9)} {int((a**0.5)*0.9)}"),
        # placement: 面积约束 → density (面积越小 density 越低)
        ("placement", "density", lambda a: round(min(0.7, a / 150000), 2)),
    ],
    "area_min": [
        ("placement", "density", lambda _: 0.85),  # 最小化面积 → 高 density
    ],
    # ────── 功耗 ──────
    "power_max": [
        # synthesis: 功耗约束 → 降低时钟频率
        ("synthesis", "power_opt", lambda p: True),
        ("placement", "power_opt", lambda p: True),
    ],
    # ────── 时序 ──────
    "wns": [
        ("STA", "wns_target", lambda w: w),
    ],
    "tns": [
        ("STA", "tns_target", lambda t: t),
    ],
    # ────── 利用率 ──────
    "utilization": [
        ("placement", "density", lambda u: round(u / 100.0, 2)),
    ],
    # ────── 拥塞 ──────
    "congestion_max": [
        ("placement", "max_density", lambda c: round(1.0 - c / 300.0, 2)),
    ],
}


def goal_to_params(goals: Dict, stage: str) -> Dict:
    """将用户 goals 转换为某个阶段的工具参数。

    Args:
        goals: {"frequency": 200, "area_max": 100000, "power_max": 5}
        stage: "synthesis" / "floorplan" / "placement" / "STA"

    Returns:
        {"CLK_PERIOD": 5.0, "density": 0.67, ...}

    示例:
        >>> goal_to_params({"frequency":200}, "synthesis")
        {"CLK_PERIOD": 5.0}
    """
    params = {}
    for goal_key, goal_val in goals.items():
        mappings = GOAL_TO_PARAM.get(goal_key, [])
        for target_stage, param_name, convert_fn in mappings:
            if target_stage == stage:
                try:
                    params[param_name] = convert_fn(goal_val)
                except (TypeError, ValueError, ZeroDivisionError):
                    pass
    return params


def goal_to_all_params(goals: Dict) -> Dict[str, Dict]:
    """将 goals 转换为所有阶段的参数字典。

    Returns:
        {"synthesis": {"CLK_PERIOD": 5.0}, "floorplan": {...}, ...}
    """
    all_params = {}
    stages = ["synthesis", "floorplan", "placement", "CTS", "routing", "STA", "DRC"]
    for stage in stages:
        p = goal_to_params(goals, stage)
        if p:
            all_params[stage] = p
    return all_params


# ═══════════════════════════════════════════════════════════
# CLI demo
# ═══════════════════════════════════════════════════════════
if __name__ == "__main__":
    import json
    test_goals = {"frequency": 500, "area_max": 80000, "power_max": 3}
    print(f"用户目标: {json.dumps(test_goals)}\n")
    print("各阶段参数:")
    for stage, params in goal_to_all_params(test_goals).items():
        print(f"  {stage:12s} → {params}")
