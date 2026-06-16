from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from hashlib import sha1
from typing import Any, Callable, Mapping
from uuid import uuid4

from claw_trade.ui_backend.settings_service import EnvLocalAllowlistWriter, UiBoundaryError
from claw_trade.ui_contracts.scope_guard import (
    FirstVersionScopeError,
    assert_data_source_type_supported,
)


class ProviderDisplayStatus(str, Enum):
    SHOW = "show"
    HIDE = "hide"


@dataclass(frozen=True)
class ProviderDisplayDecision:
    provider_id: str
    market: Any
    display_status: ProviderDisplayStatus
    reason: str
    requires_user_credential: bool
    changes_report_or_select_result: bool
    writes_mongo_and_evidence: bool
    enters_data_result_flow: bool
    consumed_by_worker_or_strategy: bool
    live_fresh_evidence_ref: str | None = None
    probe_only: bool = False


@dataclass(frozen=True)
class _SupportedSourceProfile:
    supported_type: str
    group: str
    default_display_name: str
    requires_key: bool
    env_key_map: dict[str, str]
    default_endpoint_url: str | None = None


def _api_env_map(prefix: str, *, api_key: str, endpoint_url: str | None = None, header_name: str | None = None) -> dict[str, str]:
    mapping = {
        "api_key": api_key,
        "rate_limit_max_calls": f"{prefix}_RATE_LIMIT_MAX_CALLS",
        "rate_limit_window_seconds": f"{prefix}_RATE_LIMIT_WINDOW_SECONDS",
        "rate_limit_safety_margin": f"{prefix}_RATE_LIMIT_SAFETY_MARGIN",
        "rate_limit_overflow": f"{prefix}_RATE_LIMIT_OVERFLOW",
    }
    if endpoint_url:
        mapping["endpoint_url"] = endpoint_url
    if header_name:
        mapping["header_name"] = header_name
    return mapping


