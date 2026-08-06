from __future__ import annotations

import json
import os
from pathlib import Path
from threading import Event

from claw_trade.ui_backend.report_repository import ReportRepository
from claw_trade.ui_backend.wechat_delivery_store import WechatDeliveryStore
from claw_trade.web.state import (
    _restore_wechat_delivery_failures,
    _start_wechat_delivery_recovery_watcher,
    restore_completed_workflow_reports,
)


def _write_completed_run(run_root: Path, run_id: str, *, origin_context_id: str | None = None) -> Path:
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
                    "ui_origin_context_id": origin_context_id,
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


def test_restore_completed_workflow_reports_does_not_restore_pdf_artifacts(tmp_path: Path) -> None:
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

    assert repo.get_report("run-pdf-1") is not None
    assert old_pdf_path.exists()
    assert pdf_path.exists()
    assert not hasattr(repo, "latest_pdf_artifact")


def test_restore_completed_workflow_reports_restores_wechat_origin_context(tmp_path: Path) -> None:
    run_root = tmp_path / "runs"
    _write_completed_run(
        run_root,
        "run-wechat-origin",
        origin_context_id="wechat_clawbot:account-1:sender-1",
    )
    repo = ReportRepository(deletion_index_path=run_root / ".ui-deleted-reports.json")

    restore_completed_workflow_reports(repo, run_root)

    report = repo.get_report("run-wechat-origin")
    assert report is not None
    assert report.origin_context_id == "wechat_clawbot:account-1:sender-1"
    assert repo.list_saved_reports()[0]["canForwardToChannel"] is True


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


def test_discarded_tombstone_allows_restore_when_run_files_still_exist(tmp_path: Path) -> None:
    run_root = tmp_path / "runs"
    _write_completed_run(run_root, "run-visible-again")
    deletion_index = run_root / ".ui-deleted-reports.json"
    repo = ReportRepository(deletion_index_path=deletion_index)
    restore_completed_workflow_reports(repo, run_root)
    repo.delete_saved_report("run-visible-again")
    repo.discard_deleted_report_id("run-visible-again")

    reloaded = ReportRepository(deletion_index_path=deletion_index)
    restored = restore_completed_workflow_reports(reloaded, run_root)

    assert restored == 1
    assert reloaded.list_saved_reports()[0]["id"] == "run-visible-again"


def test_wechat_delivery_watcher_recovers_report_that_finishes_after_ui_restart(tmp_path: Path) -> None:
    run_root = tmp_path / "runs"
    run_dir = run_root / "run-active"
    run_dir.mkdir(parents=True)
    intent_id = "intent-active"
    (run_dir / "state.json").write_text(
        json.dumps(
            {
                "run_id": "run-active",
                "status": "running",
                "request": {"ticker": "BTC", "ui_notification_intent_id": intent_id},
            }
        ),
        encoding="utf-8",
    )
    store = WechatDeliveryStore(run_root / ".ui-wechat-delivery.json")
    store.create_waiting_report(intent_id)
    repository = ReportRepository()

    class _NotificationService:
        calls = 0

        def recover_completed_deliveries(self) -> int:
            self.calls += 1
            if self.calls == 1:
                raise OSError("transient delivery recovery failure")
            recovered = 0
            for record in store.list_delivery_records(states={"waiting_report"}):
                report_id = str(record.get("report_id") or "")
                if report_id and repository.get_report(report_id) is not None:
                    store.mark_report_pending(str(record["intent_id"]), report_id=report_id)
                    stop_event.set()
                    recovered += 1
            return recovered

        def retry_pending(self) -> dict[str, int]:
            return {"attempted": 0, "sent": 0}

    def finish_report(_seconds: float) -> None:
        _write_completed_run(run_root, "run-active")
        state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
        state["request"]["ui_notification_intent_id"] = intent_id
        (run_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")

    stop_event = Event()
    watcher = _start_wechat_delivery_recovery_watcher(
        delivery_store=store,
        notification_service=_NotificationService(),  # type: ignore[arg-type]
        repository=repository,
        run_root=run_root,
        sleep=finish_report,
        stop_event=stop_event,
    )

    assert watcher is not None
    watcher.join(timeout=2)
    assert watcher.is_alive() is False
    assert repository.get_report("run-active") is not None
    assert store.get_delivery_status(intent_id)["state"] == "pending"


def test_failed_run_restores_waiting_delivery_as_failed(tmp_path: Path) -> None:
    run_root = tmp_path / "runs"
    run_dir = run_root / "run-failed"
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text(
        json.dumps(
            {
                "run_id": "run-failed",
                "status": "failed",
                "request": {"ui_notification_intent_id": "intent-failed"},
            }
        ),
        encoding="utf-8",
    )
    store = WechatDeliveryStore(run_root / ".ui-wechat-delivery.json")
    store.create_waiting_report("intent-failed")

    restored = _restore_wechat_delivery_failures(store, run_root)

    assert restored == 1
    assert store.get_delivery_status("intent-failed")["state"] == "failed"
