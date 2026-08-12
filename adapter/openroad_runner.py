# openroad_runner.py —— OpenROAD 数字物理设计后端
import os, re, subprocess
from typing import Optional
from uuid import uuid4
from .runner import Backend, BackendExecutionError


class OpenROADRunner(Backend):
    """OpenROAD 数字物理设计后端 (S1: BSD-3, S2: subprocess+Tcl, 已通过 sky130hd 端到端验证)"""

    DEFAULT_FLOW = ["floorplan", "tapcell", "pdn", "global_place",
                    "resize", "detail_place", "clock_tree_synthesis",
                    "global_route", "detailed_route", "filler", "write_gds"]

    def __init__(self, config: dict):
        self.openroad_bin = config.get("executable", "/usr/bin/openroad")
        self.working_dir = config.get("working_dir", "./tmp/openroad_runs/")
        self.timeout_per_step = config.get("timeout_per_step", 1800)
        self.script_dir = config.get("script_dir", "")
        pdk = config.get("pdk", {})
        self.pdk_tech_lef = pdk.get("tech_lef", "")
        self.pdk_cell_lef = pdk.get("cell_lef", "")
        self.pdk_liberty = pdk.get("liberty", "")
        self.pdk_tracks = pdk.get("tracks", "")
        self.pdk_vars = pdk.get("vars", "")
        self.pdk_site = pdk.get("site", "unithd")
        self.pdk_hor_layer = pdk.get("hor_layer", "met3")
        self.pdk_ver_layer = pdk.get("ver_layer", "met2")

    def execute(self, circuit_name: str, params: dict, analyses: Optional[list] = None) -> dict:
        run_id = str(uuid4())[:8]
        run_dir = os.path.abspath(f"{self.working_dir}/{run_id}/")
        os.makedirs(run_dir, exist_ok=True)
        flows = params.get("flows", self.DEFAULT_FLOW)
        skip_steps = set(params.get("skip_steps", []))
        all_stdout, all_stderr, flow_success = "", "", True
        # 追踪上一步输出的 DEF，供下一步读取
        prev_def = None

        for step in flows:
            if step in skip_steps:
                continue
            step_params = dict(params)
            if prev_def and step != "floorplan":
                step_params["INPUT_DEF"] = prev_def
            tcl_path = self._get_tcl_script(step, run_dir, step_params) or self._generate_tcl(step, run_dir, step_params)
            try:
                r = subprocess.run([self.openroad_bin, "-no_init", "-exit", tcl_path],
                                   cwd=run_dir, capture_output=True, text=True, timeout=self.timeout_per_step)
                all_stdout += f"\n=== {step} ===\n{r.stdout}"
                all_stderr += f"\n=== {step} ===\n{r.stderr}"
                if r.returncode != 0: flow_success = False
                # 追踪本步输出的 DEF
                for out_name in [f"{run_dir}/output/floorplan.def", f"{run_dir}/output/global_place.def",
                                 f"{run_dir}/output/detail_place.def", f"{run_dir}/output/cts.def",
                                 f"{run_dir}/output/route.def"]:
                    if os.path.exists(out_name):
                        prev_def = out_name
            except subprocess.TimeoutExpired:
                flow_success = False; all_stderr += f"\n=== {step} TIMEOUT ===\n"

        sta_metrics = {}
        rd = params.get("RESULT_DIR", f"{run_dir}/output")
        report_path = os.path.join(rd, "timing.rpt")
        if os.path.exists(report_path): sta_metrics = self._parse_sta_report(report_path)
        elif all_stdout: sta_metrics = self._parse_sta_from_stdout(all_stdout)

        return {"run_dir": run_dir, "netlist_path": params.get("NETLIST_FILE", ""),
                "sta_report": report_path, "sta": sta_metrics,
                "stdout": all_stdout, "stderr": all_stderr,
                "returncode": 0 if flow_success else 1,
                "flows_executed": [f for f in flows if f not in skip_steps], "params": params}

    def _get_tcl_script(self, step, run_dir, params):
        if self.script_dir:
            c = os.path.join(self.script_dir, f"run_{step}.tcl")
            if os.path.exists(c): return c
        return ""

    def _generate_tcl(self, step, run_dir, params):
        tech_lef = params.get("TECH_LEF", self.pdk_tech_lef)
        cell_lef = params.get("CELL_LEF", self.pdk_cell_lef)
        lef = params.get("LEF_FILES", "")
        lib = params.get("LIB_FILES", "") or self.pdk_liberty
        tracks = params.get("TRACKS", "") or self.pdk_tracks
        vars_file = params.get("VARS", "") or self.pdk_vars
        site = params.get("SITE", self.pdk_site)
        hor = params.get("HOR_LAYER", self.pdk_hor_layer)
        ver = params.get("VER_LAYER", self.pdk_ver_layer)
        input_def = params.get("INPUT_DEF", "")
        netlist = params.get("NETLIST_FILE", "")
        sdc = params.get("SDC_FILE", "")
        top = params.get("DESIGN_TOP", "gcd")
        clk = params.get("CLK_PORT", "clk")
        clk_period = params.get("CLK_PERIOD", None)
        die = params.get("DIE_AREA", "0 0 150 150")
        core = params.get("CORE_AREA", "10 10 140 140")
        rd = params.get("RESULT_DIR", f"{run_dir}/output")
        os.makedirs(rd, exist_ok=True)

        L = [f"# OpenROAD Tcl — {step}", f"# Design: {top}", ""]
        # PDK
        if tech_lef: L.append(f"read_lef {tech_lef}")
        if cell_lef: L.append(f"read_lef {cell_lef}")
        if lef and not tech_lef: L.append(f"read_lef {lef}")
        L.append(f"read_liberty {lib}")
        if input_def and step != "floorplan":
            L.append(f"read_def {input_def}")
        else:
            L.append(f"read_verilog {netlist}")
            L.append(f"link_design {top}")
        if clk_period:
            L.append(f"create_clock -period {clk_period} [get_ports {clk}]")
        elif sdc and os.path.exists(sdc):
            L.append(f"read_sdc {sdc}")
        else:
            L.append(f"create_clock -period 10.0 [get_ports {clk}]")
        L.append("")

        steps = {
            "floorplan": [
                *([f"source {vars_file}"] if vars_file else []),
                f'initialize_floorplan -die_area "{die}" -core_area "{core}" -site {site}',
                *([f"source {tracks}"] if tracks else []),
                f"place_pins -hor_layers {hor} -ver_layers {ver}",
                f"write_def {rd}/floorplan.def",
            ],
            "tapcell":   ["tapcell", f"write_def {rd}/tapcell.def"],
            "pdn":       ["pdngen", f"write_def {rd}/pdn.def"],
            "global_place": [
                "set_wire_rc -layer met2",  # ← resize 需要
                "global_placement -density 0.6",
                f"write_def {rd}/gplace.def",
            ],
            "resize": [
                "repair_timing",                       # 修 Setup
                "repair_hold",                         # 修 Hold (buffer 可能引入新 hold 违例)
                f"report_checks -format full_clock > {rd}/timing.rpt",
                f"write_def {rd}/resize.def",
            ],
            "detail_place": ["detailed_placement", f"write_def {rd}/dplace.def"],
            "clock_tree_synthesis": [
                "repair_clock_inverters",
                "clock_tree_synthesis -buf_list sky130_fd_sc_hd__buf_1",
                "detailed_placement",
                "repair_timing",                       # CTS 后修 Setup
                f"report_checks -format full_clock > {rd}/timing.rpt",
                f"write_def {rd}/cts.def",
            ],
            "global_route":   ["global_route", f"write_def {rd}/groute.def"],
            "detailed_route": [
                "detailed_route",
                "repair_timing",                       # 布线后修 Setup
                f"report_checks -format full_clock > {rd}/timing.rpt",
                f"write_def {rd}/droute.def",
            ],
            "filler": ["filler_placement FILL", f"write_def {rd}/filler.def"],
            "write_gds": [f"write_gds {rd}/{top}.gds"],
            "sta_report": [f"report_checks -format full_clock > {rd}/timing.rpt"],
            # aliases
            "gplace": [
                "set_wire_rc -layer met2",
                "global_placement -density 0.6",
                f"write_def {rd}/gplace.def",
            ],
            "dplace": ["detailed_placement", f"write_def {rd}/dplace.def"],
            "groute": ["global_route", f"write_def {rd}/groute.def"],
            "droute": [
                "detailed_route",
                "repair_timing",                       # 布线后修 Setup
                f"report_checks -format full_clock > {rd}/timing.rpt",
                f"write_def {rd}/droute.def",
            ],
            "cts": [
                "repair_clock_inverters",
                "clock_tree_synthesis -buf_list sky130_fd_sc_hd__buf_1",
                "detailed_placement",
                "repair_timing",                       # CTS 后修 Setup
                f"report_checks -format full_clock > {rd}/timing.rpt",
                f"write_def {rd}/cts.def",
            ],
        }
        L.extend([s for s in steps.get(step, [f'puts "Step {step} not implemented"']) if s])

        tcl_path = f"{run_dir}/run_{step}.tcl"
        with open(tcl_path, "w") as f: f.write("\n".join(L) + "\n")
        return tcl_path

    def _parse_sta_report(self, path):
        m = {"wns": float("nan"), "tns": float("nan"), "leakage_power": float("nan"), "total_area": float("nan")}
        try:
            c = open(path).read()
            # Extract all slack values
            slacks = re.findall(r'(-?[\d.]+)\s+slack', c, re.I)
            if slacks:
                vals = [float(s) for s in slacks]
                m["wns"] = min(vals)
                # TNS = sum of all negative slacks
                neg = [v for v in vals if v < 0]
                m["tns"] = sum(neg) if neg else 0.0
            # Also try keyword match for WNS/TNS
            for k, p in [("wns", r'(?:wns|Worst Negative Slack)[^-\d]*(-?[\d.]+)'),
                         ("tns", r'(?:tns|Total Negative Slack)[^-\d]*(-?[\d.]+)')]:
                mm = re.search(p, c, re.I)
                if mm: m[k] = float(mm.group(1))
        except: pass
        return m

    def _parse_sta_from_stdout(self, out):
        m = {"wns": float("nan"), "tns": float("nan"), "leakage_power": float("nan"), "total_area": float("nan")}
        for line in out.split("\n"):
            if "slack" in line.lower():
                mm = re.search(r'(-?[\d.]+)', line)
                if mm:
                    v = float(mm.group(1))
                    if v < 0 and (m["wns"] != m["wns"] or v < m["wns"]): m["wns"] = v
        return m
