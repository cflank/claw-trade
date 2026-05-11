from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .config import FrontlineProviderConfig
from .errors import PROVIDER_NOT_APPROVED, FrontlineValidationError
from .models import Domain, ProviderMode, ProviderPriority, ProviderSpec


@dataclass(frozen=True)
class _ProviderTemplate:
    priority: ProviderPriority
    provider: str
    endpoint: str
    role: str
    mode: ProviderMode
    required_for_complete: bool


_MARKET_PROVIDER_TEMPLATES: tuple[_ProviderTemplate, ...] = (
    _ProviderTemplate("P0", "mongodb", "fresh_normalized_ohlcv_cache", "p0_price_cache", "cache", False),
    _ProviderTemplate("P0", "tushare", "pro_bar", "p0_price_history", "remote", True),
    _ProviderTemplate("P1", "akshare", "stock_zh_a_hist", "p1_price_history", "remote", True),
    _ProviderTemplate("P1", "eastmoney_direct", "push2his_kline", "p1_price_history_backup", "remote", False),
    _ProviderTemplate("P1", "sina", "stock_zh_a_daily", "p1_price_history", "remote", False),
    _ProviderTemplate("P1", "tencent", "stock_zh_a_hist_tx", "p1_price_history", "remote", False),
    _ProviderTemplate("P2", "baostock", "daily_history_quotes", "p2_candidate", "remote", False),
    _ProviderTemplate("P2", "efinance", "history_quotes", "p2_candidate", "remote", False),
)

_NEWS_PROVIDER_TEMPLATES: tuple[_ProviderTemplate, ...] = (
    _ProviderTemplate("P0", "akshare", "stock_news_em", "company_news", "remote", True),
    _ProviderTemplate("P0", "akshare", "stock_info_global_cls", "macro_flash", "remote", False),
    _ProviderTemplate("P1", "akshare", "stock_info_global_em", "macro_flash", "remote", False),
    _ProviderTemplate("P1", "akshare", "news_cctv", "macro_background", "remote", False),
    _ProviderTemplate("P1", "bocha", "cn_web_search", "search_company_news", "remote", False),
    _ProviderTemplate("P1", "tavily", "news_web_search", "search_company_news", "remote", False),
    _ProviderTemplate("P1", "jina", "search_and_reader", "search_and_read", "remote", False),
    _ProviderTemplate("P1", "newsnow", "hot_topics_aggregator", "hot_topics", "remote", False),
    _ProviderTemplate("P2", "minimax", "structured_search", "structured_search", "remote", False),
    _ProviderTemplate("P2", "tushare", "anns_d", "announcement", "remote", False),
)

_SOCIAL_PROVIDER_TEMPLATES: tuple[_ProviderTemplate, ...] = (
    _ProviderTemplate("P0", "eastmoney_akshare", "hot_rank_latest", "target_attention", "remote", True),
    _ProviderTemplate("P0", "eastmoney_akshare", "hot_keyword", "target_keyword", "remote", True),
    _ProviderTemplate("P0", "eastmoney_akshare", "related_hot_rank", "related_symbols", "remote", True),
    _ProviderTemplate("P1", "eastmoney_direct", "full_hot_rank_board", "board_check", "remote", False),
    _ProviderTemplate("P1", "bocha", "public_page_search", "search_public_pages", "remote", False),
    _ProviderTemplate("P1", "jina", "public_page_search", "search_public_pages", "remote", False),
    _ProviderTemplate("P1", "tavily", "public_page_search", "search_public_pages", "remote", False),
    _ProviderTemplate("P1", "alphaear_news_source_list", "market_background", "market_background", "remote", False),
    _ProviderTemplate("P2", "xueqiu_guba", "post_level_sentiment", "post_level_sentiment", "remote", False),
)

_FUNDAMENTAL_PROVIDER_TEMPLATES: tuple[_ProviderTemplate, ...] = (
    _ProviderTemplate("P0", "mongodb", "fresh_cache", "fundamental_cache", "cache", False),
    _ProviderTemplate("P0", "tushare", "stock_basic", "company_profile", "remote", True),
    _ProviderTemplate("P0", "tushare", "daily_basic", "valuation_market_cap", "remote", True),
    _ProviderTemplate("P0", "tushare", "fina_indicator", "financial_indicators", "remote", True),
    _ProviderTemplate("P0", "tushare", "income", "income_statement", "remote", True),
    _ProviderTemplate("P0", "tushare", "cashflow", "cash_flow", "remote", False),
    _ProviderTemplate("P1", "akshare", "company_info", "company_profile_backup", "remote", True),
    _ProviderTemplate("P1", "akshare", "financial_abstract", "financial_summary_backup", "remote", True),
    _ProviderTemplate("P1", "akshare", "stock_zh_a_spot_em", "valuation_market_cap_backup", "remote", True),
    _ProviderTemplate(
        "P1",
        "eastmoney_direct",
        "quote_valuation_snapshot",
        "valuation_market_cap_backup",
        "remote",
        False,
    ),
    _ProviderTemplate("P1", "baostock", "candidate_financials", "candidate_financials", "remote", False),
    _ProviderTemplate("P2", "efinance", "candidate_enrichment", "candidate_enrichment", "remote", False),
)

