from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable

from claw_trade.config.report_workflow_settings import ReportWorkflowSettings
from claw_trade.instruments.resolver import resolve_instrument_identity
from claw_trade.selection.models import (
    SelectionConfirmation,
    SelectionConfirmationStatus,
    SelectionDataRunStatus,
    SelectionDecision,
)
from claw_trade.selection.report_handoff import (
    build_report_handoff_request,
    enqueue_report_handoff,
    utc_today_iso,
)
from claw_trade.selection.store import (
    SelectionDataRunRecord,
    SelectionReportHandoffRecord,
    SelectionRunStore,
)
from claw_trade.ui_backend.report_queue import ReportTaskQueue

_PM_DECISION_MATERIAL_TARGET = "selection_portfolio_decision"
_PM_DECISION_MATERIAL_TYPE = "pm_decision"


class SelectionConfirmationError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.user_message = message


@dataclass(frozen=True)
class SelectionConfirmRequest:
    confirmation_id: str
    idempotency_key: str
    select_workflow_run_id: str
    ticker: str
    origin_context_id: str | None = None

    def __post_init__(self) -> None:
        if not self.confirmation_id.strip():
            raise ValueError("confirmation_id must be non-empty")
        if not self.select_workflow_run_id.strip():
            raise ValueError("select_workflow_run_id must be non-empty")
        if not self.ticker.strip():
            raise ValueError("ticker must be non-empty")
        expected_key = _idempotency_key(
            select_workflow_run_id=self.select_workflow_run_id,
            ticker=self.ticker,
            confirmation_id=self.confirmation_id,
        )
        if self.idempotency_key != expected_key:
            raise ValueError("idempotency_key mismatch")


@dataclass(frozen=True)
class SelectionConfirmResult:
    code: str
    confirmation: SelectionConfirmation
    report_task_id: str | None
    report_run_id: str | None
    report_handoff_dedupe_key: str
    handoff_request: dict[str, Any]
    queue_payload: dict[str, Any]
    deduped: bool


@dataclass(frozen=True)
class _SelectionWorkflowContext:
    select_workflow_run_id: str
    status: str
    selection_run_id: str
    enter_report_tickers: frozenset[str]
    decision_approved_material_id: str
    company_name_by_ticker: dict[str, str]


