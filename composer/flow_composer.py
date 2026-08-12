#!/usr/bin/env python3
"""
flow_composer.py —— Flow 组合引擎

根据用户需求自动:
  1. 编排流程步骤
  2. 为每一步选择最合适的 EDA 工具
  3. 验证步骤间兼容性
  4. 提供备选方案 & 替换建议
  5. 解释推荐理由

用法:
  from flow_composer import FlowComposer
  composer = FlowComposer()
  flow = composer.compose(
      design="gcd",
      technology="sky130",
      requirements=["快速原型", "开源"],
      goals={"frequency": 100, "area_min": True},
  )
  print(composer.explain(flow))          # 人类可读的解释
  print(composer.list_alternatives(flow, step="synthesis"))  # 可选替换
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from enum import Enum

from composer.tool_registry import (
    TOOL_REGISTRY, ToolInfo, StageCapability, ArtifactSpec,
    get_tools_for_stage, get_tool,
)


class UserPriority(Enum):
    SPEED = "speed"           # 快速迭代
    QUALITY = "quality"       # 追求 PPA
    OPEN_SOURCE = "open"      # 只用开源
    RELIABILITY = "reliability"  # 最可靠
    LEARNING = "learning"     # 新手友好
    LOW_POWER = "low_power"   # 低功耗优化
    AREA_OPT = "area_opt"     # 面积优化
    SIGN_OFF = "sign_off"     # tape-out 签核质量
    AI_TRAINING = "ai_training"  # 需要执行轨迹供AI训练


# 需求关键词 → UserPriority 映射
REQUIREMENT_MAP = {
    # 开源/商业
    "开源": UserPriority.OPEN_SOURCE, "open_source": UserPriority.OPEN_SOURCE,
    "免费": UserPriority.OPEN_SOURCE, "free": UserPriority.OPEN_SOURCE,
    # 速度
    "快速": UserPriority.SPEED, "原型": UserPriority.SPEED,
    "fast": UserPriority.SPEED, "quick": UserPriority.SPEED,
    "迭代": UserPriority.SPEED, "敏捷": UserPriority.SPEED,
    # 质量
    "极致": UserPriority.QUALITY, "高性能": UserPriority.QUALITY,
    "最佳": UserPriority.QUALITY, "ppa": UserPriority.QUALITY,
    # 低功耗
    "低功耗": UserPriority.LOW_POWER, "low_power": UserPriority.LOW_POWER,
    "省电": UserPriority.LOW_POWER, "power_opt": UserPriority.LOW_POWER,
    # 面积优化
    "面积": UserPriority.AREA_OPT, "area": UserPriority.AREA_OPT,
    "缩小": UserPriority.AREA_OPT, "小型化": UserPriority.AREA_OPT,
    # 签核
    "签核": UserPriority.SIGN_OFF, "tape_out": UserPriority.SIGN_OFF,
    "signoff": UserPriority.SIGN_OFF, "量产": UserPriority.SIGN_OFF,
    # AI训练
    "ai训练": UserPriority.AI_TRAINING, "训练数据": UserPriority.AI_TRAINING,
    "execution_trace": UserPriority.AI_TRAINING, "轨迹": UserPriority.AI_TRAINING,
    # 可靠性
    "稳定": UserPriority.RELIABILITY, "可靠": UserPriority.RELIABILITY,
    "商用": UserPriority.RELIABILITY, "production": UserPriority.RELIABILITY,
    # 新手
    "新手": UserPriority.LEARNING, "入门": UserPriority.LEARNING,
    "学习": UserPriority.LEARNING, "教学": UserPriority.LEARNING,
}


@dataclass
class FlowStep:
    """Flow 中的一个步骤"""
    id: str                                    # 步骤 ID (synthesis / floorplan / ...)
    stage: str                                 # 阶段名
    primary_tool: str                          # 首选工具名
    alternatives: List[str] = field(default_factory=list)  # 备选工具
    tool_info: Optional[ToolInfo] = None       # 工具详细信息
    reason: str = ""                           # 选择理由
    inputs: List[ArtifactSpec] = field(default_factory=list)
    outputs: List[ArtifactSpec] = field(default_factory=list)
    parameters: Dict[str, str] = field(default_factory=dict)


@dataclass
class ComposedFlow:
    """组合完成的 Flow"""
    name: str                                  # Flow 名称
    description: str                           # Flow 描述
    design: str                                # 设计名称
    technology: str                            # 工艺节点
    phase: str = "full_run"                    # explore | full_run | fix | eco | sign_off
    steps: List[FlowStep] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)

    def get_step(self, stage: str) -> Optional[FlowStep]:
        for s in self.steps:
            if s.stage == stage:
                return s
        return None

    def summary(self) -> str:
        """一行摘要"""
        tools = " → ".join(s.primary_tool for s in self.steps)
        return f"[{self.name}] {tools}"


class FlowComposer:
    """Flow 组合引擎。

    工作流程:
      1. 解析用户需求 → 确定优先策略
      2. 根据设计类型匹配 Flow 模板
      3. 为每个步骤评分 & 选择最佳工具
      4. 验证工具链兼容性
      5. 生成替换建议
    """

    # 数字 IC 完整流程 (12 步)
    DIGITAL_STAGES = [
        "synthesis",           # 1. RTL → 门级网表
        "floorplan",           # 2. 芯片面积 + IO 摆放
        "tapcell",             # 3. 插入 tapcell
        "pdn",                 # 4. 电源网络
        "gplace",              # 5. 全局布局
        "resize",              # 6. 门级缩放 + pre-CTS STA ← 反馈点
        "dplace",              # 7. 详细布局
        "cts",                 # 8. 时钟树综合 + post-CTS STA ← 反馈点
        "groute",              # 9. 全局布线
        "droute",              # 10. 详细布线 + signoff STA ← 反馈点
        "filler",              # 11. 填充单元
        "gds",                 # 12. GDS 输出
    ]

    DIGITAL_LITE_STAGES = ["synthesis", "STA"]

    # 模拟电路流程
    ANALOG_STAGES = [
        "simulation",
    ]

    def __init__(self):
        # 确保 tool_registry 已初始化
        pass

    # ═══════════════════════════════════════════════════════════
    # 主入口：根据需求组合 Flow
    # ═══════════════════════════════════════════════════════════
    def compose(
        self,
        design: str,
        technology: str = "sky130",
        design_type: str = "digital",
        requirements: Optional[List[str]] = None,
        goals: Optional[Dict] = None,
        ppa_spec = None,  # PPASpec from composer.goals (文档2.1: 声明目标,不声明步骤)
        preferred_tools: Optional[List[str]] = None,
        excluded_tools: Optional[List[str]] = None,
        fast_mode: bool = False,
        history = None,     # FlowRecommender — adjusts tool scores with historical data
        diagnosis = None,   # AnalysisReport or dict — demo diagnosis for final flow
    ) -> ComposedFlow:
        """根据用户需求组合一个 Flow。支持三种模式:

        模式1 (关键词): compose(design="gcd", requirements=["低功耗","开源"])
        模式2 (目标驱动): compose(design="gcd", ppa_spec=PPASpec(timing={wns:">0"},...))
        模式3 (历史增强): compose(..., history=recommender, diagnosis=report)

        模式1 (关键词): compose(design="gcd", requirements=["低功耗","开源"])
        模式2 (目标驱动): compose(design="gcd", ppa_spec=PPASpec(timing={wns:">0"},...))

        Args:
            design: 设计名称
            technology: 工艺
            design_type: digital | analog
            requirements: 需求关键词
            goals: 设计目标 {"frequency": 100, ...}
            ppa_spec: PPASpec 目标规格 (文档2.1: 声明目标,不声明步骤)
            preferred_tools: 偏好工具
            excluded_tools: 排除工具
            fast_mode: 精简模式
            history: FlowRecommender 实例 (可选, 用于历史数据驱动工具评分)
            diagnosis: demo 诊断报告 (可选, 用于final flow步骤裁剪)
        """
        # ── 统一: PPA Goal 和 keywords 合并，目标 > 关键词 ──
        # 关键词 → 推导默认目标; 用户传的具体 goals → 覆盖默认值
        if ppa_spec is not None:
            # PPASpec → 提取目标约束, 推导关键词(可选)
            derived_reqs = []
            if ppa_spec.timing.wns and ppa_spec.timing.wns.value >= 0:
                derived_reqs.append("签核")
            if ppa_spec.power.total:
                derived_reqs.append("低功耗")
            if ppa_spec.area.utilization:
                derived_reqs.append("面积")
            if requirements is None:
                requirements = derived_reqs
            if not goals:
                goals = {}
            # PPASpec 约束合并到 goals (不覆盖用户显式传的)
            if ppa_spec.timing.fmax and "frequency" not in goals:
                goals["frequency"] = ppa_spec.timing.fmax.value
            if ppa_spec.area.cell_area and "area_max" not in goals:
                goals["area_max"] = ppa_spec.area.cell_area.value
            if ppa_spec.power.total and "power_max" not in goals:
                goals["power_max"] = ppa_spec.power.total.value
            if ppa_spec.routing.congestion_max and "congestion_max" not in goals:
                goals["congestion_max"] = ppa_spec.routing.congestion_max.value
            if ppa_spec.timing.wns and "wns" not in goals:
                goals["wns"] = ppa_spec.timing.wns.value
            if ppa_spec.routing.drc_violations and "drc" not in goals:
                goals["drc"] = 0

        requirements = requirements or ["开源"]
        goals = goals or {}

        # 1. 确定策略: 关键词 → 评分权重偏好
        priorities = self._determine_priorities(requirements, design_type)
        primary_priority = priorities[0] if priorities else UserPriority.OPEN_SOURCE
        secondary_priorities = priorities[1:] if len(priorities) > 1 else []

        # 2. 选择阶段: 诊断 → 裁剪步骤, 历史 → 建议深度
        phase = "explore" if fast_mode else "full_run"
        stages = self._select_stages(design_type, fast_mode, goals, phase)

        # ── 历史驱动: 诊断报告 → 步骤裁剪 + 深度建议 ──
        skip_steps = set()
        if diagnosis and history:
            final_advice = history.suggest_final(
                design, technology, goals, demo_diagnosis=diagnosis
            )
            if final_advice.recommended_depth == "lite":
                fast_mode = True
            skip_steps = set(final_advice.suggested_skip_steps)
        elif history and not diagnosis:
            demo_advice = history.suggest_demo(
                design, technology, goals, requirements=requirements
            )
            skip_steps = set()
        stages = [s for s in stages if s not in skip_steps]

        # 3. 为每个阶段选择工具（多维度评分）
        steps = []
        warnings = []
        prev_tool = None
        for stage in stages:
            step = self._select_tool_for_stage(
                stage, primary_priority, preferred_tools, excluded_tools,
                prev_tool=prev_tool,
                secondary_priorities=secondary_priorities,
                history=history,
            )
            if step:
                steps.append(step)
                prev_tool = step.primary_tool
            else:
                warnings.append(f"[{stage}] 没有找到可用的工具 (priority={primary_priority.value})")

        # 4. 验证兼容性
        compat_warnings = self._validate_compatibility(steps)
        warnings.extend(compat_warnings)

        # 5. 生成推荐
        recommendations = self._generate_recommendations(
            steps, primary_priority, goals, warnings
        )

        # 6. 命名
        mode = "Lite" if fast_mode else "Full"
        label_map = {"open": "Open", "quality": "Quality", "speed": "Fast",
                     "learning": "Learn", "reliability": "Reliable",
                     "low_power": "LowPower", "area_opt": "AreaOpt",
                     "sign_off": "SignOff", "ai_training": "AI-Train"}
        label = label_map.get(primary_priority.value, primary_priority.value)
        name = f"{design}_{technology}_{label}_{mode}"

        return ComposedFlow(
            name=name,
            description=self._describe_flow(design_type, primary_priority, fast_mode),
            design=design, technology=technology, phase=phase,
            steps=steps, warnings=warnings, recommendations=recommendations,
        )

    # ═══════════════════════════════════════════════════════════
    # 策略推断
    # ═══════════════════════════════════════════════════════════
    def _determine_priorities(
        self, requirements: List[str], design_type: str
    ) -> List[UserPriority]:
        """从需求列表提取多个优先级维度。

        例如 ["低功耗", "开源", "快速"] → [LOW_POWER, OPEN_SOURCE, SPEED]
        具体需求（签核/低功耗/AI训练）优先于通用偏好（开源/快速）
        """
        found = []
        for req in requirements:
            req_lower = req.lower().strip()
            if req_lower in REQUIREMENT_MAP:
                found.append(REQUIREMENT_MAP[req_lower])
            else:
                for keyword, pri in REQUIREMENT_MAP.items():
                    if keyword in req_lower or req_lower in keyword:
                        found.append(pri)
                        break

        if not found:
            found = [UserPriority.OPEN_SOURCE]

        # 去重
        seen = set()
        result = []
        for p in found:
            if p not in seen:
                seen.add(p)
                result.append(p)

        # 排序：具体需求优先于通用偏好
        SPECIFIC = {UserPriority.SIGN_OFF, UserPriority.LOW_POWER,
                    UserPriority.AREA_OPT, UserPriority.AI_TRAINING,
                    UserPriority.QUALITY, UserPriority.RELIABILITY}
        specific = [p for p in result if p in SPECIFIC]
        generic = [p for p in result if p not in SPECIFIC]
        return specific + generic

    def _determine_priority(
        self, requirements: List[str], design_type: str
    ) -> UserPriority:
        """兼容旧接口：返回主要优先级"""
        priorities = self._determine_priorities(requirements, design_type)
        return priorities[0] if priorities else UserPriority.OPEN_SOURCE

    def _select_stages(
        self, design_type: str, fast_mode: bool, goals: Dict, phase: str = "full_run"
    ) -> List[str]:
        if design_type == "analog":
            return self.ANALOG_STAGES
        # explore: 快速探索, 只跑 synth + droute (STA)
        if phase == "explore" or fast_mode:
            return self.DIGITAL_LITE_STAGES
        # fix/eco: 增量修复, 从上次失败处开始（由 Replanner 决定）
        if phase in ("fix", "eco"):
            return self.DIGITAL_LITE_STAGES  # Replanner 会裁减
        # sign_off: 完整流程 + 多 corner (暂不支持多 corner, 退化为 full)
        # full_run: 完整 9 步
        if goals.get("_force_full"):
            return list(self.DIGITAL_STAGES)
        if not goals:
            return self.DIGITAL_LITE_STAGES
        has_phys = any(k in goals for k in [
            "area_max", "area_min", "utilization", "die_area_max",
            "core_area", "congestion_max", "drc", "wirelength",
            "power_max", "power_min", "leakage_max",
        ])
        if has_phys:
            return list(self.DIGITAL_STAGES)
        return self.DIGITAL_LITE_STAGES

    # ═══════════════════════════════════════════════════════════
    # 工具选择引擎
    # ═══════════════════════════════════════════════════════════
    def _select_tool_for_stage(
        self,
        stage: str,
        priority: UserPriority,
        preferred: Optional[List[str]],
        excluded: Optional[List[str]],
        prev_tool: Optional[str] = None,
        secondary_priorities: List[UserPriority] = None,
        history = None,
    ) -> Optional[FlowStep]:
        """为某个阶段选择最佳工具（多维度联合评分）。

        评分策略:
          - 根据 priority 对不同维度加权
          - OPEN_SOURCE: 开源权重高, quality/商业工具降权
          - QUALITY: quality 权重最高, 商业工具优先
          - SPEED: speed 权重高
          - LEARNING: 简单易用优先
        """
        candidates = get_tools_for_stage(stage)
        if not candidates:
            return None

        excluded = excluded or []
        candidates = [t for t in candidates if t.name not in excluded]

        # 如果指定了偏好工具
        if preferred:
            for p in preferred:
                for t in candidates:
                    if t.name == p:
                        # 直接选择偏好工具
                        sc = self._get_stage_cap(t, stage)
                        return self._build_step(stage, t, sc, "用户偏好指定")

        # 多维度评分
        secondaries = secondary_priorities or []
        scored = []
        for tool in candidates:
            score = self._score_tool(tool, stage, priority, prev_tool, history=history)
            for sp in secondaries:
                score += self._score_tool(tool, stage, sp, prev_tool, history=history) * 0.5
            scored.append((tool, score))

        scored.sort(key=lambda x: x[1], reverse=True)

        if not scored:
            return None

        best_tool, best_score = scored[0]
        alternatives = [t.name for t, s in scored[1:4]]  # 前 3 备选
        sc = self._get_stage_cap(best_tool, stage)

        # 生成选择理由（覆盖主+副优先级）
        reasons = []
        if best_tool.is_open_source:
            reasons.append("开源免费")
        if best_tool.license_required:
            reasons.append("商业黄金标准")
        if best_tool.observation.get("object"):
            reasons.append("object级观测")
        if best_tool.observation.get("execution"):
            reasons.append("execution轨迹可用")

        all_pri = [priority] + secondaries
        if UserPriority.LOW_POWER in all_pri:
            reasons.append("适合低功耗场景")
        if UserPriority.AREA_OPT in all_pri:
            reasons.append("面积优化能力强")
        if UserPriority.SIGN_OFF in all_pri:
            reasons.append("签核级可靠性")
        if UserPriority.AI_TRAINING in all_pri and best_tool.observation.get("execution"):
            reasons.append("可提供AI训练数据")
        if best_tool.recommended_for:
            reasons.append(f"适合: {', '.join(best_tool.recommended_for[:2])}")

        return self._build_step(stage, best_tool, sc, "; ".join(reasons), alternatives)

    def _score_tool(
        self, tool: ToolInfo, stage: str, priority: UserPriority,
        prev_tool: Optional[str] = None,
        history = None,
    ) -> float:
        """评分一个工具在某个阶段的适配度（支持多维度需求 + 历史数据调整）。"""
        sc = self._get_stage_cap(tool, stage)
        if not sc:
            return 0.0

        score = 50.0

        quality_map = {"low": 10, "medium": 30, "high": 50, "highest": 70}
        speed_map = {"fast": 30, "medium": 15, "slow": 5}
        quality_bonus = quality_map.get(sc.quality, 20)
        speed_bonus = speed_map.get(sc.speed, 10)

        # ── 多维度权重 ──
        weights = {
            UserPriority.QUALITY:      {"quality": 1.5, "speed": 0.3, "open": 0.1, "reliability": 0.8},
            UserPriority.SPEED:        {"quality": 0.3, "speed": 1.5, "open": 0.3, "reliability": 0.3},
            UserPriority.OPEN_SOURCE:  {"quality": 0.5, "speed": 0.7, "open": 1.5, "reliability": 0.5},
            UserPriority.LEARNING:     {"quality": 0.3, "speed": 0.5, "open": 1.5, "reliability": 0.3},
            UserPriority.RELIABILITY:  {"quality": 1.0, "speed": 0.3, "open": 0.3, "reliability": 1.5},
            UserPriority.LOW_POWER:    {"quality": 0.8, "speed": 0.5, "open": 0.5, "reliability": 0.5,
                                       "power": 2.0},
            UserPriority.AREA_OPT:     {"quality": 0.8, "speed": 0.5, "open": 0.5, "reliability": 0.5,
                                       "area": 2.0},
            UserPriority.SIGN_OFF:     {"quality": 2.0, "speed": 0.1, "open": 0.0, "reliability": 2.0},
            UserPriority.AI_TRAINING:  {"quality": 0.5, "speed": 0.3, "open": 1.0, "reliability": 0.3,
                                       "execution": 2.0},
        }
        w = weights.get(priority, weights[UserPriority.OPEN_SOURCE])

        score += quality_bonus * w.get("quality", 0.5)
        score += speed_bonus * w.get("speed", 0.5)
        if tool.is_open_source:
            score += 40 * w.get("open", 0.5)
        else:
            score += 20 * w.get("open", 0.3)

        # 低功耗/面积特别加分
        if priority in (UserPriority.LOW_POWER, UserPriority.AREA_OPT) and sc.quality == "high":
            score += 20
        if priority == UserPriority.LOW_POWER and any("power" in r.lower() or "低功耗" in r for r in tool.recommended_for):
            score += 15
        if priority == UserPriority.SIGN_OFF and sc.quality == "highest":
            score += 30
        if priority == UserPriority.AI_TRAINING and tool.observation.get("execution"):
            score += 25

        # 兼容性
        if prev_tool and prev_tool in tool.compatible_upstream:
            score += 15
        if prev_tool and get_tool(prev_tool) and tool.name in get_tool(prev_tool).compatible_downstream:
            score += 10

        # 观测能力
        if tool.observation.get("object"):
            score += 10
        if tool.observation.get("execution"):
            score += 15 * w.get("execution", 0.5)  # AI训练需求时execution权重极高

        # ── 历史数据调整: 静态评分 × 历史成功率 ──
        if history:
            hist_rate = history.get_tool_confidence(stage, tool.name)
            if hist_rate is not None and hist_rate > 0:
                score = score * (0.5 + 0.5 * hist_rate)

        # 推荐场景匹配
        for rec in tool.recommended_for:
            rec_lower = rec.lower()
            if priority == UserPriority.OPEN_SOURCE and "开源" in rec:
                score += 10
            if priority == UserPriority.QUALITY and ("tape" in rec_lower or "ppa" in rec or "商业" in rec):
                score += 15
            if priority == UserPriority.SPEED and ("快速" in rec or "原型" in rec):
                score += 10
            if priority == UserPriority.LOW_POWER and ("低功耗" in rec_lower or "power" in rec_lower):
                score += 15
            if priority == UserPriority.AREA_OPT and ("面积" in rec or "area" in rec_lower):
                score += 15
            if priority == UserPriority.SIGN_OFF and ("tape" in rec_lower or "签核" in rec or "商业" in rec):
                score += 20
            if priority == UserPriority.AI_TRAINING and ("研究" in rec or "迭代" in rec):
                score += 15

        return score

    def _get_stage_cap(self, tool: ToolInfo, stage: str) -> Optional[StageCapability]:
        for s in tool.stages:
            if s.stage == stage:
                return s
        return None

    def _build_step(
        self, stage: str, tool: ToolInfo, sc: StageCapability,
        reason: str, alternatives: List[str] = None,
    ) -> FlowStep:
        return FlowStep(
            id=f"{stage}_{tool.name}",
            stage=stage,
            primary_tool=tool.name,
            alternatives=alternatives or [],
            tool_info=tool,
            reason=reason,
            inputs=list(sc.inputs),
            outputs=list(sc.outputs),
            parameters=dict(sc.parameters),
        )

    # ═══════════════════════════════════════════════════════════
    # 兼容性验证
    # ═══════════════════════════════════════════════════════════
    def _validate_compatibility(self, steps: List[FlowStep]) -> List[str]:
        warnings = []
        for i in range(len(steps) - 1):
            curr = steps[i]
            next_step = steps[i + 1]

            # 同一个工具连续使用不需要兼容性检查（如 OpenROAD floorplan→placement）
            if curr.primary_tool == next_step.primary_tool:
                continue

            curr_tool = get_tool(curr.primary_tool)
            next_tool = get_tool(next_step.primary_tool)

            if curr_tool and next_tool:
                if next_step.primary_tool not in curr_tool.compatible_downstream:
                    warnings.append(
                        f"[兼容性] {curr.primary_tool}→{next_step.primary_tool}: "
                        f"{curr.primary_tool} 未声明兼容 {next_step.primary_tool}，"
                        f"可能需要格式转换或手动桥接"
                    )

                curr_outputs = {a.format for a in curr.outputs if a.required}
                next_inputs = {a.format for a in next_step.inputs if a.required}
                missing = next_inputs - curr_outputs
                if missing:
                    warnings.append(
                        f"[产物] {curr.primary_tool}({curr.stage})→"
                        f"{next_step.primary_tool}({next_step.stage}): "
                        f"缺少产物格式 {missing}，可能需要额外的格式转换步骤"
                    )

        return warnings

    # ═══════════════════════════════════════════════════════════
    # 推荐 & 解释
    # ═══════════════════════════════════════════════════════════
    def _generate_recommendations(
        self, steps: List[FlowStep], priority: UserPriority,
        goals: Dict, warnings: List[str],
    ) -> List[str]:
        recs = []

        if warnings:
            recs.append("建议添加格式验证步骤确保产物可被下游消费")

        # 按策略维度给出针对性建议
        rec_map = {
            UserPriority.OPEN_SOURCE: [
                "当前使用全开源工具链，适合原型验证和学术研究",
                "如需 tape-out 签核，建议将 STA 替换为 PrimeTime",
            ],
            UserPriority.QUALITY: [
                "当前使用商业高品质工具链，建议利用沙箱环境验证优化效果",
                "可尝试将 placement 替换为 OpenROAD 获取执行轨迹用于 AI 训练",
            ],
            UserPriority.SPEED: [
                "当前优先迭代速度，建议使用精简流程 (synthesis + STA 两步)",
                "可在沙箱中并行测试多个参数组合加速收敛",
            ],
            UserPriority.LOW_POWER: [
                "低功耗优化: 建议在综合时启用 clock gating，placement 后跑功耗分析",
                "可搭配 iPA (iEDA Power Analysis) 获取功耗分布热力图",
            ],
            UserPriority.AREA_OPT: [
                "面积优化: 建议在 placement 阶段提高 density 参数 (0.7→0.85)",
                "可尝试多种 floorplan 方案对比面积利用率",
            ],
            UserPriority.SIGN_OFF: [
                "签核级质量: STA 必须使用 PrimeTime，DRC 必须使用 Calibre",
                "建议在最终签核前至少跑 3 个 corner (TT/FF/SS)",
            ],
            UserPriority.AI_TRAINING: [
                "AI 训练数据收集: 优先选择有 execution 观测能力的工具 (如 OpenROAD)",
                "建议将 placement 替换为 OpenROAD 以获取逐迭代 HPWL 数据",
                "可在沙箱中跑多种参数组合生成训练数据集",
            ],
            UserPriority.LEARNING: [
                "新手友好: 推荐从精简流程开始 (只跑 synthesis + STA)",
                "Yosys 文档丰富，社区活跃，适合学习数字 IC 设计流程",
            ],
            UserPriority.RELIABILITY: [
                "高可靠性: 建议所有步骤使用经过 tape-out 验证的工具版本",
                "建议在沙箱中多次重复运行验证结果一致性",
            ],
        }
        recs.extend(rec_map.get(priority, []))

        # 按 goal 补充针对性建议
        freq = goals.get("frequency", goals.get("fmax", goals.get("clock_period", 0)))
        if freq > 2000:
            recs.append(f"极高频设计 ({freq}MHz): 必须使用商业工具链 (DC + Innovus + PrimeTime)")
        elif freq > 500:
            recs.append(f"高频设计 ({freq}MHz): 建议 DC + Innovus，开源工具可能不满足时序")
        elif freq > 0:
            recs.append(f"中频设计 ({freq}MHz): 开源工具链可满足需求")

        if goals.get("area_min"):
            recs.append("面积最小化: placement density 从 0.6 逐步提升至时序违规边界")
        if goals.get("area_max"):
            area = goals["area_max"]
            recs.append(f"面积约束 (≤{area} um²): 建议在 placement 阶段设置较高的 density (0.75+)")
        if goals.get("utilization"):
            util = goals["utilization"]
            if isinstance(util, (int, float)) and util > 70:
                recs.append(f"高利用率 ({util}%): 关注拥塞风险，建议预留 routing margin")
        if goals.get("die_area_max"):
            recs.append(f"芯片面积上限: 需要在 floorplan 阶段精确控制 die_area，建议尝试多种 FP 方案")

        if goals.get("power_max"):
            pw = goals["power_max"]
            recs.append(f"功耗约束 (≤{pw}mW): 综合时启用 clock gating, placement 后跑功耗分析")
            recs.append("建议添加 iPA (iEDA Power Analysis) 步骤")
        if goals.get("power_min"):
            recs.append("低功耗优化: 考虑多电压域设计, 动态电压频率调节 (DVFS)")
        if goals.get("leakage_max"):
            recs.append("泄漏功耗约束: 建议使用 HVT 单元, 降低非关键路径的驱动强度")

        if goals.get("congestion_max"):
            recs.append(f"拥塞约束 (≤{goals['congestion_max']}%): 降低 density, 增大 die area")
        if goals.get("drc") or goals.get("drc_violations"):
            recs.append("DRC 零违规要求: 必须使用 Calibre 做最终 signoff (商业 license 需要)")
        if goals.get("wirelength"):
            recs.append("线长优化: 建议 floorplan 阶段尝试多种 macro 摆放方案")

        if goals.get("wns"):
            wns_target = goals["wns"]
            if isinstance(wns_target, (int, float)) and wns_target >= 0:
                recs.append(f"时序签核目标 (WNS ≥ {wns_target}): STA 建议使用 PrimeTime")
        if goals.get("tns"):
            recs.append("TNS 目标: 优先解决 WNS 最差的路径")

        if not recs:
            recs.append("当前配置已足够满足需求")
        return recs

    # ═══════════════════════════════════════════════════════════
    # 目标驱动闭环 (文档 3.2 节: 模块交互流程)
    # ═══════════════════════════════════════════════════════════
    def close_loop(
        self, flow: ComposedFlow, metrics: Dict, ppa_spec=None, constraints: Dict = None,
    ) -> Dict:
        """执行后闭环: 诊断 → 检查目标 → 建议重跑。

        对应文档:
          - 2.4 节: 结构化诊断
          - 3.1 节: analyzer.py → replanner.py 交互

        Args:
            flow: 当前 Flow
            metrics: 执行后收集的指标 {"sta": {"wns": -0.03}, ...}
            ppa_spec: PPA 目标 (可选)
            constraints: 设计约束

        Returns:
            {
                "passed": bool,
                "diagnosis": AnalysisReport,
                "rerun_plan": [(param, level, steps), ...] | None,
                "next_action": "done" | "rerun" | "human_breakpoint"
            }
        """
        from composer.analyzer import FlowAnalyzer
        from composer.replanner import Replanner

        analyzer = FlowAnalyzer()
        replanner = Replanner()

        # 1. 诊断
        report = analyzer.analyze(metrics, goal=ppa_spec, constraints=constraints)

        result = {
            "passed": report.passed,
            "diagnosis": report,
            "next_action": "done" if report.passed else "rerun",
        }

        # 2. 如果未通过，生成重跑计划 (从便宜的开始)
        if not report.passed:
            errors = [i for i in report.items if i.severity == "error"]
            # 从错误中提取需要调整的参数
            params_to_fix = []
            for e in errors:
                if e.category == "timing" and e.metric_name == "wns":
                    params_to_fix.extend(["clock_period", "place_density", "core_utilization"])
                elif e.category == "routing":
                    params_to_fix.extend(["core_utilization", "aspect_ratio"])
                elif e.category == "area":
                    params_to_fix.extend(["core_utilization", "DIE_AREA"])
                elif e.category == "power":
                    params_to_fix.append("clock_period")

            params_to_fix = list(dict.fromkeys(params_to_fix))  # 去重保序
            full_steps = [s.stage for s in flow.steps]
            rerun_plan = replanner.cheapest_first(params_to_fix, full_steps)
            result["rerun_plan"] = rerun_plan

            if not rerun_plan:
                result["next_action"] = "human_breakpoint"

        return result

    def _describe_flow(
        self, design_type: str, priority: UserPriority, fast_mode: bool,
    ) -> str:
        desc_map = {
            UserPriority.OPEN_SOURCE: "全开源工具链",
            UserPriority.QUALITY: "商业高品质工具链",
            UserPriority.SPEED: "快速原型工具链",
            UserPriority.LEARNING: "新手友好工具链",
            UserPriority.RELIABILITY: "高可靠性工具链",
            UserPriority.LOW_POWER: "低功耗优化工具链",
            UserPriority.AREA_OPT: "面积优化工具链",
            UserPriority.SIGN_OFF: "签核级工具链",
            UserPriority.AI_TRAINING: "AI训练数据采集工具链",
        }
        mode = "精简" if fast_mode else "完整"
        return f"{desc_map.get(priority, '标准')} ({design_type} {mode}流程)"

    # ═══════════════════════════════════════════════════════════
    # 对外接口
    # ═══════════════════════════════════════════════════════════
    def explain(self, flow: ComposedFlow) -> str:
        """人类可读的解释"""
        lines = [
            f"Flow: {flow.name}",
            f"描述: {flow.description}",
            f"设计: {flow.design} ({flow.technology})",
            f"步骤数: {len(flow.steps)}",
            "",
            "步骤详情:",
        ]
        for i, s in enumerate(flow.steps):
            lines.append(f"  {i+1}. [{s.stage}] → {s.primary_tool}")
            lines.append(f"     理由: {s.reason}")
            if s.alternatives:
                lines.append(f"     备选: {', '.join(s.alternatives)}")
            lines.append(f"     输入: {[a.name for a in s.inputs if a.required]}")
            lines.append(f"     输出: {[a.name for a in s.outputs if a.required]}")

        if flow.warnings:
            lines.append("")
            lines.append("⚠️ 警告:")
            for w in flow.warnings:
                lines.append(f"  - {w}")

        if flow.recommendations:
            lines.append("")
            lines.append("💡 建议:")
            for r in flow.recommendations:
                lines.append(f"  - {r}")

        return "\n".join(lines)

    def list_alternatives(self, flow: ComposedFlow, step: str) -> List[Dict]:
        """列出一个步骤的所有可替换工具。

        Args:
            flow: ComposedFlow
            step: 阶段名或步骤 ID

        Returns:
            [{"tool": "OpenROAD", "reason": "...", "tradeoffs": ["pro", "con"]}, ...]
        """
        s = flow.get_step(step)
        if not s:
            return []

        current = s.primary_tool
        candidates = get_tools_for_stage(s.stage)
        result = []
        for t in candidates:
            if t.name == current:
                continue
            tradeoffs = []
            if t.is_open_source:
                tradeoffs.append("✅ 开源免费")
            else:
                tradeoffs.append("⚠️ 需要商业 license")
            sc = self._get_stage_cap(t, s.stage)
            if sc:
                tradeoffs.append(f"质量: {sc.quality} | 速度: {sc.speed}")
            if t.observation.get("execution"):
                tradeoffs.append("✅ 可观测执行轨迹")
            if t.name in (s.tool_info.compatible_downstream if s.tool_info else []):
                tradeoffs.append("✅ 与当前上游工具兼容")
            result.append({
                "tool": t.name,
                "description": t.description,
                "reason": f"替换后的 tradeoff",
                "tradeoffs": tradeoffs,
            })
        return result

    def swap_tool(self, flow: ComposedFlow, step: str, new_tool_name: str) -> Optional[ComposedFlow]:
        """将 Flow 中某个步骤的工具替换为另一个。

        Args:
            flow: 现有 Flow
            step: 要替换的阶段名
            new_tool_name: 新工具名

        Returns:
            替换后的新 Flow，如果工具不支持该阶段则返回 None
        """
        s = flow.get_step(step)
        if not s:
            return None

        new_tool = get_tool(new_tool_name)
        if not new_tool:
            return None

        sc = self._get_stage_cap(new_tool, s.stage)
        if not sc:
            return None  # 新工具不支持这个阶段

        # 创建新步骤
        new_step = self._build_step(
            s.stage, new_tool, sc,
            reason=self._score_reason(new_tool, s.stage),
            alternatives=s.alternatives + [s.primary_tool],
        )

        # 重建 Flow
        new_steps = []
        for old_step in flow.steps:
            if old_step.stage == step:
                new_steps.append(new_step)
            else:
                new_steps.append(old_step)

        new_warnings = self._validate_compatibility(new_steps)

        return ComposedFlow(
            name=flow.name.replace(
                s.primary_tool, new_tool_name
            ),
            description=f"{flow.description} (已替换 {s.primary_tool} → {new_tool_name})",
            design=flow.design,
            technology=flow.technology,
            steps=new_steps,
            warnings=new_warnings,
            recommendations=flow.recommendations,
        )

    def _score_reason(self, tool: ToolInfo, stage: str) -> str:
        parts = []
        if tool.is_open_source:
            parts.append("开源")
        else:
            parts.append("商业工具")
        parts.append(tool.description[:30])
        return "; ".join(parts)


# ═══════════════════════════════════════════════════════════
# CLI Demo
# ═══════════════════════════════════════════════════════════
def main():
    composer = FlowComposer()

    print("=" * 65)
    print("  Flow Composer Demo")
    print("=" * 65)

    # ── 场景 1: 开源快速原型 ──
    print("\n▶ 场景 1: 开源快速原型 (gcd, sky130)")
    flow1 = composer.compose(
        design="gcd", technology="sky130",
        requirements=["开源", "快速原型"],
        goals={"frequency": 100},
    )
    print(composer.explain(flow1))

    # ── 场景 2: 高质量商业 ──
    print("\n\n▶ 场景 2: 追求极致 PPA (riscv_core, tsmc3)")
    flow2 = composer.compose(
        design="riscv_core", technology="tsmc3",
        requirements=["极致PPA", "tape-out"],
        goals={"frequency": 500, "area_min": True},
    )
    print(composer.explain(flow2))

    # ── 场景 3: 工具替换 ──
    print("\n\n▶ 场景 3: 替换 flow1 的 STA 工具")
    print("当前 STA:", flow1.get_step("STA").primary_tool)
    print("备选方案:")
    for alt in composer.list_alternatives(flow1, "STA"):
        print(f"  - {alt['tool']}: {alt['tradeoffs']}")

    # ── 场景 4: 新手学习 ──
    print("\n\n▶ 场景 4: 新手友好 (精简模式)")
    flow3 = composer.compose(
        design="counter", technology="sky130",
        requirements=["新手", "教学"],
        fast_mode=True,
    )
    print(composer.explain(flow3))


if __name__ == "__main__":
    main()
