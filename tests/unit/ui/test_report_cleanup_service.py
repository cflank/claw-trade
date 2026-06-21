from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from threading import Event, Lock
from types import SimpleNamespace

import pytest
from claw_trade.ui_backend.report_cleanup import (
    ReportCleanupResult,
    ReportCleanupScheduler,
    ReportCleanupService,
    ReportFileSendTracker,
)
from claw_trade.ui_backend.report_notification_service import ReportNotificationService
from claw_trade.ui_backend.report_queue import ReportTaskQueue
from claw_trade.ui_backend.report_repository import ReportRepository
from claw_trade.ui_backend.summary_builder import CompletionSummaryBuilder
from claw_trade.ui_backend.workflow_bridge import ReportWorkflowBridge


class _FakeRunner:
    def __init__(self) -> None:
        self.calls = 0

    def create_run(self, _request):  # type: ignore[no-untyped-def]
        self.calls += 1
        return f"run-live-{self.calls}"

    def load_state(self, _run_id: str) -> dict[str, object]:
        return {"status": "frontline_running"}


class _ReadyPdfService:
    def __init__(self, artifact_id: str) -> None:
        self._artifact_id = artifact_id

    def get_latest_record(self, _report_id: str) -> object:
        return SimpleNamespace(state="ready", pdf_artifact_id=self._artifact_id)


class _TrackingChannel:
    def __init__(self, tracker: ReportFileSendTracker, seen_active_ids: list[set[str]]) -> None:
        self._tracker = tracker
        self._seen_active_ids = seen_active_ids

    def get_channel_status(self, *, probe: bool = False) -> dict[str, object]:
        _ = probe
        return {"state": "connected", "canSendText": True, "canSendFile": True}

    def send_text(
        self,
        *,
        channel_kind: str,
        text: str,
        dedupe_key: str,
        target: str | None = None,
        account_id: str | None = None,
    ) -> dict[str, object]:
        _ = (channel_kind, text, dedupe_key, target, account_id)
        return {"sent": True}

    def send_report_file_via_channel(
        self,
        *,
        request_id: str,
        report_id: str,
        channel_kind: str,
        file_name: str,
        payload: bytes | None = None,
        file_path: Path | None = None,
        target: str | None = None,
        account_id: str | None = None,
    ) -> dict[str, object]:
        _ = (request_id, report_id, channel_kind, file_name, payload, file_path, target, account_id)
        self._seen_active_ids.append(self._tracker.active_report_ids())
        return {"sent": True, "messageId": "msg-1"}

    def resolve_default_report_file_target(self, *, channel_kind: str) -> tuple[str, str | None] | None:
        _ = channel_kind
        return None


class _RepositoryFailingForRun(ReportRepository):
    def __init__(self, failed_run_id: str) -> None:
        super().__init__()
        self._failed_run_id = failed_run_id

    def discard_deleted_report_id(self, report_id: str) -> bool:
        if report_id == self._failed_run_id:
            raise OSError("index write failed")
        return super().discard_deleted_report_id(report_id)


class _CleanupSchedulerCleanupProbe:
    def __init__(self, *, fail_calls: int = 0) -> None:
        self._fail_calls = fail_calls
        self._lock = Lock()
        self._changed = Event()
        self.retention_days: list[int] = []
        self.tombstone_cleanup_calls = 0
        self.exceptions: list[Exception] = []

    def cleanup_deleted_report_tombstones(self) -> ReportCleanupResult:
        self.tombstone_cleanup_calls += 1
        return ReportCleanupResult()

    def cleanup_expired_reports(self, *, retention_days: int) -> ReportCleanupResult:
        try:
            with self._lock:
                self.retention_days.append(retention_days)
                call_count = len(self.retention_days)
                self._changed.set()
                self._changed.clear()
            if call_count <= self._fail_calls:
                raise RuntimeError("cleanup failed")
            return ReportCleanupResult()
        except Exception as exc:
            with self._lock:
                self.exceptions.append(exc)
            raise

    def wait_for_calls(self, count: int, *, timeout: float = 1.0) -> bool:
        deadline = datetime.now(UTC).timestamp() + timeout
        while datetime.now(UTC).timestamp() < deadline:
            with self._lock:
                if len(self.retention_days) >= count:
                    return True
            self._changed.wait(timeout=0.01)
        return False


