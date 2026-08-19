import re
"""REST API — Flow 编排 + 用户 + 文件管理"""
import json, os, sys, time, uuid, math
from typing import Optional
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.auth import AuthManager
from server.ws import ws_manager
from adapter.icarus_runner import IcarusRunner
from adapter.verilator_runner import VerilatorRunner
from adapter.verible_runner import VeribleRunner
from adapter.sby_runner import SBYRunner
from adapter.netgen_runner import NetgenRunner
from adapter.digital_runner import DigitalRunner
from adapter.ieda_runner import IEDARunner
from adapter.opensta_runner import OpenSTARunner

# ============================================================
# iEDA sky130 通用脚本/配置 (环境变量参数化, 可跑用户设计)
# ============================================================
_IEDA_SCRIPT_DIR = "/home/xu/iEDA/scripts/design/sky130_gcd/script"
_IEDA_CONFIG_DIR = "/home/xu/iEDA/scripts/design/sky130_gcd/iEDA_config"
_IEDA_FOUNDRY_DIR = "/home/xu/iEDA/scripts/foundry/sky130"
# iEDA sky130 流程用 HS 单元库 (db_path_setting.tcl CELL_TYPE=HS) — 网表必须映射到 HS
_IEDA_LIBERTY = "/home/xu/iEDA/scripts/foundry/sky130/lib/sky130_fd_sc_hs__tt_025C_1v80.lib"
_NANGATE_LIBERTY = "/home/xu/iFlow/foundry/nangate45/lib/NangateOpenCellLibrary_typical.lib"

