"""
Sheet 2 活动 2 + Sheet 4 消费端: 收敛循环
方案 6.3.3: 诊断 → 决策 (独立修/联合回溯/止损) → 回溯重拼装 → 重跑 → 再检查
数据源: agent_engine.STUCK_SIGNALS (卡住信号表) + STOP_LOSS (止损策略)
"""
import os

from server.agent_engine import STUCK_SIGNALS

# depth → 收敛最大轮数 (方案 6.3.1: 竞赛有止损 / 科研可选 / 流片迭代到 clean;
# 平台以 depth 映射强度: quick=只看活动1结果, standard=3轮, signoff=5轮)
MAX_ROUNDS = {"quick": 1, "standard": 3, "signoff": 5}
# 参与活动 2 的场景 (体验/课程场景在阶段 2 截断, 不进入物理实现)
CONVERGE_SCENES = {"competition", "research", "tapeout"}
# 修复动作幅度: 降频 20% (周期 +25%) / 布局密度 ×0.7 (利用率下降 → core 变大)
FREQ_SCALE = 0.8
UTIL_SCALE = 0.7
_UTIL_MIN, _UTIL_MAX = 15.0, 90.0  # 利用率钳位 (百分比)

# Sheet 4 卡住信号表的回溯目标 → 本平台可执行的步骤名
_BACKTRACK_MAP = {
    "rtl_or_sdc": "yosys_synth",      # 综合阶段时序问题 → 降频重综合
    "synth_strategy": "yosys_synth",  # STA 时序违例 → 降频重综合
    "routing_params": "ieda_floorplan",  # 布线 DRC → 重新 floorplan (降密度)
    "placement": "ieda_floorplan",    # DRC 连续不降 → 退回 placement 前
    "floorplan": "ieda_floorplan",    # HPWL 偏大 → 放宽 die
}
# 问题类型 → 修复动作文案 (方案 6.5.1 的 action/escalation 落地)
_ACTION_FIX = {
    "timing": {"changes": {"frequency": FREQ_SCALE},
               "desc": "降频 20% 后重新综合 + 物理实现"},
    "drc": {"changes": {"utilization": UTIL_SCALE},
            "desc": "降低布局密度 (利用率 ×0.7), 重新 floorplan/布局/布线"},
    "place": {"changes": {"utilization": UTIL_SCALE},
              "desc": "放宽 die 面积 (利用率 ×0.7), 重新 floorplan"},
}


def extract_metrics(results: list) -> dict:
    """从一轮 step 结果中提取指标 (WNS/DRC/面积/功耗)"""
    metrics = {"wns": None, "drc": None, "area": None, "power": None}
    for s in results:
        m = s.get("metrics") or {}
        if s["step"] == "yosys_synth":
            if m.get("area") is not None:
                metrics["area"] = m["area"]
            if metrics["wns"] is None and m.get("wns") is not None:
                metrics["wns"] = m["wns"]
        elif s["step"] == "ista_sta":
            if m.get("wns") is not None:
                metrics["wns"] = m["wns"]
            if m.get("power") is not None:
                metrics["power"] = m["power"]
        elif s["step"] == "idrc_drc":
            metrics["drc"] = m.get("drc")
    return metrics


