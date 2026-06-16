from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from claw_trade.data_gateway.official_catalog import iter_official_catalog_endpoints
from claw_trade.data_gateway.providers import build_minimal_provider_registry


@dataclass(frozen=True)
class SDKInternalHttpRisk:
    provider_id: str
    endpoint_id: str
    official_or_sdk: str
    why_sdk_internal_unknown: str
    outer_gate_covered: bool
    subrequest_visibility: str
    migration_priority: str
    live_evidence_ref: str | None
    residual_risk: str


_SDK_INTERNAL_REASON = (
    "This endpoint performs HTTP through a provider SDK or internal client path, "
    "so managed_http cannot observe every subrequest."
)
_SDK_INTERNAL_RISK = (
    "Outer provider calls enter the claw-trade execution gate, but subrequest-level "
    "rate-limit timing, retries, and remote attempts are not fully auditable until "
    "the endpoint is migrated to managed_http."
)


def iter_sdk_internal_http_risks() -> Iterable[SDKInternalHttpRisk]:
    seen: set[tuple[str, str]] = set()
    for risk in _structured_provider_risks():
        key = (risk.provider_id, risk.endpoint_id)
        if key in seen:
            continue
        seen.add(key)
        yield risk
    for risk in _official_catalog_risks():
        key = (risk.provider_id, risk.endpoint_id)
        if key in seen:
            continue
        seen.add(key)
        yield risk


def all_inventory() -> tuple[SDKInternalHttpRisk, ...]:
    return tuple(iter_sdk_internal_http_risks())


def _structured_provider_risks() -> Iterable[SDKInternalHttpRisk]:
    for capability in build_minimal_provider_registry().list_all_capabilities():
        if _value(getattr(capability, "http_visibility", "")) != "sdk_internal_unknown":
            continue
        yield SDKInternalHttpRisk(
            provider_id=str(getattr(capability, "provider_id")),
            endpoint_id=str(getattr(capability, "endpoint_id")),
            official_or_sdk="sdk",
            why_sdk_internal_unknown=_SDK_INTERNAL_REASON,
            outer_gate_covered=True,
            subrequest_visibility="sdk_internal_unknown",
            migration_priority="p1",
            live_evidence_ref=None,
            residual_risk=_SDK_INTERNAL_RISK,
        )


def _official_catalog_risks() -> Iterable[SDKInternalHttpRisk]:
    for endpoint in iter_official_catalog_endpoints():
        if _value(getattr(endpoint, "http_visibility", "")) != "sdk_internal_unknown":
            continue
        yield SDKInternalHttpRisk(
            provider_id=str(getattr(endpoint, "provider_id")),
            endpoint_id=str(getattr(endpoint, "endpoint_id")),
            official_or_sdk="official",
            why_sdk_internal_unknown=_SDK_INTERNAL_REASON,
            outer_gate_covered=True,
            subrequest_visibility="sdk_internal_unknown",
            migration_priority="p1",
            live_evidence_ref=None,
            residual_risk=_SDK_INTERNAL_RISK,
        )


def _value(value: Any) -> Any:
    return getattr(value, "value", value)


SDK_INTERNAL_HTTP_RISK_INVENTORY = all_inventory()


__all__ = [
    "SDKInternalHttpRisk",
    "SDK_INTERNAL_HTTP_RISK_INVENTORY",
    "all_inventory",
    "iter_sdk_internal_http_risks",
]
