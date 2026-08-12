# 错误诊断器：解析仿真失败日志，诊断仿真失败原因，生成 SimError
# 输入：异常或错误日志
# 输出：SimError（{"type": "...", "likely_cause": "...", ...}）
# 被 adapter.py 调用（异常捕获后）
from typing import Dict, Any, Optional  
import re
from .contract import SimError

class ErrorDiagnosis:
    """
    解析仿真失败日志，归类为 SimError。

    输入：
        raw_log: 工具返回的原始错误日志（字符串）
        context: 可选的上下文信息（如设计类型、工具名等）

    输出：
        SimError 字典
    """

    # 错误模式匹配规则
    ERROR_PATTERNS = {
        "convergence_fail": [
            r"convergence",
            r"singular matrix",
            r"no convergence",
            r"iteration limit",
            r"Newton iteration",
        ],
        "model_missing": [
            r"model not found",
            r"no such file",
            r"library.*not found",
            r"PDK.*missing",
            r"模板不存在",
        ],
        "syntax_error": [
            r"syntax error",
            r"unexpected token",
            r"parse error",
            r"unrecognized command",
        ],
        "timeout": [
            r"timeout",
            r"timed out",
            r"exceeded time",
            r"took too long",
        ],
        "license_error": [
            r"license",
            r"feature not available",
            r"permission denied",
        ],
        "netlist_error": [
            r"netlist.*error",
            r"no such node",
            r"floating node",
            r"short circuit",
        ],
        "tool_crash": [
            r"segmentation fault",
            r"core dumped",
            r"internal error",
            r"exception",
            r"fatal",
        ],
        "tool_missing": [
            r"tool not found",
            r"未安装",
            r"未找到",
            r"not installed",
            r"not available",
        ],
    }

    def __init__(self, raw_log: str, context: Dict[str, Any] = None):
        """
        初始化错误诊断器

        Args:
            raw_log: 工具返回的原始错误日志
            context: 上下文信息（如 design_type, tool_name, circuit_name）
        """
        self.raw_log = raw_log
        self.context = context or {}

    def diagnose(self) -> SimError:
        """
        诊断错误并返回 SimError

        Returns:
            SimError 数据类实例:
                SimError(
                    type="convergence_fail",
                    likely_cause="oscillation at net049",
                    raw_log="..."
                )
        """
        error_type = self._classify_error()
        likely_cause = self._extract_likely_cause(error_type)

        return SimError(
            type=error_type,
            likely_cause=likely_cause,
            raw_log=self.raw_log[:2000] if self.raw_log else "",
        )

    def _classify_error(self) -> str:
        """根据日志内容分类错误类型"""
        if not self.raw_log:
            return "unknown"

        log_lower = self.raw_log.lower()

        for error_type, patterns in self.ERROR_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, log_lower, re.IGNORECASE):
                    return error_type

        return "unknown"

    def _extract_likely_cause(self, error_type: str) -> str:
        """提取可能的错误原因"""
        if not self.raw_log:
            return "No error log available"

        # 尝试提取日志中的关键行
        lines = self.raw_log.split("\n")

        # 常见的错误关键词
        cause_patterns = [
            r"error: (.*?)(?:\n|$)",
            r"warning: (.*?)(?:\n|$)",
            r"failed: (.*?)(?:\n|$)",
            r"exception: (.*?)(?:\n|$)",
            r"fatal: (.*?)(?:\n|$)",
        ]

        for line in lines:
            if any(kw in line.lower() for kw in ["error", "warning", "failed", "exception", "fatal"]):
                # 尝试提取错误信息
                for pattern in cause_patterns:
                    match = re.search(pattern, line, re.IGNORECASE)
                    if match:
                        cause = match.group(1).strip()
                        if len(cause) > 10 and len(cause) < 200:
                            return cause

        # 如果没提取到，根据错误类型返回默认原因
        default_causes = {
            "convergence_fail": "Circuit failed to converge; check transistor sizing or bias conditions",
            "model_missing": "Missing model file or template; check file paths and PDK setup",
            "syntax_error": "Syntax error in netlist or constraints file",
            "timeout": "Simulation exceeded time limit",
            "license_error": "License issue; check tool license availability",
            "netlist_error": "Netlist error; check node connections and component parameters",
            "tool_crash": "Tool crashed; check memory or version compatibility",
            "tool_missing": "EDA tool not installed or not found on PATH",
            "unknown": "Unknown error; check raw log for details",
        }

        return default_causes.get(error_type, default_causes["unknown"])