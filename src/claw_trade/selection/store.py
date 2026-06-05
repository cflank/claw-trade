from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable, Mapping

from claw_trade.selection.columnar_warehouse import SelectionColumnarWarehouse
from claw_trade.selection.models import (
    CandidatePackManifest,
    CandidatePackReadbackStatus,
    CandidatePackRef,
    SelectionConfirmation,
    SelectionDataRun,
    SelectionDataRunStatus,
    SelectionMarket,
    SelectionProfile,
    SelectionRunPlan,
    SelectionTriggerSource,
)


class SelectUnavailableCode(StrEnum):
    NO_COMPLETED_SELECTION_RUN = "no_completed_selection_run"
    NO_CANDIDATE_SELECTION_RUN = "no_candidate_selection_run"
    STALE_SELECTION_RUN = "stale_selection_run"
    CANDIDATE_PACK_NOT_APPROVED = "candidate_pack_not_approved"
    CANDIDATE_PACK_HASH_MISMATCH = "candidate_pack_hash_mismatch"
    CANDIDATE_PACK_INTEGRITY_FAILED = "candidate_pack_integrity_failed"
    CANDIDATE_PACK_LINEAGE_INCOMPLETE = "candidate_pack_lineage_incomplete"
    SELECTION_WAREHOUSE_CHECK_MISSING = "selection_warehouse_check_missing"
    SELECT_MARKET_UNSUPPORTED = "select_market_unsupported"
    CRYPTO_SELECT_HISTORY_MISSING = "crypto_select_history_missing"


@dataclass(frozen=True)
class SelectionRunIntegrity:
    pack_approved: bool = True
    readback_verified: bool = True
    hash_matches_manifest: bool = True
    lineage_complete: bool = True


@dataclass(frozen=True)
class SelectionDataRunRecord:
    run_plan: SelectionRunPlan
    data_run: SelectionDataRun
    manifest: CandidatePackManifest | None
    integrity: SelectionRunIntegrity = SelectionRunIntegrity()

    def __post_init__(self) -> None:
        if self.run_plan.selection_run_id != self.data_run.selection_run_id:
            raise ValueError("run_plan.selection_run_id must match data_run.selection_run_id")
        if self.manifest is not None:
            if self.manifest.selection_run_id != self.run_plan.selection_run_id:
                raise ValueError("manifest.selection_run_id must match run_plan.selection_run_id")
            if self.manifest.trade_date != self.run_plan.trade_date:
                raise ValueError("manifest.trade_date must match run_plan.trade_date")


@dataclass(frozen=True)
class LatestCompletedSelectionRun:
    run_plan: SelectionRunPlan
    data_run: SelectionDataRun
    manifest: CandidatePackManifest


@dataclass(frozen=True)
class LatestCompletedSelectionRunResult:
    run: LatestCompletedSelectionRun | None
    unavailable_code: SelectUnavailableCode | None

    @classmethod
    def available(cls, run: LatestCompletedSelectionRun) -> LatestCompletedSelectionRunResult:
        return cls(run=run, unavailable_code=None)

    @classmethod
    def unavailable(cls, code: SelectUnavailableCode) -> LatestCompletedSelectionRunResult:
        return cls(run=None, unavailable_code=code)

    @property
    def is_available(self) -> bool:
        return self.run is not None


@dataclass(frozen=True)
class SelectionReportHandoffRecord:
    confirmation: SelectionConfirmation
    report_task_id: str | None
    report_run_id: str | None
    report_handoff_dedupe_key: str
    handoff_request: Mapping[str, Any]
    queue_payload: Mapping[str, Any]
    deduped: bool


_ACTIVE_DATA_RUN_STATUSES = frozenset(
    {
        SelectionDataRunStatus.PLANNED,
        SelectionDataRunStatus.LEASE_PENDING,
        SelectionDataRunStatus.RUNNING,
        SelectionDataRunStatus.FETCHING_DATA,
        SelectionDataRunStatus.NORMALIZING_INPUTS,
        SelectionDataRunStatus.BUILDING_FEATURES,
        SelectionDataRunStatus.FILTERING_AND_SCORING,
        SelectionDataRunStatus.BUILDING_CANDIDATE_PACK,
        SelectionDataRunStatus.APPROVING_CANDIDATE_PACK,
    }
)


