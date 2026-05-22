from __future__ import annotations

from claw_trade.data_gateway.models import (
    AdmissionCheckStatus,
    DeclarativeProviderManifest,
    LicenseCheckResult,
    Market,
    PackDomain,
    PrioritySource,
    ProviderAdmissionStatus,
    SourceRole,
)
from claw_trade.data_gateway.providers.admission import DeclarativeProviderSecurityPolicy, ProviderAdmissionValidator
from claw_trade.data_gateway.providers.catalog import ProviderCatalog
from claw_trade.data_gateway.providers.declarative import config_version_for_manifest
from claw_trade.data_gateway.providers.license_policy import LicensePolicyStore
from claw_trade.data_gateway.providers.registry import ProviderRegistry
from claw_trade.data_gateway.providers.secrets import SecretStore


def _manifest(*, adapter_id: str, enabled: bool, admission_status: ProviderAdmissionStatus) -> DeclarativeProviderManifest:
    draft = DeclarativeProviderManifest(
        provider_id=adapter_id.replace(".", "_"),
        adapter_id=adapter_id,
        display_name=adapter_id,
        version="1",
        config_version="",
        markets=(Market.CRYPTO,),
        domains=(PackDomain.NEWS,),
        endpoints=("rss_items",),
        source_role=SourceRole.SEARCH_DISCOVERY,
        expected_schema_id="news.discovery.v1",
        base_url="https://example.com",
        request_template={"path": "/v1/news"},
        response_mapping={"title": "headline", "url": "source_url"},
        credential_requirements=("RSS_TOKEN",),
        rate_limit_policy_id="user.custom.default",
        cache_ttl_seconds=300,
        license_policy_id="user.custom",
        raw_export_policy="metadata_only",
        healthcheck={
            "method": "GET",
            "path": "/health",
            "sample_raw_ref": "mongo://openbb_raw_payloads/integration",
            "sample_normalized_ref": "mongo://openbb_normalized/integration",
        },
        enabled=enabled,
        admission_status=admission_status,
        priority=20,
        priority_source=PrioritySource.USER_PREFERRED,
        coverage_group="crypto_news_discovery",
        coverage_quorum=1,
    )
    return DeclarativeProviderManifest(**{**draft.__dict__, "config_version": config_version_for_manifest(draft)})


def test_declarative_provider_only_enters_registry_after_validation_and_enable() -> None:
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
            allowed_domains=("example.com",),
            dns_resolver=lambda host: ("93.184.216.34",),
        ),
        sample_ref_exists=lambda ref: ref.startswith("mongo://openbb_"),
    )
    catalog = ProviderCatalog(validator=validator)
    valid_manifest = _manifest(
        adapter_id="user.crypto.news.valid",
        enabled=True,
        admission_status=ProviderAdmissionStatus.DRAFT,
    )
    draft_manifest = _manifest(
        adapter_id="user.crypto.news.draft",
        enabled=True,
        admission_status=ProviderAdmissionStatus.DRAFT,
    )

    catalog.upsert_manifest(draft_manifest)
    receipt = catalog.validate_manifest(valid_manifest, actor="user:test", reason="integration")

    registry = ProviderRegistry.from_catalog(catalog)
    capabilities = registry.capabilities_for(market=Market.CRYPTO, domain=PackDomain.NEWS)

    assert receipt.status == ProviderAdmissionStatus.ENABLED_CANDIDATE
    assert [item.adapter_id for item in capabilities] == ["user.crypto.news.valid"]
    assert [item.adapter_id for item in catalog.enabled_candidates()] == ["user.crypto.news.valid"]
