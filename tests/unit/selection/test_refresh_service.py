from __future__ import annotations

import json
import os
from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from claw_trade.data_gateway.warehouse.selection_columnar import SelectionColumnarWarehouse
from claw_trade.selection.models import (
    CandidateCacheManifest,
    CandidateCacheReadbackStatus,
    CandidateCacheRef,
    SelectionBatchScope,
    SelectionDataRun,
    SelectionDataRunStatus,
    SelectionMarket,
    SelectionProfile,
    SelectionDataNeedAudit,
    SelectionRunPlan,
    SelectionTriggerSource,
    SelectRequest,
)
from claw_trade.selection.refresh import (
    SelectionDataRefreshService,
    _seconds_until_next_daily_refresh,
)
from claw_trade.selection.store import SelectionDataRunRecord, SelectionRunStore


def test_selection_refresh_service_starts_background_job_and_dedupes_active_run() -> None:
    store = SelectionRunStore()
    run_calls: list[str] = []
    service = SelectionDataRefreshService(
        store=store,
        run_data_job=lambda plan: run_calls.append(plan.selection_run_id),  # type: ignore[arg-type]
        resolve_closed_trade_date=lambda value: value or "2026-05-26",
        load_approved_strategy_config_ref=lambda _market, _profile: "config://cn-a-selection-v1",
        build_data_need_audit=_data_need_audit,
        now_fn=lambda: datetime(2026, 5, 26, 10, tzinfo=UTC),
        run_id_factory=lambda: "sel-refresh-1",
    )

    first = service.request_refresh(
        request=_request(),
        unavailable_code="no_completed_selection_run",
        select_workflow_run_id="select-wf-1",
    )
    second = service.request_refresh(
        request=_request(),
        unavailable_code="no_completed_selection_run",
        select_workflow_run_id="select-wf-2",
    )

    assert first.status == "started"
    assert first.selection_run_id == "sel-refresh-1"
    assert second.status == "already_running"
    assert second.selection_run_id == "sel-refresh-1"
    record = store.load_data_run_record("sel-refresh-1")
    assert record is not None
    assert record.run_plan.trigger_source == SelectionTriggerSource.SCHEDULED


def test_selection_refresh_service_blocks_when_license_denied() -> None:
    def deny() -> None:
        raise PermissionError("设备授权已失效，请在授权页修复后重试。")

    service = SelectionDataRefreshService(
        store=SelectionRunStore(),
        run_data_job=lambda _plan: None,  # type: ignore[arg-type]
        resolve_closed_trade_date=lambda value: value or "2026-05-26",
        load_approved_strategy_config_ref=lambda _market, _profile: "config://cn-a-selection-v1",
        build_data_need_audit=_data_need_audit,
        data_refresh_permission_checker=deny,
    )

    result = service.run_automatic_refresh_once(reason="startup")

    assert result.status == "failed"
    assert result.error_code == "license_blocked"
    assert result.reason == "设备授权已失效，请在授权页修复后重试。"


def test_selection_refresh_request_blocks_when_license_denied() -> None:
    def deny() -> None:
        raise PermissionError("设备授权已失效，请在授权页修复后重试。")

    service = SelectionDataRefreshService(
        store=SelectionRunStore(),
        run_data_job=lambda _plan: None,  # type: ignore[arg-type]
        resolve_closed_trade_date=lambda value: value or "2026-05-26",
        load_approved_strategy_config_ref=lambda _market, _profile: "config://cn-a-selection-v1",
        build_data_need_audit=_data_need_audit,
        data_refresh_permission_checker=deny,
    )

    result = service.request_refresh(
        request=_request(),
        unavailable_code="no_completed_selection_run",
        select_workflow_run_id="select-wf-1",
    )

    assert result.status == "failed"
    assert result.error_code == "license_blocked"
    assert result.selection_run_id is None


def test_selection_refresh_service_exposes_active_progress_for_right_rail() -> None:
    store = SelectionRunStore()
    service = SelectionDataRefreshService(
        store=store,
        run_data_job=lambda _plan: None,  # type: ignore[arg-type]
        resolve_closed_trade_date=lambda value: value or "2026-06-04",
        load_approved_strategy_config_ref=lambda _market, _profile: "config://cn-a-selection-v1",
        build_data_need_audit=_data_need_audit,
        now_fn=lambda: datetime(2026, 6, 4, 10, tzinfo=UTC),
    )
    store.save_data_run_record(
        SelectionDataRunRecord(
            run_plan=SelectionRunPlan(
                selection_run_id="sel-refresh-active-1",
                market=SelectionMarket.CN_A,
                profile=SelectionProfile.CN_A,
                trade_date="2026-06-04",
                lookback_trading_days=260,
                universe_scope="all_a_shares",
                data_need_audit_ref="plan://selection/cn_a/2026-06-04/batch-v1",
                approved_strategy_config_ref="config://cn-a-selection-v1",
                trigger_source=SelectionTriggerSource.SELECT_COMMAND_REFRESH,
            ),
            data_run=SelectionDataRun(
                selection_run_id="sel-refresh-active-1",
                status=SelectionDataRunStatus.FETCHING_DATA,
                lease_id="lease://sel-refresh-active-1",
                started_at="2026-06-04T10:00:00+00:00",
                progress_label="补齐全市场日线数据",
                progress_completed=128,
                progress_total=512,
            ),
            manifest=None,
        )
    )

    snapshot = service.latest_progress_for_user()

    progress = snapshot["selectionProgress"]
    assert isinstance(progress, dict)
    assert progress["kind"] == "data_refresh"
    assert progress["status"] == "running"
    assert progress["statusLabel"] == "补数据中"
    assert progress["command"] == "/select 2026-06-04"
    assert progress["stageLabel"] == "拉取/补齐行情数据"
    assert progress["currentAction"] == "补齐全市场日线数据：已完成 128/512。"
    assert progress["percent"] == 38
    assert progress["workerStatusLabels"] == ["拉取/补齐行情数据：补数据中（128/512）"]
    assert progress["workflowRunId"] == "sel-refresh-active-1"


