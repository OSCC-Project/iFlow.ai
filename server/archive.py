"""
Sheet 2 活动 3: 归档交付 (方案 6.3.3)
竞赛 → PPA 对比报告; 流片 → 签核交付文档; 科研 → 评估报告
输入: 活动 1/2 的最终结果 + 收敛记录 → 产出交付报告 (Markdown) + 签核清单
"""
import os, re, time, subprocess
from server.convergence import extract_metrics

SCENE_TITLES = {"competition": "PPA 对比报告", "tapeout": "流片签核交付文档",
                "research": "科研评估报告"}
_SCENE_LABEL = {"competition": "竞赛 PPA 对比", "tapeout": "流片签核",
                "research": "科研评估"}
_ARCHIVE_DIR = "/tmp/iflow_workspace/archives"


def _tool_versions() -> dict:
    """EDA 工具版本 (可复现性元数据)"""
    def first_line(cmd):
        try:
            out = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                                 timeout=10).stdout.strip().splitlines()
            return out[0][:60] if out else ""
        except Exception:
            return ""
    return {
        "verible": first_line("verible-verilog-lint --version"),
        "verilator": first_line("verilator --version"),
        "icarus": first_line("iverilog -V"),
        "yosys": first_line("yosys -V"),
        "sby": first_line("sby --version"),
    }


def _checklist(flow: dict, results: list, metrics: dict) -> list:
    """签核清单: 有数据的项如实判定, 没跑到的项如实标注"""
    steps = {s["step"]: s for s in results}
    has_sta = "ista_sta" in steps and steps["ista_sta"].get("status") == "done"
    has_drc = "idrc_drc" in steps and steps["idrc_drc"].get("status") == "done"
    has_gds = "gds_export" in steps and steps["gds_export"].get("status") == "done"

    wns = metrics.get("wns")
    hold = steps.get("ista_sta", {}).get("metrics", {}).get("hold_wns")
    drc = metrics.get("drc")
    items = []
    if has_sta and wns is not None:
        items.append({"name": "Setup 时序 (WNS ≥ 0)", "state": "pass" if wns >= 0 else "fail",
                      "note": f"WNS = {round(wns, 4)} ns"})
    else:
        items.append({"name": "Setup 时序 (WNS ≥ 0)", "state": "skip", "note": "无 STA 数据"})
    if hold is not None:
        items.append({"name": "Hold 时序 (WNS ≥ 0)", "state": "pass" if hold >= 0 else "fail",
                      "note": f"hold WNS = {round(hold, 4)} ns"})
    if has_drc and drc is not None:
        items.append({"name": "DRC 物理规则", "state": "pass" if drc == 0 else "fail",
                      "note": f"{int(drc)} 违例" if drc else "0 违例"})
    # LVS: netgen_lvs 步骤有真实结果则如实判定, 否则标注跳过原因 (magic 未装/无 GDS 等)
    lvs = steps.get("netgen_lvs", {})
    if lvs.get("status") == "done":
        items.append({"name": "LVS 版图一致性",
                      "state": "pass" if lvs.get("metrics", {}).get("lvs_match") else "fail",
                      "note": "版图与网表匹配" if lvs.get("metrics", {}).get("lvs_match") else "版图与网表不匹配"})
    else:
        items.append({"name": "LVS 版图一致性", "state": "skip", "note": lvs.get("reason", "LVS 未运行")})
    items.append({"name": "多 corner STA", "state": "skip", "note": "仅 tt corner (单 corner)"})
    if "gds_export" in steps:
        items.append({"name": "GDS 版图导出", "state": "pass" if has_gds else "fail",
                      "note": os.path.basename(steps["gds_export"].get("gds_path", "")) if has_gds else "未产出"})
    return items