class SelectionRunStore:
    def __init__(self, *, persisted_runs_dir: Path | None = None, artifact_root: Path | None = None) -> None:
        self._runs: dict[str, SelectionDataRunRecord] = {}
        self._confirmations_by_idempotency_key: dict[str, SelectionReportHandoffRecord] = {}
        self._handoff_by_dedupe_key: dict[str, SelectionReportHandoffRecord] = {}
        self._persisted_runs_dir = persisted_runs_dir
        self._artifact_root = artifact_root

    def save_data_run_record(self, record: SelectionDataRunRecord) -> None:
        existing = self._runs.get(record.run_plan.selection_run_id)
        if existing is not None and existing.data_run.status in {
            SelectionDataRunStatus.COMPLETED,
            SelectionDataRunStatus.NO_CANDIDATE,
        }:
            if existing != record:
                raise ValueError("immutable_run_update_forbidden")
            return
        self._runs[record.run_plan.selection_run_id] = record
        self._persist_record(record)

    def load_latest_terminal_selection_run(
        self,
        *,
        market: SelectionMarket,
        profile: SelectionProfile,
        trade_date: str | None,
        now: datetime,
    ) -> LatestCompletedSelectionRunResult:
        terminal_candidates = [
            item
            for item in self._runs.values()
            if item.run_plan.market == market
            and item.run_plan.profile == profile
            and item.data_run.status in {SelectionDataRunStatus.COMPLETED, SelectionDataRunStatus.NO_CANDIDATE}
            and (trade_date is None or item.run_plan.trade_date == trade_date)
        ]
        if not terminal_candidates:
            return LatestCompletedSelectionRunResult.unavailable(SelectUnavailableCode.NO_COMPLETED_SELECTION_RUN)

        latest = max(
            terminal_candidates,
            key=lambda item: (
                item.run_plan.trade_date,
                _parse_iso_timestamp(item.data_run.completed_at),
            ),
        )
        if latest.data_run.status == SelectionDataRunStatus.NO_CANDIDATE:
            return LatestCompletedSelectionRunResult.unavailable(SelectUnavailableCode.NO_CANDIDATE_SELECTION_RUN)
        code = _validate_record_for_select(latest, now=now, artifact_root=self._candidate_pack_artifact_root())
        if code is not None:
            return LatestCompletedSelectionRunResult.unavailable(code)
        assert latest.manifest is not None
        return LatestCompletedSelectionRunResult.available(
            LatestCompletedSelectionRun(
                run_plan=latest.run_plan,
                data_run=latest.data_run,
                manifest=latest.manifest,
            )
        )

    def load_latest_completed_selection_run(
        self,
        *,
        market: SelectionMarket,
        profile: SelectionProfile,
        trade_date: str | None,
        now: datetime,
    ) -> LatestCompletedSelectionRunResult:
        return self.load_latest_terminal_selection_run(
            market=market,
            profile=profile,
            trade_date=trade_date,
            now=now,
        )

    def load_data_run_record(self, selection_run_id: str) -> SelectionDataRunRecord | None:
        return self._runs.get(selection_run_id)

    def load_latest_data_run_record(
        self,
        *,
        market: SelectionMarket,
        profile: SelectionProfile,
        trade_date: str,
    ) -> SelectionDataRunRecord | None:
        candidates = [
            item
            for item in self._runs.values()
            if item.run_plan.market == market
            and item.run_plan.profile == profile
            and item.run_plan.trade_date == trade_date
        ]
        if not candidates:
            return None
        return max(
            candidates,
            key=lambda item: (
                _parse_iso_timestamp(
                    item.data_run.started_at
                    or item.data_run.completed_at
                    or item.data_run.failed_at
                    or item.run_plan.trade_date + "T00:00:00+00:00"
                ),
                item.run_plan.selection_run_id,
            ),
        )

    def load_active_data_run_record(
        self,
        *,
        market: SelectionMarket,
        profile: SelectionProfile,
        trade_date: str,
    ) -> SelectionDataRunRecord | None:
        active = [
            item
            for item in self._runs.values()
            if item.run_plan.market == market
            and item.run_plan.profile == profile
            and item.run_plan.trade_date == trade_date
            and item.data_run.status in _ACTIVE_DATA_RUN_STATUSES
        ]
        if not active:
            return None
        return max(
            active,
            key=lambda item: (
                _parse_iso_timestamp(item.data_run.started_at or item.run_plan.trade_date + "T00:00:00+00:00"),
                item.run_plan.selection_run_id,
            ),
        )

    def load_latest_active_data_run_record(
        self,
        *,
        market: SelectionMarket,
        profile: SelectionProfile,
    ) -> SelectionDataRunRecord | None:
        active = [
            item
            for item in self._runs.values()
            if item.run_plan.market == market
            and item.run_plan.profile == profile
            and item.data_run.status in _ACTIVE_DATA_RUN_STATUSES
        ]
        if not active:
            return None
        return max(active, key=_data_run_record_sort_key)

    def load_latest_any_data_run_record(
        self,
        *,
        market: SelectionMarket,
        profile: SelectionProfile,
    ) -> SelectionDataRunRecord | None:
        candidates = [
            item
            for item in self._runs.values()
            if item.run_plan.market == market and item.run_plan.profile == profile
        ]
        if not candidates:
            return None
        return max(candidates, key=_data_run_record_sort_key)

    def has_active_data_run(
        self,
        *,
        market: SelectionMarket,
        profile: SelectionProfile,
        trade_date: str,
    ) -> bool:
        return self.load_active_data_run_record(market=market, profile=profile, trade_date=trade_date) is not None

    def lookup_confirmation(self, idempotency_key: str) -> SelectionReportHandoffRecord | None:
        return self._confirmations_by_idempotency_key.get(idempotency_key)

    def lookup_report_handoff_by_dedupe_key(self, dedupe_key: str) -> SelectionReportHandoffRecord | None:
        return self._handoff_by_dedupe_key.get(dedupe_key)

    def bind_confirmation_to_existing_handoff(
        self,
        *,
        idempotency_key: str,
        existing: SelectionReportHandoffRecord,
    ) -> None:
        self._confirmations_by_idempotency_key[idempotency_key] = existing

    def save_report_handoff(
        self,
        *,
        idempotency_key: str,
        dedupe_key: str,
        record: SelectionReportHandoffRecord,
    ) -> None:
        self._confirmations_by_idempotency_key[idempotency_key] = record
        self._handoff_by_dedupe_key[dedupe_key] = record

    def _persist_record(self, record: SelectionDataRunRecord) -> None:
        if self._persisted_runs_dir is None:
            return
        self._persisted_runs_dir.mkdir(parents=True, exist_ok=True)
        output = self._persisted_runs_dir / f"{record.run_plan.selection_run_id}.json"
        payload = _serialize_data_run_record(record)
        tmp = output.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(output)

    def _candidate_pack_artifact_root(self) -> Path:
        if self._artifact_root is not None:
            return self._artifact_root
        if self._persisted_runs_dir is not None:
            return self._persisted_runs_dir.parent.parent / "artifacts"
        return Path("runs/selection/artifacts")


