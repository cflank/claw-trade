from __future__ import annotations

import importlib
import importlib.util
from collections.abc import Iterable
from typing import Any

from claw_trade.data_gateway.providers import build_minimal_provider_registry

REQUIRED_RISK_FIELDS = {
    "provider_id",
    "endpoint_id",
    "official_or_sdk",
    "why_sdk_internal_unknown",
    "outer_gate_covered",
    "subrequest_visibility",
    "migration_priority",
    "live_evidence_ref",
    "residual_risk",
}
REQUIRED_NON_EMPTY_RISK_FIELDS = {
    "provider_id",
    "endpoint_id",
    "official_or_sdk",
    "why_sdk_internal_unknown",
    "subrequest_visibility",
    "migration_priority",
    "residual_risk",
}
ALLOWED_RISK_KEYS = REQUIRED_RISK_FIELDS
VALID_OFFICIAL_OR_SDK = {"official", "sdk"}
VALID_SUBREQUEST_VISIBILITY = {"unknown", "sdk_internal_unknown", "not_visible"}
VALID_MIGRATION_PRIORITY = {"p0", "p1", "p2", "p3", "low", "medium", "high"}
BUSINESS_SCOPE_KEYS = {
    "allowed_worker",
    "allowed_domain",
    "allowed_report_section",
    "domain",
    "report_section",
    "provider_scope",
    "only_for_market",
    "only_for_fundamental",
}


def test_every_sdk_internal_unknown_provider_has_risk_inventory() -> None:
    spec = importlib.util.find_spec("claw_trade.data_gateway.providers.sdk_risk_inventory")
    assert spec is not None, "SDK internal HTTP risk inventory is missing"

    inventory = tuple(_iter_inventory())
    inventory_keys = {(_read_attr(item, "provider_id"), _read_attr(item, "endpoint_id")) for item in inventory}
    sdk_internal_keys = {
        (cap.provider_id, cap.endpoint_id)
        for cap in build_minimal_provider_registry().list_all_capabilities()
        if str(cap.http_visibility) == "sdk_internal_unknown"
    }
    sdk_internal_keys.update(_official_catalog_sdk_internal_keys())

    missing = sdk_internal_keys - inventory_keys
    assert not missing

    for item in inventory:
        item_id = (_read_attr(item, "provider_id"), _read_attr(item, "endpoint_id"))
        raw_keys = _raw_keys(item)
        assert raw_keys <= ALLOWED_RISK_KEYS, (item_id, sorted(raw_keys - ALLOWED_RISK_KEYS))
        assert BUSINESS_SCOPE_KEYS.isdisjoint(raw_keys), (item_id, sorted(BUSINESS_SCOPE_KEYS & raw_keys))
        assert REQUIRED_RISK_FIELDS <= raw_keys, (item_id, sorted(REQUIRED_RISK_FIELDS - raw_keys))
        for field in REQUIRED_NON_EMPTY_RISK_FIELDS:
            assert _read_attr(item, field), (item_id, field)
        assert isinstance(_read_attr(item, "outer_gate_covered"), bool), item_id
        assert _read_attr(item, "live_evidence_ref") is None or _read_attr(item, "live_evidence_ref"), item_id
        assert _read_attr(item, "official_or_sdk") in VALID_OFFICIAL_OR_SDK, item_id
        assert _read_attr(item, "subrequest_visibility") in VALID_SUBREQUEST_VISIBILITY, item_id
        assert str(_read_attr(item, "migration_priority")).lower() in VALID_MIGRATION_PRIORITY, item_id


def _iter_inventory() -> Iterable[Any]:
    module = importlib.import_module("claw_trade.data_gateway.providers.sdk_risk_inventory")
    for name in ("iter_sdk_internal_http_risks", "iter_inventory", "all_inventory"):
        candidate = getattr(module, name, None)
        if callable(candidate):
            return candidate()
    inventory = getattr(module, "SDK_INTERNAL_HTTP_RISK_INVENTORY", None)
    assert inventory is not None, "SDK risk inventory must expose an iterator or SDK_INTERNAL_HTTP_RISK_INVENTORY"
    return inventory


def _read_attr(obj: Any, name: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


def _official_catalog_sdk_internal_keys() -> set[tuple[Any, Any]]:
    if importlib.util.find_spec("claw_trade.data_gateway.official_catalog") is None:
        return set()
    catalog = importlib.import_module("claw_trade.data_gateway.official_catalog")
    endpoints = _iter_official_catalog_endpoints(catalog)
    return {
        (_read_attr(endpoint, "provider_id"), _read_attr(endpoint, "endpoint_id"))
        for endpoint in endpoints
        if _value(_read_attr(endpoint, "http_visibility")) == "sdk_internal_unknown"
    }


def _iter_official_catalog_endpoints(catalog: Any) -> Iterable[Any]:
    for name in ("iter_official_catalog_endpoints", "iter_endpoints", "all_endpoints"):
        candidate = getattr(catalog, name, None)
        if callable(candidate):
            return candidate()
    endpoints = getattr(catalog, "OFFICIAL_ENDPOINTS", None)
    assert endpoints is not None, "official catalog must expose iter_endpoints/all_endpoints/OFFICIAL_ENDPOINTS"
    return endpoints


def _value(value: Any) -> Any:
    return getattr(value, "value", value)


def _raw_keys(obj: Any) -> set[str]:
    if isinstance(obj, dict):
        return set(obj)
    model_dump = getattr(obj, "model_dump", None)
    if callable(model_dump):
        return set(model_dump())
    dataclass_fields = getattr(obj, "__dataclass_fields__", None)
    if isinstance(dataclass_fields, dict):
        return set(dataclass_fields)
    return set(getattr(obj, "__dict__", {}))
