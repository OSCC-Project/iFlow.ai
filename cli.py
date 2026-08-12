#!/usr/bin/env python3
"""
ic-flow CLI — 交互式 IC 设计流程工具
══════════════════════════════════════════════════════════
  交互模式: python3 cli.py
  命令模式: python3 cli.py compose|run|optimize|swap|status|history
══════════════════════════════════════════════════════════"""
import argparse, os, sys, json, time, glob as _glob
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
os.chdir(str(Path(__file__).parent))

from composer.flow_composer import FlowComposer
from composer.goals import PPASpec
from composer.replanner import Replanner
from composer.analyzer import FlowAnalyzer
from adapter.adapter import Adapter
from adapter.contract import SnapshotPackage, SimError
from adapter.run_history import FlowRecommender, record, RunInput, format_demo_report
from param_bridge import goal_to_params
from state import SnapshotReceiver
import yaml as _yaml

def _pdk(key, *default):
    """从 adapter/config.yaml 读取 PDK 路径/参数, 消除硬编码。"""
    try:
        cfg = _yaml.safe_load(open("adapter/config.yaml"))
        be = cfg.get("backend", {})
        if key.endswith("_lib"):
            corner = key.replace("_lib", "").upper()
            return be.get("openroad", {}).get("corners", {}).get(corner, {}).get("liberty", "")
        # 物理默认参数
        if key in ("die_area", "core_area"):
            defaults = be.get("openroad", {}).get("defaults", {})
            return defaults.get(key, "")
        return be.get("openroad", {}).get("pdk", {}).get(key, "")
    except: pass
    return default[0] if default else ""


# ═══════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════

def _ask(prompt, default="", allow_back=True, validate=None, err_msg=""):
    """交互式提问。输入 'b' 返回, 回车用默认, validate 返回 True 才通过。"""
    show_default = default if default and default != "" else None
    hint = f" [默认: {show_default}]" if show_default else ""
    back_hint = " [b=返回上一步]" if allow_back else ""
    while True:
        ans = input(f"{prompt}{hint}{back_hint}: ").strip()
        if allow_back and ans.lower() == 'b':
            return 'BACK'
        if not ans:
            return default  # 回车直接返回默认值 (可以是空字符串)
        if validate is None or validate(ans):
            return ans
        print(f"  ⚠️  {err_msg or '输入无效, 请重试'}")


def _validate_number(ans, lo=None, hi=None):
    try: n = int(ans); return (lo is None or n >= lo) and (hi is None or n <= hi)
    except: return False

def _validate_file(ans):
    ok = os.path.exists(ans) and os.path.isfile(ans) and os.access(ans, os.R_OK)
    if not ok: print(f"  ⚠️  文件不存在或不可读: {ans}")
    return ok

def _validate_float(ans, positive=False):
    try: v = float(ans); return not positive or v > 0
    except: return False


def _parse_goals(raw):
    goals = {}
    for item in (raw or []):
        if "=" not in item: continue
        k, v = item.split("=", 1)
        k = {"freq":"frequency","frequency":"frequency","area":"area_max",
             "area_max":"area_max","power":"power_max","power_max":"power_max",
             "wns":"wns","tns":"tns","util":"utilization"}.get(k, k)
        v = v.lower().strip()
        if v.endswith("k"): v = float(v[:-1]) * 1000
        elif v.endswith("m"): v = float(v[:-1])
        elif v.endswith("ns"): v = float(v[:-2])
        elif v.endswith("ghz"): v = float(v[:-3]) * 1000
        else:
            try: v = float(v)
            except: pass
        goals[k] = v
    return goals


# ═══════════════════════════════════════════════════════════
# 已注册的设计列表（自动扫描 + 用户自定义）
# ═══════════════════════════════════════════════════════════

# ── 持久化设计列表 ──
_USER_DESIGNS_FILE = str(Path(__file__).parent / "user_designs.json")

def _load_user_designs():
    if os.path.exists(_USER_DESIGNS_FILE):
        try:
            return json.load(open(_USER_DESIGNS_FILE))
        except: pass
    return {}

def _resolve_rtl(relative):
    """解析 RTL 路径: 相对于项目根目录。内置设计可用此函数定位。"""
    return str(Path(__file__).parent / relative)

def _save_design(name, rtl_path, lines):
    """永久保存用户自定义设计到 JSON 文件。"""
    user = _load_user_designs()
    user[name] = {"name": name, "rtl": rtl_path, "lines": lines, "desc": "用户添加"}
    json.dump(user, open(_USER_DESIGNS_FILE, "w"), indent=2, ensure_ascii=False)
    KNOWN_DESIGNS[name] = user[name]
    print(f"  ✅ 已保存到 {_USER_DESIGNS_FILE}")

KNOWN_DESIGNS = {
    "gcd": {
        "name": "GCD (最大公约数)",
        "rtl": _resolve_rtl("rtl/gcd.v"),
        "lines": 757,
        "desc": "状态机+数据通路, 32-bit输入, 16-bit输出",
    },
    "aes": {
        "name": "AES-128 加密核心",
        "rtl": _resolve_rtl("rtl/aes_cipher_top.v"),
        "lines": 253,
        "desc": "OpenCores开源, 7个模块, 128-bit密钥",
    },
    "uart": {
        "name": "UART 串口收发",
        "rtl": _resolve_rtl("rtl/uart.v"),
        "lines": 113,
        "desc": "Alex Forencich, MIT许可, 3个模块",
    },
}
# 启动时加载用户之前保存的设计
KNOWN_DESIGNS.update(_load_user_designs())

ALL_REQUIREMENTS = ["开源", "低功耗", "面积", "快速", "新手", "签核", "极致", "AI训练"]
ALL_TECHS = ["sky130", "ASAP7", "tsmc3", "tsmc5", "gf22"]


# ═══════════════════════════════════════════════════════════
# 交互式向导
# ═══════════════════════════════════════════════════════════

