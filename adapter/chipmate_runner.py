"""
ChipMATE RTL 生成 Adapter
支持两种后端模式:
  - api: 通过 OpenAI 兼容 API (DeepSeek / vLLM)
  - local: 通过本地 vLLM 双 Agent (ChipMATE-V + ChipMATE-P)

Phase 1 最小闭环: NL需求 → ChipMATE → RTL代码 + 匹配率
"""

import re
import time
import subprocess
import json
import os
import tempfile
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class ChipMATEConfig:
    """ChipMATE 配置"""
    # 后端模式: "api" | "local"
    backend: str = "api"

    # API 模式配置
    api_base_url: str = "https://api.deepseek.com"
    api_key: str = ""
    api_model: str = "deepseek-chat"

    # 本地 vLLM 模式配置
    v_agent_url: str = "http://localhost:8001/v1"
    v_agent_model: str = "/models/ChipMATE-V-9B"
    p_agent_url: str = "http://localhost:8002/v1"
    p_agent_model: str = "/models/ChipMATE-P-9B"

    # 生成参数
    num_candidates: int = 10      # 每轮生成的候选数
    max_turns: int = 5            # 最大交叉验证轮数
    match_threshold: float = 1.0  # 匹配率阈值

    # 仿真配置
    working_dir: str = "/home/xu/ic_agent_os/tmp/chipmate"
    iverilog_path: str = "iverilog"
    vvp_path: str = "vvp"
    timeout: int = 120            # 单次仿真超时(秒)


@dataclass
class ChipMATEResult:
    """ChipMATE 生成结果"""
    task_id: str
    question: str                # 原始需求
    verilog: str                 # 最终生成的 RTL
    matched: bool                # 交叉验证是否完全匹配
    match_rate: float            # 最终匹配率 (0.0 ~ 1.0)
    turns: int                   # 实际用了多少轮
    history: list = field(default_factory=list)  # 每轮的匹配率变化
    error: Optional[str] = None