def test_selection_refresh_service_marks_inactive_active_run_failed_and_allows_rerun() -> None:
    store = SelectionRunStore()
    run_calls: list[str] = []
    service = SelectionDataRefreshService(
        store=store,
        run_data_job=lambda plan: run_calls.append(plan.selection_run_id),  # type: ignore[arg-type]
        resolve_closed_trade_date=lambda value: value or "2026-06-04",
        load_approved_strategy_config_ref=lambda _market, _profile: "config://cn-a-selection-v1",
        build_data_need_audit=_data_need_audit,
        now_fn=lambda: datetime(2026, 6, 4, 10, 20, tzinfo=UTC),
        run_id_factory=lambda: "sel-refresh-rerun",
    )
    store.save_data_run_record(
        SelectionDataRunRecord(
            run_plan=SelectionRunPlan(
                selection_run_id="sel-refresh-stale",
                market=SelectionMarket.CN_A,
                profile=SelectionProfile.CN_A,
                trade_date="2026-06-04",
                lookback_trading_days=260,
                universe_scope="all_a_shares",
                data_need_audit_ref="plan://selection/cn_a/2026-06-04/batch-v1",
                approved_strategy_config_ref="config://cn-a-selection-v1",
                trigger_source=SelectionTriggerSource.SELECT_COMMAND_REFRESH,
            ),
            data_run=SelectionDataRun(
                selection_run_id="sel-refresh-stale",
                status=SelectionDataRunStatus.FETCHING_DATA,
                lease_id="lease://sel-refresh-stale",
                started_at="2026-06-04T09:58:00+00:00",
                updated_at="2026-06-04T10:00:00+00:00",
                progress_label="读取本地全市场历史日线",
                progress_completed=1,
                progress_total=1,
            ),
            manifest=None,
        )
    )

    progress = service.latest_progress_for_user()["selectionProgress"]

    assert isinstance(progress, dict)
    assert progress["status"] == "failed"
    assert progress["statusLabel"] == "补数据失败"
    assert progress["workflowRunId"] == "sel-refresh-stale"
    assert progress["workerStatusLabels"] == [
        "失败原因：补数据任务超过 15 分钟没有进度写入；按中断任务处理。请重新发送 /select 启动新的补数据。"
    ]
    stale_record = store.load_data_run_record("sel-refresh-stale")
    assert stale_record is not None
    assert stale_record.data_run.status == SelectionDataRunStatus.FAILED
    assert stale_record.data_run.failure_code == "selection_data_run_interrupted"
    store.save_data_run_record(
        replace(
            stale_record,
            data_run=replace(
                stale_record.data_run,
                status=SelectionDataRunStatus.FETCHING_DATA,
                updated_at="2026-06-04T10:21:00+00:00",
                failed_at=None,
                failure_code=None,
                failure_reason=None,
            ),
        )
    )
    stale_record_after_late_update = store.load_data_run_record("sel-refresh-stale")
    assert stale_record_after_late_update is not None
    assert stale_record_after_late_update.data_run.status == SelectionDataRunStatus.FAILED
    assert stale_record_after_late_update.data_run.failure_code == "selection_data_run_interrupted"
    assert (
        store.load_active_data_run_record(
            market=SelectionMarket.CN_A,
            profile=SelectionProfile.CN_A,
            trade_date="2026-06-04",
        )
        is None
    )

    result = service.request_refresh(
        request=_request(trade_date="2026-06-04"),
        unavailable_code="no_completed_selection_run",
        select_workflow_run_id="select-wf-rerun",
    )

    assert result.status == "started"
    assert result.selection_run_id == "sel-refresh-rerun"