def diagnose(metrics: dict, history: list) -> dict:
    """对照 Sheet 4 卡住信号表诊断本轮指标 → 问题列表 + 止损判定

    history: 本轮之前各轮 metrics 列表 (含 drc 趋势推导)
    """
    # 衍生信号: DRC 趋势 (供 STUCK_SIGNALS 的 drc_trend 行使用)
    drc_prev = None
    for h in reversed(history):
        if h.get("drc") is not None:
            drc_prev = h["drc"]
            break
    drc = metrics.get("drc")
    if drc is not None and drc > 0:
        trend = "down" if (drc_prev is not None and drc < drc_prev) else "flat"
    else:
        trend = "down"  # 无违例/已清零 → 不算卡住
    ctx = {**metrics, "drc_trend": trend, "hpwl_ratio": 1.0}

    problems = []
    for stage, cond, severity, action, backtrack, max_retries, escalation in STUCK_SIGNALS:
        # 数据可用性门控: 没有对应指标的行不评估 (如无 DRC 数据时布线行不适用)
        if stage == "ieda_route" and (drc is None or drc <= 0):
            continue
        if stage in ("ista_sta", "yosys_synth") and metrics.get("wns") is None:
            continue
        if stage == "ieda_place":
            continue  # hpwl_ratio 数据源未接入 (恒 1.0), 行暂不生效
        try:
            hit = bool(cond(ctx))
        except Exception:
            continue
        if not hit:
            continue
        kind = "drc" if stage == "ieda_route" else "timing"
        if any(p["kind"] == kind for p in problems):
            continue  # 同类问题取表中第一条规则
        problems.append({
            "kind": kind, "stage": stage, "severity": severity,
            "value": drc if kind == "drc" else metrics.get("wns"),
            "signal": action, "escalation": escalation,
            "backtrack_to": _BACKTRACK_MAP.get(backtrack, "yosys_synth"),
            "action": _ACTION_FIX.get(kind, _ACTION_FIX["timing"])["desc"],
            "changes": _ACTION_FIX.get(kind, _ACTION_FIX["timing"])["changes"],
            "max_retries": max_retries,
        })
    stop = _check_stop_loss(metrics, history)
    return {"problems": problems, "stop": stop,
            "status": "clean" if not problems else "issues"}


def _check_stop_loss(metrics: dict, history: list) -> dict:
    """Sheet 4 STOP_LOSS: 连续多轮无改善 → 止损, 不再死磕"""
    def _series(key):
        vals = [h.get(key) for h in history if h.get(key) is not None]
        cur = metrics.get(key)
        if cur is not None:
            vals.append(cur)
        return vals

    wns_s = _series("wns")
    # WNS 连续 3 轮恶化 (含当前轮, 严格递减) → 降频也救不回, 属设计本身问题
    if len(wns_s) >= 3 and wns_s[-1] < wns_s[-2] < wns_s[-3] and wns_s[-1] < 0:
        return {"rule": "wns_3_rounds_worse",
                "reason": "WNS 连续 3 轮恶化 (降频无效) → 建议重新评估设计架构, "
                          "或换更快的工艺库"}
    drc_s = _series("drc")
    # DRC 连续 5 轮无改善 (最近 5 轮的最小值 == 历史最小值 且当前仍 > 0)
    if drc_s and drc_s[-1] > 0 and len(drc_s) >= 5:
        if min(drc_s[-5:]) == min(drc_s[:-5]) and min(drc_s[-5:]) > 0:
            return {"rule": "drc_5_rounds_no_improve",
                    "reason": "DRC 连续 5 轮无改善 → 建议检查 LEF/工艺规则映射, "
                              "或更换 PDK"}
    return None


def decide(problems: list, stop: dict, round_idx: int, max_rounds: int) -> dict:
    """方案 6.5.2 收敛循环决策树:
    问题独立 → 逐个修复; 问题关联 (多种并存) → 联合回溯; 连续无改善 → 止损"""
    if problems and stop:
        return {"type": "stop", "rule": stop["rule"], "reason": stop["reason"]}
    if not problems:
        return {"type": "converged", "reason": "全部指标 clean, 收敛完成"}
    if round_idx + 1 >= max_rounds:
        names = ", ".join(p["signal"] for p in problems)
        return {"type": "max_rounds",
                "reason": f"达到轮数上限 ({max_rounds} 轮), 仍存在: {names}; "
                          f"升级建议: {problems[0]['escalation']}"}
    kinds = {p["kind"] for p in problems}
    if len(kinds) > 1:
        # 关联问题 → 联合回溯: 从综合起一次修完
        return {"type": "rerun", "backtrack_to": "yosys_synth",
                "changes": {"frequency": FREQ_SCALE, "utilization": UTIL_SCALE},
                "reason": "时序与 DRC 违例并存 (联合回溯): 降频 20% + 降低密度, "
                          "从综合重新实现"}
    p = problems[0]
    return {"type": "rerun", "backtrack_to": p["backtrack_to"],
            "changes": p["changes"],
            "reason": f"{p['signal']} → {p['action']}"}


