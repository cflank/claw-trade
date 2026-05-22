from __future__ import annotations

from claw_trade.ui_backend.report_repository import ReportRepository


def test_report_detail_user_dto_matches_design_shape_and_no_internal_fields() -> None:
    repo = ReportRepository()
    repo.save_succeeded_report(
        report_id="r-detail",
        instrument_code="TSLA",
        instrument_name="Tesla",
        market="US",
        title="TSLA 投研报告",
        markdown="# 报告\nBTC 的 L2 生态仍在早期，Layer 2 指标需要继续跟踪。",
        summary_snippet="核心结论",
    )
    detail = repo.get_report_detail(
        "r-detail",
        completion_summary={"finalConclusion": "维持观察"},
        chart_evidence={"reportId": "r-detail", "summary": "missing", "items": []},
        data_source_events=[],
        pdf_export={"state": "failed", "userMessage": "PDF 暂不可用", "updatedAt": "2026-05-19T00:00:00+00:00"},
    )
    assert set(detail.keys()) == {
        "report",
        "markdown",
        "completionSummary",
        "dataSourceEvents",
        "chartEvidence",
        "assets",
    }
    report = detail["report"]
    assert set(report.keys()) == {
        "id",
        "instrumentCode",
        "instrumentName",
        "market",
        "title",
        "generatedAt",
        "summarySnippet",
    }
    assert len(detail["assets"]) == 2
    for asset in detail["assets"]:
        assert set(asset.keys()) == {"kind", "available", "status", "userMessage", "updatedAt"}
