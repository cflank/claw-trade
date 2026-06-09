from __future__ import annotations

import json
from dataclasses import asdict

from claw_trade.data_gateway.provider_adapter_inventory import (
    implemented_adapters_from_registry,
    iter_internal_source_catalog_entries,
    known_missing_adapter_capabilities,
    provider_mapping_without_capability,
    unmapped_provider_ids,
)
from claw_trade.data_gateway.providers import build_minimal_provider_registry
from claw_trade.ui_backend import data_source_settings


def main() -> int:
    registry = build_minimal_provider_registry()
    settings_sources = {
        item.supported_type: {
            "source_type": item.supported_type,
            "source_group": item.group,
            "visible_in_settings": True,
        }
        for item in data_source_settings._SUPPORTED_SOURCE_PROFILES
    }
    internal_sources = {
        item.source_type: asdict(item)
        for item in iter_internal_source_catalog_entries()
    }
    source_catalog = dict(sorted({**settings_sources, **internal_sources}.items()))
    implemented_adapters = tuple(implemented_adapters_from_registry(registry))
    missing_adapters = tuple(known_missing_adapter_capabilities())
    unmapped = unmapped_provider_ids(registry)
    stale_mappings = provider_mapping_without_capability(registry)
    unknown_source_types = sorted(
        {
            source_type
            for adapter in implemented_adapters
            for source_type in adapter.source_types
            if source_type not in source_catalog
        }
    )

    payload = {
        "schema": "provider-adapter-inventory-audit-v1",
        "source_catalog_count": len(source_catalog),
        "settings_visible_source_count": len(settings_sources),
        "internal_source_count": len(internal_sources),
        "implemented_adapter_count": len(implemented_adapters),
        "provider_count": len({adapter.provider_id for adapter in implemented_adapters}),
        "missing_adapter_count": len(missing_adapters),
        "unmapped_provider_ids": list(unmapped),
        "stale_provider_source_mappings": list(stale_mappings),
        "unknown_source_types": unknown_source_types,
        "source_catalog": list(source_catalog.values()),
        "implemented_adapters": [asdict(adapter) for adapter in implemented_adapters],
        "known_missing_adapters": [asdict(gap) for gap in missing_adapters],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return 1 if unmapped or stale_mappings or unknown_source_types else 0


if __name__ == "__main__":
    raise SystemExit(main())
