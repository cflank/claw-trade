from __future__ import annotations

import json
from pathlib import Path

from claw_trade.production.openclaw_config import (
    PLUGIN_IDS,
    WEIXIN_PLUGIN_LOAD_PATH,
    WORKER_IDS,
    prepare_openclaw_config,
)


def test_prepare_openclaw_config_uses_runtime_asset_paths(tmp_path: Path) -> None:
    agents_root = tmp_path / "runtime-assets" / "agents"
    plugins_root = tmp_path / "runtime-assets" / "openclaw_plugins"
    _write_agents_root(agents_root)
    _write_plugins_root(plugins_root)
    config_path = tmp_path / "openclaw-state" / "openclaw.json"

    prepare_openclaw_config(
        config_path=config_path,
        agents_root=agents_root,
        plugins_root=plugins_root,
        env={},
    )

    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert payload["agents"]["defaults"]["workspace"] == str(agents_root)
    workers = {item["id"]: item for item in payload["agents"]["list"]}
    assert workers["market_analyst"]["workspace"] == str(agents_root / "market_analyst")
    assert workers["market_analyst"]["skills"] == ["claw-trade-stage"]
    assert str(plugins_root / "claw-trade-frontline-tools") in payload["plugins"]["load"]["paths"]
    assert str(plugins_root / WEIXIN_PLUGIN_LOAD_PATH) in payload["plugins"]["load"]["paths"]


def test_prepare_openclaw_config_preserves_existing_model_when_env_is_empty(tmp_path: Path) -> None:
    agents_root = tmp_path / "runtime-assets" / "agents"
    plugins_root = tmp_path / "runtime-assets" / "openclaw_plugins"
    _write_agents_root(agents_root)
    _write_plugins_root(plugins_root)
    config_path = tmp_path / "openclaw-state" / "openclaw.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(
            {
                "agents": {"defaults": {"model": {"primary": "deepseek/deepseek-chat"}}},
                "models": {"providers": {"deepseek": {"apiKey": "saved", "models": []}}},
            }
        ),
        encoding="utf-8",
    )

    prepare_openclaw_config(
        config_path=config_path,
        agents_root=agents_root,
        plugins_root=plugins_root,
        env={},
    )

    payload = json.loads(config_path.read_text(encoding="utf-8"))
    assert payload["agents"]["defaults"]["model"]["primary"] == "deepseek/deepseek-chat"
    assert payload["models"]["providers"]["deepseek"]["apiKey"] == "saved"


def _write_agents_root(root: Path) -> None:
    for agent_id in ("ui_chat", "ui_worker_chat"):
        agent_root = root / agent_id
        agent_root.mkdir(parents=True, exist_ok=True)
        (agent_root / "AGENTS.md").write_text(f"# {agent_id}\n", encoding="utf-8")
    for worker_id in WORKER_IDS:
        worker_root = root / worker_id / "skills"
        worker_root.mkdir(parents=True, exist_ok=True)
        (worker_root / "manifest.yaml").write_text(
            "skills:\n  - path: claw-trade-stage/SKILL.md\n",
            encoding="utf-8",
        )


def _write_plugins_root(root: Path) -> None:
    for plugin_id in PLUGIN_IDS:
        plugin_root = root / plugin_id
        plugin_root.mkdir(parents=True, exist_ok=True)
        (plugin_root / "index.js").write_text("export default {};\n", encoding="utf-8")
    weixin_root = root / WEIXIN_PLUGIN_LOAD_PATH
    weixin_root.mkdir(parents=True, exist_ok=True)
    (weixin_root / "openclaw.plugin.json").write_text('{"id":"openclaw-weixin"}\n', encoding="utf-8")