_TEMPLATES_BY_DOMAIN: dict[Domain, tuple[_ProviderTemplate, ...]] = {
    "market": _MARKET_PROVIDER_TEMPLATES,
    "news": _NEWS_PROVIDER_TEMPLATES,
    "social": _SOCIAL_PROVIDER_TEMPLATES,
    "fundamental": _FUNDAMENTAL_PROVIDER_TEMPLATES,
}

_ENABLE_RULES: dict[tuple[Domain, str], Callable[[FrontlineProviderConfig], bool]] = {
    ("market", "mongodb"): lambda _: True,
    ("market", "akshare"): lambda cfg: cfg.provider_flags.market_enable_akshare,
    ("market", "eastmoney_direct"): lambda cfg: cfg.provider_flags.market_enable_eastmoney_direct,
    ("market", "sina"): lambda cfg: cfg.provider_flags.market_enable_sina,
    ("market", "tencent"): lambda cfg: cfg.provider_flags.market_enable_tencent,
    ("market", "baostock"): lambda cfg: cfg.provider_flags.market_enable_baostock,
    ("market", "efinance"): lambda cfg: cfg.provider_flags.market_enable_efinance,
    ("market", "tushare"): lambda cfg: cfg.provider_flags.market_enable_tushare,
    ("news", "akshare"): lambda _: True,
    ("news", "bocha"): lambda cfg: cfg.provider_flags.news_enable_bocha,
    ("news", "tavily"): lambda cfg: cfg.provider_flags.news_enable_tavily,
    ("news", "jina"): lambda cfg: cfg.provider_flags.news_enable_jina,
    ("news", "newsnow"): lambda cfg: cfg.provider_flags.news_enable_newsnow,
    ("news", "minimax"): lambda cfg: cfg.provider_flags.news_enable_minimax,
    ("news", "tushare"): lambda _: True,
    ("social", "eastmoney_akshare"): lambda _: True,
    ("social", "eastmoney_direct"): lambda _: True,
    ("social", "bocha"): lambda _: True,
    ("social", "jina"): lambda _: True,
    ("social", "tavily"): lambda _: True,
    ("social", "alphaear_news_source_list"): lambda _: True,
    ("social", "xueqiu_guba"): lambda _: True,
    ("fundamental", "mongodb"): lambda _: True,
    ("fundamental", "akshare"): lambda _: True,
    ("fundamental", "eastmoney_direct"): lambda _: True,
    ("fundamental", "baostock"): lambda _: True,
    ("fundamental", "efinance"): lambda _: True,
    ("fundamental", "tushare"): lambda cfg: cfg.provider_flags.fundamental_enable_tushare
    and not cfg.provider_flags.fundamental_disable_tushare,
}


def load_market_provider_specs(config: FrontlineProviderConfig) -> list[ProviderSpec]:
    return _load_provider_specs("market", config)


def load_news_provider_specs(config: FrontlineProviderConfig) -> list[ProviderSpec]:
    return _load_provider_specs("news", config)


def load_social_provider_specs(config: FrontlineProviderConfig) -> list[ProviderSpec]:
    return _load_provider_specs("social", config)


def load_fundamental_provider_specs(config: FrontlineProviderConfig) -> list[ProviderSpec]:
    return _load_provider_specs("fundamental", config)


def resolve_provider_enabled(
    *,
    domain: Domain,
    provider: str,
    config: FrontlineProviderConfig,
) -> bool:
    assert_provider_approved(domain=domain, provider=provider)
    resolver = _ENABLE_RULES[(domain, provider)]
    return resolver(config)


def is_provider_approved(*, domain: Domain, provider: str) -> bool:
    return (domain, provider) in _ENABLE_RULES


def assert_provider_approved(*, domain: Domain, provider: str) -> None:
    if not is_provider_approved(domain=domain, provider=provider):
        raise FrontlineValidationError(
            PROVIDER_NOT_APPROVED,
            f"domain={domain} provider={provider} 未在批准矩阵中",
        )


def _load_provider_specs(domain: Domain, config: FrontlineProviderConfig) -> list[ProviderSpec]:
    templates = _TEMPLATES_BY_DOMAIN[domain]
    specs: list[ProviderSpec] = []
    for template in templates:
        specs.append(
            ProviderSpec(
                domain=domain,
                priority=template.priority,
                provider=template.provider,
                endpoint=template.endpoint,
                role=template.role,
                enabled=resolve_provider_enabled(domain=domain, provider=template.provider, config=config),
                mode=template.mode,
                timeout_ms=config.provider_runtime.default_timeout_ms,
                required_for_complete=template.required_for_complete,
                query_parameters=[],
            )
        )
    return specs


__all__ = [
    "assert_provider_approved",
    "is_provider_approved",
    "load_fundamental_provider_specs",
    "load_market_provider_specs",
    "load_news_provider_specs",
    "load_social_provider_specs",
    "resolve_provider_enabled",
]
