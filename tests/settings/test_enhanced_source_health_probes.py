from __future__ import annotations

import ast
from pathlib import Path

import pytest
from claw_trade.data_gateway import source_probe
from claw_trade.ui_backend.data_source_settings import (
    SUPPORTED_DATA_SOURCE_TYPES,
    DataSourceSettingsService,
)

ui_runtime_checks = source_probe

PROBE_SOURCE_TYPES: tuple[str, ...] = (
    "tushare",
    "akshare",
    "finnhub",
    "fred",
    "coingecko",
    "coingecko_pro",
    "coinglass",
    "glassnode",
    "binance",
    "okx",
)
KEYED_SOURCE_TYPES: tuple[str, ...] = (
    "tushare",
    "finnhub",
    "fred",
    "coingecko_pro",
    "coinglass",
    "glassnode",
)


def test_data_source_health_tester_is_config_only_for_all_fixed_sources() -> None:
    tester = ui_runtime_checks.build_data_source_health_tester(env={})
    for source_type in PROBE_SOURCE_TYPES:
        instance = {"supportedType": source_type}
        if source_type in KEYED_SOURCE_TYPES:
            instance["apiKeyReplacement"] = "token"

        result = tester(instance)

        assert result["status"] == "validated"
        assert result["requestKind"] == "ui_probe"
        assert result["consumerType"] == "ui_probe"
        assert result["dataRequirement"]["dataType"] == "provider_connection_probe"
        assert result["evidence"] == {
            "kind": "config_only",
            "probeOnly": True,
            "mainChainEvidence": False,
            "remoteSuccess": False,
            "dataNeedRequiredForRemoteValidation": True,
        }


def test_successful_probe_does_not_change_keyed_settings_source_catalog() -> None:
    tester = ui_runtime_checks.build_data_source_health_tester(env={})

    result = tester({"supportedType": "tushare", "apiKeyReplacement": "ts-token"})

    assert result["status"] == "validated"
    assert DataSourceSettingsService(health_tester=tester).list_data_sources()["supportedTypes"] == SUPPORTED_DATA_SOURCE_TYPES


def test_data_source_health_tester_unknown_type_uses_probe_not_supported_error() -> None:
    tester = ui_runtime_checks.build_data_source_health_tester(env={})
    with pytest.raises(RuntimeError, match="probe_not_supported"):
        tester({"supportedType": "unknown_provider"})


@pytest.mark.parametrize("source_type", KEYED_SOURCE_TYPES)
def test_keyed_sources_require_credentials_without_remote_probe(source_type: str) -> None:
    tester = ui_runtime_checks.build_data_source_health_tester(env={})
    with pytest.raises(RuntimeError, match="credential_missing"):
        tester({"supportedType": source_type})


def test_data_source_health_tester_validates_endpoint_shape_only() -> None:
    tester = ui_runtime_checks.build_data_source_health_tester(env={})

    assert tester({"supportedType": "binance", "endpointUrl": "https://api.binance.com"})["status"] == "validated"
    with pytest.raises(RuntimeError, match="invalid_endpoint"):
        tester({"supportedType": "binance", "endpointUrl": "not-a-url"})


def test_source_probe_module_has_no_direct_remote_probe_surface() -> None:
    module_path = Path(ui_runtime_checks.__file__).resolve()
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    text = module_path.read_text(encoding="utf-8")

    forbidden_imports = {"urllib.request", "requests", "importlib", "tushare", "akshare"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported = {alias.name for alias in node.names}
            assert not (imported & forbidden_imports)
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            assert module not in forbidden_imports
            assert not module.startswith("urllib.request")

    forbidden_tokens = ("urlopen", "requests.get", "pro_api", "tool_trade_date_hist_sina", "CG-API-KEY", "x-cg-pro-api-key")
    for token in forbidden_tokens:
        assert token not in text
