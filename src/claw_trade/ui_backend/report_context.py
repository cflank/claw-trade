from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from claw_trade.artifacts.openviking_backend_http import (
    OpenVikingHttpBackend,
    create_default_backend,
)
from claw_trade.artifacts.openviking_client import OpenVikingAccessError
from claw_trade.data_gateway.report_evidence import summarize_data_refs

_DEFAULT_CONTEXT_CHARS = 24_000
_SNIPPET_CHARS = 3_000
_MAX_MONGO_REFS = 8


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
        openviking_backend_factory: Callable[[], OpenVikingHttpBackend] | None = create_default_backend,
        mongo_uri: str | None = None,
        mongo_database: str | None = None,
    ) -> None:
        self._run_root = run_root
        self._max_context_chars = max_context_chars
        self._openviking_backend_factory = openviking_backend_factory
        self._mongo_uri = mongo_uri
        self._mongo_database = mongo_database

    def build_context(self, *, report_id: str, question: str, fallback_markdown: str) -> ReportContext:
        run_dir = (self._run_root / report_id).resolve()
        if not _is_relative_to(run_dir, self._run_root.resolve()) or not run_dir.exists():
            return _context_from_markdown(fallback_markdown, question, self._max_context_chars)

        manifest = _load_json_object(run_dir / "openviking" / "approved-manifest.json")
        materials = _manifest_materials(manifest)
        selected = _select_materials(materials, question)
        backend = self._create_openviking_backend()

        sections: list[str] = []
        find_section = self._openviking_find_section(backend=backend, report_id=report_id, question=question)
        if find_section:
            sections.append(find_section)

        for material in selected:
            worker_id = str(material.get("worker_id") or "")
            content = self._read_material_text(backend=backend, material=material, run_dir=run_dir)
            if not content:
                continue
            snippet = _extract_relevant_snippet(content, question, max_chars=_SNIPPET_CHARS)
            if not snippet:
                continue
            uri = str(material.get("l1_uri") or "")
            source = f"{worker_id} L1"
            if uri:
                source = f"{source} ({uri})"
            sections.append(f"### {source}\n{snippet}")

        evidence_section = self._evidence_section(run_dir=run_dir, selected_materials=selected, question=question)
        if evidence_section:
            sections.append(evidence_section)

        if not sections:
            return _context_from_markdown(fallback_markdown, question, self._max_context_chars)
        text = _join_with_budget(sections, self._max_context_chars)
        return ReportContext(text=text, source_count=len(sections))

    def _create_openviking_backend(self) -> OpenVikingHttpBackend | None:
        if self._openviking_backend_factory is None:
            return None
        try:
            return self._openviking_backend_factory()
        except Exception:
            return None

    def _openviking_find_section(
        self,
        *,
        backend: OpenVikingHttpBackend | None,
        report_id: str,
        question: str,
    ) -> str | None:
        if backend is None:
            return None
        query = question.strip()
        if not query:
            return None
        root_uri = f"viking://resources/workflow/{report_id}/"
        try:
            raw = backend.find_approved_materials(root_uri, query)
        except Exception:
            return None
        matches = raw.get("matches") if isinstance(raw, dict) else None
        if not isinstance(matches, list) or not matches:
            return None
        lines: list[str] = []
        for item in matches[:5]:
            if not isinstance(item, dict):
                continue
            uri = str(item.get("uri") or item.get("path") or "").strip()
            abstract = str(item.get("abstract") or item.get("overview") or item.get("snippet") or "").strip()
            reason = str(item.get("match_reason") or item.get("reason") or "").strip()
            if uri or abstract or reason:
                lines.append(f"- {uri} {abstract} {reason}".strip())
        if not lines:
            return None
        return "### OpenViking 检索命中\n" + "\n".join(lines)

    def _read_material_text(
        self,
        *,
        backend: OpenVikingHttpBackend | None,
        material: dict[str, Any],
        run_dir: Path,
    ) -> str | None:
        uri = str(material.get("l1_uri") or "").strip()
        if backend is not None and uri:
            try:
                content = backend.fetch_content_by_uri(uri)
                return content.decode("utf-8", errors="replace")
            except (OpenVikingAccessError, UnicodeDecodeError, OSError):
                pass
            except Exception:
                pass
        return _read_local_worker_material(run_dir=run_dir, material=material)

    def _evidence_section(
        self,
        *,
        run_dir: Path,
        selected_materials: list[dict[str, Any]],
        question: str,
    ) -> str | None:
        selected_workers = {str(item.get("worker_id") or "") for item in selected_materials}
        lineage = _load_json_object(run_dir / "openviking" / "lineage-relations.json")
        relations = lineage.get("relations") if isinstance(lineage, dict) else None
        if not isinstance(relations, list):
            return None
        refs: list[str] = []
        for relation in relations:
            if not isinstance(relation, dict):
                continue
            worker_id = str(relation.get("worker_id") or "")
            if selected_workers and worker_id and worker_id not in selected_workers:
                continue
            for key in ("from_uri", "to_uri"):
                value = str(relation.get(key) or "")
                if value.startswith("mongo://") and value not in refs:
                    refs.append(value)
            if len(refs) >= _MAX_MONGO_REFS:
                break
        summaries = summarize_data_refs(
            refs,
            mongo_uri=self._mongo_uri,
            mongo_database=self._mongo_database,
            limit=_MAX_MONGO_REFS,
        )
        if not summaries:
            summaries = refs[:_MAX_MONGO_REFS]
        if not summaries:
            return None
        rendered = "\n".join(f"- {item}" for item in summaries)
        return f"### 数据层/证据引用\n{rendered}"


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
    call_id = str(material.get("call_id") or "")
    if call_id:
        for name in ("openviking_l1.md", "raw-output.md"):
            text = _read_text(run_dir / "calls" / call_id / name)
            if text:
                return text
    return None


def _context_from_markdown(markdown: str, question: str, max_chars: int) -> ReportContext:
    snippet = _extract_relevant_snippet(markdown, question, max_chars=max_chars)
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
