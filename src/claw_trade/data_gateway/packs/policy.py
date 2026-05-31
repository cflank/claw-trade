from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Mapping

from claw_trade.data_gateway.models import (
    ChartAsset,
    DataGap,
    DataGapReason,
    DomainPackResult,
    FreshnessStatus,
    GapSeverity,
    PackAuditPayload,
    PackDomain,
    PackRequest,
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
    ProviderStatus.CREDENTIAL_MISSING: "缺少接口凭证",
    ProviderStatus.RATE_LIMITED: "接口限流",
    ProviderStatus.EMPTY: "来源返回为空",
    ProviderStatus.SCHEMA_INVALID: "返回结构不符合预期",
    ProviderStatus.REMOTE_ERROR: "远端请求失败",
    ProviderStatus.SKIPPED_NOT_CONFIGURED: "来源未配置",
    ProviderStatus.FIELD_MISSING: "字段缺失",
    ProviderStatus.LICENSE_BLOCKED: "许可边界阻断",
    ProviderStatus.EVIDENCE_WRITE_FAILED: "证据写入失败",
}

_READINESS_TEXT: dict[ReadinessStatus, str] = {
    ReadinessStatus.READY: "就绪",
    ReadinessStatus.PARTIAL: "部分覆盖",
    ReadinessStatus.INSUFFICIENT: "资料不足",
    ReadinessStatus.BLOCKED: "阻断",
}


