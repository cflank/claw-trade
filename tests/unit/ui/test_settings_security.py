from __future__ import annotations

from pathlib import Path

import pytest

from claw_trade.ui_backend.channel_bridge import ChannelBridge
from claw_trade.ui_backend.data_source_settings import DataSourceSettingsService, allowed_data_source_env_keys
from claw_trade.ui_backend.llm_settings_bridge import LlmSettingsBridge
from claw_trade.ui_backend.settings_service import EnvLocalAllowlistWriter, UiBoundaryError
from claw_trade.ui_contracts.constants import PRODUCT_ERROR_CODES


class _FakeLlmClient:
    def config_schema_lookup(self, *, path):
        return {"path": path}

    def config_get(self, *, paths):
        _ = paths
        return {
            "revision": "rev-1",
            "agents": {"defaults": {"model": "deepseek-chat"}},
            "models": {"providers": {"deepseek": {"api_key": "sk-test-0001"}}},
        }

    def models_list(self, *, provider=None):
        _ = provider
        return []

    def models_status(self, *, provider=None, as_json=True):
        _ = (provider, as_json)
        return {}

    def config_patch(self, *, expected_settings_version, patch):
        _ = (expected_settings_version, patch)
        return {"newHash": "hash-1"}

    def models_auth_status(self, *, provider, model=None, endpoint_url=None, probe=True):
        _ = (provider, model, endpoint_url, probe)
        return {"ok": True}


class _FakeChannelClient:
    def plugins_list(self):
        return [{"id": "openclaw-weixin", "enabled": True, "channels": ["openclaw-weixin"]}]

    def channels_status(self, *, probe=False):
        _ = probe
        return {"channels": {"openclaw-weixin": {"state": "connected"}}}

    def channels_capabilities(self, *, channel):
        _ = channel
        return {"media": True}

    def config_patch(self, *, expected_settings_version=None, patch=None):
        _ = (expected_settings_version, patch)
        return {"restartRequired": False}


def test_env_allowlist_writer_blocks_non_allowlisted_key(tmp_path: Path) -> None:
    writer = EnvLocalAllowlistWriter(
        tmp_path / ".env.local",
        allowed_keys=("ALLOWED_KEY",),
    )
    with pytest.raises(UiBoundaryError) as exc:
        writer.write_allowed_env_keys({"NOT_ALLOWED": "1"})
    assert exc.value.code == "INVALID_INPUT"


def test_env_allowlist_writer_updates_atomically_with_backup(tmp_path: Path) -> None:
    env_file = tmp_path / ".env.local"
    env_file.write_text("ALLOWED_KEY=old\nOTHER=keep\n", encoding="utf-8")
    writer = EnvLocalAllowlistWriter(env_file, allowed_keys=("ALLOWED_KEY",))
    writer.write_allowed_env_keys({"ALLOWED_KEY": "new"})
    assert "ALLOWED_KEY=new" in env_file.read_text(encoding="utf-8")
    assert (tmp_path / ".env.local.bak").exists()


def test_settings_payloads_do_not_leak_internal_terms() -> None:
    data_service = DataSourceSettingsService()
    llm_bridge = LlmSettingsBridge(_FakeLlmClient())
    channel_bridge = ChannelBridge(_FakeChannelClient())

    data_payload = data_service.list_data_sources()
    llm_payload = llm_bridge.load_llm_settings("deepseek")
    channel_payload = channel_bridge.get_channel_status(probe=True)


def test_ui_boundary_errors_only_use_product_error_codes() -> None:
    with pytest.raises(UiBoundaryError):
        raise UiBoundaryError("INVALID_INPUT", "x")
    assert "INVALID_INPUT" in PRODUCT_ERROR_CODES
    assert "NOTIFICATION_UNAVAILABLE" in PRODUCT_ERROR_CODES
    assert "DATASOURCE_TEST_FAILED" in PRODUCT_ERROR_CODES
    assert "FILE_SEND_UNSUPPORTED" in PRODUCT_ERROR_CODES
    assert "ASSISTANT_UNAVAILABLE" in PRODUCT_ERROR_CODES


def test_data_source_env_allowlist_is_declared_for_supported_builtin_types() -> None:
    keys = allowed_data_source_env_keys()
    assert "TUSHARE_TOKEN" in keys
    assert "COINGLASS_API_KEY" in keys