class SelectionConfirmationController:
    def __init__(
        self,
        *,
        store: SelectionRunStore,
        queue: ReportTaskQueue,
        settings: ReportWorkflowSettings,
        workflow_evidence_root: Path | None = None,
        now_fn: Callable[[], datetime] | None = None,
        today_fn: Callable[[], str] | None = None,
        run_record_loader: Callable[[str], SelectionDataRunRecord | None] | None = None,
    ) -> None:
        self._store = store
        self._queue = queue
        self._settings = settings
        self._workflow_evidence_root = workflow_evidence_root or Path("runs/selection/workflows")
        self._now_fn = now_fn or _utc_now
        self._today_fn = today_fn or utc_today_iso
        self._run_record_loader = run_record_loader or self._load_record_from_store

    def confirm(self, request: SelectionConfirmRequest) -> SelectionConfirmResult:
        existing_by_idempotency = self._store.lookup_confirmation(request.idempotency_key)
        if existing_by_idempotency is not None:
            return _result_from_store_record(existing_by_idempotency)

        normalized_ticker = request.ticker.strip().upper()
        context = self._load_workflow_context(request.select_workflow_run_id)
        if normalized_ticker not in context.enter_report_tickers:
            raise SelectionConfirmationError("ticker_not_allowed", "该标的不在可确认进入 /report 列表。")

        record = self._load_and_validate_record(context)
        report_handoff_dedupe_key = _handoff_dedupe_key(
            select_workflow_run_id=request.select_workflow_run_id,
            ticker=normalized_ticker,
        )
        existing_by_dedupe_key = self._store.lookup_report_handoff_by_dedupe_key(report_handoff_dedupe_key)
        if existing_by_dedupe_key is not None:
            self._store.bind_confirmation_to_existing_handoff(
                idempotency_key=request.idempotency_key,
                existing=existing_by_dedupe_key,
            )
            return _result_from_store_record(existing_by_dedupe_key)

        confirmation = SelectionConfirmation(
            confirmation_id=request.confirmation_id,
            idempotency_key=request.idempotency_key,
            report_handoff_dedupe_key=report_handoff_dedupe_key,
            select_workflow_run_id=request.select_workflow_run_id,
            ticker=normalized_ticker,
            status=SelectionConfirmationStatus.ACCEPTED,
        )
        company_name = _resolve_company_name(
            ticker=normalized_ticker,
            company_name_by_ticker=context.company_name_by_ticker,
        )
        decision = SelectionDecision(
            select_workflow_run_id=context.select_workflow_run_id,
            enter_report=tuple(),
            watch=tuple(),
            reject=tuple(),
            report_questions=None,
            source_summary=None,
            approved_material_id=context.decision_approved_material_id,
        )
        handoff = build_report_handoff_request(
            confirmation=confirmation,
            decision=decision,
            settings=self._settings,
            current_date=self._today_fn(),
            company_name=company_name,
            selection_context_ref=context.decision_approved_material_id,
        )
        queue_request_id = f"selection-handoff:{report_handoff_dedupe_key}"
        started = enqueue_report_handoff(
            queue=self._queue,
            request_id=queue_request_id,
            handoff=handoff,
            origin_context_id=request.origin_context_id,
        )
        started_confirmation = replace(
            confirmation,
            status=SelectionConfirmationStatus.REPORT_HANDOFF_STARTED,
            report_task_id=started.task_id,
            report_run_id=started.run_id,
        )
        record_for_store = SelectionReportHandoffRecord(
            confirmation=started_confirmation,
            report_task_id=started.task_id,
            report_run_id=started.run_id,
            report_handoff_dedupe_key=report_handoff_dedupe_key,
            handoff_request=_handoff_for_payload(handoff),
            queue_payload=dict(started.queue_payload),
            deduped=bool(started.queue_payload.get("deduped", False)),
        )
        self._store.save_report_handoff(
            idempotency_key=request.idempotency_key,
            dedupe_key=report_handoff_dedupe_key,
            record=record_for_store,
        )
        return _result_from_store_record(record_for_store)

    def _load_workflow_context(self, select_workflow_run_id: str) -> _SelectionWorkflowContext:
        path = self._workflow_evidence_root / select_workflow_run_id / "selection-workflow-evidence.json"
        if not path.exists():
            raise SelectionConfirmationError("selection_workflow_not_found", "选股工作流不存在或证据缺失。")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SelectionConfirmationError("selection_workflow_invalid", "选股工作流证据损坏。") from exc
        status = str(payload.get("status") or "").strip()
        if status not in {"completed", "waiting_report_confirmation"}:
            raise SelectionConfirmationError("selection_not_confirmable", "当前选股状态不允许确认进入 /report。")
        selection_run_id = str(payload.get("selection_run_id") or "").strip()
        if not selection_run_id:
            raise SelectionConfirmationError("selection_workflow_invalid", "选股工作流缺少 selection_run_id。")
        decision_payload = payload.get("decision")
        if not isinstance(decision_payload, dict):
            raise SelectionConfirmationError(
                "selection_decision_missing_or_unapproved",
                "选股决策缺失或未通过批准，无法确认。",
            )
        raw_enter = decision_payload.get("enter_report")
        if not isinstance(raw_enter, list):
            raise SelectionConfirmationError(
                "selection_decision_missing_or_unapproved",
                "选股决策缺失或未通过批准，无法确认。",
            )
        approved_material_id = str(decision_payload.get("approved_material_id") or "").strip()
        approval_status = str(decision_payload.get("approval_status") or "").strip().lower()
        decision_workflow_run_id = str(decision_payload.get("select_workflow_run_id") or "").strip()
        decision_material_target = str(decision_payload.get("material_target") or "").strip().lower()
        decision_material_type = str(decision_payload.get("material_type") or "").strip().lower()
        if (
            not approved_material_id
            or approval_status != "approved"
            or decision_workflow_run_id != select_workflow_run_id
            or decision_material_target != _PM_DECISION_MATERIAL_TARGET
            or decision_material_type != _PM_DECISION_MATERIAL_TYPE
        ):
            raise SelectionConfirmationError(
                "selection_decision_missing_or_unapproved",
                "选股决策缺失或未通过批准，无法确认。",
            )
        company_name_by_ticker = _decision_company_names(decision_payload)
        return _SelectionWorkflowContext(
            select_workflow_run_id=select_workflow_run_id,
            status=status,
            selection_run_id=selection_run_id,
            enter_report_tickers=frozenset(str(item).strip().upper() for item in raw_enter if str(item).strip()),
            decision_approved_material_id=approved_material_id,
            company_name_by_ticker=company_name_by_ticker,
        )

    def _load_and_validate_record(self, context: _SelectionWorkflowContext) -> SelectionDataRunRecord:
        record = self._run_record_loader(context.selection_run_id)
        if record is None:
            raise SelectionConfirmationError("selection_run_not_found", "选股批次不存在。")
        if record.data_run.status != SelectionDataRunStatus.COMPLETED:
            raise SelectionConfirmationError("selection_not_confirmable", "选股批次尚未完成，不能确认。")
        candidate_pack_ref = record.data_run.candidate_pack_ref
        if candidate_pack_ref is None:
            raise SelectionConfirmationError("candidate_pack_not_approved", "候选池事实包尚未批准。")
        if context.decision_approved_material_id == candidate_pack_ref.material_id:
            raise SelectionConfirmationError(
                "selection_decision_missing_or_unapproved",
                "选股决策缺失或未通过批准，无法确认。",
            )
        if candidate_pack_ref.selection_run_id != context.selection_run_id:
            raise SelectionConfirmationError("candidate_pack_integrity_failed", "候选池与选股工作流绑定不一致。")
        if not record.integrity.pack_approved:
            raise SelectionConfirmationError("candidate_pack_not_approved", "候选池事实包尚未批准。")
        if _parse_iso(candidate_pack_ref.expires_at) <= _to_utc(self._now_fn()):
            raise SelectionConfirmationError("stale_selection_run", "候选池已过期，请等待下一轮选股。")
        if record.manifest is None:
            raise SelectionConfirmationError("candidate_pack_integrity_failed", "候选池 manifest 缺失。")
        if record.manifest.pack_body_sha256 != candidate_pack_ref.content_sha256:
            raise SelectionConfirmationError("candidate_pack_hash_mismatch", "候选池 hash 校验失败。")
        if not record.integrity.hash_matches_manifest:
            raise SelectionConfirmationError("candidate_pack_hash_mismatch", "候选池 hash 校验失败。")
        if record.manifest.readback_status.value != "verified":
            raise SelectionConfirmationError("candidate_pack_integrity_failed", "候选池 readback 校验失败。")
        if not record.integrity.readback_verified:
            raise SelectionConfirmationError("candidate_pack_integrity_failed", "候选池 readback 校验失败。")
        if not record.integrity.lineage_complete:
            raise SelectionConfirmationError("candidate_pack_lineage_incomplete", "候选池 lineage 不完整。")
        if not record.manifest.source_lineage_refs:
            raise SelectionConfirmationError("candidate_pack_lineage_incomplete", "候选池 lineage 不完整。")
        return record

    def _load_record_from_store(self, selection_run_id: str) -> SelectionDataRunRecord | None:
        record = self._store.load_data_run_record(selection_run_id)
        return record if isinstance(record, SelectionDataRunRecord) else None