def restore_selection_run_store(
    *,
    selection_runs_root: Path | None = None,
    fail_interrupted_active: bool = False,
) -> SelectionRunStore:
    root = selection_runs_root or Path("runs/selection")
    persisted_runs_dir = root / "store" / "data-runs"
    store = SelectionRunStore(persisted_runs_dir=persisted_runs_dir)
    restored_run_ids: set[str] = set()
    for record in _iter_records_from_persisted_store_dir(persisted_runs_dir):
        if record.run_plan.selection_run_id in restored_run_ids:
            continue
        if fail_interrupted_active:
            record = _fail_interrupted_active_record(record)
        store.save_data_run_record(record)
        restored_run_ids.add(record.run_plan.selection_run_id)
    for record in _iter_records_from_data_job_evidence(root):
        if record.run_plan.selection_run_id in restored_run_ids:
            continue
        store.save_data_run_record(record)
        restored_run_ids.add(record.run_plan.selection_run_id)
    return store


def _fail_interrupted_active_record(record: SelectionDataRunRecord) -> SelectionDataRunRecord:
    if record.data_run.status not in _ACTIVE_DATA_RUN_STATUSES:
        return record
    failed_at = _isoformat(_utc_now())
    data_run = SelectionDataRun(
        selection_run_id=record.data_run.selection_run_id,
        status=SelectionDataRunStatus.FAILED,
        lease_id=record.data_run.lease_id,
        universe_snapshot_ref=record.data_run.universe_snapshot_ref,
        normalized_refs=record.data_run.normalized_refs,
        provider_attempt_refs=record.data_run.provider_attempt_refs,
        select_data_plan_ref=record.data_run.select_data_plan_ref,
        warehouse_check_ref=record.data_run.warehouse_check_ref,
        columnar_manifest_ref=record.data_run.columnar_manifest_ref,
        columnar_manifest_sha256=record.data_run.columnar_manifest_sha256,
        feature_snapshot_ref=record.data_run.feature_snapshot_ref,
        data_gaps=record.data_run.data_gaps,
        started_at=record.data_run.started_at,
        failed_at=failed_at,
        failure_code="selection_data_run_interrupted",
        failure_reason=(
            "服务启动时发现该补数据任务仍处于运行态；上一次进程已中断，"
            "原任务按 fail closed 标记失败。请重新发送 /select 启动新的补数据。"
        ),
    )
    return SelectionDataRunRecord(
        run_plan=record.run_plan,
        data_run=data_run,
        manifest=None,
        integrity=record.integrity,
    )


def resolve_latest_terminal_selection_run(
    *,
    store: SelectionRunStore,
    market: SelectionMarket,
    profile: SelectionProfile,
    trade_date: str | None,
    now_fn: Callable[[], datetime] | None = None,
) -> LatestCompletedSelectionRunResult:
    now_provider = now_fn or _utc_now
    return store.load_latest_terminal_selection_run(
        market=market,
        profile=profile,
        trade_date=trade_date,
        now=now_provider(),
    )


def resolve_latest_completed_selection_run(
    *,
    store: SelectionRunStore,
    market: SelectionMarket,
    profile: SelectionProfile,
    trade_date: str | None,
    now_fn: Callable[[], datetime] | None = None,
) -> LatestCompletedSelectionRunResult:
    return resolve_latest_terminal_selection_run(
        store=store,
        market=market,
        profile=profile,
        trade_date=trade_date,
        now_fn=now_fn,
    )


