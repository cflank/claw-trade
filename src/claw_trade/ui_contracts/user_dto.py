from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
from typing import Any

from claw_trade.ui_contracts.enums import (
    ChatContextKind,
    IntentKind,
    MarketProfile,
    ReportTaskStatus,
)


@dataclass(frozen=True)
class NotificationForUser:
    channel: str
    enabled: bool


@dataclass(frozen=True)
class ChatMessageForUser:
    message_id: str
    context_kind: ChatContextKind
    actor: str
    kind: str
    text: str
    card_id: str | None
    report_id: str | None
    task_id: str | None
    created_at: str


@dataclass(frozen=True)
class ChatContextForUser:
    context_id: str
    kind: ChatContextKind
    title: str
    active_task_id: str | None
    active_report_id: str | None


@dataclass(frozen=True)
class IntentDraftForUser:
    draft_id: str
    kind: IntentKind
    instrument_code: str
    instrument_name: str | None
    market: MarketProfile
    report_date_range: Mapping[str, Any] | None
    schedule: Mapping[str, Any] | None
    price_condition: Mapping[str, Any] | None
    notification: NotificationForUser
    status: str


@dataclass(frozen=True)
class UserFacingFailureForUser:
    code: str
    user_message: str
    severity: str


@dataclass(frozen=True)
class ReportTaskForUser:
    task_id: str
    source: str
    status: ReportTaskStatus
    status_label: str
    instrument_code: str
    instrument_name: str | None
    market: MarketProfile
    company_name: str
    currency_symbol: str
    start_date: str
    end_date: str
    current_date: str
    queue_position: int | None
    progress: Mapping[str, Any] | None
    report_id: str | None
    failure: Mapping[str, Any] | None
    created_at: str
    started_at: str | None
    finished_at: str | None


@dataclass(frozen=True)
class ReportQueueSnapshotForUser:
    running_task: ReportTaskForUser | None
    queued_tasks: tuple[ReportTaskForUser, ...]
    last_terminal_task: ReportTaskForUser | None
    queue_limit: int
    queued_count: int
    is_full: bool


@dataclass(frozen=True)
class ScheduledReportForUser:
    scheduledReportId: str
    instrumentCode: str
    instrumentName: str | None
    market: MarketProfile
    frequency: str
    timeOfDay: str
    weekday: int | None
    notification: NotificationForUser
    state: str
    nextRunAt: str | None


@dataclass(frozen=True)
class PriceAlertConditionForUser:
    type: str
    operator: str
    value: float
    window: str | None


@dataclass(frozen=True)
class PriceAlertForUser:
    priceAlertId: str
    instrumentCode: str
    instrumentName: str | None
    market: MarketProfile
    condition: PriceAlertConditionForUser
    notification: NotificationForUser
    state: str
    lastCheckedAt: str | None
    triggeredAt: str | None
    lastErrorMessage: str | None


_DTO_FIELD_NAMES: Mapping[type[object], Mapping[str, str]] = {
    NotificationForUser: {
        "channel": "channel",
        "enabled": "enabled",
    },
    ChatMessageForUser: {
        "message_id": "messageId",
        "context_kind": "contextKind",
        "actor": "actor",
        "kind": "kind",
        "text": "text",
        "card_id": "cardId",
        "report_id": "reportId",
        "task_id": "taskId",
        "created_at": "createdAt",
    },
    ChatContextForUser: {
        "context_id": "contextId",
        "kind": "kind",
        "title": "title",
        "active_task_id": "activeTaskId",
        "active_report_id": "activeReportId",
    },
    IntentDraftForUser: {
        "draft_id": "draftId",
        "kind": "kind",
        "instrument_code": "instrumentCode",
        "instrument_name": "instrumentName",
        "market": "market",
        "report_date_range": "reportDateRange",
        "schedule": "schedule",
        "price_condition": "priceCondition",
        "notification": "notification",
        "status": "status",
    },
    UserFacingFailureForUser: {
        "code": "code",
        "user_message": "userMessage",
        "severity": "severity",
    },
    ReportTaskForUser: {
        "task_id": "taskId",
        "source": "source",
        "status": "status",
        "status_label": "statusLabel",
        "instrument_code": "instrumentCode",
        "instrument_name": "instrumentName",
        "market": "market",
        "company_name": "companyName",
        "currency_symbol": "currencySymbol",
        "start_date": "startDate",
        "end_date": "endDate",
        "current_date": "currentDate",
        "queue_position": "queuePosition",
        "progress": "progress",
        "report_id": "reportId",
        "failure": "failure",
        "created_at": "createdAt",
        "started_at": "startedAt",
        "finished_at": "finishedAt",
    },
    ReportQueueSnapshotForUser: {
        "running_task": "runningTask",
        "queued_tasks": "queuedTasks",
        "last_terminal_task": "lastTerminalTask",
        "queue_limit": "queueLimit",
        "queued_count": "queuedCount",
        "is_full": "isFull",
    },
    ScheduledReportForUser: {
        "scheduledReportId": "scheduledReportId",
        "instrumentCode": "instrumentCode",
        "instrumentName": "instrumentName",
        "market": "market",
        "frequency": "frequency",
        "timeOfDay": "timeOfDay",
        "weekday": "weekday",
        "notification": "notification",
        "state": "state",
        "nextRunAt": "nextRunAt",
    },
    PriceAlertConditionForUser: {
        "type": "type",
        "operator": "operator",
        "value": "value",
        "window": "window",
    },
    PriceAlertForUser: {
        "priceAlertId": "priceAlertId",
        "instrumentCode": "instrumentCode",
        "instrumentName": "instrumentName",
        "market": "market",
        "condition": "condition",
        "notification": "notification",
        "state": "state",
        "lastCheckedAt": "lastCheckedAt",
        "triggeredAt": "triggeredAt",
        "lastErrorMessage": "lastErrorMessage",
    },
}