@dataclass
class PolicyPackBuilder:
    openbb_runtime_marker: str = "openbb-runtime"
    openbb_extension_version: str = "claw-trade.policy-pack.v1"

    def build(
        self,
        *,
        request: PackRequest,
        run_plan: RunProviderPlan,
        adapters_by_id: Mapping[str, ProviderAdapter],
        provider_execution_helper: ProviderExecutionEvidenceHelper | None = None,
    ) -> DomainPackResult:
        if request.domain != PackDomain.POLICY:
            raise ValueError("PolicyPackBuilder only supports PackDomain.POLICY")
        specs = tuple(spec for spec in run_plan.call_specs if spec.domain == PackDomain.POLICY)
        attempts: list[ProviderAttempt] = []
        provider_results: list[ProviderResult] = []
        data_gaps: list[DataGap] = [gap for gap in run_plan.initial_gaps if gap.domain == PackDomain.POLICY]
        if not specs:
            data_gaps.append(
                DataGap(
                    gap_id=f"{request.run_id}:{request.call_id}:policy:source_not_configured",
                    domain=PackDomain.POLICY,
                    severity=GapSeverity.FAIL,
                    reason=DataGapReason.SOURCE_NOT_CONFIGURED,
                    field_path="policy",
                    provider_candidates=(),
                    attempt_ids=(),
                    root_cause="no policy call specs in run plan",
                    next_action="configure policy providers",
                )
            )

        for spec in specs:
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
            if result.status != ProviderStatus.REMOTE_SUCCESS:
                data_gaps.append(self._gap_from_attempt(request=request, attempt=result.attempt))

        facts = normalize_policy_results(provider_results)
        official_gap = self._official_failure_gap(request=request, provider_results=provider_results)
        if official_gap is not None:
            data_gaps.append(official_gap)
        chart_assets = self._build_chart_assets(request=request, facts=facts, provider_results=provider_results)
        readiness = self._build_readiness(provider_results=provider_results, gaps=data_gaps, official_gap=official_gap)
        reader_brief = self._build_reader_brief(
            request=request,
            facts=facts,
            readiness=readiness,
            attempts=attempts,
            gaps=data_gaps,
            chart_assets=chart_assets,
        )

        payload_hash = hashlib.sha256(
            json.dumps(
                {
                    "facts": facts,
                    "attempt_ids": [attempt.attempt_id for attempt in attempts],
                    "gap_ids": [gap.gap_id for gap in data_gaps],
                    "chart_ids": [asset.chart_id for asset in chart_assets],
                },
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ).encode("utf-8")
        ).hexdigest()
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
            chart_assets=chart_assets,
            raw_refs=tuple(ref for ref in (attempt.raw_ref for attempt in attempts) if ref),
            normalized_refs=tuple(ref for ref in (attempt.normalized_ref for attempt in attempts) if ref),
            normalized_bundle_ref=None,
            payload_hash=f"sha256:{payload_hash}",
            generated_at=utc_now_iso(),
        )
        return DomainPackResult(
            request=request,
            reader_brief_md=reader_brief,
            compact_facts=facts,
            attempts=tuple(attempts),
            cache_receipts=(),
            data_gaps=tuple(data_gaps),
            conflicts=(),
            readiness=readiness,
            chart_assets=chart_assets,
            raw_refs=audit.raw_refs,
            normalized_refs=audit.normalized_refs,
            normalized_bundle_ref=None,
            audit_ref=f"ov://data_gateway/{request.run_id}/policy/{request.call_id}",
            audit_payload_hash=f"sha256:{payload_hash}",
            audit_payload=audit,
        )

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
            error_code="provider_error" if status == ProviderStatus.REMOTE_ERROR else status.value,
            error_message=error_message,
            schema_id=spec.expected_schema_id,
            license_note="ok",
        )

    def _gap_from_attempt(self, *, request: PackRequest, attempt: ProviderAttempt) -> DataGap:
        reason = _GAP_REASON_BY_STATUS.get(attempt.status, DataGapReason.PROVIDER_UNAVAILABLE)
        severity = GapSeverity.FAIL if attempt.required else GapSeverity.WARN
        return DataGap(
            gap_id=f"{attempt.attempt_id}:gap",
            domain=PackDomain.POLICY,
            severity=severity,
            reason=reason,
            field_path=attempt.endpoint,
            provider_candidates=(attempt.provider,),
            attempt_ids=(attempt.attempt_id,),
            root_cause=attempt.error_message or attempt.status.value,
            next_action="retry policy source or keep explicit gap in L1 report",
        )

    def _official_failure_gap(
        self,
        *,
        request: PackRequest,
        provider_results: list[ProviderResult],
    ) -> DataGap | None:
        official = [
            item
            for item in provider_results
            if item.spec.coverage_group == "cn_a_policy_official"
        ]
        if not official:
            return DataGap(
                gap_id=f"{request.run_id}:{request.call_id}:policy_official:missing_attempt",
                domain=PackDomain.POLICY,
                severity=GapSeverity.FAIL,
                reason=DataGapReason.SOURCE_NOT_CONFIGURED,
                field_path="cn_a_policy_official",
                provider_candidates=(),
                attempt_ids=(),
                root_cause="cn_a_policy_official has no provider attempt",
                next_action="must keep official failure root cause and do not mark ready",
            )
        if any(item.status == ProviderStatus.REMOTE_SUCCESS and item.row_count > 0 for item in official):
            return None
        root_cause = "; ".join(
            f"{item.spec.adapter_id}:{item.error_message or item.status.value}"
            for item in official
        )
        return DataGap(
            gap_id=f"{request.run_id}:{request.call_id}:policy_official:failed",
            domain=PackDomain.POLICY,
            severity=GapSeverity.FAIL,
            reason=DataGapReason.PROVIDER_UNAVAILABLE,
            field_path="cn_a_policy_official",
            provider_candidates=tuple(item.spec.provider for item in official),
            attempt_ids=tuple(item.attempt.attempt_id for item in official),
            root_cause=root_cause,
            next_action="official_original failure must stay visible and policy pack must not be ready",
        )

    def _build_chart_assets(
        self,
        *,
        request: PackRequest,
        facts: Mapping[str, Any],
        provider_results: list[ProviderResult],
    ) -> tuple[ChartAsset, ...]:
        del provider_results
        dated_events = int(facts.get("dated_fact_events", 0))
        chart_ref = str(facts.get("policy_timeline_chart_ref") or "").strip() or None
        if dated_events >= 2 and chart_ref:
            return (
                ChartAsset(
                    chart_id=f"{request.run_id}:{request.call_id}:policy_timeline",
                    title="政策事件时间线",
                    kind="timeline",
                    image_ref=chart_ref,
                    data_ref=None,
                    status=ReadinessStatus.READY,
                    root_cause=None,
                ),
            )
        if dated_events >= 2:
            root_cause = "政策事实事件达到绘图阈值，但未提供图表资产引用"
        elif dated_events == 1:
            root_cause = "仅 1 条可核验政策事实事件，不满足时间线图最小阈值"
        else:
            root_cause = "缺少可核验且带日期的政策事实事件，无法生成政策图表"
        return (
            ChartAsset(
                chart_id=f"{request.run_id}:{request.call_id}:policy_timeline",
                title="政策事件时间线",
                kind="timeline",
                image_ref=None,
                data_ref=None,
                status=ReadinessStatus.INSUFFICIENT,
                root_cause=root_cause,
            ),
        )

    def _build_readiness(
        self,
        *,
        provider_results: list[ProviderResult],
        gaps: list[DataGap],
        official_gap: DataGap | None,
    ) -> Readiness:
        any_success = any(item.status == ProviderStatus.REMOTE_SUCCESS and item.row_count > 0 for item in provider_results)
        blocking = tuple(gap.gap_id for gap in gaps if gap.severity == GapSeverity.FAIL)
        non_blocking = tuple(gap.gap_id for gap in gaps if gap.severity != GapSeverity.FAIL)
        if official_gap is None and any_success and not blocking:
            status = ReadinessStatus.READY
        elif any_success:
            status = ReadinessStatus.PARTIAL
        elif blocking:
            status = ReadinessStatus.BLOCKED
        else:
            status = ReadinessStatus.INSUFFICIENT
        coverage = {
            "group:cn_a_policy_official": "ok" if official_gap is None else "failed",
            "events:fact": str(sum(item.row_count for item in provider_results if item.source_role != SourceRole.SEARCH_DISCOVERY)),
            "events:discovery": str(sum(item.row_count for item in provider_results if item.source_role == SourceRole.SEARCH_DISCOVERY)),
        }
        return Readiness(
            status=status,
            coverage=coverage,
            required_domains=("cn_a_policy_official",),
            missing_domains=("cn_a_policy_official",) if official_gap is not None else (),
            blocking_gap_ids=blocking,
            non_blocking_gap_ids=non_blocking,
            root_cause=official_gap.root_cause if official_gap is not None else None,
        )

    def _build_reader_brief(
        self,
        *,
        request: PackRequest,
        facts: Mapping[str, Any],
        readiness: Readiness,
        attempts: list[ProviderAttempt],
        gaps: list[DataGap],
        chart_assets: tuple[ChartAsset, ...],
    ) -> str:
        lines = [
            "## 数据资料包：政策（A股）",
            f"- 标的：{request.ticker}（{request.company_name}）",
            f"- 覆盖结论：{_READINESS_TEXT.get(readiness.status, readiness.status.value)}",
            "- 事实边界：官方原文与宏观事实优先；新闻与搜索仅作线索，不替代官方政策事实。",
            "",
            "### 政策事实",
        ]
        fact_rows = list(facts.get("fact_events") or ())
        if fact_rows:
            for row in fact_rows[:8]:
                lines.append(
                    f"- [{row.get('policy_level','policy')}] {row.get('published_at') or '日期缺失'} | {row.get('title')} | {row.get('source')}"
                )
        else:
            lines.append("- 未获取到可核验的政策事实。")
        lines.append("")
        lines.append("### 新闻与发现线索")
        clue_rows = list(facts.get("clue_events") or ())
        if clue_rows:
            for row in clue_rows[:6]:
                lines.append(f"- {row.get('published_at') or '时间未给出'} | {row.get('title')} | {row.get('source')}")
        else:
            lines.append("- 当前无新增线索。")
        lines.append("")
        lines.append("### 图表与来源")
        for chart in chart_assets:
            if chart.status == ReadinessStatus.READY:
                lines.append(f"- {chart.title}：{chart.image_ref}")
            else:
                lines.append(f"- {chart.title}：未就绪（{chart.root_cause}）")
        lines.append("")
        lines.append("### 来源和缺口")
        for attempt in attempts:
            lines.append(
                f"- {attempt.coverage_group} | {attempt.provider} | {_STATUS_TEXT.get(attempt.status, attempt.status.value)}"
            )
        if gaps:
            lines.append("- 缺口：")
            for gap in gaps:
                lines.append(f"  - {gap.field_path} | {gap.reason.value} | {gap.root_cause}")
        else:
            lines.append("- 无新增缺口。")
        return "\n".join(lines).strip()


def normalize_policy_results(results: list[ProviderResult]) -> dict[str, Any]:
    fact_events: list[dict[str, Any]] = []
    clue_events: list[dict[str, Any]] = []
    for result in results:
        if result.status != ProviderStatus.REMOTE_SUCCESS:
            continue
        for row in result.rows:
            payload = {
                "title": row.get("title"),
                "url": row.get("url"),
                "published_at": row.get("published_at"),
                "source": row.get("source"),
                "policy_level": row.get("policy_level") or "industry",
                "source_role": result.source_role.value,
            }
            if result.source_role == SourceRole.SEARCH_DISCOVERY:
                clue_events.append(payload)
            else:
                fact_events.append(payload)
    dated_fact_events = sum(1 for row in fact_events if str(row.get("published_at", "")).strip())
    return {
        "fact_events": fact_events,
        "clue_events": clue_events,
        "fact_event_count": len(fact_events),
        "clue_event_count": len(clue_events),
        "dated_fact_events": dated_fact_events,
        "policy_timeline_chart_ref": None,
    }