def interactive():
    """交互式向导: 一步步引导, 每步输入 'b' 返回, 输入 'r' 重来"""
    composer = FlowComposer()
    adapter = Adapter("adapter/config.yaml", "adapter/metric_define.yaml")
    receiver = SnapshotReceiver()
    analyzer = FlowAnalyzer()
    recommender = FlowRecommender()  # 历史知识库 — 驱动 demo/final flow

    print("\n" + "=" * 55)
    print("  IC-Agent-OS  交互式向导")
    print("  提示: 输入 'b' 返回上一步 | 'r' 重新开始 | 回车用默认值")
    print("=" * 55)

    designs = dict(KNOWN_DESIGNS)
    design_name, tech, reqs, freq, area, power, lite = None, None, None, None, None, None, False

    _BUILTIN = {"gcd", "aes", "uart", "picorv32"}

    def show_designs():
        print(f"\n  📋 第一步: 选择设计")
        print(f"  {'─'*40}")
        builtin = {k: v for k, v in designs.items() if k in _BUILTIN}
        user = {k: v for k, v in designs.items() if k not in _BUILTIN}
        i = 0
        for k, v in builtin.items():
            i += 1; print(f"  [{i}] {v['name']:25s} ({v['lines']} 行 RTL)  ← 内置")
        if user:
            print(f"  {'─'*40}")
            for k, v in user.items():
                i += 1; print(f"  [{i}] {v['name']:25s} ({v['lines']} 行 RTL)  ← 已添加")
        print(f"  {'─'*40}")
        print(f"  [0] 自定义设计 (添加新的)")

    TECH_DESC = {
        "sky130": "SkyWater 130nm — 真实开源 PDK, 可流片",
        "ASAP7": "7nm 预测工艺 — ICCAD 竞赛官方, 学术基准",
        "tsmc3": "TSMC 3nm — 商业工艺, 无真实 PDK (仅逻辑选型)",
        "tsmc5": "TSMC 5nm — 商业工艺, 无真实 PDK (仅逻辑选型)",
        "gf22": "GF 22nm — 商业工艺, 无真实 PDK (仅逻辑选型)",
    }

    def show_tech():
        print(f"\n  📋 第二步: 选择工艺")
        for i, t in enumerate(ALL_TECHS, 1):
            print(f"  [{i}] {t:8s} — {TECH_DESC.get(t, '')}")

    REQ_DESC = {
        "开源": "只用开源免费工具 (Yosys/iEDA/OpenROAD)",
        "低功耗": "优先低功耗优化 (clock gating + iPA功耗分析)",
        "面积": "优先面积优化 (高 density placement, 小芯片)",
        "快速": "优先速度, 流程精简 (原型验证, 快速试错)",
        "新手": "最简流程 + 文档丰富的工具 (降低学习成本)",
        "签核": "全商业工具链 (DC+Innovus+PrimeTime+Calibre), tape-out级",
        "极致": "追求最高 PPA, 不计成本 (适合竞赛/高性能场景)",
        "AI训练": "优先选有 execution trace 的工具, 可采集训练数据",
    }

    def show_reqs():
        print(f"\n  📋 第三步: 选择需求 (多选, 逗号分隔)")
        for i, r in enumerate(ALL_REQUIREMENTS, 1):
            print(f"  [{i}] {r:6s} — {REQ_DESC[r]}")

    step = 0
    while step < 7:
        if step == 0:  # 设计
            show_designs()
            c = _ask("  选择", "1", allow_back=False,
                     validate=lambda x: _validate_number(x, 0, len(designs)),
                     err_msg=f"请输入 0~{len(designs)}")
            if c == 'r': step = 0; continue
            if c == '0':
                # 自动递增默认设计名: my_design → my_design2 → my_design3
                base = "my_design"; n = 1; default_dn = base
                while default_dn in designs:
                    n += 1; default_dn = f"{base}{n}"
                dn = _ask("  设计名", default_dn)
                if dn == 'BACK': continue
                print(f"    示例: rtl/gcd.v (内置设计)")
                rtl = _ask("  RTL 文件路径 (必须存在)", "", validate=_validate_file,
                           err_msg="文件不存在或不可读, 请检查路径 (例: ./rtl/gcd.v)")
                if rtl == 'BACK': continue
                lines = sum(1 for _ in open(rtl)) if rtl and os.path.exists(rtl) else "?"
                design_name = dn
                designs[dn] = {"name": dn, "rtl": rtl, "lines": lines, "desc": "自定义"}
                # ── 自动询问是否永久保存 ──
                if rtl and os.path.exists(rtl):
                    if _ask(f"  永久添加到已知设计列表? (Y/n)", "y") != 'n':
                        _save_design(dn, rtl, lines)
            else:
                idx = int(c) - 1 if c.isdigit() else 0
                design_name = list(designs.keys())[idx]
            print(f"  ✅ {designs[design_name]['name']}"); step = 1

        elif step == 1:  # 工艺
            show_tech()
            c = _ask("  选择", "1",
                     validate=lambda x: _validate_number(x, 1, len(ALL_TECHS)),
                     err_msg=f"请输入 1~{len(ALL_TECHS)}")
            if c == 'BACK': step = 0; continue
            if c == 'r': step = 0; continue
            tech = ALL_TECHS[int(c) - 1]
            print(f"  ✅ {tech}"); step = 2

        elif step == 2:  # 需求
            show_reqs()
            c = _ask("  选择", "",
                     validate=lambda x: all(
                         (p.strip().isdigit() and 1 <= int(p.strip()) <= len(ALL_REQUIREMENTS))
                         or p.strip() in ALL_REQUIREMENTS
                         for p in x.split(",")),
                     err_msg=f"必填, 请输入 1~{len(ALL_REQUIREMENTS)} 的数字或关键词, 逗号分隔")
            if c == 'BACK': step = 1; continue
            if c == 'r': step = 0; continue
            reqs = []
            for x in c.split(","):
                x = x.strip()
                if x.isdigit() and 1 <= int(x) <= len(ALL_REQUIREMENTS):
                    reqs.append(ALL_REQUIREMENTS[int(x) - 1])
                elif x in ALL_REQUIREMENTS:
                    reqs.append(x)
            if not reqs: reqs = ["开源"]
            print(f"  ✅ {reqs}"); step = 3

        elif step == 3:  # 目标
            print(f"\n  📋 第四步: 设计目标")
            print(f"  [1] 简单模式 (频率+面积+功耗)")
            print(f"  [2] 高级模式 (PPA 四维全约束)")
            advanced = _ask("  选择", "1", validate=lambda x: x in ("1","2")) == "2"
            if advanced == 'BACK': step = 2; continue

            if not advanced:
                print(f"\n    频率 = 芯片主频 (MHz), 如 200=200MHz")
                print(f"    面积 = 标准单元总面积 (μm²)")
                print(f"    功耗 = 总功耗 (mW)")
                freq = _ask("  频率 MHz", "",
                           validate=lambda x: x.strip() != "" and _validate_float(x, positive=True),
                           err_msg="必填, 请输入正数, 如 100")
                if freq == 'BACK': step = 2; continue
                if freq == 'r': step = 0; continue
                area = _ask("  面积上限 μm² (回车=无限制)", "",
                           validate=lambda x: x=="" or _validate_float(x, positive=True),
                           err_msg="请输入正数或直接回车跳过")
                if area == 'BACK': step = 2; continue
                power = _ask("  功耗上限 mW (回车=无限制)", "",
                            validate=lambda x: x=="" or _validate_float(x, positive=True),
                            err_msg="请输入正数或留空")
                if power == 'BACK': step = 2; continue
            else:
                print(f"\n  ══ PPA 四维约束 (回车跳过则无约束) ══")
                print(f"  [Performance] 时序约束:")
                freq = _ask("    频率 MHz", "",
                           validate=lambda x: x.strip() != "" and _validate_float(x, positive=True),
                           err_msg="必填, 请输入正数")
                if freq == 'BACK': step = 2; continue
                wns = _ask("    WNS 目标 ns (>0 即无违规, 回车跳过)", "",
                          validate=lambda x: x=="" or _validate_float(x))
                if wns == 'BACK': step = 2; continue
                tns = _ask("    TNS 目标 ns (回车跳过)", "",
                          validate=lambda x: x=="" or _validate_float(x))
                if tns == 'BACK': step = 2; continue
                print(f"\n  [Area] 面积约束:")
                area = _ask("    单元面积上限 μm² (回车=无限制)", "",
                           validate=lambda x: x=="" or _validate_float(x, positive=True))
                if area == 'BACK': step = 2; continue
                util = _ask("    利用率上限 % (如 65, 回车跳过)", "",
                           validate=lambda x: x=="" or _validate_float(x, positive=True))
                if util == 'BACK': step = 2; continue
                print(f"\n  [Power] 功耗约束:")
                power = _ask("    总功耗上限 mW (回车=无限制)", "",
                            validate=lambda x: x=="" or _validate_float(x, positive=True))
                if power == 'BACK': step = 2; continue
                leak = _ask("    泄漏功耗上限 mW (回车跳过)", "",
                           validate=lambda x: x=="" or _validate_float(x, positive=True))
                if leak == 'BACK': step = 2; continue
                print(f"\n  [Routing] 布线约束:")
                cong = _ask("    最大拥塞率 % (如 80, 回车跳过)", "",
                           validate=lambda x: x=="" or _validate_float(x, positive=True))
                if cong == 'BACK': step = 2; continue
                drc = _ask("    要求 DRC 零违规? (y/N)", "n")
                if drc == 'BACK': step = 2; continue
                goals["wns"] = float(wns) if wns else 0
                goals["tns"] = float(tns) if tns else 0
                if util: goals["utilization"] = float(util)
                if leak: goals["leakage_max"] = float(leak)
                if cong: goals["congestion_max"] = float(cong)
                if drc.lower() == 'y': goals["drc"] = True

            print(f"  ✅ freq={freq}MHz" + (f" area≤{area}" if area else "")
                  + (f" power≤{power}mW" if power else ""))
            step = 4

        elif step == 4:  # 流程深度
            print(f"\n  📋 第五步: 流程深度")
            print(f"  [1] 完整  (9步: synth→fp→tapcell→pdn→gplace→dplace→cts→groute→droute)")
            print(f"            STA 嵌入 cts/droute 中; resize/filler/gds 未验证")
            print(f"  [2] 精简  (2步: synthesis+droute)  ← 仅频率目标, 快速迭代")
            c = _ask("  选择", "1",
                     validate=lambda x: x in ("1","2"),
                     err_msg="请输入 1 或 2")
            if c == 'BACK': step = 3; continue
            if c == 'r': step = 0; continue
            lite = c == "2"
            print(f"  ✅ {'精简' if lite else '完整'}"); step = 5

        elif step == 5:  # 生成+展示 Flow
            goals = {"frequency": float(freq)} if freq and freq != "" else {}
            if area and area != "": goals["area_max"] = float(area)
            if power and power != "": goals["power_max"] = float(power)
            # 用户明确选了"完整"但没填面积/功耗 → 传信号强制完整流程
            if not lite and not area and not power:
                goals["_force_full"] = True

            flow = composer.compose(design=design_name, technology=tech,
                                    requirements=reqs, goals=goals, fast_mode=lite,
                                    history=recommender)
            print(f"\n{'═'*55}")
            print(f"  🎯 Flow: {flow.summary()}  ({len(flow.steps)} 步)")
            print(f"{'═'*55}")
            for i, s in enumerate(flow.steps):
                alts = composer.list_alternatives(flow, s.stage)
                alt_str = ", ".join(a['tool'] for a in alts[:2]) or "—"
                print(f"  {i+1}. [{s.stage:12s}] {s.primary_tool:18s}  可换: {alt_str}")

            # 是否满意? 不满可以 b 回去重选
            print(f"\n  满意这个方案吗?")
            print(f"  [回车] 继续  [b] 返回重选需求  [r] 全部重来")
            c = _ask("  选择", "ok")
            if c == 'BACK': step = 2; continue
            if c == 'r': step = 0; continue
            step = 6

        elif step == 6:  # 替换+执行
            print(f"\n  📋 第六步: 需要替换工具吗?")
            if _ask("  替换? (y/N)", "n") == 'y':
                stage = _ask("  哪个阶段? (STA/DRC...)", "")
                tool = _ask("  换成什么? (OpenSTA/Yosys...)", "")
                if stage != 'BACK' and tool != 'BACK':
                    sw = composer.swap_tool(flow, stage, tool)
                    if sw: flow = sw; print(f"  ✅ {flow.summary()}")
                    else: print(f"  ❌ {tool} 不支持 {stage}")

            print(f"\n  📋 第七步: 执行模式")
            print(f"  [1] 执行一遍 — 跑完就停, 看结果自己判断")
            print(f"  [2] 迭代优化 — 自动诊断→建议→重跑, 直到达标或达到上限")
            c = _ask("  选择", "1",
                     validate=lambda x: x in ("1","2"),
                     err_msg="请输入 1 或 2")
            if c == 'BACK': step = 5; continue
            if c == 'r': step = 0; continue

            # 执行
            work = "/tmp/ic_flow_interactive"; os.makedirs(work, exist_ok=True)
            prev_netlist = None  # 追踪 Yosys 网表
            prev_def = None      # 追踪 DEF 链
            sta_result = None    # 探索轮 STA 结果 (供 record 使用)

            # 从 RTL 提取真正的顶层模块名 (Yosys 和 OpenROAD 都需要这个名字)
            rtl_path = designs[design_name].get("rtl", f"{work}/{design_name}.v")
            top_module = design_name
            if os.path.exists(rtl_path):
                with open(rtl_path) as f:
                    for line in f:
                        m = __import__('re').match(r'module\s+(\w+)', line)
                        if m:
                            top_module = m.group(1); break

            # 12 步 stage → OpenROAD substep 映射
            _STAGE_FLOWS = {
                "floorplan": ["floorplan"], "tapcell": ["tapcell"], "pdn": ["pdn"],
                "gplace": ["gplace"], "resize": ["resize", "sta_report"],
                "dplace": ["dplace"],
                "cts": ["clock_tree_synthesis", "sta_report"],
                "groute": ["groute"], "droute": ["droute", "sta_report", "drc_report"],
                "filler": ["filler"], "gds": ["write_gds"],
            }

            if c in ("1", "2"):
                # ── 5 轮渐进式流程 ──
                full_steps = (["synthesis","floorplan","tapcell","pdn","gplace","resize","dplace","cts","groute","droute","filler","gds"]
                              if not lite else ["synthesis","sta"])
                _STAGE_FLOWS["sta"] = ["sta_report"]
                if lite:
                    ROUNDS = [("精简流程", ["synthesis", "sta"])]
                else:
                    ROUNDS = [
                        ("探索轮: 摸清设计体质", ["synthesis", "sta"]),
                        ("全流程轮: 物理实现基线", [s for s in full_steps if s != "synthesis"]),  # 第2轮复用第1轮网表, 不重新综合
                    ]
                # 动态变量
                round_idx = 0
                failed_stages = set()
                failed_reasons = {}   # stage → error type (供Replanner诊断)
                gate_count = 0
                sta_result = None
                signoff_done = False
                signoff_failed = False  # signoff 失败时跳过修复轮的 failed_stages 空检查
                ppa_corners = {}  # 收集 signoff 多 corner 结果
                # 按设计名动态生成 SDC 路径
                _sdc_path = _resolve_rtl(f"rtl/{design_name}.sdc")
                if not os.path.exists(_sdc_path):
                    _sdc_path = _resolve_rtl("rtl/gcd.sdc")  # fallback

                while round_idx < len(ROUNDS):
                    round_idx += 1
                    round_name, round_steps = ROUNDS[round_idx - 1][:2]
                    # 修复轮(3+) / ECO轮: 诊断 + 展示修复策略
                    if round_idx >= 4 and not failed_stages and not signoff_failed:
                        break
                    actual_steps = list(round_steps)
                    if round_idx >= 4 and failed_stages:
                        actual_steps = [s for s in round_steps if s in failed_stages]
                        if not actual_steps:
                            print(f"\n    所有失败步骤已修复, 跳过"); continue
                    # 修复轮/ECO轮: 展示诊断和建议
                    is_fix_round = ("修复" in round_name)
                    is_eco_round = ("ECO" in round_name)
                    if is_fix_round or is_eco_round:
                        print(f"\n  ══ {round_name} 诊断 ══")
                        if signoff_failed:
                            print(f"  根因: Sign-off 轮 SLOW corner 时序不满足")
                            print(f"  当前 {goals.get('frequency')}MHz 在 1.4V/-40°C 下无法正常工作")
                            print(f"  修复方案 (按成功率排序):")
                            print(f"    [1] 单元增肥(Upsizing)+插Buffer → 换驱动更强的标准单元")
                            print(f"        如 BUF_X1→BUF_X4, 可砍 5~8ns 延迟 (L1, 重跑 resize+droute)")
                            print(f"        OpenROAD repair_timing 自动完成, 无需手动干预")
                            print(f"    [2] 切换 HS 高速库 → 完整重跑物理流 (L3, 小时级)")
                            print(f"        HD→HS 后单元面积/驱动/延迟全变, 原布局+时钟树全部失效")
                            print(f"        必须 synth→floorplan→place→CTS→route→DRC 全部重跑")
                            print(f"        仅在方案[1]完全无效且项目时间允许(多2-3天)时考虑")
                            print(f"    [3] 降频 → 放宽时序约束 (L0, 秒级, 保底手段, 100%收敛)")
                            new_freq = int(float(goals.get("frequency", 100)) * 0.7)
                            print(f"        建议值: {new_freq}MHz → CLK_PERIOD={round(1000/new_freq,1)}ns")
                            print(f"    [4] 接受当前频率 → 仅验证 TYP corner, 放弃 SLOW/FAST Sign-off")
                            if is_eco_round:
                                c = input(f"  选择方案 [1-4, 回车=1]: ").strip()
                                if c == "2":
                                    print(f"  → 需手动切换 config.yaml 中 PDK 路径为 HS 库, 完整重跑物理流")
                                elif c == "3":
                                    old_f = goals.get("frequency", 100)
                                    goals["frequency"] = new_freq
                                    print(f"  → 频率 {old_f}MHz → {new_freq}MHz")
                                elif c == "4":
                                    print(f"  → 跳过 Sign-off, 仅 TYP corner 验证通过")
                                    signoff_done = True
                                else:  # Upsizing+Buffer: 从 gplace 开始重跑关键路径
                                    print(f"  → 降低 density 0.6→0.5 给 repair_timing 留余量")
                                    goals["place_density"] = 0.5
                                    # 标记需要重跑的步骤: gplace→resize→dplace→cts→groute→droute
                                    eco_fix_steps = ["gplace","resize","dplace","cts","groute","droute"]
                                    failed_stages.update(eco_fix_steps)
                                    print(f"  → 将重跑: {eco_fix_steps}")
                                    print(f"  → resize 含 repair_timing+repair_hold (修Setup→修Hold→STA)")
                        elif failed_stages:
                            print(f"  失败步骤: {failed_stages}")
                            if failed_reasons:
                                print(f"  失败原因: {failed_reasons}")
                            replanner = Replanner()
                            params_to_try = []
                            if "wns" in failed_reasons: params_to_try = ["clock_period", "place_density", "core_utilization"]
                            cheapest = replanner.cheapest_first(params_to_try, full_steps)
                            if cheapest:
                                for param, cost_level, rst in cheapest[:3]:
                                    cost_names = {0:"秒级(STA only)", 1:"分钟(place+)", 2:"分钟(fp+)", 3:"小时(full flow)"}
                                    print(f"    调 {param:20s} → L{cost_level} {cost_names.get(cost_level,'')} | 重跑{len(rst)}步")
                        print()

                    print(f"\n{'═'*55}")
                    print(f"  第 {round_idx} 轮: {round_name}")
                    print(f"  步骤: {actual_steps}")
                    print(f"{'═'*55}")
                    all_ok = True; final_wns = float("nan")
                    for stage_name in actual_steps:
                        if stage_name == "sta":
                            # 探索轮: 必须先有合成网表才能跑 STA
                            if not prev_netlist:
                                print(f"  ⏭️  [STA] 无合成网表, 跳过 (请先跑 synthesis)")
                                continue
                            t00 = time.time()
                            sta_params = {
                                "NETLIST_FILE": prev_netlist,
                                "LIBERTY_PATH": _pdk("liberty"),
                                "TOP_MODULE": top_module, "DESIGN_TOP": top_module,
                            }
                            sta_params.update(goal_to_params(goals, "STA"))
                            sta_result = adapter.run("opensta", design_name.upper(), sta_params, observation_level="1")
                            if isinstance(sta_result, SimError):
                                print(f"  ❌ [STA] OpenSTA: {sta_result.type}")
                                all_ok = False
                            else:
                                receiver.submit_snapshot(sta_result)
                                print(f"  ✅ [STA] OpenSTA {max(0,(time.time()-t00)*1000):.0f}ms")
                                for a in sta_result.artifact_manifest:
                                    label = "STA时序报告" if "timing" in a.logical_name else a.logical_name
                                    print(f"     📤 输出-{label}: {a.source_uri}")
                                # 提取 WNS
                                sm = sta_result.digital_twin.metrics
                                sw = sm.get("sta",{}).get("wns") or sm.get("sta.wns_ns")
                                if sw is not None:
                                    try: final_wns = float(sw)
                                    except: pass
                            continue
                        step_s = flow.get_step(stage_name)
                        if not step_s: continue
                        # gds: 使用独立的 gdstk runner, 不经过 OpenROAD KLayout
                        if stage_name == "gds":
                            adp = "gds"; params["INPUT_DEF"] = prev_def or ""
                            t0=time.time(); result=adapter.run("gds",design_name.upper(),params,observation_level="1")
                            dur=max(0,(time.time()-t0)*1000)
                            if isinstance(result,SimError):
                                print(f"  ❌ [gds] GDS生成: {result.type}"); all_ok=False
                            else:
                                receiver.submit_snapshot(result)
                                print(f"  ✅ [gds] GDS生成 {dur:.0f}ms")
                                for a in result.artifact_manifest:
                                    print(f"     📤 输出-GDS2流片文件: {a.source_uri}")
                            # 追踪 DEF → GDS 跳过 openroad 分支的 prev_def 更新
                            continue
                        adp = (step_s.tool_info.adapter if step_s and step_s.tool_info else "")
                        if not adp or adp not in adapter.backends:
                            print(f"  ⏭️  {step_s.primary_tool}"); continue
                        params = {"TOP_MODULE": top_module, "DESIGN_TOP": top_module}
                        if adp == "digital":
                            params["VERILOG_SRC"] = rtl_path
                            params["LIBERTY_PATH"] = _pdk("liberty")
                            params.update(goal_to_params(goals, "synthesis"))  # freq → CLK_PERIOD
                        elif adp == "openroad":
                            netlist = prev_netlist or ""
                            flows = _STAGE_FLOWS.get(step_s.stage, ["floorplan", "sta_report"])
                            params.update({"NETLIST_FILE":netlist,"SDC_FILE":_sdc_path,
                                          "DIE_AREA":_pdk("die_area") or "0 0 150 150",
                                          "CORE_AREA":_pdk("core_area") or "10 10 140 140","flows":flows})
                            params.update(goal_to_params(goals, "floorplan"))
                            if prev_def and step_s.stage != "floorplan": params["INPUT_DEF"] = prev_def
                        elif adp == "opensta":
                            params.update({"NETLIST_FILE":prev_netlist or "",
                                          "LIBERTY_PATH":_pdk("liberty")})
                            params.update(goal_to_params(goals, "STA"))
                        elif adp == "gds":
                            params["INPUT_DEF"] = prev_def or ""
                        obs = "2" if adp in ("openroad","ieda") else "1"
                        # 输入文件 (保留完整路径)
                        inputs = []
                        if params.get("VERILOG_SRC"): inputs.append(("RTL源码", params['VERILOG_SRC']))
                        if params.get("NETLIST_FILE"): inputs.append(("综合网表", params['NETLIST_FILE']))
                        if params.get("INPUT_DEF") and params.get("INPUT_DEF") != "": inputs.append(("上游版图DEF", params['INPUT_DEF']))
                        if params.get("LIBERTY_PATH"): inputs.append(("时序库", params['LIBERTY_PATH']))
                        print(f"  📥 [{step_s.stage}] {step_s.primary_tool}")
                        for label, path in inputs: print(f"     📥 输入-{label}: {path}")
                        t0=time.time(); result=adapter.run(adp,design_name.upper(),params,observation_level=obs)
                        dur=max(0,(time.time()-t0)*1000)
                        if isinstance(result,SimError):
                            print(f"     ❌ {result.type}")
                            all_ok=False
                        else:
                            receiver.submit_snapshot(result)
                            print(f"     ✅ {dur:.0f}ms")
                            has_def = False
                            _file_labels = {
                                "netlist":"综合网表","floorplan_def":"floorplan版图","tapcell_def":"tapcell版图",
                                "pdn_def":"电源网络版图","gplace_def":"全局布局版图","resize_def":"门级优化版图",
                                "dplace_def":"详细布局版图","cts_def":"时钟树版图","groute_def":"全局布线版图",
                                "droute_def":"详细布线版图","filler_def":"填充单元版图",
                                "timing_report":"STA时序报告","sta_report":"STA时序报告",
                                "gds":"GDS2流片文件",
                            }
                            for a in result.artifact_manifest:
                                label = _file_labels.get(a.logical_name, a.logical_name)
                                print(f"     📤 输出-{label}: {a.source_uri}")
                                if a.source_uri.endswith(".def"): has_def = True
                            # 物理步骤应该产出 DEF，没产出就警告
                            if adp == "openroad" and not has_def:
                                print(f"     ⚠️  未生成 DEF 文件 (设计太小或工具跳过该步骤)")
                            m = result.digital_twin.metrics
                            # 兼容两种 key 格式: {"sta":{"wns":...}} 和 {"sta.wns_ns":...}
                            w = m.get("sta",{}).get("wns") or m.get("sta.wns_ns") or "?"
                            t = m.get("sta",{}).get("tns") or m.get("sta.tns_ns") or "?"
                            if w != "?" and w == w:
                                final_wns = w
                                print(f"     📊 WNS={w:+.2f}ns  TNS={t}")
                                if w < 0:
                                    print(f"     ⚠️  WNS<0 — 需 Optimizer 介入调整参数")
                            if adp == "digital":
                                for a in result.artifact_manifest:
                                    if a.source_uri.endswith(".v"): prev_netlist=a.source_uri
                            elif adp == "openroad":
                                for a in result.artifact_manifest:
                                    if a.source_uri.endswith(".def"): prev_def=a.source_uri
                    wns_fail = (final_wns == final_wns and final_wns < 0)
                    # NaN means STA extraction failed — not a real pass
                    if final_wns != final_wns:
                        all_ok = False
                    if wns_fail:
                        print(f"\n  ❌ 时序未满足 (WNS={final_wns:+.2f}ns < 0)")
                        all_ok = False
                    # Sign-off 轮不参与正常的 pass/fail 判断
                    is_signoff_round = ("signoff" in actual_steps)
                    if not is_signoff_round:
                        if not all_ok:
                            print(f"  ⚠️  第 {round_idx} 轮未通过")
                    round_passed = all_ok and final_wns == final_wns and not wns_fail
                    if not is_signoff_round:
                        print(f"  → 第{round_idx}轮 {'✅ 通过' if round_passed else '❌ 未通过'}")
                    if not is_signoff_round and (not all_ok or wns_fail):
                        failed_stages.update(actual_steps)
                        if wns_fail: failed_reasons["wns"] = f"WNS={final_wns:+.2f}ns"

                    # ══ 历史反馈: 每轮结束都入库 + 诊断 ══
                    if not lite and final_wns == final_wns and not is_signoff_round:
                        if gate_count == 0 and prev_netlist and os.path.exists(prev_netlist):
                            try:
                                with open(prev_netlist) as f:
                                    gate_count = sum(1 for l in f if l.strip().startswith("sky130_"))
                            except: pass
                        user_in = RunInput(design=design_name, technology=tech,
                                          requirements=reqs, goals=goals, fast_mode=lite,
                                          rtl_path=rtl_path)
                        run_type = "demo" if round_idx == 1 else "final"
                        record(user_in, flow, sta_result or None, run_type=run_type,
                               gate_count=gate_count, top_module=top_module)
                        demo_metrics = {"wns": final_wns, "gate_count": gate_count, "passed": round_passed}
                        final_advice = recommender.suggest_final(
                            design_name, tech, goals, demo_metrics=demo_metrics)

                        if round_idx == 1:
                            # 第1轮: 打印诊断 + 裁剪第2轮
                            print(f"\n  {'─'*45}")
                            print(f"  📊 Demo 诊断 — 历史反馈")
                            print(f"  {'─'*45}")
                            print(f"  WNS={final_wns:+.2f}ns | gates≈{gate_count} | {'✅' if round_passed else '❌'}")
                            print(f"  推荐深度: {'⚡精简' if final_advice.recommended_depth=='lite' else '📐完整'}")
                            if final_advice.suggested_skip_steps:
                                print(f"  建议跳过: {', '.join(final_advice.suggested_skip_steps)}")
                            if final_advice.param_advice:
                                for pa in final_advice.param_advice: print(f"  💡 {pa}")
                            for rsn in final_advice.reasoning: print(f"  → {rsn}")
                            if final_advice.suggested_skip_steps:
                                new_full = [s for s in full_steps if s not in set(final_advice.suggested_skip_steps)]
                                if len(new_full) < len(full_steps):
                                    print(f"  ✂️  第2轮裁剪: {len(full_steps)}步 → {len(new_full)}步")
                                    ROUNDS[1] = ("全流程轮: 物理实现基线", new_full)
                        elif not round_passed:
                            # 修复轮/ECO轮未通过: 用历史建议指导下一步
                            print(f"  📊 历史诊断: {final_advice.reasoning[0] if final_advice.reasoning else '建议调整参数'}")
                            if final_advice.param_advice:
                                for pa in final_advice.param_advice: print(f"  💡 {pa}")

                    # ══ 第1轮结束: 重新 compose ══
                    if round_idx == 1 and not lite and final_wns == final_wns:
                        diagnosis_goals = dict(goals)
                        diagnosis_goals["_force_full"] = True
                        flow = composer.compose(design=design_name, technology=tech,
                                                requirements=reqs, goals=diagnosis_goals,
                                                fast_mode=False, history=recommender,
                                                diagnosis={"wns": final_wns, "gate_count": gate_count,
                                                           "passed": round_passed})

                    # ══ 动态追加后续轮 ══
                    if round_idx == len(ROUNDS) and not lite and not signoff_done and not is_signoff_round:
                        if not round_passed and round_idx >= 2:
                            print(f"\n  ⚠️  全流程未通过 → 可追加修复轮")
                            # 用 Replanner 分析: 应该从哪步开始重跑?
                            if failed_reasons:
                                print(f"  失败原因: {failed_reasons}")
                                replanner = Replanner()
                                params_to_try = []
                                if "wns" in failed_reasons:
                                    params_to_try = ["clock_period", "place_density", "core_utilization"]
                                cheapest = replanner.cheapest_first(params_to_try, full_steps)
                                if cheapest:
                                    cheapest_param, cost_level, rerun_steps = cheapest[0]
                                    cost_names = {0:"秒级(STA only)",1:"分钟(place+)",2:"分钟(fp+)",3:"小时(full flow)"}
                                    print(f"  Replanner: 建议先调 {cheapest_param} (L{cost_level} {cost_names.get(cost_level,'')})")
                                    print(f"  最小重跑: {rerun_steps}")
                            if input(f"  添加修复轮(重跑失败步骤)? [Y/n]: ").strip().lower() != 'n':
                                ROUNDS.append(("修复轮: 重跑失败步骤", list(full_steps)))
                            if input(f"  添加ECO轮(调参数字段增量重跑)? [Y/n]: ").strip().lower() != 'n':
                                ROUNDS.append(("ECO轮: 参数微调+增量重跑", list(full_steps)))
                            signoff_done = True  # signoff appended below
                        # 追加 Sign-off 轮
                        if input(f"  添加 Sign-off 轮(多corner STA)? [Y/n]: ").strip().lower() != 'n':
                            ROUNDS.append(("Sign-off轮: 多Corner STA", ["signoff"]))
                            signoff_done = True
                        if len(ROUNDS) > round_idx:
                            print(f"  → 已追加 {len(ROUNDS)-round_idx} 轮: {[r[0] for r in ROUNDS[round_idx:]]}")

                    # ══ Sign-off 轮: 多 Corner STA ══
                    if signoff_done and round_idx == len(ROUNDS):
                        last_round = ROUNDS[-1]
                        if last_round[1] == ["signoff"] and prev_netlist:
                            print(f"\n  ══ Sign-off 轮: 多 Corner STA ══")
                            # 跑 TYP corner (不依赖 round2 的 final_wns)
                            corner_results = {}
                            all_corners = [("TYP", _pdk("liberty"))] + \
                                         [(c, _pdk(f"{c.lower()}_lib")) for c in ["SLOW", "FAST"]]
                            for corner, lib in all_corners:
                                lib = _pdk(f"{corner.lower()}_lib")
                                print(f"    {corner} corner: {lib}")
                                cp = {"NETLIST_FILE": prev_netlist, "LIBERTY_PATH": lib,
                                      "TOP_MODULE": top_module, "DESIGN_TOP": top_module}
                                cp.update(goal_to_params(goals, "STA"))
                                r = adapter.run("opensta", design_name.upper(), cp, observation_level="1")
                                if isinstance(r, SimError):
                                    corner_results[corner] = float("-inf")
                                    print(f"    ❌ {corner}: {r.type}")
                                else:
                                    receiver.submit_snapshot(r)
                                    wns_raw = (r.digital_twin.metrics.get("sta",{}).get("wns")
                                               or r.digital_twin.metrics.get("sta.wns_ns"))
                                    try:    wns = float(wns_raw)
                                    except (TypeError, ValueError): wns = float("-inf")
                                    corner_results[corner] = wns
                                    print(f"    {'✅' if wns >= 0 else '❌'} {corner} WNS={wns:+.2f}ns")
                            ppa_corners = dict(corner_results)
                            all_pass = all(v >= 0 for v in corner_results.values())
                            print(f"\n  {'🎉 Sign-off 通过!' if all_pass else '❌ Sign-off 失败 — 时序不满足'}")
                            for c,w in corner_results.items(): print(f"    {c:6s} WNS={w:+.2f}ns")
                            if all_pass:
                                break  # 全部通过, 流程结束
                            else:
                                # Sign-off 失败 → 允许回到修复
                                signoff_failed = True
                                print(f"\n  Sign-off 未通过, 可返回修复:")
                                signoff_done = False  # 允许重新追加 signoff
                                if input(f"  添加修复轮(重跑失败步骤)? [Y/n]: ").strip().lower() != 'n':
                                    ROUNDS.append(("修复轮: 重跑失败步骤", list(full_steps)))
                                if input(f"  添加ECO轮(调参数+增量重跑)? [Y/n]: ").strip().lower() != 'n':
                                    ROUNDS.append(("ECO轮: 参数微调+增量重跑", list(full_steps)))

                    # ══ 继续下一轮 ══
                    if round_idx < len(ROUNDS):
                        print(f"\n  下一轮: {ROUNDS[round_idx][0]} ({len(ROUNDS[round_idx][1])}步)")
                        if input(f"  继续? [Y/n]: ").strip().lower() == 'n': break
                    else:
                        break  # 无更多轮次
            _st = receiver.store
            step = 7
    # ── PPA 汇总 ──
    print(f"\n{'═'*55}")
    print(f"  📊 最终 PPA 汇总 — {design_name} @ {goals.get('frequency','?')}MHz, {tech}")
    print(f"{'═'*55}")
    print(f"  Performance:")
    if ppa_corners:
        for c in ["TYP","SLOW","FAST"]:
            w = ppa_corners.get(c)
            if w is not None and w == w:
                print(f"    {c:5s} WNS={w:+.2f}ns  {'✅' if w>=0 else '❌'}")
    elif final_wns == final_wns:
        print(f"    TYP  WNS={final_wns:+.2f}ns  {'✅' if final_wns>=0 else '❌'}")
    print(f"  Area:")
    print(f"    Gate Count: {gate_count} cells" if gate_count else f"    Gate Count: 未统计")
    die = _pdk("die_area") or "未配置"
    core = _pdk("core_area") or "未配置"
    print(f"    Die Area:   {die}")
    print(f"    Core Area:  {core}")
    gds_files = []
    for d in ["tmp/gds_runs","tmp/openroad_runs"]:
        if os.path.isdir(d):
            for root,_,files in os.walk(d):
                for f in files:
                    if f.endswith(".gds"): gds_files.append(os.path.join(root,f))
    if gds_files:
        latest = max(gds_files, key=os.path.getmtime)
        print(f"  GDS2: {latest} ({os.path.getsize(latest)//1024}KB)")
    print(f"{'═'*55}")
    print(f"  数据库:        {_st.db_path}")
    print(f"  快照 JSON:     {_st.snapshots_dir}/<snap_id>/snapshot.json")
    print(f"  产物文件副本:  {_st.snapshots_dir}/<snap_id>/artifacts/")
    print(f"  原始工作目录:  tmp/digital_runs/  |  tmp/openroad_runs/  |  tmp/ieda_runs/")
    print(f"  查看历史:      python3 cli.py history")
    print(f"{'═'*55}\n")


