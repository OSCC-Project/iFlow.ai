# 指标提取器：按 MetricDefine 提供的规则，从原始仿真数据中提取指标数值
# 输入：规则字典 + 原始数据（如 ngspice 日志）
# 输出：StructuredMetrics（{"gain_db": 72.3, ...}）
# 被 adapter.py 调用
from .contract import StructuredMetrics
from typing import Dict, Any
import re
import math


class MetricParser:
    """
    按规则从原始仿真数据中提取指标数值。

    输入：
        rules: 从 MetricDefine 获取的规则字典
               {"gain": {"source": "ac", "expression": "max(v(out)/v(in))"}, ...}
        raw_data: 后端返回的原始仿真数据
                 模拟侧:ngspice 的 .raw 或 .log 数据
                 数字侧:STA 报告的指标字典

    输出：
        {"gain_db": 72.3, "gbw_hz": 15.2e6, "power_mw": 0.85, ...}
    """

    def __init__(self, rules: Dict[str, Dict[str, Any]], raw_data: Dict[str, Any]):
        """
        初始化指标提取器

        Args:
            rules: 指标提取规则字典
            raw_data: 后端返回的原始仿真数据
        """
        self.rules = rules
        self.raw_data = raw_data

    def extract(self) -> Dict[str, Dict[str, float]]:
        """
        执行指标提取

        Returns:
            按 source 分组的指标数值字典:
            {"ac": {"gain_db": 72.3, "gbw_hz": 15.2e6},
             "dc": {"power_mw": 0.85}}
        """
        from collections import defaultdict
        metrics: Dict[str, Dict[str, float]] = defaultdict(dict)

        for metric_name, rule in self.rules.items():
            source = rule["source"]
            expression = rule["expression"]

            # 根据 source 从 raw_data 中取对应的数据集
            data = self.raw_data.get(source, {})
            if not data:
                # 如果原始数据中没有该 source，返回 NaN
                metrics[source][metric_name] = float("nan")
                continue

            # 计算 expression
            try:
                value = self._compute_expression(expression, data)
                metrics[source][metric_name] = value
            except Exception:
                # 提取失败时返回 NaN
                metrics[source][metric_name] = float("nan")

        return dict(metrics)

    def _compute_expression(self, expression: str, data: Dict[str, Any]) -> float:
        """
        计算表达式值。

        支持的表达式格式：
            - 直接取值: "wns" -> data["wns"]
            - 简单运算: "max(v(out)/v(in))" -> 取最大值
            - 单位转换: "20 * log10(gain)" -> 计算 dB

        注：这是一个简化实现。实际场景中可能需要更复杂的表达式解析器
        （如使用 Python 的 eval 配合安全上下文，或使用 sympy）。
        """
        # 先尝试直接取值（用于数字 STA 的简单指标）
        if expression in data:
            return float(data[expression])

        # 尝试简单的数值解析（模拟侧常用公式）
        # 示例：解析 "max(v(out)/v(in))"
        # 实际实现需要根据 raw_data 的具体结构来解析
        # 这里给出一个占位实现

        # 如果是"max(...)"格式
        max_match = re.match(r"max\((.+)\)", expression)
        if max_match:
            inner = max_match.group(1)
            # 从 data 中获取数组，取最大值
            # 简化假设：data 中有 inner 对应的数组
            # 实际需要根据 ngspice 的 .raw 或 .log 结构实现
            values = data.get(inner, [])
            if values:
                return max(values)

        # 简单的乘法运算（如 "20 * log10(...)"）
        # 实际实现需要更完整的表达式解析
        if "log10" in expression:
            # 提取括号内的表达式
            match = re.search(r"log10\(([^)]+)\)", expression)
            if match:
                inner = match.group(1)
                # 简化：从 data 中取值
                value = data.get(inner, 0)
                if "20 *" in expression:
                    return 20 * math.log10(float(value))
                return math.log10(float(value))

        # 默认：尝试作为数值解析
        try:
            return float(expression)
        except ValueError:
            # 如果都失败，抛异常或返回 NaN
            raise ValueError(f"无法解析表达式: {expression}")