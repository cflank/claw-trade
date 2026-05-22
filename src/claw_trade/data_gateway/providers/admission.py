from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from typing import Callable, Iterable
from urllib.parse import urlparse

from claw_trade.data_gateway.models import (
    AdmissionCheckStatus,
    DeclarativeProviderManifest,
    ProviderAdmissionStatus,
    ProviderValidationReceipt,
    SourceRole,
    utc_now_iso,
)
from claw_trade.data_gateway.providers.declarative import config_version_for_manifest, is_code_like_manifest
from claw_trade.data_gateway.providers.license_policy import LicensePolicyStore
from claw_trade.data_gateway.providers.secrets import SecretStore

_METADATA_HOSTS = frozenset(
    {
        "metadata.google.internal",
        "metadata.azure.internal",
    }
)
_METADATA_IPS = frozenset(
    {
        "169.254.169.254",
        "100.100.100.200",
    }
)
_ALLOWED_HEALTHCHECK_METHODS = frozenset({"GET", "HEAD", "POST"})
_ALLOWED_SOURCE_ROLES = frozenset(role for role in SourceRole)
_INVALID_SAMPLE_REF_PREFIXES = ("mock://", "fake://", "stub://")


def _path_has_url_authority(value: object) -> bool:
    text = str(value).strip()
    if not text:
        return False
    parsed = urlparse(text)
    return bool(parsed.scheme or parsed.netloc or text.startswith("//"))


def _ip_blocked(ip_value: str) -> bool:
    ip = ipaddress.ip_address(ip_value)
    return any(
        (
            ip.is_loopback,
            ip.is_private,
            ip.is_link_local,
            ip.is_multicast,
            ip.is_unspecified,
            ip.is_reserved,
            ip_value in _METADATA_IPS,
        )
    )


def _dns_resolve(hostname: str) -> tuple[str, ...]:
    records = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
    ips = {record[4][0] for record in records}
    return tuple(sorted(ips))


def _sample_ref_invalid(value: object) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return True
    return any(text.startswith(prefix) for prefix in _INVALID_SAMPLE_REF_PREFIXES)


@dataclass(frozen=True)
class DeclarativeProviderSecurityPolicy:
    allowed_domains: tuple[str, ...]
    allow_http_domains: tuple[str, ...] = ()
    dns_resolver: Callable[[str], tuple[str, ...]] = _dns_resolve

    def is_domain_allowed(self, hostname: str) -> bool:
        host = hostname.strip().lower()
        for rule in self.allowed_domains:
            normalized = rule.strip().lower()
            if not normalized:
                continue
            if normalized.startswith("*."):
                suffix = normalized[2:]
                if host.endswith("." + suffix):
                    return True
            elif host == normalized:
                return True
        return False

    def _scheme_allowed(self, scheme: str, hostname: str) -> bool:
        scheme_lower = scheme.lower()
        if scheme_lower == "https":
            return True
        if scheme_lower != "http":
            return False
        host = hostname.strip().lower()
        return host in {value.strip().lower() for value in self.allow_http_domains}

    def validate_url(self, url: str, *, context: str) -> tuple[AdmissionCheckStatus, tuple[str, ...]]:
        errors: list[str] = []
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.hostname:
            return (AdmissionCheckStatus.FAIL, (f"{context}:url_invalid",))
        host = parsed.hostname.strip().lower()
        if host == "localhost" or host in _METADATA_HOSTS:
            return (AdmissionCheckStatus.BLOCKED, (f"{context}:blocked_host:{host}",))
        if not self._scheme_allowed(parsed.scheme, host):
            return (AdmissionCheckStatus.BLOCKED, (f"{context}:blocked_scheme:{parsed.scheme}",))
        if not self.is_domain_allowed(host):
            return (AdmissionCheckStatus.BLOCKED, (f"{context}:domain_not_allowed:{host}",))
        try:
            if _ip_blocked(host):
                return (AdmissionCheckStatus.BLOCKED, (f"{context}:blocked_ip:{host}",))
        except ValueError:
            pass
        try:
            for ip in self.dns_resolver(host):
                if _ip_blocked(ip):
                    errors.append(f"{context}:dns_blocked_ip:{ip}")
        except socket.gaierror:
            errors.append(f"{context}:dns_resolution_failed:{host}")
        except OSError:
            errors.append(f"{context}:dns_resolution_failed:{host}")
        if errors:
            return (AdmissionCheckStatus.BLOCKED, tuple(errors))
        return (AdmissionCheckStatus.PASS, ())


