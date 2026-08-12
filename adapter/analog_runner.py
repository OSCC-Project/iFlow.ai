import os
import subprocess
from uuid import uuid4
import jinja2
from .runner import Backend, BackendExecutionError

class AnalogRunner(Backend):
    def __init__(self, config):
        self.simulator_path = config.get("simulator_path", "ngspice")
        self.template_dir = config.get("netlist_templates", "./templates/analog/")
        self.run_base_dir = config.get("working_dir", "./runs/")
        self.timeout = config.get("timeout_seconds", 600)

    def execute(self, circuit_name: str, params: dict, analyses: list = None) -> dict:
        try:
            run_dir = f"{self.run_base_dir}/{uuid4()}/"
            os.makedirs(f"{run_dir}/input", exist_ok=True)

            # 检查模板文件是否存在
            template_path = f"{self.template_dir}/{circuit_name}.sp"
            if not os.path.exists(template_path):
                raise BackendExecutionError(f"模板文件不存在: {template_path}")

            with open(template_path, "r") as f:
                template_str = f.read()

            template = jinja2.Template(template_str)
            rendered_netlist = template.render(**params)

            netlist_path = f"{run_dir}/input/{circuit_name}.sp"
            with open(netlist_path, "w") as f:
                f.write(rendered_netlist)

            result = subprocess.run(
                [self.simulator_path, "-b", netlist_path],
                cwd=run_dir,
                capture_output=True,
                text=True,
                timeout=self.timeout
            )

            return {
                "stdout": result.stdout,
                "stderr": result.stderr,
                "returncode": result.returncode,
                "run_dir": run_dir,
                "netlist_path": netlist_path,
                "log_path": f"{run_dir}/ngspice.log",
            }

        except subprocess.TimeoutExpired as e:
            raise BackendExecutionError(f"仿真超时 ({self.timeout}s): {circuit_name}") from e
        except FileNotFoundError as e:
            raise BackendExecutionError(f"ngspice 未找到: {self.simulator_path}") from e
        except Exception as e:
            raise BackendExecutionError(f"仿真失败: {str(e)}") from e