_REPORT_TASK_STATUS_LABELS = {
    ReportTaskStatus.DRAFT: "草稿",
    ReportTaskStatus.CONFIRMED: "已确认",
    ReportTaskStatus.QUEUED: "排队中",
    ReportTaskStatus.RUNNING: "生成中",
    ReportTaskStatus.SAVING_REPORT: "保存报告中",
    ReportTaskStatus.PDF_EXPORTING: "导出 PDF 中",
    ReportTaskStatus.SUCCEEDED: "已完成",
    ReportTaskStatus.FAILED: "失败",
    ReportTaskStatus.CANCELLED: "已取消",
}


def to_chat_message_for_user(source: Mapping[str, Any] | object) -> ChatMessageForUser:
    return ChatMessageForUser(
        message_id=_string(source, "id", alias_keys=("message_id", "messageId")),
        context_kind=_enum_value(
            ChatContextKind,
            _value(source, "contextKind", alias_keys=("context_kind",)),
            field_name="contextKind",
        ),
        actor=_actor(source),
        kind=_string(source, "kind", default="plain"),
        text=_string(source, "text", alias_keys=("content",)),
        card_id=_optional_string(source, "cardId", alias_keys=("card_id",)),
        report_id=_optional_string(source, "reportId", alias_keys=("report_id",)),
        task_id=_optional_string(source, "taskId", alias_keys=("task_id",)),
        created_at=_string(source, "createdAt", alias_keys=("created_at", "timestamp")),
    )


def to_chat_context_for_user(source: Mapping[str, Any] | object) -> ChatContextForUser:
    return ChatContextForUser(
        context_id=_string(source, "id", alias_keys=("context_id", "contextId")),
        kind=_enum_value(ChatContextKind, _value(source, "kind"), field_name="kind"),
        title=_string(source, "title"),
        active_task_id=_optional_string(source, "activeTaskId", alias_keys=("active_task_id",)),
        active_report_id=_optional_string(source, "activeReportId", alias_keys=("active_report_id",)),
    )


def to_intent_draft_for_user(source: Mapping[str, Any] | object) -> IntentDraftForUser:
    notification = _notification(source)
    return IntentDraftForUser(
        draft_id=_string(source, "id", alias_keys=("draft_id", "draftId")),
        kind=_enum_value(IntentKind, _value(source, "kind"), field_name="kind"),
        instrument_code=_string(source, "instrumentCode", alias_keys=("instrument_code",)),
        instrument_name=_optional_string(source, "instrumentName", alias_keys=("instrument_name",)),
        market=_enum_value(MarketProfile, _value(source, "market"), field_name="market"),
        report_date_range=_optional_mapping(source, "reportDateRange", alias_keys=("report_date_range",)),
        schedule=_optional_mapping(source, "schedule"),
        price_condition=_optional_mapping(source, "priceCondition", alias_keys=("price_condition",)),
        notification=notification,
        status=_string(source, "status"),
    )


