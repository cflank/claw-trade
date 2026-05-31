from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Mapping

from claw_trade.data_gateway.models import (
    DataGap,
    DataGapReason,
    DomainPackResult,
    FreshnessStatus,
    GapSeverity,
    Market,
    PackAuditPayload,
    PackDomain,
    PackRequest,
    PrioritySource,
    ProviderAttempt,
    ProviderCallSpec,
    ProviderResult,
    ProviderStatus,
    Readiness,
    ReadinessStatus,
    RunProviderPlan,
    SourceRole,
    utc_now_iso,
)
from claw_trade.data_gateway.providers.base import ProviderAdapter
from claw_trade.data_gateway.providers.execution import ProviderExecutionEvidenceHelper
from claw_trade.data_gateway.providers.social_source_roles import (
    is_alternative_me_provider,
    is_polymarket_provider,
    is_social_core_role,
    is_social_role_allowed,
    is_social_search_discovery_provider,
)

_GAP_REASON_BY_STATUS: dict[ProviderStatus, DataGapReason] = {
    ProviderStatus.CREDENTIAL_MISSING: DataGapReason.CREDENTIAL_MISSING,
    ProviderStatus.RATE_LIMITED: DataGapReason.RATE_LIMITED,
    ProviderStatus.EMPTY: DataGapReason.EMPTY,
    ProviderStatus.FIELD_MISSING: DataGapReason.FIELD_MISSING,
    ProviderStatus.SCHEMA_INVALID: DataGapReason.SCHEMA_INVALID,
    ProviderStatus.CACHE_ERROR: DataGapReason.CACHE_ERROR,
    ProviderStatus.CACHED_EMPTY: DataGapReason.CACHED_EMPTY,
    ProviderStatus.CACHE_STALE: DataGapReason.STALE_CACHE_UNUSABLE,
    ProviderStatus.EVIDENCE_WRITE_FAILED: DataGapReason.EVIDENCE_WRITE_FAILED,
    ProviderStatus.LICENSE_BLOCKED: DataGapReason.LICENSE_BLOCKED,
    ProviderStatus.SKIPPED_NOT_CONFIGURED: DataGapReason.SOURCE_NOT_CONFIGURED,
    ProviderStatus.REMOTE_ERROR: DataGapReason.PROVIDER_UNAVAILABLE,
}

_STATUS_TEXT: dict[ProviderStatus, str] = {
    ProviderStatus.REMOTE_SUCCESS: "远端获取成功",
    ProviderStatus.WAREHOUSE_HIT: "使用主仓库数据",
    ProviderStatus.CREDENTIAL_MISSING: "缺少接口凭证",
    ProviderStatus.RATE_LIMITED: "接口限流",
    ProviderStatus.EMPTY: "来源返回为空",
    ProviderStatus.SCHEMA_INVALID: "返回结构不符合预期",
    ProviderStatus.REMOTE_ERROR: "远端请求失败",
    ProviderStatus.SKIPPED_NOT_CONFIGURED: "来源未配置",
}

_READINESS_TEXT: dict[ReadinessStatus, str] = {
    ReadinessStatus.READY: "就绪",
    ReadinessStatus.PARTIAL: "部分覆盖",
    ReadinessStatus.INSUFFICIENT: "资料不足",
    ReadinessStatus.BLOCKED: "阻断",
}


def _result_is_usable(result: ProviderResult) -> bool:
    return result.status in {
        ProviderStatus.REMOTE_SUCCESS,
        ProviderStatus.CACHE_HIT,
        ProviderStatus.WAREHOUSE_HIT,
        ProviderStatus.SHARED_RESULT,
    } and result.row_count > 0 and bool(result.normalized_ref)


def _coverage_group_satisfied(*, spec: ProviderCallSpec, previous_results: list[ProviderResult]) -> bool:
    if not spec.coverage_group:
        return False
    if spec.user_preferred or spec.priority_source == PrioritySource.USER_PREFERRED:
        return False
    return any(result.spec.coverage_group == spec.coverage_group and _result_is_usable(result) for result in previous_results)


