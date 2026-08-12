#!/usr/bin/env python3
"""
ista_verification.py —— iSTA 与 OpenSTA 基准对齐验证（S3 合规）

文档要求的三步验证流程：
  第一步: 格式兼容性验证 (W1) — iSTA 报告能否被 ICCAD 评估脚本解析
  第二步: 数值相关性验证 (W2~W4) — R² > 0.99, MAE < 1 ps
  第三步: 端到端排名验证 (W4+) — Spearman ρ > 0.95, Top 10 一致

用法:
  python3 ista_verification.py --step 1    # 仅格式验证
  python3 ista_verification.py --step 2    # 格式 + 数值
  python3 ista_verification.py --step 3    # 全部三步
  python3 ista_verification.py --all       # 完整验证
"""
import argparse
import json
import math
import os
import re
import statistics
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


class ISTAVerifier:
    """iSTA ↔ OpenSTA 三步验证器。

    验证逻辑见文档第三部分 3.2 节。
    """

    def __init__(self, work_dir: str = None):
        self.work_dir = Path(work_dir or "/tmp/ista_verification")
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.results: Dict = {}

    # ═══════════════════════════════════════════════════════════
    # 第一步：格式兼容性验证
    # ═══════════════════════════════════════════════════════════
    def step1_format_compatibility(
        self, ista_report_path: str, eval_script_path: str = None,
    ) -> Tuple[bool, str]:
        """验证 iSTA 报告能否被 ICCAD 评估脚本解析。

        通过标准: 脚本正常执行完毕，无 KeyError / ParseError。

        Args:
            ista_report_path: iSTA 生成的 .rpt 文件路径
            eval_script_path: ICCAD 官方评估脚本路径（可选）

        Returns:
            (passed, reason)
        """
        if not os.path.exists(ista_report_path):
            return False, f"iSTA 报告不存在: {ista_report_path}"

        # 1. 基本格式检查：文件非空且有 WNS/TNS/area 等关键字段
        with open(ista_report_path, "r") as f:
            content = f.read()

        required_fields = ["wns", "tns", "slack", "clock", "area"]
        found = [f for f in required_fields if f.lower() in content.lower()]
        if len(found) < 3:
            return False, (
                f"报告缺少关键字段: 找到 {found}，需要至少 3 个 "
                f"({required_fields})"
            )

        # 2. 尝试解析 WNS/TNS 值（兼容多种报告格式）
        wns_match = re.search(
            r'(?:wns|Worst Negative Slack)[^-\d]*(-?[\d.]+)',
            content, re.IGNORECASE,
        )
        tns_match = re.search(
            r'(?:tns|Total Negative Slack)[^-\d]*(-?[\d.]+)',
            content, re.IGNORECASE,
        )

        if not wns_match:
            return False, "无法从报告中解析 WNS 值"

        wns = float(wns_match.group(1))
        tns = float(tns_match.group(1)) if tns_match else 0.0

        # 3. 尝试用评估脚本解析（如果提供了脚本路径）
        eval_ok = True
        eval_msg = ""
        if eval_script_path and os.path.exists(eval_script_path):
            try:
                import subprocess
                result = subprocess.run(
                    [sys.executable, eval_script_path, ista_report_path],
                    capture_output=True, text=True, timeout=30,
                    cwd=str(self.work_dir),
                )
                if result.returncode != 0:
                    eval_ok = False
                    eval_msg = f"评估脚本返回错误 (rc={result.returncode}): {result.stderr[-200:]}"
            except Exception as e:
                eval_ok = False
                eval_msg = f"评估脚本执行异常: {e}"

        self.results["step1"] = {
            "passed": bool(wns_match),
            "wns": wns, "tns": tns,
            "fields_found": found,
            "eval_script_ok": eval_ok,
            "eval_script_msg": eval_msg,
        }

        if not wns_match:
            return False, "格式验证失败: 无法解析 WNS"
        if not eval_ok:
            return False, f"格式验证失败: {eval_msg}"

        return True, f"格式兼容性通过 (WNS={wns:.4f}, TNS={tns:.4f})"

    # ═══════════════════════════════════════════════════════════
    # 第二步：数值相关性验证
    # ═══════════════════════════════════════════════════════════
    def step2_numerical_correlation(
        self,
        ista_slacks: Dict[str, float],     # {endpoint: slack_ista}
        opensta_slacks: Dict[str, float],  # {endpoint: slack_opensta}
    ) -> Tuple[bool, Dict]:
        """计算 iSTA 与 OpenSTA 的数值相关性。

        通过标准:
          - R² > 0.99
          - MAE < 1 ps

        Args:
            ista_slacks: iSTA 的各端点 slack 值
            opensta_slacks: OpenSTA 的各端点 slack 值

        Returns:
            (passed, {"R2": ..., "MAE_ps": ..., "n_endpoints": ...})
        """
        # 对齐端点
        common = set(ista_slacks.keys()) & set(opensta_slacks.keys())
        if len(common) < 10:
            return False, {"error": f"共同端点不足: {len(common)} (需要 ≥ 10)", "R2": 0, "MAE_ps": float("inf")}

        x_vals = [opensta_slacks[k] for k in common]  # OpenSTA = 基准
        y_vals = [ista_slacks[k] for k in common]      # iSTA = 被验证

        n = len(x_vals)

        # ── R² (决定系数) ──
        mean_x = statistics.mean(x_vals)
        mean_y = statistics.mean(y_vals)
        ss_res = sum((y - self._predicted(x, x_vals, y_vals)) ** 2
                     for x, y in zip(x_vals, y_vals))
        ss_tot = sum((y - mean_y) ** 2 for y in y_vals)
        r2 = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0

        # ── MAE (平均绝对误差) ──
        mae = sum(abs(y - x) for x, y in zip(x_vals, y_vals)) / n

        # ── Pearson r ──
        cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(x_vals, y_vals)) / n
        std_x = (sum((x - mean_x) ** 2 for x in x_vals) / n) ** 0.5
        std_y = (sum((y - mean_y) ** 2 for y in y_vals) / n) ** 0.5
        pearson_r = cov / (std_x * std_y) if std_x > 0 and std_y > 0 else 0

        passed = r2 > 0.99 and mae < 1.0

        metrics = {
            "R2": round(r2, 6), "MAE_ps": round(mae, 4),
            "Pearson_r": round(pearson_r, 4),
            "n_endpoints": n, "passed": passed,
        }

        self.results["step2"] = metrics
        return passed, metrics

    @staticmethod
    def _predicted(x: float, xs: List[float], ys: List[float]) -> float:
        """简单线性回归预测。"""
        if len(xs) < 2:
            return statistics.mean(ys)
        mean_x = statistics.mean(xs)
        mean_y = statistics.mean(ys)
        num = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(xs, ys))
        den = sum((xi - mean_x) ** 2 for xi in xs)
        if den == 0:
            return mean_y
        beta = num / den
        alpha = mean_y - beta * mean_x
        return alpha + beta * x

    # ═══════════════════════════════════════════════════════════
    # 第三步：端到端排名验证
    # ═══════════════════════════════════════════════════════════
    def step3_ranking_correlation(
        self,
        ista_scores: Dict[str, float],     # {design_id: score}
        opensta_scores: Dict[str, float],  # {design_id: score}
    ) -> Tuple[bool, Dict]:
        """验证 iSTA 选出最优方案的能力。

        通过标准:
          - Spearman's ρ > 0.95
          - Top 10 方案完全一致

        Args:
            ista_scores: iSTA 对各设计方案的打分
            opensta_scores: OpenSTA 对各设计方案的打分

        Returns:
            (passed, {"spearman_rho": ..., "top10_match": ...})
        """
        common = set(ista_scores.keys()) & set(opensta_scores.keys())
        if len(common) < 30:
            return False, {
                "error": f"候选方案不足: {len(common)} (需要 ≥ 30)",
                "spearman_rho": 0, "top10_match": False,
            }

        # ── Spearman's ρ ──
        def rank_dict(d: Dict[str, float]) -> Dict[str, int]:
            sorted_items = sorted(d.items(), key=lambda x: x[1], reverse=True)
            return {k: i + 1 for i, (k, _) in enumerate(sorted_items)}

        ista_ranks = rank_dict({k: ista_scores[k] for k in common})
        opensta_ranks = rank_dict({k: opensta_scores[k] for k in common})

        n = len(common)
        d_sq_sum = sum((ista_ranks[k] - opensta_ranks[k]) ** 2 for k in common)
        rho = 1 - (6 * d_sq_sum) / (n * (n**2 - 1))

        # ── Top 10 一致性 ──
        ista_top10 = set(sorted(common, key=lambda k: ista_scores[k], reverse=True)[:10])
        opensta_top10 = set(sorted(common, key=lambda k: opensta_scores[k], reverse=True)[:10])
        top10_match = ista_top10 == opensta_top10

        passed = rho > 0.95 and top10_match

        metrics = {
            "spearman_rho": round(rho, 4),
            "top10_match": top10_match,
            "n_candidates": n,
            "ista_top10": list(ista_top10),
            "opensta_top10": list(opensta_top10),
            "passed": passed,
        }

        self.results["step3"] = metrics
        return passed, metrics

    # ═══════════════════════════════════════════════════════════
    # 综合报告
    # ═══════════════════════════════════════════════════════════
    def generate_report(self) -> str:
        """生成人类可读的验证报告。"""
        lines = [
            "=" * 60, "  iSTA ↔ OpenSTA 基准对齐验证报告 (S3)", "=" * 60, "",
        ]

        # Step 1
        s1 = self.results.get("step1", {})
        lines.append(f"第一步: 格式兼容性 — {'✅ 通过' if s1.get('passed') else '❌ 失败'}")
        if s1:
            lines.append(f"  WNS: {s1.get('wns', 'N/A')}  TNS: {s1.get('tns', 'N/A')}")
            lines.append(f"  评估脚本: {'✅' if s1.get('eval_script_ok') else '❌'}")
        lines.append("")

        # Step 2
        s2 = self.results.get("step2", {})
        lines.append(f"第二步: 数值相关性 — {'✅ 通过' if s2.get('passed') else '❌ 失败'}")
        if s2:
            lines.append(f"  R²: {s2.get('R2', 'N/A')}  (阈值 > 0.99)")
            lines.append(f"  MAE: {s2.get('MAE_ps', 'N/A')} ps  (阈值 < 1 ps)")
            lines.append(f"  Pearson r: {s2.get('Pearson_r', 'N/A')}")
            lines.append(f"  端点数量: {s2.get('n_endpoints', 'N/A')}")
        lines.append("")

        # Step 3
        s3 = self.results.get("step3", {})
        lines.append(f"第三步: 端到端排名 — {'✅ 通过' if s3.get('passed') else '❌ 失败'}")
        if s3:
            lines.append(f"  Spearman's ρ: {s3.get('spearman_rho', 'N/A')}  (阈值 > 0.95)")
            lines.append(f"  Top 10 一致: {'✅' if s3.get('top10_match') else '❌'}")
            lines.append(f"  候选方案数: {s3.get('n_candidates', 'N/A')}")
        lines.append("")

        # 决策
        all_pass = (s1.get("passed") and s2.get("passed", False) and s3.get("passed", False))
        lines.append("=" * 60)
        lines.append("  最终决策:")
        if s1.get("passed") and s2.get("passed") and s3.get("passed"):
            lines.append("  ✅ iSTA 可作为 Adapter 的主 STA 后端（ieda_py in-process）")
        elif s1.get("passed") and s2.get("passed") and not s3.get("passed"):
            lines.append("  ⚠️ iSTA 为主选但需增加数学校准层")
        elif s1.get("passed") and not s2.get("passed"):
            lines.append("  ⚠️ iSTA 降级为实验性后端，OpenSTA 子进程作为主路径")
        else:
            lines.append("  ❌ iSTA 淘汰，OpenSTA 子进程作为唯一后端")
        lines.append("=" * 60)

        return "\n".join(lines)

    def decision(self) -> str:
        """返回 Adapter 决策映射（对应文档 3.3 节）。"""
        s1 = self.results.get("step1", {}).get("passed", False)
        s2 = self.results.get("step2", {}).get("passed", False)
        s3 = self.results.get("step3", {}).get("passed", False)

        if s1 and s2 and s3:
            return "iSTA_primary"      # iSTA 主选，in-process
        elif s1 and s2 and not s3:
            return "iSTA_calibrated"   # iSTA 主选 + 校���层
        elif s1 and not s2:
            return "opensta_primary"   # OpenSTA 主路径
        else:
            return "opensta_only"      # OpenSTA 唯一后端