def _validate_record_for_select(
    record: SelectionDataRunRecord,
    *,
    now: datetime,
    artifact_root: Path,
) -> SelectUnavailableCode | None:
    if not record.integrity.pack_approved:
        return SelectUnavailableCode.CANDIDATE_PACK_NOT_APPROVED
    if record.data_run.candidate_pack_ref is None:
        return SelectUnavailableCode.CANDIDATE_PACK_NOT_APPROVED
    if _parse_iso_timestamp(record.data_run.candidate_pack_ref.expires_at) <= _to_utc(now):
        return SelectUnavailableCode.STALE_SELECTION_RUN
    if record.manifest is None:
        return SelectUnavailableCode.CANDIDATE_PACK_INTEGRITY_FAILED
    if record.manifest.readback_status != CandidatePackReadbackStatus.VERIFIED:
        return SelectUnavailableCode.CANDIDATE_PACK_INTEGRITY_FAILED
    if not record.integrity.readback_verified:
        return SelectUnavailableCode.CANDIDATE_PACK_INTEGRITY_FAILED
    if record.manifest.pack_body_sha256 != record.data_run.candidate_pack_ref.content_sha256:
        return SelectUnavailableCode.CANDIDATE_PACK_HASH_MISMATCH
    if not record.integrity.hash_matches_manifest:
        return SelectUnavailableCode.CANDIDATE_PACK_HASH_MISMATCH
    if not record.integrity.lineage_complete:
        return SelectUnavailableCode.CANDIDATE_PACK_LINEAGE_INCOMPLETE
    if not record.manifest.source_lineage_refs:
        return SelectUnavailableCode.CANDIDATE_PACK_LINEAGE_INCOMPLETE
    file_code = _validate_candidate_pack_files_for_select(record, artifact_root=artifact_root)
    if file_code is not None:
        return file_code
    warehouse_code = _validate_warehouse_evidence_for_select(record)
    if warehouse_code is not None:
        return warehouse_code
    return None


def _validate_candidate_pack_files_for_select(
    record: SelectionDataRunRecord,
    *,
    artifact_root: Path,
) -> SelectUnavailableCode | None:
    candidate_pack_ref = record.data_run.candidate_pack_ref
    if candidate_pack_ref is None:
        return SelectUnavailableCode.CANDIDATE_PACK_NOT_APPROVED

    body_path = _resolve_selection_artifact_path(candidate_pack_ref.l1_uri, artifact_root=artifact_root)
    manifest_path = _resolve_selection_artifact_path(candidate_pack_ref.manifest_ref, artifact_root=artifact_root)
    summary_path = _resolve_selection_artifact_path(candidate_pack_ref.pack_summary_ref, artifact_root=artifact_root)
    if body_path is None or manifest_path is None or summary_path is None:
        return SelectUnavailableCode.CANDIDATE_PACK_INTEGRITY_FAILED

    try:
        body_bytes = body_path.read_bytes()
    except OSError:
        return SelectUnavailableCode.CANDIDATE_PACK_HASH_MISMATCH
    if sha256(body_bytes).hexdigest() != candidate_pack_ref.content_sha256:
        return SelectUnavailableCode.CANDIDATE_PACK_HASH_MISMATCH

    manifest_payload = _read_json_object(manifest_path)
    if manifest_payload is None:
        return SelectUnavailableCode.CANDIDATE_PACK_INTEGRITY_FAILED
    try:
        manifest = _candidate_pack_manifest_from_payload(manifest_payload)
    except (TypeError, ValueError):
        return SelectUnavailableCode.CANDIDATE_PACK_INTEGRITY_FAILED
    if manifest is None:
        return SelectUnavailableCode.CANDIDATE_PACK_INTEGRITY_FAILED
    if manifest.pack_body_sha256 != candidate_pack_ref.content_sha256:
        return SelectUnavailableCode.CANDIDATE_PACK_HASH_MISMATCH
    if manifest.selection_run_id != record.run_plan.selection_run_id:
        return SelectUnavailableCode.CANDIDATE_PACK_INTEGRITY_FAILED
    if manifest.trade_date != record.run_plan.trade_date:
        return SelectUnavailableCode.CANDIDATE_PACK_INTEGRITY_FAILED
    if manifest.readback_status != CandidatePackReadbackStatus.VERIFIED:
        return SelectUnavailableCode.CANDIDATE_PACK_INTEGRITY_FAILED

    if not _readback_verify_log_matches(
        body_path,
        expected_sha256=candidate_pack_ref.content_sha256,
    ):
        return SelectUnavailableCode.CANDIDATE_PACK_INTEGRITY_FAILED
    try:
        manifest_sha256 = sha256(manifest_path.read_bytes()).hexdigest()
    except OSError:
        return SelectUnavailableCode.CANDIDATE_PACK_INTEGRITY_FAILED
    if not _readback_verify_log_matches(manifest_path, expected_sha256=manifest_sha256):
        return SelectUnavailableCode.CANDIDATE_PACK_INTEGRITY_FAILED
    try:
        if not summary_path.read_text(encoding="utf-8").strip():
            return SelectUnavailableCode.CANDIDATE_PACK_INTEGRITY_FAILED
    except OSError:
        return SelectUnavailableCode.CANDIDATE_PACK_INTEGRITY_FAILED
    return None


def _resolve_selection_artifact_path(ref: str, *, artifact_root: Path) -> Path | None:
    prefix = "local://selection/"
    if ref.startswith(prefix):
        relative = ref[len(prefix) :].strip("/")
        segments = [part for part in relative.split("/") if part]
        if not segments or ".." in segments:
            return None
        return artifact_root / Path(*segments)
    return Path(ref)


def _readback_verify_log_matches(path: Path, *, expected_sha256: str) -> bool:
    payload = _read_json_object(_readback_verify_path(path))
    if payload is None:
        return False
    return (
        _optional_text(payload.get("status")) == "verified"
        and _optional_text(payload.get("expected_sha256")) == expected_sha256
        and _optional_text(payload.get("readback_sha256")) == expected_sha256
    )


