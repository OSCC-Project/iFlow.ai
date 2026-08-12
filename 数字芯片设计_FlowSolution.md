# 数字芯片设计：真正的 Flow Solution 结构

> 回答：真正的 flow solution 结构是怎样的？从 0 开始需要跑几次 flow？
> 日期：2026-07-14

---

## 一、真正的 Flow Solution 结构

常见的 12 步线性流程是**简化视图**。真实的结构是一个**带反馈回路的层次化决策树**：

```
                            RTL + SDC
                                │
                    ┌───────────▼───────────┐
                    │    Synthesis (Yosys)   │
                    └───────────┬───────────┘
                                │ STA checkpoint ①
                    ┌───────────▼───────────┐
                    │      Floorplan        │ ←── 如果 post-route 面积不够,回到这里
                    └───────────┬───────────┘
                                │
                    ┌───────────▼───────────┐
                    │  Tapcell + PDN        │
                    └───────────┬───────────┘
                                │
              ┌─────────────────▼─────────────────┐
              │        Placement Loop              │
              │  ┌─────────────────────────────┐   │
              │  │ gplace → resize → dplace    │   │
              │  │        ↓ STA checkpoint ②    │   │
              │  │   timing ok? ──No──→ 回到gplace │
              │  │        Yes                    │   │
              │  └─────────────────────────────┘   │
              └─────────────────┬─────────────────┘
                                │
              ┌─────────────────▼─────────────────┐
              │          CTS Loop                  │
              │  ┌─────────────────────────────┐   │
              │  │  CTS → STA checkpoint ③      │   │
              │  │  skew ok? timing ok?         │   │
              │  │  No → 调CTS参数重跑           │   │
              │  └─────────────────────────────┘   │
              └─────────────────┬─────────────────┘
                                │
              ┌─────────────────▼─────────────────┐
              │        Routing Loop                │
              │  ┌─────────────────────────────┐   │
              │  │ groute → droute              │   │
              │  │   ↓ DRC checkpoint ④         │   │
              │  │   ↓ STA checkpoint ⑤ (final) │   │
              │  │   violations? → ECO或回到上一步 │   │
              │  └─────────────────────────────┘   │
              └─────────────────┬─────────────────┘
                                │
                    ┌───────────▼───────────┐
                    │   Filler + GDS merge   │
                    └───────────┬───────────┘
                                │
                    ┌───────────▼───────────┐
                    │   Sign-off (all corners)│
                    └───────────────────────┘
```

关键点：**STA 和 DRC 是嵌入在多个位置的门禁，不是独立的最终步骤**。典型工业 Flow 通常会设置多个 STA Checkpoint（例如 5 个）和多个 DRC Checkpoint。具体数量取决于公司流程、EDA 工具和 Sign-off 策略，并不存在统一标准。

### 5 个 STA checkpoint

| # | 位置 | 名称 | 用途 |
|---|------|------|------|
| ① | synth 之后 | post-synthesis STA | 快速检查网表时序。如果 WNS 已经是 -5ns，不应继续跑 physical |
| ② | resize 之后 | pre-CTS STA | 有预估线延迟的时序检查。如果 WNS < 0，回到 gplace 调整 density |
| ③ | cts 之后 | post-CTS STA | 时钟树引入后的时序。如果 degradation 过大，调整 CTS 参数 |
| ④ | droute 之后 | post-route STA (signoff) | 真实线延迟的最终时序。如果不通过，ECO 或回到 groute |
| ⑤ | sign-off 阶段 | multi-corner STA | TYP + SLOW + FAST 三个 corner 全部通过才可流片 |

### 2 个 DRC checkpoint

| # | 位置 | 用途 |
|---|------|------|
| ① | droute 之后 | 检查 routing shorts/opens，不通过则回到 groute |
| ② | sign-off 阶段 | 最终 GDS 的 DRC sign-off |

---


> **说明：本文讨论的是 Flow / Workflow 层面的迭代策略，不涉及 Placement、CTS、Routing 工具内部已有的优化循环。工具内部本身通常已经包含多轮优化与修复算法。**

## 二、12 步标准流程

```
synth → floorplan → tapcell → pdn → gplace → resize → dplace
  → cts → groute → droute → filler → gds
```

