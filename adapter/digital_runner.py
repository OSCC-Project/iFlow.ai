# digital_runner.py —— 数字后端实现
# 支持 Yosys 综合 + iSTA/OpenSTA 时序分析
import os
import re
import subprocess
from typing import Optional
from uuid import uuid4

from .runner import Backend, BackendExecutionError


class DigitalRunner(Backend):
    """数字后端：Yosys 综合 + STA 时序分析。

    流程：
        1. Yosys → 生成门级网表 (.v)
        2. iSTA 或 OpenSTA → 产生时序报告
        3. 返回结构化原始数据供 MetricParser 消费
    """

    def __init__(self, config: dict):
        # ---- 综合配置 ----
        synthesis_cfg = config.get("synthesis", {})
        self.yosys_path = synthesis_cfg.get("yosys_path", "/usr/bin/yosys")
        self.fallback_to_cli = synthesis_cfg.get("fallback_to_cli", True)
        self.synthesis_timeout = synthesis_cfg.get("timeout_seconds", 120)

        # ---- STA 主选（iEDA bin 路径在 backend.ieda 下，此处提供合理默认值） ----
        sta_primary = config.get("sta_primary", {})
        self.sta_tool = sta_primary.get("tool", "iSTA")
        self.ieda_bin = sta_primary.get("ieda_bin", "/home/xu/iEDA/bin/iEDA")

        # ---- STA 兼容后端（兜底） ----
        sta_compatible = config.get("sta_compatible", {})
        self.opensta_path = sta_compatible.get("executable", "/usr/local/bin/opensta")
        self.sta_timeout = sta_compatible.get("timeout_seconds", 300)

        # ---- 通用 ----
        self.template_dir = config.get("template_dir", "./templates/digital/")
        self.working_dir = config.get("working_dir", "./tmp/digital_runs/")

    def execute(
        self,
        circuit_name: str,
        params: dict,
        analyses: Optional[list] = None,
    ) -> dict:
        """执行数字流程。

        Args:
            circuit_name: 电路名称（如 "GCD"）
            params: 参数字典（如 {"CLK_PERIOD": 2.0, "TOP_MODULE": "gcd"}）
            analyses: 保留参数，数字侧暂不使用

        Returns:
            原始数据字典，包含 netlist_path 和 STA 报告
        """
        run_id = str(uuid4())[:8]
        run_dir = os.path.abspath(f"{self.working_dir}/{run_id}/")
        os.makedirs(f"{run_dir}/input", exist_ok=True)
        os.makedirs(f"{run_dir}/output", exist_ok=True)

        # ---- 1. Yosys 综合 ----
        ys_result = self._run_yosys(circuit_name, params, run_dir)

        # ---- 2. STA 时序分析 ----
        if self.sta_tool.lower() == "ista":
            sta_result = self._run_ista(
                netlist_path=ys_result["netlist_path"],
                run_dir=run_dir,
                params=params,
            )
        else:
            sta_result = self._run_opensta(
                netlist_path=ys_result["netlist_path"],
                run_dir=run_dir,
                params=params,
            )

        return {
            "run_dir": run_dir,
            "netlist_path": ys_result["netlist_path"],
            "synth_log": ys_result.get("stdout", ""),
            "synth_success": True,
            "sta_report": sta_result.get("report_path", ""),
            "sta": sta_result.get("metrics", {}),
            "sta_success": sta_result.get("success", False),
            "stdout": sta_result.get("stdout", ""),
            "stderr": sta_result.get("stderr", ""),
            "returncode": 0 if ys_result.get("netlist_path") else 1,
            "params": params,
        }

    # ============================================================
    # Yosys 综合
    # ============================================================
    def _run_yosys(
        self, circuit_name: str, params: dict, run_dir: str
    ) -> dict:
        """运行 Yosys 综合（S2 合规：优先 pyosys in-process）。

        调用层级（按 S2 优先）：
        1. pyosys in-process 调用（符合 S2 Python 原生）
        2. subprocess yosys -s（降级方案，需进程隔离时使用）

        Args:
            circuit_name: 电路名称
            params: 设计参数（CLK_PERIOD, TOP_MODULE 等）
            run_dir: 运行目录

        Returns:
            {"netlist_path": ..., "stdout": ..., "stderr": ...}
        """
        netlist_path = f"{run_dir}/output/{circuit_name}_synth.v"

        # ---- 尝试 pyosys in-process（S2 合规）----
        top_module = params.get("TOP_MODULE", circuit_name)
        liberty = params.get("LIBERTY_PATH", "")
        verilog_src = params.get("VERILOG_SRC", f"./verilog/{circuit_name}.v")

        pyosys_result = self._try_pyosys_inprocess(
            verilog_src, top_module, liberty, netlist_path,
        )
        if pyosys_result is not None:
            return pyosys_result

        # ---- 降级: subprocess 调用（pyosys 不可用时） ----
        return self._run_yosys_subprocess(
            circuit_name, params, run_dir, netlist_path,
        )

    def _try_pyosys_inprocess(
        self, verilog_src: str, top_module: str, liberty: str, netlist_path: str,
    ) -> Optional[dict]:
        """尝试使用 pyosys in-process 调用 Yosys（S2 合规）。

        文档风险提示: pyosys 接口历史上变动较频繁，若编译失败或 import 失败，
        返回 None 让调用方降级到 subprocess。

        Returns:
            成功时返回结果字典，失败时返回 None
        """
        try:
            import pyosys
            # pyosys 的 API 可能因版本不同而变化
            # 尝试标准调用模式
            ys = pyosys.Yosys()

            # 读取并综合
            ys.run(f"read_verilog {verilog_src}")
            ys.run(f"hierarchy -top {top_module}")
            ys.run("proc")
            ys.run("opt")
            ys.run("fsm")
            ys.run("opt")
            ys.run("memory")
            ys.run("opt")
            if params.get("ASYNC2SYNC"):
                # 工艺库无异步复位 DFF 时 (如 asap7) → 转同步复位 + 复位 mux
                ys.run("async2sync")
            ys.run("techmap")
            ys.run("opt")

            if params.get("DFF_MAP_FILE"):
                # 工艺库无 ff() 属性 (如 asap7): techmap 直接映射内部 DFF → 库单元
                ys.run(f"techmap -map {params['DFF_MAP_FILE']}")
            elif liberty:
                ys.run(f"dfflibmap -liberty {liberty}")
            if params.get("NO_ABC"):
                # 绕过 abc: 组合门直接 techmap 到库单元
                # (asap7 liberty 的 SCL 会触发本机 abc 断言崩溃, 质量低于 abc 但功能正确)
                ys.run(f"techmap -map {params['COMB_MAP_FILE']}")
            elif liberty:
                ys.run(f"abc -liberty {liberty}")
            else:
                ys.log("# abc skipped: no liberty file")
            if params.get("HILO_HI"):
                # 常量 0/1 映射到 tie cell — 否则 OpenROAD 把常量当 POWER 网络, TritonRoute 拒绝布线
                ys.run(f"hilomap -hicell {params['HILO_HI']} {params.get('HILO_HI_PORT', 'Y')} "
                       f"-locell {params['HILO_LO']} {params.get('HILO_LO_PORT', 'Y')}")
                # tie 实例重命名为可预测名字 (供 OpenROAD global_connect 按 inst_pattern 连接)
                for cell in (params.get("TIE_RENAME") or "").split():
                    ys.run(f"rename -enumerate -pattern {{{cell}_%d}} t:{cell}")

            ys.run("opt")
            ys.run("clean")
            if liberty and not params.get("NO_ABC"):
                # 面积指标 (Chip area for module ...) — 收敛历史图/PPA 数据源
                # NO_ABC 路径单元跨多个库, stat 单库无意义 → 跳过 (面积由后端物理流程报)
                ys.run(f"stat -liberty {liberty}")
            # -noattr: 网表供 iEDA/OpenSTA 消费, (* src = ... *) 属性会让 iEDA 解析器崩溃
            ys.run(f"write_verilog -noattr {netlist_path}")
            try:
                log = ys.log() or ""
            except Exception:
                log = ""  # pyosys API 差异: 拿不到日志就留空, 不影响综合结果

            return {
                "netlist_path": netlist_path,
                "stdout": log[-4000:],
                "stderr": "",
                "method": "pyosys_inprocess",
            }

        except ImportError:
            return None  # pyosys 未编译/未安装
        except AttributeError:
            return None  # pyosys API 不兼容
        except Exception:
            return None  # 其他异常，降级

    def _run_yosys_subprocess(
        self, circuit_name: str, params: dict, run_dir: str, netlist_path: str,
    ) -> dict:
        """subprocess 降级方案：调用 yosys -s（与 S2 的 subprocess 降级对齐）。"""
        ys_script_path = f"{run_dir}/input/synth.ys"

        # ---- 优先使用模板文件 ----
        template_path = f"{self.template_dir}/{circuit_name}.ys"
        if os.path.exists(template_path):
            import jinja2
            with open(template_path, "r") as f:
                template_str = f.read()
            template = jinja2.Template(template_str)
            rendered = template.render(
                circuit_name=circuit_name, output_path=netlist_path, **params,
            )
            with open(ys_script_path, "w") as f:
                f.write(rendered)
        else:
            # ---- 自动生成 Yosys 脚本 ----
            top_module = params.get("TOP_MODULE", circuit_name)
            liberty = params.get("LIBERTY_PATH", "")
            verilog_src = params.get("VERILOG_SRC", f"./verilog/{circuit_name}.v")

            ys_commands = [
                f"read_verilog {verilog_src}",
                f"hierarchy -top {top_module}",
                "proc; opt; fsm; opt; memory; opt",
            ]
            if params.get("ASYNC2SYNC"):
                # 工艺库无异步复位 DFF 时 (如 asap7) → 转同步复位 + 复位 mux
                ys_commands.append("async2sync")
            ys_commands.append("techmap; opt")
            if params.get("DFF_MAP_FILE"):
                # 工艺库无 ff() 属性 (如 asap7): techmap 直接映射内部 DFF → 库单元
                ys_commands.append(f"techmap -map {params['DFF_MAP_FILE']}")
            elif liberty:
                ys_commands.append(f"dfflibmap -liberty {liberty}")
            if params.get("NO_ABC"):
                # 绕过 abc: 组合门直接 techmap 到库单元
                # (asap7 liberty 的 SCL 会触发本机 abc 断言崩溃, 质量低于 abc 但功能正确)
                ys_commands.append(f"techmap -map {params['COMB_MAP_FILE']}")
            elif liberty:
                ys_commands.append(f"abc -liberty {liberty}")
            else:
                ys_commands.append("# abc skipped: no liberty file")
            if params.get("HILO_HI"):
                # 常量 0/1 映射到 tie cell — 否则 OpenROAD 把常量当 POWER 网络, TritonRoute 拒绝布线
                ys_commands.append(f"hilomap -hicell {params['HILO_HI']} {params.get('HILO_HI_PORT', 'Y')} "
                                   f"-locell {params['HILO_LO']} {params.get('HILO_LO_PORT', 'Y')}")
                # tie 实例重命名为可预测名字 (供 OpenROAD global_connect 按 inst_pattern 连接)
                for cell in (params.get("TIE_RENAME") or "").split():
                    ys_commands.append(f"rename -enumerate -pattern {{{cell}_%d}} t:{cell}")

            ys_commands.append("opt; clean")
            if liberty and not params.get("NO_ABC"):
                # 面积指标 (Chip area for module ...) — 收敛历史图/PPA 数据源
                # NO_ABC 路径单元跨多个库, stat 单库无意义 → 跳过 (面积由后端物理流程报)
                ys_commands.append(f"stat -liberty {liberty}")
            ys_commands.append(f"write_verilog -noattr {netlist_path}")
            with open(ys_script_path, "w") as f:
                f.write("\n".join(ys_commands))

        cmd = [self.yosys_path, "-s", ys_script_path]

        try:
            result = subprocess.run(
                cmd, cwd=run_dir, capture_output=True, text=True,
                timeout=self.synthesis_timeout,
            )
            if result.returncode != 0 and not os.path.exists(netlist_path):
                raise BackendExecutionError(
                    f"Yosys 综合失败 (returncode={result.returncode}): "
                    f"{result.stderr[-500:]}"
                )
            return {
                "netlist_path": netlist_path,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "method": "subprocess",
            }
        except subprocess.TimeoutExpired as e:
            raise BackendExecutionError(
                f"Yosys 综合超时 ({self.synthesis_timeout}s): {circuit_name}"
            ) from e
        except FileNotFoundError:
            raise BackendExecutionError(
                f"Yosys 未找到: {self.yosys_path}"
            )

    # ============================================================
    # iSTA 时序分析（通过 iEDA 子进程）
    # ============================================================
    def _run_ista(
        self, netlist_path: str, run_dir: str, params: dict
    ) -> dict:
        """通过 iEDA 的 STA 功能做时序分析。

        使用子进程调用 iEDA 的 STA Tcl 脚本，解析输出报告。

        Args:
            netlist_path: Yosys 输出的网表路径
            run_dir: 运行目录
            params: 设计参数

        Returns:
            {"report_path": ..., "metrics": {...}, "success": bool, ...}
        """
        # 生成 STA Tcl 脚本
        tcl_path = f"{run_dir}/input/sta.tcl"
        report_path = f"{run_dir}/output/timing.rpt"

        tcl_content = self._generate_sta_tcl(
            netlist_path=netlist_path,
            report_path=report_path,
            params=params,
        )
        with open(tcl_path, "w") as f:
            f.write(tcl_content)

        try:
            result = subprocess.run(
                [self.ieda_bin, "-script", tcl_path],
                cwd=run_dir,
                capture_output=True,
                text=True,
                timeout=self.sta_timeout,
            )

            # 解析 STA 报告提取指标
            metrics = {}
            if os.path.exists(report_path):
                metrics = self._parse_sta_report(report_path)

            # iEDA 崩溃或失败时，尝试从 stdout 解析
            if not metrics or result.returncode != 0:
                stdout_metrics = self._parse_sta_from_stdout(result.stdout)
                metrics.update(stdout_metrics)

            return {
                "report_path": report_path if os.path.exists(report_path) else "",
                "metrics": metrics,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "success": result.returncode == 0,
            }

        except subprocess.TimeoutExpired as e:
            raise BackendExecutionError(
                f"iSTA 时序分析超时 ({self.sta_timeout}s)"
            ) from e
        except FileNotFoundError:
            # iEDA 不可用时降级到 OpenSTA
            return self._run_opensta(netlist_path, run_dir, params)
        except Exception:
            # iEDA 其他异常（如 crash），降级到 OpenSTA
            return self._run_opensta(netlist_path, run_dir, params)

    # ============================================================
    # OpenSTA 时序分析（兼容后端）
    # ============================================================
    def _run_opensta(
        self, netlist_path: str, run_dir: str, params: dict
    ) -> dict:
        """通过 OpenSTA 做时序分析。

        生成 Tcl 脚本 → 启动 opensta 子进程 → 解析报告。

        Args:
            netlist_path: 网表路径
            run_dir: 运行目录
            params: 设计参数

        Returns:
            {"report_path": ..., "metrics": {...}, "success": bool, ...}
        """
        report_path = f"{run_dir}/output/timing.rpt"
        tcl_path = f"{run_dir}/input/sta.tcl"

        tcl_content = self._generate_sta_tcl(
            netlist_path=netlist_path,
            report_path=report_path,
            params=params,
        )
        with open(tcl_path, "w") as f:
            f.write(tcl_content)

        try:
            result = subprocess.run(
                [self.opensta_path, "-no_init", tcl_path],
                cwd=run_dir,
                capture_output=True,
                text=True,
                timeout=self.sta_timeout,
            )

            metrics = {}
            if os.path.exists(report_path):
                metrics = self._parse_sta_report(report_path)

            return {
                "report_path": report_path,
                "metrics": metrics,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "success": result.returncode == 0,
            }

        except subprocess.TimeoutExpired as e:
            raise BackendExecutionError(
                f"OpenSTA 时序分析超时 ({self.sta_timeout}s)"
            ) from e
        except FileNotFoundError:
            # OpenSTA 也不可用，返回占位数据（不阻塞流程）
            return {
                "report_path": "",
                "metrics": {"wns": float("nan"), "tns": float("nan"),
                            "leakage_power": float("nan"), "total_area": float("nan")},
                "stdout": "",
                "stderr": "",
                "success": False,
            }

    # ============================================================
    # 辅助方法
    # ============================================================
    def _generate_sta_tcl(
        self, netlist_path: str, report_path: str, params: dict
    ) -> str:
        """生成 STA Tcl 脚本。

        Args:
            netlist_path: 网表路径
            report_path: 报告输出路径
            params: 设计参数

        Returns:
            Tcl 脚本字符串
        """
        liberty = params.get("LIBERTY_PATH", "/opt/pdk/sky130/sky130_tt.lib")
        top_module = params.get("TOP_MODULE", "gcd")
        clk_period = params.get("CLK_PERIOD", 10.0)
        sdc_path = params.get("SDC_PATH", "")

        lines = [
            f'read_liberty {liberty}',
            f'read_verilog {netlist_path}',
            f'link_design {top_module}',
        ]

        if sdc_path and os.path.exists(sdc_path):
            lines.append(f'read_sdc {sdc_path}')
        else:
            lines.extend([
                f'create_clock -period {clk_period} [get_ports clk]',
                'set_input_delay 0.1 [all_inputs]',
                'set_output_delay 0.1 [all_outputs]',
            ])

        lines.extend([
            'report_checks -path_delay min_max -format full_clock_expanded',
            f'redirect {report_path} {{ report_checks -path_delay min_max }}',
            'exit',
        ])

        return "\n".join(lines)

    def _parse_sta_report(self, report_path: str) -> dict:
        """解析 STA 报告，提取关键指标。

        从 OpenSTA / iSTA 文本报告中提取 WNS, TNS, 泄漏功耗等。

        Args:
            report_path: STA 报告文件路径

        Returns:
            指标字典 {"wns": ..., "tns": ..., "leakage_power": ..., "total_area": ...}
        """
        metrics = {
            "wns": float("nan"),
            "tns": float("nan"),
            "leakage_power": float("nan"),
            "total_area": float("nan"),
        }

        try:
            with open(report_path, "r") as f:
                content = f.read()
        except (FileNotFoundError, PermissionError):
            return metrics

        # ---- WNS (Worst Negative Slack) ----
        wns_match = re.search(
            r'(?:wns|Worst Negative Slack)[\s:]+(-?[\d.]+)',
            content, re.IGNORECASE
        )
        if wns_match:
            metrics["wns"] = float(wns_match.group(1))

        # ---- TNS (Total Negative Slack) ----
        tns_match = re.search(
            r'(?:tns|Total Negative Slack)[\s:]+(-?[\d.]+)',
            content, re.IGNORECASE
        )
        if tns_match:
            metrics["tns"] = float(tns_match.group(1))

        # ---- 泄漏功耗 ----
        leak_match = re.search(
            r'(?:leakage|Leakage Power)[\s:]+([\d.eE+-]+)',
            content, re.IGNORECASE
        )
        if leak_match:
            metrics["leakage_power"] = float(leak_match.group(1))

        # ---- 面积 ----
        area_match = re.search(
            r'(?:area|Total Area|Design Area)[\s:]+([\d.eE+-]+)',
            content, re.IGNORECASE
        )
        if area_match:
            metrics["total_area"] = float(area_match.group(1))

        return metrics

    def _parse_sta_from_stdout(self, stdout: str) -> dict:
        """从 STA 的 stdout 解析时序指标（当没有 report 文件时）。

        Args:
            stdout: STA 工具的 stdout 输出

        Returns:
            指标字典
        """
        metrics = {
            "wns": float("nan"),
            "tns": float("nan"),
            "leakage_power": float("nan"),
            "total_area": float("nan"),
        }

        # 尝试匹配 slack 行
        for line in stdout.split("\n"):
            if "slack" in line.lower():
                match = re.search(r'(-?[\d.]+)', line)
                if match:
                    val = float(match.group(1))
                    if val < 0 and (metrics["wns"] is None or val < (metrics["wns"] or 0)):
                        metrics["wns"] = val

        return metrics
