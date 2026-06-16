from __future__ import annotations

from typing import Any, Callable, Mapping
from urllib.parse import urlparse

from claw_trade.ui_contracts.enums import MarketProfile

_SUPPORTED_PROBE_TYPES = frozenset(
    {
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
    }
)
_CREDENTIAL_REQUIRED_TYPES = frozenset({"tushare", "finnhub", "fred", "coingecko", "coingecko_pro", "coinglass", "glassnode"})


def build_price_alert_quote_provider(
    *,
    env: Mapping[str, str] | None = None,
    now_provider: Callable[[], object] | None = None,
    **evidence_chain: object,
) -> Callable[[str, MarketProfile], dict[str, float]]:
    del env, now_provider
    required = ("evidence_helper", "cache_store", "rate_limit_store", "single_flight", "attempt_store")

    def _provider(instrument_code: str, market_profile: MarketProfile) -> dict[str, float]:
        del instrument_code, market_profile
        if any(evidence_chain.get(key) is None for key in required):
            raise RuntimeError("datasource_test_failed: evidence_chain_unavailable")
        raise RuntimeError("datasource_test_failed: price_alert_quote_provider_unimplemented")

    return _provider


def build_data_source_health_tester(
    *,
    env: Mapping[str, str] | None = None,
    now_provider: Callable[[], object] | None = None,
) -> Callable[[Mapping[str, Any]], Mapping[str, Any]]:
    del env, now_provider

    def _tester(instance: Mapping[str, Any]) -> Mapping[str, Any]:
        supported_type = str(instance.get("supportedType", "")).strip()
        _validate_supported_type(supported_type)
        _validate_endpoint(instance)
        if supported_type in _CREDENTIAL_REQUIRED_TYPES:
            _required_credential(instance=instance)
        return _probe_only_success(supported_type)

    return _tester


def _probe_only_success(supported_type: str) -> Mapping[str, Any]:
    return {
        "status": "validated",
        "message": "本地配置校验通过。未直接出网；真实验证将在下一次 DataNeed 调用中完成。",
        "impact": "low",
        "requestKind": "ui_probe",
        "consumerType": "ui_probe",
        "dataRequirement": {
            "requirementId": f"ui_probe:{supported_type}:connection",
            "dataType": "provider_connection_probe",
            "requiredLevel": "optional",
            "consumerType": "ui_probe",
            "consumerId": f"settings:{supported_type}",
            "domain": "settings",
        },
        "evidence": {
            "kind": "config_only",
            "probeOnly": True,
            "mainChainEvidence": False,
            "remoteSuccess": False,
            "dataNeedRequiredForRemoteValidation": True,
        },
    }


def _validate_supported_type(supported_type: str) -> None:
    if supported_type not in _SUPPORTED_PROBE_TYPES:
        raise RuntimeError("datasource_test_failed: probe_not_supported")


def _validate_endpoint(instance: Mapping[str, Any]) -> None:
    endpoint = _optional_text(instance.get("endpointUrl"))
    if not endpoint:
        return
    parsed = urlparse(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeError("datasource_test_failed: invalid_endpoint")


def _required_credential(*, instance: Mapping[str, Any]) -> str:
    provided = _optional_text(instance.get("apiKeyReplacement"))
    if provided:
        return provided
    raise RuntimeError("datasource_test_failed: credential_missing")


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
