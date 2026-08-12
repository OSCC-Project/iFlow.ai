# ic_agent_os 与 SiliconCompiler 架构对比分析

> 对照对象：SiliconCompiler（业界成熟的 Python EDA Flow 框架，DAC 2023 最佳论文）
> 分析日期：2026-07-25

---

## 一、一句话总结

**SiliconCompiler 用一个统一的 Schema 解决了 ic_agent_os 用硬编码解决的几乎所有问题。** ic_agent_os 的"集成"本质上是 Python dict 在不同文件之间传递的胶水代码；SiliconCompiler 的"集成"是一个有类型系统、版本控制、节点依赖追踪的统一数据模型。

---

## 二、六项关键架构差异

### 2.1 数据模型：dict vs Schema

| | ic_agent_os | SiliconCompiler |
|---|---|---|
| 参数载体 | `params = {"TOP_MODULE":"gcd", ...}` — 普通 dict | `Schema` — 带类型的参数注册表 |
| 参数校验 | 无（key 拼错静默忽略） | 注册时定义类型+默认值，写错 key 报错 |
| 跨步骤传递 | `prev_netlist`、`prev_def` 两个全局变量手动追踪 | Flowgraph 中每个 node 声明 inputs/outputs，自动推导依赖链 |
| 版本兼容 | 无（config.yaml 改一个路径名可能导致 runner 读不到） | Schema 有 `Scope` 和 `PerNode` 概念，参数作用域明确 |

**ic_agent_os 的问题**：`params` dict 在 5 个文件之间裸传，没有任何结构保证。`CLK_PERIOD` 要同时出现在 `cli.py`、`param_bridge.py`、`openroad_runner.py`、`opensta_runner.py`、`digital_runner.py` 五个地方才能正确生效。漏了一处就是沉默 bug。

**SiliconCompiler 的方案**：

```python
# sc 的参数定义（简化示意）
class DesignParameter:
    clock_period = Parameter(type=float, default=10.0, scope=Scope.PERNODE,
                             short_help="Clock period in ns")

# 任何 task 通过统一 API 读
clk = design.get("clock_period")  # 10.0
# 不存在 "谁设了什么值" 的歧义——全在 Schema 里
```

---

### 2.2 步骤组织：线性列表 vs DAG

| | ic_agent_os | SiliconCompiler |
|---|---|---|
| Flow 结构 | `full_steps = ["synthesis","floorplan",...]` — Python list | `Flowgraph` — 有向无环图 (DAG) |
| 依赖关系 | 隐含（"上一步的 DEF 是下一步的输入"靠注释和人工约定） | 显式声明（edge 定义 input/output 依赖） |
| 并行能力 | 无（`for stage in steps:` 串行） | 原生支持（无依赖的节点可并行执行，有调度器 `SchedulerNode`） |
| 分支选择 | 无（工具选择在前，步骤在后，一次 compose 定死） | 支持（同一 stage 可以有多个 index 并行跑，跑完选最优） |

**ic_agent_os 的问题**：`for stage_name in round_steps:` 这个循环决定了流程**只能是串行的**。PDF (power delivery network) 和 tapcell 完全可以并行跑（它们之间无依赖），但现有架构做不到。

**SiliconCompiler 的方案**：

```
Flowgraph (DAG):

  synthesis ──→ floorplan ──→ tapcell ──→ pdn ──→ gplace
                                            │            │
                                            └────────────┘
                                          (pdn 和 gplace 可并行?)

  不，pdn 需要 tapcell 的结果。但：

  synth ──→ STA(synth)                 ← 独立列
  synth ──→ floorplan → tapcell → pdn → gplace → ...
         └─→ LEC(synth)                ← 独立列

  STA 和 LEC 可以和 floorplan 并行跑，因为它们只依赖 synth 结果。
```

---

### 2.3 工具抽象：硬编码 if/elif vs 统一 Task 接口

| | ic_agent_os | SiliconCompiler |
|---|---|---|
| 工具注册 | `adapter.py` 手动 `BackendRegistry.register("digital", DigitalRunner)` | `Task` 基类，每个工具继承并实现 `setup()` / `run()` |
| 工具参数 | Tcl 模板硬编码在 runner 里，例如 `global_placement -density 0.6` | `design.get("density")` — 参数从 Schema 读取，Tcl 由 Task 动态生成 |
| 工具替换 | `FlowComposer.swap_tool()` 重新评分选工具 | 同一个 step 换 index 即可（`place[0]`→Yosys, `place[1]`→Innovus） |
| 新增工具 | 需要改 5 个文件（runner + adapter + tool_registry + composer + cli） | 继承 Task → 实现 setup/run → 注册到 flowgraph |

**ic_agent_os 的问题**：要加一个工具需要碰 5 个文件。`openroad_runner.py` 里 200 行的 Tcl 模板生成函数是一个巨大的 if/elif 链，改一个 step 可能影响其他 step。

**SiliconCompiler 的方案**：

