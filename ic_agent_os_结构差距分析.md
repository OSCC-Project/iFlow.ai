# ic_agent_os 与真实 Flow Solution 结构差距分析

> 对照基准：数字芯片设计从 0 到 sign-off 的实际工程流程
> 分析日期：2026-07-14

---

## 一、真实 Flow Solution 结构（参照标准）

### 1.1 12 步标准流程

```
synth → floorplan → tapcell → pdn → gplace → resize → dplace
  → cts → groute → droute → filler → gds
```

### 1.2 STA/DRC checkpoint 嵌入位置（典型工业 Flow（示例）：约 5 个 STA + 2 个 DRC（具体数量因公司 Flow 而异））

```
synth   ──→ STA ① (post-synthesis, 快速检查网表时序)
resize  ──→ STA ② (pre-CTS, 有预估线延迟)
cts     ──→ STA ③ (post-CTS, 时钟树引入后)
droute  ──→ STA ④ (post-route signoff) + DRC ① (post-route)
sign-off ──→ STA ⑤ (multi-corner: TYP+SLOW+FAST) + DRC ② (final GDS)
```

### 1.3 从 0 到 sign-off 需要跑 5 轮

| 轮次 | 名称 | 目标 | 流程范围 | 预计耗时 |
|------|------|------|---------|----------|
| 1 | 探索轮 | 摸清设计体质 | synth + STA（精简 2 步） | ~2 min |
| 2 | 全流程轮 | 发现所有问题 | 完整 12 步 | ~10 min |
| 3 | 修复轮 | 修 DRC/timing | 从失败 step 开始增量重跑 | ~2-8 min |
| 4 | ECO 轮 | 最后微调 | 单步或几步增量 | ~1-3 min |
| 5 | Sign-off 轮 | 多 corner 闭合 | TYP + SLOW + FAST 全 STA | ~15 min |

### 1.4 每轮之后有 Gate Check

- 输出文件大小 > 合理阈值
- log 无 ERROR/FATAL
- 上一步失败则后续全部跳过（fail-fast）

---

## 二、对齐项 ✅

### 2.1 12 步流程定义 — 完全一致

`composer/flow_composer.py:129-142` `DIGITAL_STAGES`：

```python
"synthesis", "floorplan", "tapcell", "pdn", "gplace",
"resize", "dplace", "cts", "groute", "droute", "filler", "gds"
```

**结论：无差距。**

### 2.2 STA 嵌入为 checkpoint — 部分对齐 (3/5)

ic_agent_os 实现了 5 个 STA checkpoint 中的 **3 个**：

`cli.py:411-418` / `demo/demo_flow_e2e.py:110-122` `_STAGE_FLOWS`：

```python
"resize": ["resize", "sta_report"],           # ← STA ② pre-CTS  ✅
"cts":    ["clock_tree_synthesis", "sta_report"],  # ← STA ③ post-CTS ✅
"droute": ["detailed_route", "sta_report"],   # ← STA ④ signoff   ✅
```

**缺失的 2 个 checkpoint：**

| 缺失 | 位置 | 用途 | 为什么重要 |
|------|------|------|-----------|
| STA ① | synth 之后 | 快速检查网表时序 | 在进入耗时的物理实现之前，先确认网表本身时序是否合理。如果 post-synthesis WNS 已经是 -5ns，根本不应该继续跑 floorplan |
| STA ⑤ | sign-off 阶段 | 多 corner (TYP+SLOW+FAST) | 单一 corner 通过不代表可流片。SLOW corner 决定 setup sign-off，FAST corner 决定 hold sign-off。缺一个都可能 tape-out 失败 |

`cli.py:533-534` 在 compose 输出中也正确标注了现有 3 个 checkpoint。

**结论：当前实现已覆盖多个关键 STA Checkpoint（resize、CTS、route），能够支持基本闭环；若希望进一步贴近工业 Sign-off Flow，建议补充 post-synthesis STA 与 multi-corner STA。**

### 2.3 DEF 链传机制 — 正确

