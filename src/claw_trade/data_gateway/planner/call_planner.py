from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime
from hashlib import sha256
from typing import Any

from claw_trade.data_gateway.models import GapReason, Market
from claw_trade.data_gateway.needs import DataNeed, DataNeedGap, MergeEvidence, NeedPlan, ProviderCallSpec
from claw_trade.data_gateway.official_catalog import iter_official_catalog_endpoints
from claw_trade.data_gateway.official_catalog.models import OfficialEndpoint

from .need_resolver import resolve_need

_FORBIDDEN_CALLER_KEYS = frozenset({"provider", "path", "api_name", "url", "header", "token"})


def plan_data_needs(
    needs: Iterable[DataNeed],
    *,
    catalog_endpoints: Iterable[OfficialEndpoint] | None = None,
) -> NeedPlan:
    ordered_needs = tuple(needs)
    resolved_catalog = tuple(iter_official_catalog_endpoints() if catalog_endpoints is None else catalog_endpoints)
    planned: "OrderedDict[tuple[str, str], ProviderCallSpec]" = OrderedDict()
    skipped = []
    merge_evidence = []

    for need in ordered_needs:
        resolved = resolve_need(need, catalog_endpoints=resolved_catalog)
        if resolved.gap is not None:
            skipped.append(resolved.gap)
            continue

        semantic_key = _semantic_need_key(need)
        planned_for_need = 0
        param_errors: list[str] = []
        for endpoint in resolved.candidate_endpoints:
            try:
                params = normalize_need_params(need, endpoint)
            except ValueError as exc:
                param_errors.append(f"{endpoint.endpoint_id}: {exc}")
                continue
            batch_key = build_batch_key(
                provider_id=endpoint.provider_id,
                catalog_endpoint_id=endpoint.endpoint_id,
                official_path_or_api_name=endpoint.official_path_or_api_name,
                auth_scope=endpoint.auth,
                params=params,
            )
            group_key = (endpoint.endpoint_id, semantic_key)
            current = planned.get(group_key)
            if current is None:
                planned[group_key] = build_provider_call_spec(
                    need=need,
                    endpoint=endpoint,
                    params=params,
                    batch_key=batch_key,
                    need_ids=(need.need_id,),
                )
                merge_evidence.append(
                    MergeEvidence(batch_key=batch_key, need_ids=(need.need_id,), call_id=planned[group_key].call_id, merged=False)
                )
                planned_for_need += 1
                continue

            need_ids = _append_unique(current.need_ids, need.need_id)
            planned[group_key] = current.model_copy(update={"need_ids": need_ids, "deadline_at": min(current.deadline_at, need.deadline_at)})
            merge_evidence.append(
                MergeEvidence(batch_key=batch_key, need_ids=need_ids, call_id=current.call_id, merged=True, reason="same_semantic_need")
            )
            planned_for_need += 1

        if planned_for_need == 0:
            skipped.append(
                resolved.gap
                or _planner_mapping_gap(
                    need,
                    reason="; ".join(param_errors) if param_errors else "no catalog endpoint could be planned",
                )
            )

    created_at = datetime.now(UTC)
    return NeedPlan(
        plan_id=_stable_id("need-plan", *(need.need_id for need in ordered_needs)),
        needs=ordered_needs,
        planned_calls=tuple(planned.values()),
        skipped_needs=tuple(skipped),
        merge_evidence=tuple(merge_evidence),
        created_at=created_at,
    )


