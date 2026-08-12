"""Verilator 仿真/检查 Adapter — lint + C++ 编译 + 高速仿真"""
import os, re, subprocess
from typing import Optional
from uuid import uuid4
from .runner import Backend


class VerilatorRunner(Backend):
    """Verilator 后端。

    流程:
        1. --lint-only 做语法检查
        2. --cc 编译为 C++ (可选)
        3. 返回 lint 结果
    """

    def __init__(self, config: dict):
        cfg = config.get("verilator", {})
        self.verilator_path = cfg.get("verilator_path", "verilator")
        self.timeout = cfg.get("timeout_seconds", 120)
        self.working_dir = cfg.get("working_dir", "./tmp/verilator_runs/")

    def execute(self, circuit_name: str, params: dict,
                analyses: Optional[list] = None) -> dict:
        """执行 Verilator lint 检查"""
        run_id = str(uuid4())[:8]
        run_dir = os.path.abspath(f"{self.working_dir}/{run_id}/")
        os.makedirs(run_dir, exist_ok=True)

        rtl_files = params.get("rtl_files", [])
        top_module = params.get("top_module", "")
        mode = params.get("mode", "lint")  # "lint" | "compile"

        if mode == "lint":
            return self._run_lint(rtl_files, top_module, run_dir)
        else:
            return self._run_compile(rtl_files, top_module, run_dir)

    def _run_lint(self, rtl_files: list, top_module: str,
                  run_dir: str) -> dict:
        """Verilator --lint-only"""
        cmd = [self.verilator_path, "--lint-only", "-Wall", "-Wno-fatal"]
        if top_module:
            cmd += ["--top-module", top_module]
        cmd += rtl_files

        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=self.timeout, cwd=run_dir)

        output = r.stdout + r.stderr
        warning_count = len(re.findall(r"%Warning", output))
        error_count = len(re.findall(r"%Error", output))
        fatal_count = len(re.findall(r"%Fatal", output))

        return {
            "mode": "lint",
            "success": r.returncode == 0 and error_count == 0 and fatal_count == 0,
            "returncode": r.returncode,
            "warning_count": warning_count,
            "error_count": error_count,
            "fatal_count": fatal_count,
            "output": output[-3000:],
            "run_dir": run_dir,
        }

    def _run_compile(self, rtl_files: list, top_module: str,
                     run_dir: str) -> dict:
        """Verilator --cc 编译为 C++"""
        obj_dir = os.path.join(run_dir, "obj_dir")
        cmd = [self.verilator_path, "--cc", "--build", "-j",
               "-Wall", "--Mdir", obj_dir]
        if top_module:
            cmd += ["--top-module", top_module]
        cmd += rtl_files

        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=self.timeout * 3, cwd=run_dir)

        sim_exe = os.path.join(obj_dir, f"V{top_module}")
        compiled = os.path.exists(sim_exe) if top_module else False

        return {
            "mode": "compile",
            "success": r.returncode == 0,
            "returncode": r.returncode,
            "compiled": compiled,
            "sim_exe": sim_exe if compiled else None,
            "output": (r.stdout + r.stderr)[-3000:],
            "run_dir": run_dir,
        }