| # | 步骤 | 工具 | 输入 | 输出 | Gate Check |
|---|------|------|------|------|-------------|
| 1 | **synth** | Yosys | RTL + .lib + .sdc | gate-level netlist | netlist 语法检查 |
| 2 | **floorplan** | OpenROAD | netlist + .lef + die约束 | floorplan.def | die area 合理 |
| 3 | **tapcell** | OpenROAD | floorplan.def | tapcell.def | tap + endcap > 0 |
| 4 | **pdn** | OpenROAD | tapcell.def + PDN配置 | pdn.def | VDD/VSS net 存在 |
| 5 | **gplace** | OpenROAD | pdn.def + SDC | gplace.def | 放置率 > 0% |
| 6 | **resize** | OpenROAD | gplace.def + SDC | resize.def | **STA ②** WNS ≥ 0 |
| 7 | **dplace** | OpenROAD | resize.def | dplace.def | 无非法放置 |
| 8 | **cts** | OpenROAD | dplace.def + SDC | cts.def | **STA ③** 时钟树深度 ≥ 1 |
| 9 | **groute** | OpenROAD | cts.def | groute.def | congestion < 100% |
| 10 | **droute** | OpenROAD | groute.def | droute.def | **DRC ①** = 0 + **STA ④** WNS ≥ 0 |
| 11 | **filler** | OpenROAD | droute.def | filler.def | 无 N-well 间隙 |
| 12 | **gds** | gdstk | filler.def + .gds 单元库 | final.gds | GDS ≥ 1KB，可正常读取 |

---

## 三、从 0 开始需要跑几次 Flow？

**答案：没有固定次数。简单设计可能一次收敛；典型项目通常需要多轮（约 3~5 次）迭代；复杂 SoC 往往需要十几甚至数十次迭代。**

### 逐次分解（以 GCD 为例）

```
第 1 次（推荐）— 探索轮 (Exploration)
═══════════════════════════════
目标：摸清设计的"体质"，建立初始 floorplan
跑法：synth → STA（只 2 步）
耗时：~6 秒
产出：
  - 综合后 gate count、关键路径
  - 第一个 timing 报告（看 slack 空间有多大）
决策点：
  - 100MHz 下 WNS 有多少 ns？→ 如果 < 2ns，降频或换 Faster lib
  - 利用率设多少？→ GCD 太小，设 40% 避免浪费面积

第 2 次 — 全流程轮 (Full Run)
═══════════════════════════════
目标：跑通完整 12 步，发现所有问题
跑法：全流程 synth → ... → gds
耗时：~8-10 分钟
产出：
  - 第一份 GDS2
  - 所有 step 的 log 和 report
  - DRC 报告（大概率有 violation）
  - post-route STA 报告
发现问题：
  - DRC violations 在哪个区域？（通常是 corner cell 附近）
  - post-route timing 相比 pre-route 退化多少？
  - PDN IR drop 是否超标？
  - routing congestion 热点在哪？

第 3 次 — 修复轮 (Fix)
═══════════════════════════════
目标：修复第 2 次发现的问题
跑法：
  情况A（问题小）→ 只重跑有问题的 step + 下游 steps（增量）
  情况B（问题大）→ 从 floorplan 开始重跑（调 die size/utilization/margin）
  情况C（严重）  → 修改约束/SDC，从 synthesis 重跑
耗时：2-8 分钟（取决于从哪里开始）
产出：
  - DRC = 0
  - Timing WNS ≥ 0

第 4 次（可选）— ECO 轮
═══════════════════════════════
目标：在不重跑全流程的前提下修最后的瑕疵
跑法：手工修改 netlist/buffer placement → droute → STA/DRC
耗时：1-3 分钟

第 5 次 — Sign-off 轮
═══════════════════════════════
目标：多 corner 闭合 + 最终交付
跑法：对 SLOW/FAST corner 各跑一次（或复用 TYP 结果只跑 STA）
耗时：~15 分钟（两个 corner）
产出：
  - 3-corner STA 全部 clean
  - 最终 GDS2（可流片）
  - 全套 sign-off 文档
```

---

## 四、总结：最小可行轮次

```
从 0 到 sign-off 的 GCD 项目：

  第 1 次 ──→ 第 2 次 ──→ 第 3 次 ──→ 第 4 次（可选）──→ 第 5 次
   探索      全流程      修复        ECO               Sign-off
   6s        10min      2-8min     1-3min             15min
     │          │          │           │                  │
     └─ 摸清 ──→└─ 发现 ──→└─ 修干净 ─→└─ 微调 ─────→└─ 交付
```

**最终没有统一的"必须"轮次。对于 AI Agent Flow，推荐采用"探索→全流程→修复→ECO→Sign-off"的迭代方式；传统 Flow 也可能首次即直接运行完整流程。**

- GCD（~640 gates, 100MHz）大概率 3 次收敛：探索 → 全流程（发现小问题）→ 修复即 clean
- AES-128（~20K gates, 200MHz）预计 6-8 次迭代：placement/CTS/routing 各自的收敛循环会更多
- 大型 SoC（百万门级, GHz）可能需要 20-50 次迭代

