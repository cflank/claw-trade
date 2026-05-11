from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]

MARKET_SCRIPTS_DIR = REPO_ROOT / "agents" / "market_analyst" / "skills" / "cn-a-market-data" / "scripts"
FUNDAMENTAL_SCRIPTS_DIR = (
    REPO_ROOT / "agents" / "fundamental_analyst" / "skills" / "cn-a-fundamental-data" / "scripts"
)
NEWS_SCRIPT_PATH = (
    REPO_ROOT / "agents" / "news_analyst" / "skills" / "cn-a-news-data" / "scripts" / "news_data_pack.py"
)
SOCIAL_SCRIPT_PATH = (
    REPO_ROOT / "agents" / "social_analyst" / "skills" / "cn-a-social-data" / "scripts" / "social_data_pack.py"
)
_PACK_SCHEMA_VERSION = "cn_a_frontline_pack.v1"
_DOMAIN_SCHEMA_VERSION = {
    "market": "cn_a_market_pack.v1",
    "fundamental": "cn_a_fundamental_pack.v1",
    "news": "cn_a_news_pack.v1",
    "social": "cn_a_social_pack.v1",
}
_PACK_REQUIRED_KEYS = {
    "ok",
    "schema_version",
    "domain",
    "run_id",
    "stage",
    "worker_id",
    "call_id",
    "tool_name",
    "input",
    "quality",
    "provider_attempts",
    "field_sources",
    "raw_payload_refs",
    "mongo_cache_refs",
    "openviking_l2_refs",
    "diagnostic_flags",
    "reader_brief",
    "domain_data",
}
_URI_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://")
_JSON_NOISE_RE = re.compile(r'"[^"\n]{1,64}"\s*:\s*')
_UNSUPPORTED_CONCLUSION_RE = re.compile(
    r"(目标价|买入|卖出|增持|减持|买卖建议|投资评级|评级调整|跑赢大盘|强烈推荐)"
)
_TOOL_LOG_RE = re.compile(r"\b(traceback|exception|stderr|stdout|runtime_context|tool_name|call_id)\b", re.IGNORECASE)
_CHINESE_TEXT_RE = re.compile(r"[\u4e00-\u9fff]")


def _market_payload() -> dict[str, Any]:
    return {
        "tool_input": {"ticker": "600519.SH", "market": "CN_A"},
        "runtime_context": {
            "run_id": "it-run-cli",
            "stage": "frontline",
            "worker_id": "market_analyst",
            "call_id": "it-call-market",
            "tool_name": "market_market_data_pack",
        },
    }


def _fundamental_payload() -> dict[str, Any]:
    return {
        "tool_input": {
            "ticker": "600519.SH",
            "market": "CN_A",
            "start_date": "2026-02-01",
            "end_date": "2026-05-09",
        },
        "runtime_context": {
            "run_id": "it-run-cli",
            "dispatch_id": "it-dispatch-fundamental",
            "worker_id": "fundamental_analyst",
            "current_date": "2026-05-09",
        },
    }


def _news_payload() -> dict[str, Any]:
    return {
        "tool_input": {"ticker": "600519.SH", "market": "US"},
        "runtime_context": {
            "run_id": "it-run-cli",
            "stage": "frontline",
            "worker_id": "news_analyst",
            "call_id": "it-call-news",
            "tool_name": "news_news_data_pack",
            "evidence_root": "/tmp/news-evidence",
            "current_time": "2026-05-09T10:00:00Z",
        },
    }


def _social_payload() -> dict[str, Any]:
    return {
        "tool_input": {"ticker": "600519.SH", "market": "US"},
        "runtime_context": {
            "run_id": "it-run-cli",
            "stage": "frontline",
            "worker_id": "social_analyst",
            "call_id": "it-call-social",
            "tool_name": "social_social_sentiment_pack",
            "evidence_root": "/tmp/social-evidence",
            "current_time": "2026-05-09T10:00:00Z",
        },
    }


