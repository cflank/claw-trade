from __future__ import annotations

from models import BriefInput, NewsItem, ProviderAttempt, Quality

_MAX_BRIEF_LENGTH = 4000
_MAX_TIMELINE_ITEMS = 20
_TRUNCATED_NOTE = "（以下内容因长度限制已截断。）"
_FAILED_LIMIT_LINE = "该资料包未形成正式新闻判断材料，只能用于诊断数据缺口。"
_PARTIAL_LIMIT_LINE = "该资料包不可用于公司方向性新闻判断，只能作为受限背景材料。"


class ReaderBriefBuilder:
    def build(self, brief_input: BriefInput) -> str:
        quality = brief_input.quality
        lines: list[str] = []
        failed_with_empty_items = quality.status == "failed" and not brief_input.items
        if failed_with_empty_items:
            lines.extend(_build_provider_lines(brief_input.provider_attempts))
            lines.extend(_build_gap_lines(quality))
            lines.extend(_build_status_limit_lines(quality))
        else:
            lines.extend(_build_count_lines(brief_input))
            lines.extend(_build_provider_lines(brief_input.provider_attempts))
            lines.extend(_build_gap_lines(quality))
            lines.extend(_build_status_limit_lines(quality))
            for item in brief_input.items[:_MAX_TIMELINE_ITEMS]:
                lines.append(_build_timeline_line(item))

        brief = _enforce_length_limit(lines)
        return _ensure_status_limit_line(brief, quality)


def _build_count_lines(brief_input: BriefInput) -> list[str]:
    quality = brief_input.quality
    plan = brief_input.query_plan
    return [
        f"本资料包覆盖 {plan.start_date} 至 {plan.end_date}。",
        (
            f"共获取 {quality.total_raw_count} 条原始新闻，"
            f"去重后 {quality.after_dedup_count} 条，接受 {quality.accepted_count} 条。"
        ),
        (
            f"公司直连新闻 {quality.company_direct_news_count} 条，"
            f"行业背景 {quality.industry_background_count} 条，"
            f"政策/宏观背景 {quality.policy_macro_count} 条。"
        ),
    ]


def _build_provider_lines(provider_attempts: list[ProviderAttempt]) -> list[str]:
    lines: list[str] = []
    for attempt in provider_attempts:
        if attempt.ok:
            lines.append(
                f"{attempt.provider}.{attempt.endpoint} 返回 {attempt.raw_count} 条，"
                f"接受 {attempt.accepted_count} 条。"
            )
            continue

        reason = attempt.empty_reason if attempt.empty_reason else "unknown"
        lines.append(f"{attempt.provider}.{attempt.endpoint} 未形成可用结果，原因：{reason}。")
    return lines


def _build_gap_lines(quality: Quality) -> list[str]:
    lines: list[str] = []
    if quality.warnings:
        lines.append(f"限制与告警：{'；'.join(quality.warnings)}。")
    if quality.missing_fields:
        lines.append(f"资料缺口字段：{', '.join(quality.missing_fields)}。")
    return lines


def _build_status_limit_lines(quality: Quality) -> list[str]:
    lines: list[str] = []
    if quality.status == "partial":
        lines.append(_PARTIAL_LIMIT_LINE)
    if quality.status == "failed":
        lines.append(_FAILED_LIMIT_LINE)
    return lines


def _build_timeline_line(item: NewsItem) -> str:
    publish_time = _normalize_text(item.publish_time, default_text="发布时间缺失")
    source = _normalize_text(item.source, default_text="来源字段缺失")
    title = _normalize_text(item.title, default_text="标题字段缺失")
    match_type = _normalize_text(item.match_type, default_text="unknown")
    match_span = _normalize_text(item.match_evidence_span, default_text="匹配证据缺失")

    gaps: list[str] = []
    if _is_empty_text(item.publish_time):
        gaps.append("发布时间缺失")
    if _is_empty_text(item.source):
        gaps.append("来源字段缺失")
    if item.evidence_gap and item.evidence_gap.strip():
        gaps.append(item.evidence_gap.strip())

    line = f"{publish_time}，{source}，{title}；匹配原因：{match_type}/{match_span}"
    if gaps:
        return f"{line}；缺口：{', '.join(gaps)}。"
    return f"{line}。"


def _normalize_text(value: str | None, *, default_text: str) -> str:
    if value is None:
        return default_text
    stripped = value.strip()
    return stripped if stripped else default_text


def _is_empty_text(value: str | None) -> bool:
    return value is None or value.strip() == ""


def _enforce_length_limit(lines: list[str]) -> str:
    full_text = "\n".join(lines)
    if len(full_text) <= _MAX_BRIEF_LENGTH:
        return full_text

    kept_lines: list[str] = []
    current_len = 0
    note_with_newline_len = len(f"\n{_TRUNCATED_NOTE}")
    note_len = len(_TRUNCATED_NOTE)

    for line in lines:
        piece = line if not kept_lines else f"\n{line}"
        projected_len = current_len + len(piece)
        suffix_len = note_len if not kept_lines else note_with_newline_len
        if projected_len + suffix_len > _MAX_BRIEF_LENGTH:
            break
        kept_lines.append(line)
        current_len = projected_len

    if kept_lines:
        result = "\n".join(kept_lines)
        if len(result) + note_with_newline_len <= _MAX_BRIEF_LENGTH:
            result = f"{result}\n{_TRUNCATED_NOTE}"
        else:
            result = result[:_MAX_BRIEF_LENGTH]
        return result

    return _TRUNCATED_NOTE[:_MAX_BRIEF_LENGTH]


def _ensure_status_limit_line(brief: str, quality: Quality) -> str:
    required_line: str | None = None
    if quality.status == "partial":
        required_line = _PARTIAL_LIMIT_LINE
    elif quality.status == "failed":
        required_line = _FAILED_LIMIT_LINE

    if required_line is None or required_line in brief:
        return brief

    max_prefix_len = _MAX_BRIEF_LENGTH - len(required_line) - 1
    if max_prefix_len <= 0:
        return required_line[:_MAX_BRIEF_LENGTH]

    prefix = brief[:max_prefix_len].rstrip("\n")
    if not prefix:
        return required_line

    result = f"{prefix}\n{required_line}"
    return result[:_MAX_BRIEF_LENGTH]
