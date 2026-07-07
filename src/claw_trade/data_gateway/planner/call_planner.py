from __future__ import annotations

import json
from collections import OrderedDict
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from hashlib import sha256
from typing import Any
from zoneinfo import ZoneInfo

from claw_trade.data_gateway.granularity import canonical_granularity
from claw_trade.data_gateway.models import GapReason, Market
from claw_trade.data_gateway.needs import (
    DataNeed,
    DataNeedGap,
    ExecutionGroup,
    ExecutionGroupKind,
    MergeEvidence,
    NeedPlan,
    ProviderCallSpec,
)
from claw_trade.data_gateway.official_catalog import iter_official_catalog_endpoints
from claw_trade.data_gateway.official_catalog.models import OfficialEndpoint
from claw_trade.data_gateway.public_api import PublicDataRequest, public_api_contracts, public_output_contract_for_api
from claw_trade.data_gateway.warehouse.trading_calendar import is_expected_daily_date
from claw_trade.instruments.resolver import resolve_crypto_provider_symbols

_FORBIDDEN_CALLER_KEYS = frozenset(
    {
        "provider",
        "path",
        "api_name",
        "url",
        "header",
        "headers",
        "header_name",
        "token",
        "api_key",
        "secret",
        "api_id",
        "data_type",
        "fields",
        "worker",
        "worker_id",
        "requested_by_worker",
        "consumer",
        "domain",
        "report_section",
        "allowed_worker",
        "allowed_domain",
        "allowed_report_section",
        "provider_scope",
        "only_for_market",
        "only_for_fundamental",
    }
)
_COMPOSITION_API_SUFFIXES = frozenset(
    {
        "financial_statement",
        "company_news",
        "macro_series",
        "macro_news",
        "official_filing",
        "event_calendar",
        "social_signal",
        "valuation_metric",
        "onchain_metric",
    }
)
_FRED_SERIES_BY_ENDPOINT = {
    "fred.series_fedfunds": "FEDFUNDS",
    "fred.series_dgs10": "DGS10",
    "fred.series_walcl": "WALCL",
    "fred.series_m2sl": "M2SL",
    "fred.series_cpiaucsl": "CPIAUCSL",
}


