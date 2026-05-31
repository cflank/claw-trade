from __future__ import annotations

import ast
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from claw_trade.data_gateway import selection_batch, selection_local_baostock
from claw_trade.selection import provider_batch as selection_provider_batch
from claw_trade.selection.controller import SelectionController
from claw_trade.selection.models import (
    CandidatePackManifest,
    CandidatePackReadbackStatus,
    CandidatePackRef,
    SelectRequest,
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
    resolve_latest_completed_selection_run,
    restore_selection_run_store,
)
from claw_trade.workflow.models import WorkflowEntryPoint

_REPO = Path(__file__).resolve().parents[2]
_OLD_SELECT_MODULES = {
    "claw_trade.selection.provider_batch",
    "claw_trade.data_gateway.selection_batch",
    "claw_trade.data_gateway.selection_local_baostock",
}
_ENTRY_FILES = (
    "src/claw_trade/workflow/runner.py",
    "src/claw_trade/selection/controller.py",
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


def _completed_record(run_id: str) -> SelectionDataRunRecord:
    return SelectionDataRunRecord(
        run_plan=_plan(run_id),
        data_run=SelectionDataRun(
            selection_run_id=run_id,
            status=SelectionDataRunStatus.COMPLETED,
            normalized_refs=("normalized://mongo/openbb_normalized/select-cutover-row",),
            provider_attempt_refs=("attempt://provider/select-cutover",),
            select_data_plan_ref="select-data-plan://selection/cutover/2026-05-26",
            warehouse_check_ref="warehouse-check://selection/cutover/2026-05-26/ok",
            candidate_pack_ref=_pack_ref(run_id),
            completed_at="2026-05-26T08:30:00+00:00",
        ),
        manifest=_manifest(run_id),
    )


def _request(request_id: str) -> SelectRequest:
    return SelectRequest(
        request_id=request_id,
        market=SelectionMarket.CN_A,
        profile=SelectionProfile.CN_A,
        trade_date=None,
        user_id=None,
        created_at="2026-05-26T10:00:00+00:00",
        entry_point=WorkflowEntryPoint.SELECT_COMMAND,
    )


def _controller(store: SelectionRunStore) -> SelectionController:
    return SelectionController(
        store=store,
        now_fn=lambda: datetime(2026, 5, 26, 10, 0, tzinfo=UTC),
    )


def _patch_old_select_paths_to_raise(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    calls = {"count": 0}

    def blocked(*args: object, **kwargs: object) -> object:
        calls["count"] += 1
        raise AssertionError("old selection data path was called")

    monkeypatch.setattr(selection_provider_batch, "fetch_selection_batch_from_data_gateway", blocked)
    monkeypatch.setattr(selection_batch, "fetch_selection_batch_from_data_gateway", blocked)
    monkeypatch.setattr(selection_local_baostock, "fetch_selection_batch_from_local_baostock", blocked)
    return calls


def test_old_select_modules_are_importable_only_as_archived_fetchers() -> None:
    assert "explicit data-job/fetcher" in (selection_provider_batch.__doc__ or "")
    assert "explicit fetcher" in (selection_batch.__doc__ or "")
    assert "seed/import/audit input only" in (selection_local_baostock.__doc__ or "")


def test_select_read_entry_does_not_call_old_batch_or_local_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_old_select_paths_to_raise(monkeypatch)
    store = SelectionRunStore()
    store.save_data_run_record(_completed_record("sel-t13-cutover-ok"))

    result = _controller(store).load_latest_completed_for_select(_request("t13-cutover-ok"))

    assert result.is_available is True
    assert calls["count"] == 0


def test_restored_legacy_data_job_without_warehouse_marker_is_read_only_reference(tmp_path: Path) -> None:
    run_id = "sel-t13-restored-legacy"
    trade_dir = tmp_path / "2026-05-26"
    trade_dir.mkdir(parents=True)
    (trade_dir / f"{run_id}.json").write_text(
        json.dumps(_legacy_data_job_payload(run_id), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    restored = restore_selection_run_store(selection_runs_root=tmp_path)
    result = resolve_latest_completed_selection_run(
        store=restored,
        market=SelectionMarket.CN_A,
        profile=SelectionProfile.CN_A,
        trade_date=None,
        now_fn=lambda: datetime(2026, 5, 26, 10, 0, tzinfo=UTC),
    )

    assert restored.load_data_run_record(run_id) is not None
    assert result.is_available is False
    assert result.unavailable_code == SelectUnavailableCode.SELECTION_WAREHOUSE_CHECK_MISSING


def test_entry_modules_do_not_import_old_select_paths() -> None:
    for relative in _ENTRY_FILES:
        path = _REPO / relative
        imports = _imported_modules(path)
        disallowed = sorted(module for module in imports if module in _OLD_SELECT_MODULES)
        assert not disallowed, f"{relative} imported old select path: {disallowed}"


def test_workflow_runner_has_no_old_direct_provider_or_selection_batch_entry() -> None:
    source = (_REPO / "src/claw_trade/workflow/runner.py").read_text(encoding="utf-8")
    forbidden_tokens = (
        "fetch_selection_batch_from_data_gateway",
        "build_selection_provider_batch_plan",
        "fetch_selection_batch_from_local_baostock",
        "claw_trade.data_gateway.providers.market",
        "claw_trade.data_gateway.providers.fundamental",
        "claw_trade.data_gateway.providers.news",
        "claw_trade.data_gateway.providers.social",
    )
    hits = sorted(token for token in forbidden_tokens if token in source)
    assert not hits, f"workflow runner contains old data path tokens: {hits}"


def _legacy_data_job_payload(run_id: str) -> dict[str, object]:
    return {
        "status": "completed",
        "selection_run_id": run_id,
        "market": "CN_A",
        "profile": "CN_A",
        "trade_date": "2026-05-26",
        "lookback_trading_days": 260,
        "universe_scope": "legacy_data_job_evidence",
        "provider_batch_plan_ref": f"restored://{run_id}",
        "trigger_source": "scheduled",
        "completed_at": "2026-05-26T08:30:00+00:00",
        "candidate_pack_ref": {
            "selection_run_id": run_id,
            "material_id": f"mat-{run_id}",
            "l1_uri": f"ov://selection/{run_id}",
            "content_sha256": "a" * 64,
            "manifest_ref": f"manifest://{run_id}",
            "approved_at": "2026-05-26T08:00:00+00:00",
            "expires_at": "2026-05-27T12:00:00+00:00",
            "pack_summary_ref": f"summary://{run_id}",
        },
        "candidate_pack_manifest": {
            "schema_version": "sel-04-candidate-pack-v1",
            "selection_run_id": run_id,
            "market": "CN_A",
            "profile": "CN_A",
            "trade_date": "2026-05-26",
            "candidate_count": 20,
            "source_lineage_refs": ["lineage://legacy-provider-attempts", "feature://legacy-selection"],
            "pack_body_sha256": "a" * 64,
            "strategy_config_ref": "config://approved",
            "readback_status": "verified",
        },
    }


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules
