from __future__ import annotations

from dataclasses import replace
import re

from claw_trade.data_gateway.models import (
    DataGap,
    DataGapReason,
    DomainPack,
    DomainPackApprovalStatus,
    DomainPackResult,
    GapSeverity,
    ProviderStatus,
)

_PROTOCOL_REF_RE = re.compile(r"\b(?:mongo|viking|object|attempt|cache|raw|normalized|analysis)://\S+")


def materialize_domain_pack_result(pack_result: DomainPackResult) -> DomainPack:
    gaps = tuple(_materialize_gap(gap) for gap in pack_result.data_gaps)
    evidence_failed = any(attempt.status == ProviderStatus.EVIDENCE_WRITE_FAILED for attempt in pack_result.attempts)
    blocked_by_gap = any(gap.severity in {GapSeverity.BLOCKER, GapSeverity.FAIL} for gap in gaps)
    blocked_by_required_remote_failure = _has_required_remote_failure(pack_result)
    normalized_refs = tuple(dict.fromkeys(pack_result.normalized_refs))

    if evidence_failed or blocked_by_gap or blocked_by_required_remote_failure:
        approval_status = DomainPackApprovalStatus.BLOCKED
    elif gaps:
        approval_status = DomainPackApprovalStatus.WARN
    else:
        approval_status = DomainPackApprovalStatus.APPROVED

    if approval_status == DomainPackApprovalStatus.APPROVED and not normalized_refs:
        approval_status = DomainPackApprovalStatus.BLOCKED
        gaps = (
            *gaps,
            DataGap(
                gap_id=f"{pack_result.request.run_id}:{pack_result.request.call_id}:{pack_result.request.domain.value}:missing_normalized_ref",
                domain=pack_result.request.domain,
                severity=GapSeverity.BLOCKER,
                reason=DataGapReason.EVIDENCE_WRITE_FAILED,
                field_path="normalized_refs",
                provider_candidates=tuple(sorted({attempt.provider for attempt in pack_result.attempts})),
                attempt_ids=tuple(attempt.attempt_id for attempt in pack_result.attempts),
                root_cause="approved pack missing normalized refs",
                next_action="排查 attempt/raw/normalized 写入链路",
                human_readable="资料包缺少标准化结果引用，不能作为可用材料。",
            ),
        )

    source_summary = _render_source_summary(
        pack_result=pack_result,
        gaps=gaps,
        approval_status=approval_status,
        evidence_failed=evidence_failed,
    )

    return DomainPack(
        pack_id=f"{pack_result.request.run_id}:{pack_result.request.call_id}:{pack_result.request.domain.value}",
        run_id=pack_result.request.run_id,
        domain=pack_result.request.domain,
        market=pack_result.request.market,
        ticker=pack_result.request.ticker,
        normalized_refs=normalized_refs,
        raw_payload_refs=tuple(dict.fromkeys(pack_result.raw_refs)),
        attempt_refs=tuple(
            f"mongo://openbb_provider_attempts/{attempt.attempt_id}"
            for attempt in pack_result.attempts
        ),
        http_evidence_refs=_collect_http_evidence_refs(pack_result),
        data_gaps=gaps,
        readable_markdown=_sanitize_worker_text(pack_result.reader_brief_md),
        source_summary=source_summary,
        approval_status=approval_status,
        material_ref=None,
    )

_REQUIRED_FAILURE_STATUSES = frozenset(
    {
        ProviderStatus.REMOTE_ERROR,
        ProviderStatus.CREDENTIAL_MISSING,
        ProviderStatus.RATE_LIMITED,
        ProviderStatus.COOLDOWN_SKIPPED,
        ProviderStatus.EMPTY,
        ProviderStatus.FIELD_MISSING,
        ProviderStatus.SCHEMA_INVALID,
        ProviderStatus.CACHED_EMPTY,
        ProviderStatus.SDK_HTTP_UNKNOWN,
        ProviderStatus.EVIDENCE_WRITE_FAILED,
        ProviderStatus.LICENSE_BLOCKED,
    }
)


