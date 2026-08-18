"""覆盖率收集 Adapter — Verilator --coverage (Line + Toggle)

方案 3.3 覆盖率热力图的数据源。本机 Verilator 4.038 不支持 --timing
(TB 里的 #delay 会被直接忽略), 因此用 C++ testbench 驱动时钟;
激励与 ChipMATE 交叉验证同源 (同一组随机向量), 保证覆盖率与匹配率统计口径一致。
"""
import os, re, subprocess
from uuid import uuid4
from .runner import Backend

# C++ testbench 模板: 复位预处理 3 拍 → 逐组施加激励 (每拍一个 posedge)
_MAIN_CPP = """#include "V{top}.h"
#include "verilated.h"
#include "verilated_cov.h"
int main(int argc, char** argv) {{
    Verilated::commandArgs(argc, argv);
    V{top}* top = new V{top};
{body}
    VerilatedCov::write("coverage.dat");
    delete top;
    return 0;
}}
"""


class CoverageRunner(Backend):
    """Verilator 覆盖率后端: Line (语句行) + Toggle (信号翻转) + Branch (分支臂) + FSM (状态寄存器)。

    - Branch: Verilator v_line 行点按分支臂 (if/elsif/else) 统计, 与 Line 同源同激励
    - FSM: 同一次仿真的 VCD 中 DUT 作用域寄存器取值域覆盖 (状态由寄存器编码)
    """

    def __init__(self, config: dict):
        cfg = config.get("verilator", {})
        self.verilator_path = cfg.get("verilator_path", "verilator")
        self.timeout = cfg.get("timeout_seconds", 180)
        self.working_dir = cfg.get("working_dir", "./tmp/coverage_runs/")

    def execute(self, circuit_name: str, params: dict,
                analyses: list = None) -> dict:
        run_id = str(uuid4())[:8]
        run_dir = os.path.abspath(f"{self.working_dir}/{run_id}/")
        obj_dir = os.path.join(run_dir, "obj_dir")
        os.makedirs(run_dir, exist_ok=True)

        rtl_code = params.get("rtl_code", "")
        top = params.get("top_module", "top")
        inputs = params.get("inputs", [])
        stimuli = params.get("stimuli", [])
        has_clk = params.get("has_clk", True)
        vcd_path = params.get("vcd_path", "")
        clk_name = params.get("clk_name", "")
        if not clk_name:
            for n in inputs:
                if "clk" in n.lower():
                    clk_name = n
                    break
        resets = [n for n in inputs
                  if ("rst" in n.lower() or "reset" in n.lower()) and n != clk_name]

        if not rtl_code.strip():
            return {"success": False, "error": "无 RTL 代码"}
        if not stimuli:
            return {"success": False, "error": "无激励数据 (需先运行自动激励仿真)"}

        dut_path = os.path.join(run_dir, "dut.v")
        with open(dut_path, "w") as f:
            f.write(rtl_code)

        # ---- 生成 C++ testbench ----
        def posedge():
            return ("    top->%s = 1; top->eval();\n"
                    "    top->%s = 0; top->eval();" % (clk_name, clk_name)) if has_clk else ""

        lines = []
        for n in inputs:
            if n == clk_name:
                continue
            lines.append(f"    top->{n} = 0;")
        for n in resets:
            active_low = n.endswith("_n") or n.endswith("_b") or n.endswith("_l")
            lines.append(f"    top->{n} = {0 if active_low else 1};")  # 复位有效
        if has_clk:
            for _ in range(3):
                lines.append(posedge())
        for n in resets:
            active_low = n.endswith("_n") or n.endswith("_b") or n.endswith("_l")
            lines.append(f"    top->{n} = {1 if active_low else 0};")  # 解复位
        if has_clk:
            lines.append(posedge())
        for stim in stimuli:
            for n, v in stim.items():
                lines.append(f"    top->{n} = {int(v)};")
            lines.append(posedge() if has_clk else "    top->eval();")
        body = "\n".join(lines)
        main_path = os.path.join(run_dir, "tb.cpp")
        with open(main_path, "w") as f:
            f.write(_MAIN_CPP.format(top=top, body=body))

        # ---- 编译 (含覆盖率插桩) ----
        cmd = [self.verilator_path, "--cc", "--exe", "--build", "-j",
               "--coverage", "--coverage-line", "--coverage-toggle",
               "--top-module", top, "--Mdir", obj_dir, "-Wno-fatal",
               dut_path, main_path]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               timeout=self.timeout, cwd=run_dir)
        except subprocess.TimeoutExpired:
            return {"success": False, "error": "Verilator 编译超时"}
        if r.returncode != 0:
            return {"success": False, "error": "Verilator 编译失败",
                    "stdout": (r.stdout + r.stderr)[-1500:]}
        sim_exe = os.path.join(obj_dir, f"V{top}")
        if not os.path.exists(sim_exe):
            return {"success": False, "error": f"未生成可执行文件 V{top}"}

        # ---- 运行仿真 (覆盖率点随 eval 记录) ----
        try:
            r = subprocess.run([sim_exe], capture_output=True, text=True,
                               timeout=60, cwd=run_dir)
        except subprocess.TimeoutExpired:
            return {"success": False, "error": "覆盖率仿真超时 (设计可能死循环)"}
        dat_path = os.path.join(run_dir, "coverage.dat")
        if not os.path.exists(dat_path):
            return {"success": False, "error": "未生成 coverage.dat",
                    "stdout": (r.stdout + r.stderr)[-500:]}

        # ---- 解析: lcov (Line) + 原始 dat (Toggle/Branch) + VCD (FSM 状态) ----
        line_info = self._parse_line_coverage(dat_path)
        toggle_info = self._parse_toggle_coverage(dat_path)
        branch_info = self._parse_branch_coverage(dat_path)
        if line_info is None and toggle_info is None and branch_info is None:
            return {"success": False, "error": "覆盖率数据解析失败 (空数据)"}

        line_total, line_covered, lines = (line_info or (0, 0, {}))
        tg_total, tg_covered, toggles = (toggle_info or (0, 0, {}))
        br_total, br_covered, branch_lines = (branch_info or (0, 0, {}))
        fsm_info = self._parse_vcd_state_coverage(vcd_path) if vcd_path else None
        return {
            "success": True,
            "run_dir": run_dir,
            "line_total": line_total,
            "line_covered": line_covered,
            "line_pct": round(line_covered / line_total * 100, 1) if line_total else None,
            "toggle_total": tg_total,
            "toggle_covered": tg_covered,
            "toggle_pct": round(tg_covered / tg_total * 100, 1) if tg_total else None,
            "lines": lines,          # {行号: 命中次数}
            "toggles": toggles,      # {信号名: 翻转次数}
            # Branch: Verilator v_line 行点按分支臂 (if/elsif/else) 统计, 与 Line 同源同激励
            "branch_total": br_total,
            "branch_covered": br_covered,
            "branch_pct": round(br_covered / br_total * 100, 1) if br_total else None,
            "branch_lines": branch_lines,  # {行号: {total, covered}}
            # FSM/状态寄存器: 同一次仿真的 VCD 中 DUT 寄存器取值域覆盖
            "fsm": fsm_info,
        }

    def _parse_line_coverage(self, dat_path: str):
        """verilator_coverage --write-info → lcov 格式 (DA:行号,命中数)"""
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            vlt = os.path.join(td, "vlt_coverage.dat")
            import shutil
            shutil.copy(dat_path, vlt)
            out_json = os.path.join(td, "cov.json")
            try:
                r = subprocess.run(
                    ["verilator_coverage", "--write-info", out_json, vlt],
                    capture_output=True, text=True, timeout=60)
            except (subprocess.TimeoutExpired, FileNotFoundError):
                return None
            if r.returncode != 0 or not os.path.exists(out_json):
                return None
            with open(out_json) as f:
                content = f.read()
        lines: dict = {}
        for m in re.finditer(r"DA:(\d+),(\d+)", content):
            ln = int(m.group(1))
            hits = int(m.group(2))
            lines[ln] = lines.get(ln, 0) + hits
        if not lines:
            return None
        return len(lines), sum(1 for h in lines.values() if h > 0), lines

    def _parse_toggle_coverage(self, dat_path: str):
        """原始 coverage.dat 的 v_toggle 页: 每个信号一个翻转计数点"""
        try:
            with open(dat_path, errors="replace") as f:
                content = f.read()
        except OSError:
            return None
        toggles: dict = {}
        for m in re.finditer(r"C '([^']*)'\s+(\d+)", content):
            payload, count = m.group(1), int(m.group(2))
            if "v_toggle" not in payload:
                continue
            sig = None
            for seg in payload.split("\x01"):
                if seg.startswith("o\x02"):
                    sig = seg[2:]
                    break
            if sig:
                toggles[sig] = toggles.get(sig, 0) + count
        if not toggles:
            return None
        return len(toggles), sum(1 for c in toggles.values() if c > 0), toggles

    def _parse_branch_coverage(self, dat_path: str):
        """原始 coverage.dat 的 v_line 页按分支臂统计:
        o=if/elsif/else 的行点即分支臂, 每臂独立计数 (与 lcov 的按行聚合互补)"""
        try:
            with open(dat_path, errors="replace") as f:
                content = f.read()
        except OSError:
            return None
        arms: dict = {}   # (行号, stmt_id) → 命中数
        for m in re.finditer(r"C '([^']*)'\s+(\d+)", content):
            payload, count = m.group(1), int(m.group(2))
            if "v_line" not in payload:
                continue
            kv = {}
            for seg in payload.split("\x01"):
                if "\x02" in seg:
                    k, _, v = seg.partition("\x02")
                    kv[k] = v
            if kv.get("o") not in ("if", "elsif", "else"):
                continue
            line = int(kv.get("l", 0))
            stmt = kv.get("S", "?")
            arms[(line, stmt)] = arms.get((line, stmt), 0) + count
        if not arms:
            return None
        covered = sum(1 for c in arms.values() if c > 0)
        # 每行分支臂汇总 (前端可提示哪一行有未覆盖的臂)
        branch_lines: dict = {}
        for (line, _), hits in arms.items():
            d = branch_lines.setdefault(line, {"total": 0, "covered": 0})
            d["total"] += 1
            if hits > 0:
                d["covered"] += 1
        return len(arms), covered, branch_lines

    def _parse_vcd_state_coverage(self, vcd_path: str):
        """从同一次仿真的 VCD 提取 DUT 状态寄存器取值域覆盖。

        规则: 顶层 scope 是 TB (激励信号为 reg), 嵌套 scope 中的 reg 即 DUT 寄存器。
        每个寄存器: 达到的不同取值数 / 取值域 (2^width, width>16 时域不适用只报取值数)。
        """
        try:
            with open(vcd_path, errors="replace") as f:
                lines = f.read().splitlines()
        except OSError:
            return {"error": f"VCD 不可读: {vcd_path}"}

        # ---- 声明解析 (顺序处理 $scope/$var/$upscope) ----
        sigs: dict = {}       # id → {w, name}
        scope_stack: list = []
        for line in lines:
            if line.startswith("$scope"):
                m = re.search(r"\$scope\s+\w+\s+(\S+)\s+\$end", line)
                scope_stack.append(m.group(1) if m else "")
            elif line.startswith("$upscope"):
                if scope_stack:
                    scope_stack.pop()
            elif line.startswith("$var"):
                m = re.match(r"\$var\s+(reg|wire|logic|integer)\s+(\d+)\s+(\S+)\s+([^\s$]+)", line)
                if not m:
                    continue
                vtype, width, ref, name = m.group(1), int(m.group(2)), m.group(3), m.group(4)
                # 嵌套 scope (DUT 层级) 的 reg = 状态寄存器; 顶层 TB scope 的 reg 是激励
                if vtype == "reg" and len(scope_stack) > 1:
                    sigs[ref] = {"w": width, "name": ".".join(scope_stack[2:] + [name])}
            elif line.startswith("$enddefinitions"):
                break
        if not sigs:
            return {"error": "VCD 中未找到 DUT 寄存器"}

        # ---- 值变更解析: b<binary> <id> (多bit) 或 <0|1><id> (单bit) ----
        values: dict = {ref: set() for ref in sigs}
        for line in lines:
            m = re.match(r"^b([01]+)\s*(\S+)$", line)
            if m and m.group(2) in sigs:
                values[m.group(2)].add(int(m.group(1), 2))
                continue
            m = re.match(r"^([01])(\S+)$", line)
            if m and m.group(2) in sigs:
                values[m.group(2)].add(int(m.group(1)))
        regs = []
        pcts = []
        for ref, s in sigs.items():
            w, name = s["w"], s["name"]
            distinct = len(values[ref])
            total = (1 << min(w, 16)) if w <= 16 else None
            pct = round(distinct / total * 100, 1) if total else None
            if pct is not None:
                pcts.append(pct)
            regs.append({"name": name, "width": w, "distinct": distinct,
                         "total": total, "pct": pct})
        return {
            "regs": regs,
            "pct": round(sum(pcts) / len(pcts), 1) if pcts else None,
        }
