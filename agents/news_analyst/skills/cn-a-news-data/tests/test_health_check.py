from __future__ import annotations

import json
from pathlib import Path
import sys

import pandas as pd

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import providers as providers_module  # noqa: E402
from health_check import SCHEMA_VERSION, main  # noqa: E402


class _StockNewsEmCallSwap:
    def __init__(self, replacement):
        self._replacement = replacement
        self._original = None

    def __enter__(self):
        self._original = providers_module.ak.stock_news_em
        providers_module.ak.stock_news_em = self._replacement

    def __exit__(self, exc_type, exc, tb):
        providers_module.ak.stock_news_em = self._original


def _read_stdout_json(captured_stdout: str) -> dict[str, object]:
    return json.loads(captured_stdout.strip())


def test_stock_news_em_success_returns_exit_0_and_schema(tmp_path, capsys) -> None:
    def _stock_news_em_replacement(*, symbol: str):
        assert symbol == "600519"
        return pd.DataFrame(
            [
                {
                    "新闻标题": "贵州茅台公告",
                    "新闻内容": "经营稳定",
                    "发布时间": "2026-05-07 10:00:00",
                    "文章来源": "证券时报",
                    "新闻链接": "https://example.com/news?a=1",
                }
            ]
        )

    with _StockNewsEmCallSwap(_stock_news_em_replacement):
        exit_code = main(
            [
                "--provider",
                "akshare.stock_news_em",
                "--ticker",
                "600519",
                "--evidence-dir",
                str(tmp_path),
            ]
        )

    captured = capsys.readouterr()
    payload = _read_stdout_json(captured.out)
    assert exit_code == 0
    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["provider"] == "akshare"
    assert payload["endpoint"] == "stock_news_em"
    assert payload["attempt"]["ok"] is True
    assert payload["exit_code"] == 0
    assert payload["diagnosis"] == "provider_ok"

    output_file = (
        tmp_path
        / "provider-health-check"
        / payload["check_time"][:10]
        / "akshare_stock_news_em.json"
    )
    assert output_file.exists()


def test_tushare_not_configured_returns_exit_1(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.delenv("CN_A_NEWS_TUSHARE_TOKEN", raising=False)
    exit_code = main(
        [
            "--provider",
            "tushare.anns_d",
            "--ts-code",
            "600519.SH",
            "--evidence-dir",
            str(tmp_path),
        ]
    )
    captured = capsys.readouterr()
    payload = _read_stdout_json(captured.out)
    assert exit_code == 1
    assert payload["attempt"]["ok"] is False
    assert payload["attempt"]["empty_reason"] == "not_configured"
    assert payload["exit_code"] == 1


def test_invalid_date_returns_exit_2_and_error_is_sanitized(capsys) -> None:
    exit_code = main(
        [
            "--provider",
            "akshare.news_cctv",
            "--date",
            "2026-99-99 token=top-secret",
        ]
    )
    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.out == ""
    assert "top-secret" not in captured.err
    assert "token=***" in captured.err


def test_default_output_path_is_created_under_docs(monkeypatch, tmp_path, capsys) -> None:
    monkeypatch.delenv("CN_A_NEWS_TUSHARE_TOKEN", raising=False)
    monkeypatch.chdir(tmp_path)
    exit_code = main(
        [
            "--provider",
            "tushare.anns_d",
            "--ts-code",
            "600519.SH",
        ]
    )
    captured = capsys.readouterr()
    payload = _read_stdout_json(captured.out)
    assert exit_code == 1
    expected_file = (
        tmp_path
        / "docs"
        / "evidence"
        / "cn_a_news"
        / "provider-health-check"
        / payload["check_time"][:10]
        / "tushare_anns_d.json"
    )
    assert expected_file.exists()


def test_runtime_evidence_mode_writes_runtime_path(tmp_path, capsys) -> None:
    def _stock_news_em_replacement(*, symbol: str):
        assert symbol == "600519"
        return pd.DataFrame(
            [
                {
                    "新闻标题": "贵州茅台公告",
                    "新闻内容": "经营稳定",
                    "发布时间": "2026-05-07 10:00:00",
                    "文章来源": "证券时报",
                    "新闻链接": "https://example.com/news?a=1",
                }
            ]
        )

    with _StockNewsEmCallSwap(_stock_news_em_replacement):
        exit_code = main(
            [
                "--provider",
                "akshare.stock_news_em",
                "--ticker",
                "600519",
                "--runtime-evidence-root",
                str(tmp_path),
                "--run-id",
                "run-1",
                "--stage",
                "frontline",
                "--call-id",
                "call-1",
            ]
        )

    captured = capsys.readouterr()
    payload = _read_stdout_json(captured.out)
    assert exit_code == 0
    expected_file = (
        tmp_path
        / "run-1"
        / "frontline"
        / "news_analyst"
        / "call-1"
        / "provider-health-check.json"
    )
    assert expected_file.exists()
    saved = json.loads(expected_file.read_text(encoding="utf-8"))
    assert saved["schema_version"] == SCHEMA_VERSION
    assert payload["exit_code"] == 0


def test_runtime_evidence_incomplete_args_returns_exit_2_and_no_secret_leak(capsys) -> None:
    exit_code = main(
        [
            "--provider",
            "akshare.stock_info_global_cls",
            "--runtime-evidence-root",
            "/tmp/token=top-secret",
            "--run-id",
            "run-1",
        ]
    )
    captured = capsys.readouterr()
    assert exit_code == 2
    assert captured.out == ""
    assert "top-secret" not in captured.err
