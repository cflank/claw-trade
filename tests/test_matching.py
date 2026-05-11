from __future__ import annotations

from dataclasses import dataclass
import importlib.util
from pathlib import Path
import sys


SOCIAL_SCRIPTS_ROOT = Path("agents/social_analyst/skills/cn-a-social-data/scripts")
SOCIAL_MATCHING_PATH = SOCIAL_SCRIPTS_ROOT / "matching.py"


@dataclass(frozen=True)
class _Alias:
    alias: str


@dataclass(frozen=True)
class _Profile:
    ticker: str
    ticker_plain: str
    eastmoney_symbol: str
    company_name: str | None
    market: str
    industry: str | None
    approved_aliases: tuple[_Alias, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class _Config:
    max_signals_per_bucket: int = 50


def test_classify_match_returns_exact_ticker_when_row_contains_plain_ticker() -> None:
    row = _build_row(name="贵州茅台（600519）")
    result = MATCHING_MODULE.classify_match(row, _profile())
    assert result == "exact_ticker"


def test_classify_match_returns_provider_target_symbol_when_query_symbol_matches_profile() -> None:
    row = _build_row(
        endpoint="stock_hot_rank_latest_em",
        query={"symbol": "100.600519"},
        name="贵州茅台热度快照",
    )
    result = MATCHING_MODULE.classify_match(row, _profile())
    assert result == "provider_target_symbol"


def test_classify_match_returns_industry_or_topic_only_for_industry_term_without_target_hit() -> None:
    row = _build_row(title="白酒板块今日热度上升", keyword="消费升级")
    result = MATCHING_MODULE.classify_match(row, _profile())
    assert result == "industry_or_topic_only"


def test_classify_match_returns_related_symbol_only_for_related_source_without_target_hit() -> None:
    row = _build_row(
        source_kind="related_symbol",
        symbol="000858",
        name="五粮液",
    )
    result = MATCHING_MODULE.classify_match(row, _profile())
    assert result == "related_symbol_only"


def test_classify_match_supports_company_name() -> None:
    row = _build_row(title="贵州茅台走势讨论")
    result = MATCHING_MODULE.classify_match(row, _profile(company_name="贵州茅台", aliases=()))
    assert result == "company_name"


def test_classify_match_supports_approved_alias() -> None:
    row = _build_row(title="茅台分红预期升温")
    result = MATCHING_MODULE.classify_match(row, _profile(company_name=None, aliases=("茅台",)))
    assert result == "approved_alias"


def test_classify_match_returns_unmatched_when_no_target_or_topic_context() -> None:
    row = _build_row(title="半导体指数震荡")
    result = MATCHING_MODULE.classify_match(row, _profile(company_name=None, aliases=(), industry=None))
    assert result == "unmatched"


def test_match_deduplicate_and_bucket_places_target_heat_rank_into_attention_signals() -> None:
    row = _build_row(
        source_kind="heat_rank",
        name="贵州茅台（600519）热度上升",
        rank="2",
        value="97.8",
    )
    buckets = MATCHING_MODULE.match_deduplicate_and_bucket([row], _profile(), _Config())
    assert len(buckets.attention_signals) == 1
    assert buckets.attention_signals[0].match_type == "exact_ticker"
    assert buckets.attention_signals[0].raw_payload_ref == "viking://resources/workflow/run/frontline/social/call/evidence/provider_raw/row.json"
    assert buckets.attention_signals[0].content_hash == "sha256:1111111111111111111111111111111111111111111111111111111111111111"
    assert buckets.attention_signals[0].evidence_gap is None


def test_match_deduplicate_and_bucket_places_related_symbol_only_into_related_bucket() -> None:
    row = _build_row(
        source_kind="related_symbol",
        symbol="000858",
        name="五粮液",
        title="白酒板块联动",
    )
    buckets = MATCHING_MODULE.match_deduplicate_and_bucket([row], _profile(company_name=None, aliases=(), industry="新能源"), _Config())
    assert len(buckets.related_symbol_signals) == 1
    assert buckets.related_symbol_signals[0].match_type == "related_symbol_only"
    assert not buckets.attention_signals
    assert not buckets.topic_keyword_signals


def test_match_deduplicate_and_bucket_rejects_row_without_content_hash() -> None:
    row = _build_row(name="贵州茅台（600519）", payload_hash="")
    buckets = MATCHING_MODULE.match_deduplicate_and_bucket([row], _profile(), _Config())
    assert not buckets.attention_signals
    assert len(buckets.rejected_signals) == 1
    assert buckets.rejected_signals[0].evidence_gap == "missing_raw_ref_or_hash"


def test_match_deduplicate_and_bucket_trims_each_bucket_by_max_signals_per_bucket() -> None:
    rows = [
        _build_row(
            raw_index=index,
            source_kind="heat_rank",
            name=f"贵州茅台（600519）第{index}条",
            rank=index + 1,
            value=100 - index,
            payload_hash=f"sha256:{index:064x}",
            raw_payload_ref=f"viking://resources/workflow/run/frontline/social/call/evidence/provider_raw/{index}.json",
        )
        for index in range(5)
    ]
    buckets = MATCHING_MODULE.match_deduplicate_and_bucket(rows, _profile(), _Config(max_signals_per_bucket=2))
    assert len(buckets.attention_signals) == 2


def test_deduplicate_signals_removes_duplicate_candidates() -> None:
    row_a = _build_row(raw_index=1, name="贵州茅台（600519）", rank=1, value=99)
    row_b = _build_row(raw_index=2, name="贵州茅台（600519）", rank=1, value=99)
    buckets = MATCHING_MODULE.match_deduplicate_and_bucket([row_a, row_b], _profile(), _Config())
    assert len(buckets.attention_signals) == 1


def _build_row(
    *,
    endpoint: str = "stock_hot_rank_em",
    source_kind: str = "heat_rank",
    provider: str = "akshare",
    platform: str = "东方财富",
    query: dict[str, str] | None = None,
    raw_index: int = 0,
    payload_hash: str = "sha256:1111111111111111111111111111111111111111111111111111111111111111",
    raw_payload_ref: str = "viking://resources/workflow/run/frontline/social/call/evidence/provider_raw/row.json",
    source_fetch_time: str = "2026-05-07T09:00:00+08:00",
    symbol: str = "",
    code: str = "",
    name: str = "",
    keyword: str = "",
    title: str = "",
    rank: object = "",
    rank_change: object = "",
    value: object = "",
    source_time: str = "2026-05-07",
) -> dict[str, object]:
    return {
        "provider": provider,
        "endpoint": endpoint,
        "platform": platform,
        "source_kind": source_kind,
        "query": query or {},
        "raw_index": raw_index,
        "payload_hash": payload_hash,
        "raw_payload_ref": raw_payload_ref,
        "source_fetch_time": source_fetch_time,
        "fields": {
            "symbol": symbol,
            "code": code,
            "name": name,
            "keyword": keyword,
            "title": title,
            "rank": rank,
            "rank_change": rank_change,
            "value": value,
            "source_time": source_time,
        },
    }


def _profile(
    *,
    company_name: str | None = "贵州茅台",
    aliases: tuple[str, ...] = ("茅台",),
    industry: str | None = "白酒",
) -> _Profile:
    return _Profile(
        ticker="600519.SH",
        ticker_plain="600519",
        eastmoney_symbol="100.600519",
        company_name=company_name,
        market="CN_A",
        industry=industry,
        approved_aliases=tuple(_Alias(alias=name) for name in aliases),
        warnings=(),
    )


def _load_matching_module():
    scripts_root = str(SOCIAL_SCRIPTS_ROOT.resolve())
    if scripts_root not in sys.path:
        sys.path.insert(0, scripts_root)
    module_name = "cn_a_social_matching"
    spec = importlib.util.spec_from_file_location(module_name, SOCIAL_MATCHING_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载匹配模块: {SOCIAL_MATCHING_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


MATCHING_MODULE = _load_matching_module()