def to_report_task_for_user(source: Mapping[str, Any] | object) -> ReportTaskForUser:
    status = _enum_value(ReportTaskStatus, _value(source, "status"), field_name="status")
    return ReportTaskForUser(
        task_id=_string(source, "id", alias_keys=("task_id", "taskId")),
        source=_string(source, "source"),
        status=status,
        status_label=_string(source, "statusLabel", alias_keys=("status_label",), default=_status_label(status)),
        instrument_code=_string(source, "instrumentCode", alias_keys=("instrument_code", "ticker")),
        instrument_name=_optional_string(source, "instrumentName", alias_keys=("instrument_name", "company_name")),
        market=_enum_value(MarketProfile, _value(source, "market"), field_name="market"),
        company_name=_string(source, "companyName", alias_keys=("company_name",)),
        currency_symbol=_string(source, "currencySymbol", alias_keys=("currency_symbol",)),
        start_date=_string(source, "startDate", alias_keys=("start_date",)),
        end_date=_string(source, "endDate", alias_keys=("end_date",)),
        current_date=_string(source, "currentDate", alias_keys=("current_date",)),
        queue_position=_optional_int(source, "queuePosition", alias_keys=("queue_position",)),
        progress=_optional_mapping(source, "progress"),
        report_id=_optional_string(source, "reportId", alias_keys=("report_id",)),
        failure=_user_failure(source),
        created_at=_string(source, "createdAt", alias_keys=("created_at",)),
        started_at=_optional_string(source, "startedAt", alias_keys=("started_at",)),
        finished_at=_optional_string(source, "finishedAt", alias_keys=("finished_at",)),
    )


def to_report_queue_snapshot_for_user(source: Mapping[str, Any] | object) -> ReportQueueSnapshotForUser:
    running = _value(source, "runningTask", alias_keys=("running_task",))
    queued = _value(source, "queuedTasks", alias_keys=("queued_tasks",), default=())
    last_terminal = _value(source, "lastTerminalTask", alias_keys=("last_terminal_task",), default=None)
    queued_items = tuple(to_report_task_for_user(item) for item in queued)
    queue_limit = int(_value(source, "queueLimit", alias_keys=("queue_limit", "maxQueueSize", "max_queue_size"), default=10))
    queued_count = int(_value(source, "queuedCount", alias_keys=("queued_count",), default=len(queued_items)))
    return ReportQueueSnapshotForUser(
        running_task=to_report_task_for_user(running) if running else None,
        queued_tasks=queued_items,
        last_terminal_task=to_report_task_for_user(last_terminal) if last_terminal else None,
        queue_limit=queue_limit,
        queued_count=queued_count,
        is_full=bool(_value(source, "isFull", alias_keys=("is_full",), default=queued_count >= queue_limit)),
    )


def to_scheduled_report_for_user(source: Mapping[str, Any] | object) -> ScheduledReportForUser:
    return ScheduledReportForUser(
        scheduledReportId=_string(source, "id", alias_keys=("scheduled_report_id", "scheduledReportId")),
        instrumentCode=_string(source, "instrumentCode", alias_keys=("instrument_code", "ticker")),
        instrumentName=_optional_string(source, "instrumentName", alias_keys=("instrument_name",)),
        market=_enum_value(MarketProfile, _value(source, "market"), field_name="market"),
        frequency=_string(source, "frequency"),
        timeOfDay=_string(source, "timeOfDay", alias_keys=("time_of_day",)),
        weekday=_optional_int(source, "weekday"),
        notification=_notification(source),
        state=_string(source, "state"),
        nextRunAt=_optional_string(source, "nextRunAt", alias_keys=("next_run_at",)),
    )


def to_price_alert_for_user(source: Mapping[str, Any] | object) -> PriceAlertForUser:
    condition = _mapping(source, "condition")
    return PriceAlertForUser(
        priceAlertId=_string(source, "id", alias_keys=("price_alert_id", "priceAlertId")),
        instrumentCode=_string(source, "instrumentCode", alias_keys=("instrument_code", "ticker")),
        instrumentName=_optional_string(source, "instrumentName", alias_keys=("instrument_name",)),
        market=_enum_value(MarketProfile, _value(source, "market"), field_name="market"),
        condition=PriceAlertConditionForUser(
            type=_string(condition, "type"),
            operator=_string(condition, "operator"),
            value=float(_value(condition, "value")),
            window=_optional_string(condition, "window"),
        ),
        notification=_notification(source),
        state=_string(source, "state"),
        lastCheckedAt=_optional_string(source, "lastCheckedAt", alias_keys=("last_checked_at",)),
        triggeredAt=_optional_string(source, "triggeredAt", alias_keys=("triggered_at",)),
        lastErrorMessage=_optional_string(source, "lastErrorMessage", alias_keys=("last_error_message",)),
    )


def to_user_payload(dto: object) -> dict[str, Any]:
    serialized = _serialize(dto)
    if not isinstance(serialized, dict):
        raise TypeError("用户 DTO 必须序列化为 dict")
    return serialized