def test_selection_refresh_service_cancel_hides_active_refresh_and_blocks_stale_updates() -> None:
    store = SelectionRunStore()
    service = SelectionDataRefreshService(
        store=store,
        run_data_job=lambda _plan: None,  # type: ignore[arg-type]
        resolve_closed_trade_date=lambda value: value or "2026-06-04",
        load_approved_strategy_config_ref=lambda _market, _profile: "config://cn-a-selection-v1",
        build_data_need_audit=_data_need_audit,
        now_fn=lambda: datetime(2026, 6, 4, 10, tzinfo=UTC),
    )
    active_record = SelectionDataRunRecord(
        run_plan=SelectionRunPlan(
            selection_run_id="sel-refresh-active-1",
            market=SelectionMarket.CN_A,
            profile=SelectionProfile.CN_A,
            trade_date="2026-06-04",
            lookback_trading_days=260,
            universe_scope="all_a_shares",
            data_need_audit_ref="plan://selection/cn_a/2026-06-04/batch-v1",
            approved_strategy_config_ref="config://cn-a-selection-v1",
            trigger_source=SelectionTriggerSource.SELECT_COMMAND_REFRESH,
        ),
        data_run=SelectionDataRun(
            selection_run_id="sel-refresh-active-1",
            status=SelectionDataRunStatus.FETCHING_DATA,
            lease_id="lease://sel-refresh-active-1",
            started_at="2026-06-04T10:00:00+00:00",
            progress_label="补齐全市场日线数据",
        ),
        manifest=None,
    )
    store.save_data_run_record(active_record)

    assert service.cancel_refresh(selection_run_id="sel-refresh-active-1") is True

    cancelled_record = store.load_data_run_record("sel-refresh-active-1")
    assert cancelled_record is not None
    assert cancelled_record.data_run.status == SelectionDataRunStatus.FAILED
    assert cancelled_record.data_run.failure_code == "selection_refresh_cancelled"
    assert service.latest_progress_for_user(include_terminal=False) == {"selectionProgress": None}

    store.save_data_run_record(
        replace(
            cancelled_record,
            data_run=replace(
                cancelled_record.data_run,
                status=SelectionDataRunStatus.FETCHING_DATA,
                failed_at=None,
                failure_code=None,
                failure_reason=None,
            ),
        )
    )

    persisted_record = store.load_data_run_record("sel-refresh-active-1")
    assert persisted_record is not None
    assert persisted_record.data_run.status == SelectionDataRunStatus.FAILED
    assert persisted_record.data_run.failure_code == "selection_refresh_cancelled"


def test_selection_refresh_service_right_rail_prefers_latest_active_task_across_dates() -> None:
    store = SelectionRunStore()
    service = SelectionDataRefreshService(
        store=store,
        run_data_job=lambda _plan: None,  # type: ignore[arg-type]
        resolve_closed_trade_date=lambda value: value or "2026-06-05",
        load_approved_strategy_config_ref=lambda _market, _profile: "config://cn-a-selection-v1",
        build_data_need_audit=_data_need_audit,
        now_fn=lambda: datetime(2026, 6, 5, 10, tzinfo=UTC),
    )
    store.save_data_run_record(
        SelectionDataRunRecord(
            run_plan=SelectionRunPlan(
                selection_run_id="sel-refresh-failed-current",
                market=SelectionMarket.CN_A,
                profile=SelectionProfile.CN_A,
                trade_date="2026-06-05",
                lookback_trading_days=260,
                universe_scope="all_a_shares",
                data_need_audit_ref="plan://selection/cn_a/2026-06-05/batch-v1",
                approved_strategy_config_ref="config://cn-a-selection-v1",
                trigger_source=SelectionTriggerSource.SELECT_COMMAND_REFRESH,
            ),
            data_run=SelectionDataRun(
                selection_run_id="sel-refresh-failed-current",
                status=SelectionDataRunStatus.FAILED,
                lease_id="lease://sel-refresh-failed-current",
                started_at="2026-06-05T08:00:00+00:00",
                failed_at="2026-06-05T08:01:00+00:00",
                failure_code="selection_data_run_interrupted",
                failure_reason="上一次进程已中断。",
            ),
            manifest=None,
        )
    )
    store.save_data_run_record(
        SelectionDataRunRecord(
            run_plan=SelectionRunPlan(
                selection_run_id="sel-refresh-active-previous",
                market=SelectionMarket.CN_A,
                profile=SelectionProfile.CN_A,
                trade_date="2026-06-04",
                lookback_trading_days=260,
                universe_scope="all_a_shares",
                data_need_audit_ref="plan://selection/cn_a/2026-06-04/batch-v1",
                approved_strategy_config_ref="config://cn-a-selection-v1",
                trigger_source=SelectionTriggerSource.SELECT_COMMAND_REFRESH,
            ),
            data_run=SelectionDataRun(
                selection_run_id="sel-refresh-active-previous",
                status=SelectionDataRunStatus.FETCHING_DATA,
                lease_id="lease://sel-refresh-active-previous",
                started_at="2026-06-05T08:02:00+00:00",
                progress_label="检查本地全市场日线缓存",
                progress_completed=0,
                progress_total=1,
            ),
            manifest=None,
        )
    )

    progress = service.latest_progress_for_user()["selectionProgress"]

    assert isinstance(progress, dict)
    assert progress["command"] == "/select 2026-06-04"
    assert progress["status"] == "running"
    assert progress["workflowRunId"] == "sel-refresh-active-previous"


