from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from errors import E_EVIDENCE_WRITE_FAILED, NewsDataError  # noqa: E402
from evidence import EvidenceWriter  # noqa: E402
from models import (  # noqa: E402
    EvidenceWriteRequest,
    KeywordObservations,
    NewsDataPack,
    NewsItem,
    ProviderAttempt,
    Quality,
    QueryPlan,
    ToolRuntimeContext,
)


def _build_pack(
    *,
    error_text: str | None = None,
    reader_brief: str = "测试摘要",
    profile: dict[str, str | None] | None = None,
) -> NewsDataPack:
    observation = KeywordObservations(
        matched_terms=["经营"],
        keyword_categories=["经营"],
        method="keyword_match",
        is_sentiment_judgment=False,
    )
    item = NewsItem(
        news_id="n-1",
        title="标题",
        summary="摘要 token=abc123",
        source="来源",
        publish_time="2026-05-07T10:00:00+08:00",
        url="https://example.com/1?api_key=TOPSECRET",
        data_source="akshare.stock_news_em",
        matched_keywords=["600519"],
        match_type="ticker_exact",
        match_evidence_span="600519",
        match_confidence="high",
        bucket="company_news",
        keyword_observations=observation,
        source_fetch_time="2026-05-07T10:01:00+08:00",
        content_hash="hash-1",
        is_primary_source=True,
        merged_from=[],
        evidence_gap=None,
    )
    plan = QueryPlan(
        ticker="600519",
        exchange_ticker="600519.SH",
        company_name="贵州茅台",
        industry="白酒",
        start_date="2026-05-01",
        end_date="2026-05-07",
        company_keywords=["600519", "600519.SH", "贵州茅台"],
        industry_keywords=["白酒"],
        macro_keywords=["消费"],
    )
    attempt = ProviderAttempt(
        provider="akshare",
        endpoint="stock_news_em",
        query="symbol=600519&token=TOPSECRET",
        ok=True,
        elapsed_ms=23,
        raw_count=1,
        accepted_count=1,
        empty_reason=None,
        error=error_text,
        cancelled=False,
    )
    quality = Quality(
        status="complete",
        company_direct_news_count=1,
        industry_background_count=0,
        policy_macro_count=0,
        total_raw_count=1,
        after_dedup_count=1,
        accepted_count=1,
        missing_fields=[],
        directional_judgment_allowed=True,
        warnings=["Authorization: Bearer supersecret-token"],
    )
    return NewsDataPack(
        ok=True,
        profile=profile or {"industry": "白酒", "company_name": "贵州茅台"},
        query_plan=plan,
        provider_attempts=[attempt],
        data={"company_news": [item], "policy_macro_news": []},
        quality=quality,
        reader_brief=reader_brief,
        evidence={"pack_path": "runs/abc/pack.json"},
    )


def _build_context(evidence_root: Path, call_id: str = "call-1") -> ToolRuntimeContext:
    return ToolRuntimeContext(
        run_id="run-1",
        stage="frontline",
        worker_id="news_analyst",
        call_id=call_id,
        tool_name="news_news_data_pack",
        evidence_root=str(evidence_root),
    )


def test_write_pack_writes_expected_files_and_hash(tmp_path: Path) -> None:
    evidence_root = tmp_path / "evidence"
    raw_path = evidence_root / "provider_raw" / "raw-1.json"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text('{"ok":true,"api_key":"TOPSECRET"}', encoding="utf-8")
    request = EvidenceWriteRequest(
        context=_build_context(evidence_root),
        pack=_build_pack(),
        provider_raw_refs=[str(raw_path)],
    )

    refs = EvidenceWriter().write_pack(request)

    pack_path = Path(refs.pack_path)
    attempts_path = Path(refs.provider_attempts_path)
    assert pack_path.exists()
    assert attempts_path.exists()
    assert "run-1/frontline/news_analyst/call-1" in refs.pack_path
    assert len(refs.provider_raw_paths) == 1
    copied_raw_path = Path(refs.provider_raw_paths[0])
    assert copied_raw_path.exists()
    assert "run-1/frontline/news_analyst/call-1/provider_raw/" in refs.provider_raw_paths[0]
    copied_raw_payload = json.loads(copied_raw_path.read_text(encoding="utf-8"))
    assert copied_raw_payload["api_key"] == "***"
    expected_hash = hashlib.sha256(pack_path.read_bytes()).hexdigest()
    assert refs.content_hash == expected_hash


