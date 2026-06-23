from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, cast

WorkerChatMode = Literal["generic_worker_chat", "report_worker_chat"]
GENERIC_WORKER_CHAT_AGENT_ID = "ui_worker_chat"


class OpenClawWorkerChatGateway(Protocol):
    def sessions_create(self, *, metadata: dict[str, object]) -> str: ...

    def worker_chat_send(
        self,
        *,
        session_key: str,
        message: str,
        idempotency_key: str,
    ) -> dict[str, object] | str: ...


@dataclass(frozen=True)
class OpenClawAgentChatRequest:
    mode: WorkerChatMode
    worker_id: str
    session_key: str
    user_message: str
    idempotency_key: str


@dataclass(frozen=True)
class OpenClawAgentChatResponse:
    text: str
    session_key: str


class OpenClawWorkerChatClient:
    def __init__(self, gateway: OpenClawWorkerChatGateway) -> None:
        self._gateway = gateway
        self._session_key_cache: set[str] = set()

    def send_worker_chat(
        self,
        *,
        mode: str,
        worker_id: str,
        user_message: str,
        conversation_id: str,
        idempotency_key: str,
        report_id: str | None = None,
    ) -> OpenClawAgentChatResponse:
        normalized_mode = _require_mode(mode)
        session_key = build_session_key(
            mode=normalized_mode,
            worker_id=worker_id,
            report_id=report_id,
            conversation_id=conversation_id,
        )
        return self.send_agent_chat(
            OpenClawAgentChatRequest(
                mode=normalized_mode,
                worker_id=worker_id,
                session_key=session_key,
                user_message=user_message,
                idempotency_key=idempotency_key,
            )
        )

    def send_agent_chat(self, request: OpenClawAgentChatRequest) -> OpenClawAgentChatResponse:
        _validate_request(request)
        requested_session_key = request.session_key.strip()
        if requested_session_key in self._session_key_cache:
            session_key = requested_session_key
        else:
            session_key = self._gateway.sessions_create(
                metadata={
                    "scope": request.mode,
                    "workerId": request.worker_id.strip(),
                    "agentId": _agent_id_for_mode(request.mode, request.worker_id),
                    "sessionKey": requested_session_key,
                    "label": requested_session_key,
                }
            )
        session_key = session_key.strip()
        if not _session_key_matches_request(request.mode, session_key, request.worker_id):
            raise ValueError("session_key does not match worker chat request")
        self._session_key_cache.add(session_key)
        reply = self._gateway.worker_chat_send(
            session_key=session_key,
            message=request.user_message.strip(),
            idempotency_key=request.idempotency_key.strip(),
        )
        return OpenClawAgentChatResponse(text=_reply_text(reply), session_key=session_key)


def build_session_key(
    *,
    mode: str,
    worker_id: str,
    report_id: str | None = None,
    conversation_id: str,
) -> str:
    normalized_mode = _require_mode(mode)
    worker = _required_string(worker_id, "worker_id")
    safe_conversation = _safe_key(conversation_id)
    if normalized_mode == "generic_worker_chat":
        return f"agent:{GENERIC_WORKER_CHAT_AGENT_ID}:generic:{worker}:{safe_conversation}"
    safe_report = _safe_key(_required_string(report_id, "report_id"))
    return f"agent:{worker}:report:{safe_report}:{safe_conversation}"


def _validate_request(request: OpenClawAgentChatRequest) -> None:
    mode = _require_mode(request.mode)
    worker_id = _required_string(request.worker_id, "worker_id")
    session_key = _required_string(request.session_key, "session_key")
    if not _session_key_matches_request(mode, session_key, worker_id):
        raise ValueError("session_key does not match worker chat request")
    _required_string(request.user_message, "user_message")
    _required_string(request.idempotency_key, "idempotency_key")


def _require_mode(mode: str) -> WorkerChatMode:
    if mode in {"generic_worker_chat", "report_worker_chat"}:
        return cast(WorkerChatMode, mode)
    raise ValueError("mode must be generic_worker_chat or report_worker_chat")


def _required_string(value: str | None, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} is required")
    return value.strip()


def _safe_key(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in {"-", "_", ".", ":"} else "-" for char in value.strip())
    return (cleaned or "chat")[:160]


def _session_key_contains_worker(session_key: str, worker_id: str) -> bool:
    return session_key.startswith(f"agent:{worker_id.strip()}:")


def _session_key_matches_request(mode: WorkerChatMode, session_key: str, worker_id: str) -> bool:
    if mode == "generic_worker_chat":
        return session_key.startswith(f"agent:{GENERIC_WORKER_CHAT_AGENT_ID}:generic:{worker_id.strip()}:")
    return _session_key_contains_worker(session_key, worker_id)


def _agent_id_for_mode(mode: WorkerChatMode, worker_id: str) -> str:
    if mode == "generic_worker_chat":
        return GENERIC_WORKER_CHAT_AGENT_ID
    return worker_id.strip()


def _reply_text(reply: dict[str, object] | str) -> str:
    if isinstance(reply, str):
        return reply
    return str(reply.get("text") or "")