`demo/demo_flow_e2e.py:135-138`：

```python
if prev_def and step.stage != "floorplan":
    params["INPUT_DEF"] = prev_def
```

每步自动追踪 DEF 产物，下一步自动读入，避免重复跑前面的步骤。

**结论：无差距。**

### 2.4 迭代优化闭环 — 框架正确

`composer/flow_composer.py:670-731` `close_loop()` 实现了：

```python
report = analyzer.analyze(metrics, goal=ppa_spec)   # 诊断
if not report.passed:
    rerun_plan = replanner.cheapest_first(...)       # 生成重跑计划
```

`demo/demo_flow_e2e.py:428-500` Phase 5 演示了 `compose → execute → diagnose → replan → execute` 闭环。

**结论：闭环框架存在，但实现细节有差距（见 3.1）。**

### 2.5 代价感知的增量重跑 — 正确

`composer/replanner.py:32-53` `RERUN_MAP` 分 L0-L3 四级：

| Level | 触发参数 | 成本 |
|-------|---------|------|
| L0 | `clock_period`, `input_delay`, `clock_uncertainty` | 秒级, STA only |
| L1 | `place_density`, `max_fanout`, `CLK_PERIOD` | 分钟, place+ |
| L2 | `core_utilization`, `aspect_ratio`, `DIE_AREA` | 分钟, floorplan+ |
| L3 | `rtl_change`, `LIBERTY_PATH` | 小时, full flow |

**结论：概念正确，但阶段名匹配有 bug（见 3.5）。**

---

## 三、差距项 ❌

### 3.1 【P2】建议增加 Flow Phase（架构增强） — 没有"第几次跑"的概念

**现状：**

`composer/flow_composer.py:322-343` `_select_stages()` 只靠 goal key 是否存在来决定跑完整还是精简：

```python
has_phys = any(k in goals for k in ["area_max", "area_min", ...])
if has_phys:
    return list(self.DIGITAL_STAGES)  # 完整 12 步
return self.DIGITAL_LITE_STAGES       # 精简 2 步
```

**问题：** 无法区分以下场景：
- 第一次跑只想看看 WNS 有多少余量（应该 synth + STA，2 步）
- 第二次跑要发现所有问题（应该完整 12 步）
- 第三次跑只修 PDN 配置后的问题（应该从 PDN 开始增量重跑）
- 最终 Sign-off 需要多 corner（应该 TYP→SLOW→FAST 各跑一次 STA）

**影响：** 用户无法表达"我先探索一下，再全跑，再修，再签核"这个自然的工作流。每次只能 `compose()` 一次然后线性执行，缺乏对迭代轮次的感知。

**建议：** 在 `ComposedFlow` 或 CLI 交互中增加 `phase` 概念：

```python
class FlowPhase(Enum):
    EXPLORE = "explore"       # → synth + STA, ~6s
    FULL_RUN = "full_run"     # → 12 steps + Gate Check, ~10min
    FIX = "fix"               # → 从失败 step 增量重跑
    ECO = "eco"               # → 单步微调
    SIGN_OFF = "sign_off"     # → 多 corner STA + DRC
```

---

### 3.2 【P2】可考虑增加 Intra-Run 反馈回路（增强项）

**现状：** ic_agent_os 的 12 步执行是严格线性的——每步跑完自动进下一步，没有"这一步失败了回到前面某步重试"的机制。

**真实 Flow 的结构是带反馈回路的分层决策树：**

```
                    ┌──────────────────────────────────┐
                    │         Placement Loop            │
                    │  gplace → resize → dplace        │
                    │               ↓ STA ②             │
                    │        timing ok? ──No──→ 回到 gplace  │
                    │          Yes                      │
                    └──────────────┬───────────────────┘
                                   │
                    ┌──────────────▼────────────────────┐
                    │           CTS Loop                 │
                    │  CTS → STA ③                       │
                    │  skew ok? timing ok?               │
                    │    No → 调 CTS 参数重跑              │
                    └──────────────┬────────────────────┘
                                   │
                    ┌──────────────▼────────────────────┐
                    │         Routing Loop               │
                    │  groute → droute                   │
                    │    ↓ DRC ①  ↓ STA ④                │
                    │  violations? → ECO 或回到 groute     │
                    └──────────────────────────────────┘
```

