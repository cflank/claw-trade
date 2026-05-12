from __future__ import annotations

import re

from .errors import (
    BRIEF_MACHINE_NOISE,
    BRIEF_SECRET_LEAK,
    BRIEF_UNSUPPORTED_CONCLUSION,
    FrontlineValidationError,
)
from .models import BriefInput, GateInput, PackEnvelope
from .observability import record_brief_build, record_brief_validation_failed


_DOMAIN_LABELS = {
    "market": "行情与技术面资料",
    "fundamental": "基本面资料",
    "news": "新闻资料",
    "social": "社交与情绪资料",
}
_URI_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://")
_SECRET_RE = re.compile(
    r"(?i)\b(?:api[_-]?key|token|authorization|password|passwd|signature|sign|secret)\b\s*[:=]\s*\S+"
)
_JSON_PAIR_RE = re.compile(r'"[^"\n]{1,64}"\s*:\s*')
_UNSUPPORTED_CONCLUSION_RE = re.compile(
    r"(目标价|买入|卖出|增持|减持|买卖建议|投资评级|评级调整|跑赢大盘|强烈推荐)"
)
_MISSING_FIELDS_MARKER_RE = re.compile(
    r"(?:^|[\s:：])(?:missing_core_fields?|missing_fields?|missing_items?)\s*[:=：]\s*(.+)$",
    re.IGNORECASE,
)
_UNSUPPORTED_RISK_MARKER_RE = re.compile(
    r"(?:^|[\s:：])(?:unsupported_claim_risk_fields?|unsupported_claim_fields?|unsupported_claim_risk)\s*[:=：]\s*(.+)$",
    re.IGNORECASE,
)
_FIELD_SPLIT_RE = re.compile(r"[，,；;|/]+")


def build_reader_brief(input: BriefInput) -> str:
    """生成给 worker 阅读的中文事实材料，禁止投资结论。"""
    try:
        domain_label = _DOMAIN_LABELS[input.domain]
        date_window = f"{input.input.start_date} 至 {input.input.end_date}"

        paragraphs: list[str] = []
        paragraphs.append(
            f"{input.input.company_name}（{input.input.ticker}）{domain_label}，统计窗口 {date_window}。"
        )
        paragraphs.append(
            f"事实材料：{_summarize_evidence(input.evidence_summary)}"
        )

        attempt_summary = _summarize_attempts(input)
        if attempt_summary:
            paragraphs.append(f"来源：{attempt_summary}")

        if input.missing_items:
            paragraphs.append(
                "未覆盖项："
                f"{_join_human_list(input.missing_items)}。"
            )

        if input.conflict_diagnostics:
            paragraphs.append(
                "来源冲突："
                f"{_join_human_list(input.conflict_diagnostics)}。"
            )

        text = "\n\n".join(paragraphs).strip()
        text = _ensure_brief_length(text, input)
        try:
            validate_reader_brief(text)
        except FrontlineValidationError:
            record_brief_validation_failed(domain=input.domain)
            raise
        record_brief_build(domain=input.domain, status="success")
        return text
    except Exception:
        record_brief_build(domain=input.domain, status="failure")
        raise


def build_gate_input(pack: PackEnvelope) -> GateInput:
    """从 pack 中提取 hard gate 所需结构化信息。"""

    missing_core_fields = _extract_missing_core_fields(pack)
    unsupported_claim_risk_fields = _extract_unsupported_claim_risk_fields(pack, missing_core_fields)
    return GateInput(
        run_id=pack.run_id,
        worker_id=pack.worker_id,
        call_id=pack.call_id,
        domain=pack.domain,
        quality_status=pack.quality.status,
        provider_attempt_count=len(pack.provider_attempts),
        l2_verified_count=sum(1 for ref in pack.openviking_l2_refs if ref.readback_verified),
        missing_core_fields=missing_core_fields,
        unsupported_claim_risk_fields=unsupported_claim_risk_fields,
        diagnostic_flags=list(pack.diagnostic_flags),
    )


def validate_reader_brief(text: str) -> None:
    """检查 secret、URI 噪音、投资结论和 JSON 堆砌。"""

    normalized = text.strip()
    if normalized == "":
        raise FrontlineValidationError(BRIEF_MACHINE_NOISE, "reader_brief 不能为空")
    if _SECRET_RE.search(normalized):
        raise FrontlineValidationError(BRIEF_SECRET_LEAK, "reader_brief 含疑似敏感字段")
    if _URI_RE.search(normalized):
        raise FrontlineValidationError(BRIEF_MACHINE_NOISE, "reader_brief 含 URI 主体")
    if _UNSUPPORTED_CONCLUSION_RE.search(normalized):
        raise FrontlineValidationError(BRIEF_UNSUPPORTED_CONCLUSION, "reader_brief 含投资结论词")
    if _looks_like_machine_noise(normalized):
        raise FrontlineValidationError(BRIEF_MACHINE_NOISE, "reader_brief 含机器噪音")