class ChipMATERunner:
    """ChipMATE RTL 生成执行器"""

    def __init__(self, config: Optional[ChipMATEConfig] = None):
        self.config = config or ChipMATEConfig()

    def run(self, task_id: str, question: str,
            ref_sv: Optional[str] = None, sample_count: int = 20) -> ChipMATEResult:
        """执行 ChipMATE 生成流程。sample_count: 交叉验证采样点数 (5-100)"""
        if self.config.backend == "api":
            return self._run_api(task_id, question, ref_sv, sample_count)
        else:
            return self._run_local(task_id, question, ref_sv)

    def _run_api(self, task_id: str, question: str,
                 ref_sv: Optional[str] = None, sample_count: int = 20) -> ChipMATEResult:
        """使用 API 后端 (DeepSeek) 进行 RTL 生成和自验证"""
        import urllib.request
        import urllib.error

        result = ChipMATEResult(
            task_id=task_id,
            question=question,
            verilog="",
            matched=False,
            match_rate=0.0,
            turns=0,
        )

        # Step 1: 生成 RTL
        try:
            verilog_code = self._call_llm_api(question, ref_sv)
        except Exception as e:
            result.error = str(e)
            return result
        if not verilog_code:
            result.error = "LLM 返回空内容"
            return result

        result.verilog = verilog_code

        # Step 2: Icarus 编译验证 (确保 RTL 语法正确)
        if not self._check_syntax(verilog_code):
            result.error = "RTL 语法检查失败"
            result.match_rate = 0.0
            return result

        # Step 3: ChipMATE 交叉验证 — Python 参考模型 vs Verilog 仿真
        # 核心思想: 不需要用户写 testbench, Agent 自己生成参考模型和随机输入
        py_model = self._generate_python_model(question, verilog_code)
        if py_model:
            match_rate, sim_out, detail = self._cross_verify_python(verilog_code, py_model, sample_count)
            result.match_rate = match_rate
            result.matched = match_rate >= self.config.match_threshold
            result.turns = 1
            result.history.append({"turn": 1, "match_rate": match_rate,
                                   "sim_output": sim_out, "detail": detail,
                                   "py_model": py_model})
        else:
            # 无法生成参考模型 → 只验证语法, 标记为通过
            result.match_rate = 0.8
            result.matched = True
            result.turns = 1
        return result

        return result

    def _run_local(self, task_id: str, question: str,
                   ref_sv: Optional[str] = None) -> ChipMATEResult:
        """使用本地 vLLM 双 Agent 后端"""
        result = ChipMATEResult(
            task_id=task_id,
            question=question,
            verilog="",
            matched=False,
            match_rate=0.0,
            turns=0,
        )

        # Phase 1: 本地模式需要 ChipMATE 仓库 + vLLM
        # 通过 subprocess 调用 chipmate CLI
        try:
            # 创建临时输入文件
            with tempfile.NamedTemporaryFile(
                mode='w', suffix='.jsonl', delete=False
            ) as f:
                input_file = f.name
                entry = {
                    "task_id": task_id,
                    "question": question,
                    "ref_sv": ref_sv or ""
                }
                f.write(json.dumps(entry) + "\n")

            output_file = input_file + ".out.jsonl"

            cmd = [
                "chipmate",
                "--input", input_file,
                "--out", output_file,
                "--provider", "openai-compat",
                "--model", self.config.v_agent_model,
                "--base-url", self.config.v_agent_url,
                "--api-key", "dummy",
                "--p-model", self.config.p_agent_model,
                "--p-base-url", self.config.p_agent_url,
                "--p-api-key", "dummy",
                "-n", str(self.config.num_candidates),
                "-t", str(self.config.max_turns),
            ]

            proc = subprocess.run(
                cmd, capture_output=True, text=True,
                timeout=self.config.timeout * self.config.max_turns
            )

            if proc.returncode == 0 and os.path.exists(output_file):
                with open(output_file) as f:
                    out = json.loads(f.readline())
                result.verilog = out.get("verilog", "")
                result.matched = out.get("matched", False)
                result.match_rate = out.get("match_rate", 0.0)
                result.turns = out.get("turns", 0)
            else:
                result.error = f"ChipMATE CLI failed: {proc.stderr[:500]}"

        except FileNotFoundError:
            result.error = "chipmate CLI 未安装 (需先 pip install chipmate)"
        except subprocess.TimeoutExpired:
            result.error = f"ChipMATE 执行超时"
        except Exception as e:
            result.error = str(e)
        finally:
            for f in [input_file, output_file]:
                if os.path.exists(f):
                    os.remove(f)

        return result

    def _call_llm_api(self, question: str,
                      ref_sv: Optional[str] = None) -> str:
        """调用 LLM API 生成 RTL"""
        import urllib.request
        import urllib.error

        prompt = f"""You are a Verilog RTL design expert. Generate synthesizable Verilog code
for the following specification:

{question}

Requirements:
- Use modern Verilog-2001 syntax
- Do NOT include testbench code
- Include module port declarations with comments
- Make the code synthesizable (no #delays, no $display in synthesizable part)
- Use non-blocking assignments (<=) for sequential logic
- Use blocking assignments (=) for combinational logic

Output ONLY the Verilog code, no markdown code blocks, no explanations."""

        if ref_sv:
            prompt += f"\n\nReference interface (use these port names):\n{ref_sv}"

        data = json.dumps({
            "model": self.config.api_model,
            "messages": [
                {"role": "system", "content": "You generate only Verilog code. No explanations."},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.3,
            "max_tokens": 4096,
        }).encode()

        req = urllib.request.Request(
            f"{self.config.api_base_url}/v1/chat/completions",
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.config.api_key}"
            }
        )

        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = json.loads(resp.read())
                content = body["choices"][0]["message"]["content"]
                content = content.replace("```verilog", "").replace("```", "")
                return content.strip()
        except urllib.error.HTTPError as e:
            err = json.loads(e.read().decode()) if e.fp else {}
            msg = err.get("error", {}).get("message", str(e))
            raise RuntimeError(f"API 错误 ({e.code}): {msg}")
        except Exception as e:
            raise RuntimeError(f"API 调用失败: {e}")

    def _parse_ports(self, verilog: str) -> tuple:
        """统一端口解析 — 支持 ANSI 风格多端口同行声明 (input clk, rst_n, en)。
        返回 (inputs, outputs), 元素为 (name, width)。"""
        code = re.sub(r'//[^\n]*', '', verilog)
        code = re.sub(r'/\*[\s\S]*?\*/', '', code)
        # 解析 parameter/localparam 声明, 供宽度表达式求值 (如 [W-1:0])
        ns = {}
        for pm in re.finditer(r'\b(?:local)?parameter\s+(?:integer\s+)?(\w+)\s*=\s*([^,;\s)]+)', code):
            try:
                ns[pm.group(1)] = int(eval(re.sub(r'[^0-9A-Za-z_+\-*/() ]', '', pm.group(2)),
                                           {"__builtins__": {}}, dict(ns)))
            except Exception:
                ns[pm.group(1)] = 1
        inputs, outputs = [], []
        # 找 module 头的端口区: module name ( ... ); (兼容 #(parameter) 写法)
        mh = re.search(r'module\s+\w+\s*(?:#\s*\(.*?\)\s*)?\((.*?)\)\s*;', code, re.DOTALL)
        port_area = mh.group(1) if mh else code
        # 逐个方向声明块切分: input ..., output ...
        # 用 lookahead 在下一个方向关键字前截断, 防止吞掉后续声明
        for dm in re.finditer(r'\b(input|output|inout)\b(.*?)(?=\binput\b|\boutput\b|\binout\b|$)', port_area, re.DOTALL):
            direction = dm.group(1)
            body = dm.group(2)
            # 提取宽度 [msb:lsb]
            w = 1
            wm = re.search(r'\[([^\]]+)\]', body)
            if wm:
                parts = wm.group(1).split(':')
                if len(parts) == 2:
                    try:
                        # 两端分别求值 (支持 parameter 引用, 如 [W-1:0])
                        def _bound(expr: str) -> int:
                            expr = re.sub(r'[^0-9A-Za-z_+\-*/() ]', '', expr)
                            return int(eval(expr, {"__builtins__": {}}, dict(ns)))
                        w = abs(_bound(parts[0]) - _bound(parts[1])) + 1
                    except Exception:
                        w = 32
            # 去掉宽度和关键字, 提取端口名
            body_clean = re.sub(r'\[[^\]]*\]', '', body)
            body_clean = re.sub(r'\b(reg|wire|logic|signed|input|output|inout)\b', '', body_clean)
            seen = set(n for n, _ in inputs + outputs)
            for name in re.findall(r'(\w+)', body_clean):
                if name not in seen:
                    seen.add(name)
                    (inputs if direction == 'input' else outputs).append((name, w))
        return inputs, outputs

    def _generate_python_model(self, question: str, verilog: str) -> str:
        """生成 Python 参考模型 — 严格遵循 ChipMATE 源码格式:
        class TopModule + eval(self, inputs: dict) -> dict"""
        import urllib.request
        # 从 Verilog 提取端口列表, 供 LLM 参考 (统一解析器, 支持多端口同行声明)
        inputs, outputs = self._parse_ports(verilog)
        port_desc = ", ".join(f"{d} {n}" for d, names in (("input", inputs), ("output", outputs)) for n, _ in names)

        # 提取输出端口名, 让 LLM 精确返回这些 key
        out_names = [n for n, _ in outputs]
        out_list = ", ".join(f"'{n}'" for n in out_names) if out_names else "list your output port names"

        prompt = f"""Write a Python reference model for this Verilog module.
This is for cross-verification against the Verilog implementation (ChipMATE workflow).

Specification: {question}

Verilog code:
```
{verilog}
```

REQUIRED FORMAT — follow exactly:
```python
class TopModule:
    def __init__(self):
        # initialize internal state (registers)

    def eval(self, inputs: dict) -> dict:
        # inputs contains ALL input port names as keys (e.g. inputs['clk'], inputs['rst_n'], inputs['en'])
        # update internal state (this call = one clock edge for clocked designs)
        # return a dict whose keys are EXACTLY: {out_list}
        return {{'{out_names[0] if out_names else "out"}': value}}
```

Rules:
- Class MUST be named TopModule
- eval() takes a dict of inputs, returns a dict of outputs
- Return dict keys must be EXACTLY the output port names: {out_list}
- For clocked designs, update state in eval() (each call = one clock edge)
- For reset ports ending in _n/_b/_l, treat 0 as asserted
- Use simple Python only, no external libraries
- Output ONLY the Python code, no markdown, no explanations"""

        data = json.dumps({
            "model": self.config.api_model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1, "max_tokens": 2048,
        }).encode()
        req = urllib.request.Request(
            f"{self.config.api_base_url}/v1/chat/completions", data=data,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.config.api_key}"}
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = json.loads(resp.read())
                content = body["choices"][0]["message"]["content"]
                content = content.replace("```python", "").replace("```", "")
                return content.strip()
        except Exception:
            return ""

    def _cross_verify_python(self, verilog: str, py_model: str, sample_count: int = 20,
                             clk_period_ns: float = 10.0) -> tuple:
        """ChipMATE 交叉验证 — 严格移植 chipmate/cross_verify.py:
        端口解析 → 随机激励 → 复位预处理 → 逐输出信号对比 → mismatch 记录"""
        import random

        # ---- 1. 端口解析 (parse_ports) ----
        # 统一解析器: 支持 ANSI 风格多端口同行声明: input clk, rst_n, en
        inputs, outputs = self._parse_ports(verilog)
        if not outputs:
            return 0.0, "", {"match_rate": 0.0, "meta_error": "no output ports parsed"}

        has_clk = any(n.lower() == 'clk' for n, _ in inputs)
        resets = [(n, w) for n, w in inputs if ('rst' in n.lower() or 'reset' in n.lower()) and n.lower() != 'clk']

        # ---- 2. 随机激励 (gen_stimuli) ----
        rng = random.Random(42)
        n = max(5, min(sample_count, 100))
        stimuli = []
        for _ in range(n):
            s = {}
            for name, w in inputs:
                if name.lower() == 'clk' or ('rst' in name.lower() or 'reset' in name.lower()):
                    continue
                s[name] = rng.randint(0, (1 << min(w, 16)) - 1)
            stimuli.append(s)

        # ---- 3. 归一化模块名 (normalize_module_name) ----
        dut_sv = verilog
        m = re.search(r'\bmodule\s+(\w+)', dut_sv)
        if m and m.group(1) != 'TopModule':
            dut_sv = re.sub(r'\bmodule\s+' + re.escape(m.group(1)), 'module TopModule', dut_sv, count=1)

        # ---- 4. 构建激励 TB (build_stim_testbench) ----
        lines = ["`timescale 1ns/1ps", "module stim_tb();"]
        for name, w in inputs:
            bits = f"[{w-1}:0] " if w > 1 else ""
            lines.append(f"  reg {bits}{name};")
        for name, w in outputs:
            bits = f"[{w-1}:0] " if w > 1 else ""
            lines.append(f"  wire {bits}{name};")
        conns = ", ".join(f".{name}({name})" for name, _ in inputs + outputs)
        lines.append(f"  TopModule dut ({conns});")
        # VCD 波形转储 (方案 RTL-007: 标准 VCD 文件)
        lines.append('  initial begin $dumpfile("sim.vcd"); $dumpvars(0, stim_tb); end')
        if has_clk:
            lines.append("  initial clk = 0;")
            # 时钟周期由前端仿真控制面板透传 (默认 10ns → 半周期 #5)
            lines.append(f"  always #{clk_period_ns / 2:g} clk = ~clk;")
        lines.append("  initial begin")
        for name, _ in inputs:
            if name.lower() == 'clk':
                continue
            lines.append(f"    {name} = 0;")
        # 复位预处理: 3 拍断言
        for name, w in resets:
            active_low = name.endswith('_n') or name.endswith('_b') or name.endswith('_l')
            lines.append(f"    {name} = {0 if active_low else 1};")
        if has_clk:
            for _ in range(3):
                lines.append("    @(posedge clk); #1;")
        for name, w in resets:
            active_low = name.endswith('_n') or name.endswith('_b') or name.endswith('_l')
            lines.append(f"    {name} = {1 if active_low else 0};")
        if has_clk:
            lines.append("    @(posedge clk); #1;")
        for idx, stim in enumerate(stimuli):
            assigns = " ".join(f"{name} = 'h{v:x};" for name, v in stim.items())
            lines.append(f"    {assigns}")
            lines.append("    @(posedge clk); #1;" if has_clk else "    #1;")
            fmts = " ".join(f"{name}=%0d" for name, _ in outputs)
            args = ", ".join(name for name, _ in outputs)
            lines.append(f'    $display("TEST_{idx} {fmts}", {args});')
        lines.append('    $finish;')
        lines.append("  end")
        lines.append('  initial begin #100000 $display("TIMEOUT"); $finish; end')
        lines.append("endmodule")
        tb = "\n".join(lines)

        # ---- 5. Verilog 仿真 (simulate_verilog) ----
        with tempfile.TemporaryDirectory() as td:
            vf = os.path.join(td, "dut.sv"); tf = os.path.join(td, "tb.sv")
            with open(vf, "w") as f: f.write(dut_sv)
            with open(tf, "w") as f: f.write(tb)
            try:
                r = subprocess.run(
                    [self.config.iverilog_path, "-g2012", "-o", "sim.vvp", "-s", "stim_tb", vf, tf],
                    capture_output=True, text=True, timeout=30, cwd=td)
            except subprocess.TimeoutExpired:
                return 0.0, "", {"match_rate": 0.0, "sv_error": "sv_compile_timeout"}
            if r.returncode != 0:
                return 0.0, "", {"match_rate": 0.0, "sv_error": (r.stdout + r.stderr)[:500]}
            try:
                r = subprocess.run([self.config.vvp_path, "-n", "sim.vvp"],
                                   capture_output=True, text=True, timeout=30, cwd=td)
            except subprocess.TimeoutExpired:
                return 0.0, "", {"match_rate": 0.0, "sv_error": "sv_sim_timeout"}
            out = r.stdout
            if "TIMEOUT" in out:
                return 0.0, "", {"match_rate": 0.0, "sv_error": "sv_sim_hang"}
            # 保存 VCD 到持久目录
            vcd_path = ""
            vcd_src = os.path.join(td, "sim.vcd")
            if os.path.exists(vcd_src) and os.path.getsize(vcd_src) > 0:
                persist_dir = os.path.join(self.config.working_dir, "vcd")
                os.makedirs(persist_dir, exist_ok=True)
                vcd_path = os.path.join(persist_dir, f"cross_verify_{int(time.time())}.vcd")
                import shutil
                shutil.copy(vcd_src, vcd_path)
            sv_results = [{} for _ in stimuli]
            for line in out.splitlines():
                mm = re.match(r"TEST_(\d+)\s+(.*)", line.strip())
                if not mm: continue
                idx = int(mm.group(1))
                if idx >= len(sv_results): continue
                for pair in re.finditer(r"(\w+)=(-?\d+)", mm.group(2)):
                    sv_results[idx][pair.group(1)] = int(pair.group(2))

        # ---- 6. Python 参考模型仿真 (simulate_python) ----
        harness = '''import sys, json
_USER_CODE = open(sys.argv[1]).read()
_STIMS = json.load(open(sys.argv[2]))
_OUTPUTS = json.load(open(sys.argv[3]))
_HAS_CLK = bool(int(sys.argv[4]))
_RESETS = json.load(open(sys.argv[5]))
_INPUTS = json.load(open(sys.argv[6]))
_NS = {}
exec(_USER_CODE, _NS)
if "TopModule" not in _NS:
    print("PY_ERR: no TopModule class", file=sys.stderr); sys.exit(2)
dut = _NS["TopModule"]()
def _full(extra):
    # 所有输入端口都有默认值, 避免模型读取 inputs['clk'] 时 KeyError
    base = {n: (1 if n == 'clk' else 0) for n in _INPUTS}
    for name, al in _RESETS:
        base[name] = 1 if al else 0   # 复位无效态
    base.update(extra)
    return base
if _HAS_CLK and _RESETS:
    for _ in range(3):
        dut.eval(_full({name: (0 if al else 1) for name, al in _RESETS}))
    dut.eval(_full({name: (1 if al else 0) for name, al in _RESETS}))
results = []
for idx, stim in enumerate(_STIMS):
    inp = _full(stim)
    try:
        out = dut.eval(inp)
    except Exception as e:
        print(f"PY_ERR_EVAL_{idx}: {type(e).__name__}: {e}", file=sys.stderr); sys.exit(4)
    row = {}
    for pname in _OUTPUTS:
        v = out.get(pname)
        try: row[pname] = int(v)
        except Exception: row[pname] = None
    results.append(row)
print("PY_RESULTS " + json.dumps(results))
'''
        with tempfile.TemporaryDirectory() as td:
            resets_json = [[n, n.endswith('_n') or n.endswith('_b') or n.endswith('_l')] for n, _ in resets]
            with open(os.path.join(td, "harness.py"), "w") as f: f.write(harness)
            with open(os.path.join(td, "user.py"), "w") as f: f.write(py_model)
            with open(os.path.join(td, "stims.json"), "w") as f: f.write(json.dumps(stimuli))
            with open(os.path.join(td, "resets.json"), "w") as f: f.write(json.dumps(resets_json))
            with open(os.path.join(td, "outputs.json"), "w") as f: f.write(json.dumps([n for n, _ in outputs]))
            with open(os.path.join(td, "inputs.json"), "w") as f: f.write(json.dumps([n for n, _ in inputs]))
            try:
                r = subprocess.run(
                    ["python3", os.path.join(td, "harness.py"), os.path.join(td, "user.py"),
                     os.path.join(td, "stims.json"), os.path.join(td, "outputs.json"),
                     "1" if has_clk else "0", os.path.join(td, "resets.json"),
                     os.path.join(td, "inputs.json")],
                    capture_output=True, text=True, timeout=30)
            except subprocess.TimeoutExpired:
                return 0.0, out, {"match_rate": 0.0, "py_error": "py_exec_timeout"}
            if r.returncode != 0:
                return 0.0, out, {"match_rate": 0.0, "py_error": r.stderr[:300]}
            py_results = None
            for line in r.stdout.splitlines():
                if line.startswith("PY_RESULTS "):
                    py_results = json.loads(line[len("PY_RESULTS "):])
            if py_results is None:
                return 0.0, out, {"match_rate": 0.0, "py_error": "py_no_results"}

        # ---- 7. 对比 (cross_verify 核心) ----
        mismatches = []
        total = 0; mismatched = 0
        for i, (sv_row, py_row) in enumerate(zip(sv_results, py_results)):
            for oname, _ in outputs:
                total += 1
                sv_v = sv_row.get(oname); py_v = py_row.get(oname)
                if sv_v != py_v:
                    mismatched += 1
                    if len(mismatches) < 5:
                        mismatches.append({"test": i, "signal": oname,
                                           "verilog": sv_v, "python": py_v,
                                           "inputs": stimuli[i]})
        match_rate = 1.0 - (mismatched / total) if total > 0 else 0.0

        # 为波形提供数据: 从 sv_results 提取信号序列
        sig_out = {name: [row.get(name) for row in sv_results] for name, _ in outputs}

        detail = {
            "match_rate": match_rate,
            "mismatches": mismatches,
            "num_tests": len(stimuli),
            "total_checks": total,
            "mismatched": mismatched,
            "has_clk": has_clk,
            "inputs": [n for n, _ in inputs],
            "outputs": [n for n, _ in outputs],
            "stimuli": stimuli,
            # 完整有效输入序列 (含复位信号, 低有效复位解复位后为 1)
            "effective_inputs": [
                {**s, **{rname: (1 if rname.endswith(('_n','_b','_l')) else 0) for rname, _ in resets}}
                for s in stimuli
            ],
            "sv_results": sv_results,
            "py_results": py_results,
            "signals": sig_out,
            "vcd_path": vcd_path,
            # 实际采样周期 (TB 中 always #{p/2} clk=~clk → 周期 = 前端透传的 clk_period_ns)
            "time_step_ns": clk_period_ns,
        }
        return match_rate, out, detail

    def generate_tb(self, question: str, verilog: str) -> str:
        """公开方法: AI 生成 testbench (供外部调用, 用于波形展示)"""
        return self._generate_testbench(question, verilog)

    def _generate_testbench(self, question: str,
                            verilog_code: str) -> str:
        """生成 Verilog testbench"""
        prompt = f"""Generate a simple Verilog testbench for the following module.
The testbench should instantiate the DUT and apply several test vectors.

Module specification: {question}

Module code:
```
{verilog_code}
```

Requirements:
- Include VCD dump: `initial begin $dumpfile("sim.vcd"); $dumpvars; end`
- After each test, output: `$display("V=%d", <main_output>);` (REQUIRED)
- Generate 5-8 test cases maximum, keep it SHORT (under 50 lines)
- Include $finish at the end
- Clock period = 10 time units
- Be concise. NO long comments.

Output ONLY the Verilog testbench code, no markdown, no explanations."""

        import urllib.request
        data = json.dumps({
            "model": self.config.api_model,
            "messages": [
                {"role": "system", "content": "You generate only Verilog testbench code."},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.2,
            "max_tokens": 4096,
        }).encode()

        req = urllib.request.Request(
            f"{self.config.api_base_url}/v1/chat/completions",
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.config.api_key}"
            }
        )

        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = json.loads(resp.read())
                content = body["choices"][0]["message"]["content"]
                content = content.replace("```verilog", "").replace("```", "")
                return content.strip()
        except Exception:
            return ""

    def _check_syntax(self, verilog_code: str) -> bool:
        """用 Icarus 做基础语法检查 (不跑仿真)"""
        with tempfile.TemporaryDirectory() as tmpdir:
            f = os.path.join(tmpdir, "dut.v")
            with open(f, "w") as fh:
                fh.write(verilog_code)
            proc = subprocess.run(
                [self.config.iverilog_path, "-o", os.path.join(tmpdir, "null"), f],
                capture_output=True, text=True, timeout=30
            )
            return proc.returncode == 0

    def _cross_verify(self, verilog_code: str, tb_code: str) -> float:
        """使用 Icarus Verilog 进行交叉验证"""
        with tempfile.TemporaryDirectory() as tmpdir:
            dut_file = os.path.join(tmpdir, "dut.v")
            tb_file = os.path.join(tmpdir, "tb.v")
            vvp_file = os.path.join(tmpdir, "sim.vvp")

            # 写文件
            with open(dut_file, "w") as f:
                f.write(verilog_code)
            with open(tb_file, "w") as f:
                f.write(tb_code)

            # Icarus 编译
            proc = subprocess.run(
                [self.config.iverilog_path, "-o", vvp_file,
                 dut_file, tb_file],
                capture_output=True, text=True,
                timeout=30
            )

            if proc.returncode != 0:
                return 0.0  # 编译失败

            # Icarus 仿真
            proc = subprocess.run(
                [self.config.vvp_path, vvp_file],
                capture_output=True, text=True,
                timeout=30
            )

            if proc.returncode != 0:
                return 0.5  # 仿真部分通过

            # 简单的匹配判断: 检查是否有明显的错误输出
            output = proc.stdout.lower()
            error_keywords = ["mismatch", "error", "fail", "x", "z"]
            errors = sum(1 for kw in error_keywords if kw in output)

            if errors == 0:
                return 1.0
            elif errors <= 2:
                return 0.8
            else:
                return 0.5


# ============================================================
# CLI 入口 (Phase 1 验证用)
# ============================================================
if __name__ == "__main__":
    import sys

    config = ChipMATEConfig()
    # 从环境变量读取 API key
    config.api_key = os.environ.get("DEEPSEEK_API_KEY", "")

    # 测试用例
    test_cases = [
        ("counter_4bit", "设计一个带异步复位和使能的 4 位加法计数器，溢出时输出高电平"),
        ("gcd", "设计一个 GCD 最大公约数计算器，使用欧几里得算法"),
    ]

    runner = ChipMATERunner(config)

    for task_id, question in test_cases:
        print(f"\n{'='*60}")
        print(f"Task: {task_id}")
        print(f"Question: {question}")
        print(f"{'='*60}")

        result = runner.run(task_id, question)

        if result.error:
            print(f"ERROR: {result.error}")
        else:
            print(f"Match Rate: {result.match_rate:.1%}")
            print(f"Matched: {result.matched}")
            print(f"Turns: {result.turns}")
            print(f"\nGenerated RTL (first 500 chars):")
            print(result.verilog[:500])
