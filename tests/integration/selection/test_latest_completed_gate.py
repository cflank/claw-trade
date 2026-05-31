from __future__ import annotations

from datetime import UTC, datetime

import pytest
from claw_trade.selection.controller import SelectionController
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
    SelectRequest,
)
from claw_trade.selection.store import (
    SelectionDataRunRecord,
    SelectionRunIntegrity,
    SelectionRunStore,
)


class _Spy:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, *args: object, **kwargs: object) -> None:  # pragma: no cover - must never be called
        self.calls += 1
        raise AssertionError(f"unexpected dependency call args={args} kwargs={kwargs}")


def _request(trade_date: str | None = None) -> SelectRequest:
    return SelectRequest(
        request_id="req-1",
        market=SelectionMarket.CN_A,
        profile=SelectionProfile.CN_A,
        trade_date=trade_date,
        user_id="user-1",
        created_at="2026-05-26T12:00:00+00:00",
    )


def _plan(run_id: str, trade_date: str) -> SelectionRunPlan:
    return SelectionRunPlan(
        selection_run_id=run_id,
        market=SelectionMarket.CN_A,
        profile=SelectionProfile.CN_A,
        trade_date=trade_date,
        lookback_trading_days=260,
        universe_scope="all_a_shares",
        provider_batch_plan_ref=f"plan://{run_id}",
        approved_strategy_config_ref="config://approved",
        trigger_source=SelectionTriggerSource.SCHEDULED,
    )


def _pack_ref(run_id: str, *, expires_at: str = "2026-05-27T09:00:00+00:00", sha: str = "a" * 64) -> CandidatePackRef:
    return CandidatePackRef(
        selection_run_id=run_id,
        material_id=f"mat-{run_id}",
        l1_uri=f"ov://selection/{run_id}",
        content_sha256=sha,
        manifest_ref=f"manifest://{run_id}",
        approved_at="2026-05-26T08:00:00+00:00",
        expires_at=expires_at,
        pack_summary_ref=f"summary://{run_id}",
    )


def _manifest(run_id: str, trade_date: str, *, sha: str = "a" * 64) -> CandidatePackManifest:
    return CandidatePackManifest(
        schema_version="v1",
        selection_run_id=run_id,
        market=SelectionMarket.CN_A,
        profile=SelectionProfile.CN_A,
        trade_date=trade_date,
        candidate_count=20,
        source_lineage_refs=("lineage://provider-attempts", "lineage://feature-snapshot"),
        pack_body_sha256=sha,
        strategy_config_ref="config://approved",
        readback_status=CandidatePackReadbackStatus.VERIFIED,
    )


def _completed_record(
    run_id: str,
    trade_date: str,
    *,
    completed_at: str,
    expires_at: str = "2026-05-27T09:00:00+00:00",
    pack_sha: str = "a" * 64,
    manifest_sha: str = "a" * 64,
    integrity: SelectionRunIntegrity = SelectionRunIntegrity(),
    manifest: CandidatePackManifest | None = None,
) -> SelectionDataRunRecord:
    pack_ref = _pack_ref(run_id, expires_at=expires_at, sha=pack_sha)
    data_run = SelectionDataRun(
        selection_run_id=run_id,
        status=SelectionDataRunStatus.COMPLETED,
        candidate_pack_ref=pack_ref,
        completed_at=completed_at,
    )
    final_manifest = manifest if manifest is not None else _manifest(run_id, trade_date, sha=manifest_sha)
    return SelectionDataRunRecord(
        run_plan=_plan(run_id, trade_date),
        data_run=data_run,
        manifest=final_manifest,
        integrity=integrity,
    )


def _no_candidate_record(run_id: str, trade_date: str, *, completed_at: str) -> SelectionDataRunRecord:
    return SelectionDataRunRecord(
        run_plan=_plan(run_id, trade_date),
        data_run=SelectionDataRun(
            selection_run_id=run_id,
            status=SelectionDataRunStatus.NO_CANDIDATE,
            completed_at=completed_at,
        ),
        manifest=None,
    )


