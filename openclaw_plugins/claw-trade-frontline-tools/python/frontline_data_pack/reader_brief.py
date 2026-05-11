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
_STATUS_LABELS = {
    "complete": "资料完整，可支撑后续分析章节",
    "partial": "资料部分可用，后续分析需明确受限边界",
    "failed": "资料不可用，仅可作为失败审计记录",
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
        coverage_percent = round(input.quality.coverage_score * 100, 2)
        date_window = f"{input.input.start_date} 至 {input.input.end_date}"
        attempts_total = len(input.provider_attempts)
        success_attempts = sum(1 for attempt in input.provider_attempts if attempt.status == "success")
        accepted_total = sum(input.accepted_counts.values())
        raw_total = sum(attempt.raw_count for attempt in input.provider_attempts)

        paragraphs: list[str] = []
        paragraphs.append(
            "资料范围："
            f"本次资料包面向{input.input.ticker}，归属市场为{input.input.market}，资料主题属于{domain_label}。"
            f"统计窗口覆盖 {date_window}。"
            "以下内容是给后续 worker 直接阅读的事实材料，不是机器审计包，也不扩展为投资判断。"
        )
        paragraphs.append(
            "材料正文："
            f"{_summarize_evidence(input.evidence_summary)}"
            "后续报告只能引用这里已有的事实；材料没有给出的数字、新闻、情绪或图表结论不得补写。"
        )
        paragraphs.append(
            "质量状态："
            f"当前质量状态为 {input.quality.status}，覆盖分为 {coverage_percent}% ，"
            f"新鲜度标记为 {input.quality.freshness_status}。"
            f"{_STATUS_LABELS[input.quality.status]}。"
        )
        paragraphs.append(
            "来源概况："
            f"本轮累计访问 {attempts_total} 个数据接口，其中成功取得可用结果 {success_attempts} 个，"
            f"形成可用材料 {accepted_total} 条，原始返回记录 {raw_total} 条。"
            f"{_summarize_attempts(input)}"
        )
        if input.missing_items:
            paragraphs.append(
                "证据缺口："
                f"目前确认的缺口包括 {_join_human_list(input.missing_items)}。"
                "这些缺口会直接限制可被稳健支持的分析维度。"
            )
        else:
            paragraphs.append("证据缺口：当前未发现显式缺口项，但仍需以已采集证据范围为上限解释结论。")

        if input.conflict_diagnostics:
            paragraphs.append(
                "冲突诊断："
                f"已记录的冲突提示包括 {_join_human_list(input.conflict_diagnostics)}。"
                "冲突项表示来源之间存在不一致，需要在后续分析中注明差异。"
            )
        else:
            paragraphs.append("冲突诊断：当前未记录显式冲突项，来源之间未发现明显相互否定。")

        paragraphs.append(_status_boundary_line(input))

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
        grouped[key] = grouped.get(key, 0) + attempt.accepted_count

    if not grouped:
        return "本轮未形成可归因来源。"

    parts: list[str] = []
    for (provider, endpoint), accepted_count in sorted(grouped.items(), key=lambda item: item[1], reverse=True):
        if len(parts) >= 6:
            break
        parts.append(f"{provider}/{endpoint} 提供 {accepted_count} 条可用材料")
    return "来源分布为：" + "；".join(parts) + "。"


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


def _status_boundary_line(input: BriefInput) -> str:
    if input.quality.status == "complete":
        return (
            "使用边界：当前资料包可作为后续 worker 的主要证据基础，但仍需保持“仅引用已采集与可追溯事实”的约束，"
            "新增结论必须能够回溯到本次来源摘要中的证据类别。"
        )
    if input.quality.status == "partial":
        return (
            "使用边界：当前资料包只允许在已覆盖维度内展开分析；对缺口维度不得补写推测性事实，"
            "并应明确标注缺口对结论稳定性的影响范围。"
        )
    return (
        "使用边界：当前资料包处于失败状态，仅可用于说明失败原因、证据缺口与冲突诊断，"
        "不得据此扩展事实判断。后续流程应优先触发补采或重跑。"
    )


def _ensure_brief_length(text: str, input: BriefInput) -> str:
    if len(text) > 3000:
        truncated = text[:3000].rstrip()
        if truncated and truncated[-1] not in "。！？":
            if len(truncated) >= 3000:
                truncated = truncated[:2999].rstrip()
            truncated += "。"
        return truncated[:3000]

    if len(text) >= 500:
        return text

    supplement = (
        f"补充说明：本次资料窗口为 {input.input.start_date} 至 {input.input.end_date}，"
        f"覆盖对象为 {input.input.ticker}，质量状态 {input.quality.status}，"
        f"覆盖分 {round(input.quality.coverage_score * 100, 2)}%，"
        f"数据接口访问 {len(input.provider_attempts)} 次。"
        "这份材料强调已取得的事实、证据缺口与冲突诊断，供后续 worker 按自然语言材料引用。"
    )
    enriched = text
    while len(enriched) < 500:
        next_block = "\n\n" + supplement
        if len(enriched) + len(next_block) > 3000:
            break
        enriched += next_block
    return enriched


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
