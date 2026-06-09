from __future__ import annotations

import pytest
from claw_trade.data_gateway.models import Market
from claw_trade.ui_backend import data_source_runtime_checks as ui_runtime_checks
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
    "alpha_vantage",
    "fmp",
    "polygon",
    "finnhub",
    "fred",
    "tiingo",
    "nasdaq_data_link",
    "coingecko_pro",
    "coinmarketcap",
    "coinglass",
)


def _show_decision(provider_id: str, *, market: Market) -> ProviderDisplayDecision:
    return ProviderDisplayDecision(
        provider_id=provider_id,
        market=market,
        display_status=ProviderDisplayStatus.SHOW,
        reason="test main-chain evidence",
        requires_user_credential=True,
        changes_report_or_select_result=True,
        writes_mongo_and_evidence=True,
        enters_domain_pack=True,
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


def test_data_source_health_tester_dispatches_all_fixed_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[str] = []

    def _record(name: str):
        def _probe(**_: object) -> None:
            called.append(name)

        return _probe

    monkeypatch.setattr(ui_runtime_checks, "_probe_tushare", _record("tushare"))
    monkeypatch.setattr(ui_runtime_checks, "_probe_alpha_vantage", _record("alpha_vantage"))
    monkeypatch.setattr(ui_runtime_checks, "_probe_fmp", _record("fmp"))
    monkeypatch.setattr(ui_runtime_checks, "_probe_polygon", _record("polygon"))
    monkeypatch.setattr(ui_runtime_checks, "_probe_finnhub", _record("finnhub"))
    monkeypatch.setattr(ui_runtime_checks, "_probe_fred", _record("fred"))
    monkeypatch.setattr(ui_runtime_checks, "_probe_tiingo", _record("tiingo"))
    monkeypatch.setattr(ui_runtime_checks, "_probe_nasdaq_data_link", _record("nasdaq_data_link"))
    monkeypatch.setattr(ui_runtime_checks, "_probe_coingecko_pro", _record("coingecko_pro"))
    monkeypatch.setattr(ui_runtime_checks, "_probe_coinmarketcap", _record("coinmarketcap"))
    monkeypatch.setattr(ui_runtime_checks, "_probe_coinglass", _record("coinglass"))

    tester = ui_runtime_checks.build_data_source_health_tester(env={})
    for source_type in PROBE_BACKED_SOURCE_TYPES:
        result = tester({"supportedType": source_type})
        assert result["status"] == "validated"

    assert called == list(PROBE_BACKED_SOURCE_TYPES)


@pytest.mark.parametrize(
    ("source_type", "instance", "env"),
    (
        ("tushare", {"supportedType": "tushare"}, {}),
        ("alpha_vantage", {"supportedType": "alpha_vantage"}, {}),
        ("fmp", {"supportedType": "fmp"}, {}),
        ("polygon", {"supportedType": "polygon"}, {}),
        ("finnhub", {"supportedType": "finnhub"}, {}),
        ("fred", {"supportedType": "fred"}, {}),
        ("tiingo", {"supportedType": "tiingo"}, {}),
        ("nasdaq_data_link", {"supportedType": "nasdaq_data_link"}, {}),
        ("coingecko_pro", {"supportedType": "coingecko_pro"}, {}),
        ("coinmarketcap", {"supportedType": "coinmarketcap"}, {}),
        ("coinglass", {"supportedType": "coinglass"}, {}),
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
        display_decision_source=lambda: (_show_decision("alpha_vantage", market=Market.US),),
    )

    with pytest.raises(UiBoundaryError) as exc:
        service.test_data_source_instance(
            {
                "supportedType": "alpha_vantage",
                "displayName": "Alpha Vantage",
                "enabled": False,
            },
            request_id="s05-alpha-vantage-missing-key",
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


class _HttpProbeResponse:
    def __init__(self, *, status_code: int = 200, payload: object | None = None) -> None:
        self.status_code = status_code
        self._payload = payload if payload is not None else {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"http_{self.status_code}")

    def json(self) -> object:
        return self._payload


@pytest.mark.parametrize(
    ("source_type", "credential", "payload"),
    (
        ("alpha_vantage", "k", {"Error Message": "bad key"}),
        ("fmp", "k", {"error": "bad key"}),
        ("polygon", "k", {"status": "ERROR", "error": "bad key"}),
        ("finnhub", "k", {"error": "invalid token"}),
        ("fred", "k", {"error_message": "bad key"}),
        ("tiingo", "k", {"detail": "bad key"}),
        ("nasdaq_data_link", "k", {"quandl_error": {"code": "QEAx01"}}),
        ("coingecko_pro", "k", {"error": "throttled"}),
        ("coinmarketcap", "k", {"status": {"error_code": 1001, "error_message": "bad key"}}),
        ("coinglass", "k", {"message": "bad key"}),
    ),
)
def test_data_source_health_tester_does_not_validate_error_payloads(
    monkeypatch: pytest.MonkeyPatch,
    source_type: str,
    credential: str,
    payload: dict[str, object],
) -> None:
    def _record_get(*_: object, **__: object) -> _HttpProbeResponse:
        return _HttpProbeResponse(payload=payload)

    monkeypatch.setattr(ui_runtime_checks.requests, "get", _record_get)
    tester = ui_runtime_checks.build_data_source_health_tester(env={})
    with pytest.raises(RuntimeError):
        tester({"supportedType": source_type, "apiKeyReplacement": credential})
