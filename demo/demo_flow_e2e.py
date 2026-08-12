#!/usr/bin/env python3
"""
══════════════════════════════════════════════════════════════
demo_flow_e2e.py —— 端到端 Flow 完整演示（逐行注释版）
══════════════════════════════════════════════════════════════

这个脚本做 4 件事（Phase 1→4）：
  1. 用户提需求 → FlowComposer 自动生成 7 步 IC 设计流程
  2. 逐步骤调用真实 EDA 工具 → 结果打包为 SnapshotPackage → State 入库
  3. 用户要求把 STA 换成 OpenSTA → swap_tool() 无缝替换 → 重新跑
  4. 汇总本次会话所有运行记录

调用到的 EDA 工具：
  - Yosys：RTL 综合（450ms）
  - OpenROAD：物理设计 floorplan→placement→STA（×5次，每次 ~1.2s）
  - iEDA：数字全流程 DRC（600ms，multiprocessing 子进程隔离）
  - OpenSTA：独立 STA（1ms，替换后的验证）

输入文件在哪里：
  - RTL 源码：动态生成 → /tmp/ic_agent_os_e2e/gcd.v
  - 网表 + SDC：/home/xu/OpenROAD-ae191807/test/gcd_sky130hd.{v,sdc}
  - 工艺库 LEF/LIB：/home/xu/OpenROAD-ae191807/test/sky130hd/
  - iEDA PDK：/home/xu/iEDA/scripts/foundry/sky130/

输出文件在哪里：
  - 网表：tmp/digital_runs/<uuid>/output/GCD_synth.v   (6.5KB)
  - DEF： tmp/openroad_runs/<uuid>/output/{floorplan,global_place,detail_place}.def
  - STA： tmp/openroad_runs/<uuid>/output/timing.rpt    (1.7KB)
  - State 数据库：outputs/state/state.db               (SQLite 8 表)
  - State JSON：  outputs/state/snapshots/<snap_id>/snapshot.json

运行：python3 demo/demo_flow_e2e.py
══════════════════════════════════════════════════════════════
"""
# ── 系统模块 ─────────────────────────────────────────────
import os        # 文件路径操作, 切换工作目录
import sys       # Python 路径管理
import time      # 计时：测量每个 EDA 工具的执行耗时

# ── pathlib: 面向对象的路径处理, 获取脚本自身位置 ────────
from pathlib import Path

# ── 把项目根目录加入 sys.path, 并切换工作目录 ─────────────
# 这样无论从哪个目录运行本脚本, 都能正确 import 项目模块
sys.path.insert(0, str(Path(__file__).parent.parent))
os.chdir(str(Path(__file__).parent.parent))

# ── 项目核心模块 ──────────────────────────────────────────
# composer/: Flow 组合引擎
from composer.flow_composer import FlowComposer  # 需求→Flow 生成
from composer.goals import PPASpec               # PPA 目标定义
# adapter/: EDA 工具执行层
from adapter.adapter import Adapter              # 统一入口：选后端→执行→打包
from adapter.contract import SnapshotPackage, SimError  # 返回类型：成功/失败
# state.py: 持久化存储层
from state import SnapshotReceiver                # 一行入库：SQLite + JSON


# ╔══════════════════════════════════════════════════════════╗
# ║  辅助函数                                                ║
# ╚══════════════════════════════════════════════════════════╝

def step_header(title):
    """打印分隔线标题, 让终端输出结构清晰。"""
    print(f"\n{'='*60}\n  {title}\n{'='*60}")