def test_write_pack_rejects_raw_ref_outside_evidence_root(tmp_path: Path) -> None:
    evidence_root = tmp_path / "evidence"
    outside_raw = tmp_path / "outside" / "raw-1.json"
    outside_raw.parent.mkdir(parents=True, exist_ok=True)
    outside_raw.write_text('{"ok":true}', encoding="utf-8")

    request = EvidenceWriteRequest(
        context=_build_context(evidence_root),
        pack=_build_pack(),
        provider_raw_refs=[str(outside_raw)],
    )
    with pytest.raises(NewsDataError) as exc_info:
        EvidenceWriter().write_pack(request)

    assert exc_info.value.code == E_EVIDENCE_WRITE_FAILED


def test_write_pack_fails_on_same_call_directory_and_keeps_existing_files(tmp_path: Path) -> None:
    evidence_root = tmp_path / "evidence"
    raw_path = evidence_root / "provider_raw" / "raw-1.json"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text('{"ok":true}', encoding="utf-8")
    context = _build_context(evidence_root, call_id="call-dup")
    first_request = EvidenceWriteRequest(
        context=context,
        pack=_build_pack(reader_brief="first"),
        provider_raw_refs=[str(raw_path)],
    )
    first_refs = EvidenceWriter().write_pack(first_request)
    first_content = Path(first_refs.pack_path).read_text(encoding="utf-8")

    second_request = EvidenceWriteRequest(
        context=context,
        pack=_build_pack(reader_brief="second"),
        provider_raw_refs=[str(raw_path)],
    )
    with pytest.raises(NewsDataError) as exc_info:
        EvidenceWriter().write_pack(second_request)

    assert exc_info.value.code == E_EVIDENCE_WRITE_FAILED
    assert Path(first_refs.pack_path).read_text(encoding="utf-8") == first_content


def test_write_pack_sanitizes_secret_fields_and_error_strings(tmp_path: Path) -> None:
    evidence_root = tmp_path / "evidence"
    raw_path = evidence_root / "provider_raw" / "raw-1.json"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text('{"ok":true}', encoding="utf-8")

    pack = _build_pack(
        error_text="request failed, cookie=super-cookie, Authorization: Bearer supersecret-token",
        reader_brief="密钥 token=abc123",
        profile={"api_key": "ULTRA-SECRET", "company_name": "贵州茅台"},
    )
    request = EvidenceWriteRequest(
        context=_build_context(evidence_root),
        pack=pack,
        provider_raw_refs=[str(raw_path)],
    )
    refs = EvidenceWriter().write_pack(request)

    pack_payload = json.loads(Path(refs.pack_path).read_text(encoding="utf-8"))
    attempts_payload = json.loads(Path(refs.provider_attempts_path).read_text(encoding="utf-8"))
    pack_text = json.dumps(pack_payload, ensure_ascii=False)
    attempts_text = json.dumps(attempts_payload, ensure_ascii=False)
    raw_text = "\n".join(
        Path(raw_path_ref).read_text(encoding="utf-8") for raw_path_ref in refs.provider_raw_paths
    )
    combined_text = f"{pack_text}\n{attempts_text}\n{raw_text}"
    for secret in ("abc123", "TOPSECRET", "ULTRA-SECRET", "super-cookie", "supersecret-token"):
        assert secret not in combined_text
    assert pack_payload["profile"]["api_key"] == "***"


def test_write_pack_sanitizes_bearer_in_plain_pack_string_field(tmp_path: Path) -> None:
    evidence_root = tmp_path / "evidence"
    raw_path = evidence_root / "provider_raw" / "raw-1.json"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text('{"ok":true}', encoding="utf-8")

    pack = _build_pack(profile={"note": "Bearer supersecret-token", "company_name": "贵州茅台"})
    request = EvidenceWriteRequest(
        context=_build_context(evidence_root, call_id="call-pack-bearer"),
        pack=pack,
        provider_raw_refs=[str(raw_path)],
    )
    refs = EvidenceWriter().write_pack(request)
    pack_payload = json.loads(Path(refs.pack_path).read_text(encoding="utf-8"))
    pack_text = json.dumps(pack_payload, ensure_ascii=False)

    assert "supersecret-token" not in pack_text
    assert pack_payload["profile"]["note"] == "Bearer ***"


