from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from claw_trade.ui_backend.report_repository import ReportRepository, SavedReportRecord, UiProductError

FORBIDDEN_REPORT_WORKER_PROTOCOL_MARKERS: tuple[str, ...] = (
    "raw-output",
    "raw-output.md",
    "raw-output.json",
    "raw-output.txt",
    "provider_request",
    "provider request",
    "receipt",
    "sha256",
    "hash",
    "manifest",
    "mongo://",
    "viking://",
    "OpenViking",
    "Mongo",
    "RuntimeTarget",
    "[ApprovedMaterials]",
    "single_worker_minimal",
    "toolPolicy",
    "promptProfile",
    "promptVariables",
    "visibleTools",
    "captureProviderPayload",
)

_TOKEN_PROTOCOL_MARKERS: frozenset[str] = frozenset({"receipt", "sha256", "hash", "manifest"})
_PM_WORKER_ID = "portfolio_manager"
_MAX_WORKER_MATERIAL_CHARS = 16_000
_MAX_FINAL_REPORT_SNIPPET_CHARS = 6_000
_MAX_CONTEXT_CHARS = 24_000
_PROTOCOL_REPLACEMENT = "（内部协议标记已移除）"
_PROTOCOL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?:viking|mongo)://[^\s)\]}>\"'，。；、]+", re.IGNORECASE),
    re.compile(r"\braw-output(?:\.(?:md|json|txt))?\b", re.IGNORECASE),
    re.compile(r"\b(?:OpenViking|Mongo)\b", re.IGNORECASE),
)


@dataclass(frozen=True)
class ReportWorkerChatContext:
    text: str


class ReportWorkerChatContextResolver:
    def __init__(self, repository: ReportRepository) -> None:
        self._repository = repository

    def build_report_context(self, *, report_id: str, worker_id: str, question: str) -> ReportWorkerChatContext:
        report = self._repository.get_report(report_id)
        if report is None:
            raise UiProductError("REPORT_NOT_FOUND", "没有找到这份报告。")
        report_markdown = self._repository.read_markdown(report_id)
        if not report_markdown:
            raise UiProductError("REPORT_NOT_READY", "报告尚未准备好，请稍后再试。")

        sanitized_report_markdown = sanitize_report_worker_chat_visible_text(report_markdown)
        final_report_snippet = _extract_relevant_snippet(
            sanitized_report_markdown,
            question,
            max_chars=_MAX_FINAL_REPORT_SNIPPET_CHARS,
        ).strip()
        worker_material = _read_worker_appendix(report=report, worker_id=worker_id)
        if worker_material is None:
            worker_material = _pm_fallback_material(
                report=report,
                worker_id=worker_id,
                final_report_snippet=final_report_snippet,
            )
        if worker_material is None:
            raise UiProductError("REPORT_WORKER_MATERIAL_NOT_FOUND", "没有找到这个角色可供追问的报告材料，请重新生成报告。")

        sanitized_worker_material = sanitize_report_worker_chat_visible_text(worker_material).strip()
        if not sanitized_worker_material:
            raise UiProductError("REPORT_WORKER_MATERIAL_NOT_FOUND", "没有找到这个角色可供追问的报告材料，请重新生成报告。")

        title = sanitize_report_worker_chat_visible_text(report.title).strip() or "已保存报告"
        sections = [
            f"当前已完成报告：{title}",
            "",
            "## 当前 worker 可见报告材料",
            _limit_text(sanitized_worker_material, _MAX_WORKER_MATERIAL_CHARS),
        ]
        if final_report_snippet:
            sections.extend(
                [
                    "",
                    "## 正式报告相关片段",
                    final_report_snippet,
                ]
            )
        return ReportWorkerChatContext(text=_limit_text("\n".join(sections).strip(), _MAX_CONTEXT_CHARS))


def sanitize_report_worker_chat_visible_text(text: str) -> str:
    sanitized = text
    for pattern in _PROTOCOL_PATTERNS:
        sanitized = pattern.sub(_PROTOCOL_REPLACEMENT, sanitized)
    for marker in FORBIDDEN_REPORT_WORKER_PROTOCOL_MARKERS:
        pattern = (
            rf"(?<![A-Za-z0-9_]){re.escape(marker)}(?![A-Za-z0-9_])"
            if marker.lower() in _TOKEN_PROTOCOL_MARKERS
            else re.escape(marker)
        )
        sanitized = re.sub(pattern, _PROTOCOL_REPLACEMENT, sanitized, flags=re.IGNORECASE)
    return sanitized


def _read_worker_appendix(*, report: SavedReportRecord, worker_id: str) -> str | None:
    if report.asset_dir is None:
        return None
    appendix_dir = report.asset_dir.parent / "worker-appendix"
    if not appendix_dir.exists() or not appendix_dir.is_dir():
        return None
    for path in sorted(appendix_dir.glob(f"*-{worker_id}.md")):
        if not _is_child_file(path, appendix_dir):
            continue
        text = _read_text(path)
        if text:
            return text
    return None


def _pm_fallback_material(*, report: SavedReportRecord, worker_id: str, final_report_snippet: str) -> str | None:
    if worker_id != _PM_WORKER_ID:
        return None
    conclusion = (report.pm_final_conclusion or "").strip()
    if conclusion:
        return f"# PM 最终结论\n\n{conclusion}\n"
    if final_report_snippet:
        return f"# 已保存正式报告片段\n\n{final_report_snippet}\n"
    return None


def _is_child_file(path: Path, root: Path) -> bool:
    try:
        resolved = path.resolve()
        resolved.relative_to(root.resolve())
    except ValueError:
        return False
    return resolved.is_file()


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _extract_relevant_snippet(text: str, question: str, *, max_chars: int) -> str:
    cleaned = text.strip()
    if len(cleaned) <= max_chars:
        return cleaned
    paragraphs = [paragraph.strip() for paragraph in re.split(r"\n{2,}", cleaned) if paragraph.strip()]
    tokens = _query_tokens(question)
    scored: list[tuple[int, int, str]] = []
    for index, paragraph in enumerate(paragraphs):
        lower = paragraph.lower()
        score = sum(lower.count(token) for token in tokens)
        if score:
            scored.append((index, score, paragraph))
    if not scored:
        return _limit_text(cleaned, max_chars)
    selected = sorted(sorted(scored, key=lambda item: (-item[1], item[0]))[:4], key=lambda item: item[0])
    return _join_with_budget([paragraph for _index, _score, paragraph in selected], max_chars=max_chars)


def _query_tokens(question: str) -> tuple[str, ...]:
    lower = question.lower()
    tokens: list[str] = re.findall(r"[a-z0-9_]{2,}", lower)
    cjk_chunks = re.findall(r"[\u4e00-\u9fff]+", lower)
    for chunk in cjk_chunks:
        if len(chunk) >= 2:
            tokens.append(chunk)
        if len(chunk) > 2:
            tokens.extend(chunk[index : index + 2] for index in range(0, len(chunk) - 1))
    return tuple(dict.fromkeys(token for token in tokens if len(token) >= 2))


def _join_with_budget(paragraphs: list[str], *, max_chars: int) -> str:
    selected: list[str] = []
    used = 0
    for paragraph in paragraphs:
        separator = 2 if selected else 0
        remaining = max_chars - used - separator
        if remaining <= 0:
            break
        selected.append(_limit_text(paragraph, remaining))
        used += separator + len(selected[-1])
    return "\n\n".join(selected).strip()


def _limit_text(text: str, max_chars: int) -> str:
    cleaned = text.strip()
    if len(cleaned) <= max_chars:
        return cleaned
    return cleaned[:max_chars].rstrip()
