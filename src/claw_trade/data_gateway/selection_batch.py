"""Archived CN_A selection provider-batch fetcher.

T13 cutover note: this file remains as an explicit fetcher for approved data
jobs/import checks. It is not a `/select` read-entry path and must not be used
to turn a missing or invalid warehouse run into available selection results.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from claw_trade.artifacts.openviking_backend_http import (
    OpenVikingHttpBackend,
    create_default_backend,
)
from claw_trade.artifacts.openviking_client import OpenVikingAccessError
from claw_trade.data_gateway.models import (
    DeclarativeProviderManifest,
    FreshnessPolicy,
    FreshnessStatus,
    Market,
    PackDomain,
    PackRequest,
    PrioritySource,
    ProviderAttempt,
    ProviderCallResult,
    ProviderCallSpec,
    ProviderCapability,
    ProviderKind,
    ProviderResult,
    ProviderStatus,
    SourceRole,
    utc_now_iso,
)
from claw_trade.data_gateway.providers.defaults import (
    build_default_provider_adapters,
    default_provider_config_version,
    load_default_system_capabilities,
)
from claw_trade.data_gateway.providers.execution import ProviderExecutionEvidenceHelper
from claw_trade.data_gateway.providers.gate import run_provider_call_gate
from claw_trade.data_gateway.providers.market import cn_a_selection_batch_capabilities
from claw_trade.data_gateway.providers.market_adapters import build_cn_a_selection_batch_adapters
from claw_trade.data_gateway.providers.registry import ProviderRegistry
from claw_trade.data_gateway.provider_user_settings import build_runtime_data_source_env
from claw_trade.data_gateway.store import (
    OPENBB_CACHE_ENTRIES,
    OPENBB_NORMALIZED,
    OPENBB_PROVIDER_ATTEMPTS,
    OPENBB_PROVIDER_HTTP_EVIDENCE,
    OPENBB_PROVIDER_MANIFESTS,
    OPENBB_RATE_LIMITS,
    OPENBB_RAW_PAYLOADS,
    OPENBB_SINGLE_FLIGHT_CALLS,
    MongoAttemptStore,
    MongoCacheStore,
    MongoNormalizedStore,
    MongoProviderHttpEvidenceStore,
    MongoProviderManifestStore,
    MongoRateLimitStore,
    MongoRawPayloadStore,
    MongoSingleFlightCoordinator,
    ensure_openbb_store_indexes,
)
from claw_trade.data_gateway.store.mongo import parse_mongo_ref
from claw_trade.selection.data_job import SelectionProviderBatchResult
from claw_trade.selection.engine import ApprovedSelectionStrategy
from claw_trade.selection.models import (
    DataGapRef,
    DataGapSeverity,
    SelectionBatchScope,
    SelectionMarket,
    SelectionProfile,
    SelectionProviderBatchPlan,
    SelectionRunPlan,
    SelectionTriggerSource,
)
from claw_trade.selection.strategy_config import (
    CN_A_SELECTION_STRATEGY_CONFIG_REF as _CN_A_SELECTION_STRATEGY_CONFIG_REF,
)
from claw_trade.selection.strategy_config import (
    load_cn_a_selection_v1_strategy,
)
from claw_trade.selection.strategy_config import (
    load_cn_a_selection_v1_strategy_config_ref as _load_cn_a_selection_v1_strategy_config_ref,
)

_CN_A_SELECTION_BATCH_ENDPOINT = "stock_zh_a_spot_em_batch"
_CN_A_SELECTION_BATCH_DEFAULT_PROVIDER = "selection_batch_provider_unconfigured"
_CN_A_SELECTION_BATCH_DEFAULT_ADAPTER_ID = "project.cn_a.selection_batch"
_CN_A_SELECTION_BATCH_TUSHARE_ADAPTER_ID = "project.cn_a.tushare_selection_batch"
_CN_A_SELECTION_BATCH_DEFAULT_COVERAGE_GROUP = "cn_a_selection_batch"
_CN_A_SELECTION_BATCH_COMPLETE_ENDPOINTS = frozenset(
    {
        "stock_zh_a_spot_em_batch",
    }
)
_CN_A_SELECTION_BATCH_PARTIAL_ENDPOINTS = frozenset(
    {
        "stock_zh_a_spot_mootdx_batch",
        "stock_zh_a_spot_tencent_batch",
        "stock_zh_a_spot_sina_batch",
        "stock_zh_a_daily_baostock_batch",
    }
)
_CN_A_SELECTION_BATCH_UNSUPPORTED_ENDPOINTS = frozenset(
    {
        "stock_quote",
        "quote_tencent",
        "quote_sina",
        "kline_baidu",
        "orderbook",
        "orderbook_tencent",
        "daily",
    }
)
_RECOVERABLE_SELECTION_PROVIDER_GAP_CODES = frozenset(
    {
        "selection_batch_credential_missing",
        "selection_batch_source_not_configured",
        "selection_batch_remote_error",
        "selection_batch_empty",
        "selection_batch_capability_blocked",
    }
)
_SELECTION_USABLE_DATA_STATUSES = frozenset(
    {
        ProviderStatus.REMOTE_SUCCESS,
        ProviderStatus.CACHE_HIT,
        ProviderStatus.SHARED_RESULT,
    }
)
_SELECTION_STRATEGY_DERIVED_FIELD_DEPENDENCIES: Mapping[str, tuple[str, ...]] = {
    "close_open_ratio": ("history",),
    "intraday_return_pct": ("history",),
    "p_change_pct": ("history",),
    "range_pct": ("history",),
    "vol_ratio": ("history",),
    "prev_close": ("history",),
    "prev_volume": ("history",),
    "ma5": ("history",),
    "ma10": ("history",),
    "ma20": ("history",),
    "ma30": ("history",),
    "ma60": ("history",),
    "ma120": ("history",),
    "ma250": ("history",),
    "prev_ma5": ("history",),
    "prev_ma20": ("history",),
    "prev_ma60": ("history",),
    "ma30_slope_5d": ("history",),
    "ma30_slope_10d": ("history",),
    "ma30_growth_30d": ("history",),
    "limit_up_recent": ("history",),
    "post_limit_up_range_pct": ("history",),
    "post_limit_up_return_abs_pct": ("history",),
    "post_limit_up_window_days": ("history",),
    "ma250_backtrace_days": ("history",),
    "ma250_back_ratio": ("history",),
    "platform_deviation_pct": ("history",),
    "return_40d": ("history",),
    "return_60d": ("history",),
    "return_120d": ("history",),
    "single_day_min_return_60d": ("history",),
    "highest_close_60d": ("history",),
    "limit_up_count_20d": ("history",),
    "limit_up_streak_2d": ("history",),
    "limit_down_today": ("history",),
    "atr_14": ("history",),
    "volume_ma20": ("history",),
    "high_20d": ("history",),
    "high_40d": ("history",),
    "low_10d": ("history",),
    "range_10d": ("history",),
    "limit_up_yesterday": ("history",),
    "rps60": ("history",),
    "rps120": ("history",),
    "roll_high_120d": ("history",),
    "score": (),
    "strategy_hit_count": (),
    "strategy_variant_count": (),
    "strategy_missing_field_count": (),
    "strategy_required_field_count": (),
    "strategy_hit_coverage_score": (),
    "strategy_inner_strength_score": (),
    "rps_trend_score": (),
    "liquidity_tradability_score": (),
    "industry_theme_score": (),
    "evidence_completeness_score": (),
    "risk_penalty_score": (),
    "data_gap_penalty_score": (),
}

_ENV_DATA_GATEWAY_MONGODB_URI = "DATA_GATEWAY_MONGODB_URI"
_ENV_DATA_GATEWAY_MONGODB_DATABASE = "DATA_GATEWAY_MONGODB_DATABASE"
_FALLBACK_MONGO_URI_ENV = "CN_A_MONGODB_URI"


@dataclass(frozen=True)
class _SelectionBatchCapabilityCandidate:
    capability: ProviderCapability
    candidate_type: str
    blocked_reason: str | None = None


def _selection_batch_capabilities_catalog(*, registry: ProviderRegistry | None = None) -> tuple[ProviderCapability, ...]:
    if registry is None:
        capabilities = [
            item
            for item in cn_a_selection_batch_capabilities()
            if item.market == Market.CN_A
            and item.domain == PackDomain.MARKET
            and item.endpoint == _CN_A_SELECTION_BATCH_ENDPOINT
        ]
        capabilities.sort(key=lambda item: (item.priority, item.provider, item.adapter_id))
        return tuple(capabilities)
    return tuple(
        item
        for item in registry.capabilities_for(market=Market.CN_A, domain=PackDomain.MARKET)
        if item.endpoint == _CN_A_SELECTION_BATCH_ENDPOINT
    )


def _selection_batch_capability_candidates(
    *,
    registry: ProviderRegistry,
) -> tuple[_SelectionBatchCapabilityCandidate, ...]:
    candidates: list[_SelectionBatchCapabilityCandidate] = []
    for capability in registry.capabilities_for(market=Market.CN_A, domain=PackDomain.MARKET):
        if capability.endpoint in _CN_A_SELECTION_BATCH_COMPLETE_ENDPOINTS:
            candidates.append(
                _SelectionBatchCapabilityCandidate(
                    capability=capability,
                    candidate_type="complete_batch_candidate",
                )
            )
            continue
        if capability.endpoint in _CN_A_SELECTION_BATCH_PARTIAL_ENDPOINTS:
            candidates.append(
                _SelectionBatchCapabilityCandidate(
                    capability=capability,
                    candidate_type="partial_batch_candidate",
                )
            )
            continue
        if capability.endpoint in _CN_A_SELECTION_BATCH_UNSUPPORTED_ENDPOINTS:
            candidates.append(
                _SelectionBatchCapabilityCandidate(
                    capability=capability,
                    candidate_type="unsupported",
                    blocked_reason=(
                        "single-ticker capability is not an approved selection batch endpoint; "
                        "batch mode must use explicit cn_a selection batch adapters"
                    ),
                )
            )
    return tuple(candidates)


def _selection_batch_ordered_candidates(
    candidates: tuple[_SelectionBatchCapabilityCandidate, ...],
) -> tuple[_SelectionBatchCapabilityCandidate, ...]:
    return tuple(
        [item for item in candidates if item.candidate_type == "complete_batch_candidate"]
        + [item for item in candidates if item.candidate_type == "partial_batch_candidate"]
        + [item for item in candidates if item.candidate_type == "unsupported"]
    )


def build_selection_provider_batch_plan(
    *,
    market: SelectionMarket,
    profile: SelectionProfile,
    trade_date: str,
    plan_id: str | None = None,
) -> SelectionProviderBatchPlan:
    if market != SelectionMarket.CN_A or profile != SelectionProfile.CN_A:
        raise ValueError("selection batch v1 仅支持 CN_A")
    registry, _provider_config_version, manifest_load_state = _build_selection_provider_registry_context()
    if manifest_load_state.error:
        raise RuntimeError(f"selection batch manifest load failed: {manifest_load_state.error}")
    return _build_selection_provider_batch_plan_from_registry(
        registry=registry,
        market=market,
        profile=profile,
        trade_date=trade_date,
        plan_id=plan_id,
    )


def _build_selection_provider_batch_plan_from_registry(
    *,
    registry: ProviderRegistry,
    market: SelectionMarket,
    profile: SelectionProfile,
    trade_date: str,
    plan_id: str | None = None,
) -> SelectionProviderBatchPlan:
    ordered_candidates = _selection_batch_ordered_candidates(_selection_batch_capability_candidates(registry=registry))
    candidate_capabilities = tuple(item for item in ordered_candidates if item.candidate_type != "unsupported")
    provider_candidates = tuple(dict.fromkeys(item.capability.provider for item in candidate_capabilities))
    coverage_groups = tuple(
        dict.fromkeys(
            item.capability.coverage_group
            for item in candidate_capabilities
            if item.capability.coverage_group
        )
    )
    if not provider_candidates:
        provider_candidates = (_CN_A_SELECTION_BATCH_DEFAULT_PROVIDER,)
    if not coverage_groups:
        coverage_groups = (_CN_A_SELECTION_BATCH_DEFAULT_COVERAGE_GROUP,)
    resolved_plan_id = plan_id or f"plan://selection/cn_a/{trade_date}/batch-v1"
    return SelectionProviderBatchPlan(
        plan_id=resolved_plan_id,
        scope=SelectionBatchScope.SELECTION_BATCH,
        market=market,
        profile=profile,
        trade_date=trade_date,
        lookback_trading_days=260,
        universe_scope="all_a_shares",
        coverage_groups=coverage_groups,
        provider_candidates=provider_candidates,
        ttl_policy_ref="ttl://selection/cn_a/batch-v1/900s",
        lineage_root_ref=f"lineage://selection/cn_a/{trade_date}/batch-v1",
    )


def resolve_cn_a_closed_trade_date(now: datetime) -> str:
    value = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
    shanghai_now = value.astimezone(ZoneInfo("Asia/Shanghai"))
    close_cutoff = shanghai_now.replace(hour=16, minute=0, second=0, microsecond=0)
    if shanghai_now >= close_cutoff:
        return shanghai_now.date().isoformat()
    return (shanghai_now.date() - timedelta(days=1)).isoformat()


def load_cn_a_selection_v1_strategy_config_ref(
    market: SelectionMarket,
    profile: SelectionProfile,
) -> str | None:
    return _load_cn_a_selection_v1_strategy_config_ref(market, profile)


def resolve_cn_a_closed_trade_date_for_scheduler(trade_date: str | None, *, now: datetime | None = None) -> str:
    if trade_date is not None and trade_date.strip():
        return trade_date.strip()
    return resolve_cn_a_closed_trade_date(now or datetime.now(tz=UTC))


def fetch_selection_batch_from_data_gateway(
    plan: SelectionRunPlan,
    *,
    evidence_root: Path | None = None,
) -> SelectionProviderBatchResult:
    _ = evidence_root
    gateway = _build_selection_gateway_context()
    provider_plan = _build_selection_provider_batch_plan_from_registry(
        registry=gateway.registry,
        market=plan.market,
        profile=plan.profile,
        trade_date=plan.trade_date,
        plan_id=plan.provider_batch_plan_ref,
    )
    runtime = gateway.evidence

    attempts: list[str] = []
    normalized_refs: list[str] = []
    data_gaps: list[DataGapRef] = []
    deferred_data_gaps: list[DataGapRef] = []
    provider_source_matrix: dict[str, Mapping[str, Any]] = {}
    request = _selection_batch_request(plan)

    manifest_load_error = gateway.manifest_load_state.error
    if manifest_load_error:
        manifest_error_spec = _selection_batch_spec(
            request=request,
            provider_config_version=gateway.provider_config_version,
            capability=None,
        )
        attempt_ref = _record_attempt_only(
            runtime=runtime,
                attempt=_attempt_for_precheck(
                    request=request,
                    spec=manifest_error_spec,
                    status=ProviderStatus.SKIPPED_NOT_CONFIGURED,
                    error_code="selection_batch_manifest_load_failed",
                    error_message=manifest_load_error,
                    source_metadata=_selection_source_metadata_for_spec(spec=manifest_error_spec),
                ),
            )
        if attempt_ref is not None:
            attempts.append(attempt_ref)
        attempt_refs = _selection_gap_attempt_refs(
            refs=tuple(attempts),
            default_ref=provider_plan.lineage_root_ref,
        )
        data_gaps.append(
            _blocker_gap(
                gap_id=f"{plan.selection_run_id}-selection-batch-manifest-load-failed",
                gap_code="selection_batch_manifest_load_failed",
                attempt_refs=attempt_refs,
                reader_message=(
                    "selection batch 读取 enabled user manifest 失败，"
                    "为避免隐藏替代成功路径已按 fail closed 终止："
                    f"{manifest_load_error}。"
                ),
                source_metadata=_selection_source_metadata_for_spec(spec=manifest_error_spec),
            )
        )
        finalized_refs = _attach_openviking_ref_or_gap(
            runtime=runtime,
            plan=plan,
            provider_plan=provider_plan,
            attempt_refs=attempt_refs,
            normalized_refs=(),
            data_gaps=data_gaps,
            status="failed",
            reason="manifest_load_failed",
        )
        return SelectionProviderBatchResult(
            provider_batch_plan=provider_plan,
            attempt_refs=finalized_refs,
            normalized_refs=(),
            rows=(),
            data_gaps=tuple(data_gaps),
        )

    helper = runtime.helper
    if (
        helper is None
        or runtime.cache_store is None
        or runtime.rate_limit_store is None
        or runtime.single_flight is None
        or runtime.attempt_store is None
    ):
        attempt_refs = _selection_gap_attempt_refs(
            refs=tuple(attempts),
            default_ref=provider_plan.lineage_root_ref,
        )
        data_gaps.append(
            _blocker_gap(
                gap_id=f"{plan.selection_run_id}-selection-batch-evidence-store-unavailable",
                gap_code="selection_batch_evidence_store_unavailable",
                attempt_refs=attempt_refs,
                reader_message=(
                    "selection batch 未连接 data_gateway Mongo evidence store，"
                    f"无法写 raw/http/attempt/normalized 证据：{runtime.bootstrap_error or 'unknown'}。"
                ),
            )
        )
        finalized_refs = _attach_openviking_ref_or_gap(
            runtime=runtime,
            plan=plan,
            provider_plan=provider_plan,
            attempt_refs=attempt_refs,
            normalized_refs=(),
            data_gaps=data_gaps,
            status="failed",
            reason="mongo_evidence_unavailable",
        )
        return SelectionProviderBatchResult(
            provider_batch_plan=provider_plan,
            attempt_refs=finalized_refs,
            normalized_refs=(),
            rows=(),
            data_gaps=tuple(data_gaps),
        )

    candidate_capabilities = _selection_batch_capability_candidates(registry=gateway.registry)
    if not candidate_capabilities:
        missing_spec = _selection_batch_spec(
            request=request,
            provider_config_version=gateway.provider_config_version,
            capability=None,
        )
        attempt_ref = _record_attempt_only(
            runtime=runtime,
                attempt=_attempt_for_precheck(
                    request=request,
                    spec=missing_spec,
                    status=ProviderStatus.SKIPPED_NOT_CONFIGURED,
                    error_code="selection_batch_source_not_configured",
                    error_message="selection batch capability missing in provider registry",
                    source_metadata=_selection_source_metadata_for_spec(spec=missing_spec),
                ),
            )
        if attempt_ref is not None:
            attempts.append(attempt_ref)
        attempt_refs = _selection_gap_attempt_refs(
            refs=tuple(attempts),
            default_ref=provider_plan.lineage_root_ref,
        )
        data_gaps.append(
            _blocker_gap(
                gap_id=f"{plan.selection_run_id}-selection-batch-capability-missing",
                gap_code="selection_batch_source_not_configured",
                attempt_refs=attempt_refs,
                reader_message="CN_A selection batch capability 未配置，任务失败且不切换到旧路径。",
                source_metadata=_selection_source_metadata_for_spec(spec=missing_spec),
            )
        )
        finalized_refs = _attach_openviking_ref_or_gap(
            runtime=runtime,
            plan=plan,
            provider_plan=provider_plan,
            attempt_refs=attempt_refs,
            normalized_refs=(),
            data_gaps=data_gaps,
            status="failed",
            reason="capability_missing",
        )
        return SelectionProviderBatchResult(
            provider_batch_plan=provider_plan,
            attempt_refs=finalized_refs,
            normalized_refs=(),
            rows=(),
            data_gaps=tuple(data_gaps),
        )

    ordered_candidates = _selection_batch_ordered_candidates(candidate_capabilities)

    for candidate in ordered_candidates:
        capability = candidate.capability
        adapter = gateway.adapters_by_id.get(capability.adapter_id)
        spec = _selection_batch_spec(
            request=request,
            provider_config_version=gateway.provider_config_version,
            capability=capability,
            adapter=adapter,
        )
        source_metadata = _selection_source_metadata_for_spec(spec=spec)
        runtime_source_metadata = dict(source_metadata)
        provider_source_matrix[f"{capability.provider}:{capability.endpoint}"] = runtime_source_metadata
        if candidate.candidate_type == "unsupported":
            blocked_message = (
                f"provider={capability.provider}, endpoint={capability.endpoint}, reason={candidate.blocked_reason or 'blocked'}"
            )
            attempt_ref = _record_attempt_only(
                runtime=runtime,
                attempt=_attempt_for_precheck(
                    request=request,
                    spec=spec,
                    status=ProviderStatus.SKIPPED_NOT_CONFIGURED,
                    error_code="selection_batch_capability_blocked",
                    error_message=blocked_message,
                    source_metadata=source_metadata,
                ),
            )
            if attempt_ref is not None:
                attempts.append(attempt_ref)
            deferred_data_gaps.append(
                _blocker_gap(
                    gap_id=f"{plan.selection_run_id}-{capability.adapter_id}-batch-capability-blocked",
                    gap_code="selection_batch_capability_blocked",
                    attempt_refs=_selection_gap_attempt_refs(
                        refs=tuple(attempts),
                        default_ref=provider_plan.lineage_root_ref,
                    ),
                    reader_message=(
                        f"selection batch 跳过 {capability.provider}/{capability.endpoint}："
                        f"{candidate.blocked_reason or 'capability blocked'}。"
                        f" source_metadata={json.dumps(source_metadata, ensure_ascii=False, sort_keys=True)}"
                    ),
                    source_metadata=source_metadata,
                )
            )
            continue

        is_partial_candidate = candidate.candidate_type == "partial_batch_candidate"
        if adapter is None:
            attempt_ref = _record_attempt_only(
                runtime=runtime,
                attempt=_attempt_for_precheck(
                    request=request,
                    spec=spec,
                    status=ProviderStatus.SKIPPED_NOT_CONFIGURED,
                    error_code="adapter_not_configured",
                    error_message=f"adapter {capability.adapter_id} not configured",
                    source_metadata=source_metadata,
                ),
            )
            if attempt_ref is not None:
                attempts.append(attempt_ref)
            data_gaps.append(
                _blocker_gap(
                    gap_id=f"{plan.selection_run_id}-{capability.adapter_id}-adapter-missing",
                    gap_code="selection_batch_source_not_configured",
                    attempt_refs=_selection_gap_attempt_refs(
                        refs=tuple(attempts),
                        default_ref=provider_plan.lineage_root_ref,
                    ),
                    reader_message=f"selection batch adapter {capability.adapter_id} 未配置，任务失败。",
                    source_metadata=source_metadata,
                )
            )
            continue

        credential = adapter.validate_credentials()
        if not credential.ok:
            attempt_ref = _record_attempt_only(
                runtime=runtime,
                attempt=_attempt_for_precheck(
                    request=request,
                    spec=spec,
                    status=ProviderStatus.CREDENTIAL_MISSING,
                    error_code="credential_missing",
                    error_message=credential.root_cause or "credential missing",
                    source_metadata=source_metadata,
                ),
            )
            if attempt_ref is not None:
                attempts.append(attempt_ref)
            data_gaps.append(
                _blocker_gap(
                    gap_id=f"{plan.selection_run_id}-{capability.adapter_id}-credential-missing",
                    gap_code="selection_batch_credential_missing",
                    attempt_refs=_selection_gap_attempt_refs(
                        refs=tuple(attempts),
                        default_ref=provider_plan.lineage_root_ref,
                    ),
                    reader_message=f"selection batch provider 凭证缺失：{','.join(credential.missing_keys)}。",
                    source_metadata=source_metadata,
                )
            )
            continue

        owner_result: dict[str, ProviderResult] = {}
        started_at = utc_now_iso()

        def owner_call() -> ProviderResult:
            value = helper.execute(
                request=request,
                spec=spec,
                adapter=adapter,
                started_at=started_at,
            )
            owner_result["value"] = value
            return value

        gate_result = run_provider_call_gate(
            request=request,
            spec=spec,
            cache_store=runtime.cache_store,
            rate_limit_store=runtime.rate_limit_store,
            single_flight=runtime.single_flight,
            attempt_store=runtime.attempt_store,
            owner_call=owner_call,
        )
        if gate_result.status == ProviderStatus.REMOTE_SUCCESS and "value" in owner_result:
            result = owner_result["value"]
        elif gate_result.status not in {
            ProviderStatus.CACHE_HIT,
            ProviderStatus.CACHED_EMPTY,
            ProviderStatus.RATE_LIMITED,
            ProviderStatus.SHARED_RESULT,
        } and "value" in owner_result:
            result = owner_result["value"]
        else:
            result = _selection_provider_result_from_gate_result(
                runtime=runtime,
                request=request,
                spec=spec,
                gate_result=gate_result,
            )
        evidence_chain_failed = False
        attempt_ref, attempt_read = _verified_attempt_ref(runtime=runtime, attempt_id=result.attempt.attempt_id)
        if attempt_read is not None and attempt_read.source_metadata:
            runtime_source_metadata = _merge_selection_source_metadata(
                base=runtime_source_metadata,
                runtime=attempt_read.source_metadata,
            )
            provider_source_matrix[f"{capability.provider}:{capability.endpoint}"] = runtime_source_metadata
        if attempt_ref is not None:
            attempts.append(attempt_ref)
        if attempt_ref is None:
            evidence_chain_failed = True
            data_gaps.append(
                _blocker_gap(
                    gap_id=f"{plan.selection_run_id}-{capability.adapter_id}-attempt-readback-failed",
                    gap_code="selection_batch_evidence_readback_failed",
                    attempt_refs=_selection_gap_attempt_refs(
                        refs=tuple(attempts),
                        default_ref=provider_plan.lineage_root_ref,
                    ),
                    reader_message=(
                        "selection batch attempt evidence readback 失败，"
                        f"attempt_id={result.attempt.attempt_id}。"
                    ),
                )
            )
        else:
            chain_error = _verify_attempt_evidence_chain(
                runtime=runtime,
                attempt=attempt_read,
            )
            if chain_error is not None:
                evidence_chain_failed = True
                data_gaps.append(
                    _blocker_gap(
                        gap_id=f"{plan.selection_run_id}-{capability.adapter_id}-attempt-chain-invalid",
                        gap_code="selection_batch_evidence_readback_failed",
                        attempt_refs=(attempt_ref,),
                        reader_message=f"selection batch attempt evidence 链校验失败：{chain_error}",
                    )
                )
        normalized_ref = (
            _verified_normalized_ref_for_selection(runtime=runtime, normalized_ref=result.normalized_ref)
            if result.normalized_ref
            else None
        )
        if normalized_ref is not None:
            normalized_refs.append(normalized_ref)
            runtime_source_metadata = _merge_selection_source_metadata(
                base=runtime_source_metadata,
                runtime=_selection_chunk_metadata_from_normalized_ref(
                    runtime=runtime,
                    normalized_ref=result.normalized_ref,
                ),
            )
            provider_source_matrix[f"{capability.provider}:{capability.endpoint}"] = runtime_source_metadata
        if result.normalized_ref and normalized_ref is None:
            evidence_chain_failed = True
            data_gaps.append(
                _blocker_gap(
                    gap_id=f"{plan.selection_run_id}-{capability.adapter_id}-normalized-readback-failed",
                    gap_code="selection_batch_evidence_readback_failed",
                    attempt_refs=_selection_gap_attempt_refs(
                        refs=tuple(attempts),
                        default_ref=provider_plan.lineage_root_ref,
                    ),
                    reader_message=f"selection batch normalized readback 失败：{result.normalized_ref}",
                )
            )
        quality_gap = _selection_row_drop_quality_gap(
            runtime=runtime,
            selection_run_id=plan.selection_run_id,
            adapter_id=capability.adapter_id,
            normalized_ref=result.normalized_ref,
            attempt_refs=_selection_gap_attempt_refs(
                refs=tuple(attempts),
                default_ref=provider_plan.lineage_root_ref,
            ),
        )
        if quality_gap is not None:
            data_gaps.append(quality_gap)

        if (
            result.status in _SELECTION_USABLE_DATA_STATUSES
            and result.row_count > 0
            and normalized_ref is not None
            and not evidence_chain_failed
        ):
            missing_fail_closed_fields = _selection_missing_fail_closed_required_fields(
                plan=plan,
                rows=result.rows,
            )
            if missing_fail_closed_fields:
                data_gaps.append(
                    _warn_gap(
                        gap_id=f"{plan.selection_run_id}-{capability.adapter_id}-fail-closed-fields-missing",
                        gap_code="selection_batch_strategy_fields_missing",
                        attempt_refs=_selection_gap_attempt_refs(
                            refs=tuple(attempts),
                            default_ref=provider_plan.lineage_root_ref,
                        ),
                        reader_message=(
                            f"selection batch provider 返回可用数据（status={result.status.value}），"
                            "但缺少硬过滤或 stable top20 tie-break 必需字段："
                            f"{','.join(missing_fail_closed_fields)}；"
                            "按现有 fail-closed 语义跳过该 provider，继续尝试后续 provider。"
                        ),
                        source_metadata=runtime_source_metadata,
                    )
                )
                continue
            missing_strategy_fields = _selection_missing_strategy_required_fields(
                plan=plan,
                rows=result.rows,
            )
            if missing_strategy_fields:
                data_gaps.append(
                    _warn_gap(
                        gap_id=f"{plan.selection_run_id}-{capability.adapter_id}-strategy-fields-missing",
                        gap_code="selection_batch_strategy_fields_missing",
                        attempt_refs=_selection_gap_attempt_refs(
                            refs=tuple(attempts),
                            default_ref=provider_plan.lineage_root_ref,
                        ),
                        reader_message=(
                            f"selection batch provider 返回可用数据（status={result.status.value}），"
                            "但缺少 approved strategy 必需字段："
                            f"{','.join(missing_strategy_fields)}；"
                            "按 fail-closed 语义跳过该 provider，继续尝试后续 provider。"
                        ),
                        source_metadata=runtime_source_metadata,
                    )
                )
                continue
            recovered_data_gaps = _downgrade_recovered_provider_source_gaps(data_gaps=data_gaps)
            openviking_ref = _write_openviking_evidence_ref(
                runtime=runtime,
                plan=plan,
                provider_plan=provider_plan,
                attempt_refs=tuple(attempts),
                normalized_refs=tuple(normalized_refs),
                status="success",
                reason=None,
                provider_source_matrix=provider_source_matrix,
            )
            if openviking_ref is None:
                recovered_data_gaps.append(
                    _blocker_gap(
                        gap_id=f"{plan.selection_run_id}-{capability.adapter_id}-openviking-evidence-unavailable",
                        gap_code="selection_batch_openviking_evidence_unavailable",
                        attempt_refs=_selection_gap_attempt_refs(
                            refs=tuple(attempts),
                            default_ref=provider_plan.lineage_root_ref,
                        ),
                        reader_message=(
                            "selection batch OpenViking evidence 写入或 readback 校验失败，"
                            "按 fail closed 终止 completed run。"
                        ),
                    )
                )
                finalized_refs = _selection_gap_attempt_refs(
                    refs=tuple(attempts),
                    default_ref=provider_plan.lineage_root_ref,
                )
                return SelectionProviderBatchResult(
                    provider_batch_plan=provider_plan,
                    attempt_refs=finalized_refs,
                    normalized_refs=tuple(normalized_refs),
                    rows=(),
                    data_gaps=tuple(recovered_data_gaps),
                )
            mapped_rows = tuple(_map_selection_row(row=row, source_ref=normalized_ref) for row in result.rows)
            final_attempts = tuple(_dedup_refs((*attempts, openviking_ref)))
            return SelectionProviderBatchResult(
                provider_batch_plan=provider_plan,
                attempt_refs=final_attempts,
                normalized_refs=tuple(normalized_refs),
                rows=mapped_rows,
                data_gaps=tuple(recovered_data_gaps),
                warehouse_check_ref=_selection_warehouse_check_ref(
                    plan=plan,
                    normalized_refs=tuple(normalized_refs),
                    attempt_refs=final_attempts,
                ),
            )

        if result.status == ProviderStatus.REMOTE_SUCCESS and evidence_chain_failed:
            data_gaps.append(
                _blocker_gap(
                    gap_id=f"{plan.selection_run_id}-{capability.adapter_id}-remote-success-evidence-chain-failed",
                    gap_code="selection_batch_evidence_readback_failed",
                    attempt_refs=_selection_gap_attempt_refs(
                        refs=tuple(attempts),
                        default_ref=provider_plan.lineage_root_ref,
                    ),
                    reader_message="selection batch provider 远端成功但 evidence chain/readback 校验失败，按 fail closed 终止。",
                )
            )
            continue

        gap_factory = _warn_gap if is_partial_candidate else _blocker_gap
        data_gaps.append(
            gap_factory(
                gap_id=f"{plan.selection_run_id}-{capability.adapter_id}-{result.status.value}",
                gap_code=_selection_gap_code_for_status(result.status),
                attempt_refs=_selection_gap_attempt_refs(
                    refs=tuple(attempts),
                    default_ref=provider_plan.lineage_root_ref,
                ),
                reader_message=(
                    _selection_partial_gap_message_for_result(result=result)
                    if is_partial_candidate
                    else _selection_gap_message_for_result(result=result)
                ),
                source_metadata=runtime_source_metadata,
            )
        )
        if is_partial_candidate:
            continue

    if deferred_data_gaps:
        data_gaps.extend(deferred_data_gaps)
    if not data_gaps:
        data_gaps.append(
            _blocker_gap(
                gap_id=f"{plan.selection_run_id}-selection-batch-no-data",
                gap_code="selection_batch_empty",
                attempt_refs=_selection_gap_attempt_refs(
                    refs=tuple(attempts),
                    default_ref=provider_plan.lineage_root_ref,
                ),
                reader_message="selection batch 未获取到可用数据，任务失败且不切换到旧路径。",
            )
        )
    final_attempts = _attach_openviking_ref_or_gap(
        runtime=runtime,
        plan=plan,
        provider_plan=provider_plan,
        attempt_refs=_selection_gap_attempt_refs(
            refs=tuple(attempts),
            default_ref=provider_plan.lineage_root_ref,
        ),
        normalized_refs=tuple(normalized_refs),
        data_gaps=data_gaps,
        status="failed",
        reason="no_usable_selection_data",
        provider_source_matrix=provider_source_matrix,
    )
    return SelectionProviderBatchResult(
        provider_batch_plan=provider_plan,
        attempt_refs=final_attempts,
        normalized_refs=tuple(normalized_refs),
        rows=(),
        data_gaps=tuple(data_gaps),
    )


@dataclass(frozen=True)
class _SelectionEvidenceRuntime:
    helper: ProviderExecutionEvidenceHelper | None
    attempt_store: MongoAttemptStore | None = None
    cache_store: MongoCacheStore | None = None
    rate_limit_store: MongoRateLimitStore | None = None
    single_flight: MongoSingleFlightCoordinator | None = None
    normalized_store: MongoNormalizedStore | None = None
    raw_store: MongoRawPayloadStore | None = None
    http_evidence_store: MongoProviderHttpEvidenceStore | None = None
    openviking_backend: OpenVikingHttpBackend | None = None
    bootstrap_error: str | None = None


@dataclass(frozen=True)
class _SelectionManifestLoadState:
    manifests: tuple[DeclarativeProviderManifest, ...]
    error: str | None = None


@dataclass(frozen=True)
class _SelectionGatewayContext:
    registry: ProviderRegistry
    adapters_by_id: Mapping[str, Any]
    provider_config_version: str
    evidence: _SelectionEvidenceRuntime
    manifest_load_state: _SelectionManifestLoadState


def _build_selection_gateway_context() -> _SelectionGatewayContext:
    runtime_env = build_runtime_data_source_env()
    registry, provider_config_version, manifest_load_state = _build_selection_provider_registry_context(
        runtime_env=runtime_env,
    )
    default_adapters = build_default_provider_adapters(provider_config_version=provider_config_version, env=runtime_env)
    selection_adapters = build_cn_a_selection_batch_adapters(provider_config_version=provider_config_version, env=runtime_env)
    adapters = tuple(default_adapters) + tuple(selection_adapters)
    return _SelectionGatewayContext(
        registry=registry,
        adapters_by_id={adapter.adapter_id: adapter for adapter in adapters},
        provider_config_version=provider_config_version,
        evidence=_build_selection_evidence_runtime(),
        manifest_load_state=manifest_load_state,
    )


def _build_selection_provider_registry_context(
    *,
    runtime_env: Mapping[str, str] | None = None,
) -> tuple[ProviderRegistry, str, _SelectionManifestLoadState]:
    resolved_env = build_runtime_data_source_env(runtime_env)
    system_capabilities = tuple(load_default_system_capabilities()) + tuple(cn_a_selection_batch_capabilities())
    manifest_load_state = _load_enabled_selection_user_manifests()
    registry = ProviderRegistry.from_enabled_manifests(
        enabled_manifests=manifest_load_state.manifests,
        system_capabilities=system_capabilities,
    )
    _apply_selection_runtime_user_preferences(registry=registry, env=resolved_env)
    provider_config_version = default_provider_config_version(registry.all_capabilities())
    return registry, provider_config_version, manifest_load_state


def _apply_selection_runtime_user_preferences(*, registry: ProviderRegistry, env: Mapping[str, str]) -> None:
    if not str(env.get("TUSHARE_TOKEN", "")).strip():
        return
    registry.apply_user_preferred(
        adapter_id=_CN_A_SELECTION_BATCH_TUSHARE_ADAPTER_ID,
        market=Market.CN_A,
        domain=PackDomain.MARKET,
        source_role=SourceRole.MARKET_DATA,
        coverage_group=_CN_A_SELECTION_BATCH_DEFAULT_COVERAGE_GROUP,
    )


def _load_enabled_selection_user_manifests() -> _SelectionManifestLoadState:
    mongo_uri = (
        os.environ.get(_ENV_DATA_GATEWAY_MONGODB_URI, "").strip()
        or os.environ.get(_FALLBACK_MONGO_URI_ENV, "").strip()
    )
    if not mongo_uri:
        return _SelectionManifestLoadState(manifests=())
    try:
        from pymongo import MongoClient
    except Exception as exc:  # noqa: BLE001
        return _SelectionManifestLoadState(manifests=(), error=f"pymongo import failed: {exc}")
    try:
        database_name = _resolve_mongo_database_name(mongo_uri)
        client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
        manifest_store = MongoProviderManifestStore(client[database_name][OPENBB_PROVIDER_MANIFESTS])
        return _SelectionManifestLoadState(
            manifests=manifest_store.enabled_candidates(
                market=Market.CN_A,
                domain=PackDomain.MARKET,
                endpoint=_CN_A_SELECTION_BATCH_ENDPOINT,
            ),
        )
    except Exception as exc:  # noqa: BLE001
        return _SelectionManifestLoadState(
            manifests=(),
            error=f"selection manifest load failed: {type(exc).__name__}: {exc}",
        )


def _build_selection_evidence_runtime() -> _SelectionEvidenceRuntime:
    mongo_uri = (
        os.environ.get(_ENV_DATA_GATEWAY_MONGODB_URI, "").strip()
        or os.environ.get(_FALLBACK_MONGO_URI_ENV, "").strip()
    )
    if not mongo_uri:
        return _SelectionEvidenceRuntime(
            helper=None,
            bootstrap_error=f"missing {_ENV_DATA_GATEWAY_MONGODB_URI}/{_FALLBACK_MONGO_URI_ENV}",
            openviking_backend=_build_openviking_backend(),
        )
    try:
        from pymongo import MongoClient
    except Exception as exc:  # noqa: BLE001
        return _SelectionEvidenceRuntime(
            helper=None,
            bootstrap_error=f"pymongo import failed: {exc}",
            openviking_backend=_build_openviking_backend(),
        )

    try:
        database_name = _resolve_mongo_database_name(mongo_uri)
        client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
        database = client[database_name]
        ensure_openbb_store_indexes(database)
        raw_store = MongoRawPayloadStore(database[OPENBB_RAW_PAYLOADS])
        normalized_store = MongoNormalizedStore(database[OPENBB_NORMALIZED])
        attempt_store = MongoAttemptStore(database[OPENBB_PROVIDER_ATTEMPTS])
        cache_store = MongoCacheStore(database[OPENBB_CACHE_ENTRIES])
        rate_limit_store = MongoRateLimitStore(database[OPENBB_RATE_LIMITS])
        http_store = MongoProviderHttpEvidenceStore(database[OPENBB_PROVIDER_HTTP_EVIDENCE])
        helper = ProviderExecutionEvidenceHelper(
            raw_store=raw_store,
            normalized_store=normalized_store,
            attempt_store=attempt_store,
            http_evidence_store=http_store,
        )
        return _SelectionEvidenceRuntime(
            helper=helper,
            attempt_store=attempt_store,
            cache_store=cache_store,
            rate_limit_store=rate_limit_store,
            single_flight=MongoSingleFlightCoordinator(
                collection=database[OPENBB_SINGLE_FLIGHT_CALLS],
                attempt_store=attempt_store,
            ),
            normalized_store=normalized_store,
            raw_store=raw_store,
            http_evidence_store=http_store,
            openviking_backend=_build_openviking_backend(),
            bootstrap_error=None,
        )
    except Exception as exc:  # noqa: BLE001
        return _SelectionEvidenceRuntime(
            helper=None,
            bootstrap_error=str(exc),
            openviking_backend=_build_openviking_backend(),
        )


def _resolve_mongo_database_name(mongo_uri: str) -> str:
    configured = os.environ.get(_ENV_DATA_GATEWAY_MONGODB_DATABASE, "").strip()
    if configured:
        return configured
    parsed = urlparse(mongo_uri)
    path_name = parsed.path.strip("/")
    if path_name:
        return path_name.split("/", 1)[0]
    return "claw_trade_openbb"


def _selection_batch_request(plan: SelectionRunPlan) -> PackRequest:
    return PackRequest(
        run_id=plan.selection_run_id,
        call_id="selection-batch",
        worker_id="selection_data_job",
        market=Market.CN_A,
        domain=PackDomain.MARKET,
        ticker="CN_A_SELECTION_BATCH",
        company_name="A股全市场批量行情",
        start_date=plan.trade_date,
        end_date=plan.trade_date,
        current_date=plan.trade_date,
        currency="CNY",
        profile=plan.profile.value,
        freshness_policy=FreshnessPolicy(max_age_seconds=900),
    )


def _selection_batch_spec(
    *,
    request: PackRequest,
    provider_config_version: str,
    capability: ProviderCapability | None,
    adapter: Any | None = None,
) -> ProviderCallSpec:
    if capability is None:
        default_capability = _selection_batch_capabilities_catalog()
        first_capability = default_capability[0] if default_capability else None
        return ProviderCallSpec(
            call_key=(
                f"{PackDomain.MARKET.value}:"
                f"{(first_capability.adapter_id if first_capability else _CN_A_SELECTION_BATCH_DEFAULT_ADAPTER_ID)}:"
                f"{_CN_A_SELECTION_BATCH_ENDPOINT}"
            ),
            provider=(first_capability.provider if first_capability else _CN_A_SELECTION_BATCH_DEFAULT_PROVIDER),
            adapter_id=(first_capability.adapter_id if first_capability else _CN_A_SELECTION_BATCH_DEFAULT_ADAPTER_ID),
            provider_kind=(first_capability.provider_kind if first_capability else ProviderKind.PROJECT_EXTENSION),
            provider_config_version=provider_config_version,
            endpoint=_CN_A_SELECTION_BATCH_ENDPOINT,
            source_role=(first_capability.source_role if first_capability else SourceRole.MARKET_DATA),
            market=Market.CN_A,
            domain=PackDomain.MARKET,
            required=(first_capability.required if first_capability else True),
            attempt_required=True,
            coverage_group=(
                first_capability.coverage_group if first_capability and first_capability.coverage_group else _CN_A_SELECTION_BATCH_DEFAULT_COVERAGE_GROUP
            ),
            coverage_quorum=(first_capability.coverage_quorum if first_capability and first_capability.coverage_quorum else 1),
            params={"trade_date": request.current_date, "currency": "CNY", "timezone": "Asia/Shanghai"},
            cache_ttl_seconds=900,
            license_policy_id=(first_capability.license_policy_id if first_capability else "personal_research"),
            expected_schema_id=(first_capability.expected_schema_id if first_capability else "cn_a.selection.batch.v1"),
            priority=(first_capability.priority if first_capability else 0),
            priority_source=PrioritySource.SYSTEM_DEFAULT,
            user_preferred=False,
            raw_export_policy=(first_capability.raw_export_policy if first_capability else "metadata_only"),
        )
    if adapter is None:
        return replace(
            _selection_batch_spec(
                request=request,
                provider_config_version=provider_config_version,
                capability=None,
            ),
            provider=capability.provider,
            adapter_id=capability.adapter_id,
            provider_kind=capability.provider_kind,
            source_role=capability.source_role,
            required=capability.required,
            attempt_required=capability.attempt_required,
            coverage_group=capability.coverage_group,
            coverage_quorum=capability.coverage_quorum,
            license_policy_id=capability.license_policy_id,
            expected_schema_id=capability.expected_schema_id,
            priority=capability.priority,
            priority_source=capability.priority_source,
            user_preferred=(capability.priority_source == PrioritySource.USER_PREFERRED),
            raw_export_policy=capability.raw_export_policy,
        )
    specs = tuple(item for item in adapter.build_call_specs(request) if item.endpoint == capability.endpoint)
    if not specs:
        raise ValueError(f"selection batch endpoint missing in adapter: {capability.adapter_id}/{capability.endpoint}")
    spec = specs[0]
    return replace(
        spec,
        provider_config_version=provider_config_version,
        priority_source=capability.priority_source,
        user_preferred=(capability.priority_source == PrioritySource.USER_PREFERRED),
    )


def _attempt_for_precheck(
    *,
    request: PackRequest,
    spec: ProviderCallSpec,
    status: ProviderStatus,
    error_code: str,
    error_message: str,
    source_metadata: Mapping[str, Any] | None = None,
) -> ProviderAttempt:
    now = utc_now_iso()
    return ProviderAttempt(
        attempt_id=f"{request.run_id}:{request.call_id}:{spec.adapter_id}:{spec.endpoint}",
        run_id=request.run_id,
        call_id=request.call_id,
        worker_id=request.worker_id,
        pack=request.domain.value,
        provider=spec.provider,
        adapter_id=spec.adapter_id,
        adapter_kind=spec.provider_kind.value,
        provider_kind=spec.provider_kind,
        provider_config_version=spec.provider_config_version,
        endpoint=spec.endpoint,
        source_role=spec.source_role,
        started_at=now,
        finished_at=now,
        status=status,
        required=spec.required,
        attempt_required=spec.attempt_required,
        coverage_group=spec.coverage_group,
        coverage_quorum=spec.coverage_quorum,
        priority_source=spec.priority_source,
        user_preferred=spec.user_preferred,
        from_cache=False,
        cache_status=None,
        single_flight_role="none",
        shared_from_attempt_id=None,
        latency_ms=0,
        row_count=0,
        raw_ref=None,
        normalized_ref=None,
        error_code=error_code,
        error_message=error_message,
        schema_id=spec.expected_schema_id,
        license_note="approved",
        source_metadata=dict(source_metadata) if source_metadata else None,
    )


def _record_attempt_only(*, runtime: _SelectionEvidenceRuntime, attempt: ProviderAttempt) -> str | None:
    store = runtime.attempt_store
    if store is None:
        return None
    try:
        store.write(attempt)
    except Exception:  # noqa: BLE001
        return None
    attempt_ref, _ = _verified_attempt_ref(runtime=runtime, attempt_id=attempt.attempt_id)
    return attempt_ref


def _selection_provider_result_from_gate_result(
    *,
    runtime: _SelectionEvidenceRuntime,
    request: PackRequest,
    spec: ProviderCallSpec,
    gate_result: ProviderCallResult,
) -> ProviderResult:
    attempt = None
    if gate_result.attempt_ref and runtime.attempt_store is not None:
        try:
            attempt = runtime.attempt_store.get(gate_result.attempt_ref)
        except Exception:  # noqa: BLE001
            attempt = None
    if attempt is None:
        attempt = ProviderAttempt(
            attempt_id=gate_result.attempt_ref or f"{request.run_id}:{request.call_id}:{spec.adapter_id}:{spec.endpoint}:gate",
            run_id=request.run_id,
            call_id=request.call_id,
            worker_id=request.worker_id,
            pack=request.domain.value,
            provider=spec.provider,
            adapter_id=spec.adapter_id,
            adapter_kind=spec.provider_kind.value,
            provider_kind=spec.provider_kind,
            provider_config_version=spec.provider_config_version,
            endpoint=spec.endpoint,
            source_role=spec.source_role,
            started_at=utc_now_iso(),
            finished_at=utc_now_iso(),
            status=gate_result.status,
            required=spec.required,
            attempt_required=spec.attempt_required,
            coverage_group=spec.coverage_group,
            coverage_quorum=spec.coverage_quorum,
            priority_source=spec.priority_source,
            user_preferred=spec.user_preferred,
            from_cache=gate_result.status in {ProviderStatus.CACHE_HIT, ProviderStatus.CACHED_EMPTY},
            cache_status=gate_result.status if gate_result.status in {ProviderStatus.CACHE_HIT, ProviderStatus.CACHED_EMPTY} else None,
            single_flight_role="consumer" if gate_result.status == ProviderStatus.SHARED_RESULT else "none",
            shared_from_attempt_id=gate_result.shared_owner_attempt_ref,
            latency_ms=0,
            row_count=None,
            raw_ref=gate_result.raw_payload_ref,
            normalized_ref=gate_result.normalized_ref,
            error_code=gate_result.status.value if gate_result.data_gaps else None,
            error_message=gate_result.data_gaps[0].root_cause if gate_result.data_gaps else None,
            schema_id=spec.expected_schema_id,
            license_note="provider call gate result",
        )
    rows = _selection_rows_from_normalized_ref(runtime=runtime, normalized_ref=gate_result.normalized_ref)
    row_count = len(rows) if rows else int(attempt.row_count or 0)
    return ProviderResult(
        spec=spec,
        status=gate_result.status,
        request_id=None,
        requested_at=utc_now_iso(),
        latency_ms=0,
        source_role=spec.source_role,
        freshness=FreshnessStatus.FRESH_CACHE
        if gate_result.status == ProviderStatus.CACHE_HIT
        else FreshnessStatus.FRESH_REMOTE
        if gate_result.status == ProviderStatus.SHARED_RESULT
        else FreshnessStatus.NOT_FETCHED,
        license_note=attempt.license_note,
        raw_ref=gate_result.raw_payload_ref,
        normalized_ref=gate_result.normalized_ref,
        rows=rows,
        row_count=row_count,
        cache_receipt=None,
        attempt=attempt,
        error_code=attempt.error_code,
        error_message=attempt.error_message,
    )


def _selection_rows_from_normalized_ref(
    *,
    runtime: _SelectionEvidenceRuntime,
    normalized_ref: str | None,
) -> tuple[Mapping[str, Any], ...]:
    if not normalized_ref or runtime.normalized_store is None:
        return ()
    try:
        normalized = runtime.normalized_store.read(normalized_ref)
    except Exception:  # noqa: BLE001
        return ()
    return tuple(dict(row) for row in normalized.rows)


def _attempt_ref_for(attempt_id: str) -> str:
    return f"attempt://mongo/{OPENBB_PROVIDER_ATTEMPTS}/{attempt_id}"


def _normalized_ref_for_selection(normalized_ref: str) -> str | None:
    try:
        collection, document_id = parse_mongo_ref(normalized_ref)
    except ValueError:
        return None
    return f"normalized://mongo/{collection}/{document_id}"


def _selection_warehouse_check_ref(
    *,
    plan: SelectionRunPlan,
    normalized_refs: tuple[str, ...],
    attempt_refs: tuple[str, ...],
) -> str:
    digest = _short_digest(
        "|".join((plan.selection_run_id, plan.trade_date, *sorted(normalized_refs), *sorted(attempt_refs)))
    )
    return f"warehouse-check://selection/{plan.selection_run_id}/{plan.trade_date}/{digest}"


def _short_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _selection_gap_attempt_refs(*, refs: tuple[str, ...], default_ref: str) -> tuple[str, ...]:
    if refs:
        return refs
    return (default_ref,)


def _build_openviking_backend() -> OpenVikingHttpBackend | None:
    try:
        return create_default_backend()
    except Exception:  # noqa: BLE001
        return None


def _verified_attempt_ref(
    *,
    runtime: _SelectionEvidenceRuntime,
    attempt_id: str,
) -> tuple[str | None, ProviderAttempt | None]:
    store = runtime.attempt_store
    if store is None:
        return None, None
    try:
        attempt = store.get(attempt_id)
    except Exception:  # noqa: BLE001
        return None, None
    if attempt is None:
        return None, None
    return _attempt_ref_for(attempt_id), attempt


def _verify_attempt_evidence_chain(
    *,
    runtime: _SelectionEvidenceRuntime,
    attempt: ProviderAttempt | None,
) -> str | None:
    if attempt is None:
        return "attempt_not_found"
    if attempt.status in {
        ProviderStatus.CACHE_HIT,
        ProviderStatus.CACHED_EMPTY,
        ProviderStatus.RATE_LIMITED,
        ProviderStatus.SHARED_RESULT,
    }:
        return None
    if attempt.raw_ref and not _mongo_ref_exists(
        collection=getattr(runtime.raw_store, "collection", None),
        ref=attempt.raw_ref,
        expected_collection=OPENBB_RAW_PAYLOADS,
    ):
        return f"raw_ref_unreadable:{attempt.raw_ref}"
    if attempt.normalized_ref and _verified_normalized_ref_for_selection(runtime=runtime, normalized_ref=attempt.normalized_ref) is None:
        return f"normalized_ref_unreadable:{attempt.normalized_ref}"
    if attempt.source_metadata and attempt.source_metadata.get("http_evidence_expected") is True:
        if not _http_evidence_exists(runtime=runtime, attempt_id=attempt.attempt_id):
            return "http_evidence_missing"
    return None


def _http_evidence_exists(*, runtime: _SelectionEvidenceRuntime, attempt_id: str) -> bool:
    store = runtime.http_evidence_store
    collection = getattr(store, "collection", None) if store is not None else None
    if collection is None:
        return False
    try:
        if collection.find_one({"_id": f"{attempt_id}:http"}) is not None:
            return True
        pattern = "^" + re.escape(f"{attempt_id}:http:")
        return collection.find_one({"_id": {"$regex": pattern}}) is not None
    except Exception:  # noqa: BLE001
        return False


def _mongo_ref_exists(*, collection: Any | None, ref: str, expected_collection: str) -> bool:
    if collection is None:
        return False
    try:
        resolved_collection, document_id = parse_mongo_ref(ref)
    except ValueError:
        return False
    if resolved_collection != expected_collection:
        return False
    try:
        return collection.find_one({"_id": document_id}) is not None
    except Exception:  # noqa: BLE001
        return False


def _verified_normalized_ref_for_selection(
    *,
    runtime: _SelectionEvidenceRuntime,
    normalized_ref: str,
) -> str | None:
    store = runtime.normalized_store
    if store is None:
        return None
    selection_ref = _normalized_ref_for_selection(normalized_ref)
    if selection_ref is None:
        return None
    try:
        store.read(normalized_ref)
    except Exception:  # noqa: BLE001
        return None
    return selection_ref


def _selection_row_drop_quality_gap(
    *,
    runtime: _SelectionEvidenceRuntime,
    selection_run_id: str,
    adapter_id: str,
    normalized_ref: str | None,
    attempt_refs: tuple[str, ...],
) -> DataGapRef | None:
    if not normalized_ref:
        return None
    store = runtime.normalized_store
    if store is None:
        return None
    try:
        normalized = store.read(normalized_ref)
    except Exception:  # noqa: BLE001
        return None
    dropped_count = _coerce_int(normalized.compact_facts.get("dropped_row_count"))
    if dropped_count <= 0:
        return None
    source_count = _coerce_int(normalized.compact_facts.get("source_row_count"))
    valid_count = _coerce_int(normalized.compact_facts.get("valid_row_count"))
    dropped_ratio = _coerce_float(normalized.compact_facts.get("dropped_row_ratio"))
    if dropped_ratio is None:
        dropped_ratio = (float(dropped_count) / float(source_count)) if source_count > 0 else 1.0
    return DataGapRef(
        gap_id=f"{selection_run_id}-{adapter_id}-rows-dropped",
        domain="selection",
        gap_code="selection_batch_rows_dropped",
        severity=DataGapSeverity.WARN,
        attempt_refs=attempt_refs,
        reader_message=(
            "selection batch 丢弃脏行并保留有效行："
            f"dropped_count={dropped_count}, dropped_ratio={dropped_ratio:.4f}, "
            f"source_count={source_count}, valid_count={valid_count}。"
        ),
        source_metadata={
            "selection_candidate_type": normalized.compact_facts.get("selection_candidate_type"),
            "universe_source": normalized.compact_facts.get("universe_source"),
            "quote_source": normalized.compact_facts.get("quote_source"),
            "mixed_source_chain": normalized.compact_facts.get("mixed_source_chain"),
            "partial_chunk_error_count": normalized.compact_facts.get("partial_chunk_error_count"),
            "partial_chunk_errors": normalized.compact_facts.get("partial_chunk_errors"),
            "failed_chunk_symbols_sample": normalized.compact_facts.get("failed_chunk_symbols_sample"),
        },
    )


def _selection_chunk_metadata_from_normalized_ref(
    *,
    runtime: _SelectionEvidenceRuntime,
    normalized_ref: str | None,
) -> Mapping[str, Any] | None:
    if not normalized_ref or runtime.normalized_store is None:
        return None
    try:
        normalized = runtime.normalized_store.read(normalized_ref)
    except Exception:  # noqa: BLE001
        return None
    compact_facts = normalized.compact_facts or {}
    metadata_keys = (
        "selection_candidate_type",
        "universe_source",
        "quote_source",
        "mixed_source_chain",
        "partial_chunk_error_count",
        "partial_chunk_errors",
        "failed_chunk_symbols_sample",
    )
    return {key: compact_facts.get(key) for key in metadata_keys if key in compact_facts}


def _merge_selection_source_metadata(
    *,
    base: Mapping[str, Any] | None,
    runtime: Mapping[str, Any] | None,
) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    if base:
        merged.update(base)
    if runtime:
        merged.update(runtime)
    return merged


def _coerce_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    try:
        text = str(value).strip()
        if not text:
            return 0
        return int(float(text))
    except (TypeError, ValueError):
        return 0


def _coerce_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        text = str(value).strip()
        if not text:
            return None
        return float(text)
    except (TypeError, ValueError):
        return None


def _selection_batch_openviking_uri(selection_run_id: str) -> str:
    return (
        f"viking://resources/workflow/{selection_run_id}/"
        "selection_data_job/selection_batch/evidence/selection-batch-evidence.json"
    )


def _write_openviking_evidence_ref(
    *,
    runtime: _SelectionEvidenceRuntime,
    plan: SelectionRunPlan,
    provider_plan: SelectionProviderBatchPlan,
    attempt_refs: tuple[str, ...],
    normalized_refs: tuple[str, ...],
    status: str,
    reason: str | None,
    provider_source_matrix: Mapping[str, Mapping[str, Any]] | None = None,
) -> str | None:
    backend = runtime.openviking_backend
    if backend is None:
        return None
    uri = _selection_batch_openviking_uri(plan.selection_run_id)
    operations: list[dict[str, object]] = []
    payload = {
        "selection_run_id": plan.selection_run_id,
        "trade_date": plan.trade_date,
        "status": status,
        "reason": reason,
        "provider_batch_plan_ref": provider_plan.plan_id,
        "provider_candidates": list(provider_plan.provider_candidates),
        "attempt_refs": list(attempt_refs),
        "normalized_refs": list(normalized_refs),
        "provider_source_matrix": dict(provider_source_matrix or {}),
        "written_at": utc_now_iso(),
    }
    try:
        verified = backend._write_verified_content(  # noqa: SLF001
            uri=uri,
            content=f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n",
            operations=operations,
            operation_prefix="selection.batch.evidence",
        )
        stat = backend.fetch_stat_by_uri(uri)
        if not stat.ok or not stat.exists or stat.is_dir:
            return None
        content = backend.fetch_content_by_uri(uri)
        content_sha = hashlib.sha256(content).hexdigest()
        if content_sha != str(verified["sha256"]):
            return None
        if len(content) != int(verified["size_bytes"]):
            return None
        if stat.size_bytes is not None and stat.size_bytes != len(content):
            return None
    except OpenVikingAccessError:
        return None
    except Exception:  # noqa: BLE001
        return None
    return uri


def _attach_openviking_ref_or_gap(
    *,
    runtime: _SelectionEvidenceRuntime,
    plan: SelectionRunPlan,
    provider_plan: SelectionProviderBatchPlan,
    attempt_refs: tuple[str, ...],
    normalized_refs: tuple[str, ...],
    data_gaps: list[DataGapRef],
    status: str,
    reason: str | None,
    provider_source_matrix: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[str, ...]:
    openviking_ref = _write_openviking_evidence_ref(
        runtime=runtime,
        plan=plan,
        provider_plan=provider_plan,
        attempt_refs=attempt_refs,
        normalized_refs=normalized_refs,
        status=status,
        reason=reason,
        provider_source_matrix=provider_source_matrix,
    )
    if openviking_ref is None:
        data_gaps.append(
            _blocker_gap(
                gap_id=f"{plan.selection_run_id}-selection-batch-openviking-evidence-unavailable",
                gap_code="selection_batch_openviking_evidence_unavailable",
                attempt_refs=_selection_gap_attempt_refs(
                    refs=attempt_refs,
                    default_ref=provider_plan.lineage_root_ref,
                ),
                reader_message="selection batch OpenViking evidence 不可写或 readback 校验失败。",
            )
        )
        return _selection_gap_attempt_refs(
            refs=attempt_refs,
            default_ref=provider_plan.lineage_root_ref,
        )
    return tuple(_dedup_refs((*attempt_refs, openviking_ref)))


def _dedup_refs(refs: tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for ref in refs:
        value = str(ref).strip()
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return tuple(result)


def _selection_gap_code_for_status(status: ProviderStatus) -> str:
    if status == ProviderStatus.CREDENTIAL_MISSING:
        return "selection_batch_credential_missing"
    if status == ProviderStatus.SKIPPED_NOT_CONFIGURED:
        return "selection_batch_source_not_configured"
    if status == ProviderStatus.EVIDENCE_WRITE_FAILED:
        return "selection_batch_evidence_write_failed"
    if status in {ProviderStatus.EMPTY, ProviderStatus.FIELD_MISSING}:
        return "selection_batch_empty"
    return "selection_batch_remote_error"


def _selection_missing_strategy_required_fields(
    *,
    plan: SelectionRunPlan,
    rows: tuple[Mapping[str, object], ...],
) -> tuple[str, ...]:
    strategy = load_cn_a_selection_v1_strategy(plan.approved_strategy_config_ref)
    if strategy is None:
        return ()
    required_fields = _selection_strategy_required_source_fields(strategy=strategy)
    if not required_fields:
        return ()
    missing_fields = sorted(
        field
        for field in required_fields
        if not all(
            _row_has_required_history(row=row, plan=plan) if field == "history" else row.get(field) is not None
            for row in rows
        )
    )
    return tuple(missing_fields)


def _selection_missing_fail_closed_required_fields(
    *,
    plan: SelectionRunPlan,
    rows: tuple[Mapping[str, object], ...],
) -> tuple[str, ...]:
    strategy = load_cn_a_selection_v1_strategy(plan.approved_strategy_config_ref)
    if strategy is None:
        return ()
    required_fields = _selection_fail_closed_required_source_fields(strategy=strategy)
    if not required_fields:
        return ()
    available_fields: set[str] = set()
    for row in rows:
        for field in required_fields:
            if row.get(field) is not None:
                available_fields.add(field)
    missing_fields = sorted(field for field in required_fields if field not in available_fields)
    return tuple(missing_fields)


def _selection_strategy_required_source_fields(*, strategy: ApprovedSelectionStrategy) -> tuple[str, ...]:
    required: set[str] = set()
    signal_dependencies = _selection_strategy_signal_source_dependencies(strategy=strategy)
    for rule in strategy.strategy_set:
        for field in rule.required_fields:
            required.update(_strategy_source_dependencies(field=field, signal_dependencies=signal_dependencies))
    return tuple(sorted(required))


def _selection_fail_closed_required_source_fields(*, strategy: ApprovedSelectionStrategy) -> tuple[str, ...]:
    required: set[str] = set()
    for rule in strategy.hard_filters:
        required.update(_strategy_source_dependencies(field=rule.field))
    for tie_break in strategy.stable_top20_rule.tie_break_fields:
        required.update(_strategy_source_dependencies(field=tie_break.field))
    return tuple(sorted(required))


def _selection_strategy_signal_source_dependencies(
    *,
    strategy: ApprovedSelectionStrategy,
) -> Mapping[str, tuple[str, ...]]:
    dependencies: dict[str, tuple[str, ...]] = {}
    for rule in strategy.strategy_set:
        signal_fields = tuple(field for field in rule.required_fields if field.startswith("strategy_signal_"))
        if not signal_fields:
            continue
        source_fields: set[str] = set()
        for field in rule.required_fields:
            if field in signal_fields:
                continue
            source_fields.update(_strategy_source_dependencies(field=field))
        for field in signal_fields:
            dependencies[field] = tuple(sorted(source_fields))
    return dependencies


def _strategy_source_dependencies(
    *,
    field: str,
    signal_dependencies: Mapping[str, tuple[str, ...]] | None = None,
) -> tuple[str, ...]:
    if signal_dependencies is not None and field in signal_dependencies:
        return signal_dependencies[field]
    dependencies = _SELECTION_STRATEGY_DERIVED_FIELD_DEPENDENCIES.get(field)
    if dependencies is not None:
        return dependencies
    return (field,)


def _row_has_required_history(*, row: Mapping[str, object], plan: SelectionRunPlan) -> bool:
    history = row.get("history")
    if not isinstance(history, Sequence) or isinstance(history, (str, bytes, bytearray)):
        return False
    required = max(250, int(plan.lookback_trading_days or 0))
    valid = 0
    for item in history:
        if not isinstance(item, Mapping):
            continue
        if all(item.get(field) is not None for field in ("open", "high", "low", "close", "volume")):
            valid += 1
    return valid >= required


def _selection_gap_message_for_result(*, result: Any) -> str:
    if result.status == ProviderStatus.CREDENTIAL_MISSING:
        return "selection batch provider 凭证缺失，按 fail closed 失败。"
    if result.status == ProviderStatus.SKIPPED_NOT_CONFIGURED:
        return "selection batch provider 未配置，按 fail closed 失败。"
    if result.status == ProviderStatus.EVIDENCE_WRITE_FAILED:
        return (
            "selection batch evidence 写入失败，无法形成 raw/http/attempt/normalized 闭环。"
            f" root_cause={result.error_message or result.error_code or 'unknown'}"
        )
    if result.status in {ProviderStatus.EMPTY, ProviderStatus.FIELD_MISSING}:
        return "selection batch 数据为空或字段缺失，按 fail closed 失败。"
    return f"selection batch provider 调用失败：{result.error_message or result.error_code or result.status.value}"


def _selection_partial_gap_message_for_result(*, result: Any) -> str:
    source_metadata = _selection_source_metadata_for_spec(spec=result.spec)
    source_metadata_text = json.dumps(source_metadata, ensure_ascii=False, sort_keys=True)
    if result.status in {ProviderStatus.EMPTY, ProviderStatus.FIELD_MISSING}:
        return (
            "selection batch partial source 已真实执行，但字段未闭合（如 vol_ratio/industry），"
            "仅记录数据缺口并继续后续完整源。"
            f" source_metadata={source_metadata_text}"
        )
    if result.status == ProviderStatus.CREDENTIAL_MISSING:
        return (
            "selection batch partial source 凭证缺失，已记录数据缺口并继续后续完整源。"
            f" source_metadata={source_metadata_text}"
        )
    if result.status == ProviderStatus.SKIPPED_NOT_CONFIGURED:
        return (
            "selection batch partial source 未配置，已记录数据缺口并继续后续完整源。"
            f" source_metadata={source_metadata_text}"
        )
    return (
        "selection batch partial source 执行失败，已保留 attempt/evidence 并继续后续完整源："
        f"{result.error_message or result.error_code or result.status.value}; "
        f"source_metadata={source_metadata_text}"
    )


def _map_selection_row(*, row: Mapping[str, object], source_ref: str) -> Mapping[str, object]:
    mapped: dict[str, object] = {
        "ticker": str(row["ticker"]),
        "company_name": str(row["company_name"]),
        "industry": str(row.get("industry") or ""),
        "open": float(row["open"]),
        "close": float(row["close"]),
        "high": float(row["high"]),
        "low": float(row["low"]),
        "source_ref": source_ref,
    }
    if row.get("amount") is not None:
        mapped["amount"] = float(row["amount"])
    if row.get("volume") is not None:
        mapped["volume"] = float(row["volume"])
    if row.get("vol_ratio") is not None:
        mapped["vol_ratio"] = float(row["vol_ratio"])
    if isinstance(row.get("history"), Sequence) and not isinstance(row.get("history"), (str, bytes, bytearray)):
        mapped["history"] = tuple(item for item in row["history"] if isinstance(item, Mapping))
    if row.get("private_placement_event_date") is not None:
        mapped["private_placement_event_date"] = str(row["private_placement_event_date"]).strip()
    if row.get("private_placement_days_since") is not None:
        mapped["private_placement_days_since"] = float(row["private_placement_days_since"])
    if row.get("private_placement_source_ref") is not None:
        mapped["private_placement_source_ref"] = str(row["private_placement_source_ref"]).strip()
    return mapped


def _blocker_gap(
    *,
    gap_id: str,
    gap_code: str,
    attempt_refs: tuple[str, ...],
    reader_message: str,
    source_metadata: Mapping[str, Any] | None = None,
) -> DataGapRef:
    return DataGapRef(
        gap_id=gap_id,
        domain="selection",
        gap_code=gap_code,
        severity=DataGapSeverity.BLOCKER,
        attempt_refs=attempt_refs,
        reader_message=reader_message,
        source_metadata=dict(source_metadata) if source_metadata else None,
    )


def _warn_gap(
    *,
    gap_id: str,
    gap_code: str,
    attempt_refs: tuple[str, ...],
    reader_message: str,
    source_metadata: Mapping[str, Any] | None = None,
) -> DataGapRef:
    return DataGapRef(
        gap_id=gap_id,
        domain="selection",
        gap_code=gap_code,
        severity=DataGapSeverity.WARN,
        attempt_refs=attempt_refs,
        reader_message=reader_message,
        source_metadata=dict(source_metadata) if source_metadata else None,
    )


def _downgrade_recovered_provider_source_gaps(*, data_gaps: list[DataGapRef]) -> list[DataGapRef]:
    downgraded: list[DataGapRef] = []
    for gap in data_gaps:
        if gap.severity != DataGapSeverity.BLOCKER:
            downgraded.append(gap)
            continue
        if gap.gap_code not in _RECOVERABLE_SELECTION_PROVIDER_GAP_CODES:
            downgraded.append(gap)
            continue
        downgraded.append(replace(gap, severity=DataGapSeverity.WARN))
    return downgraded


def _selection_source_metadata_for_spec(*, spec: ProviderCallSpec) -> Mapping[str, Any]:
    base = {
        "selection_candidate_type": "unknown",
        "universe_source": "unknown",
        "quote_source": spec.provider,
        "mixed_source_chain": [spec.provider],
    }
    if spec.endpoint == "stock_zh_a_spot_mootdx_batch":
        return {
            "selection_candidate_type": "partial_batch_candidate",
            "universe_source": "mootdx.stock_all",
            "quote_source": "mootdx.quotes",
            "mixed_source_chain": ["mootdx.stock_all", "mootdx.quotes"],
        }
    if spec.endpoint == "stock_zh_a_spot_tencent_batch":
        return {
            "selection_candidate_type": "partial_batch_candidate",
            "universe_source": "mootdx.stock_all",
            "quote_source": "tencent.qt_gtimg",
            "mixed_source_chain": ["mootdx.stock_all", "tencent.qt_gtimg"],
        }
    if spec.endpoint == "stock_zh_a_spot_sina_batch":
        return {
            "selection_candidate_type": "partial_batch_candidate",
            "universe_source": "mootdx.stock_all",
            "quote_source": "sina.hq",
            "mixed_source_chain": ["mootdx.stock_all", "sina.hq"],
        }
    if spec.endpoint == "stock_zh_a_daily_baostock_batch":
        return {
            "selection_candidate_type": "partial_batch_candidate",
            "universe_source": "baostock.query_all_stock",
            "quote_source": "baostock.query_all_stock",
            "mixed_source_chain": ["baostock.query_all_stock"],
        }
    if spec.endpoint == "stock_zh_a_spot_em_batch":
        return {
            "selection_candidate_type": "complete_batch_candidate",
            "universe_source": "same_as_quote_source",
            "quote_source": spec.provider,
            "mixed_source_chain": [spec.provider],
        }
    return base


def _smoke_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SEL-13 selection batch smoke (debug evidence only).")
    parser.add_argument("--selection-run-id", required=True)
    parser.add_argument("--trade-date", required=True)
    parser.add_argument("--output-json", default="")
    return parser


def _serialize_selection_data_gap(gap: DataGapRef) -> dict[str, object]:
    return {
        "gap_id": gap.gap_id,
        "domain": gap.domain,
        "gap_code": gap.gap_code,
        "severity": gap.severity.value,
        "attempt_refs": list(gap.attempt_refs),
        "reader_message": gap.reader_message,
        "source_metadata": dict(gap.source_metadata or {}),
    }


def _smoke_cli_run(argv: list[str]) -> int:
    args = _smoke_cli_parser().parse_args(argv)
    provider_plan = build_selection_provider_batch_plan(
        market=SelectionMarket.CN_A,
        profile=SelectionProfile.CN_A,
        trade_date=args.trade_date,
    )
    plan = SelectionRunPlan(
        selection_run_id=args.selection_run_id,
        market=SelectionMarket.CN_A,
        profile=SelectionProfile.CN_A,
        trade_date=args.trade_date,
        lookback_trading_days=provider_plan.lookback_trading_days,
        universe_scope=provider_plan.universe_scope,
        provider_batch_plan_ref=provider_plan.plan_id,
        approved_strategy_config_ref=_CN_A_SELECTION_STRATEGY_CONFIG_REF,
        trigger_source=SelectionTriggerSource.SCHEDULED,
    )
    result = fetch_selection_batch_from_data_gateway(plan)
    has_blocker_gap = any(item.severity == DataGapSeverity.BLOCKER for item in result.data_gaps)
    payload = {
        "selection_run_id": plan.selection_run_id,
        "trade_date": plan.trade_date,
        "attempt_refs": list(result.attempt_refs),
        "normalized_refs": list(result.normalized_refs),
        "rows": len(result.rows),
        "data_gaps": [_serialize_selection_data_gap(item) for item in result.data_gaps],
        "data_gap_codes": [item.gap_code for item in result.data_gaps],
        "data_gap_severities": [item.severity.value for item in result.data_gaps],
    }
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output_json.strip():
        target = Path(args.output_json.strip())
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered, encoding="utf-8")
    print(rendered)
    return 0 if result.rows and not has_blocker_gap else 2


def main(argv: list[str] | None = None) -> int:
    return _smoke_cli_run(argv if argv is not None else sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
