from __future__ import annotations

import os
import signal
import sys
import threading
from contextlib import contextmanager, redirect_stdout
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any, Mapping, Sequence

from claw_trade.data_gateway.analysis.crypto_lens import analyze_crypto_lens_data_results
from claw_trade.data_gateway.coordination.scheduler import DataRunScheduleContext, DataRunScheduler
from claw_trade.data_gateway.crypto_symbols import KNOWN_QUOTE_ASSETS as CRYPTO_QUOTE_ASSETS
from claw_trade.data_gateway.crypto_symbols import map_asset_to_symbol as map_crypto_asset_to_symbol
from claw_trade.data_gateway.execution.rate_limit_policy import policy_to_namespace
from claw_trade.data_gateway.granularity import canonical_granularity
from claw_trade.data_gateway.models import DataGap, DataRequest, DataResult, DataResultStatus, FetchResult, GapReason, Market
from claw_trade.data_gateway.needs import AttemptState, DataNeed, DataNeedGap, NeedPriority, ProviderCallSpec, ScheduledCall
from claw_trade.data_gateway.official_catalog import iter_official_catalog_endpoints
from claw_trade.data_gateway.planner import plan_public_data_requests
from claw_trade.data_gateway.public_api import (
    PublicDataRequest,
    PublicRequestPriority,
    forbidden_public_payload_keys,
    public_api_id_for_item,
    public_output_contract_for_api,
)
from claw_trade.data_gateway.providers.plugins.official_api import OFFICIAL_API_DATA_TYPE, OFFICIAL_API_FIELDS
from claw_trade.data_gateway.runtime import build_data_gateway_runtime_from_env
from claw_trade.data_gateway.warehouse.trading_calendar import is_expected_daily_date

_MARKET_DATA_NEED_DEFAULT_LOOKBACK_DAYS = 450
_DATA_NEED_JSON_RETURN_BUFFER_SECONDS = 2.0
_DATA_NEED_TOOL_BUDGET_DEFAULT_SECONDS = 180
_DATA_NEED_PROVIDER_CALL_TIMEOUT_DEFAULT_SECONDS = 12.0
_TUSHARE_PROVIDER_CALL_TIMEOUT_SECONDS = 6.0
_SLOW_PROVIDER_CALL_TIMEOUT_SECONDS_BY_ENDPOINT = {
    "coinglass.bitcoin_ahr999": 55.0,
    "coinglass.futures_liquidation_heatmap": 55.0,
}
_DATA_NEED_LEASE_TTL_SECONDS = 30
_DATA_NEED_REPOSITORY_ROW_LIMIT = 600
_NON_REMOTE_GATE_KINDS = frozenset({"cache_hit", "cached_empty", "rate_limited", "shared_result", "cooldown_skipped"})
_MATERIAL_RESULT_GAP_REASONS = frozenset(
    {
        GapReason.FIELD_MISSING,
        GapReason.DATE_RANGE_MISSING,
        GapReason.DATA_INTEGRITY_FAILED,
        GapReason.GRANULARITY_MISMATCH,
    }
)
_EMPTY_EVENT_RESULT_CAN_SATISFY_SUFFIXES = frozenset({"lockup_event"})
_MARKET_DEFAULTS: dict[Market, dict[str, str | None]] = {
    Market.CN_A: {
        "exchange": "SSE",
        "currency": "CNY",
        "timezone": "Asia/Shanghai",
        "calendar": "CN_A_SSE_SZSE",
    },
    Market.US: {
        "exchange": "NASDAQ",
        "currency": "USD",
        "timezone": "America/New_York",
        "calendar": "US_NYSE_NASDAQ",
    },
    Market.HK: {
        "exchange": "XHKG",
        "currency": "HKD",
        "timezone": "Asia/Hong_Kong",
        "calendar": "HK_XHKG",
    },
    Market.CRYPTO: {
        "exchange": "BINANCE",
        "currency": "USDT",
        "timezone": "UTC",
        "calendar": "CRYPTO_24_7",
    },
}

_ALLOWED_EVIDENCE_COLLECTIONS = {
    "provider_attempts",
    "raw_payloads",
    "normalized_datasets",
    "dataset_manifests",
}



def run_data_need_tool_request(tool_input: Mapping[str, Any], runtime_context: Mapping[str, Any]) -> dict[str, Any]:
    try:
        public_request = _build_public_data_request(tool_input=tool_input, runtime_context=runtime_context)
    except Exception as exc:
        return _error_payload("invalid_public_data_request", str(exc))

    plan = plan_public_data_requests((public_request,))
    need = plan.needs[0] if plan.needs else _internal_need_for_public_request(public_request)
    local_warehouse_requests = _local_warehouse_requests_for_need(need=need, runtime_context=runtime_context)
    runtime = None
    if plan.planned_calls or local_warehouse_requests:
        try:
            with redirect_stdout(sys.stderr):
                runtime = build_data_gateway_runtime_from_env()
        except Exception as exc:
            return _error_payload("data_need_runtime_blocked", str(exc))

    attempts: list[dict[str, Any]] = []
    data_results: list[DataResult] = []
    execution_gaps: list[DataNeedGap] = []
    refs = _NeedResultRefs()
    need_satisfied = False

    if local_warehouse_requests and runtime is not None:
        try:
            with redirect_stdout(sys.stderr):
                local_results = tuple(runtime.data_service.read_warehouse_batch(local_warehouse_requests))
        except Exception:
            local_results = ()
        for data_result in local_results:
            if data_result.rows:
                refs.add_data_result(data_result)
                data_results.append(data_result)
        if any(_local_warehouse_result_usable_for_need(need=need, data_result=item) for item in data_results):
            return _data_need_success_payload(
                need=need,
                public_request=public_request,
                runtime_context=runtime_context,
                planned_count=len(plan.planned_calls),
                scheduled_count=0,
                skipped_gaps=(),
                execution_gaps=execution_gaps,
                refs=refs,
                attempts=attempts,
                data_results=data_results,
                need_satisfied=_any_data_result_satisfies_need(need=need, data_results=data_results),
                merge_evidence=(),
                rate_limit_evidence=(),
            )

    policies: dict[str, Any] = {}
    if plan.planned_calls and runtime is not None:
        policies = {
            call.rate_limit_bucket: runtime.rate_limit_policy_resolver.resolve(
                provider_id=call.provider_id,
                rate_limit_bucket=call.rate_limit_bucket,
                default_policy=None,
            )
            for call in plan.planned_calls
        }
    scheduler = getattr(getattr(runtime, "data_service", None), "scheduler", None) or DataRunScheduler(
        rate_limit_policies=policies,
        rate_limiter=getattr(runtime, "rate_limiter", None),
    )
    policy_updater = getattr(scheduler, "update_rate_limit_policies", None)
    if callable(policy_updater):
        policy_updater(policies)
    schedule_context = DataRunScheduleContext.for_plan(
        run_id=str(runtime_context.get("run_id") or "run"),
        run_started_at=_runtime_now(runtime_context),
    )
    scheduled_plan = scheduler.schedule(plan, schedule_context)
    scheduled_history: list[ScheduledCall] = list(scheduled_plan.scheduled_calls)
    satisfied_composition_sources: dict[str, str] = {}

    if scheduled_plan.scheduled_calls:
        try:
            with redirect_stdout(sys.stderr):
                if runtime is None:
                    runtime = build_data_gateway_runtime_from_env()
                call_by_id = {call.call_id: call for call in plan.planned_calls}
                need_by_id = {item.need_id: item for item in scheduled_plan.needs}
                scheduled_queue: list[ScheduledCall] = list(scheduled_plan.scheduled_calls)
                while scheduled_queue:
                    scheduled = scheduled_queue.pop(0)
                    call = call_by_id.get(scheduled.call_id)
                    if call is None:
                        continue
                    if need_satisfied and not _scheduled_call_should_run_after_need_satisfied(
                        scheduled,
                        call=call,
                        satisfied_composition_sources=satisfied_composition_sources,
                    ):
                        break
                    contract_satisfied = False
                    gap_reason: GapReason | None = None
                    if _deadline_has_expired(call.deadline_at):
                        execution_gaps.extend(_tool_budget_gaps(call, human_readable="data need tool deadline reached before provider call"))
                        break
                    batch = _provider_call_batch(
                        call=call,
                        need=need_by_id.get(call.need_ids[0], need),
                        policy=runtime.rate_limit_policy_resolver.resolve(
                            provider_id=call.provider_id,
                            rate_limit_bucket=call.rate_limit_bucket,
                            default_policy=None,
                        ),
                        runtime=runtime,
                        earliest_start_at=scheduled.earliest_start_at,
                        rate_limit_reserved_at=scheduled.rate_limit_reserved_at,
                    )
                    gate = runtime.data_service.execution_gate.enter(batch)
                    if gate.kind in _NON_REMOTE_GATE_KINDS:
                        ingest = runtime.ingest.record_gate_result(batch, gate)
                        refs.add_ingest(ingest)
                        attempts.append(_provider_attempt_summary_from_gate(call=call, gate=gate, ingest=ingest))
                        data_result = _data_need_result_from_repository(
                            need=need,
                            call=call,
                            batch=batch,
                            ingest=ingest,
                            repository=getattr(runtime, "repository", None),
                            index=len(data_results),
                            as_of=_runtime_now(runtime_context),
                        )
                        if data_result is not None:
                            data_results.append(data_result)
                            if _data_need_result_satisfies_need(need=need, data_result=data_result, batch=batch):
                                need_satisfied = True
                                contract_satisfied = True
                                _record_satisfied_composition_source(call, satisfied_composition_sources)
                            else:
                                gap_reason = _first_material_gap_reason(data_result)
                        elif _gate_result_satisfies_need(gate=gate, ingest=ingest):
                            need_satisfied = True
                            contract_satisfied = True
                            _record_satisfied_composition_source(call, satisfied_composition_sources)
                        if not contract_satisfied and gap_reason is None:
                            gap_reason = _attempt_gap_reason_from_gate(gate)
                        if _deadline_has_expired(call.deadline_at):
                            execution_gaps.extend(_remaining_tool_budget_gaps(scheduled_plan, after_call_id=scheduled.call_id))
                            break
                        if _should_advance_schedule_after_attempt(
                            need_satisfied=need_satisfied,
                            scheduled_queue=scheduled_queue,
                            call_by_id=call_by_id,
                            call=call,
                        ):
                            scheduled_plan, scheduled_queue = _advance_schedule_after_attempt(
                                scheduler=scheduler,
                                scheduled_plan=scheduled_plan,
                                schedule_context=schedule_context,
                                scheduled_history=scheduled_history,
                                scheduled_queue=scheduled_queue,
                                call=call,
                                contract_satisfied=contract_satisfied,
                                gap_reason=gap_reason,
                            )
                        continue
                    try:
                        with _provider_call_timer(
                            call.deadline_at,
                            provider_id=call.provider_id,
                            catalog_endpoint_id=call.catalog_endpoint_id,
                            fallback_available=_has_remaining_execution_group_candidate(scheduled_plan, call),
                        ):
                            fetch_result = runtime.fetch_engine.fetch(batch)
                    except _DataNeedProviderTimeout as exc:
                        fetch_result = FetchResult.from_error(batch, status="error", error=exc)
                    retry_waiter = getattr(runtime.data_service.execution_gate, "wait_after_rate_limited_fetch", None)
                    if callable(retry_waiter) and retry_waiter(batch, fetch_result):
                        try:
                            with _provider_call_timer(
                                call.deadline_at,
                                provider_id=call.provider_id,
                                catalog_endpoint_id=call.catalog_endpoint_id,
                                fallback_available=_has_remaining_execution_group_candidate(scheduled_plan, call),
                            ):
                                fetch_result = runtime.fetch_engine.fetch(batch)
                        except _DataNeedProviderTimeout as exc:
                            fetch_result = FetchResult.from_error(batch, status="error", error=exc)
                    cooldown_marker = getattr(runtime.data_service.execution_gate, "mark_cooldown_after_fetch", None)
                    if callable(cooldown_marker):
                        cooldown_marker(batch, fetch_result)
                    ingest = runtime.ingest.ingest(fetch_result, batch)
                    if gate.kind == "owner" and gate.owner_token:
                        runtime.data_service.execution_gate.publish_shared_result(batch.single_flight_key, gate.owner_token, ingest, batch=batch)
                    refs.add_ingest(ingest)
                    attempts.append(_provider_attempt_summary(call=call, fetch_result=fetch_result, ingest=ingest))
                    data_result = _data_need_result_from_fetch(
                        need=need,
                        call=call,
                        batch=batch,
                        fetch_result=fetch_result,
                        ingest=ingest,
                        index=len(data_results),
                        as_of=_runtime_now(runtime_context),
                    )
                    if data_result is None:
                        data_result = _data_need_result_from_repository(
                            need=need,
                            call=call,
                            batch=batch,
                            ingest=ingest,
                            repository=getattr(runtime, "repository", None),
                            index=len(data_results),
                            as_of=_runtime_now(runtime_context),
                        )
                    if data_result is not None:
                        data_results.append(data_result)
                        if _data_need_result_satisfies_need(need=need, data_result=data_result, batch=batch):
                            need_satisfied = True
                            contract_satisfied = True
                            _record_satisfied_composition_source(call, satisfied_composition_sources)
                        else:
                            gap_reason = _first_material_gap_reason(data_result)
                    if not contract_satisfied and gap_reason is None:
                        gap_reason = _attempt_gap_reason_from_fetch(fetch_result)
                    if _deadline_has_expired(call.deadline_at):
                        execution_gaps.extend(_remaining_tool_budget_gaps(scheduled_plan, after_call_id=scheduled.call_id))
                        break
                    if _should_advance_schedule_after_attempt(
                        need_satisfied=need_satisfied,
                        scheduled_queue=scheduled_queue,
                        call_by_id=call_by_id,
                        call=call,
                    ):
                        scheduled_plan, scheduled_queue = _advance_schedule_after_attempt(
                            scheduler=scheduler,
                            scheduled_plan=scheduled_plan,
                            schedule_context=schedule_context,
                            scheduled_history=scheduled_history,
                            scheduled_queue=scheduled_queue,
                            call=call,
                            contract_satisfied=contract_satisfied,
                            gap_reason=gap_reason,
                        )
        except Exception as exc:
            return _error_payload("data_need_runtime_blocked", str(exc))

    return _data_need_success_payload(
        need=need,
        public_request=public_request,
        runtime_context=runtime_context,
        planned_count=len(scheduled_plan.planned_calls),
        scheduled_count=len(scheduled_history),
        skipped_gaps=scheduled_plan.skipped_needs,
        execution_gaps=execution_gaps,
        refs=refs,
        attempts=attempts,
        data_results=data_results,
        need_satisfied=need_satisfied
        or _empty_event_attempts_satisfy_need(need=need, attempts=attempts, data_results=data_results),
        merge_evidence=scheduled_plan.merge_evidence,
        rate_limit_evidence=scheduled_plan.rate_limit_evidence,
    )


