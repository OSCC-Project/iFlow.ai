# ieda_runner.py —— iEDA 数字全流程后端
# 调用层级（S2 合规）:
#   1. ieda_py in-process（优先）→ import ieda_py
#   2. subprocess（降级）→ ./iEDA -script xxx.tcl
import os
import re
import subprocess
import sys
from typing import Optional
from uuid import uuid4

from .runner import Backend, BackendExecutionError

# ieda_py in-process 调用 — 通过子进程隔离 C++ 退出 crash
# 用法: 直接调 ieda_runner，内部自动用 multiprocessing 包装 ieda_py
_IEDA_PY = None
_IEDA_PY_LOADED = False

def _run_ieda_py_in_subprocess(params: dict, result_dir: str) -> Optional[dict]:
    """在子进程中调用 ieda_py，父进程收集结果。
    C++ 退出 crash 只影响子进程，父进程安全。
    """
    import multiprocessing, pickle, tempfile

    def _worker(pipe_send, params_pickle, result_dir):
        """子进程: import ieda_py → 执行 → 通过 pipe 发结果 → os._exit 跳过析构"""
        import os as _os
        # 在 C++ 析构之前直接退出, 跳过 double-free
        try:
            params = pickle.loads(params_pickle)
            if "/home/xu/iEDA/bin" not in sys.path:
                sys.path.insert(0, "/home/xu/iEDA/bin")
            import ieda_py

            top = params.get("DESIGN_TOP", params.get("circuit_name", "gcd"))
            netlist = params.get("NETLIST_FILE", "")
            foundry = params.get("FOUNDRY_DIR", "")
            flows = params.get("flows", ["floorplan"])

            ieda_py.flow_init()
            ieda_py.init_log()

            if foundry:
                lef_dir = os.path.join(foundry, "lef")
                if os.path.isdir(lef_dir):
                    for f in sorted(os.listdir(lef_dir)):
                        if f.endswith('.tlef'):
                            ieda_py.tech_lef_init(os.path.join(lef_dir, f))
                    for f in sorted(os.listdir(lef_dir)):
                        if f.endswith('.lef') and 'merged' in f.lower():
                            ieda_py.lef_init(os.path.join(lef_dir, f))

            if netlist and os.path.exists(netlist):
                ieda_py.verilog_init(netlist)
            ieda_py.create_data_flow()

            if "floorplan" in flows:
                ieda_py.init_floorplan()
                ieda_py.auto_place_pins()

            if "sta" in flows or "STA" in flows:
                ieda_py.init_sta()
                ieda_py.run_sta()

            ieda_py.flow_exit()

            result = {"success": True, "method": "ieda_py_inprocess"}
        except Exception as e:
            result = {"success": False, "error": str(e)}
        finally:
            pipe_send.send(pickle.dumps(result))
            pipe_send.close()
            _os._exit(0)  # 跳过 C++ 析构

    recv, send = multiprocessing.Pipe(duplex=False)
    p = multiprocessing.Process(
        target=_worker,
        args=(send, pickle.dumps(params), result_dir),
    )
    p.start()
    send.close()

    # 父进程等待结果
    try:
        data = recv.recv()
        result = pickle.loads(data)
    except EOFError:
        result = {"success": False, "error": "子进程异常退出"}
    finally:
        recv.close()
        p.join(timeout=5)
        if p.is_alive():
            p.terminate()
    return result


def _get_ieda_py():
    """返回可用的 ieda_py 调用方式。始终可用（通过子进程隔离）。"""
    global _IEDA_PY, _IEDA_PY_LOADED
    if _IEDA_PY_LOADED:
        return _IEDA_PY
    _IEDA_PY_LOADED = True
    # 返回一个 sentinel 表示"子进程模式可用"
    _IEDA_PY = True  # True 表示子进程包装已就绪
    return _IEDA_PY