class _CleanupSchedulerSettingsProbe:
    def __init__(self, days: list[int]) -> None:
        self._days = days
        self.calls = 0

    def load_settings(self) -> dict[str, int]:
        day = self._days[min(self.calls, len(self._days) - 1)]
        self.calls += 1
        return {"reportRetentionDays": day}


class _BlockingCleanupSchedulerCleanupProbe:
    def __init__(self) -> None:
        self._lock = Lock()
        self._changed = Event()
        self._release = Event()
        self.retention_days: list[int] = []
        self.tombstone_cleanup_calls = 0

    def cleanup_deleted_report_tombstones(self) -> ReportCleanupResult:
        self.tombstone_cleanup_calls += 1
        return ReportCleanupResult()

    def cleanup_expired_reports(self, *, retention_days: int) -> ReportCleanupResult:
        with self._lock:
            self.retention_days.append(retention_days)
            self._changed.set()
            self._changed.clear()
        self._release.wait()
        return ReportCleanupResult()

    def release(self) -> None:
        self._release.set()

    def wait_for_calls(self, count: int, *, timeout: float = 1.0) -> bool:
        deadline = datetime.now(UTC).timestamp() + timeout
        while datetime.now(UTC).timestamp() < deadline:
            with self._lock:
                if len(self.retention_days) >= count:
                    return True
            self._changed.wait(timeout=0.01)
        return False


def test_cleanup_scheduler_start_calls_cleanup_once() -> None:
    cleanup = _CleanupSchedulerCleanupProbe()
    settings = _CleanupSchedulerSettingsProbe([14])
    scheduler = ReportCleanupScheduler(
        cleanup_service=cleanup,  # type: ignore[arg-type]
        settings_service=settings,
        initial_delay_seconds=0,
        interval_seconds=60,
    )

    scheduler.start()
    try:
        assert cleanup.wait_for_calls(1)
    finally:
        scheduler.stop()

    assert cleanup.retention_days == [14]
    assert cleanup.tombstone_cleanup_calls == 1
    assert cleanup.exceptions == []
    assert settings.calls == 1


def test_cleanup_scheduler_stop_exits_cleanly() -> None:
    cleanup = _CleanupSchedulerCleanupProbe()
    scheduler = ReportCleanupScheduler(
        cleanup_service=cleanup,  # type: ignore[arg-type]
        settings_service=_CleanupSchedulerSettingsProbe([7]),
        initial_delay_seconds=60,
        interval_seconds=60,
    )

    scheduler.start()
    scheduler.stop(timeout_seconds=1)

    thread = scheduler._thread  # noqa: SLF001
    assert thread is not None
    assert not thread.is_alive()
    assert cleanup.retention_days == []


def test_cleanup_scheduler_start_raises_while_previous_thread_is_stopping() -> None:
    cleanup = _BlockingCleanupSchedulerCleanupProbe()
    scheduler = ReportCleanupScheduler(
        cleanup_service=cleanup,  # type: ignore[arg-type]
        settings_service=_CleanupSchedulerSettingsProbe([7, 14]),
        initial_delay_seconds=0,
        interval_seconds=60,
    )

    scheduler.start()
    assert cleanup.wait_for_calls(1)
    scheduler.stop(timeout_seconds=0.001)

    with pytest.raises(RuntimeError, match="still stopping"):
        scheduler.start()

    cleanup.release()
    scheduler.stop(timeout_seconds=1)
    scheduler.start()
    try:
        assert cleanup.wait_for_calls(2)
    finally:
        scheduler.stop()

    assert cleanup.retention_days == [7, 14]