def test_selection_refresh_service_exposes_latest_terminal_failure_for_right_rail() -> None:
    store = SelectionRunStore()
    service = SelectionDataRefreshService(
        store=store,
        run_data_job=lambda _plan: None,  # type: ignore[arg-type]
        resolve_closed_trade_date=lambda value: value or "2026-06-04",
        load_approved_strategy_config_ref=lambda _market, _profile: "config://cn-a-selection-v1",
        build_data_need_audit=_data_need_audit,
        now_fn=lambda: datetime(2026, 6, 4, 10, tzinfo=UTC),
    )
    store.save_data_run_record(
        SelectionDataRunRecord(
            run_plan=SelectionRunPlan(
                selection_run_id="sel-refresh-failed-1",
                market=SelectionMarket.CN_A,
                profile=SelectionProfile.CN_A,
                trade_date="2026-06-04",
                lookback_trading_days=260,
                universe_scope="all_a_shares",
                data_need_audit_ref="plan://selection/cn_a/2026-06-04/batch-v1",
                approved_strategy_config_ref="config://cn-a-selection-v1",
                trigger_source=SelectionTriggerSource.SELECT_COMMAND_REFRESH,
            ),
            data_run=SelectionDataRun(
                selection_run_id="sel-refresh-failed-1",
                status=SelectionDataRunStatus.FAILED,
                lease_id="lease://sel-refresh-failed-1",
                started_at="2026-06-04T09:00:00+00:00",
                failed_at="2026-06-04T09:05:00+00:00",
                failure_code="selection_data_run_interrupted",
                failure_reason="上一次进程已中断。",
            ),
            manifest=None,
        )
    )

    snapshot = service.latest_progress_for_user()

    progress = snapshot["selectionProgress"]
    assert isinstance(progress, dict)
    assert progress["status"] == "failed"
    assert progress["statusLabel"] == "补数据失败"
    assert progress["stageLabel"] == "数据刷新失败"
    assert progress["currentAction"] == "选股数据刷新失败，请查看失败原因。"
    assert progress["workerStatusLabels"] == ["失败原因：上一次进程已中断。"]
    assert progress["workflowRunId"] == "sel-refresh-failed-1"
    assert service.latest_progress_for_user(include_terminal=False) == {"selectionProgress": None}


def test_selection_refresh_service_hides_failed_refresh_when_valid_candidate_cache_exists(tmp_path: Path) -> None:
    store = SelectionRunStore(persisted_runs_dir=tmp_path / "store" / "data-runs")
    _save_completed_candidate_cache_record(
        store=store,
        artifact_root=tmp_path / "artifacts",
        selection_run_id="sel-existing-cache-for-right-rail",
        trade_date="2026-06-04",
    )
    service = SelectionDataRefreshService(
        store=store,
        run_data_job=lambda _plan: None,  # type: ignore[arg-type]
        resolve_closed_trade_date=lambda value: value or "2026-06-04",
        load_approved_strategy_config_ref=lambda _market, _profile: "config://cn-a-selection-v1",
        build_data_need_audit=_data_need_audit,
        now_fn=lambda: datetime(2026, 6, 4, 10, tzinfo=UTC),
    )
    store.save_data_run_record(
        SelectionDataRunRecord(
            run_plan=SelectionRunPlan(
                selection_run_id="sel-refresh-failed-after-valid-cache",
                market=SelectionMarket.CN_A,
                profile=SelectionProfile.CN_A,
                trade_date="2026-06-04",
                lookback_trading_days=260,
                universe_scope="all_a_shares",
                data_need_audit_ref="plan://selection/cn_a/2026-06-04/batch-v1",
                approved_strategy_config_ref="config://cn-a-selection-v1",
                trigger_source=SelectionTriggerSource.SELECT_COMMAND_REFRESH,
            ),
            data_run=SelectionDataRun(
                selection_run_id="sel-refresh-failed-after-valid-cache",
                status=SelectionDataRunStatus.FAILED,
                lease_id="lease://sel-refresh-failed-after-valid-cache",
                started_at="2026-06-04T09:00:00+00:00",
                failed_at="2026-06-04T09:05:00+00:00",
                failure_code="selection_data_run_interrupted",
                failure_reason="上一次进程已中断。",
            ),
            manifest=None,
        )
    )

    snapshot = service.latest_progress_for_user()

    assert snapshot == {"selectionProgress": None}


def test_selection_refresh_service_hides_non_trading_date_failure_when_canonical_candidate_cache_exists(tmp_path: Path) -> None:
    store = SelectionRunStore(persisted_runs_dir=tmp_path / "store" / "data-runs")
    _save_completed_candidate_cache_record(
        store=store,
        artifact_root=tmp_path / "artifacts",
        selection_run_id="sel-valid-20260605",
        trade_date="2026-06-05",
    )
    store.save_data_run_record(
        SelectionDataRunRecord(
            run_plan=SelectionRunPlan(
                selection_run_id="sel-weekend-failed-20260606",
                market=SelectionMarket.CN_A,
                profile=SelectionProfile.CN_A,
                trade_date="2026-06-06",
                lookback_trading_days=260,
                universe_scope="all_a_shares",
                data_need_audit_ref="plan://selection/cn_a/2026-06-06/batch-v1",
                approved_strategy_config_ref="config://cn-a-selection-v1",
                trigger_source=SelectionTriggerSource.SELECT_COMMAND_REFRESH,
            ),
            data_run=SelectionDataRun(
                selection_run_id="sel-weekend-failed-20260606",
                status=SelectionDataRunStatus.FAILED,
                lease_id="lease://sel-weekend-failed-20260606",
                started_at="2026-06-06T16:36:36+00:00",
                failed_at="2026-06-06T16:36:40+00:00",
                failure_code="provider_evidence_failed",
                failure_reason="provider attempts 缺失",
            ),
            manifest=None,
        )
    )
    service = SelectionDataRefreshService(
        store=store,
        run_data_job=lambda _plan: None,  # type: ignore[arg-type]
        resolve_closed_trade_date=lambda value: "2026-06-05" if value in {None, "2026-06-06"} else str(value),
        load_approved_strategy_config_ref=lambda _market, _profile: "config://cn-a-selection-v1",
        build_data_need_audit=_data_need_audit,
        now_fn=lambda: datetime(2026, 6, 6, 7, tzinfo=UTC),
    )

    snapshot = service.latest_progress_for_user()

    assert snapshot == {"selectionProgress": None}


