# RunHistory 反馈闭环实现

## 背景

ic_agent_os 的核心功能是根据用户需求自动选择 EDA 工具组合生成 IC 设计流程。当前的工具选择逻辑完全依赖静态评分：

```python
# tool_registry.py — 固定权重
Yosys:  is_open_source=True → +40 分
DC:     quality="highest" → +70 分
```

**问题**：无论 Yosys 在历史上执行成功多少次、失败多少次，评分永远不变。一个工具在某频率段全部失败，评分依然优于实际表现更好的工具。此外，每次跑完的诊断报告仅输出到终端，不存储、不累积、不影响后续决策。

## 目标

实现一个历史反馈闭环，使得：

1. **每次执行结果自动入库**，形成持续积累的知识库
2. **下次生成 Flow 时查询历史**，用真实成功率调整静态评分
3. **Demo Flow 跑完后自动输出诊断报告**，基于诊断 + 历史给出 Final Flow 的构建建议（步骤裁剪、工具替换、参数调整方向）

## 设计方案

### 数据模型

新建 `adapter/run_history/` 模块。SQLite 单表存储每次完整运行：

| 字段 | 来源 | 说明 |
|------|------|------|
| design, technology | CLI 输入 | 设计名、PDK |
| requirements_json | CLI 输入 | 需求关键词 (如 ["开源","低功耗"]) |
| goals_json | CLI 输入 | 设计目标 (如 {"frequency":200}) |
| gate_count, top_module | RTL 预分析 | 门数、顶层模块名 |
| run_type | 标记 | "demo"(探索) 或 "final"(生产) |
| flow_name, flow_phase, flow_steps_json | ComposedFlow | 生成的流程方案 |
| metrics_json | SnapshotPackage | 执行结果指标 (WNS,TNS,area...) |
| passed, duration_ms | SnapshotPackage | 是否达标、耗时 |

### 相似度查询

```python
querier.find_similar(design, technology, goals, gate_count, requirements)
```

匹配逻辑：
- 同 technology (精确)
- 同 design (精确，可选)
- 频率 ±50% (范围)
- gate_count ±50% (范围)
- 需求关键词重叠数 (加分)
- 时效性：final 类型 +5 分

### 评分调整

FlowComposer 的工具评分逻辑新增历史调整因子：

```python
# 原逻辑
score = 50 + quality_bonus × w.quality + speed_bonus × w.speed + open_bonus × w.open + ...

# 新增
if history:
    success_rate = history.get_tool_confidence(stage, tool.name)
    if success_rate is not None:
        score *= (0.5 + 0.5 × success_rate)
```

采用 (0.5 + 0.5 × rate) 而非直接用 rate 是为了：
- 新工具无历史数据 (rate=None) → 不走调整 → 静态评分完全有效
- 历史成功率 0% → 最终得分 = 静态×0.5，不会归零（可能是特定场景问题）

### 两步推荐

**Demo 建议（跑之前）**：

```
suggest_demo(design, technology, goals, gate_count)
```

输出：
- `initial_params`：历史上同类设计最优的 CLK_PERIOD、density 初始值
- `warnings`：该 PDK 已知的坑、频率风险
- `tool_confidence`：每个 (stage, tool) 的历史成功率
- `historical_baselines`：类似设计的历史记录摘要

**Final 建议（跑完 demo 后）**：

```
suggest_final(design, technology, goals, demo_diagnosis)
```

输出：
- `recommended_depth`：精简/完整/自定义
- `suggested_skip_steps`：可跳过的步骤列表
- `tool_confidence`：是否有成功率过低需要替换的工具
- `param_advice`：参数调整方向
- `reasoning`：每条建议的依据

深度推断规则：

| 条件 | 推荐深度 | 理由 |
|------|---------|------|
| gates < 2000, WNS ≥ 0, 历史精简全过 | lite | 小设计+时序余量+历史验证 |
| WNS > 5.0, gates < 5000 | lite | 时序余量极大 |
| gates > 10000 | full | 大设计需要完整物理流程 |
| freq > 500MHz | full | 高频需 STA checkpoints |
| 0 ≤ WNS < 1.0 | full | 时序紧张，保安全 |

### 诊断报告格式

Demo Flow 执行完毕后，终端输出 5 段式报告：

```
═══ Demo Flow 诊断报告 ═══
── 1. 执行结果 ──         每个步骤的成功/失败状态和耗时
── 2. PPA 体检 ──          WNS/TNS/Area/Power 逐项 + 达标判断
── 3. 历史对比 ──          相似设计的执行记录表格 + 工具成功率统计
── 4. Final Flow 构建建议 ── 深度建议/步骤裁剪/工具替换/参数调整
── 5. 下一步 ──            交互提示
```

## 实现

### 文件结构

