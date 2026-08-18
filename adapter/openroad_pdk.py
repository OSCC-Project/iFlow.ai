"""OpenROAD 多工艺物理流程 — nangate45 / asap7 接入 (方案 Sheet 3 工具替换维度: physical=openroad)

参数取自本机 OpenROAD-flow-scripts (ORFS) 平台配置, 生成自包含全流程 TCL
(不依赖 ORFS Makefile 环境; make_tracks/pdn grid strategy 直接 source 平台文件)。
sky130 仍走 iEDA (口径与 aes11 参考报告一致), 对比实验表用「工具」列如实标注。
"""
import os, re, subprocess

ORFS = "/home/xu/OpenROAD-flow-scripts/flow/platforms"
ORFS_UTIL = "/home/xu/OpenROAD-flow-scripts/flow/util"

OR_PDK = {
    "nangate45": {
        "tech_lef": f"{ORFS}/nangate45/lef/NangateOpenCellLibrary.tech.lef",
        "cell_lef": f"{ORFS}/nangate45/lef/NangateOpenCellLibrary.macro.mod.lef",
        "liberty": [f"{ORFS}/nangate45/lib/NangateOpenCellLibrary_typical.lib"],
        "site": "FreePDK45_38x28_10R_NP_162NW_34O",
        "tracks": f"{ORFS}/nangate45/make_tracks.tcl",
        "tap": 'tapcell -distance 120 -tapcell_master "TAPCELL_X1" -endcap_master "TAPCELL_X1"',
        "pdn": f"{ORFS}/nangate45/grid_strategy-M1-M4-M7.tcl",
        "cts_buffer": "CLKBUF_X3",
        "fillers": "FILLCELL_X1 FILLCELL_X2 FILLCELL_X4 FILLCELL_X8 FILLCELL_X16 FILLCELL_X32",
        "rc_signal": "metal2", "rc_clock": "metal5",
        "hor_layer": "metal5", "ver_layer": "metal6",
        # GDS 导出 (KLayout DEF→GDS): 单元 GDS + 层映射
        "gds_files": f"{ORFS}/nangate45/gds/NangateOpenCellLibrary.gds",
        "lyt": f"{ORFS}/nangate45/FreePDK45.lyt",
    },
    "asap7": {
        "tech_lef": f"{ORFS}/asap7/lef/asap7_tech_1x_201209.lef",
        "cell_lef": f"{ORFS}/asap7/lef/asap7sc7p5t_28_R_1x_220121a.lef",
        "liberty": [
            f"{ORFS}/asap7/lib/NLDM/asap7sc7p5t_AO_RVT_TT_nldm_211120.lib.gz",
            f"{ORFS}/asap7/lib/NLDM/asap7sc7p5t_OA_RVT_TT_nldm_211120.lib.gz",
            f"{ORFS}/asap7/lib/NLDM/asap7sc7p5t_INVBUF_RVT_TT_nldm_220122.lib.gz",
            f"{ORFS}/asap7/lib/NLDM/asap7sc7p5t_SEQ_RVT_TT_nldm_220123.lib",
        ],
        "site": "asap7sc7p5t",
        "tracks": f"{ORFS}/asap7/openRoad/make_tracks.tcl",
        "tap": 'tapcell -distance 25 -tapcell_master "TAPCELL_ASAP7_75t_R" -endcap_master "TAPCELL_ASAP7_75t_R"',
        "pdn": f"{ORFS}/asap7/openRoad/pdn/grid_strategy-M1-M2-M5-M6.tcl",
        "cts_buffer": "BUFx2_ASAP7_75t_R",
        "fillers": "FILLERxp5_ASAP7_75t_R",
        "rc_signal": "M2", "rc_clock": "M5",
        "hor_layer": "M4", "ver_layer": "M5",
        # GDS 导出 (KLayout DEF→GDS)
        "gds_files": " ".join(f"{ORFS}/asap7/gds/{f}" for f in sorted(os.listdir(f"{ORFS}/asap7/gds")) if f.endswith(".gds")),
        "lyt": f"{ORFS}/asap7/KLayout/asap7.lyt",
    },
}

# 全流程 TCL 模板: floorplan → tap → pdn → place → CTS → route → filler → 报告 → DEF/GDS
_FLOW_TCL = """# OpenROAD 全流程 ({pdk}) — iflow-lab 自动生成
read_lef {tech_lef}
read_lef {cell_lef}
{liberty_lines}
read_verilog {netlist}
link_design {top}
create_clock -period {clk_period} [get_ports {clk}]

initialize_floorplan -die_area "{die}" -core_area "{core}" -site {site}
source {tracks}
place_pins -hor_layers {hor_layer} -ver_layers {ver_layer}
{tap_cmd}
source {pdn}
pdngen
set_wire_rc -signal -layer {rc_signal}
set_wire_rc -clock -layer {rc_clock}
global_placement -density {density}
repair_timing
detailed_placement
repair_timing
clock_tree_synthesis -buf_list {cts_buffer}
detailed_placement
repair_timing
global_route
detailed_route
repair_timing
filler_placement {{{fillers}}}
report_checks -format full_clock > {rd}/timing.rpt
report_design_area > {rd}/area.rpt
write_def {rd}/route.def
"""