def _summarize_attempts(input: BriefInput) -> str:
    grouped: dict[tuple[str, str], int] = {}
    for attempt in input.provider_attempts:
        key = (attempt.provider, attempt.endpoint)
        grouped[key] = grouped.get(key, 0) + max(0, attempt.accepted_count)

    if not grouped:
        return ""

    parts: list[str] = []
    for (provider, endpoint), accepted_count in sorted(grouped.items(), key=lambda item: item[1], reverse=True):
        if accepted_count <= 0:
            continue
        if len(parts) >= 6:
            break
        parts.append(f"{provider}/{endpoint} 提供 {accepted_count} 条可用材料")
    if not parts:
        return ""
    return "；".join(parts) + "。"


def _summarize_evidence(evidence_summary: list[str]) -> str:
    if not evidence_summary:
        return "当前没有可直接展开的事实材料。"
    chosen = [_strip_sentence_tail(item) for item in evidence_summary[:8]]
    text = "；".join(chosen)
    if len(evidence_summary) > len(chosen):
        text += f"；其余 {len(evidence_summary) - len(chosen)} 项已省略"
    return text + "。"


def _strip_sentence_tail(text: str) -> str:
    return text.strip().rstrip("。；; ")


def _join_human_list(items: list[str]) -> str:
    chosen = [item.strip() for item in items if item.strip()][:12]
    if not chosen:
        return "无"
    if len(items) > len(chosen):
        chosen.append(f"其余 {len(items) - len(chosen)} 项")
    return "、".join(chosen)


def _ensure_brief_length(text: str, input: BriefInput) -> str:
    _ = input
    if len(text) > 3000:
        truncated = text[:3000].rstrip()
        if truncated and truncated[-1] not in "。！？":
            if len(truncated) >= 3000:
                truncated = truncated[:2999].rstrip()
            truncated += "。"
        return truncated[:3000]
    return text


def _extract_missing_core_fields(pack: PackEnvelope) -> list[str]:
    candidates: list[str] = []
    _extend_string_list(candidates, pack.domain_data.get("missing_core_fields"))
    _extend_string_list(candidates, pack.domain_data.get("missing_items"))
    for item in pack.quality.warnings:
        _extend_marker_items(candidates, item, _MISSING_FIELDS_MARKER_RE)
    for item in pack.diagnostic_flags:
        _extend_marker_items(candidates, item, _MISSING_FIELDS_MARKER_RE)
    return _deduplicate(candidates)


def _extract_unsupported_claim_risk_fields(
    pack: PackEnvelope,
    missing_core_fields: list[str],
) -> list[str]:
    candidates: list[str] = []
    _extend_string_list(candidates, pack.domain_data.get("unsupported_claim_risk_fields"))
    for item in pack.quality.warnings:
        _extend_marker_items(candidates, item, _UNSUPPORTED_RISK_MARKER_RE)
    for item in pack.diagnostic_flags:
        _extend_marker_items(candidates, item, _UNSUPPORTED_RISK_MARKER_RE)
    if not candidates:
        candidates.extend(missing_core_fields)
    return _deduplicate(candidates)


def _extend_string_list(buffer: list[str], value: object) -> None:
    if not isinstance(value, list):
        return
    for item in value:
        if isinstance(item, str) and item.strip():
            buffer.append(item.strip())


def _extend_marker_items(buffer: list[str], text: str, pattern: re.Pattern[str]) -> None:
    match = pattern.search(text)
    if match is None:
        return
    raw_text = match.group(1).strip()
    if not raw_text:
        return
    for piece in _FIELD_SPLIT_RE.split(raw_text):
        item = piece.strip()
        if item:
            buffer.append(item)


def _deduplicate(items: list[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for item in items:
        normalized = item.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered


def _looks_like_machine_noise(text: str) -> bool:
    json_pairs = len(_JSON_PAIR_RE.findall(text))
    if json_pairs >= 3:
        return True
    machine_chars = sum(text.count(ch) for ch in '{}[]"')
    if machine_chars >= 60 and machine_chars * 5 > len(text):
        return True
    return False
