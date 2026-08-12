# format_bridge.py —— EDA 工具间产物格式桥接器
"""
解决 Flow Composer 的兼容性警告: 工具 A 输出 verilog,工具 B 需要 def,怎么办?

  1. Format Checker: 检查两个工具间产物格式是否匹配
  2. Bridge Registry: 注册格式转换规则
  3. Auto Bridge: 自动查找转换路径

用法:
  from format_bridge import FormatBridge
  bridge = FormatBridge()
  bridge.check_compatibility(tool_a, tool_b, stage_a, stage_b)
  bridge.suggest_bridge("verilog", "def")
"""
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from composer.tool_registry import (
    ToolInfo, StageCapability, ArtifactSpec, get_tool, get_tools_for_stage,
)


@dataclass
class BridgeRule:
    """格式转换规则"""
    from_format: str
    to_format: str
    description: str
    tool_needed: str = ""      # 需要的桥接工具
    lossy: bool = False        # 是否有信息损失
    reversible: bool = False   # 是否可逆


class FormatBridge:
    """EDA 产物格式桥接器。

    解决 Flow Composer 生成的 flow 中工具间格式不兼容的问题。
    """

    # 已知的格式转换规则
    BRIDGES: List[BridgeRule] = [
        BridgeRule("verilog", "liberty", "RTL 综合需要 liberty 库文件", lossy=True),
        BridgeRule("verilog", "sdc", "需要 SDC 约束文件", lossy=True),
        BridgeRule("verilog", "def", "需要 Yosys+OpenROAD/iEDA 做 floorplan", "Yosys+floorplan"),
        BridgeRule("verilog", "gds2", "需要完整的 PnR 流程", "RTL2GDS flow"),
        BridgeRule("def", "gds2", "需要 routing 步骤", "routing"),
        BridgeRule("def", "spef", "需要 RC 提取工具", "OpenRCX"),
        BridgeRule("liberty", "sdc", "两个独立文件，不需要转换", lossy=False),
        BridgeRule("odb", "def", "OpenROAD 内部格式转换", "OpenROAD", reversible=True),
        BridgeRule("def", "odb", "OpenROAD 内部格式转换", "OpenROAD", reversible=True),
    ]

    def check_compatibility(
        self, tool_a: ToolInfo, tool_b: ToolInfo,
        stage_a: str, stage_b: str,
    ) -> Tuple[bool, List[str]]:
        """检查工具 A 的输出是否能被工具 B 消费。

        Returns:
            (is_compatible, missing_formats)
        """
        sc_a = self._get_stage(tool_a, stage_a)
        sc_b = self._get_stage(tool_b, stage_b)

        if not sc_a or not sc_b:
            return False, ["阶段信息缺失"]

        outputs = {a.format for a in sc_a.outputs if a.required}
        inputs = {a.format for a in sc_b.inputs if a.required}

        missing = inputs - outputs
        if not missing:
            return True, []

        # 检查是否可以通过桥接解决
        bridgeable = []
        for m in missing:
            for bridge in self.BRIDGES:
                if bridge.from_format in outputs and bridge.to_format == m:
                    bridgeable.append(
                        f"{m} (可从 {bridge.from_format} 通过 {bridge.tool_needed or '手工'} 转换)"
                    )
                    break

        still_missing = [m for m in missing
                        if not any(b.to_format == m and b.from_format in outputs
                                  for b in self.BRIDGES)]

        if not still_missing:
            return True, bridgeable  # 可通过桥接解决

        return False, [f"{m} (无法从当前产物转换)" for m in still_missing] + bridgeable

    def suggest_bridge(self, from_format: str, to_format: str) -> List[BridgeRule]:
        """推荐从 from 到 to 的转换路径。"""
        # 直接转换
        direct = [b for b in self.BRIDGES
                  if b.from_format == from_format and b.to_format == to_format]
        if direct:
            return direct

        # 两跳转换
        suggestions = []
        for b1 in self.BRIDGES:
            if b1.from_format == from_format:
                for b2 in self.BRIDGES:
                    if b2.from_format == b1.to_format and b2.to_format == to_format:
                        suggestions.append(BridgeRule(
                            from_format=from_format, to_format=to_format,
                            description=f"两步: {b1.description} → {b2.description}",
                            tool_needed=f"{b1.tool_needed} + {b2.tool_needed}",
                        ))
        return suggestions

    def validate_flow_formats(self, steps) -> List[str]:
        """验证一个 flow 中所有步骤的格式兼容性。

        Args:
            steps: FlowStep 列表

        Returns:
            问题列表（空列表 = 全兼容）
        """
        issues = []
        for i in range(len(steps) - 1):
            a, b = steps[i], steps[i + 1]
            # 同工具跳过
            if a.primary_tool == b.primary_tool:
                continue

            tool_a = get_tool(a.primary_tool)
            tool_b = get_tool(b.primary_tool)
            if not tool_a or not tool_b:
                continue

            ok, details = self.check_compatibility(tool_a, tool_b, a.stage, b.stage)
            if not ok:
                issues.append(
                    f"[{a.stage}→{b.stage}] {a.primary_tool}→{b.primary_tool}: "
                    f"缺失格式 {details}"
                )

        return issues

    def _get_stage(self, tool: ToolInfo, stage: str) -> Optional[StageCapability]:
        for s in tool.stages:
            if s.stage == stage:
                return s
        return None
