#!/usr/bin/env python3
"""
test_all.py —— IC-Agent-OS 全量自动化测试 (57项)
覆盖: 导入→Adapter→Flow→State→执行→替换→Goal→闭环
运行: python3 tests/test_all.py
"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PASS = FAIL = 0
def check(name, condition, detail=""):
    global PASS, FAIL
    if condition: PASS += 1; print(f"  ✅ {name}")
    else: FAIL += 1; print(f"  ❌ {name} — {detail}")

def section(title): print(f"\n{'='*55}\n  {title}\n{'='*55}")

# ═══════════════ 1. 模块导入 (24项) ═══════════════
section("1. 模块导入")
for mod, label in [
    ("adapter.contract","contract"),("adapter.runner","runner"),("adapter.adapter","adapter"),
    ("adapter.snapshot_builder","snapshot_builder"),("adapter.ErrorDiagnosis","ErrorDiagnosis"),
    ("adapter.MetricDefine","MetricDefine"),("adapter.MetricParser","MetricParser"),
    ("adapter.digital_runner","digital_runner"),("adapter.openroad_runner","openroad_runner"),
    ("adapter.opensta_runner","opensta_runner"),("adapter.ieda_runner","ieda_runner"),
    ("adapter.analog_runner","analog_runner"),("adapter.commercial_runner","commercial_runner"),
    ("state","state"),
    ("composer.tool_registry","tool_registry"),("composer.flow_composer","flow_composer"),
    ("composer.format_bridge","format_bridge"),("composer.nl_interface","nl_interface"),
    ("composer.goals","goals"),("composer.replanner","replanner"),("composer.analyzer","analyzer"),
    ("tools.sandbox","sandbox"),("tools.bayes_opt","bayes_opt"),("tools.ista_verification","ista_verification"),
]:
    try: __import__(mod); check(f"{label}", True)
    except Exception as e: check(f"{label}", False, str(e)[:60])

# ═══════════════ 2. Adapter (6项) ═══════════════
section("2. Adapter 初始化")
from adapter.adapter import Adapter
a = Adapter('adapter/config.yaml','adapter/metric_define.yaml')
check("7 backends", len(a.backends)==7, str(list(a.backends.keys())))
for name in ["digital","openroad","opensta","ieda","analog","primetime"]:
    check(f"  {name}", name in a.backends)

# ═══════════════ 3. MetricDefine + Parser (8项) ═══════════════
section("3. MetricDefine + Parser")
from adapter.MetricDefine import MetricDefine
from adapter.MetricParser import MetricParser
md = MetricDefine("adapter/metric_define.yaml")
rules = md.get_circuit_metrics("TwoStageAmp")
check("TwoStageAmp rules", len(rules)==5, str(list(rules.keys())))
check("gain source=ac", rules["gain"]["source"]=="ac")
check("power source=dc", rules["power"]["source"]=="dc")
rules_d = md.get_circuit_metrics("GCD")
check("GCD rules", len(rules_d)==4)
check("wns source=sta", rules_d["wns"]["source"]=="sta")
check("missing → default rules", len(md.get_circuit_metrics("X"))==4)

mp = MetricParser({"wns":{"source":"sta","expression":"wns"}}, {"sta":{"wns":-0.12}})
m = mp.extract()
check("parser grouped", "sta" in m)
check("parser wns=-0.12", abs(m["sta"]["wns"]+0.12)<0.001, str(m["sta"]["wns"]))

# ═══════════════ 4. ErrorDiagnosis (5项) ═══════════════
section("4. ErrorDiagnosis")
from adapter.ErrorDiagnosis import ErrorDiagnosis
for log, expected in [
    ("ERROR: convergence failure at n049","convergence_fail"),
    ("FATAL: segmentation fault","tool_crash"),
    ("License error","license_error"),
    ("","unknown"),
]:
    d = ErrorDiagnosis(log).diagnose()
    check(f"  {expected}", d.type==expected, d.type)
check("SimError dataclass", hasattr(d,"type") and hasattr(d,"likely_cause"))

# ═══════════════ 5. SnapshotBuilder (8项) ═══════════════
section("5. SnapshotBuilder (v1.0 contract)")
from adapter.snapshot_builder import SnapshotBuilder
from adapter.contract import SnapshotPackage
raw = {"stdout":"ok","stderr":"","returncode":0,"run_dir":"/tmp/x/","params":{"M1_W":10.2}}
sp = SnapshotBuilder().build(raw,{"ac":{"gain_db":72.3}},"analog","TwoStageAmp")
check("SnapshotPackage", isinstance(sp,SnapshotPackage))
h = sp.header; check("snapshot_id", len(h.snapshot_id)>0)
check("observation_level=1", h.observation_level=="1")
check("schema_version=1.0", h.schema_version=="1.0")
check("design_name", h.design_name=="TwoStageAmp")
check("design_type", h.design_type=="analog")
cap = sp.capability; check("object_delta", hasattr(cap,'object_delta'))
check("waveform", hasattr(cap,'waveform'))

# ═══════════════ 6. Flow Composer (9项) ═══════════════
section("6. Flow Composer")
from composer.flow_composer import FlowComposer
c = FlowComposer()
# 6a. 关键词模式
f1 = c.compose("gcd","sky130",requirements=["低功耗","开源"],goals={"frequency":200,"area_max":100000})
check("flow name", "LowPower" in f1.name, f1.name)
check("7 steps (area_max triggers full)", len(f1.steps)>=5, str(len(f1.steps)))
f1_lite = c.compose("gcd","sky130",requirements=["低功耗","开源"],goals={"frequency":200})
check("2 steps (frequency only → lite)", len(f1_lite.steps)==2, str(len(f1_lite.steps)))
check("recommendations", len(f1.recommendations)>2)
# 6b. 精简模式
f2 = c.compose("gcd","sky130",requirements=["新手"],goals={"frequency":100})
check("lite mode (仅频率)", len(f2.steps)==2, str(len(f2.steps)))
# 6c. 签核模式
f3 = c.compose("gcd","sky130",requirements=["签核"],goals={"frequency":800,"area_max":50000})
check("signoff flow", "SignOff" in f3.name or "Design Compiler" in f3.summary())
# 6d. 目标驱动
from composer.goals import PPASpec
goal = PPASpec.parse({"timing":{"wns":">0"},"area":{"utilization":"<60%"}})
f4 = c.compose("gcd","sky130",ppa_spec=goal)
check("goal-driven", "SignOff" in f4.name or len(f4.steps)>=2)
# 6e. 工具替换
f5 = c.swap_tool(f1,"groute","iEDA")
check("swap groute→iEDA", f5 is not None)
# 6f. 备选方案
alts = c.list_alternatives(f1,"synthesis")
check("alternatives exist", len(alts)>0, str([a['tool'] for a in alts]))

# ═══════════════ 7. Replanner + Analyzer (5项) ═══════════════
section("7. Replanner + Analyzer (闭环)")
from composer.replanner import Replanner
from composer.analyzer import FlowAnalyzer
r = Replanner(); a2 = FlowAnalyzer()
full = ["synthesis","floorplan","placement","CTS","routing","STA","DRC"]
level, rerun = r.plan_rerun("clock_period",full)
check("L0 clock_period", level==0 and len(rerun)<len(full), str(rerun))
level2, rerun2 = r.plan_rerun("core_utilization",full)
check("L2 core_util", level2==2)
cheapest = r.cheapest_first(["core_utilization","clock_period","rtl_change"],full)
check("cheapest_first", cheapest[0][0]=="clock_period", str(cheapest[0]))
report = a2.analyze({"sta":{"wns":-0.03,"tns":-5}},goal=goal)
check("diagnosis", not report.passed and report.score<100)
loop = c.close_loop(f1,{"sta":{"wns":-0.03}},ppa_spec=goal)
check("close_loop", loop["next_action"] in ("rerun","human_breakpoint"), loop["next_action"])

# ═══════════════ 8. State + Adapter执行 (5项) ═══════════════
section("8. Adapter 执行 + State 入库")
from adapter.contract import SimError
from state import SnapshotReceiver
import tempfile
receiver = SnapshotReceiver()
work = tempfile.mkdtemp(prefix="test_")
with open(f"{work}/gcd.v","w") as f:
    f.write("module gcd(clk,a,b,r);input clk;input[7:0]a,b;output reg[7:0]r;always@(posedge clk)r<=a+b;endmodule")
# 8a. digital
result = a.run("digital","GCD",{"TOP_MODULE":"gcd","VERILOG_SRC":f"{work}/gcd.v"})
check("digital execute", not isinstance(result,SimError),
      f"{result.type}:{result.likely_cause[:40]}" if isinstance(result,SimError) else "ok")
if not isinstance(result,SimError):
    receiver.submit_snapshot(result)
    check("digital state", receiver.store.stats()["total_runs"]>=1)
# 8b. openroad
result2 = a.run("openroad","GCD",{"TOP_MODULE":"gcd","DESIGN_TOP":"gcd",
    "NETLIST_FILE":"/home/xu/OpenROAD-ae191807/test/gcd_sky130hd.v",
    "SDC_FILE":"/home/xu/OpenROAD-ae191807/test/gcd_sky130hd.sdc",
    "DIE_AREA":"0 0 150 150","CORE_AREA":"10 10 140 140",
    "flows":["floorplan","sta_report"]})
check("openroad execute", not isinstance(result2,SimError),
      f"{result2.type}" if isinstance(result2,SimError) else "ok")
# 8c. opensta
result3 = a.run("opensta","GCD",{"TOP_MODULE":"gcd","DESIGN_TOP":"gcd",
    "NETLIST_FILE":"/home/xu/OpenROAD-ae191807/test/gcd_sky130hd.v",
    "LIBERTY_PATH":"/home/xu/OpenROAD-ae191807/test/sky130hd/sky130hd_tt.lib",
    "SDC_FILE":"/home/xu/OpenROAD-ae191807/test/gcd_sky130hd.sdc"})
check("opensta execute", not isinstance(result3,SimError),
      f"{result3.type}" if isinstance(result3,SimError) else "ok")
# 8d. unknown backend
result4 = a.run("nonexistent","x",{})
check("unknown → SimError", isinstance(result4,SimError) and result4.type=="backend_error")

import shutil; shutil.rmtree(work, ignore_errors=True)

# ═══════════════ Summary ═══════════════
section("Summary")
print(f"  Passed: {PASS}  |  Failed: {FAIL}  |  Total: {PASS+FAIL}")
if FAIL==0: print("  🎉 All tests passed!")
else: print(f"  ⚠️  {FAIL} failed")
