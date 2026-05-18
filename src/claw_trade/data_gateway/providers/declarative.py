from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from claw_trade.data_gateway.models import DeclarativeProviderManifest

_CODE_SHAPE_KEYS = frozenset(
    {
        "python_module",
        "module",
        "function",
        "callable",
        "script",
        "code",
        "entrypoint",
        "shell",
    }
)


def canonical_manifest_payload(manifest: DeclarativeProviderManifest) -> Mapping[str, Any]:
    return {
        "provider_id": manifest.provider_id,
        "adapter_id": manifest.adapter_id,
        "version": manifest.version,
        "markets": tuple(m.value for m in manifest.markets),
        "domains": tuple(d.value for d in manifest.domains),
        "endpoints": manifest.endpoints,
        "source_role": manifest.source_role.value,
        "expected_schema_id": manifest.expected_schema_id,
        "base_url": manifest.base_url,
        "request_template": manifest.request_template,
        "response_mapping": manifest.response_mapping,
        "credential_requirements": manifest.credential_requirements,
        "rate_limit_policy_id": manifest.rate_limit_policy_id,
        "cache_ttl_seconds": manifest.cache_ttl_seconds,
        "license_policy_id": manifest.license_policy_id,
        "raw_export_policy": manifest.raw_export_policy,
        "healthcheck": manifest.healthcheck,
        "priority": manifest.priority,
        "priority_source": manifest.priority_source.value,
        "coverage_group": manifest.coverage_group,
        "coverage_quorum": manifest.coverage_quorum,
    }


def config_version_for_manifest(manifest: DeclarativeProviderManifest) -> str:
    payload = canonical_manifest_payload(manifest)
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def is_code_like_manifest(manifest: DeclarativeProviderManifest) -> bool:
    for key in _CODE_SHAPE_KEYS:
        if key in manifest.request_template:
            return True
        if key in manifest.healthcheck:
            return True
    for endpoint in manifest.endpoints:
        lowered = endpoint.strip().lower()
        if lowered.startswith("python:") or lowered.startswith("file:"):
            return True
    return False
