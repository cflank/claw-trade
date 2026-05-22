from __future__ import annotations

from claw_trade.ui_backend.report_repository import ReportRepository
from claw_trade.ui_backend.summary_builder import CompletionSummaryBuilder


def test_completion_summary_only_extracts_from_saved_report_and_pm_conclusion() -> None:
    repo = ReportRepository()
    repo.save_succeeded_report(
        report_id="r-sum-1",
        instrument_code="BTC",
        market="CRYPTO",
        title="BTC 报告",
        pm_final_conclusion="建议分批建仓",
        markdown=(
            "# 报告\n"
            "## 核心理由\n"
            "- 成交量回升\n"
            "- 资金费率回归\n"
            "## 主要风险\n"
            "- 波动仍高\n"
        ),
    )
    builder = CompletionSummaryBuilder(repo)
    summary = builder.build_completion_summary_from_saved_report("r-sum-1", pdf_available=True)
    assert summary["reportId"] == "r-sum-1"
    assert summary["finalConclusion"] == "建议分批建仓"
    assert summary["coreReasons"] == ["成交量回升", "资金费率回归"]
    assert summary["mainRisks"] == ["波动仍高"]
    assert summary["pdfAvailable"] is True


def test_completion_summary_uses_fallback_lines_when_sections_missing() -> None:
    repo = ReportRepository()
    repo.save_succeeded_report(
        report_id="r-sum-2",
        instrument_code="AAPL",
        market="US",
        title="AAPL 报告",
        markdown="# 报告\n正文",
    )
    builder = CompletionSummaryBuilder(repo)
    summary = builder.build_completion_summary_from_saved_report("r-sum-2")
    assert summary["coreReasons"] == ["完整理由请查看报告正文。"]
    assert summary["mainRisks"] == ["主要风险请查看报告正文。"]
