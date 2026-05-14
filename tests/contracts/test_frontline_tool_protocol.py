from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PATH = REPO_ROOT / "openclaw_plugins" / "claw-trade-frontline-tools" / "index.js"


def _runtime_ctx(
    worker_id: str,
    stage: str = "frontline",
    runtime_vars: dict[str, object] | None = None,
) -> dict[str, object]:
    vars_payload: dict[str, object] = {"ticker": "600519", "market": "CN_A"}
    if runtime_vars:
        vars_payload.update(runtime_vars)
    return {
        "singleWorkerCommand": {
            "run_id": "run-1",
            "stage": stage,
            "worker_id": worker_id,
            "call_id": "call-1",
            "evidence_dir": "/tmp/evidence/run-1/call-1",
            "runtime_vars": vars_payload,
        }
    }


CN_A_TOOLS = (
    ("market_market_data_pack", "market_analyst"),
    ("fundamental_fundamentals_data_pack", "fundamental_analyst"),
    ("news_news_data_pack", "news_analyst"),
    ("social_social_sentiment_pack", "social_analyst"),
)

US_TOOLS = (
    ("get_stock_data", "market_analyst"),
    ("get_indicators", "market_analyst"),
    ("get_fundamentals", "fundamental_analyst"),
    ("get_balance_sheet", "fundamental_analyst"),
    ("get_cashflow", "fundamental_analyst"),
    ("get_income_statement", "fundamental_analyst"),
    ("get_news", "news_analyst"),
    ("get_global_news", "news_analyst"),
    ("get_news", "social_analyst"),
)


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    current_mode = path.stat().st_mode
    path.chmod(current_mode | stat.S_IXUSR)


def _run_tool(
    *,
    tool_name: str,
    ctx: dict[str, object],
    params: dict[str, object] | list[object] | None = None,
    env_overrides: dict[str, str] | None = None,
) -> dict[str, object]:
    payload = {} if params is None else params
    script = f"""
import plugin from {json.dumps(str(PLUGIN_PATH))};
const ctx = JSON.parse(process.argv[1]);
const params = JSON.parse(process.argv[2]);
const toolName = process.argv[3];
const tools = [];
const api = {{
  registerTool(factory) {{
    tools.push(factory(ctx));
  }},
}};
plugin.register(api);
const tool = tools.find((item) => item.name === toolName);
if (!tool) {{
  throw new Error(`tool not found: ${{toolName}}`);
}}
const result = await tool.execute("call", params);
process.stdout.write(JSON.stringify(result));
"""
    env = os.environ.copy()
    if env_overrides:
        env.update(env_overrides)
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script, json.dumps(ctx), json.dumps(payload), tool_name],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return json.loads(completed.stdout)


def _error_code(result: dict[str, object]) -> str:
    details = result.get("details", {})
    if not isinstance(details, dict):
        raise AssertionError("tool result.details must be dict")
    error = details.get("error", {})
    if not isinstance(error, dict):
        raise AssertionError("tool result.details.error must be dict")
    code = error.get("code")
    if not isinstance(code, str):
        raise AssertionError("tool result error code missing")
    return code


def test_missing_single_worker_command_returns_runtime_context_missing() -> None:
    result = _run_tool(tool_name="market_market_data_pack", ctx={}, params={"ticker": "600519"})
    assert result.get("isError") is True
    assert _error_code(result) == "TOOL_RUNTIME_CONTEXT_MISSING"


def test_worker_mismatch_returns_worker_mismatch_error() -> None:
    result = _run_tool(
        tool_name="news_news_data_pack",
        ctx=_runtime_ctx(worker_id="market_analyst"),
        params={"ticker": "600519", "market": "CN_A"},
    )
    assert result.get("isError") is True
    assert _error_code(result) == "TOOL_WORKER_MISMATCH"


def test_non_frontline_stage_returns_context_incomplete_without_spawning_python(tmp_path: Path) -> None:
    marker = tmp_path / "python_called.txt"
    fake_python = tmp_path / "fake_python.sh"
    _write_executable(
        fake_python,
        f"""#!/usr/bin/env bash
echo called > {marker}
echo '{{"ok": true}}'
""",
    )
    result = _run_tool(
        tool_name="news_news_data_pack",
        ctx=_runtime_ctx(worker_id="news_analyst", stage="investment_debate"),
        params={"ticker": "600519", "market": "CN_A"},
        env_overrides={"CLAW_TRADE_FRONTLINE_TOOL_PYTHON": str(fake_python)},
    )
    assert result.get("isError") is True
    assert _error_code(result) == "TOOL_CONTEXT_INCOMPLETE"
    assert marker.exists() is False


