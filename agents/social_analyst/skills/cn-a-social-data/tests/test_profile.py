from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from profile import (  # noqa: E402
    SOCIAL_INVALID_INPUT,
    SOCIAL_INVALID_TICKER_MARKET_PREFIX,
    SOCIAL_PROFILE_REF_INVALID,
    NormalizeCnATicker,
    ResolveDateWindow,
    SocialToolInput,
    SocialProfileError,
    ToEastmoneySymbol,
    build_query_keywords,
    resolve_social_target_profile,
)


def test_to_eastmoney_symbol_for_600519_returns_expected_symbol() -> None:
    normalized = NormalizeCnATicker("600519")

    assert ToEastmoneySymbol(normalized) == "100.600519"


@pytest.mark.parametrize(
    ("raw_ticker", "expected_ticker", "expected_symbol"),
    [
        ("600519.SH", "600519.SH", "100.600519"),
        ("SH600519", "600519.SH", "100.600519"),
        ("000001.SZ", "000001.SZ", "0.000001"),
        ("SZ000001", "000001.SZ", "0.000001"),
    ],
)
def test_normalize_cn_a_ticker_supports_required_formats(
    raw_ticker: str,
    expected_ticker: str,
    expected_symbol: str,
) -> None:
    normalized = NormalizeCnATicker(raw_ticker)

    assert normalized.ticker == expected_ticker
    assert ToEastmoneySymbol(normalized) == expected_symbol


def test_normalize_cn_a_ticker_for_sz000001_returns_expected_values() -> None:
    normalized = NormalizeCnATicker("SZ000001")

    assert normalized.ticker == "000001.SZ"
    assert ToEastmoneySymbol(normalized) == "0.000001"


def test_normalize_cn_a_ticker_raises_when_market_prefix_unknown() -> None:
    with pytest.raises(SocialProfileError) as exc_info:
        NormalizeCnATicker("12345")

    assert exc_info.value.code == SOCIAL_INVALID_TICKER_MARKET_PREFIX


def test_resolve_date_window_raises_when_start_date_later_than_end_date() -> None:
    with pytest.raises(SocialProfileError) as exc_info:
        ResolveDateWindow(
            start_date="2026-05-08",
            end_date="2026-05-07",
            current_time="2026-05-07T10:30:00+08:00",
        )

    assert exc_info.value.code == SOCIAL_INVALID_INPUT


def test_resolve_date_window_uses_run_date_when_dates_missing() -> None:
    window = ResolveDateWindow(
        start_date=None,
        end_date=None,
        current_time="2026-05-07T10:30:00+08:00",
    )

    assert window.end_date == "2026-05-07"
    assert window.start_date == "2026-04-30"
    assert window.as_of_date == "2026-05-07"


def test_resolve_social_target_profile_loads_approved_aliases_from_profile_ref(tmp_path: Path) -> None:
    payload = {
        "approved": True,
        "company_name": "贵州茅台",
        "approved_aliases": ["茅台", "贵州茅台"],
    }
    ref_path = tmp_path / "approved-profile.json"
    ref_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    profile = resolve_social_target_profile(
        SocialToolInput(
            ticker="600519",
            market="CN_A",
            approved_artifact_refs=(
                {
                    "kind": "profile",
                    "artifact_ref": str(ref_path),
                    "content_hash": _sha256_json(payload),
                },
            ),
        )
    )

    assert [alias.alias for alias in profile.approved_aliases] == ["茅台", "贵州茅台"]


def test_resolve_social_target_profile_raises_when_ref_hash_mismatch(tmp_path: Path) -> None:
    payload = {
        "approved": True,
        "approved_aliases": ["茅台"],
    }
    ref_path = tmp_path / "approved-profile.json"
    ref_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(SocialProfileError) as exc_info:
        resolve_social_target_profile(
            SocialToolInput(
                ticker="600519",
                market="CN_A",
                approved_artifact_refs=(
                    {
                        "kind": "profile",
                        "artifact_ref": str(ref_path),
                        "content_hash": "sha256:" + ("0" * 64),
                    },
                ),
            )
        )

    assert exc_info.value.code == SOCIAL_PROFILE_REF_INVALID


def test_resolve_social_target_profile_returns_warning_when_company_and_alias_missing() -> None:
    profile = resolve_social_target_profile(
        SocialToolInput(
            ticker="600519",
            market="CN_A",
            company_name=None,
            approved_artifact_refs=(),
        )
    )

    assert profile.ticker == "600519.SH"
    assert profile.warnings == ("company_name_and_alias_missing",)


def test_build_query_keywords_ignores_unapproved_worker_alias_field() -> None:
    profile = resolve_social_target_profile(
        SocialToolInput(
            ticker="600519.SH",
            market="CN_A",
            company_name="贵州茅台",
            industry="白酒",
            aliases=("飞天茅台",),
            approved_artifact_refs=(),
        )
    )

    keywords = build_query_keywords(profile)

    assert "飞天茅台" not in keywords
    assert "白酒" not in keywords


def _sha256_json(payload: object) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()
