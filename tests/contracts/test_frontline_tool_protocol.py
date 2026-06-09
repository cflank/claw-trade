from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PATH = REPO_ROOT / "openclaw_plugins" / "claw-trade-frontline-tools" / "index.js"

CANONICAL_TOOLS = (
    ("claw_get_market_pack", "market_analyst", "market"),
    ("claw_get_fundamental_pack", "fundamental_analyst", "fundamental"),
    ("claw_get_news_pack", "news_analyst", "news"),
    ("claw_get_social_pack", "social_analyst", "social"),
)


def _runtime_ctx(
    worker_id: str,
    stage: str = "frontline",
    runtime_vars: dict[str, object] | None = None,
) -> dict[str, object]:
    vars_payload: dict[str, object] = {
        "ticker": "00700.HK",
        "market": "HK",
        "profile": "HK",
        "company_name": "腾讯控股",
        "start_date": "2026-05-01",
        "end_date": "2026-05-17",
        "current_date": "2026-05-17",
        "currency": "HKD",
    }
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


def _run_reply_dispatch_hook(
    *,
    event: dict[str, object],
    ui_response: dict[str, object],
    http_status: int = 200,
    inbound_url: str = "http://127.0.0.1:8789/api/ui/channel-inbound-message",
    timeout_ms: str = "3000",
    fetch_delay_ms: int = 0,
) -> dict[str, object]:
    script = f"""
import plugin from {json.dumps(str(PLUGIN_PATH))};
const event = JSON.parse(process.argv[1]);
const uiResponse = JSON.parse(process.argv[2]);
const httpStatus = Number(process.argv[3]);
const inboundUrl = process.argv[4];
const timeoutMs = process.argv[5];
const fetchDelayMs = Number(process.argv[6]);
const captured = {{ requests: [], finalReplies: [], timeline: [] }};
const hooks = [];
process.env.CLAW_TRADE_UI_INBOUND_URL = inboundUrl;
process.env.CLAW_TRADE_UI_INBOUND_TIMEOUT_MS = timeoutMs;
globalThis.fetch = async (url, init = {{}}) => {{
  captured.timeline.push("fetch-start");
  captured.requests.push({{
    url: String(url),
    body: init.body ? JSON.parse(String(init.body)) : null,
  }});
  if (fetchDelayMs > 0) {{
    await new Promise((resolve) => setTimeout(resolve, fetchDelayMs));
  }}
  captured.timeline.push("fetch-response");
  return new Response(JSON.stringify(uiResponse), {{
    status: httpStatus,
    headers: {{ "Content-Type": "application/json" }},
  }});
}};
const api = {{
  registerTool() {{}},
  on(hookName, handler) {{
    hooks.push({{ hookName, handler }});
  }},
}};
plugin.register(api);
const hook = hooks.find((item) => item.hookName === "reply_dispatch");
if (!hook) {{
  throw new Error("reply_dispatch hook not registered");
}}
const dispatcher = {{
  sendFinalReply(payload) {{
    captured.finalReplies.push(payload);
    captured.timeline.push(`reply:${{payload.text ?? ""}}`);
    return true;
  }},
  getQueuedCounts() {{
    return {{ tool: 0, block: 0, final: captured.finalReplies.length }};
  }},
}};
const result = await hook.handler(event, {{ dispatcher }});
process.stdout.write(JSON.stringify({{ result, captured, hookCount: hooks.length }}));
"""
    completed = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            script,
            json.dumps(event),
            json.dumps(ui_response),
            str(http_status),
            inbound_url,
            timeout_ms,
            str(fetch_delay_ms),
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_plugin_registers_only_canonical_data_pack_tools() -> None:
    script = f"""
import plugin from {json.dumps(str(PLUGIN_PATH))};
const tools = [];
const api = {{
  registerTool(factory) {{
    const tool = factory({{ singleWorkerCommand: {{}} }});
    tools.push({{
      name: tool.name,
      schemaType: tool.parameters?.type,
      additionalProperties: tool.parameters?.additionalProperties,
      fields: Object.keys(tool.parameters?.properties ?? {{}}),
    }});
  }},
}};
plugin.register(api);
process.stdout.write(JSON.stringify(tools));
"""
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    tools = json.loads(completed.stdout)
    assert [item["name"] for item in tools] == [
        "claw_get_market_pack",
        "claw_get_fundamental_pack",
        "claw_get_news_pack",
        "claw_get_social_pack",
        "claw_get_policy_pack",
        "claw_get_hot_money_pack",
        "claw_get_lockup_pack",
    ]
    for item in tools:
        assert item["schemaType"] == "object"
        assert item["additionalProperties"] is False
        assert set(item["fields"]) == set()


