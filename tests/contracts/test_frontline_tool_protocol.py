from __future__ import annotations

import json
import os
import stat
import subprocess
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PATH = REPO_ROOT / "openclaw_plugins" / "claw-trade-frontline-tools" / "index.js"


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
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def _registered_tools() -> list[dict[str, object]]:
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
  on() {{}},
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
    return json.loads(completed.stdout)


def _run_before_dispatch_hook(text: str, *, fetch_impl: str) -> dict[str, object]:
    script = f"""
import plugin from {json.dumps(str(PLUGIN_PATH))};
globalThis.fetch = {fetch_impl};
process.env.CLAW_TRADE_UI_INBOUND_URL = "http://127.0.0.1:5175/api/ui/channel-inbound-message";
const api = {{
  registerTool() {{}},
  on(name, handler) {{
    if (name === "before_dispatch") {{
      globalThis.__beforeDispatchHook = handler;
    }}
  }},
}};
plugin.register(api);
const hook = globalThis.__beforeDispatchHook;
const result = await hook(
  {{
    channel: "openclaw-weixin",
    body: {json.dumps(text)},
    content: {json.dumps(text)},
    sessionKey: "session-1",
    timestamp: 1770000000000,
  }},
  {{
    channelId: "openclaw-weixin",
    accountId: "account-1",
    conversationId: "sender-1",
    sessionKey: "session-1",
  }},
);
process.stdout.write(JSON.stringify({{ result: result ?? null }}));
"""
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_wechat_report_reply_bridge_failure_does_not_fall_back_to_default_agent() -> None:
    result = _run_before_dispatch_hook(
        "发送完整报告",
        fetch_impl='async () => { throw new Error("timeout"); }',
    )

    assert result["result"]["handled"] is True
    assert result["result"]["text"].startswith("报告请求已收到")
    assert "/report" not in result["result"]["text"]


def test_wechat_ordinary_reply_bridge_failure_can_fall_back_to_default_agent() -> None:
    result = _run_before_dispatch_hook(
        "你好",
        fetch_impl='async () => { throw new Error("timeout"); }',
    )

    assert result["result"] is None


def test_wechat_ordinary_reply_bridge_handled_false_falls_back_to_default_agent() -> None:
    result = _run_before_dispatch_hook(
        "你好",
        fetch_impl="async () => ({ ok: true, json: async () => ({ handled: false }) })",
    )

    assert result["result"] is None


def test_wechat_deferred_ordinary_reply_does_not_duplicate_processing_ack() -> None:
    result = _run_before_dispatch_hook(
        "你好",
        fetch_impl='async () => ({ ok: true, json: async () => ({ handled: true, replyText: "收到，正在处理。", state: "chat_processing", deferFinalReply: true }) })',
    )

    assert result["result"]["handled"] is True
    assert result["result"]["text"] == "收到，正在处理。"


def _run_tool(
    *,
    tool_name: str,
    ctx: dict[str, object],
    params: dict[str, object] | None = None,
    env_overrides: dict[str, str] | None = None,
) -> dict[str, object]:
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
  on() {{}},
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
        ["node", "--input-type=module", "-e", script, json.dumps(ctx), json.dumps(params or {}), tool_name],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return json.loads(completed.stdout)


def _error_code(result: dict[str, object]) -> str:
    details = result.get("details", {})
    assert isinstance(details, dict)
    error = details.get("error", {})
    assert isinstance(error, dict)
    code = error.get("code")
    assert isinstance(code, str)
    return code


def test_plugin_registers_only_data_need_tool() -> None:
    tools = _registered_tools()
    assert [item["name"] for item in tools] == ["claw_request_data"]
    tool = tools[0]
    assert tool["schemaType"] == "object"
    assert tool["additionalProperties"] is False
    assert set(tool["fields"]) == {
        "item",
        "instrument",
        "market",
        "time_range",
        "granularity",
        "purpose",
        "priority",
    }


def test_plugin_manifest_contract_exposes_only_data_need_tool() -> None:
    manifest = json.loads((PLUGIN_PATH.parent / "openclaw.plugin.json").read_text(encoding="utf-8"))
    assert manifest["contracts"]["tools"] == ["claw_request_data"]


def test_registered_tools_are_exactly_current_data_tool() -> None:
    names = {str(item["name"]) for item in _registered_tools()}
    assert names == {"claw_request_data"}


def test_missing_single_worker_command_returns_runtime_context_missing() -> None:
    result = _run_tool(
        tool_name="claw_request_data",
        ctx={},
        params={"item": "日线", "purpose": "market_report"},
    )
    assert result.get("isError") is True
    assert _error_code(result) == "TOOL_RUNTIME_CONTEXT_MISSING"


