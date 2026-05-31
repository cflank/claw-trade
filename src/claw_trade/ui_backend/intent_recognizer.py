from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from claw_trade.config.report_workflow_settings import ReportWorkflowSettings
from claw_trade.instruments.resolver import resolve_instrument_identity
from claw_trade.ui_contracts.enums import IntentKind, MarketProfile
from claw_trade.ui_contracts.scope_guard import assert_schedule_frequency_supported
from claw_trade.workflow.report_request_factory import report_display_name


@dataclass(frozen=True)
class WorkflowSettingsSnapshot:
    maxDebateRounds: int
    maxRiskDiscussRounds: int
    frontlineExecutionMode: str
    defaultProfile: str
    defaultMarket: str
    defaultCurrency: str
    defaultCurrencySymbol: str


@dataclass(frozen=True)
class IntentDraft:
    draft_id: str
    kind: IntentKind
    summary: str
    source_message_id: str
    instrument_code: str
    instrument_name: str | None
    market: MarketProfile
    notification: dict[str, object]
    workflow_settings: WorkflowSettingsSnapshot
    dedupe_key: str
    schedule: dict[str, object] | None = None
    price_condition: dict[str, object] | None = None
    status: str = "draft"
    expires_at: str | None = None


class IntentRecognizer:
    def __init__(self, *, now: callable | None = None) -> None:
        self._now = now or _now_iso
        self._seq = 0

    @staticmethod
    def looks_like_report_intent(text: str) -> bool:
        return _looks_like_report_intent(text.strip().lower())

    def classify_user_intent(
        self,
        *,
        text: str,
        source_message_id: str,
        settings: ReportWorkflowSettings,
    ) -> IntentDraft | None:
        normalized = text.strip()
        if not normalized:
            raise ValueError("invalid_input")
        lowered = normalized.lower()
        instrument = _resolve_instrument(normalized)
        snapshot = _snapshot_from_settings(settings)
        if _looks_like_report_intent(lowered):
            if _looks_like_hourly(lowered):
                assert_schedule_frequency_supported("hourly")
            if instrument is None:
                return None
            schedule = _parse_schedule(lowered)
            if schedule is not None:
                return self._build_draft(
                    kind=IntentKind.SCHEDULED_REPORT,
                    summary=f"定时报告：{instrument['instrumentCode']} {schedule['frequency']}",
                    source_message_id=source_message_id,
                    instrument=instrument,
                    snapshot=snapshot,
                    schedule=schedule,
                )
            return self._build_draft(
                kind=IntentKind.REPORT,
                summary=f"生成报告：{instrument['instrumentCode']}",
                source_message_id=source_message_id,
                instrument=instrument,
                snapshot=snapshot,
            )
        price_condition = _parse_price_condition(lowered)
        if price_condition and instrument:
            return self._build_draft(
                kind=IntentKind.PRICE_ALERT,
                summary=f"价格提醒：{instrument['instrumentCode']}",
                source_message_id=source_message_id,
                instrument=instrument,
                snapshot=snapshot,
                price_condition=price_condition,
            )
        return None

    def create_regenerate_draft_after_completion(
        self,
        *,
        source_message_id: str,
        current_task: dict[str, object],
        extra_requirement: str,
        settings: ReportWorkflowSettings,
    ) -> IntentDraft:
        instrument = {
            "instrumentCode": str(current_task.get("instrumentCode") or ""),
            "instrumentName": current_task.get("instrumentName"),
            "market": str(current_task.get("market") or MarketProfile.CN_A.value),
        }
        if not instrument["instrumentCode"]:
            instrument["instrumentCode"] = "UNKNOWN"
        snapshot = _snapshot_from_settings(settings)
        return self._build_draft(
            kind=IntentKind.REPORT,
            summary=f"完成后重做：{instrument['instrumentCode']}（新增要求）",
            source_message_id=source_message_id,
            instrument=instrument,
            snapshot=snapshot,
            dedupe_suffix=extra_requirement.strip(),
        )

    @staticmethod
    def looks_like_task_mutation(text: str) -> bool:
        lowered = text.strip().lower()
        markers = ("改成", "改为", "增加", "加上", "去掉", "换成", "再补充", "adjust", "change", "update")
        return any(marker in lowered for marker in markers)

    @staticmethod
    def looks_like_progress_question(text: str) -> bool:
        lowered = text.strip().lower()
        markers = ("进度", "到哪", "好了没", "状态", "progress")
        return any(marker in lowered for marker in markers)

    def _build_draft(
        self,
        *,
        kind: IntentKind,
        summary: str,
        source_message_id: str,
        instrument: dict[str, object],
        snapshot: WorkflowSettingsSnapshot,
        schedule: dict[str, object] | None = None,
        price_condition: dict[str, object] | None = None,
        dedupe_suffix: str = "",
    ) -> IntentDraft:
        self._seq += 1
        draft_id = f"draft-{self._seq}"
        instrument_code = str(instrument["instrumentCode"])
        market = MarketProfile(str(instrument["market"]))
        snapshot = _snapshot_for_market(snapshot, market)
        dedupe_key = f"{kind.value}:{instrument_code}:{market.value}:{dedupe_suffix}".rstrip(":")
        return IntentDraft(
            draft_id=draft_id,
            kind=kind,
            summary=summary,
            source_message_id=source_message_id,
            instrument_code=instrument_code,
            instrument_name=instrument.get("instrumentName"),
            market=market,
            notification={"channel": "in_app", "enabled": True},
            workflow_settings=snapshot,
            dedupe_key=dedupe_key,
            schedule=schedule,
            price_condition=price_condition,
            expires_at=(datetime.now(tz=UTC) + timedelta(minutes=15)).isoformat(),
        )