def test_selection_refresh_service_humanizes_provider_evidence_failure_for_right_rail() -> None:
    store = SelectionRunStore()
    service = SelectionDataRefreshService(
        store=store,
        run_data_job=lambda _plan: None,  # type: ignore[arg-type]
        resolve_closed_trade_date=lambda value: value or "2026-06-04",
        load_approved_strategy_config_ref=lambda _market, _profile: "config://cn-a-selection-v1",
        build_data_need_audit=_data_need_audit,
        now_fn=lambda: datetime(2026, 6, 4, 10, tzinfo=UTC),
    )
    store.save_data_run_record(
        SelectionDataRunRecord(
            run_plan=SelectionRunPlan(
                selection_run_id="sel-refresh-provider-failed-1",
                market=SelectionMarket.CN_A,
                profile=SelectionProfile.CN_A,
                trade_date="2026-06-04",
                lookback_trading_days=260,
                universe_scope="all_a_shares",
                data_need_audit_ref="plan://selection/cn_a/2026-06-04/batch-v1",
                approved_strategy_config_ref="config://cn-a-selection-v1",
                trigger_source=SelectionTriggerSource.SELECT_COMMAND_REFRESH,
            ),
            data_run=SelectionDataRun(
                selection_run_id="sel-refresh-provider-failed-1",
                status=SelectionDataRunStatus.FAILED,
                lease_id="lease://sel-refresh-provider-failed-1",
                started_at="2026-06-04T09:00:00+00:00",
                failed_at="2026-06-04T09:05:00+00:00",
                failure_code="provider_evidence_failed",
                failure_reason="provider attempts 缺失",
            ),
            manifest=None,
        )
    )

    progress = service.latest_progress_for_user()["selectionProgress"]

    assert isinstance(progress, dict)
    assert progress["workerStatusLabels"] == [
        "失败原因：缺少数据源调用证据（provider attempts 缺失）",
    ]


def test_selection_refresh_service_startup_check_runs_data_job_and_dedupes_select_refresh() -> None:
    store = SelectionRunStore()
    job_calls: list[str] = []

    def run_job(plan: SelectionRunPlan) -> None:
        active = store.load_active_data_run_record(
            market=plan.market,
            profile=plan.profile,
            trade_date=plan.trade_date,
        )
        assert active is not None
        assert active.run_plan.selection_run_id == plan.selection_run_id
        refresh = service.request_refresh(
            request=_request(trade_date=plan.trade_date),
            unavailable_code="no_completed_selection_run",
            select_workflow_run_id="select-during-auto",
        )
        assert refresh.status == "already_running"
        assert refresh.selection_run_id == plan.selection_run_id
        job_calls.append(plan.selection_run_id)
        store.save_data_run_record(
            SelectionDataRunRecord(
                run_plan=plan,
                data_run=SelectionDataRun(
                    selection_run_id=plan.selection_run_id,
                    status=SelectionDataRunStatus.NO_CANDIDATE,
                    lease_id=f"lease://{plan.selection_run_id}",
                    started_at="2026-06-04T08:00:00+00:00",
                    completed_at="2026-06-04T08:01:00+00:00",
                ),
                manifest=None,
            )
        )

    service = SelectionDataRefreshService(
        store=store,
        run_data_job=run_job,  # type: ignore[arg-type]
        resolve_closed_trade_date=lambda value: value or "2026-06-04",
        load_approved_strategy_config_ref=lambda _market, _profile: "config://cn-a-selection-v1",
        build_data_need_audit=_data_need_audit,
        now_fn=lambda: datetime(2026, 6, 4, 8, tzinfo=UTC),
        run_id_factory=lambda: "sel-auto-check-1",
    )

    result = service.run_automatic_refresh_once(reason="startup_data_check")

    assert result.status == "completed"
    assert result.selection_run_id == "sel-auto-check-1"
    assert result.trade_date == "2026-06-04"
    assert job_calls == ["sel-auto-check-1"]


def test_selection_refresh_service_startup_batch_runs_cn_a_and_crypto(monkeypatch) -> None:
    monkeypatch.setattr(
        "claw_trade.selection.refresh.resolve_crypto_selection_trade_date_for_scheduler",
        lambda value: value or "2026-06-04",
    )
    store = SelectionRunStore()
    run_ids = iter(("sel-auto-cn-a-1", "sel-auto-crypto-1"))
    markets: list[SelectionMarket] = []

    def run_check(plan: SelectionRunPlan) -> None:
        markets.append(plan.market)
        store.save_data_run_record(
            SelectionDataRunRecord(
                run_plan=plan,
                data_run=SelectionDataRun(
                    selection_run_id=plan.selection_run_id,
                    status=SelectionDataRunStatus.NO_CANDIDATE,
                    lease_id=f"lease://{plan.selection_run_id}",
                    started_at="2026-06-04T08:00:00+00:00",
                    completed_at="2026-06-04T08:01:00+00:00",
                ),
                manifest=None,
            )
        )

    service = SelectionDataRefreshService(
        store=store,
        run_data_job=run_check,  # type: ignore[arg-type]
        run_data_check=run_check,
        resolve_closed_trade_date=lambda value: value or "2026-06-04",
        load_approved_strategy_config_ref=_strategy_config_ref,
        build_data_need_audit=_data_need_audit,
        now_fn=lambda: datetime(2026, 6, 4, 8, tzinfo=UTC),
        run_id_factory=lambda: next(run_ids),
    )

    service._run_automatic_refresh_batch(reason="startup_data_check")

    assert markets == [SelectionMarket.CN_A, SelectionMarket.CRYPTO]
    crypto_record = store.load_latest_data_run_record(
        market=SelectionMarket.CRYPTO,
        profile=SelectionProfile.CRYPTO,
        trade_date="2026-06-04",
    )
    assert crypto_record is not None
    assert crypto_record.run_plan.approved_strategy_config_ref == "config://crypto-selection-v1"
    assert crypto_record.run_plan.universe_scope == "spot_usdt"


