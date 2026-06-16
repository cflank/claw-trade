from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Mapping, Protocol

from claw_trade.data_gateway.execution.rate_limiter import RateLimitPolicy


class DataSourceSettingsLike(Protocol):
    def get_instance(self, name: str) -> Mapping[str, Any] | None: ...


_PROVIDER_TO_SOURCE_TYPE: dict[str, str] = {
    "cn_a_primary": "tushare",
    "cn_a_tushare_fundamental": "tushare",
    "cn_a_tushare_realtime": "tushare",
    "hk_tushare": "tushare",
    "us_finnhub_data": "finnhub",
    "hk_finnhub_data": "finnhub",
    "us_fred_macro": "fred",
    "crypto_coingecko_market": "coingecko_pro",
    "crypto_coinglass_derivatives": "coinglass",
    "crypto_glassnode_onchain": "glassnode",
    "official_api_tushare": "tushare",
    "official_api_finnhub": "finnhub",
    "official_api_fred": "fred",
    "official_api_coingecko_pro": "coingecko_pro",
    "official_api_coinglass": "coinglass",
    "official_api_glassnode": "glassnode",
}

class RateLimitPolicyResolver:
    def __init__(self, *, data_source_settings: DataSourceSettingsLike | None = None) -> None:
        self._data_source_settings = data_source_settings

    def resolve(self, *, provider_id: str, default_policy: Any | None, rate_limit_bucket: str | None = None) -> RateLimitPolicy:
        source_type = provider_rate_limit_namespace(provider_id, rate_limit_bucket=rate_limit_bucket)
        settings = self._settings_for_source_type(source_type)
        if settings is not None:
            policy = _policy_from_settings(settings)
            if policy is not None:
                return policy
        return _policy_without_hard_limit(default_policy)

    def _settings_for_provider(self, provider_id: str) -> Mapping[str, Any] | None:
        return self._settings_for_source_type(provider_rate_limit_namespace(provider_id))

    def _settings_for_source_type(self, source_type: str) -> Mapping[str, Any] | None:
        if self._data_source_settings is None:
            return None
        if not source_type:
            return None
        getter = getattr(self._data_source_settings, "get_instance", None)
        if not callable(getter):
            return None
        return getter(f"data_source:{source_type}")


def _policy_from_settings(settings: Mapping[str, Any]) -> RateLimitPolicy | None:
    max_calls = _optional_int(settings.get("rate_limit_max_calls"))
    window_seconds = _optional_int(settings.get("rate_limit_window_seconds"))
    safety_margin = _optional_int(settings.get("rate_limit_safety_margin")) or 0
    if max_calls is None and window_seconds is None:
        return None
    return RateLimitPolicy(
        window_seconds=window_seconds or 60,
        max_requests=max_calls,
        safety_margin=safety_margin,
    )


def _policy_from_any(raw: Any | None) -> RateLimitPolicy:
    if isinstance(raw, RateLimitPolicy):
        return raw
    if raw is None:
        return RateLimitPolicy(window_seconds=60, max_requests=None)
    return RateLimitPolicy(
        window_seconds=int(_read_attr(raw, "window_seconds", 60) or 60),
        max_requests=_optional_int(_read_attr(raw, "max_requests", _read_attr(raw, "max_calls", None))),
        safety_margin=int(_read_attr(raw, "safety_margin", 0) or 0),
    )


def _policy_without_hard_limit(raw: Any | None) -> RateLimitPolicy:
    if raw is None:
        return RateLimitPolicy(window_seconds=60, max_requests=None)
    return RateLimitPolicy(
        window_seconds=int(_read_attr(raw, "window_seconds", 60) or 60),
        max_requests=None,
    )


def provider_rate_limit_namespace(provider_id: str, *, rate_limit_bucket: str | None = None) -> str:
    bucket_source = _source_type_from_rate_limit_bucket(rate_limit_bucket)
    if bucket_source:
        return bucket_source
    return _PROVIDER_TO_SOURCE_TYPE.get(provider_id) or provider_id


def _source_type_from_rate_limit_bucket(rate_limit_bucket: str | None) -> str | None:
    text = str(rate_limit_bucket or "").strip()
    if not text.startswith("ratelimit:"):
        return None
    source_type = text.split(":", 1)[1].strip()
    return source_type or None


def _read_attr(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return int(text)


def policy_to_namespace(policy: RateLimitPolicy) -> Any:
    return SimpleNamespace(
        window_seconds=policy.window_seconds,
        max_requests=policy.max_requests,
        safety_margin=policy.safety_margin,
        window_anchor=policy.window_anchor,
    )
