"""
SVA 模板库 — 分析 RTL 结构 → 匹配模板 → 填充信号名 → 迭代生成
"""
import re
from dataclasses import dataclass, field

@dataclass
class RTLStructure:
    """从 Verilog 代码中提取的结构信息"""
    module_name: str = ""
    clocks: list[str] = field(default_factory=list)       # 时钟信号
    resets: list[str] = field(default_factory=list)       # 复位信号 (低有效)
    outputs: list[str] = field(default_factory=list)      # 输出端口
    registers: list[str] = field(default_factory=list)    # 寄存器信号 (always @(posedge ...) 赋值的)
    enables: list[str] = field(default_factory=list)      # 使能信号 (if (en) 里的 en)
    counters: list[dict] = field(default_factory=list)    # 计数器 [{name, width, inc_expr}]
    fsm_states: list[str] = field(default_factory=list)   # FSM 状态 (parameter/localparam 定义的)
    handshake: dict = field(default_factory=dict)         # {valid, ready} 握手信号
    fifo_like: bool = False                               # FIFO 特征
    has_default_case: bool = False                        # case 语句有 default 吗
    arith_ops: list[str] = field(default_factory=list)    # 算术运算: + - *
    reset_vals: dict = field(default_factory=dict)        # 寄存器复位值 {reg: "4'b1111"}


