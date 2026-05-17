from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_PATH = REPO_ROOT / "openclaw_plugins" / "claw-trade-frontline-tools" / "index.js"


def _runtime_ctx(
    worker_id: str,
    *,
    tool_name: str,
    call_id: str,
    ticker: str = "600519.SH",
    market: str = "CN_A",
) -> dict[str, object]:
    return {
        "singleWorkerCommand": {
            "run_id": "it-plugin-run",
            "stage": "frontline",
            "worker_id": worker_id,
            "call_id": call_id,
            "evidence_dir": f"/tmp/evidence/it-plugin-run/{call_id}",
            "runtime_vars": {
                "ticker": ticker,
                "market": market,
                "current_date": "2026-05-09",
            },
        }
    }


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    mode = path.stat().st_mode
    path.chmod(mode | stat.S_IXUSR)


def _run_tool(
    *,
    tool_name: str,
    ctx: dict[str, object],
    params: dict[str, object],
    env_overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
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
        ["node", "--input-type=module", "-e", script, json.dumps(ctx), json.dumps(params), tool_name],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return json.loads(completed.stdout)


def test_t_test_004_openclaw_plugin_data_pack_tools_receive_runtime_context(tmp_path: Path) -> None:
    capture_dir = tmp_path / "captures"
    capture_dir.mkdir(parents=True, exist_ok=True)
    fake_python = tmp_path / "capture_python.py"
    _write_executable(
        fake_python,
        """#!/usr/bin/env python3
import json
import os
import re
import sys
from pathlib import Path

payload = json.load(sys.stdin)
runtime = payload.get("runtime_context", {})
tool_name = runtime.get("tool_name", "unknown")
safe_name = re.sub(r"[^A-Za-z0-9_.-]", "_", str(tool_name))
capture_dir = Path(os.environ["FRONTLINE_CAPTURE_DIR"])
capture_dir.mkdir(parents=True, exist_ok=True)
(capture_dir / f"{safe_name}.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
sys.stdout.write(json.dumps({"ok": True, "tool_name": tool_name}, ensure_ascii=False))
sys.stdout.write("\\n")
""",
    )

    tools = [
        ("market_market_data_pack", "market_analyst", "600519.SH", "CN_A"),
        ("crypto_market_data_pack", "market_analyst", "BTC", "CRYPTO"),
        ("fundamental_fundamentals_data_pack", "fundamental_analyst", "600519.SH", "CN_A"),
        ("crypto_fundamental_data_pack", "fundamental_analyst", "BTC", "CRYPTO"),
        ("crypto_news_data_pack", "news_analyst", "BTC", "CRYPTO"),
        ("crypto_social_sentiment_pack", "social_analyst", "BTC", "CRYPTO"),
        ("news_news_data_pack", "news_analyst", "600519.SH", "CN_A"),
        ("social_social_sentiment_pack", "social_analyst", "600519.SH", "CN_A"),
    ]

    for index, (tool_name, worker_id, ticker, market) in enumerate(tools, start=1):
        call_id = f"it-plugin-call-{index}"
        result = _run_tool(
            tool_name=tool_name,
            ctx=_runtime_ctx(worker_id, tool_name=tool_name, call_id=call_id, ticker=ticker, market=market),
            params={"ticker": ticker, "market": market},
            env_overrides={
                "CLAW_TRADE_FRONTLINE_TOOL_PYTHON": str(fake_python),
                "FRONTLINE_CAPTURE_DIR": str(capture_dir),
            },
        )
        assert result.get("isError") is False
        details = result.get("details")
        assert isinstance(details, dict)
        assert details.get("ok") is True
        assert details.get("tool_name") == tool_name

    for index, (tool_name, worker_id, _ticker, _market) in enumerate(tools, start=1):
        capture_path = capture_dir / f"{tool_name}.json"
        assert capture_path.exists(), f"missing capture for {tool_name}"
        payload = json.loads(capture_path.read_text(encoding="utf-8"))
        runtime_context = payload.get("runtime_context")
        assert isinstance(runtime_context, dict)
        assert runtime_context.get("run_id") == "it-plugin-run"
        assert runtime_context.get("stage") == "frontline"
        assert runtime_context.get("worker_id") == worker_id
        assert runtime_context.get("call_id") == f"it-plugin-call-{index}"
        assert runtime_context.get("dispatch_id") == f"it-plugin-call-{index}"
        assert runtime_context.get("tool_call_id") == "call"
        assert runtime_context.get("tool_name") == tool_name
        evidence_root = runtime_context.get("evidence_root")
        assert isinstance(evidence_root, str)
        assert evidence_root.endswith("/pack-tool-evidence")
        current_time = runtime_context.get("current_time")
        assert isinstance(current_time, str) and current_time.strip()


def test_frontline_plugin_domain_tool_timeout_never_below_provider_total_budget() -> None:
    text = PLUGIN_PATH.read_text(encoding="utf-8")

    assert 'function providerTotalTimeoutMs()' in text
    assert 'positiveIntegerEnv("CN_A_PROVIDER_TOTAL_TIMEOUT_MS", DEFAULT_PROVIDER_TOTAL_TIMEOUT_MS)' in text
    assert 'function domainToolTimeoutMs(domainTotalTimeoutMs)' in text
    assert "return Math.max(totalTimeout, providerTotalTimeoutMs());" in text
    assert 'positiveSecondsEnvToMs("CN_A_NEWS_TOTAL_TIMEOUT_SECONDS", DEFAULT_NEWS_TOTAL_TIMEOUT_MS)' in text
    assert 'positiveSecondsEnvToMs("CN_A_SOCIAL_PACK_TIMEOUT_SECONDS", DEFAULT_SOCIAL_PACK_TIMEOUT_MS)' in text
