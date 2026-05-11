from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from errors import E_PROFILE_RESOLVE_FAILED, NewsDataError  # noqa: E402
from profile_resolver import ApprovedProfileResolver  # noqa: E402


def _write_json(path: Path, payload: dict[str, object]) -> str:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return str(path)


def _write_alias_rules(path: Path, conflicts: list[str]) -> str:
    conflict_items = "\n".join(
        f'  - alias: "{alias}"\n    blocked_terms: []'
        for alias in conflicts
    )
    payload = (
        'version: "1"\n'
        "aliases: []\n"
        "conflicts:\n"
        f"{conflict_items}\n"
    )
    path.write_text(payload, encoding="utf-8")
    return str(path)


def test_resolve_prefers_runtime_profile_company_name_when_three_sources_have_values(tmp_path: Path) -> None:
    runtime_ref = _write_json(
        tmp_path / "runtime.json",
        {"company_name": "贵州茅台", "approved_aliases": ["茅台"], "approved_historical_names": ["贵州茅台酒股份"]},
    )
    fundamentals_ref = _write_json(tmp_path / "fundamentals.json", {"company_name": "茅台集团"})
    market_ref = _write_json(tmp_path / "market.json", {"company_name": "贵州茅台股份"})

    resolved = ApprovedProfileResolver().resolve(
        ticker="600519",
        run_id="run-1",
        stage="frontline",
        profile_ref=runtime_ref,
        fundamentals_ref=fundamentals_ref,
        market_ref=market_ref,
    )

    assert resolved.company_name == "贵州茅台"


def test_resolve_falls_back_to_fundamentals_sector_when_runtime_industry_is_empty(tmp_path: Path) -> None:
    runtime_ref = _write_json(
        tmp_path / "runtime.json",
        {
            "industry": "   ",
            "approved_aliases": ["茅台"],
            "approved_historical_names": ["贵州茅台酒股份"],
        },
    )
    fundamentals_ref = _write_json(tmp_path / "fundamentals.json", {"sector": "白酒"})

    resolved = ApprovedProfileResolver().resolve(
        ticker="600519",
        run_id="run-2",
        stage="frontline",
        profile_ref=runtime_ref,
        fundamentals_ref=fundamentals_ref,
        market_ref=None,
    )

    assert resolved.industry == "白酒"


def test_resolve_does_not_accept_unapproved_alias_from_worker_request(tmp_path: Path) -> None:
    worker_unapproved_alias = "未批准别名"
    runtime_ref = _write_json(
        tmp_path / "runtime.json",
        {"approved_aliases": ["茅台"], "approved_historical_names": ["贵州茅台酒股份"]},
    )
    fundamentals_ref = _write_json(tmp_path / "fundamentals.json", {"aliases": ["贵州茅台"]})
    market_ref = _write_json(tmp_path / "market.json", {"aliases": ["600519"]})

    resolved = ApprovedProfileResolver().resolve(
        ticker="600519",
        run_id="run-3",
        stage="frontline",
        profile_ref=runtime_ref,
        fundamentals_ref=fundamentals_ref,
        market_ref=market_ref,
    )

    assert worker_unapproved_alias not in resolved.approved_aliases
    assert resolved.approved_aliases == ["茅台", "贵州茅台", "600519"]


def test_resolve_deduplicates_aliases_and_removes_empty_items(tmp_path: Path) -> None:
    runtime_ref = _write_json(
        tmp_path / "runtime.json",
        {
            "company_name": "贵州茅台",
            "industry": "白酒",
            "approved_aliases": ["茅台", " 茅台 ", ""],
            "approved_historical_names": ["贵州茅台酒股份"],
        },
    )

    resolved = ApprovedProfileResolver().resolve(
        ticker="600519",
        run_id="run-5",
        stage="frontline",
        profile_ref=runtime_ref,
        fundamentals_ref=None,
        market_ref=None,
    )

    assert resolved.approved_aliases == ["茅台"]


def test_resolve_filters_conflict_alias_from_approved_aliases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    alias_rules_path = _write_alias_rules(tmp_path / "alias_rules.yaml", conflicts=["平安"])
    monkeypatch.setenv("CN_A_NEWS_ALIAS_CONFLICT_BLACKLIST_PATH", alias_rules_path)
    runtime_ref = _write_json(
        tmp_path / "runtime.json",
        {
            "company_name": "中国平安",
            "industry": "保险",
            "approved_aliases": ["平安", "中国平安"],
            "approved_historical_names": ["中国平安保险"],
        },
    )

    resolved = ApprovedProfileResolver().resolve(
        ticker="601318",
        run_id="run-6",
        stage="frontline",
        profile_ref=runtime_ref,
        fundamentals_ref=None,
        market_ref=None,
    )

    assert "平安" not in resolved.approved_aliases
    assert resolved.approved_aliases == ["中国平安"]


def test_resolve_marks_only_industry_missing_when_three_sources_have_no_industry(tmp_path: Path) -> None:
    runtime_ref = _write_json(
        tmp_path / "runtime.json",
        {
            "company_name": "贵州茅台",
            "approved_aliases": ["茅台"],
            "approved_historical_names": ["贵州茅台酒股份"],
        },
    )
    fundamentals_ref = _write_json(tmp_path / "fundamentals.json", {"company_name": "贵州茅台"})
    market_ref = _write_json(tmp_path / "market.json", {"name": "贵州茅台"})

    resolved = ApprovedProfileResolver().resolve(
        ticker="600519",
        run_id="run-7",
        stage="frontline",
        profile_ref=runtime_ref,
        fundamentals_ref=fundamentals_ref,
        market_ref=market_ref,
    )

    assert resolved.missing_fields == ["industry"]


def test_resolve_marks_list_field_missing_when_empty_after_dedupe_and_blacklist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    alias_rules_path = _write_alias_rules(tmp_path / "alias_rules.yaml", conflicts=["平安"])
    monkeypatch.setenv("CN_A_NEWS_ALIAS_CONFLICT_BLACKLIST_PATH", alias_rules_path)
    runtime_ref = _write_json(
        tmp_path / "runtime.json",
        {
            "company_name": "中国平安",
            "industry": "保险",
            "approved_aliases": ["平安", " 平安 ", ""],
            "approved_historical_names": [" ", ""],
        },
    )

    resolved = ApprovedProfileResolver().resolve(
        ticker="601318",
        run_id="run-8",
        stage="frontline",
        profile_ref=runtime_ref,
        fundamentals_ref=None,
        market_ref=None,
    )

    assert resolved.approved_aliases == []
    assert resolved.approved_historical_names == []
    assert resolved.missing_fields == ["approved_aliases", "approved_historical_names"]


def test_resolve_raises_profile_resolve_failed_when_artifact_ref_is_unreadable(tmp_path: Path) -> None:
    runtime_ref = str(tmp_path / "missing_runtime.json")

    with pytest.raises(NewsDataError) as exc_info:
        ApprovedProfileResolver().resolve(
            ticker="600519",
            run_id="run-4",
            stage="frontline",
            profile_ref=runtime_ref,
            fundamentals_ref=None,
            market_ref=None,
        )

    assert exc_info.value.code == E_PROFILE_RESOLVE_FAILED