def analyze_rtl(code: str) -> RTLStructure:
    """分析 RTL 代码，提取结构信息"""
    s = RTLStructure()

    # 模块名
    m = re.search(r'module\s+(\w+)', code)
    if m: s.module_name = m.group(1)

    # 时钟
    s.clocks = re.findall(r'always\s*@\s*\(\s*posedge\s+(\w+)', code)

    # 复位 (低有效: if(!rst_n) 或 if(~rst_n))
    s.resets = re.findall(r'if\s*\(\s*[!~]\s*(\w+)\s*\)', code)

    # 输出端口
    s.outputs = re.findall(r'output\s+(?:reg\s+)?(?:\[\d+:\d+\]\s+)?(\w+)', code)

    # 寄存器 (在 always @(posedge ...) 里用 <= 赋值的)
    for line in code.split('\n'):
        if 'always' in line and 'posedge' in line:
            # 找这个 always 块里的非阻塞赋值
            pass
    reg_assigns = re.findall(r'(\w+)\s*<=\s*', code)
    s.registers = list(set(reg_assigns))[:10]

    # 使能信号
    enables = re.findall(r'if\s*\(\s*(\w+)\s*\)', code)
    # 过滤掉复位信号
    s.enables = [e for e in enables if e not in s.resets and e not in s.clocks][:5]

    # 计数器: q <= q + 1 / q <= q + 4'd1 / q <= q + 4'b1 (递增)
    # 以及递减: q <= q - 4'b0001 (减计数器)
    lit_re = r'(\w+)\s*<=\s*(\w+)\s*[+\-]\s*(\d+)(?:\'([bdh])([0-9a-fA-F]+))?'
    for cnt in re.findall(lit_re, code):
        if cnt[0] != cnt[1]:
            continue
        if cnt[3]:
            try:
                inc = str(int(cnt[4], {'b': 2, 'd': 10, 'h': 16}[cnt[3].lower()]))
            except (ValueError, KeyError):
                inc = cnt[2]
        else:
            inc = cnt[2]
        # 方向: 计数器自赋值行 (count <= count ± N) 出现 '-' → 递减
        line = next((l for l in code.split('\n')
                     if f'{cnt[0]} <=' in l and f'<= {cnt[0]}' in l), '')
        dec = ('-' in line)
        # 检测位宽
        width = 4  # 默认
        wm = re.search(rf'(?:reg|wire|output)\s*\[(\d+):(\d+)\]\s*{cnt[0]}', code)
        if wm:
            width = max(int(wm.group(1)), int(wm.group(2))) + 1
        else:
            wm = re.search(rf'{cnt[0]}.*?\[(\d+):(\d+)\]', code)
            if wm: width = max(int(wm.group(1)), int(wm.group(2))) + 1
        s.counters.append({"name": cnt[0], "inc_expr": inc, "width": width, "dec": dec})

    # 计数器使能: 计数器自赋值行向前找最近的 if 条件 (else if (en) 与赋值可能分行)
    # 避免把同块的其它控制信号 (如 load) 误当使能
    lines = code.split('\n')
    for c in s.counters:
        for i, line in enumerate(lines):
            if f'{c["name"]} <=' in line and f'<= {c["name"]}' in line:
                for back in range(i - 1, max(i - 5, -1), -1):
                    m = re.search(r'(?:else\s+)?if\s*\(\s*(\w+)\s*\)', lines[back])
                    if m:
                        c["en"] = m.group(1)
                        break
                break

    # 复位分支赋值: if (!rst_n) ... reg <= VALUE (支持复位到非零值, 如 4'b1111)
    reset_vals: dict = {}
    in_reset = False
    for line in code.split('\n'):
        stripped = line.strip()
        if re.search(r'if\s*\(\s*[!~]\s*\w+\s*\)', stripped):
            in_reset = True
            continue
        if in_reset and re.search(r'\belse\b', stripped):
            in_reset = False
        m = re.search(r'(\w+)\s*<=\s*([^;]+);', stripped)
        if m and in_reset:
            reset_vals[m.group(1)] = m.group(2).strip()
    s.reset_vals = reset_vals

    # FSM 状态 (parameter/localparam)
    state_params = re.findall(r'(?:parameter|localparam)\s+(\w+)\s*=\s*(\d+)', code)
    state_names = [name for name, val in state_params if name.isupper() or 'STATE' in name.upper() or 'IDLE' in name.upper() or 'S_' in name]
    s.fsm_states = state_names[:10]

    # 握手信号
    valid_sigs = [p for p in s.outputs + s.registers if 'valid' in p.lower() or 'vld' in p.lower()]
    ready_sigs = [p for p in code.split() if 'ready' in p.lower() or 'rdy' in p.lower()]
    if valid_sigs and ready_sigs:
        s.handshake = {"valid": list(set(valid_sigs)), "ready": list(set(ready_sigs))}

    # FIFO 特征
    fifo_keywords = ['fifo', 'empty', 'full', 'wr_en', 'rd_en', 'wr_ptr', 'rd_ptr', 'wptr', 'rptr']
    s.fifo_like = sum(1 for kw in fifo_keywords if kw in code.lower()) >= 3

    # default case
    s.has_default_case = 'default' in code and 'case' in code

    # 算术运算
    if '+' in code: s.arith_ops.append('add')
    if '-' in code: s.arith_ops.append('sub')
    if '*' in code: s.arith_ops.append('mul')

    return s


