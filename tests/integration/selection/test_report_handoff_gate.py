from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
from claw_trade.config.report_workflow_settings import ReportWorkflowSettings
from claw_trade.selection.confirmation import (
    SelectionConfirmationController,
    SelectionConfirmationError,
    SelectionConfirmRequest,
)
from claw_trade.selection.models import (
    CandidateCacheManifest,
    CandidateCacheReadbackStatus,
    CandidateCacheRef,
    SelectionDataRun,
    SelectionDataRunStatus,
    SelectionMarket,
    SelectionProfile,
    SelectionRunPlan,
    SelectionTriggerSource,
)
from claw_trade.selection.store import (
    SelectionDataRunRecord,
    SelectionRunIntegrity,
    SelectionRunStore,
)
from claw_trade.ui_backend.report_queue import ReportTaskQueue
from claw_trade.ui_backend.workflow_bridge import ReportWorkflowBridge


@dataclass
class _FakeWorkflowState:
    status: str = "frontline_running"


class _FakeWorkflowRunner:
    def __init__(self) -> None:
        self.create_calls = 0

    def create_run(self, request):  # type: ignore[no-untyped-def]
        self.create_calls += 1
        _ = request
        return f"run-{self.create_calls}"

    def load_state(self, run_id: str) -> _FakeWorkflowState:
        _ = run_id
        return _FakeWorkflowState()


def _build_store(
    *,
    expires_at: str = "2026-05-27T09:00:00+00:00",
    cache_sha: str = "c" * 64,
    manifest_sha: str | None = None,
    source_lineage_refs: tuple[str, ...] = ("lineage://a",),
    integrity: SelectionRunIntegrity | None = None,
    include_manifest: bool = True,
) -> SelectionRunStore:
    store = SelectionRunStore()
    run_id = "sel-run-09-gate"
    final_manifest_sha = manifest_sha if manifest_sha is not None else cache_sha
    manifest = (
        CandidateCacheManifest(
            schema_version="sel-04-candidate-cache-v1",
            selection_run_id=run_id,
            market=SelectionMarket.CN_A,
            profile=SelectionProfile.CN_A,
            trade_date="2026-05-26",
            candidate_count=20,
            source_lineage_refs=source_lineage_refs,
            cache_body_sha256=final_manifest_sha,
            strategy_config_ref="config://approved",
            readback_status=CandidateCacheReadbackStatus.VERIFIED,
            stage="approving_candidate_cache",
            target="candidate_cache",
        )
        if include_manifest
        else None
    )
    store.save_data_run_record(
        SelectionDataRunRecord(
            run_plan=SelectionRunPlan(
                selection_run_id=run_id,
                market=SelectionMarket.CN_A,
                profile=SelectionProfile.CN_A,
                trade_date="2026-05-26",
                lookback_trading_days=260,
                universe_scope="all_a_shares",
                data_need_audit_ref="plan://sel-run-09-gate",
                approved_strategy_config_ref="config://approved",
                trigger_source=SelectionTriggerSource.SCHEDULED,
            ),
            data_run=SelectionDataRun(
                selection_run_id=run_id,
                status=SelectionDataRunStatus.COMPLETED,
                candidate_cache_ref=CandidateCacheRef(
                    selection_run_id=run_id,
                    material_id="selection-candidate-cache-sel-run-09-gate",
                    l1_uri="ov://selection/sel-run-09-gate/l1",
                    content_sha256=cache_sha,
                    manifest_ref="ov://selection/sel-run-09-gate/manifest",
                    approved_at="2026-05-26T09:00:00+00:00",
                    expires_at=expires_at,
                    cache_summary_ref="ov://selection/sel-run-09-gate/summary",
                ),
                completed_at="2026-05-26T09:01:00+00:00",
            ),
            manifest=manifest,
            integrity=integrity or SelectionRunIntegrity(),
        )
    )
    return store


