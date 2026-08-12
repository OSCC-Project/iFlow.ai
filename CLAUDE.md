# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# 全量自动化测试 (71 项)
python3 tests/test_all.py

# 完整功能演示 (8 场景)
python3 demo/demo_full.py

# 端到端 Flow (需求→执行→替换→DIFF 链传→迭代)
python3 demo/demo_flow_e2e.py

# CLI 交互式向导
python3 cli.py

# CLI 命令模式
python3 cli.py compose "低功耗,开源" -d gcd --goals freq=200 area=100k
python3 cli.py run "开源" -d gcd --rtl /path/to/design.v
python3 cli.py swap STA OpenSTA -d gcd
python3 cli.py status
python3 cli.py history
python3 cli.py clear -y
```

## Architecture

Three-layer design:

```
用户需求 → composer/ (FlowComposer: 需求→工具选择→方案)
       → adapter/ (统一调用 6 个 EDA 后端, 返回 SnapshotPackage v1.0)
       → state.py (SQLite 8 表 + JSON 持久化)
```

| 层 | 目录 | 核心文件 | 职责 |
|---|---|---|---|
| Flow 引擎 | `composer/` | `flow_composer.py`, `tool_registry.py` | 需求→7步流程, 工具评分, 替换 |
| 执行层 | `adapter/` | `adapter.py`, `*_runner.py`, `contract.py` | 子进程调 EDA, 构建 SnapshotPackage |
| 存储层 | `state.py` | — | SQLite + JSON, 查询历史 |

`tools/` 是辅助模块 (沙箱/贝叶斯优化/iSTA 验证).  
`param_bridge.py` 把用户目标 (freq=200) 翻译成工具参数 (CLK_PERIOD=5.0ns).

## EDA Tool Status (verified 2026-07-14)

| Tool | Adapter | Status | Notes |
|---|---|---|---|
| Yosys | `digital` | ✅ rc=0 | `/usr/bin/yosys`, RTL→netlist |
| OpenROAD | `openroad` | ✅ rc=0, WNS=0.23 | `/usr/bin/openroad`, PDK at `/home/xu/OpenROAD-ae191807/test/sky130hd/` |
| OpenSTA | `opensta` | ✅ rc=0, WNS=0.23 | `/usr/bin/sta` (bundled with OpenROAD) |
| iEDA | `ieda` | ✅ subprocess mode | multiprocessing isolation for C++ crash, `ieda_py` at `/home/xu/iEDA/bin/ieda_py.*.so` |
| ngspice | `analog` | ❌ not installed | `apt install ngspice` |
| PrimeTime | `primetime` | ❌ needs license | code ready, binary missing |
| Design Compiler | — | ❌ no adapter | Tool Registry only |
| Innovus | — | ❌ no adapter | Tool Registry only |
| Calibre | — | ❌ no adapter | Tool Registry only |

**Available RTL designs**: gcd (757 lines), aes_cipher_top (253 lines), uart (113 lines) at `/home/xu/iFlow/rtl/`. iEDA sky130_gcd test case at `/home/xu/iEDA/scripts/design/sky130_gcd/`.

## Contract v1.0 — Do NOT change without updating State

`adapter/contract.py` aligns with Unified Contract v1.0. Key fields:

- `observation_level`: `"0"`=artifact, `"1"`=metric, `"2"`=object, `"3"`=execution (数字字符串, 不是英文)
- `Capability.object_delta` (not `object`), `Capability.execution_trace` (not `execution`)
- `SnapshotHeader` includes `design_name`, `design_type`, `schema_version="1.0"`
- `TracePoint` replaces `ExecutionTraceEntry`, adds `command`/`parameters`/`duration_ms`/`trigger`
- `DigitalTwin.design: DesignInfo`, `DesignObject.master`
- `SnapshotPackage.optimizer_hints`
- `adapter/snapshot_builder.py` is the single source for building these.

State SQLite uses these exact column names. If you rename any field in contract.py, you must also update state.py.

## Tool Scoring Weights

`composer/flow_composer.py` `_score_tool()`: 50 base + quality×weight + speed×weight + open×weight + compat + obs. Weights vary by `UserPriority` (LOW_POWER/AREA_OPT/SIGN_OFF/AI_TRAINING etc). Specific requirements (signoff/low_power) are ordered before generic preferences (open_source). `composer/goals.py` `PPASpec` handles structured PPA constraints. `composer/replanner.py` maps parameter changes to minimal rerun steps (L0 seconds to L3 hours).

## Known Issues & Workarounds

- **ieda_py C++ exit crash**: `adapter/ieda_runner.py` uses `multiprocessing.Process` + `os._exit(0)` to isolate. Subprocess mode is the default and works fine.
- **OpenROAD multi-step**: Each step is a separate `openroad` process. DEF chaining requires reading previous step's DEF via `INPUT_DEF`. `demo/demo_flow_e2e.py` shows correct incremental flow.
- **OpenROAD PDK**: Must use tech LEF before cell LEF, source vars before tracks. Correct config at `adapter/config.yaml`.
- **Yosys without liberty**: skips abc mapping, uses generic techmap cells. For real sky130 cells, pass `LIBERTY_PATH`.
- **metric_define.yaml**: unknown circuits use `_default` rules (WNS/TNS/leakage/area), no metric_error.

## Development Rules — Read Before Editing Any File

1. **Read before Edit** — never Edit a file you haven't Read this session. The linter may have changed it.
2. **Check syntax immediately** — `python3 -c "compile(open('file.py').read(),'file.py','exec')"` after every edit.
3. **Run full test suite** — `python3 tests/test_all.py` (71 tests, ~2s). Must pass before responding to user.
4. **Rewrite large blocks, don't patch** — use Read→Write for function-sized changes. Avoid chaining sed+Edit on the same file. Each incremental edit has ~30% failure rate from changed file content.
5. **No dead code** — after major refactors, search for duplicate `adapter.run` blocks or orphaned execution loops. Delete them.
6. **12-step flow** (not 7): synthesis→floorplan→tapcell→pdn→gplace→resize→dplace→cts→groute→droute→filler→gds
7. **CLI logic lives in `interactive()`** — all execution flow is there. `_STAGE_FLOWS` maps stages→OpenROAD substeps. `_STAGE_ADAPTER` maps stages→adapter names.
8. **Contract v1.0 changes must sync** — contract.py + snapshot_builder.py + state.py must all be updated together.
9. **Common paths**: RTL at `/home/xu/iFlow/rtl/`, PDK at `/home/xu/OpenROAD-ae191807/test/sky130hd/`, corner libs at `*_tt_*.lib`, `*_ss_*.lib`, `*_ff_*.lib`
