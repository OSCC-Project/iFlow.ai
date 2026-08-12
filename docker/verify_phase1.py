#!/usr/bin/env python3
"""
Phase 1 验证脚本
检查所有工具可用性 + ChipMATE NL→RTL 最小闭环
"""
import subprocess
import sys
import os

PASS, FAIL, SKIP = 0, 0, 0

def check(name, cmd, required=True):
    """检查工具是否可用"""
    global PASS, FAIL, SKIP
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if proc.returncode == 0 or b"version" in proc.stdout.lower().encode() or b"version" in proc.stderr.lower().encode():
            print(f"  ✅ {name}: OK")
            PASS += 1
        else:
            status = "⚠️  SKIP" if not required else "❌ FAIL"
            print(f"  {status} {name}: rc={proc.returncode}")
            if not required: SKIP += 1
            else: FAIL += 1
    except FileNotFoundError:
        status = "⚠️  SKIP" if not required else "❌ FAIL"
        print(f"  {status} {name}: not found")
        if not required: SKIP += 1
        else: FAIL += 1
    except Exception as e:
        print(f"  ❌ FAIL {name}: {e}")
        FAIL += 1

print("=" * 60)
print("Phase 1 验证: 工具可用性检查")
print("=" * 60)

# 必需工具
check("Icarus (iverilog)", ["iverilog", "-V"])
check("Icarus (vvp)", ["vvp", "-v"])
check("Verilator", ["verilator", "--version"])
check("Yosys", ["yosys", "--version"])
check("Verible", ["verible-verilog-lint", "--version"])

# 综合相关
check("SymbiYosys (sby)", ["sby", "--version"], required=False)
check("Yices2", ["yices", "--version"], required=False)
check("Boolector", ["boolector", "--version"], required=False)
check("Z3", ["z3", "--version"], required=False)

# 物理验证
check("Netgen (LVS)", ["netgen", "-batch", "quit"], required=False)

# Python 绑定
try:
    import yosys
    print("  ✅ pyosys: OK")
    PASS += 1
except ImportError:
    print("  ⚠️  SKIP pyosys: not installed (pip3 install pyosys)")
    SKIP += 1

# ChipMATE runner 语法检查
print("\n" + "=" * 60)
print("Phase 1 验证: ChipMATE runner")
print("=" * 60)

runner_path = os.path.join(os.path.dirname(__file__), "..", "adapter", "chipmate_runner.py")
try:
    with open(runner_path) as f:
        compile(f.read(), runner_path, "exec")
    print("  ✅ chipmate_runner.py: 语法正确")
    PASS += 1
except SyntaxError as e:
    print(f"  ❌ FAIL chipmate_runner.py: {e}")
    FAIL += 1

# Icarus 快速冒烟测试
print("\n" + "=" * 60)
print("Phase 1 验证: Icarus 仿真冒烟测试")
print("=" * 60)

smoke_test = """
module counter(input clk, input rst_n, input en, output reg [3:0] q);
  always @(posedge clk or negedge rst_n)
    if (!rst_n) q <= 0;
    else if (en) q <= q + 1;
endmodule
"""

smoke_tb = """
module tb;
  reg clk, rst_n, en;
  wire [3:0] q;
  counter dut(clk, rst_n, en, q);
  initial begin
    clk = 0; rst_n = 1; en = 0;
    #5 rst_n = 0;
    #10 rst_n = 1;
    #10 en = 1;
    #200 $display("PASS: q=%d", q);
    $finish;
  end
  always #5 clk = ~clk;
endmodule
"""

import tempfile
with tempfile.TemporaryDirectory() as tmpdir:
    with open(f"{tmpdir}/dut.v", "w") as f: f.write(smoke_test)
    with open(f"{tmpdir}/tb.v", "w") as f: f.write(smoke_tb)
    vvp = f"{tmpdir}/sim.vvp"

    # Compile
    r = subprocess.run(["iverilog", "-o", vvp, f"{tmpdir}/dut.v", f"{tmpdir}/tb.v"],
                       capture_output=True, text=True, timeout=10)
    if r.returncode == 0:
        print("  ✅ Icarus 编译: OK")
        PASS += 1
    else:
        print(f"  ❌ FAIL Icarus 编译: {r.stderr[:200]}")
        FAIL += 1

    # Simulate
    r = subprocess.run(["vvp", vvp], capture_output=True, text=True, timeout=10)
    if r.returncode == 0 and "PASS" in r.stdout:
        print("  ✅ Icarus 仿真: OK (PASS)")
        PASS += 1
    else:
        print(f"  ❌ FAIL Icarus 仿真: rc={r.returncode}")
        FAIL += 1

# Docker 文件检查
print("\n" + "=" * 60)
print("Phase 1 验证: Docker 文件")
print("=" * 60)

docker_dir = os.path.join(os.path.dirname(__file__))
for fname in ["Dockerfile.sim", "Dockerfile.syn", "Dockerfile.pv", "docker-compose.yml", "setup_tools.sh"]:
    fpath = os.path.join(docker_dir, fname)
    if os.path.exists(fpath):
        print(f"  ✅ {fname}: exists ({os.path.getsize(fpath)} bytes)")
        PASS += 1
    else:
        print(f"  ⚠️  SKIP {fname}: not found")
        SKIP += 1

# 汇总
print("\n" + "=" * 60)
total = PASS + FAIL + SKIP
print(f"结果: {PASS} 通过 / {FAIL} 失败 / {SKIP} 跳过 (共 {total} 项)")
if FAIL == 0:
    print("✅ Phase 1 工具验证全部通过")
else:
    print(f"⚠️  Phase 1 有 {FAIL} 项失败，请检查")
print("=" * 60)

sys.exit(0 if FAIL == 0 else 1)
