from __future__ import annotations

from collections import OrderedDict
from datetime import date, datetime
from typing import Any, Sequence

from claw_trade.data_gateway.models import MergeGroup, MergeItem, ProviderCandidate, RequiredLevel
from claw_trade.data_gateway.providers.base import CapabilityError


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


def _merge_start(current: date | datetime | None, nxt: Any) -> date | datetime | None:
    if nxt is None:
        return current
    if not isinstance(nxt, (date, datetime)):
        return current
    if current is None:
        return nxt
    return min(current, nxt)


def _merge_end(current: date | datetime | None, nxt: Any) -> date | datetime | None:
    if nxt is None:
        return current
    if not isinstance(nxt, (date, datetime)):
        return current
    if current is None:
        return nxt
    return max(current, nxt)


class RequestCoalescer:
    def coalesce(
        self,
        gaps: Sequence[Any],
        candidates: Sequence[ProviderCandidate],
        capabilities: Any | None = None,
    ) -> tuple[MergeGroup, ...]:
        del gaps

        grouped: dict[tuple[str, str, str, str, str, str], dict[str, Any]] = {}
        for candidate in candidates:
            if capabilities is not None:
                provider_id = str(_read_attr(candidate, "provider_id"))
                endpoint_id = str(_read_attr(candidate, "endpoint_id"))
                market = _as_string(_read_attr(candidate, "market"))
                data_type = _as_string(_read_attr(candidate, "data_type"))
                capabilities.get(provider_id, endpoint_id, market=market, data_type=data_type)
            key = (
                str(_read_attr(candidate, "provider_id")),
                str(_read_attr(candidate, "endpoint_id")),
                _as_string(_read_attr(candidate, "market")),
                _as_string(_read_attr(candidate, "data_type")),
                _as_string(_read_attr(candidate, "granularity")),
                _as_string(_read_attr(candidate, "source_role")),
            )
            bucket = grouped.setdefault(
                key,
                {
                    "priority_rank": int(_read_attr(candidate, "priority_rank", 100)),
                    "required_levels": OrderedDict(),
                    "request_ids": OrderedDict(),
                    "symbol_ids": OrderedDict(),
                    "fields": OrderedDict(),
                    "date_range_start": None,
                    "date_range_end": None,
                    "exchange": None,
                    "currency": None,
                    "timezone": None,
                    "calendar": None,
                    "base_asset": None,
                    "quote_asset": None,
                    "items": OrderedDict(),
                },
            )

            request_id = str(_read_attr(candidate, "request_id"))
            symbol_id = _read_attr(candidate, "symbol_id", None)
            fields = tuple(sorted(set(_as_tuple(_read_attr(candidate, "fields", ())))))
            item = MergeItem(
                request_id=request_id,
                symbol_ids=((str(symbol_id),) if symbol_id else ()),
                date_range_start=_read_attr(candidate, "date_range_start", None),
                date_range_end=_read_attr(candidate, "date_range_end", None),
                fields=fields,
                required_level=_as_string(_read_attr(candidate, "required_level", "required")),
                exchange=_read_attr(candidate, "exchange", None),
                currency=_read_attr(candidate, "currency", None),
                timezone=_read_attr(candidate, "timezone", None),
                calendar=_read_attr(candidate, "calendar", None),
                base_asset=_read_attr(candidate, "base_asset", None),
                quote_asset=_read_attr(candidate, "quote_asset", None),
            )

            if request_id not in bucket["items"]:
                bucket["items"][request_id] = item
            bucket["request_ids"][request_id] = None
            bucket["required_levels"][item.required_level] = None

            if symbol_id:
                bucket["symbol_ids"][str(symbol_id)] = None

            for field in fields:
                bucket["fields"][field] = None

            bucket["date_range_start"] = _merge_start(bucket["date_range_start"], item.date_range_start)
            bucket["date_range_end"] = _merge_end(bucket["date_range_end"], item.date_range_end)
            for dimension in ("exchange", "currency", "timezone", "calendar", "base_asset", "quote_asset"):
                bucket[dimension] = bucket[dimension] or getattr(item, dimension)

        groups: list[MergeGroup] = []
        for key, bucket in grouped.items():
            provider_id, endpoint_id, market, data_type, granularity, source_role = key
            required_level = self._resolve_required_level(tuple(bucket["required_levels"].keys()))
            groups.append(
                MergeGroup(
                    provider_id=provider_id,
                    endpoint_id=endpoint_id,
                    market=market,
                    data_type=data_type,
                    granularity=granularity,
                    source_role=source_role,
                    priority_rank=int(bucket["priority_rank"]),
                    request_ids=tuple(bucket["request_ids"].keys()),
                    symbol_ids=tuple(bucket["symbol_ids"].keys()),
                    date_range_start=bucket["date_range_start"],
                    date_range_end=bucket["date_range_end"],
                    exchange=bucket["exchange"],
                    currency=bucket["currency"],
                    timezone=bucket["timezone"],
                    calendar=bucket["calendar"],
                    base_asset=bucket["base_asset"],
                    quote_asset=bucket["quote_asset"],
                    fields_union=tuple(bucket["fields"].keys()),
                    required_level=required_level,
                    items=tuple(bucket["items"].values()),
                )
            )

        groups.sort(
            key=lambda group: (
                group.priority_rank,
                group.provider_id,
                group.endpoint_id,
                group.market,
                group.data_type,
                group.granularity,
                group.source_role,
            )
        )
        return tuple(groups)

    @staticmethod
    def _resolve_required_level(levels: tuple[str, ...]) -> RequiredLevel:
        if not levels:
            return RequiredLevel.REQUIRED
        normalized = []
        for level in levels:
            try:
                normalized.append(RequiredLevel(level))
            except ValueError as exc:
                raise CapabilityError(f"unsupported_required_level:{level}") from exc
        if RequiredLevel.REQUIRED in normalized:
            return RequiredLevel.REQUIRED
        if RequiredLevel.EXPENSIVE in normalized:
            return RequiredLevel.EXPENSIVE
        if RequiredLevel.OPTIONAL in normalized:
            return RequiredLevel.OPTIONAL
        return RequiredLevel.NOT_APPLICABLE