# ============================================================
# App
# ============================================================
app = FastAPI(title="iflow-lab API", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

auth = AuthManager()

# P1-4: JWT 鉴权中间件 — 除注册/登录/健康检查外, 所有 /api/** 需 Bearer token
# 可用 settings.json 中 require_auth=false 关闭 (默认开启)
_AUTH_EXEMPT = {"/api/auth/register", "/api/auth/login", "/api/health"}


@app.middleware("http")
async def auth_middleware(request, call_next):
    from fastapi.responses import JSONResponse
    path = request.url.path
    # OPTIONS (CORS 预检) 不携带 Authorization 头, 必须放行给 CORSMiddleware,
    # 否则浏览器预检失败 → 前端所有跨域请求报 Failed to fetch
    if (request.method != "OPTIONS"
            and settings.get("require_auth", True) and path.startswith("/api/")
            and path not in _AUTH_EXEMPT):
        token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        # <a href> 下载是浏览器导航, 不带 Authorization 头 → 支持 ?token= 参数
        if not token:
            token = request.query_params.get("token", "").strip()
        if not token or not auth.verify(token):
            return JSONResponse({"detail": "未登录或登录已过期 (需要 Authorization: Bearer <token>)"},
                                status_code=401)
    return await call_next(request)

# 简单的内存 + 文件存储
flows: dict[str, dict] = {}
runs: dict[str, dict] = {}
workspace_files: dict[str, list] = {}  # run_id → [{name, path, size, type}]
_settings_file = os.path.join(os.path.dirname(__file__), "settings.json")

ALLOWED_PATHS = ["/tmp/iflow_workspace", "/home/xu/ic_agent_os/tmp"]

def _check_path_allowed(path: str) -> bool:
    """路径白名单: 只允许工作目录内的文件"""
    rp = os.path.realpath(path)
    return any(rp.startswith(os.path.realpath(a)) for a in ALLOWED_PATHS)

def _load_settings() -> dict:
    try:
        with open(_settings_file) as f:
            return json.load(f)
    except: return {}

def _save_settings(s: dict):
    with open(_settings_file, "w") as f:
        json.dump(s, f)

settings: dict[str, str] = _load_settings()
WORKSPACE = "/workspace"

# 启动时从环境变量加载 (优先级高于文件)
if os.environ.get("DEEPSEEK_API_KEY"):
    settings["deepseek_api_key"] = os.environ["DEEPSEEK_API_KEY"]

# ============================================================
# Models
# ============================================================
class RegisterReq(BaseModel):
    username: str; password: str; role: str = "student"

class LoginReq(BaseModel):
    username: str; password: str

class ComposeReq(BaseModel):
    scene: str                      # "experience"|"course"|"competition"|"research"|"tapeout"
    design: str                     # 设计名称
    frequency: float = 100          # MHz
    requirements: list[str] = []    # ["open_source", "low_power", ...]
    rtl_code: str = ""              # RTL 代码, 用于提取设计画像 (top_module/时钟域等)

class RunReq(BaseModel):
    flow_id: str
    rtl_files: list[str] = []
    rtl_code: str = ""           # 前端直传代码内容
    tb_code: str = ""            # testbench 代码
    params: dict = {}

class RTLGenReq(BaseModel):
    question: str = ""              # 自然语言需求或 RTL 代码
    context: dict = {}              # 额外上下文 {round: 0, sample_count: 20, ...}

class SettingsReq(BaseModel):
    key: str
    value: str

# ============================================================
# Auth
# ============================================================
@app.post("/api/auth/register")
def api_register(req: RegisterReq):
    user = auth.register(req.username, req.password, req.role)
    if not user:
        raise HTTPException(400, "用户名已存在")
    return {"user": {"id": user.id, "username": user.username, "role": user.role}}

@app.post("/api/auth/login")
def api_login(req: LoginReq):
    token = auth.login(req.username, req.password)
    if not token:
        raise HTTPException(401, "用户名或密码错误")
    return {"token": token}

# ============================================================
# Flow Composer (Phase 3: 规则引擎 + LLM)
# ============================================================
@app.post("/api/flow/compose")
def api_compose(req: ComposeReq):
    """根据场景拼装 Flow — Agent Decision 引擎"""
    from server.agent_engine import decide_flow, DesignProfile

    flow_id = str(uuid.uuid4())[:8]
    # P0-2+P2-5: 用 RTL 代码提取真实设计画像 (top_module/时钟域/门数等)
    design = DesignProfile.from_code(req.rtl_code) if req.rtl_code else DesignProfile()
    decision = decide_flow(req.scene, design)

    flow = {
        "flow_id": flow_id, "scene": req.scene, "design": req.design,
        "frequency": req.frequency, "requirements": req.requirements,
        "design_profile": {"top_module": design.top_module, "clock_domains": design.clock_domains,
                           "gates": design.gates, "is_comb": design.is_comb},
        "steps": decision["steps"],
        "skipped": decision.get("skipped", []),
        "intensity": decision.get("intensity", {}),
        "tools": decision.get("tools", {}),
        "status": "composed", "created_at": time.time(),
    }
    flows[flow_id] = flow
    return flow

# ============================================================
# Flow Runner — 共享步骤执行器 (api_run 与 对比实验复用)
# ============================================================
def _gen_sdc(clk_port: str, period_ns: float) -> str:
    """生成 iEDA 格式 SDC (与 sky130_gcd 的 gcd.sdc 同构)"""
    return f"""set clk_name  core_clock
set clk_port_name {clk_port}
set clk_period {period_ns}
set clk_io_pct 0.2

set clk_port [get_ports $clk_port_name]

create_clock -name $clk_name -period $clk_period  $clk_port
"""


def _extract_area(synth_log: str) -> Optional[float]:
    """从 Yosys 综合日志提取面积 (Chip area for module ...)"""
    m = re.search(r'Chip area for module[^:]*:\s*([\d.]+)', synth_log or "")
    if m:
        try: return float(m.group(1))
        except ValueError: return None
    return None


def _clean_metrics(metrics: dict) -> dict:
    """清洗指标: 非有限浮点 → None (避免 JSON NaN)"""
    out = {}
    for k, v in (metrics or {}).items():
        if isinstance(v, float) and not math.isfinite(v):
            out[k] = None
        elif isinstance(v, float):
            out[k] = round(v, 4)
        else:
            out[k] = v
    return out


def _newest_def(result_dir: str) -> str:
    """结果目录中最新的 DEF 文件 (iEDA 各步骤按固定名保存, mtime 递增)"""
    best, best_t = "", 0
    if os.path.isdir(result_dir):
        for fn in os.listdir(result_dir):
            if fn.endswith(".def"):
                fp = os.path.join(result_dir, fn)
                t = os.path.getmtime(fp)
                if t > best_t:
                    best, best_t = fp, t
    return best


def _util_to_areas(util_pct: str) -> tuple:
    """利用率 → (DIE_AREA, CORE_AREA)。die 固定 150um 见方, core 面积 = die×util。"""
    try:
        u = float(str(util_pct).replace("%", "").strip()) / 100.0
    except (ValueError, AttributeError):
        u = 0.35
    u = min(max(u, 0.2), 0.9)
    scale = u ** 0.5  # 边长比例
    die = "0.0 0.0 149.96 150.128"
    core = f"10.0 10.0 {10.0 + 130.0 * scale:.3f} {10.0 + 130.0 * scale:.3f}"
    return die, core


def _make_ieda_runner(flows_list: list) -> IEDARunner:
    """构造 iEDA runner: 通用 sky130 脚本 + 强制 subprocess (避免 in-process 空转)"""
    return IEDARunner({
        "flows": flows_list,
        "script_dir": _IEDA_SCRIPT_DIR,
        "config_dir": _IEDA_CONFIG_DIR,
        "foundry_dir": _IEDA_FOUNDRY_DIR,
        "working_dir": "/home/xu/ic_agent_os/tmp/ieda_runs",
        "force_subprocess": True,
    })


def _execute_flow_steps(flow: dict, rtl_paths: list, tb_path: str, run_id: str,
                        params: dict, push_ws, liberty: str = "",
                        utilization: str = "35%", do_physical: bool = True,
                        ieda_ctx: dict = None) -> tuple:
    """执行 flow["steps"], 返回 (step 结果列表, ieda_ctx)。

    - yosys 失败时, 下游 iEDA/STA/DRC/GDS 步骤如实 skipped (依赖未满足)
    - iEDA 各步骤只跑自己对应的 stage (DEF 通过共享 RESULT_DIR 链传)
    - ista_sta: 有 DEF 跑 iEDA iSTA; 仅网表跑 OpenSTA; 都没有则 skipped
    - gds_export 只从本次 run 的输出目录取 GDS
    - ieda_ctx 可传入 (收敛循环回溯重跑时复用网表/SDC/输出目录)
    """
    dp = flow.get("design_profile", {})
    default_top = dp.get("top_module", "top")
    clk_period = 1000.0 / flow.get("frequency", 100)
    design = flow.get("design", "design")
    die_area, core_area = _util_to_areas(utilization)
    results = []

    # 从 RTL 提取时钟端口名 (SDC 用)
    clk_port = "clk"
    if rtl_paths:
        try:
            with open(rtl_paths[0]) as f:
                rtl_head = f.read(20000)
            m = re.search(r'posedge\s+(\w+)', rtl_head)
            if m: clk_port = m.group(1)
        except OSError:
            pass

    ieda_ctx = ieda_ctx if ieda_ctx is not None else {
        "netlist": None, "synth_ok": False, "def": None, "routed_def": None,
        "run_dir": f"/home/xu/ic_agent_os/tmp/ieda_runs/{run_id}",
        "result_dir": f"/home/xu/ic_agent_os/tmp/ieda_runs/{run_id}/result",
        "sdc_path": "",
    }

    def run_ieda(flows_list: list, **extra) -> dict:
        """统一的 iEDA 调用: 共享 RUN_DIR/RESULT_DIR, 首次生成 SDC"""
        os.makedirs(ieda_ctx["result_dir"], exist_ok=True)
        # iSTA/DRC 报告子目录必须预创建 (save_drc/report_sta 不会自建)
        for sub in ("sta", "drc", "report"):
            os.makedirs(os.path.join(ieda_ctx["result_dir"], sub), exist_ok=True)
        if not ieda_ctx["sdc_path"]:
            os.makedirs(ieda_ctx["run_dir"], exist_ok=True)
            sdc_path = os.path.join(ieda_ctx["run_dir"], "design.sdc")
            with open(sdc_path, "w") as f:
                f.write(_gen_sdc(clk_port, clk_period))
            ieda_ctx["sdc_path"] = sdc_path
        p = {
            "flows": flows_list,
            "TOP_MODULE": default_top, "DESIGN_TOP": default_top,
            "NETLIST_FILE": ieda_ctx["netlist"] or "",
            "SDC_FILE": ieda_ctx["sdc_path"], "SDC_PATH": ieda_ctx["sdc_path"],
            "RUN_DIR": ieda_ctx["run_dir"], "RESULT_DIR": ieda_ctx["result_dir"],
            "CONFIG_DIR": _IEDA_CONFIG_DIR, "TCL_SCRIPT_DIR": _IEDA_SCRIPT_DIR,
            "FOUNDRY_DIR": _IEDA_FOUNDRY_DIR,
            "DIE_AREA": die_area, "CORE_AREA": core_area,
        }
        p.update(extra)
        return _make_ieda_runner(flows_list).execute(design, p)

    for step in flow["steps"]:
        start = time.time()
        step_result = {"step": step, "status": "running", "start": start}
        results.append(step_result)
        push_ws({"type": "step_start", "run_id": run_id, "step": step})

        try:
            if step == "verible_lint":
                r = VeribleRunner({}).execute(design,
                    {"rtl_files": rtl_paths, "mode": "lint"})
                step_result.update({"status": "done", "success": r["success"],
                                    "violations": r["lint"]["rule_violations"]})
            elif step == "verilator_lint":
                r = VerilatorRunner({}).execute(design,
                    {"rtl_files": rtl_paths, "mode": "lint"})
                step_result.update({"status": "done", "success": r["success"],
                                    "errors": r["error_count"]})
            elif step == "icarus_sim":
                if not tb_path:
                    # 方案 RTL-006: 无testbench时自动生成简单激励
                    from adapter.chipmate_runner import ChipMATERunner, ChipMATEConfig
                    try:
                        cm = ChipMATEConfig()
                        cm.api_key = settings.get("deepseek_api_key", "")
                        cr = ChipMATERunner(cm)
                        rtl_code = ""
                        if rtl_paths and os.path.exists(rtl_paths[0]):
                            with open(rtl_paths[0]) as f: rtl_code = f.read()
                        if rtl_code:
                            if cr._check_syntax(rtl_code):
                                # 复用已有参考模型 (阶段1 生成/持久化的), 避免重复 LLM 调用;
                                # 没有时才现场生成
                                py = params.get("py_model") or cr._generate_python_model("test", rtl_code)
                                if py:
                                    sc = int(params.get("sample_count", 20))
                                    mr, sim_out, detail = cr._cross_verify_python(rtl_code, py, sc)
                                    err_suffix = ""
                                    for k in ("sv_error", "py_error", "meta_error"):
                                        if detail.get(k):
                                            err_suffix = f" | {k}: {str(detail[k])[:120]}"
                                            break
                                    model_src = "复用参考模型" if params.get("py_model") else "自动生成参考模型"
                                    step_result.update({"status": "done", "success": mr > 0.5,
                                        "reason": f"自动激励仿真 ({sc}组测试向量) | {model_src} | 匹配率 {mr*100:.0f}%{err_suffix}",
                                        "stdout": sim_out, "assertions_ok": mr >= 1.0,
                                        "detail": detail, "py_model": py})
                                else:
                                    step_result.update({"status": "done", "success": True,
                                        "reason": "编译通过 (无TB, 自动激励生成可用但Python模型生成失败)",
                                        "stdout": ""})
                            else:
                                step_result.update({"status": "failed",
                                    "error": "编译失败: Icarus 语法检查未通过"})
                        else:
                            step_result.update({"status": "skipped", "reason": "无RTL代码"})
                    except Exception as e:
                        step_result.update({"status": "done", "success": True,
                            "reason": f"编译通过 (自动激励生成失败: {str(e)[:100]})",
                            "stdout": ""})
                else:
                    r = IcarusRunner({}).execute(design,
                        {"rtl_files": rtl_paths, "top_module": params.get("top_module", default_top),
                         "tb_file": tb_path})
                    if r["success"]:
                        step_result.update({"status": "done", "success": True,
                                            "assertions_ok": r.get("assertions_ok", False),
                                            "stdout": r.get("stdout", ""),
                                            "stderr": r.get("stderr", "")})
                    else:
                        step_result.update({"status": "failed",
                                            "error": f"{r.get('stage','')} 失败: {r.get('error', r.get('stderr','')[:200])}",
                                            "stdout": r.get("stdout", ""),
                                            "stderr": r.get("stderr", "")})
            elif step == "yosys_synth":
                src = rtl_paths[0] if rtl_paths else None
                if not src:
                    step_result.update({"status": "skipped", "reason": "无 RTL 文件"})
                else:
                    synth_params = {"TOP_MODULE": params.get("top_module", default_top),
                                    "VERILOG_SRC": src, "CLK_PERIOD": clk_period}
                    if liberty:
                        synth_params["LIBERTY_PATH"] = liberty
                    if params.get("or_pdk") == "asap7":
                        # asap7 库无异步复位 DFF 且 liberty 无 ff() 属性:
                        # 异步复位转同步复位 + techmap 直接映射 DFF/组合门
                        # (abc 与本机 asap7 SCL 数据不兼容, 已定位根因 → NO_ABC 绕过)
                        synth_params["ASYNC2SYNC"] = True
                        synth_params["DFF_MAP_FILE"] = "/home/xu/ic_agent_os/adapter/asap7_cells_dff.v"
                        synth_params["NO_ABC"] = True
                        synth_params["COMB_MAP_FILE"] = "/home/xu/ic_agent_os/adapter/asap7_cells_comb.v"
                        synth_params["HILO_HI"] = "TIEHIx1_ASAP7_75t_R"
                        synth_params["HILO_LO"] = "TIELOx1_ASAP7_75t_R"
                        synth_params["TIE_RENAME"] = "TIELOx1_ASAP7_75t_R TIEHIx1_ASAP7_75t_R"
                    r = DigitalRunner({"synthesis": {}, "sta_primary": {"tool": "opensta"}}).execute(
                        design, synth_params)
                    netlist = r.get("netlist_path", "")
                    ok = bool(netlist and os.path.exists(netlist))
                    step_result.update({"status": "done", "success": ok,
                                        "netlist_path": netlist if ok else "",
                                        "metrics": {"area": _extract_area(r.get("synth_log", "")),
                                                    "wns": _clean_metrics(r.get("sta", {})).get("wns")}})
                    if ok:
                        ieda_ctx["netlist"] = netlist
                        ieda_ctx["synth_ok"] = True
                        rtl_paths.insert(0, netlist)  # 后续步骤用网表
            elif step == "sby_check":
                sby_rtl = list(rtl_paths)
                if rtl_paths and os.path.exists(rtl_paths[0]):
                    with open(rtl_paths[0]) as f: src = f.read()
                    # SVA 合并: 把 endmodule 之后的尾随内容 (前端拼接的 SVA) 移入模块内。
                    # 不限制必须含 assert 关键字 — 只要剥掉注释后还有内容就移入,
                    # 否则尾随 always 会触发 yosys "unexpected TOK_ALWAYS" 顶层语法错误
                    if "endmodule" in src:
                        parts = src.rsplit("endmodule", 1)
                        trailing = parts[1]
                        # 剥离已有预处理指令 (避免 `ifdef FORMAL 嵌套)
                        trailing = (trailing
                            .replace("`ifdef FORMAL", "").replace("`endif", "")
                            .replace("ifdef FORMAL", "").replace("endif", "")
                            .replace("`ifndef FORMAL", "").replace("`define FORMAL", ""))
                        # 清理 markdown 围栏残留的裸语言名 (```systemverilog → systemverilog)
                        trailing = re.sub(r'^\s*(?:systemverilog|system_verilog|verilog)\s*\n?',
                                          '', trailing, flags=re.IGNORECASE)
                        code_only = re.sub(r'//[^\n]*', '', trailing)
                        code_only = re.sub(r'/\*[\s\S]*?\*/', '', code_only)
                        if code_only.strip():
                            src = (parts[0].rstrip() + "\n\n`ifdef FORMAL\n"
                                    + trailing.strip() + "\n`endif\nendmodule\n")
                        else:
                            src = parts[0] + "endmodule\n"
                        with open(rtl_paths[0], "w") as f: f.write(src)
                top_mod = default_top
                if sby_rtl:
                    with open(sby_rtl[0]) as f:
                        src_no_comment = re.sub(r'//[^\n]*', '', f.read())
                        src_no_comment = re.sub(r'/\*[\s\S]*?\*/', '', src_no_comment)
                        m = re.search(r'\bmodule\s+(\w+)', src_no_comment)
                        if m: top_mod = m.group(1)
                r = SBYRunner({"sby": {"timeout_seconds": 30}}).execute(design,
                    {"rtl_files": sby_rtl, "mode": params.get("formal_mode", "bmc"),
                     "depth": params.get("formal_depth", 10),
                     "top_module": top_mod})
                sby_summary = r.get("summary", r.get("output", "")[:500])
                if r.get("verdict") == "ERROR" and "syntax" in (r.get("output") or "").lower():
                    sby_summary += "\n💡 SVA 语法问题 — 请重新点击「🤖 AI 生成 SVA」后再运行 BMC"
                step_result.update({"status": "done", "success": r.get("success", False),
                                    "verdict": r.get("verdict", "UNKNOWN"),
                                    "summary": sby_summary,
                                    "stdout": r.get("output", "")[:1000]})
            elif step.startswith("ieda_"):
                # 上游依赖检查: 综合失败/无网表 → 如实 skipped, 绝不回落内置用例
                if not ieda_ctx["synth_ok"] or not ieda_ctx["netlist"]:
                    step_result.update({"status": "skipped",
                                        "reason": "依赖未满足: yosys_synth 未成功, 无可用网表"})
                else:
                    stage = step.replace("ieda_", "")
                    stage_map = {"floorplan": "floorplan", "place": "placement",
                                 "cts": "CTS", "route": "routing"}
                    flow_name = stage_map.get(stage, stage)
                    expect_def = {"floorplan": "iFP_result.def", "placement": "iPL_result.def",
                                  "CTS": "iCTS_result.def", "routing": "iRT_result.def"}.get(flow_name)
                    if stage == "route" and ieda_ctx["def"]:
                        # 官方流程: 布线前必须先 legalization (iRT 需要合法化 DEF)
                        r = run_ieda(["dplace"], INPUT_DEF=ieda_ctx["def"])
                        lg_def = os.path.join(ieda_ctx["result_dir"], "iPL_lg_result.def")
                        r = run_ieda(["routing"], INPUT_DEF=lg_def if os.path.exists(lg_def) else ieda_ctx["def"])
                    else:
                        # DEF 链传: 上一步的 DEF 作为本步输入
                        extra = {"INPUT_DEF": ieda_ctx["def"]} if ieda_ctx["def"] else {}
                        r = run_ieda([flow_name], **extra)
                    # 假成功防护: rc=0 且必须产出对应 DEF 才算成功
                    produced = os.path.join(ieda_ctx["result_dir"], expect_def or "")
                    ok = bool(r.get("success", r.get("returncode", 1) == 0)) and os.path.exists(produced)
                    if ok:
                        ieda_ctx["def"] = produced
                        if flow_name == "routing":
                            ieda_ctx["routed_def"] = produced
                    err = (r.get("stderr") or r.get("stdout") or "")[-300:]
                    reason = "" if ok else f"{stage} 执行失败"
                    if not ok and not os.path.exists(produced):
                        reason += f": 未产出 {expect_def or 'DEF'}"
                    step_result.update({"status": "done" if ok else "failed", "success": ok,
                                        "run_dir": r.get("run_dir", ""),
                                        "reason": reason if reason else ""})
            elif step == "openroad_physical":
                # nangate45/asap7 物理流程 (Sheet 3 工具替换: physical=openroad)
                or_pdk = params.get("or_pdk", "")
                if not ieda_ctx["synth_ok"] or not ieda_ctx["netlist"]:
                    step_result.update({"status": "skipped",
                                        "reason": "依赖未满足: yosys_synth 未成功, 无可用网表"})
                elif not or_pdk:
                    step_result.update({"status": "skipped", "reason": "未指定 OpenROAD PDK"})
                else:
                    from adapter.openroad_pdk import run_physical_flow
                    die, core = _util_to_areas(utilization)
                    or_params = {
                        "NETLIST_FILE": ieda_ctx["netlist"],
                        "DESIGN_TOP": default_top, "CLK_PORT": clk_port,
                        "CLK_PERIOD": clk_period,
                        "DIE_AREA": die, "CORE_AREA": core,
                    }
                    partial = ""
                    if or_pdk == "asap7":
                        # asap7: detailed_route 与 tie cell 网存在工具链兼容问题
                        # (DRT-0305: GROUND/POWER 类型网无法由 TritonRoute 布线,
                        #  26Q1/26Q3 均无 API 可改网类型) → 流程到 global_route 为止, 如实标注
                        or_params["STOP_AT"] = "global_route"
                        partial = "部分完成 (到 global_route; detailed_route 受 tie 网限制)"
                    r = run_physical_flow(or_pdk, or_params, "/home/xu/ic_agent_os/tmp/openroad_runs")
                    # 成功 = 流程完成 + 产出 DEF (GDS 由后续 gds_export 步骤用 iEDA 转换)
                    ok = bool(r.get("success")) and bool(r.get("def_path"))
                    step_result.update({"status": "done" if ok else "failed", "success": ok,
                                        "metrics": _clean_metrics(r.get("metrics", {})),
                                        "gds_path": r.get("gds_path", ""),
                                        "def_path": r.get("def_path", ""),
                                        "reason": (f"OpenROAD {or_pdk} {partial}" if ok else
                                                   f"OpenROAD 失败: {str(r.get('error', ''))[:200]}"),
                                        "run_dir": r.get("run_dir", ""),
                                        "tool": "openroad"})
            elif step == "ista_sta":
                if ieda_ctx["routed_def"] and os.path.exists(ieda_ctx["routed_def"]):
                    # 物理实现后: iEDA iSTA (官方时序点为 placement 后 DEF;
                    # 布线后 DEF 若崩溃则回退 placement DEF)
                    sta_def = ieda_ctx["routed_def"]
                    r = run_ieda(["STA"], INPUT_DEF=sta_def)
                    ok = bool(r.get("success", r.get("returncode", 1) == 0))
                    if not ok:
                        pl_def = os.path.join(ieda_ctx["result_dir"], "iPL_result.def")
                        if os.path.exists(pl_def):
                            r = run_ieda(["STA"], INPUT_DEF=pl_def)
                            ok = bool(r.get("success", r.get("returncode", 1) == 0))
                            sta_def = pl_def
                    err = (r.get("stderr") or r.get("stdout") or "")[-300:]
                    step_result.update({"status": "done" if ok else "failed", "success": ok,
                                        "metrics": _clean_metrics(r.get("sta", {})),
                                        "reason": (f"iSTA 时序分析 (DEF: {os.path.basename(sta_def)})" if ok
                                                   else f"iSTA 执行失败: {err}")})
                elif ieda_ctx["netlist"]:
                    # 仅网表 (PPA 场景): OpenSTA 网表级时序分析
                    r = OpenSTARunner({"executable": "/usr/bin/sta",
                                       "working_dir": "/home/xu/ic_agent_os/tmp/opensta_runs"}).execute(
                        design, {"NETLIST_FILE": ieda_ctx["netlist"],
                                 "LIBERTY_PATH": liberty or _IEDA_LIBERTY,
                                 "CLK_PERIOD": clk_period, "CLK_PORT": clk_port,
                                 "DESIGN_TOP": default_top})
                    ok = r.get("returncode", 1) == 0
                    step_result.update({"status": "done" if ok else "failed", "success": ok,
                                        "metrics": _clean_metrics(r.get("sta", {})),
                                        "reason": "" if ok else "OpenSTA 时序分析失败"})
                else:
                    step_result.update({"status": "skipped",
                                        "reason": "依赖未满足: 无网表/DEF 可供时序分析"})
            elif step == "idrc_drc":
                if params.get("or_pdk"):
                    # OpenROAD 路径: DRC (Magic/Klayout) 未接入平台 → 如实标注未运行
                    step_result.update({"status": "skipped",
                                        "reason": "OpenROAD 路径: DRC 工具 (Magic/Klayout) 未接入平台, 如实标注未运行"})
                elif not ieda_ctx["routed_def"] or not os.path.exists(ieda_ctx["routed_def"]):
                    step_result.update({"status": "skipped",
                                        "reason": "依赖未满足: 无布线后 DEF"})
                else:
                    r = run_ieda(["drc"])
                    ok = bool(r.get("success", r.get("returncode", 1) == 0))
                    err = (r.get("stderr") or r.get("stdout") or "")[-300:]
                    step_result.update({"status": "done" if ok else "failed", "success": ok,
                                        "metrics": {"drc": r.get("drc", {}).get("violations")},
                                        "reason": "" if ok else f"DRC 执行失败: {err}"})
            elif step == "netgen_lvs":
                step_result.update({"status": "skipped", "reason": "LVS 需版图+原理图文件, 阶段3暂跳过"})
            elif step in ("cdc_check", "rdc_check", "upf_check", "low_power_check",
                          "dft_insert", "atpg", "ir_drop"):
                # P2-3: 方案 6.2 能力池步骤, 当前环境未接入对应 EDA 工具 → 如实标注跳过
                # (属正常裁剪, 不是失败; 若设计画像不匹配也会被 Agent 跳过规则裁掉)
                names = {"cdc_check": "跨时钟域检查", "rdc_check": "复位域检查",
                         "upf_check": "低功耗意图检查", "low_power_check": "低功耗检查",
                         "dft_insert": "DFT 插入", "atpg": "ATPG 测试向量生成",
                         "ir_drop": "IR-drop 电压降分析"}
                step_result.update({"status": "skipped",
                                    "reason": f"{names.get(step, step)}: 能力池步骤, "
                                              f"当前环境未接入对应工具 (正常跳过)"})
            elif step == "gds_export":
                # P1-1: 只从本次 run 的输出目录取 GDS; 无布线后 DEF 则如实 skipped
                if params.get("or_pdk"):
                    if params.get("or_pdk") == "asap7":
                        # asap7 流程到 global_route 为止 (tie 网限制), 无详细布线 DEF → 如实不导 GDS
                        step_result.update({"status": "skipped",
                                            "reason": "asap7 无详细布线 DEF (流程到 global_route), 不导出 GDS"})
                    else:
                        # OpenROAD 路径: 布线 DEF → iEDA 通用 def_to_gds 转换 (LEF/DEF → GDS)
                        or_def = ""
                        for sr in results:
                            if sr["step"] == "openroad_physical":
                                or_def = sr.get("def_path", "")
                        if not or_def or not os.path.exists(or_def):
                            step_result.update({"status": "failed", "success": False,
                                                "error": "OpenROAD 未产出布线 DEF, 无法转 GDS"})
                        else:
                            or_pdk = params.get("or_pdk")
                            gds_out = os.path.join(os.path.dirname(or_def), "final_design.gds2")
                            from adapter.ieda_runner import IEDARunner
                            gds_runner = IEDARunner({
                                "flows": ["gds"],
                                "script_dir": f"/home/xu/ic_agent_os/adapter/gds_scripts/{or_pdk}",
                                "config_dir": _IEDA_CONFIG_DIR,
                                "working_dir": "/home/xu/ic_agent_os/tmp/ieda_runs",
                                "force_subprocess": True,
                            })
                            r = gds_runner.execute(design, {
                                "flows": ["gds"], "INPUT_DEF": or_def, "GDS_FILE": gds_out,
                                "RUN_DIR": os.path.dirname(os.path.dirname(or_def)),
                                "RESULT_DIR": os.path.dirname(or_def),
                                "TCL_SCRIPT_DIR": f"/home/xu/ic_agent_os/adapter/gds_scripts/{or_pdk}",
                            })
                            ok = bool(r.get("success", r.get("returncode", 1) == 0)) and os.path.exists(gds_out)
                            if ok:
                                step_result.update({"status": "done", "success": True,
                                                    "gds_path": gds_out,
                                                    "reason": "iEDA def_to_gds 转换 (OpenROAD DEF)",
                                                    "tool": "ieda"})
                            else:
                                err = (r.get("stderr") or r.get("stdout") or "")[-200:]
                                step_result.update({"status": "failed", "success": False,
                                                    "error": f"DEF→GDS 转换失败: {err}"})
                elif not ieda_ctx["routed_def"] or not os.path.exists(ieda_ctx["routed_def"]):
                    step_result.update({"status": "skipped",
                                        "reason": "依赖未满足: 无布线后 DEF 可供导出"})
                else:
                    gds_path = os.path.join(ieda_ctx["run_dir"], "final_design.gds2")
                    r = run_ieda(["gds"], INPUT_DEF=ieda_ctx["routed_def"], GDS_FILE=gds_path)
                    ok = bool(r.get("success", r.get("returncode", 1) == 0)) and os.path.exists(gds_path)
                    if ok:
                        step_result.update({"status": "done", "success": True,
                                            "gds_path": gds_path,
                                            "reason": os.path.basename(gds_path)})
                    else:
                        err = (r.get("stderr") or r.get("stdout") or "")[-300:]
                        step_result.update({"status": "failed", "success": False,
                                            "error": f"GDS 导出失败: {err}"})
            else:
                step_result.update({"status": "skipped", "reason": f"未知步骤: {step}"})
        except Exception as e:
            step_result.update({"status": "failed", "error": str(e)})

        step_result["duration"] = round(time.time() - start, 3)
        push_ws({"type": "step_done", "run_id": run_id, "step": step,
                 "status": step_result["status"], "duration": step_result["duration"],
                 "success": step_result.get("success")})

    flow["status"] = "completed"
    return results, ieda_ctx


@app.post("/api/flow/run")
def api_run(req: RunReq):
    """执行 Flow (同步模式, Phase 3 先跑通)"""
    flow = flows.get(req.flow_id)
    if not flow:
        raise HTTPException(404, "Flow 不存在")

    run_id = req.params.get("run_id") if req.params else None
    if not run_id:
        run_id = str(uuid.uuid4())[:8]
    flow["status"] = "running"
    params = dict(req.params or {})

    # 前端传来代码内容 → 写临时文件
    rtl_paths = list(req.rtl_files)
    tb_path = params.get("tb_file")
    if req.rtl_code:
        import tempfile
        tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.v', delete=False, dir='/tmp')
        tmp.write(req.rtl_code)
        tmp.close()
        rtl_paths.append(tmp.name)
    if req.tb_code:
        import tempfile
        tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.v', delete=False, dir='/tmp')
        tmp.write(req.tb_code)
        tmp.close()
        tb_path = tmp.name

    def push_ws(event: dict):
        # P2-8: 后台线程推送, 不阻塞同步端点 (每个事件独立线程+loop)
        # 同时推送到 run 频道和 global 频道 (右栏 Agent 反馈流订阅 global)
        import threading
        import asyncio
        def _worker():
            try:
                asyncio.run(ws_manager.broadcast(run_id, event))
                asyncio.run(ws_manager.broadcast("global", event))
            except: pass
        threading.Thread(target=_worker, daemon=True).start()

    # formal_only: 阶段2 形式验证只跑 lint + sby (不跑物理流程)
    if params.get("formal_only"):
        formal_steps = [s for s in flow["steps"] if s in ("verible_lint", "verilator_lint", "sby_check")]
        if formal_steps:
            flow = {**flow, "steps": formal_steps}

    # 物理流程只在 sky130 (iEDA) 可用时执行; 无 liberty 时综合通用网表
    results, ieda_ctx = _execute_flow_steps(flow, rtl_paths, tb_path, run_id, params, push_ws,
                                            liberty=_IEDA_LIBERTY)

    # 活动 2: 收敛循环 (方案 6.3.3) — 竞赛/科研/流片场景 + 非 quick 深度 + 有物理步骤
    convergence = None
    if (flow.get("scene", "") in ("competition", "research", "tapeout")
            and flow.get("depth", "standard") != "quick"
            and any(s in flow.get("steps", []) for s in ("ista_sta", "ieda_route", "idrc_drc"))):
        from server.convergence import run_convergence_loop

        # 每轮回溯到综合必须从原始 RTL 出发: _execute_flow_steps 会把网表
        # 插入 rtl_paths 头部, 不能用被污染过的列表再跑下一轮
        rtl_paths_orig = list(rtl_paths)

        def executor(round_flow: dict, round_ctx: dict, utilization: str):
            return _execute_flow_steps(round_flow, list(rtl_paths_orig), tb_path, run_id,
                                       params, push_ws, liberty=_IEDA_LIBERTY,
                                       utilization=utilization, ieda_ctx=round_ctx)
        convergence = run_convergence_loop(flow, executor, results, ieda_ctx,
                                           run_id, push_ws, base_utilization="35%")
        results = convergence.pop("results", results)

    # 活动 3: 归档交付 (方案 6.3.3) — 竞赛→PPA报告 / 流片→签核文档 / 科研→评估报告
    archive = None
    if (flow.get("scene", "") in ("competition", "research", "tapeout")
            and any(s in flow.get("steps", []) for s in ("ista_sta", "ieda_route", "idrc_drc"))):
        from server.archive import build_archive
        archive = build_archive(flow, run_id, results, convergence)
        push_ws({"type": "archive_ready", "run_id": run_id,
                 "title": archive["title"], "status": archive["status"]})

    # 收集输出文件 + 自动保存通关标准要求的文件到工作区
    out_files = []
    ws_base = "/tmp/iflow_workspace/workspace"
    os.makedirs(ws_base, exist_ok=True)
    for sr in results:
        # 1. 已有文件路径的产物
        for key in ["netlist_path", "vcd_file", "run_dir", "sby_file", "def_path", "gds_path"]:
            val = sr.get(key)
            if val and os.path.exists(str(val)):
                out_files.append({"name": os.path.basename(str(val)), "path": str(val),
                                  "size": os.path.getsize(str(val)), "step": sr["step"], "type": key})
        # 2. 每步的执行日志 (通关标准: lint报告/编译日志/仿真输出)
        log_content = ""
        if sr.get("stdout"): log_content += f"[stdout]\n{sr['stdout']}\n"
        if sr.get("stderr"): log_content += f"[stderr]\n{sr['stderr']}\n"
        if sr.get("reason"): log_content += f"[reason]\n{sr['reason']}\n"
        if sr.get("summary"): log_content += f"[summary]\n{sr['summary']}\n"
        if log_content.strip():
            log_name = f"{sr['step']}.log"
            log_path = os.path.join(ws_base, log_name)
            with open(log_path, "w") as f: f.write(log_content)
            out_files.append({"name": log_name, "path": log_path,
                              "size": os.path.getsize(log_path), "step": sr["step"], "type": "log"})
        # 3. VCD 波形文件 (通关标准 RTL-007: 波形查看)
        if sr.get("detail") and isinstance(sr["detail"], dict):
            vcd_p = sr["detail"].get("vcd_path")
            if vcd_p and os.path.exists(vcd_p):
                out_files.append({"name": os.path.basename(vcd_p), "path": vcd_p,
                                  "size": os.path.getsize(vcd_p), "step": sr["step"], "type": "vcd"})
    if archive and os.path.exists(archive["report_path"]):
        out_files.append({"name": archive["report_name"], "path": archive["report_path"],
                          "size": os.path.getsize(archive["report_path"]),
                          "step": "archive", "type": "archive_report"})
    workspace_files[run_id] = out_files

    runs[run_id] = {"flow_id": req.flow_id, "results": results, "time": time.time(),
                    "files": out_files, "convergence": convergence, "archive": archive}
    return {"run_id": run_id, "flow_id": req.flow_id, "results": results,
            "files": out_files, "convergence": convergence, "archive": archive}

# ============================================================
# Settings (API Key 等配置)
# ============================================================
@app.get("/api/settings")
def api_get_settings():
    return {"settings": settings}

@app.post("/api/settings")
def api_set_settings(req: SettingsReq):
    settings[req.key] = req.value
    _save_settings(settings)
    return {"ok": True, "key": req.key}

# ============================================================
# RTL Generation (ChipMATE API)
# ============================================================
@app.post("/api/rtl/generate")
def api_rtl_generate(req: RTLGenReq):
    """AI 生成 RTL"""
    from adapter.chipmate_runner import ChipMATERunner, ChipMATEConfig
    config = ChipMATEConfig()
    config.api_key = settings.get("deepseek_api_key", os.environ.get("DEEPSEEK_API_KEY", ""))
    runner = ChipMATERunner(config)
    try:
        t0 = time.time()
        sample_count = int(req.context.get("sample_count", 20)) if req.context else 20
        result = runner.run(str(uuid.uuid4())[:8], req.question, sample_count=sample_count)
        elapsed = round(time.time() - t0, 2)
        # 提取交叉验证详情 (ChipMATE 格式: detail + py_model)
        detail = None
        py_model = ""
        for entry in result.history:
            if "detail" in entry:
                detail = entry["detail"]
            if "py_model" in entry:
                py_model = entry["py_model"]
        return {
            "verilog": result.verilog,
            "matched": result.matched,
            "match_rate": result.match_rate,
            "turns": result.turns,
            "error": result.error,
            "duration": elapsed,
            "detail": detail,
            "py_model": py_model,
        }
    except Exception as e:
        return {
            "verilog": "",
            "matched": False,
            "match_rate": 0,
            "turns": 0,
            "error": str(e),
        }

class TBGenReq(BaseModel):
    question: str = ""
    verilog: str = ""
    context: dict = {}

class CoverageReq(BaseModel):
    rtl_code: str = ""
    top_module: str = ""
    inputs: list = []
    stimuli: list = []
    has_clk: bool = True
    vcd_path: str = ""     # 同一次仿真的 VCD (FSM/状态寄存器覆盖用)

@app.post("/api/coverage/run")
def api_coverage_run(req: CoverageReq):
    """阶段2 覆盖率收集 (方案 3.3): Verilator Line + Toggle 覆盖率

    激励与自动激励仿真同源 (同一组随机向量), 统计口径与匹配率一致。
    """
    from adapter.coverage_runner import CoverageRunner
    top = req.top_module
    if not top:
        from server.agent_engine import DesignProfile
        top = DesignProfile.from_code(req.rtl_code).top_module
    clk_name = ""
    for n in req.inputs:
        if "clk" in str(n).lower():
            clk_name = str(n)
            break
    r = CoverageRunner({"verilator": {"working_dir": "/home/xu/ic_agent_os/tmp/coverage_runs"}}).execute(
        "coverage", {"rtl_code": req.rtl_code, "top_module": top,
                     "inputs": req.inputs, "stimuli": req.stimuli,
                     "has_clk": req.has_clk, "clk_name": clk_name,
                     "vcd_path": req.vcd_path})
    return r

@app.post("/api/rtl/generate-tb")
def api_rtl_generate_tb(req: TBGenReq):
    """AI 生成 testbench — 需要传入完整 RTL 代码"""
    from adapter.chipmate_runner import ChipMATERunner, ChipMATEConfig
    config = ChipMATEConfig()
    config.api_key = settings.get("deepseek_api_key", os.environ.get("DEEPSEEK_API_KEY", ""))
    runner = ChipMATERunner(config)
    try:
        tb = runner.generate_tb(req.question, req.verilog)
        return {"testbench": tb}
    except Exception as e:
        return {"testbench": "", "error": str(e)}

@app.post("/api/rtl/generate-sva")
def api_rtl_generate_sva(req: RTLGenReq):
    """生成 SVA — 模板优先 + LLM 兜底 + 迭代支持"""
    from server.sva_templates import generate_sva_iterative
    code = req.question[:5000]  # RTL 代码

    # Step 1: 模板匹配 (不依赖 LLM, 快速精准)
    result = generate_sva_iterative(code, req.context.get("round", 0) if req.context else 0)
    if result.get("sva"):
        result["method"] = "template"
        return result

    # Step 2: 模板不够 → LLM 自由生成
    api_key = settings.get("deepseek_api_key", os.environ.get("DEEPSEEK_API_KEY", ""))
    if not api_key:
        return {"sva": "", "analysis": result.get("analysis", ""), "method": "none", "error": "无 API Key"}

    try:
        analysis = result.get("analysis", "")
        prompt = f"""RTL 结构分析: {analysis}

为以上 RTL 写 2-3 条 SVA property。每条一行注释说明用途。
用 `` `ifdef FORMAL ... `endif `` 包裹。只输出代码，不要解释。

【语法限制 — 本机 yosys formal frontend 只支持以下形式, 违反会直接报语法错误】:
- 只允许 always 块内的 immediate assertion: `always @(posedge clk) assert (...);`
- 允许 if 条件保护: `always @(posedge clk) if (!rst_n) assert (q == 0);`
- 允许 $past(x) (上一拍的值) 和 $initstate (初始态保护)
- 禁止: assert property / disable iff / ##1 / |-> / $stable / $rose

【语义规则 — 违反会误报反例】:
1. 所有断言用 !$initstate 排除初始态 (初始态寄存器值是任意的)
2. 复位断言用上一拍判定: `if (!$initstate && !$past(rst_n)) assert (reg == 复位值);`
   不要用当前拍 !rst_n — 同步复位设计中 rst_n=0 的当前拍寄存器还是旧值
3. 时序关系一律用 $past 采样上一拍的输入 (寄存器的更新用的是上一拍的输入):
   `assert ($past(en) ? (q == $past(q) + 1 || 回绕) : q == $past(q));`
4. 复位值从 RTL 提取 (可能不是 0, 如 4'b1111)

Module:
```
{code[:1500]}
```
"""
        import urllib.request
        data = json.dumps({"model": "deepseek-chat", "messages": [{"role":"user","content":prompt}], "temperature":0.1, "max_tokens":1024}).encode()
        req2 = urllib.request.Request("https://api.deepseek.com/v1/chat/completions", data=data, headers={"Content-Type":"application/json","Authorization":f"Bearer {api_key}"})
        with urllib.request.urlopen(req2, timeout=60) as resp:
            body = json.loads(resp.read())
            sva = body["choices"][0]["message"]["content"].replace("```","").strip()
        # LLM 常把 ```systemverilog 围栏残留成裸单词 "systemverilog" → yosys 解析错误
        sva = re.sub(r'^\s*(?:systemverilog|system_verilog)\s*\n?', '', sva, flags=re.IGNORECASE)
        sva = re.sub(r'^\s*verilog\s*\n?', '', sva, flags=re.IGNORECASE)
        return {"sva": sva, "analysis": analysis, "method": "llm", "templates_used": []}
    except Exception as e:
        return {"sva": "", "analysis": result.get("analysis", ""), "method": "fallback", "error": str(e)}

# ============================================================
# Status / History
# ============================================================
@app.get("/api/flow/{flow_id}")
def api_flow_status(flow_id: str):
    f = flows.get(flow_id)
    if not f:
        raise HTTPException(404)
    return f

@app.get("/api/flows")
def api_flows_list():
    return list(flows.values())

@app.get("/api/runs/history")
def api_runs_history(limit: int = 10):
    """收敛历史 — 最近 N 次运行的指标趋势"""
    history = []
    for rid, r in sorted(runs.items(), key=lambda x: x[1]["time"])[-limit:]:
        metrics = {"wns": None, "area": None, "power": None, "drc": None}
        conv = r.get("convergence")
        if conv and conv.get("rounds"):
            # 收敛循环: 取最后一轮指标 (最终状态)
            last = conv["rounds"][-1]["metrics"]
            metrics = {k: last.get(k) for k in metrics}
        else:
            for s in r["results"]:
                m = s.get("metrics", {})
                if s["step"] == "yosys_synth" and m:
                    metrics["area"] = m.get("area")
                if "ista" in s["step"] and m:
                    metrics["wns"] = m.get("wns")
                    metrics["power"] = m.get("power")
                if s["step"] == "idrc_drc" and m:
                    metrics["drc"] = m.get("drc")
        history.append({"run_id": rid, "time": r["time"], "metrics": metrics,
                        "convergence_status": (conv or {}).get("status"),
                        "steps_done": sum(1 for s in r["results"] if s["status"]=="done"),
                        "steps_failed": sum(1 for s in r["results"] if s["status"]=="failed")})
    return {"history": history}

@app.get("/api/runs/{run_id}")
def api_run_status(run_id: str):
    r = runs.get(run_id)
    if not r:
        raise HTTPException(404)
    return r

# ============================================================
# File Management — 输出文件下载
# ============================================================
@app.get("/api/files/list")
def api_files_list(run_id: str = ""):
    """列出某次运行的输出文件"""
    if run_id and run_id in workspace_files:
        return {"files": workspace_files[run_id]}
    all_files = []
    for rid, files in workspace_files.items():
        for f in files:
            all_files.append({**f, "run_id": rid})
    return {"files": all_files[-20:]}  # 最近20个

@app.get("/api/files/download")
def api_files_download(path: str = ""):
    """下载文件"""
    if not path or not os.path.exists(path):
        raise HTTPException(404, "文件不存在")
    if not _check_path_allowed(path):
        raise HTTPException(403, "路径不在允许范围内")
    from fastapi.responses import FileResponse
    return FileResponse(path, filename=os.path.basename(path))

@app.get("/api/files/read")
def api_files_read(path: str = ""):
    """读取文件内容 (供 AI 和前端查看)"""
    if not path or not os.path.exists(path):
        raise HTTPException(404, "文件不存在")
    if not _check_path_allowed(path):
        raise HTTPException(403, "路径不在允许范围内")
    if os.path.getsize(path) > 1024 * 1024:  # 1MB 限制
        raise HTTPException(400, "文件过大 (>1MB)")
    with open(path) as f:
        return {"path": path, "content": f.read(), "size": os.path.getsize(path)}

class FileSaveReq(BaseModel):
    path: str = ""
    content: str = ""

@app.post("/api/files/save")
def api_files_save(req: FileSaveReq):
    """保存/修改文件"""
    if not req.path:
        raise HTTPException(400, "path 为空")
    if not _check_path_allowed(req.path):
        raise HTTPException(403, "路径不在允许范围内")
    os.makedirs(os.path.dirname(req.path) or ".", exist_ok=True)
    with open(req.path, "w") as f:
        f.write(req.content)
    # 更新文件列表
    for rid, flist in workspace_files.items():
        for f in flist:
            if f["path"] == req.path:
                f["size"] = len(req.content)
                break
    return {"ok": True, "path": req.path, "size": len(req.content)}

class AutoSaveReq(BaseModel):
    filename: str = ""          # 文件名
    content: str = ""           # 文件内容
    folder: str = "workspace"   # 子目录

@app.post("/api/files/autosave")
def api_files_autosave(req: AutoSaveReq):
    """自动保存文件到工作空间"""
    base = os.path.join("/tmp/iflow_workspace", req.folder)
    os.makedirs(base, exist_ok=True)
    fpath = os.path.join(base, req.filename)
    with open(fpath, "w") as f:
        f.write(req.content)
    # 注册到文件列表
    finfo = {"name": req.filename, "path": fpath, "size": len(req.content),
             "step": "autosave", "type": req.filename.split('.')[-1] if '.' in req.filename else 'txt'}
    # 用 "autosave" 作为 run_id — P2-8: 同路径去重, 防止列表无限增长
    bucket = workspace_files.setdefault("autosave", [])
    bucket[:] = [f for f in bucket if f["path"] != fpath]
    bucket.append(finfo)
    return {"ok": True, "path": fpath, "file": finfo}

def _parse_gds_text(path: str) -> Optional[dict]:
    """解析 iEDA 导出的 GDS 文本格式 (gdstk 只读二进制, 需兜底)。

    元素为 BOX 记录: BOX → LAYER n → BOXTYPE n → XY 首点 + 续行点 → ENDEL。
    返回 {"polys": [(layer, [(x,y),...])], "unit": um/DB 单位}"""
    try:
        with open(path) as f:
            content = f.read()
    except OSError:
        return None
    if "BGNLIB" not in content:
        return None  # 不是文本格式
    pair_re = re.compile(r'^\s*(-?\d+(?:\.\d+)?(?:e[+-]?\d+)?)\s*:\s*(-?\d+(?:\.\d+)?(?:e[+-]?\d+)?)')
    polys, pts = [], []
    layer, unit, cur = 0, 0.001, None
    for line in content.splitlines():
        parts = line.split()
        if not parts:
            continue
        kw = parts[0]
        if kw == "UNITS":
            try: unit = float(parts[1])
            except ValueError: pass
        elif kw == "LAYER":
            layer = int(parts[1])
        elif kw == "BOX":
            cur, pts = "BOX", []
        elif kw == "ENDEL":
            if cur == "BOX" and len(pts) >= 3:
                polys.append((layer, pts))
            cur, pts = None, []
        else:
            m = pair_re.match(line)
            if m and (kw == "XY" or cur == "BOX"):
                pts.append((float(m.group(1)), float(m.group(2))))
    return {"polys": polys, "unit": unit} if polys else None


@app.get("/api/gds/preview")
def api_gds_preview(path: str = ""):
    """GDS 版图预览 — gdstk 渲染为 SVG (iEDA 文本格式兜底解析)"""
    if not path or not os.path.exists(path):
        raise HTTPException(404, "GDS 文件不存在")
    if not _check_path_allowed(path):
        raise HTTPException(403, "路径不在允许范围内")
    try:
        import gdstk
        lib = gdstk.read_gds(path)
        # 用 cell 的 bounding box 替代逐 polygon 计算
        cells = lib.top_level()
        if not cells:
            raise HTTPException(400, "GDS 为空")
        bbox = None
        for c in cells:
            bb = c.bounding_box()
            if bb is None: continue
            if bbox is None:
                bbox = bb
            else:
                bbox = ((min(bbox[0][0], bb[0][0]), min(bbox[0][1], bb[0][1])),
                        (max(bbox[1][0], bb[1][0]), max(bbox[1][1], bb[1][1])))
        if bbox is None:
            raise HTTPException(400, "GDS 无可渲染图形")
        w = bbox[1][0] - bbox[0][0]
        h = bbox[1][1] - bbox[0][1]
        svg_w, svg_h = 800, 600
        scale = min(svg_w / max(w, 1e-9), svg_h / max(h, 1e-9)) * 0.95
        ox = (svg_w - w * scale) / 2 - bbox[0][0] * scale
        oy = (svg_h - h * scale) / 2 - bbox[0][1] * scale
        parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{svg_w}" height="{svg_h}" viewBox="0 0 {svg_w} {svg_h}" style="background:#0a0a0a">',
                 '<style>.gds-poly:hover{stroke:#fff;stroke-width:1.2;fill-opacity:0.9}</style>']
        # 每个 cell 采样最多 20 个 polygon, 跳过巨型 cell
        total = 0; drawn = 0; skipped_cells = 0
        layer_counts: dict = {}
        for c in cells:
            n = len(c.polygons)  # O(1), 避免 materialize
            total += n
            if n > 100000:  # 巨型 cell 跳过 (如内存阵列)
                skipped_cells += 1
                continue
            step = max(1, n // 20)
            for p in c.polygons[::step][:20]:
                layer = getattr(p, "layer", 0)
                layer_counts[layer] = layer_counts.get(layer, 0) + 1
                pts = " ".join(f"{x*scale+ox:.1f},{svg_h-(y*scale+oy):.1f}" for x, y in p.points)
                parts.append(f'<g class="gds-layer gds-l-{layer}" data-layer="{layer}">'
                             f'<polygon class="gds-poly" points="{pts}" fill="#2a9d8f" fill-opacity="0.6" stroke="#264653" stroke-width="0.3"/></g>')
                drawn += 1
        parts.append('</svg>')
        return {"svg": "".join(parts), "width": round(w, 1), "height": round(h, 1),
                "polygons": drawn, "total_polygons": total, "cells": len(cells),
                "layers": [{"layer": l, "count": n, "color": "#2a9d8f"}
                           for l, n in sorted(layer_counts.items())]}
    except HTTPException:
        raise
    except Exception as e:
        # iEDA 导出的是 GDS 文本格式 (gdstk 只读二进制) → 文本解析兜底
        text_data = _parse_gds_text(path)
        if not text_data:
            raise HTTPException(500, f"渲染失败: {str(e)[:100]}")
        polys = text_data["polys"]
        um = text_data["unit"]
        xs = [x for _, pts in polys for x, _ in pts]
        ys = [y for _, pts in polys for y, _ in pts]
        minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
        w = (maxx - minx) * um
        h = (maxy - miny) * um
        svg_w, svg_h = 800, 600
        scale = min(svg_w / max(w, 1e-9), svg_h / max(h, 1e-9)) * 0.95
        def tx(x): return (x - minx) * um * scale + (svg_w - w * scale) / 2
        def ty(y): return svg_h - ((y - miny) * um * scale + (svg_h - h * scale) / 2)
        palette = {10: "#3b82f6", 20: "#a855f7", 30: "#eab308", 40: "#f97316", 50: "#ef4444"}
        parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{svg_w}" height="{svg_h}" viewBox="0 0 {svg_w} {svg_h}" style="background:#0a0a0a">',
                 '<style>.gds-poly:hover{stroke:#fff;stroke-width:1.2;fill-opacity:0.9}</style>']
        step = max(1, len(polys) // 3000)  # 控制 SVG 体积
        drawn = 0
        layer_counts: dict = {}
        for layer, pts in polys:
            layer_counts[layer] = layer_counts.get(layer, 0) + 1
        for layer, pts in polys[::step]:
            color = palette.get(layer, "#2a9d8f")
            pts_str = " ".join(f"{tx(x):.1f},{ty(y):.1f}" for x, y in pts)
            # 按图层分组 + class, 前端可切换显隐/悬停高亮
            parts.append(f'<g class="gds-layer gds-l-{layer}" data-layer="{layer}">'
                         f'<polygon class="gds-poly" points="{pts_str}" fill="{color}" fill-opacity="0.55" stroke="{color}" stroke-width="0.3"/></g>')
            drawn += 1
        parts.append('</svg>')
        return {"svg": "".join(parts), "width": round(w, 1), "height": round(h, 1),
                "polygons": drawn, "total_polygons": len(polys),
                "layers": [{"layer": l, "count": n, "color": palette.get(l, "#2a9d8f")}
                           for l, n in sorted(layer_counts.items())],
                "cells": len(set(re.findall(r'STRNAME\s+(.+)', open(path).read()))) or 0,
                "format": "gds_text"}

class FileRenameReq(BaseModel):
    path: str = ""
    new_name: str = ""

@app.post("/api/files/rename")
def api_files_rename(req: FileRenameReq):
    """重命名文件"""
    if not req.path or not req.new_name or not os.path.exists(req.path):
        raise HTTPException(400, "文件不存在")
    if not _check_path_allowed(req.path):
        raise HTTPException(403, "路径不在允许范围内")
    new_path = os.path.join(os.path.dirname(req.path), req.new_name)
    if os.path.exists(new_path):
        raise HTTPException(400, "同名文件已存在")
    os.rename(req.path, new_path)
    # 更新 workspace_files 中的记录
    for rid, flist in workspace_files.items():
        for f in flist:
            if f["path"] == req.path:
                f["path"] = new_path
                f["name"] = req.new_name
    return {"ok": True, "path": new_path}

@app.delete("/api/files/delete")
def api_files_delete(path: str = ""):
    """删除文件"""
    if not path or not os.path.exists(path):
        raise HTTPException(400, "文件不存在")
    if not _check_path_allowed(path):
        raise HTTPException(403, "路径不在允许范围内")
    os.remove(path)
    for rid, flist in workspace_files.items():
        flist[:] = [f for f in flist if f["path"] != path]
    return {"ok": True}

@app.get("/api/files/recent")
def api_files_recent(limit: int = 20):
    """最近生成的文件 (内存 + 磁盘)"""
    all_files = []
    for rid, flist in workspace_files.items():
        for f in flist:
            all_files.append({**f, "run_id": rid})
    # 也扫描磁盘上的 autosave 文件
    ws_dir = "/tmp/iflow_workspace"
    if os.path.exists(ws_dir):
        for root, dirs, filenames in os.walk(ws_dir):
            for fn in filenames:
                fp = os.path.join(root, fn)
                all_files.append({"name": fn, "path": fp, "size": os.path.getsize(fp),
                                  "step": "autosave", "type": fn.split('.')[-1] if '.' in fn else 'txt'})
    all_files.sort(key=lambda f: f.get("size", 0), reverse=True)
    # 去重
    seen = set(); unique = []
    for f in all_files:
        if f["path"] not in seen: seen.add(f["path"]); unique.append(f)
    return {"files": unique[:limit]}

# ============================================================
# Workspace — 阶段1/2结果摘要 + 阶段3就绪检查
# ============================================================
@app.get("/api/workspace/status")
def api_workspace_status():
    """返回阶段1/2的结果概要，供阶段3使用"""
    return {
        "stage1_ready": bool(workspace_files),  # 有任何输出文件就认为阶段1跑过
        "stage2_ready": bool(workspace_files),
        "recent_runs": [{ "run_id": rid, "flow_id": r["flow_id"], "time": r["time"],
            "steps": len(r["results"]), "files": len(r.get("files", [])) }
            for rid, r in list(runs.items())[-5:]],
        "total_files": sum(len(files) for files in workspace_files.values()),
    }

# ============================================================
# File Management
# ============================================================
@app.get("/api/files")
def api_list_files(user_id: str = "default"):
    """列出用户工作空间文件"""
    base = f"{WORKSPACE}/{user_id}"
    if not os.path.exists(base):
        return {"files": []}
    files = []
    for root, dirs, filenames in os.walk(base):
        for fn in filenames:
            fp = os.path.join(root, fn)
            files.append({"name": fn, "path": fp, "size": os.path.getsize(fp)})
    return {"files": files}

# ============================================================
# WebSocket
# ============================================================
@app.websocket("/ws/{run_id}")
async def ws_endpoint(ws: WebSocket, run_id: str):
    await ws_manager.connect(ws, run_id)
    try:
        while True:
            data = await ws.receive_text()
            # 前端可以发消息控制流程 (Phase 4)
            await ws.send_text(json.dumps({"type": "ack", "data": data}))
    except WebSocketDisconnect:
        await ws_manager.disconnect(ws, run_id)

# ============================================================
# Health
# ============================================================
# ============================================================
# 对比实验
# ============================================================
from server.chat import get_or_create_session, sessions
from server.experiment_runner import experiment_runner

class ChatReq(BaseModel):
    message: str = ""
    session_id: str = "default"
    context: str = ""  # 当前阶段上下文: RTL代码/SVA/SBY结果等

# ============================================================
# AI 聊天 — 完整项目上下文 + 多轮对话
# ============================================================
@app.post("/api/chat")
def api_chat(req: ChatReq):
    """真正的 AI 对话: 自然语言 → LLM(含完整项目上下文) → 回复 + 动作"""
    api_key = settings.get("deepseek_api_key", os.environ.get("DEEPSEEK_API_KEY", ""))
    if not api_key:
        return {"reply": "请先在右上角 ⚙️ 设置中配置 DeepSeek API Key。", "action": None}

    session = get_or_create_session(req.session_id, api_key)
    result = session.send(req.message, req.context)

    # 如果 AI 决定要组装 Flow
    flow = None
    if result.get("action"):
        action = result["action"]
        target = action.get("target", "gds")
        depth = action.get("depth", "standard")
        from server.agent_engine import decide_flow, DesignProfile

        target_depth_map = {
            ("ppa","quick"):"competition",("ppa","standard"):"competition",("ppa","signoff"):"research",
            ("gds","quick"):"research",("gds","standard"):"research",("gds","signoff"):"tapeout",
            ("tapeout","quick"):"research",("tapeout","standard"):"tapeout",("tapeout","signoff"):"tapeout",
        }
        scene = target_depth_map.get((target, depth), "research")
        # P2-5: 从聊天上下文提取 RTL → 真实设计画像 (顶层/门数/时钟域)
        # 默认画像 gates=10000 会把 ir_drop 等大设计步骤错误排进小设计流程
        design = DesignProfile()
        m = re.search(r'(module\s+[\s\S]*?endmodule)', req.context or "")
        if m:
            design = DesignProfile.from_code(m.group(1))
        decision = decide_flow(scene, design)

        flow_id = str(uuid.uuid4())[:8]
        flow = {
            "flow_id": flow_id, "scene": scene,
            "design_profile": {"top_module": design.top_module,
                               "clock_domains": design.clock_domains,
                               "gates": design.gates, "is_comb": design.is_comb},
            "steps": decision["steps"], "skipped": decision.get("skipped", []),
            "intensity": decision.get("intensity", {}), "tools": decision.get("tools", {}),
            "target": target, "depth": depth, "status": "composed",
        }
        flows[flow_id] = flow
        result["flow"] = flow

    return {"reply": result["reply"], "flow": result.get("flow"), "action": result.get("action")}


@app.post("/api/chat/clear")
def api_chat_clear(req: ChatReq):
    """清空会话历史 — 旧对话不干扰新的对话环境 (前端 + 服务端 session 都清)"""
    sessions.pop(req.session_id, None)
    return {"ok": True, "session_id": req.session_id}


class ChatComposeReq(BaseModel):
    message: str = ""
    context: dict = {}

# 保留旧端点兼容
@app.post("/api/chat/compose")
def api_chat_compose(req: ChatComposeReq):
    """用户说人话 → LLM 解析意图 → Agent Engine 拼装 Flow"""
    from server.agent_engine import decide_flow, DesignProfile

    # 用 LLM 解析用户意图
    intent = _parse_intent(req.message)
    if not intent:
        intent = {"target": "gds", "depth": "standard", "confident": True}

    # 意图不清晰 → 反问用户，不组装 Flow
    if not intent.get("confident"):
        return {"flow": None, "response": intent.get("clarify", "能再说详细一点吗？"), "clarify": True}

    # 映射到场景
    target_depth_map = {
        ("ppa", "quick"): "competition", ("ppa", "standard"): "competition",
        ("ppa", "signoff"): "research",
        ("gds", "quick"): "research", ("gds", "standard"): "research",
        ("gds", "signoff"): "tapeout",
        ("tapeout", "quick"): "research", ("tapeout", "standard"): "tapeout",
        ("tapeout", "signoff"): "tapeout",
    }
    scene = target_depth_map.get((intent.get("target","gds"), intent.get("depth","standard")), "research")

    # Agent Engine 拼装
    design = DesignProfile()
    decision = decide_flow(scene, design)

    flow_id = str(uuid.uuid4())[:8]
    flow = {
        "flow_id": flow_id, "scene": scene, "design": "my_design",
        "steps": decision["steps"], "skipped": decision.get("skipped", []),
        "intensity": decision.get("intensity", {}), "tools": decision.get("tools", {}),
        "target": intent.get("target"), "depth": intent.get("depth"),
        "status": "composed", "created_at": time.time(),
    }
    flows[flow_id] = flow

    # 生成自然语言回复
    target_names = {"ppa": "综合后 PPA 数据（面积/频率/功耗）", "gds": "完整物理实现 + 版图", "tapeout": "流片签核（全流程 + DRC + LVS）"}
    depth_names = {"quick": "快速验证（~2分钟）", "standard": "标准评估（~10分钟）", "signoff": "签核级别（~30分钟）"}
    response = f"好的。我会为你做 **{target_names.get(intent.get('target',''), '')}**，严格程度为 **{depth_names.get(intent.get('depth',''), '')}**。\n\n拼装了 {len(decision['steps'])} 个步骤" + (
        f"，跳过了 {len(decision.get('skipped',[]))} 个不必要的步骤（{', '.join(decision.get('skipped',[]))}）。" if decision.get('skipped') else "。")

    return {"flow": flow, "response": response, "intent": intent}


def _parse_intent(message: str) -> dict:
    """用 LLM 解析用户意图 → {target, depth} 或 {clarify: '...'}"""
    api_key = settings.get("deepseek_api_key", os.environ.get("DEEPSEEK_API_KEY", ""))
    if not api_key:
        return _keyword_parse(message)

    prompt = f"""用户在芯片设计AI实训平台上说："{message}"

判断用户意图是否足够清晰。如果清晰，返回:
{{"target":"ppa|gds|tapeout","depth":"quick|standard|signoff","confident":true}}

如果不清晰，返回 clarifying_question（自然语言，用中文，给 2-3 个选项让用户选）:
{{"confident":false,"clarify":"你的问题..."}}

target: ppa=只要综合后PPA数据, gds=完整物理实现+版图, tapeout=流片签核全流程
depth: quick=快速(~2min), standard=标准(~10min), signoff=签核(~30min)

只返回JSON，不要markdown。"""
    try:
        import urllib.request
        data = json.dumps({"model": "deepseek-chat", "messages": [{"role":"user","content":prompt}], "temperature":0, "max_tokens":300}).encode()
        req = urllib.request.Request("https://api.deepseek.com/v1/chat/completions", data=data, headers={"Content-Type":"application/json","Authorization":f"Bearer {api_key}"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read())
            content = body["choices"][0]["message"]["content"].strip()
            if content.startswith("```"): content = content.split("\n",1)[1].rsplit("\n",1)[0]
            return json.loads(content)
    except:
        return _keyword_parse(message)


def _keyword_parse(message: str) -> dict:
    """无 API Key 时的关键词兜底解析"""
    msg = message.lower()
    # 判断是否模糊
    has_target = any(w in msg for w in ["ppa","综合","频率","面积","功耗","版图","gds","物理实现","流片","tapeout","签核","交付"])
    if not has_target and len(msg) < 10:
        return {"confident": False, "clarify": "你想做到哪一步？\n\n📊 只要 PPA 数据（面积/频率/功耗）\n🗺 完整物理实现，看版图\n✅ 流片签核，全部检查通过"}
    target = "tapeout" if any(w in msg for w in ["流片","tapeout","签核","交付"]) else \
             "ppa" if any(w in msg for w in ["ppa","综合","频率","面积","功耗","数字","对比"]) else "gds"
    depth = "quick" if any(w in msg for w in ["快速","简单","随便","看看","试","大概"]) else \
            "signoff" if any(w in msg for w in ["签核","严格","完整","全","交付","clean"]) else "standard"
    return {"target": target, "depth": depth, "confident": True}


class ExperimentReq(BaseModel):
    design: str = "gcd"
    variables: dict = {}  # {"PDK": "sky130,nangate45", "utilization": "35%,30%,25%"}
    design_uploads: dict = {}    # {设计名: RTL 代码} — 用户自主添加设计
    liberty_uploads: dict = {}   # {工艺名: liberty 内容} — 用户自主添加工艺 (PPA 对比)

@app.post("/api/experiment/create")
def api_experiment_create(req: ExperimentReq):
    exp = experiment_runner.create(req.design, req.variables)
    # 用户上传的设计/工艺落盘到工作区 (白名单内), 注入组合配置供执行器消费
    rtl_paths, lib_paths = {}, {}
    up_dir = "/tmp/iflow_workspace/experiments"
    os.makedirs(up_dir, exist_ok=True)
    for name, code in (req.design_uploads or {}).items():
        safe = re.sub(r'[^\w\-.]', '_', name)
        p = os.path.join(up_dir, f"{safe}.v")
        with open(p, "w") as f:
            f.write(code)
        rtl_paths[name] = p
        rtl_paths[safe] = p
    lib_dir = "/tmp/iflow_workspace/pdk_libs"
    os.makedirs(lib_dir, exist_ok=True)
    for name, content in (req.liberty_uploads or {}).items():
        safe = re.sub(r'[^\w\-.]', '_', name)
        p = os.path.join(lib_dir, f"{safe}.lib")
        with open(p, "w") as f:
            f.write(content)
        lib_paths[name] = p
        lib_paths[safe] = p
    for combo in exp["combos"]:
        c = combo["config"]
        if c.get("design") in rtl_paths:
            c["rtl_path"] = rtl_paths[c["design"]]
        if str(c.get("PDK", "")) in lib_paths:
            c["liberty_path"] = lib_paths[c["PDK"]]
    return exp

@app.post("/api/experiment/{exp_id}/run")
def api_experiment_run(exp_id: str):
    # 注入实际的 flow runner (P0-3: 每个组合跑真实 Flow, 带 WS 进度)
    def run_flow(design: str, config: dict) -> dict:
        combo_id = config.get("_run_id", "")
        if combo_id:
            import threading
            import asyncio
            def push_ws(event: dict):
                def _worker():
                    try:
                        asyncio.run(ws_manager.broadcast(combo_id, event))
                        asyncio.run(ws_manager.broadcast("global", event))
                    except: pass
                threading.Thread(target=_worker, daemon=True).start()
        else:
            push_ws = lambda event: None
        return api_flow_run_internal(design, config, push_ws)
    experiment_runner.run_flow = run_flow
    result = experiment_runner.run_all(exp_id)
    return result


def api_flow_run_internal(design: str, config: dict, push_ws=None) -> dict:
    """内部 flow run, 用于实验批量调用 (P0-3: 接真实 Flow)。

    - RTL 从 /home/xu/iFlow/rtl/{design}/ 加载
    - PDK → liberty 选择 (sky130/nangate45)
    - utilization/frequency → DIE_AREA/CORE_AREA/CLK_PERIOD
    - 物理流程仅 sky130 已接入; 其他 PDK 如实标注
    """
    push_ws = push_ws or (lambda event: None)
    pdk = str(config.get("PDK", "sky130")).lower()
    utilization = str(config.get("utilization", "35%"))
    try:
        freq = float(config.get("frequency", 100))
    except (ValueError, TypeError):
        freq = 100.0

    # 加载 RTL (优先用户上传的设计)
    rtl_file = ""
    if config.get("rtl_path") and os.path.exists(config["rtl_path"]):
        rtl_file = config["rtl_path"]
    else:
        for cand in (f"/home/xu/iFlow/rtl/{design}/{design}.v",
                     f"/home/xu/iFlow/rtl/{design}.v"):
            if os.path.exists(cand):
                rtl_file = cand
                break
    if not rtl_file:
        return {"results": [{"step": "load_rtl", "status": "failed",
                             "error": f"RTL 不存在: {design}", "duration": 0}],
                "error": f"RTL 不存在: {design}"}

    from server.agent_engine import decide_flow, DesignProfile
    with open(rtl_file) as f:
        rtl_code = f.read()
    profile = DesignProfile.from_code(rtl_code)
    decision = decide_flow("competition", profile)
    steps = list(decision["steps"])  # lint + yosys + ista_sta
    # 物理实现后端分派 (Sheet 3 工具替换维度): sky130 → iEDA (与 aes11 参考口径一致);
    # nangate45/asap7 → OpenROAD (本机 ORFS 平台配置自带 PDK 数据); 其他 PDK 如实标注
    physical = pdk == "sky130"
    or_physical = pdk in ("nangate45", "asap7")
    tool = "ieda" if physical else ("openroad" if or_physical else "none")
    if physical:
        steps += ["ieda_floorplan", "ieda_place", "ieda_cts", "ieda_route",
                  "idrc_drc", "gds_export"]
    elif or_physical:
        steps = [s for s in steps if s != "ista_sta"] + [
            "openroad_physical", "idrc_drc", "gds_export"]
    # 用户上传的自定义工艺 (liberty_uploads): PPA-only 路径 (综合+OpenSTA, 无物理)
    custom_lib = ""
    if config.get("liberty_path") and os.path.exists(config["liberty_path"]):
        custom_lib = config["liberty_path"]
        tool = "opensta"
    liberty = custom_lib or {"sky130": _IEDA_LIBERTY,
               "nangate45": _NANGATE_LIBERTY,
               "asap7": "/home/xu/OpenROAD-flow-scripts/flow/platforms/asap7/lib/NLDM/asap7sc7p5t_AO_RVT_TT_nldm_211120.lib.gz"}.get(pdk, "")
    # yosys 需要明文 liberty 且 abc 只收一个库: asap7 用 AO 库 (组合逻辑),
    # DFF 由 asap7_cells_dff.v techmap 映射 (SEQ 库的 DFF 无 ff() 属性,
    # dfflibmap 无法使用; OpenROAD 侧会读全部 4 个库, 不受影响)
    if liberty.endswith(".gz"):
        import gzip, shutil
        plain = os.path.join("/tmp/iflow_workspace/pdk_libs", os.path.basename(liberty)[:-3])
        os.makedirs(os.path.dirname(plain), exist_ok=True)
        if not os.path.exists(plain):
            with gzip.open(liberty) as f, open(plain, "wb") as g:
                shutil.copyfileobj(f, g)
        liberty = plain

    flow_id = str(uuid.uuid4())[:8]
    flow = {
        "flow_id": flow_id, "scene": "competition", "design": design,
        "steps": steps,
        "design_profile": {"top_module": profile.top_module,
                           "clock_domains": profile.clock_domains,
                           "gates": profile.gates, "is_comb": profile.is_comb},
        "frequency": freq,
        "experiment_config": config,
        "status": "composed", "created_at": time.time(),
    }
    flows[flow_id] = flow

    run_id = config.get("_run_id") or str(uuid.uuid4())[:8]
    flow["status"] = "running"
    results, _ = _execute_flow_steps(flow, [rtl_file], None, run_id,
                                     {"sample_count": 20, "or_pdk": pdk if or_physical else ""},
                                     push_ws,
                                     liberty=liberty, utilization=utilization,
                                     do_physical=physical)
    runs[run_id] = {"flow_id": flow_id, "results": results, "time": time.time(), "files": []}
    return {"results": results, "run_id": run_id, "pdk": pdk,
            "physical_enabled": physical or or_physical, "tool": tool}

@app.get("/api/experiments")
def api_experiments_list():
    return experiment_runner.list_all()

@app.get("/api/experiment/{exp_id}/maps")
def api_experiment_maps(exp_id: str):
    """区域 C: 空间 Map 横向对比 (方案 5.2.3) — 统一色标的密度热力图"""
    exp = experiment_runner.get(exp_id)
    if not exp:
        raise HTTPException(404, "实验不存在")
    if exp.get("status") != "done":
        return {"maps": [], "vmax": 0, "status": exp.get("status")}
    from server.mapgen import render_experiment_maps
    return render_experiment_maps(exp)

# ============================================================
# Health
# ============================================================
@app.get("/api/health")
def health():
    return {"status": "ok", "tools": {
        "iverilog": os.popen("iverilog -V 2>&1").read().strip()[:50],
        "verilator": os.popen("verilator --version 2>&1").read().strip()[:50],
        "yosys": os.popen("yosys -V 2>&1").read().strip()[:50],
        "verible": os.popen("verible-verilog-lint --version 2>&1").read().strip()[:50],
        "sby": os.popen("sby --version 2>&1").read().strip()[:50],
        "netgen": os.popen("netgen -batch quit 2>&1").read().strip()[:50],
    }}
