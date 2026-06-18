from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast

from claw_trade.ui_backend.report_repository import ReportRepository, UiProductError
from claw_trade.ui_backend.report_worker_chat_context import (
    ReportWorkerChatContextResolver,
    sanitize_report_worker_chat_visible_text,
)
from claw_trade.ui_backend.worker_chat_catalog import require_allowed_worker
from claw_trade.ui_backend.worker_chat_openclaw import OpenClawWorkerChatClient
from claw_trade.ui_backend.worker_chat_store import (
    WorkerChatTurn,
    WorkerChatTurnStore,
    compute_worker_chat_request_fingerprint,
)

WorkerChatMode = Literal["generic_worker_chat", "report_worker_chat"]


@dataclass(frozen=True)
class WorkerChatReplyForUser:
    kind: str
    workerDisplayName: str
    text: str
    mode: WorkerChatMode


class WorkerChatController:
    def __init__(
        self,
        repository: ReportRepository,
        openclaw: OpenClawWorkerChatClient,
        turn_store: WorkerChatTurnStore | None = None,
        report_context_resolver: ReportWorkerChatContextResolver | None = None,
    ) -> None:
        self._repository = repository
        self._openclaw = openclaw
        self._turn_store = turn_store or WorkerChatTurnStore()
        self._report_context_resolver = report_context_resolver or ReportWorkerChatContextResolver(repository)

    def send_worker_chat(
        self,
        *,
        request_id: str,
        mode: str,
        worker_id: str,
        text: str,
        conversation_id: str,
        report_id: str | None,
    ) -> WorkerChatReplyForUser:
        normalized_mode = _require_mode(mode)
        message = text.strip()
        if not message:
            raise UiProductError("INVALID_INPUT", "请输入要发送的内容。")
        normalized_worker_id = worker_id.strip()
        if not normalized_worker_id:
            raise UiProductError("INVALID_INPUT", "请选择要聊天的角色。")
        normalized_conversation_id = conversation_id.strip()
        normalized_report_id = report_id.strip() if report_id is not None else None
        if normalized_report_id == "":
            normalized_report_id = None
        request_fingerprint = compute_worker_chat_request_fingerprint(
            mode=normalized_mode,
            worker_id=normalized_worker_id,
            conversation_id=normalized_conversation_id,
            report_id=normalized_report_id,
            message=message,
        )
        existing_turn = self._turn_store.replay_turn_if_present(
            request_id=request_id,
            request_fingerprint=request_fingerprint,
        )
        if existing_turn is not None:
            return _reply_from_turn(existing_turn)
        worker = require_allowed_worker(normalized_worker_id)
        if normalized_mode == "generic_worker_chat" and normalized_report_id:
            raise UiProductError("INVALID_INPUT", "普通 worker 聊天不能绑定报告。")
        report_context_text = ""
        prompt_user_text = message
        if normalized_mode == "report_worker_chat":
            if not normalized_report_id:
                raise UiProductError("REPORT_NOT_FOUND", "请先打开一份报告后再问。")
            report_context = self._report_context_resolver.build_report_context(
                report_id=normalized_report_id,
                worker_id=worker.worker_id,
                question=message,
            )
            report_context_text = report_context.text
            prompt_user_text = _sanitize_report_question(message)
        prompt = _build_worker_chat_message(
            mode=normalized_mode,
            worker_display_name=worker.display_name,
            user_text=prompt_user_text,
            report_context=report_context_text,
        )
        reply = self._openclaw.send_worker_chat(
            mode=normalized_mode,
            worker_id=worker.worker_id,
            user_message=prompt,
            conversation_id=normalized_conversation_id,
            idempotency_key=request_id,
            report_id=normalized_report_id,
        )
        saved_turn = self._turn_store.save_turn_if_absent(
            WorkerChatTurn(
                request_id=request_id,
                request_fingerprint=request_fingerprint,
                mode=normalized_mode,
                worker_id=worker.worker_id,
                conversation_id=normalized_conversation_id,
                report_id=normalized_report_id,
                message=message,
                reply_text=reply.text,
            )
        )
        return _reply_from_turn(saved_turn)


def _reply_from_turn(turn: WorkerChatTurn) -> WorkerChatReplyForUser:
    worker = require_allowed_worker(turn.worker_id)
    return WorkerChatReplyForUser(
        kind="worker_chat_reply",
        workerDisplayName=worker.display_name,
        text=turn.reply_text,
        mode=turn.mode,
    )


def _require_mode(mode: str) -> WorkerChatMode:
    if mode in {"generic_worker_chat", "report_worker_chat"}:
        return cast(WorkerChatMode, mode)
    raise UiProductError("INVALID_INPUT", "worker 聊天模式无效。")


def _build_worker_chat_message(
    *,
    mode: WorkerChatMode,
    worker_display_name: str,
    user_text: str,
    report_context: str,
) -> str:
    if mode == "generic_worker_chat":
        return user_text
    return (
        "【claw-trade report_worker_chat】\n"
        f"worker_display_name: {worker_display_name}\n"
        "请按当前 worker workspace 的 AGENTS.md「报告追问模式」回答。\n\n"
        "【SavedReport】\n"
        f"{report_context}\n\n"
        "【UserQuestion】\n"
        f"{user_text}"
    )


def _sanitize_report_question(question: str) -> str:
    sanitized = sanitize_report_worker_chat_visible_text(question).strip()
    return sanitized or "（用户问题包含内部协议标记，已移除。）"