def test_reply_dispatch_hook_forwards_wechat_inbound_and_uses_ui_reply_text() -> None:
    result = _run_reply_dispatch_hook(
        event={
            "runId": "run-bridge-1",
            "ctx": {
                "OriginatingChannel": "openclaw-weixin",
                "AccountId": "account-1",
                "SenderId": "sender-1",
                "BodyForCommands": "/report TSLA",
                "MessageSid": "msg-1",
                "Timestamp": 1716552000000,
            },
        },
        ui_response={"handled": True, "replyText": "收到，已创建确认卡。"},
    )

    assert result["result"] == {
        "handled": True,
        "queuedFinal": True,
        "counts": {"tool": 0, "block": 0, "final": 1},
    }
    request = result["captured"]["requests"][0]
    assert request["url"].endswith("/api/ui/channel-inbound-message")
    assert request["body"]["channelKind"] == "wechat_clawbot"
    assert request["body"]["accountId"] == "account-1"
    assert request["body"]["senderId"] == "sender-1"
    assert request["body"]["text"] == "/report TSLA"
    assert request["body"]["messageId"] == "msg-1"
    assert request["body"]["receivedAt"] == "2024-05-24T12:00:00.000Z"
    assert result["captured"]["finalReplies"] == [{"text": "收到，已创建确认卡。"}]


def test_reply_dispatch_hook_default_timeout_covers_real_llm_reply_latency() -> None:
    source = PLUGIN_PATH.read_text(encoding="utf-8")

    assert "const DEFAULT_UI_INBOUND_TIMEOUT_MS = 60000;" in source


def test_reply_dispatch_hook_sends_immediate_ack_for_ordinary_wechat_text() -> None:
    result = _run_reply_dispatch_hook(
        event={
            "runId": "run-bridge-ordinary",
            "ctx": {
                "OriginatingChannel": "openclaw-weixin",
                "AccountId": "account-1",
                "From": "sender-ordinary",
                "Body": "hi",
            },
        },
        ui_response={"handled": True, "replyText": "你好，有什么需要帮忙？"},
        fetch_delay_ms=20,
    )

    assert result["result"] == {
        "handled": True,
        "queuedFinal": True,
        "counts": {"tool": 0, "block": 0, "final": 2},
    }
    assert result["captured"]["finalReplies"] == [
        {"text": "收到，正在处理。"},
        {"text": "你好，有什么需要帮忙？"},
    ]
    assert result["captured"]["timeline"] == [
        "reply:收到，正在处理。",
        "fetch-start",
        "fetch-response",
        "reply:你好，有什么需要帮忙？",
    ]


def test_reply_dispatch_hook_does_not_send_when_ui_returns_unhandled() -> None:
    result = _run_reply_dispatch_hook(
        event={
            "runId": "run-bridge-2",
            "ctx": {
                "OriginatingChannel": "openclaw-weixin",
                "AccountId": "account-1",
                "From": "sender-2",
                "Body": "/report TSLA",
            },
        },
        ui_response={"handled": False},
    )

    assert result["result"] == {
        "handled": False,
        "queuedFinal": False,
        "counts": {"tool": 0, "block": 0, "final": 0},
    }
    assert len(result["captured"]["requests"]) == 1
    assert result["captured"]["finalReplies"] == []