def _data_need_success_payload(
    *,
    need: DataNeed,
    public_request: PublicDataRequest,
    runtime_context: Mapping[str, Any],
    planned_count: int,
    scheduled_count: int,
    skipped_gaps: Sequence[Any],
    execution_gaps: Sequence[DataNeedGap],
    refs: "_NeedResultRefs",
    attempts: Sequence[Mapping[str, Any]],
    data_results: Sequence[DataResult],
    need_satisfied: bool,
    merge_evidence: Sequence[Any],
    rate_limit_evidence: Sequence[Any],
) -> dict[str, Any]:
    if not need_satisfied:
        need_satisfied = _any_data_result_satisfies_need(need=need, data_results=data_results)
    gaps = _final_gaps_for_data_need_payload(
        skipped_gaps=skipped_gaps,
        execution_gaps=execution_gaps,
        refs=refs,
        data_results=data_results,
        need_satisfied=need_satisfied,
    )
    gap_payloads = [item.model_dump(mode="json") for item in gaps]
    status = _data_need_status(
        refs=refs,
        gap_count=len(gap_payloads),
        attempts=attempts,
        need_satisfied=need_satisfied,
        has_result_gaps=any(_data_result_has_material_gap(item) for item in data_results),
    )
    model_visible_text = _basic_data_need_model_visible_text(
        need=need,
        status=status,
        planned_count=planned_count,
        scheduled_count=scheduled_count,
        refs=refs,
        gaps=gaps,
        attempts=attempts,
        data_results=data_results,
        need_satisfied=need_satisfied,
    )
    return {
        "ok": True,
        "schema_version": "data_need_result.v1",
        "tool_name": runtime_context.get("tool_name"),
        "status": status,
        "readiness": {"status": status},
        "model_visible_text": model_visible_text,
        "readable_summary": model_visible_text,
        "need_satisfied": need_satisfied,
        "request": public_request.model_dump(mode="json"),
        "need_domain": _data_need_domain(need),
        "chart_tool_input": _data_need_tool_input_for_chart(need),
        "planned_calls_count": planned_count,
        "scheduled_calls_count": scheduled_count,
        "normalized_refs": refs.dataset_refs,
        "raw_refs": refs.raw_refs,
        "attempt_refs": refs.attempt_refs,
        "refs": {
            "dataset_refs": refs.dataset_refs,
            "raw_refs": refs.raw_refs,
            "attempt_refs": refs.attempt_refs,
            "gaps": [item.model_dump(mode="json") for item in gaps],
        },
        "data_results": [item.model_dump(mode="json") for item in data_results],
        "gaps": gap_payloads,
        "provider_attempts_summary": attempts,
        "merge_evidence": [item.model_dump(mode="json") for item in merge_evidence],
        "rate_limit_evidence": [item.model_dump(mode="json") for item in rate_limit_evidence],
    }


def _final_gaps_for_data_need_payload(
    *,
    skipped_gaps: Sequence[Any],
    execution_gaps: Sequence[DataNeedGap],
    refs: "_NeedResultRefs",
    data_results: Sequence[DataResult],
    need_satisfied: bool,
) -> tuple[Any, ...]:
    if need_satisfied and _has_body_usable_result(data_results):
        material_result_gaps: list[Any] = []
        for result in data_results:
            ignored = _non_material_gap_reasons_for_result(result)
            for gap in tuple(result.gaps or ()):
                reason = _gap_reason(gap)
                if reason in _MATERIAL_RESULT_GAP_REASONS and reason not in ignored:
                    material_result_gaps.append(gap)
        return tuple(material_result_gaps)
    return (*tuple(skipped_gaps), *tuple(execution_gaps), *tuple(refs.gaps))


class _NeedResultRefs:
    def __init__(self) -> None:
        self.dataset_refs: tuple[str, ...] = ()
        self.raw_refs: tuple[str, ...] = ()
        self.attempt_refs: tuple[str, ...] = ()
        self.gaps: tuple[DataGap, ...] = ()

    def add_ingest(self, ingest: Any) -> None:
        self.dataset_refs = tuple(_dedupe((*self.dataset_refs, *tuple(getattr(ingest, "dataset_refs", ()) or ()))))
        self.raw_refs = tuple(_dedupe((*self.raw_refs, *tuple(getattr(ingest, "raw_refs", ()) or ()))))
        self.attempt_refs = tuple(_dedupe((*self.attempt_refs, *tuple(getattr(ingest, "attempt_refs", ()) or ()))))
        self.gaps = (*self.gaps, *tuple(getattr(ingest, "gaps", ()) or ()))

    def add_data_result(self, data_result: DataResult) -> None:
        self.dataset_refs = tuple(_dedupe((*self.dataset_refs, *tuple(data_result.dataset_refs or ()))))
        self.raw_refs = tuple(_dedupe((*self.raw_refs, *tuple(data_result.raw_refs or ()))))
        self.attempt_refs = tuple(_dedupe((*self.attempt_refs, *tuple(data_result.attempt_refs or ()))))
        self.gaps = (*self.gaps, *tuple(data_result.gaps or ()))


def _build_public_data_request(*, tool_input: Mapping[str, Any], runtime_context: Mapping[str, Any]) -> PublicDataRequest:
    forbidden = forbidden_public_payload_keys(tool_input)
    if forbidden:
        raise ValueError(f"公开数据请求不能包含数据层执行细节或旧字段: {', '.join(sorted(forbidden))}")
    market = Market(str(tool_input.get("market") or "").strip().upper())
    item = _required_text(tool_input.get("item"), "item")
    api_id = _public_api_id_for_business_item(item=item, market=market)
    purpose = _required_text(tool_input.get("purpose"), "purpose")
    instrument = _normalize_need_instrument(
        _required_text(tool_input.get("instrument"), "instrument"),
        market=market,
    )
    start, end = _data_need_time_range(tool_input=tool_input, runtime_context=runtime_context, api_id=api_id, purpose=purpose)
    priority = _public_request_priority(tool_input.get("priority"))
    run_id = _safe_identifier(str(runtime_context.get("run_id") or "run"))
    call_id = _safe_identifier(str(runtime_context.get("call_id") or "call"))
    worker_id = _required_text(runtime_context.get("worker_id"), "worker_id")
    return PublicDataRequest(
        request_id=f"{run_id}:{call_id}:data:{_safe_identifier(api_id)}",
        item=item,
        market=market,
        instrument=instrument,
        time_range_start=start,
        time_range_end=end,
        granularity=_optional_text(tool_input.get("granularity")),
        priority=priority,
        requested_by_worker=worker_id,
        purpose=purpose,
        freshness_policy="trading_day",
        deadline_at=_deadline_at(runtime_context),
        consumer="report",
    )


