from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from tempfile import NamedTemporaryFile
from typing import Protocol

import markdown as markdown_lib
from claw_trade.ui_backend.pdf_markdown_cleaner import PdfMarkdownCleaner

_MARKDOWN_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_BLOCKED_IMAGE_TARGET = "#blocked-local-image"


@dataclass(frozen=True)
class PdfHtmlRenderResult:
    html: str
    local_image_uris: tuple[str, ...]
    remote_image_targets: tuple[str, ...]


@dataclass(frozen=True)
class PdfHtmlRenderer:
    title: str = "分析报告"

    def render(self, markdown: str, *, report_asset_dir: Path | None = None) -> PdfHtmlRenderResult:
        local_uris: list[str] = []
        remote_targets: list[str] = []
        markdown_for_html = self._rewrite_local_markdown_images(
            markdown,
            report_asset_dir=report_asset_dir,
            local_uris=local_uris,
            remote_targets=remote_targets,
        )
        html_body = markdown_lib.markdown(
            markdown_for_html,
            extensions=[
                "markdown.extensions.tables",
                "markdown.extensions.fenced_code",
                "markdown.extensions.nl2br",
            ],
        )
        return PdfHtmlRenderResult(
            html=self._wrap_html_document(html_body),
            local_image_uris=tuple(local_uris),
            remote_image_targets=tuple(remote_targets),
        )

    def _rewrite_local_markdown_images(
        self,
        markdown: str,
        *,
        report_asset_dir: Path | None,
        local_uris: list[str],
        remote_targets: list[str],
    ) -> str:
        if not markdown:
            return markdown

        def replace(match: re.Match[str]) -> str:
            alt = match.group(1)
            raw_target = match.group(2).strip()
            target, suffix = _split_markdown_target(raw_target)
            if _is_remote_target(target):
                remote_targets.append(target)
                return match.group(0)
            if _should_block_local_target(target):
                return f"![{alt}]({_BLOCKED_IMAGE_TARGET}{suffix})"
            if report_asset_dir is None:
                return match.group(0)
            resolved = _resolve_local_asset_uri(report_asset_dir, target)
            if resolved is None:
                return match.group(0)
            local_uris.append(resolved)
            return f"![{alt}]({resolved}{suffix})"

        return _MARKDOWN_IMAGE_RE.sub(replace, markdown)

    def _wrap_html_document(self, html_body: str) -> str:
        return f"""<!DOCTYPE html>
<html lang="zh-CN" dir="ltr">
<head>
  <meta charset="UTF-8">
  <title>{self.title}</title>
  <style>
    html, body, p, div, span, td, th, li {{
      writing-mode: horizontal-tb;
      text-orientation: mixed;
      direction: ltr;
    }}
    body {{
      font-family: "Noto Sans CJK SC", "Microsoft YaHei", "SimHei", "Arial", sans-serif;
      line-height: 1.8;
      color: #333;
      margin: 20mm;
      padding: 0;
      background: white;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      page-break-inside: auto;
    }}
    thead {{
      display: table-header-group;
    }}
    tr {{
      page-break-inside: avoid;
      page-break-after: auto;
    }}
    th, td {{
      border: 1px solid #ddd;
      padding: 8px 10px;
      text-align: left;
      word-break: break-word;
    }}
    img {{
      max-width: 100%;
      height: auto;
      page-break-inside: avoid;
    }}
    pre, code {{
      white-space: pre-wrap;
      word-wrap: break-word;
      word-break: break-word;
    }}
    blockquote, pre, img {{
      page-break-inside: avoid;
    }}
    p, li {{
      orphans: 3;
      widows: 3;
    }}
    @page {{
      size: A4;
      margin: 20mm;
    }}
  </style>
</head>
<body>
{html_body}
</body>
</html>
"""


class PdfKitRenderer:
    def __init__(
        self,
        *,
        cleaner: PdfMarkdownCleaner | None = None,
        html_renderer: PdfHtmlRenderer | None = None,
    ) -> None:
        self._cleaner = cleaner or PdfMarkdownCleaner()
        self._html_renderer = html_renderer or PdfHtmlRenderer()

    def render(self, markdown: str, *, report_asset_dir: Path | None = None) -> bytes:
        cleaned = self._cleaner.clean(markdown)
        rendered_html = self._html_renderer.render(cleaned, report_asset_dir=report_asset_dir)
        return _render_pdf_with_pdfkit(rendered_html.html)


@dataclass(frozen=True)
class PandocRenderAttempt:
    engine: str
    exception_type: str | None
    message: str | None
    output_path: str
    output_exists: bool
    output_size: int


class PandocRenderError(RuntimeError):
    def __init__(self, attempts: tuple[PandocRenderAttempt, ...]) -> None:
        self.attempts = attempts
        super().__init__(self._build_message())

    def _build_message(self) -> str:
        if not self.attempts:
            return "pandoc fallback render failed with no attempts"
        lines = ["pandoc fallback render failed"]
        for item in self.attempts:
            lines.append(
                f"{item.engine}: {item.exception_type or 'unknown'}: {item.message or ''} "
                f"(path={item.output_path} exists={item.output_exists} size={item.output_size})"
            )
        return "; ".join(lines)


class _PandocModule(Protocol):
    def convert_text(  # type: ignore[no-untyped-def]
        self,
        source: str,
        to: str,
        format: str,
        outputfile: str,
        extra_args: list[str],
    ) -> object: ...


class _PdfRenderer(Protocol):
    def render(self, markdown: str, *, report_asset_dir: Path | None = None) -> bytes: ...


