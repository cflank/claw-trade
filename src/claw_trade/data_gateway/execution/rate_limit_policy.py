from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Mapping, Protocol

from claw_trade.data_gateway.execution.rate_limiter import RateLimitPolicy


class DataSourceSettingsLike(Protocol):
    def get_instance(self, name: str) -> Mapping[str, Any] | None: ...


_PROVIDER_TO_SOURCE_TYPE: dict[str, str] = {
    "cn_a_primary": "tushare",
    "cn_a_tushare_fundamental": "tushare",
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

    def resolve(self, *, provider_id: str, default_policy: Any | None) -> RateLimitPolicy:
        source_type = _PROVIDER_TO_SOURCE_TYPE.get(provider_id)
        settings = self._settings_for_provider(provider_id)
        if settings is not None:
            policy = _policy_from_settings(settings)
            if policy is not None:
                return policy
        return _policy_without_hard_limit(default_policy)

    def _settings_for_provider(self, provider_id: str) -> Mapping[str, Any] | None:
        if self._data_source_settings is None:
            return None
        source_type = _PROVIDER_TO_SOURCE_TYPE.get(provider_id)
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
    overflow = _overflow(settings.get("rate_limit_overflow"))
    wait_timeout = _optional_int(settings.get("rate_limit_wait_timeout_seconds")) or 0
    if max_calls is None and window_seconds is None:
        return None
    return RateLimitPolicy(
        window_seconds=window_seconds or 60,
        max_requests=max_calls,
        safety_margin=safety_margin,
        overflow=overflow,
        wait_timeout_seconds=wait_timeout,
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
        overflow=_overflow(_read_attr(raw, "overflow", "fail_fast")),
        wait_timeout_seconds=int(_read_attr(raw, "wait_timeout_seconds", 0) or 0),
    )


def _policy_without_hard_limit(raw: Any | None) -> RateLimitPolicy:
    if raw is None:
        return RateLimitPolicy(window_seconds=60, max_requests=None)
    return RateLimitPolicy(
        window_seconds=int(_read_attr(raw, "window_seconds", 60) or 60),
        max_requests=None,
    )


def provider_rate_limit_namespace(provider_id: str) -> str:
    return _PROVIDER_TO_SOURCE_TYPE.get(provider_id) or provider_id


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


def _overflow(value: Any) -> str:
    text = str(value or "fail_fast").strip().lower()
    if text == "wait":
        return "wait"
    return "fail_fast"


def policy_to_namespace(policy: RateLimitPolicy) -> Any:
    return SimpleNamespace(
        window_seconds=policy.window_seconds,
        max_requests=policy.max_requests,
        safety_margin=policy.safety_margin,
        overflow=policy.overflow,
        wait_timeout_seconds=policy.wait_timeout_seconds,
        window_anchor=policy.window_anchor,
    )
