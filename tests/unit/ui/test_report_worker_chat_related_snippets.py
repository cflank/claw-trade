from __future__ import annotations

from claw_trade.ui_backend.report_repository import ReportRepository
from claw_trade.ui_backend.report_worker_chat_context import ReportWorkerChatContextResolver


def test_report_worker_chat_adds_question_related_saved_report_snippets(tmp_path) -> None:  # type: ignore[no-untyped-def]
    reports_dir = tmp_path / "run-1" / "reports"
    asset_dir = reports_dir / "assets"
    asset_dir.mkdir(parents=True)
    appendix_dir = reports_dir / "worker-appendix"
    appendix_dir.mkdir()
    appendix_dir.joinpath("01-market_analyst.md").write_text("市场附录：量价结构改善。", encoding="utf-8")
    repo = ReportRepository()
    repo.save_succeeded_report(
        report_id="report-1",
        instrument_code="BTC",
        market="CRYPTO",
        title="BTC 报告",
        markdown=(
            "# BTC 报告\n\n"
            "新闻段落：" + ("宏观事件。" * 800) + "\n\n"
            "估值风险段落：估值处于中性区间，但下破关键均线会放大回撤风险。\n\n"
            "情绪段落：" + ("社交热度。" * 800)
        ),
        asset_dir=asset_dir,
    )

    context = ReportWorkerChatContextResolver(repo).build_report_context(
        report_id="report-1",
        worker_id="market_analyst",
        question="估值风险怎么看？",
    )

    assert "市场附录：量价结构改善。" in context.text
    assert "估值风险段落：估值处于中性区间" in context.text
    assert "新闻段落" not in context.text
    assert "情绪段落" not in context.text