def test_frontline_stage_does_not_use_plugin_worker_whitelist(tmp_path: Path) -> None:
    marker = tmp_path / "python_called.txt"
    probe_python = tmp_path / "probe_python.sh"
    _write_executable(
        probe_python,
        f"""#!/usr/bin/env bash
cat > /dev/null
echo called > {marker}
echo '{{"ok": true}}'
""",
    )
    result = _run_tool(
        tool_name="claw_request_data",
        ctx=_runtime_ctx(worker_id="trader"),
        params={"item": "日线", "purpose": "market_report"},
        env_overrides={"CLAW_TRADE_FRONTLINE_TOOL_PYTHON": str(probe_python)},
    )
    assert result.get("isError") in {False, None}
    assert marker.exists() is True


def test_data_need_uses_openclaw_market_tool_python_alias(tmp_path: Path) -> None:
    marker = tmp_path / "python_called.txt"
    probe_python = tmp_path / "probe_python.sh"
    _write_executable(
        probe_python,
        f"""#!/usr/bin/env bash
cat > /dev/null
echo called > {marker}
echo '{{"ok": true}}'
""",
    )

    result = _run_tool(
        tool_name="claw_request_data",
        ctx=_runtime_ctx(worker_id="market_analyst"),
        params={"item": "日线", "purpose": "market_report"},
        env_overrides={"OPENCLAW_MARKET_TOOL_PYTHON": str(probe_python)},
    )

    assert result.get("isError") in {False, None}
    assert marker.exists() is True


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
        tool_name="claw_request_data",
        ctx=_runtime_ctx(worker_id="news_analyst", stage="investment_debate"),
        params={"item": "公司新闻", "purpose": "news_report"},
        env_overrides={"CLAW_TRADE_FRONTLINE_TOOL_PYTHON": str(probe_python)},
    )
    assert result.get("isError") is True
    assert _error_code(result) == "TOOL_CONTEXT_INCOMPLETE"
    assert marker.exists() is False


def test_data_need_forbids_provider_execution_details_without_spawning_python(tmp_path: Path) -> None:
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
        tool_name="claw_request_data",
        ctx=_runtime_ctx(worker_id="market_analyst"),
        params={
            "item": "日线",
            "purpose": "market_report",
            "provider": "tushare",
        },
        env_overrides={"CLAW_TRADE_FRONTLINE_TOOL_PYTHON": str(probe_python)},
    )
    assert result.get("isError") is True
    assert _error_code(result) == "TOOL_PARAMS_INVALID"
    assert marker.exists() is False


def test_successful_data_need_passes_clean_context_to_python(tmp_path: Path) -> None:
    stdin_path = tmp_path / "stdin.json"
    probe_python = tmp_path / "probe_python.sh"
    payload = {
        "ok": True,
        "schema_version": "data_need_result.v1",
        "tool_name": "claw_request_data",
        "model_visible_text": "数据需求结果：可用。",
    }
    _write_executable(
        probe_python,
        f"""#!/usr/bin/env bash
cat > {stdin_path}
echo {json.dumps(json.dumps(payload, ensure_ascii=False))}
""",
    )

    result = _run_tool(
        tool_name="claw_request_data",
        ctx=_runtime_ctx(
            worker_id="policy_analyst",
            runtime_vars={
                "ticker": "600519.SH",
                "market": "CN_A",
                "profile": "CN_A",
                "company_name": "贵州茅台",
                "currency": "CNY",
            },
        ),
        params={
            "item": "事件日历",
            "purpose": "policy_report",
            "priority": "required",
        },
        env_overrides={"CLAW_TRADE_FRONTLINE_TOOL_PYTHON": str(probe_python)},
    )

    assert result.get("isError") is False
    assert result["content"][0]["text"] == payload["model_visible_text"]
    stdin_payload = json.loads(stdin_path.read_text(encoding="utf-8"))
    assert stdin_payload["tool_input"] == {
        "item": "事件日历",
        "instrument": "600519.SH",
        "market": "CN_A",
        "purpose": "policy_report",
        "priority": "required",
    }
    assert stdin_payload["runtime_context"]["tool_name"] == "claw_request_data"
    assert stdin_payload["runtime_context"]["worker_id"] == "policy_analyst"
    current_time = datetime.fromisoformat(str(stdin_payload["runtime_context"]["current_time"]).replace("Z", "+00:00"))
    deadline_at = datetime.fromisoformat(str(stdin_payload["runtime_context"]["deadline_at"]).replace("Z", "+00:00"))
    assert current_time.tzinfo is not None
    assert deadline_at.tzinfo is not None
    assert 175 <= (deadline_at - current_time.astimezone(timezone.utc)).total_seconds() <= 185
    assert "pack_domain" not in stdin_payload["runtime_context"]
    forbidden = {"provider", "path", "api_name", "url", "header", "token", "api_id"}
    assert forbidden.isdisjoint(stdin_payload["tool_input"])


