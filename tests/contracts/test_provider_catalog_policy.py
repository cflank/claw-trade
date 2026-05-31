from __future__ import annotations

from pathlib import Path

import pytest

from claw_trade.data_gateway.models import (
    HttpVisibility,
    Market,
    PackDomain,
    PrioritySource,
    ProviderCapability,
    ProviderKind,
    SourceRole,
    source_role_is_discovery_only,
)
from claw_trade.data_gateway.providers.defaults import load_default_system_capabilities
from claw_trade.data_gateway.providers.registry import ProviderRegistry


def _cap(
    *,
    provider: str,
    adapter_id: str,
    source_role: SourceRole,
    data_type: str,
    priority: int,
    priority_source: PrioritySource = PrioritySource.SYSTEM_DEFAULT,
    coverage_group: str | None = None,
) -> ProviderCapability:
    return ProviderCapability(
        provider=provider,
        adapter_id=adapter_id,
        provider_kind=ProviderKind.PROJECT_EXTENSION,
        market=Market.CN_A,
        domain=PackDomain.NEWS,
        endpoint="news",
        source_role=source_role,
        expected_schema_id=f"cn_a.news.{data_type}.v1",
        license_policy_id="personal_research",
        credential_requirements=(),
        rate_limit_policy_id="default",
        cache_ttl_seconds=300,
        required=True,
        attempt_required=True,
        coverage_group=coverage_group,
        coverage_quorum=1 if coverage_group else None,
        priority=priority,
        priority_source=priority_source,
        data_type=data_type,
    )


def test_priority_resolution_is_scoped_inside_same_data_type() -> None:
    registry = ProviderRegistry(
        capabilities=(
            _cap(
                provider="cninfo",
                adapter_id="news.cninfo.cn_a",
                source_role=SourceRole.OFFICIAL_ORIGINAL,
                data_type="official_filing",
                priority=30,
            ),
            _cap(
                provider="eastmoney_news",
                adapter_id="news.eastmoney.cn_a",
                source_role=SourceRole.MARKET_DATA,
                data_type="official_filing",
                priority=0,
            ),
            _cap(
                provider="google_news",
                adapter_id="news.google.cn_a",
                source_role=SourceRole.SEARCH_DISCOVERY,
                data_type="search_discovery",
                priority=0,
            ),
        )
    )

    official_filing = registry.capabilities_for_data_type(
        market=Market.CN_A,
        domain=PackDomain.NEWS,
        data_type="official_filing",
    )
    search_discovery = registry.capabilities_for_data_type(
        market=Market.CN_A,
        domain=PackDomain.NEWS,
        data_type="search_discovery",
    )

    assert [item.adapter_id for item in official_filing] == [
        "news.cninfo.cn_a",
        "news.eastmoney.cn_a",
    ]
    assert [item.adapter_id for item in search_discovery] == ["news.google.cn_a"]


def test_user_preferred_cannot_replace_official_disclosure_source() -> None:
    registry = ProviderRegistry(
        capabilities=(
            _cap(
                provider="cninfo",
                adapter_id="news.cninfo.cn_a",
                source_role=SourceRole.OFFICIAL_ORIGINAL,
                data_type="official_filing",
                priority=99,
            ),
            _cap(
                provider="paid_feed",
                adapter_id="news.paid.cn_a",
                source_role=SourceRole.PAID_DATA,
                data_type="official_filing",
                priority=1,
            ),
        )
    )

    registry.apply_user_preferred(
        adapter_id="news.paid.cn_a",
        market=Market.CN_A,
        domain=PackDomain.NEWS,
        source_role=SourceRole.PAID_DATA,
        coverage_group=None,
        data_type="official_filing",
    )

    ordered = registry.capabilities_for_data_type(
        market=Market.CN_A,
        domain=PackDomain.NEWS,
        data_type="official_filing",
    )
    assert [item.adapter_id for item in ordered] == [
        "news.cninfo.cn_a",
        "news.paid.cn_a",
    ]
    assert ordered[1].priority_source == PrioritySource.USER_PREFERRED


def test_user_preferred_requires_data_type_when_multiple_data_types_match() -> None:
    registry = ProviderRegistry(
        capabilities=(
            _cap(
                provider="paid_feed",
                adapter_id="news.paid.cn_a",
                source_role=SourceRole.PAID_DATA,
                data_type="official_filing",
                priority=1,
            ),
            _cap(
                provider="paid_feed",
                adapter_id="news.paid.cn_a",
                source_role=SourceRole.PAID_DATA,
                data_type="search_discovery",
                priority=1,
            ),
        )
    )

    with pytest.raises(ValueError, match="user_preferred_requires_data_type:news.paid.cn_a"):
        registry.apply_user_preferred(
            adapter_id="news.paid.cn_a",
            market=Market.CN_A,
            domain=PackDomain.NEWS,
            source_role=SourceRole.PAID_DATA,
            coverage_group=None,
        )


def test_discovery_and_event_expectation_are_never_formal_fact_sources() -> None:
    capabilities = load_default_system_capabilities()
    discovery_caps = [item for item in capabilities if source_role_is_discovery_only(item.source_role)]
    assert discovery_caps
    for item in discovery_caps:
        assert item.can_be_formal_fact_source is False
        assert item.can_enter_worker_pack is False


def test_default_http_visibility_keeps_unknown_or_no_http_without_managed_guessing() -> None:
    capabilities = load_default_system_capabilities()
    assert capabilities
    for item in capabilities:
        assert item.http_visibility in {HttpVisibility.NO_HTTP, HttpVisibility.SDK_INTERNAL_UNKNOWN}


def test_default_catalog_keeps_approved_provider_scope_and_data_type_boundary() -> None:
    capabilities = load_default_system_capabilities()
    assert capabilities
    assert all(item.data_type for item in capabilities)
    assert all(item.provider for item in capabilities)
    assert all(item.source_role for item in capabilities)

    provider_ids = {item.provider for item in capabilities}
    assert {"fmp", "polygon", "intrinio"}.isdisjoint(provider_ids)
    assert {"cninfo", "hkexnews", "sec"}.issubset(provider_ids)


def test_provider_catalog_policy_does_not_add_parallel_primary_tables() -> None:
    files = (
        "src/claw_trade/data_gateway/providers/catalog.py",
        "src/claw_trade/data_gateway/providers/registry.py",
        "src/claw_trade/data_gateway/providers/defaults.py",
    )
    forbidden_tokens = (
        "select_data_plans",
        "provider_cooldowns",
        "http_cache",
        "provider_cache",
        "data_gaps",
    )

    for rel in files:
        text = Path(rel).read_text(encoding="utf-8")
        for token in forbidden_tokens:
            assert token not in text
