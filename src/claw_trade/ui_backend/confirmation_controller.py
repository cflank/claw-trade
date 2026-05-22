from __future__ import annotations

from dataclasses import asdict
from typing import Any

from claw_trade.config.report_workflow_settings import ReportWorkflowSettings
from claw_trade.config.profiles import is_profile_approved
from claw_trade.ui_backend.intent_recognizer import IntentDraft
from claw_trade.ui_backend.price_alert_service import PriceAlertService
from claw_trade.ui_backend.report_queue import QueueError, ReportTaskQueue
from claw_trade.ui_backend.scheduler_service import SchedulerService
from claw_trade.ui_contracts.enums import IntentKind, MarketProfile
from claw_trade.workflow.report_request_factory import build_report_run_request


class ConfirmationController:
    def __init__(
        self,
        queue: ReportTaskQueue,
        *,
        approved_profiles: set[str] | None = None,
        scheduler_service: SchedulerService | None = None,
        price_alert_service: PriceAlertService | None = None,
    ) -> None:
        self._queue = queue
        self._approved_profiles = approved_profiles
        self._scheduler_service = scheduler_service or SchedulerService(
            enqueue_report_task=self._enqueue_scheduled_task,
            queue_snapshot_provider=self._queue.get_report_queue_snapshot_for_user,
        )
        self._price_alert_service = price_alert_service or PriceAlertService(
            quote_provider=lambda _instrument, _market: {"current_price": 0.0, "percent_change": 0.0},
        )
        self._drafts: dict[str, IntentDraft] = {}
        self._idempotency: dict[str, dict[str, Any]] = {}

    def register_draft(self, draft: IntentDraft) -> None:
        self._drafts[draft.draft_id] = draft

    def get_draft(self, draft_id: str) -> IntentDraft | None:
        return self._drafts.get(draft_id)

    def build_confirmation_card(self, draft: IntentDraft) -> dict[str, Any]:
        lines = [f"类型：{_label_intent_kind(draft.kind)}", f"标的：{draft.instrument_code}", f"市场：{draft.market.value}"]
        if draft.schedule:
            lines.append(f"频率：{draft.schedule['frequency']}")
        if draft.price_condition:
            lines.append(
                "条件："
                + f"{draft.price_condition['operator']} {draft.price_condition['value']}"
            )
        lines.append("通知：应用内")
        return {
            "id": f"card-{draft.draft_id}",
            "draftId": draft.draft_id,
            "title": f"请确认是否创建{_label_intent_kind(draft.kind)}",
            "summaryLines": lines,
            "dataSourceSummary": "unknown",
            "actions": ["confirm", "cancel"],
            "status": "active",
            "createdAt": draft.expires_at,
        }

    def confirm_intent_draft(
        self,
        *,
        request_id: str,
        draft_id: str,
        decision: str,
        overrides: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if request_id in self._idempotency:
            return self._idempotency[request_id]
        draft = self._drafts.get(draft_id)
        if draft is None:
            raise QueueError("DRAFT_EXPIRED", "invalid_input", "确认草稿已过期，请重新提交。")
        if decision == "cancel":
            result = {"status": "cancelled"}
            self._idempotency[request_id] = result
            return result
        frozen = self._apply_overrides(draft, overrides or {})
        self._assert_profile_strategy_approved(frozen)
        if frozen.kind == IntentKind.REPORT:
            task_input = self._build_report_task_input(frozen)
            result = self._queue.enqueue_report_task(request_id=request_id, task_input=task_input, source="manual")
            payload = {"status": "confirmed", **result}
            self._idempotency[request_id] = payload
            return payload
        if frozen.kind == IntentKind.SCHEDULED_REPORT:
            schedule = frozen.schedule or {}
            payload = {
                "status": "confirmed",
                "scheduledReport": self._scheduler_service.create_scheduled_report(
                    request_id=request_id,
                    instrument_code=frozen.instrument_code,
                    instrument_name=frozen.instrument_name,
                    market=frozen.market,
                    frequency=str(schedule.get("frequency", "daily")),
                    time_of_day=str(schedule.get("timeOfDay", "09:00")),
                    weekday=schedule.get("weekday"),
                    notification=dict(frozen.notification),
                    workflow_settings=asdict(frozen.workflow_settings),
                ),
            }
            self._idempotency[request_id] = payload
            return payload
        if frozen.kind == IntentKind.PRICE_ALERT:
            if not frozen.price_condition:
                raise QueueError("INVALID_INPUT", "invalid_input", "价格提醒条件缺失，请重新输入。")
            payload = {
                "status": "confirmed",
                "priceAlert": self._price_alert_service.create_price_alert(
                    request_id=request_id,
                    instrument_code=frozen.instrument_code,
                    instrument_name=frozen.instrument_name,
                    market=frozen.market,
                    condition=dict(frozen.price_condition),
                    notification=dict(frozen.notification),
                ),
            }
            self._idempotency[request_id] = payload
            return payload
        raise QueueError("INVALID_INPUT", "invalid_input", "暂不支持的确认类型。")

    def _build_report_task_input(self, draft: IntentDraft) -> dict[str, Any]:
        request = build_report_run_request(
            ticker=draft.instrument_code,
            company_name=draft.instrument_name,
            market=draft.market.value,
            current_date=draft.expires_at[:10] if draft.expires_at else None,
            settings=_settings_from_snapshot(draft),
        )
        return {
            "instrumentCode": request.ticker,
            "instrumentName": request.company_name,
            "market": request.market,
            "companyName": request.company_name,
            "currencySymbol": request.currency_symbol,
            "startDate": request.start_date,
            "endDate": request.end_date,
            "currentDate": request.current_date,
            "workflowSettings": _workflow_settings_from_request(request),
        }

    def _assert_profile_strategy_approved(self, draft: IntentDraft) -> None:
        profile = draft.workflow_settings.defaultProfile or draft.market.value
        if profile not in {"HK", "CRYPTO"}:
            return
        approved = profile in self._approved_profiles if self._approved_profiles is not None else is_profile_approved(profile)
        if not approved:
            raise QueueError("PROFILE_STRATEGY_UNAPPROVED", "profile_strategy_unapproved", "当前市场策略尚未批准。")

    def _enqueue_scheduled_task(self, task_input: dict[str, Any], request_id: str) -> dict[str, Any]:
        result = self._queue.enqueue_report_task(
            request_id=request_id,
            task_input=task_input,
            source="scheduled",
        )
        task = result.get("task")
        if isinstance(task, dict):
            return task
        return result

    @staticmethod
    def _apply_overrides(draft: IntentDraft, overrides: dict[str, Any]) -> IntentDraft:
        if not overrides:
            return draft
        raw = asdict(draft)
        if "market" in overrides and str(overrides["market"]).strip():
            raw["market"] = str(overrides["market"]).strip()
        if "instrumentCode" in overrides and str(overrides["instrumentCode"]).strip():
            raw["instrument_code"] = str(overrides["instrumentCode"]).strip().upper()
        return IntentDraft(
            draft_id=raw["draft_id"],
            kind=raw["kind"],
            summary=raw["summary"],
            source_message_id=raw["source_message_id"],
            instrument_code=raw["instrument_code"],
            instrument_name=raw["instrument_name"],
            market=raw["market"] if isinstance(raw["market"], MarketProfile) else MarketProfile(str(raw["market"])),
            notification=raw["notification"],
            workflow_settings=draft.workflow_settings,
            dedupe_key=raw["dedupe_key"],
            schedule=raw["schedule"],
            price_condition=raw["price_condition"],
            status=raw["status"],
            expires_at=raw["expires_at"],
        )


def _label_intent_kind(kind: IntentKind) -> str:
    mapping = {
        IntentKind.REPORT: "完整报告",
        IntentKind.SCHEDULED_REPORT: "定时报告",
        IntentKind.PRICE_ALERT: "价格提醒",
    }
    return mapping[kind]


def _settings_from_snapshot(draft: IntentDraft) -> ReportWorkflowSettings:
    snapshot = draft.workflow_settings
    return ReportWorkflowSettings(
        max_debate_rounds=snapshot.maxDebateRounds,
        max_risk_discuss_rounds=snapshot.maxRiskDiscussRounds,
        frontline_execution_mode=snapshot.frontlineExecutionMode,
        default_profile=snapshot.defaultProfile,
        default_market=snapshot.defaultMarket,
        default_currency=snapshot.defaultCurrency,
        default_currency_symbol=snapshot.defaultCurrencySymbol,
    )


def _workflow_settings_from_request(request: Any) -> dict[str, Any]:
    return {
        "maxDebateRounds": request.max_debate_rounds,
        "maxRiskDiscussRounds": request.max_risk_discuss_rounds,
        "frontlineExecutionMode": request.frontline_execution_mode,
        "defaultProfile": request.profile,
        "defaultMarket": request.market,
        "defaultCurrency": request.currency,
        "defaultCurrencySymbol": request.currency_symbol,
    }
