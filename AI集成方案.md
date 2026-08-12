# IC-Agent-OS AI 集成方案

> 需求 1：自然语言交互替代逐步向导
> 需求 2：AI 辅助生成可自由组配的 Flow 架构
> 状态：方案设计阶段

---

## 一、总体架构

```
┌─────────────────────────────────────────────────┐
│                    用户                         │
│          "跑一个200MHz的AES, 要能流片"            │
└────────────────────┬────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────┐
│              AI Client (adapter/ai_client.py)    │
│                                                  │
│  parse_intent()           score_tools()          │
│   NL→结构化参数            设计上下文→工具评分     │
│                                                  │
│  失败降级: 返回 None → 走现有规则引擎              │
└────────┬───────────────────┬────────────────────┘
         │                   │
         ▼                   ▼
┌─────────────────┐  ┌──────────────────────────┐
│    cli.py        │  │  FlowComposer._score_tool │
│  交互式向导       │  │  静态规则 + 历史 + AI加权  │
│  NL入口/7步向导   │  │                            │
└─────────────────┘  └──────────────────────────┘
         │                   │
         └─────────┬─────────┘
                   ▼
┌─────────────────────────────────────────────────┐
│             现有执行层 (不变)                     │
│  adapter.run() → Yosys/OpenROAD/OpenSTA          │
│  state.py → SQLite + JSON                       │
│  run_history → 历史反馈                          │
│  5轮流程 → 探索→全流程→修复→ECO→Sign-off         │
└─────────────────────────────────────────────────┘
```

**核心原则**：AI 是增强层，不是替代层。AI 不可用时所有功能降级到现有行为。

---

## 二、需求 1：自然语言交互

### 2.1 入口设计

`cli.py` 启动后第一步：

```
═════════════════════════════════
  IC-Agent-OS  AI 交互模式
═════════════════════════════════

  描述你的设计需求:
  > _

  示例: "用sky130跑一个100MHz的GCD, 开源工具, 要出GDS"
        "低功耗UART, 200MHz, 面积越小越好"
        "签核级AES加密芯片, 500MHz, 最大10万μm²"

  输入 q 进入逐步向导 / 输入 h 查看可用的设计/工艺列表
```

### 2.2 意图解析流程

```
用户输入: "跑一个200MHz的AES加密芯片，sky130工艺，要能流片"
                │
                ▼
┌──────────────────────────────────────┐
│  AI Prompt:                           │
│  ─────────                            │
│  你是芯片设计助手。从用户描述中提取     │
│  结构化参数。                          │
│                                       │
│  可用设计:                             │
│    gcd(640gates, 简单运算单元)         │
│    aes(7K gates, 加密核心)            │
│    uart(1.5K, 串口收发)               │
│    picorv32(10K, RISC-V CPU)          │
│                                       │
│  可用工艺: sky130(真实PDK), ASAP7(7nm) │
│                                       │
│  可用需求: 开源, 低功耗, 面积优化,      │
│           快速原型, 签核, AI训练        │
│                                       │
│  返回仅JSON(没有markdown代码块):        │
│  {"design":"aes","technology":"sky130", │
│   "frequency":200,"requirements":[...], │
│   "area_max":null,"power_max":null,     │
│   "fast_mode":false}                    │
└──────────────────┬───────────────────┘
                   │
                   ▼
┌──────────────────────────────────────┐
│  解析结果:                             │
│  {"design":"aes",                     │
│   "technology":"sky130",              │
│   "frequency":200,                    │
│   "requirements":["签核"]}             │
└──────────────────┬───────────────────┘
                   │
                   ▼
        直接跳转到步骤⑥(方案确认)
        展示 compose 生成的 Flow
        用户确认 → 执行
```

### 2.3 模糊语义识别

| 用户说的 | 解析为 | 逻辑 |
|---------|--------|------|
| "要能流片" / "tape-out" / "量产" | `requirements: ["签核"]` | 关键词匹配 |
| "随便跑跑" / "试一试" / "学习用的" | `requirements: ["新手"]` | 关键词匹配 |
| "面积小一点" / "越小越好" | `area_max: 自动估算` | 根据设计规模估算 |
| "功耗无所谓" / "速度优先" | `requirements: ["快速"]` | 关键词匹配 |
| "和上次一样" / "和gcd差不多" | 查 run_history.db 取上次配置 | 历史查询 |

