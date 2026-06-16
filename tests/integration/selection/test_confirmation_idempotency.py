from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from claw_trade.config.report_workflow_settings import ReportWorkflowSettings
from claw_trade.selection.confirmation import (
    SelectionConfirmationController,
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
from claw_trade.selection.store import SelectionDataRunRecord, SelectionRunStore
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


def _build_store(*, expires_at: str = "2026-05-27T09:00:00+00:00") -> SelectionRunStore:
    store = SelectionRunStore()
    run_id = "sel-run-09"
    store.save_data_run_record(
        SelectionDataRunRecord(
            run_plan=SelectionRunPlan(
                selection_run_id=run_id,
                market=SelectionMarket.CN_A,
                profile=SelectionProfile.CN_A,
                trade_date="2026-05-26",
                lookback_trading_days=260,
                universe_scope="all_a_shares",
                data_need_audit_ref="plan://sel-run-09",
                approved_strategy_config_ref="config://approved",
                trigger_source=SelectionTriggerSource.SCHEDULED,
            ),
            data_run=SelectionDataRun(
                selection_run_id=run_id,
                status=SelectionDataRunStatus.COMPLETED,
                candidate_cache_ref=CandidateCacheRef(
                    selection_run_id=run_id,
                    material_id="selection-candidate-cache-sel-run-09",
                    l1_uri="ov://selection/sel-run-09/l1",
                    content_sha256="b" * 64,
                    manifest_ref="ov://selection/sel-run-09/manifest",
                    approved_at="2026-05-26T09:00:00+00:00",
                    expires_at=expires_at,
                    cache_summary_ref="ov://selection/sel-run-09/summary",
                ),
                completed_at="2026-05-26T09:01:00+00:00",
            ),
            manifest=CandidateCacheManifest(
                schema_version="sel-04-candidate-cache-v1",
                selection_run_id=run_id,
                market=SelectionMarket.CN_A,
                profile=SelectionProfile.CN_A,
                trade_date="2026-05-26",
                candidate_count=20,
                source_lineage_refs=("lineage://a",),
                cache_body_sha256="b" * 64,
                strategy_config_ref="config://approved",
                readback_status=CandidateCacheReadbackStatus.VERIFIED,
                stage="approving_candidate_cache",
                target="candidate_cache",
            ),
        )
    )
    return store


def _write_workflow_evidence(root: Path, *, workflow_run_id: str) -> None:
    evidence_dir = root / workflow_run_id
    evidence_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "select_workflow_run_id": workflow_run_id,
        "selection_run_id": "sel-run-09",
        "status": "completed",
        "reason": "waiting_report_confirmation",
        "decision": {
            "enter_report": ["600519.SH", "000858.SZ"],
            "watch": ["300750.SZ"],
            "reject": [],
            "approved_material_id": "selection-pm-decision-select-20260526",
            "approval_status": "approved",
            "select_workflow_run_id": workflow_run_id,
            "material_target": "selection_portfolio_decision",
            "material_type": "pm_decision",
        },
    }
    (evidence_dir / "selection-workflow-evidence.json").write_text(
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n",
        encoding="utf-8",
    )


def test_selection_confirmation_same_key_returns_existing_report_task(tmp_path: Path) -> None:
    workflow_root = tmp_path / "selection-workflows"
    workflow_run_id = "select-20260526T120000-req-1"
    _write_workflow_evidence(workflow_root, workflow_run_id=workflow_run_id)
    runner = _FakeWorkflowRunner()
    queue = ReportTaskQueue(ReportWorkflowBridge(runner))
    controller = SelectionConfirmationController(
        store=_build_store(),
        queue=queue,
        settings=ReportWorkflowSettings(),
        workflow_evidence_root=workflow_root,
        now_fn=lambda: datetime(2026, 5, 26, 12, 0, tzinfo=UTC),
        today_fn=lambda: "2026-05-26",
    )

    request = SelectionConfirmRequest(
        confirmation_id="cfm-1",
        idempotency_key=f"{workflow_run_id}:600519.SH:cfm-1",
        select_workflow_run_id=workflow_run_id,
        ticker="600519.SH",
    )
    first = controller.confirm(request)
    second = controller.confirm(request)

    assert first.report_task_id == second.report_task_id
    assert first.report_run_id == second.report_run_id
    assert runner.create_calls == 1
    assert first.queue_payload["deduped"] is False


