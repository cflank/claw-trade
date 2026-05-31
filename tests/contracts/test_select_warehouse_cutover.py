from __future__ import annotations

from datetime import UTC, datetime

import pytest

from claw_trade.data_gateway import selection_batch, selection_local_baostock
from claw_trade.selection import provider_batch as selection_provider_batch
from claw_trade.selection.controller import SelectCommandCode, SelectionController
from claw_trade.selection.models import (
    CandidatePackManifest,
    CandidatePackReadbackStatus,
    CandidatePackRef,
    SelectionDataRun,
    SelectionDataRunStatus,
    SelectionMarket,
    SelectionProfile,
    SelectionRunPlan,
    SelectionTriggerSource,
)
from claw_trade.selection.store import (
    SelectionDataRunRecord,
    SelectionRunStore,
    SelectUnavailableCode,
)


def _plan(run_id: str, *, provider_batch_plan_ref: str | None = None) -> SelectionRunPlan:
    return SelectionRunPlan(
        selection_run_id=run_id,
        market=SelectionMarket.CN_A,
        profile=SelectionProfile.CN_A,
        trade_date="2026-05-26",
        lookback_trading_days=260,
        universe_scope="all_a_shares",
        provider_batch_plan_ref=provider_batch_plan_ref or f"plan://selection/cn_a/2026-05-26/{run_id}",
        approved_strategy_config_ref="config://approved",
        trigger_source=SelectionTriggerSource.SCHEDULED,
    )


def _pack_ref(run_id: str) -> CandidatePackRef:
    return CandidatePackRef(
        selection_run_id=run_id,
        material_id=f"mat-{run_id}",
        l1_uri=f"ov://selection/{run_id}",
        content_sha256="a" * 64,
        manifest_ref=f"manifest://{run_id}",
        approved_at="2026-05-26T08:00:00+00:00",
        expires_at="2026-05-27T12:00:00+00:00",
        pack_summary_ref=f"summary://{run_id}",
    )


def _manifest(run_id: str) -> CandidatePackManifest:
    return CandidatePackManifest(
        schema_version="sel-04-candidate-pack-v1",
        selection_run_id=run_id,
        market=SelectionMarket.CN_A,
        profile=SelectionProfile.CN_A,
        trade_date="2026-05-26",
        candidate_count=20,
        source_lineage_refs=("lineage://provider-attempts", "feature://selection"),
        pack_body_sha256="a" * 64,
        strategy_config_ref="config://approved",
        readback_status=CandidatePackReadbackStatus.VERIFIED,
    )


def _completed_record(
    run_id: str,
    *,
    normalized_refs: tuple[str, ...] = ("normalized://mongo/openbb_normalized/select-cutover-row",),
    provider_attempt_refs: tuple[str, ...] = ("attempt://provider/select-cutover",),
    select_data_plan_ref: str | None = "select-data-plan://selection/cutover/2026-05-26",
    warehouse_check_ref: str | None = "warehouse-check://selection/cutover/2026-05-26/ok",
    provider_batch_plan_ref: str | None = None,
) -> SelectionDataRunRecord:
    return SelectionDataRunRecord(
        run_plan=_plan(run_id, provider_batch_plan_ref=provider_batch_plan_ref),
        data_run=SelectionDataRun(
            selection_run_id=run_id,
            status=SelectionDataRunStatus.COMPLETED,
            normalized_refs=normalized_refs,
            provider_attempt_refs=provider_attempt_refs,
            select_data_plan_ref=select_data_plan_ref,
            warehouse_check_ref=warehouse_check_ref,
            candidate_pack_ref=_pack_ref(run_id),
            completed_at="2026-05-26T08:30:00+00:00",
        ),
        manifest=_manifest(run_id),
    )


def _controller(store: SelectionRunStore) -> SelectionController:
    return SelectionController(
        store=store,
        now_fn=lambda: datetime(2026, 5, 26, 10, 0, tzinfo=UTC),
    )


def _patch_old_paths_to_raise(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    calls = {"count": 0}

    def blocked(*args: object, **kwargs: object) -> object:
        calls["count"] += 1
        raise AssertionError("old selection data path was called")

    monkeypatch.setattr(selection_provider_batch, "fetch_selection_batch_from_data_gateway", blocked)
    monkeypatch.setattr(selection_batch, "fetch_selection_batch_from_data_gateway", blocked)
    monkeypatch.setattr(selection_local_baostock, "fetch_selection_batch_from_local_baostock", blocked)
    return calls


def test_select_entry_does_not_call_old_provider_or_local_seed_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_old_paths_to_raise(monkeypatch)
    store = SelectionRunStore()
    store.save_data_run_record(_completed_record("sel-cutover-ok"))
    controller = _controller(store)

    result = controller.load_latest_completed_for_select(controller_module_request("cutover-ok"))

    assert result.is_available is True
    assert calls["count"] == 0


def test_legacy_completed_run_without_warehouse_marker_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_old_paths_to_raise(monkeypatch)
    store = SelectionRunStore()
    store.save_data_run_record(
        _completed_record(
            "sel-cutover-legacy",
            select_data_plan_ref=None,
            warehouse_check_ref=None,
        )
    )
    controller = _controller(store)

    result = controller.handle_select_command(raw_text="/select", request_id="cutover-legacy")

    assert result.code == SelectCommandCode.UNAVAILABLE
    assert result.unavailable_code == SelectUnavailableCode.SELECTION_WAREHOUSE_CHECK_MISSING
    assert calls["count"] == 0


def test_restored_provider_batch_plan_ref_cannot_be_used_as_current_select_availability() -> None:
    store = SelectionRunStore()
    store.save_data_run_record(
        _completed_record(
            "sel-cutover-restored",
            provider_batch_plan_ref="restored://sel-cutover-restored",
        )
    )
    controller = _controller(store)

    result = controller.load_latest_completed_for_select(
        controller_module_request("cutover-restored"),
    )

    assert result.is_available is False
    assert result.unavailable_code == SelectUnavailableCode.SELECTION_WAREHOUSE_CHECK_MISSING


def test_local_seed_normalized_ref_cannot_satisfy_select_warehouse_gate() -> None:
    store = SelectionRunStore()
    store.save_data_run_record(
        _completed_record(
            "sel-cutover-local",
            normalized_refs=("normalized://local-baostock/qfq/2026-05-26/abc",),
        )
    )
    controller = _controller(store)

    result = controller.load_latest_completed_for_select(
        controller_module_request("cutover-local"),
    )

    assert result.is_available is False
    assert result.unavailable_code == SelectUnavailableCode.SELECTION_WAREHOUSE_CHECK_MISSING


def controller_module_request(request_id: str):
    from claw_trade.selection.models import SelectRequest
    from claw_trade.workflow.models import WorkflowEntryPoint

    return SelectRequest(
        request_id=request_id,
        market=SelectionMarket.CN_A,
        profile=SelectionProfile.CN_A,
        trade_date=None,
        user_id=None,
        created_at="2026-05-26T10:00:00+00:00",
        entry_point=WorkflowEntryPoint.SELECT_COMMAND,
    )