def _looks_like_report_intent(lowered: str) -> bool:
    return "/report" in lowered or "报告" in lowered or "生成" in lowered and "报" in lowered


def _looks_like_hourly(lowered: str) -> bool:
    return "每小时" in lowered or "hourly" in lowered or "every hour" in lowered


def _parse_schedule(lowered: str) -> dict[str, object] | None:
    if "每天" in lowered or "daily" in lowered:
        return {"frequency": "daily", "timeOfDay": _parse_time_of_day(lowered) or "09:00", "weekday": None}
    if "每周" in lowered or "weekly" in lowered:
        weekday = 1
        if "周五" in lowered or "friday" in lowered:
            weekday = 5
        return {"frequency": "weekly", "timeOfDay": _parse_time_of_day(lowered) or "09:00", "weekday": weekday}
    return None


def _parse_time_of_day(lowered: str) -> str | None:
    matched = re.search(r"(?P<hour>\d{1,2}):(?P<minute>\d{2})", lowered)
    if not matched:
        return None
    hour = int(matched.group("hour"))
    minute = int(matched.group("minute"))
    if hour > 23 or minute > 59:
        return None
    return f"{hour:02d}:{minute:02d}"


def _parse_price_condition(lowered: str) -> dict[str, object] | None:
    if "提醒" not in lowered:
        return None
    matched = re.search(r"(\d+(?:\.\d+)?)", lowered)
    if not matched:
        return None
    value = float(matched.group(1))
    if "高于" in lowered or "above" in lowered:
        return {"type": "price_threshold", "operator": "above", "value": value, "window": "24h"}
    if "低于" in lowered or "below" in lowered:
        return {"type": "price_threshold", "operator": "below", "value": value, "window": "24h"}
    if "涨" in lowered:
        return {"type": "percent_change", "operator": "up_by", "value": value, "window": "24h"}
    if "跌" in lowered:
        return {"type": "percent_change", "operator": "down_by", "value": value, "window": "24h"}
    return None


def _resolve_instrument(text: str) -> dict[str, object] | None:
    matched_report = re.search(r"/report\s+([A-Za-z0-9._/-]+)", text, re.IGNORECASE)
    if matched_report:
        code = matched_report.group(1).upper()
        return _instrument_from_code(code)
    matched_cn = re.search(r"报告\s*([A-Za-z0-9._/-]+)", text, re.IGNORECASE)
    if matched_cn:
        code = matched_cn.group(1).upper()
        return _instrument_from_code(code)
    explicit = {
        "比特币": ("BTC", "Bitcoin", MarketProfile.CRYPTO),
        "btc": ("BTC", "Bitcoin", MarketProfile.CRYPTO),
        "以太坊": ("ETH", "Ethereum", MarketProfile.CRYPTO),
        "特斯拉": ("TSLA", "Tesla", MarketProfile.US),
        "苹果": ("AAPL", "Apple", MarketProfile.US),
    }
    lowered = text.lower()
    for marker, (code, name, market) in explicit.items():
        if marker in lowered:
            return {"instrumentCode": code, "instrumentName": name, "market": market.value}
    return None


def _instrument_from_code(code: str) -> dict[str, object]:
    identity = resolve_instrument_identity(code)
    return {
        "instrumentCode": identity.ticker,
        "instrumentName": report_display_name(identity.ticker, identity.profile),
        "market": identity.profile,
    }


def _snapshot_from_settings(settings: ReportWorkflowSettings) -> WorkflowSettingsSnapshot:
    return WorkflowSettingsSnapshot(
        maxDebateRounds=settings.max_debate_rounds,
        maxRiskDiscussRounds=settings.max_risk_discuss_rounds,
        frontlineExecutionMode=settings.frontline_execution_mode,
        defaultProfile=settings.default_profile,
        defaultMarket=settings.default_market,
        defaultCurrency=settings.default_currency,
        defaultCurrencySymbol=settings.default_currency_symbol,
    )


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


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()