def scale_utilization(util_pct: str, scale: float) -> str:
    """利用率字符串缩放 (如 "35%" ×0.7 → "24%"), 钳位在 [15%, 90%]"""
    try:
        u = float(str(util_pct).replace("%", "").strip())
    except (ValueError, AttributeError):
        u = 35.0
    u = min(max(u * scale, _UTIL_MIN), _UTIL_MAX)
    return f"{u:.0f}%"


def run_convergence_loop(flow: dict, executor, round0_results: list, round0_ctx: dict,
                         run_id: str, push_ws, base_utilization: str = "35%") -> dict:
    """活动 2 收敛循环主循环 (方案 6.3.3 流程图):
    每轮: 诊断 (Sheet 4 卡住信号) → 决策 (独立修/联合回溯/止损) → 回溯重拼装 → 重跑

    executor(round_flow, ieda_ctx, utilization) -> (results, new_ctx):
        round_flow 的 steps 已裁剪为从回溯点开始的 flow 副本
    返回: {"rounds": [...], "status": converged|stop_loss|max_rounds,
          "final_metrics": {...}, "results": 最后一轮 step 结果}
    """
    depth = flow.get("depth", "standard")
    max_rounds = MAX_ROUNDS.get(depth, 3)
    freq = float(flow.get("frequency", 100) or 100)
    util = base_utilization
    ctx = dict(round0_ctx)
    history: list = []
    rounds = []
    status = "max_rounds"
    full_steps = flow.get("steps", [])
    current_results = round0_results

    for r in range(max_rounds):
        metrics = extract_metrics(current_results)
        diag = diagnose(metrics, history)
        decision = decide(diag["problems"], diag["stop"], r, max_rounds)
        rounds.append({
            "round": r + 1,
            "metrics": {k: (round(v, 4) if isinstance(v, float) else v)
                        for k, v in metrics.items()},
            "diagnosis": diag, "decision": decision,
            "frequency": round(freq, 1), "utilization": util,
        })
        push_ws({"type": "convergence_round", "run_id": run_id, "round": r + 1,
                 "status": decision["type"], "reason": decision.get("reason", "")})
        if decision["type"] != "rerun":
            status = ({"converged": "converged", "stop": "stop_loss"}
                      .get(decision["type"], "max_rounds"))
            break

        # 回溯重拼装 (Sheet 3 + Sheet 1): 应用参数修改, 从回溯点裁剪 steps
        changes = decision.get("changes", {})
        if "frequency" in changes:
            freq = round(freq * changes["frequency"], 2)
        if "utilization" in changes:
            util = scale_utilization(util, changes["utilization"])
        try:
            bt = full_steps.index(decision["backtrack_to"])
        except ValueError:
            rounds[-1]["error"] = f"回溯目标 {decision['backtrack_to']} 不在步骤列表"
            status = "stop"
            break
        round_flow = {**flow, "steps": full_steps[bt:], "frequency": freq}
        # 每轮用新的 RESULT_DIR: iEDA db_init 把数据库缓存在 output_dir,
        # 复用同一目录会直接吃旧 db (旧 SDC/旧 DEF), 新参数 (降频/密度) 不生效
        run_dir = ctx.get("run_dir", "")
        round_result_dir = os.path.join(run_dir, f"result_r{r + 1}")
        # 回溯到综合 → 一切重来 (降频需重新综合 + 重新生成 SDC);
        # 回溯到 floorplan 之后 → 复用已合成的网表与 SDC, 只重建 DEF 链
        if decision["backtrack_to"] == "yosys_synth":
            round_ctx = {"netlist": None, "synth_ok": False, "def": None, "routed_def": None,
                         "run_dir": run_dir, "result_dir": round_result_dir,
                         "sdc_path": ""}
        else:
            round_ctx = {**ctx, "def": None, "routed_def": None,
                         "result_dir": round_result_dir}
        try:
            current_results, ctx = executor(round_flow, round_ctx, util)
        except Exception as e:
            rounds[-1]["error"] = str(e)
            status = "stop"
            break
        history.append(metrics)

    return {"rounds": rounds, "status": status,
            "final_metrics": extract_metrics(current_results),
            "results": current_results}
