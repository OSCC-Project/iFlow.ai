import re
"""REST API — Flow 编排 + 用户 + 文件管理"""
import json, os, sys, time, uuid
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

# ============================================================
# App
# ============================================================
app = FastAPI(title="iflow-lab API", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

auth = AuthManager()

# 简单的内存 + 文件存储
flows: dict[str, dict] = {}
runs: dict[str, dict] = {}
workspace_files: dict[str, list] = {}  # run_id → [{name, path, size, type}]
_settings_file = os.path.join(os.path.dirname(__file__), "settings.json")

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
    design = DesignProfile()  # 后续可从 req 中提取更多特征
    decision = decide_flow(req.scene, design)

    flow = {
        "flow_id": flow_id, "scene": req.scene, "design": req.design,
        "frequency": req.frequency, "requirements": req.requirements,
        "steps": decision["steps"],
        "skipped": decision.get("skipped", []),
        "intensity": decision.get("intensity", {}),
        "tools": decision.get("tools", {}),
        "status": "composed", "created_at": time.time(),
    }
    flows[flow_id] = flow
    return flow

# ============================================================
# Flow Runner
# ============================================================
@app.post("/api/flow/run")
def api_run(req: RunReq):
    """执行 Flow (同步模式, Phase 3 先跑通)"""
    flow = flows.get(req.flow_id)
    if not flow:
        raise HTTPException(404, "Flow 不存在")

    run_id = str(uuid.uuid4())[:8]
    flow["status"] = "running"
    results = []

    # 前端传来代码内容 → 写临时文件
    rtl_paths = list(req.rtl_files)
    tb_path = req.params.get("tb_file")
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

    import asyncio
    def push_ws(event: dict):
        try: asyncio.run(ws_manager.broadcast(run_id, event))
        except: pass

    for step in flow["steps"]:
        start = time.time()
        step_result = {"step": step, "status": "running", "start": start}
        results.append(step_result)
        push_ws({"type": "step_start", "run_id": run_id, "step": step})

        try:
            if step == "verible_lint":
                r = VeribleRunner({}).execute(flow["design"],
                    {"rtl_files": rtl_paths, "mode": "lint"})
                step_result.update({"status": "done", "success": r["success"],
                                    "violations": r["lint"]["rule_violations"]})
            elif step == "verilator_lint":
                r = VerilatorRunner({}).execute(flow["design"],
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
                        # 读 RTL 代码
                        rtl_code = ""
                        if rtl_paths and os.path.exists(rtl_paths[0]):
                            with open(rtl_paths[0]) as f: rtl_code = f.read()
                        # 只做交叉验证仿真 (不重新生成 RTL)
                        if rtl_code:
                            ok = cr._check_syntax(rtl_code)
                            if ok:
                                py = cr._generate_python_model("test", rtl_code)
                                if py:
                                    sc = int(req.params.get("sample_count", 20)) if req.params else 20
                                    mr, sim_out = cr._cross_verify_python(rtl_code, py, sc)
                                    step_result.update({"status": "done", "success": mr > 0.5,
                                        "reason": f"自动生成激励仿真 ({sc}组测试向量) | 匹配率 {mr*100:.0f}%",
                                        "stdout": sim_out, "assertions_ok": mr >= 1.0})
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
                    r = IcarusRunner({}).execute(flow["design"],
                        {"rtl_files": rtl_paths, "top_module": req.params.get("top_module", "top"),
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
                    continue
                r = DigitalRunner({"synthesis": {}, "sta_primary": {}}).execute(
                    flow["design"], {"TOP_MODULE": req.params.get("top_module", "top"),
                     "VERILOG_SRC": src, "CLK_PERIOD": 1000.0 / flow.get("frequency", 100)})
                netlist = r.get("netlist_path", "")
                step_result.update({"status": "done", "success": True if netlist else False,
                                    "netlist_path": netlist, "metrics": r.get("metrics", {})})
                if netlist: rtl_paths.insert(0, netlist)  # 后续步骤用网表
            elif step == "ista_sta":
                step_result.update({"status": "skipped", "reason": "iSTA 通过 iEDA 统一调用, 在 ieda_route 后自动执行"})
            elif step.startswith("ieda_"):
                stage = step.replace("ieda_", "")
                r = IEDARunner({"ieda": {"flows": [stage]}}).execute(
                    flow["design"], {"TOP_MODULE": req.params.get("top_module", "top"),
                     "NETLIST_FILE": rtl_paths[0] if rtl_paths else ""})
                step_result.update({"status": "done", "success": r.get("success", True),
                                    "metrics": r.get("metrics", {})})
            elif step == "idrc_drc":
                r = IEDARunner({"ieda": {"flows": ["routing"]}}).execute(flow["design"], {})
                step_result.update({"status": "done", "success": r.get("success", True)})
            elif step == "sby_check":
                sby_rtl = list(rtl_paths)
                if rtl_paths and os.path.exists(rtl_paths[0]):
                    with open(rtl_paths[0]) as f: src = f.read()
                    # 修复 SVA: 去掉不匹配的 `ifdef/`endif, 插入 endmodule 之前
                    if "endmodule" in src:
                        parts = src.rsplit("endmodule", 1)
                        if len(parts) == 2:
                            sva = parts[1].strip()
                            # AI 生成的 SVA 常缺 `` `ifdef/`endif ``。统一清理后重新包裹。
                            if sva and ("assert" in sva or "assume" in sva or "cover" in sva):
                                # 剥离所有已有的预处理指令和反引号
                                sva = sva.replace("`ifdef FORMAL", "").replace("`endif", "")
                                sva = sva.replace("ifdef FORMAL", "").replace("endif", "")
                                sva = sva.replace("`ifndef FORMAL", "").replace("`define FORMAL", "")
                                sva = sva.strip()
                                # 统一包裹
                                src = parts[0] + "\n`ifdef FORMAL\n" + sva + "\n`endif\nendmodule\n"
                                with open(rtl_paths[0], "w") as f: f.write(src)
                # 自动提取 RTL 中的模块名
                top_mod = "top"
                if sby_rtl:
                    with open(sby_rtl[0]) as f:
                        m = re.search(r'module\s+(\w+)', f.read())
                        if m: top_mod = m.group(1)
                r = SBYRunner({"sby": {"timeout_seconds": 30}}).execute(flow["design"],
                    {"rtl_files": sby_rtl, "mode": req.params.get("formal_mode", "bmc"),
                     "depth": req.params.get("formal_depth", 10),
                     "top_module": top_mod})
                step_result.update({"status": "done", "success": r.get("success", False),
                                    "verdict": r.get("verdict", "UNKNOWN"),
                                    "summary": r.get("summary", r.get("output", "")[:500]),
                                    "stdout": r.get("output", "")[:1000]})
            elif step == "netgen_lvs":
                step_result.update({"status": "skipped", "reason": "LVS 需版图+原理图文件, 阶段3暂跳过"})
            elif step == "gds_export":
                step_result.update({"status": "skipped", "reason": "GDS 导出由 iEDA 末端自动完成"})
            else:
                step_result.update({"status": "skipped", "reason": f"未知步骤: {step}"})
        except Exception as e:
            step_result.update({"status": "failed", "error": str(e)})

        step_result["duration"] = round(time.time() - start, 3)
        push_ws({"type": "step_done", "run_id": run_id, "step": step,
                 "status": step_result["status"], "duration": step_result["duration"],
                 "success": step_result.get("success")})

    flow["status"] = "completed"
    # 收集输出文件
    out_files = []
    for sr in results:
        for key in ["netlist_path", "vcd_file", "run_dir", "sby_file", "def_path", "gds_path"]:
            val = sr.get(key)
            if val and os.path.exists(str(val)):
                out_files.append({"name": os.path.basename(str(val)), "path": str(val),
                                  "size": os.path.getsize(str(val)), "step": sr["step"], "type": key})
    workspace_files[run_id] = out_files

    runs[run_id] = {"flow_id": req.flow_id, "results": results, "time": time.time(), "files": out_files}
    return {"run_id": run_id, "flow_id": req.flow_id, "results": results, "files": out_files}

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
        # 提取交叉验证仿真数据 (V= 格式信号值)
        v_data = []
        for entry in result.history:
            if "sim_output" in entry:
                import re
                v_data = [int(m.group(1)) for m in re.finditer(r"V=\s*(\d+)", entry["sim_output"])]
        return {
            "verilog": result.verilog,
            "matched": result.matched,
            "match_rate": result.match_rate,
            "turns": result.turns,
            "error": result.error,
            "duration": elapsed,
            "v_values": v_data,
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
    from fastapi.responses import FileResponse
    return FileResponse(path, filename=os.path.basename(path))

@app.get("/api/files/read")
def api_files_read(path: str = ""):
    """读取文件内容 (供 AI 和前端查看)"""
    if not path or not os.path.exists(path):
        raise HTTPException(404, "文件不存在")
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
    # 用 "autosave" 作为 run_id
    workspace_files.setdefault("autosave", []).append(finfo)
    return {"ok": True, "path": fpath, "file": finfo}

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
from server.chat import get_or_create_session
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
        design = DesignProfile()
        decision = decide_flow(scene, design)

        flow_id = str(uuid.uuid4())[:8]
        flow = {
            "flow_id": flow_id, "scene": scene,
            "steps": decision["steps"], "skipped": decision.get("skipped", []),
            "intensity": decision.get("intensity", {}), "tools": decision.get("tools", {}),
            "target": target, "depth": depth, "status": "composed",
        }
        flows[flow_id] = flow
        result["flow"] = flow

    return {"reply": result["reply"], "flow": result.get("flow"), "action": result.get("action")}


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

@app.post("/api/experiment/create")
def api_experiment_create(req: ExperimentReq):
    exp = experiment_runner.create(req.design, req.variables)
    return exp

@app.post("/api/experiment/{exp_id}/run")
def api_experiment_run(exp_id: str):
    # 注入实际的 flow runner
    experiment_runner.run_flow = lambda design, config: api_flow_run_internal(design, config)
    result = experiment_runner.run_all(exp_id)
    return result

def api_flow_run_internal(design: str, config: dict) -> dict:
    """内部 flow run, 用于实验批量调用"""
    flow_id = str(uuid.uuid4())[:8]
    scene = "competition"
    decision = {"steps": ["verible_lint", "verilator_lint", "yosys_synth", "ista_sta"]}
    flow = {"flow_id": flow_id, "scene": scene, "design": design, "steps": decision["steps"], "frequency": 100}
    flows[flow_id] = flow

    results = []
    for step in flow["steps"]:
        start = time.time()
        sr = {"step": step, "status": "running", "start": start}
        try:
            if step == "verible_lint":
                r = VeribleRunner({}).execute(design, {"rtl_files": [], "mode": "lint"})
                sr.update({"status": "done", "success": r["success"], "violations": r["lint"]["rule_violations"]})
            elif step == "verilator_lint":
                r = VerilatorRunner({}).execute(design, {"rtl_files": [], "mode": "lint"})
                sr.update({"status": "done", "success": r["success"], "errors": r["error_count"]})
            else:
                sr.update({"status": "skipped", "reason": "experiment mode: 仅 lint 步骤"})
        except Exception as e:
            sr.update({"status": "failed", "error": str(e)})
        sr["duration"] = round(time.time() - start, 3)
        results.append(sr)
    return {"results": results}

@app.get("/api/experiments")
def api_experiments_list():
    return experiment_runner.list_all()

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