class IEDARunner(Backend):
    """iEDA 数字物理设计全流程后端（S2: 优先 ieda_py in-process）。

    通过 iEDA 工具链完成从 RTL 到 GDS 的全流程：
        floorplan → PDN → fixFanout → placement → CTS →
        timingOpt → legalization → routing → filler → DRC

    iSTA 时序分析在 timingOpt 阶段通过 ieda_py.run_sta() 执行。
    """

    # 默认流程步骤
    DEFAULT_FLOW = [
        "floorplan",
        "fixFanout",
        "placement",
        "CTS",
        "optDrv",
        "optHold",
        "legalization",
        "routing",
        "filler",
    ]

    def __init__(self, config: dict):
        # ---- iEDA 可执行文件 ----
        self.ieda_bin = config.get("ieda_bin", "/home/xu/iEDA/bin/iEDA")
        self.script_dir = config.get("script_dir", "")

        # ---- 运行参数 ----
        self.working_dir = config.get("working_dir", "./tmp/ieda_runs/")
        self.timeout_per_step = config.get("timeout_per_step", 1800)

        # ---- 工艺/PDK ----
        self.foundry_dir = config.get("foundry_dir", "/opt/pdk/sky130")
        self.config_dir = config.get("config_dir", "")

        # ---- iEDA 设计目录（Tcl 脚本的 cwd） ----
        self.design_dir = config.get("design_dir", "")

        # ---- 流程控制 ----
        flows_cfg = config.get("flows") or (config.get("ieda") or {}).get("flows")
        self.flows = flows_cfg or self.DEFAULT_FLOW

        # ---- 强制 subprocess (跳过 in-process ieda_py) ----
        self.force_subprocess = config.get("force_subprocess", False)

        # ---- AiEDA Python 绑定（可选） ----
        self.use_aieda = config.get("use_aieda_binding", False)

    def execute(
        self,
        circuit_name: str,
        params: dict,
        analyses: Optional[list] = None,
    ) -> dict:
        """运行 iEDA 数字全流程（S2: 优先 ieda_py in-process）。"""

        # ── 尝试 ieda_py in-process（S2 合规）──
        # 仅 floorplan/sta 有 in-process 实现; 其余步骤必须走 subprocess,
        # 否则 in-process 会"空转成功" (假成功)。
        if not self.force_subprocess:
            ieda_py = _get_ieda_py()
            if ieda_py is not None:
                inproc_result = self._try_inprocess(circuit_name, params)
                if inproc_result is not None:
                    return inproc_result

        # ── 降级: subprocess ──
        return self._execute_subprocess(circuit_name, params)

    def _try_inprocess(self, circuit_name: str, params: dict) -> Optional[dict]:
        """使用 ieda_py 执行（子进程隔离，避免 C++ 退出 crash）。

        通过 multiprocessing 在子进程中调用 ieda_py，父进程安全收集结果。
        子进程通过 os._exit(0) 跳过 C++ 全局析构。

        Returns:
            成功时返回结果字典，失败时返回 None（触发降级到 subprocess）
        """
        # in-process 只实现了 floorplan / sta; 其余 flow 会空转, 直接降级
        requested = set(params.get("flows", self.flows))
        if not requested.issubset({"floorplan", "sta"}):
            return None
        result_dir = params.get("RESULT_DIR", f"/tmp/ieda_inproc/{uuid4().hex[:8]}")
        os.makedirs(result_dir, exist_ok=True)

        result = _run_ieda_py_in_subprocess(params, result_dir)
        if result and result.get("success"):
            return {
                "run_dir": result_dir,
                "netlist_path": params.get("NETLIST_FILE", ""),
                "stdout": "ieda_py in-process (subprocess isolation)",
                "stderr": "",
                "returncode": 0,
                "params": params,
                "method": "ieda_py_inprocess_isolated",
            }
        return None  # 触发降级到 subprocess

    def _execute_subprocess(self, circuit_name: str, params: dict) -> dict:
        # RUN_DIR 允许同一设计的多步骤共享输出目录 (DEF 链传)
        run_dir = params.get("RUN_DIR") or f"{self.working_dir}/{uuid4().hex[:8]}/"
        os.makedirs(run_dir, exist_ok=True)

        # ---- 设置环境变量 ----
        env = self._build_env(params, run_dir)

        # ---- 确定流程步骤 ----
        flows = params.get("flows", self.flows)
        skip_steps = set(params.get("skip_steps", []))

        # ---- 执行流程（追踪 DEF 链传）----
        all_stdout, all_stderr = "", ""
        sta_metrics = {}
        prev_def = {}  # stage→output DEF path
        flow_success = True

        for step in flows:
            if step in skip_steps:
                continue

            tcl_path = self._get_tcl_script(step, run_dir, params)
            if not tcl_path:
                continue

            # iEDA 需要从 design_dir 运行才能找到相对路径依赖
            work_cwd = self.design_dir or run_dir
            try:
                result = subprocess.run(
                    [self.ieda_bin, "-script", tcl_path],
                    cwd=work_cwd,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_per_step,
                )

                all_stdout += f"\n=== {step} ===\n{result.stdout}"
                all_stderr += f"\n=== {step} ===\n{result.stderr}"

                if result.returncode != 0:
                    flow_success = False
                    # 不立即中断，让后续步骤有机会运行

            except subprocess.TimeoutExpired:
                flow_success = False
                all_stderr += f"\n=== {step} TIMEOUT ===\n"

        # ---- 解析 STA 结果 ----
        # iSTA 报告实际名为 {design}.rpt (表格格式), 也可能为 timing.rpt/log
        result_dir = params.get("RESULT_DIR", f"{run_dir}/result")
        sta_report_path = ""
        sta_metrics = {}
        candidates = []
        sta_dir = os.path.join(result_dir, "sta")
        if os.path.isdir(sta_dir):
            candidates += [os.path.join(sta_dir, n) for n in ("timing.rpt", "timing.log")]
            candidates += [os.path.join(sta_dir, fn) for fn in sorted(os.listdir(sta_dir))
                           if fn.endswith(".rpt")]
        for cand in candidates:
            # iEDA init_sta -output 可能把 timing.log 建成目录 → 只解析文件
            if os.path.isfile(cand):
                sta_report_path = cand
                sta_metrics = self._parse_sta_report(cand)
                # 通用关键词解析不到 → 尝试 iSTA 表格格式
                if sta_metrics["wns"] != sta_metrics["wns"]:  # NaN
                    try:
                        with open(cand) as f:
                            extra = self._parse_ista_table(f.read())
                        for k, v in extra.items():
                            if v == v:  # 非 NaN 才覆盖
                                sta_metrics[k] = v
                    except OSError:
                        pass
                break
        if not sta_report_path:
            # 尝试从 stdout 中解析 WNS/TNS
            sta_metrics = self._parse_sta_from_stdout(all_stdout)

        # ---- 解析 DRC 结果 ----
        drc_metrics = {}
        if any("drc" in f.lower() for f in flows):
            drc_metrics = self._parse_drc_result(result_dir, all_stdout)

        return {
            "run_dir": run_dir,
            "netlist_path": params.get("NETLIST_FILE", ""),
            "sta_report": sta_report_path,
            "sta": sta_metrics,
            "drc": drc_metrics,
            "success": flow_success,
            "stdout": all_stdout,
            "stderr": all_stderr,
            "returncode": 0 if flow_success else 1,
            "flows_executed": [f for f in flows if f not in skip_steps],
            "params": params,
        }

    # ============================================================
    # 环境变量构建
    # ============================================================
    def _build_env(self, params: dict, run_dir: str) -> dict:
        """构建 iEDA 所需的环境变量。

        Args:
            params: 设计参数
            run_dir: 运行目录

        Returns:
            包含所有必要环境变量的字典
        """
        foundry_dir = params.get("FOUNDRY_DIR", self.foundry_dir)
        result_dir = params.get("RESULT_DIR", f"{run_dir}/result")
        config_dir = params.get("CONFIG_DIR", self.config_dir)
        script_dir = params.get("TCL_SCRIPT_DIR", run_dir)

        env = os.environ.copy()

        env.update({
            "DESIGN_TOP": params.get("DESIGN_TOP", "gcd"),
            "NETLIST_FILE": params.get("NETLIST_FILE", ""),
            "SDC_FILE": params.get("SDC_FILE", ""),
            # db_init_sdc.tcl 读取 SDC_PATH
            "SDC_PATH": params.get("SDC_PATH", params.get("SDC_FILE", "")),
            "SPEF_FILE": params.get("SPEF_FILE", ""),
            "INPUT_DEF": params.get("INPUT_DEF", ""),
            "GDS_FILE": params.get("GDS_FILE", ""),
            "DIE_AREA": params.get("DIE_AREA", "0.0 0.0 150.0 150.0"),
            "CORE_AREA": params.get("CORE_AREA", "10.0 10.0 140.0 140.0"),
            "FOUNDRY_DIR": foundry_dir,
            "CONFIG_DIR": config_dir,
            "RESULT_DIR": result_dir,
            "TCL_SCRIPT_DIR": script_dir,
        })

        return env

    # ============================================================
    # Tcl 脚本路由
    # ============================================================
    # step名 → 已有 Tcl 脚本路径（相对 script_dir）
    _STEP_SCRIPT = {
        "floorplan":    "iFP_script/run_iFP.tcl",
        "tapcell":      "iFP_script/run_iFP.tcl",       # iEDA 无独立 tapcell
        "pdn":          "iPNP/run_iPNP.tcl",
        "fixFanout":    "iNO_script/run_iNO_fix_fanout.tcl",
        "gplace":       "iPL_script/run_iPL.tcl",
        "resize":       "iTO_script/run_iTO_drv.tcl",   # resize ≈ timing opt
        "dplace":       "iPL_script/run_iPL_legalization.tcl",
        "cts":          "iCTS_script/run_iCTS.tcl",
        "groute":       "iRT_script/run_iRT.tcl",
        "droute":       "iRT_script/run_iRT.tcl",       # 同 iRT
        "filler":       "iPL_script/run_iPL_filler.tcl",
        "drc":          "iDRC_script/run_iDRC.tcl",
        "gds":          "DB_script/run_def_to_gds_text.tcl",
        # 旧名兼容
        "placement":    "iPL_script/run_iPL.tcl",
        "CTS":          "iCTS_script/run_iCTS.tcl",
        "routing":      "iRT_script/run_iRT.tcl",
        "STA":          "iSTA_script/run_iSTA.tcl",
    }

    def _get_tcl_script(self, step: str, run_dir: str, params: dict) -> str:
        """根据流程步骤获取 Tcl 脚本路径。

        优先使用已有的 iEDA Tcl 脚本，降级为自动生成。
        """
        os.makedirs(f"{run_dir}/scripts", exist_ok=True)

        # STA 指定 INPUT_DEF 时生成专用脚本 (官方脚本硬编码 iPL_result.def)
        if step.lower() == "sta" and params.get("INPUT_DEF"):
            tcl_path = f"{run_dir}/scripts/run_iSTA_input.tcl"
            with open(tcl_path, "w") as f:
                f.write(self._generate_sta_input_tcl())
            return tcl_path

        # 如果 script_dir 已配置，按映射查找已有脚本
        if self.script_dir:
            rel = self._STEP_SCRIPT.get(step, f"run_{step}.tcl")
            candidate = os.path.join(self.script_dir, rel)
            if os.path.exists(candidate):
                return candidate
            # 也尝试直接匹配
            candidate2 = os.path.join(self.script_dir, f"run_{step}.tcl")
            if os.path.exists(candidate2):
                return candidate2

        # 自动生成 Tcl 脚本
        tcl_path = f"{run_dir}/scripts/run_{step}.tcl"
        tcl_content = self._generate_default_tcl(step)

        if tcl_content:
            with open(tcl_path, "w") as f:
                f.write(tcl_content)
            return tcl_path

        return ""

    def _generate_sta_input_tcl(self) -> str:
        """生成读取指定 DEF 的 iSTA 脚本 (与官方 run_iSTA.tcl 等价,
        但 def_init 使用 INPUT_DEF 环境变量)。注意必须用 run_sta 单命令
        (init_sta + report_sta 组合会在 set_instance_flip_flop 段错误)。"""
        return """
#===========================================================
##   init flow config
#===========================================================
flow_init -config $::env(CONFIG_DIR)/flow_config.json

#===========================================================
##   read db config
#===========================================================
db_init -config $::env(CONFIG_DIR)/db_default_config.json -output_dir_path $::env(RESULT_DIR)

#===========================================================
##   reset data path
#===========================================================
source $::env(TCL_SCRIPT_DIR)/DB_script/db_path_setting.tcl

#===========================================================
##   reset lib
#===========================================================
source $::env(TCL_SCRIPT_DIR)/DB_script/db_init_lib.tcl

#===========================================================
##   reset sdc
#===========================================================
source $::env(TCL_SCRIPT_DIR)/DB_script/db_init_sdc.tcl

#===========================================================
##   read lef
#===========================================================
source $::env(TCL_SCRIPT_DIR)/DB_script/db_init_lef.tcl

#===========================================================
##   read def (输入 DEF 由 INPUT_DEF 指定)
#===========================================================
def_init -path $::env(INPUT_DEF)

#===========================================================
##   run STA
#===========================================================
run_sta -output $::env(RESULT_DIR)/sta/

#===========================================================
##   Exit
#===========================================================
flow_exit
""".strip()

    def _generate_default_tcl(self, step: str) -> str:
        """为指定步骤生成默认 Tcl 脚本。

        使用环境变量来参数化脚本内容。

        Args:
            step: 流程步骤名

        Returns:
            Tcl 脚本内容
        """
        step_templates = {
            "floorplan": """
# iFP: Floorplan
set DIE_AREA $::env(DIE_AREA)
set CORE_AREA $::env(CORE_AREA)
create_die_area -area "$DIE_AREA"
create_core_area -area "$CORE_AREA"
place_io -pin_thickness 10
save_def "$::env(RESULT_DIR)/gcd_floorplan.def"
""",
            "fixFanout": """
# iNO: Fix Fanout
set MAX_FANOUT 32
fix_fanout -max_fanout $MAX_FANOUT
save_def "$::env(RESULT_DIR)/gcd_fixfanout.def"
""",
            "placement": """
# iPL: Placement
run_placement
save_def "$::env(RESULT_DIR)/gcd_placed.def"
""",
            "CTS": """
# iCTS: Clock Tree Synthesis
set CLK_PORT "clk"
run_cts -clock $CLK_PORT
save_def "$::env(RESULT_DIR)/gcd_cts.def"
report_sta -output "$::env(RESULT_DIR)/sta/timing.rpt"
""",
            "optDrv": """
# iTO: Timing Optimization - DRV
fix_drv -max_transition 0.5 -max_capacitance 0.5
save_def "$::env(RESULT_DIR)/gcd_optdrv.def"
report_sta -output "$::env(RESULT_DIR)/sta/timing.rpt"
""",
            "optHold": """
# iTO: Timing Optimization - Hold
fix_hold
save_def "$::env(RESULT_DIR)/gcd_opthold.def"
report_sta -output "$::env(RESULT_DIR)/sta/timing.rpt"
""",
            "optSetup": """
# iTO: Timing Optimization - Setup
fix_setup
save_def "$::env(RESULT_DIR)/gcd_optsetup.def"
report_sta -output "$::env(RESULT_DIR)/sta/timing.rpt"
""",
            "legalization": """
# iPL: Legalization
run_legalization
save_def "$::env(RESULT_DIR)/gcd_legalized.def"
""",
            "routing": """
# iRT: Routing
run_routing
save_def "$::env(RESULT_DIR)/gcd_routed.def"
report_drc -output "$::env(RESULT_DIR)/drc/drc.rpt"
""",
            "filler": """
# iPL: Filler
run_filler
save_def "$::env(RESULT_DIR)/gcd_final.def"
""",
        }

        return step_templates.get(step, "").strip()

    # ============================================================
    # STA 报告解析
    # ============================================================
    def _parse_sta_report(self, report_path: str) -> dict:
        """解析 STA 报告，提取关键指标。

        Args:
            report_path: STA 报告文件路径

        Returns:
            指标字典
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
        except OSError:
            return metrics

        # WNS
        wns_match = re.search(
            r'(?:wns|Worst Negative Slack)[\s:]+(-?[\d.]+)',
            content, re.IGNORECASE
        )
        if wns_match:
            metrics["wns"] = float(wns_match.group(1))

        # TNS
        tns_match = re.search(
            r'(?:tns|Total Negative Slack)[\s:]+(-?[\d.]+)',
            content, re.IGNORECASE
        )
        if tns_match:
            metrics["tns"] = float(tns_match.group(1))

        # Leakage
        leak_match = re.search(
            r'(?:leakage|Leakage Power)[\s:]+([\d.eE+-]+)',
            content, re.IGNORECASE
        )
        if leak_match:
            metrics["leakage_power"] = float(leak_match.group(1))

        # Area
        area_match = re.search(
            r'(?:area|Total Area|Design Area)[\s:]+([\d.eE+-]+)',
            content, re.IGNORECASE
        )
        if area_match:
            metrics["total_area"] = float(area_match.group(1))

        return metrics

    def _parse_sta_from_stdout(self, stdout: str) -> dict:
        """从 stdout 解析 STA 指标（fallback）。

        Args:
            stdout: 工具标准输出

        Returns:
            指标字典
        """
        metrics = {
            "wns": float("nan"),
            "tns": float("nan"),
            "leakage_power": float("nan"),
            "total_area": float("nan"),
        }

        for line in stdout.split("\n"):
            if "slack" in line.lower():
                match = re.search(r'(-?[\d.]+)', line)
                if match:
                    val = float(match.group(1))
                    if val < 0 and (metrics["wns"] != metrics["wns"] or
                                    val < metrics["wns"]):
                        metrics["wns"] = val

        return metrics

    def _parse_ista_table(self, content: str) -> dict:
        """解析 iSTA 表格报告 (iEDA 输出, 如 gcd.rpt):
        - 端点表: | Endpoint | Clock Group | Delay Type | ... | Slack | Freq |
          Delay Type: max=setup / min=hold — 只有这两类行是端点汇总行;
          路径明细行 (如 library setup time) 的 Slack 列是路径分量 (与频率无关),
          混入会把恒定的分量值当成 WNS
        - TNS 表: | Clock | Delay Type | TNS |"""
        metrics = {"wns": float("nan"), "tns": float("nan"),
                   "leakage_power": float("nan"), "total_area": float("nan")}
        setup_slacks, hold_slacks = [], []
        for line in content.split("\n"):
            if not line.startswith("|"):
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            # 列序: Endpoint | Clock Group | Delay Type | ... | Slack | Freq
            if len(cells) >= 7 and cells[2] in ("max", "min"):
                try:
                    (setup_slacks if cells[2] == "max" else hold_slacks).append(float(cells[6]))
                except ValueError:
                    pass
        if setup_slacks:
            metrics["wns"] = min(setup_slacks)
        if hold_slacks:
            metrics["hold_wns"] = min(hold_slacks)
        tns_vals = []
        for line in content.split("\n"):
            if not line.startswith("|"):
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) == 3 and cells[0].lower() != "clock":
                try:
                    tns_vals.append(float(cells[2]))  # TNS 列
                except ValueError:
                    pass
        if tns_vals:
            metrics["tns"] = min(tns_vals)
        return metrics

    def _parse_drc_result(self, result_dir: str, stdout: str) -> dict:
        """解析 iEDA DRC 结果 — 从 detail.json / drc.rpt / stdout 统计违规数。"""
        # 1. detail.json (iDRC 输出: {"drc": {"number": N}} 或违规列表)
        detail_path = os.path.join(result_dir, "drc", "detail.json")
        if os.path.exists(detail_path):
            try:
                import json
                with open(detail_path) as f:
                    data = json.load(f)
                if isinstance(data, list):
                    n = len(data)
                else:
                    n = data.get("drc", {}).get("number")
                return {"violations": n, "source": "detail.json"}
            except Exception:
                pass
        # 2. drc.rpt 文本报告
        for rpt_name in ("drc.rpt", "drc.report"):
            rpt_path = os.path.join(result_dir, "report", rpt_name)
            if os.path.exists(rpt_path):
                with open(rpt_path) as f:
                    content = f.read()
                m = re.search(r'[Tt]otal\s+violations?\s*[:\s]+(\d+)', content)
                if m:
                    return {"violations": int(m.group(1)), "source": rpt_name}
                return {"violations": 0, "source": rpt_name}
        # 3. stdout 兜底: 统计 violation 关键字
        n = sum(1 for line in stdout.split("\n") if "violation" in line.lower())
        return {"violations": n, "source": "stdout"} if n else {"violations": None, "source": "none"}