# ============================================================
# 模板库
# ============================================================
TEMPLATES = [
    {
        "id": "counter_bound",
        "name": "计数器范围检查",
        "condition": lambda s: len(s.counters) > 0,
        "priority": 1,
        "generate": lambda s: [
            f"// 计数器 {c['name']} 不溢出 (位宽={c.get('width',4)})\n"
            f"always @(posedge {s.clocks[0]}) assert ({c['name']} <= {c.get('width',4)}'d{(1<<c.get('width',4))-1});"
            for c in s.counters[:1]
        ],
    },
    {
        "id": "reset_check",
        "name": "复位行为检查",
        "condition": lambda s: len(s.resets) > 0 and len(s.registers) > 0,
        "priority": 1,
        "generate": lambda s: [
            # $initstate: 排除初始态 (初始态 anyinit 任意, 复位未生效)
            # 用 $past(rst_n) 判定"上一拍复位生效": 同步复位设计中 rst_n=0 的
            # 当前拍寄存器还是旧值 (下一拍才复位), 用当前拍会误报
            # 复位值从 RTL 提取 (支持复位到非零值, 如 4'b1111); 提取不到则按 0
            f"// 复位生效后 {reg} 保持复位值 ({s.reset_vals.get(reg, '0')})\n"
            f"always @(posedge {s.clocks[0]}) if (!$initstate && !$past({s.resets[0]})) assert ({reg} == {s.reset_vals.get(reg, '0')});"
            for reg in s.registers[:1]
        ],
    },
    {
        "id": "enable_check",
        "name": "使能行为检查",
        "condition": lambda s: len(s.enables) > 0 and len(s.counters) > 0,
        "priority": 2,
        "generate": lambda s: [
            # 转换关系式 (单 property 覆盖保持/递增|递减/回绕), 经 SBY BMC 实测通过:
            # - 全部用 $past 采样上一拍 (q 的更新使用上一拍的 en, 不能混用当前拍)
            # - $initstate 排除初始态 ($past 初值 anyinit 任意)
            # - rst_n && $past(rst_n) 排除复位沿 (异步复位建模为同步)
            # - 本 yosys build 的 formal frontend 只支持 always 块内 immediate
            #   assertion, 不支持 assert property/|->/disable iff
            # - 递增回绕: 满值 +1 → 0; 递减回绕: 0 -1 → 满值
            (
                f"// {c['name']} 转换关系: 使能时递减(允许回绕), 关闭时保持\n"
                f"always @(posedge {s.clocks[0]}) if (!$initstate && {s.resets[0]} && $past({s.resets[0]})) "
                f"assert ($past({c.get('en') or s.enables[0]}) ? "
                f"({c['name']} == $past({c['name']}) - {c['inc_expr']} || "
                f"({c['name']} == {c.get('width',4)}'d{(1<<c.get('width',4))-1} && $past({c['name']}) == 0)) : "
                f"{c['name']} == $past({c['name']}));"
                if c.get('dec') else
                f"// {c['name']} 转换关系: 使能时递增(允许回绕), 关闭时保持\n"
                f"always @(posedge {s.clocks[0]}) if (!$initstate && {s.resets[0]} && $past({s.resets[0]})) "
                f"assert ($past({c.get('en') or s.enables[0]}) ? "
                f"({c['name']} == $past({c['name']}) + {c['inc_expr']} || "
                f"({c['name']} == 0 && $past({c['name']}) == {c.get('width',4)}'d{(1<<c.get('width',4))-1})) : "
                f"{c['name']} == $past({c['name']}));"
            )
            for c in s.counters[:1] if c.get('inc_expr') == '1'
        ] + [
            # 递增/递减步长非 1 时: 只检查使能关闭时保持
            f"// 使能关闭时 {c['name']} 保持不变 (首拍保护)\n"
            f"always @(posedge {s.clocks[0]}) if (!$initstate && {s.resets[0]} && $past({s.resets[0]}) && "
            f"!$past({c.get('en') or s.enables[0]})) assert ({c['name']} == $past({c['name']}));"
            for c in s.counters[:1] if c.get('inc_expr') != '1'
        ],
    },
    {
        "id": "onehot_check",
        "name": "独热码检查",
        "condition": lambda s: len(s.fsm_states) >= 3,
        "priority": 3,
        "generate": lambda s: [
            f"// FSM 状态合法 (独热码或已知状态)\n"
            f"always @(posedge {s.clocks[0]}) assert (state inside {{{', '.join(s.fsm_states[:8])}}});"
        ] if s.fsm_states else [],
    },
    {
        "id": "handshake_check",
        "name": "握手协议检查",
        "condition": lambda s: bool(s.handshake),
        "priority": 2,
        "generate": lambda s: [
            # 本 yosys build 不支持 ##1/$stable → 用 $past 表达 "valid 拉起后一拍内 ready 响应"
            f"// valid 拉起后 ready 必须响应 (首拍保护)\n"
            f"always @(posedge {s.clocks[0]}) if ($past({s.handshake['valid'][0]})) "
            f"assert ({s.handshake['ready'][0]} == 1);"
        ] if s.handshake.get('valid') and s.handshake.get('ready') else [],
    },
    {
        "id": "fifo_overflow",
        "name": "FIFO 溢出/下溢检查",
        "condition": lambda s: s.fifo_like,
        "priority": 3,
        "generate": lambda s: [
            f"// FIFO 不会同时读写满的 FIFO\n"
            f"always @(posedge {s.clocks[0]}) assert (!(wr_en && full));\n"
            f"always @(posedge {s.clocks[0]}) assert (!(rd_en && empty));"
        ],
    },
    {
        "id": "default_case",
        "name": "Case 默认分支检查",
        "condition": lambda s: not s.has_default_case,
        "priority": 4,
        "generate": lambda s: [
            f"// 建议: case 语句添加 default 分支以避免 latch"
        ],
    },
    {
        "id": "range_check",
        "name": "输出范围检查",
        "condition": lambda s: len(s.outputs) > 0,  # 总有一个
        "priority": 5,
        "generate": lambda s: [
            f"// 输出 {o} 非 X/Z\n"
            f"always @(posedge {s.clocks[0]}) assert (!$isunknown({o}));"
            for o in s.outputs[:2] if o not in s.clocks
        ],
    },
]