_SUPPORTED_SOURCE_PROFILES: tuple[_SupportedSourceProfile, ...] = (
    _SupportedSourceProfile(
        supported_type="tushare",
        group="cn_a_data",
        default_display_name="Tushare",
        requires_key=True,
        env_key_map=_api_env_map("TUSHARE", api_key="TUSHARE_TOKEN", endpoint_url="TUSHARE_HTTP_URL"),
        default_endpoint_url="https://api.tushare.pro",
    ),
    _SupportedSourceProfile(
        supported_type="akshare",
        group="cn_a_data",
        default_display_name="AKShare",
        requires_key=False,
        env_key_map={"endpoint_url": "AKSHARE_HTTP_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="eastmoney",
        group="cn_a_data",
        default_display_name="东方财富",
        requires_key=False,
        env_key_map={"endpoint_url": "EASTMONEY_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="eastmoney_guba",
        group="cn_a_social",
        default_display_name="东方财富股吧",
        requires_key=False,
        env_key_map={"endpoint_url": "EASTMONEY_GUBA_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="xueqiu",
        group="cn_a_social",
        default_display_name="雪球",
        requires_key=False,
        env_key_map={"api_key": "XUEQIU_TOKEN", "endpoint_url": "XUEQIU_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="baostock",
        group="cn_a_data",
        default_display_name="BaoStock",
        requires_key=False,
        env_key_map={"endpoint_url": "BAOSTOCK_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="tonghuashun",
        group="cn_a_social",
        default_display_name="同花顺",
        requires_key=False,
        env_key_map={"api_key": "TONGHUASHUN_TOKEN", "endpoint_url": "TONGHUASHUN_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="cninfo",
        group="cn_a_news",
        default_display_name="巨潮资讯",
        requires_key=False,
        env_key_map={"endpoint_url": "CNINFO_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="sina_finance",
        group="cn_a_data",
        default_display_name="新浪财经",
        requires_key=False,
        env_key_map={"endpoint_url": "SINA_FINANCE_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="tencent_finance",
        group="cn_a_data",
        default_display_name="腾讯财经",
        requires_key=False,
        env_key_map={"endpoint_url": "TENCENT_FINANCE_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="csindex",
        group="cn_a_data",
        default_display_name="中证指数",
        requires_key=False,
        env_key_map={"endpoint_url": "CSINDEX_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="hkex",
        group="hk_data",
        default_display_name="HKEX",
        requires_key=False,
        env_key_map={"endpoint_url": "HKEX_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="aastocks",
        group="hk_data",
        default_display_name="AASTOCKS",
        requires_key=False,
        env_key_map={"endpoint_url": "AASTOCKS_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="futu",
        group="global_data",
        default_display_name="富途 OpenD",
        requires_key=False,
        env_key_map={"api_key": "FUTU_API_KEY", "endpoint_url": "FUTU_OPEND_HOST"},
    ),
    _SupportedSourceProfile(
        supported_type="yahoo_finance",
        group="global_data",
        default_display_name="Yahoo Finance",
        requires_key=False,
        env_key_map={"endpoint_url": "YAHOO_FINANCE_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="finnhub",
        group="global_data",
        default_display_name="Finnhub",
        requires_key=True,
        env_key_map=_api_env_map("FINNHUB", api_key="FINNHUB_API_KEY", endpoint_url="FINNHUB_BASE_URL"),
        default_endpoint_url="https://finnhub.io/api/v1",
    ),
    _SupportedSourceProfile(
        supported_type="sec_edgar",
        group="global_news",
        default_display_name="SEC EDGAR",
        requires_key=False,
        env_key_map={"endpoint_url": "SEC_EDGAR_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="gdelt",
        group="global_news",
        default_display_name="GDELT",
        requires_key=False,
        env_key_map={"endpoint_url": "GDELT_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="stocktwits",
        group="global_social",
        default_display_name="Stocktwits",
        requires_key=False,
        env_key_map={"endpoint_url": "STOCKTWITS_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="fred",
        group="global_macro",
        default_display_name="FRED",
        requires_key=True,
        env_key_map=_api_env_map("FRED", api_key="FRED_API_KEY", endpoint_url="FRED_BASE_URL"),
        default_endpoint_url="https://api.stlouisfed.org",
    ),
    _SupportedSourceProfile(
        supported_type="world_bank",
        group="global_macro",
        default_display_name="World Bank",
        requires_key=False,
        env_key_map={"endpoint_url": "WORLD_BANK_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="oecd",
        group="global_macro",
        default_display_name="OECD",
        requires_key=False,
        env_key_map={"endpoint_url": "OECD_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="imf",
        group="global_macro",
        default_display_name="IMF",
        requires_key=False,
        env_key_map={"endpoint_url": "IMF_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="bis",
        group="global_macro",
        default_display_name="BIS",
        requires_key=False,
        env_key_map={"endpoint_url": "BIS_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="eurostat",
        group="global_macro",
        default_display_name="Eurostat",
        requires_key=False,
        env_key_map={"endpoint_url": "EUROSTAT_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="ecb",
        group="global_macro",
        default_display_name="ECB",
        requires_key=False,
        env_key_map={"endpoint_url": "ECB_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="bls",
        group="global_macro",
        default_display_name="BLS",
        requires_key=False,
        env_key_map={"api_key": "BLS_API_KEY", "endpoint_url": "BLS_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="us_treasury",
        group="global_macro",
        default_display_name="US Treasury",
        requires_key=False,
        env_key_map={"endpoint_url": "US_TREASURY_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="coingecko",
        group="crypto_data",
        default_display_name="CoinGecko Demo",
        requires_key=True,
        env_key_map={"api_key": "COINGECKO_DEMO_API_KEY", "endpoint_url": "COINGECKO_BASE_URL"},
        default_endpoint_url="https://api.coingecko.com/api/v3",
    ),
    _SupportedSourceProfile(
        supported_type="coingecko_pro",
        group="crypto_data",
        default_display_name="CoinGecko Pro",
        requires_key=True,
        env_key_map=_api_env_map("COINGECKO_PRO", api_key="COINGECKO_PRO_API_KEY", endpoint_url="COINGECKO_PRO_BASE_URL"),
        default_endpoint_url="https://pro-api.coingecko.com/api/v3",
    ),
    _SupportedSourceProfile(
        supported_type="binance",
        group="crypto_data",
        default_display_name="Binance",
        requires_key=False,
        env_key_map={"endpoint_url": "BINANCE_SPOT_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="okx",
        group="crypto_data",
        default_display_name="OKX",
        requires_key=False,
        env_key_map={"endpoint_url": "OKX_API_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="ccxt",
        group="crypto_data",
        default_display_name="CCXT",
        requires_key=False,
        env_key_map={"endpoint_url": "CCXT_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="coinglass",
        group="crypto_data",
        default_display_name="Coinglass",
        requires_key=True,
        env_key_map=_api_env_map(
            "COINGLASS",
            api_key="COINGLASS_API_KEY",
            endpoint_url="COINGLASS_API_BASE",
            header_name="COINGLASS_API_HEADER_NAME",
        ),
        default_endpoint_url="https://open-api-v4.coinglass.com",
    ),
    _SupportedSourceProfile(
        supported_type="glassnode",
        group="crypto_data",
        default_display_name="Glassnode",
        requires_key=True,
        env_key_map={"api_key": "GLASSNODE_API_KEY", "endpoint_url": "GLASSNODE_BASE_URL"},
        default_endpoint_url="https://api.glassnode.com",
    ),
    _SupportedSourceProfile(
        supported_type="alternative_me",
        group="crypto_social",
        default_display_name="Alternative.me",
        requires_key=False,
        env_key_map={"endpoint_url": "ALTERNATIVE_ME_BASE_URL"},
    ),
    _SupportedSourceProfile(
        supported_type="defillama",
        group="crypto_data",
        default_display_name="DeFiLlama",
        requires_key=False,
        env_key_map={"endpoint_url": "DEFILLAMA_BASE_URL"},
    ),
)

_SUPPORTED_SOURCE_BY_TYPE: dict[str, _SupportedSourceProfile] = {
    item.supported_type: item for item in _SUPPORTED_SOURCE_PROFILES
}
_PROVIDER_BACKED_API_SETTINGS_SOURCE_TYPES: tuple[str, ...] = (
    "tushare",
    "finnhub",
    "fred",
    "coingecko",
    "coingecko_pro",
    "coinglass",
    "glassnode",
)
_SETTINGS_SOURCE_PROFILES: tuple[_SupportedSourceProfile, ...] = tuple(
    item
    for item in _SUPPORTED_SOURCE_PROFILES
    if item.requires_key and item.supported_type in _PROVIDER_BACKED_API_SETTINGS_SOURCE_TYPES
)
_SETTINGS_SOURCE_BY_TYPE: dict[str, _SupportedSourceProfile] = {
    item.supported_type: item for item in _SETTINGS_SOURCE_PROFILES
}
SUPPORTED_DATA_SOURCE_TYPES: tuple[str, ...] = tuple(item.supported_type for item in _SETTINGS_SOURCE_PROFILES)

_DATA_SOURCE_ENV_KEY_MAP: dict[str, dict[str, str]] = {
    item.supported_type: dict(item.env_key_map) for item in _SETTINGS_SOURCE_PROFILES
}

_DATA_SOURCE_ENV_ALLOWLIST: tuple[str, ...] = tuple(
    sorted({value for item in _DATA_SOURCE_ENV_KEY_MAP.values() for value in item.values()})
)


@dataclass(frozen=True)
class DataSourceInstanceForUser:
    instance_id: str
    supported_type: str
    group: str
    display_name: str
    enabled: bool
    api_key_masked: str | None
    endpoint_url: str | None
    state: str
    last_success_at: str | None
    last_test_at: str | None
    rate_limit_max_calls: int | None
    rate_limit_window_seconds: int | None
    rate_limit_safety_margin: int | None
    rate_limit_overflow: str | None

    def to_user_dict(self) -> dict[str, Any]:
        return {
            "instanceId": self.instance_id,
            "supportedType": self.supported_type,
            "group": self.group,
            "displayName": self.display_name,
            "enabled": self.enabled,
            "apiKeyMasked": self.api_key_masked,
            "endpointUrl": self.endpoint_url,
            "state": self.state,
            "lastSuccessAt": self.last_success_at,
            "lastTestAt": self.last_test_at,
            "rateLimitMaxCalls": self.rate_limit_max_calls,
            "rateLimitWindowSeconds": self.rate_limit_window_seconds,
            "rateLimitSafetyMargin": self.rate_limit_safety_margin,
            "rateLimitOverflow": self.rate_limit_overflow,
        }


@dataclass(frozen=True)
class DataSourceTestResult:
    state: str
    health_event: dict[str, Any]
    can_enable: bool

    def to_user_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "healthEvent": self.health_event,
            "canEnable": self.can_enable,
        }


class InMemoryDataSourceStore:
    def __init__(self) -> None:
        self._items: dict[str, dict[str, Any]] = {}

    def get(self, instance_id: str) -> dict[str, Any] | None:
        return self._items.get(instance_id)

    def list_instances(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._items.values())

    def upsert(self, record: Mapping[str, Any]) -> dict[str, Any]:
        instance_id = str(record.get("id") or record.get("instance_id") or uuid4().hex)
        saved = dict(record)
        saved["id"] = instance_id
        self._items[instance_id] = saved
        return saved

    def clear(self) -> None:
        self._items.clear()


class InMemorySecretStore:
    def __init__(self) -> None:
        self._values: dict[str, str] = {}

    def replace(self, *, old_ref: str | None, value: str, scope: str) -> str:
        _ = old_ref
        ref = f"{scope}:{sha1(value.encode('utf-8')).hexdigest()[:12]}"
        self._values[ref] = value
        return ref

    def get(self, secret_ref: str | None) -> str | None:
        if not secret_ref:
            return None
        return self._values.get(secret_ref)

    def clear(self) -> None:
        self._values.clear()


class DataSourceSettingsService:
    def __init__(
        self,
        *,
        data_source_store: InMemoryDataSourceStore | None = None,
        secret_store: InMemorySecretStore | None = None,
        env_writer: EnvLocalAllowlistWriter | None = None,
        health_tester: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
        display_decision_source: Callable[[], tuple[ProviderDisplayDecision, ...]] | None = None,
    ) -> None:
        self._store = data_source_store or InMemoryDataSourceStore()
        self._secret_store = secret_store or InMemorySecretStore()
        self._env_writer = env_writer
        self._health_tester = health_tester or _default_health_tester
        self._display_decision_source = display_decision_source or _default_display_decision_source
        self._idempotency: dict[str, Any] = {}

    def list_data_sources(self) -> dict[str, Any]:
        visible_profiles = self._visible_settings_profiles()
        stored_by_type: dict[str, Mapping[str, Any]] = {}
        for item in self._store.list_instances():
            supported_type = str(item.get("supported_type", "")).strip()
            if supported_type in visible_profiles:
                stored_by_type[supported_type] = item
        instances = [
            to_data_source_instance_for_user(stored_by_type.get(supported_type) or _built_in_source_row(supported_type)).to_user_dict()
            for supported_type in visible_profiles
        ]
        return {
            "supportedTypes": tuple(visible_profiles),
            "instances": instances,
        }

    def test_data_source_instance(self, instance_draft: Mapping[str, Any], request_id: str) -> dict[str, Any]:
        if request_id in self._idempotency:
            return self._idempotency[request_id]
        normalized = self._validate_supported_input(instance_draft)
        existing = self._store.get(normalized["instanceId"]) if normalized.get("instanceId") else None
        out = self._probe_health(
            normalized,
            requires_key=self._resolve_requires_key(normalized=normalized, existing=existing),
            existing_credential_ref=self._credential_ref(existing),
        )
        self._idempotency[request_id] = out
        return out

    def save_data_source_instance(self, data: Mapping[str, Any], request_id: str) -> dict[str, Any]:
        if request_id in self._idempotency:
            return self._idempotency[request_id]
        normalized = self._validate_supported_input(data)
        existing = self._store.get(normalized["instanceId"]) if normalized.get("instanceId") else None
        credential_ref = str(existing.get("credential_ref")) if existing and existing.get("credential_ref") else None
        api_key_replacement = str(data.get("apiKeyReplacement", "")).strip()
        if api_key_replacement:
            credential_ref = self._secret_store.replace(
                old_ref=credential_ref,
                value=api_key_replacement,
                scope="data_source",
            )
        enabled = bool(normalized["enabled"])
        state = str(normalized["state"])
        requires_key = self._resolve_requires_key(normalized=normalized, existing=existing)
        if enabled:
            self._probe_health(
                normalized,
                requires_key=requires_key,
                existing_credential_ref=credential_ref,
            )
            state = "validated"
        saved = self._store.upsert(
            {
                "id": normalized.get("instanceId") or None,
                "supported_type": normalized["supportedType"],
                "group": normalized["group"],
                "display_name": normalized["displayName"],
                "enabled": enabled,
                "credential_ref": credential_ref,
                "endpoint_url": normalized["endpoint_url"],
                "proxy_url": normalized["proxy_url"],
                "header_name": normalized["header_name"],
                "priority": int(normalized["priority"]),
                "state": state,
                "last_success_at": normalized["last_success_at"],
                "last_test_at": normalized["last_test_at"] or _now_iso(),
                "requires_key": requires_key,
                "rate_limit_max_calls": normalized["rate_limit_max_calls"],
                "rate_limit_window_seconds": normalized["rate_limit_window_seconds"],
                "rate_limit_safety_margin": normalized["rate_limit_safety_margin"],
                "rate_limit_overflow": normalized["rate_limit_overflow"],
            }
        )
        self._write_env_updates(saved, api_key_replacement)
        out = to_data_source_instance_for_user(saved).to_user_dict()
        self._idempotency[request_id] = out
        return out

    def reset_to_defaults(self, request_id: str) -> dict[str, Any]:
        if request_id in self._idempotency:
            return self._idempotency[request_id]
        self._store.clear()
        self._secret_store.clear()
        self._idempotency.clear()
        if self._env_writer is not None:
            self._env_writer.clear_allowed_env_keys(_DATA_SOURCE_ENV_ALLOWLIST)
        out = {
            "status": "reset",
            "updatedAt": _now_iso(),
            **self.list_data_sources(),
        }
        self._idempotency[request_id] = out
        return out

    def _validate_supported_input(self, data: Mapping[str, Any]) -> dict[str, Any]:
        supported_type = str(data.get("supportedType", "")).strip()
        if not supported_type:
            raise UiBoundaryError("INVALID_INPUT", "数据源类型不能为空。")
        try:
            assert_data_source_type_supported(supported_type)
        except FirstVersionScopeError as exc:
            raise UiBoundaryError("INVALID_INPUT", str(exc)) from exc
        visible_profiles = self._visible_settings_profiles()
        profile = visible_profiles.get(supported_type)
        if profile is None:
            raise UiBoundaryError("INVALID_INPUT", f"当前数据源还没有数据网关接入和设置页连接测试，暂不支持配置：{supported_type}。")
        if data.get("customJsonMapping") or data.get("custom_json_mapping") or data.get("customScript"):
            raise UiBoundaryError("INVALID_INPUT", "当前不支持自定义数据映射或脚本。")
        return {
            "instanceId": str(data.get("instanceId", "")).strip() or None,
            "supportedType": supported_type,
            "group": profile.group,
            "displayName": profile.default_display_name,
            "enabled": bool(data.get("enabled", False)),
            "endpointUrl": _optional_str(data.get("endpointUrl")) or profile.default_endpoint_url,
            "endpoint_url": _optional_str(data.get("endpointUrl")) or profile.default_endpoint_url,
            "proxy_url": None,
            "header_name": None,
            "priority": 100,
            "state": str(data.get("state", "draft")).strip() or "draft",
            "last_success_at": _optional_str(data.get("lastSuccessAt")),
            "last_test_at": _optional_str(data.get("lastTestAt")),
            "requiresKey": profile.requires_key,
            "apiKeyReplacement": _optional_str(data.get("apiKeyReplacement")),
            "rate_limit_max_calls": _optional_positive_int(data.get("rateLimitMaxCalls")),
            "rate_limit_window_seconds": _optional_positive_int(data.get("rateLimitWindowSeconds")),
            "rate_limit_safety_margin": _optional_non_negative_int(data.get("rateLimitSafetyMargin")),
            "rate_limit_overflow": _optional_overflow(data.get("rateLimitOverflow")),
        }

    def _probe_health(
        self,
        normalized: Mapping[str, Any],
        *,
        requires_key: bool,
        existing_credential_ref: str | None,
    ) -> dict[str, Any]:
        existing_credential_value = self._secret_store.get(existing_credential_ref)
        self._require_probe_key(
            normalized=normalized,
            requires_key=requires_key,
            has_existing_credential=bool(existing_credential_ref),
        )
        probe_input = dict(normalized)
        if not _optional_str(probe_input.get("apiKeyReplacement")) and existing_credential_value:
            probe_input["apiKeyReplacement"] = existing_credential_value
        try:
            health = self._health_tester(probe_input)
            status = str(health.get("status", "")).strip().lower()
            if status not in {"validated", "enabled_candidate", "enabled"}:
                message = str(health.get("message", "数据源测试未通过。")).strip() or "数据源测试未通过。"
                raise UiBoundaryError("DATASOURCE_TEST_FAILED", message)
            return DataSourceTestResult(
                state="validated",
                health_event=to_data_source_health_event_for_user(
                    {
                        "displayName": normalized["displayName"],
                        "status": status,
                        "userMessage": str(health.get("message", "连接测试通过。")),
                        "impact": str(health.get("impact", "low")),
                        "occurredAt": _now_iso(),
                    }
                ),
                can_enable=True,
            ).to_user_dict()
        except UiBoundaryError:
            raise
        except Exception as exc:  # pragma: no cover - 防御性路径
            raise UiBoundaryError("DATASOURCE_TEST_FAILED", _probe_error_message(exc)) from exc

    @staticmethod
    def _resolve_requires_key(*, normalized: Mapping[str, Any], existing: Mapping[str, Any] | None) -> bool:
        if existing is not None and "requires_key" in existing:
            return bool(existing.get("requires_key"))
        if existing is not None and "requiresKey" in existing:
            return bool(existing.get("requiresKey"))
        return bool(normalized.get("requiresKey", True))

    @staticmethod
    def _credential_ref(existing: Mapping[str, Any] | None) -> str | None:
        if existing is None:
            return None
        return _optional_str(existing.get("credential_ref"))

    @staticmethod
    def _require_probe_key(
        *,
        normalized: Mapping[str, Any],
        requires_key: bool,
        has_existing_credential: bool,
    ) -> None:
        if not requires_key:
            return
        if has_existing_credential:
            return
        if _optional_str(normalized.get("apiKeyReplacement")):
            return
        raise UiBoundaryError("INVALID_INPUT", "需要先填写密钥，再进行测试或启用。")

    def _write_env_updates(self, saved: Mapping[str, Any], api_key_replacement: str) -> None:
        if self._env_writer is None:
            return
        mapping = _DATA_SOURCE_ENV_KEY_MAP.get(str(saved["supported_type"]), {})
        updates: dict[str, str] = {}
        if api_key_replacement and mapping.get("api_key"):
            updates[mapping["api_key"]] = api_key_replacement
        endpoint = _optional_str(saved.get("endpoint_url"))
        proxy = _optional_str(saved.get("proxy_url"))
        header = _optional_str(saved.get("header_name"))
        if endpoint and mapping.get("endpoint_url"):
            updates[mapping["endpoint_url"]] = endpoint
        if proxy and mapping.get("proxy_url"):
            updates[mapping["proxy_url"]] = proxy
        if header and mapping.get("header_name"):
            updates[mapping["header_name"]] = header
        clear_keys: list[str] = []
        for setting_name, record_key in (
            ("rate_limit_max_calls", "rate_limit_max_calls"),
            ("rate_limit_window_seconds", "rate_limit_window_seconds"),
            ("rate_limit_safety_margin", "rate_limit_safety_margin"),
            ("rate_limit_overflow", "rate_limit_overflow"),
        ):
            value = saved.get(record_key)
            env_key = mapping.get(setting_name)
            if value is not None and env_key:
                updates[env_key] = str(value)
            elif env_key:
                clear_keys.append(env_key)
        if updates:
            self._env_writer.write_allowed_env_keys(updates)
        if clear_keys:
            self._env_writer.clear_allowed_env_keys(tuple(clear_keys))

    def _visible_settings_profiles(self) -> dict[str, _SupportedSourceProfile]:
        decisions = self._display_decision_source()
        visible: dict[str, _SupportedSourceProfile] = dict(_SETTINGS_SOURCE_BY_TYPE)
        for decision in decisions:
            if decision.display_status != ProviderDisplayStatus.SHOW or not decision.requires_user_credential:
                continue
            for supported_type in _supported_types_for_display_decision(decision):
                profile = _SETTINGS_SOURCE_BY_TYPE.get(supported_type)
                if profile is not None and profile.requires_key:
                    visible[profile.supported_type] = profile
        return visible


def to_data_source_instance_for_user(instance: Mapping[str, Any]) -> DataSourceInstanceForUser:
    supported_type = str(instance.get("supported_type", ""))
    profile = _SUPPORTED_SOURCE_BY_TYPE.get(supported_type)
    credential_ref = _optional_str(instance.get("credential_ref"))
    return DataSourceInstanceForUser(
        instance_id=str(instance.get("id", "")),
        supported_type=supported_type,
        group=profile.group if profile is not None else str(instance.get("group", "custom")),
        display_name=profile.default_display_name if profile is not None else str(instance.get("display_name", "")),
        enabled=bool(instance.get("enabled", False)),
        api_key_masked=mask_secret_ref(credential_ref),
        endpoint_url=_optional_str(instance.get("endpoint_url")) or (profile.default_endpoint_url if profile is not None else None),
        state=str(instance.get("state", "draft")),
        last_success_at=_optional_str(instance.get("last_success_at")),
        last_test_at=_optional_str(instance.get("last_test_at")),
        rate_limit_max_calls=_optional_int(instance.get("rate_limit_max_calls")),
        rate_limit_window_seconds=_optional_int(instance.get("rate_limit_window_seconds")),
        rate_limit_safety_margin=_optional_int(instance.get("rate_limit_safety_margin")),
        rate_limit_overflow=_optional_str(instance.get("rate_limit_overflow")),
    )


def to_data_source_health_event_for_user(event: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "displayName": str(event.get("displayName", "")),
        "status": str(event.get("status", "unknown")),
        "userMessage": str(event.get("userMessage", "")),
        "impact": str(event.get("impact", "medium")),
        "occurredAt": _optional_str(event.get("occurredAt")) or _now_iso(),
    }


def mask_secret_ref(secret_ref: str | None) -> str | None:
    if not secret_ref:
        return None
    suffix = secret_ref[-4:] if len(secret_ref) >= 4 else secret_ref
    return f"***{suffix}"


def allowed_data_source_env_keys() -> tuple[str, ...]:
    return _DATA_SOURCE_ENV_ALLOWLIST


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: Any) -> int | None:
    text = _optional_str(value)
    if text is None:
        return None
    try:
        return int(text)
    except ValueError as exc:
        raise UiBoundaryError("INVALID_INPUT", "限流配置必须填写整数。") from exc


def _optional_positive_int(value: Any) -> int | None:
    number = _optional_int(value)
    if number is None:
        return None
    if number <= 0:
        raise UiBoundaryError("INVALID_INPUT", "限流次数和窗口秒数必须大于 0。")
    return number


def _optional_non_negative_int(value: Any) -> int | None:
    number = _optional_int(value)
    if number is None:
        return None
    if number < 0:
        raise UiBoundaryError("INVALID_INPUT", "限流安全余量和等待秒数不能小于 0。")
    return number


def _optional_overflow(value: Any) -> str | None:
    text = _optional_str(value)
    if text is None:
        return None
    if text not in {"wait", "fail_fast"}:
        raise UiBoundaryError("INVALID_INPUT", "限流超额策略只能是 wait 或 fail_fast。")
    return text


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _default_health_tester(_instance: Mapping[str, Any]) -> Mapping[str, Any]:
    raise UiBoundaryError("DATASOURCE_TEST_FAILED", "当前环境未接入可用的数据源测试能力。")


def _probe_error_message(exc: Exception) -> str:
    text = str(exc).lower()
    if "credential_missing" in text:
        return "密钥未配置，请先补全后再测试。"
    if "probe_not_supported" in text:
        return "当前数据源类型暂不支持实时测试，不能仅凭设置页探测启用。"
    return "数据源连接或鉴权失败，请到设置页更新后重试。"


def _built_in_source_row(supported_type: str) -> dict[str, Any]:
    profile = _SUPPORTED_SOURCE_BY_TYPE[supported_type]
    return {
        "id": f"builtin-{supported_type}",
        "supported_type": profile.supported_type,
        "group": profile.group,
        "display_name": profile.default_display_name,
        "enabled": False,
        "credential_ref": None,
        "endpoint_url": profile.default_endpoint_url,
        "proxy_url": None,
        "header_name": None,
        "priority": 100,
        "state": "draft",
        "last_success_at": None,
        "last_test_at": None,
        "requires_key": profile.requires_key,
        "rate_limit_max_calls": None,
        "rate_limit_window_seconds": None,
        "rate_limit_safety_margin": None,
        "rate_limit_overflow": None,
    }


def _supported_type_label() -> str:
    return ", ".join(SUPPORTED_DATA_SOURCE_TYPES)


def _default_display_decision_source() -> tuple[ProviderDisplayDecision, ...]:
    return ()


_PROVIDER_ID_TO_SETTINGS_SOURCE_TYPES: dict[str, tuple[str, ...]] = {
    "cn_a_primary": ("tushare",),
    "cn_a_tushare_fundamental": ("tushare",),
    "cn_a_tushare_realtime": ("tushare",),
    "hk_tushare": ("tushare",),
    "us_finnhub_data": ("finnhub",),
    "hk_finnhub_data": ("finnhub",),
    "us_fred_macro": ("fred",),
    "crypto_coingecko_market": ("coingecko", "coingecko_pro"),
    "crypto_coinglass_derivatives": ("coinglass",),
    "crypto_glassnode_onchain": ("glassnode",),
}


def _supported_types_for_display_decision(decision: ProviderDisplayDecision) -> tuple[str, ...]:
    direct = _SETTINGS_SOURCE_BY_TYPE.get(decision.provider_id)
    if direct is not None:
        return (direct.supported_type,)
    return _PROVIDER_ID_TO_SETTINGS_SOURCE_TYPES.get(decision.provider_id, ())