ic_agent_os 有跨 run 的闭环（`close_loop()` → `Replanner`），但**同一个 run 内部没有反馈**。如果 resize 后 STA 发现 WNS=-0.5ns：

- **真实流程**：立即回到 gplace，降低 density 重做 placement，直到 WNS≥0 才进入 dplace
- **ic_agent_os**：继续跑 dplace → cts → ... → 最后才发现 DRC/timing 全挂了，然后下一轮 `compose()` 从 floorplan 重来，浪费前面所有时间

**影响：** 这违背了 "fail fast, fix early" 的核心原则。在 Placement 阶段修 timing 的成本远低于 Routing 之后修。

**建议：** 在 `execute_step()` 中增加 checkpoint 回调机制：

```python
def execute_with_feedback(step, result, prev_results):
    if step.stage == "resize" and result.wns < 0:
        return BACKTRACK_TO("gplace", {"place_density": density - 0.05})
    if step.stage == "droute" and result.drc_violations > 0:
        return BACKTRACK_TO("groute", {"congestion_margin": margin + 0.1})
    return CONTINUE
```

---

### 3.3 【P1】探索模式可进一步优化

**现状：** `flow_composer.py:145-148` 精简模式定义为：

```python
DIGITAL_LITE_STAGES = [
    "synthesis",
    "droute",  # 精简模式下至少跑一次布线+STA
]
```

**问题：** 探索轮的目标是快速摸清设计体质（WNS 有多少余量），只需 synth + STA 两步即可。但 droute 是流程的第 10 步，跑它意味着前面的 floorplan→tapcell→pdn→gplace→resize→dplace→cts→groute 全部要跑完，实际上跑了 ~10 步。

| 探索轮 | 理想 | ic_agent_os 当前 |
|--------|------|-----------------|
| 步骤 | synth → STA (OpenSTA 独立) | synth → ... → droute (含 STA) |
| 步数 | 2 | ~10 |
| 耗时 | ~5s + 0.5s = ~6s | ~8min |

**建议：** 精简模式改为 synth + 独立 OpenSTA（不跑 physical）：

```python
DIGITAL_LITE_STAGES = [
    "synthesis",
    "sta",  # ← 独立 STA, 调用 OpenSTA adapter, 不经过 OpenROAD physical
]
```

---

### 3.4 【P1】建议增加 DRC Checkpoint

**现状：** `cli.py:406-418` `_STAGE_FLOWS` 中 `droute` 映射只有 STA，没有 DRC：

```python
"droute": ["detailed_route", "sta_report"],  # ← 缺 drc_report
```

**问题：** 整个代码库搜索不到 `check_routes` 或 `drc_report`。droute 之后不做 DRC 检查意味着：
- routing shorts/opens 不会被自动发现
- 用户必须手工查看 log 才知道 DRC 是否 clean
- 无法在 DRC 不通过时触发 intra-run 反馈回路（见 3.2）

**影响：** DRC clean 是比 timing clean 更硬的 sign-off 约束。缺失 DRC checkpoint 意味着流程Agent 无法自动依据 DRC 结果进行决策；最终 DRC 仍可由 Magic、Calibre、ICV 等 Sign-off 工具完成。

**建议：**

```python
"droute": ["detailed_route", "sta_report", "drc_report"],  # ← 加 DRC
```

并在 `analyzer.py` 中增加 `_diagnose_drc()` 方法。

---

### 3.5 【P1】Replanner 的阶段名与实际流程不匹配

**现状：** `composer/replanner.py:32-53` `RERUN_MAP` 使用旧阶段名：

```python
"place_density": (1, ["placement", "CTS", "routing", "STA"]),
```