### 2.4 降级策略

```
┌─ 启动 ─→ AI Client 初始化
│              │
│     ┌───────┴───────┐
│     │ API 可用?      │
│     ├─── Yes ───────┤
│     │  展示 NL 入口   │
│     │  用户输入描述   │
│     │      │         │
│     │  AI 解析成功?   │
│     │  ├─ Yes → 跳转确认步骤
│     │  └─ No  → 打印 "未能识别, 进入向导"
│     │              │
│     ├─── No ────────┤
│     │  打印 "AI 不可用, 进入逐步向导"    │
│     │  展示现有 7 步交互                 │
│     └──────────────┘
```

### 2.5 改动清单

| 文件 | 改动 | 行数 |
|------|------|------|
| `adapter/ai_client.py` | **新增** — AI 客户端 + `parse_intent()` | ~80 |
| `adapter/config.yaml` | 加 `ai:` 配置段 | ~6 |
| `cli.py` | `interactive()` 开头加 NL 入口 + 降级 | ~40 |

---

## 三、需求 2：AI 辅助 Flow 架构

### 3.1 调用位置

```
FlowComposer._score_tool()
  │
  ├── 1. 静态规则评分 (现有, 不变)
  │      quality×weight + speed×weight + open×weight + ...
  │
  ├── 2. 历史数据调整 (现有, 不变)
  │      score *= (0.5 + 0.5 × hist_success_rate)
  │
  └── 3. AI 辅助评分 (新增, 条件触发)
        仅以下情况调 AI:
        ├── 两个工具评分差距 < 15% (难以决策)
        ├── 所有候选工具评分 < 50 (都不理想)
        └── 频率 > 500MHz + 规则引擎选了开源工具
```

### 3.2 AI 评分流程

```
┌─────────────────────────────────────────┐
│  _score_tool() 内部                      │
│                                          │
│  static_score = 规则评分(Yosys) = 85     │
│  static_score = 规则评分(DC)    = 80     │
│  → 差距 5% < 15% → 触发 AI              │
│                                          │
│  context = {                             │
│      "design": "aes",                    │
│      "stage": "synthesis",               │
│      "frequency": 500,                   │
│      "technology": "sky130",             │
│      "candidates": [                     │
│          {"tool":"Yosys","score":85,      │
│           "source":"规则引擎"},            │
│          {"tool":"Design Compiler",       │
│           "score":80,"source":"规则引擎"}  │
│      ],                                  │
│      "history": {                        │
│          "Yosys_synthesis_success": 0.65, │
│          "DC_synthesis_success": 0.95     │
│      },                                  │
│      "design_knowledge": {               │
│          "critical_path_risk": "S-Box",   │
│          "gate_count": 7000               │
│      }                                   │
│  }                                       │
│          │                               │
│          ▼                               │
│  AI 建议:                                │
│  {"Yosys": 70, "Design Compiler": 95,    │
│   "reasoning": "AES S-Box 是组合逻辑     │
│    关键路径，Yosys abc mapper 在 500MHz  │
│    下 QoR 不稳定，历史 65% 成功率。建议   │
│    synthesis 用 DC，或用 Yosys 但降频     │
│    到 300MHz"}                            │
│          │                               │
│          ▼                               │
│  final_score = static_score × 0.7         │
│              + ai_score × 0.3             │
│  Yosys:  85×0.7 + 70×0.3 = 80.5         │
│  DC:     80×0.7 + 95×0.3 = 84.5  ← 胜出  │
└─────────────────────────────────────────┘
```

### 3.3 AI 能解答的开放性问题

| 场景 | 规则引擎限制 | AI 补充 |
|------|------------|--------|
| AES @500MHz, synth 工具选择 | Yosys 开源加分 > DC | "AES S-Box 关键路径在 Yosys abc 下 QoR 不稳定，建议 DC 或降频" |
| 低功耗 + HS 库 | 低功耗加分给 iPA | "HS 库 clock gating cell 不如 HD，纯功耗优先应切 HD" |
| 签核 DRC | 规则只知 Calibre 最高 | "商业 license 不可用时 Magic 可替代 DRC 但不等同于签核级验证" |
| picorv32 @500MHz | 大设计加完整流程 | "该设计在这个频率下历史 80% 失败，建议 300MHz 起步逐步提频" |
| 面积和频率冲突 | 并列处理 | "AES 200MHz 下面积通常 0.08mm²，若降频到 100MHz 可压缩到 0.05mm²" |