def test_write_pack_sanitizes_bearer_in_provider_raw_plain_string_field(tmp_path: Path) -> None:
    evidence_root = tmp_path / "evidence"
    raw_path = evidence_root / "provider_raw" / "raw-bearer.json"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(
        '{"note":"Bearer raw-secret-token","note2":"token=should-hide"}',
        encoding="utf-8",
    )
    request = EvidenceWriteRequest(
        context=_build_context(evidence_root, call_id="call-raw-bearer"),
        pack=_build_pack(),
        provider_raw_refs=[str(raw_path)],
    )

    refs = EvidenceWriter().write_pack(request)
    copied_payload = json.loads(Path(refs.provider_raw_paths[0]).read_text(encoding="utf-8"))
    copied_text = json.dumps(copied_payload, ensure_ascii=False)

    assert "raw-secret-token" not in copied_text
    assert copied_payload["note"] == "Bearer ***"
    assert copied_payload["note2"] == "token=***"


def test_write_pack_small_performance_sample_under_one_second(tmp_path: Path) -> None:
    evidence_root = tmp_path / "evidence"
    raw_path = evidence_root / "provider_raw" / "raw-1.json"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text('{"ok":true}', encoding="utf-8")
    large_reader_brief = "a" * (1024 * 1024)
    request = EvidenceWriteRequest(
        context=_build_context(evidence_root, call_id="call-perf"),
        pack=_build_pack(reader_brief=large_reader_brief),
        provider_raw_refs=[str(raw_path)],
    )

    started = time.perf_counter()
    refs = EvidenceWriter().write_pack(request)
    elapsed = time.perf_counter() - started

    assert elapsed < 1.0
    assert Path(refs.pack_path).stat().st_size > 1024 * 1024


def test_write_pack_copies_raw_ref_to_current_call_provider_raw_dir(tmp_path: Path) -> None:
    evidence_root = tmp_path / "evidence"
    source_raw_path = evidence_root / "provider_raw" / "akshare-stock_news_em.json"
    source_raw_path.parent.mkdir(parents=True, exist_ok=True)
    source_raw_path.write_text('{"provider":"akshare","token":"should-hide"}', encoding="utf-8")
    request = EvidenceWriteRequest(
        context=_build_context(evidence_root, call_id="call-provider-raw"),
        pack=_build_pack(),
        provider_raw_refs=[str(source_raw_path)],
    )

    refs = EvidenceWriter().write_pack(request)

    assert len(refs.provider_raw_paths) == 1
    copied_path = Path(refs.provider_raw_paths[0])
    assert copied_path.parent == (
        evidence_root / "run-1" / "frontline" / "news_analyst" / "call-provider-raw" / "provider_raw"
    )
    copied_payload = json.loads(copied_path.read_text(encoding="utf-8"))
    assert copied_payload["token"] == "***"


def test_write_pack_fails_when_raw_ref_unreadable(tmp_path: Path) -> None:
    evidence_root = tmp_path / "evidence"
    missing_raw_path = evidence_root / "provider_raw" / "missing.json"
    request = EvidenceWriteRequest(
        context=_build_context(evidence_root, call_id="call-missing-raw"),
        pack=_build_pack(),
        provider_raw_refs=[str(missing_raw_path)],
    )

    with pytest.raises(NewsDataError) as exc_info:
        EvidenceWriter().write_pack(request)

    assert exc_info.value.code == E_EVIDENCE_WRITE_FAILED


def test_write_pack_copies_text_raw_ref_with_sanitization(tmp_path: Path) -> None:
    evidence_root = tmp_path / "evidence"
    text_raw_path = evidence_root / "provider_raw" / "provider.log"
    text_raw_path.parent.mkdir(parents=True, exist_ok=True)
    text_raw_path.write_text(
        "request failed: Authorization: Bearer secret-token cookie=session-abc",
        encoding="utf-8",
    )
    request = EvidenceWriteRequest(
        context=_build_context(evidence_root, call_id="call-text-raw"),
        pack=_build_pack(),
        provider_raw_refs=[str(text_raw_path)],
    )

    refs = EvidenceWriter().write_pack(request)
    copied_path = Path(refs.provider_raw_paths[0])

    assert copied_path.suffix == ".txt"
    assert "call-text-raw/provider_raw" in str(copied_path)
    copied_text = copied_path.read_text(encoding="utf-8")
    assert "secret-token" not in copied_text
    assert "session-abc" not in copied_text
