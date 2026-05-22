from __future__ import annotations

from claw_trade.data_gateway.models import SourceRole

NEWS_ALLOWED_SOURCE_ROLES = frozenset(
    {
        SourceRole.OFFICIAL_ORIGINAL,
        SourceRole.MARKET_DATA,
        SourceRole.MACRO_DATA,
        SourceRole.SEARCH_DISCOVERY,
        SourceRole.EVENT_EXPECTATION,
    }
)

NEWS_FACT_SOURCE_ROLES = frozenset(
    {
        SourceRole.OFFICIAL_ORIGINAL,
        SourceRole.MARKET_DATA,
        SourceRole.MACRO_DATA,
    }
)

NEWS_SEARCH_DISCOVERY_PROVIDER_TOKENS = frozenset({"bocha", "tavily", "jina", "google", "minimax", "search"})


def is_news_role_allowed(role: SourceRole) -> bool:
    return role in NEWS_ALLOWED_SOURCE_ROLES


def is_news_fact_role(role: SourceRole) -> bool:
    return role in NEWS_FACT_SOURCE_ROLES


def is_news_discovery_role(role: SourceRole) -> bool:
    return role == SourceRole.SEARCH_DISCOVERY


def is_news_search_discovery_provider(provider_hint: str) -> bool:
    normalized = provider_hint.lower()
    for sep in (":", ".", "-", "/"):
        normalized = normalized.replace(sep, "_")
    return bool(NEWS_SEARCH_DISCOVERY_PROVIDER_TOKENS.intersection(token for token in normalized.split("_") if token))