def _readback_verify_path(path: Path) -> Path:
    suffix = path.suffix
    if suffix:
        return path.with_suffix(f"{suffix}.readback-verify.json")
    return path.with_name(f"{path.name}.readback-verify.json")


def _validate_warehouse_evidence_for_select(record: SelectionDataRunRecord) -> SelectUnavailableCode | None:
    if record.run_plan.market in {SelectionMarket.HK, SelectionMarket.US}:
        return SelectUnavailableCode.SELECT_MARKET_UNSUPPORTED
    if record.run_plan.market == SelectionMarket.CRYPTO:
        return SelectUnavailableCode.CRYPTO_SELECT_HISTORY_MISSING
    if record.run_plan.provider_batch_plan_ref.startswith("restored://"):
        return SelectUnavailableCode.SELECTION_WAREHOUSE_CHECK_MISSING
    data_run = record.data_run
    if not data_run.select_data_plan_ref or not data_run.warehouse_check_ref:
        return SelectUnavailableCode.SELECTION_WAREHOUSE_CHECK_MISSING
    if not data_run.columnar_manifest_ref:
        return SelectUnavailableCode.SELECTION_WAREHOUSE_CHECK_MISSING
    if not data_run.columnar_manifest_sha256:
        return SelectUnavailableCode.SELECTION_WAREHOUSE_CHECK_MISSING
    if not SelectionColumnarWarehouse.default().validate_manifest_ref(
        data_run.columnar_manifest_ref,
        expected_sha256=data_run.columnar_manifest_sha256,
    ):
        return SelectUnavailableCode.SELECTION_WAREHOUSE_CHECK_MISSING
    if not data_run.provider_attempt_refs:
        return SelectUnavailableCode.SELECTION_WAREHOUSE_CHECK_MISSING
    if not data_run.normalized_refs:
        return SelectUnavailableCode.SELECTION_WAREHOUSE_CHECK_MISSING
    if any(not _is_unified_normalized_ref(ref) for ref in data_run.normalized_refs):
        return SelectUnavailableCode.SELECTION_WAREHOUSE_CHECK_MISSING
    return None


def _is_unified_normalized_ref(ref: str) -> bool:
    return ref.startswith("normalized://mongo/normalized_datasets/") or ref.startswith("mongo://normalized_datasets/")


def _parse_iso_timestamp(value: str | None) -> datetime:
    if value is None:
        raise ValueError("completed_at is required for completed run ordering")
    normalized = value.replace("Z", "+00:00")
    return _to_utc(datetime.fromisoformat(normalized))


def _data_run_record_sort_key(record: SelectionDataRunRecord) -> tuple[datetime, str, str]:
    data_run = record.data_run
    timestamp = (
        data_run.started_at
        or data_run.completed_at
        or data_run.failed_at
        or f"{record.run_plan.trade_date}T00:00:00+00:00"
    )
    return (
        _parse_iso_timestamp(timestamp),
        record.run_plan.trade_date,
        record.run_plan.selection_run_id,
    )


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _isoformat(value: datetime) -> str:
    return _to_utc(value).isoformat().replace("+00:00", "Z")


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


def _iter_records_from_persisted_store_dir(persisted_runs_dir: Path) -> tuple[SelectionDataRunRecord, ...]:
    if not persisted_runs_dir.exists():
        return ()
    records: list[SelectionDataRunRecord] = []
    for path in sorted(persisted_runs_dir.glob("*.json")):
        payload = _read_json_object(path)
        if payload is None:
            continue
        record = _record_from_persisted_payload(payload)
        if record is not None:
            records.append(record)
    return tuple(records)


def _iter_records_from_data_job_evidence(selection_runs_root: Path) -> tuple[SelectionDataRunRecord, ...]:
    if not selection_runs_root.exists():
        return ()
    records: list[SelectionDataRunRecord] = []
    for day_dir in sorted(selection_runs_root.iterdir()):
        if not day_dir.is_dir():
            continue
        if not _looks_like_trade_date_dir(day_dir.name):
            continue
        for path in sorted(day_dir.glob("*.json")):
            payload = _read_json_object(path)
            if payload is None:
                continue
            record = _record_from_legacy_data_job_payload(payload, selection_runs_root=selection_runs_root)
            if record is not None:
                records.append(record)
    return tuple(records)


