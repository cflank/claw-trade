from __future__ import annotations

from claw_trade.data_gateway.models import SourceRole

SOCIAL_ALLOWED_SOURCE_ROLES = frozenset(
    {
        SourceRole.SOCIAL_ORIGINAL_SAMPLE,
        SourceRole.SOCIAL_AGGREGATE_METRIC,
        SourceRole.SEARCH_DISCOVERY,
        SourceRole.EVENT_EXPECTATION,
    }
)

SOCIAL_CORE_SOURCE_ROLES = frozenset(
    {
        SourceRole.SOCIAL_ORIGINAL_SAMPLE,
        SourceRole.SOCIAL_AGGREGATE_METRIC,
    }
)

SOCIAL_SEARCH_DISCOVERY_PROVIDER_TOKENS = frozenset({"bocha", "tavily", "jina", "google", "minimax", "search"})


def is_social_role_allowed(role: SourceRole) -> bool:
    return role in SOCIAL_ALLOWED_SOURCE_ROLES


def is_social_core_role(role: SourceRole) -> bool:
    return role in SOCIAL_CORE_SOURCE_ROLES


def is_alternative_me_provider(provider_hint: str) -> bool:
    return "alternative" in provider_hint.lower()


def is_polymarket_provider(provider_hint: str) -> bool:
    return "polymarket" in provider_hint.lower()


def is_social_search_discovery_provider(provider_hint: str) -> bool:
    normalized = provider_hint.lower()
    for sep in (":", ".", "-", "/"):
        normalized = normalized.replace(sep, "_")
    return bool(SOCIAL_SEARCH_DISCOVERY_PROVIDER_TOKENS.intersection(token for token in normalized.split("_") if token))