def test_selection_refresh_service_auto_check_dedupes_by_market_not_only_date(monkeypatch) -> None:
    monkeypatch.setattr(
        "claw_trade.selection.refresh.resolve_crypto_selection_trade_date_for_scheduler",
        lambda value: value or "2026-06-04",
    )
    store = SelectionRunStore()
    run_ids = iter(("sel-auto-cn-a-1", "sel-auto-crypto-1"))
    nested_results: list[tuple[str, str | None]] = []
    markets: list[SelectionMarket] = []

    def run_check(plan: SelectionRunPlan) -> None:
        markets.append(plan.market)
        if plan.market == SelectionMarket.CN_A:
            crypto_result = service.run_automatic_refresh_once(reason="startup_data_check", market=SelectionMarket.CRYPTO)
            nested_results.append((crypto_result.status, crypto_result.selection_run_id))
        store.save_data_run_record(
            SelectionDataRunRecord(
                run_plan=plan,
                data_run=SelectionDataRun(
                    selection_run_id=plan.selection_run_id,
                    status=SelectionDataRunStatus.NO_CANDIDATE,
                    lease_id=f"lease://{plan.selection_run_id}",
                    started_at="2026-06-04T08:00:00+00:00",
                    completed_at="2026-06-04T08:01:00+00:00",
                ),
                manifest=None,
            )
        )

    service = SelectionDataRefreshService(
        store=store,
        run_data_job=run_check,  # type: ignore[arg-type]
        run_data_check=run_check,
        resolve_closed_trade_date=lambda value: value or "2026-06-04",
        load_approved_strategy_config_ref=_strategy_config_ref,
        build_data_need_audit=_data_need_audit,
        now_fn=lambda: datetime(2026, 6, 4, 8, tzinfo=UTC),
        run_id_factory=lambda: next(run_ids),
    )

    result = service.run_automatic_refresh_once(reason="startup_data_check")

    assert result.status == "completed"
    assert result.selection_run_id == "sel-auto-cn-a-1"
    assert nested_results == [("completed", "sel-auto-crypto-1")]
    assert markets == [SelectionMarket.CN_A, SelectionMarket.CRYPTO]


def test_selection_refresh_service_startup_check_reuses_valid_candidate_cache(tmp_path: Path) -> None:
    store = SelectionRunStore(persisted_runs_dir=tmp_path / "store" / "data-runs")
    _save_completed_candidate_cache_record(
        store=store,
        artifact_root=tmp_path / "artifacts",
        selection_run_id="sel-existing-cache-1",
        trade_date="2026-06-04",
    )
    check_calls: list[SelectionRunPlan] = []
    service = SelectionDataRefreshService(
        store=store,
        run_data_job=lambda _plan: None,  # type: ignore[arg-type]
        run_data_check=lambda plan: check_calls.append(plan),
        resolve_closed_trade_date=lambda value: value or "2026-06-04",
        load_approved_strategy_config_ref=lambda _market, _profile: "config://cn-a-selection-v1",
        build_data_need_audit=_data_need_audit,
        now_fn=lambda: datetime(2026, 6, 4, 10, tzinfo=UTC),
        run_id_factory=lambda: "sel-auto-check-should-not-run",
    )

    result = service.run_automatic_refresh_once(reason="startup_data_check")

    assert result.status == "completed"
    assert result.selection_run_id == "sel-existing-cache-1"
    assert result.reason == "startup_data_check:candidate_cache_valid"
    assert check_calls == []


def test_selection_refresh_service_request_refresh_reuses_valid_candidate_cache(tmp_path: Path) -> None:
    store = SelectionRunStore(persisted_runs_dir=tmp_path / "store" / "data-runs")
    _save_completed_candidate_cache_record(
        store=store,
        artifact_root=tmp_path / "artifacts",
        selection_run_id="sel-existing-cache-2",
        trade_date="2026-06-04",
    )
    run_calls: list[SelectionRunPlan] = []
    service = SelectionDataRefreshService(
        store=store,
        run_data_job=lambda plan: run_calls.append(plan),  # type: ignore[arg-type]
        resolve_closed_trade_date=lambda value: value or "2026-06-04",
        load_approved_strategy_config_ref=lambda _market, _profile: "config://cn-a-selection-v1",
        build_data_need_audit=_data_need_audit,
        now_fn=lambda: datetime(2026, 6, 4, 10, tzinfo=UTC),
        run_id_factory=lambda: "sel-refresh-should-not-run",
    )

    result = service.request_refresh(
        request=_request(trade_date="2026-06-04"),
        unavailable_code="no_completed_selection_run",
        select_workflow_run_id="select-wf-valid-cache",
    )

    assert result.status == "completed"
    assert result.selection_run_id == "sel-existing-cache-2"
    assert result.reason == "no_completed_selection_run:candidate_cache_valid"
    assert run_calls == []


