from __future__ import annotations

import sys
from types import SimpleNamespace

from claw_trade.ui_backend import pdf_validation
from claw_trade.ui_backend.pdf_validation import validate_pdf_bytes, validate_pdf_file


def _pdf_bytes() -> bytes:
    return b"%PDF-1.7\n" + (b"Z" * 700)


def test_validate_pdf_bytes_fails_when_header_invalid() -> None:
    result = validate_pdf_bytes(b"NOT_PDF", min_size_bytes=8)
    assert result.valid is False
    assert result.reason == "invalid pdf header"


def test_validate_pdf_bytes_fails_when_too_small() -> None:
    result = validate_pdf_bytes(b"%PDF-1.4\ntiny", min_size_bytes=100)
    assert result.valid is False
    assert "pdf too small" in (result.reason or "")


def test_validate_pdf_bytes_uses_pdfplumber_and_checks_keyword(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    class _Page:
        def __init__(self, text: str) -> None:
            self._text = text

        def extract_text(self) -> str:
            return self._text

    class _Pdf:
        def __init__(self) -> None:
            self.pages = [_Page("TSLA 报告 正文内容")]

        def __enter__(self) -> "_Pdf":
            return self

        def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
            return None

    monkeypatch.setitem(sys.modules, "pdfplumber", SimpleNamespace(open=lambda _stream: _Pdf()))

    result = validate_pdf_bytes(_pdf_bytes(), required_keywords=("TSLA",))
    assert result.valid is True
    assert result.evidence is not None
    assert result.evidence.engine == "pdfplumber"
    assert result.evidence.page_count == 1


def test_validate_pdf_bytes_falls_back_to_pypdfium2(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.delitem(sys.modules, "pdfplumber", raising=False)

    class _TextPage:
        def get_text_bounded(self) -> str:
            return "关键字 测试正文"

        def close(self) -> None:
            return None

    class _Page:
        def get_textpage(self) -> _TextPage:
            return _TextPage()

        def close(self) -> None:
            return None

    class _PdfDocument:
        def __init__(self, _payload: bytes) -> None:
            self._pages = [_Page()]

        def __len__(self) -> int:
            return len(self._pages)

        def __getitem__(self, index: int) -> _Page:
            return self._pages[index]

        def close(self) -> None:
            return None

    monkeypatch.setitem(sys.modules, "pypdfium2", SimpleNamespace(PdfDocument=_PdfDocument))

    result = validate_pdf_bytes(_pdf_bytes(), required_keywords=("关键字",))
    assert result.valid is True
    assert result.evidence is not None
    assert result.evidence.engine == "pypdfium2"


def test_validate_pdf_file_reads_bytes_and_applies_checks(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(_pdf_bytes())
    monkeypatch.setattr(
        pdf_validation,
        "_extract_text_with_available_engine",
        lambda _bytes: (1, "TSLA 报告 正文", "stub"),
    )

    result = validate_pdf_file(pdf_path, required_keywords=("TSLA",), min_size_bytes=512)
    assert result.valid is True
    assert result.evidence is not None
    assert result.evidence.page_count == 1


def test_validate_pdf_bytes_fails_when_required_keyword_missing(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        pdf_validation,
        "_extract_text_with_available_engine",
        lambda _bytes: (1, "只有正文", "stub"),
    )

    result = validate_pdf_bytes(_pdf_bytes(), required_keywords=("TSLA",))
    assert result.valid is False
    assert "missing required keywords" in (result.reason or "")


def test_validate_pdf_bytes_fails_when_page_count_zero(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        pdf_validation,
        "_extract_text_with_available_engine",
        lambda _bytes: (0, "有文字", "stub"),
    )

    result = validate_pdf_bytes(_pdf_bytes())
    assert result.valid is False
    assert result.reason == "pdf page count < 1"


def test_validate_pdf_bytes_fails_when_extracted_text_empty(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        pdf_validation,
        "_extract_text_with_available_engine",
        lambda _bytes: (1, " \n\t ", "stub"),
    )

    result = validate_pdf_bytes(_pdf_bytes())
    assert result.valid is False
    assert result.reason == "pdf extracted text empty"
