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
from claw_trade.data_gateway.providers.declarative import config_version_for_manifest
from claw_trade.data_gateway.providers.license_policy import LicensePolicyStore
from claw_trade.data_gateway.providers.secrets import SecretStore


def _manifest(base_url: str) -> DeclarativeProviderManifest:
    draft = DeclarativeProviderManifest(
        provider_id="custom_news",
        adapter_id="user.crypto.security",
        display_name="Custom Security",
        version="1",
        config_version="",
        markets=(Market.CRYPTO,),
        domains=(PackDomain.NEWS,),
        endpoints=("rss_items",),
        source_role=SourceRole.SEARCH_DISCOVERY,
        expected_schema_id="news.discovery.v1",
        base_url=base_url,
        request_template={"path": "/v1/items"},
        response_mapping={"title": "headline"},
        credential_requirements=("RSS_TOKEN",),
        rate_limit_policy_id="user.custom.default",
        cache_ttl_seconds=300,
        license_policy_id="user.custom",
        raw_export_policy="metadata_only",
        healthcheck={"method": "GET", "path": "/health"},
        enabled=True,
        admission_status=ProviderAdmissionStatus.DRAFT,
        priority=20,
        priority_source=PrioritySource.SYSTEM_DEFAULT,
        coverage_group=None,
        coverage_quorum=None,
    )
    return DeclarativeProviderManifest(**{**draft.__dict__, "config_version": config_version_for_manifest(draft)})


def _validator(*, dns_resolver=None, allowed_domains=("feeds.example.com",), allow_http=()) -> ProviderAdmissionValidator:
    return ProviderAdmissionValidator(
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
            allowed_domains=allowed_domains,
            allow_http_domains=allow_http,
            dns_resolver=dns_resolver or (lambda host: ("93.184.216.34",)),
        ),
    )


def test_block_localhost_private_linklocal_and_metadata_service() -> None:
    validator = _validator()

    blocked_urls = (
        "https://localhost",
        "https://127.0.0.1",
        "https://10.0.0.8",
        "https://169.254.1.2",
        "https://169.254.169.254",
    )
    for url in blocked_urls:
        receipt = validator.validate(
            _manifest(url),
            actor="user:test",
            previous_status=ProviderAdmissionStatus.VALIDATING,
            transition_reason="security-check",
        )
        assert receipt.status == ProviderAdmissionStatus.QUARANTINED


def test_block_unapproved_scheme_unapproved_domain_and_redirect() -> None:
    validator = _validator(allowed_domains=("feeds.example.com",))
    blocked = _manifest("ftp://feeds.example.com")
    blocked = DeclarativeProviderManifest(**{**blocked.__dict__, "config_version": config_version_for_manifest(blocked)})
    receipt = validator.validate(
        blocked,
        actor="user:test",
        previous_status=ProviderAdmissionStatus.VALIDATING,
        transition_reason="security-check",
    )
    assert receipt.status == ProviderAdmissionStatus.QUARANTINED

    outside = _manifest("https://evil.example.net")
    outside = DeclarativeProviderManifest(**{**outside.__dict__, "config_version": config_version_for_manifest(outside)})
    receipt2 = validator.validate(
        outside,
        actor="user:test",
        previous_status=ProviderAdmissionStatus.VALIDATING,
        transition_reason="security-check",
    )
    assert receipt2.status == ProviderAdmissionStatus.QUARANTINED

    redirect = _manifest("https://feeds.example.com")
    redirect = DeclarativeProviderManifest(
        **{
            **redirect.__dict__,
            "healthcheck": {"method": "GET", "path": "/health", "redirect_targets": ("https://169.254.169.254",)},
        }
    )
    redirect = DeclarativeProviderManifest(**{**redirect.__dict__, "config_version": config_version_for_manifest(redirect)})
    receipt3 = validator.validate(
        redirect,
        actor="user:test",
        previous_status=ProviderAdmissionStatus.VALIDATING,
        transition_reason="security-check",
    )
    assert receipt3.status == ProviderAdmissionStatus.QUARANTINED


def test_dns_resolution_to_private_ip_is_blocked() -> None:
    validator = _validator(dns_resolver=lambda host: ("10.1.2.3",))
    receipt = validator.validate(
        _manifest("https://feeds.example.com"),
        actor="user:test",
        previous_status=ProviderAdmissionStatus.VALIDATING,
        transition_reason="security-check",
    )
    assert receipt.status == ProviderAdmissionStatus.QUARANTINED
    assert any("dns_blocked_ip" in item for item in receipt.errors)


def test_url_authority_is_rejected_inside_path_fields() -> None:
    validator = _validator(allowed_domains=("feeds.example.com",))

    template_path = _manifest("https://feeds.example.com")
    template_path = DeclarativeProviderManifest(
        **{
            **template_path.__dict__,
            "request_template": {"path": "https://169.254.169.254/latest"},
        }
    )
    template_path = DeclarativeProviderManifest(
        **{**template_path.__dict__, "config_version": config_version_for_manifest(template_path)}
    )
    receipt = validator.validate(
        template_path,
        actor="user:test",
        previous_status=ProviderAdmissionStatus.VALIDATING,
        transition_reason="security-check",
    )
    assert receipt.status == ProviderAdmissionStatus.QUARANTINED
    assert "request_template.path_must_be_relative" in receipt.errors

    healthcheck_path = _manifest("https://feeds.example.com")
    healthcheck_path = DeclarativeProviderManifest(
        **{
            **healthcheck_path.__dict__,
            "healthcheck": {"method": "GET", "path": "//169.254.169.254/latest"},
        }
    )
    healthcheck_path = DeclarativeProviderManifest(
        **{**healthcheck_path.__dict__, "config_version": config_version_for_manifest(healthcheck_path)}
    )
    receipt2 = validator.validate(
        healthcheck_path,
        actor="user:test",
        previous_status=ProviderAdmissionStatus.VALIDATING,
        transition_reason="security-check",
    )
    assert receipt2.status == ProviderAdmissionStatus.REJECTED
    assert "healthcheck_path_must_be_relative" in receipt2.errors


def test_http_is_allowed_only_for_explicit_domain_and_subdomain_not_implicit() -> None:
    validator = _validator(allowed_domains=("feeds.example.com",), allow_http=("feeds.example.com",))
    ok = validator.validate(
        _manifest("http://feeds.example.com"),
        actor="user:test",
        previous_status=ProviderAdmissionStatus.VALIDATING,
        transition_reason="security-check",
    )
    assert ok.status == ProviderAdmissionStatus.VALIDATED

    not_ok = _manifest("https://sub.feeds.example.com")
    not_ok = DeclarativeProviderManifest(**{**not_ok.__dict__, "config_version": config_version_for_manifest(not_ok)})
    bad = validator.validate(
        not_ok,
        actor="user:test",
        previous_status=ProviderAdmissionStatus.VALIDATING,
        transition_reason="security-check",
    )
    assert bad.status == ProviderAdmissionStatus.QUARANTINED

    wildcard = _validator(allowed_domains=("*.feeds.example.com",))
    good_subdomain = wildcard.validate(
        _manifest("https://sub.feeds.example.com"),
        actor="user:test",
        previous_status=ProviderAdmissionStatus.VALIDATING,
        transition_reason="security-check",
    )
    assert good_subdomain.status == ProviderAdmissionStatus.VALIDATED