def _write_workflow_evidence(
    root: Path,
    *,
    workflow_run_id: str,
    approved_material_id: str = "selection-pm-decision-select-20260526-gate",
    approval_status: str = "approved",
    decision_workflow_run_id: str | None = None,
    decision_material_target: str = "selection_portfolio_decision",
    decision_material_type: str = "pm_decision",
) -> None:
    evidence_dir = root / workflow_run_id
    evidence_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "select_workflow_run_id": workflow_run_id,
        "selection_run_id": "sel-run-09-gate",
        "status": "completed",
        "reason": "waiting_report_confirmation",
        "decision": {
            "enter_report": ["600519.SH"],
            "watch": ["000858.SZ"],
            "reject": ["300750.SZ"],
            "approved_material_id": approved_material_id,
            "approval_status": approval_status,
            "select_workflow_run_id": decision_workflow_run_id or workflow_run_id,
            "material_target": decision_material_target,
            "material_type": decision_material_type,
        },
    }
    (evidence_dir / "selection-workflow-evidence.json").write_text(
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n",
        encoding="utf-8",
    )


def _controller(tmp_path: Path, *, store: SelectionRunStore) -> tuple[SelectionConfirmationController, _FakeWorkflowRunner]:
    runner = _FakeWorkflowRunner()
    queue = ReportTaskQueue(ReportWorkflowBridge(runner))
    workflow_root = tmp_path / "selection-workflows"
    _write_workflow_evidence(workflow_root, workflow_run_id="select-20260526T130000-req-gate")
    controller = SelectionConfirmationController(
        store=store,
        queue=queue,
        settings=ReportWorkflowSettings(),
        workflow_evidence_root=workflow_root,
        now_fn=lambda: datetime(2026, 5, 26, 13, 0, tzinfo=UTC),
        today_fn=lambda: "2026-05-26",
    )
    return controller, runner


def _controller_with_queue(
    tmp_path: Path,
    *,
    store: SelectionRunStore,
) -> tuple[SelectionConfirmationController, _FakeWorkflowRunner, ReportTaskQueue]:
    runner = _FakeWorkflowRunner()
    queue = ReportTaskQueue(ReportWorkflowBridge(runner))
    workflow_root = tmp_path / "selection-workflows"
    _write_workflow_evidence(workflow_root, workflow_run_id="select-20260526T130000-req-gate")
    controller = SelectionConfirmationController(
        store=store,
        queue=queue,
        settings=ReportWorkflowSettings(),
        workflow_evidence_root=workflow_root,
        now_fn=lambda: datetime(2026, 5, 26, 13, 0, tzinfo=UTC),
        today_fn=lambda: "2026-05-26",
    )
    return controller, runner, queue


def _controller_with_workflow_evidence(
    tmp_path: Path,
    *,
    store: SelectionRunStore,
    approved_material_id: str = "selection-pm-decision-select-20260526-gate",
    approval_status: str = "approved",
    decision_workflow_run_id: str | None = None,
    decision_material_target: str = "selection_portfolio_decision",
    decision_material_type: str = "pm_decision",
) -> tuple[SelectionConfirmationController, _FakeWorkflowRunner]:
    runner = _FakeWorkflowRunner()
    queue = ReportTaskQueue(ReportWorkflowBridge(runner))
    workflow_root = tmp_path / "selection-workflows"
    workflow_run_id = "select-20260526T130000-req-gate"
    _write_workflow_evidence(
        workflow_root,
        workflow_run_id=workflow_run_id,
        approved_material_id=approved_material_id,
        approval_status=approval_status,
        decision_workflow_run_id=decision_workflow_run_id,
        decision_material_target=decision_material_target,
        decision_material_type=decision_material_type,
    )
    controller = SelectionConfirmationController(
        store=store,
        queue=queue,
        settings=ReportWorkflowSettings(),
        workflow_evidence_root=workflow_root,
        now_fn=lambda: datetime(2026, 5, 26, 13, 0, tzinfo=UTC),
        today_fn=lambda: "2026-05-26",
    )
    return controller, runner