def _controller(
    store: SelectionRunStore,
    *,
    now: str = "2026-05-26T12:00:00+00:00",
) -> tuple[SelectionController, _Spy, _Spy, _Spy]:
    provider_spy = _Spy()
    scheduler_spy = _Spy()
    data_job_spy = _Spy()
    controller = SelectionController(
        store=store,
        now_fn=lambda: datetime.fromisoformat(now).astimezone(UTC),
        provider_fetch=provider_spy,
        scheduler_enqueue=scheduler_spy,
        data_job_runner=data_job_spy,
    )
    return controller, provider_spy, scheduler_spy, data_job_spy


def _assert_side_effect_dependencies_not_called(provider_spy: _Spy, scheduler_spy: _Spy, data_job_spy: _Spy) -> None:
    assert provider_spy.calls == 0
    assert scheduler_spy.calls == 0
    assert data_job_spy.calls == 0


@pytest.mark.integration
def test_select_no_completed_run_does_not_fetch_or_schedule() -> None:
    store = SelectionRunStore()
    controller, provider_spy, scheduler_spy, data_job_spy = _controller(store)

    result = controller.load_latest_completed_for_select(_request())

    assert result.is_available is False
    assert result.unavailable_code == "no_completed_selection_run"
    _assert_side_effect_dependencies_not_called(provider_spy, scheduler_spy, data_job_spy)


@pytest.mark.integration
def test_latest_completed_run_filters_non_completed_and_picks_latest() -> None:
    store = SelectionRunStore()
    store.save_data_run_record(
        SelectionDataRunRecord(
            run_plan=_plan("sel-run-running", "2026-05-27"),
            data_run=SelectionDataRun(
                selection_run_id="sel-run-running",
                status=SelectionDataRunStatus.RUNNING,
                lease_id="lease-1",
            ),
            manifest=None,
        )
    )
    store.save_data_run_record(
        _completed_record(
            "sel-run-old",
            "2026-05-25",
            completed_at="2026-05-25T08:30:00+00:00",
        )
    )
    store.save_data_run_record(
        _completed_record(
            "sel-run-latest",
            "2026-05-26",
            completed_at="2026-05-26T08:30:00+00:00",
        )
    )
    controller, provider_spy, scheduler_spy, data_job_spy = _controller(store)

    result = controller.load_latest_completed_for_select(_request())

    assert result.is_available is True
    assert result.latest_completed_run is not None
    assert result.latest_completed_run.run_plan.selection_run_id == "sel-run-latest"
    _assert_side_effect_dependencies_not_called(provider_spy, scheduler_spy, data_job_spy)


@pytest.mark.integration
def test_latest_no_candidate_run_blocks_older_completed_run() -> None:
    store = SelectionRunStore()
    store.save_data_run_record(
        _completed_record(
            "sel-run-old",
            "2026-05-25",
            completed_at="2026-05-25T08:30:00+00:00",
        )
    )
    store.save_data_run_record(
        _no_candidate_record(
            "sel-run-no-candidate",
            "2026-05-26",
            completed_at="2026-05-26T08:30:00+00:00",
        )
    )
    controller, provider_spy, scheduler_spy, data_job_spy = _controller(store)

    result = controller.load_latest_completed_for_select(_request())

    assert result.is_available is False
    assert result.unavailable_code == "no_candidate_selection_run"
    _assert_side_effect_dependencies_not_called(provider_spy, scheduler_spy, data_job_spy)


@pytest.mark.integration
def test_select_stale_run_does_not_fetch_or_schedule() -> None:
    store = SelectionRunStore()
    store.save_data_run_record(
        _completed_record(
            "sel-run-stale",
            "2026-05-25",
            completed_at="2026-05-25T08:30:00+00:00",
            expires_at="2026-05-26T11:59:59+00:00",
        )
    )
    controller, provider_spy, scheduler_spy, data_job_spy = _controller(store)

    result = controller.load_latest_completed_for_select(_request())

    assert result.is_available is False
    assert result.unavailable_code == "stale_selection_run"
    _assert_side_effect_dependencies_not_called(provider_spy, scheduler_spy, data_job_spy)


