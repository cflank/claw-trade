from __future__ import annotations

import re
from dataclasses import dataclass

_STYLE_BLOCK_RE = re.compile(r"<style\b[^>]*>.*?</style>", flags=re.IGNORECASE | re.DOTALL)
_WRITING_MODE_TAG_RE = re.compile(r"<[^>]*writing-mode[^>]*>", flags=re.IGNORECASE)
_TEXT_ORIENTATION_TAG_RE = re.compile(r"<[^>]*text-orientation[^>]*>", flags=re.IGNORECASE)
_DIV_SPAN_TAG_RE = re.compile(r"<(div|span)\b([^>]*)>", flags=re.IGNORECASE)
_STYLE_ATTR_RE = re.compile(r"""\sstyle\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)""", flags=re.IGNORECASE)
_TABLE_SEPARATOR_CELL_RE = re.compile(r":?-{3,}:?")

_QUOTE_REPLACEMENTS = str.maketrans(
    {
        "“": '"',
        "”": '"',
        "‘": "'",
        "’": "'",
    }
)


@dataclass(frozen=True)
class PdfMarkdownCleaner:
    fallback_title: str = "# 分析报告"

    def clean(self, content: str) -> str:
        if not content:
            return ""

        cleaned = content.strip()
        first_line = cleaned.splitlines()[0] if cleaned else ""
        if first_line.startswith("---") or first_line.startswith("..."):
            cleaned = "\n" + cleaned

        cleaned = "\n".join(
            line if _is_markdown_table_separator(line) else line.replace("---", "—")
            for line in cleaned.splitlines()
        )
        cleaned = cleaned.replace("...", "…")

        cleaned = cleaned.translate(_QUOTE_REPLACEMENTS)
        cleaned = _STYLE_BLOCK_RE.sub("", cleaned)
        cleaned = _WRITING_MODE_TAG_RE.sub("", cleaned)
        cleaned = _TEXT_ORIENTATION_TAG_RE.sub("", cleaned)
        cleaned = _DIV_SPAN_TAG_RE.sub(self._remove_inline_style_from_div_span, cleaned)

        if not cleaned.startswith("#"):
            cleaned = f"{self.fallback_title}\n\n{cleaned}"
        return cleaned

    @staticmethod
    def _remove_inline_style_from_div_span(match: re.Match[str]) -> str:
        tag_name = match.group(1)
        attrs = match.group(2)
        attrs_without_style = _STYLE_ATTR_RE.sub("", attrs)
        return f"<{tag_name}{attrs_without_style}>"


def _is_markdown_table_separator(line: str) -> bool:
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return False
    cells = [cell.strip().replace(" ", "") for cell in stripped.strip("|").split("|")]
    return bool(cells) and all(_TABLE_SEPARATOR_CELL_RE.fullmatch(cell) for cell in cells)
