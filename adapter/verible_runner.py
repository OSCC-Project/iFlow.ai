"""Verible 语法/Style 检查 Adapter"""
import os, re, subprocess, json
from typing import Optional
from uuid import uuid4
from .runner import Backend


class VeribleRunner(Backend):
    """Verible RTL 语法检查后端。

    支持两种模式:
        - lint: 语法错误 + 警告检查
        - style: 代码风格检查 (verible-verilog-format --check)
    """

    def __init__(self, config: dict):
        cfg = config.get("verible", {})
        self.lint_path = cfg.get("lint_path", "verible-verilog-lint")
        self.format_path = cfg.get("format_path", "verible-verilog-format")
        self.syntax_path = cfg.get("syntax_path", "verible-verilog-syntax")
        self.timeout = cfg.get("timeout_seconds", 60)
        self.working_dir = cfg.get("working_dir", "./tmp/verible_runs/")

    def execute(self, circuit_name: str, params: dict,
                analyses: Optional[list] = None) -> dict:
        """执行 Verible 检查"""
        run_id = str(uuid4())[:8]
        run_dir = os.path.abspath(f"{self.working_dir}/{run_id}/")
        os.makedirs(run_dir, exist_ok=True)

        rtl_files = params.get("rtl_files", [])
        mode = params.get("mode", "lint")  # "lint" | "style" | "both"

        result = {"run_dir": run_dir, "mode": mode, "files_checked": len(rtl_files)}

        if mode in ("lint", "both"):
            result["lint"] = self._run_lint(rtl_files, run_dir)

        if mode in ("style", "both"):
            result["style"] = self._run_style_check(rtl_files, run_dir)

        # 汇总成功/失败
        if mode == "both":
            lint_ok = result.get("lint", {}).get("success", False)
            style_ok = result.get("style", {}).get("success", False)
            result["success"] = lint_ok and style_ok
        elif mode == "lint":
            result["success"] = result.get("lint", {}).get("success", False)
        else:
            result["success"] = result.get("style", {}).get("success", False)

        return result

    def _run_lint(self, rtl_files: list, run_dir: str) -> dict:
        """verible-verilog-lint"""
        cmd = [self.lint_path, "--rules_config", "all"] + rtl_files
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=self.timeout, cwd=run_dir)

        output = r.stdout + r.stderr
        errors = len(re.findall(r"Error|error|ERROR", output))
        warnings = len(re.findall(r"Warning|warning|WARNING", output))
        # 解析 lint 规则违反
        rule_violations = len(re.findall(r"violat|Rule", output, re.IGNORECASE))

        return {
            "success": r.returncode <= 1,  # 0=clean, 1=violations found, >1=tool error
            "returncode": r.returncode,
            "errors": errors,
            "warnings": warnings,
            "rule_violations": rule_violations,
            "output": output[-3000:],
        }

    def _run_style_check(self, rtl_files: list, run_dir: str) -> dict:
        """verible-verilog-format --check (不修改文件, 仅检查)"""
        cmd = [self.format_path, "--check"] + rtl_files
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=self.timeout, cwd=run_dir)

        output = r.stdout + r.stderr
        files_formatted = len(re.findall(r"Formatted|formatted|differs",
                                         output, re.IGNORECASE))

        return {
            "success": r.returncode <= 1,  # 0=already formatted, 1=needs formatting, >1=error
            "returncode": r.returncode,
            "files_need_format": files_formatted,
            "output": output[-3000:],
        }