class PandocFallbackRenderer:
    def __init__(
        self,
        *,
        cleaner: PdfMarkdownCleaner | None = None,
        pypandoc_module: _PandocModule | None = None,
    ) -> None:
        self._cleaner = cleaner or PdfMarkdownCleaner()
        self._pypandoc_module = pypandoc_module
        self._last_attempts: tuple[PandocRenderAttempt, ...] = ()

    @property
    def last_attempts(self) -> tuple[PandocRenderAttempt, ...]:
        return self._last_attempts

    def render(self, markdown: str, *, report_asset_dir: Path | None = None) -> bytes:
        _ = report_asset_dir
        cleaned = self._cleaner.clean(markdown)
        attempts: list[PandocRenderAttempt] = []

        for engine in ("wkhtmltopdf", "weasyprint", None):
            output_path = ""
            try:
                with NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                    output_path = tmp.name
                extra_args = ["--from=markdown-yaml_metadata_block"]
                if engine is not None:
                    extra_args.append(f"--pdf-engine={engine}")
                self._pandoc().convert_text(
                    cleaned,
                    "pdf",
                    format="markdown",
                    outputfile=output_path,
                    extra_args=extra_args,
                )
                output_exists, output_size = _read_output_status(output_path)
                if not output_exists or output_size < 1:
                    raise RuntimeError("pandoc output missing or empty")
                pdf_bytes = Path(output_path).read_bytes()
                attempts.append(
                    PandocRenderAttempt(
                        engine=_normalize_engine_name(engine),
                        exception_type=None,
                        message=None,
                        output_path=output_path,
                        output_exists=output_exists,
                        output_size=output_size,
                    )
                )
                self._last_attempts = tuple(attempts)
                return pdf_bytes
            except Exception as exc:  # noqa: PERF203
                output_exists, output_size = _read_output_status(output_path)
                attempts.append(
                    PandocRenderAttempt(
                        engine=_normalize_engine_name(engine),
                        exception_type=type(exc).__name__,
                        message=str(exc),
                        output_path=output_path,
                        output_exists=output_exists,
                        output_size=output_size,
                    )
                )
            finally:
                if output_path:
                    Path(output_path).unlink(missing_ok=True)

        self._last_attempts = tuple(attempts)
        raise PandocRenderError(self._last_attempts)

    def _pandoc(self) -> _PandocModule:
        if self._pypandoc_module is not None:
            return self._pypandoc_module
        import pypandoc

        return pypandoc


class PdfKitWithPandocFallbackRenderer:
    def __init__(
        self,
        *,
        primary_renderer: _PdfRenderer | None = None,
        pandoc_renderer: PandocFallbackRenderer | None = None,
    ) -> None:
        self._primary_renderer = primary_renderer or PdfKitRenderer()
        self._pandoc_renderer = pandoc_renderer or PandocFallbackRenderer()

    @property
    def last_pandoc_attempts(self) -> tuple[PandocRenderAttempt, ...]:
        return self._pandoc_renderer.last_attempts

    def render(self, markdown: str, *, report_asset_dir: Path | None = None) -> bytes:
        try:
            return self._primary_renderer.render(markdown, report_asset_dir=report_asset_dir)
        except Exception:
            return self._pandoc_renderer.render(markdown, report_asset_dir=report_asset_dir)


def _render_pdf_with_pdfkit(html: str) -> bytes:
    import pdfkit

    pdf_bytes = pdfkit.from_string(
        html,
        False,
        options={
            "encoding": "UTF-8",
            "enable-local-file-access": None,
            "page-size": "A4",
            "margin-top": "20mm",
            "margin-right": "20mm",
            "margin-bottom": "20mm",
            "margin-left": "20mm",
        },
    )
    if isinstance(pdf_bytes, bytes):
        return pdf_bytes
    if isinstance(pdf_bytes, str):
        return pdf_bytes.encode("utf-8")
    raise RuntimeError(f"pdfkit.from_string returned unsupported payload type: {type(pdf_bytes)!r}")


def _normalize_engine_name(engine: str | None) -> str:
    return engine or "default"


def _read_output_status(output_path: str) -> tuple[bool, int]:
    if not output_path:
        return False, 0
    path = Path(output_path)
    if not path.exists() or not path.is_file():
        return False, 0
    return True, path.stat().st_size


def _split_markdown_target(raw_target: str) -> tuple[str, str]:
    if " " not in raw_target:
        return raw_target, ""
    target, suffix = raw_target.split(maxsplit=1)
    return target, f" {suffix}"


def _is_remote_target(target: str) -> bool:
    normalized = target.strip().replace("\\", "/")
    return normalized.startswith("http://") or normalized.startswith("https://")


def _should_block_local_target(target: str) -> bool:
    normalized = target.strip().replace("\\", "/")
    if not normalized:
        return False
    if normalized.startswith("/") or re.match(r"^[a-zA-Z]:[\\/]", target):
        return True
    if "://" in normalized or normalized.startswith("data:"):
        return False
    parts = [part for part in PurePosixPath(normalized).parts if part not in ("", ".")]
    return any(part == ".." for part in parts)


def _resolve_local_asset_uri(report_asset_dir: Path, target: str) -> str | None:
    cleaned = target.strip().replace("\\", "/")
    if not cleaned:
        return None
    if cleaned.startswith("./"):
        cleaned = cleaned[2:]
    if cleaned.startswith("assets/"):
        cleaned = cleaned[len("assets/") :]
    asset_root = report_asset_dir.resolve()
    candidate = (asset_root / cleaned).resolve()
    try:
        candidate.relative_to(asset_root)
    except ValueError:
        return None
    if not candidate.exists() or not candidate.is_file():
        return None
    return candidate.as_uri()