def _public_api_id_for_business_item(*, item: str, market: Market) -> str:
    return public_api_id_for_item(item=item, market=market)


def _need_api_suffix(need: DataNeed) -> str:
    return str(need.api_id or "").strip().lower().rsplit(".", 1)[-1]


def _internal_need_for_public_request(request: PublicDataRequest) -> DataNeed:
    return DataNeed(
        need_id=request.request_id,
        api_id=request.api_id,
        market=request.market,
        instrument=request.instrument,
        time_range_start=request.time_range_start,
        time_range_end=request.time_range_end,
        granularity=request.granularity,
        priority=NeedPriority(request.priority.value),
        requested_by_worker=request.requested_by_worker,
        purpose=request.purpose,
        freshness_policy=request.freshness_policy,
        deadline_at=request.deadline_at,
        consumer=request.consumer,
    )


def _provider_call_batch(
    *,
    call: ProviderCallSpec,
    need: DataNeed,
    policy: Any,
    runtime: Any | None = None,
    earliest_start_at: datetime | None = None,
    rate_limit_reserved_at: datetime | None = None,
) -> Any:
    if not call.provider_id.startswith("official_api_"):
        return _structured_provider_call_batch(
            call=call,
            need=need,
            policy=policy,
            runtime=runtime,
            earliest_start_at=earliest_start_at,
            rate_limit_reserved_at=rate_limit_reserved_at,
        )
    return _official_api_call_batch(
        call=call,
        need=need,
        policy=policy,
        earliest_start_at=earliest_start_at,
        rate_limit_reserved_at=rate_limit_reserved_at,
    )


def _official_api_call_batch(
    *,
    call: ProviderCallSpec,
    need: DataNeed,
    policy: Any,
    earliest_start_at: datetime | None = None,
    rate_limit_reserved_at: datetime | None = None,
) -> Any:
    market_defaults = _MARKET_DEFAULTS[need.market]
    base_asset, quote_asset = _crypto_asset_pair(need.instrument) if need.market == Market.CRYPTO else (None, None)
    data_type, granularity, required_fields, capability_fields = _official_api_result_contract(call, need)
    return SimpleNamespace(
        batch_id=call.call_id,
        request_ids=call.need_ids,
        provider_id=call.provider_id,
        endpoint_id=call.catalog_endpoint_id,
        market=need.market,
        symbol_ids=(need.instrument,),
        data_type=data_type,
        granularity=_batch_granularity(data_type=data_type, contract_granularity=granularity, need=need),
        fields_union=required_fields,
        capability_fields=capability_fields,
        date_range_start=need.time_range_start,
        date_range_end=need.time_range_end,
        exchange=market_defaults["exchange"],
        currency=quote_asset or market_defaults["currency"],
        timezone=market_defaults["timezone"],
        calendar=market_defaults["calendar"],
        base_asset=base_asset,
        quote_asset=quote_asset,
        provider_config_version=None,
        public_api_id=call.public_api_id,
        params={"provider_call_spec": call.model_dump(mode="json")},
        http_visibility=call.http_visibility,
        rate_limit_key=call.rate_limit_bucket,
        cooldown_key=None,
        rate_limit_policy=policy_to_namespace(policy),
        deadline_at=call.deadline_at,
        earliest_start_at=earliest_start_at,
        rate_limit_reserved_at=rate_limit_reserved_at,
        single_flight_key=call.batch_key,
        lease_ttl_seconds=_DATA_NEED_LEASE_TTL_SECONDS,
        cache_key=f"data-need:{call.batch_key}",
        parser_status=call.parser_status,
    )


def _official_api_result_shape(call: ProviderCallSpec, need: DataNeed) -> tuple[str, str, tuple[str, ...]]:
    data_type, granularity, required_fields, _capability_fields = _official_api_result_contract(call, need)
    return data_type, granularity, required_fields


def _official_api_result_contract(call: ProviderCallSpec, need: DataNeed) -> tuple[str, str, tuple[str, ...], tuple[str, ...]]:
    endpoint = _official_catalog_endpoint(call.catalog_endpoint_id)
    output = _select_official_output(endpoint=endpoint, need=need, call=call)
    if output is not None:
        data_type = _optional_text(output.get("data_type"))
        granularity = _official_output_granularity(output=output, need=need, call=call)
        fields = tuple(str(field).strip() for field in tuple(output.get("fields") or ()) if str(field).strip())
        if data_type and granularity and fields:
            return data_type, granularity, _required_output_fields_for_public_api(public_api_id=call.public_api_id, fields=fields), fields
    return OFFICIAL_API_DATA_TYPE, "event", OFFICIAL_API_FIELDS, OFFICIAL_API_FIELDS


def _required_output_fields_for_public_api(*, public_api_id: str | None, fields: Sequence[str]) -> tuple[str, ...]:
    contract = public_output_contract_for_api(str(public_api_id or ""))
    if not contract:
        return tuple(fields)
    requested = tuple(str(field) for field in tuple(contract.get("required_fields") or ()) if str(field).strip())
    exact = tuple(field for field in requested if field in set(fields))
    if not exact:
        return tuple(fields)
    return tuple(_dedupe((*exact, *_identity_output_fields(fields))))


_GENERIC_OUTPUT_TOKENS = frozenset(
    {
        "data",
        "metric",
        "market",
        "need",
        "snapshot",
        "realtime",
        "daily",
        "hourly",
        "intraday",
        "rate",
        "price",
        "open",
        "high",
        "low",
        "close",
    }
)
_IDENTITY_OUTPUT_FIELD_NAMES = frozenset({"symbol_id", "date", "timestamp", "published_at", "event_date"})


def _identity_output_fields(fields: Sequence[str]) -> tuple[str, ...]:
    return tuple(field for field in fields if field in _IDENTITY_OUTPUT_FIELD_NAMES)


def _split_output_tokens(value: str) -> tuple[str, ...]:
    return tuple(token for token in value.replace("-", "_").split("_") if token)


def _normalize_output_token(value: str) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def _official_catalog_endpoint(endpoint_id: str) -> Any | None:
    for endpoint in iter_official_catalog_endpoints():
        if str(getattr(endpoint, "endpoint_id", "")) == endpoint_id:
            return endpoint
    return None


def _select_official_output(*, endpoint: Any | None, need: DataNeed, call: ProviderCallSpec | None = None) -> Mapping[str, Any] | None:
    outputs = _official_outputs(endpoint)
    if not outputs:
        return None
    market_matches = tuple(output for output in outputs if str(output.get("market") or "").strip().upper() == need.market.value)
    candidates = market_matches or outputs
    public_api_ids = tuple(
        dict.fromkeys(
            item
            for item in (
                str(getattr(call, "public_api_id", "") or "").strip().lower(),
                str(getattr(need, "api_id", "") or "").strip().lower(),
            )
            if item
        )
    )
    if public_api_ids:
        capability_matches = tuple(
            output
            for output in candidates
            if {
                str(item).strip().lower()
                for item in tuple(output.get("public_api_ids") or ())
                if str(item).strip()
            }
            & set(public_api_ids)
        )
    else:
        capability_matches = ()
    if not capability_matches:
        capability_matches = tuple(output for output in candidates if _output_matches_api_suffix(output=output, need=need))
    if not capability_matches:
        return None
    candidates = capability_matches
    requested = canonical_granularity(need.granularity)
    if requested is not None:
        for output in candidates:
            granularities = tuple(
                canonical_granularity(str(item))
                for item in tuple(output.get("granularity") or output.get("granularities") or ())
            )
            if requested in granularities:
                return output
    return candidates[0]


def _output_matches_api_suffix(*, output: Mapping[str, Any], need: DataNeed) -> bool:
    api_suffix = _canonical_api_suffix(_need_api_suffix(need))
    declared = {
        _canonical_api_suffix(item)
        for item in tuple(output.get("public_api_ids") or ())
        if _canonical_api_suffix(item)
    }
    return api_suffix in declared or any(item.endswith(f".{api_suffix}") for item in declared)