def plan_public_data_requests(
    requests: Iterable[PublicDataRequest],
    *,
    catalog_endpoints: Iterable[OfficialEndpoint] | None = None,
) -> NeedPlan:
    ordered_requests = tuple(requests)
    resolved_catalog = tuple(iter_official_catalog_endpoints() if catalog_endpoints is None else catalog_endpoints)
    planned: "OrderedDict[tuple[str, str], ProviderCallSpec]" = OrderedDict()
    skipped: list[DataNeedGap] = []
    merge_evidence: list[MergeEvidence] = []
    internal_needs: list[DataNeed] = []

    for request in ordered_requests:
        need = _internal_need_for_public_request(request)
        internal_needs.append(need)
        candidates = _public_candidates_for_request(request, catalog_endpoints=resolved_catalog)
        if not candidates:
            skipped.append(
                DataNeedGap(
                    need_id=request.request_id,
                    reason=GapReason.CATALOG_MATCH_MISSING,
                    human_readable=f"project data item has no callable provider API binding: {request.market.value}:{request.item}",
                )
            )
            continue

        request_key = _public_request_key(request)
        planned_for_request = 0
        param_errors: list[str] = []
        execution_group_kind = _execution_group_kind_for_api(request.api_id)
        execution_group_id = _stable_id("execution-group", request_key, execution_group_kind.value)
        for fallback_order, endpoint in enumerate(candidates):
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
                rate_limit_bucket=endpoint.rate_limit_bucket,
                http_visibility=endpoint.http_visibility.value,
                params=params,
            )
            group_key = (batch_key, request_key)
            current = planned.get(group_key)
            if current is None:
                planned[group_key] = build_provider_call_spec(
                    need=need,
                    endpoint=endpoint,
                    params=params,
                    batch_key=batch_key,
                    need_ids=(request.request_id,),
                    public_api_id=request.api_id,
                    implementation_id=f"{request.api_id}:{endpoint.provider_id}:{endpoint.endpoint_id}",
                    business_api_id=request.api_id,
                    execution_group_id=execution_group_id,
                    execution_group_kind=execution_group_kind,
                    source_group_id=_source_group_id(endpoint),
                    fallback_order=fallback_order,
                    component_id=_component_id(endpoint=endpoint, request=request),
                    **_contract_ids_for_public_api(request.api_id),
                )
                merge_evidence.append(
                    MergeEvidence(batch_key=batch_key, need_ids=(request.request_id,), call_id=planned[group_key].call_id, merged=False)
                )
                planned_for_request += 1
                continue

            request_ids = _append_unique(current.need_ids, request.request_id)
            planned[group_key] = current.model_copy(
                update={
                    "need_ids": request_ids,
                    "deadline_at": min(current.deadline_at, request.deadline_at),
                    "priority": _higher_priority(current.priority, request.priority),
                }
            )
            merge_evidence.append(
                MergeEvidence(batch_key=batch_key, need_ids=request_ids, call_id=current.call_id, merged=True, reason="same_public_request")
            )
            planned_for_request += 1

        if planned_for_request == 0:
            skipped.append(
                DataNeedGap(
                    need_id=request.request_id,
                    reason=GapReason.CATALOG_MATCH_MISSING,
                    human_readable=(
                        "project data item API binding cannot be planned "
                        f"for {request.market.value}:{request.item}: "
                        f"{'; '.join(param_errors) if param_errors else 'no callable provider API binding could be planned'}"
                    ),
                )
            )

    created_at = datetime.now(UTC)
    planned_calls = tuple(planned.values())
    execution_groups = _execution_groups_for_planned_calls(planned_calls)
    initial_call_ids = {call_id for group in execution_groups for call_id in group.initial_call_ids}
    return NeedPlan(
        plan_id=_stable_id("public-data-plan", *(request.request_id for request in ordered_requests)),
        needs=tuple(internal_needs),
        execution_groups=execution_groups,
        planned_calls=planned_calls,
        initial_scheduled_calls=tuple(call for call in planned_calls if call.call_id in initial_call_ids),
        deferred_calls=tuple(call for call in planned_calls if call.call_id not in initial_call_ids),
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
    public_api_id: str | None = None,
    implementation_id: str | None = None,
    business_api_id: str | None = None,
    execution_group_id: str | None = None,
    execution_group_kind: ExecutionGroupKind | str | None = None,
    source_group_id: str | None = None,
    fallback_order: int = 0,
    component_id: str | None = None,
    satisfaction_contract_id: str | None = None,
    standard_output_contract_id: str | None = None,
) -> ProviderCallSpec:
    normalized_params = _reject_forbidden_keys(dict(params or normalize_need_params(need, endpoint)))
    missing_required = _missing_required_params(endpoint, normalized_params)
    if missing_required:
        raise ValueError(f"missing required params for {endpoint.endpoint_id}: {', '.join(missing_required)}")
    normalized_need_ids = need_ids or (need.need_id,)
    return ProviderCallSpec(
        call_id=_stable_id("provider-call", endpoint.endpoint_id, _need_request_key(need)),
        method=endpoint.method,
        public_api_id=public_api_id,
        implementation_id=implementation_id,
        business_api_id=business_api_id,
        execution_group_id=execution_group_id,
        execution_group_kind=execution_group_kind,
        source_group_id=source_group_id,
        fallback_order=fallback_order,
        component_id=component_id,
        satisfaction_contract_id=satisfaction_contract_id,
        standard_output_contract_id=standard_output_contract_id,
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
            rate_limit_bucket=endpoint.rate_limit_bucket,
            http_visibility=endpoint.http_visibility.value,
            params=normalized_params,
        ),
        official_doc_ref=endpoint.official_doc_ref,
        deadline_at=need.deadline_at,
        need_ids=normalized_need_ids,
        priority=need.priority,
    )


def _execution_group_kind_for_api(api_id: str) -> ExecutionGroupKind:
    suffix = api_id.rsplit(".", 1)[-1]
    if suffix in _COMPOSITION_API_SUFFIXES:
        return ExecutionGroupKind.COMPOSITION_GROUP
    return ExecutionGroupKind.FALLBACK_CHAIN