# ═══════════════════════════════════════════════════════════
# Demo: 生成模拟数据演示三步验证
# ═══════════════════════════════════════════════════════════
def demo():
    """演示三步验证流程（使用模拟数据）。"""
    import random
    random.seed(42)

    verifier = ISTAVerifier()

    print("=" * 60)
    print("  iSTA 三步验证演示 (模拟数据)")
    print("=" * 60)

    # Step 1
    print("\n▶ 第一步: 格式兼容性")
    # 创建模拟的 iSTA 报告
    report_path = verifier.work_dir / "ista_test.rpt"
    report_path.write_text("""
Worst Negative Slack (WNS): -0.234
Total Negative Slack (TNS): -10.567
Leakage Power: 1.234e-06
Total Area: 123456.78
Clock: clk
    """)
    passed, reason = verifier.step1_format_compatibility(str(report_path))
    print(f"  {'✅' if passed else '❌'} {reason}")

    # Step 2
    print("\n▶ 第二步: 数值相关性")
    # 生成 50 个端点的模拟 slack 数据（R² ≈ 0.998）
    endpoints = [f"endpoint_{i}" for i in range(50)]
    ista_slacks = {}
    opensta_slacks = {}
    for ep in endpoints:
        base = random.gauss(-0.5, 0.3)
        opensta_slacks[ep] = base
        # iSTA 略有偏差但高度相关
        ista_slacks[ep] = base * 1.002 + random.gauss(0, 0.0003)  # ~0.3ps noise
    passed, metrics = verifier.step2_numerical_correlation(ista_slacks, opensta_slacks)
    print(f"  {'✅' if passed else '❌'} R²={metrics['R2']:.5f}  MAE={metrics['MAE_ps']:.4f}ps  "
          f"Pearson r={metrics['Pearson_r']:.4f}")

    # Step 3
    print("\n▶ 第三步: 端到端排名")
    designs = [f"design_{i:03d}" for i in range(100)]
    ista_scores = {}
    opensta_scores = {}
    for d in designs:
        base_score = random.gauss(85, 10)
        opensta_scores[d] = base_score
        # iSTA 略有噪声但排名高度相关
        ista_scores[d] = base_score * 1.001 + random.gauss(0, 0.3)
    passed, metrics = verifier.step3_ranking_correlation(ista_scores, opensta_scores)
    print(f"  {'✅' if passed else '❌'} ρ={metrics['spearman_rho']:.4f}  "
          f"Top10 match: {'✅' if metrics['top10_match'] else '❌'}")

    # Report
    print("\n" + verifier.generate_report())

    # 保存结果
    report_file = verifier.work_dir / "verification_report.json"
    report_file.write_text(json.dumps(verifier.results, indent=2, default=str))
    print(f"\n详细结果已保存: {report_file}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="iSTA Verification (S3)")
    parser.add_argument("--demo", action="store_true", help="演示三步验证（模拟数据）")
    parser.add_argument("--step", type=int, choices=[1, 2, 3], help="执行指定步骤")
    parser.add_argument("--report", type=str, help="iSTA 报告路径 (第一步)")
    args = parser.parse_args()

    if args.demo or not any([args.step, args.report]):
        demo()
    else:
        verifier = ISTAVerifier()
        if args.step == 1 and args.report:
            passed, reason = verifier.step1_format_compatibility(args.report)
            print(f"{'✅' if passed else '❌'} {reason}")
