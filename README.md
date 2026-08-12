# iflow-lab — 集成电路 AI 实训平台

基于自然语言的芯片设计全流程平台。AI 生成 RTL、自动验证、物理实现，覆盖从需求到 GDS 的完整流程。

**底层引擎**: [ic_agent_os](https://github.com/OSCC-Project/ic_agent_os) (Adapter / Optimizer / State / Evaluator)

## 快速开始

```bash
# 1. 安装 EDA 工具
sudo bash docker/setup_tools.sh

# 2. 启动后端
export DEEPSEEK_API_KEY="your-key"  # DeepSeek API Key (https://platform.deepseek.com)
uvicorn server.api:app --host 0.0.0.0 --port 8000

# 3. 启动前端
cd frontend && npm install && npm run dev

# 4. 打开浏览器
# http://localhost:3000
```

## 功能

### 阶段 1 — RTL 设计与生成
- 🤖 AI 生成 RTL（ChipMATE + DeepSeek，交叉验证匹配率 100%）
- ✍ Monaco 代码编辑器（Verilog 语法高亮）
- 📎 文件上传
- ✅ 编译检查（Verible + Verilator + Icarus 自动激励仿真）

### 阶段 2 — 仿真与验证
- 📊 功能仿真（AI 生成 Testbench + SVG 波形，多信号显示）
- 🔷 形式化验证（AI 生成 SVA Property + SymbiYosys BMC/k-induction）
- 🤖 AI 分析验证结果，给出修改建议
- 📈 可调采样点数（5-100）

### 阶段 3 — 芯片实现
- 💬 AI 聊天式交互：自然语言描述需求 → Agent 自动拼装物理实现 Flow
- ⚡ 全链路物理实现：Yosys 综合 → iEDA 物理设计（floorplan→GDS）→ iSTA 时序分析 → iDRC 物理验证
- 📋 前置阶段状态自动检查（Verible/Verilator/Icarus 必须全部通过）

### 对比实验
- 🔬 笛卡尔积展开：多 PDK × 多参数 → 批量执行 → 结果汇总表

### 通用功能
- 📁 右侧文件浏览器（查看/编辑/下载 AI 生成的所有文件）
- 🤖 全局 AI 助手（任何页面都能随时提问）
- 💾 自动保存（RTL / Testbench / SVA / 仿真结果）
- 🌓 深色主题 UI

## 技术栈

| 层 | 技术 |
|----|------|
| 前端 | Next.js 16 + React 18 + TypeScript + Tailwind + Monaco Editor |
| 后端 | FastAPI + WebSocket + JWT |
| AI | DeepSeek-V4 (Chat + RTL生成) + ChipMATE 交叉验证 |
| EDA | Icarus 11.0 / Verilator 4.038 / Yosys 0.68 / Verible / SymbiYosys 0.68 / iEDA / Netgen 1.5 |

## 项目结构

```
├── frontend/               # Next.js Web 前端 (localhost:3000)
│   └── src/app/
│       ├── stage1/         # 阶段1: RTL 设计
│       ├── stage2/         # 阶段2: 仿真验证
│       ├── stage3/         # 阶段3: 芯片实现
│       └── compare/        # 对比实验
├── server/                 # FastAPI 后端 (localhost:8000)
│   ├── api.py              # REST API (compose/run/files/chat)
│   ├── chat.py             # AI 聊天引擎 (多轮对话 + 项目知识库)
│   ├── agent_engine.py     # Agent Decision 规则引擎 (4维决策)
│   ├── sva_templates.py    # SVA 模板库 (RTL结构分析 + 模板匹配)
│   └── experiment_runner.py # 对比实验执行器 (笛卡尔积批量调度)
├── adapter/                # EDA 工具 Runner (统一接口)
│   ├── chipmate_runner.py  # ChipMATE (API + local 双后端)
│   ├── digital_runner.py   # Yosys 综合
│   ├── ieda_runner.py      # iEDA 物理设计
│   ├── icarus_runner.py    # Icarus 仿真
│   ├── verilator_runner.py # Verilator 编译
│   ├── verible_runner.py   # Verible 语法检查
│   ├── sby_runner.py       # SymbiYosys 形式验证
│   └── netgen_runner.py    # Netgen LVS
├── composer/               # Flow 拼装引擎 (ic_agent_os)
├── tools/                  # 贝叶斯优化等 (ic_agent_os)
└── docker/                 # Docker 镜像 + 安装脚本
```

## 要求

- Python ≥ 3.10
- Node.js ≥ 18
- EDA 工具：Icarus / Verilator / Yosys / Verible / SymbiYosys / Netgen（`sudo bash docker/setup_tools.sh` 一键安装）
- DeepSeek API Key（在页面右上角 ⚙️ 设置中配置）