@pytest.mark.parametrize("missing_field", ("run_id", "call_id", "evidence_dir"))
def test_missing_runtime_required_field_returns_context_incomplete_without_spawning_python(
    tmp_path: Path,
    missing_field: str,
) -> None:
    marker = tmp_path / "python_called.txt"
    fake_python = tmp_path / "fake_python.sh"
    _write_executable(
        fake_python,
        f"""#!/usr/bin/env bash
echo called > {marker}
echo '{{"ok": true}}'
""",
    )
    ctx = _runtime_ctx(worker_id="market_analyst")
    command = ctx["singleWorkerCommand"]
    assert isinstance(command, dict)
    command[missing_field] = ""
    result = _run_tool(
        tool_name="market_market_data_pack",
        ctx=ctx,
        params={"ticker": "600519", "market": "CN_A"},
        env_overrides={"CLAW_TRADE_FRONTLINE_TOOL_PYTHON": str(fake_python)},
    )
    assert result.get("isError") is True
    assert _error_code(result) == "TOOL_CONTEXT_INCOMPLETE"
    assert marker.exists() is False


@pytest.mark.parametrize(
    ("tool_name", "worker_id"),
    CN_A_TOOLS,
)
def test_non_cn_a_market_returns_structured_context_or_params_error_without_provider_attempts(
    tmp_path: Path,
    tool_name: str,
    worker_id: str,
) -> None:
    marker = tmp_path / "python_called.txt"
    fake_python = tmp_path / "fake_python.sh"
    _write_executable(
        fake_python,
        f"""#!/usr/bin/env bash
echo called > {marker}
echo '{{"ok": true}}'
""",
    )
    result = _run_tool(
        tool_name=tool_name,
        ctx=_runtime_ctx(worker_id=worker_id, runtime_vars={"market": "US"}),
        params={"ticker": "600519", "market": "US"},
        env_overrides={"CLAW_TRADE_FRONTLINE_TOOL_PYTHON": str(fake_python)},
    )
    assert result.get("isError") is True
    assert _error_code(result) in {"TOOL_CONTEXT_INCOMPLETE", "TOOL_PARAMS_INVALID"}
    details = result.get("details")
    assert isinstance(details, dict)
    assert "provider_attempts" not in details
    assert marker.exists() is False


@pytest.mark.parametrize(
    ("tool_name", "worker_id"),
    US_TOOLS,
)
def test_non_us_market_for_us_tools_returns_structured_params_error_without_provider_attempts(
    tmp_path: Path,
    tool_name: str,
    worker_id: str,
) -> None:
    marker = tmp_path / "python_called.txt"
    fake_python = tmp_path / "fake_python.sh"
    _write_executable(
        fake_python,
        f"""#!/usr/bin/env bash
echo called > {marker}
echo '{{"ok": true}}'
""",
    )
    result = _run_tool(
        tool_name=tool_name,
        ctx=_runtime_ctx(worker_id=worker_id, runtime_vars={"ticker": "AAPL", "market": "CN_A"}),
        params={"ticker": "AAPL", "market": "CN_A"},
        env_overrides={"CLAW_TRADE_FRONTLINE_TOOL_PYTHON": str(fake_python)},
    )
    assert result.get("isError") is True
    assert _error_code(result) in {"TOOL_CONTEXT_INCOMPLETE", "TOOL_PARAMS_INVALID"}
    details = result.get("details")
    assert isinstance(details, dict)
    assert "provider_attempts" not in details
    assert marker.exists() is False