def _run_entry(
    *,
    args: list[str],
    payload: dict[str, Any],
    env_overrides: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)
    completed = subprocess.run(
        [sys.executable, *args],
        cwd=REPO_ROOT,
        input=json.dumps(payload, ensure_ascii=False),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    return completed.returncode, completed.stdout, completed.stderr


def _pack_envelope_error(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return "payload_not_dict"
    if not _PACK_REQUIRED_KEYS.issubset(set(payload.keys())):
        return "required_pack_keys_missing"
    if payload.get("schema_version") != _PACK_SCHEMA_VERSION:
        return "pack_schema_version_mismatch"
    domain = payload.get("domain")
    if domain not in _DOMAIN_SCHEMA_VERSION:
        return "pack_domain_invalid"
    domain_data = payload.get("domain_data")
    if not isinstance(domain_data, dict):
        return "domain_data_not_dict"
    if domain_data.get("schema_version") != _DOMAIN_SCHEMA_VERSION[domain]:
        return "domain_data_schema_version_mismatch"
    provider_attempts = payload.get("provider_attempts")
    if not isinstance(provider_attempts, list) or len(provider_attempts) == 0:
        return "provider_attempts_empty"
    reader_brief_error = _reader_brief_error(payload.get("reader_brief"))
    if reader_brief_error is not None:
        return reader_brief_error
    if _contains_placeholder_flag(payload):
        return "placeholder_pack_not_allowed"
    return None


def _reader_brief_error(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return "reader_brief_missing"
    text = value.strip()
    if _URI_RE.search(text):
        return "reader_brief_contains_uri"
    if _JSON_NOISE_RE.search(text) or text.startswith("{") or text.startswith("["):
        return "reader_brief_contains_json_noise"
    if _TOOL_LOG_RE.search(text):
        return "reader_brief_contains_tool_log"
    if _UNSUPPORTED_CONCLUSION_RE.search(text):
        return "reader_brief_contains_investment_conclusion"
    if _CHINESE_TEXT_RE.search(text) is None:
        return "reader_brief_missing_chinese_facts"
    return None


def _contains_placeholder_flag(payload: dict[str, Any]) -> bool:
    diagnostic_flags = payload.get("diagnostic_flags")
    if isinstance(diagnostic_flags, list):
        for flag in diagnostic_flags:
            if isinstance(flag, str) and "MARKET_DATA_PACK_NOT_IMPLEMENTED" in flag:
                return True
    quality = payload.get("quality")
    if isinstance(quality, dict):
        warnings = quality.get("warnings")
        if isinstance(warnings, list):
            for warning in warnings:
                if isinstance(warning, str) and "MARKET_DATA_PACK_NOT_IMPLEMENTED" in warning:
                    return True
    return False


def _is_structured_tool_error(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    if payload.get("ok") is not False:
        return False
    error = payload.get("error")
    if isinstance(error, dict) and isinstance(error.get("code"), str):
        return True
    quality = payload.get("quality")
    if isinstance(quality, dict) and quality.get("status") == "failed":
        warnings = quality.get("warnings")
        if isinstance(warnings, list) and warnings:
            first = warnings[0]
            if isinstance(first, dict) and isinstance(first.get("code"), str):
                return True
    return False


def test_t_test_004_cli_stdin_stdout_contract_collect_first() -> None:
    entries = [
        (
            "market",
            [
                "-c",
                (
                    "import json, sys; from pathlib import Path; "
                    "scripts_dir = Path(sys.argv[1]).resolve(); "
                    "sys.path.insert(0, str(scripts_dir)); "
                    "from market_data_pack import run_market_data_pack; "
                    "payload = json.load(sys.stdin); "
                    "result = run_market_data_pack(payload['tool_input'], payload['runtime_context']); "
                    "print(json.dumps(result, ensure_ascii=False, default=str))"
                ),
                str(MARKET_SCRIPTS_DIR),
            ],
            _market_payload(),
            None,
        ),
        (
            "fundamental",
            [
                "-c",
                (
                    "import json, sys; from pathlib import Path; "
                    "scripts_dir = Path(sys.argv[1]).resolve(); "
                    "sys.path.insert(0, str(scripts_dir)); "
                    "from fundamental_data_pack import tool_entrypoint; "
                    "payload = json.load(sys.stdin); "
                    "result = tool_entrypoint(payload['tool_input'], payload['runtime_context']); "
                    "print(json.dumps(result, ensure_ascii=False, default=str))"
                ),
                str(FUNDAMENTAL_SCRIPTS_DIR),
            ],
            _fundamental_payload(),
            {
                "CN_A_MONGODB_URI": "mongodb://127.0.0.1:27017/claw_trade_ttest004",
                "CN_A_MONGODB_DATABASE": "claw_trade_ttest004",
                "CN_A_MONGODB_CACHE_COLLECTION": "cn_a_fundamental_cache_ttest004",
                "OPENVIKING_ENDPOINT": "http://127.0.0.1:1933",
                "OPENVIKING_API_KEY": "local-dev-key",
                "OPENVIKING_WORKSPACE": "workflow",
                "CN_A_FUNDAMENTAL_DISABLE_TUSHARE": "true",
                "CN_A_FUNDAMENTAL_DISABLE_AKSHARE": "true",
            },
        ),
        ("news", [str(NEWS_SCRIPT_PATH)], _news_payload(), None),
        ("social", [str(SOCIAL_SCRIPT_PATH)], _social_payload(), None),
    ]

    failures: list[str] = []
    for name, args, payload, env_overrides in entries:
        exit_code, stdout, stderr = _run_entry(
            args=args,
            payload=payload,
            env_overrides=env_overrides,
        )
        if exit_code not in {0, 1, 2}:
            failures.append(f"{name}: unexpected_exit_code={exit_code}, stderr={stderr.strip()[:300]}")
            continue
        try:
            parsed = json.loads(stdout)
        except json.JSONDecodeError as exc:
            failures.append(f"{name}: stdout_not_json: {exc}: stdout={stdout.strip()[:300]}")
            continue
        pack_error = _pack_envelope_error(parsed)
        if pack_error is None:
            continue
        if _is_structured_tool_error(parsed):
            continue
        failures.append(f"{name}: output_not_valid_pack_or_structured_error reason={pack_error}")

    assert not failures, "\n".join(failures)
