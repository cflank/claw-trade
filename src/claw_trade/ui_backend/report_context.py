from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from claw_trade.ui_backend.report_worker_chat_context import sanitize_report_worker_chat_visible_text

_DEFAULT_CONTEXT_CHARS = 24_000
_SNIPPET_CHARS = 3_000


@dataclass(frozen=True)
class ReportContext:
    text: str
    source_count: int


class ReportContextRetriever:
    def __init__(
        self,
        *,
        run_root: Path,
        max_context_chars: int = _DEFAULT_CONTEXT_CHARS,
        openviking_backend_factory: Callable[[], object] | None = None,
        mongo_uri: str | None = None,
        mongo_database: str | None = None,
    ) -> None:
        self._run_root = run_root
        self._max_context_chars = max_context_chars
        _ = (openviking_backend_factory, mongo_uri, mongo_database)

    def build_context(self, *, report_id: str, question: str, fallback_markdown: str) -> ReportContext:
        run_dir = (self._run_root / report_id).resolve()
        if not _is_relative_to(run_dir, self._run_root.resolve()) or not run_dir.exists():
            return _context_from_markdown(fallback_markdown, question, self._max_context_chars)

        manifest = _load_json_object(run_dir / "openviking" / "approved-manifest.json")
        materials = _manifest_materials(manifest)
        selected = _select_materials(materials, question)

        sections: list[str] = []
        for material in selected:
            worker_id = str(material.get("worker_id") or "")
            content = _read_local_worker_material(run_dir=run_dir, material=material)
            if not content:
                continue
            content = sanitize_report_worker_chat_visible_text(content)
            snippet = _extract_relevant_snippet(content, question, max_chars=_SNIPPET_CHARS)
            if not snippet:
                continue
            source = f"{worker_id} L1"
            sections.append(f"### {source}\n{snippet}")

        if not sections:
            return _context_from_markdown(fallback_markdown, question, self._max_context_chars)
        text = _join_with_budget(sections, self._max_context_chars)
        return ReportContext(text=text, source_count=len(sections))


def _manifest_materials(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    raw = manifest.get("materials")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _select_materials(materials: list[dict[str, Any]], question: str) -> list[dict[str, Any]]:
    text = question.lower()
    wanted: set[str] = {"portfolio_manager", "research_manager"}
    if any(token in text for token in ("基本面", "估值", "财务", "pe", "pb", "roe", "ahr999", "sopr", "nupl", "链上")):
        wanted.update({"fundamental_analyst", "market_analyst"})
    if any(token in text for token in ("价格", "走势", "技术", "图表", "指标", "清算", "资金费率", "衍生品")):
        wanted.add("market_analyst")
    if any(token in text for token in ("新闻", "宏观", "公告", "事件", "fomc", "cpi")):
        wanted.add("news_analyst")
    if any(token in text for token in ("情绪", "社交", "舆情", "reddit", "twitter", "恐慌", "贪婪")):
        wanted.add("social_analyst")
    if any(token in text for token in ("风险", "回撤", "止损", "仓位")):
        wanted.update({"risk_challenger", "risk_guardian", "risk_moderator"})
    if any(token in text for token in ("交易", "执行", "买", "卖", "持有", "入场", "离场")):
        wanted.add("trader")

    selected = [item for item in materials if str(item.get("worker_id") or "") in wanted]
    if selected:
        return selected[:8]
    return materials[:6]


def _read_local_worker_material(*, run_dir: Path, material: dict[str, Any]) -> str | None:
    worker_id = str(material.get("worker_id") or "")
    appendix_dir = run_dir / "reports" / "worker-appendix"
    if appendix_dir.exists() and worker_id:
        for path in sorted(appendix_dir.glob(f"*-{worker_id}.md")):
            return _read_text(path)
    return None


def _context_from_markdown(markdown: str, question: str, max_chars: int) -> ReportContext:
    snippet = _extract_relevant_snippet(sanitize_report_worker_chat_visible_text(markdown), question, max_chars=max_chars)
    return ReportContext(text=f"### 正式报告片段\n{snippet}", source_count=1)


def _extract_relevant_snippet(text: str, question: str, *, max_chars: int) -> str:
    cleaned = text.strip()
    if len(cleaned) <= max_chars:
        return cleaned
    tokens = _query_tokens(question)
    if not tokens:
        return cleaned[:max_chars].rstrip()
    paragraphs = re.split(r"\n{2,}", cleaned)
    hits = [para.strip() for para in paragraphs if any(token in para.lower() for token in tokens)]
    if not hits:
        return cleaned[:max_chars].rstrip()
    return _join_with_budget(hits, max_chars)


def _query_tokens(question: str) -> list[str]:
    raw_tokens = re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]{2,}", question.lower())
    stop = {"这个", "为什么", "怎么", "如何", "报告", "当前", "请问", "请解释"}
    return [token for token in raw_tokens if token not in stop][:12]


def _join_with_budget(parts: list[str], max_chars: int) -> str:
    rendered: list[str] = []
    used = 0
    for part in parts:
        chunk = part.strip()
        if not chunk:
            continue
        remaining = max_chars - used
        if remaining <= 0:
            break
        if len(chunk) > remaining:
            rendered.append(chunk[:remaining].rstrip())
            break
        rendered.append(chunk)
        used += len(chunk) + 2
    return "\n\n".join(rendered)


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