def _source_group_id(endpoint: OfficialEndpoint) -> str:
    return str(endpoint.source_type or endpoint.provider_id).strip() or endpoint.provider_id


def _component_id(*, endpoint: OfficialEndpoint, request: PublicDataRequest) -> str:
    output = _output_for_request(endpoint=endpoint, request=request)
    data_type = str((output or {}).get("data_type") or "").strip()
    if data_type:
        return data_type
    official = str(endpoint.official_path_or_api_name or "").strip()
    return official or endpoint.endpoint_id


def _output_for_request(*, endpoint: OfficialEndpoint, request: PublicDataRequest) -> Mapping[str, Any] | None:
    for output in _endpoint_public_outputs(endpoint):
        if str(output.get("market") or "").strip().upper() != request.market.value:
            continue
        public_api_ids = {str(item).strip().lower() for item in tuple(output.get("public_api_ids") or ()) if str(item).strip()}
        if request.api_id.lower() in public_api_ids:
            return output
    return None


def _contract_ids_for_public_api(api_id: str) -> dict[str, str | None]:
    contract = public_output_contract_for_api(api_id)
    return {
        "satisfaction_contract_id": _optional_contract_text(contract.get("satisfaction_contract_id")),
        "standard_output_contract_id": _optional_contract_text(contract.get("standard_output_contract_id")),
    }


def _optional_contract_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _execution_groups_for_planned_calls(planned_calls: Sequence[ProviderCallSpec]) -> tuple[ExecutionGroup, ...]:
    groups: OrderedDict[str, list[ProviderCallSpec]] = OrderedDict()
    for call in planned_calls:
        group_id = call.execution_group_id or call.call_id
        groups.setdefault(group_id, []).append(call)
    execution_groups: list[ExecutionGroup] = []
    for group_id, calls in groups.items():
        kind = _normalized_group_kind(calls[0].execution_group_kind)
        initial_call_ids = _initial_call_ids_for_group(kind=kind, calls=tuple(calls))
        planned_call_ids = tuple(call.call_id for call in calls)
        execution_groups.append(
            ExecutionGroup(
                group_id=group_id,
                kind=kind,
                business_api_id=str(calls[0].business_api_id or calls[0].public_api_id or ""),
                source_group_id=calls[0].source_group_id if kind == ExecutionGroupKind.COMPOSITION_GROUP else None,
                planned_call_ids=planned_call_ids,
                initial_call_ids=initial_call_ids,
                deferred_call_ids=tuple(call_id for call_id in planned_call_ids if call_id not in set(initial_call_ids)),
            )
        )
    return tuple(execution_groups)


def _initial_call_ids_for_group(*, kind: ExecutionGroupKind, calls: Sequence[ProviderCallSpec]) -> tuple[str, ...]:
    if not calls:
        return ()
    ordered = sorted(calls, key=lambda call: (call.fallback_order, call.provider_id, call.catalog_endpoint_id))
    if kind == ExecutionGroupKind.FALLBACK_CHAIN:
        return (ordered[0].call_id,)
    if _composition_group_collects_all_sources(ordered):
        return tuple(call.call_id for call in ordered)
    first_source = ordered[0].source_group_id
    return tuple(call.call_id for call in ordered if call.source_group_id == first_source)


def _composition_group_collects_all_sources(calls: Sequence[ProviderCallSpec]) -> bool:
    suffixes = {
        str(call.business_api_id or call.public_api_id or "").rsplit(".", 1)[-1].strip().lower()
        for call in calls
    }
    return "event_calendar" in suffixes


def _normalized_group_kind(value: ExecutionGroupKind | str | None) -> ExecutionGroupKind:
    if isinstance(value, ExecutionGroupKind):
        return value
    try:
        return ExecutionGroupKind(str(value))
    except ValueError:
        return ExecutionGroupKind.FALLBACK_CHAIN