def test_selection_refresh_service_auto_check_does_not_run_when_data_job_is_active() -> None:
    store = SelectionRunStore()
    store.save_data_run_record(
        SelectionDataRunRecord(
            run_plan=SelectionRunPlan(
                selection_run_id="sel-active",
                market=SelectionMarket.CN_A,
                profile=SelectionProfile.CN_A,
                trade_date="2026-06-04",
                lookback_trading_days=260,
                universe_scope="all_a_shares",
                data_need_audit_ref="plan://selection/cn_a/2026-06-04/batch-v1",
                approved_strategy_config_ref="config://cn-a-selection-v1",
                trigger_source=SelectionTriggerSource.SELECT_COMMAND_REFRESH,
            ),
            data_run=SelectionDataRun(
                selection_run_id="sel-active",
                status=SelectionDataRunStatus.FETCHING_DATA,
                lease_id="lease://sel-active",
                started_at="2026-06-04T08:00:00+00:00",
            ),
            manifest=None,
        )
    )
    check_calls: list[SelectionRunPlan] = []
    service = SelectionDataRefreshService(
        store=store,
        run_data_job=lambda _plan: None,  # type: ignore[arg-type]
        run_data_check=lambda plan: check_calls.append(plan),
        resolve_closed_trade_date=lambda value: value or "2026-06-04",
        load_approved_strategy_config_ref=lambda _market, _profile: "config://cn-a-selection-v1",
        build_data_need_audit=_data_need_audit,
    )

    result = service.run_automatic_refresh_once(reason="startup_data_check")

    assert result.status == "already_running"
    assert result.trade_date == "2026-06-04"
    assert check_calls == []


def test_selection_auto_refresh_daily_delay_uses_1600_shanghai_cutoff() -> None:
    before_cutoff_utc = datetime(2026, 6, 4, 7, 0, tzinfo=UTC)
    after_cutoff_utc = datetime(2026, 6, 4, 8, 1, tzinfo=UTC)

    assert _seconds_until_next_daily_refresh(before_cutoff_utc) == 3600.0
    assert _seconds_until_next_daily_refresh(after_cutoff_utc) == 23 * 3600 + 59 * 60


def _request(*, trade_date: str = "2026-05-26") -> SelectRequest:
    return SelectRequest(
        request_id="req-refresh",
        market=SelectionMarket.CN_A,
        profile=SelectionProfile.CN_A,
        trade_date=trade_date,
        user_id="u",
        created_at=f"{trade_date}T10:00:00+00:00",
    )


def _data_need_audit(*, market: SelectionMarket, profile: SelectionProfile, trade_date: str) -> SelectionDataNeedAudit:
    if market == SelectionMarket.CRYPTO:
        segment = "crypto"
        universe_scope = "spot_usdt"
    else:
        segment = "cn_a"
        universe_scope = "all_a_shares"
    return SelectionDataNeedAudit(
        plan_id=f"plan://selection/{segment}/{trade_date}/batch-v1",
        scope=SelectionBatchScope.SELECTION_BATCH,
        market=market,
        profile=profile,
        trade_date=trade_date,
        lookback_trading_days=260,
        universe_scope=universe_scope,
        coverage_groups=("daily",),
        ttl_policy_ref=f"ttl://selection/{segment}",
        lineage_root_ref=f"lineage://selection/{segment}/{trade_date}",
    )


def _strategy_config_ref(market: SelectionMarket, profile: SelectionProfile) -> str:
    if (market, profile) == (SelectionMarket.CRYPTO, SelectionProfile.CRYPTO):
        return "config://crypto-selection-v1"
    return "config://cn-a-selection-v1"


