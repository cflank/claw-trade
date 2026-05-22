from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime

from claw_trade.ui_contracts.enums import ChatContextKind


@dataclass(frozen=True)
class ChatContext:
    id: str
    kind: ChatContextKind
    title: str
    active_task_id: str | None = None
    active_report_id: str | None = None
    locked_workflow_run_id: str | None = None
    created_at: str = ""
    updated_at: str = ""


def create_normal_chat_context(context_id: str = "ctx-normal") -> ChatContext:
    now = _now_iso()
    return ChatContext(
        id=context_id,
        kind=ChatContextKind.NORMAL_CHAT,
        title="普通聊天",
        created_at=now,
        updated_at=now,
    )


def switch_chat_context(
    context: ChatContext,
    *,
    kind: ChatContextKind,
    title: str | None = None,
    active_task_id: str | None = None,
    active_report_id: str | None = None,
    locked_workflow_run_id: str | None = None,
) -> ChatContext:
    next_title = title or _default_title(kind, active_task_id=active_task_id)
    return replace(
        context,
        kind=kind,
        title=next_title,
        active_task_id=active_task_id if kind == ChatContextKind.TASK_FOLLOWING else None,
        active_report_id=active_report_id if kind == ChatContextKind.REPORT_READING else None,
        locked_workflow_run_id=locked_workflow_run_id if kind == ChatContextKind.TASK_FOLLOWING else None,
        updated_at=_now_iso(),
    )


def to_chat_context_for_user_payload(context: ChatContext, *, workflow_running: bool = False) -> dict[str, object]:
    _ = workflow_running
    payload = {
        "contextId": context.id,
        "kind": context.kind.value,
        "title": context.title,
    }
    if context.active_task_id:
        payload["activeTaskId"] = context.active_task_id
    if context.active_report_id:
        payload["activeReportId"] = context.active_report_id
    return payload


def _default_title(kind: ChatContextKind, *, active_task_id: str | None) -> str:
    if kind == ChatContextKind.NORMAL_CHAT:
        return "普通聊天"
    if kind == ChatContextKind.INTENT_CONFIRMING:
        return "确认操作"
    if kind == ChatContextKind.TASK_FOLLOWING:
        suffix = active_task_id or "任务"
        return f"任务跟进 {suffix}"
    if kind == ChatContextKind.REPORT_READING:
        return "报告阅读"
    return "聊天"


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()