def _materialize_gap(gap: DataGap) -> DataGap:
    human_text = gap.human_readable
    if human_text is None or not human_text.strip():
        human_text = _default_gap_human_text(gap)
    return replace(gap, human_readable=_sanitize_worker_text(human_text))


def _default_gap_human_text(gap: DataGap) -> str:
    reason = gap.reason.value
    field = gap.field_path.strip() or "unknown_field"
    if gap.reason == DataGapReason.EVIDENCE_WRITE_FAILED:
        return f"证据写入失败：{field}，无法作为可用材料。"
    if gap.reason == DataGapReason.CREDENTIAL_MISSING:
        return f"数据源凭证缺失：{field}。"
    if gap.reason == DataGapReason.RATE_LIMITED:
        return f"数据源限流：{field}。"
    if gap.reason == DataGapReason.CACHED_EMPTY:
        return f"缓存为空：{field}。"
    if gap.reason == DataGapReason.SDK_HTTP_UNKNOWN:
        return f"SDK 内部 HTTP 不可见：{field}。"
    return f"数据缺口（{reason}）：{field}。"


def _has_required_remote_failure(pack_result: DomainPackResult) -> bool:
    for attempt in pack_result.attempts:
        if not attempt.attempt_required:
            continue
        if attempt.status in _REQUIRED_FAILURE_STATUSES:
            return True
    return False


def _render_source_summary(
    *,
    pack_result: DomainPackResult,
    gaps: tuple[DataGap, ...],
    approval_status: DomainPackApprovalStatus,
    evidence_failed: bool,
) -> str:
    status_text = {
        DomainPackApprovalStatus.APPROVED: "approved",
        DomainPackApprovalStatus.WARN: "warn",
        DomainPackApprovalStatus.BLOCKED: "blocked",
    }[approval_status]
    provider_text = "、".join(sorted({attempt.provider for attempt in pack_result.attempts})) or "none"
    attempt_count = len(pack_result.attempts)
    gap_count = len(gaps)
    blocker_count = sum(1 for gap in gaps if gap.severity in {GapSeverity.BLOCKER, GapSeverity.FAIL})
    warn_count = sum(1 for gap in gaps if gap.severity == GapSeverity.WARN)
    info_count = sum(1 for gap in gaps if gap.severity == GapSeverity.INFO)
    evidence_text = "yes" if evidence_failed else "no"
    return _sanitize_worker_text(
        (
            f"资料状态：{status_text}；来源 provider：{provider_text}；"
            f"attempts={attempt_count}，data_gaps={gap_count} "
            f"(blocker={blocker_count}, warn={warn_count}, info={info_count})；"
            f"evidence_write_failed={evidence_text}。"
        )
    )


def _sanitize_worker_text(text: str) -> str:
    return _PROTOCOL_REF_RE.sub("[evidence_ref]", text)


def _collect_http_evidence_refs(pack_result: DomainPackResult) -> tuple[str, ...]:
    refs: list[str] = []
    for gap in pack_result.data_gaps:
        for ref in gap.evidence_refs:
            _append_http_ref(refs, ref)
    for attempt in pack_result.attempts:
        metadata = attempt.source_metadata
        if not isinstance(metadata, dict):
            continue
        for key in ("http_evidence_ref", "http_evidence_refs"):
            value = metadata.get(key)
            if isinstance(value, str):
                _append_http_ref(refs, value)
            elif isinstance(value, (tuple, list)):
                for item in value:
                    _append_http_ref(refs, item)
    return tuple(dict.fromkeys(refs))


def _append_http_ref(target: list[str], value: object) -> None:
    if not isinstance(value, str):
        return
    ref = value.strip()
    if not ref:
        return
    if "openbb_provider_http_evidence" not in ref:
        return
    target.append(ref)