def test_selection_confirmation_different_confirmation_id_same_ticker_dedupes_by_workflow_ticker(tmp_path: Path) -> None:
    workflow_root = tmp_path / "selection-workflows"
    workflow_run_id = "select-20260526T120500-req-2"
    _write_workflow_evidence(workflow_root, workflow_run_id=workflow_run_id)
    runner = _FakeWorkflowRunner()
    queue = ReportTaskQueue(ReportWorkflowBridge(runner))
    controller = SelectionConfirmationController(
        store=_build_store(),
        queue=queue,
        settings=ReportWorkflowSettings(),
        workflow_evidence_root=workflow_root,
        now_fn=lambda: datetime(2026, 5, 26, 12, 5, tzinfo=UTC),
        today_fn=lambda: "2026-05-26",
    )

    first = controller.confirm(
        SelectionConfirmRequest(
            confirmation_id="cfm-1",
            idempotency_key=f"{workflow_run_id}:600519.SH:cfm-1",
            select_workflow_run_id=workflow_run_id,
            ticker="600519.SH",
        )
    )
    second = controller.confirm(
        SelectionConfirmRequest(
            confirmation_id="cfm-2",
            idempotency_key=f"{workflow_run_id}:600519.SH:cfm-2",
            select_workflow_run_id=workflow_run_id,
            ticker="600519.SH",
        )
    )

    assert first.report_task_id == second.report_task_id
    assert first.report_run_id == second.report_run_id
    assert first.report_handoff_dedupe_key == second.report_handoff_dedupe_key
    assert runner.create_calls == 1


def test_selection_confirmation_dedupes_across_controller_instances_with_shared_store(tmp_path: Path) -> None:
    workflow_root = tmp_path / "selection-workflows"
    workflow_run_id = "select-20260526T121000-req-cross-instance"
    _write_workflow_evidence(workflow_root, workflow_run_id=workflow_run_id)
    shared_store = _build_store()

    runner_1 = _FakeWorkflowRunner()
    queue_1 = ReportTaskQueue(ReportWorkflowBridge(runner_1))
    controller_1 = SelectionConfirmationController(
        store=shared_store,
        queue=queue_1,
        settings=ReportWorkflowSettings(),
        workflow_evidence_root=workflow_root,
        now_fn=lambda: datetime(2026, 5, 26, 12, 10, tzinfo=UTC),
        today_fn=lambda: "2026-05-26",
    )
    first = controller_1.confirm(
        SelectionConfirmRequest(
            confirmation_id="cfm-cross-1",
            idempotency_key=f"{workflow_run_id}:600519.SH:cfm-cross-1",
            select_workflow_run_id=workflow_run_id,
            ticker="600519.SH",
        )
    )

    runner_2 = _FakeWorkflowRunner()
    queue_2 = ReportTaskQueue(ReportWorkflowBridge(runner_2))
    controller_2 = SelectionConfirmationController(
        store=shared_store,
        queue=queue_2,
        settings=ReportWorkflowSettings(),
        workflow_evidence_root=workflow_root,
        now_fn=lambda: datetime(2026, 5, 26, 12, 11, tzinfo=UTC),
        today_fn=lambda: "2026-05-26",
    )
    second = controller_2.confirm(
        SelectionConfirmRequest(
            confirmation_id="cfm-cross-2",
            idempotency_key=f"{workflow_run_id}:600519.SH:cfm-cross-2",
            select_workflow_run_id=workflow_run_id,
            ticker="600519.SH",
        )
    )

    assert first.report_task_id == second.report_task_id
    assert first.report_run_id == second.report_run_id
    assert runner_1.create_calls == 1
    assert runner_2.create_calls == 0