def _public_candidates_for_request(
    request: PublicDataRequest,
    *,
    catalog_endpoints: Iterable[OfficialEndpoint],
) -> tuple[OfficialEndpoint, ...]:
    matches: list[tuple[int, int, int, str, str, OfficialEndpoint]] = []
    requested_granularity = _effective_request_granularity(request)
    for index, endpoint in enumerate(catalog_endpoints):
        if _endpoint_excluded_for_public_request(endpoint, request):
            continue
        best_rank: int | None = None
        best_granularity_rank: int | None = None
        for output in _endpoint_public_outputs(endpoint):
            if str(output.get("market") or "").strip().upper() != request.market.value:
                continue
            public_api_ids = {str(item).strip().lower() for item in tuple(output.get("public_api_ids") or ()) if str(item).strip()}
            if request.api_id.lower() not in public_api_ids:
                continue
            granularity_rank = _granularity_match_rank(
                _tuple_text(output.get("granularity") or output.get("granularities")),
                requested_granularity,
                data_type=str(output.get("data_type") or ""),
            )
            if granularity_rank is None:
                continue
            rank = _int_or(output.get("priority_rank"), 90)
            best_rank = rank if best_rank is None else min(best_rank, rank)
            best_granularity_rank = (
                granularity_rank
                if best_granularity_rank is None
                else min(best_granularity_rank, granularity_rank)
            )
        if best_rank is not None:
            matches.append((best_rank, best_granularity_rank or 0, index, endpoint.provider_id, endpoint.endpoint_id, endpoint))
    provider_min_ranks: dict[str, int] = {}
    provider_first_indexes: dict[str, int] = {}
    for rank, _granularity_rank, index, provider_id, _endpoint_id, _endpoint in matches:
        provider_min_ranks[provider_id] = min(rank, provider_min_ranks.get(provider_id, rank))
        provider_first_indexes.setdefault(provider_id, index)
    return tuple(
        row[-1]
        for row in sorted(
            matches,
            key=lambda row: (
                provider_min_ranks[row[3]],
                provider_first_indexes[row[3]],
                row[3],
                row[1],
                row[0],
                row[2],
                row[4],
            ),
        )
    )


def _endpoint_excluded_for_public_request(endpoint: OfficialEndpoint, request: PublicDataRequest) -> bool:
    return (
        request.api_id == "crypto.onchain_metric"
        and endpoint.endpoint_id == "coinglass.onchain_exchange_balance"
    )


def _endpoint_public_outputs(endpoint: OfficialEndpoint) -> tuple[Mapping[str, Any], ...]:
    outputs = endpoint.response_shape.get("outputs")
    if not isinstance(outputs, (list, tuple)):
        return ()
    return tuple(output for output in outputs if isinstance(output, Mapping) and output.get("public_api_ids"))


def _internal_need_for_public_request(request: PublicDataRequest) -> DataNeed:
    return DataNeed(
        need_id=request.request_id,
        api_id=request.api_id,
        market=request.market,
        instrument=request.instrument,
        time_range_start=request.time_range_start,
        time_range_end=request.time_range_end,
        granularity=_effective_request_granularity(request),
        priority=request.priority.value,
        requested_by_worker=request.requested_by_worker,
        purpose=request.purpose,
        freshness_policy=request.freshness_policy,
        deadline_at=request.deadline_at,
        consumer=request.consumer,
    )


def _public_request_key(request: PublicDataRequest) -> str:
    parts = (
        request.market.value,
        _canonical_instrument(request.instrument),
        request.api_id,
        _effective_request_granularity(request) or "",
        _range_value(request.time_range_start),
        _range_value(request.time_range_end),
    )
    return "|".join(parts)


def _effective_request_granularity(request: PublicDataRequest) -> str | None:
    contract = {item.api_id: item for item in public_api_contracts()}.get(request.api_id)
    if request.api_id.endswith(".macro_series"):
        if request.granularity and contract is not None and request.granularity in contract.granularities:
            return request.granularity
        if contract is not None and contract.granularities:
            return contract.granularities[0]
    if request.granularity:
        return request.granularity
    if contract is None or not contract.granularities:
        return None
    return contract.granularities[0]


def build_batch_key(
    *,
    provider_id: str,
    catalog_endpoint_id: str,
    official_path_or_api_name: str,
    auth_scope: str,
    rate_limit_bucket: str = "",
    http_visibility: str = "",
    params: Mapping[str, Any],
) -> str:
    shape = ",".join(f"{key}:{_shape_name(value)}:{_value_fingerprint(value)}" for key, value in sorted(params.items()))
    return "|".join(
        (
            f"provider={provider_id}",
            f"endpoint={catalog_endpoint_id}",
            f"official={official_path_or_api_name}",
            f"auth={auth_scope}",
            f"bucket={rate_limit_bucket}",
            f"http={http_visibility}",
            f"params={shape}",
        )
    )


