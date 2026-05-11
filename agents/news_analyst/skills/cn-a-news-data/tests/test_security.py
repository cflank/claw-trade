from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest


def _load_security_module():
    module_path = Path(__file__).resolve().parents[1] / "scripts" / "security.py"
    spec = importlib.util.spec_from_file_location("cn_a_news_security", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


security = _load_security_module()


def test_sanitize_error_masks_sensitive_query_value():
    raw = "error url: https://example.com/api?token=abc123&lang=zh"
    sanitized = security.sanitize_error(raw)
    assert "token=***" in sanitized
    assert "token=abc123" not in sanitized
    assert "lang=zh" in sanitized


def test_sanitize_error_truncates_to_max_length_with_suffix():
    raw = "x" * 600
    sanitized = security.sanitize_error(raw)
    assert len(sanitized) <= 523
    assert sanitized.endswith("[truncated]")


@pytest.mark.parametrize(
    ("raw", "expected_masked", "leaked_value"),
    [
        ("provider failed secret=shhh", "secret=***", "secret=shhh"),
        ("provider failed key=abc", "key=***", "key=abc"),
        ("provider failed password=pw123", "password=***", "password=pw123"),
        ("provider failed bearer=opaque-token", "bearer=***", "bearer=opaque-token"),
        (
            "provider failed Authorization: Bearer token-12345",
            "Authorization: ***",
            "token-12345",
        ),
        (
            "provider failed with Bearer opaque-token",
            "Bearer ***",
            "opaque-token",
        ),
    ],
)
def test_sanitize_error_masks_expanded_sensitive_values(
    raw: str, expected_masked: str, leaked_value: str
):
    sanitized = security.sanitize_error(raw)
    assert expected_masked in sanitized
    assert leaked_value not in sanitized


def test_validate_external_url_rejects_non_http_scheme():
    result = security.validate_external_url("ftp://example.com/a")
    assert result.valid is False
    assert result.sanitized_url is None
    assert result.evidence_gap == "invalid_url"


def test_validate_external_url_rejects_control_characters():
    result = security.validate_external_url("https://example.com/a\x0bpath")
    assert result.valid is False
    assert result.sanitized_url is None
    assert result.evidence_gap == "invalid_url"
