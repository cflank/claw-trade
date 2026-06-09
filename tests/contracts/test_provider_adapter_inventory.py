from __future__ import annotations

from pathlib import Path

from claw_trade.data_gateway.provider_adapter_inventory import (
    implemented_adapters_from_registry,
    iter_internal_source_catalog_entries,
    iter_provider_source_mappings,
    known_missing_adapter_capabilities,
    provider_mapping_without_capability,
    unmapped_provider_ids,
)
from claw_trade.data_gateway.providers import build_minimal_provider_registry
from claw_trade.ui_backend import data_source_settings


def test_settings_and_internal_sources_have_inventory_entries() -> None:
    settings_sources = {item.supported_type for item in data_source_settings._SUPPORTED_SOURCE_PROFILES}
    internal_sources = {item.source_type for item in iter_internal_source_catalog_entries()}
    mapped_sources = {
        source_type
        for mapping in iter_provider_source_mappings()
        for source_type in mapping.source_types
    }

    assert internal_sources - settings_sources == {"baidu_finance", "google_news", "mootdx", "project_official"}
    assert mapped_sources <= settings_sources | internal_sources


def test_provider_registry_generates_full_adapter_inventory() -> None:
    registry = build_minimal_provider_registry()
    registry_keys = {
        (cap.provider_id, cap.endpoint_id, cap.market, cap.data_type)
        for cap in registry.list_all_capabilities()
    }
    inventory = implemented_adapters_from_registry(registry)
    inventory_keys = {
        (adapter.provider_id, adapter.endpoint_id, adapter.market, adapter.data_type)
        for adapter in inventory
    }

    assert inventory_keys == registry_keys
    assert all(adapter.source_types for adapter in inventory)


def test_every_registered_provider_has_source_mapping_and_no_stale_mapping() -> None:
    registry = build_minimal_provider_registry()

    assert unmapped_provider_ids(registry) == ()
    assert provider_mapping_without_capability(registry) == ()


def test_known_missing_adapters_are_not_misreported_as_implemented() -> None:
    registry = build_minimal_provider_registry()
    implemented = {
        (adapter.source_types, adapter.market, adapter.data_type, adapter.granularities)
        for adapter in implemented_adapters_from_registry(registry)
    }

    for gap in known_missing_adapter_capabilities():
        assert not any(
            gap.source_type in source_types
            and gap.market == market
            and gap.data_type == data_type
            and gap.granularity in granularities
            for source_types, market, data_type, granularities in implemented
        ), gap


def test_adapter_inventory_is_not_imported_by_runtime_code() -> None:
    root = Path(__file__).resolve().parents[2]
    offenders: list[str] = []
    for path in (root / "src").rglob("*.py"):
        if path.name == "provider_adapter_inventory.py":
            continue
        text = path.read_text(encoding="utf-8")
        if "provider_adapter_inventory" in text:
            offenders.append(str(path.relative_to(root)))

    assert offenders == []


def test_old_allowlist_code_path_is_removed() -> None:
    root = Path(__file__).resolve().parents[2]
    old_module = "approved" + "_provider" + "_scope.py"
    old_script = "audit_" + "approved" + "_provider" + "_scope.py"
    assert not (root / "src/claw_trade/data_gateway" / old_module).exists()
    assert not (root / "scripts/validation" / old_script).exists()