def build_archive(flow: dict, run_id: str, results: list, convergence: dict) -> dict:
    """活动 3: 生成交付报告 + 签核清单, 写入工作区归档目录"""
    scene = flow.get("scene", "research")
    title = SCENE_TITLES.get(scene, SCENE_TITLES["research"])
    metrics = (convergence or {}).get("final_metrics") or extract_metrics(results)
    checklist = _checklist(flow, results, metrics)
    # 可交付判定: 所有 pass/skip 之外没有 fail (skip = 如实标注未执行的项)
    delivered = all(c["state"] != "fail" for c in checklist)

    # 收敛摘要
    rounds = (convergence or {}).get("rounds", [])
    rounds_table = "| 轮次 | 频率 | 利用率 | WNS (ns) | DRC | 面积 | 决策 |\n|---|---|---|---|---|---|---|\n"
    for rd in rounds:
        m, d = rd["metrics"], rd["decision"]
        wns = m.get("wns")
        rounds_table += (f"| R{rd['round']} | {rd['frequency']} MHz | {rd['utilization']} | "
                         f"{round(wns, 3) if wns is not None else '—'} | {m.get('drc', '—')} | "
                         f"{m.get('area', '—')} | {d.get('type', '')} |\n")
    if not rounds:
        rounds_table = "(本次运行未启用收敛循环)\n"

    # 结论
    status = (convergence or {}).get("status")
    if delivered:
        conclusion = "全部已执行的检查项通过。当前工具链尚未覆盖的项 (LVS/多 corner) 已在签核清单中如实标注。"
    elif status == "stop_loss":
        conclusion = (f"止损结束: {(convergence or {}).get('rounds', [{}])[-1].get('decision', {}).get('reason', '')}")
    else:
        conclusion = "存在未通过项, 详见签核清单。建议参考收敛循环记录继续迭代。"

    # 交付物清单
    deliverables = []
    for s in results:
        for key, label in (("netlist_path", "综合网表"), ("gds_path", "GDS 版图"),
                           ("vcd_file", "VCD 波形"), ("sby_file", "形式验证脚本")):
            v = s.get(key)
            if v and os.path.exists(str(v)):
                deliverables.append(f"| {label} | `{v}` |")
    deliv_md = "\n".join(deliverables) if deliverables else "| (无文件交付物) | |"

    report = f"""# {title} — {flow.get('design', 'my_design')}

> 生成时间: {time.strftime('%Y-%m-%d %H:%M:%S')} · 场景: {_SCENE_LABEL.get(scene, scene)} · run: {run_id}

## 1. 设计信息
| 项 | 值 |
|---|---|
| 顶层模块 | {flow.get('design_profile', {}).get('top_module', '—')} |
| 目标频率 | {flow.get('frequency', 100)} MHz |
| 面积 | {metrics.get('area', '—')} um² |
| 深度 | {flow.get('depth', '—')} |

## 2. 收敛过程
{rounds_table}
## 3. 签核清单
| 检查项 | 状态 | 说明 |
|---|---|---|
{chr(10).join(f"| {c['name']} | {'✅' if c['state']=='pass' else '❌' if c['state']=='fail' else '⏭️'} | {c['note']} |" for c in checklist)}
## 4. 交付物
{deliv_md}
## 5. 工具链 (可复现性)
| 工具 | 版本 |
|---|---|
{chr(10).join(f"| {k} | {v or '—'} |" for k, v in _tool_versions().items())}
## 6. 结论
{conclusion}
"""
    os.makedirs(_ARCHIVE_DIR, exist_ok=True)
    report_path = os.path.join(_ARCHIVE_DIR, f"{run_id}_delivery_report.md")
    with open(report_path, "w") as f:
        f.write(report)

    return {
        "title": title,
        "scene": scene,
        "status": "delivered" if delivered else "partial",
        "report_path": report_path,
        "report_name": os.path.basename(report_path),
        "metrics": {k: (round(v, 4) if isinstance(v, float) else v)
                    for k, v in metrics.items()},
        "checklist": checklist,
        "conclusion": conclusion,
        "rounds": rounds,
    }