def normalize_need_params(need: DataNeed, endpoint: OfficialEndpoint) -> dict[str, Any]:
    params: dict[str, Any] = {}
    symbol = _normalize_symbol(need.instrument, endpoint, need.market)
    base_asset = _base_asset(need.instrument, need.market)

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
        if _is_universe_instrument(need.instrument):
            return None
        return symbol
    if name == "secid" and endpoint.source_type == "eastmoney":
        return _eastmoney_secid(symbol)
    if name in {"id", "ids"} and endpoint.source_type == "coingecko_pro":
        return symbol
    if name in {"a", "id", "ids"}:
        return base_asset.lower() if name in {"id", "ids"} else base_asset.upper()
    if name == "slug" and endpoint.source_type == "defillama":
        return _defillama_slug(need.instrument)
    if name in {"vs_currency", "vs_currencies"}:
        return "usd"
    if name == "days":
        return _days_param(need)
    if name == "type" and endpoint.source_type == "yahoo_finance":
        return _yahoo_timeseries_type(need)
    if name == "interval":
        if _daily_bar_source_for_aggregate_period_need(endpoint, need):
            return "1d"
        return _interval(need.granularity)
    if name == "unit" and endpoint.source_type == "coinglass":
        return "USD"
    if name == "i":
        return _glassnode_interval(need.granularity)
    if name == "resolution":
        return _finnhub_resolution(need.granularity)
    if name == "freq" and endpoint.source_type == "tushare":
        return _tushare_frequency(need.granularity)
    if name in {"from", "to"} and endpoint.endpoint_id == "finnhub.stock_candle":
        value = need.time_range_start if name == "from" else need.time_range_end
        return _timestamp_param(value)
    if name in {"start_date", "observation_start", "from"}:
        return _date_param(need.time_range_start, compact=name == "start_date")
    if name in {"end_date", "observation_end", "to"}:
        return _date_param(need.time_range_end, compact=name == "end_date")
    if name == "trade_date":
        return _trade_date_param(need, endpoint)
    if name == "content_type" and endpoint.source_type == "tushare" and endpoint.official_path_or_api_name == "moneyflow_ind_dc":
        return "行业"
    if name == "year":
        return _latest_completed_quarter(need)[0]
    if name == "quarter":
        return _latest_completed_quarter(need)[1]
    if name == "start_time" and endpoint.source_type == "coinglass":
        return _timestamp_millis_param(need.time_range_start, end_of_day=False)
    if name == "end_time" and endpoint.source_type == "coinglass":
        return _timestamp_millis_param(need.time_range_end, end_of_day=True)
    if name == "startTime":
        return _timestamp_millis_param(need.time_range_start, end_of_day=False)
    if name == "endTime":
        return _timestamp_millis_param(need.time_range_end, end_of_day=True)
    if name == "start_time":
        return _timestamp_param(need.time_range_start)
    if name == "end_time":
        return _timestamp_param(need.time_range_end)
    if name == "limit" and endpoint.source_type == "coinglass":
        if endpoint.endpoint_id in {"coinglass.spot_cvd_history", "coinglass.futures_cvd_history"}:
            return "4500"
        return "1000"
    if name == "exchange_list" and endpoint.source_type == "coinglass":
        return "Binance"
    if name == "metric":
        return "all"
    if name == "range":
        if endpoint.source_type == "coinglass" and endpoint.official_path_or_api_name == "/api/option/exchange-oi-history":
            return "all"
        return _range_param(need)
    if name == "exchange":
        return "Binance"
    if name == "series_id":
        if endpoint.source_type == "fred":
            return _FRED_SERIES_BY_ENDPOINT.get(endpoint.endpoint_id, symbol)
        return symbol
    if name == "cik":
        return symbol
    if name == "repo":
        return symbol
    return None


