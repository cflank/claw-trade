from __future__ import annotations

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


def _cap(
    *,
    adapter_id: str,
    source_role: SourceRole,
    priority: int,
    priority_source: PrioritySource,
    coverage_group: str | None = None,
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


def test_registry_loads_only_enabled_candidates_from_catalog() -> None:
    catalog = _catalog_with_user_manifest()
    registry = ProviderRegistry.from_catalog(catalog)

    candidates = catalog.enabled_candidates()
    assert len(candidates) == 1
    caps = registry.capabilities_for(market=Market.CRYPTO, domain=PackDomain.NEWS)
    assert [item.adapter_id for item in caps] == [candidates[0].adapter_id]
