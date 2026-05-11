from __future__ import annotations

import json
from pathlib import Path
import sys

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from integration_validate_600519 import (  # noqa: E402
    _extract_accepted_news,
    _validate_accepted_news_audit,
    _validate_p0_failure,
    _validate_visible_tools,
)


def test_validate_visible_tools_passes_for_pack_tool_only() -> None:
    payload = {
        "source": "provider_request",
        "tools": [
            {"name": "news_news_data_pack"},
        ],
    }
    blocking_reasons: list[str] = []
    result = _validate_visible_tools(payload, blocking_reasons)
    assert result["ok"] is True
    assert blocking_reasons == []


def test_validate_visible_tools_fails_for_extra_tool() -> None:
    payload = {
        "source": "provider_request",
        "tools": [
            {"name": "news_news_data_pack"},
            {"name": "company_news"},
        ],
    }
    blocking_reasons: list[str] = []
    result = _validate_visible_tools(payload, blocking_reasons)
    assert result["ok"] is False
    assert "company_news" in result["extra"]
    assert any("多出" in reason for reason in blocking_reasons)


def test_validate_p0_failure_rejects_directional_brief() -> None:
    pack_payload = {
        "ok": False,
        "quality": {"status": "failed"},
        "reader_brief": "资料缺失，建议买入。",
    }
    blocking_reasons: list[str] = []
    result = _validate_p0_failure(pack_payload, blocking_reasons)
    assert result["checked"] is True
    assert result["ok"] is False
    assert "买入" in result["directional_terms"]
    assert any("方向性判断词" in reason for reason in blocking_reasons)


def test_validate_accepted_news_audit_fails_when_missing_without_create_flag(tmp_path: Path) -> None:
    call_dir = tmp_path / "run" / "frontline" / "news_analyst" / "call-1"
    call_dir.mkdir(parents=True, exist_ok=True)
    pack_payload = {
        "data": {
            "company_news": [
                {
                    "news_id": "n1",
                    "title": "A",
                    "source": "s",
                    "publish_time": "2026-05-06T10:00:00+08:00",
                    "data_source": "akshare.stock_news_em",
                    "match_type": "ticker_exact",
                    "match_confidence": "high",
                    "content_hash": "h1",
                }
            ]
        }
    }
    accepted = _extract_accepted_news(pack_payload)
    checked_paths: list[str] = []
    blocking_reasons: list[str] = []
    status = _validate_accepted_news_audit(
        call_dir=call_dir,
        sample={
            "ticker": "600519",
            "company_name": "贵州茅台",
            "market": "CN_A",
            "start_date": "2026-04-30",
            "end_date": "2026-05-06",
        },
        accepted_news=accepted,
        create_if_missing=False,
        checked_paths=checked_paths,
        blocking_reasons=blocking_reasons,
    )
    assert status["ok"] is False
    assert status["created"] is False
    assert not (call_dir / "accepted_news_list.json").exists()
    assert any("accepted news list 不存在" in reason for reason in blocking_reasons)


def test_validate_accepted_news_audit_writes_when_missing_with_create_flag(tmp_path: Path) -> None:
    call_dir = tmp_path / "run" / "frontline" / "news_analyst" / "call-1"
    call_dir.mkdir(parents=True, exist_ok=True)
    pack_payload = {
        "data": {
            "company_news": [
                {
                    "news_id": "n1",
                    "title": "A",
                    "source": "s",
                    "publish_time": "2026-05-06T10:00:00+08:00",
                    "data_source": "akshare.stock_news_em",
                    "match_type": "ticker_exact",
                    "match_confidence": "high",
                    "content_hash": "h1",
                }
            ]
        }
    }
    accepted = _extract_accepted_news(pack_payload)
    checked_paths: list[str] = []
    blocking_reasons: list[str] = []
    status = _validate_accepted_news_audit(
        call_dir=call_dir,
        sample={
            "ticker": "600519",
            "company_name": "贵州茅台",
            "market": "CN_A",
            "start_date": "2026-04-30",
            "end_date": "2026-05-06",
        },
        accepted_news=accepted,
        create_if_missing=True,
        checked_paths=checked_paths,
        blocking_reasons=blocking_reasons,
    )
    assert status["ok"] is True
    assert status["created"] is True
    assert (call_dir / "accepted_news_list.json").exists()
    assert blocking_reasons == []


def test_validate_accepted_news_audit_fails_when_existing_list_mismatches_pack_data(tmp_path: Path) -> None:
    call_dir = tmp_path / "run" / "frontline" / "news_analyst" / "call-1"
    call_dir.mkdir(parents=True, exist_ok=True)
    (call_dir / "accepted_news_list.json").write_text(
        json.dumps(
            {
                "schema_version": "cn_a_news_accepted_news_list.v1",
                "sample": {
                    "ticker": "600519",
                    "company_name": "贵州茅台",
                    "market": "CN_A",
                    "start_date": "2026-04-30",
                    "end_date": "2026-05-06",
                },
                "accepted_news_count": 1,
                "accepted_news": [
                    {
                        "bucket": "company_news",
                        "news_id": "mismatch",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    pack_payload = {
        "data": {
            "company_news": [
                {
                    "news_id": "n1",
                    "title": "A",
                    "source": "s",
                    "publish_time": "2026-05-06T10:00:00+08:00",
                    "data_source": "akshare.stock_news_em",
                    "match_type": "ticker_exact",
                    "match_confidence": "high",
                    "content_hash": "h1",
                }
            ]
        }
    }
    accepted = _extract_accepted_news(pack_payload)
    checked_paths: list[str] = []
    blocking_reasons: list[str] = []
    status = _validate_accepted_news_audit(
        call_dir=call_dir,
        sample={
            "ticker": "600519",
            "company_name": "贵州茅台",
            "market": "CN_A",
            "start_date": "2026-04-30",
            "end_date": "2026-05-06",
        },
        accepted_news=accepted,
        create_if_missing=False,
        checked_paths=checked_paths,
        blocking_reasons=blocking_reasons,
    )
    assert status["ok"] is False
    assert status["created"] is False
    assert any("不一致" in reason for reason in blocking_reasons)
