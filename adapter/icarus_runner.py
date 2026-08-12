"""Icarus Verilog 仿真 Adapter — 编译 + 仿真 + VCD 解析"""
import os, re, subprocess, tempfile
from typing import Optional
from uuid import uuid4
from .runner import Backend


class IcarusRunner(Backend):
    """Icarus Verilog 仿真后端。

    流程:
        1. iverilog 编译 → vvp 可执行
        2. vvp 执行仿真 → VCD/日志
        3. 解析输出, 返回结构化结果
    """

    def __init__(self, config: dict):
        cfg = config.get("icarus", {})
        self.iverilog_path = cfg.get("iverilog_path", "iverilog")
        self.vvp_path = cfg.get("vvp_path", "vvp")
        self.timeout = cfg.get("timeout_seconds", 120)
        self.working_dir = cfg.get("working_dir", "./tmp/icarus_runs/")

    def execute(self, circuit_name: str, params: dict,
                analyses: Optional[list] = None) -> dict:
        """执行 Icarus 仿真"""
        run_id = str(uuid4())[:8]
        run_dir = os.path.abspath(f"{self.working_dir}/{run_id}/")
        os.makedirs(run_dir, exist_ok=True)

        # ---- 写输入文件 ----
        rtl_files = params.get("rtl_files", [])
        tb_file = params.get("tb_file", None)
        top_module = params.get("top_module", "tb")
        vcd_file = os.path.join(run_dir, "sim.vcd")

        # ---- 1. 编译 ----
        vvp_file = os.path.join(run_dir, "sim.vvp")
        compile_cmd = [self.iverilog_path, "-o", vvp_file, "-g2012"]
        if params.get("include_dirs"):
            for d in params["include_dirs"]:
                compile_cmd += ["-I", d]
        if params.get("defines"):
            for d in params["defines"]:
                compile_cmd += ["-D", d]
        compile_cmd += rtl_files
        if tb_file:
            compile_cmd.append(tb_file)

        cr = subprocess.run(compile_cmd, capture_output=True, text=True,
                            timeout=self.timeout, cwd=run_dir)

        if cr.returncode != 0:
            return {
                "success": False,
                "stage": "compile",
                "returncode": cr.returncode,
                "stderr": cr.stderr[-2000:],
                "stdout": cr.stdout[-1000:],
                "error": cr.stderr.strip().split('\n')[-1] if cr.stderr else f"rc={cr.returncode}",
                "run_dir": run_dir,
            }

        # ---- 2. 仿真 ----
        sim_cmd = [self.vvp_path, vvp_file]
        sr = subprocess.run(sim_cmd, capture_output=True, text=True,
                            timeout=self.timeout, cwd=run_dir)

        # ---- 3. 解析结果 ----
        stdout = sr.stdout
        stderr = sr.stderr

        # 提取 $display/$monitor 输出
        assertions_ok = "FAIL" not in stdout and "Error" not in stdout
        assertions_failed = len(re.findall(r"FAIL|Error|ERROR", stdout))

        # 提取仿真时间
        time_match = re.search(r"(\d+)\s*ns|#(\d+)", stdout)
        sim_time = time_match.group(1) if time_match else "unknown"

        # 检查 VCD 是否生成
        vcd_generated = os.path.exists(vcd_file) and os.path.getsize(vcd_file) > 0

        return {
            "success": assertions_ok,
            "stage": "simulate",
            "returncode": sr.returncode,
            "stdout": stdout[-3000:],
            "stderr": stderr[-1000:],
            "assertions_ok": assertions_ok,
            "assertions_failed": assertions_failed,
            "sim_time": sim_time,
            "vcd_file": vcd_file if vcd_generated else None,
            "run_dir": run_dir,
        }