def _result_from_store_record(record: SelectionReportHandoffRecord) -> SelectionConfirmResult:
    return SelectionConfirmResult(
        code="report_handoff_started",
        confirmation=record.confirmation,
        report_task_id=record.report_task_id,
        report_run_id=record.report_run_id,
        report_handoff_dedupe_key=record.report_handoff_dedupe_key,
        handoff_request=dict(record.handoff_request),
        queue_payload=dict(record.queue_payload),
        deduped=record.deduped,
    )


def _handoff_for_payload(handoff: Any) -> dict[str, Any]:
    request = handoff.report_request
    return {
        "confirmationId": handoff.confirmation_id,
        "ticker": handoff.ticker,
        "companyName": handoff.company_name,
        "market": handoff.market.value,
        "profile": handoff.profile.value,
        "currentDate": handoff.current_date,
        "selectionContextRef": handoff.selection_context_ref,
        "selectionStageMarker": handoff.selection_stage_marker.value,
        "reportRequest": {
            "ticker": request.ticker,
            "companyName": request.company_name,
            "market": request.market,
            "profile": request.profile,
            "currentDate": request.current_date,
            "startDate": request.start_date,
            "endDate": request.end_date,
            "entryPoint": request.entry_point.value,
        },
    }


def _resolve_company_name(*, ticker: str, company_name_by_ticker: dict[str, str]) -> str:
    resolved = company_name_by_ticker.get(ticker)
    if resolved:
        return resolved
    identity = resolve_instrument_identity(ticker, market_hint="CN_A")
    return identity.ticker


def _decision_company_names(decision_payload: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    for key in ("enter_report_items", "watch_items", "reject_items"):
        items = decision_payload.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            ticker = str(item.get("ticker") or "").strip().upper()
            company_name = str(item.get("company_name") or "").strip()
            if ticker and company_name:
                result[ticker] = company_name
    return result


def _idempotency_key(*, select_workflow_run_id: str, ticker: str, confirmation_id: str) -> str:
    return f"{select_workflow_run_id}:{ticker.strip().upper()}:{confirmation_id}"


def _handoff_dedupe_key(*, select_workflow_run_id: str, ticker: str) -> str:
    return f"{select_workflow_run_id}:{ticker.strip().upper()}"


def _parse_iso(value: str) -> datetime:
    return _to_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)
