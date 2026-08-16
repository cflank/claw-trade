from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from typing import Any, Callable, Protocol

from claw_trade.config.profiles import is_profile_approved
from claw_trade.config.report_workflow_settings import ReportWorkflowSettings
from claw_trade.instruments.resolver import resolve_instrument_identity
from claw_trade.ui_backend.intent_recognizer import IntentDraft, WorkflowSettingsSnapshot
from claw_trade.ui_backend.price_alert_service import PriceAlertService, UiServiceError
from claw_trade.ui_backend.report_queue import QueueError, ReportTaskQueue
from claw_trade.ui_backend.scheduler_service import SchedulerService
from claw_trade.ui_contracts.enums import IntentKind, MarketProfile
from claw_trade.workflow.report_request_factory import build_report_run_request, report_display_name

_LOGGER = logging.getLogger("uvicorn.error")


class CompanyNameResolver(Protocol):
    def __call__(self, *, market: str, symbol_ids: Sequence[str]) -> Mapping[str, str]: ...


class ConfirmationController:
    def __init__(
        self,
        queue: ReportTaskQueue,
        *,
        approved_profiles: set[str] | None = None,
        scheduler_service: SchedulerService | None = None,
        price_alert_service: PriceAlertService | None = None,
        report_model_ready_checker: Callable[[], None] | None = None,
        company_name_resolver: CompanyNameResolver | None = None,
        default_price_alert_notification: Callable[[], dict[str, Any] | None] | None = None,
    ) -> None:
        self._queue = queue
        self._approved_profiles = approved_profiles
        self._scheduler_service = scheduler_service or SchedulerService(
            enqueue_report_task=self._enqueue_scheduled_task,
            queue_snapshot_provider=self._queue.get_report_queue_snapshot_for_user,
        )
        self._price_alert_service = price_alert_service or PriceAlertService(
            quote_provider=_missing_price_alert_quote_provider,
        )
        self._report_model_ready_checker = report_model_ready_checker
        self._company_name_resolver = company_name_resolver
        self._company_name_cache: dict[tuple[str, str], str | None] = {}
        self._default_price_alert_notification = default_price_alert_notification
        self._drafts: dict[str, IntentDraft] = {}
        self._idempotency: dict[str, dict[str, Any]] = {}

    def register_draft(self, draft: IntentDraft) -> None:
        self._drafts[draft.draft_id] = draft

    def get_draft(self, draft_id: str) -> IntentDraft | None:
        return self._drafts.get(draft_id)

    def build_confirmation_card(self, draft: IntentDraft) -> dict[str, Any]:
        self.assert_confirmation_card_available(draft)
        if draft.kind == IntentKind.SCHEDULED_SELECTION:
            schedule = draft.schedule or {}
            return {
                "id": f"card-{draft.draft_id}",
                "draftId": draft.draft_id,
                "title": "请确认是否创建定时选股",
                "summaryLines": [
                    f"命令：{draft.instrument_code}",
                    f"市场：{draft.market.value}",
                    f"时间：每天 {schedule.get('timeOfDay', '09:00')}",
                ],
                "instrumentCode": draft.instrument_code,
                "instrumentName": draft.instrument_name,
                "market": draft.market.value,
                "dataSourceSummary": "unknown",
                "actions": ["confirm", "cancel"],
                "status": "active",
                "createdAt": draft.expires_at,
            }
        instrument_name = self._display_company_name_for_draft(draft)
        lines = [f"标的：{draft.instrument_code}", f"名称：{instrument_name}", f"市场：{draft.market.value}"]
        return {
            "id": f"card-{draft.draft_id}",
            "draftId": draft.draft_id,
            "title": f"请确认是否创建{_label_intent_kind(draft.kind)}",
            "summaryLines": lines,
            "instrumentCode": draft.instrument_code,
            "instrumentName": instrument_name,
            "market": draft.market.value,
            "dataSourceSummary": "unknown",
            "actions": ["confirm", "cancel"],
            "status": "active",
            "createdAt": draft.expires_at,
        }

    def assert_confirmation_card_available(self, draft: IntentDraft) -> None:
        if draft.kind != IntentKind.SCHEDULED_SELECTION:
            self._assert_profile_strategy_approved(draft)
        if draft.kind in {IntentKind.REPORT, IntentKind.SCHEDULED_REPORT}:
            self._assert_company_name_available(draft)

    def confirm_intent_draft(
        self,
        *,
        request_id: str,
        draft_id: str,
        decision: str,
        overrides: dict[str, Any] | None = None,
        origin_context_id: str | None = None,
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
        self.assert_confirmation_card_available(frozen)
        if frozen.kind != IntentKind.SCHEDULED_SELECTION:
            self._assert_instrument_market_match(frozen)
        if frozen.kind == IntentKind.REPORT:
            self._assert_report_model_ready()
            task_input = self._build_report_task_input(frozen)
            result = self._queue.enqueue_report_task(
                request_id=request_id,
                task_input=task_input,
                source="manual",
                origin_context_id=origin_context_id,
            )
            payload = {"status": "confirmed", **result}
            self._idempotency[request_id] = payload
            return payload
        if frozen.kind == IntentKind.SCHEDULED_REPORT:
            schedule = frozen.schedule or {}
            instrument_name = self._required_company_name_for_draft(frozen)
            notification = self._scheduled_notification(dict(frozen.notification), origin_context_id)
            payload = {
                "status": "confirmed",
                **self._scheduler_service.create_scheduled_report_for_user(
                    request_id=request_id,
                    instrument_code=frozen.instrument_code,
                    instrument_name=instrument_name,
                    market=frozen.market,
                    frequency=str(schedule.get("frequency", "daily")),
                    time_of_day=str(schedule.get("timeOfDay", "09:00")),
                    weekday=schedule.get("weekday"),
                    notification=notification,
                    workflow_settings=asdict(frozen.workflow_settings),
                ),
            }
            self._idempotency[request_id] = payload
            return payload
        if frozen.kind == IntentKind.SCHEDULED_SELECTION:
            schedule = frozen.schedule or {}
            notification = self._scheduled_notification(dict(frozen.notification), origin_context_id)
            payload = {
                "status": "confirmed",
                "scheduledTaskKind": "selection",
                **self._scheduler_service.create_scheduled_selection_for_user(
                    request_id=request_id,
                    market=frozen.market,
                    frequency=str(schedule.get("frequency", "daily")),
                    time_of_day=str(schedule.get("timeOfDay", "09:00")),
                    notification=notification,
                ),
            }
            self._idempotency[request_id] = payload
            return payload
        if frozen.kind == IntentKind.PRICE_ALERT:
            if not frozen.price_condition:
                raise QueueError("INVALID_INPUT", "invalid_input", "价格提醒条件缺失，请重新输入。")
            notification = self._price_alert_notification(dict(frozen.notification), origin_context_id)
            payload = {
                "status": "confirmed",
                "priceAlert": self._price_alert_service.create_price_alert(
                    request_id=request_id,
                    instrument_code=frozen.instrument_code,
                    instrument_name=frozen.instrument_name,
                    market=frozen.market,
                    condition=dict(frozen.price_condition),
                    notification=notification,
                ),
            }
            self._idempotency[request_id] = payload
            return payload
        raise QueueError("INVALID_INPUT", "invalid_input", "暂不支持的确认类型。")

    def _build_report_task_input(self, draft: IntentDraft) -> dict[str, Any]:
        company_name = self._required_company_name_for_draft(draft)
        request = build_report_run_request(
            ticker=draft.instrument_code,
            company_name=company_name,
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

    def _display_company_name_for_draft(self, draft: IntentDraft) -> str:
        required_name = self._required_company_name_for_draft(draft) if draft.kind in {IntentKind.REPORT, IntentKind.SCHEDULED_REPORT} else None
        if required_name:
            return required_name
        local_name = self._local_company_name_for_draft(draft)
        if local_name:
            return local_name
        return draft.instrument_code

    def _required_company_name_for_draft(self, draft: IntentDraft) -> str:
        name = self._local_company_name_for_draft(draft) or self._company_name_for_draft(draft)
        if name:
            return name
        raise QueueError(
            "INVALID_INPUT",
            "invalid_input",
            f"标的 {draft.instrument_code} 名称解析失败，请检查代码或市场后重新输入。",
        )

    def _local_company_name_for_draft(self, draft: IntentDraft) -> str | None:
        existing = (draft.instrument_name or "").strip()
        if self._is_usable_display_name(draft, existing):
            return existing
        fallback = report_display_name(draft.instrument_code, draft.market.value).strip()
        if self._is_usable_display_name(draft, fallback):
            return fallback
        return None

    def _company_name_for_draft(self, draft: IntentDraft) -> str | None:
        key = (draft.market.value, draft.instrument_code)
        if key in self._company_name_cache:
            return self._company_name_cache[key]
        resolver = self._company_name_resolver
        if resolver is None:
            self._company_name_cache[key] = None
            return None
        try:
            names = resolver(market=draft.market.value, symbol_ids=(draft.instrument_code,))
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning(
                "company name resolver failed market=%s symbol=%s error=%s",
                draft.market.value,
                draft.instrument_code,
                exc,
                exc_info=True,
            )
            self._company_name_cache[key] = None
            return None
        name = str(names.get(draft.instrument_code) or "").strip()
        if not self._is_usable_display_name(draft, name):
            name = None
        self._company_name_cache[key] = name
        return name

    @staticmethod
    def _notification_for_origin_context(notification: dict[str, Any], origin_context_id: str | None) -> dict[str, Any]:
        parts = str(origin_context_id or "").strip().split(":", 2)
        if len(parts) != 3:
            return notification
        channel_kind, account_id, sender_id = (part.strip() for part in parts)
        if not channel_kind or not sender_id:
            return notification
        return {
            **notification,
            "channel": channel_kind,
            "enabled": True,
            "target": sender_id,
            "accountId": account_id or None,
        }

    def _price_alert_notification(self, notification: dict[str, Any], origin_context_id: str | None) -> dict[str, Any]:
        routed = self._notification_for_origin_context(notification, origin_context_id)
        if str(routed.get("channel") or "") != "in_app" or routed.get("target"):
            return routed
        if self._default_price_alert_notification is None:
            return routed
        default = self._default_price_alert_notification()
        return routed if default is None else {**routed, **default, "enabled": True}

    @staticmethod
    def _scheduled_notification(notification: dict[str, Any], origin_context_id: str | None) -> dict[str, Any]:
        parts = str(origin_context_id or "").strip().split(":", 2)
        if len(parts) == 3 and parts[0].strip() == "wechat_clawbot" and parts[2].strip():
            return {
                "channel": "wechat_clawbot",
                "enabled": True,
                "recipientKey": "wechat_primary",
            }
        return notification

    def _assert_company_name_available(self, draft: IntentDraft) -> None:
        self._required_company_name_for_draft(draft)

    @staticmethod
    def _is_usable_display_name(draft: IntentDraft, value: str) -> bool:
        name = value.strip()
        if not name:
            return False
        if name.upper() != draft.instrument_code.upper():
            return True
        try:
            identity = resolve_instrument_identity(draft.instrument_code, market_hint=draft.market.value)
        except Exception:  # noqa: BLE001
            return False
        return identity.profile == "CRYPTO" and bool(identity.provider_symbols.crypto_base_symbol)

    def _assert_profile_strategy_approved(self, draft: IntentDraft) -> None:
        profile = draft.workflow_settings.defaultProfile or draft.market.value
        if profile not in {"HK", "CRYPTO"}:
            return
        approved = profile in self._approved_profiles if self._approved_profiles is not None else is_profile_approved(profile)
        if not approved:
            if profile == "HK":
                message = "港股报告暂未启用，请先配置 HK 报告策略。"
            elif profile == "CRYPTO":
                message = "加密报告暂未启用，请先配置 CRYPTO 报告策略。"
            else:
                message = "当前市场策略尚未批准。"
            raise QueueError("PROFILE_STRATEGY_UNAPPROVED", "profile_strategy_unapproved", message)

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

    def _assert_report_model_ready(self) -> None:
        if self._report_model_ready_checker is None:
            return
        try:
            self._report_model_ready_checker()
        except Exception as exc:
            raise QueueError("REPORT_MODEL_NOT_READY", "report_model_not_ready", str(exc) or "报告模型未就绪。") from exc

    @staticmethod
    def _apply_overrides(draft: IntentDraft, overrides: dict[str, Any]) -> IntentDraft:
        if not overrides:
            return draft
        raw = asdict(draft)
        market_value = draft.market
        market_overridden = "market" in overrides and str(overrides["market"]).strip()
        if "market" in overrides and str(overrides["market"]).strip():
            market_value = MarketProfile(str(overrides["market"]).strip().upper())
            raw["market"] = market_value
        if "instrumentCode" in overrides and str(overrides["instrumentCode"]).strip():
            code = str(overrides["instrumentCode"]).strip().upper()
            identity = resolve_instrument_identity(code, market_hint=market_value.value if market_overridden else None)
            market_value = MarketProfile(identity.profile)
            raw["instrument_code"] = identity.ticker
            raw["instrument_name"] = report_display_name(identity.ticker, identity.profile)
            raw["market"] = market_value
        return IntentDraft(
            draft_id=raw["draft_id"],
            kind=raw["kind"],
            summary=raw["summary"],
            source_message_id=raw["source_message_id"],
            instrument_code=raw["instrument_code"],
            instrument_name=raw["instrument_name"],
            market=raw["market"] if isinstance(raw["market"], MarketProfile) else MarketProfile(str(raw["market"])),
            notification=raw["notification"],
            workflow_settings=_snapshot_for_market(draft.workflow_settings, market_value),
            dedupe_key=raw["dedupe_key"],
            schedule=raw["schedule"],
            price_condition=raw["price_condition"],
            status=raw["status"],
            expires_at=raw["expires_at"],
        )

    @staticmethod
    def _assert_instrument_market_match(draft: IntentDraft) -> None:
        identity = resolve_instrument_identity(draft.instrument_code)
        detected_market = MarketProfile(identity.profile)
        if detected_market == draft.market:
            return
        raise QueueError(
            "INVALID_INPUT",
            "invalid_input",
            f"标的 {draft.instrument_code} 与所选市场不匹配，请改用 {detected_market.value} 或更换标的。",
        )


def _label_intent_kind(kind: IntentKind) -> str:
    mapping = {
        IntentKind.REPORT: "完整报告",
        IntentKind.SCHEDULED_REPORT: "定时报告",
        IntentKind.SCHEDULED_SELECTION: "定时选股",
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


def _snapshot_for_market(snapshot: WorkflowSettingsSnapshot, market: MarketProfile) -> WorkflowSettingsSnapshot:
    currency_by_market = {
        MarketProfile.CN_A: ("CNY", "\u00a5"),
        MarketProfile.US: ("USD", "$"),
        MarketProfile.HK: ("HKD", "HK$"),
        MarketProfile.CRYPTO: ("USDT", "USDT"),
    }
    currency, symbol = currency_by_market[market]
    return WorkflowSettingsSnapshot(
        maxDebateRounds=snapshot.maxDebateRounds,
        maxRiskDiscussRounds=snapshot.maxRiskDiscussRounds,
        frontlineExecutionMode=snapshot.frontlineExecutionMode,
        defaultProfile=market.value,
        defaultMarket=market.value,
        defaultCurrency=currency,
        defaultCurrencySymbol=symbol,
    )


def _missing_price_alert_quote_provider(_instrument: str, _market: MarketProfile) -> dict[str, Any]:
    raise UiServiceError("DATASOURCE_TEST_FAILED", "价格提醒数据源未配置，不能用默认价格完成检查。")