def _assert_queue_has_no_enqueued_or_started_report_task(queue: ReportTaskQueue) -> None:
    snapshot = queue.get_report_queue_snapshot_for_user()
    assert snapshot["runningTask"] is None
    assert snapshot["queuedCount"] == 0
    assert snapshot["queuedTasks"] == []
    assert snapshot["lastTerminalTask"] is None
    assert queue.right_rail_active_task_ids() == set()
    assert queue.list_saved_reports_for_user() == []


def test_watch_ticker_can_trigger_report(tmp_path: Path) -> None:
    controller, runner, queue = _controller_with_queue(tmp_path, store=_build_store())
    result = controller.confirm(
        SelectionConfirmRequest(
            confirmation_id="cfm-watch",
            idempotency_key="select-20260526T130000-req-gate:000858.SZ:cfm-watch",
            select_workflow_run_id="select-20260526T130000-req-gate",
            ticker="000858.SZ",
        )
    )

    assert result.code == "report_handoff_started"
    assert result.handoff_request["ticker"] == "000858.SZ"
    assert result.queue_payload["task"]["instrumentCode"] == "000858.SZ"
    assert runner.create_calls == 1


def test_reject_ticker_cannot_trigger_report(tmp_path: Path) -> None:
    controller, runner, queue = _controller_with_queue(tmp_path, store=_build_store())
    with pytest.raises(SelectionConfirmationError) as exc:
        controller.confirm(
            SelectionConfirmRequest(
                confirmation_id="cfm-reject",
                idempotency_key="select-20260526T130000-req-gate:300750.SZ:cfm-reject",
                select_workflow_run_id="select-20260526T130000-req-gate",
                ticker="300750.SZ",
            )
        )
    assert exc.value.code == "ticker_not_allowed"
    assert runner.create_calls == 0
    _assert_queue_has_no_enqueued_or_started_report_task(queue)


def test_confirmation_revalidates_candidate_cache_freshness(tmp_path: Path) -> None:
    stale_store = _build_store(expires_at="2026-05-25T09:00:00+00:00")
    controller, runner, queue = _controller_with_queue(tmp_path, store=stale_store)
    with pytest.raises(SelectionConfirmationError) as exc:
        controller.confirm(
            SelectionConfirmRequest(
                confirmation_id="cfm-stale",
                idempotency_key="select-20260526T130000-req-gate:600519.SH:cfm-stale",
                select_workflow_run_id="select-20260526T130000-req-gate",
                ticker="600519.SH",
            )
        )
    assert exc.value.code == "stale_selection_run"
    assert runner.create_calls == 0
    _assert_queue_has_no_enqueued_or_started_report_task(queue)


def test_confirmation_rejects_when_candidate_cache_hash_mismatch(tmp_path: Path) -> None:
    mismatch_store = _build_store(
        cache_sha="c" * 64,
        manifest_sha="d" * 64,
    )
    controller, runner, queue = _controller_with_queue(tmp_path, store=mismatch_store)
    with pytest.raises(SelectionConfirmationError) as exc:
        controller.confirm(
            SelectionConfirmRequest(
                confirmation_id="cfm-hash-mismatch",
                idempotency_key="select-20260526T130000-req-gate:600519.SH:cfm-hash-mismatch",
                select_workflow_run_id="select-20260526T130000-req-gate",
                ticker="600519.SH",
            )
        )
    assert exc.value.code == "candidate_cache_hash_mismatch"
    assert runner.create_calls == 0
    _assert_queue_has_no_enqueued_or_started_report_task(queue)


def test_confirmation_rejects_when_readback_not_verified(tmp_path: Path) -> None:
    store = _build_store()
    record = store.load_data_run_record("sel-run-09-gate")
    assert record is not None
    assert record.manifest is not None
    object.__setattr__(record.manifest, "readback_status", type("ReadbackStatus", (), {"value": "pending"})())
    controller, runner, queue = _controller_with_queue(tmp_path, store=store)
    with pytest.raises(SelectionConfirmationError) as exc:
        controller.confirm(
            SelectionConfirmRequest(
                confirmation_id="cfm-readback-not-verified",
                idempotency_key="select-20260526T130000-req-gate:600519.SH:cfm-readback-not-verified",
                select_workflow_run_id="select-20260526T130000-req-gate",
                ticker="600519.SH",
            )
        )
    assert exc.value.code == "candidate_cache_integrity_failed"
    assert runner.create_calls == 0
    _assert_queue_has_no_enqueued_or_started_report_task(queue)


