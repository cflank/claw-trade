from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

FORBIDDEN_TOOL_INPUT_KEYS = {"provider", "path", "api_name", "url", "header", "token"}
DATA_NEED_TOOL_NAME = "claw_request_data"


def test_frontline_data_need_tool_schema_hides_provider_details() -> None:
    tools = _registered_frontline_tools()
    schema = tools.get(DATA_NEED_TOOL_NAME)
    assert schema is not None, f"{DATA_NEED_TOOL_NAME} is not registered on the frontline OpenClaw plugin"

    forbidden = _forbidden_schema_keys(schema, FORBIDDEN_TOOL_INPUT_KEYS)
    assert forbidden == set()


def _registered_frontline_tools() -> dict[str, dict[str, Any]]:
    repo_root = Path(__file__).resolve().parents[2]
    plugin_path = repo_root / "openclaw_plugins" / "claw-trade-frontline-tools" / "index.js"
    script = f"""
import plugin from {json.dumps(str(plugin_path))};
const tools = {{}};
const api = {{
  registerTool(factory) {{
    const tool = factory({{ singleWorkerCommand: {{}} }});
    tools[tool.name] = tool.parameters ?? null;
  }},
  on() {{}},
}};
plugin.register(api);
process.stdout.write(JSON.stringify(tools));
"""
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def _forbidden_schema_keys(value: Any, forbidden: set[str]) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key in forbidden:
                found.add(key)
            if key in {"properties", "$defs", "definitions"} and isinstance(child, dict):
                found.update(set(child) & forbidden)
            found.update(_forbidden_schema_keys(child, forbidden))
    elif isinstance(value, list):
        for child in value:
            if isinstance(child, str) and child in forbidden:
                found.add(child)
            found.update(_forbidden_schema_keys(child, forbidden))
    return found
