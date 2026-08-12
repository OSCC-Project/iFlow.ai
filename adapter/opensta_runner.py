# opensta_runner.py —— OpenSTA 静态时序分析后端
# S3: ICCAD'24 官方评估脚本指定工具
# 调用方式: opensta -no_init tcl_script.tcl
import os, re, subprocess
from typing import Optional
from uuid import uuid4
from .runner import Backend, BackendExecutionError


class OpenSTARunner(Backend):
    """OpenSTA 静态时序分析后端 (S3: ICCAD'24 基准对齐)。

    作为 iSTA 的兼容后端，当 iSTA 三步验证失败时自动启用。
    使用 subprocess + Tcl 脚本调用。
    """

    def __init__(self, config: dict):
        self.opensta_bin = config.get("executable", "/usr/bin/sta")  # OpenROAD 内嵌 OpenSTA
        self.working_dir = config.get("working_dir", "./tmp/opensta_runs/")
        self.timeout = config.get("timeout_seconds", 300)

    def execute(self, circuit_name: str, params: dict, analyses: Optional[list] = None) -> dict:
        run_id = str(uuid4())[:8]
        run_dir = os.path.abspath(f"{self.working_dir}/{run_id}/")
        os.makedirs(run_dir, exist_ok=True)

        tcl_path = self._generate_tcl(run_dir, params)
        if not os.path.exists(self.opensta_bin):
            return self._tool_missing_result(run_dir, params)

        try:
            result = subprocess.run(
                [self.opensta_bin, "-no_init", tcl_path],
                cwd=run_dir, capture_output=True, text=True, timeout=self.timeout,
            )
            sta_metrics = self._parse_report(run_dir, result.stdout)
            return {
                "run_dir": run_dir, "returncode": result.returncode,
                "stdout": result.stdout, "stderr": result.stderr,
                "sta": sta_metrics, "metadata": {"design": circuit_name},
                "params": params,
            }
        except subprocess.TimeoutExpired:
            raise BackendExecutionError(f"OpenSTA 超时 ({self.timeout}s)")
        except FileNotFoundError:
            return self._tool_missing_result(run_dir, params)

    def _tool_missing_result(self, run_dir, params):
        return {
            "run_dir": run_dir, "returncode": -1,
            "stdout": "", "stderr": "",
            "sta": {"wns": float("nan"), "tns": float("nan"),
                    "leakage_power": float("nan"), "total_area": float("nan")},
            "params": params,
        }

    def _generate_tcl(self, run_dir, params):
        netlist = params.get("NETLIST_FILE", "")
        liberty = params.get("LIBERTY_PATH", params.get("LIB_FILES", ""))
        sdc = params.get("SDC_FILE", "")
        spef = params.get("SPEF_FILE", "")
        top = params.get("DESIGN_TOP", params.get("TOP_MODULE", "gcd"))
        clk_period = params.get("CLK_PERIOD", 10.0)
        clk = params.get("CLK_PORT", "clk")
        result_dir = params.get("RESULT_DIR", f"{run_dir}/output")
        os.makedirs(result_dir, exist_ok=True)

        lines = [
            f"# OpenSTA Tcl — {top}",
            "# S3: ICCAD'24 benchmark compatible",
            "",
        ]
        if liberty:
            for lib in liberty.split():
                if os.path.exists(lib):
                    lines.append(f"read_liberty {lib}")
        elif os.path.exists(liberty):
            lines.append(f"read_liberty {liberty}")

        if netlist and os.path.exists(netlist):
            lines.append(f"read_verilog {netlist}")
        if top:
            lines.append(f"link_design {top}")
        # SDC: 仅当用户未显式指定 CLK_PERIOD 时才用 SDC 的时钟定义
        if clk_period:
            lines.append(f"create_clock -period {clk_period} [get_ports {clk}]")
        elif sdc and os.path.exists(sdc):
            lines.append(f"read_sdc {sdc}")
        else:
            lines.append(f"create_clock -period 10.0 [get_ports {clk}]")
        if spef and os.path.exists(spef):
            lines.append(f"read_spef {spef}")

        lines.extend([
            "",
            f"report_checks -path_delay min_max -format full_clock_expanded > {result_dir}/timing.rpt",
            "exit",
        ])

        tcl_path = f"{run_dir}/run.tcl"
        with open(tcl_path, "w") as f:
            f.write("\n".join(lines) + "\n")
        return tcl_path

    def _parse_report(self, run_dir, stdout):
        report_path = os.path.join(run_dir, "output", "timing.rpt")
        metrics = {"wns": float("nan"), "tns": float("nan"),
                   "leakage_power": float("nan"), "total_area": float("nan")}

        content = ""
        if os.path.exists(report_path):
            try:
                content = open(report_path).read()
            except: pass
        if not content:
            content = stdout

        for label, pattern in [
            ("wns", r'(?:wns|Worst Negative Slack)[^-\d]*(-?[\d.]+)'),
            ("tns", r'(?:tns|Total Negative Slack)[^-\d]*(-?[\d.]+)'),
            ("leakage_power", r'(?:leakage|Leakage Power)[\s:]+([\d.eE+-]+)'),
            ("total_area", r'(?:area|Total Area|Design Area)[\s:]+([\d.eE+-]+)'),
        ]:
            m = re.search(pattern, content, re.IGNORECASE)
            if m:
                metrics[label] = float(m.group(1))

        # Fallback: slack from timing path
        if metrics["wns"] != metrics["wns"]:  # NaN
            slacks = re.findall(r'(-?[\d.]+)\s+slack', content, re.I)
            if slacks:
                metrics["wns"] = min(float(s) for s in slacks)

        return metrics
