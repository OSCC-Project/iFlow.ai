// 知识卡片 (方案 3.5 右栏第一区): 按阶段上下文切换的轻量知识点
export type KnowledgeCard = { id: string; stage: number[]; title: string; body: string }

export const KNOWLEDGE: KnowledgeCard[] = [
  { id: 'rtl', stage: [1], title: '什么是 RTL', body: 'RTL (寄存器传输级) 是用 Verilog/SystemVerilog 描述硬件电路"每个时钟沿做什么"的语言。写 RTL 时心里要有一张电路图: 时序逻辑用 always @(posedge clk), 组合逻辑用 assign 或 always @(*)。' },
  { id: 'timing-tpl', stage: [1], title: '时序逻辑标准模板', body: '同步复位时序逻辑:\n  always @(posedge clk) begin\n    if (!rst_n) q <= 复位值;\n    else if (en) q <= q + 1;\n  end\n非阻塞赋值 <= 保证先算后存, 模拟真实寄存器的行为。' },
  { id: 'cov', stage: [2], title: '覆盖率是什么', body: '覆盖率衡量"测试激励走了多少代码": Line=每行语句执行过没有; Toggle=每个信号翻转过没有; Branch=每个 if/else 分支走过没有; FSM=状态机每个状态/跳转到达过没有。覆盖率低 ≠ 一定有问题, 但没覆盖的角落最可能藏 bug。' },
  { id: 'sva', stage: [2], title: 'SVA 断言入门', body: '断言 (Assertion) 是写在 RTL 里的"性质声明", 例如"计数器不能超过 15"。形式验证工具 (SymbiYosys) 用数学方法穷举证明性质永远成立——比仿真更强: 仿真只能证明"测过的情况对", 形式验证能证明"所有情况都对"。' },
  { id: 'wave', stage: [2], title: '波形怎么看', body: '波形横轴是时间, 每根信号轨道显示该信号随时间的变化。阶梯形状 = 信号保持直到下一拍才跳变 (寄存器的实际行为)。调试三步: ① 复位段输出是不是复位值; ② 使能沿处是否跳变; ③ 输出和预期相差几拍。' },
  { id: 'synth', stage: [3], title: '综合 (Synthesis)', body: '综合把 RTL 翻译成标准单元门级网表 (与工艺库绑定)。关键输出: 面积 (Chip area) 和时序初估。Yosys 的开源流程: read_verilog → proc/opt → techmap → abc (工艺映射) → 网表。' },
  { id: 'timing', stage: [3], title: '时序收敛与 WNS', body: 'WNS (最差负松弛) < 0 表示最长路径慢于时钟周期, 触发器会采错数据。收敛手段: 降频 (加大周期)、换更快的库、优化综合策略、物理设计时减小拥塞。平台收敛循环就是自动做这件事: 诊断 → 降频/降密度 → 重跑 → 再检查。' },
  { id: 'drc', stage: [3], title: 'DRC 违例是什么', body: 'DRC (设计规则检查) 检查版图是否违反工艺厂制造的物理规则 (最小间距、最小线宽、密度)。违例意味着芯片造不出来或良率受损。常见修复: 降低布局密度、增加布线迭代、退回 placement 重新布。' },
  { id: 'ppa', stage: [1, 2, 3], title: 'PPA 三要素', body: '芯片设计的三个目标: Performance (频率/性能)、Power (功耗)、Area (面积)。三者互相制约: 追求性能往往牺牲面积和功耗。对比实验的本质就是换变量 (PDK/利用率/频率) 看 PPA 怎么变化。' },
]

export function cardsForStage(stage: number): KnowledgeCard[] {
  return KNOWLEDGE.filter(c => c.stage.includes(stage))
}