def _canonical_api_suffix(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_")


def _official_outputs(endpoint: Any | None) -> tuple[Mapping[str, Any], ...]:
    response_shape = getattr(endpoint, "response_shape", None)
    if not isinstance(response_shape, Mapping):
        return ()
    outputs = response_shape.get("outputs")
    if not isinstance(outputs, (list, tuple)):
        return ()
    return tuple(output for output in outputs if isinstance(output, Mapping))


def _official_output_granularity(*, output: Mapping[str, Any], need: DataNeed, call: ProviderCallSpec) -> str | None:
    requested = canonical_granularity(
        _optional_text(call.params.get("interval"))
        or _optional_text(call.params.get("i"))
        or need.granularity
    )
    granularities = tuple(
        item
        for item in (
            canonical_granularity(str(value))
            for value in tuple(output.get("granularity") or output.get("granularities") or ())
        )
        if item
    )
    if requested in granularities:
        return requested
    return granularities[0] if granularities else requested


def _structured_provider_call_batch(
    *,
    call: ProviderCallSpec,
    need: DataNeed,
    policy: Any,
    runtime: Any | None,
    earliest_start_at: datetime | None = None,
    rate_limit_reserved_at: datetime | None = None,
) -> Any:
    market_defaults = _MARKET_DEFAULTS[need.market]
    data_type, granularity, required_fields, capability_fields = _official_api_result_contract(call, need)
    license_policy = _provider_license_policy(runtime=runtime, provider_id=call.provider_id)
    return SimpleNamespace(
        batch_id=call.call_id,
        request_ids=call.need_ids,
        provider_id=call.provider_id,
        endpoint_id=_adapter_endpoint_id(call),
        market=need.market,
        symbol_ids=(need.instrument,),
        data_type=data_type,
        granularity=_batch_granularity(data_type=data_type, contract_granularity=granularity, need=need),
        fields_union=required_fields,
        capability_fields=capability_fields,
        date_range_start=need.time_range_start,
        date_range_end=need.time_range_end,
        exchange=market_defaults["exchange"],
        currency=market_defaults["currency"],
        timezone=market_defaults["timezone"],
        calendar=market_defaults["calendar"],
        base_asset=None,
        quote_asset=None,
        provider_config_version=None,
        public_api_id=call.public_api_id,
        params=dict(call.params),
        http_visibility=call.http_visibility,
        rate_limit_key=call.rate_limit_bucket,
        cooldown_key=None,
        rate_limit_policy=policy_to_namespace(policy),
        deadline_at=call.deadline_at,
        earliest_start_at=earliest_start_at,
        rate_limit_reserved_at=rate_limit_reserved_at,
        single_flight_key=call.batch_key,
        lease_ttl_seconds=_DATA_NEED_LEASE_TTL_SECONDS,
        cache_key=f"data-need:{call.batch_key}",
        license_policy=license_policy,
    )


def _provider_license_policy(*, runtime: Any | None, provider_id: str) -> Any | None:
    registry = getattr(runtime, "registry", None) if runtime is not None else None
    if registry is None:
        return None
    try:
        return registry.get(provider_id).capabilities().license_policy
    except Exception:
        return None


def _batch_granularity(*, data_type: str, contract_granularity: str, need: DataNeed) -> str:
    requested = canonical_granularity(need.granularity)
    if data_type == "daily_bar" and contract_granularity == "daily" and requested in {"weekly", "monthly"}:
        return contract_granularity
    if data_type in {"financial_metric", "financial_statement"} and requested == "annual" and contract_granularity == "quarterly":
        return contract_granularity
    if (
        _event_like_data_type(data_type)
        and contract_granularity in {"event", "realtime"}
        and requested in {"daily", "intraday"}
    ):
        return contract_granularity
    return requested or contract_granularity


def _event_like_data_type(data_type: str) -> bool:
    return str(data_type or "").replace("_", "").lower() in {
        "companynews",
        "cryptoonchainmetric",
        "macronews",
        "officialfiling",
        "eventcalendar",
        "socialsignal",
    }


def _adapter_endpoint_id(call: ProviderCallSpec) -> str:
    endpoint = _official_catalog_endpoint(call.catalog_endpoint_id)
    request_template = getattr(endpoint, "request_template", None)
    if isinstance(request_template, Mapping):
        explicit = _optional_text(request_template.get("adapter_endpoint_id"))
        if explicit:
            return explicit

    candidates = _structured_endpoint_id_candidates(call)
    for candidate in candidates:
        stripped = _strip_market_prefix(candidate)
        if stripped:
            return stripped
    return call.catalog_endpoint_id


def _structured_endpoint_id_candidates(call: ProviderCallSpec) -> tuple[str, ...]:
    raw = call.catalog_endpoint_id
    pieces = []
    if "." in raw:
        pieces.append(raw.rsplit(".", 1)[1])
        pieces.append(raw.split(".", 1)[1])
        pieces.append(raw.split(".", 1)[1].replace(".", "_"))
    pieces.extend((raw.replace(".", "_"), raw, call.official_path_or_api_name))
    return tuple(_dedupe(piece for piece in pieces if piece))


def _strip_market_prefix(value: str) -> str:
    text = str(value or "").strip()
    for prefix in ("cn_a_", "us_", "hk_", "crypto_"):
        if text.startswith(prefix) and len(text) > len(prefix):
            return text[len(prefix) :]
    return text


def _data_need_time_range(
    *,
    tool_input: Mapping[str, Any],
    runtime_context: Mapping[str, Any],
    api_id: str | None = None,
    purpose: str | None = None,
) -> tuple[date | None, date | None]:
    time_range = tool_input.get("time_range")
    explicit_time_range = isinstance(time_range, Mapping)
    if not isinstance(time_range, Mapping):
        time_range = {}
    end = _parse_date(time_range.get("end") or runtime_context.get("end_date") or runtime_context.get("current_date"))
    start = _parse_date(time_range.get("start") or runtime_context.get("start_date"))
    lookback_days = _positive_int(time_range.get("lookback_days"))
    if start is None and lookback_days is not None:
        anchor = end or datetime.now(tz=UTC).date()
        start = anchor - timedelta(days=lookback_days)
    if not explicit_time_range and _is_market_report_api(api_id=api_id, purpose=purpose):
        anchor = end or datetime.now(tz=UTC).date()
        default_start = anchor - timedelta(days=_MARKET_DATA_NEED_DEFAULT_LOOKBACK_DAYS)
        if start is None or start > default_start:
            start = default_start
    return start, end


def _is_market_report_api(*, api_id: str | None, purpose: str | None) -> bool:
    suffix = str(api_id or "").strip().lower().rsplit(".", 1)[-1]
    return suffix in {"daily_bar", "intraday_bar", "realtime_quote"} and str(purpose or "").strip().lower() == "market_report"


def _local_warehouse_requests_for_need(*, need: DataNeed, runtime_context: Mapping[str, Any]) -> tuple[DataRequest, ...]:
    if need.market != Market.CN_A or not _is_market_report_api(api_id=f"cn_a.{_need_api_suffix(need)}", purpose=need.purpose):
        return ()
    defaults = _MARKET_DEFAULTS[need.market]
    return (
        DataRequest(
            request_id=f"data_need:warehouse:{_data_need_domain(need)}:0:daily_bar",
            market=need.market,
            symbol_id=need.instrument,
            exchange=defaults["exchange"],
            currency=defaults["currency"],
            timezone=str(defaults["timezone"] or ""),
            calendar=str(defaults["calendar"] or ""),
            data_type="daily_bar",
            granularity="daily",
            fields=("open", "high", "low", "close", "volume", "amount"),
            date_range_start=need.time_range_start,
            date_range_end=need.time_range_end,
            freshness_policy="warehouse_only",
            consumer="report",
            consumer_id=_safe_identifier(f"{need.requested_by_worker}:{need.purpose}"),
            as_of=_runtime_now(runtime_context),
            deadline_at=need.deadline_at,
        ),
    )


def _deadline_at(runtime_context: Mapping[str, Any]) -> datetime:
    parsed = _parse_iso_datetime(runtime_context.get("deadline_at"))
    if parsed is not None:
        return parsed
    budget_seconds = _positive_int(os.environ.get("CLAW_TRADE_DATA_NEED_TOOL_BUDGET_SECONDS")) or _DATA_NEED_TOOL_BUDGET_DEFAULT_SECONDS
    return _runtime_now(runtime_context) + timedelta(seconds=budget_seconds)


class _DataNeedProviderTimeout(TimeoutError):
    pass


@contextmanager
def _provider_call_timer(
    deadline_at: datetime,
    *,
    provider_id: str | None = None,
    catalog_endpoint_id: str | None = None,
    fallback_available: bool = False,
) -> Any:
    timeout_seconds = _provider_call_timeout_seconds(
        deadline_at,
        provider_id=provider_id,
        catalog_endpoint_id=catalog_endpoint_id,
        fallback_available=fallback_available,
    )
    if timeout_seconds <= 0:
        raise _DataNeedProviderTimeout("data_need_provider_call_deadline_exceeded")
    if not _can_use_signal_timer():
        yield
        return

    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)

    def _handle_timeout(signum: int, frame: Any) -> None:  # noqa: ARG001
        raise _DataNeedProviderTimeout(f"data_need_provider_call_timeout:{timeout_seconds:.3f}s")

    signal.signal(signal.SIGALRM, _handle_timeout)
    signal.setitimer(signal.ITIMER_REAL, timeout_seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)
        if previous_timer[0] > 0:
            signal.setitimer(signal.ITIMER_REAL, previous_timer[0], previous_timer[1])


def _can_use_signal_timer() -> bool:
    return hasattr(signal, "SIGALRM") and threading.current_thread() is threading.main_thread()


def _provider_call_timeout_seconds(
    deadline_at: datetime,
    *,
    provider_id: str | None = None,
    catalog_endpoint_id: str | None = None,
    fallback_available: bool = False,
) -> float:
    remaining_seconds = (deadline_at - datetime.now(tz=UTC)).total_seconds() - _DATA_NEED_JSON_RETURN_BUFFER_SECONDS
    provider_limit_seconds = (
        _positive_float(os.environ.get("CLAW_TRADE_DATA_NEED_PROVIDER_CALL_TIMEOUT_SECONDS"))
        or _DATA_NEED_PROVIDER_CALL_TIMEOUT_DEFAULT_SECONDS
    )
    if provider_id == "official_api_tushare":
        provider_limit_seconds = min(provider_limit_seconds, _TUSHARE_PROVIDER_CALL_TIMEOUT_SECONDS)
    endpoint_limit_seconds = _SLOW_PROVIDER_CALL_TIMEOUT_SECONDS_BY_ENDPOINT.get(str(catalog_endpoint_id or ""))
    if endpoint_limit_seconds is not None:
        provider_limit_seconds = max(provider_limit_seconds, endpoint_limit_seconds)
    return min(remaining_seconds, provider_limit_seconds)


def _deadline_has_expired(deadline_at: datetime) -> bool:
    return (deadline_at - datetime.now(tz=UTC)).total_seconds() <= _DATA_NEED_JSON_RETURN_BUFFER_SECONDS


def _has_remaining_execution_group_candidate(scheduled_plan: Any, call: ProviderCallSpec) -> bool:
    group_id = call.execution_group_id or call.call_id
    if not group_id:
        return False
    planned_calls = tuple(getattr(scheduled_plan, "planned_calls", ()) or ())
    for candidate in planned_calls:
        if candidate.call_id == call.call_id:
            continue
        if (candidate.execution_group_id or candidate.call_id) != group_id:
            continue
        if candidate.fallback_order > call.fallback_order:
            return True
    return False


def _tool_budget_gaps(call: ProviderCallSpec, *, human_readable: str) -> tuple[DataNeedGap, ...]:
    return tuple(
        DataNeedGap(
            need_id=need_id,
            reason=GapReason.RATE_LIMITED_BY_TOOL_BUDGET,
            evidence_refs=(f"tool_budget:{call.call_id}",),
            human_readable=human_readable,
            provider_ids_tried=(call.provider_id,),
        )
        for need_id in call.need_ids
    )


def _remaining_tool_budget_gaps(scheduled_plan: Any, *, after_call_id: str) -> tuple[DataNeedGap, ...]:
    call_by_id = {call.call_id: call for call in scheduled_plan.planned_calls}
    seen_current = False
    gaps: list[DataNeedGap] = []
    for scheduled in scheduled_plan.scheduled_calls:
        if scheduled.call_id == after_call_id:
            seen_current = True
            continue
        if not seen_current:
            continue
        call = call_by_id.get(scheduled.call_id)
        if call is not None:
            gaps.extend(_tool_budget_gaps(call, human_readable="data need tool deadline reached before remaining provider calls"))
    return tuple(gaps)


