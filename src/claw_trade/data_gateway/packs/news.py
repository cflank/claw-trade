from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
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
from claw_trade.data_gateway.providers.news_source_roles import (
    is_news_discovery_role,
    is_news_fact_role,
    is_news_role_allowed,
    is_news_search_discovery_provider,
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


@dataclass
class NewsPackBuilder:
    openbb_runtime_marker: str = "openbb-runtime"
    openbb_extension_version: str = "claw-trade.news-pack.v1"

    def build(
        self,
        *,
        request: PackRequest,
        run_plan: RunProviderPlan,
        adapters_by_id: Mapping[str, ProviderAdapter],
        provider_execution_helper: ProviderExecutionEvidenceHelper | None = None,
    ) -> DomainPackResult:
        if request.domain != PackDomain.NEWS:
            raise ValueError("NewsPackBuilder only supports PackDomain.NEWS")

        specs = tuple(spec for spec in run_plan.call_specs if spec.domain == PackDomain.NEWS)
        attempts: list[ProviderAttempt] = []
        provider_results: list[ProviderResult] = []
        data_gaps: list[DataGap] = [gap for gap in run_plan.initial_gaps if gap.domain == PackDomain.NEWS]
        if not specs:
            data_gaps.append(
                DataGap(
                    gap_id=f"{request.run_id}:{request.call_id}:news:source_not_configured",
                    domain=PackDomain.NEWS,
                    severity=GapSeverity.FAIL,
                    reason=DataGapReason.SOURCE_NOT_CONFIGURED,
                    field_path="news",
                    provider_candidates=(),
                    attempt_ids=(),
                    root_cause="no news call specs in run plan",
                    next_action="configure news providers",
                )
            )

        for spec in specs:
            role_error = self._validate_news_source_role(spec=spec)
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
            helper = provider_execution_helper
            if helper is not None:
                result = helper.execute(
                    request=request,
                    spec=spec,
                    adapter=adapter,
                    started_at=started_at,
                )
            else:
                try:
                    fetch = adapter.fetch(spec, request)
                    normalized = adapter.normalize(spec, fetch)
                except Exception as exc:
                    finished_at = utc_now_iso()
                    attempt = self._attempt(
                        request=request,
                        spec=spec,
                        status=ProviderStatus.REMOTE_ERROR,
                        error_message=str(exc),
                        started_at=started_at,
                        finished_at=finished_at,
                        adapter_kind=adapter.adapter_kind,
                        provider_id=adapter.provider_id,
                        provider_kind=adapter.provider_kind,
                    )
                    attempts.append(attempt)
                    data_gaps.append(self._gap_from_attempt(request=request, attempt=attempt))
                    continue

                finished_at = utc_now_iso()
                status = normalized.status
                if status == ProviderStatus.REMOTE_SUCCESS and normalized.row_count == 0:
                    status = ProviderStatus.EMPTY
                attempt = self._attempt(
                    request=request,
                    spec=spec,
                    status=status,
                    error_message=normalized.error_message,
                    started_at=started_at,
                    finished_at=finished_at,
                    row_count=normalized.row_count,
                    raw_ref=normalized.source_raw_ref,
                    normalized_ref=None,
                    adapter_kind=adapter.adapter_kind,
                    provider_id=adapter.provider_id,
                    provider_kind=adapter.provider_kind,
                )
                result = ProviderResult(
                    spec=spec,
                    status=status,
                    request_id=fetch.provider_request_id,
                    requested_at=started_at,
                    latency_ms=attempt.latency_ms,
                    source_role=spec.source_role,
                    freshness=FreshnessStatus.FRESH_REMOTE,
                    license_note="ok",
                    raw_ref=attempt.raw_ref,
                    normalized_ref=attempt.normalized_ref,
                    rows=normalized.rows,
                    row_count=normalized.row_count,
                    cache_receipt=None,
                    attempt=attempt,
                    missing_fields=normalized.missing_fields,
                    error_code=normalized.error_code,
                    error_message=attempt.error_message,
                )

            attempts.append(result.attempt)
            provider_results.append(result)
            if result.status != ProviderStatus.REMOTE_SUCCESS:
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
            audit_ref=f"ov://data_gateway/{request.run_id}/news/{request.call_id}",
            audit_payload_hash=f"sha256:{payload_hash}",
            audit_payload=audit,
        )

    def _validate_news_source_role(self, *, spec: ProviderCallSpec) -> str | None:
        if not is_news_role_allowed(spec.source_role):
            return "新闻来源角色不在允许范围内。"
        provider_hint = f"{spec.provider}:{spec.adapter_id}:{spec.endpoint}"
        if is_news_search_discovery_provider(provider_hint) and spec.source_role != SourceRole.SEARCH_DISCOVERY:
            return "搜索类来源只能作为发现线索，不能作为新闻事实。"
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
        facts: list[dict[str, str]] = []
        official: list[dict[str, str]] = []
        macro: list[dict[str, str]] = []
        discovery: list[dict[str, str]] = []
        expectations: list[dict[str, str]] = []
        for result in provider_results:
            if result.status != ProviderStatus.REMOTE_SUCCESS:
                continue
            for row in result.rows:
                title = str(row.get("title") or row.get("headline") or row.get("summary") or "未命名条目").strip()
                url = str(row.get("url") or row.get("link") or "").strip()
                entry = {
                    "title": title,
                    "url": url,
                    "source_role": result.source_role.value,
                }
                if is_news_discovery_role(result.source_role):
                    discovery.append(entry)
                    continue
                if result.source_role == SourceRole.EVENT_EXPECTATION:
                    expectations.append(entry)
                    continue
                if result.source_role == SourceRole.OFFICIAL_ORIGINAL:
                    official.append(entry)
                if result.source_role == SourceRole.MACRO_DATA:
                    macro.append(entry)
                if is_news_fact_role(result.source_role):
                    facts.append(entry)
        return {
            "news_facts": facts,
            "official_original": official,
            "global_macro_news": macro,
            "search_discovery": discovery,
            "event_expectation": expectations,
        }

    def _build_readiness(self, *, provider_results: list[ProviderResult], gaps: list[DataGap]) -> Readiness:
        success_fact_count = sum(
            1
            for result in provider_results
            if result.status == ProviderStatus.REMOTE_SUCCESS and is_news_fact_role(result.source_role)
        )
        coverage = {
            "news_fact_count": str(success_fact_count),
            "gap_count": str(len(gaps)),
        }
        if success_fact_count <= 0:
            status = ReadinessStatus.INSUFFICIENT
            root_cause = "缺少可追溯新闻事实或宏观新闻事实。"
        elif gaps:
            status = ReadinessStatus.PARTIAL
            root_cause = "部分来源失败，新闻覆盖不完整。"
        else:
            status = ReadinessStatus.READY
            root_cause = None
        return Readiness(
            status=status,
            coverage=coverage,
            required_domains=("news",),
            missing_domains=() if status != ReadinessStatus.INSUFFICIENT else ("news",),
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
            f"## 新闻资料包（{request.market.value}）",
            f"可用性：{_READINESS_TEXT.get(readiness.status, readiness.status.value)}",
            "说明：本资料包只呈现事实来源与缺口，不包含投资判断。",
            (
                "新闻事实来源（官方原文或宏观新闻）："
                f"{len(compact_facts.get('news_facts', []))} 条。"
            ),
            (
                "官方原文："
                f"{len(compact_facts.get('official_original', []))} 条。"
            ),
            (
                "全球/宏观新闻："
                f"{len(compact_facts.get('global_macro_news', []))} 条。"
            ),
            (
                "搜索发现："
                f"{len(compact_facts.get('search_discovery', []))} 条，仅作线索，不作为新闻事实"
                f"{_crypto_news_discovery_boundary(request)}。"
            ),
            (
                "事件预期："
                f"{len(compact_facts.get('event_expectation', []))} 条，仅表示市场预期。"
            ),
        ]
        event_expectations = compact_facts.get("event_expectation", [])
        if event_expectations:
            lines.append("事件预期样本：" + "；".join(item["title"] for item in event_expectations[:3]) + "。")
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


def _crypto_news_discovery_boundary(request: PackRequest) -> str:
    if request.market == Market.CRYPTO:
        return "、ETF 资金流、机构持仓/买入、监管事实或事件已发生的证明"
    return ""
