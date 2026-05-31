from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

MIN_PDF_SIZE_BYTES = 512


@dataclass(frozen=True)
class PdfValidationEvidence:
    engine: str
    page_count: int
    extracted_text: str


@dataclass(frozen=True)
class PdfValidationResult:
    valid: bool
    reason: str | None = None
    evidence: PdfValidationEvidence | None = None


def validate_pdf_bytes(
    pdf_bytes: bytes,
    *,
    required_keywords: tuple[str, ...] = (),
    min_size_bytes: int = MIN_PDF_SIZE_BYTES,
) -> PdfValidationResult:
    if not pdf_bytes.startswith(b"%PDF-"):
        return PdfValidationResult(valid=False, reason="invalid pdf header")
    if len(pdf_bytes) < min_size_bytes:
        return PdfValidationResult(valid=False, reason=f"pdf too small: {len(pdf_bytes)} < {min_size_bytes}")

    extract = _extract_text_with_available_engine(pdf_bytes)
    if extract is None:
        return PdfValidationResult(valid=False, reason="unable to extract text from pdf with pdfplumber/pypdfium2")

    page_count, extracted_text, engine = extract
    if page_count < 1:
        return PdfValidationResult(valid=False, reason="pdf page count < 1")
    if _normalize_text(extracted_text) == "":
        return PdfValidationResult(valid=False, reason="pdf extracted text empty")

    normalized_text = _normalize_text(extracted_text)
    wanted = tuple(_normalize_text(item) for item in required_keywords if _normalize_text(item))
    if wanted and not any(keyword in normalized_text for keyword in wanted):
        return PdfValidationResult(valid=False, reason=f"pdf text missing required keywords: {wanted}")

    return PdfValidationResult(
        valid=True,
        evidence=PdfValidationEvidence(
            engine=engine,
            page_count=page_count,
            extracted_text=extracted_text,
        ),
    )


def validate_pdf_file(
    pdf_path: Path,
    *,
    required_keywords: tuple[str, ...] = (),
    min_size_bytes: int = MIN_PDF_SIZE_BYTES,
) -> PdfValidationResult:
    if not pdf_path.exists() or not pdf_path.is_file():
        return PdfValidationResult(valid=False, reason=f"pdf file not found: {pdf_path}")
    return validate_pdf_bytes(
        pdf_path.read_bytes(),
        required_keywords=required_keywords,
        min_size_bytes=min_size_bytes,
    )


def _extract_text_with_available_engine(pdf_bytes: bytes) -> tuple[int, str, str] | None:
    extracted = _extract_with_pdfplumber(pdf_bytes)
    if extracted is not None:
        return extracted[0], extracted[1], "pdfplumber"
    extracted = _extract_with_pypdfium2(pdf_bytes)
    if extracted is not None:
        return extracted[0], extracted[1], "pypdfium2"
    return None


def _extract_with_pdfplumber(pdf_bytes: bytes) -> tuple[int, str] | None:
    try:
        import pdfplumber
    except Exception:
        return None

    try:
        with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:  # type: ignore[arg-type]
            page_count = len(pdf.pages)
            texts = []
            for page in pdf.pages:
                text = page.extract_text() or ""
                texts.append(text)
            return page_count, "\n".join(texts)
    except Exception:
        return None


def _extract_with_pypdfium2(pdf_bytes: bytes) -> tuple[int, str] | None:
    try:
        import pypdfium2 as pdfium
    except Exception:
        return None

    try:
        doc = pdfium.PdfDocument(pdf_bytes)
        page_count = len(doc)
        texts = []
        for index in range(page_count):
            page = doc[index]
            text_page = page.get_textpage()
            texts.append(text_page.get_text_bounded() or "")
            text_page.close()
            page.close()
        doc.close()
        return page_count, "\n".join(texts)
    except Exception:
        return None


def _normalize_text(text: str) -> str:
    return " ".join(text.split()).strip().lower()
