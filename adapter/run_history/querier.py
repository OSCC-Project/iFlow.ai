"""
run_history/querier.py — Query historical runs by similarity

Fuzzy matching across design, technology, frequency, gate count, requirements.
Used by both FlowRecommender (before run) and recorder (for context).
"""
import json
from .schema import get_conn


class RunQuerier:
    """Query historical runs to find similar past executions."""

    def find_similar(self, design=None, technology=None, goals=None,
                     gate_count=None, requirements=None, limit=20):
        """Find runs similar to the given parameters. Returns list of dicts.

        Similarity is determined by:
          - Exact: same technology
          - Exact: same design (if known)
          - Range: frequency within ±50% of target
          - Range: gate_count within ±50% of target (if known)
          - Overlap: shared requirements keywords
        """
        conn = get_conn()
        conditions = []
        params = []

        if technology:
            conditions.append("technology = ?")
            params.append(technology)

        if design:
            conditions.append("design = ?")
            params.append(design)

        where = " AND ".join(conditions) if conditions else "1=1"
        sql = f"SELECT * FROM run_history WHERE {where} ORDER BY created_at DESC LIMIT ?"
        params.append(limit * 3)  # fetch more, filter in Python for fuzzy
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
        conn.close()

        # Post-filter: frequency similarity
        freq = _extract_freq(goals) if goals else None
        if freq:
            rows = [r for r in rows if _freq_similar(r, freq)]
        if gate_count:
            rows = [r for r in rows if _gates_similar(r, gate_count)]

        # Score and sort by similarity
        scored = [(r, _similarity_score(r, design, technology, goals, requirements))
                  for r in rows]
        scored.sort(key=lambda x: x[1], reverse=True)
        return [r for r, s in scored[:limit]]

    def find_by_metrics(self, design=None, technology=None,
                        min_wns=None, max_gates=None, limit=20):
        """Find runs where WNS >= min_wns and gates <= max_gates."""
        conn = get_conn()
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM run_history WHERE design=? AND technology=? "
            "ORDER BY created_at DESC LIMIT ?",
            (design or "%", technology or "%", limit * 3)
        ).fetchall()]
        conn.close()

        results = []
        for r in rows:
            metrics = _safe_json(r.get("metrics_json", "{}"))
            wns = metrics.get("wns", float("nan"))
            gates = r.get("gate_count", 0) or 0
            if min_wns is not None and (wns != wns or wns < min_wns):
                continue
            if max_gates is not None and gates > max_gates:
                continue
            results.append(r)
        return results[:limit]

    def stats_by_tool(self, design=None, technology=None):
        """Aggregate: for each (stage, tool) pair, count pass/fail."""
        conn = get_conn()
        rows = [dict(r) for r in conn.execute(
            "SELECT flow_steps_json, passed FROM run_history "
            "WHERE (design=? OR ? IS NULL) AND (technology=? OR ? IS NULL)",
            (design, design, technology, technology)
        ).fetchall()]
        conn.close()

        stats = {}
        for r in rows:
            steps = _safe_json(r.get("flow_steps_json", "[]"))
            passed = r.get("passed", 0)
            for s in steps:
                stage = s.get("stage", "")
                tool = s.get("primary_tool", s.get("tool", ""))
                key = f"{stage}:{tool}"
                if key not in stats:
                    stats[key] = {"pass": 0, "fail": 0}
                if passed:
                    stats[key]["pass"] += 1
                else:
                    stats[key]["fail"] += 1

        for k, v in stats.items():
            total = v["pass"] + v["fail"]
            v["success_rate"] = round(v["pass"] / total, 2) if total > 0 else None
        return stats

    def get_all(self, limit=50):
        conn = get_conn()
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM run_history ORDER BY created_at DESC LIMIT ?",
            (limit,)
        ).fetchall()]
        conn.close()
        return rows


# ── helpers ──────────────────────────────────────────────

def _extract_freq(goals):
    if not goals:
        return None
    return goals.get("frequency") or goals.get("fmax") or goals.get("freq")


def _freq_similar(run, target_freq, ratio=0.5):
    g = _safe_json(run.get("goals_json", "{}"))
    hist_freq = _extract_freq(g)
    if hist_freq is None or target_freq is None:
        return True
    lo = target_freq * (1 - ratio)
    hi = target_freq * (1 + ratio)
    return lo <= hist_freq <= hi


def _gates_similar(run, target_gates, ratio=0.5):
    hist_gates = run.get("gate_count", 0) or 0
    if hist_gates == 0 or target_gates == 0:
        return True
    lo = target_gates * (1 - ratio)
    hi = target_gates * (1 + ratio)
    return lo <= hist_gates <= hi


def _similarity_score(run, design, technology, goals, requirements):
    score = 0
    # Exact match bonuses
    if design and run.get("design") == design:
        score += 30
    if technology and run.get("technology") == technology:
        score += 20
    # Requirements overlap
    hist_reqs = set(_safe_json(run.get("requirements", "[]")))
    if requirements:
        req_set = set(requirements)
        overlap = len(hist_reqs & req_set)
        score += overlap * 10
    # Frequency proximity
    freq = _extract_freq(goals) if goals else None
    if freq:
        hist_freq = _extract_freq(_safe_json(run.get("goals_json", "{}")))
        if hist_freq:
            diff = abs(hist_freq - freq) / max(freq, 1)
            score += max(0, int(20 * (1 - diff)))
    # Recency bonus
    run_type = run.get("run_type", "")
    if run_type == "final":
        score += 5    # final runs more valuable than demo
    return score


def _safe_json(s):
    try:
        return json.loads(s) if isinstance(s, str) else (s or {})
    except (json.JSONDecodeError, TypeError):
        return {} if isinstance(s, str) and s.startswith("{") else []