# ═══════════════════════════════════════════════════════════
# 命令行命令 (非交互式)
# ═══════════════════════════════════════════════════════════

def cmd_compose(args):
    c = FlowComposer()
    reqs = args.requirements or ["开源"]
    if len(reqs) == 1 and "," in reqs[0]:
        reqs = [r.strip() for r in reqs[0].split(",")]
    goals = _parse_goals(args.goals)
    flow = c.compose(design=args.design, technology=args.tech,
                     requirements=reqs, goals=goals, fast_mode=args.lite)
    print(f"\n{'═'*55}")
    print(f"  Flow Solution: {flow.summary()}")
    print(f"{'═'*55}")
    print(f"  名称: {flow.name}")
    print(f"  设计: {flow.design} @ {flow.technology}")
    print(f"  模式: {flow.description}")
    print(f"  步数: {len(flow.steps)}")
    print(f"\n  {'步骤':5s} {'阶段':12s} {'工具':18s} {'可替换为'}")
    print(f"  {'─'*5} {'─'*12} {'─'*18} {'─'*25}")
    for i, s in enumerate(flow.steps, 1):
        alts = c.list_alternatives(flow, s.stage)
        alt_str = ", ".join(a['tool'] for a in alts[:2]) or "—"
        print(f"  {i:<5d} {s.stage:12s} {s.primary_tool:18s} {alt_str}")
        if s.stage in ("resize", "cts", "droute"):
            print(f"       └─ 含 STA checkpoint")
    if flow.warnings:
        print(f"\n  ⚠️  兼容性 ({len(flow.warnings)} 条):")
        for w in flow.warnings[:3]: print(f"      {w}")
    print(f"\n  💡 建议 ({len(flow.recommendations)} 条):")
    for r in flow.recommendations: print(f"      {r}")
    print(f"\n  {'─'*55}")
    print(f"  Flow 的生命周期:")
    print(f"    第1次: 全量 12 步, 建立基线")
    print(f"    第2次: 诊断不达标 → 只重跑受影响的步骤")
    print(f"           例: 调 clock_period → 只重跑 synthesis+droute (L0 秒级)")
    print(f"           例: 调 core_utilization → 重跑 fp~gds 10步 (L2 分钟级)")
    print(f"    第3次: 参数穷举仍失败 → human breakpoint, 输出诊断")

