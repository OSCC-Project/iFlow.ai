import os
import shutil
import subprocess
from uuid import uuid4
import jinja2
from .runner import Backend, BackendExecutionError

class PrimeTimeRunner(Backend):
    def __init__(self, config):
        self.tool_path = config.get("executable", "/usr/local/bin/pt_shell")
        self.template_dir = config.get("template_dir", "./templates/commercial/")
        self.run_base_dir = config.get("working_dir", "./tmp/primetime_runs/")
        self.timeout = config.get("timeout_seconds", 3600)

    def execute(self, circuit_name: str, params: dict, analyses: list = None) -> dict:
        run_dir = f"{self.run_base_dir}/{uuid4()}/"
        os.makedirs(f"{run_dir}/input", exist_ok=True)
        os.makedirs(f"{run_dir}/output", exist_ok=True)

        # 1. 检查模板文件
        template_path = f"{self.template_dir}/{circuit_name}.tcl"
        if not os.path.exists(template_path):
            raise BackendExecutionError(
                f"Tcl 模板不存在: {template_path}\n"
                f"请在 {self.template_dir} 下创建 {circuit_name}.tcl，"
                f"使用 {{变量名}} 作为占位符"
            )

        with open(template_path, "r") as f:
            template_str = f.read()
        template = jinja2.Template(template_str)
        rendered_script = template.render(**params)

        # 2. 写入 Tcl 脚本
        tcl_path = f"{run_dir}/input/run.tcl"
        with open(tcl_path, "w") as f:
            f.write(rendered_script)

        # 3. 检查工具是否可用
        if not os.path.exists(self.tool_path) and not shutil.which(self.tool_path):
            raise BackendExecutionError(
                f"商业 EDA 工具未安装: {self.tool_path}\n"
                f"PrimeTime 是 Synopsys 商业工具，需要单独安装和 license"
            )

        # 4. 启动子进程执行
        try:
            result = subprocess.run(
                [self.tool_path, "-f", tcl_path],
                cwd=run_dir,
                capture_output=True,
                text=True,
                timeout=self.timeout
            )
        except subprocess.TimeoutExpired as e:
            raise BackendExecutionError(
                f"工具执行超时 ({self.timeout}s): {circuit_name}"
            ) from e
        except FileNotFoundError as e:
            raise BackendExecutionError(
                f"工具未找到: {self.tool_path}"
            ) from e

        # 5. 返回原始输出（供 MetricParser 解析）
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
            "run_dir": run_dir,
            "report_path": f"{run_dir}/output/timing.rpt",
        }