from __future__ import annotations

import pytest

from claw_trade.ui_backend.pdf_markdown_cleaner import PdfMarkdownCleaner
from claw_trade.ui_backend.report_repository import ReportRepository


@pytest.mark.parametrize(
    ("raw_markdown", "expected_marker"),
    [
        ("---\n正文", "—\n正文"),
        ("...\n正文", "…\n正文"),
    ],
)
def test_cleaner_handles_starting_yaml_like_markers(raw_markdown: str, expected_marker: str) -> None:
    cleaned = PdfMarkdownCleaner().clean(raw_markdown)

    assert cleaned.startswith("# 分析报告\n\n")
    assert expected_marker in cleaned


def test_cleaner_replaces_normal_separator_and_ellipsis() -> None:
    cleaned = PdfMarkdownCleaner().clean("# 标题\n第一段---第二段\n等待...\n")

    assert "第一段—第二段" in cleaned
    assert "等待…" in cleaned


def test_cleaner_keeps_markdown_table_separators() -> None:
    cleaned = PdfMarkdownCleaner().clean(
        "# 标题\n|------|\n|------|------|\n---\n...\n"
    )

    assert "|------|" in cleaned
    assert "|------|------|" in cleaned
    assert "__CLAW_TRADE_TABLE_SEPARATOR" not in cleaned
    assert "\n—\n" in cleaned
    assert cleaned.endswith("\n…")


def test_cleaner_removes_vertical_text_related_html_styles() -> None:
    cleaned = PdfMarkdownCleaner().clean(
        "# 标题\n"
        "<style>.x{writing-mode:vertical-rl;}</style>\n"
        '<div style="color:red" class="x">段落</div>\n'
        "<span style='font-size:12px'>测试</span>\n"
        '<p style="writing-mode: vertical-rl;">A</p>\n'
        '<p style="text-orientation: upright;">B</p>\n'
    )

    assert "<style" not in cleaned
    assert 'style="color:red"' not in cleaned
    assert "style='font-size:12px'" not in cleaned
    assert "writing-mode" not in cleaned
    assert "text-orientation" not in cleaned
    assert '<div class="x">' in cleaned
    assert "<span>" in cleaned


def test_cleaner_adds_fallback_title_and_normalizes_special_quotes() -> None:
    cleaned = PdfMarkdownCleaner().clean("“买入” ‘测试’")

    assert cleaned.startswith("# 分析报告\n\n")
    assert '"买入"' in cleaned
    assert "'测试'" in cleaned


def test_cleaner_keeps_existing_heading_without_extra_fallback() -> None:
    cleaned = PdfMarkdownCleaner().clean("# 原始标题\n正文")

    assert cleaned.startswith("# 原始标题\n正文")
    assert cleaned.count("# 分析报告") == 0


def test_cleaner_does_not_change_repository_markdown_or_hash() -> None:
    repo = ReportRepository()
    original_markdown = (
        "<style>.x{writing-mode:vertical-rl;}</style>\n"
        "第一段---第二段\n"
        "等待...\n"
        "|------|\n"
        "|------|------|\n"
    )
    repo.save_succeeded_report(
        report_id="r-cleaner-hash",
        instrument_code="TSLA",
        market="US",
        title="TSLA 报告",
        markdown=original_markdown,
    )
    original_hash = repo.get_report("r-cleaner-hash").markdown_hash
    original_text_in_repo = repo.read_markdown("r-cleaner-hash")

    cleaned = PdfMarkdownCleaner().clean(original_markdown)

    assert cleaned != original_markdown
    assert repo.read_markdown("r-cleaner-hash") == original_text_in_repo
    assert repo.get_report("r-cleaner-hash").markdown_hash == original_hash