def _reject_forbidden_keys(params: dict[str, Any]) -> dict[str, Any]:
    forbidden = {key for key in params if _is_forbidden_caller_key(key)}
    if forbidden:
        raise ValueError(f"worker/tool input cannot provide provider call details: {', '.join(sorted(forbidden))}")
    return params


def _is_forbidden_caller_key(key: str) -> bool:
    return key in _FORBIDDEN_CALLER_KEYS or key.startswith(("allowed_", "only_for_"))


def _missing_required_params(endpoint: OfficialEndpoint, params: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(name for name in endpoint.required_params if name not in params or params[name] is None)


def _planner_mapping_gap(need: DataNeed, *, reason: str) -> DataNeedGap:
    return DataNeedGap(
        need_id=need.need_id,
        reason=GapReason.CATALOG_MATCH_MISSING,
        human_readable=f"project data item API binding cannot be planned for {need.market.value}:{_need_api_suffix(need)}: {reason}",
    )


def _need_request_key(need: DataNeed) -> str:
    parts = (
        need.market.value,
        _canonical_instrument(need.instrument),
        need.api_id,
        need.granularity or "",
        _range_value(need.time_range_start),
        _range_value(need.time_range_end),
    )
    return "|".join(parts)


def _need_api_suffix(need: DataNeed) -> str:
    return str(need.api_id or "").strip().lower().rsplit(".", 1)[-1]


def _stable_id(prefix: str, *parts: str) -> str:
    digest = sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]
    return f"{prefix}:{digest}"


def _append_unique(values: tuple[str, ...], value: str) -> tuple[str, ...]:
    return values if value in values else (*values, value)


def _higher_priority(first: Any, second: Any) -> Any:
    return first if _priority_rank(first) <= _priority_rank(second) else second


def _priority_rank(value: Any) -> int:
    raw = str(getattr(value, "value", value)).strip().lower()
    return {
        "required": 0,
        "normal": 1,
        "optional": 2,
        "expensive": 3,
    }.get(raw, 1)


def _normalize_symbol(instrument: str, endpoint: OfficialEndpoint, market: Market) -> str:
    if market == Market.CRYPTO:
        symbols = resolve_crypto_provider_symbols(instrument)
        if endpoint.source_type == "coinglass":
            if _coinglass_symbol_param_uses_contract(endpoint):
                return symbols.coinglass_contract_symbol or symbols.crypto_provider_symbol or instrument.replace("/", "").upper()
            return symbols.coinglass_asset_symbol or _base_asset(instrument, market)
        if endpoint.source_type == "coingecko_pro":
            return symbols.coingecko_coin_id or _base_asset(instrument, market).lower()
        if endpoint.source_type == "binance":
            return symbols.crypto_provider_symbol or instrument.replace("/", "").upper()
        if endpoint.source_type == "glassnode":
            return symbols.crypto_base_symbol or _base_asset(instrument, market)
    if market == Market.US:
        return instrument.upper()
    return instrument


def _coinglass_symbol_param_uses_contract(endpoint: OfficialEndpoint) -> bool:
    path = str(endpoint.official_path_or_api_name or "").strip().lower()
    return path in {
        "/api/futures/global-long-short-account-ratio/history",
        "/api/futures/top-long-short-account-ratio/history",
        "/api/futures/top-long-short-position-ratio/history",
        "/api/futures/funding-rate/history",
        "/api/futures/open-interest/history",
        "/api/futures/cvd/history",
        "/api/spot/cvd/history",
        "/api/futures/liquidation/history",
        "/api/futures/liquidation/map",
        "/api/futures/liquidation/heatmap/model1",
        "/api/futures/liquidation/heatmap/model2",
        "/api/futures/liquidation/heatmap/model3",
        "/api/futures/price/history",
        "/api/spot/price/history",
        "/api/futures/orderbook/ask-bids-history",
        "/api/spot/orderbook/ask-bids-history",
    }


def _canonical_instrument(instrument: str) -> str:
    return instrument.strip().upper()


def _is_universe_instrument(instrument: str) -> bool:
    return instrument.strip().lower() in {"all_a_shares", "cn_a_all", "universe:all_a_shares"}


