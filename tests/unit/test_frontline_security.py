from __future__ import annotations

import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_PYTHON_ROOT = REPO_ROOT / "openclaw_plugins" / "claw-trade-frontline-tools" / "python"
if str(SHARED_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(SHARED_PYTHON_ROOT))

from frontline_data_pack.errors import (  # noqa: E402
    L2_TARGET_INVALID,
    MARKET_INVALID,
    MONGO_CONFIG_INVALID,
    TOOL_CONTEXT_INCOMPLETE,
    TOOL_PARAMS_INVALID,
    FrontlineValidationError,
)
from frontline_data_pack.security import (  # noqa: E402
    MAX_PROVIDER_ERROR_SUMMARY_LENGTH,
    build_cn_a_provider_query,
    normalize_ticker,
    redact_secret,
    summarize_provider_error,
    validate_l2_target_path,
    validate_mongodb_uri,
    validate_runtime_context,
    validate_tool_params,
)


def test_t_sec_001_normalize_ticker_accepts_cn_a_variants() -> None:
    assert normalize_ticker("600519") == "600519.SH"
    assert normalize_ticker("600519.SH") == "600519.SH"
    assert normalize_ticker("SH600519") == "600519.SH"


def test_t_sec_001_provider_query_rejects_non_cn_a_market() -> None:
    with pytest.raises(FrontlineValidationError) as error:
        build_cn_a_provider_query(
            {
                "ticker": "600519",
                "market": "US",
                "start_date": "2026-01-01",
                "end_date": "2026-02-01",
            }
        )

    assert error.value.code == MARKET_INVALID


def test_t_sec_001_l2_relative_path_rejects_parent_escape() -> None:
    with pytest.raises(FrontlineValidationError) as error:
        validate_l2_target_path("../a.json")

    assert error.value.code == L2_TARGET_INVALID


def test_t_sec_001_redact_secret_masks_mongodb_credentials_and_query_secret() -> None:
    message = "boom mongodb://user:pass@host/db?authSource=admin&key=abc123"
    redacted = redact_secret(message)

    assert "user" not in redacted
    assert "pass" not in redacted
    assert "abc123" not in redacted
    assert "mongodb://<redacted-url>" in redacted


def test_t_sec_001_provider_error_summary_is_redacted_and_capped_to_500_chars() -> None:
    raw = "token=super_secret_token " + ("x" * 800)
    summary = summarize_provider_error(raw)

    assert len(summary) <= MAX_PROVIDER_ERROR_SUMMARY_LENGTH
    assert "super_secret_token" not in summary


def test_t_sec_001_provider_error_summary_redacts_full_url_host_path_and_query() -> None:
    raw = "provider failed token=abc https://user:pass@example.com/path?token=abc&foo=bar"
    summary = summarize_provider_error(raw)

    assert "provider failed" in summary
    assert "token=***" in summary
    assert "example.com" not in summary
    assert "/path" not in summary
    assert "foo=bar" not in summary
    assert "abc" not in summary
    assert "user" not in summary
    assert "pass" not in summary
    assert "<redacted-url>" in summary


def test_validate_tool_params_rejects_non_object() -> None:
    with pytest.raises(FrontlineValidationError) as error:
        validate_tool_params(["not", "an", "object"])

    assert error.value.code == TOOL_PARAMS_INVALID


def test_validate_runtime_context_rejects_missing_required_field() -> None:
    with pytest.raises(FrontlineValidationError) as error:
        validate_runtime_context(
            {
                "run_id": "run-1",
                "stage": "frontline",
                "worker_id": "market_analyst",
                "call_id": "call-1",
                "dispatch_id": "dispatch-1",
                "tool_name": "market_market_data_pack",
                "current_time": "2026-05-08T11:00:00Z",
            }
        )

    assert error.value.code == TOOL_CONTEXT_INCOMPLETE


def test_validate_mongodb_uri_requires_database_path() -> None:
    with pytest.raises(FrontlineValidationError) as error:
        validate_mongodb_uri("mongodb://localhost:27017")

    assert error.value.code == MONGO_CONFIG_INVALID