def _scheduled_call_should_run_after_need_satisfied(
    scheduled: ScheduledCall,
    *,
    call: ProviderCallSpec,
    satisfied_composition_sources: Mapping[str, str],
) -> bool:
    kind = getattr(scheduled, "execution_group_kind", None)
    if str(getattr(kind, "value", kind) or "") != "composition_group":
        return False
    if _composition_call_collects_all_sources(call):
        return True
    group_id = call.execution_group_id or call.call_id
    source = _provider_call_source_group(call)
    return bool(group_id and source and satisfied_composition_sources.get(group_id) == source)


def _composition_call_collects_all_sources(call: ProviderCallSpec) -> bool:
    suffix = str(call.business_api_id or call.public_api_id or "").rsplit(".", 1)[-1].strip().lower()
    return suffix in {"event_calendar"}


def _record_satisfied_composition_source(call: ProviderCallSpec, satisfied_composition_sources: dict[str, str]) -> None:
    kind = getattr(call, "execution_group_kind", None)
    if str(getattr(kind, "value", kind) or "") != "composition_group":
        return
    group_id = call.execution_group_id or call.call_id
    source = _provider_call_source_group(call)
    if group_id and source:
        satisfied_composition_sources.setdefault(group_id, source)


def _should_advance_schedule_after_attempt(
    *,
    need_satisfied: bool,
    scheduled_queue: Sequence[ScheduledCall],
    call_by_id: Mapping[str, ProviderCallSpec],
    call: ProviderCallSpec,
) -> bool:
    if need_satisfied:
        return False
    kind = getattr(call, "execution_group_kind", None)
    if str(getattr(kind, "value", kind) or "") != "composition_group":
        return True
    return not _has_pending_same_composition_source_call(scheduled_queue=scheduled_queue, call_by_id=call_by_id, call=call)


def _has_pending_same_composition_source_call(
    *,
    scheduled_queue: Sequence[ScheduledCall],
    call_by_id: Mapping[str, ProviderCallSpec],
    call: ProviderCallSpec,
) -> bool:
    group_id = call.execution_group_id or call.call_id
    source = _provider_call_source_group(call)
    for scheduled in scheduled_queue:
        pending = call_by_id.get(scheduled.call_id)
        if pending is None:
            continue
        if (pending.execution_group_id or pending.call_id) != group_id:
            continue
        if _provider_call_source_group(pending) == source:
            return True
    return False


def _provider_call_source_group(call: ProviderCallSpec) -> str:
    return str(call.source_group_id or call.provider_id or "").strip()


def _advance_schedule_after_attempt(
    *,
    scheduler: DataRunScheduler,
    scheduled_plan: Any,
    schedule_context: DataRunScheduleContext,
    scheduled_history: list[ScheduledCall],
    scheduled_queue: list[ScheduledCall],
    call: ProviderCallSpec,
    contract_satisfied: bool,
    gap_reason: GapReason | None,
) -> tuple[Any, list[ScheduledCall]]:
    group_id = call.execution_group_id or call.call_id
    if contract_satisfied:
        return scheduled_plan, scheduled_queue
    if not getattr(scheduled_plan, "execution_groups", ()):
        return scheduled_plan, scheduled_queue
    attempt_state = AttemptState(
        plan_id=scheduled_plan.plan_id,
        execution_group_id=group_id,
        call_id=call.call_id,
        attempt_outcome=str(getattr(gap_reason, "value", gap_reason) or "not_satisfied"),
        contract_satisfied=False,
        satisfied_component_ids=(),
        missing_component_ids=(call.component_id,) if call.component_id else (),
        gap_reason=gap_reason,
    )
    next_plan = scheduler.schedule_next(scheduled_plan, schedule_context, attempt_state)
    next_calls = list(next_plan.scheduled_calls)
    if next_calls:
        scheduled_history.extend(next_calls)
        scheduled_queue.extend(next_calls)
    return next_plan, scheduled_queue


def _first_material_gap_reason(data_result: DataResult) -> GapReason | None:
    for gap in tuple(data_result.gaps or ()):
        reason = getattr(gap, "reason", None)
        if isinstance(reason, GapReason):
            return reason
        try:
            return GapReason(str(getattr(reason, "value", reason)))
        except ValueError:
            continue
    if data_result.status == DataResultStatus.MISSING:
        return GapReason.PROVIDER_EMPTY
    if data_result.status == DataResultStatus.ERROR:
        return GapReason.PROVIDER_ERROR
    return None


def _attempt_gap_reason_from_gate(gate: Any) -> GapReason | None:
    kind = str(getattr(gate, "kind", "") or "").strip()
    return {
        "cached_empty": GapReason.CACHED_EMPTY,
        "rate_limited": GapReason.RATE_LIMITED,
        "cooldown_skipped": GapReason.COOLDOWN_SKIPPED,
    }.get(kind)


def _attempt_gap_reason_from_fetch(fetch_result: FetchResult) -> GapReason:
    status = str(getattr(fetch_result, "status", "") or "").strip().lower()
    if status in {"empty", "not_applicable"}:
        return GapReason.PROVIDER_EMPTY
    if status == "credential_missing":
        return GapReason.CREDENTIAL_MISSING
    if status == "permission_denied":
        return GapReason.PERMISSION_DENIED
    if status == "rate_limited":
        return GapReason.RATE_LIMITED
    return GapReason.PROVIDER_ERROR


def _runtime_now(runtime_context: Mapping[str, Any]) -> datetime:
    parsed = _parse_iso_datetime(runtime_context.get("current_time"))
    return parsed or datetime.now(tz=UTC)


def _parse_iso_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _public_request_priority(value: Any) -> PublicRequestPriority:
    text = str(value or PublicRequestPriority.NORMAL.value).strip().lower()
    try:
        return PublicRequestPriority(text)
    except ValueError:
        raise ValueError(f"priority 非法: {value}")


def _data_need_status(
    *,
    refs: _NeedResultRefs,
    gap_count: int,
    attempts: Sequence[Mapping[str, Any]],
    need_satisfied: bool = False,
    has_result_gaps: bool = False,
) -> str:
    has_data_refs = bool(refs.dataset_refs or refs.raw_refs)
    if need_satisfied:
        return "ready"
    if has_data_refs and gap_count:
        return "partial"
    if has_data_refs:
        return "partial"
    if gap_count:
        return "missing"
    return "missing"


def _empty_event_attempts_satisfy_need(
    *,
    need: DataNeed,
    attempts: Sequence[Mapping[str, Any]],
    data_results: Sequence[DataResult],
) -> bool:
    if data_results or _need_api_suffix(need) not in _EMPTY_EVENT_RESULT_CAN_SATISFY_SUFFIXES:
        return False
    remote_attempts = tuple(attempt for attempt in attempts if attempt.get("remote_attempted") is True)
    if not remote_attempts:
        return False
    for attempt in remote_attempts:
        status = str(attempt.get("status") or "").strip().lower()
        error_code = str(attempt.get("error_code") or "").strip().lower()
        if status != "empty" and error_code not in {"provider_empty", "empty_result"}:
            return False
    return True


def _provider_attempt_summary(*, call: ProviderCallSpec, fetch_result: Any, ingest: Any) -> dict[str, Any]:
    attempt_refs = tuple(getattr(ingest, "attempt_refs", ()) or ())
    raw_refs = tuple(getattr(ingest, "raw_refs", ()) or ())
    dataset_refs = tuple(getattr(ingest, "dataset_refs", ()) or ())
    gaps = tuple(getattr(ingest, "gaps", ()) or ())
    http_observations = tuple(getattr(fetch_result, "http_observations", ()) or ())
    fetch_status = _enum_value(getattr(fetch_result, "status", ""))
    return {
        "call_id": call.call_id,
        "provider_id": call.provider_id,
        "catalog_endpoint_id": call.catalog_endpoint_id,
        "parser_status": call.parser_status,
        "http_visibility": call.http_visibility,
        "status": fetch_status,
        "remote_attempted": bool(http_observations)
        or fetch_status in {"success", "empty", "error", "rate_limited", "permission_denied"},
        "remote_success": bool(getattr(ingest, "remote_success", False)),
        "http_audit_status": _attempt_http_audit_status(http_visibility=call.http_visibility, http_observations=http_observations),
        "error_code": getattr(fetch_result, "error_code", None),
        "error_message": getattr(fetch_result, "error_message", None),
        "ingest_status": str(getattr(ingest, "status", "")),
        "attempt_refs": attempt_refs,
        "raw_refs": raw_refs,
        "dataset_refs": dataset_refs,
        "gap_reasons": _attempt_gap_reasons(gaps),
        "raw_refs_count": len(raw_refs),
        "normalized_refs_count": len(dataset_refs),
        "attempt_refs_count": len(attempt_refs),
        "gap_count": len(gaps),
    }


def _provider_attempt_summary_from_gate(*, call: ProviderCallSpec, gate: Any, ingest: Any) -> dict[str, Any]:
    attempt_refs = tuple(getattr(ingest, "attempt_refs", ()) or ())
    raw_refs = tuple(getattr(ingest, "raw_refs", ()) or ())
    dataset_refs = tuple(getattr(ingest, "dataset_refs", ()) or ())
    gaps = tuple(getattr(ingest, "gaps", ()) or ())
    return {
        "call_id": call.call_id,
        "provider_id": call.provider_id,
        "catalog_endpoint_id": call.catalog_endpoint_id,
        "parser_status": call.parser_status,
        "http_visibility": call.http_visibility,
        "status": str(getattr(gate, "kind", "")),
        "gate_kind": str(getattr(gate, "kind", "")),
        "remote_attempted": False,
        "remote_success": False,
        "http_audit_status": "no_remote_attempt",
        "error_code": None,
        "error_message": _gate_error_message(gate),
        "ingest_status": str(getattr(ingest, "status", "")),
        "attempt_refs": attempt_refs,
        "raw_refs": raw_refs,
        "dataset_refs": dataset_refs,
        "gap_reasons": _attempt_gap_reasons(gaps),
        "raw_refs_count": len(raw_refs),
        "normalized_refs_count": len(dataset_refs),
        "attempt_refs_count": len(attempt_refs),
        "gap_count": len(gaps),
    }