```python
class OpenROADTask(Task):
    def setup(self):
        self.add_input("def")       # 声明输入
        self.add_output("def")      # 声明输出
        self.add_tool_option("-density", type=float, default=0.6)
    
    def run(self):
        density = self.get("density")  # 从 Schema 取，不硬编码
        # 生成 Tcl...
```

---

### 2.4 状态追踪：SQLite 快照 vs Schema 内置 Record

| | ic_agent_os | SiliconCompiler |
|---|---|---|
| 运行状态 | `state.py` SQLite — 独立于执行流程的存储 | `Record` — Schema 内置的 task 执行记录 |
| 增量重跑 | 无原生支持（手动追踪 `prev_def`） | `RuntimeFlowgraph` — 自动检测哪些 task 的 input 变了，只重跑受影响的 |
| 失败恢复 | 无（崩了从头来） | `NodeStatus` 记录每个 task 状态，从失败点 resume |

**ic_agent_os 的问题**：`state.py` 存了运行结果，但 `FlowComposer` 不看。`Replanner` 的 L0-L3 重跑规划是理论框架，实际执行时没有自动化的增量重跑机制——需要用户手动指定 `failed_stages`。

---

### 2.5 参数流：全局变量 vs 节点作用域

**ic_agent_os**：

```python
# 全局变量追踪
prev_netlist = None   # ← 任何地方都能改
prev_def = None       # ← 隐式依赖链
final_wns = float("nan")

# 在 for 循环中手动更新
if adp == "digital":
    for a in result.artifact_manifest:
        if a.source_uri.endswith(".v"): prev_netlist = a.source_uri
elif adp == "openroad":
    for a in result.artifact_manifest:
        if a.source_uri.endswith(".def"): prev_def = a.source_uri
```

**SiliconCompiler**：

```python
# 每个 task 声明自己的输入输出
place_task.add_input("floorplan.def")    # ← 明确声明依赖
place_task.add_output("placed.def")      # ← 明确声明产出

# 框架自动推导：place 依赖 floorplan → floorplan 没变 → place 可以 skip
# 参数按 step 隔离——clock_period 可能在 synth=5.0, STA=2.5（无歧义）
```

---

### 2.6 PDK 管理：路径字符串 vs Library 对象

| | ic_agent_os | SiliconCompiler |
|---|---|---|
| PDK 表示 | `config.yaml` 中 `tech_lef:"/path/to/..."` — 裸字符串 | `PDK` + `StdCellLibrary` + `MacroLibrary` — 结构化对象 |
| Corner 管理 | 手动维护 corner→lib 映射，3 个硬编码字符串 | `Library.corners` 字典，自动匹配 |
| 多工艺支持 | `ALL_TECHS = ["sky130","ASAP7"...]` — 但只有 sky130 的 PDK 路径可配 | 每个 PDK 一个独立配置树 |

---

## 三、ic_agent_os 的核心问题

不是"代码写得不好"，而是**架构层次太低**：

```
当前问题:
  params dict 裸传 → 拼写错误无声失效 → 修复需要 grep 5 个文件
  Tcl 硬编码 → 新 PDK 需要改 runner → 新工具需要改 5 个文件
  串行 for 循环 → 无法并行 → 慢
  prev_def 全局变量 → 增量重跑不可靠 → 每次从头跑
  工具评分+注册分离 → 新工具接入成本高
```

---

## 四、渐进式改进路线（不改底层的前提下）

### 可以不改架构直接改进的

| 改进 | 方案 | 影响力 |
|------|------|--------|
| 参数 Schema 化 | 在 `compose()` 和 `adapter.run()` 之间加一个 `DesignContext` 对象替代裸 dict | 高 — 消灭 CLK_PERIOD 相关 bug |
| Tcl 模板外置 | 把 `openroad_runner.py` 中的 if/elif 链拆成 YAML 模板文件 | 中 — 新 PDK 适配更简单 |
| 工具注册收敛 | `tool_registry.py` 加 `runner_class` 字段，`adapter.py` 自动从 registry 创建 backend | 中 — 新工具只需改一个文件 |

### 需要底层重构的

| 改进 | 方案 | 影响力 |
|------|------|--------|
| list → DAG | 步骤从 Python list 改为 dependency graph | 高 — 支持并行、增量、分支 |
| 全局变量 → 节点参数 | `prev_netlist/prev_def` 改为 node.inputs/outputs 声明 | 高 — 增量重跑自动化 |
| Task 抽象 | runner 继承统一 Task 基类 | 高 — 新工具编写成本降低 80% |

---

## 五、结论

ic_agent_os 当前是一辆**能跑的卡丁车**——简单、直接、容易理解，适合原型验证。SiliconCompiler 是一辆**工程化的量产车**——抽象层次高、可扩展性强、但有学习成本。

如果要继续朝"生产可用"方向迭代，最优先的三步：

1. **参数 Schema 化**（消灭 #1 类 bug）
2. **Tcl 模板外置**（让 PDK 适配不再需要改 Python）
3. **Task 抽象**（让新工具接入降到改 1 个文件）
