from __future__ import annotations

import importlib.util
import subprocess
from dataclasses import dataclass
from typing import Literal, Protocol

CapabilityCategory = Literal["primary", "compatibility", "optional"]


@dataclass(frozen=True)
class PdfRuntimeCapability:
    name: str
    category: CapabilityCategory
    available: bool
    required: bool
    detail: str | None = None


@dataclass(frozen=True)
class PdfRuntimeCapabilities:
    items: tuple[PdfRuntimeCapability, ...]

    @property
    def primary_ready(self) -> bool:
        return all(item.available for item in self.items if item.category == "primary" and item.required)

    def by_category(self, category: CapabilityCategory) -> tuple[PdfRuntimeCapability, ...]:
        return tuple(item for item in self.items if item.category == category)

    def missing_primary(self) -> tuple[PdfRuntimeCapability, ...]:
        return tuple(item for item in self.items if item.category == "primary" and item.required and not item.available)


class RuntimeProbe(Protocol):
    def module_available(self, module_name: str) -> bool: ...

    def command_output(self, args: list[str]) -> tuple[bool, str]: ...


class _SystemRuntimeProbe:
    def module_available(self, module_name: str) -> bool:
        return importlib.util.find_spec(module_name) is not None

    def command_output(self, args: list[str]) -> tuple[bool, str]:
        try:
            completed = subprocess.run(args, capture_output=True, text=True, check=False)
        except Exception as exc:
            return False, str(exc)
        output = (completed.stdout or completed.stderr or "").strip()
        return completed.returncode == 0, output


def detect_pdf_runtime_capabilities(probe: RuntimeProbe | None = None) -> PdfRuntimeCapabilities:
    runtime_probe = probe or _SystemRuntimeProbe()
    checks: list[PdfRuntimeCapability] = [
        _module_check(runtime_probe, module_name="markdown", label="python:markdown", category="primary", required=True),
        _module_check(runtime_probe, module_name="pdfkit", label="python:pdfkit", category="primary", required=True),
        _command_check(runtime_probe, args=["wkhtmltopdf", "--version"], label="bin:wkhtmltopdf", category="primary", required=True),
        _command_check(runtime_probe, args=["fc-match", "--version"], label="bin:fontconfig(fc-match)", category="primary", required=True),
        _noto_font_check(runtime_probe),
        _module_check(runtime_probe, module_name="pypandoc", label="python:pypandoc", category="compatibility", required=False),
        _command_check(runtime_probe, args=["pandoc", "--version"], label="bin:pandoc", category="compatibility", required=False),
        _module_check(runtime_probe, module_name="weasyprint", label="python:weasyprint", category="optional", required=False),
    ]
    return PdfRuntimeCapabilities(items=tuple(checks))


def _module_check(
    probe: RuntimeProbe,
    *,
    module_name: str,
    label: str,
    category: CapabilityCategory,
    required: bool,
) -> PdfRuntimeCapability:
    available = probe.module_available(module_name)
    return PdfRuntimeCapability(
        name=label,
        category=category,
        available=available,
        required=required,
        detail=None if available else f"module {module_name} not importable",
    )


def _command_check(
    probe: RuntimeProbe,
    *,
    args: list[str],
    label: str,
    category: CapabilityCategory,
    required: bool,
) -> PdfRuntimeCapability:
    ok, output = probe.command_output(args)
    detail = None if ok else _trim_detail(output)
    return PdfRuntimeCapability(name=label, category=category, available=ok, required=required, detail=detail)


def _noto_font_check(probe: RuntimeProbe) -> PdfRuntimeCapability:
    ok, output = probe.command_output(["fc-match", "-f", "%{family}\n", "Noto Sans CJK SC"])
    normalized = output.lower()
    available = ok and "noto" in normalized and "cjk" in normalized
    detail: str | None = None
    if not available:
        detail = _trim_detail(output) or "fc-match did not resolve Noto CJK family"
    return PdfRuntimeCapability(
        name="font:noto-cjk",
        category="primary",
        available=available,
        required=True,
        detail=detail,
    )


def _trim_detail(text: str) -> str:
    clean = " ".join(text.split())
    if len(clean) <= 180:
        return clean
    return clean[:177] + "..."
