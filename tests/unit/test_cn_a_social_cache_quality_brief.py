from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


SOCIAL_SCRIPTS_ROOT = Path("agents/social_analyst/skills/cn-a-social-data/scripts")
SOCIAL_CACHE_PATH = SOCIAL_SCRIPTS_ROOT / "cache.py"
SOCIAL_QUALITY_PATH = SOCIAL_SCRIPTS_ROOT / "quality.py"
SOCIAL_BRIEF_PATH = SOCIAL_SCRIPTS_ROOT / "reader_brief.py"


def test_cache_key_is_stable_for_same_query_input() -> None:
    first = _cache_key()
    second = _cache_key()

    assert first.canonical_json() == second.canonical_json()
    assert first.cache_id() == second.cache_id()


def test_inspect_provider_cache_hit_returns_raw_ref_and_payload_hash(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(UTC)
    fetched_at = (now - timedelta(minutes=2)).isoformat()
    monkeypatch.setattr(
        CACHE_MODULE,
        "_find_cache_record",
        lambda **kwargs: {
            "cache_id": _cache_key().cache_id(),
            "market": "CN_A",
            "ticker": "600519.SH",
            "provider": "akshare",
            "endpoint": "stock_hot_rank_latest_em",
            "query_fingerprint": "sha256:query-a",
            "date_window": "2026-05-01:2026-05-07",
            "as_of_date": "2026-05-07",
            "fetched_at": fetched_at,
            "schema_version": "cn_a_social_pack.v1",
            "payload_hash": "sha256:" + ("a" * 64),
            "raw_payload_ref": "viking://resources/workflow/run/frontline/social_analyst/cache-hit.json",
            "row_count": 1,
            "fields": {
                "symbol": "100.600519",
                "rank": 1,
                "heat": 999.0,
                "as_of_date": "2026-05-07",
            },
            "ttl_seconds": 1800,
            "created_at": fetched_at,
            "updated_at": fetched_at,
        },
    )

    result = CACHE_MODULE.inspect_provider_cache(
        key=_cache_key(),
        now_iso=now.isoformat(),
        config=_cache_config(),
    )

    assert result.status == "hit"
    assert result.payload_hash == "sha256:" + ("a" * 64)
    assert result.raw_payload_ref == "viking://resources/workflow/run/frontline/social_analyst/cache-hit.json"


def test_inspect_provider_cache_returns_stale_when_ttl_expired(monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(UTC)
    fetched_at = (now - timedelta(hours=2)).isoformat()
    monkeypatch.setattr(
        CACHE_MODULE,
        "_find_cache_record",
        lambda **kwargs: {
            "cache_id": _cache_key().cache_id(),
            "market": "CN_A",
            "ticker": "600519.SH",
            "provider": "akshare",
            "endpoint": "stock_hot_rank_latest_em",
            "query_fingerprint": "sha256:query-a",
            "date_window": "2026-05-01:2026-05-07",
            "as_of_date": "2026-05-07",
            "fetched_at": fetched_at,
            "schema_version": "cn_a_social_pack.v1",
            "payload_hash": "sha256:" + ("b" * 64),
            "raw_payload_ref": "viking://resources/workflow/run/frontline/social_analyst/cache-stale.json",
            "row_count": 1,
            "fields": {
                "symbol": "100.600519",
                "rank": 1,
                "heat": 888.0,
                "as_of_date": "2026-05-07",
            },
            "ttl_seconds": 1800,
            "created_at": fetched_at,
            "updated_at": fetched_at,
        },
    )

    result = CACHE_MODULE.inspect_provider_cache(
        key=_cache_key(),
        now_iso=now.isoformat(),
        config=_cache_config(),
    )

    assert result.status == "stale"
    assert result.payload_hash is None
    assert result.raw_payload_ref is None


def test_evaluate_social_quality_returns_complete_for_heat_and_keyword() -> None:
    decision = QUALITY_MODULE.evaluate_social_quality(
        QUALITY_MODULE.QualityInput(
            attempts=(
                _attempt(ok=True, status="success"),
                _attempt(ok=True, status="success"),
            ),
            buckets=_buckets(
                attention_signals=[_signal(endpoint="stock_hot_rank_latest_em", rank_change=1)],
                topic_keyword_signals=[_signal(endpoint="stock_hot_keyword_em", rank_change=2)],
            ),
            required_p0_endpoints=("stock_hot_rank_latest_em", "stock_hot_keyword_em"),
        )
    )

    assert decision.ok is True
    assert decision.status == "complete"
    assert decision.social_judgment_allowed is True
    assert QUALITY_MODULE.COMPLETE_CONDITIONS_MET in decision.reason_codes


def test_evaluate_social_quality_fails_when_accepted_signal_missing_evidence() -> None:
    decision = QUALITY_MODULE.evaluate_social_quality(
        QUALITY_MODULE.QualityInput(
            attempts=(_attempt(ok=True, status="success"),),
            buckets=_buckets(
                attention_signals=[
                    _signal(
                        endpoint="stock_hot_rank_latest_em",
                        raw_payload_ref="",
                        content_hash="sha256:" + ("1" * 64),
                    )
                ]
            ),
            required_p0_endpoints=("stock_hot_rank_latest_em",),
        )
    )

    assert decision.ok is False
    assert decision.status == "failed"
    assert QUALITY_MODULE.EVIDENCE_REF_MISSING in decision.reason_codes


def test_build_reader_brief_limited_when_brief_contains_forbidden_investment_claim() -> None:
    brief = BRIEF_MODULE.build_reader_brief(
        BRIEF_MODULE.ReaderBriefInput(
            profile=SimpleNamespace(ticker="600519.SH"),
            attempts=(
                {
                    "provider": "买入雷达",
                    "endpoint": "stock_hot_rank_latest_em",
                    "priority": "P0",
                    "query": "{}",
                    "ok": True,
                    "status": "success",
                    "elapsed_ms": 11,
                    "raw_count": 1,
                    "accepted_count": 1,
                    "cache_status": "hit",
                    "cache_key": "cache-key",
                    "payload_hash": "sha256:" + ("a" * 64),
                    "raw_payload_ref": "viking://resources/workflow/run/frontline/social_analyst/call/provider_raw.json",
                    "empty_reason": None,
                    "error": None,
                    "cancelled": False,
                },
            ),
            buckets=_buckets(
                attention_signals=[_signal(endpoint="stock_hot_rank_latest_em", rank_change=1)],
            ),
            quality=SimpleNamespace(
                status="complete",
                missing_fields=[],
                warnings=[],
                reason_codes=("COMPLETE_CONDITIONS_MET",),
            ),
            date_window=SimpleNamespace(start_date="2026-05-01", end_date="2026-05-07"),
        )
    )

    assert brief.text == "本摘要仅保留数据统计、来源与缺口信息，其他结论已限制输出。"
    assert brief.limitation_codes == (BRIEF_MODULE.SOCIAL_BRIEF_FORBIDDEN_CLAIM,)


def _cache_key():
    return CACHE_MODULE.CacheKey(
        market="CN_A",
        ticker="600519.SH",
        provider="akshare",
        endpoint="stock_hot_rank_latest_em",
        query_fingerprint="sha256:query-a",
        date_window="2026-05-01:2026-05-07",
        schema_version="cn_a_social_pack.v1",
    )


def _cache_config():
    return CACHE_MODULE.SocialDataConfig(
        schema_version="cn_a_social_pack.v1",
        provider_timeout_seconds=10,
        pack_timeout_seconds=20,
        provider_max_concurrency=3,
        max_signals_per_bucket=50,
        mongodb_uri="mongodb://127.0.0.1:27017",
        mongodb_database="claw_trade",
        mongodb_cache_collection="social_provider_cache",
        cache_required=False,
        ttl_by_endpoint={
            "stock_hot_rank_latest_em": 1800,
            "stock_hot_rank_em": 1800,
            "stock_hot_up_em": 1800,
            "stock_hot_keyword_em": 3600,
            "stock_hot_rank_relate_em": 3600,
        },
        p1_hot_up_enabled=True,
        p1_xueqiu_enabled=False,
        evidence_root="runs",
        openviking_l2_write_target_root=None,
    )


def _attempt(*, ok: bool, status: str) -> dict[str, object]:
    return {
        "provider": "akshare",
        "endpoint": "stock_hot_rank_latest_em",
        "priority": "P0",
        "query": "{}",
        "ok": ok,
        "status": status,
        "elapsed_ms": 10,
        "raw_count": 1,
        "accepted_count": 1,
        "cache_status": "miss",
        "cache_key": "cache-key",
        "payload_hash": "sha256:" + ("2" * 64),
        "raw_payload_ref": "viking://resources/workflow/run/frontline/social_analyst/call/provider_raw.json",
        "empty_reason": None,
        "error": None,
        "cancelled": False,
    }


def _signal(
    *,
    endpoint: str,
    raw_payload_ref: str = "viking://resources/workflow/run/frontline/social_analyst/call/signal.json",
    content_hash: str = "sha256:" + ("3" * 64),
    rank_change: int | None = None,
) -> dict[str, object]:
    return {
        "provider": "akshare",
        "platform": "eastmoney",
        "endpoint": endpoint,
        "raw_payload_ref": raw_payload_ref,
        "content_hash": content_hash,
        "rank_change": rank_change,
        "source_time": "2026-05-07T10:00:00+08:00",
    }


def _buckets(
    *,
    attention_signals: list[dict[str, object]] | None = None,
    topic_keyword_signals: list[dict[str, object]] | None = None,
    related_symbol_signals: list[dict[str, object]] | None = None,
    narrative_signals: list[dict[str, object]] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        attention_signals=attention_signals or [],
        topic_keyword_signals=topic_keyword_signals or [],
        related_symbol_signals=related_symbol_signals or [],
        narrative_signals=narrative_signals or [],
        rejected_signals=[],
    )


def _load_module(module_name: str, module_path: Path):
    scripts_root = str(SOCIAL_SCRIPTS_ROOT.resolve())
    if scripts_root not in sys.path:
        sys.path.insert(0, scripts_root)
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载模块: {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


CACHE_MODULE = _load_module("cn_a_social_cache_unit", SOCIAL_CACHE_PATH)
QUALITY_MODULE = _load_module("cn_a_social_quality_unit", SOCIAL_QUALITY_PATH)
BRIEF_MODULE = _load_module("cn_a_social_reader_brief_unit", SOCIAL_BRIEF_PATH)
