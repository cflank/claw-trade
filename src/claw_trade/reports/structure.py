from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

SECTION_NUMERALS: tuple[str, ...] = ("一", "二", "三", "四", "五", "六", "七", "八")
SECTION_NUMBER_TO_NUMERAL = {index: numeral for index, numeral in enumerate(SECTION_NUMERALS, start=1)}
SECTION_NUMERAL_TO_NUMBER = {numeral: index for index, numeral in SECTION_NUMBER_TO_NUMERAL.items()}

_H1_RE = re.compile(r"^# (?!#)(.*)$")
_H2_SECTION_RE = re.compile(r"^## ([一二三四五六七八])[、.．]")
_H2_TITLE_RE = re.compile(r"^## .*(投资研究报告|Investment Research Report)\s*$")
_MARKDOWN_HEADING_RE = re.compile(r"^(#{1,6})\s+\S")
_MISSING_DATA_META_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(输入材料|上游材料|材料|资料|数据结果|数据工具|数据接口|接口|工具).{0,32}(未提供|未返回|未取得|不可见|未可用|返回空|未调用成功|覆盖不足|覆盖限制|数据限制|缺失|资料缺口|缺少|无法验证|无法确认|无法确定|不能判断|没有可引用|没有可用|未形成可引用)"),
    re.compile(r"(未提供|未返回|未取得).{0,32}(数据|材料|资料|指标|数值|个股|散户|机构|情绪|来源|证据)"),
    re.compile(r"(无法验证|无法确认|无法确定|无法判断|不能判断).{0,32}(数据|材料|资料|指标|数值|观点|分歧|来源|证据|读数)"),
    re.compile(r"(无法从|无法对).{0,32}(确认|判断|评估|展开|进行)"),
    re.compile(r"(当前缺少|缺少证据|没有证据|数据空白|资料状态|工具状态|请求失败|不掌握)"),
    re.compile(r"当前无其他.{0,32}(可靠来源|确认)"),
    re.compile(r"当前无上游.{0,32}(可靠来源|确认)"),
    re.compile(r"(数据|资料|材料|信息|样本|窗口|融资融券|板块资金|公开财务解释).{0,32}(缺失|缺乏|不足)"),
    re.compile(r"(缺失|缺乏|不足).{0,32}(数据|资料|材料|信息|样本|解释|支撑|证据)"),
    re.compile(r"整个基本面框架.{0,32}(缺失|缺乏|不足)"),
    re.compile(r"(未得到|没有|无).{0,32}(官方信息|公开财务解释|公司公告|行业新闻|订单支撑|证据|材料|数据|支撑)"),
)
_MISSING_DATA_META_TOKENS: tuple[str, ...] = (
    "无法验证",
    "无法确认",
    "无法确定",
    "无法判断",
    "不能判断",
    "未取得",
    "未提供",
    "未返回",
    "未形成可引用",
    "未进入论证",
    "覆盖不足",
    "覆盖限制",
    "数据空白",
    "资料状态",
    "工具状态",
    "请求失败",
    "没有可引用",
    "没有可用",
    "当前缺少",
    "缺少证据",
    "没有证据",
    "输入材料未",
    "材料未",
    "不掌握",
    "待可读数据源",
    "需补充验证",
    "因图片附件无法提取",
    "当前无其他已由可靠来源确认",
    "当前无上游可靠来源确认",
    "整个基本面框架缺乏",
)
_MISSING_DATA_META_FRAGMENT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"不因缺少可执行价位条件而改变研究性方向。?"),
)


@dataclass(frozen=True)
class FinalReportStructureResult:
    ok: bool
    category: str
    reason: str | None
    required_sections: tuple[str, ...]
    present_sections: tuple[str, ...]
    h1_count: int
    duplicate_title_headings: tuple[str, ...] = ()
    extra_sections: tuple[str, ...] = ()

    @classmethod
    def passed(
        cls,
        *,
        required_sections: tuple[str, ...],
        present_sections: tuple[str, ...],
        h1_count: int,
    ) -> FinalReportStructureResult:
        return cls(
            ok=True,
            category="ok",
            reason=None,
            required_sections=required_sections,
            present_sections=present_sections,
            h1_count=h1_count,
        )

    @classmethod
    def failed(
        cls,
        *,
        reason: str,
        required_sections: tuple[str, ...],
        present_sections: tuple[str, ...],
        h1_count: int,
        duplicate_title_headings: tuple[str, ...] = (),
        extra_sections: tuple[str, ...] = (),
    ) -> FinalReportStructureResult:
        return cls(
            ok=False,
            category="final_report_structure",
            reason=reason,
            required_sections=required_sections,
            present_sections=present_sections,
            h1_count=h1_count,
            duplicate_title_headings=duplicate_title_headings,
            extra_sections=extra_sections,
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "category": self.category,
            "reason": self.reason,
            "required_sections": list(self.required_sections),
            "present_sections": list(self.present_sections),
            "h1_count": self.h1_count,
            "duplicate_title_headings": list(self.duplicate_title_headings),
            "extra_sections": list(self.extra_sections),
        }