def _base_asset(instrument: str, market: Market | None = None) -> str:
    if market == Market.CRYPTO:
        symbols = resolve_crypto_provider_symbols(instrument)
        if symbols.crypto_base_symbol:
            return symbols.crypto_base_symbol
    return instrument.split("/", 1)[0].strip().upper()


def _defillama_slug(instrument: str) -> str | None:
    symbols = resolve_crypto_provider_symbols(instrument)
    return symbols.defillama_protocol_slug


def _eastmoney_secid(symbol: str) -> str:
    token = symbol.strip().upper()
    code = token.split(".", 1)[0] if "." in token else token
    if code.startswith(("SH", "SZ", "BJ")):
        code = code[2:]
    if code.isdigit() and len(code) < 6:
        code = code.zfill(6)
    market = "1" if token.endswith(".SH") or code.startswith(("6", "9")) else "0"
    return f"{market}.{code}"


def _interval(granularity: str | None) -> str:
    mapping = {
        "realtime": "1h",
        "intraday": "1h",
        "minute": "1m",
        "1m": "1m",
        "hourly": "1h",
        "hour": "1h",
        "1h": "1h",
        "daily": "1d",
        "day": "1d",
        "1d": "1d",
        "weekly": "1w",
        "week": "1w",
        "1w": "1w",
        "1wk": "1w",
        "monthly": "1M",
        "month": "1M",
        "1mo": "1M",
        "1mon": "1M",
    }
    return mapping.get((granularity or "daily").lower(), granularity or "1d")


def _daily_bar_source_for_aggregate_period_need(endpoint: OfficialEndpoint, need: DataNeed) -> bool:
    if canonical_granularity(need.granularity) not in {"weekly", "monthly"}:
        return False
    outputs = endpoint.response_shape.get("outputs")
    if not isinstance(outputs, (list, tuple)):
        return False
    for raw in outputs:
        if not isinstance(raw, dict):
            continue
        if str(raw.get("data_type") or "").strip().lower() != "daily_bar":
            continue
        granularities = raw.get("granularities") or raw.get("granularity")
        if isinstance(granularities, str):
            values = {granularities.lower()}
        elif isinstance(granularities, (list, tuple, set)):
            values = {str(item).strip().lower() for item in granularities}
        else:
            values = set()
        if "daily" in values:
            return True
    return False


def _glassnode_interval(granularity: str | None) -> str:
    mapping = {"minute": "10m", "intraday": "10m", "hourly": "1h", "1h": "1h", "daily": "24h", "1d": "24h"}
    return mapping.get((granularity or "daily").lower(), "24h")


def _finnhub_resolution(granularity: str | None) -> str:
    mapping = {"minute": "1", "hourly": "60", "daily": "D", "weekly": "W"}
    return mapping.get((granularity or "daily").lower(), "D")


def _tushare_frequency(granularity: str | None) -> str:
    mapping = {
        "realtime": "1MIN",
        "intraday": "1MIN",
        "minute": "1MIN",
        "1m": "1MIN",
        "5m": "5MIN",
        "15m": "15MIN",
        "30m": "30MIN",
        "hourly": "60MIN",
        "1h": "60MIN",
    }
    return mapping.get((granularity or "realtime").lower(), "1MIN")


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


def _single_day_param(need: DataNeed) -> str | None:
    if need.time_range_start is None or need.time_range_end is None:
        return None
    start = need.time_range_start.date() if isinstance(need.time_range_start, datetime) else need.time_range_start
    end = need.time_range_end.date() if isinstance(need.time_range_end, datetime) else need.time_range_end
    if start != end:
        return None
    return _date_param(start, compact=True)


def _trade_date_param(need: DataNeed, endpoint: OfficialEndpoint) -> str | None:
    single_day = _single_day_param(need)
    if single_day is not None:
        return single_day
    if endpoint.source_type == "tushare" and _uses_only_trade_date(endpoint):
        if need.market == Market.CN_A and str(need.freshness_policy or "") == "trading_day":
            return _date_param(_latest_completed_cn_a_trade_date(need), compact=True)
        return _date_param(need.time_range_end, compact=True)
    return None


def _uses_only_trade_date(endpoint: OfficialEndpoint) -> bool:
    param_names = {*endpoint.required_params, *endpoint.optional_params}
    return "trade_date" in param_names and "start_date" not in param_names and "end_date" not in param_names


