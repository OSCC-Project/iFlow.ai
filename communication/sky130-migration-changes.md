# Sky130 Migration — 容器适配修改记录

> 以 `ic_agent_os_v1.1_20260716.tar.gz` 为基础，将 `/home/xu/` 路径替换为容器的 `/opt/iFlow/foundry/sky130/` 路径。

## 修改文件

### 1. `cli.py` — PDK 路径适配（7 处）

将合作者硬编码的个人路径替换为容器实际路径：

| 用途 | 旧值 | 新值 |
|------|------|------|
| Yosys LIBERTY | `/home/xu/.../sky130hd_tt.lib` | `/opt/iFlow/foundry/sky130/lib/sky130_fd_sc_hd__tt_025C_1v80.lib` |
| OpenROAD LIBERTY | 同上 | 同上 |
| OpenSTA LIBERTY | 同上 | 同上 |
| SDC 文件 (全部 4 处) | `/home/xu/.../gcd_sky130hd.sdc` | `_resolve_rtl("rtl/gcd.sdc")` |
| 网表 fallback (2 处) | `/home/xu/.../gcd_sky130hd.v` | `""` (由 Yosys 动态产出) |
| SLOW corner lib | `/home/xu/.../ss_n40C_1v40.lib` | `hd__tt_025C` (容器无 SS lib, 用 TT 暂代) |
| FAST corner lib | `/home/xu/.../ff_n40C_1v95.lib` | `hd__tt_100C` (容器无 FF lib, 用高温 TT 暂代) |

### 2. `composer/flow_composer.py` — 合并 run_history 历史反馈闭环（6 处新增）

在 v1.1 基础上保留旧版的"历史反馈闭环"模块：

| 位置 | 修改 |
|------|------|
| `compose()` 签名 | 新增 `history` 和 `diagnosis` 两个可选参数 |
| `compose()` 步骤选择 | 新增诊断驱动步骤裁剪 (`suggest_final()` / `suggest_demo()`) |
| `_select_tool_for_stage()` 签名 | 新增 `history` 参数 |
| `_select_tool_for_stage()` 评分调用 | `_score_tool()` 传入 `history=history` |
| `_score_tool()` 签名 | 新增 `history` 参数 |
| `_score_tool()` 返回前 | 新增历史数据调整: `score *= (0.5 + 0.5 * success_rate)` |

### 3. `adapter/run_history/` — 保留（v1.1 无此模块，从旧版恢复）

6 个文件：`schema.py`, `recorder.py`, `querier.py`, `recommender.py`, `report.py`, `__init__.py`

### 4. `work_log/` — 保留（v1.1 无此目录，从旧版恢复）

## 为什么改？

1. **`/home/xu/` 路径不存在于目标容器** — 合作者 v1.1 的路径基于个人开发机，容器环境是 iFlow Docker (WSL2)，PDK 统一在 `/opt/iFlow/foundry/`
2. **SDC 文件统一用 `_resolve_rtl()`** — 避免硬编码绝对路径，提高可移植性
3. **run_history 是之前在容器中开发的已有功能** — 历史反馈闭环对 Flow 自动选择有价值，需要保留
4. **多 corner lib 暂缺** — 容器内 `sky130_fd_sc_hd` 只有 TT corner (25°C / 100°C)，真正的 SS/FF corner lib 需要单独添加

## 验证

```
✅ Yosys synthesis → 成功 (724ms, 输出 45KB netlist)
✅ OpenSTA STA   → 成功 (179ms)
✅ 70/71 测试通过 (1 个失败是 OpenROAD 执行测试的预存问题)
```

## 容器环境

| 项 | 值 |
|----|-----|
| 容器 ID | `8b62a6201420` |
| Python | 3.10.14 |
| PDK 路径 | `/opt/iFlow/foundry/sky130/` |
| 可用 lib | `hd__tt_025C_1v80`, `hs__tt_025C_1v80`, `hs__tt_100C_1v80` |
| LEF | `sky130_fd_sc_hd_merged.lef`, `sky130_fd_sc_hs_merged.lef` |
| 项目路径 | `/opt/siliconcompiler/ic_agent_os/ic_agent_os/` |
