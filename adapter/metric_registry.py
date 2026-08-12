# metric_registry.py —— 标准指标注册表 (反馈 Issue 2)
"""
所有 EDA 工具输出的指标统一映射到标准名。
Optimizer 只能读取标准指标, 不能依赖各工具私有字段名。

用法:
  from adapter.metric_registry import canonicalize
  standard = canonicalize(raw_metrics, tool="digital")
  # → {"sta.wns_ns": -0.12, "design.area_um2": 123456, ...}
"""
from typing import Dict, Optional


# ═══════════════════════════════════════════════════════════
# 标准指标注册表
# ═══════════════════════════════════════════════════════════

CANONICAL_METRICS = {
    # ── 时序 (Performance) ──
    "sta.wns_ns": {
        "unit": "ns",
        "direction": "maximize",
        "aliases": ["wns", "WNS", "sta.wns", "worst_negative_slack"],
        "description": "最差建立时间负 slack",
    },
    "sta.tns_ns": {
        "unit": "ns",
        "direction": "maximize",
        "aliases": ["tns", "TNS", "sta.tns", "total_negative_slack"],
        "description": "总建立时间负 slack",
    },
    "sta.fmax_mhz": {
        "unit": "MHz",
        "direction": "maximize",
        "aliases": ["fmax", "frequency", "sta.fmax", "max_frequency"],
        "description": "最大工作频率",
    },

    # ── 面积 (Area) ──
    "design.area_um2": {
        "unit": "um^2",
        "direction": "minimize",
        "aliases": ["area", "total_area", "sta.total_area", "cell_area"],
        "description": "标准单元总面积",
    },
    "design.utilization_pct": {
        "unit": "%",
        "direction": "minimize",
        "aliases": ["utilization", "util"],
        "description": "芯片面积利用率",
    },

    # ── 功耗 (Power) ──
    "power.total_mw": {
        "unit": "mW",
        "direction": "minimize",
        "aliases": ["total_power", "power", "power_mw"],
        "description": "总功耗",
    },
    "power.leakage_uw": {
        "unit": "uW",
        "direction": "minimize",
        "aliases": ["leakage", "leakage_power", "sta.leakage"],
        "description": "静态泄漏功耗",
    },

    # ── 模拟 (Analog) ──
    "analog.gain_db": {
        "unit": "dB",
        "direction": "maximize",
        "aliases": ["gain", "gain_db", "ac.gain_db"],
        "description": "低频开环增益",
    },
    "analog.pm_deg": {
        "unit": "deg",
        "direction": "maximize",
        "aliases": ["pm", "pm_deg", "ac.pm_deg"],
        "description": "相位裕度",
    },
    "analog.power_mw": {
        "unit": "mW",
        "direction": "minimize",
        "aliases": ["power", "power_mw", "dc.power_mw"],
        "description": "静态功耗",
    },
}

# 方向常量
MAXIMIZE = "maximize"
MINIMIZE = "minimize"


def canonicalize(metrics: Dict[str, Dict[str, float]], tool: str = "digital") -> Dict[str, float]:
    """将工具私有指标名映射为标准名。

    Args:
        metrics: {"sta": {"wns": -0.12, "tns": -5.3}, ...}
        tool: 工具名 "digital" / "openroad" / "analog" / ...

    Returns:
        {"sta.wns_ns": -0.12, "sta.tns_ns": -5.3, ...}

    例:
        >>> canonicalize({"sta": {"wns": -0.12}})
        {"sta.wns_ns": -0.12}
    """
    result = {}
    for src, vals in metrics.items():
        if isinstance(vals, dict):
            for name, value in vals.items():
                canonical_name = _find_canonical(name)
                if canonical_name:
                    result[canonical_name] = value
        else:
            canonical_name = _find_canonical(src)
            if canonical_name:
                result[canonical_name] = vals
    return result


def _find_canonical(raw_name: str) -> Optional[str]:
    """查找原始指标名对应的标准名。"""
    raw_lower = raw_name.lower()
    for canonical, info in CANONICAL_METRICS.items():
        if raw_name == canonical or raw_lower == canonical.lower():
            return canonical
        for alias in info["aliases"]:
            if raw_name == alias or raw_lower == alias.lower():
                return canonical
    return None


def get_unit(metric_name: str) -> str:
    """获取标准指标的单位。"""
    return CANONICAL_METRICS.get(metric_name, {}).get("unit", "")


def get_direction(metric_name: str) -> str:
    """获取标准指标的优化方向 (maximize/minimize)。"""
    return CANONICAL_METRICS.get(metric_name, {}).get("direction", "maximize")