```
adapter/run_history/
├── __init__.py       # 统一导出，自动建表
├── schema.py         # SQLite 表定义
├── recorder.py       # record(RunInput, flow, result) → INSERT
├── querier.py        # find_similar() + stats_by_tool()
├── recommender.py    # suggest_demo() + suggest_final()
└── report.py         # format_demo_report()
```

### FlowComposer 集成

修改 `composer/flow_composer.py`，`compose()` 新增两个可选参数（向后兼容）：

| 参数 | 类型 | 作用 |
|------|------|------|
| `history` | FlowRecommender | `_score_tool()` 内调整评分 |
| `diagnosis` | dict | `_select_stages()` 内裁剪步骤 |

增加约 35 行，`history=None` 时行为完全不变。

### CLI 集成

`cli.py` 增加约 15 行：
- 实例化 `FlowRecommender`
- `compose(history=recommender)` 传入
- demo 执行完毕后调用 `format_demo_report()` 输出诊断报告
- `record()` 入库

## 执行效果

### 端到端闭环

```
冷启动 (无历史)
  → suggest_demo() 返回空建议，默认 explore 精简 flow
  → Yosys 综合 (0.5s) + OpenSTA (0.3s)
  → WNS=0.13ns, gate_count=640
  → suggest_final(): 设计小 + WNS>0 + 历史精简全通过 → 推荐精简
     跳过 resize (时序余量充足), 跳过 filler (小设计非必须)
  → Final Flow 从 12 步裁剪为 10 步
  → record() 入库

第二次运行 (有 1 条历史)
  → suggest_demo() 查询到上次记录
  → CLK_PERIOD=5.0ns (历史最优), Yosys 100% 成功率
  → 同上执行并记录

第三次运行 (有 2 条历史)
  → stats_by_tool() 返回: synthesis:Yosys=100%, STA:OpenSTA=100%
  → suggest_final() 历史足够 → 更精准的建议
```

### 测试结果

```
$ python3 tests/test_all.py
Passed: 71  |  Failed: 0  |  Total: 71
```

### 诊断报告样本

```
═══ Demo Flow 诊断报告 ═══
  Design: gcd  |  Technology: Nangate45  |  200MHz

── 1. 执行结果 ──
  [synthesis   ] Yosys         ✅
  [STA         ] OpenSTA       ✅
  耗时: 0.9s  |  状态: ✅ 通过

── 2. PPA 体检 ──
  Timing (WNS)     0.13 ns    ✅  (目标: >0)

── 3. 历史对比 ──
  查到 3 条相似历史记录:
  ┌──────────────────────────────────────────────────┐
  │ 设计    频率    工具链              WNS    结果   │
  │ gcd     200M    Yosys→OpenSTA      +0.1   ✅    │
  │ gcd     200M    Yosys→OpenSTA      +4.2   ✅    │
  │ gcd     200M    Yosys→OpenSTA      +3.9   ✅    │
  └──────────────────────────────────────────────────┘
  工具历史成功率:
    ✅ synthesis:Yosys = 100%
    ✅ STA:OpenSTA = 100%

── 4. Final Flow 构建建议 ──
  📐 流程深度: ⚡ 精简 (2步)
  ✂️  建议跳过: filler
  💡 设计小 (640 gates) + WNS=0.13>0, 历史精简全通过 → 推荐精简

── 5. 下一步 ──
  [回车]  按上述建议自动生成 Final Flow 并执行
```

## 环境适配（附带工作）

项目原本硬编码指向另一台机器的 sky130 PDK 路径 (`/home/xu/...`)，适配到本机 Nangate45 PDK (`/opt/iFlow/foundry/nangate45/`)，涉及 9 个文件。OpenROAD 多步物理流程的 DEF 文件链传存在缺失，修复后 8 步增量流程全部通过。

## 文件清单

### 新建

| 文件 | 说明 |
|------|------|
| `adapter/run_history/schema.py` | SQLite 表定义 |
| `adapter/run_history/recorder.py` | 执行记录器 |
| `adapter/run_history/querier.py` | 相似度查询 + 工具统计 |
| `adapter/run_history/recommender.py` | Demo / Final 建议生成 |
| `adapter/run_history/report.py` | 终端诊断报告格式化 |
| `adapter/run_history/__init__.py` | 模块导出 |

### 修改

| 文件 | 改动 | 说明 |
|------|------|------|
| `composer/flow_composer.py` | +35 行 | `compose()` +history +diagnosis |
| `cli.py` | +15 行 | 集成 run_history |
| `adapter/config.yaml` | 重写 | PDK 路径适配 |
| `adapter/openroad_runner.py` | 重写 | DEF 链传修复 + Nangate45 适配 |
| `tests/test_all.py` | ~20 行 | 路径适配 |
| `setup_check.py` | ~5 行 | PDK 检查适配 |
