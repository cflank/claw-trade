from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, cast

from claw_trade.ui_backend.report_repository import UiProductError
from claw_trade.ui_backend.worker_chat_catalog import require_allowed_worker

WorkerChatMode = Literal["generic_worker_chat", "report_worker_chat"]


@dataclass(frozen=True)
class WorkerChatRequest:
    request_id: str
    mode: WorkerChatMode
    worker_id: str
    text: str
    conversation_id: str
    report_id: str | None = None

    @classmethod
    def from_api_payload(cls, payload: Mapping[str, object]) -> WorkerChatRequest:
        request_id = _required_string(payload, "requestId", "请求缺少 requestId。")
        mode = _required_mode(payload)
        worker_id = _required_string(payload, "workerId", "请选择要聊天的角色。")
        require_allowed_worker(worker_id)
        text = _required_string(payload, "text", "请输入要发送的内容。")
        conversation_id = _required_string(payload, "conversationId", "请求缺少 conversationId。")
        report_id = _optional_string(payload, "reportId")
        if mode == "generic_worker_chat" and report_id:
            raise UiProductError("INVALID_INPUT", "普通 worker 聊天不能绑定报告。")
        return cls(
            request_id=request_id,
            mode=mode,
            worker_id=worker_id,
            text=text,
            conversation_id=conversation_id,
            report_id=report_id,
        )


@dataclass(frozen=True)
class WorkerChatReplyForUser:
    kind: Literal["worker_chat_reply"]
    workerDisplayName: str
    text: str
    mode: WorkerChatMode

    def to_api_payload(self) -> dict[str, str]:
        return {
            "kind": self.kind,
            "workerDisplayName": self.workerDisplayName,
            "text": self.text,
            "mode": self.mode,
        }

    @classmethod
    def from_controller_reply(cls, reply: object) -> WorkerChatReplyForUser:
        mode = _require_reply_mode(_reply_value(reply, "mode"))
        return cls(
            kind="worker_chat_reply",
            workerDisplayName=str(_reply_value(reply, "workerDisplayName") or ""),
            text=str(_reply_value(reply, "text") or ""),
            mode=mode,
        )


def _required_string(payload: Mapping[str, object], key: str, user_message: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise UiProductError("INVALID_INPUT", user_message)
    return value.strip()


def _optional_string(payload: Mapping[str, object], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise UiProductError("INVALID_INPUT", f"{key} 格式不正确。")
    normalized = value.strip()
    return normalized or None


def _required_mode(payload: Mapping[str, object]) -> WorkerChatMode:
    value = payload.get("mode")
    if value in {"generic_worker_chat", "report_worker_chat"}:
        return cast(WorkerChatMode, value)
    raise UiProductError("INVALID_INPUT", "worker 聊天模式无效。")


def _require_reply_mode(value: object) -> WorkerChatMode:
    if value in {"generic_worker_chat", "report_worker_chat"}:
        return cast(WorkerChatMode, value)
    raise UiProductError("INVALID_INPUT", "worker 聊天模式无效。")


def _reply_value(reply: object, key: str) -> object:
    if isinstance(reply, Mapping):
        return reply.get(key)
    return getattr(reply, key)