def test_plugin_source_uses_data_layer_bridge_and_not_legacy_pack_runtime() -> None:
    source = PLUGIN_PATH.read_text(encoding="utf-8")

    assert "run_frontline_data_pack" in source
    assert "claw_trade.reports.data_pack_bridge" in source
    assert ("Open" + "BBRuntimeWrapper") not in source
    assert "DomainPackService" not in source
    assert "build_default_provider_adapters" not in source
    assert ("claw_trade.data_gateway." + "mcp") not in source
    assert ("claw_trade.data_gateway." + "packs") not in source
    assert "frontline_data_pack.provider_executor" not in source
    assert "import frontline_data_pack.us_data_pack" not in source
    assert "get_stock_data" not in source


def test_missing_single_worker_command_returns_runtime_context_missing() -> None:
    result = _run_tool(tool_name="claw_get_market_pack", ctx={}, params={"ticker": "00700.HK"})
    assert result.get("isError") is True
    assert _error_code(result) == "TOOL_RUNTIME_CONTEXT_MISSING"


def test_worker_mismatch_returns_worker_mismatch_error() -> None:
    result = _run_tool(
        tool_name="claw_get_news_pack",
        ctx=_runtime_ctx(worker_id="market_analyst"),
        params={"ticker": "00700.HK", "market": "HK"},
    )
    assert result.get("isError") is True
    assert _error_code(result) == "TOOL_WORKER_MISMATCH"