def _record_from_persisted_payload(payload: Mapping[str, Any]) -> SelectionDataRunRecord | None:
    try:
        run_plan_payload = _read_mapping(payload, "run_plan")
        data_run_payload = _read_mapping(payload, "data_run")
        run_plan = SelectionRunPlan(
            selection_run_id=_read_text(run_plan_payload, "selection_run_id"),
            market=SelectionMarket(_read_text(run_plan_payload, "market")),
            profile=SelectionProfile(_read_text(run_plan_payload, "profile")),
            trade_date=_read_text(run_plan_payload, "trade_date"),
            lookback_trading_days=int(run_plan_payload.get("lookback_trading_days", 1)),
            universe_scope=_read_text(run_plan_payload, "universe_scope"),
            provider_batch_plan_ref=_read_text(run_plan_payload, "provider_batch_plan_ref"),
            approved_strategy_config_ref=_read_text(run_plan_payload, "approved_strategy_config_ref"),
            trigger_source=SelectionTriggerSource(_read_text(run_plan_payload, "trigger_source")),
            supersedes_run_id=_optional_text(run_plan_payload.get("supersedes_run_id")),
        )
        candidate_pack_payload = _read_mapping(data_run_payload, "candidate_pack_ref", optional=True)
        candidate_pack_ref = _candidate_pack_ref_from_payload(candidate_pack_payload)
        data_run = SelectionDataRun(
            selection_run_id=_read_text(data_run_payload, "selection_run_id"),
            status=SelectionDataRunStatus(_read_text(data_run_payload, "status")),
            lease_id=_optional_text(data_run_payload.get("lease_id")),
            universe_snapshot_ref=_optional_text(data_run_payload.get("universe_snapshot_ref")),
            normalized_refs=tuple(_read_text_list(data_run_payload, "normalized_refs")),
            provider_attempt_refs=tuple(_read_text_list(data_run_payload, "provider_attempt_refs")),
            select_data_plan_ref=_optional_text(data_run_payload.get("select_data_plan_ref")),
            warehouse_check_ref=_optional_text(data_run_payload.get("warehouse_check_ref")),
            columnar_manifest_ref=_optional_text(data_run_payload.get("columnar_manifest_ref")),
            columnar_manifest_sha256=_optional_text(data_run_payload.get("columnar_manifest_sha256")),
            feature_snapshot_ref=_optional_text(data_run_payload.get("feature_snapshot_ref")),
            candidate_pack_ref=candidate_pack_ref,
            started_at=_optional_text(data_run_payload.get("started_at")),
            completed_at=_optional_text(data_run_payload.get("completed_at")),
            failed_at=_optional_text(data_run_payload.get("failed_at")),
            failure_code=_optional_text(data_run_payload.get("failure_code")),
            failure_reason=_optional_text(data_run_payload.get("failure_reason")),
            progress_label=_optional_text(data_run_payload.get("progress_label")),
            progress_completed=_optional_int(data_run_payload.get("progress_completed")),
            progress_total=_optional_int(data_run_payload.get("progress_total")),
        )
        manifest_payload = _read_mapping(payload, "manifest", optional=True)
        manifest = _candidate_pack_manifest_from_payload(manifest_payload)
        integrity_payload = _read_mapping(payload, "integrity", optional=True)
        integrity = SelectionRunIntegrity(
            pack_approved=bool(integrity_payload.get("pack_approved", True)) if integrity_payload else True,
            readback_verified=bool(integrity_payload.get("readback_verified", True)) if integrity_payload else True,
            hash_matches_manifest=bool(integrity_payload.get("hash_matches_manifest", True)) if integrity_payload else True,
            lineage_complete=bool(integrity_payload.get("lineage_complete", True)) if integrity_payload else True,
        )
        return SelectionDataRunRecord(
            run_plan=run_plan,
            data_run=data_run,
            manifest=manifest,
            integrity=integrity,
        )
    except (ValueError, TypeError):
        return None


def _record_from_legacy_data_job_payload(
    payload: Mapping[str, Any],
    *,
    selection_runs_root: Path,
) -> SelectionDataRunRecord | None:
    try:
        if _read_text(payload, "status") != SelectionDataRunStatus.COMPLETED.value:
            return None
        selection_run_id = _read_text(payload, "selection_run_id")
        candidate_pack_payload = _read_mapping(payload, "candidate_pack_ref")
        manifest_payload = _read_mapping(payload, "candidate_pack_manifest")
        market_text = _optional_text(payload.get("market"))
        profile_text = _optional_text(payload.get("profile"))
        trade_date_text = _optional_text(payload.get("trade_date"))
        manifest = _candidate_pack_manifest_from_payload(
            manifest_payload,
            fallback_market=market_text,
            fallback_profile=profile_text,
            fallback_trade_date=trade_date_text,
        )
        if manifest is None:
            return None
        approved_at = _optional_text(candidate_pack_payload.get("approved_at"))
        expires_at = _optional_text(candidate_pack_payload.get("expires_at"))
        if approved_at is None or expires_at is None:
            approved_at, expires_at = _legacy_candidate_pack_times(
                candidate_pack_payload=candidate_pack_payload,
                selection_runs_root=selection_runs_root,
            )
        candidate_pack_ref = CandidatePackRef(
            selection_run_id=selection_run_id,
            material_id=_read_text(candidate_pack_payload, "material_id"),
            l1_uri=_read_text(candidate_pack_payload, "l1_uri"),
            content_sha256=_read_text(candidate_pack_payload, "content_sha256"),
            manifest_ref=_read_text(candidate_pack_payload, "manifest_ref"),
            approved_at=approved_at,
            expires_at=expires_at,
            pack_summary_ref=_read_text(candidate_pack_payload, "pack_summary_ref"),
        )
        completed_at = _optional_text(payload.get("completed_at")) or approved_at
        run_plan = SelectionRunPlan(
            selection_run_id=selection_run_id,
            market=manifest.market,
            profile=manifest.profile,
            trade_date=_optional_text(payload.get("trade_date")) or manifest.trade_date,
            lookback_trading_days=int(payload.get("lookback_trading_days", 1)),
            universe_scope=_optional_text(payload.get("universe_scope")) or "restored_from_data_job_evidence",
            provider_batch_plan_ref=(
                _optional_text(payload.get("provider_batch_plan_ref")) or f"restored://{selection_run_id}"
            ),
            approved_strategy_config_ref=manifest.strategy_config_ref,
            trigger_source=SelectionTriggerSource(
                _optional_text(payload.get("trigger_source")) or SelectionTriggerSource.SCHEDULED.value
            ),
            supersedes_run_id=_optional_text(payload.get("supersedes_run_id")),
        )
        data_run = SelectionDataRun(
            selection_run_id=selection_run_id,
            status=SelectionDataRunStatus.COMPLETED,
            normalized_refs=tuple(_read_text_list(payload, "normalized_refs")),
            provider_attempt_refs=tuple(_read_text_list(payload, "provider_attempt_refs")),
            select_data_plan_ref=_optional_text(payload.get("select_data_plan_ref")),
            warehouse_check_ref=_optional_text(payload.get("warehouse_check_ref")),
            columnar_manifest_ref=_optional_text(payload.get("columnar_manifest_ref")),
            columnar_manifest_sha256=_optional_text(payload.get("columnar_manifest_sha256")),
            feature_snapshot_ref=_optional_text(payload.get("feature_snapshot_ref")),
            candidate_pack_ref=candidate_pack_ref,
            completed_at=completed_at,
        )
        return SelectionDataRunRecord(
            run_plan=run_plan,
            data_run=data_run,
            manifest=manifest,
        )
    except (ValueError, TypeError):
        return None


