from __future__ import annotations

import pytest

from claw_trade.data_gateway.errors import DataGatewayError
from claw_trade.data_gateway.models import (
    AdmissionCheckStatus,
    DeclarativeProviderManifest,
    LicenseCheckResult,
    Market,
    PackDomain,
    PrioritySource,
    ProviderAdmissionStatus,
    ProviderCapability,
    ProviderKind,
    SourceRole,
)
from claw_trade.data_gateway.providers.admission import DeclarativeProviderSecurityPolicy, ProviderAdmissionValidator
from claw_trade.data_gateway.providers.catalog import ProviderCatalog
from claw_trade.data_gateway.providers.declarative import config_version_for_manifest
from claw_trade.data_gateway.providers.license_policy import LicensePolicyStore
from claw_trade.data_gateway.providers.registry import ProviderRegistry
from claw_trade.data_gateway.providers.secrets import SecretStore
from claw_trade.data_gateway.store.manifests import MongoProviderManifestStore


def _cap(
    *,
    adapter_id: str,
    source_role: SourceRole,
    priority: int,
    priority_source: PrioritySource,
    coverage_group: str | None = None,
    data_type: str = "crypto_news_fact",
) -> ProviderCapability:
    return ProviderCapability(
        provider=adapter_id.split(".")[-1],
        adapter_id=adapter_id,
        provider_kind=ProviderKind.PROJECT_EXTENSION,
        market=Market.CRYPTO,
        domain=PackDomain.NEWS,
        endpoint="news",
        source_role=source_role,
        expected_schema_id="news.v1",
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


def _catalog_with_user_manifest() -> ProviderCatalog:
    validator = ProviderAdmissionValidator(
        secret_store=SecretStore({"RSS_TOKEN": "token"}),
        license_store=LicensePolicyStore(
            {
                "user.custom": LicenseCheckResult(
                    status=AdmissionCheckStatus.PASS,
                    license_policy_id="user.custom",
                    cost_tier="free",
                    raw_export_policy="metadata_only",
                    commercial_use_allowed=False,
                    note="ok",
                )
            }
        ),
        security_policy=DeclarativeProviderSecurityPolicy(
            allowed_domains=("feeds.example.com",),
            dns_resolver=lambda host: ("93.184.216.34",),
        ),
        sample_ref_exists=lambda ref: ref.startswith("mongo://openbb_"),
    )
    catalog = ProviderCatalog(validator=validator)
    draft = DeclarativeProviderManifest(
        provider_id="custom_news",
        adapter_id="user.crypto.news",
        display_name="Custom News",
        version="1",
        config_version="",
        markets=(Market.CRYPTO,),
        domains=(PackDomain.NEWS,),
        endpoints=("rss_items",),
        source_role=SourceRole.SEARCH_DISCOVERY,
        expected_schema_id="news.discovery.v1",
        base_url="https://feeds.example.com",
        request_template={"path": "/v1"},
        response_mapping={"title": "headline"},
        credential_requirements=("RSS_TOKEN",),
        rate_limit_policy_id="user.custom.default",
        cache_ttl_seconds=300,
        license_policy_id="user.custom",
        raw_export_policy="metadata_only",
        healthcheck={
            "method": "GET",
            "path": "/health",
            "sample_raw_ref": "mongo://openbb_raw_payloads/registry",
            "sample_normalized_ref": "mongo://openbb_normalized/registry",
        },
        enabled=True,
        admission_status=ProviderAdmissionStatus.DRAFT,
        priority=10,
        priority_source=PrioritySource.USER_PREFERRED,
        coverage_group="crypto_news_discovery",
        coverage_quorum=1,
    )
    manifest = DeclarativeProviderManifest(**{**draft.__dict__, "config_version": config_version_for_manifest(draft)})
    catalog.validate_manifest(manifest, actor="user:test", reason="create")
    return catalog


class _FakeManifestCollection:
    def __init__(self, docs: tuple[dict[str, object], ...]) -> None:
        self._docs = docs

    def find(self, query: dict[str, object]) -> tuple[dict[str, object], ...]:
        del query
        return self._docs


def test_user_preferred_priority_only_reorders_within_same_role_and_group() -> None:
    registry = ProviderRegistry(
        capabilities=(
            _cap(
                adapter_id="system.official",
                source_role=SourceRole.OFFICIAL_ORIGINAL,
                priority=1,
                priority_source=PrioritySource.SYSTEM_DEFAULT,
            ),
            _cap(
                adapter_id="system.discovery",
                source_role=SourceRole.SEARCH_DISCOVERY,
                priority=1,
                priority_source=PrioritySource.SYSTEM_DEFAULT,
                coverage_group="crypto_news_discovery",
            ),
            _cap(
                adapter_id="user.discovery",
                source_role=SourceRole.SEARCH_DISCOVERY,
                priority=5,
                priority_source=PrioritySource.USER_PREFERRED,
                coverage_group="crypto_news_discovery",
            ),
            _cap(
                adapter_id="user.discovery.other_group",
                source_role=SourceRole.SEARCH_DISCOVERY,
                priority=0,
                priority_source=PrioritySource.USER_PREFERRED,
                coverage_group="other_group",
            ),
        )
    )

    ordered = registry.capabilities_for(market=Market.CRYPTO, domain=PackDomain.NEWS)
    ordered_ids = [item.adapter_id for item in ordered]
    assert ordered_ids[0] == "system.official"
    assert ordered_ids[1:3] == ["user.discovery", "system.discovery"]


def test_apply_user_preferred_does_not_cross_official_original_boundary() -> None:
    registry = ProviderRegistry(
        capabilities=(
            _cap(
                adapter_id="system.official",
                source_role=SourceRole.OFFICIAL_ORIGINAL,
                priority=10,
                priority_source=PrioritySource.SYSTEM_DEFAULT,
            ),
            _cap(
                adapter_id="user.market",
                source_role=SourceRole.MARKET_DATA,
                priority=1,
                priority_source=PrioritySource.SYSTEM_DEFAULT,
            ),
        )
    )
    registry.apply_user_preferred(
        adapter_id="user.market",
        market=Market.CRYPTO,
        domain=PackDomain.NEWS,
        source_role=SourceRole.MARKET_DATA,
        coverage_group=None,
    )
    ordered = registry.capabilities_for(market=Market.CRYPTO, domain=PackDomain.NEWS)
    assert ordered[0].adapter_id == "system.official"
    assert ordered[1].priority_source == PrioritySource.USER_PREFERRED


def test_priority_is_resolved_within_same_data_type_only() -> None:
    registry = ProviderRegistry(
        capabilities=(
            _cap(
                adapter_id="official.news",
                source_role=SourceRole.OFFICIAL_ORIGINAL,
                priority=10,
                priority_source=PrioritySource.SYSTEM_DEFAULT,
                data_type="official_filing",
            ),
            _cap(
                adapter_id="market.news",
                source_role=SourceRole.MARKET_DATA,
                priority=0,
                priority_source=PrioritySource.SYSTEM_DEFAULT,
                data_type="official_filing",
            ),
            _cap(
                adapter_id="discovery.news",
                source_role=SourceRole.SEARCH_DISCOVERY,
                priority=0,
                priority_source=PrioritySource.SYSTEM_DEFAULT,
                data_type="news_discovery",
            ),
        )
    )

    official_filing = registry.capabilities_for_data_type(
        market=Market.CRYPTO,
        domain=PackDomain.NEWS,
        data_type="official_filing",
    )
    assert [item.adapter_id for item in official_filing] == ["official.news", "market.news"]

    news_discovery = registry.capabilities_for_data_type(
        market=Market.CRYPTO,
        domain=PackDomain.NEWS,
        data_type="news_discovery",
    )
    assert [item.adapter_id for item in news_discovery] == ["discovery.news"]


def test_apply_user_preferred_without_data_type_fails_when_multiple_data_types_match() -> None:
    registry = ProviderRegistry(
        capabilities=(
            _cap(
                adapter_id="user.same",
                source_role=SourceRole.MARKET_DATA,
                priority=5,
                priority_source=PrioritySource.SYSTEM_DEFAULT,
                data_type="official_filing",
            ),
            _cap(
                adapter_id="user.same",
                source_role=SourceRole.MARKET_DATA,
                priority=5,
                priority_source=PrioritySource.SYSTEM_DEFAULT,
                data_type="news_discovery",
            ),
        )
    )

    with pytest.raises(ValueError, match="user_preferred_requires_data_type:user.same"):
        registry.apply_user_preferred(
            adapter_id="user.same",
            market=Market.CRYPTO,
            domain=PackDomain.NEWS,
            source_role=SourceRole.MARKET_DATA,
            coverage_group=None,
        )


def test_registry_loads_only_enabled_candidates_from_catalog() -> None:
    catalog = _catalog_with_user_manifest()
    registry = ProviderRegistry.from_catalog(catalog)

    candidates = catalog.enabled_candidates()
    assert len(candidates) == 1
    caps = registry.capabilities_for(market=Market.CRYPTO, domain=PackDomain.NEWS)
    assert [item.adapter_id for item in caps] == [candidates[0].adapter_id]


def test_registry_from_enabled_manifests_matches_catalog_enabled_candidates() -> None:
    catalog = _catalog_with_user_manifest()
    expected = ProviderRegistry.from_catalog(catalog)
    actual = ProviderRegistry.from_enabled_manifests(enabled_manifests=catalog.enabled_candidates())

    expected_ids = [item.adapter_id for item in expected.capabilities_for(market=Market.CRYPTO, domain=PackDomain.NEWS)]
    actual_ids = [item.adapter_id for item in actual.capabilities_for(market=Market.CRYPTO, domain=PackDomain.NEWS)]
    assert actual_ids == expected_ids


def test_manifest_store_enabled_candidates_decode_failure_is_not_silently_dropped() -> None:
    bad_doc = {
        "_id": "user.bad.manifest",
        "adapter_id": "user.bad.manifest",
        "provider_id": "bad_provider",
        "display_name": "Bad Provider",
        "version": "1",
        "config_version": "cfg://bad",
        "markets": [Market.CN_A.value],
        "domains": [PackDomain.MARKET.value],
        "endpoints": [],
    }
    good_doc = {
        "_id": "user.good.manifest",
        "provider_id": "good_provider",
        "adapter_id": "user.good.manifest",
        "display_name": "Good Provider",
        "version": "1",
        "config_version": "cfg://good",
        "markets": [Market.CN_A.value],
        "domains": [PackDomain.MARKET.value],
        "endpoints": ["stock_zh_a_spot_em_batch"],
        "source_role": SourceRole.MARKET_DATA.value,
        "expected_schema_id": "cn_a.selection.batch.v1",
        "base_url": "https://example.com",
        "request_template": {"path": "/selection"},
        "response_mapping": {"rows": "rows"},
        "credential_requirements": [],
        "rate_limit_policy_id": "user.good.default",
        "cache_ttl_seconds": 900,
        "license_policy_id": "personal_research",
        "raw_export_policy": "metadata_only",
        "healthcheck": {"method": "GET", "path": "/health"},
        "enabled": True,
        "admission_status": ProviderAdmissionStatus.ENABLED_CANDIDATE.value,
        "priority": 1,
        "priority_source": PrioritySource.USER_PREFERRED.value,
        "coverage_group": "cn_a_selection_batch",
        "coverage_quorum": 1,
    }
    store = MongoProviderManifestStore(_FakeManifestCollection((bad_doc, good_doc)))

    with pytest.raises(DataGatewayError) as excinfo:
        store.enabled_candidates(
            market=Market.CN_A,
            domain=PackDomain.MARKET,
            endpoint="stock_zh_a_spot_em_batch",
        )

    assert "_id=user.bad.manifest" in str(excinfo.value)
    assert "provider manifest decode failed" in str(excinfo.value)