def cmd_status(args):
    from composer.tool_registry import get_all_tools
    import shutil
    adapter = Adapter("adapter/config.yaml", "adapter/metric_define.yaml")
    print(f"{'工具':18s} {'Adapter':12s} {'二进制':12s} {'状态'}")
    print(f"{'─'*18} {'─'*12} {'─'*12} {'─'*15}")
    for name, info in sorted(get_all_tools().items()):
        adp = info.adapter
        has_adp = adp in adapter.backends if adp else False
        bin_ok = False
        if has_adp:
            for attr in ('yosys_path','openroad_bin','opensta_bin','ieda_bin','simulator_path','tool_path'):
                bp = getattr(adapter.backends[adp], attr, '')
                if bp and (os.path.exists(bp) or shutil.which(bp)):
                    bin_ok = True; break
        if not has_adp: status = "❌ 无Adapter"
        elif not bin_ok: status = "❌ 缺二进制"
        else: status = "✅ 可用"
        print(f"  {name:18s} {adp:12s} {'✅' if bin_ok else '❌':12s} {status}")

def cmd_clear(args):
    """ic-flow clear — 清空所有历史快照"""
    receiver = SnapshotReceiver()
    store = receiver.store
    if not args.yes:
        ans = input(f"  确认删除全部 {store.stats()['total_runs']} 条记录? [y/N]: ").strip().lower()
        if ans != 'y':
            print("  已取消。")
            return
    # 删除 SQLite 数据库
    if os.path.exists(store.db_path):
        os.remove(store.db_path)
    import shutil
    if os.path.exists(store.snapshots_dir):
        shutil.rmtree(store.snapshots_dir)
    print(f"  ✅ 已清空。下次运行会自动重建数据库。")