def _attempt_http_audit_status(*, http_visibility: str, http_observations: Sequence[Any]) -> str:
    if str(http_visibility or "") == "sdk_internal_unknown":
        return "sdk_http_unknown"
    if http_observations:
        return "managed_http_observed"
    return "managed_http_no_observation" if str(http_visibility or "") == "managed_http" else "no_remote_attempt"


def _attempt_gap_reasons(gaps: Sequence[Any]) -> tuple[str, ...]:
    return tuple(
        str(getattr(getattr(gap, "reason", None), "value", getattr(gap, "reason", "")) or "")
        for gap in gaps
        if str(getattr(getattr(gap, "reason", None), "value", getattr(gap, "reason", "")) or "")
    )


def _gate_error_message(gate: Any) -> str | None:
    message = str(getattr(gate, "reason", "") or "").strip()
    return message or None


def _gate_result_satisfies_need(*, gate: Any, ingest: Any) -> bool:
    if str(getattr(gate, "kind", "")) not in {"cache_hit", "shared_result"}:
        return False
    return False


def _enum_value(value: Any) -> str:
    enum_value = getattr(value, "value", None)
    if isinstance(enum_value, str):
        return enum_value
    return str(value)


def _data_need_result_from_fetch(
    *,
    need: DataNeed,
    call: ProviderCallSpec,
    batch: Any,
    fetch_result: Any,
    ingest: Any,
    index: int,
    as_of: datetime,
) -> DataResult | None:
    if not tuple(getattr(ingest, "dataset_refs", ()) or ()):
        return None
    rows = _fetch_result_rows(fetch_result)
    if not rows:
        return None
    gaps = tuple(getattr(ingest, "gaps", ()) or ())
    status = DataResultStatus.PARTIAL if gaps else DataResultStatus.READY
    data_type = str(getattr(batch, "data_type", "") or _data_need_dataset(call))
    return DataResult(
        request_id=f"data_need:call:{_data_need_domain(need)}:{index}:{data_type}",
        status=status,
        rows=rows,
        dataset_refs=tuple(getattr(ingest, "dataset_refs", ()) or ()),
        raw_refs=tuple(getattr(ingest, "raw_refs", ()) or ()),
        attempt_refs=tuple(getattr(ingest, "attempt_refs", ()) or ()),
        gaps=gaps,
        as_of=as_of,
    )


def _data_need_result_from_repository(
    *,
    need: DataNeed,
    call: ProviderCallSpec,
    batch: Any,
    ingest: Any,
    repository: Any,
    index: int,
    as_of: datetime,
) -> DataResult | None:
    dataset_refs = tuple(getattr(ingest, "dataset_refs", ()) or ())
    if not dataset_refs:
        return None
    query = getattr(repository, "query_normalized", None)
    if not callable(query):
        return None
    data_type = str(getattr(batch, "data_type", "") or _data_need_dataset(call))
    if not data_type:
        return None
    records = ()
    for symbol_id in _repository_symbol_ids_for_need(need):
        try:
            records = query(
                dataset=data_type,
                market=need.market.value,
                symbol_id=symbol_id,
                universe_ref=None,
                date_range_start=need.time_range_start,
                date_range_end=need.time_range_end,
                include_row=True,
                fields=(),
            )
        except Exception:
            return None
        if records:
            break
    wanted_refs = set(dataset_refs)
    rows: list[dict[str, Any]] = []
    row_refs: list[str] = []
    for record in records:
        dataset_ref = str(getattr(record, "dataset_ref", "") or "")
        if wanted_refs and dataset_ref not in wanted_refs:
            continue
        row = getattr(record, "row", None)
        if not isinstance(row, Mapping):
            continue
        row_payload = dict(row)
        _inject_dataset_record_metadata(row_payload, record)
        rows.append(row_payload)
        if dataset_ref:
            row_refs.append(dataset_ref)
        if len(rows) >= _DATA_NEED_REPOSITORY_ROW_LIMIT:
            break
    if not rows:
        return None
    gaps = tuple(getattr(ingest, "gaps", ()) or ())
    status = DataResultStatus.PARTIAL if gaps else DataResultStatus.READY
    return DataResult(
        request_id=f"data_need:call:{_data_need_domain(need)}:{index}:{data_type}",
        status=status,
        rows=tuple(rows),
        dataset_refs=tuple(_dedupe(row_refs)) or dataset_refs,
        raw_refs=tuple(getattr(ingest, "raw_refs", ()) or ()),
        attempt_refs=tuple(getattr(ingest, "attempt_refs", ()) or ()),
        gaps=gaps,
        as_of=as_of,
    )


def _inject_dataset_record_metadata(row: dict[str, Any], record: Any) -> None:
    for field in ("dataset_ref", "symbol_id", "granularity", "period_start", "period_end"):
        value = getattr(record, field, None)
        if value is not None and row.get(field) is None:
            row[field] = value
    source_roles = tuple(getattr(record, "source_roles", ()) or ())
    if source_roles and row.get("source_roles") is None:
        row["source_roles"] = source_roles


def _data_need_result_satisfies_need(*, need: DataNeed, data_result: DataResult, batch: Any) -> bool:
    data_type = str(getattr(batch, "data_type", "") or "")
    public_api_id = str(getattr(batch, "public_api_id", "") or "")
    required_fields = tuple(
        str(field).strip()
        for field in tuple(getattr(batch, "fields_union", ()) or ())
        if str(field).strip()
    )
    return _data_result_satisfies_need(
        need=need,
        data_result=data_result,
        data_type=data_type,
        required_fields=required_fields,
        public_api_id=public_api_id,
    )


def _any_data_result_satisfies_need(*, need: DataNeed, data_results: Sequence[DataResult]) -> bool:
    return any(
        _data_result_satisfies_need(
            need=need,
            data_result=data_result,
            data_type=_data_type_from_data_result(data_result),
            required_fields=(),
            public_api_id=need.api_id,
        )
        for data_result in data_results
    )


def _data_result_satisfies_need(
    *,
    need: DataNeed,
    data_result: DataResult,
    data_type: str,
    required_fields: Sequence[str],
    public_api_id: str | None = None,
) -> bool:
    if _need_api_suffix(need) in {"daily_bar", "intraday_bar"} and need.purpose == "market_report" and data_type == "quote_snapshot":
        return False
    if not (data_type and data_type != OFFICIAL_API_DATA_TYPE and data_result.rows):
        return False
    contract = public_output_contract_for_api(str(public_api_id or need.api_id or ""))
    if not _data_type_can_satisfy_need(need=need, data_type=data_type, public_api_id=public_api_id, output_contract=contract):
        return False
    if not _data_result_satisfies_output_contract(data_result=data_result, output_contract=contract, required_fields=required_fields):
        return False
    if _need_api_suffix(need) in {"daily_bar", "intraday_bar"} and need.purpose == "market_report":
        if not _market_price_result_covers_requested_range(need=need, data_result=data_result):
            return False
    if not _crypto_detail_need_has_required_payload(need=need, data_result=data_result):
        return False
    if _data_result_has_material_gap_for_need(need=need, data_result=data_result):
        return False
    if data_result.status == DataResultStatus.PARTIAL or data_result.gaps:
        if not _data_result_has_material_gap_for_need(need=need, data_result=data_result):
            return True
        return _data_result_rows_cover_requested_range(need=need, data_result=data_result)
    return True


def _data_type_can_satisfy_need(
    *,
    need: DataNeed,
    data_type: str,
    public_api_id: str | None = None,
    output_contract: Mapping[str, Any] | None = None,
) -> bool:
    if str(public_api_id or "").strip().lower() == str(need.api_id or "").strip().lower():
        return True
    expected_dataset = str((output_contract or {}).get("dataset") or "").strip()
    if expected_dataset and data_type == expected_dataset:
        return True
    return False


def _data_result_satisfies_output_contract(
    *,
    data_result: DataResult,
    output_contract: Mapping[str, Any] | None,
    required_fields: Sequence[str],
) -> bool:
    contract = output_contract or {}
    if not contract and required_fields:
        contract = {"all_of": tuple(required_fields), "any_of_groups": ()}
    return any(_row_satisfies_output_contract(row=row, output_contract=contract) for row in data_result.rows if isinstance(row, Mapping))


def _row_satisfies_output_contract(*, row: Mapping[str, Any], output_contract: Mapping[str, Any]) -> bool:
    present = {str(key) for key, value in row.items() if value is not None and str(value).strip() != ""}
    raw_all_of = output_contract.get("all_of") if "all_of" in output_contract else output_contract.get("required_fields")
    all_of = tuple(str(field) for field in tuple(raw_all_of or ()))
    if any(field not in present for field in all_of):
        return False
    for raw_group in tuple(output_contract.get("any_of_groups") or ()):
        group = tuple(str(field) for field in tuple(raw_group or ()))
        if group and not any(field in present for field in group):
            return False
    return bool(present)


def _market_price_result_has_required_payload(data_result: DataResult) -> bool:
    return bool(_market_price_required_payload_rows(data_result))


def _market_price_result_covers_requested_range(*, need: DataNeed, data_result: DataResult) -> bool:
    rows = _market_price_required_payload_rows(data_result)
    if not rows:
        return False
    if need.time_range_start is None or need.time_range_end is None:
        return True
    starts: list[date] = []
    ends: list[date] = []
    for row in rows:
        start = _date_from_row_value(
            row.get("period_start") or row.get("start_date") or row.get("open_time") or row.get("date") or row.get("timestamp")
        )
        end = _date_from_row_value(
            row.get("period_end") or row.get("end_date") or row.get("close_time") or row.get("date") or row.get("timestamp")
        )
        if start is not None:
            starts.append(start)
        if end is not None:
            ends.append(end)
    if not starts or not ends:
        return False
    request_start = need.time_range_start.date() if isinstance(need.time_range_start, datetime) else need.time_range_start
    request_end = need.time_range_end.date() if isinstance(need.time_range_end, datetime) else need.time_range_end
    request_start, request_end = _effective_market_price_request_range(need=need, request_start=request_start, request_end=request_end)
    if request_start is None or request_end is None:
        return True
    calendar = _market_calendar_for_need(need)
    latest_result_end = max(ends)
    if latest_result_end < request_end:
        missing_tail = _expected_market_dates(
            request_start=latest_result_end + timedelta(days=1),
            request_end=request_end,
            calendar=calendar,
        )
        if 0 < len(missing_tail) <= 1:
            request_end = latest_result_end
    if min(starts) > request_start or latest_result_end < request_end:
        return False
    covered_dates = {
        item
        for item in (
            _date_from_row_value(row.get("date") or row.get("period_start") or row.get("open_time") or row.get("timestamp"))
            for row in rows
        )
        if item is not None
    }
    return len(covered_dates) >= _market_price_min_required_rows(
        request_start=request_start,
        request_end=request_end,
        calendar=calendar,
    )


