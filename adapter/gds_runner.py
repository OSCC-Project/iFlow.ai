# gds_runner.py —— GDS 生成后端
# 使用 iFlow 的 def2gds.py (gdstk) 替代 OpenROAD write_gds (依赖 KLayout)
import os, subprocess, sys
from typing import Optional
from uuid import uuid4
from .runner import Backend, BackendExecutionError


class GDSRunner(Backend):
    """GDS 生成后端: DEF → GDS2 (通过 gdstk, 不依赖 KLayout)"""

    def __init__(self, config: dict):
        self.def2gds_script = config.get("def2gds_script",
                                         "/home/xu/iFlow/scripts/common/def2gds.py")
        self.gds_dir = config.get("gds_dir", "/home/xu/iFlow/foundry/sky130/gds")
        self.working_dir = config.get("working_dir", "./tmp/gds_runs/")

    def execute(self, circuit_name: str, params: dict, analyses: Optional[list] = None) -> dict:
        run_id = str(uuid4())[:8]
        run_dir = os.path.abspath(f"{self.working_dir}/{run_id}/")
        os.makedirs(run_dir, exist_ok=True)

        input_def = params.get("INPUT_DEF", params.get("NETLIST_FILE", ""))
        output_gds = params.get("OUTPUT_GDS", f"{run_dir}/{circuit_name}.gds")

        if not os.path.exists(input_def):
            return {"run_dir": run_dir, "returncode": -1, "stdout": "", "stderr": "",
                    "error": f"DEF 文件不存在: {input_def}"}

        if not os.path.exists(self.def2gds_script):
            return {"run_dir": run_dir, "returncode": -1, "stdout": "", "stderr": "",
                    "error": f"def2gds 脚本不存在: {self.def2gds_script}"}

        # def2gds_gdstk.py 参数: {def_file} {gds_dir} {output_gds}
        os.makedirs(os.path.dirname(output_gds) or run_dir, exist_ok=True)
        try:
            result = subprocess.run(
                [sys.executable, self.def2gds_script, input_def, self.gds_dir, output_gds],
                cwd=run_dir, capture_output=True, text=True, timeout=300,
            )
            return {
                "run_dir": run_dir, "returncode": result.returncode,
                "stdout": result.stdout, "stderr": result.stderr,
                "gds_path": output_gds if os.path.exists(output_gds) else "",
                "params": params,
            }
        except subprocess.TimeoutExpired:
            raise BackendExecutionError("GDS 生成超时 (300s)")
        except Exception as e:
            raise BackendExecutionError(f"GDS 生成失败: {e}")