@pytest.mark.parametrize(
    ("tool_name", "worker_id"),
    (*CN_A_TOOLS, *US_TOOLS),
)
def test_missing_required_market_returns_structured_params_error_without_spawning_python(
    tmp_path: Path,
    tool_name: str,
    worker_id: str,
) -> None:
    marker = tmp_path / "python_called.txt"
    fake_python = tmp_path / "fake_python.sh"
    _write_executable(
        fake_python,
        f"""#!/usr/bin/env bash
echo called > {marker}
echo '{{"ok": true}}'
""",
    )
    result = _run_tool(
        tool_name=tool_name,
        ctx=_runtime_ctx(worker_id=worker_id, runtime_vars={"market": ""}),
        params={"ticker": "600519"},
        env_overrides={"CLAW_TRADE_FRONTLINE_TOOL_PYTHON": str(fake_python)},
    )
    assert result.get("isError") is True
    assert _error_code(result) in {"TOOL_CONTEXT_INCOMPLETE", "TOOL_PARAMS_INVALID"}
    assert marker.exists() is False


def test_successful_us_original_tool_uses_us_profile_and_passes_runtime_market_to_python(tmp_path: Path) -> None:
    stdin_path = tmp_path / "stdin.json"
    fake_python = tmp_path / "fake_python_pack.sh"
    payload = {
        "ok": True,
        "schema_version": "us_get_stock_data.v1",
        "tool_name": "get_stock_data",
        "reader_brief": "# Stock data for AAPL from 2026-04-01 to 2026-05-13\nDate,Open,High,Low,Close,Volume",
    }
    _write_executable(
        fake_python,
        f"""#!/usr/bin/env bash
cat > {stdin_path}
echo {json.dumps(json.dumps(payload))}
""",
    )
    result = _run_tool(
        tool_name="get_stock_data",
        ctx=_runtime_ctx(
            worker_id="market_analyst",
            runtime_vars={
                "ticker": "AAPL",
                "market": "US",
                "start_date": "2026-04-01",
                "end_date": "2026-05-13",
            },
        ),
        params={"symbol": "AAPL"},
        env_overrides={"CLAW_TRADE_FRONTLINE_TOOL_PYTHON": str(fake_python)},
    )
    assert result.get("isError") is False
    assert result["content"][0]["text"] == payload["reader_brief"]
    stdin_payload = json.loads(stdin_path.read_text(encoding="utf-8"))
    assert stdin_payload["tool_input"]["symbol"] == "AAPL"
    assert stdin_payload["tool_input"]["market"] == "US"
    assert stdin_payload["tool_input"]["start_date"] == "2026-04-01"
    assert stdin_payload["tool_input"]["end_date"] == "2026-05-13"
    assert stdin_payload["runtime_context"]["tool_name"] == "get_stock_data"


def test_non_json_stdout_returns_protocol_error_with_redacted_stderr_summary(tmp_path: Path) -> None:
    long_secret = "token=super_secret " + ("x" * 2600) + " mongodb://user:pass@localhost:27017/db"
    stdout_secret = "HTTPConnectionPool token=stdout_secret_value mongodb://user:pass@localhost:27017/db"
    fake_python = tmp_path / "fake_python_non_json.sh"
    _write_executable(
        fake_python,
        f"""#!/usr/bin/env bash
echo {json.dumps(stdout_secret)}
echo {json.dumps(long_secret)} 1>&2
exit 0
""",
    )
    result = _run_tool(
        tool_name="news_news_data_pack",
        ctx=_runtime_ctx(worker_id="news_analyst"),
        params={"ticker": "600519", "market": "CN_A"},
        env_overrides={"CLAW_TRADE_FRONTLINE_TOOL_PYTHON": str(fake_python)},
    )
    assert result.get("isError") is True
    assert _error_code(result) == "TOOL_PROTOCOL_ERROR"
    details = result.get("details", {})
    assert isinstance(details, dict)
    error = details.get("error", {})
    assert isinstance(error, dict)
    summary = error.get("stderr_summary")
    assert isinstance(summary, str)
    assert len(summary) <= 2000
    assert "super_secret" not in summary
    assert "user:pass@" not in summary
    stdout_summary = error.get("stdout_summary")
    assert isinstance(stdout_summary, str)
    assert len(stdout_summary) <= 2000
    assert "stdout_secret_value" not in stdout_summary
    assert "user:pass@" not in stdout_summary


