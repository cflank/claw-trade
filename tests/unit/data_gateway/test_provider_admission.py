from __future__ import annotations

from claw_trade.data_gateway.models import (
    AdmissionCheckStatus,
    DeclarativeProviderManifest,
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
from claw_trade.data_gateway.providers.secrets import SecretStore
from claw_trade.data_gateway.models import LicenseCheckResult


def _manifest(*, adapter_id: str = "user.crypto.news", enabled: bool = True) -> DeclarativeProviderManifest:
    draft = DeclarativeProviderManifest(
        provider_id="custom_news",
        adapter_id=adapter_id,
        display_name="Custom News",
        version="1",
        config_version="",
        markets=(Market.CRYPTO,),
        domains=(PackDomain.NEWS,),
        endpoints=("rss_items",),
        source_role=SourceRole.SEARCH_DISCOVERY,
        expected_schema_id="news.discovery.v1",
        base_url="https://example.com",
        request_template={"path": "/v1/items", "query": {"ticker": "{ticker}"}},
        response_mapping={"title": "headline", "url": "source_url"},
        credential_requirements=("RSS_TOKEN",),
        rate_limit_policy_id="user.custom.default",
        cache_ttl_seconds=300,
        license_policy_id="user.custom",
        raw_export_policy="metadata_only",
        healthcheck={
            "method": "GET",
            "path": "/health",
            "sample_raw_ref": "mongo://openbb_raw_payloads/sample",
            "sample_normalized_ref": "mongo://openbb_normalized/sample",
        },
        enabled=enabled,
        admission_status=ProviderAdmissionStatus.DRAFT,
        priority=20,
        priority_source=PrioritySource.SYSTEM_DEFAULT,
        coverage_group="crypto_news_discovery",
        coverage_quorum=1,
    )
    return DeclarativeProviderManifest(**{**draft.__dict__, "config_version": config_version_for_manifest(draft)})


def _catalog(*, secrets: dict[str, str] | None = None) -> ProviderCatalog:
    secret_store = SecretStore({"RSS_TOKEN": "token-value"} if secrets is None else secrets)
    license_store = LicensePolicyStore(
        {
            "user.custom": LicenseCheckResult(
                status=AdmissionCheckStatus.PASS,
                license_policy_id="user.custom",
                cost_tier="free",
                raw_export_policy="metadata_only",
                commercial_use_allowed=False,
                note="personal research only",
            )
        }
    )
    validator = ProviderAdmissionValidator(
        secret_store=secret_store,
        license_store=license_store,
        security_policy=DeclarativeProviderSecurityPolicy(
            allowed_domains=("example.com", "*.trusted.news"),
            dns_resolver=lambda host: ("93.184.216.34",),
        ),
        sample_ref_exists=lambda ref: ref.startswith("mongo://openbb_"),
    )
    return ProviderCatalog(validator=validator)


def _catalog_with_license_result(license_result: LicenseCheckResult, *, secrets: dict[str, str] | None = None) -> ProviderCatalog:
    secret_store = SecretStore({"RSS_TOKEN": "token-value"} if secrets is None else secrets)
    license_store = LicensePolicyStore({"user.custom": license_result})
    validator = ProviderAdmissionValidator(
        secret_store=secret_store,
        license_store=license_store,
        security_policy=DeclarativeProviderSecurityPolicy(
            allowed_domains=("example.com", "*.trusted.news"),
            dns_resolver=lambda host: ("93.184.216.34",),
        ),
        sample_ref_exists=lambda ref: ref.startswith("mongo://openbb_"),
    )
    return ProviderCatalog(validator=validator)


def test_manifest_validation_transitions_to_enabled_candidate_with_receipts() -> None:
    catalog = _catalog()
    manifest = _manifest()

    receipt = catalog.validate_manifest(manifest, actor="user:test", reason="create")
    history = catalog.receipts_for(manifest.adapter_id)

    assert receipt.status == ProviderAdmissionStatus.ENABLED_CANDIDATE
    assert [item.status for item in history] == [
        ProviderAdmissionStatus.VALIDATING,
        ProviderAdmissionStatus.VALIDATED,
        ProviderAdmissionStatus.ENABLED_CANDIDATE,
    ]
    assert catalog.enabled_candidates()[0].adapter_id == manifest.adapter_id


def test_credential_missing_rejected_and_not_in_enabled_candidates() -> None:
    catalog = _catalog(secrets={})
    manifest = _manifest(adapter_id="user.crypto.news.missing")

    receipt = catalog.validate_manifest(manifest, actor="user:test", reason="missing_secret")

    assert receipt.status == ProviderAdmissionStatus.REJECTED
    assert receipt.credential_status == AdmissionCheckStatus.MISSING
    assert catalog.enabled_candidates() == ()


def test_disabled_candidate_filtered_out() -> None:
    catalog = _catalog()
    manifest = _manifest(adapter_id="user.crypto.news.disable")
    catalog.validate_manifest(manifest, actor="user:test", reason="create")
    catalog.set_enabled(
        manifest.adapter_id,
        enabled=False,
        actor="user:test",
        reason="manual_disable",
    )
    assert catalog.enabled_candidates() == ()


def test_code_like_provider_rejected() -> None:
    catalog = _catalog()
    manifest = _manifest(adapter_id="user.crypto.news.code")
    manifest = DeclarativeProviderManifest(
        **{**manifest.__dict__, "request_template": {"python_module": "evil.module", "path": "/v1"}}
    )
    manifest = DeclarativeProviderManifest(**{**manifest.__dict__, "config_version": config_version_for_manifest(manifest)})

    receipt = catalog.validate_manifest(manifest, actor="user:test", reason="code")
    assert receipt.status == ProviderAdmissionStatus.REJECTED
    assert "code_provider_not_allowed" in receipt.errors


def test_quarantined_must_revalidate_before_enabled_again() -> None:
    catalog = _catalog()
    manifest = _manifest(adapter_id="user.crypto.news.revalidate")
    bad = DeclarativeProviderManifest(**{**manifest.__dict__, "base_url": "https://127.0.0.1"})
    bad = DeclarativeProviderManifest(**{**bad.__dict__, "config_version": config_version_for_manifest(bad)})

    quarantine = catalog.validate_manifest(bad, actor="user:test", reason="bad_url")
    assert quarantine.status == ProviderAdmissionStatus.QUARANTINED
    assert catalog.enabled_candidates() == ()

    fixed = DeclarativeProviderManifest(**{**manifest.__dict__, "adapter_id": "user.crypto.news.revalidate"})
    fixed = DeclarativeProviderManifest(**{**fixed.__dict__, "config_version": config_version_for_manifest(fixed)})
    final = catalog.validate_manifest(fixed, actor="user:test", reason="fixed")
    assert final.status == ProviderAdmissionStatus.ENABLED_CANDIDATE


def test_user_manifest_cannot_declare_official_original() -> None:
    catalog = _catalog()
    manifest = _manifest(adapter_id="user.crypto.news.official")
    manifest = DeclarativeProviderManifest(
        **{
            **manifest.__dict__,
            "source_role": SourceRole.OFFICIAL_ORIGINAL,
            "config_version": "",
        }
    )
    manifest = DeclarativeProviderManifest(**{**manifest.__dict__, "config_version": config_version_for_manifest(manifest)})

    receipt = catalog.validate_manifest(manifest, actor="user:test", reason="official")

    assert receipt.status == ProviderAdmissionStatus.REJECTED
    assert "source_role_official_original_forbidden_for_user_provider" in receipt.errors
    assert catalog.enabled_candidates() == ()


def test_manifest_without_real_sample_refs_is_rejected() -> None:
    catalog = _catalog()
    manifest = _manifest(adapter_id="user.crypto.news.no-sample")
    manifest = DeclarativeProviderManifest(
        **{
            **manifest.__dict__,
            "healthcheck": {"method": "GET", "path": "/health"},
            "config_version": "",
        }
    )
    manifest = DeclarativeProviderManifest(**{**manifest.__dict__, "config_version": config_version_for_manifest(manifest)})

    receipt = catalog.validate_manifest(manifest, actor="user:test", reason="missing_sample")

    assert receipt.status == ProviderAdmissionStatus.REJECTED
    assert "healthcheck_sample_raw_ref_missing_or_invalid" in receipt.errors
    assert "healthcheck_sample_normalized_ref_missing_or_invalid" in receipt.errors


def test_manifest_with_nonexistent_sample_refs_is_rejected() -> None:
    secret_store = SecretStore({"RSS_TOKEN": "token-value"})
    license_store = LicensePolicyStore(
        {
            "user.custom": LicenseCheckResult(
                status=AdmissionCheckStatus.PASS,
                license_policy_id="user.custom",
                cost_tier="free",
                raw_export_policy="metadata_only",
                commercial_use_allowed=False,
                note="personal research only",
            )
        }
    )
    validator = ProviderAdmissionValidator(
        secret_store=secret_store,
        license_store=license_store,
        security_policy=DeclarativeProviderSecurityPolicy(
            allowed_domains=("example.com", "*.trusted.news"),
            dns_resolver=lambda host: ("93.184.216.34",),
        ),
        sample_ref_exists=lambda ref: False,
    )
    catalog = ProviderCatalog(validator=validator)
    manifest = _manifest(adapter_id="user.crypto.news.sample-not-found")

    receipt = catalog.validate_manifest(manifest, actor="user:test", reason="sample_not_found")

    assert receipt.status == ProviderAdmissionStatus.REJECTED
    assert "healthcheck_sample_raw_ref_not_found" in receipt.errors
    assert "healthcheck_sample_normalized_ref_not_found" in receipt.errors


def test_license_policy_missing_is_rejected_and_not_candidate() -> None:
    catalog = _catalog()
    manifest = _manifest(adapter_id="user.crypto.news.license-missing")
    manifest = DeclarativeProviderManifest(
        **{
            **manifest.__dict__,
            "license_policy_id": "missing.policy",
            "config_version": "",
        }
    )
    manifest = DeclarativeProviderManifest(**{**manifest.__dict__, "config_version": config_version_for_manifest(manifest)})

    receipt = catalog.validate_manifest(manifest, actor="user:test", reason="license_missing")

    assert receipt.status == ProviderAdmissionStatus.REJECTED
    assert receipt.license_status == AdmissionCheckStatus.MISSING
    assert "license_policy_missing:missing.policy" in receipt.errors
    assert catalog.enabled_candidates() == ()


def test_license_policy_blocked_is_rejected_and_not_candidate() -> None:
    catalog = _catalog_with_license_result(
        LicenseCheckResult(
            status=AdmissionCheckStatus.BLOCKED,
            license_policy_id="user.custom",
            cost_tier="paid",
            raw_export_policy="metadata_only",
            commercial_use_allowed=False,
            note="blocked",
        )
    )
    manifest = _manifest(adapter_id="user.crypto.news.license-blocked")

    receipt = catalog.validate_manifest(manifest, actor="user:test", reason="license_blocked")

    assert receipt.status == ProviderAdmissionStatus.REJECTED
    assert receipt.license_status == AdmissionCheckStatus.BLOCKED
    assert "license_policy_blocked:user.custom" in receipt.errors
    assert catalog.enabled_candidates() == ()


def test_raw_export_policy_not_allowed_is_rejected_and_not_candidate() -> None:
    catalog = _catalog()
    manifest = _manifest(adapter_id="user.crypto.news.raw-invalid")
    manifest = DeclarativeProviderManifest(
        **{
            **manifest.__dict__,
            "raw_export_policy": "unsafe_full_raw",
            "config_version": "",
        }
    )
    manifest = DeclarativeProviderManifest(**{**manifest.__dict__, "config_version": config_version_for_manifest(manifest)})

    receipt = catalog.validate_manifest(manifest, actor="user:test", reason="raw_policy_invalid")

    assert receipt.status == ProviderAdmissionStatus.REJECTED
    assert receipt.license_status == AdmissionCheckStatus.FAIL
    assert "raw_export_policy_not_allowed:unsafe_full_raw" in receipt.errors
    assert catalog.enabled_candidates() == ()


def test_raw_export_policy_mismatch_is_rejected_and_not_candidate() -> None:
    catalog = _catalog_with_license_result(
        LicenseCheckResult(
            status=AdmissionCheckStatus.PASS,
            license_policy_id="user.custom",
            cost_tier="free",
            raw_export_policy="redacted",
            commercial_use_allowed=False,
            note="policy requires redacted",
        )
    )
    manifest = _manifest(adapter_id="user.crypto.news.raw-mismatch")

    receipt = catalog.validate_manifest(manifest, actor="user:test", reason="raw_policy_mismatch")

    assert receipt.status == ProviderAdmissionStatus.REJECTED
    assert receipt.license_status == AdmissionCheckStatus.FAIL
    assert any(item.startswith("raw_export_policy_mismatch:") for item in receipt.errors)
    assert catalog.enabled_candidates() == ()