def test_confirmation_rejects_when_lineage_complete_flag_is_false(tmp_path: Path) -> None:
    store = _build_store(integrity=SelectionRunIntegrity(lineage_complete=False))
    controller, runner, queue = _controller_with_queue(tmp_path, store=store)
    with pytest.raises(SelectionConfirmationError) as exc:
        controller.confirm(
            SelectionConfirmRequest(
                confirmation_id="cfm-lineage-flag-false",
                idempotency_key="select-20260526T130000-req-gate:600519.SH:cfm-lineage-flag-false",
                select_workflow_run_id="select-20260526T130000-req-gate",
                ticker="600519.SH",
            )
        )
    assert exc.value.code == "candidate_cache_lineage_incomplete"
    assert runner.create_calls == 0
    _assert_queue_has_no_enqueued_or_started_report_task(queue)


def test_confirmation_rejects_when_source_lineage_refs_is_empty(tmp_path: Path) -> None:
    store = _build_store()
    record = store.load_data_run_record("sel-run-09-gate")
    assert record is not None
    assert record.manifest is not None
    object.__setattr__(record.manifest, "source_lineage_refs", ())
    controller, runner, queue = _controller_with_queue(tmp_path, store=store)
    with pytest.raises(SelectionConfirmationError) as exc:
        controller.confirm(
            SelectionConfirmRequest(
                confirmation_id="cfm-lineage-refs-empty",
                idempotency_key="select-20260526T130000-req-gate:600519.SH:cfm-lineage-refs-empty",
                select_workflow_run_id="select-20260526T130000-req-gate",
                ticker="600519.SH",
            )
        )
    assert exc.value.code == "candidate_cache_lineage_incomplete"
    assert runner.create_calls == 0
    _assert_queue_has_no_enqueued_or_started_report_task(queue)


def test_confirmation_rejects_when_manifest_missing(tmp_path: Path) -> None:
    controller, runner, queue = _controller_with_queue(
        tmp_path,
        store=_build_store(include_manifest=False),
    )
    with pytest.raises(SelectionConfirmationError) as exc:
        controller.confirm(
            SelectionConfirmRequest(
                confirmation_id="cfm-manifest-missing",
                idempotency_key="select-20260526T130000-req-gate:600519.SH:cfm-manifest-missing",
                select_workflow_run_id="select-20260526T130000-req-gate",
                ticker="600519.SH",
            )
        )
    assert exc.value.code == "candidate_cache_integrity_failed"
    assert runner.create_calls == 0
    _assert_queue_has_no_enqueued_or_started_report_task(queue)


def test_report_handoff_request_uses_selection_marker_and_report_entry_point(tmp_path: Path) -> None:
    controller, runner = _controller(tmp_path, store=_build_store())
    result = controller.confirm(
        SelectionConfirmRequest(
            confirmation_id="cfm-ok",
            idempotency_key="select-20260526T130000-req-gate:600519.SH:cfm-ok",
            select_workflow_run_id="select-20260526T130000-req-gate",
            ticker="600519.SH",
        )
    )

    assert runner.create_calls == 1
    assert result.code == "report_handoff_started"
    assert result.handoff_request["selectionStageMarker"] == "selection_report_handoff"
    assert result.handoff_request["reportRequest"]["entryPoint"] == "report_command"
    assert result.handoff_request["selectionContextRef"] == "selection-pm-decision-select-20260526-gate"
    assert "decision" not in result.handoff_request