def _legacy_candidate_pack_times(
    *,
    candidate_pack_payload: Mapping[str, Any],
    selection_runs_root: Path,
) -> tuple[str, str]:
    manifest_ref = _read_text(candidate_pack_payload, "manifest_ref")
    if not manifest_ref.startswith("local://selection/"):
        raise ValueError("unsupported manifest_ref uri")
    relative = manifest_ref[len("local://selection/") :]
    manifest_path = selection_runs_root / "artifacts" / relative
    if manifest_path.suffix:
        verify_path = manifest_path.with_suffix(f"{manifest_path.suffix}.readback-verify.json")
    else:
        verify_path = manifest_path.with_name(f"{manifest_path.name}.readback-verify.json")
    verify_payload = _read_json_object(verify_path)
    if verify_payload is None:
        raise ValueError("candidate pack verification log missing")
    verified_at = _read_text(verify_payload, "verified_at")
    approved_at_ts = _parse_iso_timestamp(verified_at)
    expires_at_ts = approved_at_ts.replace(microsecond=0) + _ONE_DAY
    approved_at = approved_at_ts.isoformat().replace("+00:00", "Z")
    expires_at = expires_at_ts.isoformat().replace("+00:00", "Z")
    return approved_at, expires_at


def _serialize_data_run_record(record: SelectionDataRunRecord) -> dict[str, Any]:
    run_plan = record.run_plan
    data_run = record.data_run
    candidate_pack_ref = data_run.candidate_pack_ref
    manifest = record.manifest
    return {
        "schema_version": "selection-run-store-v1",
        "run_plan": {
            "selection_run_id": run_plan.selection_run_id,
            "market": run_plan.market.value,
            "profile": run_plan.profile.value,
            "trade_date": run_plan.trade_date,
            "lookback_trading_days": run_plan.lookback_trading_days,
            "universe_scope": run_plan.universe_scope,
            "provider_batch_plan_ref": run_plan.provider_batch_plan_ref,
            "approved_strategy_config_ref": run_plan.approved_strategy_config_ref,
            "trigger_source": run_plan.trigger_source.value,
            "supersedes_run_id": run_plan.supersedes_run_id,
        },
        "data_run": {
            "selection_run_id": data_run.selection_run_id,
            "status": data_run.status.value,
            "lease_id": data_run.lease_id,
            "universe_snapshot_ref": data_run.universe_snapshot_ref,
            "normalized_refs": list(data_run.normalized_refs),
            "provider_attempt_refs": list(data_run.provider_attempt_refs),
            "select_data_plan_ref": data_run.select_data_plan_ref,
            "warehouse_check_ref": data_run.warehouse_check_ref,
            "columnar_manifest_ref": data_run.columnar_manifest_ref,
            "columnar_manifest_sha256": data_run.columnar_manifest_sha256,
            "feature_snapshot_ref": data_run.feature_snapshot_ref,
            "candidate_pack_ref": (
                {
                    "selection_run_id": candidate_pack_ref.selection_run_id,
                    "material_id": candidate_pack_ref.material_id,
                    "l1_uri": candidate_pack_ref.l1_uri,
                    "content_sha256": candidate_pack_ref.content_sha256,
                    "manifest_ref": candidate_pack_ref.manifest_ref,
                    "approved_at": candidate_pack_ref.approved_at,
                    "expires_at": candidate_pack_ref.expires_at,
                    "pack_summary_ref": candidate_pack_ref.pack_summary_ref,
                }
                if candidate_pack_ref is not None
                else None
            ),
            "started_at": data_run.started_at,
            "completed_at": data_run.completed_at,
            "failed_at": data_run.failed_at,
            "failure_code": data_run.failure_code,
            "failure_reason": data_run.failure_reason,
            "progress_label": data_run.progress_label,
            "progress_completed": data_run.progress_completed,
            "progress_total": data_run.progress_total,
        },
        "manifest": (
            {
                "schema_version": manifest.schema_version,
                "selection_run_id": manifest.selection_run_id,
                "market": manifest.market.value,
                "profile": manifest.profile.value,
                "trade_date": manifest.trade_date,
                "candidate_count": manifest.candidate_count,
                "source_lineage_refs": list(manifest.source_lineage_refs),
                "pack_body_sha256": manifest.pack_body_sha256,
                "strategy_config_ref": manifest.strategy_config_ref,
                "strategy_config_version": manifest.strategy_config_version,
                "weight_version": manifest.weight_version,
                "candidate_scores_ref": manifest.candidate_scores_ref,
                "stable_top20_rule": dict(manifest.stable_top20_rule or {}),
                "readback_status": manifest.readback_status.value,
                "stage": manifest.stage,
                "target": manifest.target,
            }
            if manifest is not None
            else None
        ),
        "integrity": {
            "pack_approved": record.integrity.pack_approved,
            "readback_verified": record.integrity.readback_verified,
            "hash_matches_manifest": record.integrity.hash_matches_manifest,
            "lineage_complete": record.integrity.lineage_complete,
        },
    }


