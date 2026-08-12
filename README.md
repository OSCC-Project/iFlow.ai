# IC-Agent-OS

EDA 工具统一调用 + Flow 自动生成 + 迭代优化

## 快速开始

```bash
# 1. 检查环境
python3 setup_check.py

# 2. 安装依赖
pip install pyyaml jinja2

# 3. 跑第一个流程
python3 cli.py
# 回车 7 次 → GCD + sky130 + 开源 → synth+STA → 完成
```

## 命令

```bash
python3 cli.py                    # 交互式向导
python3 cli.py compose "低功耗,开源" --goals freq=200  # 只看方案
python3 cli.py status             # 工具状态
python3 cli.py history            # 历史记录
python3 tests/test_all.py         # 71 项自动化测试
```

## 项目结构

```
ic_agent_os/
├── cli.py                  # 用户入口
├── setup_check.py          # 环境检查
├── rtl/                    # 内置 RTL 设计
├── composer/               # Flow 引擎
├── adapter/                # EDA 适配器
├── state.py                # 快照存储
├── demo/                   # 演示 & 指南
└── tests/                  # 测试
```

## 要求

- Python ≥ 3.10
- Yosys (apt install yosys)
- OpenROAD (apt install openroad)
- pyyaml, jinja2