def _save_completed_candidate_cache_record(
    *,
    store: SelectionRunStore,
    artifact_root: Path,
    selection_run_id: str,
    trade_date: str,
) -> None:
    body_uri = f"local://selection/{selection_run_id}/candidate-cache/approved/body.md"
    manifest_uri = f"local://selection/{selection_run_id}/candidate-cache/approved/manifest.json"
    summary_uri = f"local://selection/{selection_run_id}/candidate-cache/approved/summary.md"
    body_path = _selection_artifact_path(artifact_root, body_uri)
    manifest_path = _selection_artifact_path(artifact_root, manifest_uri)
    summary_path = _selection_artifact_path(artifact_root, summary_uri)
    body_path.parent.mkdir(parents=True, exist_ok=True)
    body_text = "# 候选池\n\n| rank | ticker | company |\n| --- | --- | --- |\n| 1 | 600000.SH | 浦发银行 |\n"
    body_sha = sha256(body_text.encode("utf-8")).hexdigest()
    body_path.write_text(body_text, encoding="utf-8")
    summary_path.write_text(body_text, encoding="utf-8")
    manifest_payload = {
        "schema_version": "selection-candidate-cache-manifest-v1",
        "selection_run_id": selection_run_id,
        "market": SelectionMarket.CN_A.value,
        "profile": SelectionProfile.CN_A.value,
        "trade_date": trade_date,
        "candidate_count": 1,
        "source_lineage_refs": ["dataset://normalized/CN_A/daily/600000"],
        "cache_body_sha256": body_sha,
        "strategy_config_ref": "config://cn-a-selection-v1",
        "strategy_config_version": "cn_a.selection_strategy.v1",
        "weight_version": "cn_a.selection_weights.v1",
        "candidate_scores_ref": "score://selection/top20",
        "stable_top20_rule": {},
        "readback_status": CandidateCacheReadbackStatus.VERIFIED.value,
        "stage": "approving_candidate_cache",
        "target": "candidate_cache",
    }
    manifest_text = f"{json.dumps(manifest_payload, ensure_ascii=False, indent=2)}\n"
    manifest_sha = sha256(manifest_text.encode("utf-8")).hexdigest()
    manifest_path.write_text(manifest_text, encoding="utf-8")
    _write_readback_log(body_path, expected_sha256=body_sha)
    _write_readback_log(manifest_path, expected_sha256=manifest_sha)

    plan = SelectionRunPlan(
        selection_run_id=selection_run_id,
        market=SelectionMarket.CN_A,
        profile=SelectionProfile.CN_A,
        trade_date=trade_date,
        lookback_trading_days=260,
        universe_scope="all_a_shares",
        data_need_audit_ref=f"plan://selection/cn_a/{trade_date}/batch-v1",
        approved_strategy_config_ref="config://cn-a-selection-v1",
        trigger_source=SelectionTriggerSource.SCHEDULED,
    )
    candidate_cache_ref = CandidateCacheRef(
        selection_run_id=selection_run_id,
        material_id=f"selection-candidate-cache-{selection_run_id}",
        l1_uri=body_uri,
        content_sha256=body_sha,
        manifest_ref=manifest_uri,
        approved_at="2026-06-04T08:00:00Z",
        expires_at="2026-06-06T08:00:00Z",
        cache_summary_ref=summary_uri,
    )
    columnar_manifest_ref, columnar_manifest_sha256 = _write_columnar_manifest(
        root=artifact_root.parent / "columnar",
        plan=plan,
        normalized_refs=("dataset://normalized/CN_A/daily/600000",),
        provider_attempt_refs=("attempt://akshare-1",),
    )
    manifest = CandidateCacheManifest(
        schema_version="selection-candidate-cache-manifest-v1",
        selection_run_id=selection_run_id,
        market=SelectionMarket.CN_A,
        profile=SelectionProfile.CN_A,
        trade_date=trade_date,
        candidate_count=1,
        source_lineage_refs=("dataset://normalized/CN_A/daily/600000",),
        cache_body_sha256=body_sha,
        strategy_config_ref="config://cn-a-selection-v1",
        strategy_config_version="cn_a.selection_strategy.v1",
        weight_version="cn_a.selection_weights.v1",
        candidate_scores_ref="score://selection/top20",
        stable_top20_rule={},
        readback_status=CandidateCacheReadbackStatus.VERIFIED,
        stage="approving_candidate_cache",
        target="candidate_cache",
    )
    store.save_data_run_record(
        SelectionDataRunRecord(
            run_plan=plan,
            data_run=SelectionDataRun(
                selection_run_id=selection_run_id,
                status=SelectionDataRunStatus.COMPLETED,
                lease_id=f"lease://{selection_run_id}",
                normalized_refs=("dataset://normalized/CN_A/daily/600000",),
                provider_attempt_refs=("attempt://akshare-1",),
                select_data_plan_ref=f"selection-data-plan://{selection_run_id}",
                warehouse_check_ref=f"warehouse-check://selection/{selection_run_id}/{trade_date}/success",
                columnar_manifest_ref=columnar_manifest_ref,
                columnar_manifest_sha256=columnar_manifest_sha256,
                feature_snapshot_ref=f"feature://selection/{selection_run_id}",
                candidate_cache_ref=candidate_cache_ref,
                started_at="2026-06-04T08:00:00Z",
                completed_at="2026-06-04T08:01:00Z",
            ),
            manifest=manifest,
        )
    )


def _write_columnar_manifest(
    *,
    root: Path,
    plan: SelectionRunPlan,
    normalized_refs: tuple[str, ...],
    provider_attempt_refs: tuple[str, ...],
) -> tuple[str, str]:
    os.environ["CLAW_TRADE_SELECTION_COLUMNAR_ROOT"] = str(root)
    writer = SelectionColumnarWarehouse(root=root).begin_write(plan=plan)
    writer.add_daily_rows(
        (
            {
                "ticker": "600000.SH",
                "date": plan.trade_date,
                "close": 10.0,
                "source_ref": normalized_refs[0],
            },
        )
    )
    writer.add_feature_rows(
        (
            {
                "ticker": "600000.SH",
                "company_name": "浦发银行",
                "source_ref": normalized_refs[0],
                "trade_date": plan.trade_date,
                "selection_features_materialized": True,
                "amount": 200000000.0,
            },
        )
    )
    manifest = writer.commit(provider_attempt_refs=provider_attempt_refs, normalized_refs=normalized_refs)
    manifest_sha256 = SelectionColumnarWarehouse(root=root).manifest_sha256(manifest.manifest_ref)
    assert manifest_sha256 is not None
    return manifest.manifest_ref, manifest_sha256


def _selection_artifact_path(artifact_root: Path, uri: str) -> Path:
    prefix = "local://selection/"
    assert uri.startswith(prefix)
    return artifact_root / uri[len(prefix) :].strip("/")


def _write_readback_log(path: Path, *, expected_sha256: str) -> None:
    suffix = path.suffix
    if suffix:
        verify_path = path.with_suffix(f"{suffix}.readback-verify.json")
    else:
        verify_path = path.with_name(f"{path.name}.readback-verify.json")
    verify_path.write_text(
        f"{json.dumps({'status': 'verified', 'expected_sha256': expected_sha256, 'readback_sha256': expected_sha256}, ensure_ascii=False, indent=2)}\n",
        encoding="utf-8",
    )
