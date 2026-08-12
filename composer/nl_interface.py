# nl_interface.py —— 自然语言接口
"""
将用户的中文/英文需求转换为 FlowComposer.compose() 参数。

支持:
  - 自由文本: "帮我做一个 200MHz 的 gcd，用 sky130 工艺，开源工具"
  - 结构化提取: design, technology, requirements[], goals{}
  - 对话式补充: 缺失信息时自动提示

用法:
  from nl_interface import NLInterface
  nli = NLInterface()
  params = nli.parse("我要做一个开源的 riscv 芯片，跑在 500MHz")
  flow = composer.compose(**params)
"""
import re
from typing import Dict, List, Optional, Tuple


class NLInterface:
    """自然语言 → FlowComposer 参数解析器。"""

    # ── 设计名匹配 ──
    DESIGN_PATTERNS = {
        "gcd": [r"\bgcd\b", r"最大公约数", r"greatest common divisor"],
        "aes": [r"\baes\b", r"加密", r"aes\s*128", r"aes\s*256"],
        "riscv": [r"risc\s*v", r"riscv", r"risc-v", r"risc_v", r"\brv32", r"\brv64"],
        "uart": [r"\buart\b", r"串口"],
        "counter": [r"counter", r"计数器"],
    }

    # ── 工艺匹配 ──
    TECH_PATTERNS = {
        "sky130": [r"sky130", r"sky\s*130", r"skywater"],
        "ASAP7": [r"asap7", r"asap\s*7"],
        "tsmc3": [r"tsmc\s*3", r"tsmc3", r"3nm", r"3纳米"],
        "tsmc5": [r"tsmc\s*5", r"tsmc5", r"5nm", r"5纳米"],
        "gf22": [r"gf22", r"gf\s*22", r"global\s*foundries"],
    }

    # ── 需求关键词 ──
    REQ_PATTERNS = {
        "开源": [r"开源", r"open\s*source", r"免费", r"free"],
        "快速原型": [r"快速", r"原型", r"prototype", r"quick", r"fast", r"迅速"],
        "极致PPA": [r"极致", r"ppa", r"高性能", r"最佳", r"tape\s*out", r"签核", r"面积最"],
        "新手友好": [r"新手", r"入门", r"学习", r"教学", r"初学", r"小白", r"beginner"],
        "高可靠性": [r"可靠", r"稳定", r"商用", r"industr", r"production"],
        "低功耗": [r"低功耗", r"low\s*power", r"省电"],
    }

    def parse(self, text: str) -> Dict:
        """解析自然语言文本 → compose() 参数字典。

        Args:
            text: 用户输入的自然语言描述

        Returns:
            {"design": "gcd", "technology": "sky130",
             "requirements": ["开源", "快速原型"],
             "goals": {"frequency": 200}, "fast_mode": False}

        如果信息不足，返回的 dict 中会有 "questions" 字段提示缺失信息。
        """
        text_lower = text.lower()

        design = self._match_design(text_lower)
        technology = self._match_technology(text_lower)
        requirements = self._match_requirements(text_lower)
        goals = self._extract_goals(text_lower)
        fast_mode = any(w in text_lower for w in ["精简", "快速", "简单", "lite", "两步"])

        result = {
            "design": design or "gcd",
            "technology": technology or "sky130",
            "requirements": requirements or ["开源"],
            "goals": goals,
            "fast_mode": fast_mode,
        }

        # 检查缺失信息
        questions = []
        if not design:
            questions.append("请问您要设计什么芯片？(如 gcd / riscv / aes)")
        if not technology:
            questions.append("请问使用哪个工艺？(如 sky130 / ASAP7)")
        if not requirements:
            questions.append("您对工具有什么偏好吗？(如开源 / 商业 / 快速)")
        if questions:
            result["questions"] = questions

        return result

    def _match_design(self, text: str) -> Optional[str]:
        for design, patterns in self.DESIGN_PATTERNS.items():
            for p in patterns:
                if re.search(p, text, re.IGNORECASE):
                    return design
        # 尝试匹配 "xxx芯片" / "xxx设计" 等
        m = re.search(r'([a-zA-Z_]\w*)\s*(?:芯片|设计|电路|chip|design)', text)
        if m:
            return m.group(1)
        return None

    def _match_technology(self, text: str) -> Optional[str]:
        for tech, patterns in self.TECH_PATTERNS.items():
            for p in patterns:
                if re.search(p, text, re.IGNORECASE):
                    return tech
        return None

    def _match_requirements(self, text: str) -> List[str]:
        matched = []
        for req, patterns in self.REQ_PATTERNS.items():
            for p in patterns:
                if re.search(p, text, re.IGNORECASE):
                    matched.append(req)
                    break
        return matched

    def _extract_goals(self, text: str) -> Dict:
        goals = {}

        # 频率: 200MHz / 1GHz / 500M / 100 MHz
        freq_match = re.search(
            r'(\d+\.?\d*)\s*(?:MHz|Mhz|mhz|M\b|兆)', text, re.IGNORECASE
        )
        if freq_match:
            goals["frequency"] = float(freq_match.group(1))
        ghz_match = re.search(r'(\d+\.?\d*)\s*(?:GHz|Ghz|ghz|G\b|吉)', text, re.IGNORECASE)
        if ghz_match:
            goals["frequency"] = float(ghz_match.group(1)) * 1000

        # 面积
        if re.search(r'面积.*?(\d+\.?\d*)', text):
            m = re.search(r'面积.*?(\d+\.?\d*)', text)
            goals["area_max"] = float(m.group(1))
        if re.search(r'最小面积|面积最小|面积优化', text):
            goals["area_min"] = True

        # 功耗
        if re.search(r'功耗.*?(\d+\.?\d*)', text):
            m = re.search(r'功耗.*?(\d+\.?\d*)', text)
            goals["power_max"] = float(m.group(1))
        if re.search(r'低功耗|功耗优化|省电', text):
            goals["power_min"] = True

        return goals

    def explain(self, text: str) -> str:
        """解析并给出人类可读的解释。"""
        params = self.parse(text)
        lines = [f"输入: {text}", "", "解析结果:"]
        lines.append(f"  设计:     {params.get('design', '未识别')}")
        lines.append(f"  工艺:     {params.get('technology', '未识别')}")
        lines.append(f"  需求:     {', '.join(params.get('requirements', [])) or '未识别'}")
        goals = params.get("goals", {})
        if goals:
            lines.append(f"  目标:     {goals}")
        lines.append(f"  精简模式: {'是' if params.get('fast_mode') else '否'}")

        if params.get("questions"):
            lines.append("")
            lines.append("⚠️ 缺失信息:")
            for q in params["questions"]:
                lines.append(f"  - {q}")

        return "\n".join(lines)