def test_python_early_exit_stdin_epipe_returns_protocol_error(tmp_path: Path) -> None:
    fake_python = tmp_path / "fake_python_exit_immediately.sh"
    _write_executable(
        fake_python,
        """#!/usr/bin/env bash
exit 0
""",
    )
    script = f"""
import plugin from {json.dumps(str(PLUGIN_PATH))};
const ctx = {json.dumps(_runtime_ctx(worker_id="news_analyst"))};
const tools = [];
const api = {{
  registerTool(factory) {{
    tools.push(factory(ctx));
  }},
}};
plugin.register(api);
const tool = tools.find((item) => item.name === "news_news_data_pack");
if (!tool) {{
  throw new Error("news_news_data_pack not found");
}}
const params = {{
  ticker: "600519",
  market: "CN_A",
  aliases: Array.from({{ length: 512 }}, () => "x".repeat(1024)),
}};
const result = await tool.execute("call", params);
process.stdout.write(JSON.stringify(result));
"""
    env = os.environ.copy()
    env["CLAW_TRADE_FRONTLINE_TOOL_PYTHON"] = str(fake_python)
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    result = json.loads(completed.stdout)
    assert result.get("isError") is True
    assert _error_code(result) == "TOOL_PROTOCOL_ERROR"
    details = result.get("details", {})
    assert isinstance(details, dict)
    error = details.get("error", {})
    assert isinstance(error, dict)
    summary = error.get("stderr_summary")
    assert isinstance(summary, str)
    assert len(summary) <= 2000
    assert "EPIPE" in summary


def test_successful_pack_only_exposes_natural_material_to_model_and_keeps_details_off_prompt(tmp_path: Path) -> None:
    fake_python = tmp_path / "fake_python_pack.sh"
    payload = {
        "ok": True,
        "schema_version": "cn_a_frontline_pack.v1",
        "tool_name": "news_news_data_pack",
        "reader_brief": "资料范围：已采集公司新闻十条。材料正文：公司新闻包括茅台发布经营进展公告。证据缺口：宏观新闻不足。只可按这些事实写报告。",
        "provider_attempts": [
            {
                "provider": "akshare",
                "endpoint": "stock_news_em",
                "raw_payload_ref": "viking://resources/workflow/run/frontline/news/raw.json",
            }
        ],
        "raw_payload_refs": ["viking://resources/workflow/run/frontline/news/raw.json"],
        "openviking_l2_refs": ["viking://resources/workflow/run/frontline/news/normalized_pack.json"],
    }
    _write_executable(
        fake_python,
        f"""#!/usr/bin/env bash
echo {json.dumps(json.dumps(payload, ensure_ascii=False))}
""",
    )
    result = _run_tool(
        tool_name="news_news_data_pack",
        ctx=_runtime_ctx(worker_id="news_analyst"),
        params={"ticker": "600519", "market": "CN_A"},
        env_overrides={"CLAW_TRADE_FRONTLINE_TOOL_PYTHON": str(fake_python)},
    )
    assert result.get("isError") is False
    content = result.get("content")
    assert isinstance(content, list)
    assert content[0]["text"] == payload["reader_brief"]
    assert "viking://" not in content[0]["text"]
    assert "provider_attempts" not in content[0]["text"]
    assert "raw_payload_ref" not in content[0]["text"]
    details = result.get("details")
    assert isinstance(details, dict)
    assert details.get("provider_attempts") == payload["provider_attempts"]
    assert details.get("openviking_l2_refs") == payload["openviking_l2_refs"]


def test_provider_total_timeout_contract_keeps_subprocess_plus_five_seconds_buffer() -> None:
    source = PLUGIN_PATH.read_text(encoding="utf-8")
    assert "CN_A_PROVIDER_TOTAL_TIMEOUT_MS" in source
    assert "SUBPROCESS_TIMEOUT_BUFFER_MS = 5000" in source
    assert "Math.max(totalTimeout + SUBPROCESS_TIMEOUT_BUFFER_MS, DEFAULT_MIN_SUBPROCESS_TIMEOUT_MS)" in source


def test_news_social_wiring_uses_shared_frontline_data_pack_modules() -> None:
    source = PLUGIN_PATH.read_text(encoding="utf-8")
    assert "from frontline_data_pack.news_data_pack import run_news_data_pack" in source
    assert "from frontline_data_pack.social_sentiment_pack import run_social_sentiment_pack" in source
    assert "import frontline_data_pack.us_data_pack as us_data_pack" in source
    assert "from frontline_data_pack.models import to_jsonable" in source
    assert "json.dumps(to_jsonable(result), ensure_ascii=False, default=str)" in source
    assert '"cn-a-news-data"' not in source
    assert '"cn-a-social-data"' not in source
    assert '"social_data_pack.py"' not in source
