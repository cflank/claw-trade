from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from claw_trade.ui_backend.report_context import ReportContextRetriever
from claw_trade.ui_backend.report_repository import ReportRepository, UiProductError
from claw_trade.ui_backend.report_worker_chat_context import sanitize_report_worker_chat_visible_text


class OpenClawChatGateway(Protocol):
    def sessions_create(self, *, metadata: dict[str, object]) -> str: ...

    def chat_send(
        self,
        *,
        request_id: str,
        context_id: str,
        session_id: str,
        text: str,
    ) -> dict[str, object] | str: ...


@dataclass(frozen=True)
class ReportQaContextPolicy:
    max_total_chars: int | None

    def fits_without_summarizing(self, report_markdown: str, question: str) -> bool:
        if self.max_total_chars is None:
            return False
        return len(report_markdown) + len(question) <= self.max_total_chars


class ReportQuestionService:
    def __init__(
        self,
        repository: ReportRepository,
        gateway: OpenClawChatGateway,
        *,
        context_policy: ReportQaContextPolicy,
        context_retriever: ReportContextRetriever | None = None,
    ) -> None:
        self._repository = repository
        self._gateway = gateway
        self._context_policy = context_policy
        self._context_retriever = context_retriever
        self._session_by_context: dict[tuple[str, str], str] = {}
        self._reply_cache: dict[str, dict[str, str]] = {}

    def ask_report_question(
        self,
        *,
        report_id: str,
        text: str,
        request_id: str,
        context_id: str,
    ) -> dict[str, str]:
        cached = self._reply_cache.get(request_id)
        if cached is not None:
            return cached
        report = self._repository.get_report(report_id)
        if report is None:
            raise UiProductError("REPORT_NOT_FOUND", "没有找到这份报告。")
        report_markdown = self._repository.read_markdown(report_id)
        if not report_markdown:
            raise UiProductError("REPORT_NOT_READY", "报告尚未准备好，请稍后再试。")
        context_text = self._build_context_text(report_id=report_id, question=text, report_markdown=report_markdown)
        if not context_text:
            raise UiProductError("REPORT_CONTEXT_TOO_LONG", "当前报告过长，暂时无法追问。")

        session_id = self._get_or_create_session_id(report_id=report_id, context_id=context_id)
        prompt = _build_report_qa_prompt(report_context=context_text, question=text)
        reply = self._gateway.chat_send(
            request_id=request_id,
            context_id=context_id,
            session_id=session_id,
            text=prompt,
        )
        if isinstance(reply, str):
            result = {"text": reply}
        else:
            result = {"text": str(reply.get("text") or "")}
        self._reply_cache[request_id] = result
        return result

    def _build_context_text(self, *, report_id: str, question: str, report_markdown: str) -> str:
        if self._context_retriever is not None:
            return self._context_retriever.build_context(
                report_id=report_id,
                question=question,
                fallback_markdown=report_markdown,
            ).text
        max_chars = self._context_policy.max_total_chars or 24_000
        if max_chars - len(question) < 200:
            return ""
        return sanitize_report_worker_chat_visible_text(report_markdown[: max_chars - len(question)]).rstrip()

    def _get_or_create_session_id(self, *, report_id: str, context_id: str) -> str:
        key = (report_id, context_id)
        existing = self._session_by_context.get(key)
        if existing:
            return existing
        session_key = f"ui:report_qa:{_session_safe_report_id(report_id)}"
        session_id = self._gateway.sessions_create(
            metadata={
                "scope": "report_qa",
                "reportId": report_id,
                "sessionKey": session_key,
                "label": session_key,
            }
        )
        self._session_by_context[key] = session_id
        return session_id


def _build_report_qa_prompt(*, report_context: str, question: str) -> str:
    return (
        "下面是从已保存正式报告和已批准 worker 材料中选出的相关上下文。\n"
        "请只基于这些上下文回答追问；上下文没有的信息，请明确说报告材料里没有。\n"
        "回答可以解释报告内容，但不得改写、覆盖或新增正式报告结论。\n\n"
        f"{report_context}\n\n"
        "用户追问：\n"
        f"{sanitize_report_worker_chat_visible_text(question)}"
    )


def _session_safe_report_id(report_id: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in {"-", "_", ".", ":"} else "-" for char in report_id.strip())
    return (cleaned or "report")[:420]