def cmd_history(args):
    receiver = SnapshotReceiver()
    runs = receiver.store.list_all(limit=args.n)
    if not runs:
        print("暂无运行记录。")
        return
    print(f"{'snapshot_id':22s} {'tool':12s} {'level':6s} {'type':12s} {'design'}")
    for r in runs:
        print(f"{r.get('snapshot_id','')[:20]:22s} {r.get('tool',''):12s} "
              f"L{r.get('observation_level',''):5s} {r.get('snapshot_type',''):12s} "
              f"{r.get('design_name','')}")

def cmd_swap(args):
    c = FlowComposer()
    flow = c.compose(design=args.design, technology=args.tech, requirements=["开源"], goals={"frequency":100})
    print(f"原 flow: {flow.summary()}")
    swapped = c.swap_tool(flow, args.stage, args.tool)
    if swapped:
        print(f"新 flow: {swapped.summary()}")
        if swapped.warnings:
            for w in swapped.warnings[:3]: print(f"  ⚠️  {w}")


# ═══════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════

def main():
    p = argparse.ArgumentParser(description="ic-flow — IC 设计流程工具")
    sub = p.add_subparsers(dest="cmd")
    pc = sub.add_parser("compose", help="只出方案, 不执行")
    pc.add_argument("requirements", nargs="*", default=["开源"])
    pc.add_argument("--design", "-d", default="gcd"); pc.add_argument("--tech", "-t", default="sky130")
    pc.add_argument("--goals", "-g", nargs="*"); pc.add_argument("--lite", action="store_true")
    pr = sub.add_parser("run", help="生成方案 + 执行"); pr.set_defaults(cmd="run")
    pr.add_argument("requirements", nargs="*", default=["开源"])
    pr.add_argument("--design", "-d", default="gcd"); pr.add_argument("--tech", "-t", default="sky130")
    pr.add_argument("--goals", "-g", nargs="*"); pr.add_argument("--rtl", "-r")
    pr.add_argument("--lite", action="store_true")
    po = sub.add_parser("optimize", help="迭代优化"); po.set_defaults(cmd="optimize")
    po.add_argument("requirements", nargs="*", default=["开源"])
    po.add_argument("--design", "-d", default="gcd"); po.add_argument("--tech", "-t", default="sky130")
    po.add_argument("--goals", "-g", nargs="*"); po.add_argument("--rtl", "-r")
    po.add_argument("--max-iter", "-n", type=int, default=10); po.add_argument("--yes", "-y", action="store_true")
    ps = sub.add_parser("swap", help="替换工具"); ps.add_argument("stage"); ps.add_argument("tool")
    ps.add_argument("--design", "-d", default="gcd"); ps.add_argument("--tech", "-t", default="sky130")
    sub.add_parser("status", help="工具状态")
    ph = sub.add_parser("history", help="历史记录"); ph.add_argument("-n", type=int, default=20)
    pcl = sub.add_parser("clear", help="清空所有历史快照"); pcl.add_argument("-y", "--yes", action="store_true", help="跳过确认")

    args = p.parse_args()
    if not args.cmd:
        interactive()
        return
    if args.cmd == "compose": cmd_compose(args)
    elif args.cmd == "run": cmd_compose(args)
    elif args.cmd == "swap": cmd_swap(args)
    elif args.cmd == "status": cmd_status(args)
    elif args.cmd == "history": cmd_history(args)
    elif args.cmd == "clear": cmd_clear(args)

if __name__ == "__main__":
    main()
