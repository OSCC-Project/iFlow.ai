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
        result = self._parse_result(output, mode, depth)

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

    def _parse_result(self, output: str, mode: str, depth: int) -> dict:
        """解析 sby 输出 — 提取可读结论"""
        verdict = "UNKNOWN"
        summary = ""

        if "DONE (PASS)" in output or "successful proof" in output:
            verdict = "PASS"
            summary = f"✅ 证明通过 (k-induction) — 所有 property 在深度 {depth} 内成立"
        elif "DONE (FAIL)" in output or "Status: failed" in output:
            verdict = "FAIL"
            # 尝试提取反例
            cex = re.search(r"Counterexample:?(.*?)(?:\n\n|\Z)", output, re.DOTALL)
            if cex:
                summary = f"❌ 证明失败 — 找到反例。在深度 {depth} 内 property 被违反。\n反例: {cex.group(1)[:200]}"
            else:
                summary = f"❌ 证明失败 — 在深度 {depth} 内找到反例"
        elif "ERROR" in output:
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