def required_section_numerals(section_numbers: Iterable[int]) -> tuple[str, ...]:
    numerals: list[str] = []
    for number in section_numbers:
        numeral = SECTION_NUMBER_TO_NUMERAL.get(int(number))
        if numeral is None:
            raise ValueError(f"unknown final report section number: {number}")
        numerals.append(numeral)
    return tuple(numerals)


def section_heading_marker(numeral: str) -> str:
    if numeral not in SECTION_NUMERAL_TO_NUMBER:
        raise ValueError(f"unknown final report section numeral: {numeral}")
    return f"## {numeral}、"


def validate_report_polisher_segment_text(
    text: str,
    *,
    required_sections: tuple[str, ...],
    allow_h1: bool,
) -> FinalReportStructureResult:
    present_sections = _present_h2_sections(text)
    h1_count = _h1_count(text)
    duplicate_titles = _duplicate_title_headings(text)
    missing = tuple(section for section in required_sections if section not in present_sections)
    extra = tuple(section for section in present_sections if section not in required_sections)
    first_line = _first_nonblank_line(text)

    if not required_sections:
        return FinalReportStructureResult.failed(
            reason="report_polisher segment 缺少 required_sections",
            required_sections=required_sections,
            present_sections=present_sections,
            h1_count=h1_count,
        )
    if allow_h1:
        if h1_count != 1 or not first_line.startswith("# "):
            return FinalReportStructureResult.failed(
                reason="report_polisher 首段必须且只能有一个 H1 标题",
                required_sections=required_sections,
                present_sections=present_sections,
                h1_count=h1_count,
                duplicate_title_headings=duplicate_titles,
                extra_sections=extra,
            )
    else:
        expected_start = section_heading_marker(required_sections[0])
        if h1_count:
            return FinalReportStructureResult.failed(
                reason="report_polisher 非首段禁止 H1 标题",
                required_sections=required_sections,
                present_sections=present_sections,
                h1_count=h1_count,
                duplicate_title_headings=duplicate_titles,
                extra_sections=extra,
            )
        if not first_line.startswith(expected_start):
            return FinalReportStructureResult.failed(
                reason=f"report_polisher segment 第一行必须以 `{expected_start}` 开头",
                required_sections=required_sections,
                present_sections=present_sections,
                h1_count=h1_count,
                duplicate_title_headings=duplicate_titles,
                extra_sections=extra,
            )
    if missing:
        return FinalReportStructureResult.failed(
            reason=f"report_polisher segment 缺少章节: {','.join(missing)}",
            required_sections=required_sections,
            present_sections=present_sections,
            h1_count=h1_count,
            duplicate_title_headings=duplicate_titles,
            extra_sections=extra,
        )
    if extra:
        return FinalReportStructureResult.failed(
            reason=f"report_polisher segment 输出了范围外章节: {','.join(extra)}",
            required_sections=required_sections,
            present_sections=present_sections,
            h1_count=h1_count,
            duplicate_title_headings=duplicate_titles,
            extra_sections=extra,
        )
    if duplicate_titles:
        return FinalReportStructureResult.failed(
            reason="report_polisher segment 含重复报告标题",
            required_sections=required_sections,
            present_sections=present_sections,
            h1_count=h1_count,
            duplicate_title_headings=duplicate_titles,
            extra_sections=extra,
        )
    return FinalReportStructureResult.passed(
        required_sections=required_sections,
        present_sections=present_sections,
        h1_count=h1_count,
    )