def build_provider_call_spec(
    *,
    need: DataNeed,
    endpoint: OfficialEndpoint,
    params: Mapping[str, Any] | None = None,
    batch_key: str | None = None,
    need_ids: tuple[str, ...] | None = None,
) -> ProviderCallSpec:
    normalized_params = _reject_forbidden_keys(dict(params or normalize_need_params(need, endpoint)))
    missing_required = _missing_required_params(endpoint, normalized_params)
    if missing_required:
        raise ValueError(f"missing required params for {endpoint.endpoint_id}: {', '.join(missing_required)}")
    normalized_need_ids = need_ids or (need.need_id,)
    return ProviderCallSpec(
        call_id=_stable_id("provider-call", endpoint.endpoint_id, _semantic_need_key(need)),
        method=endpoint.method,
        provider_id=endpoint.provider_id,
        catalog_endpoint_id=endpoint.endpoint_id,
        official_path_or_api_name=endpoint.official_path_or_api_name,
        params=normalized_params,
        auth_scope=endpoint.auth,
        rate_limit_bucket=endpoint.rate_limit_bucket,
        http_visibility=endpoint.http_visibility.value,
        parser_status=endpoint.parser_status,
        batch_key=batch_key
        or build_batch_key(
            provider_id=endpoint.provider_id,
            catalog_endpoint_id=endpoint.endpoint_id,
            official_path_or_api_name=endpoint.official_path_or_api_name,
            auth_scope=endpoint.auth,
            params=normalized_params,
        ),
        official_doc_ref=endpoint.official_doc_ref,
        deadline_at=need.deadline_at,
        need_ids=normalized_need_ids,
    )


def build_batch_key(
    *,
    provider_id: str,
    catalog_endpoint_id: str,
    official_path_or_api_name: str,
    auth_scope: str,
    params: Mapping[str, Any],
) -> str:
    shape = ",".join(f"{key}:{_shape_name(value)}" for key, value in sorted(params.items()))
    return "|".join(
        (
            f"provider={provider_id}",
            f"endpoint={catalog_endpoint_id}",
            f"official={official_path_or_api_name}",
            f"auth={auth_scope}",
            f"params={shape}",
        )
    )


def normalize_need_params(need: DataNeed, endpoint: OfficialEndpoint) -> dict[str, Any]:
    params: dict[str, Any] = {}
    symbol = _normalize_symbol(need.instrument, endpoint, need.market)
    base_asset = _base_asset(need.instrument)

    for name in endpoint.required_params:
        value = _param_value(name, need, symbol, base_asset, endpoint)
        if value is not None:
            params[name] = value

    for name in endpoint.optional_params:
        value = _param_value(name, need, symbol, base_asset, endpoint)
        if value is not None:
            params[name] = value

    missing_required = _missing_required_params(endpoint, params)
    if missing_required:
        raise ValueError(f"missing required params for {endpoint.endpoint_id}: {', '.join(missing_required)}")
    return _reject_forbidden_keys(params)


def _param_value(name: str, need: DataNeed, symbol: str, base_asset: str, endpoint: OfficialEndpoint) -> Any:
    if name in {"symbol", "ts_code", "stock", "code", "input", "q"}:
        return symbol
    if name in {"a", "id", "ids"}:
        return base_asset.lower() if name in {"id", "ids"} else base_asset.upper()
    if name in {"vs_currency", "vs_currencies"}:
        return "usd"
    if name == "days":
        return _days_param(need)
    if name == "type" and endpoint.source_type == "yahoo_finance":
        return _yahoo_timeseries_type(need)
    if name == "interval":
        return _interval(need.granularity)
    if name == "resolution":
        return _finnhub_resolution(need.granularity)
    if name in {"from", "to"} and endpoint.endpoint_id == "finnhub.stock_candle":
        value = need.time_range_start if name == "from" else need.time_range_end
        return _timestamp_param(value)
    if name in {"start_date", "observation_start", "from"}:
        return _date_param(need.time_range_start, compact=name == "start_date")
    if name in {"end_date", "observation_end", "to"}:
        return _date_param(need.time_range_end, compact=name == "end_date")
    if name == "start_time":
        return _timestamp_param(need.time_range_start)
    if name == "end_time":
        return _timestamp_param(need.time_range_end)
    if name == "metric":
        return "all"
    if name == "range":
        return _range_param(need)
    if name == "exchange":
        return "Binance"
    if name == "series_id":
        return symbol
    if name == "cik":
        return symbol
    if name == "repo":
        return symbol
    return None


def _reject_forbidden_keys(params: dict[str, Any]) -> dict[str, Any]:
    forbidden = _FORBIDDEN_CALLER_KEYS & set(params)
    if forbidden:
        raise ValueError(f"worker/tool input cannot provide provider call details: {', '.join(sorted(forbidden))}")
    return params