---

## 五、关键原则

### 5.1 Fail Fast, Fix Early

- Placement 阶段修 timing 的成本：~30s（只重跑 gplace→resize→dplace）
- Routing 之后修 timing 的成本：~8min（从 floorplan 重跑全流程）
- **在同一轮 run 内发现问题就立即回溯，不要等到最后**

### 5.2 代价感知的增量重跑

| Level | 参数类型 | 重跑范围 | 耗时 |
|-------|---------|---------|------|
| L0 | clock_period, input_delay | STA only | 秒级 |
| L1 | place_density, max_fanout | placement+ | 分钟级 |
| L2 | core_utilization, DIE_AREA | floorplan+ | 分钟级 |
| L3 | rtl_change, LIBERTY_PATH | full flow | 小时级 |

### 5.3 不是"跑一次就完事"

```
compose → execute → diagnose → replan → execute → ...
    ↑                                            │
    └────────────── 根据结果决定下一步 ──────────────┘
```

每次 run 的结果（WNS、DRC、利用率、功耗）决定下一次 run 的参数和起点。

---

## 六、ic_agent_os 实现对照（2026-07-14）

### 6.1 对齐项

| 文档要求 | 实现状态 |
|---------|---------|
| 12 步线性流程定义 | ✅ `DIGITAL_STAGES` 完全一致 |
| STA 嵌入 resize/cts/droute | ✅ `_STAGE_FLOWS` 正确映射 |
| DRC 嵌入 droute | ✅ `droute` 映射含 `drc_report` |
| 多 Corner 配置 | ✅ `config.yaml` 已定义 TYP/SLOW/FAST |
| 代价感知 L0-L3 | ✅ `replanner.py` 实现 |
| 5 轮迭代框架 | ⚠️ `close_loop()` 存在但未接入 CLI 执行循环 |

### 6.2 差距项

| 文档要求 | 当前状态 | 差距描述 |
|---------|---------|---------|
| ① post-synthesis STA | ❌ | synth 后不跑 STA，无法判断是否继续 physical |
| ② resize 后 WNS<0 回到 gplace | ❌ | STA 跑了，报 WNS，但流程继续往下跑，不回头 |
| ③ CTS loop（skew 差→调参重跑） | ❌ | 线性执行，不分支 |
| ④ Routing loop（DRC violation→回 groute） | ❌ | 同上 |
| 修复轮 / ECO 轮 | ❌ | CLI 的 5 轮迭代只实现了探索+全流程，修复轮未接入 |
| Sign-off 自动化 | ⚠️ | 需用户手动确认，不自动触发 |
| Step-specific Gate Check | ⚠️ | 只有 `synthesis` 和 `_default_physical` 两条通用规则 |

### 6.3 不修复的理由

上述差距项分为两类，各有不修的原因。

**第一类：反馈回路（①-④）— 属于 Optimizer，不属于 Adapter**

Adapter 的职责边界：
```
Adapter  → 调用工具、呈现仿真结果、搭建 flow 方案
Optimizer→ 判断何时回头、修改什么参数、如何收敛
```

`Adapter.run()` 返回 `SnapshotPackage` 之后，流程不再属于 Adapter。WNS<0 时"该不该回头、回哪一步、改什么参数"是 Optimizer 的决策空间。把回溯逻辑写进 Adapter 意味着每次跑 flow 都绑定了一种特定的优化策略——换一个 Optimizer（贝叶斯 vs 随机搜索 vs RL）就要改 Adapter。

**第二类：精细化 Gate Check — 宜由 PDK 配置驱动，不宜硬编码**

"DRC=0"、"VDD/VSS net 存在"这类检查规则依赖具体 PDK 和工艺节点。sky130 的 DRC rule 和 ASAP7 不同，硬编码在 Adapter 里会导致：换一个工艺就要改一处代码、Gate Check 和实际 DRC 工具的输出格式不同步、维护成本随 PDK 数量线性增长。

当前的基础 Gate Check（文件存在、大小>阈值、日志无 ERROR/FATAL）已能拦截多数静默失败。精细化规则应在 `config.yaml` 的 PDK 配置段中声明，由 Gate Check 引擎读取执行——这需要额外的设计工作。

**第三类：修复/ECO 轮 — 属于流程编排层，不属于 Flow Solution 定义**

5 轮迭代中的"哪一轮跑什么步骤"是流程编排逻辑，不是单个 Flow Solution 的结构。`close_loop()` 框架已存在，接入 CLI 执行循环需要的不是改 Flow Solution，而是改交互式向导的状态机。这是实现优先级问题，不是设计缺陷。