### 3.4 AI 明确不取代的部分

| 组件 | 原因 |
|------|------|
| 工具执行 (Yosys/OpenROAD/OpenSTA) | 确定性计算，AI 无法替代 EDA 工具 |
| 状态持久化 (state.db / run_history.db) | 结构化数据，AI 不应直接操作数据库 |
| 多轮流程控制 (5轮循环) | 确定性状态机，AI 作为建议者而非控制者 |
| Gate Check + 产物校验 | 确定性规则，不应交给概率模型 |
| GDS2 生成 | 物理格式转换，AI 无相关能力 |

### 3.5 改动清单

| 文件 | 改动 | 行数 |
|------|------|------|
| `adapter/ai_client.py` | 加 `score_tools()` + `design_advice()` | ~60 |
| `composer/flow_composer.py` | `_score_tool()` 加 AI 通路 + 触发条件 | ~30 |
| `adapter/config.yaml` | 加 `ai:` 配置段 (同上) | — |

---

## 四、AI Client 接口设计

### 4.1 新增文件

```
adapter/ai_client.py    (~150 行)
├── class AIClient
│   ├── __init__(config)         # 读取 api_url/model/timeout
│   ├── _call(prompt) → str|None # 底层 HTTP 调用
│   ├── parse_intent(text)→dict  # 需求 1: NL→结构化参数
│   ├── score_tools(ctx)→dict    # 需求 2: 上下文→工具评分建议
│   └── failure_advice(ctx)→str  # 扩展: 失败诊断中文建议
```

### 4.2 配置项

```yaml
# adapter/config.yaml
ai:
  enabled: false              # 默认关闭, 需手动开启
  api_url: ""                 # OpenAI/Claude 兼容 API 地址
  api_key: ""                 # 留空则从环境变量 AI_API_KEY 读取
  model: "claude-sonnet-5"   # 模型名
  timeout: 30                 # 请求超时(秒)
  score_weight: 0.3           # AI 评分在最终评分中的权重
```

### 4.3 接口示例

```python
from adapter.ai_client import AIClient

ai = AIClient(config)

# 需求 1: 意图解析
params = ai.parse_intent("跑一个低功耗的UART，200MHz")
# → {"design":"uart","frequency":200,"requirements":["低功耗"]}

# 需求 2: 工具评分
scores = ai.score_tools({
    "design": "aes", "stage": "synthesis", "frequency": 500,
    "candidates": [{"tool":"Yosys","score":85},{"tool":"DC","score":80}],
    "history": {"Yosys_synthesis": 0.65, "DC_synthesis": 0.95}
})
# → {"Yosys": 70, "DC": 95, "reasoning": "AES S-Box 关键路径..."}
```

### 4.4 统一降级：AI 不可用时

```python
def parse_intent(self, text):
    if not self.enabled: return None
    try:
        response = self._call(prompt)
        return json.loads(response)
    except Exception:
        return None  # 静默降级, 调用方走现有逻辑
```

---

## 五、实现计划

| 阶段 | 内容 | 预估改动 | 优先级 |
|------|------|---------|--------|
| **Phase 1** | NL 词法解析（需求 1） | `ai_client.py` + `cli.py` + `config.yaml`, ~130 行 | P0 |
| **Phase 2** | AI 评分通道（需求 2） | `ai_client.py` + `flow_composer.py`, ~90 行 | P1 |
| **Phase 3** | AI 失败诊断 | `ai_client.py` + `cli.py` 修复轮, ~60 行 | P2 |
| **Phase 4** | AI 辅助参数优化 | `recommender.py` 加 AI fallback | P3 |

---

## 六、风险与缓解

| 风险 | 缓解 |
|------|------|
| AI 返回格式错误 | JSON 解析失败 → 降级到规则引擎 |
| AI 延迟过高 | 30s 超时 + 仅冷启动/关键决策点调用 |
| AI 成本过高 | 带缓存 + 规则引擎先跑，AI 仅补充边界 case |
| AI 建议不专业 | 限制 AI 不做工具执行/状态管理/流程控制 |
| API key 泄露 | 从环境变量读取，不硬编码 |
