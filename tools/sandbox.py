# sandbox.py —— 沙箱验证环境
"""
隔离执行 EDA flow，捕获所有输出，对比不同方案的结果。

用法:
  from sandbox import Sandbox
  sb = Sandbox()
  result = sb.run_flow(flow, adapter, params)
  comparison = sb.compare(result_a, result_b)

沙箱职责:
  1. 在独立目录中执行 flow 的每一步
  2. 捕获所有产物、日志、指标
  3. 对比两次运行的结果差异
  4. 生成对比报告
"""
import json
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4


@dataclass
class StepResult:
    """单步执行结果"""
    stage: str
    tool: str
    success: bool
    snapshot_id: str = ""
    duration_ms: float = 0.0
    metrics: Dict = field(default_factory=dict)
    artifacts: List[Dict] = field(default_factory=list)
    error: str = ""


@dataclass
class SandboxRun:
    """一次沙箱运行"""
    run_id: str
    design: str
    technology: str
    steps: List[StepResult] = field(default_factory=list)
    total_duration_ms: float = 0.0

    @property
    def all_metrics(self) -> Dict:
        result = {}
        for s in self.steps:
            result[s.stage] = s.metrics
        return result


class Sandbox:
    """沙箱验证环境。"""

    def __init__(self, base_dir: str = None):
        if base_dir is None:
            base_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "outputs", "sandbox")
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def run_flow(
        self, flow, adapter, params: dict,
        label: str = "",
    ) -> SandboxRun:
        """在沙箱中执行一个 flow 的所有步骤。

        Args:
            flow: ComposedFlow
            adapter: Adapter 实例
            params: 顶层参数字典
            label: 运行标签

        Returns:
            SandboxRun 包含所有步骤的结果
        """
        run_id = str(uuid4())[:8]
        sandbox_dir = self.base_dir / f"{label}_{run_id}" if label else self.base_dir / run_id
        sandbox_dir.mkdir(parents=True, exist_ok=True)

        run = SandboxRun(run_id=run_id, design=flow.design, technology=flow.technology)
        t_start = time.time()

        from adapter.contract import SimError

        for step in flow.steps:
            step_t0 = time.time()

            # 确定 adapter 类型
            tool_info = step.tool_info
            adapter_name = tool_info.adapter if tool_info else "digital"

            if adapter_name not in adapter.backends:
                run.steps.append(StepResult(
                    stage=step.stage, tool=step.primary_tool,
                    success=False, error=f"Adapter {adapter_name} 未实现",
                ))
                continue

            # 组装参数
            step_params = dict(params)
            step_params.update({
                "TOP_MODULE": flow.design,
                "DESIGN_TOP": flow.design,
            })

            try:
                result = adapter.run(
                    adapter_name, flow.design, step_params,
                    observation_level=(
                        "object" if tool_info and tool_info.observation.get("object")
                        else "metric"
                    ),
                    snapshot_type="CHECKPOINT",
                )
            except Exception as e:
                run.steps.append(StepResult(
                    stage=step.stage, tool=step.primary_tool,
                    success=False, error=str(e),
                    duration_ms=(time.time() - step_t0) * 1000,
                ))
                continue

            if isinstance(result, SimError):
                run.steps.append(StepResult(
                    stage=step.stage, tool=step.primary_tool,
                    success=False, error=f"{result.type}: {result.likely_cause}",
                    duration_ms=(time.time() - step_t0) * 1000,
                ))
            else:
                h = result.header
                dt = result.digital_twin
                metrics = dt.metrics if hasattr(dt, 'metrics') else dt.get("metrics", {})
                artifacts = [
                    {"name": a.logical_name, "size": a.size, "checksum": a.checksum}
                    for a in result.artifact_manifest
                ]
                run.steps.append(StepResult(
                    stage=step.stage, tool=step.primary_tool,
                    success=True, snapshot_id=h.snapshot_id,
                    duration_ms=(time.time() - step_t0) * 1000,
                    metrics=metrics,
                    artifacts=artifacts,
                ))

        run.total_duration_ms = (time.time() - t_start) * 1000

        # 保存运行报告
        report = {
            "run_id": run.run_id,
            "design": run.design,
            "technology": run.technology,
            "total_duration_ms": run.total_duration_ms,
            "steps": [
                {"stage": s.stage, "tool": s.tool, "success": s.success,
                 "duration_ms": s.duration_ms, "metrics": s.metrics,
                 "error": s.error}
                for s in run.steps
            ],
        }
        (sandbox_dir / "sandbox_report.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False, default=str)
        )

        return run

    def compare(self, run_a: SandboxRun, run_b: SandboxRun) -> Dict:
        """对比两次沙箱运行。"""
        comparison = {
            "run_a": run_a.run_id,
            "run_b": run_b.run_id,
            "duration_diff_ms": run_b.total_duration_ms - run_a.total_duration_ms,
            "step_comparisons": [],
        }

        for sa, sb in zip(run_a.steps, run_b.steps):
            if sa.stage != sb.stage:
                continue

            metric_diff = {}
            all_keys = set()
            for m in [sa.metrics, sb.metrics]:
                for src, vals in m.items():
                    for name in vals:
                        all_keys.add(f"{src}.{name}")

            for key in all_keys:
                src, name = key.split(".", 1)
                va = sa.metrics.get(src, {}).get(name)
                vb = sb.metrics.get(src, {}).get(name)
                if va != vb:
                    metric_diff[key] = {"a": va, "b": vb}

            comparison["step_comparisons"].append({
                "stage": sa.stage,
                "tool_a": sa.tool, "tool_b": sb.tool,
                "success_a": sa.success, "success_b": sb.success,
                "duration_diff_ms": sb.duration_ms - sa.duration_ms,
                "metric_differences": metric_diff,
            })

        # 生成可读总结
        summary_lines = [
            f"对比报告: {run_a.run_id} vs {run_b.run_id}",
            f"总耗时: {run_a.total_duration_ms:.0f}ms → {run_b.total_duration_ms:.0f}ms "
            f"({comparison['duration_diff_ms']:+.0f}ms)",
            "",
            "步骤对比:",
        ]
        for sc in comparison["step_comparisons"]:
            status = "✅" if sc["success_a"] and sc["success_b"] else "⚠️"
            summary_lines.append(
                f"  {status} [{sc['stage']}] {sc['tool_a']} ↔ {sc['tool_b']} "
                f"耗时差={sc['duration_diff_ms']:+.0f}ms"
            )
            for k, v in sc["metric_differences"].items():
                summary_lines.append(f"      {k}: {v['a']} → {v['b']}")

        comparison["summary"] = "\n".join(summary_lines)
        return comparison
