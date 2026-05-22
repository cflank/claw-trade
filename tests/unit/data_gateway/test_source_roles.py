from __future__ import annotations

from claw_trade.data_gateway.models import (
    PRIMARY_FACT_SOURCE_ROLES,
    DISCOVERY_ONLY_SOURCE_ROLES,
    SourceRole,
    source_role_can_be_primary_fact,
    source_role_is_discovery_only,
)
from claw_trade.data_gateway.providers.news_source_roles import (
    is_news_discovery_role,
    is_news_fact_role,
    is_news_role_allowed,
    is_news_search_discovery_provider,
)
from claw_trade.data_gateway.providers.social_source_roles import (
    is_alternative_me_provider,
    is_polymarket_provider,
    is_social_search_discovery_provider,
    is_social_core_role,
    is_social_role_allowed,
)


def test_source_role_primary_fact_boundary() -> None:
    assert SourceRole.OFFICIAL_ORIGINAL in PRIMARY_FACT_SOURCE_ROLES
    assert SourceRole.MARKET_DATA in PRIMARY_FACT_SOURCE_ROLES
    assert SourceRole.FUNDAMENTAL_DATA in PRIMARY_FACT_SOURCE_ROLES
    assert source_role_can_be_primary_fact(SourceRole.SEARCH_DISCOVERY) is False
    assert source_role_can_be_primary_fact(SourceRole.EVENT_EXPECTATION) is False


def test_source_role_discovery_boundary() -> None:
    assert SourceRole.SEARCH_DISCOVERY in DISCOVERY_ONLY_SOURCE_ROLES
    assert SourceRole.EVENT_EXPECTATION in DISCOVERY_ONLY_SOURCE_ROLES
    assert source_role_is_discovery_only(SourceRole.SEARCH_DISCOVERY) is True
    assert source_role_is_discovery_only(SourceRole.EVENT_EXPECTATION) is True
    assert source_role_is_discovery_only(SourceRole.OFFICIAL_ORIGINAL) is False


def test_news_source_role_boundary() -> None:
    assert is_news_role_allowed(SourceRole.OFFICIAL_ORIGINAL) is True
    assert is_news_role_allowed(SourceRole.MARKET_DATA) is True
    assert is_news_role_allowed(SourceRole.MACRO_DATA) is True
    assert is_news_role_allowed(SourceRole.SEARCH_DISCOVERY) is True
    assert is_news_role_allowed(SourceRole.SOCIAL_ORIGINAL_SAMPLE) is False
    assert is_news_fact_role(SourceRole.OFFICIAL_ORIGINAL) is True
    assert is_news_fact_role(SourceRole.MARKET_DATA) is True
    assert is_news_fact_role(SourceRole.MACRO_DATA) is True
    assert is_news_fact_role(SourceRole.SEARCH_DISCOVERY) is False
    assert is_news_discovery_role(SourceRole.SEARCH_DISCOVERY) is True
    assert is_news_search_discovery_provider("news.search.hk:search_discovery") is True


def test_social_source_role_boundary_and_provider_constraints() -> None:
    assert is_social_role_allowed(SourceRole.SOCIAL_ORIGINAL_SAMPLE) is True
    assert is_social_role_allowed(SourceRole.SOCIAL_AGGREGATE_METRIC) is True
    assert is_social_role_allowed(SourceRole.SEARCH_DISCOVERY) is True
    assert is_social_role_allowed(SourceRole.MACRO_DATA) is False
    assert is_social_core_role(SourceRole.SOCIAL_ORIGINAL_SAMPLE) is True
    assert is_social_core_role(SourceRole.EVENT_EXPECTATION) is False
    assert is_alternative_me_provider("project.alternative_me") is True
    assert is_polymarket_provider("project.polymarket") is True
    assert is_social_search_discovery_provider("social.search.crypto:search_discovery") is True
