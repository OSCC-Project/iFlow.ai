# composer/analyzer.py —— 结构化诊断引擎
"""
读 flow 执行后的 metrics → 生成结构化诊断报告。

对应文档 2.4 节: 结构化中间结果可观测性
对应文档 4 节 L1: 反馈 — 跑完后生成诊断报告

用法:
  from composer.analyzer import FlowAnalyzer
  a = FlowAnalyzer()
  report = a.analyze(snapshots)  # snapshots from State
  print(a.format(report))
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from composer.goals import PPASpec


@dataclass
class DiagnosticItem:
    """单条诊断"""
    severity: str        # "error" | "warning" | "info" | "success"
    category: str        # "timing" | "area" | "power" | "routing" | "general"
    metric_name: str     # "wns", "utilization", ...
    actual_value: Any    # 实际值
    threshold: str       # 阈值描述
    detail: str = ""     # 人类可读详情
    suggestions: List[str] = field(default_factory=list)


@dataclass
class AnalysisReport:
    """完整诊断报告"""
    summary: str                           # 一句话总结
    passed: bool                           # 是否全部满足
    score: float = 0.0                     # 综合评分 0-100
    items: List[DiagnosticItem] = field(default_factory=list)
    stats: Dict = field(default_factory=dict)
    recommendations: List[str] = field(default_factory=list)


class FlowAnalyzer:
    """Flow 执行后诊断分析器。

    纯读数据，不跑工具。输入 State 中的 metrics，输出结构化诊断。
    """

    def analyze(
        self,
        metrics: Dict[str, Dict[str, float]],
        goal: Optional[PPASpec] = None,
        constraints: Optional[Dict] = None,
    ) -> AnalysisReport:
        """分析 metrics 并生成诊断报告。

        Args:
            metrics: {"sta": {"wns": -0.03, "tns": -5}, "area": {"cell_area": 12345}}
            goal: PPA 目标规格 (可选, 用于判断是否达标)
            constraints: 设计约束 {"CLK_PERIOD": 2.0, "UTILIZATION": 0.7}

        Returns:
            AnalysisReport
        """
        items: List[DiagnosticItem] = []
        all_metrics = self._flatten(metrics)

        # ── 1. 时序诊断 ──
        items.extend(self._diagnose_timing(all_metrics))

        # ── 2. 拥塞诊断 ──
        items.extend(self._diagnose_congestion(all_metrics))

        # ── 3. 面积诊断 ──
        items.extend(self._diagnose_area(all_metrics, constraints))

        # ── 4. 功耗诊断 ──
        items.extend(self._diagnose_power(all_metrics))

        # ── 5. 对照 Goal 检查 ──
        if goal:
            check = goal.check(metrics)
            for v in check.get("violations", []):
                items.append(DiagnosticItem(
                    severity="error",
                    category=v["dimension"],
                    metric_name=v["metric"],
                    actual_value=v["actual"],
                    threshold=v["bound"],
                    detail=f"{v['metric']} 不满足目标约束",
                ))

        # ── 汇总 ──
        errors = [i for i in items if i.severity == "error"]
        warnings = [i for i in items if i.severity == "warning"]
        passed = len(errors) == 0
        score = 100 - len(errors) * 20 - len(warnings) * 5
        score = max(0, min(100, score))

        # 建议
        recs = []
        for item in items:
            recs.extend(item.suggestions)
        recs = list(dict.fromkeys(recs))[:10]  # 去重, 限10条

        summary = (
            f"✅ 全部 {len(items)} 项检查通过" if passed
            else f"❌ {len(errors)} 项错误, {len(warnings)} 项警告"
        )

        return AnalysisReport(
            summary=summary, passed=passed, score=score,
            items=items,
            stats={"total_checks": len(items), "errors": len(errors), "warnings": len(warnings)},
            recommendations=recs,
        )

    def _flatten(self, metrics: Dict) -> Dict[str, float]:
        result = {}
        for src, vals in metrics.items():
            if isinstance(vals, dict):
                for k, v in vals.items():
                    result[k] = v
            else:
                result[src] = vals
        return result

    def _diagnose_timing(self, m: Dict[str, float]) -> List[DiagnosticItem]:
        items = []
        wns = m.get("wns")

        if wns is not None and wns == wns and wns < 0:
            items.append(DiagnosticItem(
                severity="error", category="timing", metric_name="wns",
                actual_value=wns, threshold=">0",
                detail=f"最差负时序裕量 WNS={wns:.2f}ns — 时序违规",
                suggestions=[
                    "增大时钟周期 (relax CLK_PERIOD)",
                    "降低 placement density (当前过密)",
                    "对关键路径使用 set_false_path (如果确认是 false path)",
                    "增加关键单元驱动强度 (upsize cells on critical path)",
                    "增加 die area (降低 core utilization)",
                ],
            ))
        elif wns is not None and wns == wns and 0 <= wns < 0.1:
            items.append(DiagnosticItem(
                severity="warning", category="timing", metric_name="wns",
                actual_value=wns, threshold=">0 (margin>0.1)",
                detail=f"WNS={wns:.2f}ns — 时序通过但 margin 小 (<0.1ns)",
                suggestions=["增大 slack margin 以应对工艺变异"],
            ))
        elif wns is not None and wns == wns:
            items.append(DiagnosticItem(
                severity="success", category="timing", metric_name="wns",
                actual_value=wns, threshold=">0",
                detail=f"WNS={wns:.2f}ns — 时序满足",
            ))

        tns = m.get("tns")
        if tns is not None and tns == tns and tns < -10:
            items.append(DiagnosticItem(
                severity="error", category="timing", metric_name="tns",
                actual_value=tns, threshold=">-10",
                detail=f"总负时序裕量 TNS={tns:.1f}ns — 多条路径违规",
                suggestions=["先解决 WNS 最大的路径，TNS 通常会随之改善"],
            ))

        return items

    def _diagnose_congestion(self, m: Dict[str, float]) -> List[DiagnosticItem]:
        items = []
        cong = m.get("congestion_max") or m.get("congestion")
        if cong is not None and cong == cong and cong > 80:
            items.append(DiagnosticItem(
                severity="error", category="routing", metric_name="congestion",
                actual_value=f"{cong:.0f}%", threshold="<80%",
                detail=f"最大拥塞率 {cong:.0f}% — 布线可能失败",
                suggestions=[
                    "降低 core utilization",
                    "增大 die area",
                    "调整 pin placement",
                    "增加 routing layers",
                ],
            ))
        return items

    def _diagnose_area(self, m: Dict[str, float], constraints: Dict = None) -> List[DiagnosticItem]:
        items = []
        area = m.get("total_area") or m.get("cell_area")
        util = m.get("utilization")
        constraints = constraints or {}

        if util is not None and util == util and util > 70:
            items.append(DiagnosticItem(
                severity="warning", category="area", metric_name="utilization",
                actual_value=f"{util:.0f}%", threshold="<70%",
                detail=f"面积利用率 {util:.0f}% — 偏高，可能导致拥塞/时序问题",
                suggestions=["降低 density", "增大 die area"],
            ))

        if area is not None and area == area:
            items.append(DiagnosticItem(
                severity="info", category="area", metric_name="cell_area",
                actual_value=area, threshold="",
                detail=f"标准单元面积: {area:.0f} um²",
            ))
        return items

    def _diagnose_power(self, m: Dict[str, float]) -> List[DiagnosticItem]:
        items = []
        total_p = m.get("total_power") or m.get("leakage_power")
        if total_p is not None and total_p == total_p:
            items.append(DiagnosticItem(
                severity="info", category="power", metric_name="total_power",
                actual_value=total_p, threshold="",
                detail=f"总功耗: {total_p:.6f} W",
            ))
        return items

    def format(self, report: AnalysisReport) -> str:
        """人类可读的报告格式。"""
        lines = [
            "=" * 60,
            f"  Flow 诊断报告  |  评分: {report.score:.0f}/100",
            "=" * 60,
            f"  {report.summary}",
            f"  通过: {report.passed} | 检查项: {report.stats['total_checks']}",
            f"  错误: {report.stats['errors']} | 警告: {report.stats['warnings']}",
            "",
        ]

        sever_icon = {"error": "❌", "warning": "⚠️", "info": "ℹ️", "success": "✅"}
        for item in report.items:
            icon = sever_icon.get(item.severity, "•")
            lines.append(f"  {icon} [{item.category}] {item.metric_name}: {item.detail}")
            if item.suggestions:
                for s in item.suggestions[:3]:
                    lines.append(f"       → {s}")

        if report.recommendations:
            lines.append("")
            lines.append("  💡 综合建议:")
            for r in report.recommendations:
                lines.append(f"      {r}")

        return "\n".join(lines)