def toChatMessageForUser(source: Mapping[str, Any] | object) -> ChatMessageForUser:
    return to_chat_message_for_user(source)


def toChatContextForUser(source: Mapping[str, Any] | object) -> ChatContextForUser:
    return to_chat_context_for_user(source)


def toIntentDraftForUser(source: Mapping[str, Any] | object) -> IntentDraftForUser:
    return to_intent_draft_for_user(source)


def toReportTaskForUser(source: Mapping[str, Any] | object) -> ReportTaskForUser:
    return to_report_task_for_user(source)


def toReportQueueSnapshotForUser(source: Mapping[str, Any] | object) -> ReportQueueSnapshotForUser:
    return to_report_queue_snapshot_for_user(source)


def toScheduledReportForUser(source: Mapping[str, Any] | object) -> ScheduledReportForUser:
    return to_scheduled_report_for_user(source)


def toPriceAlertForUser(source: Mapping[str, Any] | object) -> PriceAlertForUser:
    return to_price_alert_for_user(source)


def _serialize(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        name_map = _DTO_FIELD_NAMES.get(type(value))
        if name_map is None:
            raise TypeError(f"未登记用户 DTO 字段映射: {type(value).__name__}")
        result: dict[str, Any] = {}
        for field in fields(value):
            result[name_map[field.name]] = _serialize(getattr(value, field.name))
        return result
    if isinstance(value, Mapping):
        return {str(key): _serialize(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_serialize(item) for item in value]
    return value


def _value(
    source: Mapping[str, Any] | object,
    key: str,
    *,
    alias_keys: tuple[str, ...] = (),
    default: Any = None,
) -> Any:
    keys = (key, *alias_keys)
    if isinstance(source, Mapping):
        for item in keys:
            if item in source:
                return source[item]
        return default
    for item in keys:
        if hasattr(source, item):
            return getattr(source, item)
    return default


def _string(
    source: Mapping[str, Any] | object,
    key: str,
    *,
    alias_keys: tuple[str, ...] = (),
    default: str | None = None,
) -> str:
    value = _value(source, key, alias_keys=alias_keys, default=default)
    if value is None:
        raise ValueError(f"{key} 不能为空")
    text = str(value).strip()
    if not text:
        raise ValueError(f"{key} 不能为空")
    return text


def _optional_string(source: Mapping[str, Any] | object, key: str, *, alias_keys: tuple[str, ...] = ()) -> str | None:
    value = _value(source, key, alias_keys=alias_keys)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(source: Mapping[str, Any] | object, key: str, *, alias_keys: tuple[str, ...] = ()) -> int | None:
    value = _value(source, key, alias_keys=alias_keys)
    if value is None:
        return None
    return int(value)


def _mapping(source: Mapping[str, Any] | object, key: str, *, alias_keys: tuple[str, ...] = ()) -> Mapping[str, Any]:
    value = _value(source, key, alias_keys=alias_keys)
    if not isinstance(value, Mapping):
        raise ValueError(f"{key} 必须是对象")
    return value


def _optional_mapping(
    source: Mapping[str, Any] | object,
    key: str,
    *,
    alias_keys: tuple[str, ...] = (),
) -> Mapping[str, Any] | None:
    value = _value(source, key, alias_keys=alias_keys)
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError(f"{key} 必须是对象")
    return value


def _enum_value(enum_cls: type, raw: Any, *, field_name: str) -> Any:
    if isinstance(raw, enum_cls):
        return raw
    for item in enum_cls:
        if item.value == raw:
            return item
    raise ValueError(f"{field_name} 非法: {raw}")


def _actor(source: Mapping[str, Any] | object) -> str:
    actor = _optional_string(source, "actor")
    if actor:
        return actor
    return _string(source, "role")


def _notification(source: Mapping[str, Any] | object) -> NotificationForUser:
    raw = _value(source, "notification")
    if not isinstance(raw, Mapping):
        raise ValueError("notification 必须是对象")
    return NotificationForUser(
        channel=_string(raw, "channel"),
        enabled=bool(_value(raw, "enabled")),
    )


def _user_failure(source: Mapping[str, Any] | object) -> Mapping[str, Any] | None:
    failure = _optional_mapping(source, "failure")
    if failure is None:
        return None
    return to_user_payload(
        UserFacingFailureForUser(
            code=_string(failure, "code"),
            user_message=_string(failure, "userMessage", alias_keys=("user_message",)),
            severity=_string(failure, "severity", default="error"),
        )
    )


def _status_label(status: ReportTaskStatus) -> str:
    return _REPORT_TASK_STATUS_LABELS[status]