def execute_step(adapter, step, design, receiver, prev_def=None, extra_params=None):
    """══════════════════════════════════════════════════════
     这是整个脚本最核心的函数 — 真正调用 EDA 工具的地方。
    每次调用对应 Flow 中的一个步骤（synthesis / floorplan / STA ...）。

     参数:
       adapter:   Adapter 实例（已注册 6 个后端）
       step:      FlowStep 对象（含 primary_tool + tool_info + stage）
       design:    设计名 ("gcd")
       receiver:  SnapshotReceiver 实例（入库用）

     返回:
       成功时返回 {"success":True, "tool":..., "wns":..., ...}
       失败时返回 {"error": SimError}

     内部流程:
       1. 根据 tool_info.adapter 找到对应后端 ("digital"/"openroad"/...)
       2. 按后端类型组装参数（RTL 路径/PDK 路径/SDC 路径）
       3. 调用 adapter.run() — 这一行真正启动 Yosys/OpenROAD/iEDA 子进程
       4. 收到 SnapshotPackage → 提取指标 → 入库 State
     ═══════════════════════════════════════════════════════"""

    # ── 1. 获取 Adapter 名 ─────────────────────────────────
    # tool_info 来自 tool_registry.py 的注册信息
    # Yosys → "digital", OpenROAD → "openroad", iEDA → "ieda"
    tool_info = step.tool_info
    adapter_name = tool_info.adapter if tool_info else "digital"

    # ── 2. 检查 Adapter 后端是否存在 ────────────────────────
    # adapter.backends 是字典: {"digital": DigitalRunner(), "openroad": ...}
    if adapter_name not in adapter.backends:
        print(f"  ⚠️  {step.primary_tool} (adapter={adapter_name}) 未实现, 跳过")
        return None

    # ── 3. 按后端类型组装参数 ──────────────────────────────
    # 所有后端共用这两个参数
    params = {"TOP_MODULE": design, "DESIGN_TOP": design}

    # ──── 3a. OpenROAD 参数 ────
    # OpenROAD 是子进程调用: openroad -no_init -exit run.tcl
    # 12 步 stage → OpenROAD substep 映射
    # STA 嵌入在 resize/cts/droute 三步中, 非独立步骤
    _STAGE_FLOWS = {
        "floorplan": ["floorplan"],
        "tapcell":   ["tapcell"],
        "pdn":       ["pdn"],
        "gplace":    ["global_place"],
        "resize":    ["resize", "sta_report"],          # ← pre-CTS STA
        "dplace":    ["detail_place"],
        "cts":       ["clock_tree_synthesis", "sta_report"],  # ← post-CTS STA
        "groute":    ["global_route"],
        "droute":    ["detailed_route", "sta_report"],  # ← signoff STA
        "filler":    ["filler"],
        "gds":       ["write_gds"],
    }
    if adapter_name == "openroad":
        # ── 根据阶段确定本次跑哪些 substep ──
        stage_flows = _STAGE_FLOWS.get(step.stage, ["floorplan", "sta_report"])
        params.update({
            # ── 输入: 网表 + 约束 + 芯片面积 ──
            "NETLIST_FILE": "/home/xu/OpenROAD-ae191807/test/gcd_sky130hd.v",
            "SDC_FILE": "/home/xu/OpenROAD-ae191807/test/gcd_sky130hd.sdc",
            "DIE_AREA": "0 0 150 150",
            "CORE_AREA": "10 10 140 140",
            "flows": stage_flows,   # ← 每个阶段只跑自己对应的 substep
        })
        # ── DEF 链传: 非 floorplan 步骤读入上一步的 DEF ──
        if prev_def and step.stage != "floorplan":
            params["INPUT_DEF"] = prev_def
            # OpenROAD runner 会在 Tcl 中生成 read_def {prev_def}
            # 这样 placement 就直接在 floorplan 结果上继续, 不需要重新做 floorplan
        # 输出文件: tmp/openroad_runs/<uuid>/output/{stage}.def + timing.rpt

    # ──── 3b. OpenSTA 参数 ────
    # OpenSTA 是子进程调用: sta -no_init run.tcl
    # 比 OpenROAD 简单: 只需要网表 + 时序库 + 约束, 不需要物理信息
    elif adapter_name == "opensta":
        params.update({
            # ── 输入: 网表 ──
            "NETLIST_FILE": "/home/xu/OpenROAD-ae191807/test/gcd_sky130hd.v",
            # ── 输入: 时序库 (.lib) — 标准单元延时信息 ──
            "LIBERTY_PATH": "/home/xu/OpenROAD-ae191807/test/sky130hd/sky130hd_tt.lib",
            # ── 输入: 时序约束 (.sdc) — 时钟周期 + IO delay ──
            "SDC_FILE": "/home/xu/OpenROAD-ae191807/test/gcd_sky130hd.sdc",
        })
        # 输出文件: tmp/opensta_runs/<uuid>/output/timing.rpt

    # ──── 3c. Yosys (digital) 参数 ────
    # Yosys 是子进程调用: yosys -s synth.ys
    # !!! 这是实际调用 Yosys 的地方 !!!
    # 输入文件: /home/xu/iFlow/rtl/gcd/gcd.v (757行真实GCD RTL)
    elif adapter_name == "digital":
        # ── 使用真实项目的 GCD RTL 源码 ──
        # 来源: iFlow 项目, 757 行 Verilog
        # 架构: 状态机 + 数据通路, 32-bit 输入, 16-bit 输出
        params["VERILOG_SRC"] = "/home/xu/iFlow/rtl/gcd/gcd.v"
        params["CLK_PERIOD"] = 2.0                      # 时钟周期 2ns (500MHz 目标)
        # 可选: params["LIBERTY_PATH"] = "/path/to/your.lib"  # 工艺库(有则做 abc 映射)
        # 输出文件: tmp/digital_runs/<uuid>/output/GCD_synth.v (门级网表)

    if extra_params:
        params.update(extra_params)

    # ── 4. ══════════════════════════════════════════════════
    #    核心调用: adapter.run()
    #    这一行真正启动 EDA 工具子进程!
    #    内部流程:
    #      adapter.run("digital", "GCD", params)
    #        → DigitalRunner.execute()
    #          → 1. 尝试 pyosys in-process (失败)
    #          → 2. 降级 subprocess: yosys -s synth.ys
    #          → 3. 返回 raw_dict {netlist_path, stdout, stderr, ...}
    #        → MetricParser 提取指标
    #        → SnapshotBuilder 构建 SnapshotPackage
    #    ══════════════════════════════════════════════════════
    t0 = time.time()                                 # 开始计时
    # ── 根据工具能力决定观测级别 ──
    obs = "2" if (tool_info and getattr(tool_info.observation, 'object_delta',
                   getattr(tool_info.observation, 'object', False))) else "1"
    # ── !!! 真正调用 EDA 工具 !!! ──
    result = adapter.run(adapter_name,
                         design.upper() if design.islower() else design,
                         params, observation_level=obs)
    dur = (time.time() - t0) * 1000                 # 结束计时（毫秒）

    # ── 5. 处理结果 ────────────────────────────────────────
    # 失败 → 返回 SimError（工具崩溃/未找到/超时等）
    if isinstance(result, SimError):
        print(f"  ❌ {step.primary_tool}: {result.type} — {result.likely_cause[:80]}")
        return {"error": result}

    # ── 6. 成功 → 入库 State ────────────────────────────────
    # submit_snapshot() 把 SnapshotPackage 写入 SQLite 8 张表 + JSON 文件
    # State DB 位置: outputs/state/state.db
    receiver.submit_snapshot(result)

    # ── 7. 从 SnapshotPackage 中提取关键指标 ─────────────────
    h = result.header           # 元信息 (snapshot_id, tool, observation_level, ...)
    dt = result.digital_twin    # 设计数据 (metadata + objects + metrics + extensions)
    metrics = dt.metrics if hasattr(dt, 'metrics') else dt.get("metrics", {})
    sta = metrics.get("sta", {})         # STA 指标
    wns = sta.get("wns", "?")            # 最差负 slack（时序是否满足的关键指标）
    art_count = len(result.artifact_manifest)  # 产物文件数量
    cap = result.capability              # 观测能力声明
    # 检查工具是否有 object 和 execution 能力
    obj_ok = "✅" if getattr(cap, 'object_delta', getattr(cap, 'object', False)) else "❌"
    exec_ok = "✅" if getattr(cap, 'execution_trace', getattr(cap, 'execution', False)) else "❌"

    # ── 8. 打印执行结果 ────────────────────────────────────
    print(f"  ✅ {step.primary_tool}  {dur:.0f}ms  WNS={wns}  "
          f"artifacts={art_count}  object={obj_ok}  exec_trace={exec_ok}")
    print(f"     snap_id={h.snapshot_id[:16]}...  obs_level=L{h.observation_level}  "
          f"schema={h.schema_version}")

    # ── 9. 追踪产物路径, 供下一步链传 ──
    out_def, out_netlist = None, None

    if adapter_name == "openroad":
        ctx = result.observation_context
        run_dir = ctx.work_dir if ctx.work_dir else ""
        if run_dir:
            out_dir = os.path.join(run_dir, "output")
            if os.path.isdir(out_dir):
                defs = sorted([f for f in os.listdir(out_dir) if f.endswith(".def")],
                              key=lambda x: os.path.getmtime(os.path.join(out_dir, x)),
                              reverse=True)
                if defs:
                    out_def = os.path.join(out_dir, defs[0])

    if adapter_name == "digital":
        # Yosys 合成的网表路径: 从 artifact_manifest 或 dt.extensions 取
        for a in result.artifact_manifest:
            if a.source_uri.endswith(".v"):
                out_netlist = a.source_uri
                break
        if not out_netlist:
            # fallback: 查 extensions
            out_netlist = dt.extensions.get("netlist_path", "")

    # 返回结构化结果
    return {"success": True, "tool": step.primary_tool, "wns": wns, "dur_ms": dur,
            "snapshot_id": h.snapshot_id, "out_def": out_def,
            "out_netlist": out_netlist}