@dataclass(frozen=True)
class ProviderAdmissionValidator:
    secret_store: SecretStore
    license_store: LicensePolicyStore
    security_policy: DeclarativeProviderSecurityPolicy
    # Must be bound to a real evidence-store existence check in production/integration.
    # A permissive always-true verifier can incorrectly admit non-existent sample refs.
    sample_ref_exists: Callable[[str], bool]

    def _validate_healthcheck(self, manifest: DeclarativeProviderManifest) -> tuple[AdmissionCheckStatus, tuple[str, ...]]:
        errors: list[str] = []
        method = str(manifest.healthcheck.get("method", "GET")).upper()
        if method not in _ALLOWED_HEALTHCHECK_METHODS:
            errors.append(f"healthcheck_method_invalid:{method}")
        path = manifest.healthcheck.get("path")
        url = manifest.healthcheck.get("url")
        if not path and not url:
            errors.append("healthcheck_missing_target")
        if path and _path_has_url_authority(path):
            errors.append("healthcheck_path_must_be_relative")
        raw_ref = str(manifest.healthcheck.get("sample_raw_ref") or "").strip()
        normalized_ref = str(manifest.healthcheck.get("sample_normalized_ref") or "").strip()
        if _sample_ref_invalid(raw_ref):
            errors.append("healthcheck_sample_raw_ref_missing_or_invalid")
        elif not self.sample_ref_exists(raw_ref):
            errors.append("healthcheck_sample_raw_ref_not_found")
        if _sample_ref_invalid(normalized_ref):
            errors.append("healthcheck_sample_normalized_ref_missing_or_invalid")
        elif not self.sample_ref_exists(normalized_ref):
            errors.append("healthcheck_sample_normalized_ref_not_found")
        if url:
            status, details = self.security_policy.validate_url(str(url), context="healthcheck.url")
            if status != AdmissionCheckStatus.PASS:
                return (status, details)
        redirect_targets = manifest.healthcheck.get("redirect_targets", ())
        if isinstance(redirect_targets, str):
            redirect_targets = (redirect_targets,)
        if isinstance(redirect_targets, Iterable):
            for index, redirect_url in enumerate(redirect_targets):
                status, details = self.security_policy.validate_url(
                    str(redirect_url), context=f"healthcheck.redirect[{index}]"
                )
                if status != AdmissionCheckStatus.PASS:
                    return (status, details)
        if errors:
            return (AdmissionCheckStatus.FAIL, tuple(errors))
        return (AdmissionCheckStatus.PASS, ())

    def _validate_schema_sample(self, manifest: DeclarativeProviderManifest) -> tuple[AdmissionCheckStatus, tuple[str, ...]]:
        errors: list[str] = []
        if not manifest.expected_schema_id.strip():
            errors.append("expected_schema_id_empty")
        if not manifest.response_mapping:
            errors.append("response_mapping_empty")
        for key, value in manifest.response_mapping.items():
            if not str(key).strip() or not str(value).strip():
                errors.append("response_mapping_invalid")
                break
        if errors:
            return (AdmissionCheckStatus.FAIL, tuple(errors))
        return (AdmissionCheckStatus.PASS, ())

    def _validate_source_role(self, manifest: DeclarativeProviderManifest) -> tuple[AdmissionCheckStatus, tuple[str, ...]]:
        if manifest.source_role not in _ALLOWED_SOURCE_ROLES:
            return (AdmissionCheckStatus.FAIL, ("source_role_invalid",))
        if manifest.source_role == SourceRole.OFFICIAL_ORIGINAL:
            return (AdmissionCheckStatus.FAIL, ("source_role_official_original_forbidden_for_user_provider",))
        return (AdmissionCheckStatus.PASS, ())

    def _validate_rate_limit(self, manifest: DeclarativeProviderManifest) -> tuple[AdmissionCheckStatus, tuple[str, ...]]:
        if not manifest.rate_limit_policy_id.strip():
            return (AdmissionCheckStatus.FAIL, ("rate_limit_policy_missing",))
        return (AdmissionCheckStatus.PASS, ())

    def _validate_base_urls(self, manifest: DeclarativeProviderManifest) -> tuple[AdmissionCheckStatus, tuple[str, ...]]:
        status, details = self.security_policy.validate_url(manifest.base_url, context="base_url")
        if status != AdmissionCheckStatus.PASS:
            return (status, details)
        for endpoint in manifest.endpoints:
            parsed = urlparse(endpoint)
            if parsed.scheme:
                status, details = self.security_policy.validate_url(endpoint, context=f"endpoint:{endpoint}")
                if status != AdmissionCheckStatus.PASS:
                    return (status, details)
        template_url = manifest.request_template.get("url")
        if template_url:
            status, details = self.security_policy.validate_url(str(template_url), context="request_template.url")
            if status != AdmissionCheckStatus.PASS:
                return (status, details)
        template_path = manifest.request_template.get("path")
        if template_path and _path_has_url_authority(template_path):
            return (AdmissionCheckStatus.BLOCKED, ("request_template.path_must_be_relative",))
        return (AdmissionCheckStatus.PASS, ())

    def validate(
        self,
        manifest: DeclarativeProviderManifest,
        *,
        actor: str,
        previous_status: ProviderAdmissionStatus | None,
        transition_reason: str,
    ) -> ProviderValidationReceipt:
        errors: list[str] = []
        if is_code_like_manifest(manifest):
            errors.append("code_provider_not_allowed")

        if config_version_for_manifest(manifest) != manifest.config_version:
            errors.append("config_version_mismatch")

        credential_detail = self.secret_store.credential_status(manifest)
        credential_status = credential_detail.status
        if credential_status != AdmissionCheckStatus.PASS:
            errors.append(f"credential_{credential_status.value}")

        base_url_status, base_url_errors = self._validate_base_urls(manifest)
        if base_url_errors:
            errors.extend(base_url_errors)

        healthcheck_status, healthcheck_errors = self._validate_healthcheck(manifest)
        if healthcheck_errors:
            errors.extend(healthcheck_errors)

        schema_status, schema_errors = self._validate_schema_sample(manifest)
        if schema_errors:
            errors.extend(schema_errors)

        source_role_status, source_role_errors = self._validate_source_role(manifest)
        if source_role_errors:
            errors.extend(source_role_errors)

        rate_limit_status, rate_limit_errors = self._validate_rate_limit(manifest)
        if rate_limit_errors:
            errors.extend(rate_limit_errors)

        license_status, license_detail, license_errors = self.license_store.evaluate(manifest)
        if license_errors:
            errors.extend(license_errors)

        secret_status = AdmissionCheckStatus.PASS if credential_status == AdmissionCheckStatus.PASS else credential_status

        if errors and (base_url_status == AdmissionCheckStatus.BLOCKED or healthcheck_status == AdmissionCheckStatus.BLOCKED):
            final_status = ProviderAdmissionStatus.QUARANTINED
        elif errors:
            final_status = ProviderAdmissionStatus.REJECTED
        else:
            final_status = ProviderAdmissionStatus.VALIDATED

        if source_role_status != AdmissionCheckStatus.PASS:
            final_status = ProviderAdmissionStatus.REJECTED
        if rate_limit_status != AdmissionCheckStatus.PASS:
            final_status = ProviderAdmissionStatus.REJECTED
        if credential_status != AdmissionCheckStatus.PASS:
            final_status = ProviderAdmissionStatus.REJECTED

        receipt = ProviderValidationReceipt(
            provider_id=manifest.provider_id,
            adapter_id=manifest.adapter_id,
            config_version=manifest.config_version,
            status=final_status,
            credential_status=credential_status,
            healthcheck_status=healthcheck_status,
            schema_status=schema_status,
            license_status=license_status,
            secret_status=secret_status,
            credential_detail=credential_detail,
            license_detail=license_detail,
            sample_raw_ref=manifest.healthcheck.get("sample_raw_ref"),
            sample_normalized_ref=manifest.healthcheck.get("sample_normalized_ref"),
            transition_actor=actor,
            previous_status=previous_status,
            transition_reason=transition_reason,
            errors=tuple(errors),
            validated_at=utc_now_iso(),
        )
        return receipt
