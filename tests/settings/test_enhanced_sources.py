from __future__ import annotations

import pytest
from claw_trade.data_gateway import source_probe as ui_runtime_checks
from claw_trade.data_gateway.models import Market
from claw_trade.ui_backend.data_source_settings import (
    SUPPORTED_DATA_SOURCE_TYPES,
    DataSourceSettingsService,
    InMemoryDataSourceStore,
    ProviderDisplayDecision,
    ProviderDisplayStatus,
)
from claw_trade.ui_backend.settings_service import UiBoundaryError

PROBE_BACKED_SOURCE_TYPES: tuple[str, ...] = (
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
KEYED_SOURCE_TYPES = frozenset({"tushare", "finnhub", "fred", "coingecko_pro", "coinglass", "glassnode"})


def _show_decision(provider_id: str, *, market: Market) -> ProviderDisplayDecision:
    return ProviderDisplayDecision(
        provider_id=provider_id,
        market=market,
        display_status=ProviderDisplayStatus.SHOW,
        reason="test main-chain evidence",
        requires_user_credential=True,
        changes_report_or_select_result=True,
        writes_mongo_and_evidence=True,
        enters_data_result_flow=True,
        consumed_by_worker_or_strategy=True,
        live_fresh_evidence_ref=f"evidence://main-chain/{provider_id}",
        probe_only=False,
    )


def _service_with_tushare(**kwargs) -> DataSourceSettingsService:
    return DataSourceSettingsService(
        display_decision_source=lambda: (_show_decision("tushare", market=Market.CN_A),),
        **kwargs,
    )


def test_settings_enhanced_sources_exposes_provider_backed_api_sources_and_filters_unapproved_provider() -> None:
    store = InMemoryDataSourceStore()
    store.upsert(
        {
            "id": "approved-tushare",
            "supported_type": "tushare",
            "group": "cn_a_data",
            "display_name": "Tushare",
            "enabled": False,
            "priority": 100,
            "state": "draft",
        }
    )
    store.upsert(
        {
            "id": "unapproved-custom-vendor",
            "supported_type": "custom_vendor",
            "group": "cn_a_news",
            "display_name": "Custom Vendor",
            "enabled": True,
            "priority": 10,
            "state": "enabled",
        }
    )
    service = _service_with_tushare(data_source_store=store, health_tester=lambda _: {"status": "validated"})

    listed = service.list_data_sources()

    assert SUPPORTED_DATA_SOURCE_TYPES
    assert tuple(listed["supportedTypes"]) == SUPPORTED_DATA_SOURCE_TYPES
    listed_types = [item["supportedType"] for item in listed["instances"]]
    assert len(listed["instances"]) == len(SUPPORTED_DATA_SOURCE_TYPES)
    assert listed_types == list(SUPPORTED_DATA_SOURCE_TYPES)
    assert all(item["displayName"] != "Custom Vendor" for item in listed["instances"])
    for default_or_internal in (
        "akshare",
        "eastmoney",
        "coingecko",
        "binance",
        "okx",
        "ccxt",
        "sec_edgar",
        "yahoo_finance",
        "alpha_vantage",
        "fmp",
        "polygon",
        "tiingo",
        "nasdaq_data_link",
        "coinmarketcap",
        "wind",
        "csmar",
        "newsapi",
        "x",
        "reddit",
        "lunarcrush",
        "token_terminal",
    ):
        assert default_or_internal not in listed_types
    for item in listed["instances"]:
        assert "proxyUrl" not in item
        assert "headerName" not in item
        assert "priority" not in item


def test_settings_enhanced_sources_save_ignores_forbidden_surface_fields() -> None:
    store = InMemoryDataSourceStore()
    service = _service_with_tushare(
        data_source_store=store,
        health_tester=lambda _: {"status": "validated", "message": "连接测试通过。", "impact": "low"},
    )

    saved = service.save_data_source_instance(
        {
            "supportedType": "tushare",
            "enabled": True,
            "apiKeyReplacement": "test-token",
            "endpointUrl": "https://api.tushare.pro",
            "proxyUrl": "http://127.0.0.1:8000",
            "headerName": "X-Test-Header",
            "priority": 1,
        },
        request_id="s05-save-ignore-forbidden-surface",
    )

    assert "proxyUrl" not in saved
    assert "headerName" not in saved
    assert "priority" not in saved

    stored = store.list_instances()[0]
    assert stored["priority"] == 100
    assert stored["proxy_url"] is None
    assert stored["header_name"] is None


def test_settings_enhanced_sources_enable_requires_real_success() -> None:
    service = _service_with_tushare(
        health_tester=lambda _: {
            "status": "rejected",
            "message": "token invalid",
            "impact": "high",
        }
    )

    with pytest.raises(UiBoundaryError) as exc:
        service.save_data_source_instance(
            {
                "supportedType": "tushare",
                "displayName": "Tushare",
                "enabled": True,
                "apiKeyReplacement": "test-token",
                "endpointUrl": "https://api.tushare.pro",
            },
            request_id="s05-save-enable-fail",
        )

    assert exc.value.code == "DATASOURCE_TEST_FAILED"
    assert "invalid" in exc.value.user_message.lower()


def test_settings_enhanced_sources_enable_persists_after_successful_test() -> None:
    service = _service_with_tushare(
        health_tester=lambda _: {
            "status": "validated",
            "message": "连接测试通过。",
            "impact": "low",
        }
    )

    saved = service.save_data_source_instance(
        {
            "supportedType": "tushare",
            "displayName": "Tushare",
            "enabled": True,
            "apiKeyReplacement": "test-token",
            "endpointUrl": "https://api.tushare.pro",
        },
        request_id="s05-save-enable-pass",
    )

    assert saved["supportedType"] == "tushare"
    assert saved["enabled"] is True
    assert saved["state"] == "validated"
    assert saved["lastTestAt"]


def test_settings_enhanced_sources_probe_not_supported_does_not_enable_source() -> None:
    def _unsupported_probe(_: dict[str, object]) -> dict[str, object]:
        raise RuntimeError("datasource_test_failed: probe_not_supported")

    service = DataSourceSettingsService(
        health_tester=_unsupported_probe,
        display_decision_source=lambda: (_show_decision("coinglass", market=Market.CRYPTO),),
    )

    with pytest.raises(UiBoundaryError) as exc:
        service.save_data_source_instance(
            {
                "supportedType": "coinglass",
                "displayName": "Coinglass",
                "enabled": True,
                "apiKeyReplacement": "cg-token",
                "endpointUrl": "https://open-api-v4.coinglass.com",
            },
            request_id="s05-save-unsupported-probe-enabled",
        )

    assert exc.value.code == "DATASOURCE_TEST_FAILED"


def test_settings_enhanced_sources_rejects_unapproved_provider_types() -> None:
    service = DataSourceSettingsService()

    with pytest.raises(UiBoundaryError) as exc:
        service.test_data_source_instance(
            {"supportedType": "custom_vendor", "displayName": "Custom Vendor", "enabled": False},
            request_id="s05-test-unapproved-provider",
        )

    assert exc.value.code == "INVALID_INPUT"
    assert "设置页连接测试" in exc.value.user_message


@pytest.mark.parametrize(
    ("source_type", "request_id"),
    (
        ("custom_http", "s05-test-block-custom-http"),
        ("custom_json", "s05-test-block-custom-json"),
    ),
)
def test_settings_enhanced_sources_rejects_blocked_source_types(source_type: str, request_id: str) -> None:
    service = DataSourceSettingsService()

    with pytest.raises(UiBoundaryError) as exc:
        service.save_data_source_instance(
            {
                "supportedType": source_type,
                "displayName": source_type,
                "enabled": False,
            },
            request_id=request_id,
        )

    assert exc.value.code == "INVALID_INPUT"
    assert "NT-07" in exc.value.user_message


def test_data_source_health_tester_validates_fixed_sources_without_remote_probe() -> None:
    tester = ui_runtime_checks.build_data_source_health_tester(env={})
    for source_type in PROBE_BACKED_SOURCE_TYPES:
        instance = {"supportedType": source_type}
        if source_type in KEYED_SOURCE_TYPES:
            instance["apiKeyReplacement"] = "token"
        result = tester(instance)
        assert result["status"] == "validated"
        assert result["evidence"]["kind"] == "config_only"
        assert result["evidence"]["remoteSuccess"] is False


@pytest.mark.parametrize(
    ("source_type", "instance", "env"),
    (
        ("tushare", {"supportedType": "tushare"}, {}),
        ("finnhub", {"supportedType": "finnhub"}, {}),
        ("fred", {"supportedType": "fred"}, {}),
        ("coingecko_pro", {"supportedType": "coingecko_pro"}, {}),
        ("coinglass", {"supportedType": "coinglass"}, {}),
        ("glassnode", {"supportedType": "glassnode"}, {}),
    ),
)
def test_data_source_health_tester_requires_credentials_for_keyed_sources(
    source_type: str,
    instance: dict[str, str],
    env: dict[str, str],
) -> None:
    tester = ui_runtime_checks.build_data_source_health_tester(env=env)
    with pytest.raises(RuntimeError, match="credential_missing"):
        tester(instance)


def test_settings_enhanced_sources_keyed_source_without_key_is_rejected_before_probe() -> None:
    service = DataSourceSettingsService(
        health_tester=ui_runtime_checks.build_data_source_health_tester(env={}),
        display_decision_source=lambda: (_show_decision("finnhub", market=Market.US),),
    )

    with pytest.raises(UiBoundaryError) as exc:
        service.test_data_source_instance(
            {
                "supportedType": "finnhub",
                "displayName": "Finnhub",
                "enabled": False,
            },
            request_id="s05-finnhub-missing-key",
        )

    assert exc.value.code == "INVALID_INPUT"
    assert "密钥" in exc.value.user_message


def test_settings_enhanced_sources_probe_credential_missing_is_translated_to_ui_error_message() -> None:
    def _raise_credential_missing(_: dict[str, object]) -> dict[str, object]:
        raise RuntimeError("datasource_test_failed: credential_missing")

    service = DataSourceSettingsService(
        health_tester=_raise_credential_missing,
        display_decision_source=lambda: (_show_decision("coingecko_pro", market=Market.CRYPTO),),
    )

    with pytest.raises(UiBoundaryError) as exc:
        service.test_data_source_instance(
            {
                "supportedType": "coingecko_pro",
                "displayName": "CoinGecko Pro",
                "enabled": False,
                "apiKeyReplacement": "cg-pro-token",
            },
            request_id="s05-coingecko-pro-credential-message",
        )

    assert exc.value.code == "DATASOURCE_TEST_FAILED"
    assert "密钥未配置" in exc.value.user_message


def test_data_source_health_tester_does_not_expose_requests_probe_surface() -> None:
    assert not hasattr(ui_runtime_checks, "requests")
    assert not hasattr(ui_runtime_checks, "create_tushare_pro")
    assert not hasattr(ui_runtime_checks, "_probe_tushare")


@pytest.mark.parametrize("source_type", tuple(sorted(KEYED_SOURCE_TYPES)))
def test_data_source_health_tester_does_not_validate_provider_payloads(source_type: str) -> None:
    tester = ui_runtime_checks.build_data_source_health_tester(env={})
    result = tester({"supportedType": source_type, "apiKeyReplacement": "k"})
    assert result["status"] == "validated"
    assert result["evidence"]["dataNeedRequiredForRemoteValidation"] is True
