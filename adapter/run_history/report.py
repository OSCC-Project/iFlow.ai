"""
run_history/report.py — Demo flow diagnostic report formatter.

After a demo flow completes, this generates a terminal report that:
  1. Shows what ran and how it performed
  2. Compares against historical similar runs
  3. Recommends how to build the final flow
"""
import json
from .recommender import DemoAdvice, FinalAdvice


def format_demo_report(design, technology, goals, requirements,
                       demo_flow, demo_result,
                       demo_advice: DemoAdvice,
                       final_advice: FinalAdvice,
                       historical_baselines) -> str:
    """Generate a formatted terminal report after demo flow completion.

    Returns a string ready to print().
    """
    lines = []

    # ── Extract metrics ──
    metrics = {}
    duration_ms = 0
    if demo_result is not None and not _is_error(demo_result):
        dt = getattr(demo_result, 'digital_twin', None)
        if dt:
            m = getattr(dt, 'metrics', {}) or {}
            for src, vals in m.items():
                if isinstance(vals, dict):
                    metrics.update(vals)
                else:
                    metrics[src] = vals
        ctx = getattr(demo_result, 'observation_context', None)
        if ctx:
            duration_ms = getattr(ctx, 'duration_ms', 0) or 0

    def _first_valid(*keys):
        for k in keys:
            v = metrics.get(k)
            if v is not None and (not isinstance(v, float) or v == v):
                return v
        return float("nan")

    wns = _first_valid("wns", "sta.wns_ns")
    tns = _first_valid("tns", "sta.tns_ns")
    area = _first_valid("total_area", "design.area_um2", "sta.area_um2")
    leak = _first_valid("leakage_power", "power.leakage_uw")

    freq = goals.get("frequency", 0) if goals else 0
    gates = 0
    if final_advice and final_advice.reasoning:
        # extract gate count from reasoning if available
        pass

    passed = (wns == wns and wns >= 0)  # NaN-safe

    # ── Header ──
    lines.append("")
    lines.append("═" * 60)
    lines.append(f"  Demo Flow 诊断报告")
    lines.append(f"  Design: {design}  |  Technology: {technology}  |  "
                 f"{freq}MHz" + (f"  |  Area≤{goals.get('area_max')}" if goals.get('area_max') else ""))
    lines.append("═" * 60)

    # ── Section 1: Execution results ──
    lines.append("")
    lines.append("── 1. 执行结果 ──")
    if demo_flow and hasattr(demo_flow, 'steps'):
        for s in demo_flow.steps:
            result_str = "✅" if not _is_error(demo_result) else "❌"
            lines.append(f"  [{s.stage:12s}] {s.primary_tool:12s}  {result_str}")
    else:
        lines.append(f"  {'✅' if passed else '❌'} 完成")

    dur_s = duration_ms / 1000 if duration_ms else 0
    if passed:
        status = "✅ 通过"
    elif wns != wns:
        status = "⚠️  STA 未运行 (无时序数据)"
    else:
        status = "❌ 时序不达标"
    lines.append(f"  耗时: {dur_s:.1f}s  |  状态: {status}")
    if demo_result is not None and _is_error(demo_result):
        lines.append(f"  错误: {demo_result.type}: {getattr(demo_result, 'likely_cause', '')[:100]}")
    elif demo_result is None:
        lines.append(f"  (demo 结果未传入 — 使用诊断数据)")

    # ── Section 2: PPA check ──
    lines.append("")
    lines.append("── 2. PPA 体检 ──")
    _metric_line(lines, "Timing (WNS)", wns, "ns", ">0", passed)
    _metric_line(lines, "Timing (TNS)", tns, "ns", ">0",
                 tns == tns and (tns >= 0 if tns is not None else True))

    if area is not None and area == area:
        area_ok = goals.get("area_max") is None or area <= goals["area_max"]
        _metric_line(lines, "Area", area, "um²", f"<{goals.get('area_max', '?')}" if goals.get('area_max') else "—",
                     area_ok)
    else:
        lines.append("  Area:     —  (demo 未跑物理流程, 无数据)")

    if leak is not None and leak == leak:
        lines.append(f"  Leakage:  {leak:.4f} µW")
    else:
        lines.append("  Power:    —  (demo 未跑物理流程, 无数据)")

    # ── Section 3: Historical comparison ──
    lines.append("")
    lines.append("── 3. 历史对比 ──")
    if historical_baselines:
        lines.append(f"  查到 {len(historical_baselines)} 条相似历史记录:")
        lines.append("  ┌" + "─" * 56 + "┐")
        lines.append(f"  │ {'设计':8s} {'频率':6s} {'工具链':20s} {'WNS':6s} {'结果':4s} │")
        lines.append("  ├" + "─" * 56 + "┤")
        for b in historical_baselines[:8]:
            b_freq = b.get("goals", {}).get("frequency", "?")
            b_wns = b.get("wns", "?")
            b_wns_str = f"{b_wns:+.1f}" if isinstance(b_wns, (int, float)) and b_wns == b_wns else "?"
            b_passed = "✅" if b.get("passed") else "❌"
            tools = "→".join(b.get("tools", [])[:3]) or "?"
            lines.append(f"  │ {b.get('design','?')[:8]:8s} {str(b_freq)+'M':6s} "
                         f"{tools:20s} {b_wns_str:6s} {b_passed:4s} │")
        lines.append("  └" + "─" * 56 + "┘")

        # Tool confidence stats
        if demo_advice and demo_advice.tool_confidence:
            lines.append("")
            lines.append("  工具历史成功率:")
            for stage, tools in demo_advice.tool_confidence.items():
                for tool, rate in tools.items():
                    icon = "✅" if rate is None or rate >= 0.7 else "⚠️" if rate >= 0.3 else "❌"
                    rate_str = f"{rate:.0%}" if rate is not None else "无数据"
                    lines.append(f"    {icon} {stage}:{tool} = {rate_str}")
    else:
        lines.append("  (无历史数据 — 这是第一次运行)")
        if demo_advice and demo_advice.warnings:
            lines.append("")
            lines.append("  已知问题 (来自 PDK 层面):")
            for w in demo_advice.warnings:
                lines.append(f"    {w}")

    # ── Section 4: Final Flow advice ──
    lines.append("")
    lines.append("── 4. Final Flow 构建建议 ──")
    if final_advice:
        # Adjust depth display based on skip steps
        effective_depth = final_advice.recommended_depth
        if effective_depth == "full" and final_advice.suggested_skip_steps:
            effective_depth = "custom"
        depth_icon = {"lite": "⚡ 精简 (2步)", "full": "🏗️ 完整 (12步)",
                      "custom": "🔧 自定义裁剪"}
        depth_str = depth_icon.get(effective_depth, effective_depth)
        lines.append(f"  📐 流程深度: {depth_str}")

        if final_advice.suggested_skip_steps:
            skip_str = ", ".join(final_advice.suggested_skip_steps)
            lines.append(f"  ✂️  建议跳过: {skip_str}")

        if final_advice.tool_confidence:
            any_low = False
            for stage, tools in final_advice.tool_confidence.items():
                for tool, rate in tools.items():
                    if rate is not None and rate < 0.7:
                        lines.append(f"  ⚠️  {stage}:{tool} 历史成功率仅 {rate:.0%}")
                        any_low = True
            if not any_low:
                lines.append(f"  🔧 工具选择: 无风险, 当前选择均可靠")

        if final_advice.param_advice:
            lines.append(f"  ⚙️  参数建议:")
            for p in final_advice.param_advice:
                lines.append(f"      {p}")

        for r in final_advice.reasoning:
            lines.append(f"  💡 {r}")
    else:
        lines.append("  (需先运行 demo 以生成建议)")

    # Historical warnings from demo_advice
    if demo_advice and demo_advice.warnings:
        for w in demo_advice.warnings:
            lines.append(f"  ⚠️  {w}")

    # ── Section 5: Next steps ──
    lines.append("")
    lines.append("── 5. 下一步 ──")
    lines.append("  [回车]  按上述建议自动生成 Final Flow 并执行")
    lines.append("  [m]     手动修改 Flow 后生成")
    lines.append("  [q]     退出")

    lines.append("")
    lines.append("═" * 60)
    return "\n".join(lines)


def _metric_line(lines, label, value, unit, threshold, ok):
    """Format a single metric line with status icon."""
    if value is None or (isinstance(value, float) and value != value):
        lines.append(f"  {label:16s} —  (无数据)")
        return
    icon = "✅" if ok else "❌"
    lines.append(f"  {label:16s} {value:.2f} {unit:4s}  {icon}  (目标: {threshold})")


def _is_error(result):
    if result is None:
        return True
    if hasattr(result, 'type') and hasattr(result, 'likely_cause'):
        return True
    return False
