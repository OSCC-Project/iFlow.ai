# composer/goals.py —— PPA 目标定义 (对齐 SiliconCompiler 声明式设计)
"""
用户声明 PPA 目标，不再声明步骤。

用法:
  from composer.goals import PPASpec
  goal = PPASpec(
      timing={"wns": ">0", "tns": ">0", "fmax": ">200"},
      area={"utilization": "<65%"},
      power={"total": "<5mW"},
  )

对应文档 2.1 节: 声明目标，不声明步骤
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class MetricBound:
    """单个指标的约束: name + operator + value"""
    name: str
    operator: str   # ">" | "<" | ">=" | "<=" | "==" | "min" | "max"
    value: float
    unit: str = ""


@dataclass
class TimingGoal:
    """时序目标 (Performance)"""
    wns: Optional[MetricBound] = None       # WNS > 0
    tns: Optional[MetricBound] = None       # TNS > 0
    hold_wns: Optional[MetricBound] = None  # hold WNS > 0
    hold_tns: Optional[MetricBound] = None  # hold TNS > 0
    fmax: Optional[MetricBound] = None      # fmax > 500MHz
    drv_violations: Optional[MetricBound] = None  # max_cap/max_slew = 0


@dataclass
class AreaGoal:
    """面积目标"""
    cell_area: Optional[MetricBound] = None    # 最小化
    utilization: Optional[MetricBound] = None  # <65%
    die_area: Optional[MetricBound] = None     # max bounding box


@dataclass
class PowerGoal:
    """功耗目标"""
    total: Optional[MetricBound] = None       # <5mW
    leakage: Optional[MetricBound] = None     # <1mW
    dynamic: Optional[MetricBound] = None     # <4mW


@dataclass
class RoutingGoal:
    """布线质量目标"""
    congestion_max: Optional[MetricBound] = None  # <80%
    drc_violations: Optional[MetricBound] = None  # =0
    wirelength_total: Optional[MetricBound] = None # min


@dataclass
class PPASpec:
    """PPA 目标规格 — 用户只声明要什么，不声明怎么做。

    用法:
      goal = PPASpec.parse({"timing": {"wns": ">0"}, "area": {"utilization": "<60%"}})
      goal = PPASpec.from_keywords(["低功耗", "面积优化"], frequency=200)
    """
    timing: TimingGoal = field(default_factory=TimingGoal)
    area: AreaGoal = field(default_factory=AreaGoal)
    power: PowerGoal = field(default_factory=PowerGoal)
    routing: RoutingGoal = field(default_factory=RoutingGoal)
    priority: List[str] = field(default_factory=lambda: ["timing", "area", "power"])
    # 词典序: 先满足第一个，再第二个...

    @classmethod
    def parse(cls, raw: dict) -> "PPASpec":
        """从字典解析 PPA 目标。"""
        spec = cls()
        for dim_name in ("timing", "area", "power", "routing"):
            if dim_name in raw:
                dim_raw = raw[dim_name]
                dim_obj = getattr(spec, dim_name)
                for metric_name, constraint in dim_raw.items():
                    if isinstance(constraint, str):
                        bound = cls._parse_constraint(metric_name, constraint)
                    elif isinstance(constraint, (int, float)):
                        bound = MetricBound(metric_name, "min" if constraint == 0 else "<",
                                           float(constraint))
                    else:
                        bound = constraint
                    if hasattr(dim_obj, metric_name):
                        setattr(dim_obj, metric_name, bound)
        if "priority" in raw:
            spec.priority = raw["priority"]
        return spec

    @classmethod
    def from_keywords(cls, keywords: List[str], frequency: float = None,
                      area_max: float = None, power_max: float = None) -> "PPASpec":
        """从需求关键词 + 数值构建 PPA 目标。"""
        spec = cls()

        # 频率
        if frequency:
            spec.timing.fmax = MetricBound("fmax", ">", frequency, "MHz")

        # 面积
        if area_max:
            spec.area.cell_area = MetricBound("cell_area", "<", area_max, "um^2")
        if any("面积" in k for k in keywords):
            spec.area.utilization = MetricBound("utilization", "<", 65, "%")

        # 功耗
        if power_max:
            spec.power.total = MetricBound("total_power", "<", power_max, "mW")
        if any("低功耗" in k for k in keywords):
            spec.power.total = MetricBound("total_power", "<", 5, "mW")

        # 签核 → 时序是最硬约束
        if any("签核" in k or "tape" in k.lower() for k in keywords):
            spec.timing.wns = MetricBound("wns", ">", 0)
            spec.timing.tns = MetricBound("tns", ">", 0)
            spec.routing.drc_violations = MetricBound("drc_violations", "==", 0)
            spec.priority = ["timing", "routing", "area", "power"]

        return spec

    @staticmethod
    def _parse_constraint(name: str, constraint: str) -> MetricBound:
        """解析 ">0", "<65%", ">500MHz" 等约束字符串。"""
        import re
        m = re.match(r'(>=?|<=?|==)?\s*([\d.]+)\s*(%|MHz|mW|ns|ps|um\^?2)?', constraint)
        if m:
            op = m.group(1) or ">"
            val = float(m.group(2))
            unit = m.group(3) or ""
            if unit == "%":
                val = val / 100 if op in (">", ">=") else val
            return MetricBound(name, op, val, unit)
        return MetricBound(name, ">", 0)

    def check(self, metrics: Dict) -> Dict:
        """检查指标是否满足所有 PPA 约束。

        Returns: {"passed": bool, "violations": [...], "satisfied": [...]}
        """
        violations = []
        satisfied = []

        # Timing
        for attr_name in ("wns", "tns", "hold_wns", "hold_tns", "fmax", "drv_violations"):
            bound = getattr(self.timing, attr_name)
            if bound is None:
                continue
            actual = self._find_metric(metrics, attr_name)
            ok = self._check_bound(actual, bound)
            (satisfied if ok else violations).append({
                "dimension": "timing", "metric": attr_name,
                "actual": actual, "bound": f"{bound.operator}{bound.value}{bound.unit}",
                "passed": ok,
            })

        # Area
        for attr_name in ("cell_area", "utilization", "die_area"):
            bound = getattr(self.area, attr_name)
            if bound is None:
                continue
            actual = self._find_metric(metrics, attr_name)
            ok = self._check_bound(actual, bound)
            (satisfied if ok else violations).append({
                "dimension": "area", "metric": attr_name,
                "actual": actual, "bound": f"{bound.operator}{bound.value}{bound.unit}",
                "passed": ok,
            })

        # Power
        for attr_name in ("total", "leakage", "dynamic"):
            bound = getattr(self.power, attr_name)
            if bound is None:
                continue
            actual = self._find_metric(metrics, f"{attr_name}_power" if attr_name != "total" else "total_power")
            ok = self._check_bound(actual, bound)
            (satisfied if ok else violations).append({
                "dimension": "power", "metric": attr_name,
                "actual": actual, "bound": f"{bound.operator}{bound.value}{bound.unit}",
                "passed": ok,
            })

        return {
            "passed": len(violations) == 0,
            "violations": violations,
            "satisfied": satisfied,
        }

    def _find_metric(self, metrics: Dict, name: str) -> Optional[float]:
        """从 metrics 字典中查找指标值。"""
        for src, vals in metrics.items():
            if isinstance(vals, dict):
                for k, v in vals.items():
                    if k.lower() == name.lower():
                        return v if v == v else None  # NaN check
        return None

    def _check_bound(self, actual: Optional[float], bound: MetricBound) -> bool:
        if actual is None or (isinstance(actual, float) and actual != actual):
            return False  # NaN → 不满足
        op, val = bound.operator, bound.value
        if op == ">": return actual > val
        if op == ">=": return actual >= val
        if op == "<": return actual < val
        if op == "<=": return actual <= val
        if op == "==": return abs(actual - val) < 1e-9
        if op == "min": return True  # optimization target
        if op == "max": return True
        return True