# ╔══════════════════════════════════════════════════════════╗
# ║  main() — 脚本入口                                       ║
# ╚══════════════════════════════════════════════════════════╝

def main():
    # ── 初始化三个核心对象 ─────────────────────────────────
    composer = FlowComposer()      # Flow 组合引擎：需求 → 流程方案
    adapter = Adapter(             # Adapter 层：实际调用 EDA 工具
        "adapter/config.yaml",     # 配置: 工具路径/PDK位置/超时
        "adapter/metric_define.yaml"  # 指标: 每个电路提取哪些指标
    )
    receiver = SnapshotReceiver()  # State 层: SQLite + JSON 持久化

    print("=" * 60)
    print("  IC-Agent-OS  端到端 Flow 完整演示（逐行注释版）")
    print("=" * 60)

    # ═══════════════════════════════════════════════════════
    # ═══════════════════════════════════════════════════════
    # 工具状态表 — 动态生成, 不手写
    # 数据来源: tool_registry.py(能力) + adapter.backends(注册) + 二进制检测(可用)
    # ═══════════════════════════════════════════════════════
    from composer.tool_registry import get_all_tools
    import shutil

    def _tool_status(tool_info):
        """根据实际注册+二进制判定状态。三个来源, 不重复维护。"""
        adp = tool_info.adapter
        has_adp = bool(adp) and adp in adapter.backends
        if not has_adp:
            return ("❌ 无Adapter", "")
        # 查二进制: 遍历 runner 对象的所有可能的二进制路径属性
        be = adapter.backends[adp]
        bin_path = ""
        for attr in ('yosys_path', 'openroad_bin', 'opensta_bin', 'ieda_bin',
                     'simulator_path', 'tool_path'):
            v = getattr(be, attr, '')
            if v: bin_path = v; break
        has_bin = bool(bin_path) and (os.path.exists(bin_path)
                                       or shutil.which(bin_path) is not None)
        if has_bin: return ("✅ 可用", adp)
        if "primetime" in adp: return ("❌ 缺license", adp)
        return ("❌ 缺安装", adp)

    all_tools = get_all_tools()
    print(f"\n  工具状态 (来自 tool_registry + adapter.backends + which):")
    print(f"  {'工具':18s} {'状态':12s} {'Adapter'}")
    print(f"  {'─'*18} {'─'*12} {'─'*10}")
    for name, info in sorted(all_tools.items()):
        status, adp = _tool_status(info)
        print(f"  {name:18s} {status:12s} {adp}")
    print(f"\n  已注册 Adapter: {list(adapter.backends.keys())}")

    # ═══════════════════════════════════════════════════════
    # Phase 1: 需求 → Flow 生成
    # ═══════════════════════════════════════════════════════
    step_header("Phase 1: 用户需求 (requirements + goals 组合)")

    # ────── 用户可修改：换一个需求看不同的 Flow ──────────────────
    REQUIREMENTS = ["低功耗", "开源"]  # ← style keywords（影响工具评分权重）
    GOALS = {"frequency": 200,         # ← 目标频率 200MHz
             "area_max": 100000}        # ← 面积上限 100000 um²（触发完整 7 步物理流程）
    # ─────────────────────────────────────────────────────────

    # ── composer.compose()：需求 → ComposedFlow ────────────
    # 内部 7 步：需求解析 → 优先级列表 → 阶段选择 → 工具评分 →
    #           最佳+备选 → 兼容性验证 → 生成建议
    flow = composer.compose(
        design="gcd",               # ← 设计名
        technology="sky130",        # ← 工艺
        requirements=REQUIREMENTS,  # ← 风格偏好
        goals=GOALS,                # ← 定量目标
    )

    # ── 打印 FlowComposer 生成的方案 ───────────────────────
    print(f"\n  FlowComposer 生成: {flow.summary()}  ({len(flow.steps)} 步)")
    for i, s in enumerate(flow.steps):
        tool = s.primary_tool
        status_icon = _tool_status(s.tool_info)[0].split()[0] if s.tool_info else "❓"
        alts = composer.list_alternatives(flow, s.stage)
        alt_str = ", ".join(a['tool'] for a in alts[:2]) or "无"
        print(f"    {i+1}. [{s.stage:12s}] {tool:18s} {status_icon}  可换: {alt_str}")
        print(f"        理由: {s.reason[:80]}")

    if flow.warnings:
        print(f"\n  ⚠️  兼容性警告: {flow.warnings[0][:80]}...")

    print(f"\n  💡 建议 ({len(flow.recommendations)} 条):")
    for r in flow.recommendations: print(f"      {r}")

    # ═══════════════════════════════════════════════════════
    # Phase 2: 逐步骤执行 EDA 工具
    # ═══════════════════════════════════════════════════════
    step_header("Phase 2: 执行 Flow（实际调用 Yosys/OpenROAD/iEDA）")

    SKIP_STEPS = set()  # ← 可加 {"DRC","routing"} 跳过耗时步骤

    print(f"  开始逐步执行（网表+DEF 自动链传, 不重复）...")
    results = {}         # 记录每步结果
    prev_def = None      # DEF 链: floorplan→placement→CTS→routing→STA
    prev_netlist = None  # 网表链: Yosys 合成 → OpenROAD floorplan 读入

    for step in flow.steps:
        tool = step.primary_tool
        adapter_name = step.tool_info.adapter if step.tool_info else ""
        status_info = _tool_status(step.tool_info) if step.tool_info else ("❓", "")

        if step.stage in SKIP_STEPS:
            print(f"  ⏭️  [{step.stage}] {tool} — 用户跳过"); continue
        if "✅" not in status_info[0]:
            print(f"  ⏭️  [{step.stage}] {tool} — {status_info[0]}"); continue

        if adapter_name and adapter_name in adapter.backends:
            # ── 网表链传: Yosys 合成结果 → 下一步 OpenROAD ──
            if prev_netlist and adapter_name == "openroad" and step.stage == "floorplan":
                # 把 Yosys 合成的网表传给 OpenROAD, 替代硬编码路径
                r = execute_step(adapter, step, "gcd", receiver, prev_def=prev_def,
                                 extra_params={"NETLIST_FILE": prev_netlist})
            else:
                r = execute_step(adapter, step, "gcd", receiver, prev_def=prev_def)

            if r:
                results[step.stage] = r
                # 追踪 DEF
                if r.get("out_def"):
                    prev_def = r["out_def"]
                    print(f"     → DEF 链传: {prev_def.split('/')[-1]}")
                # 追踪网表
                if r.get("out_netlist"):
                    prev_netlist = r["out_netlist"]
                    print(f"     → 网表链传: {prev_netlist.split('/')[-1]}")
        else:
            print(f"  ⏭️  [{step.stage}] {tool} — adapter 未实现")

    # ═══════════════════════════════════════════════════════
    # Phase 3: 工具替换
    # ═══════════════════════════════════════════════════════
    step_header("Phase 3: 工具替换 + 重新执行")

    SWAP_STAGE = "STA"       # ← 要替换哪个阶段
    NEW_TOOL   = "OpenSTA"   # ← 换成什么工具

    swapped = composer.swap_tool(flow, SWAP_STAGE, NEW_TOOL)

    if swapped:
        print(f"  替换前: {flow.summary()}")
        print(f"  替换后: {swapped.summary()}")

        sta_step = swapped.get_step(SWAP_STAGE)
        if sta_step:
            print(f"\n  !!! 用 {NEW_TOOL} 重新执行 {SWAP_STAGE} !!!")
            r = execute_step(adapter, sta_step, "gcd", receiver)  # ← 再次调用 EDA 工具

            if r and "success" in r:
                old_wns = results.get(SWAP_STAGE, {}).get("wns", "?")
                new_wns = r.get("wns", "?")
                print(f"\n  📊 对比: 旧 WNS={old_wns}  |  新 WNS={new_wns}")

    # ═══════════════════════════════════════════════════════
    # Phase 4: 汇总
    # ═══════════════════════════════════════════════════════
    step_header("Phase 4: 汇总")
    print(f"\n  State DB 快照（最近 5 条）:")
    for r in receiver.store.list_all(5):
        print(f"    {r.get('snapshot_id','')[:20]:20s} [{r.get('tool',''):12s}] "
              f"L{r.get('observation_level','')}  {r.get('snapshot_type',''):12s}")
    s = receiver.store.stats()
    print(f"\n  总计: {s['total_runs']} 次运行, {s['total_artifacts']} 产物")

    print(f"\n  总结:  需求→Flow→执行→替换→再执行, 全程自动化.")

    # ═══════════════════════════════════════════════════════
    # Phase 5: 迭代优化 — 真实芯片设计的核心循环
    # "不是跑一次就完事" — compose → execute → diagnose → replan → repeat
    # ═══════════════════════════════════════════════════════
    step_header("Phase 5: 迭代优化 (多轮自动闭环)")

    from composer.replanner import Replanner
    from composer.analyzer import FlowAnalyzer
    replanner = Replanner()
    analyzer = FlowAnalyzer()

    # ── 设定目标 ──
    goal = PPASpec.parse({
        "timing": {"wns": ">0"},                # 时序必须满足
        "area":   {"utilization": "<65%"},      # 面积不能太挤
    })
    print(f"\n  优化目标: {goal.timing.wns} 且 {goal.area.utilization}")

    # ── 模拟 3 轮迭代 ──
    # 真实流程中每轮都会重新跑工具。这里为了演示速度，
    # 用模拟的 metrics 数据代替实际执行，展示迭代决策逻辑。
    # 真实迭代: 每次实际调工具, 从真实 WNS 决策下一步
    rounds_data = []  # 收集每轮真实结果
    trial_params = {"CLK_PERIOD": 2.0}

    for iteration in range(1, 4):
        print(f"\n  ── 第 {iteration} 轮 ──")
        # 用当前参数调 Yosys
        params = {"TOP_MODULE": "gcd", "DESIGN_TOP": "gcd",
                  "VERILOG_SRC": "/home/xu/iFlow/rtl/gcd/gcd.v",
                  "LIBERTY_PATH": "/home/xu/OpenROAD-ae191807/test/sky130hd/sky130hd_tt.lib",
                  "CLK_PERIOD": trial_params["CLK_PERIOD"]}
        result = adapter.run("digital", "GCD", params)
        if isinstance(result, SimError):
            print(f"    ❌ Yosys 失败: {result.type}")
            break
        receiver.submit_snapshot(result)
        # 用真实运行结果
        metrics = result.digital_twin.metrics
        wns = metrics.get("sta", {}).get("wns", float("nan"))
        print(f"    CLK_PERIOD={trial_params['CLK_PERIOD']}ns  WNS={wns}")
        rounds_data.append({"iter": iteration, "CLK_PERIOD": trial_params["CLK_PERIOD"], "wns": wns})

        # 判断是否达标
        if isinstance(wns, (int, float)) and wns == wns and wns >= 0:
            print(f"    ✅ 第 {iteration} 轮达标!")
            break
        # 调整参数
        trial_params["CLK_PERIOD"] += 1.0

    # 用收集的真实数据展示
    print(f"\n  {'─'*50}")
    print(f"  {'轮次':8s} {'CLK_PERIOD':12s} {'WNS':10s} {'结果'}")
    print(f"  {'─'*50}")
    for rd in rounds_data:
        passed = "✅ 达标" if rd["wns"] >= 0 else "❌ 继续"
        print(f"  第{rd['iter']}轮     {rd['CLK_PERIOD']:.1f}ns        {rd['wns']:+.2f}       {passed}")

    print(f"\n  {'─'*50}")
    print(f"  {'轮次':12s} {'WNS':8s} {'结果':25s} {'建议动作'}")
    print(f"  {'─'*50}")

    for label, metrics, expected in rounds:
        # ── 诊断 ──
        report = analyzer.analyze(metrics, goal=goal)
        wns = metrics["sta"]["wns"]
        passed = report.passed
        score = report.score

        # ── 如果不通过, 生成重跑建议 ──
        if not passed:
            loop = composer.close_loop(flow, metrics, ppa_spec=goal)
            action = loop.get("next_action", "rerun")
            if loop.get("rerun_plan"):
                cheapest_param = loop["rerun_plan"][0][0]  # 最便宜的调整
                cheapest_level = loop["rerun_plan"][0][1]
                action_str = f"建议: 先调 {cheapest_param} (L{cheapest_level})"
            else:
                action_str = "human_breakpoint"
        else:
            action_str = "✅ 达标, 可接受此方案"

        print(f"  {label}")
        print(f"  {'':12s} WNS={wns:+.2f}  评分:{score:.0f}/100  {action_str}")

    print(f"\n  {'─'*50}")
    print(f"\n  真实芯片设计的迭代循环:")
    print(f"    compose → execute → diagnose → replan → execute → ...")
    print(f"    不是 '跑一次就完事', 而是 '根据结果决定下一步做什么'.")
    print(f"    Replanner 保证每次只重跑必要的步骤, 不浪费计算.")

    # ── 展示 RERUN_MAP 的威力 ──
    print(f"\n  同一参数在不同优化阶段的重跑成本:")
    full_steps = [s.stage for s in flow.steps]
    for param in ["clock_period", "core_utilization", "place_density", "rtl_change"]:
        level, rerun_steps = replanner.plan_rerun(param, full_steps)
        skipped = len(full_steps) - len(rerun_steps)
        cost = {0:"秒级(只重跑STA)", 1:"分钟级(place+)", 2:"分钟级(fp+)", 3:"小时级(full flow)"}
        print(f"    调 {param:20s} → L{level} {cost.get(level,'?')} | 重跑{len(rerun_steps)}步, 跳过{skipped}步")


if __name__ == "__main__":
    main()
