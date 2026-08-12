# composer/replanner.py —— 代价感知的局部重跑规划
"""
给定一个参数变化，自动确定最小重跑步骤集，避免全量重跑。

对应文档 2.3 节: 代价感知的局部重跑

用法:
  from composer.replanner import Replanner
  r = Replanner()
  steps = r.plan_rerun("core_utilization", full_flow_steps)
  # → ['floorplan', 'tapcell', 'pdn', 'gplace', 'resize', 'dplace', 'cts', 'groute', 'droute']
  # (跳过了 synthesis — 网表没变)
"""
from typing import Dict, List, Optional, Tuple


class Replanner:
    """局部重跑规划器。

    核心数据结构: RERUN_MAP
      参数 → 受影响的最小步骤集合

    原则（文档 2.2 节）:
      从便宜的调整开始，成本越高的调整越靠后。
      L0: 调约束 (STA only, 秒级)
      L1: 调 tool 参数 (place+, 分钟)
      L2: 调 floorplan (floorplan+, 分钟)
      L3: 调 RTL (full flow, 小时)
    """

    # 参数 → 受影响的步骤 + 重跑成本级别
    RERUN_MAP: Dict[str, Tuple[int, List[str]]] = {
        # Level 0: 约束层 (秒级, STA only — STA 在 cts/droute 中)
        "clock_period":        (0, ["synthesis", "cts", "droute"]),
        "clock_uncertainty":   (0, ["cts", "droute"]),
        "input_delay":         (0, ["cts", "droute"]),
        "output_delay":        (0, ["cts", "droute"]),

        # Level 1: 布局参数 (分钟级, place+)
        "place_density":       (1, ["gplace", "dplace", "cts", "groute", "droute"]),
        "max_fanout":          (1, ["synthesis", "gplace", "dplace", "cts", "groute", "droute"]),
        "CLK_PERIOD":          (1, ["synthesis", "cts", "droute"]),

        # Level 2: floorplan 结构 (分钟级, fp+)
        "core_utilization":    (2, ["floorplan", "gplace", "resize", "dplace", "cts", "groute", "droute", "DRC"]),
        "aspect_ratio":        (2, ["floorplan", "gplace", "resize", "dplace", "cts", "groute", "droute", "DRC"]),
        "DIE_AREA":            (2, ["floorplan", "gplace", "resize", "dplace", "cts", "groute", "droute", "DRC"]),
        "CORE_AREA":           (2, ["floorplan", "gplace", "resize", "dplace", "cts", "groute", "droute", "DRC"]),

        # Level 3: RTL 修改 (小时, full flow)
        "rtl_change":          (3, ["synthesis", "floorplan", "gplace", "resize", "dplace", "cts", "groute", "droute", "DRC"]),
        "LIBERTY_PATH":        (3, ["synthesis", "droute"]),
    }

    LEVEL_NAMES = {
        0: "L0-约束调整 (秒级, STA only)",
        1: "L1-参数调整 (分钟, place+)",
        2: "L2-结构调整 (分钟, floorplan+)",
        3: "L3-RTL变更 (小时, full flow)",
    }

    def plan_rerun(self, param_name: str, full_steps: List[str]) -> Tuple[int, List[str]]:
        """给定参数变化, 返回 (cost_level, affected_steps)。

        Args:
            param_name: 参数名 (clock_period, core_utilization, ...)
            full_steps: 完整流程步骤列表

        Returns:
            (cost_level, [需要重跑的步骤])
        """
        if param_name in self.RERUN_MAP:
            level, affected = self.RERUN_MAP[param_name]
            # 过滤出 full_steps 中受影响的步骤
            rerun = [s for s in full_steps if any(a in s.lower() for a in affected)]
            return level, rerun

        # 未知参数 → 保守策略: 全量重跑
        return 3, list(full_steps)

    def explain(self, param_name: str, full_steps: List[str]) -> str:
        """人类可读的解释。"""
        level, rerun = self.plan_rerun(param_name, full_steps)
        skipped = [s for s in full_steps if s not in rerun]
        lines = [
            f"参数变更: {param_name}",
            f"成本级别: {self.LEVEL_NAMES.get(level, 'L3')}",
            f"需要重跑 ({len(rerun)} steps): {rerun}",
            f"可以跳过 ({len(skipped)} steps): {skipped}",
        ]
        return "\n".join(lines)

    def cheapest_first(self, params_to_try: List[str], full_steps: List[str]) -> List[Tuple[str, int, List[str]]]:
        """按成本从低到高排列参数调整方案。

        Args:
            params_to_try: 待尝试的参数列表
            full_steps: 完整步骤

        Returns:
            [(param, cost_level, affected_steps), ...] 按 cost 升序
        """
        plans = [(p, *self.plan_rerun(p, full_steps)) for p in params_to_try]
        plans.sort(key=lambda x: x[1])  # 按 level 升序
        return plans