但 `DIGITAL_STAGES` 实际是 `"gplace", "resize", "dplace"`（不是 `"placement"`），`"groute", "droute"`（不是 `"routing"`）。

**问题：** 匹配逻辑 `any(a in s.lower() for a in affected)` 中，`"placement"` 不是 `"gplace"`/`"resize"`/`"dplace"` 的子串，`"routing"` 也不是 `"groute"`/`"droute"` 的子串。**Replanner 可能返回空的重跑列表。**

**建议：** 修改 `RERUN_MAP` 使用实际阶段名：

```python
"place_density": (1, ["gplace", "resize", "dplace", "cts", "groute", "droute"]),
"core_utilization": (2, ["floorplan", "gplace", "resize", "dplace", "cts", "groute", "droute"]),
```

---

### 3.6 【P1】缺少 Gate Check（流程卫士）

**现状：** `demo/demo_flow_e2e.py:195-197` 只检查工具进程是否崩溃（`SimError`），不检查：

| 检查项 | 缺失后果 |
|--------|---------|
| 输出文件是否存在 | 工具跑了但结果没生成 → 静默失败 |
| 输出文件大小 > 阈值 | `.def` 只有 200 bytes → 必定失败但检测不到 |
| log 中无 `ERROR:`/`FATAL:` | 工具 "成功" 退出但内部报错 |
| 上一步失败 → 跳过本步 | 级联失败浪费时间（评估报告中 P0 级发现） |

**建议：** 在 `adapter/` 层增加 Gate Check：

```python
GATE_CHECKS = {
    "synth": {
        "min_artifact_size": 1024,
        "log_forbidden": ["ERROR:", "FATAL:"],
    },
    "droute": {
        "min_artifact_size": 8192,
        "log_forbidden": ["violation", "short", "open"],
        "metric_check": {"drc_violations": "== 0"},
    },
}
```

---

### 3.7 【P2】Demo Phase 5 使用模拟数据演示

**现状：** `demo/demo_flow_e2e.py:445-458` 三组数据全是 hardcode：

```python
rounds = [
    ("第1轮: 初始参数", {"sta": {"wns": -0.50}}, "FAIL"),
    ("第2轮: 放松时序",  {"sta": {"wns": -0.10}}, "FAIL"),
    ("第3轮: 增大面积",  {"sta": {"wns": 0.05}},  "PASS"),
]
```

**问题：** 没有实际调 EDA 工具获取真实 WNS。`close_loop()` + `Replanner` 的能力无法在真实数据上验证。

**建议：** Phase 5 改为循环调用 `execute_step()` 跑 synth+STA，每次根据 `close_loop()` 的建议修改参数，直到 WNS ≥ 0 或达到最大迭代次数。

---

### 3.8 【P2】缺少多 Corner 支持

**现状：** 搜索 `SLOW`、`FAST`、`ss`、`ff`、`corner` 在整个 `ic_agent_os/` 中无结果。所有工具调用硬编码 TYP corner：

```python
"LIBERTY_PATH": "/home/xu/OpenROAD-ae191807/test/sky130hd/sky130hd_tt.lib",
#                                             只有 tt（TYP）──^^
```

**真实 sign-off 需要：**

| Corner | 电压 | 温度 | 用途 |
|--------|------|------|------|
| TYP (tt) | 1.80V | 25°C | 初始验证 |
| SLOW (ss) | 1.60V | 125°C | Setup timing sign-off |
| FAST (ff) | 2.00V | -40°C | Hold timing sign-off |

**建议：**
1. `adapter/config.yaml` 增加 corner 配置段（`tt_lib` / `ss_lib` / `ff_lib`）
2. `ComposedFlow` 或 `PPASpec` 增加 `corners: List[str]`
3. `FlowPhase.SIGN_OFF` 自动对每个 corner 跑一次 STA

---

### 3.9 【P2】GDS merge — stage 存在但走 KLayout 路径

