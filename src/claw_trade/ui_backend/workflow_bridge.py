from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from claw_trade.config.report_workflow_settings import ReportWorkflowSettings
from claw_trade.instruments.resolver import InstrumentResolveError, resolve_instrument_identity
from claw_trade.workflow.models import RunRequest, RunStatus, WorkflowEntryPoint
from claw_trade.workflow.report_request_factory import build_report_run_request

_LOGGER = logging.getLogger("uvicorn.error")


class WorkflowRunStartPending(RuntimeError):
    def __init__(self, attempt_id: str) -> None:
        super().__init__("workflow_start_timeout")
        self.attempt_id = attempt_id


@dataclass(frozen=True)
class WorkflowStartAttemptStatus:
    run_id: str | None
    has_exited: bool
    error: str | None = None


@dataclass(frozen=True)
class WorkflowRunRecord:
    run_id: str
    status: str
    created_at: str


class WorkflowRunnerPort(Protocol):
    def create_run(self, request: RunRequest) -> str | dict[str, Any]: ...

    def load_state(self, run_id: str) -> Any: ...

    def wait_run_exit(self, run_id: str) -> bool: ...


class CompanyNameResolver(Protocol):
    def __call__(self, *, market: str, symbol_ids: Sequence[str]) -> Mapping[str, str]: ...


class ReportWorkflowBridge:
    def __init__(self, runner: WorkflowRunnerPort, *, company_name_resolver: CompanyNameResolver | None = None) -> None:
        self._runner = runner
        self._company_name_resolver = company_name_resolver
        self._runs: dict[str, WorkflowRunRecord] = {}

    def build_run_request(self, task: dict[str, Any]) -> RunRequest:
        settings = task["workflowSettings"]
        ticker = str(task["instrumentCode"])
        market = str(task["market"])
        return build_report_run_request(
            ticker=ticker,
            company_name=self._company_name_for_task(task=task, ticker=ticker, market=market),
            market=market,
            profile=str(settings.get("defaultProfile") or task["market"]),
            currency=str(settings.get("defaultCurrency") or ""),
            currency_symbol=str(settings.get("defaultCurrencySymbol") or ""),
            current_date=str(task["currentDate"]),
            start_date=str(task["startDate"]),
            end_date=str(task["endDate"]),
            settings=_settings_from_task(settings),
            entry_point=WorkflowEntryPoint.REPORT_COMMAND,
            ui_origin_context_id=_optional_task_text(task.get("originContextId") or task.get("origin_context_id")),
            ui_notification_intent_id=_optional_task_text(
                task.get("notificationIntentId") or task.get("notification_intent_id")
            ),
        )

    def create_workflow_run(self, task: dict[str, Any]) -> WorkflowRunRecord:
        request = self.build_run_request(task)
        raw = self._runner.create_run(request)
        if isinstance(raw, dict):
            run_id = str(raw.get("runId") or raw.get("run_id") or "").strip()
        else:
            run_id = str(raw).strip()
        if not run_id:
            raise ValueError("workflow_failed")
        record = WorkflowRunRecord(run_id=run_id, status=RunStatus.CREATED.value, created_at=_now_iso())
        self._runs[run_id] = record
        return record

    def poll_workflow_run(self, run_id: str) -> dict[str, str]:
        state = self._runner.load_state(run_id)
        if isinstance(state, dict):
            status = str(state.get("status") or "").strip() or "unknown"
        else:
            status = str(getattr(state, "status", "unknown"))
        return {"runId": run_id, "status": status}

    def load_workflow_state(self, run_id: str) -> Any:
        return self._runner.load_state(run_id)

    def cancel_workflow_run(self, run_id: str) -> bool:
        cancel_run = getattr(self._runner, "cancel_run", None)
        if not callable(cancel_run):
            return False
        return bool(cancel_run(run_id))

    def workflow_run_has_exited(self, run_id: str) -> bool:
        has_exited = getattr(self._runner, "run_has_exited", None)
        return bool(callable(has_exited) and has_exited(run_id))

    def wait_for_workflow_run_exit(self, run_id: str) -> bool:
        wait_for_exit = getattr(self._runner, "wait_run_exit", None)
        return bool(callable(wait_for_exit) and wait_for_exit(run_id))

    def supports_workflow_run_exit_wait(self) -> bool:
        return callable(getattr(self._runner, "wait_run_exit", None))

    def poll_workflow_start_attempt(self, attempt_id: str) -> WorkflowStartAttemptStatus:
        poll_attempt = getattr(self._runner, "poll_start_attempt", None)
        if not callable(poll_attempt):
            return WorkflowStartAttemptStatus(run_id=None, has_exited=False)
        return poll_attempt(attempt_id)

    def _company_name_for_task(self, *, task: dict[str, Any], ticker: str, market: str) -> str | None:
        fallback = str(task.get("companyName") or task.get("instrumentName") or "").strip()
        if _is_usable_display_name(ticker=ticker, market=market, value=fallback):
            return fallback
        resolver = self._company_name_resolver
        if resolver is None:
            return None
        try:
            identity = resolve_instrument_identity(ticker, market_hint=market)
            names = resolver(market=identity.profile, symbol_ids=(identity.ticker,))
        except (InstrumentResolveError, RuntimeError, ValueError):
            return None
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning(
                "company name resolver failed market=%s ticker=%s error=%s",
                market,
                ticker,
                exc,
                exc_info=True,
            )
            return None
        name = str(names.get(identity.ticker) or "").strip()
        return name if _is_usable_display_name(ticker=identity.ticker, market=identity.profile, value=name) else None


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def _optional_task_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _is_usable_display_name(*, ticker: str, market: str, value: str) -> bool:
    name = value.strip()
    if not name:
        return False
    if name.upper() != ticker.upper():
        return True
    try:
        identity = resolve_instrument_identity(ticker, market_hint=market)
    except Exception:  # noqa: BLE001
        return False
    return identity.profile == "CRYPTO" and bool(identity.provider_symbols.crypto_base_symbol)


def _settings_from_task(settings: dict[str, Any]) -> ReportWorkflowSettings:
    return ReportWorkflowSettings(
        max_debate_rounds=int(settings["maxDebateRounds"]),
        max_risk_discuss_rounds=int(settings["maxRiskDiscussRounds"]),
        frontline_execution_mode=str(settings["frontlineExecutionMode"]),
        default_profile=str(settings.get("defaultProfile") or "CN_A"),
        default_market=str(settings.get("defaultMarket") or "CN_A"),
        default_currency=str(settings.get("defaultCurrency") or "CNY"),
        default_currency_symbol=str(settings.get("defaultCurrencySymbol") or "\u00a5"),
    )