# ============================================================
# 生成 SVA — 按优先级逐步生成
# ============================================================
def generate_sva_iterative(code: str, round_num: int = 0) -> dict:
    """
    迭代式生成 SVA。
    round_num: 0=第一批(高优先级), 1=第二批, 2=自由发挥
    """
    s = analyze_rtl(code)

    if round_num == 0:
        # 第一批: 高优先级模板 (每类最多1条)
        candidates = [t for t in TEMPLATES if t["condition"](s) and t["priority"] <= 2]
        candidates.sort(key=lambda t: t["priority"])
    elif round_num == 1:
        # 第二批: 中优先级
        candidates = [t for t in TEMPLATES if t["condition"](s) and 3 <= t["priority"] <= 3]
    else:
        # 自由发挥: 中低优先级
        candidates = [t for t in TEMPLATES if t["condition"](s) and t["priority"] >= 3]

    if not candidates:
        return {"sva": "", "analysis": _describe_structure(s), "templates_used": []}

    # 每个模板生成1条
    svas = []
    used = []
    for t in candidates[:3]:  # 最多3个模板
        items = t["generate"](s)
        if items:
            svas.extend(items[:1])  # 每个模板只取1条
            used.append(t["name"])

    sva_text = "\n".join(svas)

    return {
        "sva": sva_text,
        "analysis": _describe_structure(s),
        "templates_used": used,
        "total_templates_available": len([t for t in TEMPLATES if t["condition"](s)]),
        "round": round_num + 1,
        "next_round_available": round_num < 3 and len(used) > 0,
    }


def _describe_structure(s: RTLStructure) -> str:
    """用自然语言描述 RTL 结构"""
    parts = []
    if s.clocks: parts.append(f"{len(s.clocks)} 个时钟 ({', '.join(s.clocks)})")
    if s.resets: parts.append(f"复位信号: {', '.join(s.resets)}")
    if s.counters: parts.append(f"{len(s.counters)} 个计数器: {', '.join(c['name'] for c in s.counters)}")
    if s.fsm_states: parts.append(f"FSM ({len(s.fsm_states)} 个状态)")
    if s.handshake: parts.append("握手协议")
    if s.fifo_like: parts.append("FIFO 结构")
    if s.enables: parts.append(f"使能信号: {', '.join(s.enables)}")
    return "检测到: " + "; ".join(parts) if parts else "简单组合/时序逻辑"