def test_confirmation_rejects_when_pm_approved_decision_material_missing(tmp_path: Path) -> None:
    controller, runner = _controller_with_workflow_evidence(
        tmp_path,
        store=_build_store(),
        approved_material_id="",
        approval_status="approved",
    )
    with pytest.raises(SelectionConfirmationError) as exc:
        controller.confirm(
            SelectionConfirmRequest(
                confirmation_id="cfm-missing-pm-decision",
                idempotency_key="select-20260526T130000-req-gate:600519.SH:cfm-missing-pm-decision",
                select_workflow_run_id="select-20260526T130000-req-gate",
                ticker="600519.SH",
            )
        )
    assert exc.value.code == "selection_decision_missing_or_unapproved"
    assert runner.create_calls == 0


def test_confirmation_rejects_when_pm_decision_not_approved_or_not_bound(tmp_path: Path) -> None:
    controller, runner = _controller_with_workflow_evidence(
        tmp_path,
        store=_build_store(),
        approved_material_id="selection-pm-decision-select-20260526-gate",
        approval_status="pending",
        decision_workflow_run_id="select-20260526T130000-req-other",
    )
    with pytest.raises(SelectionConfirmationError) as exc:
        controller.confirm(
            SelectionConfirmRequest(
                confirmation_id="cfm-unapproved-pm-decision",
                idempotency_key="select-20260526T130000-req-gate:600519.SH:cfm-unapproved-pm-decision",
                select_workflow_run_id="select-20260526T130000-req-gate",
                ticker="600519.SH",
            )
        )
    assert exc.value.code == "selection_decision_missing_or_unapproved"
    assert runner.create_calls == 0


def test_confirmation_rejects_candidate_cache_material_even_if_marked_as_pm_decision(tmp_path: Path) -> None:
    controller, runner = _controller_with_workflow_evidence(
        tmp_path,
        store=_build_store(),
        approved_material_id="selection-candidate-cache-sel-run-09-gate",
        approval_status="approved",
        decision_workflow_run_id="select-20260526T130000-req-gate",
        decision_material_target="selection_portfolio_decision",
        decision_material_type="pm_decision",
    )
    with pytest.raises(SelectionConfirmationError) as exc:
        controller.confirm(
            SelectionConfirmRequest(
                confirmation_id="cfm-candidate-cache-id",
                idempotency_key="select-20260526T130000-req-gate:600519.SH:cfm-candidate-cache-id",
                select_workflow_run_id="select-20260526T130000-req-gate",
                ticker="600519.SH",
            )
        )
    assert exc.value.code == "selection_decision_missing_or_unapproved"
    assert runner.create_calls == 0


@pytest.mark.parametrize(
    ("decision_material_target", "decision_material_type"),
    [
        ("", "pm_decision"),
        ("selection_portfolio_decision", ""),
        ("pm_decision", "selection_portfolio_decision"),
        ("selection_portfolio_decision_wrong", "pm_decision"),
        ("selection_portfolio_decision", "pm_decision_wrong"),
    ],
)
def test_confirmation_rejects_pm_decision_when_target_type_not_exact_match(
    tmp_path: Path,
    decision_material_target: str,
    decision_material_type: str,
) -> None:
    controller, runner = _controller_with_workflow_evidence(
        tmp_path,
        store=_build_store(),
        approved_material_id="selection-pm-decision-select-20260526-gate",
        approval_status="approved",
        decision_workflow_run_id="select-20260526T130000-req-gate",
        decision_material_target=decision_material_target,
        decision_material_type=decision_material_type,
    )
    with pytest.raises(SelectionConfirmationError) as exc:
        controller.confirm(
            SelectionConfirmRequest(
                confirmation_id=f"cfm-invalid-type-target-{decision_material_target}-{decision_material_type}",
                idempotency_key=(
                    "select-20260526T130000-req-gate:600519.SH:"
                    f"cfm-invalid-type-target-{decision_material_target}-{decision_material_type}"
                ),
                select_workflow_run_id="select-20260526T130000-req-gate",
                ticker="600519.SH",
            )
        )
    assert exc.value.code == "selection_decision_missing_or_unapproved"
    assert runner.create_calls == 0