def _market_price_required_payload_rows(data_result: DataResult) -> tuple[Mapping[str, Any], ...]:
    return tuple(row for row in data_result.rows if isinstance(row, Mapping) and _market_price_row_has_required_payload(row))


def _market_price_row_has_required_payload(row: Mapping[str, Any]) -> bool:
    return _row_has_any(row, ("date", "period_start", "open_time", "timestamp")) and all(
        row.get(field) is not None for field in ("open", "high", "low", "close", "volume")
    )


def _effective_market_price_request_range(*, need: DataNeed, request_start: date, request_end: date) -> tuple[date | None, date | None]:
    calendar = _market_calendar_for_need(need)
    if calendar == "CRYPTO_24_7":
        return request_start, request_end
    expected = _expected_market_dates(request_start=request_start, request_end=request_end, calendar=calendar)
    if not expected:
        return None, None
    return expected[0], expected[-1]


def _market_calendar_for_need(need: DataNeed) -> str:
    return str(_MARKET_DEFAULTS.get(need.market, {}).get("calendar") or "")


def _expected_market_dates(*, request_start: date, request_end: date, calendar: str) -> tuple[date, ...]:
    if request_start > request_end:
        return ()
    output: list[date] = []
    cursor = request_start
    while cursor <= request_end:
        if is_expected_daily_date(cursor, calendar):
            output.append(cursor)
        cursor += timedelta(days=1)
    return tuple(output)


def _market_price_min_required_rows(*, request_start: date, request_end: date, calendar: str) -> int:
    trading_days = len(_expected_market_dates(request_start=request_start, request_end=request_end, calendar=calendar))
    if trading_days <= 0:
        return 0
    if trading_days <= 3:
        return 1
    if trading_days <= 35:
        return trading_days
    return min(45, max(35, int(trading_days * 0.4)))


def _local_warehouse_result_usable_for_need(*, need: DataNeed, data_result: DataResult) -> bool:
    if need.market != Market.CN_A or not _is_market_report_api(api_id=f"cn_a.{_need_api_suffix(need)}", purpose=need.purpose):
        return False
    if _data_type_from_data_result(data_result) != "daily_bar":
        return False
    return _data_result_satisfies_need(
        need=need,
        data_result=data_result,
        data_type="daily_bar",
        required_fields=(),
    )


def _data_result_has_material_gap(data_result: DataResult) -> bool:
    ignored = _non_material_gap_reasons_for_result(data_result)
    return any(_gap_reason(gap) in _MATERIAL_RESULT_GAP_REASONS and _gap_reason(gap) not in ignored for gap in data_result.gaps)


def _non_material_gap_reasons_for_result(data_result: DataResult) -> set[GapReason]:
    dataset = _data_result_dataset(data_result)
    if dataset in {"macro_series", "crypto_derivative_metric", "crypto_onchain_metric"}:
        return {GapReason.DATE_RANGE_MISSING}
    return set()


def _data_result_dataset(data_result: DataResult) -> str:
    for row in data_result.rows:
        if isinstance(row, Mapping) and str(row.get("dataset") or "").strip():
            return str(row.get("dataset")).strip()
    return _data_type_from_data_result(data_result)


def _data_result_has_material_gap_for_need(*, need: DataNeed, data_result: DataResult) -> bool:
    ignored = set()
    if _need_api_suffix(need) in {
        "funding_rate",
        "open_interest",
        "long_short_ratio",
        "cvd",
        "liquidation",
        "liquidation_heatmap",
        "exchange_netflow",
        "exchange_balance",
        "ahr999",
        "onchain_metric",
    }:
        ignored.add(GapReason.GRANULARITY_MISMATCH)
        ignored.add(GapReason.DATE_RANGE_MISSING)
    if _need_api_suffix(need) == "macro_series":
        ignored.add(GapReason.DATE_RANGE_MISSING)
    return any(_gap_reason(gap) in _MATERIAL_RESULT_GAP_REASONS and _gap_reason(gap) not in ignored for gap in data_result.gaps)


def _crypto_detail_need_has_required_payload(*, need: DataNeed, data_result: DataResult) -> bool:
    if need.market != Market.CRYPTO:
        return True
    api_suffix = str(_need_api_suffix(need) or "").strip().lower()
    if api_suffix == "liquidation":
        return any(
            _row_has_any(row, ("long_liquidation", "short_liquidation", "liquidation_value"))
            for row in data_result.rows
            if isinstance(row, Mapping)
        )
    if api_suffix in {"liquidation_map", "liquidation_heatmap"}:
        return any(
            _row_has_any(row, ("liquidation_price", "liq_price", "price"))
            and _row_has_any(row, ("liquidation_size", "liq_size", "size", "amount"))
            for row in data_result.rows
            if isinstance(row, Mapping)
        )
    if api_suffix == "cvd":
        return any(_row_has_any(row, ("cvd", "cum_vol_delta", "cumulative_volume_delta")) for row in data_result.rows if isinstance(row, Mapping))
    if api_suffix in {"taker_buy_sell", "active_buy_sell"}:
        return any(
            _row_has_any(row, ("taker_buy_volume", "agg_taker_buy_vol"))
            and _row_has_any(row, ("taker_sell_volume", "agg_taker_sell_vol"))
            for row in data_result.rows
            if isinstance(row, Mapping)
        )
    if api_suffix == "ahr999":
        return any(
            _row_has_any(row, ("ahr999", "ahr999_index", "ahr999_value"))
            or (str(row.get("metric") or "").strip().lower() == "ahr999" and row.get("value") is not None)
            for row in data_result.rows
            if isinstance(row, Mapping)
        )
    if api_suffix == "exchange_netflow":
        return any(_row_has_any(row, ("netflow", "net_inflow", "net_inflow_usd", "value")) for row in data_result.rows if isinstance(row, Mapping))
    if api_suffix in {"macro_news", "event_calendar"}:
        return any(not _row_is_discovery_only(row) for row in data_result.rows if isinstance(row, Mapping))
    return True


def _row_has_any(row: Mapping[str, Any], keys: tuple[str, ...]) -> bool:
    return any(row.get(key) is not None for key in keys)


def _row_is_discovery_only(row: Mapping[str, Any]) -> bool:
    source_roles = _string_values(row.get("source_roles"))
    if "discovery" in source_roles:
        return True
    if row.get("can_be_formal_fact_source") is False:
        return True
    quality_flags = _string_values(row.get("quality_flags"))
    return "discovery_not_formal_fact_source" in quality_flags