def _candidate_pack_ref_from_payload(payload: Mapping[str, Any] | None) -> CandidatePackRef | None:
    if payload is None:
        return None
    return CandidatePackRef(
        selection_run_id=_read_text(payload, "selection_run_id"),
        material_id=_read_text(payload, "material_id"),
        l1_uri=_read_text(payload, "l1_uri"),
        content_sha256=_read_text(payload, "content_sha256"),
        manifest_ref=_read_text(payload, "manifest_ref"),
        approved_at=_read_text(payload, "approved_at"),
        expires_at=_read_text(payload, "expires_at"),
        pack_summary_ref=_read_text(payload, "pack_summary_ref"),
    )


def _candidate_pack_manifest_from_payload(
    payload: Mapping[str, Any] | None,
    *,
    fallback_market: str | None = None,
    fallback_profile: str | None = None,
    fallback_trade_date: str | None = None,
) -> CandidatePackManifest | None:
    if payload is None:
        return None
    market_text = _optional_text(payload.get("market")) or fallback_market
    profile_text = _optional_text(payload.get("profile")) or fallback_profile
    trade_date_text = _optional_text(payload.get("trade_date")) or fallback_trade_date
    if market_text is None or profile_text is None or trade_date_text is None:
        raise ValueError("manifest market/profile/trade_date missing")
    return CandidatePackManifest(
        schema_version=_read_text(payload, "schema_version"),
        selection_run_id=_read_text(payload, "selection_run_id"),
        market=SelectionMarket(market_text),
        profile=SelectionProfile(profile_text),
        trade_date=trade_date_text,
        candidate_count=int(payload.get("candidate_count", 0)),
        source_lineage_refs=tuple(_read_text_list(payload, "source_lineage_refs")),
        pack_body_sha256=_read_text(payload, "pack_body_sha256"),
        strategy_config_ref=_read_text(payload, "strategy_config_ref"),
        readback_status=CandidatePackReadbackStatus(_read_text(payload, "readback_status")),
        strategy_config_version=_optional_text(payload.get("strategy_config_version")) or "cn_a.selection_strategy.v1",
        weight_version=_optional_text(payload.get("weight_version")) or "cn_a.selection_weights.v1",
        candidate_scores_ref=_optional_text(payload.get("candidate_scores_ref")),
        stable_top20_rule=_read_mapping(payload, "stable_top20_rule", optional=True),
        stage=_optional_text(payload.get("stage")) or "approving_candidate_pack",
        target=_optional_text(payload.get("target")) or "candidate_pack",
    )


def _read_json_object(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _read_mapping(payload: Mapping[str, Any], field: str, *, optional: bool = False) -> Mapping[str, Any] | None:
    value = payload.get(field)
    if value is None:
        if optional:
            return None
        raise ValueError(f"missing field: {field}")
    if not isinstance(value, Mapping):
        raise ValueError(f"field must be object: {field}")
    return value


def _read_text(payload: Mapping[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str):
        raise ValueError(f"field must be string: {field}")
    text = value.strip()
    if not text:
        raise ValueError(f"field must be non-empty: {field}")
    return text


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    return int(value)


def _read_text_list(payload: Mapping[str, Any], field: str) -> tuple[str, ...]:
    raw = payload.get(field)
    if raw is None:
        return ()
    if not isinstance(raw, list | tuple):
        raise ValueError(f"field must be list: {field}")
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            raise ValueError(f"list item must be string: {field}")
        text = item.strip()
        if not text:
            raise ValueError(f"list item must be non-empty: {field}")
        out.append(text)
    return tuple(out)


def _looks_like_trade_date_dir(name: str) -> bool:
    return re.match(r"^\d{4}-\d{2}-\d{2}$", name) is not None


_ONE_DAY = timedelta(days=1)
