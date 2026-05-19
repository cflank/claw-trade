from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable

SECTION_NUMERALS: tuple[str, ...] = ("一", "二", "三", "四", "五", "六", "七", "八")
SECTION_NUMBER_TO_NUMERAL = {index: numeral for index, numeral in enumerate(SECTION_NUMERALS, start=1)}
SECTION_NUMERAL_TO_NUMBER = {numeral: index for index, numeral in SECTION_NUMBER_TO_NUMERAL.items()}

_H1_RE = re.compile(r"^# (?!#)(.*)$")
_H2_SECTION_RE = re.compile(r"^## ([一二三四五六七八])[、.．]")
_H2_TITLE_RE = re.compile(r"^## .*(投资研究报告|Investment Research Report)\s*$")


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


def _first_nonblank_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


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
