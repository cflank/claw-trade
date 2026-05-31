from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Mapping

from claw_trade.data_gateway.models import (
    AdmissionCheckStatus,
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
    ProviderKind,
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

_EQUITY_REQUIRED_FIELDS: tuple[str, ...] = (
    "valuation.pe",
    "valuation.pb",
    "financial_indicators.roe",
)

_CRYPTO_REQUIRED_FIELDS: tuple[str, ...] = (
    "valuation.market_cap_usd",
    "supply.circulating",
    "supply.total",
    "defi.tvl_usd",
    "defi.fees_24h_usd",
    "defi.revenue_24h_usd",
    "security.score",
    "funding.last_round",
)


def _required_fields(market: Market) -> tuple[str, ...]:
    if market == Market.CRYPTO:
        return _CRYPTO_REQUIRED_FIELDS
    return _EQUITY_REQUIRED_FIELDS


def _required_fields_from_specs(call_specs: tuple[ProviderCallSpec, ...]) -> tuple[str, ...]:
    fields: list[str] = []
    for spec in call_specs:
        params_fields = spec.params.get("required_fields")
        if not isinstance(params_fields, (list, tuple)):
            continue
        fields.extend(str(field) for field in params_fields if str(field).strip())
    return tuple(dict.fromkeys(fields))


def _normalize_conflict_value(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, float):
        return f"{value:.12g}"
    if isinstance(value, (list, tuple, dict)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return str(value)


def _flatten_fields(row: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in row.items():
        path = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, Mapping):
            out.update(_flatten_fields(value, path))
        else:
            out[path] = value
    return out


def _status_to_gap_reason(status: ProviderStatus) -> DataGapReason | None:
    mapping = {
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
    return mapping.get(status)


def _is_blocking_status(status: ProviderStatus) -> bool:
    return status in {
        ProviderStatus.CREDENTIAL_MISSING,
        ProviderStatus.LICENSE_BLOCKED,
        ProviderStatus.EVIDENCE_WRITE_FAILED,
        ProviderStatus.FIELD_MISSING,
        ProviderStatus.SCHEMA_INVALID,
    }


def _result_gap_is_blocking(result: ProviderResult) -> bool:
    if result.status == ProviderStatus.EVIDENCE_WRITE_FAILED:
        return True
    return result.spec.required and _is_blocking_status(result.status)


def _coverage_group_satisfied(*, spec: ProviderCallSpec, previous_results: list[ProviderResult]) -> bool:
    if not spec.coverage_group:
        return False
    if spec.user_preferred or spec.priority_source == PrioritySource.USER_PREFERRED:
        return False
    return any(
        result.spec.coverage_group == spec.coverage_group
        and result.status in {
            ProviderStatus.REMOTE_SUCCESS,
            ProviderStatus.CACHE_HIT,
            ProviderStatus.WAREHOUSE_HIT,
            ProviderStatus.SHARED_RESULT,
        }
        and result.row_count > 0
        and bool(result.normalized_ref)
        for result in previous_results
    )


_STATUS_TEXT: dict[ProviderStatus, str] = {
    ProviderStatus.REMOTE_SUCCESS: "远端获取成功",
    ProviderStatus.CACHE_HIT: "使用有效缓存",
    ProviderStatus.WAREHOUSE_HIT: "使用主仓库数据",
    ProviderStatus.SHARED_RESULT: "复用同次运行结果",
    ProviderStatus.CREDENTIAL_MISSING: "缺少接口凭证",
    ProviderStatus.LICENSE_BLOCKED: "许可边界阻断",
    ProviderStatus.RATE_LIMITED: "接口限流",
    ProviderStatus.EMPTY: "来源返回为空",
    ProviderStatus.FIELD_MISSING: "字段缺失",
    ProviderStatus.SCHEMA_INVALID: "返回结构不符合预期",
    ProviderStatus.CACHE_ERROR: "缓存读取失败",
    ProviderStatus.CACHED_EMPTY: "缓存为空",
    ProviderStatus.CACHE_STALE: "缓存已过期",
    ProviderStatus.EVIDENCE_WRITE_FAILED: "证据写入失败",
    ProviderStatus.SKIPPED_NOT_CONFIGURED: "来源未配置",
    ProviderStatus.REMOTE_ERROR: "远端请求失败",
    ProviderStatus.CACHE_MISS: "缓存未命中",
}

_READINESS_TEXT: dict[ReadinessStatus, str] = {
    ReadinessStatus.READY: "就绪",
    ReadinessStatus.PARTIAL: "部分覆盖",
    ReadinessStatus.INSUFFICIENT: "资料不足",
    ReadinessStatus.BLOCKED: "阻断",
}


@dataclass
class FundamentalPackBuilder:
    openbb_runtime_marker: str = "openbb-v4.7.0"
    openbb_extension_version: str = "claw-trade-fundamental-pack-v1"

    def build(
        self,
        *,
        request: PackRequest,
        run_plan: RunProviderPlan,
        call_specs: tuple[ProviderCallSpec, ...],
        results: tuple[ProviderResult, ...],
        extra_gaps: tuple[DataGap, ...] = (),
    ) -> DomainPackResult:
        flat_fields, raw_refs, normalized_refs, conflicts = self._collect_fields_and_conflicts(results)
        gaps = list(extra_gaps)
        gaps.extend(self._gaps_from_results(request, results))
        gaps.extend(self._gaps_from_required_fields(request, call_specs, flat_fields, results))
        readiness = self._compute_readiness(gaps, results)
        brief = self._render_reader_brief(request, readiness, flat_fields, gaps, conflicts, results)

        payload_seed = {
            "run_id": request.run_id,
            "call_id": request.call_id,
            "market": request.market.value,
            "domain": request.domain.value,
            "attempt_ids": [result.attempt.attempt_id for result in results],
            "gap_ids": [gap.gap_id for gap in gaps],
            "conflict_ids": [conflict.conflict_id for conflict in conflicts],
            "raw_refs": list(raw_refs),
            "normalized_refs": list(normalized_refs),
        }
        payload_hash = "sha256:" + hashlib.sha256(
            json.dumps(payload_seed, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        audit_ref = f"audit://{request.run_id}/{request.call_id}/fundamental"
        audit_payload = PackAuditPayload(
            request=request,
            openbb_runtime_marker=self.openbb_runtime_marker,
            openbb_extension_version=self.openbb_extension_version,
            run_provider_plan_id=run_plan.run_id,
            call_specs=call_specs,
            attempts=tuple(result.attempt for result in results),
            cache_receipts=tuple(
                receipt
                for result in results
                if (receipt := result.cache_receipt) is not None
            ),
            data_gaps=tuple(gaps),
            conflicts=conflicts,
            readiness=readiness,
            chart_assets=(),
            raw_refs=raw_refs,
            normalized_refs=normalized_refs,
            normalized_bundle_ref=None,
            payload_hash=payload_hash,
            generated_at=utc_now_iso(),
        )

        return DomainPackResult(
            request=request,
            reader_brief_md=brief,
            compact_facts=flat_fields,
            attempts=tuple(result.attempt for result in results),
            cache_receipts=tuple(),
            data_gaps=tuple(gaps),
            conflicts=conflicts,
            readiness=readiness,
            chart_assets=(),
            raw_refs=raw_refs,
            normalized_refs=normalized_refs,
            normalized_bundle_ref=None,
            audit_ref=audit_ref,
            audit_payload_hash=payload_hash,
            audit_payload=audit_payload,
        )

    def _collect_fields_and_conflicts(
        self,
        results: tuple[ProviderResult, ...],
    ) -> tuple[dict[str, Any], tuple[str, ...], tuple[str, ...], tuple]:
        field_meta: dict[str, tuple[Any, SourceRole, str]] = {}
        conflicts = []
        raw_refs: list[str] = []
        normalized_refs: list[str] = []

        for result in results:
            if result.raw_ref:
                raw_refs.append(result.raw_ref)
            if result.normalized_ref:
                normalized_refs.append(result.normalized_ref)
            if result.status not in {
                ProviderStatus.REMOTE_SUCCESS,
                ProviderStatus.CACHE_HIT,
                ProviderStatus.WAREHOUSE_HIT,
                ProviderStatus.SHARED_RESULT,
            }:
                continue
            for key, value in _flatten_fields(result.rows[0]).items() if result.rows else ():
                existing = field_meta.get(key)
                if existing is None:
                    field_meta[key] = (value, result.source_role, result.attempt.attempt_id)
                    continue
                existing_value, existing_role, existing_attempt = existing
                value_text = _normalize_conflict_value(value)
                existing_text = _normalize_conflict_value(existing_value)
                if value_text != existing_text:
                    conflicts.append(
                        {
                            "field_path": key,
                            "values": (existing_text, value_text),
                            "provider_refs": (existing_attempt, result.attempt.attempt_id),
                        }
                    )
                    if existing_role != SourceRole.OFFICIAL_ORIGINAL and result.source_role == SourceRole.OFFICIAL_ORIGINAL:
                        field_meta[key] = (value, result.source_role, result.attempt.attempt_id)

        from claw_trade.data_gateway.models import Conflict

        resolved_conflicts = tuple(
            Conflict(
                conflict_id=f"conflict:{item['field_path']}:{idx}",
                field_path=item["field_path"],
                values=item["values"],
                provider_refs=item["provider_refs"],
                resolution="official_original_prioritized",
                confidence="medium",
            )
            for idx, item in enumerate(conflicts, start=1)
        )
        compact_facts = {field_path: meta[0] for field_path, meta in field_meta.items()}
        return compact_facts, tuple(raw_refs), tuple(normalized_refs), resolved_conflicts

    def _gaps_from_results(self, request: PackRequest, results: tuple[ProviderResult, ...]) -> tuple[DataGap, ...]:
        gaps: list[DataGap] = []
        for result in results:
            reason = _status_to_gap_reason(result.status)
            if reason is None:
                continue
            severity = GapSeverity.FAIL if _result_gap_is_blocking(result) else GapSeverity.WARN
            gaps.append(
                DataGap(
                    gap_id=f"{request.run_id}:{request.call_id}:{result.attempt.attempt_id}:{result.status.value}",
                    domain=PackDomain.FUNDAMENTAL,
                    severity=severity,
                    reason=reason,
                    field_path=result.spec.endpoint,
                    provider_candidates=(result.spec.provider,),
                    attempt_ids=(result.attempt.attempt_id,),
                    root_cause=result.error_message or result.status.value,
                    next_action="retry provider or add alternative official/fundamental source",
                )
            )
        return tuple(gaps)

    def _gaps_from_required_fields(
        self,
        request: PackRequest,
        call_specs: tuple[ProviderCallSpec, ...],
        flat_fields: Mapping[str, Any],
        results: tuple[ProviderResult, ...],
    ) -> tuple[DataGap, ...]:
        expected = _required_fields_from_specs(call_specs) or _required_fields(request.market)
        providers = tuple(sorted({spec.provider for spec in call_specs}))
        attempt_ids = tuple(result.attempt.attempt_id for result in results)
        gaps: list[DataGap] = []
        for field in expected:
            if field in flat_fields and flat_fields[field] not in (None, ""):
                continue
            gaps.append(
                DataGap(
                    gap_id=f"{request.run_id}:{request.call_id}:field_missing:{field}",
                    domain=PackDomain.FUNDAMENTAL,
                    severity=GapSeverity.FAIL,
                    reason=DataGapReason.FIELD_MISSING,
                    field_path=field,
                    provider_candidates=providers,
                    attempt_ids=attempt_ids,
                    root_cause=f"{field} missing from normalized fundamental fields",
                    next_action="collect field from approved provider and keep source refs",
                )
            )
        return tuple(gaps)

    def _compute_readiness(self, gaps: list[DataGap], results: tuple[ProviderResult, ...]) -> Readiness:
        has_blocking = any(gap.severity == GapSeverity.FAIL for gap in gaps)
        any_success = any(
            result.status in {ProviderStatus.REMOTE_SUCCESS, ProviderStatus.CACHE_HIT, ProviderStatus.SHARED_RESULT}
            or result.status == ProviderStatus.WAREHOUSE_HIT
            for result in results
        )
        has_blocked_result = any(
            result.spec.required
            and result.status in {ProviderStatus.CREDENTIAL_MISSING, ProviderStatus.LICENSE_BLOCKED}
            for result in results
        )
        if has_blocked_result:
            status = ReadinessStatus.BLOCKED
        elif has_blocking and not any_success:
            status = ReadinessStatus.INSUFFICIENT
        elif has_blocking or any(gap.severity != GapSeverity.FAIL for gap in gaps):
            status = ReadinessStatus.PARTIAL
        else:
            status = ReadinessStatus.READY

        coverage = {"fundamental": status.value}
        missing_domains = ("fundamental",) if status in {ReadinessStatus.INSUFFICIENT, ReadinessStatus.BLOCKED} else ()
        blocking_gap_ids = tuple(gap.gap_id for gap in gaps if gap.severity == GapSeverity.FAIL)
        non_blocking_gap_ids = tuple(gap.gap_id for gap in gaps if gap.severity != GapSeverity.FAIL)
        return Readiness(
            status=status,
            coverage=coverage,
            required_domains=("fundamental",),
            missing_domains=missing_domains,
            blocking_gap_ids=blocking_gap_ids,
            non_blocking_gap_ids=non_blocking_gap_ids,
            root_cause=gaps[0].root_cause if gaps else None,
        )

    def _render_reader_brief(
        self,
        request: PackRequest,
        readiness: Readiness,
        flat_fields: Mapping[str, Any],
        gaps: list[DataGap],
        conflicts: tuple,
        results: tuple[ProviderResult, ...],
    ) -> str:
        lines = [
            f"# 基本面资料包（{request.market.value} / {request.ticker}）",
            f"资料状态：{_READINESS_TEXT.get(readiness.status, readiness.status.value)}。",
        ]
        lines.append("本资料包仅呈现事实材料、资料缺口与来源状态，不提供投资建议或交易结论。")
        lines.append("")
        lines.append("## 核心字段")
        if flat_fields:
            for key in sorted(flat_fields.keys()):
                value = flat_fields[key]
                if isinstance(value, float):
                    show = f"{value:.6g}"
                else:
                    show = str(value)
                lines.append(f"- {key}: {show}")
        else:
            lines.append("- 当前未得到可用的基本面核心字段。")

        official_refs = []
        for result in results:
            if result.source_role == SourceRole.OFFICIAL_ORIGINAL and result.raw_ref:
                official_refs.append((result.spec.provider, result.spec.endpoint))
        lines.append("")
        lines.append("## 官方原文来源")
        if official_refs:
            for provider, endpoint in official_refs:
                lines.append(f"- {provider}.{endpoint}：官方原文引用已保留在审计证据中。")
        else:
            lines.append("- 当前未获取到官方原文引用。")

        lines.append("")
        lines.append("## 来源状态")
        for result in results:
            status_text = _STATUS_TEXT.get(result.status, result.status.value)
            lines.append(f"- {result.spec.provider}.{result.spec.endpoint}：{status_text}。")

        lines.append("")
        lines.append("## 资料缺口")
        if gaps:
            for gap in gaps:
                lines.append(f"- {gap.field_path}：{gap.root_cause}")
        else:
            lines.append("- 未发现字段缺口。")

        lines.append("")
        lines.append("## 口径冲突")
        if conflicts:
            for item in conflicts:
                lines.append(f"- {item.field_path}：不同来源口径不一致，已在审计证据中保留双方数值。")
        else:
            lines.append("- 未发现 provider 字段冲突。")
        return "\n".join(lines)


@dataclass
class FundamentalPackService:
    settings: Any
    adapters: tuple[ProviderAdapter, ...]
    builder: FundamentalPackBuilder = field(default_factory=FundamentalPackBuilder)
    provider_execution_helper: ProviderExecutionEvidenceHelper | None = None

    def __post_init__(self) -> None:
        self._adapters = {adapter.adapter_id: adapter for adapter in self.adapters}

    def get_pack(
        self,
        request: PackRequest,
        run_plan: RunProviderPlan,
        *,
        warehouse_results: tuple[ProviderResult, ...] = (),
        warehouse_gaps: tuple[DataGap, ...] = (),
    ) -> DomainPackResult:
        specs = tuple(
            spec
            for spec in run_plan.call_specs
            if spec.domain == PackDomain.FUNDAMENTAL and spec.market == request.market
        )
        if not specs:
            gap = DataGap(
                gap_id=f"{request.run_id}:{request.call_id}:fundamental:source_not_configured",
                domain=PackDomain.FUNDAMENTAL,
                severity=GapSeverity.FAIL,
                reason=DataGapReason.SOURCE_NOT_CONFIGURED,
                field_path="fundamental",
                provider_candidates=(),
                attempt_ids=(),
                root_cause="no fundamental call specs in run plan",
                next_action="configure fundamental providers",
            )
            return self.builder.build(
                request=request,
                run_plan=run_plan,
                call_specs=(),
                results=(),
                extra_gaps=(gap,),
            )

        results: list[ProviderResult] = list(warehouse_results)
        for spec in specs:
            if _coverage_group_satisfied(spec=spec, previous_results=results):
                continue
            results.append(self._execute_spec(request, spec))
        return self.builder.build(
            request=request,
            run_plan=run_plan,
            call_specs=specs,
            results=tuple(results),
            extra_gaps=tuple(
                gap
                for gap in run_plan.initial_gaps
                if gap.domain == PackDomain.FUNDAMENTAL
            )
            + warehouse_gaps,
        )

    def _execute_spec(self, request: PackRequest, spec: ProviderCallSpec) -> ProviderResult:
        started = utc_now_iso()
        t0 = time.perf_counter()
        adapter = self._adapters.get(spec.adapter_id)
        if adapter is None:
            return self._build_result(
                request=request,
                spec=spec,
                started=started,
                status=ProviderStatus.SKIPPED_NOT_CONFIGURED,
                freshness=FreshnessStatus.NOT_FETCHED,
                rows=(),
                row_count=0,
                raw_ref=None,
                normalized_ref=None,
                error_code="adapter_not_configured",
                error_message=f"adapter {spec.adapter_id} not configured",
                latency_ms=int((time.perf_counter() - t0) * 1000),
            )

        credential = adapter.validate_credentials()
        if credential.status == AdmissionCheckStatus.MISSING:
            return self._build_result(
                request=request,
                spec=spec,
                started=started,
                status=ProviderStatus.CREDENTIAL_MISSING,
                freshness=FreshnessStatus.NOT_FETCHED,
                rows=(),
                row_count=0,
                raw_ref=None,
                normalized_ref=None,
                error_code="credential_missing",
                error_message=credential.root_cause or "credential missing",
                latency_ms=int((time.perf_counter() - t0) * 1000),
            )

        executor = self.provider_execution_helper
        if executor is not None and getattr(executor, "gate_controlled", False):
            return executor.execute(
                request=request,
                spec=spec,
                adapter=adapter,
                started_at=started,
            )

        return self._build_result(
            request=request,
            spec=spec,
            started=started,
            status=ProviderStatus.EVIDENCE_WRITE_FAILED,
            freshness=FreshnessStatus.NOT_FETCHED,
            rows=(),
            row_count=0,
            raw_ref=None,
            normalized_ref=None,
            error_code="provider_call_gate_missing",
            error_message="provider call gate is not configured; pack runtime remote calls must use run_provider_call_gate",
            latency_ms=int((time.perf_counter() - t0) * 1000),
        )

    def _build_result(
        self,
        *,
        request: PackRequest,
        spec: ProviderCallSpec,
        started: str,
        status: ProviderStatus,
        freshness: FreshnessStatus,
        rows: tuple[Mapping[str, Any], ...],
        row_count: int,
        raw_ref: str | None,
        normalized_ref: str | None,
        error_code: str | None,
        error_message: str | None,
        latency_ms: int,
    ) -> ProviderResult:
        attempt_id = f"{request.run_id}:{request.call_id}:{spec.adapter_id}:{spec.endpoint}"
        attempt = ProviderAttempt(
            attempt_id=attempt_id,
            run_id=request.run_id,
            call_id=request.call_id,
            worker_id=request.worker_id,
            pack="fundamental",
            provider=spec.provider,
            adapter_id=spec.adapter_id,
            adapter_kind="fundamental_adapter",
            provider_kind=spec.provider_kind,
            provider_config_version=spec.provider_config_version,
            endpoint=spec.endpoint,
            source_role=spec.source_role,
            started_at=started,
            finished_at=utc_now_iso(),
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
            latency_ms=max(0, latency_ms),
            row_count=row_count,
            raw_ref=raw_ref,
            normalized_ref=normalized_ref,
            error_code=error_code,
            error_message=error_message,
            schema_id=spec.expected_schema_id,
            license_note="approved",
        )
        return ProviderResult(
            spec=spec,
            status=status,
            request_id=None,
            requested_at=started,
            latency_ms=max(0, latency_ms),
            source_role=spec.source_role,
            freshness=freshness,
            license_note="approved",
            raw_ref=raw_ref,
            normalized_ref=normalized_ref,
            rows=rows,
            row_count=row_count,
            cache_receipt=None,
            attempt=attempt,
            error_code=error_code,
            error_message=error_message,
        )
