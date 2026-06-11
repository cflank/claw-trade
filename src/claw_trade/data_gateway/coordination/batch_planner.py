from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any, Sequence

from claw_trade.data_gateway.execution.rate_limit_policy import (
    RateLimitPolicyResolver,
    policy_to_namespace,
    provider_rate_limit_namespace,
)
from claw_trade.data_gateway.providers.base import CapabilityError, validate_batch_policy

_DEFAULT_LEASE_TTL_SECONDS = 30
_DEFAULT_WAIT_TIMEOUT_SECONDS = 1


def _read_attr(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _as_tuple(value: Any) -> tuple[Any, ...]:
    if value is None:
        return ()
    if isinstance(value, tuple):
        return value
    if isinstance(value, list):
        return tuple(value)
    return (value,)


def _as_string(value: Any) -> str:
    enum_value = getattr(value, "value", None)
    if isinstance(enum_value, str):
        return enum_value
    return str(value)


def _rate_limit_policy(cap: Any) -> Any:
    raw = _read_attr(cap, "rate_limit_policy", None)
    if raw is None:
        return SimpleNamespace(
            window_seconds=60,
            max_requests=None,
            safety_margin=0,
            overflow="fail_fast",
            wait_timeout_seconds=0,
        )
    return SimpleNamespace(
        window_seconds=int(_read_attr(raw, "window_seconds", 60)),
        max_requests=_read_optional_int(raw, "max_requests", alias_key="max_calls"),
        safety_margin=int(_read_attr(raw, "safety_margin", 0) or 0),
        overflow=str(_read_attr(raw, "overflow", "fail_fast")),
        wait_timeout_seconds=int(_read_attr(raw, "wait_timeout_seconds", 0) or 0),
    )


def _read_optional_int(raw: Any, key: str, *, alias_key: str | None = None) -> int | None:
    value = _read_attr(raw, key, None)
    if value is None and alias_key is not None:
        value = _read_attr(raw, alias_key, None)
    if value is None:
        return None
    return int(value)


def _chunk_by_size(items: Sequence[Any], size: int) -> list[tuple[Any, ...]]:
    if size <= 0:
        raise CapabilityError(f"invalid_batch_chunk_size:{size}")
    return [tuple(items[index : index + size]) for index in range(0, len(items), size)]


def _date_chunks(start: date, end: date, max_days: int) -> list[tuple[date, date]]:
    if max_days <= 0:
        raise CapabilityError(f"invalid_max_days_per_call:{max_days}")
    if start > end:
        raise CapabilityError("date_range_start 不能晚于 date_range_end")
    chunks: list[tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        next_end = min(cursor + timedelta(days=max_days - 1), end)
        chunks.append((cursor, next_end))
        cursor = next_end + timedelta(days=1)
    return chunks


def _to_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


def _build_model(model_name: str, payload: dict[str, Any]) -> Any:
    try:
        from claw_trade.data_gateway import models as data_models  # type: ignore
    except Exception:
        data_models = None
    if data_models is not None and hasattr(data_models, model_name):
        model_cls = getattr(data_models, model_name)
        return model_cls(**payload)
    return SimpleNamespace(**payload)


class ProviderBatchPlanner:
    def __init__(self, *, rate_limit_policy_resolver: RateLimitPolicyResolver | None = None) -> None:
        self._rate_limit_policy_resolver = rate_limit_policy_resolver or RateLimitPolicyResolver()

    def build_batches(self, groups: Sequence[Any], capabilities: Any) -> tuple[Any, ...]:
        batches: list[Any] = []
        sequence = 0
        for group in groups:
            cap = capabilities.get(
                _read_attr(group, "provider_id"),
                _read_attr(group, "endpoint_id"),
                market=_read_attr(group, "market"),
                data_type=_read_attr(group, "data_type"),
            )
            policy = _read_attr(cap, "batch_policy")
            validate_batch_policy(policy)
            supports_batch = bool(_read_attr(policy, "supports_batch", False))
            if not supports_batch:
                for item in _as_tuple(_read_attr(group, "items", ())):
                    sequence += 1
                    batches.append(self._single_item_batch(group, item, cap, sequence))
                continue

            sequence = self._append_batched(group, cap, sequence, batches)
        return tuple(batches)

    def _append_batched(self, group: Any, cap: Any, sequence: int, batches: list[Any]) -> int:
        policy = _read_attr(cap, "batch_policy")
        batch_by = _read_attr(policy, "batch_by", "none")
        symbols = tuple(dict.fromkeys(_as_tuple(_read_attr(group, "symbol_ids", ()))))
        universe_ref = _read_attr(group, "universe_ref", None)
        request_ids = tuple(dict.fromkeys(_as_tuple(_read_attr(group, "request_ids", ()))))
        fields_union = tuple(dict.fromkeys(_as_tuple(_read_attr(group, "fields_union", ()))))
        start = _read_attr(group, "date_range_start")
        end = _read_attr(group, "date_range_end")

        if batch_by == "symbol":
            max_symbols = int(_read_attr(policy, "max_symbols_per_call", 0) or 0)
            if max_symbols <= 0:
                raise CapabilityError("supports_batch=True 但 max_symbols_per_call 不可执行")
            symbol_chunks = _chunk_by_size(symbols, max_symbols)
            for symbol_chunk in symbol_chunks:
                sequence += 1
                batches.append(
                    self._build_batch(
                        group=group,
                        cap=cap,
                        sequence=sequence,
                        request_ids=request_ids,
                        symbol_ids=symbol_chunk,
                        universe_ref=universe_ref,
                        date_range_start=start,
                        date_range_end=end,
                        fields_union=fields_union,
                        required_level=_as_string(_read_attr(group, "required_level", "required")),
                    )
                )
            return sequence

        if batch_by == "date":
            max_days = int(_read_attr(policy, "max_days_per_call", 0) or 0)
            start_date = _to_date(start)
            end_date = _to_date(end)
            if start_date is None or end_date is None:
                raise CapabilityError("supports_batch=True 且 batch_by=date 时必须有可执行日期区间")
            for range_start, range_end in _date_chunks(start_date, end_date, max_days):
                sequence += 1
                batches.append(
                    self._build_batch(
                        group=group,
                        cap=cap,
                        sequence=sequence,
                        request_ids=request_ids,
                        symbol_ids=symbols,
                        universe_ref=universe_ref,
                        date_range_start=range_start,
                        date_range_end=range_end,
                        fields_union=fields_union,
                        required_level=_as_string(_read_attr(group, "required_level", "required")),
                    )
                )
            return sequence

        if batch_by == "symbol_date":
            max_symbols = int(_read_attr(policy, "max_symbols_per_call", 0) or 0)
            max_days = int(_read_attr(policy, "max_days_per_call", 0) or 0)
            if max_symbols <= 0 or max_days <= 0:
                raise CapabilityError("supports_batch=True 且 batch_by=symbol_date 时 max_symbols/max_days 必须可执行")
            start_date = _to_date(start)
            end_date = _to_date(end)
            if start_date is None or end_date is None:
                raise CapabilityError("supports_batch=True 且 batch_by=symbol_date 时必须有可执行日期区间")
            for symbol_chunk in _chunk_by_size(symbols, max_symbols):
                for range_start, range_end in _date_chunks(start_date, end_date, max_days):
                    sequence += 1
                    batches.append(
                        self._build_batch(
                            group=group,
                            cap=cap,
                            sequence=sequence,
                            request_ids=request_ids,
                            symbol_ids=symbol_chunk,
                            universe_ref=universe_ref,
                            date_range_start=range_start,
                            date_range_end=range_end,
                            fields_union=fields_union,
                            required_level=_as_string(_read_attr(group, "required_level", "required")),
                        )
                    )
            return sequence

        if batch_by == "field":
            mergeable = set(_as_tuple(_read_attr(policy, "mergeable_fields", ())))
            if not mergeable:
                raise CapabilityError("supports_batch=True 且 batch_by=field 时 mergeable_fields 不可为空")
            mergeable_fields = tuple(field for field in fields_union if field in mergeable)
            non_mergeable_fields = tuple(field for field in fields_union if field not in mergeable)
            if mergeable_fields:
                sequence += 1
                batches.append(
                    self._build_batch(
                        group=group,
                        cap=cap,
                        sequence=sequence,
                        request_ids=request_ids,
                        symbol_ids=symbols,
                        universe_ref=universe_ref,
                        date_range_start=start,
                        date_range_end=end,
                        fields_union=mergeable_fields,
                        required_level=_as_string(_read_attr(group, "required_level", "required")),
                    )
                )
            for field in non_mergeable_fields:
                sequence += 1
                batches.append(
                    self._build_batch(
                        group=group,
                        cap=cap,
                        sequence=sequence,
                        request_ids=request_ids,
                        symbol_ids=symbols,
                        universe_ref=universe_ref,
                        date_range_start=start,
                        date_range_end=end,
                        fields_union=(field,),
                        required_level=_as_string(_read_attr(group, "required_level", "required")),
                    )
                )
            return sequence

        raise CapabilityError(f"supports_batch=True 但 batch_by 不可执行: {batch_by}")

    def _single_item_batch(self, group: Any, item: Any, cap: Any, sequence: int) -> Any:
        return self._build_batch(
            group=group,
            cap=cap,
            sequence=sequence,
            request_ids=(str(_read_attr(item, "request_id")),),
            symbol_ids=_as_tuple(_read_attr(item, "symbol_ids", ())),
            universe_ref=_read_attr(item, "universe_ref", _read_attr(group, "universe_ref", None)),
            date_range_start=_read_attr(item, "date_range_start", None),
            date_range_end=_read_attr(item, "date_range_end", None),
            fields_union=_as_tuple(_read_attr(item, "fields", ())),
            required_level=_as_string(_read_attr(item, "required_level", "required")),
        )

    def _build_batch(
        self,
        *,
        group: Any,
        cap: Any,
        sequence: int,
        request_ids: tuple[str, ...],
        symbol_ids: tuple[str, ...],
        universe_ref: Any,
        date_range_start: Any,
        date_range_end: Any,
        fields_union: tuple[str, ...],
        required_level: str,
    ) -> Any:
        provider_id = str(_read_attr(group, "provider_id"))
        endpoint_id = str(_read_attr(group, "endpoint_id"))
        market = _as_string(_read_attr(group, "market"))
        data_type = _as_string(_read_attr(group, "data_type"))
        granularity = _as_string(_read_attr(group, "granularity"))
        plan_id = str(_read_attr(group, "plan_id", "plan-unknown"))
        provider_config_version = str(_read_attr(cap, "plugin_version", "unknown"))
        capability_fields = tuple(str(field) for field in _as_tuple(_read_attr(cap, "fields", ())) if str(field).strip())
        as_of = datetime.now(UTC)
        http_visibility = _as_string(_read_attr(cap, "http_visibility", "managed_http"))
        rate_limit_key = f"ratelimit:{provider_rate_limit_namespace(provider_id)}"
        cooldown_key = rate_limit_key if http_visibility == "managed_http" else f"cooldown:{provider_id}:{endpoint_id}"

        key_material = {
            "provider": provider_id,
            "endpoint": endpoint_id,
            "market": market,
            "data_type": data_type,
            "granularity": granularity,
            "request_ids": request_ids,
            "symbol_ids": symbol_ids,
            "universe_ref": str(universe_ref) if universe_ref else None,
            "date_range_start": str(date_range_start) if date_range_start is not None else None,
            "date_range_end": str(date_range_end) if date_range_end is not None else None,
            "fields_union": fields_union,
            "provider_config_version": provider_config_version,
        }
        key_payload = json.dumps(key_material, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        key_hash = hashlib.sha256(key_payload.encode("utf-8")).hexdigest()
        short_hash = key_hash[:24]

        payload = {
            "batch_id": f"batch-{short_hash}-{sequence:03d}",
            "plan_id": plan_id,
            "provider_id": provider_id,
            "endpoint_id": endpoint_id,
            "market": market,
            "data_type": data_type,
            "granularity": granularity,
            "request_ids": tuple(request_ids),
            "symbol_ids": tuple(symbol_ids),
            "universe_ref": str(universe_ref) if universe_ref else None,
            "date_range_start": date_range_start,
            "date_range_end": date_range_end,
            "exchange": _read_attr(group, "exchange", None),
            "currency": _read_attr(group, "currency", None),
            "timezone": _read_attr(group, "timezone", None),
            "calendar": _read_attr(group, "calendar", None),
            "base_asset": _read_attr(group, "base_asset", None),
            "quote_asset": _read_attr(group, "quote_asset", None),
            "fields_union": tuple(fields_union),
            "capability_fields": capability_fields,
            "params_redacted": {"key_material_redacted": key_material},
            "priority_rank": int(_read_attr(group, "priority_rank", _read_attr(cap, "priority_rank", 100))),
            "required_level": required_level,
            "cache_key": f"cache:{short_hash}",
            "rate_limit_key": rate_limit_key,
            "cooldown_key": cooldown_key,
            "rate_limit_policy": policy_to_namespace(
                self._rate_limit_policy_resolver.resolve(
                    provider_id=provider_id,
                    default_policy=_rate_limit_policy(cap),
                )
            ),
            "http_visibility": http_visibility,
            "single_flight_key": f"singleflight:{short_hash}",
            "lease_ttl_seconds": _DEFAULT_LEASE_TTL_SECONDS,
            "wait_timeout_seconds": _DEFAULT_WAIT_TIMEOUT_SECONDS,
            "provider_config_version": provider_config_version,
            "license_policy": _read_attr(cap, "license_policy", None),
            "as_of": as_of,
        }
        return _build_model("ProviderBatchPlan", payload)