def test_data_need_normalizes_lowercase_market_before_python(tmp_path: Path) -> None:
    stdin_path = tmp_path / "stdin.json"
    probe_python = tmp_path / "probe_python.sh"
    payload = {
        "ok": True,
        "schema_version": "data_need_result.v1",
        "tool_name": "claw_request_data",
        "model_visible_text": "数据需求结果：可用。",
    }
    _write_executable(
        probe_python,
        f"""#!/usr/bin/env bash
cat > {stdin_path}
echo {json.dumps(json.dumps(payload, ensure_ascii=False))}
""",
    )

    result = _run_tool(
        tool_name="claw_request_data",
        ctx=_runtime_ctx(
            worker_id="social_analyst",
            runtime_vars={
                "ticker": "ALLO/USDT",
                "market": "CRYPTO",
                "profile": "CRYPTO",
                "company_name": "Allora",
                "currency": "USDT",
            },
        ),
        params={
            "item": "社交情绪",
            "instrument": "ALLO/USDT",
            "market": "crypto",
            "purpose": "social_report",
        },
        env_overrides={"CLAW_TRADE_FRONTLINE_TOOL_PYTHON": str(probe_python)},
    )

    assert result.get("isError") is False
    stdin_payload = json.loads(stdin_path.read_text(encoding="utf-8"))
    assert stdin_payload["tool_input"]["market"] == "CRYPTO"


def test_data_need_runtime_error_text_tells_worker_not_to_fill_missing_results(tmp_path: Path) -> None:
    probe_python = tmp_path / "probe_python.sh"
    payload = {
        "ok": False,
        "error": {
            "code": "TOOL_SUBPROCESS_TIMEOUT",
            "message": "claw_request_data python subprocess timed out",
        },
    }
    _write_executable(
        probe_python,
        f"""#!/usr/bin/env bash
echo {json.dumps(json.dumps(payload, ensure_ascii=False))}
""",
    )

    result = _run_tool(
        tool_name="claw_request_data",
        ctx=_runtime_ctx(
            worker_id="market_analyst",
            runtime_vars={
                "ticker": "600519.SH",
                "market": "CN_A",
                "profile": "CN_A",
                "company_name": "贵州茅台",
                "currency": "CNY",
            },
        ),
        params={"item": "日线", "purpose": "market_report"},
        env_overrides={"CLAW_TRADE_FRONTLINE_TOOL_PYTHON": str(probe_python)},
    )

    assert result.get("isError") is True
    text = result["content"][0]["text"]
    assert "数据工具执行超时" in text
    assert "不要补写不存在的数据结果" in text
    assert "材料外内容直接跳过" not in text
    assert "数据限制" not in text
    assert "缺口" not in text


def test_data_need_subprocess_timeout_returns_when_descendant_keeps_pipe_open(tmp_path: Path) -> None:
    probe_python = tmp_path / "probe_python.sh"
    _write_executable(
        probe_python,
        """#!/usr/bin/env bash
(sleep 60) &
echo started >&2
sleep 60
""",
    )

    result = _run_tool(
        tool_name="claw_request_data",
        ctx=_runtime_ctx(
            worker_id="market_analyst",
            runtime_vars={
                "ticker": "ALLO/USDT",
                "market": "CRYPTO",
                "profile": "CRYPTO",
                "company_name": "Allora",
                "currency": "USDT",
            },
        ),
        params={"item": "资金费率", "purpose": "market_report"},
        env_overrides={
            "CLAW_TRADE_FRONTLINE_TOOL_PYTHON": str(probe_python),
            "CLAW_TRADE_DATA_NEED_TOOL_BUDGET_SECONDS": "1",
            "CN_A_PROVIDER_TOTAL_TIMEOUT_MS": "1",
        },
    )

    assert result.get("isError") is True
    assert _error_code(result) == "TOOL_SUBPROCESS_TIMEOUT"
    assert "数据工具执行超时" in result["content"][0]["text"]


