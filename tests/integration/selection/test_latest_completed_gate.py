from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import pytest
from claw_trade.data_gateway.warehouse.selection_columnar import SelectionColumnarWarehouse
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


@pytest.fixture(autouse=True)
def _isolated_selection_artifacts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAW_TRADE_SELECTION_COLUMNAR_ROOT", str(tmp_path / "columnar"))
    monkeypatch.setenv("CLAW_TRADE_SELECTION_GATE_ARTIFACT_ROOT", str(tmp_path / "candidate-artifacts"))


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
    pack_sha: str | None = None,
    manifest_sha: str | None = None,
    integrity: SelectionRunIntegrity = SelectionRunIntegrity(),
    manifest: CandidatePackManifest | None = None,
) -> SelectionDataRunRecord:
    body_path, summary_path, manifest_path, body_sha = _write_candidate_pack_files(run_id, trade_date)
    resolved_pack_sha = pack_sha or body_sha
    resolved_manifest_sha = manifest_sha or resolved_pack_sha
    columnar_manifest = _write_columnar_manifest(_plan(run_id, trade_date))
    pack_ref = CandidatePackRef(
        selection_run_id=run_id,
        material_id=f"mat-{run_id}",
        l1_uri=str(body_path),
        content_sha256=resolved_pack_sha,
        manifest_ref=str(manifest_path),
        approved_at="2026-05-26T08:00:00+00:00",
        expires_at=expires_at,
        pack_summary_ref=str(summary_path),
    )
    data_run = SelectionDataRun(
        selection_run_id=run_id,
        status=SelectionDataRunStatus.COMPLETED,
        normalized_refs=(f"dataset://normalized/CN_A/daily/{run_id}",),
        provider_attempt_refs=(f"attempt://{run_id}",),
        select_data_plan_ref=f"select-data-plan://selection/{run_id}/{trade_date}",
        warehouse_check_ref=columnar_manifest.warehouse_check_ref,
        columnar_manifest_ref=columnar_manifest.manifest_ref,
        columnar_manifest_sha256=SelectionColumnarWarehouse.default().manifest_sha256(columnar_manifest.manifest_ref),
        candidate_pack_ref=pack_ref,
        completed_at=completed_at,
    )
    final_manifest = manifest if manifest is not None else _manifest(run_id, trade_date, sha=resolved_manifest_sha)
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


def _write_candidate_pack_files(run_id: str, trade_date: str) -> tuple[Path, Path, Path, str]:
    root = Path(os.environ["CLAW_TRADE_SELECTION_GATE_ARTIFACT_ROOT"]) / run_id
    root.mkdir(parents=True, exist_ok=True)
    body_path = root / "candidate-pack.md"
    summary_path = root / "candidate-pack-summary.md"
    manifest_path = root / "candidate-pack-manifest.json"
    body_text = "\n".join(
        [
            "# A股候选事实包",
            "",
            "| 排名 | 股票代码 | 股票名称 |",
            "| --- | --- | --- |",
            "| 1 | 600000.SH | 浦发银行 |",
        ]
    )
    summary_text = "\n".join(
        [
            "# A股候选事实包",
            "",
            "## 本轮范围",
            f"- 交易日：{trade_date}",
            "- 市场：CN_A",
            "- 候选数量：1",
        ]
    )
    body_path.write_text(body_text, encoding="utf-8")
    summary_path.write_text(summary_text, encoding="utf-8")
    body_sha = sha256(body_text.encode("utf-8")).hexdigest()
    manifest_payload = {
        "schema_version": "v1",
        "selection_run_id": run_id,
        "market": "CN_A",
        "profile": "CN_A",
        "trade_date": trade_date,
        "candidate_count": 1,
        "source_lineage_refs": ["lineage://provider-attempts", "lineage://feature-snapshot"],
        "pack_body_sha256": body_sha,
        "strategy_config_ref": "config://approved",
        "strategy_config_version": "cn_a.selection_strategy.v1",
        "weight_version": "cn_a.selection_weights.v1",
        "candidate_scores_ref": f"scores://{run_id}",
        "stable_top20_rule": {"score_field": "score", "tie_break_fields": ["amount"], "missing_policy": "fail"},
        "readback_status": "verified",
        "stage": "approving_candidate_pack",
        "target": "candidate_pack",
    }
    manifest_path.write_text(json.dumps(manifest_payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    _write_readback_log(body_path, expected_sha256=body_sha)
    _write_readback_log(manifest_path, expected_sha256=sha256(manifest_path.read_bytes()).hexdigest())
    return body_path, summary_path, manifest_path, body_sha


def _write_columnar_manifest(plan: SelectionRunPlan):
    writer = SelectionColumnarWarehouse.default().begin_write(plan=plan)
    writer.add_daily_rows(
        (
            {
                "market": "CN_A",
                "profile": "CN_A",
                "selection_trade_date": plan.trade_date,
                "ticker": "600000.SH",
                "date": plan.trade_date,
                "close": 10.0,
                "amount": 1000000.0,
                "source_ref": f"dataset://normalized/CN_A/daily/{plan.selection_run_id}",
            },
        )
    )
    writer.add_feature_rows(
        (
            {
                "ticker": "600000.SH",
                "company_name": "浦发银行",
                "trade_date": plan.trade_date,
                "selection_features_materialized": True,
                "close": 10.0,
                "amount": 1000000.0,
                "source_ref": f"dataset://normalized/CN_A/daily/{plan.selection_run_id}",
            },
        )
    )
    return writer.commit(
        provider_attempt_refs=(f"attempt://{plan.selection_run_id}",),
        normalized_refs=(f"dataset://normalized/CN_A/daily/{plan.selection_run_id}",),
    )


def _write_readback_log(path: Path, *, expected_sha256: str) -> None:
    suffix = path.suffix
    verify_path = path.with_suffix(f"{suffix}.readback-verify.json") if suffix else path.with_name(f"{path.name}.readback-verify.json")
    verify_path.write_text(
        json.dumps(
            {
                "status": "verified",
                "expected_sha256": expected_sha256,
                "readback_sha256": expected_sha256,
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        encoding="utf-8",
    )


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
        normalized_refs=(f"dataset://normalized/CN_A/daily/{run_id}",),
        provider_attempt_refs=(f"attempt://{run_id}",),
        select_data_plan_ref=f"select-data-plan://selection/{run_id}/2026-05-26",
        warehouse_check_ref=f"warehouse-check://selection/{run_id}/2026-05-26/ok",
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
