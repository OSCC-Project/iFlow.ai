"""LVS 适配器 — 两级策略:

1. magic 晶体管级: GDS → magic extract → SPICE, 与综合网表比对
   前提: 每 PDK 的 magic.tech 带 extract 器件模型 (nangate45 的 tech 只有 DRC 规则,
   无器件提取模型 → 提取结果为空, 如实回退)
2. 门级回退: OpenROAD 布线后网表 vs 综合网表 (yosys write_spice 转 SPICE,
   netgen 黑盒比对连通性; tie/fill 单元按 LVS 惯例 unmatch, 结果如实标注)

依赖: netgen ✓; magic 需 `sudo apt-get install -y magic`
"""
import os, re, shutil, subprocess, tempfile


class MagicLVSRunner:
    def __init__(self, config: dict):
        self.magic = config.get("magic_executable", "magic")
        self.tech_dir = config.get("tech_dir", "")  # 每 PDK 的 magic.tech 所在目录

    def available(self) -> bool:
        return shutil.which(self.magic) is not None

    def execute(self, design: str, params: dict) -> dict:
        """params: {GDS_FILE, NETLIST_FILE, TOP_MODULE, RUN_DIR, ROUTE_NETLIST(可选)}"""
        gds = params.get("GDS_FILE", "")
        netlist = params.get("NETLIST_FILE", "")
        top = params.get("TOP_MODULE", design)
        run_dir = params.get("RUN_DIR", tempfile.mkdtemp(prefix="lvs_"))
        os.makedirs(run_dir, exist_ok=True)

        if not netlist or not os.path.exists(netlist):
            return {"success": False, "lvs_match": False, "error": "无综合网表"}

        # ---- 1. magic 晶体管级提取 (工具/tech 可用时才走) ----
        if (gds and os.path.exists(gds) and self.available()
                and self.tech_dir and os.path.exists(os.path.join(self.tech_dir, "magic.tech"))):
            layout_sp = self._magic_extract(gds, top, run_dir)
            if layout_sp:
                lvs = self._netgen(netlist, layout_sp, top, run_dir)
                if lvs.get("success"):
                    lvs["method"] = "transistor (magic 提取)"
                    return lvs
                # 提取出东西但比对失败 → 如实返回失败, 不冒充门级结果
                return {"success": True, "lvs_match": False, "netgen": lvs,
                        "error": "magic 提取版图与网表不匹配", "run_dir": run_dir}

        # ---- 2. 门级回退: 布线后网表 vs 原理图侧网表 ----
        # 优先 CTS 后网表 (与布线后同源含时钟树, 比对不含 CTS 增量), 否则用综合网表
        route_nl = params.get("ROUTE_NETLIST", "")
        if route_nl and os.path.exists(route_nl):
            sch_side = params.get("CTS_NETLIST", "")
            if not (sch_side and os.path.exists(sch_side)):
                sch_side = netlist
            gate = self._gate_level_lvs(sch_side, route_nl, top, run_dir)
            if gate:
                return gate

        # ---- 3. 如实报告未运行原因 ----
        if not gds or not os.path.exists(gds):
            return {"success": False, "lvs_match": False, "error": "无 GDS 文件"}
        if not self.available():
            return {"success": False, "lvs_match": False,
                    "error": "magic 未安装 (sudo apt-get install -y magic)"}
        if not self.tech_dir or not os.path.exists(os.path.join(self.tech_dir, "magic.tech")):
            return {"success": False, "lvs_match": False,
                    "error": "该 PDK 无 magic 技术文件 (nangate45 有, sky130 本机缺失)"}
        return {"success": False, "lvs_match": False,
                "error": "magic 提取无器件 (PDK tech 无 extract 模型) 且无布线后网表可做门级比对"}

    def _magic_extract(self, gds: str, top: str, run_dir: str) -> str:
        """GDS → SPICE; 提取为空 (无器件模型) 返回 ''"""
        tech = os.path.join(self.tech_dir, "magic.tech")
        layout_sp = os.path.join(run_dir, "layout.spice")

        # GDS 格式归一: iEDA def_to_gds 输出 ASCII GDS (GDT 风格) → 二进制 GDSII
        gds_bin = gds
        try:
            with open(gds, "rb") as f:
                head = f.read(8)
            if head.startswith(b"HEADER"):
                from adapter.gds_ascii_to_binary import convert
                with open(gds) as f:
                    text = f.read()
                gds_bin = os.path.join(run_dir, "design.gds")
                with open(gds_bin, "wb") as f:
                    f.write(convert(text))
        except OSError:
            pass

        # tech 文件必须用 `tech load` 在脚本里加载; -rcfile 是启动命令脚本, 不是 tech
        ext_tcl = os.path.join(run_dir, "extract.tcl")
        with open(ext_tcl, "w") as f:
            f.write(f"""tech load {tech}
gds read {gds_bin}
load {top}
extract all
ext2spice lvs
ext2spice format ngspice
ext2spice -o {layout_sp}
quit
""")
        try:
            r = subprocess.run([self.magic, "-dnull", "-noconsole", ext_tcl],
                               capture_output=True, text=True, timeout=600, cwd=run_dir)
        except subprocess.TimeoutExpired:
            return ""
        if r.returncode != 0 or not os.path.exists(layout_sp):
            return ""
        try:
            content = open(layout_sp).read()
        except OSError:
            return ""
        if content.count(".subckt") == 0:
            return ""  # 无器件模型 → 提取为空, 如实回退
        return layout_sp

    def _parse_module_ports(self, nl_path: str, top: str) -> list:
        """解析 verilog 模块端口并展开成 write_spice 风格 (总线 LSB 优先 name.N)

        端口宽度可能在头部内联 ([1:0] name) 或在模块体里声明 (input [1:0] name;)
        → 两处都要读
        """
        with open(nl_path) as f:
            src = f.read()
        src = re.sub(r"//[^\n]*", "", src)
        src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
        m = re.search(r"\bmodule\s+" + re.escape(top) + r"\s*\(([^;]*?)\)\s*;(.*?)\bendmodule\b", src, flags=re.S)
        if not m:
            return []
        names = [p.strip() for p in m.group(1).split(",") if p.strip()]
        body = m.group(2)
        # 模块体内的宽度声明: input/output/wire/reg [31:0] name;
        widths = {}
        for wm in re.finditer(r"\[(\d+)\s*:\s*(\d+)\]\s*([\w.]+)\s*[,;=]", body):
            widths[wm.group(3)] = (int(wm.group(1)), int(wm.group(2)))

        def expand(base: str, w) -> list:
            hi, lo = w
            if hi >= lo:
                return [f"{base}.{i}" for i in range(lo, hi + 1)]
            return [f"{base}.{i}" for i in range(lo, hi - 1, -1)]

        ports = []
        for p in names:
            bm = re.match(r"(.+?)\s*\[(\d+)\s*:\s*(\d+)\]", p)
            if bm:
                ports += expand(bm.group(1), (int(bm.group(2)), int(bm.group(3))))
            elif p in widths:
                ports += expand(p, widths[p])
            else:
                ports.append(p)
        return ports

    def _wrap_top_spice(self, sp_path: str, top: str, ports: list):
        """本机 yosys 在 hierarchy -top 后 write_spice 会裸写顶层 (无 .SUBCKT 头) → 手动补包装"""
        with open(sp_path) as f:
            lines = [l for l in f.read().splitlines() if l and not l.startswith("*")]
        if any(l.upper().startswith(".SUBCKT") for l in lines):
            return  # 已有包装 (自然顶层的网表)
        with open(sp_path, "w") as f:
            f.write(f".SUBCKT {top} " + " ".join(ports) + "\n")
            for l in lines:
                f.write(l + "\n")
            f.write(f".ENDS {top}\n")

    def _gate_level_lvs(self, synth_nl: str, route_nl: str, top: str, run_dir: str) -> dict:
        """门级 LVS: 原理图侧网表 vs 布线后网表, 经 write_spice 转 SPICE 后 netgen 黑盒比对"""
        sch_sp = os.path.join(run_dir, "sch.spice")
        lay_sp = os.path.join(run_dir, "lay.spice")
        try:
            # 两侧都选同一顶层再 flatten: 布线后网表是平的 (OpenROAD db 无层级),
            # 综合网表带子模块层级 → 不平会层级不匹配
            subprocess.run(["yosys", "-q", "-p",
                            f"read_verilog {synth_nl}; hierarchy -top {top}; flatten; "
                            f"write_spice {sch_sp}"],
                           capture_output=True, text=True, timeout=120)
            subprocess.run(["yosys", "-q", "-p",
                            f"read_verilog {route_nl}; hierarchy -top {top}; flatten; "
                            f"write_spice {lay_sp}"],
                           capture_output=True, text=True, timeout=120)
        except Exception as e:
            return {"success": False, "lvs_match": False,
                    "error": f"write_spice 转换失败: {str(e)[:150]}"}
        if not (os.path.exists(sch_sp) and os.path.exists(lay_sp)):
            return {"success": False, "lvs_match": False, "error": "SPICE 转换失败"}

        # hierarchy -top 后 write_spice 裸写顶层 → 按模块端口补 .SUBCKT 包装
        self._wrap_top_spice(sch_sp, top, self._parse_module_ports(synth_nl, top))
        self._wrap_top_spice(lay_sp, top, self._parse_module_ports(route_nl, top))

        # 电源网不比对 (LVS 惯例): 两侧顶层的 VDD/VSS 引脚都剥掉
        for sp in (sch_sp, lay_sp):
            with open(sp) as f:
                lines = f.read().splitlines()
            with open(sp, "w") as f:
                for l in lines:
                    if l.upper().startswith(f".SUBCKT {top}".upper()):
                        l = re.sub(r"\s+VDD\b", "", l, flags=re.I)
                        l = re.sub(r"\s+VSS\b", "", l, flags=re.I)
                    f.write(l + "\n")

        from adapter.netgen_runner import NetgenRunner
        # tie/fill 单元只存在于布线后网表 → LVS 惯例 ignore (结果注明口径);
        # ignore 只接受实际存在的单元名 → 从布局侧 SPICE 动态收集 FILL/TIE/LOGIC 类
        fill_cells = set()
        if os.path.exists(lay_sp):
            with open(lay_sp) as f:
                for line in f:
                    parts = line.split()
                    # X128 FILLCELL_X32 (无引脚 fill 只有 2 段); X0 net1 net2 INV_X1 (>=3 段)
                    if len(parts) >= 2 and parts[0].startswith("X"):
                        cell = parts[-1]
                        if (cell.startswith(("FILL", "TIE", "LOGIC0", "LOGIC1", "DECAP", "TAP"))):
                            fill_cells.add(cell)
        setup = "\n".join(f"ignore class {c}" for c in sorted(fill_cells))
        lvs = NetgenRunner({}).execute(top, {
            "layout_file": lay_sp, "schematic_file": sch_sp,
            "top_module": top, "setup_content": setup})
        return {"success": True, "lvs_match": bool(lvs.get("lvs_match")),
                "netgen": lvs, "layout_spice": lay_sp, "schematic_spice": sch_sp,
                "run_dir": run_dir, "method": "gate_level (布线后 vs CTS/综合网表; tie/fill/tap ignore)"}

    def _netgen(self, synth_nl: str, layout_sp: str, top: str, run_dir: str) -> dict:
        from adapter.netgen_runner import NetgenRunner
        return NetgenRunner({}).execute(top, {
            "layout_file": layout_sp, "schematic_file": synth_nl,
            "top_module": top, "setup_content": ""})
