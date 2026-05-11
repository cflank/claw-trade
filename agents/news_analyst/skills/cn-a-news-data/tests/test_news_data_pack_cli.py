from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from errors import E_CONTEXT_MISMATCH, E_INVALID_INPUT, NewsDataError  # noqa: E402
import news_data_pack as news_data_pack_module  # noqa: E402


def _build_stdin_payload(tmp_path: Path, *, market: str = "CN_A") -> dict[str, object]:
    return {
        "tool_input": {
            "ticker": "600519",
            "market": market,
        },
        "runtime_context": {
            "run_id": "run-1",
            "stage": "frontline",
            "worker_id": "news_analyst",
            "call_id": "call-1",
            "tool_name": "news_news_data_pack",
            "evidence_root": str(tmp_path / "evidence"),
        },
    }


def test_main_returns_json_and_exit_0_on_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    payload = _build_stdin_payload(tmp_path)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload, ensure_ascii=False)))
    monkeypatch.setattr(
        news_data_pack_module,
        "run_news_data_pack",
        lambda tool_input, context: {
            "ok": True,
            "quality": {"status": "complete", "missing_fields": [], "warnings": []},
            "reader_brief": "中文资料包",
        },
    )

    exit_code = news_data_pack_module.main()

    captured = capsys.readouterr()
    response = json.loads(captured.out.strip())
    assert exit_code == 0
    assert response["ok"] is True
    assert response["quality"]["status"] == "complete"
    assert "中文资料包" in captured.out
    assert captured.err == ""


def test_main_returns_structured_json_on_invalid_stdin_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    malformed = '{"tool_input":{"ticker":"600519","market":"CN_A"},"runtime_context":token=top-secret}'
    monkeypatch.setattr(sys, "stdin", io.StringIO(malformed))

    exit_code = news_data_pack_module.main()

    captured = capsys.readouterr()
    response = json.loads(captured.out.strip())
    assert exit_code != 0
    assert response["ok"] is False
    assert response["error"]["code"] == E_INVALID_INPUT
    assert response["quality"]["status"] == "failed"
    assert "news_data_pack failed:" in captured.err
    assert "top-secret" not in captured.err


def test_main_keeps_business_failed_pack_for_unsupported_market(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    payload = _build_stdin_payload(tmp_path, market="US")
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload, ensure_ascii=False)))
    monkeypatch.setattr(
        news_data_pack_module,
        "run_news_data_pack",
        lambda tool_input, context: {
            "ok": False,
            "quality": {
                "status": "failed",
                "missing_fields": ["industry"],
                "warnings": ["E_UNSUPPORTED_MARKET: market must be CN_A"],
            },
            "reader_brief": "资料包失败：E_UNSUPPORTED_MARKET。market must be CN_A",
            "provider_attempts": [],
            "data": {
                "company_news": [],
                "industry_news": [],
                "policy_macro_news": [],
                "announcements": [],
            },
        },
    )

    exit_code = news_data_pack_module.main()

    captured = capsys.readouterr()
    response = json.loads(captured.out.strip())
    assert exit_code != 0
    assert response["ok"] is False
    assert response["quality"]["status"] == "failed"
    assert response["quality"]["missing_fields"] == ["industry"]
    assert "error" not in response
    assert "E_UNSUPPORTED_MARKET" in captured.err


def test_main_returns_structured_runtime_error_for_context_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    payload = _build_stdin_payload(tmp_path)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload, ensure_ascii=False)))

    def _raise_runtime_error(tool_input, context):
        raise NewsDataError(
            code=E_CONTEXT_MISMATCH,
            message="worker_id mismatch token=top-secret",
            details={"expected": "news_analyst"},
        )

    monkeypatch.setattr(news_data_pack_module, "run_news_data_pack", _raise_runtime_error)

    exit_code = news_data_pack_module.main()

    captured = capsys.readouterr()
    response = json.loads(captured.out.strip())
    assert exit_code != 0
    assert response["ok"] is False
    assert response["status"] == "failed"
    assert response["error"]["code"] == E_CONTEXT_MISMATCH
    assert response["quality"]["status"] == "failed"
    assert response["quality"]["missing_fields"] == []
    assert response["evidence_gap"] == "runtime_error"
    assert "top-secret" not in captured.err
    assert "token=***" in captured.err


def test_timeout_targets_match_dld_requirements() -> None:
    assert news_data_pack_module.TOOL_ADAPTER_TIMEOUT_SECONDS == 25
    assert news_data_pack_module.SERVICE_TOTAL_TIMEOUT_SECONDS == 20
    assert news_data_pack_module.TOOL_ADAPTER_TIMEOUT_SECONDS > news_data_pack_module.SERVICE_TOTAL_TIMEOUT_SECONDS
