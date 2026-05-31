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
class HotMoneyPackBuilder:
    openbb_runtime_marker: str = "openbb-runtime"
    openbb_extension_version: str = "claw-trade.hot-money-pack.v1"

    def build(
        self,
        *,
        request: PackRequest,
        run_plan: RunProviderPlan,
        adapters_by_id: Mapping[str, ProviderAdapter],
        provider_execution_helper: ProviderExecutionEvidenceHelper | None = None,
    ) -> DomainPackResult:
        if request.domain != PackDomain.HOT_MONEY:
            raise ValueError("HotMoneyPackBuilder only supports PackDomain.HOT_MONEY")
        specs = tuple(spec for spec in run_plan.call_specs if spec.domain == PackDomain.HOT_MONEY)
        attempts: list[ProviderAttempt] = []
        provider_results: list[ProviderResult] = []
        data_gaps: list[DataGap] = [gap for gap in run_plan.initial_gaps if gap.domain == PackDomain.HOT_MONEY]
        if not specs:
            data_gaps.append(
                DataGap(
                    gap_id=f"{request.run_id}:{request.call_id}:hot_money:source_not_configured",
                    domain=PackDomain.HOT_MONEY,
                    severity=GapSeverity.FAIL,
                    reason=DataGapReason.SOURCE_NOT_CONFIGURED,
                    field_path="hot_money",
                    provider_candidates=(),
                    attempt_ids=(),
                    root_cause="no hot_money call specs in run plan",
                    next_action="configure hot_money providers",
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

        facts = normalize_hot_money_results(provider_results)
        chart_assets = self._build_chart_assets(request=request, facts=facts)
        readiness = self._build_readiness(provider_results=provider_results, specs=specs, gaps=data_gaps)
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
            audit_ref=f"ov://data_gateway/{request.run_id}/hot_money/{request.call_id}",
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
            domain=PackDomain.HOT_MONEY,
            severity=severity,
            reason=reason,
            field_path=attempt.endpoint,
            provider_candidates=(attempt.provider,),
            attempt_ids=(attempt.attempt_id,),
            root_cause=attempt.error_message or attempt.status.value,
            next_action="retry hot_money source and keep explicit gap if still unavailable",
        )

    def _build_chart_assets(
        self,
        *,
        request: PackRequest,
        facts: Mapping[str, Any],
    ) -> tuple[ChartAsset, ...]:
        dated_points = int(facts.get("dated_amount_points", 0))
        chart_ref = str(facts.get("fund_flow_chart_ref") or "").strip() or None
        if dated_points >= 2 and chart_ref:
            return (
                ChartAsset(
                    chart_id=f"{request.run_id}:{request.call_id}:hot_money_fund_flow",
                    title="资金流趋势",
                    kind="timeseries",
                    image_ref=chart_ref,
                    data_ref=None,
                    status=ReadinessStatus.READY,
                    root_cause=None,
                ),
            )
        if dated_points >= 2:
            root_cause = "资金流数据满足绘图阈值，但缺少图表资产引用"
        elif dated_points == 1:
            root_cause = "只有单日资金流，无法形成趋势图"
        else:
            root_cause = "缺少带日期和金额的资金流数据，无法生成图表"
        return (
            ChartAsset(
                chart_id=f"{request.run_id}:{request.call_id}:hot_money_fund_flow",
                title="资金流趋势",
                kind="timeseries",
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
        specs: tuple[ProviderCallSpec, ...],
        gaps: list[DataGap],
    ) -> Readiness:
        success_groups = {
            item.spec.coverage_group
            for item in provider_results
            if item.status == ProviderStatus.REMOTE_SUCCESS and item.row_count > 0
        }
        required_groups = tuple(
            sorted(
                {
                    spec.coverage_group or spec.endpoint
                    for spec in specs
                    if spec.required
                }
            )
        )
        missing_required = tuple(group for group in required_groups if group not in success_groups)
        blocking = tuple(gap.gap_id for gap in gaps if gap.severity == GapSeverity.FAIL)
        non_blocking = tuple(gap.gap_id for gap in gaps if gap.severity != GapSeverity.FAIL)
        if success_groups and not missing_required and not blocking:
            status = ReadinessStatus.READY
        elif success_groups:
            status = ReadinessStatus.PARTIAL
        elif blocking:
            status = ReadinessStatus.BLOCKED
        else:
            status = ReadinessStatus.INSUFFICIENT
        coverage = {f"group:{group}": ("ok" if group in success_groups else "missing") for group in required_groups}
        coverage["events:hot_money"] = str(
            sum(item.row_count for item in provider_results if item.status == ProviderStatus.REMOTE_SUCCESS)
        )
        return Readiness(
            status=status,
            coverage=coverage,
            required_domains=required_groups,
            missing_domains=missing_required,
            blocking_gap_ids=blocking,
            non_blocking_gap_ids=non_blocking,
            root_cause="required hot_money group missing" if missing_required else None,
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
            "## 数据资料包：游资与资金（A股）",
            f"- 标的：{request.ticker}（{request.company_name}）",
            f"- 覆盖结论：{_READINESS_TEXT.get(readiness.status, readiness.status.value)}",
            "- 事实边界：资金流、龙虎榜、北向和板块热度是交易事实或热度指标，不等于确定买卖意图。",
            "",
            "### 资金与龙虎榜事实",
        ]
        records = list(facts.get("records") or ())
        if records:
            for row in records[:10]:
                lines.append(
                    f"- {row.get('coverage_group')} | {row.get('as_of') or '日期缺失'} | {row.get('name')} | {row.get('amount')} {row.get('unit')}"
                )
        else:
            lines.append("- 当前无可核验资金流或龙虎榜记录。")
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


def normalize_hot_money_results(results: list[ProviderResult]) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    dated_amount_points = 0
    for result in results:
        if result.status != ProviderStatus.REMOTE_SUCCESS:
            continue
        for row in result.rows:
            amount = row.get("amount")
            as_of = row.get("as_of")
            unit = row.get("unit") or ("index" if result.source_role == SourceRole.SOCIAL_AGGREGATE_METRIC else "CNY")
            payload = {
                "coverage_group": result.spec.coverage_group,
                "name": row.get("name") or result.spec.endpoint,
                "amount": amount,
                "unit": unit,
                "as_of": as_of,
                "source": row.get("source") or result.spec.provider,
            }
            records.append(payload)
            if str(as_of or "").strip() and str(amount or "").strip():
                dated_amount_points += 1
    return {
        "records": records,
        "record_count": len(records),
        "dated_amount_points": dated_amount_points,
        "fund_flow_chart_ref": None,
    }
