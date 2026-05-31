from __future__ import annotations

from claw_trade.ui_backend.pdf_renderer import PdfHtmlRenderer


def test_pdf_html_renderer_generates_zh_cn_a4_html_template() -> None:
    markdown = (
        "# 标题\n\n"
        "第一行\n第二行\n\n"
        "| 列1 | 列2 |\n"
        "| --- | --- |\n"
        "| A | B |\n\n"
        "> 引用\n\n"
        "```python\nprint('ok')\n```\n\n"
        "![图表](assets/chart.png)\n"
    )

    result = PdfHtmlRenderer().render(markdown)
    html = result.html

    assert "<html lang=\"zh-CN\" dir=\"ltr\">" in html
    assert "<meta charset=\"UTF-8\">" in html
    assert "\"Noto Sans CJK SC\", \"Microsoft YaHei\", \"SimHei\"" in html
    assert "@page {" in html
    assert "size: A4;" in html
    assert "margin: 20mm;" in html
    assert "writing-mode: horizontal-tb;" in html
    assert "text-orientation: mixed;" in html
    assert "page-break-inside: auto;" in html
    assert "display: table-header-group;" in html
    assert "max-width: 100%;" in html
    assert "white-space: pre-wrap;" in html
    assert "word-wrap: break-word;" in html
    assert "<table>" in html
    assert "<pre><code" in html
    assert "<blockquote>" in html
    assert "<br" in html


def test_pdf_html_renderer_resolves_local_assets_to_file_uri(tmp_path) -> None:  # type: ignore[no-untyped-def]
    asset_dir = tmp_path / "reports" / "assets"
    asset_dir.mkdir(parents=True)
    chart = asset_dir / "chart.png"
    chart.write_bytes(b"\x89PNG\r\n\x1a\n")

    markdown = "![图表](assets/chart.png)"
    result = PdfHtmlRenderer().render(markdown, report_asset_dir=asset_dir)

    assert result.local_image_uris == (chart.as_uri(),)
    assert chart.as_uri() in result.html


def test_pdf_html_renderer_records_remote_images_without_local_success() -> None:
    remote = "https://example.com/chart.png"
    result = PdfHtmlRenderer().render(f"![远程图]({remote})")

    assert result.local_image_uris == ()
    assert result.remote_image_targets == (remote,)
    assert remote in result.html


def test_pdf_html_renderer_blocks_local_path_escape(tmp_path) -> None:  # type: ignore[no-untyped-def]
    asset_dir = tmp_path / "reports" / "assets"
    asset_dir.mkdir(parents=True)

    result = PdfHtmlRenderer().render("![逃逸](../../etc/passwd)", report_asset_dir=asset_dir)

    assert result.local_image_uris == ()
    assert "../../etc/passwd" not in result.html
    assert "#blocked-local-image" in result.html


def test_pdf_html_renderer_blocks_absolute_local_path() -> None:
    result = PdfHtmlRenderer().render("![绝对路径](/etc/passwd)")

    assert result.local_image_uris == ()
    assert result.remote_image_targets == ()
    assert "/etc/passwd" not in result.html
    assert "#blocked-local-image" in result.html


def test_pdf_html_renderer_does_not_rewrite_or_record_missing_local_asset(tmp_path) -> None:  # type: ignore[no-untyped-def]
    asset_dir = tmp_path / "reports" / "assets"
    asset_dir.mkdir(parents=True)
    markdown = "![缺失](assets/missing.png)"

    result = PdfHtmlRenderer().render(markdown, report_asset_dir=asset_dir)

    assert result.local_image_uris == ()
    assert result.remote_image_targets == ()
    assert "assets/missing.png" in result.html
    assert "file://" not in result.html