@dataclass
class SocialPackBuilder:
    openbb_runtime_marker: str = "openbb-runtime"
    openbb_extension_version: str = "claw-trade.social-pack.v1"

    def build(
        self,
        *,
        request: PackRequest,
        run_plan: RunProviderPlan,
        adapters_by_id: Mapping[str, ProviderAdapter],
        provider_execution_helper: ProviderExecutionEvidenceHelper | None = None,
        warehouse_results: tuple[ProviderResult, ...] = (),
        extra_gaps: tuple[DataGap, ...] = (),
    ) -> DomainPackResult:
        if request.domain != PackDomain.SOCIAL:
            raise ValueError("SocialPackBuilder only supports PackDomain.SOCIAL")

        specs = tuple(spec for spec in run_plan.call_specs if spec.domain == PackDomain.SOCIAL)
        attempts: list[ProviderAttempt] = [result.attempt for result in warehouse_results]
        provider_results: list[ProviderResult] = list(warehouse_results)
        data_gaps: list[DataGap] = [gap for gap in run_plan.initial_gaps if gap.domain == PackDomain.SOCIAL]
        data_gaps.extend(extra_gaps)
        if not specs:
            data_gaps.append(
                DataGap(
                    gap_id=f"{request.run_id}:{request.call_id}:social:source_not_configured",
                    domain=PackDomain.SOCIAL,
                    severity=GapSeverity.FAIL,
                    reason=DataGapReason.SOURCE_NOT_CONFIGURED,
                    field_path="social",
                    provider_candidates=(),
                    attempt_ids=(),
                    root_cause="no social call specs in run plan",
                    next_action="configure social providers",
                )
            )

        for spec in specs:
            if _coverage_group_satisfied(spec=spec, previous_results=provider_results):
                continue
            role_error = self._validate_social_source_role(spec=spec)
            if role_error is not None:
                attempt = self._attempt(
                    request=request,
                    spec=spec,
                    status=ProviderStatus.SCHEMA_INVALID,
                    error_message=role_error,
                    started_at=utc_now_iso(),
                    finished_at=utc_now_iso(),
                )
                attempts.append(attempt)
                data_gaps.append(self._gap_from_attempt(request=request, attempt=attempt))
                continue

            adapter = adapters_by_id.get(spec.adapter_id)
            if adapter is None:
                attempt = self._attempt(
                    request=request,
                    spec=spec,
                    status=ProviderStatus.SKIPPED_NOT_CONFIGURED,
                    error_message=f"adapter not configured: {spec.adapter_id}",
                    started_at=utc_now_iso(),
                    finished_at=utc_now_iso(),
                )
                attempts.append(attempt)
                data_gaps.append(self._gap_from_attempt(request=request, attempt=attempt))
                continue

            credential = adapter.validate_credentials()
            if credential.missing:
                attempt = self._attempt(
                    request=request,
                    spec=spec,
                    status=ProviderStatus.CREDENTIAL_MISSING,
                    error_message=credential.root_cause or "missing credentials",
                    started_at=utc_now_iso(),
                    finished_at=utc_now_iso(),
                    adapter_kind=adapter.adapter_kind,
                    provider_id=adapter.provider_id,
                    provider_kind=adapter.provider_kind,
                )
                attempts.append(attempt)
                data_gaps.append(self._gap_from_attempt(request=request, attempt=attempt))
                continue

            started_at = utc_now_iso()
            executor = provider_execution_helper
            if executor is not None and getattr(executor, "gate_controlled", False):
                result = executor.execute(
                    request=request,
                    spec=spec,
                    adapter=adapter,
                    started_at=started_at,
                )
            else:
                finished_at = utc_now_iso()
                attempt = self._attempt(
                    request=request,
                    spec=spec,
                    status=ProviderStatus.EVIDENCE_WRITE_FAILED,
                    error_message="provider call gate is not configured; pack runtime remote calls must use run_provider_call_gate",
                    started_at=started_at,
                    finished_at=finished_at,
                    adapter_kind=adapter.adapter_kind,
                    provider_id=adapter.provider_id,
                    provider_kind=adapter.provider_kind,
                )
                attempts.append(attempt)
                data_gaps.append(self._gap_from_attempt(request=request, attempt=attempt))
                continue

            attempts.append(result.attempt)
            provider_results.append(result)
            if not _result_is_usable(result):
                data_gaps.append(self._gap_from_attempt(request=request, attempt=result.attempt))

        compact_facts = self._build_compact_facts(provider_results)
        readiness = self._build_readiness(provider_results=provider_results, gaps=data_gaps)
        brief = self._build_reader_brief(
            request=request,
            compact_facts=compact_facts,
            readiness=readiness,
            attempts=attempts,
            gaps=data_gaps,
        )
        payload_hash = hashlib.sha256(
            json.dumps(compact_facts, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        generated_at = utc_now_iso()
        audit = PackAuditPayload(
            request=request,
            openbb_runtime_marker=self.openbb_runtime_marker,
            openbb_extension_version=self.openbb_extension_version,
            run_provider_plan_id=run_plan.run_id,
            call_specs=specs,
            attempts=tuple(attempts),
            cache_receipts=(),
            data_gaps=tuple(data_gaps),
            conflicts=(),
            readiness=readiness,
            chart_assets=(),
            raw_refs=tuple(ref for ref in (attempt.raw_ref for attempt in attempts) if ref),
            normalized_refs=tuple(ref for ref in (attempt.normalized_ref for attempt in attempts) if ref),
            normalized_bundle_ref=None,
            payload_hash=f"sha256:{payload_hash}",
            generated_at=generated_at,
        )
        return DomainPackResult(
            request=request,
            reader_brief_md=brief,
            compact_facts=compact_facts,
            attempts=tuple(attempts),
            cache_receipts=(),
            data_gaps=tuple(data_gaps),
            conflicts=(),
            readiness=readiness,
            chart_assets=(),
            raw_refs=audit.raw_refs,
            normalized_refs=audit.normalized_refs,
            normalized_bundle_ref=None,
            audit_ref=f"ov://data_gateway/{request.run_id}/social/{request.call_id}",
            audit_payload_hash=f"sha256:{payload_hash}",
            audit_payload=audit,
        )

    def _validate_social_source_role(self, *, spec: ProviderCallSpec) -> str | None:
        if not is_social_role_allowed(spec.source_role):
            return "社交来源角色不在允许范围内。"
        provider_hint = f"{spec.provider}:{spec.adapter_id}:{spec.endpoint}"
        if is_social_search_discovery_provider(provider_hint) and spec.source_role != SourceRole.SEARCH_DISCOVERY:
            return "搜索类来源只能作为发现线索，不能作为社交事实或聚合情绪。"
        if is_alternative_me_provider(provider_hint) and spec.source_role != SourceRole.SOCIAL_AGGREGATE_METRIC:
            return "Alternative.me 只能作为市场级情绪来源。"
        if is_polymarket_provider(provider_hint) and spec.source_role != SourceRole.EVENT_EXPECTATION:
            return "Polymarket 只能作为事件预期来源。"
        return None

    def _attempt(
        self,
        *,
        request: PackRequest,
        spec: ProviderCallSpec,
        status: ProviderStatus,
        started_at: str,
        finished_at: str,
        error_message: str | None = None,
        row_count: int | None = None,
        raw_ref: str | None = None,
        normalized_ref: str | None = None,
        adapter_kind: str | None = None,
        provider_id: str | None = None,
        provider_kind=None,
    ) -> ProviderAttempt:
        return ProviderAttempt(
            attempt_id=f"{request.run_id}:{request.call_id}:{spec.call_key}:{status.value}",
            run_id=request.run_id,
            call_id=request.call_id,
            worker_id=request.worker_id,
            pack=request.domain.value,
            provider=provider_id or spec.provider,
            adapter_id=spec.adapter_id,
            adapter_kind=adapter_kind or spec.provider_kind.value,
            provider_kind=provider_kind or spec.provider_kind,
            provider_config_version=spec.provider_config_version,
            endpoint=spec.endpoint,
            source_role=spec.source_role,
            started_at=started_at,
            finished_at=finished_at,
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
            row_count=row_count,
            raw_ref=raw_ref,
            normalized_ref=normalized_ref,
            error_code=None if status == ProviderStatus.REMOTE_SUCCESS else status.value,
            error_message=error_message,
            schema_id=spec.expected_schema_id,
            license_note="ok",
        )

    def _gap_from_attempt(self, *, request: PackRequest, attempt: ProviderAttempt) -> DataGap:
        reason = _GAP_REASON_BY_STATUS.get(attempt.status, DataGapReason.PROVIDER_UNAVAILABLE)
        return DataGap(
            gap_id=f"{attempt.attempt_id}:gap",
            domain=request.domain,
            severity=GapSeverity.FAIL,
            reason=reason,
            field_path=attempt.endpoint,
            provider_candidates=(attempt.provider,),
            attempt_ids=(attempt.attempt_id,),
            root_cause=_gap_root_cause(attempt),
            next_action="检查 provider key、限流、返回格式与 source_role 边界",
        )

    def _build_compact_facts(self, provider_results: list[ProviderResult]) -> dict[str, list[dict[str, str]]]:
        social_samples: list[dict[str, str]] = []
        aggregate_metrics: list[dict[str, str]] = []
        market_level_sentiment: list[dict[str, str]] = []
        event_expectation: list[dict[str, str]] = []
        search_discovery: list[dict[str, str]] = []
        for result in provider_results:
            if not _result_is_usable(result):
                continue
            provider_hint = f"{result.spec.provider}:{result.spec.adapter_id}:{result.spec.endpoint}"
            for row in result.rows:
                entry = {
                    "title": str(row.get("title") or row.get("headline") or row.get("summary") or "未命名条目"),
                    "url": str(row.get("url") or row.get("link") or "").strip(),
                    "source_role": result.source_role.value,
                }
                if result.source_role == SourceRole.SEARCH_DISCOVERY:
                    search_discovery.append(entry)
                elif result.source_role == SourceRole.EVENT_EXPECTATION:
                    event_expectation.append(entry)
                elif is_alternative_me_provider(provider_hint):
                    market_level_sentiment.append(entry)
                elif result.source_role == SourceRole.SOCIAL_ORIGINAL_SAMPLE:
                    social_samples.append(entry)
                elif result.source_role == SourceRole.SOCIAL_AGGREGATE_METRIC:
                    aggregate_metrics.append(entry)
        return {
            "social_original_samples": social_samples,
            "social_aggregate_metrics": aggregate_metrics,
            "market_level_sentiment": market_level_sentiment,
            "event_expectation": event_expectation,
            "search_discovery": search_discovery,
        }

    def _build_readiness(self, *, provider_results: list[ProviderResult], gaps: list[DataGap]) -> Readiness:
        core_success_count = sum(
            1
            for result in provider_results
            if _result_is_usable(result) and is_social_core_role(result.source_role)
        )
        coverage = {
            "social_core_count": str(core_success_count),
            "gap_count": str(len(gaps)),
        }
        if core_success_count <= 0:
            status = ReadinessStatus.INSUFFICIENT
            root_cause = "缺少社交原始样本或聚合指标。"
        elif gaps:
            status = ReadinessStatus.PARTIAL
            root_cause = "部分来源失败，社交覆盖不完整。"
        else:
            status = ReadinessStatus.READY
            root_cause = None
        return Readiness(
            status=status,
            coverage=coverage,
            required_domains=("social",),
            missing_domains=() if status != ReadinessStatus.INSUFFICIENT else ("social",),
            blocking_gap_ids=tuple(gap.gap_id for gap in gaps),
            non_blocking_gap_ids=(),
            root_cause=root_cause,
        )

    def _build_reader_brief(
        self,
        *,
        request: PackRequest,
        compact_facts: Mapping[str, list[dict[str, str]]],
        readiness: Readiness,
        attempts: list[ProviderAttempt],
        gaps: list[DataGap],
    ) -> str:
        lines = [
            f"## 社交资料包（{request.market.value}）",
            f"可用性：{_READINESS_TEXT.get(readiness.status, readiness.status.value)}",
            "说明：本资料包只呈现来源事实与缺口，不包含投资判断。",
            (
                "原始社交样本："
                f"{len(compact_facts.get('social_original_samples', []))} 条。"
            ),
            (
                "聚合情绪指标："
                f"{len(compact_facts.get('social_aggregate_metrics', []))} 条。"
            ),
            (
                "Alternative.me 市场级情绪："
                f"{len(compact_facts.get('market_level_sentiment', []))} 条，不代表单标的社交共识。"
            ),
            (
                "Polymarket 事件预期："
                f"{len(compact_facts.get('event_expectation', []))} 条，不代表新闻事实或社交共识。"
            ),
            (
                "搜索发现："
                f"{len(compact_facts.get('search_discovery', []))} 条，仅作公开讨论线索"
                f"{_crypto_social_discovery_boundary(request)}。"
            ),
        ]
        event_expectations = compact_facts.get("event_expectation", [])
        if event_expectations:
            lines.append("Polymarket 事件样本：" + "；".join(item["title"] for item in event_expectations[:3]) + "。")
        if attempts:
            lines.append(
                "来源尝试："
                + "；".join(f"{a.provider}/{a.endpoint}：{_STATUS_TEXT.get(a.status, a.status.value)}" for a in attempts[:8])
                + "。"
            )
        if gaps:
            lines.append("资料缺口：" + "；".join(gap.root_cause for gap in gaps[:6]) + "。")
        return "\n".join(lines)


def _gap_root_cause(attempt: ProviderAttempt) -> str:
    if attempt.error_message and attempt.error_message != attempt.status.value:
        return attempt.error_message
    return _STATUS_TEXT.get(attempt.status, attempt.status.value)


def _crypto_social_discovery_boundary(request: PackRequest) -> str:
    if request.market == Market.CRYPTO:
        return "，不能作为社交共识、ETF 资金流、机构持仓/买入、链上大户行为或已验证事实"
    return ""
