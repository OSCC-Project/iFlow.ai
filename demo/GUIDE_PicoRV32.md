# CLI 使用指南 — PicoRV32 自定义设计

> 自己提供的 RTL + 具体目标 + 完整流程

## 前置条件

```bash
# 下载 PicoRV32（开源 RISC-V CPU, 单文件 Verilog）
curl -o ~/picorv32.v https://raw.githubusercontent.com/YosysHQ/picorv32/main/picorv32.v
```

## 全程操作记录

```
$ python3 cli.py

第一步: 选择设计
  [0] 自定义设计  → 输入 0
  设计名          → picorv32
  示例: /home/xu/iFlow/rtl/gcd/gcd.v
  RTL 文件路径    → /home/xu/picorv32.v
  永久添加到列表?  → 回车（y）

第二步: 选择工艺
  [1] sky130      → 回车（默认）

第三步: 选择需求
  输入 1       → 开源 

第四步: 设计目标
  [1] 简单模式     → 回车
  频率 MHz        → 100
  面积上限 μm²    → 4050
  功耗上限 mW     → 2

第五步: 流程深度
  [1] 完整         → 回车（有面积/功耗目标，自动走完整流程）

═══════════════════════════════════════════
  Flow: Yosys → OpenROAD × 10 (12 步)
  1.  synthesis   Yosys
  2.  floorplan   OpenROAD
  3.  tapcell     OpenROAD
  4.  pdn         OpenROAD
  5.  gplace      OpenROAD
  6.  resize      OpenROAD  ← pre-CTS STA
  7.  dplace      OpenROAD
  8.  cts         OpenROAD  ← post-CTS STA
  9.  groute      OpenROAD
  10. droute      OpenROAD  ← signoff STA
  11. filler      OpenROAD
  12. gds         gds_runner
═══════════════════════════════════════════

第六步: 替换工具?
  回车 → 不替换

第七步: 执行模式
  [1] 执行一遍     → 输入 1
```

## 执行结果示例

```
第 1 轮: 探索轮 (synth + STA)
  ✅ synthesis  Yosys   6.5s   netlist: 1.0MB (picorv32 综合)
  ✅ STA        OpenSTA 0.3s   WNS=-12.0ns ← 时序不满足

第 2 轮: 全流程轮 (完整 12 步)
  ✅ floorplan  378ms   floorplan.def 1.1MB
  ✅ tapcell    322ms   tapcell.def
  ✅ pdn        333ms   pdn.def
  ✅ gplace     319ms   gplace.def
  ✅ dplace     25s     详细布局
  ✅ cts        26s     时钟树 + STA WNS=-12.0
  ✅ groute     436ms   groute.def
  ✅ droute     1.0s    详细布线 + STA WNS=-12.0
  ✅ filler     300ms   filler.def
  ✅ gds        2s      GDS2 输出

❌ 时序未满足 (WNS=-12.0ns < 0)
⏭️  Adapter 职责到此为止 — 优化和收敛交给 Optimizer
```

## 结果文件位置

| 文件 | 路径 |
|------|------|
| 综合网表 | `tmp/digital_runs/<uuid>/output/PICORV32_synth.v` |
| floorplan DEF | `tmp/openroad_runs/<uuid>/output/floorplan.def` |
| 最终 DEF | `tmp/openroad_runs/<uuid>/output/droute.def` |
| STA 报告 | `tmp/openroad_runs/<uuid>/output/timing.rpt` |
| GDS2 | `tmp/gds_runs/<uuid>/picorv32.gds` |
| 历史记录 | `outputs/state/state.db` → `python3 cli.py history` |

## 后续

WNS=-12.0 说明 100MHz 对 PicoRV32 太紧。Optimizer 可以尝试：降频到 50MHz、增大 die_area、换 SLOW corner lib。这些不是 Adapter 的职责——Adapter 只负责调工具、报结果、搭流程。