def test_cleanup_scheduler_exception_does_not_crash_scheduler() -> None:
    cleanup = _CleanupSchedulerCleanupProbe(fail_calls=1)
    scheduler = ReportCleanupScheduler(
        cleanup_service=cleanup,  # type: ignore[arg-type]
        settings_service=_CleanupSchedulerSettingsProbe([7, 30]),
        initial_delay_seconds=0,
        interval_seconds=0.01,
    )

    scheduler.start()
    try:
        assert cleanup.wait_for_calls(2)
    finally:
        scheduler.stop()

    assert cleanup.retention_days[:2] == [7, 30]
    assert [type(exc) for exc in cleanup.exceptions] == [RuntimeError]


def test_expired_inactive_run_deleted(tmp_path: Path) -> None:
    service, run_root, _, _, _ = _service(tmp_path)
    _write_run(run_root, "run-old", updated_at="2026-05-01T00:00:00Z")

    result = service.cleanup_expired_reports(retention_days=30)

    assert result.deletedRunIds == ["run-old"]
    assert not (run_root / "run-old").exists()
    assert result.deletedBytesApprox > 0


def test_recent_run_kept(tmp_path: Path) -> None:
    service, run_root, _, _, _ = _service(tmp_path)
    _write_run(run_root, "run-recent", updated_at="2026-06-15T00:00:00Z")

    result = service.cleanup_expired_reports(retention_days=30)

    assert result.deletedRunIds == []
    assert (run_root / "run-recent").exists()


def test_symlink_run_dir_outside_root_is_not_scanned(tmp_path: Path) -> None:
    service, run_root, _, _, _ = _service(tmp_path)
    run_root.mkdir(parents=True)
    outside = tmp_path / "outside" / "run-escape"
    _write_run(tmp_path / "outside", "run-escape", updated_at="2026-05-01T00:00:00Z")
    link = run_root / "run-escape"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    result = service.cleanup_expired_reports(retention_days=30)

    assert result.deletedRunIds == []
    assert result.failedRunIds == []
    assert (outside / "state.json").exists()
    assert link.exists()


