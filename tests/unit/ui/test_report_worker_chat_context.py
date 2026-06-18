from __future__ import annotations

import pytest

from claw_trade.ui_backend.report_repository import ReportRepository, UiProductError
from claw_trade.ui_backend.report_worker_chat_context import ReportWorkerChatContextResolver


def test_report_worker_context_uses_selected_worker_appendix_and_saved_report_snippet(tmp_path) -> None:  # type: ignore[no-untyped-def]
    repo, reports_dir = _repo_with_saved_report(
        tmp_path,
        markdown=(
            "# BTC 报告\n\n"
            "市场段落：资金费率保持温和。\n\n"
            "风险段落：最大风险来自高杠杆回撤和成交量萎缩。\n\n"
            "无关段落：" + ("X" * 7_000)
        ),
    )
    _write_appendix(reports_dir, "03-market_analyst", "市场附录：不要给风险经理。")
    _write_appendix(reports_dir, "07-risk_moderator", "风险经理附录：只讨论仓位和回撤。")

    context = ReportWorkerChatContextResolver(repo).build_report_context(
        report_id="report-1",
        worker_id="risk_moderator",
        question="最大风险是什么？",
    )

    assert "风险经理附录：只讨论仓位和回撤。" in context.text
    assert "最大风险来自高杠杆回撤" in context.text
    assert "市场附录：不要给风险经理。" not in context.text
    assert "无关段落" not in context.text


def test_report_worker_context_rejects_non_pm_worker_without_reader_visible_appendix(tmp_path) -> None:  # type: ignore[no-untyped-def]
    repo, _reports_dir = _repo_with_saved_report(tmp_path, markdown="# BTC 报告\n\n正文。")

    with pytest.raises(UiProductError) as exc:
        ReportWorkerChatContextResolver(repo).build_report_context(
            report_id="report-1",
            worker_id="news_analyst",
            question="新闻怎么看？",
        )

    assert exc.value.code == "REPORT_WORKER_MATERIAL_NOT_FOUND"


def _repo_with_saved_report(tmp_path, *, markdown: str) -> tuple[ReportRepository, object]:  # type: ignore[no-untyped-def]
    reports_dir = tmp_path / "run-1" / "reports"
    asset_dir = reports_dir / "assets"
    asset_dir.mkdir(parents=True)
    repo = ReportRepository()
    repo.save_succeeded_report(
        report_id="report-1",
        instrument_code="BTC",
        market="CRYPTO",
        title="BTC 报告",
        markdown=markdown,
        asset_dir=asset_dir,
    )
    return repo, reports_dir


def _write_appendix(reports_dir, file_stem: str, text: str) -> None:  # type: ignore[no-untyped-def]
    appendix_dir = reports_dir / "worker-appendix"
    appendix_dir.mkdir(parents=True, exist_ok=True)
    (appendix_dir / f"{file_stem}.md").write_text(text, encoding="utf-8")
