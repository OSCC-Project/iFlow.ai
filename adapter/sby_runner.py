"""SymbiYosys 形式验证 Adapter — BMC / k-induction / cover"""
import os, re, subprocess
from typing import Optional
from uuid import uuid4
from .runner import Backend


class SBYRunner(Backend):
    """SymbiYosys 形式验证后端。

    流程:
        1. 生成 .sby 配置文件
        2. sby -f 执行验证
        3. 解析结果 (PASS/FAIL/UNKNOWN)
    """

    # 支持的验证模式
    ENGINES = {
        "bmc": "smtbmc",         # Bounded Model Check
        "cover": "smtbmc",       # Coverage
        "prove": "smtbmc z3",    # k-induction with Z3
        "live": "smtbmc",        # Liveness
    }

    def __init__(self, config: dict):
        cfg = config.get("sby", {})
        self.sby_path = cfg.get("sby_path", "sby")
        self.yosys_path = cfg.get("yosys_path", "yosys")
        self.timeout = cfg.get("timeout_seconds", 300)
        self.working_dir = cfg.get("working_dir", "./tmp/sby_runs/")

    def execute(self, circuit_name: str, params: dict,
                analyses: Optional[list] = None) -> dict:
        """执行 SymbiYosys 形式验证"""
        run_id = str(uuid4())[:8]
        run_dir = os.path.abspath(f"{self.working_dir}/{run_id}/")
        os.makedirs(run_dir, exist_ok=True)

        rtl_files = params.get("rtl_files", [])
        sva_file = params.get("sva_file", None)          # 单独 SVA 属性文件
        properties = params.get("properties", [])         # 内联 property 列表
        mode = params.get("mode", "bmc")                  # bmc|cover|prove|live
        depth = params.get("depth", 20)                   # BMC 深度
        top_module = params.get("top_module", "top")

        # ---- 1. 生成 .sby 文件 ----
        sby_content = self._generate_sby(
            rtl_files=rtl_files,
            sva_file=sva_file,
            properties=properties,
            mode=mode,
            depth=depth,
            top_module=top_module,
        )
        sby_file = os.path.join(run_dir, "check.sby")
        with open(sby_file, "w") as f:
            f.write(sby_content)

        # ---- 2. 执行 SymbiYosys ----
        cmd = [self.sby_path, "-f", sby_file]
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=self.timeout, cwd=run_dir)

        # ---- 3. 解析结果 ----
        output = r.stdout + r.stderr
        result = self._parse_result(output, mode, depth, run_dir=run_dir)

        # 确保使用新版本 Yosys
        new_yosys = os.path.expanduser("~/.local/bin/yosys")
        if os.path.exists(new_yosys):
            self.yosys_path = new_yosys

        return {
            "success": result["verdict"] in ("PASS", "BMC_PASS"),
            "verdict": result["verdict"],
            "summary": result.get("summary", f"结果: {result['verdict']}"),
            "mode": mode,
            "depth": depth,
            "assertions_total": result["assertions_total"],
            "assertions_passed": result["assertions_passed"],
            "assertions_failed": result["assertions_failed"],
            "output": output[-3000:],
            "sby_file": sby_file,
            "run_dir": run_dir,
        }

    def _generate_sby(self, rtl_files: list, sva_file: Optional[str],
                      properties: list, mode: str, depth: int,
                      top_module: str) -> str:
        """生成 .sby 配置文件 (新版 Yosys 兼容格式)"""
        lines = ["[tasks]"]
        lines.append(f"{mode}")
        lines.append("")

        lines.append("[options]")
        lines.append(f"{mode}: mode {mode}")
        lines.append(f"{mode}: depth {depth}")
        lines.append("")

        lines.append("[engines]")
        lines.append("smtbmc z3")
        lines.append("")

        lines.append("[script]")
        for f in rtl_files:
            lines.append(f"read -formal {os.path.abspath(f)}")
        if sva_file:
            lines.append(f"read -sv {os.path.abspath(sva_file)}")
        lines.append(f"prep -top {top_module}")
        lines.append("")

        lines.append("[files]")
        for f in rtl_files:
            lines.append(os.path.abspath(f))
        if sva_file:
            lines.append(os.path.abspath(sva_file))

        return "\n".join(lines)

    def _parse_result(self, output: str, mode: str, depth: int, run_dir: str = "") -> dict:
        """解析 sby 输出 — 提取可读结论"""
        # 去 ANSI 颜色码/不可见字符: "DONE (ESC[32mPASS..." 会破坏匹配
        output = re.sub(r'\x1b\[[0-9;]*m', '', output)
        output = ''.join(c for c in output if c.isprintable() or c in '\n')
        verdict = "UNKNOWN"
        summary = ""

        if re.search(r'DONE\s*\(\s*PASS', output) or "successful proof" in output:
            verdict = "PASS"
            summary = f"✅ 证明通过 (BMC) — 所有 property 在深度 {depth} 内成立"
        elif re.search(r'DONE\s*\(\s*FAIL', output) or re.search(r'Status:\s*failed', output):
            verdict = "FAIL"
            summary = f"❌ 证明失败 — 在深度 {depth} 内找到反例"
            # 方案 P1-3: 附带"失败的是哪条 property" + 反例输入序列
            cex = self._parse_cex_details(output, run_dir)
            if cex:
                summary += "\n" + cex
        elif re.search(r'\bERROR\b', output):
            verdict = "ERROR"
            err = re.search(r"ERROR:?(.*?)(?:\n|\Z)", output)
            summary = f"⚠️ 运行错误: {err.group(1).strip() if err else '未知错误'}"
        elif "reached depth" in output.lower():
            bmc = re.search(r"reached depth (\d+)", output)
            if bmc and int(bmc.group(1)) >= depth:
                verdict = "BMC_PASS"
                summary = f"✅ BMC 通过 — 在深度 {depth} 内未发现违例（深度限制内成立）"

        if not summary:
            summary = f"结果: {verdict} (详情见完整日志)"

        return {
            "verdict": verdict,
            "summary": summary,
            "assertions_total": len(re.findall(r"Assert|assert|cover", output)) or 1,
            "assertions_passed": len(re.findall(r"PASS|pass|proved", output)),
            "assertions_failed": len(re.findall(r"FAIL|violation|CEX", output)),
        }

    def _parse_cex_details(self, output: str, run_dir: str) -> str:
        """解析反例细节: 失败断言原文 + 反例输入激励序列"""
        parts = []
        # 1. 失败断言位置: "Assert failed in counter: /path/file.v:12.54-12.76 (...)"
        m = re.search(r'Assert failed in \w+: (\S+):(\d+)\.(\d+)-(\d+)', output)
        if m:
            src_path, line_no = m.group(1), int(m.group(2))
            sva_line = ""
            for cand in (src_path,
                         os.path.join(run_dir, "check_bmc", "src", os.path.basename(src_path))):
                try:
                    with open(cand) as f:
                        lines = f.read().splitlines()
                    if 0 < line_no <= len(lines):
                        sva_line = lines[line_no - 1].strip()
                    break
                except OSError:
                    continue
            if sva_line:
                parts.append(f"失败的断言 (第{line_no}行): {sva_line[:160]}")
        # 2. 反例输入序列 (trace_tb.v 中的 state/输入赋值)
        tb_path = os.path.join(run_dir, "check_bmc", "engine_0", "trace_tb.v")
        if os.path.exists(tb_path):
            try:
                content = open(tb_path).read()
            except OSError:
                content = ""
            states = []
            cur = None
            for line in content.splitlines():
                sm = re.search(r'// state (\d+)', line)
                if sm:
                    cur = {"inputs": {}}
                    states.append(cur)
                    continue
                if cur is not None:
                    # 值字面量: 1'b0 / 4'b1000 / 5 / 4'd7 → 按进制转十进制
                    im = re.search(r'PI_(\w+)\s*(?:<=|=)\s*(\d+)\'([bdh])([0-9a-fA-F_]+)', line)
                    if im:
                        try:
                            cur["inputs"][im.group(1)] = str(int(im.group(4).replace('_', ''),
                                                               {'b': 2, 'd': 10, 'h': 16}[im.group(3)]))
                        except (ValueError, KeyError):
                            cur["inputs"][im.group(1)] = im.group(4)
                        continue
                    im2 = re.search(r'PI_(\w+)\s*(?:<=|=)\s*(\d+)\s*;', line)
                    if im2:
                        cur["inputs"][im2.group(1)] = im2.group(2)
            # 反例发生在哪个 state (从 logfile 的 "Assert failed ... step N" 推断)
            step_m = re.search(r'Assert failed[^\n]*step (\d+)', output)
            fail_step = int(step_m.group(1)) if step_m else (len(states) - 1 if states else -1)
            if states:
                seq = " → ".join(f"[{', '.join(f'{k}={v}' for k, v in s['inputs'].items())}]"
                                 for s in states[: max(fail_step + 1, 3)])
                mark = f"\n反例输入序列 (含导致失败的第{fail_step}拍): {seq}" if fail_step >= 0 else f"\n反例输入序列: {seq}"
                parts.append(mark.strip())
        return "\n".join(parts)
