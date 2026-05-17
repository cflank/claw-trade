#!/usr/bin/env python3
"""Generate worker-level comparison reports for trading evidence folders."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


WORKERS = (
    "market_analyst",
    "fundamental_analyst",
    "news_analyst",
    "social_analyst",
    "bull_researcher",
    "bear_researcher",
    "research_manager",
    "trader",
    "risk_challenger",
    "risk_guardian",
    "risk_moderator",
    "portfolio_manager",
    "report_polisher",
)

ACTION_TERMS = (
    "买入",
    "持有",
    "卖出",
    "增持",
    "减仓",
    "止损",
    "BUY",
    "HOLD",
    "SELL",
    "FINAL TRANSACTION PROPOSAL",
)

DEBATE_TERMS = (
    "反驳",
    "辩论",
    "多头",
    "空头",
    "aggressive",
    "conservative",
    "bull",
    "bear",
    "rebut",
)


@dataclass(frozen=True)
class TextStats:
    chars: int
    cjk_chars: int
    ascii_words: int
    lines: int
    headings: int
    tables: int
    bullets: int
    numbered: int
    images: int
    action_terms: int
    debate_terms: int

    @property
    def cjk_ratio(self) -> float:
        return self.cjk_chars / self.chars if self.chars else 0.0


def read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def stats_for(text: str) -> TextStats:
    lines = text.splitlines()
    return TextStats(
        chars=len(text),
        cjk_chars=len(re.findall(r"[\u4e00-\u9fff]", text)),
        ascii_words=len(re.findall(r"\b[A-Za-z][A-Za-z0-9_'-]*\b", text)),
        lines=len(lines),
        headings=sum(1 for line in lines if re.match(r"^#{1,6}\s+", line)),
        tables=sum(1 for line in lines if re.match(r"^\s*\|.*\|\s*$", line)),
        bullets=sum(1 for line in lines if re.match(r"^\s*[-*]\s+", line)),
        numbered=sum(1 for line in lines if re.match(r"^\s*\d+[.)]\s+", line)),
        images=sum(1 for line in lines if "![" in line),
        action_terms=sum(text.count(term) for term in ACTION_TERMS),
        debate_terms=sum(text.lower().count(term.lower()) for term in DEBATE_TERMS),
    )


def classify_language(stats: TextStats) -> str:
    if stats.chars == 0:
        return "缺失"
    if stats.cjk_ratio >= 0.45:
        return "中文为主"
    if stats.cjk_ratio <= 0.10:
        return "英文为主"
    return "中英混合"


def ratio(left: int, right: int) -> float | None:
    if right <= 0:
        return None
    return left / right


def ratio_text(left: int, right: int) -> str:
    value = ratio(left, right)
    if value is None:
        return "n/a"
    return f"{value:.2f}x"


def delta_text(left: int, right: int) -> str:
    delta = left - right
    sign = "+" if delta >= 0 else ""
    return f"{sign}{delta}"


def evidence_file(root: Path, worker: str, kind: str) -> Path:
    return root / f"{worker}_{kind}.md"


def load_worker_stats(root: Path, worker: str) -> dict[str, TextStats]:
    return {
        "final_prompt": stats_for(read_text(evidence_file(root, worker, "final_prompt"))),
        "llm_back": stats_for(read_text(evidence_file(root, worker, "llm_back"))),
        "report": stats_for(read_text(evidence_file(root, worker, "report"))),
    }


def style_sentence(claw: TextStats, base: TextStats, label: str) -> str:
    length_ratio = ratio(claw.chars, base.chars)
    if length_ratio is None:
        length_part = f"{label} 缺少基准文本，无法做比例判断"
    elif length_ratio >= 1.35:
        length_part = f"{label} 明显更长（{length_ratio:.2f}x），上下文或展开程度更重"
    elif length_ratio <= 0.70:
        length_part = f"{label} 明显更短（{length_ratio:.2f}x），信息密度或展开程度偏薄"
    else:
        length_part = f"{label} 篇幅接近（{length_ratio:.2f}x）"

    layout_bits: list[str] = []
    if claw.headings > base.headings + 2:
        layout_bits.append("标题层级更多")
    elif base.headings > claw.headings + 2:
        layout_bits.append("标题层级更少")
    if claw.tables > base.tables:
        layout_bits.append("表格化更强")
    elif base.tables > claw.tables:
        layout_bits.append("表格化更弱")
    if claw.images > base.images:
        layout_bits.append("包含更多图片/图表引用")
    layout_part = "、".join(layout_bits) if layout_bits else "布局复杂度接近"

    style_bits: list[str] = []
    if claw.action_terms > base.action_terms:
        style_bits.append("交易动作词更密集")
    elif base.action_terms > claw.action_terms:
        style_bits.append("交易动作词更少")
    if claw.debate_terms > base.debate_terms:
        style_bits.append("辩论/对抗词更明显")
    elif base.debate_terms > claw.debate_terms:
        style_bits.append("辩论/对抗词偏弱")
    style_part = "、".join(style_bits) if style_bits else "决策/辩论词密度接近"
    return f"{length_part}；{layout_part}；{style_part}。"


def write_metric_table(lines: list[str], claw_root: Path, base_root: Path, workers: Iterable[str]) -> None:
    lines.extend(
        [
            "| worker | prompt chars claw/base | prompt ratio | report chars claw/base | report ratio | headings claw/base | tables claw/base | language claw/base |",
            "|---|---:|---:|---:|---:|---:|---:|---|",
        ]
    )
    for worker in workers:
        claw = load_worker_stats(claw_root, worker)
        base = load_worker_stats(base_root, worker)
        claw_prompt = claw["final_prompt"]
        base_prompt = base["final_prompt"]
        claw_report = claw["report"]
        base_report = base["report"]
        lines.append(
            f"| `{worker}` | {claw_prompt.chars}/{base_prompt.chars} "
            f"({delta_text(claw_prompt.chars, base_prompt.chars)}) | "
            f"{ratio_text(claw_prompt.chars, base_prompt.chars)} | "
            f"{claw_report.chars}/{base_report.chars} ({delta_text(claw_report.chars, base_report.chars)}) | "
            f"{ratio_text(claw_report.chars, base_report.chars)} | "
            f"{claw_report.headings}/{base_report.headings} | "
            f"{claw_report.tables}/{base_report.tables} | "
            f"{classify_language(claw_report)}/{classify_language(base_report)} |"
        )


def artifact_counts(root: Path) -> dict[str, int]:
    return {
        "final_prompt": len(list(root.glob("*_final_prompt.md"))),
        "llm_back": len(list(root.glob("*_llm_back.md"))),
        "worker_report": len([path for path in root.glob("*_report.md") if path.name != "final_report.md"]),
        "final_report": int((root / "final_report.md").exists()),
        "summary": int((root / "capture_summary.md").exists()),
    }


def extra_workers(root: Path) -> list[str]:
    seen: set[str] = set()
    for path in root.glob("*_final_prompt.md"):
        seen.add(path.name.removesuffix("_final_prompt.md"))
    return sorted(seen.difference(WORKERS))


def write_completeness_section(lines: list[str], claw_root: Path, base_root: Path) -> None:
    claw_counts = artifact_counts(claw_root)
    base_counts = artifact_counts(base_root)
    lines.extend(
        [
            "## Evidence Completeness",
            "",
            "| evidence set | final prompts | LLM backs | worker reports | final report | capture summary | extra workers |",
            "|---|---:|---:|---:|---:|---:|---|",
            (
                f"| claw-trade | {claw_counts['final_prompt']} | {claw_counts['llm_back']} | "
                f"{claw_counts['worker_report']} | {claw_counts['final_report']} | "
                f"{claw_counts['summary']} | {', '.join(extra_workers(claw_root)) or '-'} |"
            ),
            (
                f"| baseline | {base_counts['final_prompt']} | {base_counts['llm_back']} | "
                f"{base_counts['worker_report']} | {base_counts['final_report']} | "
                f"{base_counts['summary']} | {', '.join(extra_workers(base_root)) or '-'} |"
            ),
            "",
        ]
    )


def worker_ratios(claw_root: Path, base_root: Path, kind: str) -> list[tuple[str, float, int, int]]:
    rows: list[tuple[str, float, int, int]] = []
    for worker in WORKERS:
        claw_stats = load_worker_stats(claw_root, worker)[kind]
        base_stats = load_worker_stats(base_root, worker)[kind]
        value = ratio(claw_stats.chars, base_stats.chars)
        if value is not None:
            rows.append((worker, value, claw_stats.chars, base_stats.chars))
    return rows


def format_worker_ratio(row: tuple[str, float, int, int]) -> str:
    worker, value, left, right = row
    return f"`{worker}` {value:.2f}x ({left}/{right})"


def write_executive_findings(lines: list[str], claw_root: Path, base_root: Path) -> None:
    prompt_rows = worker_ratios(claw_root, base_root, "final_prompt")
    report_rows = worker_ratios(claw_root, base_root, "report")
    short_reports = [
        (worker, "claw-trade", load_worker_stats(claw_root, worker)["report"].chars)
        for worker in WORKERS
        if load_worker_stats(claw_root, worker)["report"].chars < 200
    ] + [
        (worker, "baseline", load_worker_stats(base_root, worker)["report"].chars)
        for worker in WORKERS
        if load_worker_stats(base_root, worker)["report"].chars < 200
    ]
    prompt_long = sorted((row for row in prompt_rows if row[1] >= 1.35), key=lambda item: item[1], reverse=True)
    prompt_short = sorted((row for row in prompt_rows if row[1] <= 0.70), key=lambda item: item[1])
    report_long = sorted((row for row in report_rows if row[1] >= 1.35), key=lambda item: item[1], reverse=True)
    report_short = sorted((row for row in report_rows if row[1] <= 0.70), key=lambda item: item[1])
    lines.extend(["## Executive Findings", ""])
    if prompt_long:
        lines.append("- claw-trade final prompt 明显更长的 worker: " + "; ".join(format_worker_ratio(row) for row in prompt_long[:5]))
    if prompt_short:
        lines.append("- claw-trade final prompt 明显更短的 worker: " + "; ".join(format_worker_ratio(row) for row in prompt_short[:5]))
    if report_long:
        lines.append("- claw-trade report 明显更长的 worker: " + "; ".join(format_worker_ratio(row) for row in report_long[:5]))
    if report_short:
        lines.append("- claw-trade report 明显更短的 worker: " + "; ".join(format_worker_ratio(row) for row in report_short[:5]))
    if short_reports:
        lines.append(
            "- 异常短报告需要单独审查: "
            + "; ".join(f"`{worker}`/{side} {chars} chars" for worker, side, chars in short_reports)
        )
    if not any((prompt_long, prompt_short, report_long, report_short, short_reports)):
        lines.append("- worker 级长度没有出现超过阈值的异常扩张、压缩或短报告。")
    lines.append("")


def write_worker_sections(lines: list[str], claw_root: Path, base_root: Path, workers: Iterable[str]) -> None:
    for worker in workers:
        claw = load_worker_stats(claw_root, worker)
        base = load_worker_stats(base_root, worker)
        lines.extend([f"### {worker}", ""])
        lines.append(style_sentence(claw["final_prompt"], base["final_prompt"], "final prompt"))
        lines.append(style_sentence(claw["llm_back"], base["llm_back"], "LLM back"))
        lines.append(style_sentence(claw["report"], base["report"], "worker report"))
        lines.append("")
        lines.extend(
            [
                "| artifact | claw chars | base chars | claw headings/tables/bullets | base headings/tables/bullets | action terms claw/base |",
                "|---|---:|---:|---:|---:|---:|",
            ]
        )
        for kind in ("final_prompt", "llm_back", "report"):
            left = claw[kind]
            right = base[kind]
            lines.append(
                f"| `{kind}` | {left.chars} | {right.chars} | "
                f"{left.headings}/{left.tables}/{left.bullets} | "
                f"{right.headings}/{right.tables}/{right.bullets} | "
                f"{left.action_terms}/{right.action_terms} |"
            )
        lines.append("")


def final_report_section(lines: list[str], claw_root: Path, base_root: Path) -> None:
    claw_text = read_text(claw_root / "final_report.md")
    base_text = read_text(base_root / "final_report.md")
    claw = stats_for(claw_text)
    base = stats_for(base_text)
    lines.extend(
        [
            "## Final Report",
            "",
            "| metric | claw-trade | baseline | ratio/delta |",
            "|---|---:|---:|---:|",
            f"| chars | {claw.chars} | {base.chars} | {ratio_text(claw.chars, base.chars)} / {delta_text(claw.chars, base.chars)} |",
            f"| headings | {claw.headings} | {base.headings} | {delta_text(claw.headings, base.headings)} |",
            f"| markdown table lines | {claw.tables} | {base.tables} | {delta_text(claw.tables, base.tables)} |",
            f"| bullet lines | {claw.bullets} | {base.bullets} | {delta_text(claw.bullets, base.bullets)} |",
            f"| image refs | {claw.images} | {base.images} | {delta_text(claw.images, base.images)} |",
            f"| action terms | {claw.action_terms} | {base.action_terms} | {delta_text(claw.action_terms, base.action_terms)} |",
            "",
            style_sentence(claw, base, "final report"),
            "",
        ]
    )


def missing_files(root: Path, workers: Iterable[str]) -> list[str]:
    missing: list[str] = []
    for worker in workers:
        for kind in ("final_prompt", "llm_back", "report"):
            path = evidence_file(root, worker, kind)
            if not path.exists():
                missing.append(str(path))
    if not (root / "final_report.md").exists():
        missing.append(str(root / "final_report.md"))
    return missing


def generate_report(args: argparse.Namespace) -> Path:
    claw_root = Path(args.claw_dir)
    base_root = Path(args.baseline_dir)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    missing = missing_files(claw_root, WORKERS) + missing_files(base_root, WORKERS)
    lines: list[str] = [
        f"# {args.title}",
        "",
        "## Verdict",
        "",
    ]
    if missing:
        lines.extend(
            [
                "无法判断完整 parity：证据文件存在缺口。下面仍输出已能读取部分的统计，但缺失项不能当作通过。",
                "",
                "## Missing Evidence",
                "",
            ]
        )
        lines.extend(f"- `{item}`" for item in missing)
        lines.append("")
    else:
        lines.extend(
            [
                "部分同意：本轮证据足以做 worker 级长度、风格和布局比较；是否达到产品级 parity 还要结合具体事实正确性和 provider/tool 成功证据逐项审查。",
                "",
            ]
        )

    lines.extend(
        [
            "## Evidence Scope",
            "",
            f"- claw-trade evidence: `{claw_root}`",
            f"- baseline evidence: `{base_root}`",
            "- artifacts compared per worker: `final_prompt.md`, `llm_back.md`, `report.md`",
            "- final report compared: `final_report.md`",
            "",
        ]
    )
    write_completeness_section(lines, claw_root, base_root)
    write_executive_findings(lines, claw_root, base_root)
    lines.extend(
        [
            "## Worker Metrics",
            "",
        ]
    )
    write_metric_table(lines, claw_root, base_root, WORKERS)
    lines.extend(
        [
            "",
            "## Worker-Level Analysis",
            "",
        ]
    )
    write_worker_sections(lines, claw_root, base_root, WORKERS)
    final_report_section(lines, claw_root, base_root)
    lines.extend(
        [
            "## Professional Readout",
            "",
            "- 长度差异主要说明材料边界和展开程度，不单独证明质量高低；final prompt 过长可能带来基线外控制面或上游材料过载，过短则可能丢失原版任务语气或证据链。",
            "- 布局差异主要看标题、表格、列表、图表引用。卖方研究报告感通常需要稳定章节、数据表和关键条件，但 debate worker 过度表格化会削弱辩论室语气。",
            "- 风格差异主要看动作词、辩论词、角色声线和最终建议是否明确。强观点本身不是问题，前提是来自 worker 证据而不是 Python/exporter 改写。",
            "- `llm_back` 与 `report` 在 claw-trade 证据中通常相同，因为 worker L1 report 由模型 raw output 批准后保存；原版脚本中也按节点 state report 保存，两者可比较但不是同一 runtime 机制。",
            "",
        ]
    )
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--claw-dir", required=True)
    parser.add_argument("--baseline-dir", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def main() -> int:
    output = generate_report(parse_args())
    print(f"wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