def _string_values(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        items = (value,)
    elif isinstance(value, Sequence):
        items = tuple(value)
    else:
        items = (value,)
    return tuple(str(item).strip().lower() for item in items if str(item).strip())


def _data_type_from_data_result(data_result: DataResult) -> str:
    request_id = str(data_result.request_id or "")
    return request_id.rsplit(":", 1)[-1] if ":" in request_id else ""


def _data_result_rows_cover_requested_range(*, need: DataNeed, data_result: DataResult) -> bool:
    if need.time_range_start is None or need.time_range_end is None:
        return True
    starts: list[date] = []
    ends: list[date] = []
    for row in data_result.rows:
        if not isinstance(row, Mapping):
            continue
        start = _date_from_row_value(
            row.get("period_start") or row.get("start_date") or row.get("open_time") or row.get("date") or row.get("timestamp")
        )
        end = _date_from_row_value(
            row.get("period_end") or row.get("end_date") or row.get("close_time") or row.get("date") or row.get("timestamp")
        )
        if start is not None:
            starts.append(start)
        if end is not None:
            ends.append(end)
    if not starts or not ends:
        return False
    request_start = need.time_range_start.date() if isinstance(need.time_range_start, datetime) else need.time_range_start
    request_end = need.time_range_end.date() if isinstance(need.time_range_end, datetime) else need.time_range_end
    return min(starts) <= request_start and max(ends) >= request_end


def _date_from_row_value(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None


def _fetch_result_rows(fetch_result: Any) -> tuple[dict[str, Any], ...]:
    payload = getattr(fetch_result, "payload", None)
    if not isinstance(payload, Mapping):
        return ()
    raw_rows = payload.get("rows")
    if not isinstance(raw_rows, Sequence) or isinstance(raw_rows, (str, bytes, bytearray)):
        return ()
    rows: list[dict[str, Any]] = []
    for item in raw_rows:
        if isinstance(item, Mapping):
            rows.append(dict(item))
    return tuple(rows)


def _data_need_domain(need: DataNeed) -> str:
    text = f"{_need_api_suffix(need)} {need.purpose}".lower()
    if any(token in text for token in ("news", "filing", "announcement")):
        return "news"
    if any(token in text for token in ("social", "sentiment", "interaction")):
        return "social"
    if any(token in text for token in ("fundamental", "financial", "valuation", "statement", "metric")):
        return "fundamental"
    return "market"


def _data_need_dataset(call: ProviderCallSpec) -> str:
    endpoint = str(call.catalog_endpoint_id or call.official_path_or_api_name or "")
    if endpoint.endswith(".daily") or endpoint.endswith("stock_zh_a_hist") or "daily" in endpoint:
        return "daily_bar"
    return endpoint.rsplit(".", 1)[-1] or "data"


def _data_need_tool_input_for_chart(need: DataNeed) -> dict[str, Any]:
    return {
        "ticker": need.instrument,
        "market": need.market.value,
        "start_date": _date_text(need.time_range_start),
        "end_date": _date_text(need.time_range_end),
    }


def _date_text(value: date | datetime | None) -> str | None:
    if value is None:
        return None
    return value.date().isoformat() if isinstance(value, datetime) else value.isoformat()


def _basic_data_need_model_visible_text(
    *,
    need: DataNeed,
    status: str,
    planned_count: int,
    scheduled_count: int,
    refs: _NeedResultRefs,
    gaps: Sequence[Any],
    attempts: Sequence[Mapping[str, Any]],
    data_results: Sequence[DataResult] = (),
    need_satisfied: bool = False,
) -> str:
    result_gaps = _data_result_gaps(data_results)
    has_body_usable_result = _has_body_usable_result(data_results)
    lines = [
        f"数据结果：{need.market.value} {need.instrument} 的{_business_data_label(need)}。",
    ]
    if data_results:
        if need_satisfied and has_body_usable_result:
            lines.append("本次数据结果已经可用于正文；不要重复请求同一数据需求。")
        elif result_gaps:
            lines.append("本次数据需求已经完成；报告只引用已返回的可引用材料，不要重复请求同一数据需求。")
        else:
            lines.append("本次返回了结构化数据；只使用已返回的可引用材料，不要重复请求同一数据需求。")
    elif attempts:
        if need_satisfied:
            lines.append("本次真实查询范围内未发现该类事件记录。")
            lines.append("不要重复请求同一数据需求。")
        else:
            lines.append("本次数据需求已经完成真实数据尝试；不要重复请求同一数据需求。")
            lines.append("不得用模型记忆、公开常识或工具外资料补写营收、利润、ROE、PE/PB、目标价、新闻事实或情绪结论。")
    if attempts and not need_satisfied and not (refs.dataset_refs or refs.raw_refs):
        lines.append("报告只引用已返回的可引用材料，不描述数据请求过程。")
        lines.append("具体价格、技术指标数值、支撑压力、评级、目标价或止损位必须来自可引用材料。")
    if not attempts and planned_count == 0:
        lines.append("只引用已批准上游材料中的已有事实，不描述资料范围或请求过程。")
    elif not data_results and not attempts:
        lines.append("报告只引用已返回的可引用材料，不描述数据请求过程。")
    return "\n".join(lines)


def _has_gap_free_body_result(data_results: Sequence[DataResult]) -> bool:
    return any(result.rows and not _data_result_has_material_gap(result) for result in data_results)


def _has_body_usable_result(data_results: Sequence[DataResult]) -> bool:
    return any(
        result.rows
        and not _data_result_has_material_gap(result)
        and any(isinstance(row, Mapping) and not _row_is_discovery_only(row) for row in result.rows)
        for result in data_results
    )


def _business_data_label(need: DataNeed) -> str:
    suffix = _need_api_suffix(need)
    return {
        "daily_bar": "日线",
        "intraday_bar": "分钟线",
        "realtime_quote": "实时行情",
        "order_book": "盘口",
        "financial_statement": "财报",
        "financial_metric": "财务指标",
        "valuation_metric": "估值",
        "capital_flow": "资金流",
        "northbound_flow": "北向资金",
        "margin_trading": "融资融券",
        "company_news": "公司新闻",
        "macro_news": "宏观新闻",
        "official_filing": "公告",
        "social_signal": "社交情绪",
        "event_calendar": "事件日历",
        "hot_money_event": "游资",
        "lockup_event": "解禁",
        "sector_snapshot": "板块",
        "corporate_action": "公司行动",
        "funding_rate": "资金费率",
        "open_interest": "未平仓量",
        "long_short_ratio": "多空比",
        "liquidation": "清算",
        "liquidation_heatmap": "清算地图",
        "cvd": "主动买卖量差",
        "taker_buy_sell": "主动买卖量",
        "exchange_netflow": "交易所净流量",
        "options_open_interest": "期权持仓",
        "options_volume": "期权成交",
        "etf_flow": "ETF资金流",
        "borrow_interest_rate": "借贷利率",
        "exchange_balance": "交易所余额",
        "whale_transfer": "大额转账",
        "ahr999": "AHR999",
        "onchain_metric": "链上指标",
        "defi_metric": "DeFi指标",
        "company_profile": "项目资料",
        "macro_series": "宏观序列",
    }.get(suffix, "请求数据")


def _visible_gap_labels(gaps: Sequence[Any]) -> list[str]:
    labels: list[str] = []
    seen: set[str] = set()
    for item in gaps:
        raw_reason = getattr(item, "reason", None)
        if raw_reason is None and isinstance(item, Mapping):
            raw_reason = item.get("reason")
        if raw_reason is None:
            label = "未知缺口"
        else:
            reason = str(getattr(raw_reason, "value", raw_reason))
            label = _gap_reason_label(reason)
        if label not in seen:
            seen.add(label)
            labels.append(label)
    return labels


def _data_result_gaps(data_results: Sequence[DataResult]) -> tuple[DataGap, ...]:
    return tuple(gap for result in data_results for gap in tuple(result.gaps or ()))


def _gap_reason(gap: Any) -> GapReason | None:
    raw_reason = getattr(gap, "reason", None)
    if raw_reason is None and isinstance(gap, Mapping):
        raw_reason = gap.get("reason")
    if isinstance(raw_reason, GapReason):
        return raw_reason
    try:
        return GapReason(str(getattr(raw_reason, "value", raw_reason)))
    except ValueError:
        return None


def _repository_symbol_ids_for_need(need: DataNeed) -> tuple[str, ...]:
    symbols = [need.instrument]
    if need.market == Market.CRYPTO:
        base_asset, quote_asset = _crypto_asset_pair(need.instrument)
        if base_asset and quote_asset:
            symbols.append(f"{base_asset}{quote_asset}")
        elif need.instrument:
            mapped = map_crypto_asset_to_symbol(need.instrument)
            symbols.extend((mapped, mapped.replace("/", "")))
    return _dedupe(symbols)


def _normalize_need_instrument(value: str, *, market: Market) -> str:
    text = value.strip().upper()
    if market != Market.CRYPTO:
        return text
    if "/" in text:
        base, quote = (item.strip() for item in text.split("/", 1))
        if not base or not quote:
            raise ValueError(f"CRYPTO 标的非法: {value}")
        if quote not in CRYPTO_QUOTE_ASSETS:
            raise ValueError(f"CRYPTO 报价币不支持: {quote}")
        return f"{base}/{quote}"
    for quote in sorted(CRYPTO_QUOTE_ASSETS, key=len, reverse=True):
        if text.endswith(quote) and len(text) > len(quote):
            return f"{text[:-len(quote)]}/{quote}"
    return f"{text}/USDT"


def _crypto_asset_pair(instrument: str) -> tuple[str | None, str | None]:
    if "/" not in instrument:
        return None, None
    base, quote = instrument.split("/", 1)
    return base.strip().upper() or None, quote.strip().upper() or None


def analyze_crypto_lens_report_data_results(*args: Any, **kwargs: Any) -> Any:
    return analyze_crypto_lens_data_results(*args, **kwargs)


def _required_text(value: Any, field_name: str) -> str:
    text = _optional_text(value)
    if text is None:
        raise ValueError(f"{field_name} 不能为空")
    return text


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _positive_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        parsed = int(str(value))
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def _positive_float(value: Any) -> float | None:
    try:
        parsed = float(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _safe_identifier(value: str) -> str:
    text = "".join(ch if ch.isalnum() or ch in "_.-" else "-" for ch in value.strip())
    return text.strip("-")[:96] or "unknown"


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    return date.fromisoformat(text[:10])


def _dedupe(values: Sequence[str] | Any) -> tuple[str, ...]:
    output: list[str] = []
    for value in values:
        text = str(value)
        if text and text not in output:
            output.append(text)
    return tuple(output)


def _status_zh(value: str) -> str:
    return {
        "ready": "可用",
        "partial": "部分可用",
        "missing": "缺失",
        "error": "错误",
    }.get(value, value)


def _gap_reason_label(value: str) -> str:
    return {
        "credential_missing": "数据源凭证缺失",
        "permission_denied": "数据源权限不足",
        "provider_empty": "数据源返回空结果",
        "provider_error": "数据源调用失败",
        "parser_missing": "解析器缺失",
        "rate_limited": "数据源限流",
        "rate_limited_by_tool_budget": "任务预算内无法等待限流窗口",
        "catalog_match_missing": "项目数据项未绑定可调用接口",
        "granularity_mismatch": "数据粒度不匹配",
        "field_missing": "字段缺失",
        "warehouse_missing": "本地库缺失",
        "warehouse_stale": "本地库过期",
        "sdk_http_unknown": "SDK 内部请求不可审计",
    }.get(value, value)


def _error_payload(code: str, message: str) -> dict[str, Any]:
    safe_message = _safe_tool_error_message(code)
    return {
        "ok": False,
        "error": {"code": code, "message": safe_message, "audit_message": message},
        "model_visible_text": (
            "本次数据需求已经有工具结果，不要再次调用同一数据需求；"
            "报告只引用已返回的可引用材料，不描述数据请求过程。"
        ),
    }


def _safe_tool_error_message(code: str) -> str:
    return {
        "invalid_public_data_request": "数据需求参数不符合公开工具合同",
        "data_need_runtime_blocked": "数据层运行时未能完成本次数据请求",
    }.get(str(code or "").strip(), "数据工具未能完成本次数据请求")


def summarize_data_refs(
    refs: list[str],
    *,
    mongo_uri: str | None = None,
    mongo_database: str | None = None,
    limit: int = 8,
) -> list[str]:
    uri = (mongo_uri or os.environ.get("DATA_GATEWAY_MONGODB_URI") or os.environ.get("CN_A_MONGODB_URI") or "").strip()
    database = (
        mongo_database
        or os.environ.get("DATA_GATEWAY_MONGODB_DATABASE")
        or os.environ.get("CN_A_MONGODB_DATABASE")
        or "claw_trade"
    ).strip()
    if not uri or not refs:
        return []
    try:
        from pymongo import MongoClient
    except Exception:
        return []
    summaries: list[str] = []
    try:
        client: MongoClient[Any] = MongoClient(uri, serverSelectionTimeoutMS=500)
        db = client[database]
        for ref in refs[:limit]:
            collection, document_id = _parse_mongo_ref(ref)
            if collection not in _ALLOWED_EVIDENCE_COLLECTIONS:
                continue
            doc = db[collection].find_one({"_id": document_id})
            if not isinstance(doc, dict):
                summaries.append(ref)
                continue
            summaries.append(_summarize_data_doc(collection, document_id, doc))
    except Exception:
        return []
    return summaries


def _parse_mongo_ref(ref: str) -> tuple[str, str]:
    body = ref.removeprefix("mongo://")
    collection, _, document_id = body.partition("/")
    return collection, document_id


def _summarize_data_doc(collection: str, document_id: str, doc: dict[str, Any]) -> str:
    if collection == "provider_attempts":
        provider = str(doc.get("provider") or "")
        endpoint = str(doc.get("endpoint") or "")
        status = str(doc.get("status") or "")
        return f"{collection}/{document_id}: provider={provider} endpoint={endpoint} status={status}"
    if collection == "normalized_datasets":
        provider = str(doc.get("provider") or "")
        dataset = str(doc.get("dataset") or "")
        schema = str(doc.get("schema_id") or "")
        return f"{collection}/{document_id}: provider={provider} dataset={dataset} schema={schema}"
    return f"{collection}/{document_id}: raw payload stored in data layer"
