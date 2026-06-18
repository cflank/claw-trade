from __future__ import annotations

import pytest

from claw_trade.ui_backend.report_repository import ReportRepository, UiProductError
from claw_trade.ui_backend.report_worker_chat_context import ReportWorkerChatContextResolver


def test_report_worker_chat_does_not_use_raw_outputs_when_appendix_is_missing(tmp_path) -> None:  # type: ignore[no-untyped-def]
    repo, run_dir = _repo_with_saved_report(tmp_path)
    raw_dir = run_dir / "calls" / "call-risk"
    raw_dir.mkdir(parents=True)
    (raw_dir / "raw-output.md").write_text("RAW OUTPUT SHOULD NOT BECOME CHAT CONTEXT", encoding="utf-8")
    (run_dir / "openviking" / "evidence").mkdir(parents=True)
    (run_dir / "openviking" / "evidence" / "risk.md").write_text("EVIDENCE SHOULD NOT BE READ", encoding="utf-8")

    with pytest.raises(UiProductError) as exc:
        ReportWorkerChatContextResolver(repo).build_report_context(
            report_id="report-1",
            worker_id="risk_moderator",
            question="最大风险是什么？",
        )

    assert exc.value.code == "REPORT_WORKER_MATERIAL_NOT_FOUND"


def test_report_worker_chat_context_only_uses_saved_report_and_selected_appendix(tmp_path) -> None:  # type: ignore[no-untyped-def]
    repo, run_dir = _repo_with_saved_report(tmp_path)
    appendix_dir = run_dir / "reports" / "worker-appendix"
    appendix_dir.mkdir(parents=True)
    (appendix_dir / "07-risk_moderator.md").write_text("风险经理附录：reader-visible L1。", encoding="utf-8")
    (run_dir / "calls" / "call-risk").mkdir(parents=True)
    (run_dir / "calls" / "call-risk" / "raw-output.md").write_text("RAW OUTPUT SHOULD NOT APPEAR", encoding="utf-8")
    (run_dir / "openviking").mkdir(parents=True, exist_ok=True)
    (run_dir / "openviking" / "approved-manifest.json").write_text("MANIFEST SHOULD NOT APPEAR", encoding="utf-8")

    context = ReportWorkerChatContextResolver(repo).build_report_context(
        report_id="report-1",
        worker_id="risk_moderator",
        question="风险是什么？",
    )

    assert "风险经理附录：reader-visible L1。" in context.text
    assert "正式报告中的风险片段" in context.text
    assert "RAW OUTPUT SHOULD NOT APPEAR" not in context.text
    assert "MANIFEST SHOULD NOT APPEAR" not in context.text


def _repo_with_saved_report(tmp_path) -> tuple[ReportRepository, object]:  # type: ignore[no-untyped-def]
    run_dir = tmp_path / "run-1"
    asset_dir = run_dir / "reports" / "assets"
    asset_dir.mkdir(parents=True)
    repo = ReportRepository()
    repo.save_succeeded_report(
        report_id="report-1",
        instrument_code="BTC",
        market="CRYPTO",
        title="BTC 报告",
        markdown="# BTC 报告\n\n正式报告中的风险片段：高杠杆是主要风险。",
        asset_dir=asset_dir,
    )
    return repo, run_dir