def _missing_required_params(endpoint: OfficialEndpoint, params: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(name for name in endpoint.required_params if name not in params or params[name] is None)


def _planner_mapping_gap(need: DataNeed, *, reason: str) -> DataNeedGap:
    return DataNeedGap(
        need_id=need.need_id,
        reason=GapReason.RESOLVER_MAPPING_MISSING,
        human_readable=f"planner cannot build catalog call params for {need.market.value}:{need.need_kind}: {reason}",
    )


def _semantic_need_key(need: DataNeed) -> str:
    parts = (
        need.market.value,
        _canonical_instrument(need.instrument),
        need.need_kind,
        need.granularity or "",
        _range_value(need.time_range_start),
        _range_value(need.time_range_end),
    )
    return "|".join(parts)


def _stable_id(prefix: str, *parts: str) -> str:
    digest = sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]
    return f"{prefix}:{digest}"


def _append_unique(values: tuple[str, ...], value: str) -> tuple[str, ...]:
    return values if value in values else (*values, value)


def _normalize_symbol(instrument: str, endpoint: OfficialEndpoint, market: Market) -> str:
    if endpoint.source_type == "coinglass":
        return _base_asset(instrument)
    if endpoint.source_type == "coingecko_pro":
        return _base_asset(instrument).lower()
    if endpoint.source_type == "binance":
        return instrument.replace("/", "").upper()
    if market == Market.US:
        return instrument.upper()
    return instrument


def _canonical_instrument(instrument: str) -> str:
    return instrument.strip().upper()


def _base_asset(instrument: str) -> str:
    return instrument.split("/", 1)[0].strip().upper()


def _interval(granularity: str | None) -> str:
    mapping = {"minute": "1m", "hourly": "1h", "daily": "1d", "weekly": "1w"}
    return mapping.get((granularity or "daily").lower(), granularity or "1d")


def _finnhub_resolution(granularity: str | None) -> str:
    mapping = {"minute": "1", "hourly": "60", "daily": "D", "weekly": "W"}
    return mapping.get((granularity or "daily").lower(), "D")


def _range_param(need: DataNeed) -> str:
    if need.time_range_start is None or need.time_range_end is None:
        return "30d"
    start = need.time_range_start.date() if isinstance(need.time_range_start, datetime) else need.time_range_start
    end = need.time_range_end.date() if isinstance(need.time_range_end, datetime) else need.time_range_end
    days = max((end - start).days, 1)
    if days <= 1:
        return "1d"
    if days <= 7:
        return "7d"
    if days <= 30:
        return "30d"
    if days <= 90:
        return "90d"
    return "180d"


def _days_param(need: DataNeed) -> int:
    if need.time_range_start is None or need.time_range_end is None:
        return 30
    start = need.time_range_start.date() if isinstance(need.time_range_start, datetime) else need.time_range_start
    end = need.time_range_end.date() if isinstance(need.time_range_end, datetime) else need.time_range_end
    return max((end - start).days, 1)


def _yahoo_timeseries_type(need: DataNeed) -> str:
    if need.need_kind == "financial_metric":
        return "quarterlyBasicEPS,quarterlyGrossMargin"
    return "trailingPeRatio,trailingMarketCap"


def _date_param(value: date | datetime | None, *, compact: bool) -> str | None:
    if value is None:
        return None
    normalized = value.date() if isinstance(value, datetime) else value
    return normalized.strftime("%Y%m%d" if compact else "%Y-%m-%d")


def _timestamp_param(value: date | datetime | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return int(value.timestamp())
    return int(datetime(value.year, value.month, value.day, tzinfo=UTC).timestamp())


def _range_value(value: date | datetime | None) -> str:
    if value is None:
        return ""
    return value.isoformat()


def _shape_name(value: Any) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int) and not isinstance(value, bool):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, (list, tuple)):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "str"


__all__ = [
    "build_batch_key",
    "build_provider_call_spec",
    "normalize_need_params",
    "plan_data_needs",
]
