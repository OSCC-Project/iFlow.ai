#!/usr/bin/env python3
"""
demo_full.py —— IC-Agent-OS 完整功能演示

展示: 8个场景，覆盖所有核心功能
  A. 关键词模式: 低功耗+开源 → 生成Flow → 执行 → State入库
  B. 目标驱动模式: PPASpec → 声明目标不声明步骤
  C. 工具替换: swap STA → 重新执行
  D. 闭环诊断: close_loop → 检查→建议重跑
  E. 多场景对比: 开源vs签核vs新手vs极致
  F. 代价感知重跑: Replanner 演示
  G. 观测能力展示: artifact→metric→object→execution
  H. 汇总: State DB查询

运行: python3 demo/demo_full.py
"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from composer.flow_composer import FlowComposer
from composer.goals import PPASpec
from composer.replanner import Replanner
from composer.analyzer import FlowAnalyzer
from adapter.adapter import Adapter
from adapter.contract import SnapshotPackage, SimError
from state import SnapshotReceiver


def H(title): print(f"\n{'─'*55}\n  {title}\n{'─'*55}")


def main():
    composer = FlowComposer()
    adapter = Adapter("adapter/config.yaml", "adapter/metric_define.yaml")
    receiver = SnapshotReceiver()
    replanner = Replanner()
    analyzer = FlowAnalyzer()

    print("=" * 55)
    print("  IC-Agent-OS  完整功能演示")
    print("=" * 55)
    print(f"  6 Backends: {list(adapter.backends.keys())}")

    # ═══════════════ A. 关键词模式 ═══════════════
    H("A. 关键词模式: 需求→Flow→执行→入库")
    flow = composer.compose(
        design="gcd", technology="sky130",
        requirements=["低功耗", "开源"],
        goals={"frequency": 200, "area_max": 100000},
    )
    print(f"  Flow: {flow.summary()}")
    print(f"  模式: {flow.description}")
    print(f"  步骤:")
    for i, s in enumerate(flow.steps):
        alts = composer.list_alternatives(flow, s.stage)
        a_names = [x['tool'] for x in alts[:2]]
        print(f"    {i+1}. [{s.stage:12s}] {s.primary_tool:18s}  "
              f"可换: {a_names}" if a_names else f"    {i+1}. [{s.stage:12s}] {s.primary_tool}")
    print(f"  建议: {flow.recommendations[1]}")
    if flow.warnings: print(f"  ⚠️  {(flow.warnings[0])}")

    # 执行已接入的工具
    print(f"\n  执行可用步骤:")
    import tempfile
    work = tempfile.mkdtemp(prefix="demo_")
    with open(f"{work}/gcd.v","w") as f:
        f.write("module gcd(clk,a,b,r);input clk;input[7:0]a,b;output reg[7:0]r;always@(posedge clk)r<=a+b;endmodule")
    for step in flow.steps:
        adp = step.tool_info.adapter if step.tool_info else ""
        if adp not in adapter.backends: continue
        if adp == "ieda": print(f"    ⏭️  iEDA (skip, verified separately)"); continue
        t0 = time.time()
        params = {"TOP_MODULE":"gcd","DESIGN_TOP":"gcd"}
        if adp == "openroad":
            params.update({"NETLIST_FILE":"/home/xu/OpenROAD-ae191807/test/gcd_sky130hd.v",
                "SDC_FILE":"/home/xu/OpenROAD-ae191807/test/gcd_sky130hd.sdc",
                "DIE_AREA":"0 0 150 150","CORE_AREA":"10 10 140 140",
                "flows":["floorplan","sta_report"]})
        elif adp == "digital":
            params["VERILOG_SRC"] = f"{work}/gcd.v"
        r = adapter.run(adp,"GCD",params)
        dur = (time.time()-t0)*1000
        if isinstance(r,SimError): print(f"    ❌ {step.primary_tool}: {r.type}")
        else:
            receiver.submit_snapshot(r)
            cap = r.capability
            print(f"    ✅ {step.primary_tool:18s} {dur:5.0f}ms  "
                  f"L{r.header.observation_level}  obj={getattr(cap,'object_delta',False)}  exec={getattr(cap,'execution_trace',False)}")
    import shutil; shutil.rmtree(work, ignore_errors=True)

    # ═══════════════ B. 目标驱动模式 ═══════════════
    H("B. 目标驱动: 声明目标, 不声明步骤")
    goal = PPASpec.parse({"timing":{"wns":">0"},"area":{"utilization":"<65%"}})
    print(f"  用户声明: WNS>0, utilization<65%")
    flow_goal = composer.compose("gcd","sky130",ppa_spec=goal)
    print(f"  Composer 生成: {flow_goal.summary()}")
    print(f"  模式: {flow_goal.description}")

    # ═══════════════ C. 工具替换 ═══════════════
    H("C. 工具替换: STA OpenROAD→OpenSTA")
    print(f"  替换前: {flow.summary()}")
    swapped = composer.swap_tool(flow, "STA", "OpenSTA")
    if swapped:
        print(f"  替换后: {swapped.summary()}")
        print(f"  ⚠️  {swapped.warnings[0][:70]}..." if swapped.warnings else "  ✅ 无兼容性警告")
    # 重新执行 OpenSTA
    r_sta = adapter.run("opensta","GCD",{"TOP_MODULE":"gcd","DESIGN_TOP":"gcd",
        "NETLIST_FILE":"/home/xu/OpenROAD-ae191807/test/gcd_sky130hd.v",
        "LIBERTY_PATH":"/home/xu/OpenROAD-ae191807/test/sky130hd/sky130hd_tt.lib",
        "SDC_FILE":"/home/xu/OpenROAD-ae191807/test/gcd_sky130hd.sdc"})
    if not isinstance(r_sta,SimError):
        print(f"  ✅ OpenSTA 执行成功")
        receiver.submit_snapshot(r_sta)

    # ═══════════════ D. 闭环诊断 ═══════════════
    H("D. 闭环: 诊断→检查Goal→建议重跑")
    metrics_sample = {"sta": {"wns": -0.03, "tns": -5}, "area": {"cell_area": 150000}}
    loop = composer.close_loop(flow, metrics_sample, ppa_spec=goal)
    print(f"  诊断评分: {loop['diagnosis'].score:.0f}/100  通过: {loop['passed']}")
    print(f"  下一步: {loop['next_action']}")
    if loop.get('rerun_plan'):
        for param, level, steps in loop['rerun_plan'][:4]:
            cost = {0:"秒级",1:"分钟",2:"分钟",3:"小时"}.get(level,"?")
            print(f"    {param:20s} L{level}({cost}) → 重跑{len(steps)}步")

    # ═══════════════ E. 多场景对比 ═══════════════
    H("E. 多场景 Flow 对比")
    for label, reqs, g in [
        ("新手+开源", ["新手","开源"], {"frequency":100}),
        ("低功耗IoT", ["低功耗"], {"power_max":3,"frequency":100}),
        ("签核+极致", ["签核","极致"], {"frequency":800,"area_min":True}),
        ("AI训练", ["AI训练","开源"], {"frequency":300}),
        ("面积+低功耗", ["面积","低功耗"], {}),
    ]:
        f = composer.compose("gcd","sky130",requirements=reqs,goals=g)
        print(f"  {label:15s} → {f.summary()}")

    # ═══════════════ F. 代价感知重跑 ═══════════════
    H("F. Replanner: 参数变化→最小重跑")
    full = ["synthesis","floorplan","placement","CTS","routing","STA","DRC"]
    for param in ["clock_period","core_utilization","place_density","rtl_change","DIE_AREA"]:
        lv, rr = replanner.plan_rerun(param, full)
        skip = len(full)-len(rr)
        cost = {0:"秒",1:"分",2:"分",3:"时"}.get(lv,"?")
        print(f"  {param:20s} L{lv}({cost}级) → 重跑{len(rr)}步, 跳过{skip}步")

    # ═══════════════ G. 观测能力 ═══════════════
    H("G. 四级观测能力")
    tools_info = {"Yosys":"artifact+metric","OpenROAD":"artifact+metric+object+execution",
                  "iEDA":"artifact+metric+object","OpenSTA":"artifact+metric",
                  "ngspice":"artifact+metric+waveform","PrimeTime":"artifact+metric"}
    for t, obs in tools_info.items():
        print(f"  {t:15s} → {obs}")

    # ═══════════════ H. 汇总 ═══════════════
    H("H. State DB 汇总")
    stats = receiver.store.stats()
    print(f"  Total runs: {stats['total_runs']}")
    print(f"  By tool: {stats['by_tool']}")
    print(f"  By level: {stats['by_observation_level']}")
    print(f"  Artifacts: {stats['total_artifacts']} ({stats['total_artifact_bytes']:,} bytes)")
    print(f"\n  最近快照:")
    for r in receiver.store.list_all(5):
        print(f"    {r.get('snapshot_id','')[:20]:20s} [{r.get('tool',''):12s}] L{r.get('observation_level','')}  {r.get('design_name','')}")

    print(f"\n{'='*55}")
    print(f"  演示完成")
    print(f"{'='*55}")


if __name__ == "__main__":
    main()