def run_physical_flow(pdk: str, params: dict, working_dir: str) -> dict:
    """跑一个 OpenROAD 全流程, 返回与 iEDA 步骤同构的结果 (success/run_dir/metrics/gds)"""
    prof = OR_PDK.get(pdk)
    if not prof:
        return {"success": False, "error": f"OpenROAD 未配置 PDK: {pdk}"}
    run_id = str(os.urandom(4).hex())
    run_dir = os.path.abspath(f"{working_dir}/{pdk}_{run_id}/")
    rd = os.path.join(run_dir, "output")
    os.makedirs(rd, exist_ok=True)

    for f in ([prof["tech_lef"], prof["cell_lef"], prof["tracks"], prof["pdn"]]
              + prof["liberty"]):
        if not os.path.exists(f):
            return {"success": False, "error": f"PDK 文件缺失: {f}"}

    tcl = _FLOW_TCL.format(
        pdk=pdk, tech_lef=prof["tech_lef"], cell_lef=prof["cell_lef"],
        liberty_lines="\n".join(f"read_liberty {p}" for p in prof["liberty"]),
        netlist=params["NETLIST_FILE"], top=params.get("DESIGN_TOP", "gcd"),
        clk_period=params.get("CLK_PERIOD", 10.0), clk=params.get("CLK_PORT", "clk"),
        die=params.get("DIE_AREA", "0 0 150 150"), core=params.get("CORE_AREA", "10 10 140 140"),
        site=prof["site"], tracks=prof["tracks"], tap_cmd=prof["tap"], pdn=prof["pdn"],
        rc_signal=prof["rc_signal"], rc_clock=prof["rc_clock"],
        hor_layer=prof["hor_layer"], ver_layer=prof["ver_layer"],
        density=params.get("PLACE_DENSITY", 0.6), cts_buffer=prof["cts_buffer"],
        fillers=prof["fillers"], rd=rd,
    )
    tcl_path = os.path.join(run_dir, "flow.tcl")
    with open(tcl_path, "w") as f:
        f.write(tcl)

    try:
        r = subprocess.run(["/usr/bin/openroad", "-no_init", "-exit", tcl_path],
                           cwd=run_dir, capture_output=True, text=True,
                           timeout=params.get("TIMEOUT", 1800))
    except subprocess.TimeoutExpired:
        return {"success": False, "error": "OpenROAD 流程超时", "run_dir": run_dir}
    ok = r.returncode == 0
    stdout, stderr = r.stdout, r.stderr
    error = ""
    if not ok:
        # 失败诊断: 提取最后一条 ERROR/Error 行透传给前端
        err_lines = [l for l in (stdout + stderr).splitlines()
                     if "ERROR" in l or "Error:" in l]
        error = err_lines[-1][:200] if err_lines else f"returncode={r.returncode}"

    # 解析指标: WNS (timing.rpt) + 面积 (area.rpt)
    metrics = {"wns": None, "area": None}
    rpt = os.path.join(rd, "timing.rpt")
    if os.path.exists(rpt):
        c = open(rpt).read()
        slacks = [float(x) for x in re.findall(r'(-?[\d.]+)\s+slack', c, re.I)]
        if slacks:
            metrics["wns"] = min(slacks)
        m = re.search(r'(?:wns|Worst Negative Slack)[^-\d]*(-?[\d.]+)', c, re.I)
        if m:
            metrics["wns"] = float(m.group(1))
    arpt = os.path.join(rd, "area.rpt")
    if os.path.exists(arpt):
        c = open(arpt).read()
        m = re.search(r'Design area\s+([\d.eE+-]+)', c, re.I)
        if m:
            try: metrics["area"] = float(m.group(1))
            except ValueError: pass

    def_path = os.path.join(rd, "route.def")
    # GDS 不在本函数导出: OpenROAD 本身不写 GDS, 本机 KLayout 的 Ruby 绑定损坏,
    # 由上层 gds_export 步骤用 iEDA 的通用 LEF/DEF→GDS 转换完成 (adapter/gds_scripts/)
    return {"success": ok, "error": error if not ok else "", "run_dir": run_dir,
            "result_dir": rd,
            "metrics": metrics, "gds_path": "",
            "def_path": def_path if os.path.exists(def_path) else "",
            "stdout": (stdout + stderr)[-2000:]}