def test_symlink_run_dir_inside_root_to_non_run_target_is_not_scanned(tmp_path: Path) -> None:
    service, run_root, _, _, _ = _service(tmp_path)
    run_root.mkdir(parents=True)
    target = run_root / "not-a-run-dir"
    target.mkdir()
    (target / "state.json").write_text(
        json.dumps(
            {
                "run_id": "run-link",
                "status": "completed",
                "updated_at": "2026-05-01T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    link = run_root / "run-link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    result = service.cleanup_expired_reports(retention_days=30)

    assert result.deletedRunIds == []
    assert result.failedRunIds == []
    assert (target / "state.json").exists()
    assert link.exists()


def test_running_internal_run_id_skipped_even_without_user_report_id(tmp_path: Path) -> None:
    queue = ReportTaskQueue(ReportWorkflowBridge(_FakeRunner()))
    queue.enqueue_report_task(request_id="req-1", task_input=_task_input("AAPL"), source="manual")
    snapshot = queue.get_report_queue_snapshot_for_user()
    assert "reportId" not in snapshot["runningTask"]
    service, run_root, _, _, _ = _service(tmp_path, protected=queue.protected_run_ids_for_cleanup)
    _write_run(run_root, "run-live-1", updated_at="2026-05-01T00:00:00Z")

    result = service.cleanup_expired_reports(retention_days=30)

    assert result.skippedRunIds == ["run-live-1"]
    assert (run_root / "run-live-1").exists()


def test_protected_run_ids_provider_returns_snapshot_copy() -> None:
    queue = ReportTaskQueue(ReportWorkflowBridge(_FakeRunner()))
    queue.enqueue_report_task(request_id="req-1", task_input=_task_input("AAPL"), source="manual")

    snapshot = queue.protected_run_ids_for_cleanup()
    snapshot.add("run-added-by-caller")

    assert queue.protected_run_ids_for_cleanup() == {"run-live-1"}


def test_queued_task_without_run_id_does_not_crash_cleanup(tmp_path: Path) -> None:
    queue = ReportTaskQueue(ReportWorkflowBridge(_FakeRunner()))
    queue.enqueue_report_task(request_id="req-1", task_input=_task_input("AAPL"), source="manual")
    queue.enqueue_report_task(request_id="req-2", task_input=_task_input("TSLA"), source="manual")
    service, run_root, _, _, _ = _service(tmp_path, protected=queue.protected_run_ids_for_cleanup)
    _write_run(run_root, "run-old", updated_at="2026-05-01T00:00:00Z")

    result = service.cleanup_expired_reports(retention_days=30)

    assert result.deletedRunIds == ["run-old"]


def test_old_persisted_non_terminal_run_skipped_after_restart(tmp_path: Path) -> None:
    service, run_root, _, _, _ = _service(tmp_path)
    _write_run(run_root, "run-interrupted", status="frontline_running", updated_at="2026-05-01T00:00:00Z")

    result = service.cleanup_expired_reports(retention_days=30)

    assert result.skippedRunIds == ["run-interrupted"]
    assert (run_root / "run-interrupted").exists()


def test_in_flight_send_report_skipped(tmp_path: Path) -> None:
    tracker = ReportFileSendTracker()
    service, run_root, _, _, _ = _service(tmp_path, in_flight=tracker.active_report_ids)
    _write_run(run_root, "run-sending", updated_at="2026-05-01T00:00:00Z")

    with tracker.track("run-sending"):
        result = service.cleanup_expired_reports(retention_days=30)

    assert result.skippedRunIds == ["run-sending"]
    assert (run_root / "run-sending").exists()


def test_full_report_file_request_marks_report_as_in_flight() -> None:
    tracker = ReportFileSendTracker()
    repo = ReportRepository()
    repo.save_succeeded_report(
        report_id="run-sending",
        instrument_code="BTC",
        market="CRYPTO",
        title="BTC 报告",
        markdown="正文",
    )
    artifact = repo.write_pdf_artifact("run-sending", b"%PDF-1.7\n" + (b"A" * 700))
    seen_active_ids: list[set[str]] = []
    service = ReportNotificationService(
        repo,
        CompletionSummaryBuilder(repo),
        _ReadyPdfService(artifact.id),  # type: ignore[arg-type]
        _TrackingChannel(tracker, seen_active_ids),
        file_send_tracker=tracker,
    )

    result = service.request_full_report_file("run-sending", "req-send", target="sender-1")

    assert result["sent"] is True
    assert seen_active_ids == [{"run-sending"}]
    assert tracker.active_report_ids() == set()


def test_explicit_delete_ignores_retention_but_respects_protected_status(tmp_path: Path) -> None:
    service, run_root, _, _, _ = _service(tmp_path, protected=lambda: {"run-protected"})
    _write_run(run_root, "run-recent", updated_at="2026-06-19T00:00:00Z")
    _write_run(run_root, "run-protected", updated_at="2026-05-01T00:00:00Z")

    result = service.delete_report_runs(["run-recent", "run-protected"])

    assert result.deletedRunIds == ["run-recent"]
    assert result.skippedRunIds == ["run-protected"]
    assert not (run_root / "run-recent").exists()
    assert (run_root / "run-protected").exists()


def test_run_and_openviking_workflow_dirs_deleted(tmp_path: Path) -> None:
    service, run_root, workflow_root, _, _ = _service(tmp_path)
    _write_run(run_root, "run-old", updated_at="2026-05-01T00:00:00Z")
    (workflow_root / "run-old").mkdir(parents=True)
    (workflow_root / "run-old" / "workflow.json").write_text("{}", encoding="utf-8")

    result = service.cleanup_expired_reports(retention_days=30)

    assert result.deletedRunIds == ["run-old"]
    assert not (run_root / "run-old").exists()
    assert not (workflow_root / "run-old").exists()


def test_openviking_workflow_symlink_does_not_delete_target(tmp_path: Path) -> None:
    service, run_root, workflow_root, _, _ = _service(tmp_path)
    _write_run(run_root, "run-old", updated_at="2026-05-01T00:00:00Z")
    target = tmp_path / "workflow-target"
    target.mkdir()
    marker = target / "workflow.json"
    marker.write_text("run-old", encoding="utf-8")
    link = workflow_root / "run-old"
    link.parent.mkdir(parents=True)
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    result = service.cleanup_expired_reports(retention_days=30)

    assert result.deletedRunIds == ["run-old"]
    assert marker.exists()
    assert not link.exists()


def test_openclaw_session_file_with_only_run_id_deleted(tmp_path: Path) -> None:
    service, run_root, _, openclaw_root, _ = _service(tmp_path)
    _write_run(run_root, "run-old", updated_at="2026-05-01T00:00:00Z")
    session_path = openclaw_root / "agents" / "market" / "sessions" / "single-worker-s1.jsonl"
    session_path.parent.mkdir(parents=True)
    session_path.write_text('{"run_id":"run-old"}\n', encoding="utf-8")

    service.cleanup_expired_reports(retention_days=30)

    assert not session_path.exists()


def test_openclaw_session_file_with_derived_run_ids_deleted(tmp_path: Path) -> None:
    run_id = "run-20260620-134154-9ac325d0"
    service, run_root, _, openclaw_root, _ = _service(tmp_path)
    _write_run(run_root, run_id, updated_at="2026-05-01T00:00:00Z")
    session_path = openclaw_root / "agents" / "market" / "sessions" / "single-worker-s1.jsonl"
    session_path.parent.mkdir(parents=True)
    session_path.write_text(
        "\n".join(
            [
                run_id,
                f"{run_id}-frontline-t00-social_analyst-20260620T134154193668Z-9c0511e3",
                f"{run_id}-frontline-t00-social_analyst-20260620T134154193668Z-9c0511e3__tool",
            ]
        ),
        encoding="utf-8",
    )

    service.cleanup_expired_reports(retention_days=30)

    assert not session_path.exists()


def test_openclaw_session_sidecar_removed_only_after_matching_primary(tmp_path: Path) -> None:
    service, run_root, _, openclaw_root, _ = _service(tmp_path)
    _write_run(run_root, "run-old", updated_at="2026-05-01T00:00:00Z")
    sessions_dir = openclaw_root / "agents" / "market" / "sessions"
    sessions_dir.mkdir(parents=True)
    primary = sessions_dir / "single-worker-s1.trajectory.jsonl"
    sidecar = sessions_dir / "single-worker-s1.trajectory-path.json"
    orphan_sidecar = sessions_dir / "single-worker-orphan.trajectory-path.json"
    primary.write_text('{"run_id":"run-old"}\n', encoding="utf-8")
    sidecar.write_text(json.dumps({"path": str(primary)}), encoding="utf-8")
    orphan_sidecar.write_text(json.dumps({"path": "orphan"}), encoding="utf-8")

    service.cleanup_expired_reports(retention_days=30)

    assert not primary.exists()
    assert not sidecar.exists()
    assert orphan_sidecar.exists()


def test_openclaw_non_single_worker_session_file_with_only_run_id_kept(tmp_path: Path) -> None:
    service, run_root, _, openclaw_root, _ = _service(tmp_path)
    _write_run(run_root, "run-old", updated_at="2026-05-01T00:00:00Z")
    session_path = openclaw_root / "agents" / "market" / "sessions" / "chat-session.jsonl"
    session_path.parent.mkdir(parents=True)
    session_path.write_text('{"run_id":"run-old"}\n', encoding="utf-8")

    service.cleanup_expired_reports(retention_days=30)

    assert session_path.exists()


def test_openclaw_mixed_run_session_file_skipped(tmp_path: Path) -> None:
    service, run_root, _, openclaw_root, _ = _service(tmp_path)
    _write_run(run_root, "run-old", updated_at="2026-05-01T00:00:00Z")
    session_path = openclaw_root / "agents" / "market" / "sessions" / "single-worker-s1.jsonl"
    session_path.parent.mkdir(parents=True)
    session_path.write_text("run-old and run-other", encoding="utf-8")

    service.cleanup_expired_reports(retention_days=30)

    assert session_path.exists()


def test_openclaw_sessions_symlink_dir_is_not_traversed(tmp_path: Path) -> None:
    service, run_root, _, openclaw_root, _ = _service(tmp_path)
    _write_run(run_root, "run-old", updated_at="2026-05-01T00:00:00Z")
    target = tmp_path / "session-target"
    target.mkdir()
    target_file = target / "single-worker-s1.jsonl"
    target_file.write_text('{"run_id":"run-old"}\n', encoding="utf-8")
    sessions_link = openclaw_root / "agents" / "market" / "sessions"
    sessions_link.parent.mkdir(parents=True)
    try:
        sessions_link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    result = service.cleanup_expired_reports(retention_days=30)

    assert result.deletedRunIds == ["run-old"]
    assert target_file.exists()
    assert sessions_link.exists()


def test_shared_delivery_queue_list_prunes_matching_item(tmp_path: Path) -> None:
    service, run_root, _, openclaw_root, _ = _service(tmp_path)
    _write_run(run_root, "run-old", updated_at="2026-05-01T00:00:00Z")
    queue_path = openclaw_root / "delivery-queue" / "shared.json"
    queue_path.parent.mkdir(parents=True)
    queue_path.write_text(
        json.dumps([{"reportId": "run-old"}, {"reportId": "run-other"}]),
        encoding="utf-8",
    )

    result = service.cleanup_expired_reports(retention_days=30)

    assert queue_path.exists()
    assert result.warnings == []
    assert json.loads(queue_path.read_text(encoding="utf-8")) == [{"reportId": "run-other"}]


def test_shared_delivery_queue_list_removed_after_batch_delete(tmp_path: Path) -> None:
    service, run_root, _, openclaw_root, _ = _service(tmp_path)
    _write_run(run_root, "run-old", updated_at="2026-05-01T00:00:00Z")
    _write_run(run_root, "run-other", updated_at="2026-05-01T00:00:00Z")
    queue_path = openclaw_root / "delivery-queue" / "shared.json"
    queue_path.parent.mkdir(parents=True)
    queue_path.write_text(
        json.dumps([{"reportId": "run-old"}, {"reportId": "run-other"}]),
        encoding="utf-8",
    )

    result = service.delete_report_runs(["run-old", "run-other"])

    assert result.deletedRunIds == ["run-old", "run-other"]
    assert not queue_path.exists()


def test_delivery_queue_superseded_file_removed(tmp_path: Path) -> None:
    service, run_root, _, openclaw_root, _ = _service(tmp_path)
    _write_run(run_root, "run-old", updated_at="2026-05-01T00:00:00Z")
    queue_path = openclaw_root / "delivery-queue" / "old.json.superseded-1"
    queue_path.parent.mkdir(parents=True)
    queue_path.write_text(json.dumps({"mediaUrl": "runs/run-old/reports/pdf/a.pdf"}), encoding="utf-8")

    result = service.cleanup_expired_reports(retention_days=30)

    assert result.deletedRunIds == ["run-old"]
    assert not queue_path.exists()


def test_disabled_delivery_queue_file_removed(tmp_path: Path) -> None:
    service, run_root, _, openclaw_root, _ = _service(tmp_path)
    _write_run(run_root, "run-old", updated_at="2026-05-01T00:00:00Z")
    queue_path = openclaw_root / "delivery-queue.disabled-test" / "old.json"
    queue_path.parent.mkdir(parents=True)
    queue_path.write_text(json.dumps({"mediaUrl": "runs/run-old/reports/pdf/a.pdf"}), encoding="utf-8")

    result = service.cleanup_expired_reports(retention_days=30)

    assert result.deletedRunIds == ["run-old"]
    assert not queue_path.exists()


def test_delivery_queue_symlink_dir_is_not_traversed(tmp_path: Path) -> None:
    service, run_root, _, openclaw_root, _ = _service(tmp_path)
    _write_run(run_root, "run-old", updated_at="2026-05-01T00:00:00Z")
    target = tmp_path / "delivery-target"
    target.mkdir()
    target_file = target / "entry.json"
    target_file.write_text(json.dumps({"reportId": "run-old"}), encoding="utf-8")
    queue_link = openclaw_root / "delivery-queue"
    queue_link.parent.mkdir(parents=True)
    try:
        queue_link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    result = service.cleanup_expired_reports(retention_days=30)

    assert result.deletedRunIds == ["run-old"]
    assert target_file.exists()
    assert queue_link.exists()


def test_path_traversal_and_non_run_ids_rejected(tmp_path: Path) -> None:
    service, _, _, _, _ = _service(tmp_path)

    result = service.delete_report_runs(["../run-old", "abc", "run-/bad", ""])

    assert result.deletedRunIds == []
    assert result.failedRunIds == ["../run-old", "abc", "run-/bad", "<empty>"]


def test_tombstone_removed_after_hard_delete(tmp_path: Path) -> None:
    repo = ReportRepository(deletion_index_path=tmp_path / "runs" / ".ui-deleted-reports.json")
    repo.save_succeeded_report(
        report_id="run-old",
        instrument_code="BTC",
        market="CRYPTO",
        title="BTC 报告",
        markdown="正文",
    )
    assert repo.delete_saved_report("run-old") is True
    service, run_root, _, _, _ = _service(tmp_path, repository=repo)
    _write_run(run_root, "run-old", updated_at="2026-05-01T00:00:00Z")

    result = service.delete_report_runs(["run-old"])

    assert result.deletedRunIds == ["run-old"]
    assert ReportRepository(deletion_index_path=tmp_path / "runs" / ".ui-deleted-reports.json").is_deleted_report(
        "run-old"
    ) is False


def test_legacy_tombstoned_report_is_hard_deleted(tmp_path: Path) -> None:
    repo = ReportRepository(deletion_index_path=tmp_path / "runs" / ".ui-deleted-reports.json")
    repo.save_succeeded_report(
        report_id="run-old",
        instrument_code="BTC",
        market="CRYPTO",
        title="BTC 报告",
        markdown="正文",
    )
    repo.delete_saved_report("run-old")
    service, run_root, workflow_root, openclaw_root, _ = _service(tmp_path, repository=repo)
    _write_run(run_root, "run-old", updated_at="2026-06-19T00:00:00Z")
    (workflow_root / "run-old").mkdir(parents=True)
    session_path = openclaw_root / "agents" / "market" / "sessions" / "single-worker-s1.jsonl"
    session_path.parent.mkdir(parents=True)
    session_path.write_text('{"run_id":"run-old"}\n', encoding="utf-8")

    result = service.cleanup_deleted_report_tombstones()

    assert result.deletedRunIds == ["run-old"]
    assert not (run_root / "run-old").exists()
    assert not (workflow_root / "run-old").exists()
    assert not session_path.exists()
    assert ReportRepository(deletion_index_path=run_root / ".ui-deleted-reports.json").deleted_report_ids() == ()


def test_tombstoned_report_without_run_dir_still_cleans_related_artifacts(tmp_path: Path) -> None:
    repo = ReportRepository(deletion_index_path=tmp_path / "runs" / ".ui-deleted-reports.json")
    repo.delete_saved_report("run-partial")
    service, run_root, workflow_root, openclaw_root, _ = _service(tmp_path, repository=repo)
    (workflow_root / "run-partial").mkdir(parents=True)
    session_path = openclaw_root / "agents" / "market" / "sessions" / "single-worker-partial.jsonl"
    session_path.parent.mkdir(parents=True)
    session_path.write_text('{"run_id":"run-partial"}\n', encoding="utf-8")

    result = service.cleanup_deleted_report_tombstones()

    assert result.deletedRunIds == ["run-partial"]
    assert not (run_root / "run-partial").exists()
    assert not (workflow_root / "run-partial").exists()
    assert not session_path.exists()
    assert ReportRepository(deletion_index_path=run_root / ".ui-deleted-reports.json").deleted_report_ids() == ()


def test_repository_cleanup_failure_is_returned_without_aborting_batch(tmp_path: Path) -> None:
    repo = _RepositoryFailingForRun("run-bad-index")
    service, run_root, _, _, _ = _service(tmp_path, repository=repo)
    _write_run(run_root, "run-bad-index", updated_at="2026-05-01T00:00:00Z")
    _write_run(run_root, "run-good", updated_at="2026-05-01T00:00:00Z")

    result = service.delete_report_runs(["run-bad-index", "run-good"])

    assert result.failedRunIds == ["run-bad-index"]
    assert result.deletedRunIds == ["run-good"]
    assert "清理索引失败" in result.warnings[0]
    assert not (run_root / "run-bad-index").exists()
    assert not (run_root / "run-good").exists()


def _service(
    tmp_path: Path,
    *,
    repository: ReportRepository | None = None,
    protected=lambda: set(),  # type: ignore[no-untyped-def]
    in_flight=lambda: set(),  # type: ignore[no-untyped-def]
) -> tuple[ReportCleanupService, Path, Path, Path, ReportRepository]:
    run_root = tmp_path / "runs"
    workflow_root = tmp_path / "openviking" / "viking" / "default" / "resources" / "workflow"
    openclaw_root = tmp_path / "openclaw-state"
    repo = repository or ReportRepository(deletion_index_path=run_root / ".ui-deleted-reports.json")
    return (
        ReportCleanupService(
            run_root=run_root,
            openviking_workflow_root=workflow_root,
            openclaw_state_root=openclaw_root,
            repository=repo,
            protected_run_ids_provider=protected,
            in_flight_report_ids_provider=in_flight,
            now_provider=lambda: datetime(2026, 6, 20, tzinfo=UTC),
        ),
        run_root,
        workflow_root,
        openclaw_root,
        repo,
    )


def _write_run(
    run_root: Path,
    run_id: str,
    *,
    status: str = "completed",
    updated_at: str = "2026-05-01T00:00:00Z",
) -> Path:
    run_dir = run_root / run_id
    (run_dir / "reports").mkdir(parents=True)
    (run_dir / "reports" / "final-report.md").write_text("# 报告\n正文", encoding="utf-8")
    (run_dir / "state.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "status": status,
                "created_at": "2026-05-01T00:00:00Z",
                "updated_at": updated_at,
            }
        ),
        encoding="utf-8",
    )
    return run_dir


def _task_input(code: str) -> dict[str, object]:
    return {
        "instrumentCode": code,
        "instrumentName": code,
        "market": "US",
        "companyName": code,
        "currencySymbol": "$",
        "startDate": "2026-05-01",
        "endDate": "2026-05-19",
        "currentDate": "2026-05-19",
        "workflowSettings": {
            "maxDebateRounds": 1,
            "maxRiskDiscussRounds": 1,
            "frontlineExecutionMode": "parallel",
            "defaultProfile": "US",
            "defaultMarket": "US",
            "defaultCurrency": "USD",
            "defaultCurrencySymbol": "$",
        },
    }
