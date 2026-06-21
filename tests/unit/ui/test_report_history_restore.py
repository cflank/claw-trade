from __future__ import annotations

import json
import os
from pathlib import Path

from claw_trade.ui_backend.report_repository import ReportRepository
from claw_trade.web.state import restore_completed_workflow_reports


def _write_completed_run(run_root: Path, run_id: str) -> Path:
    run_dir = run_root / run_id
    (run_dir / "reports").mkdir(parents=True)
    (run_dir / "reports" / "final-report.md").write_text(
        "# BTC 报告\n\n## 最终结论\n维持观察，等待突破确认。\n\n完整正文",
        encoding="utf-8",
    )
    (run_dir / "state.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "status": "completed",
                "created_at": "2026-05-20T17:00:00Z",
                "updated_at": "2026-05-20T17:10:00Z",
                "request": {
                    "ticker": "BTC",
                    "company_name": "Bitcoin",
                    "market": "CRYPTO",
                    "profile": "CRYPTO",
                },
            }
        ),
        encoding="utf-8",
    )
    return run_dir


def test_restore_completed_workflow_reports_from_run_files(tmp_path: Path) -> None:
    run_root = tmp_path / "runs"
    _write_completed_run(run_root, "run-restore-1")
    repo = ReportRepository(deletion_index_path=run_root / ".ui-deleted-reports.json")

    restored = restore_completed_workflow_reports(repo, run_root)

    assert restored == 1
    items = repo.list_saved_reports()
    assert items[0]["id"] == "run-restore-1"
    assert items[0]["instrumentCode"] == "BTC"
    assert items[0]["summarySnippet"] == "维持观察，等待突破确认。"


def test_restore_completed_workflow_reports_restores_existing_pdf(tmp_path: Path) -> None:
    run_root = tmp_path / "runs"
    run_dir = _write_completed_run(run_root, "run-pdf-1")
    pdf_dir = run_dir / "reports" / "pdf"
    pdf_dir.mkdir()
    old_pdf_path = pdf_dir / "pdf_old.pdf"
    old_pdf_path.write_bytes(b"%PDF-1.7\n" + (b"B" * 700))
    os.utime(old_pdf_path, (1_000, 1_000))
    pdf_path = pdf_dir / "pdf_new.pdf"
    pdf_path.write_bytes(b"%PDF-1.7\n" + (b"A" * 700))
    os.utime(pdf_path, (2_000, 2_000))
    repo = ReportRepository(deletion_index_path=run_root / ".ui-deleted-reports.json")

    restore_completed_workflow_reports(repo, run_root)

    artifact = repo.latest_pdf_artifact("run-pdf-1")
    assert artifact is not None
    assert artifact.path == pdf_path.resolve()


def test_deleted_restored_report_stays_hidden_without_removing_run_files(tmp_path: Path) -> None:
    run_root = tmp_path / "runs"
    run_dir = _write_completed_run(run_root, "run-hidden-1")
    deletion_index = run_root / ".ui-deleted-reports.json"
    repo = ReportRepository(deletion_index_path=deletion_index)
    restore_completed_workflow_reports(repo, run_root)

    assert repo.delete_saved_report("run-hidden-1") is True

    reloaded = ReportRepository(deletion_index_path=deletion_index)
    restored = restore_completed_workflow_reports(reloaded, run_root)

    assert restored == 0
    assert reloaded.list_saved_reports() == []
    assert (run_dir / "reports" / "final-report.md").exists()
