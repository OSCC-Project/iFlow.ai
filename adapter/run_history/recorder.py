"""
run_history/recorder.py — Record every flow execution into run_history DB.

Hooks into the existing ic_agent_os data flow without modifying adapter internals.
"""
import json, os, time
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from .schema import get_conn


@dataclass
class RunInput:
    """What the user specified — directly from CLI variables."""
    design: str
    technology: str = "Nangate45"
    requirements: List[str] = field(default_factory=list)
    goals: Dict = field(default_factory=dict)
    fast_mode: bool = False
    rtl_path: str = ""


def record(user_input: RunInput, flow, result, run_type="demo",
           parent_run_id=None, gate_count=0, top_module=""):
    """Record a completed flow execution into the run_history table.

    Args:
        user_input: RunInput — what the user specified (from CLI)
        flow: ComposedFlow — what FlowComposer generated
        result: SnapshotPackage — what the adapter executed
        run_type: 'demo' (exploratory) or 'final' (optimized)
        parent_run_id: if final, the demo run that preceded it
        gate_count: pre-extracted gate count from RTL
        top_module: pre-extracted top module name
    """
    conn = get_conn()

    # ── Flow steps → JSON ──
    steps_json = []
    if flow and hasattr(flow, 'steps'):
        for s in flow.steps:
            steps_json.append({
                "stage": s.stage,
                "primary_tool": s.primary_tool,
                "reason": getattr(s, 'reason', ''),
            })

    # ── Warnings → JSON ──
    warnings_json = []
    if flow and hasattr(flow, 'warnings'):
        warnings_json = list(flow.warnings)

    # ── Metrics → flat JSON ──
    metrics = {}
    if result and not _is_error(result):
        dt = getattr(result, 'digital_twin', None)
        if dt:
            raw_metrics = getattr(dt, 'metrics', {})
            if raw_metrics:
                for src, vals in raw_metrics.items():
                    if isinstance(vals, dict):
                        metrics.update(vals)
                    else:
                        metrics[src] = vals
        # Also check if metrics were stored from sta
        sta = raw_metrics.get("sta", {}) if raw_metrics and 'sta' in raw_metrics else {}
        if not metrics and sta:
            metrics = dict(sta)

    # ── Pass / Fail ──
    passed = 0
    if result and not _is_error(result):
        wns = metrics.get("wns", float("nan"))
        if isinstance(wns, (int, float)) and wns == wns:
            passed = 1 if wns >= 0 else 0
        else:
            passed = 1  # no timing data → assume ok

    # ── Duration ──
    duration_ms = 0
    if result and not _is_error(result):
        ctx = getattr(result, 'observation_context', None)
        if ctx:
            duration_ms = int(getattr(ctx, 'duration_ms', 0) or 0)

    # ── Error ──
    error_msg = ""
    if _is_error(result):
        error_msg = f"{result.type}: {result.likely_cause}"[:250]

    conn.execute("""
        INSERT INTO run_history
          (design, technology, requirements, goals_json, fast_mode, rtl_path,
           gate_count, top_module,
           run_type, parent_run_id,
           flow_name, flow_phase, flow_steps_json, flow_warnings_json,
           metrics_json, passed, duration_ms, error_msg)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        user_input.design,
        user_input.technology,
        json.dumps(user_input.requirements, ensure_ascii=False),
        json.dumps(user_input.goals, ensure_ascii=False),
        1 if user_input.fast_mode else 0,
        user_input.rtl_path or "",
        gate_count,
        top_module or "",
        run_type,
        parent_run_id,
        getattr(flow, 'name', '') if flow else '',
        getattr(flow, 'phase', 'explore') if flow else '',
        json.dumps(steps_json, ensure_ascii=False),
        json.dumps(warnings_json, ensure_ascii=False),
        json.dumps(metrics, ensure_ascii=False),
        passed,
        duration_ms,
        error_msg,
    ))
    conn.commit()
    row_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return row_id


def _is_error(result):
    """Check if result is a SimError."""
    if result is None:
        return True
    if hasattr(result, 'type') and hasattr(result, 'likely_cause'):
        return True  # SimError
    return False