def _latest_completed_cn_a_trade_date(need: DataNeed) -> date | None:
    end = need.time_range_end.date() if isinstance(need.time_range_end, datetime) else need.time_range_end
    if end is None:
        return None
    as_of = need.deadline_at or datetime.now(tz=UTC)
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=UTC)
    local_as_of = as_of.astimezone(ZoneInfo("Asia/Shanghai"))
    local_day = local_as_of.date()
    if end >= local_day:
        if is_expected_daily_date(local_day, "CN_A_SSE_SZSE") and local_as_of.time() >= time(15, 0):
            completed = local_day
        else:
            completed = _previous_cn_a_trade_date(local_day - timedelta(days=1))
        return min(end, completed) if completed is not None else end
    if is_expected_daily_date(end, "CN_A_SSE_SZSE"):
        return end
    return _previous_cn_a_trade_date(end - timedelta(days=1))


def _previous_cn_a_trade_date(start: date) -> date | None:
    cursor = start
    for _ in range(14):
        if is_expected_daily_date(cursor, "CN_A_SSE_SZSE"):
            return cursor
        cursor -= timedelta(days=1)
    return None


def _latest_completed_quarter(need: DataNeed) -> tuple[int, int]:
    value = need.time_range_end
    normalized = value.date() if isinstance(value, datetime) else value
    if normalized is None:
        normalized = datetime.now(tz=UTC).date()
    quarter = (normalized.month - 1) // 3
    if quarter == 0:
        return normalized.year - 1, 4
    return normalized.year, quarter


def _yahoo_timeseries_type(need: DataNeed) -> str:
    if _need_api_suffix(need) == "financial_metric":
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


def _timestamp_millis_param(value: date | datetime | None, *, end_of_day: bool) -> int | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return int(value.timestamp() * 1000)
    boundary = time.max if end_of_day else time.min
    return int(datetime.combine(value, boundary, tzinfo=UTC).timestamp() * 1000)


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


def _tuple_text(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple, set)):
        return tuple(str(item).strip() for item in value if str(item).strip())
    text = str(value).strip()
    return (text,) if text else ()


def _int_or(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _granularity_matches(supported: tuple[str, ...], requested: str | None, *, data_type: str) -> bool:
    return _granularity_match_rank(supported, requested, data_type=data_type) is not None


def _granularity_match_rank(supported: tuple[str, ...], requested: str | None, *, data_type: str) -> int | None:
    if not requested:
        return 0
    requested_value = canonical_granularity(requested) or ""
    supported_values = {canonical_granularity(item) or "" for item in supported}
    if requested_value in supported_values:
        return 0
    if "*" in supported_values:
        return 1
    normalized_data_type = str(data_type or "").strip().lower()
    if normalized_data_type in {"company_news", "macro_news", "official_filing", "event_calendar", "social_signal"}:
        return 2 if supported_values & {"event", "realtime"} and requested_value in {"daily", "intraday"} else None
    if requested_value == "annual" and normalized_data_type in {"financial_metric", "financial_statement"}:
        return 2 if supported_values & {"annual", "quarterly"} else None
    if requested_value in {"weekly", "monthly"} and normalized_data_type == "daily_bar" and "daily" in supported_values:
        return 2
    if requested_value == "daily" and normalized_data_type == "crypto_onchain_metric" and "event" in supported_values:
        return 3
    if requested_value == "hourly" and "intraday" in supported_values:
        return 2
    if requested_value == "intraday" and supported_values & {"hourly", "intraday"}:
        return 2
    if requested_value == "realtime" and normalized_data_type in {"crypto_derivative_metric", "crypto_onchain_metric", "valuation_metric"}:
        return 3 if supported_values & {"hourly", "daily", "intraday", "realtime"} else None
    return None


def _value_fingerprint(value: Any) -> str:
    try:
        payload = json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), default=str)
    except TypeError:
        payload = repr(value)
    return sha256(payload.encode("utf-8")).hexdigest()[:16]


__all__ = [
    "build_batch_key",
    "build_provider_call_spec",
    "normalize_need_params",
    "plan_public_data_requests",
]