def validate_final_report_text(text: str) -> FinalReportStructureResult:
    required_sections = SECTION_NUMERALS
    present_sections = _present_h2_sections(text)
    h1_count = _h1_count(text)
    duplicate_titles = _duplicate_title_headings(text)
    missing = tuple(section for section in required_sections if section not in present_sections)
    duplicated_sections = _duplicated_sections(present_sections)
    first_line = _first_nonblank_line(text)

    if h1_count != 1 or not first_line.startswith("# "):
        return FinalReportStructureResult.failed(
            reason="最终报告必须且只能有一个 H1 标题",
            required_sections=required_sections,
            present_sections=present_sections,
            h1_count=h1_count,
            duplicate_title_headings=duplicate_titles,
        )
    if duplicate_titles:
        return FinalReportStructureResult.failed(
            reason="最终报告含重复报告标题",
            required_sections=required_sections,
            present_sections=present_sections,
            h1_count=h1_count,
            duplicate_title_headings=duplicate_titles,
        )
    if missing:
        return FinalReportStructureResult.failed(
            reason=f"最终报告缺少章节: {','.join(missing)}",
            required_sections=required_sections,
            present_sections=present_sections,
            h1_count=h1_count,
            duplicate_title_headings=duplicate_titles,
        )
    if duplicated_sections:
        return FinalReportStructureResult.failed(
            reason=f"最终报告含重复章节: {','.join(duplicated_sections)}",
            required_sections=required_sections,
            present_sections=present_sections,
            h1_count=h1_count,
            duplicate_title_headings=duplicate_titles,
        )
    if tuple(section for section in present_sections if section in required_sections) != required_sections:
        return FinalReportStructureResult.failed(
            reason="最终报告章节顺序错误",
            required_sections=required_sections,
            present_sections=present_sections,
            h1_count=h1_count,
            duplicate_title_headings=duplicate_titles,
        )
    return FinalReportStructureResult.passed(
        required_sections=required_sections,
        present_sections=present_sections,
        h1_count=h1_count,
    )


def remove_missing_data_meta_lines(text: str) -> str:
    """Remove human-approved missing-data meta narration from reader-facing copies."""
    kept_lines: list[str] = []
    for line in text.splitlines():
        line = _remove_missing_data_meta_fragments(line)
        stripped = line.strip()
        if stripped and not _is_markdown_table_separator(stripped) and _is_missing_data_meta_line(stripped):
            continue
        kept_lines.append(line)
    kept_lines = _remove_empty_subheadings(kept_lines)
    return "\n".join(kept_lines).strip() + ("\n" if text.endswith("\n") else "")


def _remove_missing_data_meta_fragments(line: str) -> str:
    cleaned = line
    for pattern in _MISSING_DATA_META_FRAGMENT_PATTERNS:
        cleaned = pattern.sub("", cleaned)
    return cleaned.rstrip()


def _remove_empty_subheadings(lines: list[str]) -> list[str]:
    cleaned: list[str] = []
    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        match = _MARKDOWN_HEADING_RE.match(stripped)
        if match is not None and len(match.group(1)) >= 3:
            level = len(match.group(1))
            next_index = index + 1
            has_content = False
            while next_index < len(lines):
                next_stripped = lines[next_index].strip()
                next_heading = _MARKDOWN_HEADING_RE.match(next_stripped)
                if next_heading is not None and len(next_heading.group(1)) <= level:
                    break
                if next_stripped:
                    has_content = True
                    break
                next_index += 1
            if not has_content:
                index += 1
                while index < next_index and not lines[index].strip():
                    index += 1
                continue
        cleaned.append(lines[index])
        index += 1
    return _collapse_blank_lines(cleaned)


def _collapse_blank_lines(lines: list[str]) -> list[str]:
    collapsed: list[str] = []
    blank_count = 0
    for line in lines:
        if line.strip():
            blank_count = 0
            collapsed.append(line)
            continue
        blank_count += 1
        if blank_count <= 2:
            collapsed.append(line)
    return collapsed


def _first_nonblank_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _is_markdown_table_separator(stripped: str) -> bool:
    return bool(re.fullmatch(r"\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?", stripped))


def _is_missing_data_meta_line(stripped: str) -> bool:
    return any(token in stripped for token in _MISSING_DATA_META_TOKENS) or any(
        pattern.search(stripped) for pattern in _MISSING_DATA_META_PATTERNS
    )


def _h1_count(text: str) -> int:
    return sum(1 for line in text.splitlines() if _H1_RE.match(line.strip()))


def _present_h2_sections(text: str) -> tuple[str, ...]:
    sections: list[str] = []
    for line in text.splitlines():
        match = _H2_SECTION_RE.match(line.strip())
        if match is not None:
            sections.append(match.group(1))
    return tuple(sections)


def _duplicate_title_headings(text: str) -> tuple[str, ...]:
    return tuple(line.strip() for line in text.splitlines() if _H2_TITLE_RE.match(line.strip()))


def _duplicated_sections(sections: tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    duplicates: list[str] = []
    for section in sections:
        if section in seen and section not in duplicates:
            duplicates.append(section)
        seen.add(section)
    return tuple(duplicates)