**现状：** `cli.py:417` `"gds": ["write_gds"]` — 走 OpenROAD 内置的 KLayout，在 WSL headless 下崩溃（Signal 11，已知 bug）。

**建议：** 增加 gdstk adapter，调用已验证的 `def2gds_gdstk.py`（成功生成 127K instance 的 GDS2），解除 KLayout 依赖。

---

## 四、差距汇总

| # | 问题 | 严重度 | 位置 | 影响 |
|---|------|--------|------|------|
| 1 | Flow 阶段扁平化，无 phase 概念 | **P0** | `flow_composer.py:_select_stages()` | 无法区分探索/全跑/修复/签核 |
| 2 | 缺少 Intra-Run 反馈回路 | **P0** | `demo_flow_e2e.py:execute_step()` | 线性执行，不在 run 内 fail-fast |
| 3 | 精简模式跑 ~10 步而非 2 步 | **P0** | `flow_composer.py:DIGITAL_LITE_STAGES` | "快速探索" 耗时 8min 而非 6s |
| 4 | 缺少 DRC checkpoint | **P0** | `cli.py:_STAGE_FLOWS` | droute 后不检查 DRC |
| 5 | Replanner 阶段名不匹配 (bug) | P1 | `replanner.py:RERUN_MAP` | 重跑规划可能返回空列表 |
| 6 | 缺少 Gate Check | P1 | `demo_flow_e2e.py` | 级联失败仍可能发生 |
| 7 | demo Phase 5 用假数据 | P1 | `demo_flow_e2e.py:445-458` | 迭代演示不可信 |
| 8 | 缺少多 Corner | P2 | 全局 | 无法做 sign-off |
| 9 | GDS merge 依赖 KLayout | P2 | `cli.py:_STAGE_FLOWS` | WSL headless 崩溃 |

---

## 五、修复优先级

```
Phase 1 (本周, P0 — 结构性缺陷):
  1. Flow Phase 概念 → ComposedFlow 增加 phase: EXPLORE/FULL_RUN/FIX/ECO/SIGN_OFF
  2. Intra-Run 反馈回路 → execute_step() 增加 checkpoint backtrack 机制
  3. 精简模式修复 → DIGITAL_LITE_STAGES 改为 ["synthesis", "sta"]
  4. DRC checkpoint → _STAGE_FLOWS 中 droute 加 drc_report

Phase 2 (下周, P1 — 质量保障):
  5. Gate Check → adapter 层增加产出物校验 + fail-fast
  6. Replanner 阶段名修复 → RERUN_MAP 对齐 DIGITAL_STAGES
  7. demo Phase 5 真实化 → 用 execute_step() 替代假数据

Phase 3 (后续, P2 — 完整性):
  8. 多 Corner → config.yaml + PPASpec + sign-off 流程
  9. gdstk GDS adapter → 替代 KLayout write_gds
```

---

## 六、修完后 ic_agent_os 应支持的完整工作流

```
$ python3 cli.py

  欢迎使用 IC-Agent-OS
  选择设计阶段:
  [1] 探索 — 快速 STA, 看 WNS margin (2步, ~30s)
  [2] 全流程 — 完整 RTL-to-GDS2 (12步, ~10min)
  [3] 增量修复 — 从上次失败的 step 继续
  [4] ECO — 单步微调
  [5] Sign-off — 多 corner STA + DRC (3 corner, ~15min)

  选择 [1]:
    → synth → STA
    → WNS=8.52ns ✅ 100MHz 非常宽松

  选择 [2]:
    → synth → floorplan → tapcell → pdn → gplace → resize(STA①)
    → dplace → cts(STA②) → groute → droute(STA③+DRC①)
    → filler → gds
    → 全部 Gate Check PASS ✅

  选择 [5]:
    → TYP corner:  WNS=8.31ns ✅  DRC=0 ✅
    → SLOW corner: WNS=5.12ns ✅
    → FAST corner: WNS=0.03ns ✅ (hold)
    → GDS2 已生成: outputs/gcd_final.gds (68KB)
    → Sign-off 通过 ✅
```