@pytest.mark.integration
def test_select_unapproved_pack_stops_before_any_side_effect() -> None:
    store = SelectionRunStore()
    store.save_data_run_record(
        _completed_record(
            "sel-run-unapproved",
            "2026-05-26",
            completed_at="2026-05-26T08:30:00+00:00",
            integrity=SelectionRunIntegrity(pack_approved=False),
        )
    )
    controller, provider_spy, scheduler_spy, data_job_spy = _controller(store)

    result = controller.load_latest_completed_for_select(_request())

    assert result.is_available is False
    assert result.unavailable_code == "candidate_pack_not_approved"
    _assert_side_effect_dependencies_not_called(provider_spy, scheduler_spy, data_job_spy)


@pytest.mark.integration
def test_select_hash_mismatch_stops_before_any_side_effect() -> None:
    store = SelectionRunStore()
    store.save_data_run_record(
        _completed_record(
            "sel-run-hash-mismatch",
            "2026-05-26",
            completed_at="2026-05-26T08:30:00+00:00",
            pack_sha="a" * 64,
            manifest_sha="b" * 64,
        )
    )
    controller, provider_spy, scheduler_spy, data_job_spy = _controller(store)

    result = controller.load_latest_completed_for_select(_request())

    assert result.is_available is False
    assert result.unavailable_code == "candidate_pack_hash_mismatch"
    _assert_side_effect_dependencies_not_called(provider_spy, scheduler_spy, data_job_spy)


@pytest.mark.integration
def test_select_lineage_incomplete_stops_before_any_side_effect() -> None:
    store = SelectionRunStore()
    store.save_data_run_record(
        _completed_record(
            "sel-run-lineage-incomplete",
            "2026-05-26",
            completed_at="2026-05-26T08:30:00+00:00",
            integrity=SelectionRunIntegrity(lineage_complete=False),
        )
    )
    controller, provider_spy, scheduler_spy, data_job_spy = _controller(store)

    result = controller.load_latest_completed_for_select(_request())

    assert result.is_available is False
    assert result.unavailable_code == "candidate_pack_lineage_incomplete"
    _assert_side_effect_dependencies_not_called(provider_spy, scheduler_spy, data_job_spy)


@pytest.mark.integration
def test_select_manifest_missing_returns_integrity_failure() -> None:
    store = SelectionRunStore()
    run_id = "sel-run-no-manifest"
    data_run = SelectionDataRun(
        selection_run_id=run_id,
        status=SelectionDataRunStatus.COMPLETED,
        candidate_pack_ref=_pack_ref(run_id),
        completed_at="2026-05-26T08:30:00+00:00",
    )
    store.save_data_run_record(
        SelectionDataRunRecord(
            run_plan=_plan(run_id, "2026-05-26"),
            data_run=data_run,
            manifest=None,
        )
    )
    controller, provider_spy, scheduler_spy, data_job_spy = _controller(store)

    result = controller.load_latest_completed_for_select(_request())

    assert result.is_available is False
    assert result.unavailable_code == "candidate_pack_integrity_failed"
    _assert_side_effect_dependencies_not_called(provider_spy, scheduler_spy, data_job_spy)


@pytest.mark.integration
def test_select_readback_not_verified_returns_integrity_failure() -> None:
    store = SelectionRunStore()
    store.save_data_run_record(
        _completed_record(
            "sel-run-readback-failed",
            "2026-05-26",
            completed_at="2026-05-26T08:30:00+00:00",
            integrity=SelectionRunIntegrity(readback_verified=False),
        )
    )
    controller, provider_spy, scheduler_spy, data_job_spy = _controller(store)

    result = controller.load_latest_completed_for_select(_request())

    assert result.is_available is False
    assert result.unavailable_code == "candidate_pack_integrity_failed"
    _assert_side_effect_dependencies_not_called(provider_spy, scheduler_spy, data_job_spy)
