"""
run_history/recommender.py — Generate flow-building advice from historical data.

Two entry points:
  suggest_demo()  — before first run: what exploration strategy?
  suggest_final() — after demo run: what should the final flow look like?

Consumed by FlowComposer.compose() via the `history` parameter.
"""
import json
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from .querier import RunQuerier


@dataclass
class DemoAdvice:
    """Advice for building the demo (exploration) flow."""
    suggested_phase: str = "explore"
    initial_params: Dict = field(default_factory=dict)
    historical_baselines: List[Dict] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    tool_confidence: Dict[str, Dict[str, float]] = field(default_factory=dict)


@dataclass
class FinalAdvice:
    """Advice for building the final (optimized) flow after demo diagnosis."""
    recommended_depth: str = "full"          # "lite" | "full" | "custom"
    suggested_skip_steps: List[str] = field(default_factory=list)
    tool_confidence: Dict[str, Dict[str, float]] = field(default_factory=dict)
    param_advice: List[str] = field(default_factory=list)
    reasoning: List[str] = field(default_factory=list)


class FlowRecommender:
    """Recommend flow strategy based on historical execution data.

    Usage:
        recommender = FlowRecommender()
        demo = recommender.suggest_demo("gcd", "Nangate45", {"frequency": 200})
        # → DemoAdvice with warnings, param suggestions, baselines

        final = recommender.suggest_final("gcd", "Nangate45",
                                          {"frequency": 200}, demo_diagnosis)
        # → FinalAdvice with step recommendations, tool confidence
    """

    def __init__(self):
        self.querier = RunQuerier()

    # ═══════════════════════════════════════════════════════════
    # Demo flow advice — based purely on history
    # ═══════════════════════════════════════════════════════════
    def suggest_demo(self, design, technology, goals,
                     gate_count=0, requirements=None, rtl_path="") -> DemoAdvice:
        """Before running anything: use history to guide exploration.

        Answers:
          - Should we run lite (synth+STA) or full exploration?
          - What initial CLK_PERIOD worked for similar designs?
          - Any known issues with this PDK/design combination?

        Args:
            design: design name (e.g. "gcd")
            technology: PDK name (e.g. "Nangate45")
            goals: user goals dict (e.g. {"frequency": 200})
            gate_count: pre-counted gates from RTL
            requirements: list of requirement strings
            rtl_path: path to RTL file
        """
        similar = self.querier.find_similar(
            design=design, technology=technology, goals=goals,
            gate_count=gate_count, requirements=requirements, limit=20
        )

        freq = goals.get("frequency", 0) if goals else 0
        gate_count = gate_count or 0

        # ── Warnings ──
        warnings = []
        warnings.extend(self._check_pdk_issues(similar, technology))
        warnings.extend(self._check_freq_boundaries(similar, freq))
        warnings.extend(self._check_size_surprises(similar, gate_count))

        # ── Initial parameters ──
        params = {}
        best_period = self._best_initial_clk_period(similar, freq)
        if best_period:
            params["CLK_PERIOD"] = best_period
        best_density = self._best_initial_density(similar, gate_count)
        if best_density:
            params["PLACE_DENSITY"] = best_density

        # ── Tool confidence from history ──
        tool_conf = self._compute_tool_confidence(similar)

        # ── Historical baselines (for human review) ──
        baselines = self._summarize_baselines(similar[:5])

        return DemoAdvice(
            suggested_phase="explore",
            initial_params=params,
            historical_baselines=baselines,
            warnings=warnings,
            tool_confidence=tool_conf,
        )

    # ═══════════════════════════════════════════════════════════
    # Final flow advice — demo diagnosis + history
    # ═══════════════════════════════════════════════════════════
    def suggest_final(self, design, technology, goals,
                      demo_diagnosis=None, demo_metrics=None) -> FinalAdvice:
        """After demo run: use diagnosis + history to craft the final flow.

        demo_diagnosis can be:
          - An AnalysisReport from composer.analyzer
          - A dict with keys: wns, tns, gate_count, area, passed

        Returns FinalAdvice with concrete, actionable recommendations.
        """
        demo_wns = 0.0
        demo_gates = 0
        demo_passed = True

        if demo_diagnosis:
            if hasattr(demo_diagnosis, 'items') and not isinstance(demo_diagnosis, dict):
                # AnalysisReport from composer.analyzer
                for item in demo_diagnosis.items:
                    if hasattr(item, 'metric_name') and item.metric_name == "wns":
                        demo_wns = item.actual_value if isinstance(item.actual_value, (int, float)) else 0
                demo_passed = getattr(demo_diagnosis, 'passed', True)
            elif isinstance(demo_diagnosis, dict):
                demo_wns = demo_diagnosis.get("wns", 0) or 0
                demo_gates = demo_diagnosis.get("gate_count", 0) or 0
                demo_passed = demo_diagnosis.get("passed", True)

        if demo_metrics:
            demo_wns = demo_metrics.get("wns", demo_wns) or 0
            demo_gates = demo_metrics.get("gate_count", demo_gates) or 0

        # ── Query: same design + technology + similar metrics ──
        similar = self.querier.find_similar(
            design=design, technology=technology, goals=goals,
            gate_count=demo_gates, limit=20
        )

        reasoning = []
        skip_steps = []
        depth = "full"

        # ── Reasoning 1: depth inference ──
        depth, depth_reason = self._infer_flow_depth(
            demo_wns, demo_gates, goals, similar
        )
        reasoning.append(depth_reason)

        # ── Reasoning 2: skip steps ──
        skip_steps, skip_reason = self._infer_skip_steps(
            demo_wns, demo_gates, similar
        )
        if skip_reason:
            reasoning.append(skip_reason)

        # ── Reasoning 3: tool swap suggestions ──
        tool_conf = self._compute_tool_confidence(similar)
        swap_reason = self._suggest_tool_swaps(tool_conf, similar, goals)
        if swap_reason:
            reasoning.append(swap_reason)

        # ── Reasoning 4: parameter advice ──
        param_advice = []
        param_advice.extend(self._advise_params_from_history(similar, demo_wns, goals))
        if param_advice:
            reasoning.append(f"参数建议: {'; '.join(param_advice)}")

        return FinalAdvice(
            recommended_depth=depth,
            suggested_skip_steps=skip_steps,
            tool_confidence=tool_conf,
            param_advice=param_advice,
            reasoning=reasoning,
        )

    # ═══════════════════════════════════════════════════════════
    # Tool confidence — per stage, per tool
    # ═══════════════════════════════════════════════════════════
    def get_tool_confidence(self, stage, tool, history=None):
        """Get success rate of a specific (stage, tool) pair from history.

        Called by FlowComposer._score_tool() to adjust static scores.
        Returns None if no data available (→ use static score as-is).
        Only returns exact matches — no fuzzy fallback for unknown tools.
        """
        stats = self.querier.stats_by_tool()
        key = f"{stage}:{tool}"
        entry = stats.get(key, {})
        return entry.get("success_rate")

    # ═══════════════════════════════════════════════════════════
    # Internal helpers
    # ═══════════════════════════════════════════════════════════

    def _check_pdk_issues(self, similar, technology):
        w = []
        fails = [r for r in similar if not r.get("passed")]
        if fails and len(fails) >= len(similar) * 0.5:
            w.append(f"⚠️ {technology}: {len(fails)}/{len(similar)} 次历史运行不达标")
        route_errors = [r for r in similar if "route" in (r.get("error_msg") or "").lower()]
        if route_errors:
            w.append(f"⚠️ {technology}: 历史上有 {len(route_errors)} 次 routing 失败")
        return w

    def _check_freq_boundaries(self, similar, freq):
        if not freq:
            return []
        w = []
        hi_fails = [r for r in similar
                    if _extract_freq(r) and _extract_freq(r) >= freq * 1.5
                    and not r.get("passed")]
        if hi_fails:
            w.append(f"⚠️ 频率≥{int(freq*1.5)}MHz 时 Yosys 有 {len(hi_fails)} 次不达标")
        return w

    def _check_size_surprises(self, similar, gates):
        if not gates:
            return []
        w = []
        big = [r for r in similar if (r.get("gate_count") or 0) > gates * 5]
        if big:
            w.append(f"ℹ️ 当前设计 ({gates} gates) 远小于历史类似设计")
        return w

    def _best_initial_clk_period(self, similar, freq):
        if not freq:
            return None
        periods = []
        for r in similar:
            g = _safe_json(r.get("goals_json", "{}"))
            hf = g.get("frequency", 0)
            if hf and r.get("passed"):
                periods.append(1000.0 / hf)  # MHz → ns
        if periods:
            avg = sum(periods) / len(periods)
            return round(avg, 1)
        return round(1000.0 / freq, 1) if freq else None

    def _best_initial_density(self, similar, gates):
        if not gates:
            return None
        densities = []
        for r in similar:
            m = _safe_json(r.get("metrics_json", "{}"))
            util = m.get("utilization")
            if util and r.get("passed"):
                densities.append(util)
        if densities:
            return round(sum(densities) / len(densities), 2)
        return 0.6

    def _summarize_baselines(self, similar):
        return [{
            "design": r.get("design"),
            "goals": _safe_json(r.get("goals_json", "{}")),
            "tools": [s.get("primary_tool") for s in
                       _safe_json(r.get("flow_steps_json", "[]"))[:3]],
            "passed": bool(r.get("passed")),
            "wns": (_safe_json(r.get("metrics_json", "{}")).get("wns")
                    or _safe_json(r.get("metrics_json", "{}")).get("sta.wns_ns")
                    or "?"),
            "duration_ms": r.get("duration_ms", 0),
            "run_type": r.get("run_type", "demo"),
        } for r in similar]

    def _compute_tool_confidence(self, similar):
        """Aggregate per-tool success rates from similar runs."""
        conf = {}
        for r in similar:
            steps = _safe_json(r.get("flow_steps_json", "[]"))
            passed = r.get("passed", 0)
            for s in steps:
                stage = s.get("stage", "")
                tool = s.get("primary_tool", "")
                if stage not in conf:
                    conf[stage] = {}
                if tool not in conf[stage]:
                    conf[stage][tool] = {"pass": 0, "total": 0}
                conf[stage][tool]["total"] += 1
                if passed:
                    conf[stage][tool]["pass"] += 1

        result = {}
        for stage, tools in conf.items():
            result[stage] = {}
            for tool, stats in tools.items():
                rate = stats["pass"] / stats["total"] if stats["total"] > 0 else None
                result[stage][tool] = round(rate, 2) if rate is not None else None
        return result

    def _infer_flow_depth(self, wns, gates, goals, similar):
        """Infer whether lite or full flow is needed.

        Heuristics based on design size and timing margin, backed by history.
        """
        freq = goals.get("frequency", 0) if goals else 0

        # Count historical passes: lite (≤3 steps) vs full (>3 steps)
        lite_passes = [r for r in similar if r.get("passed")
                       and len(_safe_json(r.get("flow_steps_json", "[]"))) <= 3]
        full_passes = [r for r in similar if r.get("passed")
                       and len(_safe_json(r.get("flow_steps_json", "[]"))) > 3]

        # Small design + good WNS → lite is likely enough
        if gates and 0 < gates < 2000 and wns >= 0 and wns == wns:
            if lite_passes and len(lite_passes) >= len(full_passes):
                return "lite", (f"设计小 ({gates} gates) + WNS={wns:.2f}>0, "
                                f"历史 {len(lite_passes)} 次精简全通过 → 推荐精简")

        # Timing margin is huge → no need for full physical optimization
        if wns > 5.0 and gates < 5000:
            return "lite", f"WNS={wns:.1f}ns 余量极大 → 精简流程即可"

        # Large design → full flow
        if gates and gates > 10000:
            return "full", f"设计大 ({gates} gates) → 推荐完整 12 步物理流程"

        # High frequency → full flow with STA checkpoints
        if freq and freq > 500:
            return "full", f"高频 ({freq}MHz) 需要完整物理流程 + STA checkpoints"

        # Tight timing margin → full for safety
        if 0 <= wns < 1.0:
            return "full", f"WNS={wns:.2f}ns 余量紧张 → 建议完整流程确保收敛"

        return "full", "默认完整流程"

    def _infer_skip_steps(self, wns, gates, similar):
        skip = []
        reasons = []

        # Small design: skip resize (no timing closure needed)
        if gates and gates < 1000 and wns > 2.0:
            skip.append("resize")
            reasons.append(f"小设计 ({gates} gates) + WNS={wns:.1f}>>0 → 跳过 resize")

        # If WNS is huge, timing optimization steps are unnecessary
        if wns > 3.0:
            if "resize" not in skip:
                skip.append("resize")
            reasons.append(f"WNS={wns:.1f}ns 余量充足 → 跳过低价值优化步骤")

        # Check history: did any designs skip filler without issues?
        if gates and gates < 2000:
            filler_fails = [r for r in similar
                           if not r.get("passed") and "filler" in str(
                               _safe_json(r.get("flow_warnings_json", "[]")))]
            if not filler_fails:
                skip.append("filler")
                reasons.append("小设计 filler 非必须 → 跳过")

        return skip, " | ".join(reasons) if reasons else ""

    def _suggest_tool_swaps(self, tool_conf, similar, goals):
        """Suggest tool swaps based on historical confidence."""
        freq = goals.get("frequency", 0) if goals else 0
        suggestions = []

        for stage, tools in tool_conf.items():
            for tool, rate in tools.items():
                if rate is not None and rate < 0.5 and rate > 0:
                    suggestions.append(
                        f"{stage}: {tool} 历史成功率仅 {rate:.0%} → 考虑替换"
                    )
                elif rate == 0.0:
                    suggestions.append(
                        f"{stage}: {tool} 历史 {tools.get(tool, {}).get('total', 0)} 次全部失败 → 建议替换"
                    )

        if freq > 500:
            synth_conf = tool_conf.get("synthesis", {})
            yosys_rate = synth_conf.get("Yosys")
            if yosys_rate is not None and yosys_rate < 0.7:
                suggestions.append("高频设计 → 建议 synthesis 用 Design Compiler 替代 Yosys")

        return " | ".join(suggestions[:3]) if suggestions else ""

    def _advise_params_from_history(self, similar, wns, goals):
        advice = []
        freq = goals.get("frequency", 0) if goals else 0

        # Timing: suggest period adjustment
        if wns < 0 and freq:
            deficit = abs(wns)
            new_period = round(1000.0 / freq + deficit, 1)
            advice.append(f"WNS<0 → 放宽 CLK_PERIOD 从 {round(1000.0/freq,1)}ns → {new_period}ns")

        # Check historical density settings that worked
        best_density = self._best_initial_density(similar, 0)
        if best_density and wns > 1.0:
            advice.append(f"时序余量充足 → 可提高 density 从 0.6 → {min(0.85, best_density + 0.1)}")

        return advice


# ── helpers ──────────────────────────────────────────────

def _extract_freq(run):
    g = _safe_json(run.get("goals_json", "{}"))
    return g.get("frequency") or g.get("fmax")


def _safe_json(s):
    try:
        return json.loads(s) if isinstance(s, str) else (s or {})
    except (json.JSONDecodeError, TypeError):
        return {}