def test_non_frontline_stage_returns_context_incomplete_without_spawning_python(tmp_path: Path) -> None:
    marker = tmp_path / "python_called.txt"
    probe_python = tmp_path / "probe_python.sh"
    _write_executable(
        probe_python,
        f"""#!/usr/bin/env bash
echo called > {marker}
echo '{{"ok": true}}'
""",
    )
    result = _run_tool(
        tool_name="claw_get_news_pack",
        ctx=_runtime_ctx(worker_id="news_analyst", stage="investment_debate"),
        params={"ticker": "00700.HK", "market": "HK"},
        env_overrides={"CLAW_TRADE_FRONTLINE_TOOL_PYTHON": str(probe_python)},
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
    probe_python = tmp_path / "probe_python.sh"
    _write_executable(
        probe_python,
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
        tool_name="claw_get_market_pack",
        ctx=ctx,
        params={"ticker": "00700.HK", "market": "HK"},
        env_overrides={"CLAW_TRADE_FRONTLINE_TOOL_PYTHON": str(probe_python)},
    )
    assert result.get("isError") is True
    assert _error_code(result) == "TOOL_CONTEXT_INCOMPLETE"
    assert marker.exists() is False


@pytest.mark.parametrize(("tool_name", "worker_id", "_pack_domain"), CANONICAL_TOOLS)
def test_missing_required_data_pack_field_returns_params_error_without_spawning_python(
    tmp_path: Path,
    tool_name: str,
    worker_id: str,
    _pack_domain: str,
) -> None:
    marker = tmp_path / "python_called.txt"
    probe_python = tmp_path / "probe_python.sh"
    _write_executable(
        probe_python,
        f"""#!/usr/bin/env bash
echo called > {marker}
echo '{{"ok": true}}'
""",
    )
    result = _run_tool(
        tool_name=tool_name,
        ctx=_runtime_ctx(worker_id=worker_id, runtime_vars={"company_name": ""}),
        params={"ticker": "00700.HK", "market": "HK"},
        env_overrides={"CLAW_TRADE_FRONTLINE_TOOL_PYTHON": str(probe_python)},
    )
    assert result.get("isError") is True
    assert _error_code(result) == "TOOL_PARAMS_INVALID"
    assert marker.exists() is False


@pytest.mark.parametrize(("tool_name", "worker_id", "pack_domain"), CANONICAL_TOOLS)
def test_successful_canonical_pack_passes_data_layer_context_to_python(
    tmp_path: Path,
    tool_name: str,
    worker_id: str,
    pack_domain: str,
) -> None:
    stdin_path = tmp_path / "stdin.json"
    probe_python = tmp_path / "probe_python_pack.sh"
    payload = {
        "ok": True,
        "schema_version": f"{tool_name}.test.v1",
        "tool_name": tool_name,
        "model_visible_text": f"{tool_name} returned natural-language material.",
    }
    _write_executable(
        probe_python,
        f"""#!/usr/bin/env bash
cat > {stdin_path}
echo {json.dumps(json.dumps(payload, ensure_ascii=False))}
""",
    )
    result = _run_tool(
        tool_name=tool_name,
        ctx=_runtime_ctx(worker_id=worker_id),
        params={"ticker": "00700.HK", "market": "HK"},
        env_overrides={"CLAW_TRADE_FRONTLINE_TOOL_PYTHON": str(probe_python)},
    )
    assert result.get("isError") is False
    assert result["content"][0]["text"] == payload["model_visible_text"]
    stdin_payload = json.loads(stdin_path.read_text(encoding="utf-8"))
    assert stdin_payload["tool_input"]["ticker"] == "00700.HK"
    assert stdin_payload["tool_input"]["market"] == "HK"
    assert stdin_payload["tool_input"]["company_name"] == "腾讯控股"
    assert stdin_payload["tool_input"]["current_date"] == "2026-05-17"
    assert stdin_payload["runtime_context"]["tool_name"] == tool_name
    assert stdin_payload["runtime_context"]["worker_id"] == worker_id
    assert stdin_payload["runtime_context"]["pack_domain"] == pack_domain
    assert "report_prefetch_manifest_path" not in stdin_payload["runtime_context"]


def test_successful_canonical_pack_passes_report_prefetch_manifest_path_to_python(tmp_path: Path) -> None:
    stdin_path = tmp_path / "stdin.json"
    probe_python = tmp_path / "probe_python_pack.sh"
    payload = {
        "ok": True,
        "schema_version": "claw_get_news_pack.test.v1",
        "tool_name": "claw_get_news_pack",
        "model_visible_text": "news pack returned natural-language material.",
    }
    _write_executable(
        probe_python,
        f"""#!/usr/bin/env bash
cat > {stdin_path}
echo {json.dumps(json.dumps(payload, ensure_ascii=False))}
""",
    )
    manifest_path = tmp_path / "runs" / "run-1" / "data-layer" / "report-prefetch.json"
    result = _run_tool(
        tool_name="claw_get_news_pack",
        ctx=_runtime_ctx(
            worker_id="news_analyst",
            runtime_vars={"report_prefetch_manifest_path": str(manifest_path)},
        ),
        params={},
        env_overrides={"CLAW_TRADE_FRONTLINE_TOOL_PYTHON": str(probe_python)},
    )

    assert result.get("isError") is False
    stdin_payload = json.loads(stdin_path.read_text(encoding="utf-8"))
    assert stdin_payload["runtime_context"]["report_prefetch_required"] is True
    assert stdin_payload["runtime_context"]["report_prefetch_manifest_path"] == str(manifest_path)


def test_model_supplied_data_layer_runtime_fields_are_ignored_in_favor_of_python_context(tmp_path: Path) -> None:
    stdin_path = tmp_path / "stdin.json"
    probe_python = tmp_path / "probe_python_pack.sh"
    payload = {
        "ok": True,
        "model_visible_text": "资料包已返回。",
    }
    _write_executable(
        probe_python,
        f"""#!/usr/bin/env bash
cat > {stdin_path}
echo {json.dumps(json.dumps(payload, ensure_ascii=False))}
""",
    )
    ctx = _runtime_ctx(
        worker_id="market_analyst",
        runtime_vars={
            "ticker": "AAPL",
            "market": "US",
            "profile": "US",
            "company_name": "Apple Inc.",
            "start_date": "2026-05-01",
            "end_date": "2026-05-17",
            "current_date": "2026-05-17",
            "currency": "USD",
            "freshness_max_age_seconds": 600,
        },
    )
    result = _run_tool(
        tool_name="claw_get_market_pack",
        ctx=ctx,
        params={
            "ticker": "MSFT",
            "market": "NASDAQ",
            "profile": "NASDAQ",
            "company_name": "Wrong Company",
            "start_date": "2000-01-01",
            "end_date": "2000-01-02",
            "current_date": "2000-01-03",
            "currency": "EUR",
            "freshness_max_age_seconds": -1,
        },
        env_overrides={"CLAW_TRADE_FRONTLINE_TOOL_PYTHON": str(probe_python)},
    )

    assert result.get("isError") is False
    stdin_payload = json.loads(stdin_path.read_text(encoding="utf-8"))
    assert stdin_payload["tool_input"]["ticker"] == "AAPL"
    assert stdin_payload["tool_input"]["market"] == "US"
    assert stdin_payload["tool_input"]["profile"] == "US"
    assert stdin_payload["tool_input"]["company_name"] == "Apple Inc."
    assert stdin_payload["tool_input"]["start_date"] == "2026-05-01"
    assert stdin_payload["tool_input"]["end_date"] == "2026-05-17"
    assert stdin_payload["tool_input"]["current_date"] == "2026-05-17"
    assert stdin_payload["tool_input"]["currency"] == "USD"
    assert stdin_payload["tool_input"]["freshness_max_age_seconds"] == 600
    assert stdin_payload["runtime_context"]["ignored_model_input_fields"] == [
        "company_name",
        "currency",
        "current_date",
        "end_date",
        "freshness_max_age_seconds",
        "market",
        "profile",
        "start_date",
        "ticker",
    ]


def test_non_json_stdout_returns_protocol_error_with_redacted_stderr_summary(tmp_path: Path) -> None:
    long_secret = "token=super_secret " + ("x" * 2600) + " mongodb://user:pass@localhost:27017/db"
    stdout_secret = "HTTPConnectionPool token=stdout_secret_value mongodb://user:pass@localhost:27017/db"
    probe_python = tmp_path / "probe_python_non_json.sh"
    _write_executable(
        probe_python,
        f"""#!/usr/bin/env bash
echo {json.dumps(stdout_secret)}
echo {json.dumps(long_secret)} 1>&2
exit 0
""",
    )
    result = _run_tool(
        tool_name="claw_get_news_pack",
        ctx=_runtime_ctx(worker_id="news_analyst"),
        params={"ticker": "00700.HK", "market": "HK"},
        env_overrides={"CLAW_TRADE_FRONTLINE_TOOL_PYTHON": str(probe_python)},
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
    probe_python = tmp_path / "probe_python_exit_immediately.sh"
    _write_executable(
        probe_python,
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
const tool = tools.find((item) => item.name === "claw_get_news_pack");
if (!tool) {{
  throw new Error("claw_get_news_pack not found");
}}
const params = {{
  ticker: "00700.HK",
  market: "HK",
  aliases: Array.from({{ length: 512 }}, () => "x".repeat(1024)),
}};
const result = await tool.execute("call", params);
process.stdout.write(JSON.stringify(result));
"""
    env = os.environ.copy()
    env["CLAW_TRADE_FRONTLINE_TOOL_PYTHON"] = str(probe_python)
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


def test_successful_pack_only_exposes_natural_material_to_model_and_keeps_details_off_prompt(tmp_path: Path) -> None:
    probe_python = tmp_path / "probe_python_pack.sh"
    payload = {
        "ok": True,
        "schema_version": "data_result_pack.v1",
        "tool_name": "claw_get_news_pack",
        "model_visible_text": "资料范围：已采集公司新闻十条。材料正文：公司新闻包括腾讯发布经营进展公告。证据缺口：宏观新闻不足。",
        "provider_attempts": [
            {
                "provider": "local_pack_provider",
                "endpoint": "news/company",
                "raw_payload_ref": "viking://resources/workflow/run/frontline/news/raw.json",
            }
        ],
        "raw_payload_refs": ["viking://resources/workflow/run/frontline/news/raw.json"],
        "openviking_l2_refs": ["viking://resources/workflow/run/frontline/news/normalized_pack.json"],
    }
    _write_executable(
        probe_python,
        f"""#!/usr/bin/env bash
echo {json.dumps(json.dumps(payload, ensure_ascii=False))}
""",
    )
    result = _run_tool(
        tool_name="claw_get_news_pack",
        ctx=_runtime_ctx(worker_id="news_analyst"),
        params={"ticker": "00700.HK", "market": "HK"},
        env_overrides={"CLAW_TRADE_FRONTLINE_TOOL_PYTHON": str(probe_python)},
    )
    assert result.get("isError") is False
    content = result.get("content")
    assert isinstance(content, list)
    assert content[0]["text"] == payload["model_visible_text"]
    assert "viking://" not in content[0]["text"]
    assert "provider_attempts" not in content[0]["text"]
    assert "raw_payload_ref" not in content[0]["text"]
    details = result.get("details")
    assert isinstance(details, dict)
    assert details.get("provider_attempts") == payload["provider_attempts"]
    assert details.get("openviking_l2_refs") == payload["openviking_l2_refs"]


def test_pack_runtime_uses_tool_call_scoped_provider_call_id(tmp_path: Path) -> None:
    probe_python = tmp_path / "probe_python_pack.py"
    _write_executable(
        probe_python,
        """#!/usr/bin/env python3
import json
import sys

payload = json.load(sys.stdin)
runtime = payload["runtime_context"]
print(json.dumps({
    "ok": True,
    "model_visible_text": "资料包已返回。",
    "runtime_call_id": runtime["call_id"],
    "dispatch_id": runtime["dispatch_id"],
    "worker_call_id": runtime["worker_call_id"],
    "tool_call_id": runtime["tool_call_id"],
}, ensure_ascii=False))
""",
    )
    result = _run_tool(
        tool_name="claw_get_news_pack",
        ctx=_runtime_ctx(worker_id="news_analyst"),
        params={"ticker": "00700.HK", "market": "HK"},
        env_overrides={"CLAW_TRADE_FRONTLINE_TOOL_PYTHON": str(probe_python)},
    )

    assert result.get("isError") is False
    details = result.get("details")
    assert isinstance(details, dict)
    assert details["dispatch_id"] == "call-1"
    assert details["worker_call_id"] == "call-1"
    assert details["tool_call_id"] == "call"
    assert details["runtime_call_id"] == "call-1__tool-call"


def test_pack_runtime_blocked_exit_zero_is_reported_as_tool_error(tmp_path: Path) -> None:
    probe_python = tmp_path / "probe_python_pack.sh"
    payload = {
        "ok": False,
        "error": {
            "code": "pack_runtime_blocked",
            "message": "DATA_GATEWAY_MONGODB_URI/CN_A_MONGODB_URI is not configured",
        },
    }
    _write_executable(
        probe_python,
        f"""#!/usr/bin/env bash
echo {json.dumps(json.dumps(payload, ensure_ascii=False))}
exit 0
""",
    )
    result = _run_tool(
        tool_name="claw_get_news_pack",
        ctx=_runtime_ctx(worker_id="news_analyst"),
        params={"ticker": "00700.HK", "market": "HK"},
        env_overrides={"CLAW_TRADE_FRONTLINE_TOOL_PYTHON": str(probe_python)},
    )
    assert result.get("isError") is True
    assert _error_code(result) == "pack_runtime_blocked"
    text = result["content"][0]["text"]
    assert text.startswith("资料包工具失败：pack_runtime_blocked。")
    assert "DATA_GATEWAY_MONGODB_URI/CN_A_MONGODB_URI is not configured" in text


def test_data_pack_error_readiness_does_not_become_tool_error_status(tmp_path: Path) -> None:
    probe_python = tmp_path / "probe_python_pack.sh"
    payload = {
        "ok": True,
        "schema_version": "data_result_pack.v1",
        "tool_name": "claw_get_social_pack",
        "status": "error",
        "readiness": {"status": "error"},
        "model_visible_text": "舆情资料包结果：错误。数据缺口：来源无权限。",
    }
    _write_executable(
        probe_python,
        f"""#!/usr/bin/env bash
echo {json.dumps(json.dumps(payload, ensure_ascii=False))}
""",
    )

    result = _run_tool(
        tool_name="claw_get_social_pack",
        ctx=_runtime_ctx(worker_id="social_analyst"),
        params={"ticker": "00700.HK", "market": "HK"},
        env_overrides={"CLAW_TRADE_FRONTLINE_TOOL_PYTHON": str(probe_python)},
    )

    assert result.get("isError") is False
    assert result["content"][0]["text"] == payload["model_visible_text"]
    details = result.get("details")
    assert isinstance(details, dict)
    assert "status" not in details
    assert details["data_pack_status"] == "error"
    assert details["readiness"] == {"status": "error"}


@pytest.mark.parametrize(
    ("status", "ok", "brief_field", "expected_is_error"),
    (
        ("partial", True, "model_visible_text", False),
        ("insufficient", True, "model_visible_text", False),
        ("blocked", False, "model_visible_text", True),
    ),
)
def test_partial_pack_model_text_does_not_present_tool_success_as_data_readiness(
    tmp_path: Path,
    status: str,
    ok: bool,
    brief_field: str,
    expected_is_error: bool,
) -> None:
    probe_python = tmp_path / "probe_python_pack.sh"
    model_visible_text = (
        f"00700.HK 的 HK 舆情资料包资料就绪度为{status}："
        "没有原始社交事实源。数据缺口：搜索发现不能替代舆情事实。"
    )
    payload = {
        "ok": ok,
        "schema_version": "data_result_pack.v1",
        "tool_name": "claw_get_social_pack",
        brief_field: model_visible_text,
        "readiness": {"status": status, "reason": "没有原始社交事实源。"},
        "provider_attempts": [
            {
                "provider": "local_pack_provider",
                "status": "config_blocked",
                "raw_payload_ref": "viking://resources/workflow/run/frontline/social/raw.json",
            }
        ],
    }
    if not ok:
        payload["error"] = {
            "code": "pack_runtime_blocked",
            "message": "资料源配置阻断，但 worker-visible brief 已说明可见缺口。",
        }
    _write_executable(
        probe_python,
        f"""#!/usr/bin/env bash
echo {json.dumps(json.dumps(payload, ensure_ascii=False))}
""",
    )

    result = _run_tool(
        tool_name="claw_get_social_pack",
        ctx=_runtime_ctx(worker_id="social_analyst"),
        params={"ticker": "00700.HK", "market": "HK"},
        env_overrides={"CLAW_TRADE_FRONTLINE_TOOL_PYTHON": str(probe_python)},
    )

    assert result.get("isError") is expected_is_error
    text = result["content"][0]["text"]
    assert text == model_visible_text
    assert "资料包工具已返回" not in text
    assert "资料就绪状态为" not in text
    assert "这只证明工具调用完成" not in text
    assert "不证明资料覆盖完成" not in text
    assert "viking://" not in text
    assert "provider_attempts" not in text
    assert result.get("details", {}).get("provider_attempts") == payload["provider_attempts"]


def test_provider_total_timeout_contract_keeps_subprocess_plus_five_seconds_buffer() -> None:
    source = PLUGIN_PATH.read_text(encoding="utf-8")
    assert "DEFAULT_PROVIDER_TOTAL_TIMEOUT_MS" in source
    assert "SUBPROCESS_TIMEOUT_BUFFER_MS = 5000" in source
    assert "Math.max(totalTimeout + SUBPROCESS_TIMEOUT_BUFFER_MS, DEFAULT_MIN_SUBPROCESS_TIMEOUT_MS)" in source


def test_market_pack_uses_extended_timeout_contract() -> None:
    source = PLUGIN_PATH.read_text(encoding="utf-8")
    assert "DEFAULT_MARKET_PACK_TIMEOUT_MS = 120000" in source
    assert "DEFAULT_CRYPTO_MARKET_PACK_TIMEOUT_MS = 120000" in source
    assert "function resolvePackTotalTimeoutMs(config, toolInput, toolName)" in source
    assert "toolName === TOOL_NAMES.clawGetMarketPack" in source
    assert "domainToolTimeoutMs(DEFAULT_MARKET_PACK_TIMEOUT_MS)" in source