def test_data_need_subprocess_timeout_preserves_trace_file(tmp_path: Path) -> None:
    evidence_dir = tmp_path / "evidence" / "run-1" / "call-1"
    probe_script = tmp_path / "probe.py"
    probe_script.write_text(
        """
import json
import pathlib
import sys
import time

payload = json.load(sys.stdin)
ctx = payload["runtime_context"]
trace_path = (
    pathlib.Path(ctx["evidence_root"])
    / "data-layer"
    / "data-need-trace"
    / ctx["run_id"]
    / ctx["call_id"]
    / "events.jsonl"
)
trace_path.parent.mkdir(parents=True, exist_ok=True)
trace_path.write_text(
    '{"event":"probe_before_sleep","provider_id":"probe","catalog_endpoint_id":"probe.endpoint"}\\n',
    encoding="utf-8",
)
time.sleep(60)
""",
        encoding="utf-8",
    )
    probe_python = tmp_path / "probe_python.sh"
    _write_executable(
        probe_python,
        f"""#!/usr/bin/env bash
python3 {json.dumps(str(probe_script))}
""",
    )
    ctx = _runtime_ctx(
        worker_id="market_analyst",
        runtime_vars={
            "ticker": "BTC",
            "market": "CRYPTO",
            "profile": "CRYPTO",
            "company_name": "Bitcoin",
            "currency": "USDT",
        },
    )
    command = ctx["singleWorkerCommand"]
    assert isinstance(command, dict)
    command["evidence_dir"] = str(evidence_dir)

    result = _run_tool(
        tool_name="claw_request_data",
        ctx=ctx,
        params={"item": "链上", "purpose": "fundamental_report"},
        env_overrides={
            "CLAW_TRADE_FRONTLINE_TOOL_PYTHON": str(probe_python),
            "CLAW_TRADE_DATA_NEED_TOOL_BUDGET_SECONDS": "1",
            "CN_A_PROVIDER_TOTAL_TIMEOUT_MS": "1",
        },
    )

    trace_path = evidence_dir / "data-need-tool-evidence" / "data-layer" / "data-need-trace" / "run-1" / "call-1__tool-call" / "events.jsonl"
    events = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    assert result.get("isError") is True
    assert _error_code(result) == "TOOL_SUBPROCESS_TIMEOUT"
    assert events == [{"event": "probe_before_sleep", "provider_id": "probe", "catalog_endpoint_id": "probe.endpoint"}]


def test_data_need_error_text_hides_internal_execution_details_from_model(tmp_path: Path) -> None:
    probe_python = tmp_path / "probe_python.sh"
    payload = {
        "ok": False,
        "error": {
            "code": "data_need_runtime_blocked",
            "message": "provider=official_api_tushare path=/ api_name=hk_mins token=secret",
        },
    }
    _write_executable(
        probe_python,
        f"""#!/usr/bin/env bash
cat > /dev/null
echo {json.dumps(json.dumps(payload, ensure_ascii=False))}
""",
    )

    result = _run_tool(
        tool_name="claw_request_data",
        ctx=_runtime_ctx(worker_id="market_analyst"),
        params={"item": "分时", "purpose": "market_report", "priority": "required"},
        env_overrides={"CLAW_TRADE_FRONTLINE_TOOL_PYTHON": str(probe_python)},
    )

    assert result.get("isError") is True
    text = result["content"][0]["text"]
    assert "数据层运行时未能完成本次数据请求" in text
    assert "不要补写不存在的数据结果" in text
    for forbidden in ("official_api_tushare", "api_name", "hk_mins", "token", "secret", "path=/"):
        assert forbidden not in text


def test_successful_empty_data_need_result_is_blank_to_model(tmp_path: Path) -> None:
    probe_python = tmp_path / "probe_python.sh"
    payload = {
        "ok": True,
        "status": "missing",
        "need_satisfied": False,
        "model_visible_text": "",
        "readable_summary": "",
    }
    _write_executable(
        probe_python,
        f"""#!/usr/bin/env bash
cat > /dev/null
echo {json.dumps(json.dumps(payload, ensure_ascii=False))}
""",
    )

    result = _run_tool(
        tool_name="claw_request_data",
        ctx=_runtime_ctx(worker_id="market_analyst"),
        params={"item": "清算地图", "purpose": "market_report"},
        env_overrides={"CLAW_TRADE_FRONTLINE_TOOL_PYTHON": str(probe_python)},
    )

    assert result.get("isError") is False
    assert result["content"][0]["text"] == ""
    assert result["details"]["data_result_status"] == "missing"


def test_provider_total_timeout_contract_keeps_subprocess_with_completion_buffer() -> None:
    source = PLUGIN_PATH.read_text(encoding="utf-8")
    assert "DEFAULT_PROVIDER_TOTAL_TIMEOUT_MS" in source
    assert "CLAW_TRADE_DATA_NEED_TOOL_BUDGET_SECONDS" in source
    assert "SUBPROCESS_TIMEOUT_BUFFER_MS = 5000" in source
    assert "SUBPROCESS_TIMEOUT_CLOSE_GRACE_MS" in source
    assert "Math.max(totalTimeout + SUBPROCESS_TIMEOUT_BUFFER_MS, DEFAULT_MIN_SUBPROCESS_TIMEOUT_MS)" in source
