from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest
from claw_trade.ui_backend.channel_bridge import USER_CHANNEL_KIND, ChannelBridge
from claw_trade.ui_backend.settings_service import UiBoundaryError

from claw_trade.ui_backend import channel_bridge as channel_bridge_module


class _FakeChannelClient:
    def __init__(
        self,
        *,
        installed=True,
        enabled=True,
        connected=True,
        caps_error=False,
        has_text_api=True,
        has_file_api=True,
        send_file_success=True,
        qr_data_url=None,
        qr_session_key=None,
        wait_connected=False,
        start_already_connected=False,
        wait_already_connected=False,
        start_message="scan qr",
        wait_message="scan qr",
        start_exception: Exception | None = None,
        wait_exception: Exception | None = None,
    ) -> None:
        self.installed = installed
        self.enabled = enabled
        self.connected = connected
        self.caps_error = caps_error
        self.has_text_api = has_text_api
        self.has_file_api = has_file_api
        self.send_file_success = send_file_success
        self.qr_data_url = qr_data_url
        self.qr_session_key = qr_session_key
        self.wait_connected = wait_connected
        self.start_already_connected = start_already_connected
        self.wait_already_connected = wait_already_connected
        self.start_message = start_message
        self.wait_message = wait_message
        self.start_exception = start_exception
        self.wait_exception = wait_exception
        self.plugins_list_calls = 0
        self.config_patch_calls: list[dict[str, object]] = []
        self.channels_status_calls: list[dict[str, object]] = []
        self.web_login_start_calls: list[dict[str, object]] = []
        self.web_login_wait_calls: list[dict[str, object]] = []
        self.config_get_calls: list[tuple[str, ...]] = []

    def plugins_list(self):
        self.plugins_list_calls += 1
        if not self.installed:
            return []
        return [{"id": "openclaw-weixin", "enabled": self.enabled, "channels": ["openclaw-weixin"]}]

    def channels_status(self, *, probe=False):
        self.channels_status_calls.append({"probe": probe})
        if not self.installed or not self.enabled:
            return {"channels": {}}
        state = "connected" if self.connected else "disconnected"
        return {
            "channels": {
                "openclaw-weixin": {
                    "state": state,
                    "accountLabel": "测试号",
                    "lastConnectedAt": "2026-05-19T12:00:00Z",
                }
            }
        }

    def channels_capabilities(self, *, channel):
        assert channel == "openclaw-weixin"
        if self.caps_error:
            raise RuntimeError("unknown channel")
        return {"media": True}

    def config_patch(self, *, expected_settings_version=None, patch=None):
        self.config_patch_calls.append(
            {"expectedSettingsVersion": expected_settings_version, "patch": patch}
        )
        return {"restartRequired": True}

    def config_get(self, *, paths=()):
        self.config_get_calls.append(tuple(paths))
        return {"channels": {"openclaw-weixin": {"enabled": self.enabled}}}

    def web_login_start(self, *, force=False, timeout_ms=12000):
        self.web_login_start_calls.append({"force": force, "timeoutMs": timeout_ms})
        if self.start_exception:
            raise self.start_exception
        return {
            "qrDataUrl": self.qr_data_url,
            "message": self.start_message,
            "sessionKey": self.qr_session_key,
            "alreadyConnected": self.start_already_connected,
        }

    def web_login_wait(self, *, timeout_ms=1500, current_qr_data_url=None, session_key=None):
        self.web_login_wait_calls.append(
            {"timeoutMs": timeout_ms, "currentQrDataUrl": current_qr_data_url, "sessionKey": session_key}
        )
        if self.wait_exception:
            raise self.wait_exception
        return {
            "connected": self.wait_connected,
            "alreadyConnected": self.wait_already_connected,
            "qrDataUrl": self.qr_data_url,
            "message": self.wait_message,
        }

    def channels_send_text(self, *, channel, text, dedupe_key, to, account_id=None):
        if not self.has_text_api:
            raise AttributeError("missing")
        assert channel == "openclaw-weixin"
        assert text
        assert dedupe_key
        assert to
        return {"sent": True}

    def channels_send_file(self, *, channel, file_name, dedupe_key, to, payload=None, file_path=None, account_id=None):
        if not self.has_file_api:
            raise AttributeError("missing")
        assert channel == "openclaw-weixin"
        assert file_name
        assert payload or file_path
        assert dedupe_key
        assert to
        if not self.send_file_success:
            return {"sent": False}
        return {"sent": True, "messageId": "msg-1"}


class _FakeAccountStatusClient(_FakeChannelClient):
    def __init__(self, account: dict[str, object]) -> None:
        super().__init__(connected=False)
        self.account = account

    def channels_status(self, *, probe=False):
        self.channels_status_calls.append({"probe": probe})
        return {
            "channelAccounts": {"openclaw-weixin": [self.account]},
            "channelDefaultAccountId": {"openclaw-weixin": str(self.account["accountId"])},
        }


class _RestartingConfigClient(_FakeChannelClient):
    def __init__(self) -> None:
        super().__init__(connected=True)
        self.transient_status_failures = 1

    def channels_status(self, *, probe=False):
        if self.config_patch_calls and self.transient_status_failures > 0:
            self.channels_status_calls.append({"probe": probe})
            self.transient_status_failures -= 1
            raise RuntimeError("gateway starting")
        return super().channels_status(probe=probe)


class _DisabledStaleChannelClient(_FakeChannelClient):
    def __init__(self) -> None:
        super().__init__(enabled=False, connected=False)

    def channels_status(self, *, probe=False):
        self.channels_status_calls.append({"probe": probe})
        return {
            "channels": {
                "openclaw-weixin": {
                    "state": "disconnected",
                    "accountLabel": None,
                }
            }
        }


class _PluginEnabledNoChannelClient(_FakeChannelClient):
    def __init__(self) -> None:
        super().__init__(enabled=True, connected=False, qr_data_url="data:image/png;base64,real-qr")

    def channels_status(self, *, probe=False):
        self.channels_status_calls.append({"probe": probe})
        return {"channels": {}}


class _ChannelDisabledConfigClient(_FakeChannelClient):
    def __init__(self) -> None:
        super().__init__(enabled=True, connected=False, qr_data_url="data:image/png;base64,real-qr")

    def channels_status(self, *, probe=False):
        self.channels_status_calls.append({"probe": probe})
        return {"channels": {}}

    def config_get(self, *, paths=()):
        self.config_get_calls.append(tuple(paths))
        return {"channels": {"openclaw-weixin": {"enabled": False}}}

    def weixin_replacement_inspect(self, *, operation_id):  # type: ignore[no-untyped-def]
        assert operation_id is None
        return {"storedAccountIds": ["account-a"]}


class _RawFileResponseClient(_FakeChannelClient):
    def __init__(self, raw_response) -> None:  # type: ignore[no-untyped-def]
        super().__init__()
        self.raw_response = raw_response

    def channels_send_file(self, *, channel, file_name, dedupe_key, to, payload=None, file_path=None, account_id=None):
        _ = (channel, file_name, dedupe_key, to, payload, file_path, account_id)
        return self.raw_response


class _OneTimeCdnFailureFileClient(_FakeChannelClient):
    def __init__(self) -> None:
        super().__init__()
        self.dedupe_keys: list[str] = []

    def channels_send_file(self, *, channel, file_name, dedupe_key, to, payload=None, file_path=None, account_id=None):
        _ = (channel, file_name, to, payload, file_path, account_id)
        self.dedupe_keys.append(dedupe_key)
        if len(self.dedupe_keys) == 1:
            raise RuntimeError("CDN upload server error: status 500")
        return {"sent": True, "messageId": "msg-after-cdn-retry"}


class _AlwaysCdnFailureFileClient(_FakeChannelClient):
    def channels_send_file(self, *, channel, file_name, dedupe_key, to, payload=None, file_path=None, account_id=None):
        _ = (channel, file_name, dedupe_key, to, payload, file_path, account_id)
        raise RuntimeError("CDN upload server error: status 500")


def test_get_channel_status_hides_provider_channel_id() -> None:
    bridge = ChannelBridge(_FakeChannelClient())
    payload = bridge.get_channel_status(probe=True)
    assert payload["channelKind"] == USER_CHANNEL_KIND
    assert payload["state"] == "connected"
    assert "providerChannelId" not in payload


def test_get_channel_status_treats_running_configured_account_as_connected() -> None:
    bridge = ChannelBridge(
        _FakeAccountStatusClient(
            {
                "accountId": "wechat-bot-1",
                "configured": True,
                "running": True,
                "accountLabel": "微信账号",
                "lastConnectedAt": "2026-05-20T14:00:00Z",
            }
        )
    )

    payload = bridge.get_channel_status(probe=True)

    assert payload["state"] == "connected"
    assert payload["accountLabel"] == "微信账号"
    assert payload["canSendText"] is True


def test_get_channel_status_rejects_expired_running_account() -> None:
    class _ExpiredAccountClient(_FakeAccountStatusClient):
        def weixin_replacement_inspect(self, *, operation_id):  # type: ignore[no-untyped-def]
            assert operation_id is None
            return {"storedAccountIds": ["wechat-bot-1"]}

    bridge = ChannelBridge(
        _ExpiredAccountClient(
            {
                "accountId": "wechat-bot-1",
                "configured": True,
                "running": True,
                "connected": False,
                "lastError": "session expired (errcode -14)",
            }
        )
    )

    payload = bridge.get_channel_status(probe=True)

    assert payload["state"] == "disconnected"
    assert payload["canSendText"] is False
    assert payload["replacementRequired"] is True
    assert payload["lastErrorMessage"] == "微信登录已失效，请重新连接。"


def test_get_channel_status_rejects_multiple_running_accounts() -> None:
    class _MultipleAccountClient(_FakeChannelClient):
        def channels_status(self, *, probe=False):  # type: ignore[no-untyped-def]
            self.channels_status_calls.append({"probe": probe})
            accounts = [
                {"accountId": "old-bot", "configured": True, "running": True},
                {"accountId": "new-bot", "configured": True, "running": True},
            ]
            return {
                "channelAccounts": {"openclaw-weixin": accounts},
                "channelDefaultAccountId": {"openclaw-weixin": "old-bot"},
            }

        def weixin_replacement_inspect(self, *, operation_id):  # type: ignore[no-untyped-def]
            assert operation_id is None
            return {"storedAccountIds": ["old-bot", "new-bot"]}

    payload = ChannelBridge(_MultipleAccountClient()).get_channel_status(probe=True)

    assert payload["state"] == "disconnected"
    assert payload["canSendText"] is False
    assert payload["replacementRequired"] is True
    assert payload["lastErrorMessage"] == "检测到多个微信账号，请重新连接并只保留当前账号。"


def test_get_channel_status_ignores_stale_default_account_when_running_account_exists() -> None:
    class _StaleDefaultAccountClient(_FakeChannelClient):
        def channels_status(self, *, probe=False):  # type: ignore[no-untyped-def]
            self.channels_status_calls.append({"probe": probe})
            return {
                "channelAccounts": {
                    "openclaw-weixin": [
                        {
                            "accountId": "old-bot",
                            "configured": False,
                            "running": False,
                            "lastError": "not configured",
                        },
                        {
                            "accountId": "new-bot",
                            "configured": True,
                            "running": True,
                            "accountLabel": "新微信账号",
                        },
                    ]
                },
                "channelDefaultAccountId": {"openclaw-weixin": "old-bot"},
            }

    bridge = ChannelBridge(_StaleDefaultAccountClient(connected=False))

    payload = bridge.get_channel_status(probe=True)

    assert payload["state"] == "connected"
    assert payload["accountLabel"] == "新微信账号"


def test_get_channel_status_caches_light_homepage_status_without_caching_probe() -> None:
    client = _FakeChannelClient()
    bridge = ChannelBridge(client)

    first = bridge.get_channel_status(probe=False)
    first["state"] = "mutated"
    second = bridge.get_channel_status(probe=False)
    third = bridge.get_channel_status(probe=True)

    assert second["state"] == "connected"
    assert third["state"] == "connected"
    assert client.channels_status_calls == [{"probe": False}, {"probe": True}]


def test_get_channel_status_allows_tentative_file_send_when_probe_unavailable() -> None:
    bridge = ChannelBridge(_FakeChannelClient(caps_error=True))
    payload = bridge.get_channel_status(probe=True)
    assert payload["canSendText"] is True
    assert payload["canSendFile"] is True
    assert "未验证" in (payload["lastErrorMessage"] or "")


def test_get_channel_status_allows_tentative_file_send_when_media_capability_is_not_advertised() -> None:
    class _NoAdvertisedMediaClient(_FakeChannelClient):
        def channels_capabilities(self, *, channel):  # type: ignore[no-untyped-def]
            _ = channel
            return {}

    bridge = ChannelBridge(_NoAdvertisedMediaClient())
    payload = bridge.get_channel_status(probe=True)
    assert payload["canSendText"] is True
    assert payload["canSendFile"] is True
    assert "未验证" in (payload["lastErrorMessage"] or "")


def test_get_channel_status_does_not_repeat_unsupported_capability_probe() -> None:
    class _UnsupportedCapabilitiesClient(_FakeChannelClient):
        def __init__(self) -> None:
            super().__init__()
            self.capability_calls = 0

        def channels_capabilities(self, *, channel):  # type: ignore[no-untyped-def]
            self.capability_calls += 1
            raise RuntimeError("unknown method: channels.capabilities")

    client = _UnsupportedCapabilitiesClient()
    bridge = ChannelBridge(client)

    first = bridge.get_channel_status(probe=True)
    second = bridge.get_channel_status(probe=True)

    assert first["state"] == "connected"
    assert second["state"] == "connected"
    assert client.capability_calls == 1


def test_get_channel_status_when_plugin_missing_returns_disconnected() -> None:
    bridge = ChannelBridge(_FakeChannelClient(installed=False))
    payload = bridge.get_channel_status(probe=True)
    assert payload["state"] == "disconnected"
    assert "安装微信 ClawBot 插件" in (payload["lastErrorMessage"] or "")


def test_get_channel_status_can_include_real_qr_data_url() -> None:
    client = _FakeChannelClient(
        connected=False,
        qr_data_url="data:image/png;base64,real-qr",
        qr_session_key="login-session-1",
    )
    bridge = ChannelBridge(client)

    payload = bridge.get_channel_status(probe=True, include_qr=True)

    assert payload["state"] == "disconnected"
    assert payload["qrCodeImageDataUrl"] == "data:image/png;base64,real-qr"
    assert payload["qrCodeRefreshRequired"] is False
    assert payload["lastErrorMessage"] == "请用微信扫描二维码完成登录。"
    assert client.web_login_start_calls == [{"force": False, "timeoutMs": 12000}]


def test_get_channel_status_refreshes_qr_even_when_channel_is_connected() -> None:
    client = _FakeChannelClient(
        connected=True,
        qr_data_url="data:image/png;base64,reconnect-qr",
        qr_session_key="login-session-reconnect",
    )
    bridge = ChannelBridge(client)

    payload = bridge.get_channel_status(probe=True, include_qr=True, refresh_qr=True)

    assert payload["state"] == "disconnected"
    assert payload["qrCodeImageDataUrl"] == "data:image/png;base64,reconnect-qr"
    assert payload["qrCodeRefreshRequired"] is False
    assert payload["lastErrorMessage"] == "请用微信扫描二维码完成登录。"
    assert client.web_login_start_calls == [{"force": True, "timeoutMs": 12000}]


def test_get_channel_status_keeps_connected_when_refresh_returns_no_qr() -> None:
    client = _FakeChannelClient(connected=True)
    bridge = ChannelBridge(client)

    payload = bridge.get_channel_status(probe=True, include_qr=True, refresh_qr=True)

    assert payload["state"] == "connected"
    assert payload["canSendText"] is True
    assert payload["qrCodeImageDataUrl"] is None
    assert client.web_login_start_calls == [{"force": True, "timeoutMs": 12000}]


def test_get_channel_status_treats_start_already_connected_as_connected() -> None:
    client = _FakeChannelClient(
        connected=False,
        start_already_connected=True,
    )
    bridge = ChannelBridge(client)

    payload = bridge.get_channel_status(probe=True, include_qr=True)

    assert payload["state"] == "connected"
    assert payload["canSendText"] is True
    assert payload["qrCodeImageDataUrl"] is None
    assert client.web_login_start_calls == [{"force": False, "timeoutMs": 12000}]


def test_non_light_status_clears_stale_light_status_cache() -> None:
    client = _FakeChannelClient(connected=False, start_already_connected=True)
    bridge = ChannelBridge(client)

    first = bridge.get_channel_status()
    client.connected = True
    connected = bridge.get_channel_status(probe=True, include_qr=True)
    light = bridge.get_channel_status()

    assert first["state"] == "disconnected"
    assert connected["state"] == "connected"
    assert light["state"] == "connected"


def test_get_channel_status_treats_wechat_already_connected_message_as_connected() -> None:
    client = _FakeChannelClient(
        connected=False,
        start_already_connected=True,
        start_message="已连接过此 OpenClaw，无需重复连接。",
    )
    bridge = ChannelBridge(client)

    payload = bridge.get_channel_status(probe=True, include_qr=True)

    assert payload["state"] == "connected"
    assert payload["canSendText"] is True
    assert payload["qrCodeImageDataUrl"] is None
    assert client.web_login_start_calls == [{"force": False, "timeoutMs": 12000}]


def test_get_channel_status_polls_qr_login_with_session_key_until_connected() -> None:
    client = _FakeChannelClient(
        connected=False,
        qr_data_url="data:image/png;base64,real-qr",
        qr_session_key="login-session-1",
        wait_connected=True,
    )
    bridge = ChannelBridge(client)

    first = bridge.get_channel_status(probe=True, include_qr=True)
    second = {"state": "disconnected"}
    for _ in range(20):
        second = bridge.get_channel_status(probe=True, include_qr=True, poll_login=True)
        if second["state"] == "connected":
            break

    assert first["state"] == "disconnected"
    assert second["state"] == "connected"
    assert client.web_login_start_calls == [{"force": False, "timeoutMs": 12000}]
    assert client.web_login_wait_calls == [
        {
            "timeoutMs": 480000,
            "currentQrDataUrl": "data:image/png;base64,real-qr",
            "sessionKey": "login-session-1",
        }
    ]


def test_get_channel_status_polls_qr_login_until_already_connected() -> None:
    client = _FakeChannelClient(
        connected=False,
        qr_data_url="data:image/png;base64,real-qr",
        qr_session_key="login-session-1",
        wait_already_connected=True,
    )
    bridge = ChannelBridge(client)

    first = bridge.get_channel_status(probe=True, include_qr=True)
    second = {"state": "disconnected"}
    for _ in range(20):
        second = bridge.get_channel_status(probe=True, include_qr=True, poll_login=True)
        if second["state"] == "connected":
            break

    assert first["state"] == "disconnected"
    assert second["state"] == "connected"
    assert client.web_login_start_calls == [{"force": False, "timeoutMs": 12000}]
    assert client.web_login_wait_calls == [
        {
            "timeoutMs": 480000,
            "currentQrDataUrl": "data:image/png;base64,real-qr",
            "sessionKey": "login-session-1",
        }
    ]


def test_get_channel_status_returns_existing_qr_without_polling_on_page_load() -> None:
    client = _FakeChannelClient(
        connected=False,
        qr_data_url="data:image/png;base64,real-qr",
        qr_session_key="login-session-1",
        wait_connected=True,
    )
    bridge = ChannelBridge(client)

    first = bridge.get_channel_status(probe=True, include_qr=True)
    second = bridge.get_channel_status(probe=True, include_qr=True)

    assert first["state"] == "disconnected"
    assert second["state"] == "disconnected"
    assert second["qrCodeImageDataUrl"] == "data:image/png;base64,real-qr"
    assert client.plugins_list_calls == 2
    assert client.channels_status_calls == [{"probe": True}, {"probe": True}]
    assert client.web_login_wait_calls == []


def test_get_channel_status_reuses_qr_without_session_key() -> None:
    client = _FakeChannelClient(
        connected=False,
        qr_data_url="data:image/png;base64,real-qr",
        qr_session_key=None,
    )
    bridge = ChannelBridge(client)

    first = bridge.get_channel_status(probe=True, include_qr=True)
    second = bridge.get_channel_status(probe=True, include_qr=True, poll_login=True)
    third = bridge.get_channel_status(probe=True, include_qr=True, poll_login=True)

    assert first["qrCodeImageDataUrl"] == "data:image/png;base64,real-qr"
    assert second["qrCodeImageDataUrl"] == "data:image/png;base64,real-qr"
    assert third["qrCodeImageDataUrl"] == "data:image/png;base64,real-qr"
    assert client.web_login_start_calls == [{"force": False, "timeoutMs": 12000}]
    assert client.web_login_wait_calls == []


def test_get_channel_status_restarts_qr_after_closed_login_session() -> None:
    client = _FakeChannelClient(
        connected=False,
        qr_data_url="data:image/png;base64,real-qr",
        qr_session_key="login-session-1",
        wait_message="登录超时，请重试。",
    )
    bridge = ChannelBridge(client)

    first = bridge.get_channel_status(probe=True, include_qr=True)
    client.qr_data_url = None
    second = {"qrCodeImageDataUrl": "data:image/png;base64,real-qr", "lastErrorMessage": None}
    for _ in range(20):
        second = bridge.get_channel_status(probe=True, include_qr=True, poll_login=True)
        if second["qrCodeImageDataUrl"] is None:
            break
    client.qr_data_url = "data:image/png;base64,real-qr"
    third = bridge.get_channel_status(probe=True, include_qr=True)

    assert first["qrCodeImageDataUrl"] == "data:image/png;base64,real-qr"
    assert second["qrCodeImageDataUrl"] is None
    assert second["lastErrorMessage"] == "登录超时，请重试。"
    assert third["qrCodeImageDataUrl"] == "data:image/png;base64,real-qr"
    assert client.web_login_start_calls == [
        {"force": False, "timeoutMs": 12000},
        {"force": False, "timeoutMs": 12000},
    ]


def test_get_channel_status_clears_qr_when_provider_disappears_during_poll() -> None:
    client = _FakeChannelClient(
        connected=False,
        qr_data_url="data:image/png;base64,real-qr",
        qr_session_key="login-session-1",
        wait_exception=RuntimeError("web login provider is not available"),
    )
    bridge = ChannelBridge(client)

    first = bridge.get_channel_status(probe=True, include_qr=True)
    second = {"qrCodeImageDataUrl": "data:image/png;base64,real-qr", "lastErrorMessage": None}
    for _ in range(20):
        second = bridge.get_channel_status(probe=True, include_qr=True, poll_login=True)
        if second["qrCodeImageDataUrl"] is None:
            break
    third = bridge.get_channel_status(probe=True, include_qr=True)

    assert first["qrCodeImageDataUrl"] == "data:image/png;base64,real-qr"
    assert second["qrCodeImageDataUrl"] is None
    assert second["qrCodeRefreshRequired"] is True
    assert second["lastErrorMessage"] == "微信登录服务未就绪，请刷新二维码重试。"
    assert third["qrCodeImageDataUrl"] == "data:image/png;base64,real-qr"
    assert client.web_login_start_calls == [
        {"force": False, "timeoutMs": 12000},
        {"force": False, "timeoutMs": 12000},
    ]


def test_get_channel_status_poll_login_reuses_single_background_wait() -> None:
    class _BlockingWaitClient(_FakeChannelClient):
        def __init__(self) -> None:
            super().__init__(
                connected=False,
                qr_data_url="data:image/png;base64,real-qr",
                qr_session_key="login-session-1",
            )
            self.wait_started = threading.Event()
            self.wait_release = threading.Event()

        def web_login_wait(self, *, timeout_ms=1500, current_qr_data_url=None, session_key=None):  # type: ignore[no-untyped-def]
            self.web_login_wait_calls.append(
                {"timeoutMs": timeout_ms, "currentQrDataUrl": current_qr_data_url, "sessionKey": session_key}
            )
            self.wait_started.set()
            self.wait_release.wait(0.5)
            return {"connected": False, "qrDataUrl": self.qr_data_url, "message": "scan qr"}

    client = _BlockingWaitClient()
    bridge = ChannelBridge(client)

    first = bridge.get_channel_status(probe=True, include_qr=True)
    second = bridge.get_channel_status(probe=True, include_qr=True, poll_login=True)
    assert client.wait_started.wait(0.2)
    third = bridge.get_channel_status(probe=True, include_qr=True, poll_login=True)
    client.wait_release.set()

    assert first["state"] == "disconnected"
    assert second["state"] == "disconnected"
    assert third["state"] == "disconnected"
    assert len(client.web_login_wait_calls) == 1


def test_get_channel_status_refresh_qr_ignores_stale_wait_result_from_old_session() -> None:
    class _SessionSwapClient(_FakeChannelClient):
        def __init__(self) -> None:
            super().__init__(connected=False)
            self.wait_started = threading.Event()
            self.wait_release = threading.Event()

        def web_login_start(self, *, force=False, timeout_ms=12000):  # type: ignore[no-untyped-def]
            self.web_login_start_calls.append({"force": force, "timeoutMs": timeout_ms})
            if force:
                self.qr_data_url = "data:image/png;base64,qr-2"
                self.qr_session_key = "login-session-2"
            else:
                self.qr_data_url = "data:image/png;base64,qr-1"
                self.qr_session_key = "login-session-1"
            return {"qrDataUrl": self.qr_data_url, "message": "scan qr", "sessionKey": self.qr_session_key}

        def web_login_wait(self, *, timeout_ms=1500, current_qr_data_url=None, session_key=None):  # type: ignore[no-untyped-def]
            self.web_login_wait_calls.append(
                {"timeoutMs": timeout_ms, "currentQrDataUrl": current_qr_data_url, "sessionKey": session_key}
            )
            if session_key == "login-session-1":
                self.wait_started.set()
                self.wait_release.wait(0.5)
                return {"connected": True, "message": "connected"}
            return {"connected": False, "qrDataUrl": "data:image/png;base64,qr-2", "message": "scan qr"}

    client = _SessionSwapClient()
    bridge = ChannelBridge(client)

    first = bridge.get_channel_status(probe=True, include_qr=True)
    _ = bridge.get_channel_status(probe=True, include_qr=True, poll_login=True)
    assert client.wait_started.wait(0.2)
    refreshed = bridge.get_channel_status(probe=True, include_qr=True, refresh_qr=True)
    client.wait_release.set()
    final_status = bridge.get_channel_status(probe=True, include_qr=True, poll_login=True)

    assert first["qrCodeImageDataUrl"] == "data:image/png;base64,qr-1"
    assert refreshed["qrCodeImageDataUrl"] == "data:image/png;base64,qr-2"
    assert final_status["state"] == "disconnected"
    assert final_status["qrCodeImageDataUrl"] == "data:image/png;base64,qr-2"
    assert client.web_login_start_calls == [
        {"force": False, "timeoutMs": 12000},
        {"force": True, "timeoutMs": 12000},
    ]


def test_get_channel_status_refreshes_qr_without_accepting_non_image_url() -> None:
    client = _FakeChannelClient(
        connected=False,
        qr_data_url="https://example.invalid/not-a-data-url.png",
    )
    bridge = ChannelBridge(client)

    payload = bridge.get_channel_status(probe=True, include_qr=True, refresh_qr=True)

    assert payload["qrCodeImageDataUrl"] is None
    assert payload["qrCodeRefreshRequired"] is True
    assert payload["lastErrorMessage"] == "当前通道未返回二维码，请打开设备界面查看。"
    assert client.web_login_start_calls == [{"force": True, "timeoutMs": 12000}]


def test_get_channel_status_accepts_generated_svg_qr() -> None:
    client = _FakeChannelClient(
        connected=False,
        qr_data_url="data:image/svg+xml;base64,fake-qr",
    )
    bridge = ChannelBridge(client)

    payload = bridge.get_channel_status(probe=True, include_qr=True, refresh_qr=True)

    assert payload["qrCodeImageDataUrl"] == "data:image/svg+xml;base64,fake-qr"


def test_get_channel_status_clears_cached_qr_after_channel_disabled() -> None:
    client = _FakeChannelClient(
        connected=False,
        qr_data_url="data:image/png;base64,real-qr",
        qr_session_key="login-session-1",
    )
    bridge = ChannelBridge(client)

    first = bridge.get_channel_status(probe=True, include_qr=True)
    client.enabled = False
    second = bridge.get_channel_status(probe=True, include_qr=True)

    assert first["qrCodeImageDataUrl"] == "data:image/png;base64,real-qr"
    assert second["qrCodeImageDataUrl"] is None
    assert second["lastErrorMessage"] == "请先启用微信 ClawBot 插件。"


def test_get_channel_status_does_not_request_qr_when_login_provider_disabled() -> None:
    client = _DisabledStaleChannelClient()
    bridge = ChannelBridge(client)

    payload = bridge.get_channel_status(probe=True, include_qr=True)

    assert payload["state"] == "disconnected"
    assert payload["qrCodeImageDataUrl"] is None
    assert payload["lastErrorMessage"] == "请先启用微信 ClawBot 插件。"
    assert client.web_login_start_calls == []


def test_get_channel_status_does_not_request_qr_until_channel_is_registered() -> None:
    client = _PluginEnabledNoChannelClient()
    bridge = ChannelBridge(client)

    payload = bridge.get_channel_status(probe=True, include_qr=True)

    assert payload["state"] == "disconnected"
    assert payload["qrCodeImageDataUrl"] is None
    assert payload["lastErrorMessage"] == "微信登录服务启动中，请稍后重试。"
    assert client.web_login_start_calls == []


def test_get_channel_status_reports_user_disabled_channel_without_auto_qr() -> None:
    client = _ChannelDisabledConfigClient()
    bridge = ChannelBridge(client)

    payload = bridge.get_channel_status(probe=True, include_qr=True)

    assert payload["state"] == "disconnected"
    assert payload["qrCodeImageDataUrl"] is None
    assert payload["qrCodeRefreshRequired"] is True
    assert payload["lastErrorMessage"] == "微信通知已停用，请点击重新连接。"
    assert payload["replacementRequired"] is True
    assert client.web_login_start_calls == []
    assert client.config_get_calls == [("channels.openclaw-weixin",)]


def test_disabled_channel_with_multiple_stored_accounts_still_requires_safe_replacement() -> None:
    class _ConflictedDisabledClient(_ChannelDisabledConfigClient):
        def weixin_replacement_inspect(self, *, operation_id):  # type: ignore[no-untyped-def]
            _ = operation_id
            return {"storedAccountIds": ["account-a", "account-b"]}

    payload = ChannelBridge(_ConflictedDisabledClient()).get_channel_status(probe=True)

    assert payload["replacementRequired"] is True


def test_disabled_channel_with_stale_runtime_row_never_starts_ordinary_qr_login() -> None:
    class _StaleDisabledClient(_ChannelDisabledConfigClient):
        def channels_status(self, *, probe=False):  # type: ignore[no-untyped-def]
            _ = probe
            return {
                "channelOrder": ["openclaw-weixin"],
                "channelAccounts": {
                    "openclaw-weixin": [
                        {
                            "accountId": "old-account",
                            "configured": True,
                            "enabled": False,
                            "running": False,
                        }
                    ]
                },
            }

    client = _StaleDisabledClient()
    payload = ChannelBridge(client).get_channel_status(probe=True, include_qr=True)

    assert payload["state"] == "disconnected"
    assert payload["replacementRequired"] is True
    assert client.web_login_start_calls == []


def test_config_read_failure_never_starts_ordinary_qr_login() -> None:
    class _ConfigReadFailureClient(_FakeChannelClient):
        def config_get(self, *, paths=()):  # type: ignore[no-untyped-def]
            raise RuntimeError("config unavailable")

        def weixin_replacement_inspect(self, *, operation_id):  # type: ignore[no-untyped-def]
            return {"storedAccountIds": ["old-account"]}

    client = _ConfigReadFailureClient(connected=False, qr_data_url="data:image/png;base64,unsafe")

    payload = ChannelBridge(client).get_channel_status(probe=True, include_qr=True)

    assert payload["replacementRequired"] is True
    assert client.web_login_start_calls == []


def test_startup_recovery_failure_does_not_block_ui_service(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    bridge = ChannelBridge(
        _FakeChannelClient(),
        reconnect_state_path=tmp_path / ".ui-wechat-reconnect.json",
    )
    reconnect = bridge._require_wechat_reconnect()
    monkeypatch.setattr(reconnect, "recover", lambda: (_ for _ in ()).throw(OSError("gateway down")))

    result = bridge.recover_wechat_reconnect()

    assert result is not None
    assert result["phase"] == "needs_attention"
    assert result["lastErrorCode"] == "WECHAT_RECOVERY_CHECK_FAILED"


def test_corrupt_reconnect_state_fails_closed_without_raising_from_delivery_guard(tmp_path: Path) -> None:
    state_path = tmp_path / ".ui-wechat-reconnect.json"
    state_path.write_text("not-json", encoding="utf-8")
    bridge = ChannelBridge(_FakeChannelClient(), reconnect_state_path=state_path)

    with bridge.wechat_delivery_account() as account_id:
        assert account_id is None


def test_all_text_sends_are_blocked_while_reconnect_is_active(tmp_path: Path) -> None:
    class _CountingClient(_FakeChannelClient):
        def __init__(self) -> None:
            super().__init__()
            self.send_count = 0

        def channels_send_text(self, **kwargs):  # type: ignore[no-untyped-def]
            self.send_count += 1
            return super().channels_send_text(**kwargs)

    state_path = tmp_path / ".ui-wechat-reconnect.json"
    state_path.write_text(
        json.dumps({"schemaVersion": 1, "operationId": "op-1", "phase": "awaiting_scan"}),
        encoding="utf-8",
    )
    client = _CountingClient()
    bridge = ChannelBridge(client, reconnect_state_path=state_path)

    with pytest.raises(UiBoundaryError, match="正在重新连接"):
        bridge.send_text(
            channel_kind="wechat_clawbot",
            text="不应发送",
            dedupe_key="blocked-send",
            target="sender-old",
            account_id="account-old",
        )

    assert client.send_count == 0


def test_save_channel_config_rejects_non_wechat_channel_kind() -> None:
    bridge = ChannelBridge(_FakeChannelClient())
    with pytest.raises(UiBoundaryError) as exc:
        bridge.save_channel_config_via_openclaw(
            request_id="req-1",
            channel_kind="wechat",
            config_patch={"enabled": True},
        )
    assert exc.value.code == "INVALID_INPUT"


def test_save_channel_config_marks_wechat_channel_as_configured_when_enabled() -> None:
    client = _FakeChannelClient(connected=False, qr_data_url="data:image/png;base64,real-qr")
    bridge = ChannelBridge(client)

    result = bridge.save_channel_config_via_openclaw(
        request_id="req-enable-wechat",
        channel_kind="wechat_clawbot",
        config_patch={"enabled": True},
        expected_settings_version="hash-1",
    )

    patch = client.config_patch_calls[0]["patch"]
    assert patch["channels"]["openclaw-weixin"]["enabled"] is True
    assert isinstance(patch["channels"]["openclaw-weixin"]["channelConfigUpdatedAt"], str)
    assert result["status"]["qrCodeImageDataUrl"] == "data:image/png;base64,real-qr"


def test_save_channel_config_waits_for_gateway_restart_after_enable(monkeypatch) -> None:
    monkeypatch.setattr(channel_bridge_module, "_CONFIG_PATCH_STATUS_SETTLE_INTERVAL_SECONDS", 0)
    client = _RestartingConfigClient()
    bridge = ChannelBridge(client)

    result = bridge.save_channel_config_via_openclaw(
        request_id="req-enable-wechat-after-restart",
        channel_kind="wechat_clawbot",
        config_patch={"enabled": True},
    )

    assert result["status"]["state"] == "connected"
    assert len(client.channels_status_calls) == 2


def test_save_channel_config_does_not_request_qr_when_disabled() -> None:
    client = _FakeChannelClient(connected=True)
    bridge = ChannelBridge(client)

    result = bridge.save_channel_config_via_openclaw(
        request_id="req-disable-wechat",
        channel_kind="wechat_clawbot",
        config_patch={"enabled": False},
    )

    patch = client.config_patch_calls[0]["patch"]
    assert patch["channels"]["openclaw-weixin"]["enabled"] is False
    assert result["status"]["state"] == "disconnected"
    assert result["status"]["lastErrorMessage"] == "微信通知已停用；账号凭据仍保留。"
    assert client.web_login_start_calls == []


def test_save_channel_config_disabled_preserves_plugin_owned_login_state(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state_dir = tmp_path / "openclaw-state"
    accounts_dir = state_dir / "openclaw-weixin" / "accounts"
    accounts_dir.mkdir(parents=True)
    (state_dir / "openclaw-weixin" / "accounts.json").write_text('["acc-1"]', encoding="utf-8")
    (accounts_dir / "acc-1.json").write_text('{"token":"secret"}', encoding="utf-8")
    (accounts_dir / "acc-1.sync.json").write_text('{"get_updates_buf":"abc"}', encoding="utf-8")
    (accounts_dir / "acc-1.context-tokens.json").write_text("{}", encoding="utf-8")
    legacy_credentials = state_dir / "credentials" / "openclaw-weixin" / "credentials.json"
    legacy_credentials.parent.mkdir(parents=True)
    legacy_credentials.write_text('{"token":"legacy"}', encoding="utf-8")
    legacy_sync = (
        state_dir / "agents" / "default" / "sessions" / ".openclaw-weixin-sync" / "default.json"
    )
    legacy_sync.parent.mkdir(parents=True)
    legacy_sync.write_text('{"get_updates_buf":"legacy"}', encoding="utf-8")
    monkeypatch.setenv("OPENCLAW_STATE_DIR", str(state_dir))

    class _PluginWritesSyncDuringDisableClient(_FakeChannelClient):
        def config_patch(self, *, expected_settings_version=None, patch=None):  # type: ignore[no-untyped-def]
            result = super().config_patch(expected_settings_version=expected_settings_version, patch=patch)
            (accounts_dir / "acc-1.sync.json").write_text('{"get_updates_buf":"rewritten"}', encoding="utf-8")
            return result

    bridge = ChannelBridge(_PluginWritesSyncDuringDisableClient(connected=True))
    result = bridge.save_channel_config_via_openclaw(
        request_id="req-disable-wechat-clear-state",
        channel_kind="wechat_clawbot",
        config_patch={"enabled": False},
    )

    assert result["status"]["state"] == "disconnected"
    assert (state_dir / "openclaw-weixin" / "accounts.json").exists()
    assert (accounts_dir / "acc-1.json").exists()
    assert (accounts_dir / "acc-1.sync.json").read_text(encoding="utf-8") == '{"get_updates_buf":"rewritten"}'
    assert (accounts_dir / "acc-1.context-tokens.json").exists()
    assert legacy_credentials.exists()
    assert legacy_sync.exists()


def test_save_channel_config_disabled_does_not_clear_default_runtime_without_explicit_state_dir(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("OPENCLAW_STATE_DIR", raising=False)
    monkeypatch.delenv("OPENCLAW_CONFIG_PATH", raising=False)
    monkeypatch.chdir(tmp_path)
    accounts_dir = tmp_path / ".runtime" / "dev-services" / "openclaw-state" / "openclaw-weixin" / "accounts"
    accounts_dir.mkdir(parents=True)
    account_file = accounts_dir / "acc-1.json"
    account_file.write_text('{"token":"secret"}', encoding="utf-8")

    bridge = ChannelBridge(_FakeChannelClient(connected=True))
    result = bridge.save_channel_config_via_openclaw(
        request_id="req-disable-wechat-no-env",
        channel_kind="wechat_clawbot",
        config_patch={"enabled": False},
    )

    assert result["status"]["state"] == "disconnected"
    assert account_file.exists()


def test_resolve_default_report_file_target_uses_single_weixin_context_token(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state_dir = tmp_path / "openclaw-state"
    accounts_dir = state_dir / "openclaw-weixin" / "accounts"
    accounts_dir.mkdir(parents=True)
    (accounts_dir / "acc-1.context-tokens.json").write_text(
        '{"sender-1@im.wechat":"token-1"}',
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENCLAW_STATE_DIR", str(state_dir))
    bridge = ChannelBridge(_FakeChannelClient(connected=True))

    assert bridge.resolve_default_report_file_target(channel_kind=USER_CHANNEL_KIND) == (
        "sender-1@im.wechat",
        "acc-1",
    )


def test_resolve_default_report_file_target_filters_to_connected_account(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class _ConnectedAccountClient(_FakeChannelClient):
        def channels_status(self, *, probe=False):  # type: ignore[no-untyped-def]
            self.channels_status_calls.append({"probe": probe})
            return {
                "channelAccounts": {
                    "openclaw-weixin": [
                        {
                            "accountId": "old-bot",
                            "configured": False,
                            "running": False,
                        },
                        {
                            "accountId": "new-bot",
                            "configured": True,
                            "running": True,
                        },
                    ]
                },
                "channelDefaultAccountId": {"openclaw-weixin": "old-bot"},
            }

    state_dir = tmp_path / "openclaw-state"
    accounts_dir = state_dir / "openclaw-weixin" / "accounts"
    accounts_dir.mkdir(parents=True)
    (accounts_dir / "old-bot.context-tokens.json").write_text(
        '{"old-user@im.wechat":"old-token"}',
        encoding="utf-8",
    )
    (accounts_dir / "new-bot.context-tokens.json").write_text(
        '{"new-user@im.wechat":"new-token"}',
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENCLAW_STATE_DIR", str(state_dir))
    bridge = ChannelBridge(_ConnectedAccountClient(connected=False))

    assert bridge.resolve_default_report_file_target(channel_kind=USER_CHANNEL_KIND) == (
        "new-user@im.wechat",
        "new-bot",
    )


def test_resolve_default_report_file_target_returns_none_when_status_probe_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class _StatusFailClient(_FakeChannelClient):
        def channels_status(self, *, probe=False):  # type: ignore[no-untyped-def]
            self.channels_status_calls.append({"probe": probe})
            raise RuntimeError("gateway unavailable")

    state_dir = tmp_path / "openclaw-state"
    accounts_dir = state_dir / "openclaw-weixin" / "accounts"
    accounts_dir.mkdir(parents=True)
    (accounts_dir / "old-bot.context-tokens.json").write_text(
        '{"old-user@im.wechat":"old-token"}',
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENCLAW_STATE_DIR", str(state_dir))
    bridge = ChannelBridge(_StatusFailClient(connected=False))

    assert bridge.resolve_default_report_file_target(channel_kind=USER_CHANNEL_KIND) is None


def test_resolve_default_report_file_target_uses_single_login_account_file(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state_dir = tmp_path / "openclaw-state"
    accounts_dir = state_dir / "openclaw-weixin" / "accounts"
    accounts_dir.mkdir(parents=True)
    (accounts_dir / "acc-1.json").write_text(
        '{"userId":"sender-login@im.wechat","token":"secret"}',
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENCLAW_STATE_DIR", str(state_dir))
    bridge = ChannelBridge(_FakeChannelClient(connected=True))

    assert bridge.resolve_default_report_file_target(channel_kind=USER_CHANNEL_KIND) == (
        "sender-login@im.wechat",
        "acc-1",
    )


def test_resolve_default_report_file_target_returns_none_for_multiple_weixin_targets(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state_dir = tmp_path / "openclaw-state"
    accounts_dir = state_dir / "openclaw-weixin" / "accounts"
    accounts_dir.mkdir(parents=True)
    (accounts_dir / "acc-1.context-tokens.json").write_text(
        '{"sender-1@im.wechat":"token-1","sender-2@im.wechat":"token-2"}',
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENCLAW_STATE_DIR", str(state_dir))
    bridge = ChannelBridge(_FakeChannelClient(connected=True))

    assert bridge.resolve_default_report_file_target(channel_kind=USER_CHANNEL_KIND) is None


def test_resolve_default_report_file_target_ignores_login_user_without_token(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state_dir = tmp_path / "openclaw-state"
    accounts_dir = state_dir / "openclaw-weixin" / "accounts"
    accounts_dir.mkdir(parents=True)
    (state_dir / "openclaw-weixin" / "accounts.json").write_text('["acc-1"]', encoding="utf-8")
    (accounts_dir / "acc-1.json").write_text(
        '{"userId":"sender-login@im.wechat"}',
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENCLAW_STATE_DIR", str(state_dir))
    bridge = ChannelBridge(_FakeChannelClient(connected=True))

    assert bridge.resolve_default_report_file_target(channel_kind=USER_CHANNEL_KIND) is None


def test_resolve_default_report_file_target_returns_none_for_multiple_login_account_targets(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state_dir = tmp_path / "openclaw-state"
    accounts_dir = state_dir / "openclaw-weixin" / "accounts"
    accounts_dir.mkdir(parents=True)
    (state_dir / "openclaw-weixin" / "accounts.json").write_text('["acc-1","acc-2"]', encoding="utf-8")
    (accounts_dir / "acc-1.json").write_text('{"userId":"sender-1@im.wechat","token":"secret-1"}', encoding="utf-8")
    (accounts_dir / "acc-2.json").write_text('{"userId":"sender-2@im.wechat","token":"secret-2"}', encoding="utf-8")
    monkeypatch.setenv("OPENCLAW_STATE_DIR", str(state_dir))
    bridge = ChannelBridge(_FakeChannelClient(connected=True))

    assert bridge.resolve_default_report_file_target(channel_kind=USER_CHANNEL_KIND) is None


def test_save_channel_config_hides_provider_error_detail() -> None:
    class _FailingConfigClient(_FakeChannelClient):
        def config_patch(self, *, expected_settings_version=None, patch=None):  # type: ignore[no-untyped-def]
            _ = (expected_settings_version, patch)
            raise RuntimeError("Gateway call failed: invalid config.patch params")

    bridge = ChannelBridge(_FailingConfigClient())

    with pytest.raises(UiBoundaryError) as exc:
        bridge.save_channel_config_via_openclaw(
            request_id="req-save-fail",
            channel_kind="wechat_clawbot",
            config_patch={"enabled": True},
        )

    assert exc.value.code == "NOTIFICATION_UNAVAILABLE"
    assert exc.value.user_message == "微信通知暂不可用，请在设备界面查看。"


def test_send_text_uses_runtime_capability_and_returns_sent_result() -> None:
    bridge = ChannelBridge(_FakeChannelClient())
    result = bridge.send_text(
        channel_kind="wechat_clawbot",
        text="报告完成",
        dedupe_key="d-1",
        target="sender-1",
    )
    assert result["sent"] is True
    assert result["messageId"] is None


@pytest.mark.parametrize("raw_response", [{}, {"ok": True}, None, "ok"])
def test_send_text_does_not_treat_unknown_provider_result_as_sent(raw_response) -> None:  # type: ignore[no-untyped-def]
    class _UnknownTextResponseClient(_FakeChannelClient):
        def channels_send_text(self, *, channel, text, dedupe_key, to, account_id=None):  # type: ignore[no-untyped-def]
            _ = (channel, text, dedupe_key, to, account_id)
            return raw_response

    bridge = ChannelBridge(_UnknownTextResponseClient())

    result = bridge.send_text(
        channel_kind="wechat_clawbot",
        text="报告已完成",
        dedupe_key="completion:r-unknown",
        target="sender-1",
    )

    assert result == {
        "sent": False,
        "messageId": None,
        "outcome": "unknown",
        "resultKnown": False,
    }


@pytest.mark.parametrize(
    ("raw_response", "expected_outcome"),
    [
        ({"sent": False}, "failed"),
        ({"ok": False}, "failed"),
        ({"sent": True}, "sent"),
        ({"messageId": "message-1"}, "sent"),
    ],
)
def test_send_text_preserves_known_provider_outcome(raw_response, expected_outcome: str) -> None:  # type: ignore[no-untyped-def]
    class _KnownTextResponseClient(_FakeChannelClient):
        def channels_send_text(self, *, channel, text, dedupe_key, to, account_id=None):  # type: ignore[no-untyped-def]
            _ = (channel, text, dedupe_key, to, account_id)
            return raw_response

    result = ChannelBridge(_KnownTextResponseClient()).send_text(
        channel_kind="wechat_clawbot",
        text="报告已完成",
        dedupe_key="completion:r-known",
        target="sender-1",
    )

    assert result["outcome"] == expected_outcome
    assert result["resultKnown"] is True
    assert result["sent"] is (expected_outcome == "sent")


def test_send_text_returns_notification_unavailable_when_not_connected() -> None:
    bridge = ChannelBridge(_FakeChannelClient(connected=False))
    with pytest.raises(UiBoundaryError) as exc:
        bridge.send_text(
            channel_kind="wechat_clawbot",
            text="报告完成",
            dedupe_key="d-2",
            target="sender-1",
        )
    assert exc.value.code == "NOTIFICATION_UNAVAILABLE"


def test_send_report_file_attempts_send_when_media_capability_is_unverified() -> None:
    bridge = ChannelBridge(_FakeChannelClient(caps_error=True))
    result = bridge.send_report_file_via_channel(
        request_id="r-1",
        report_id="rp-1",
        channel_kind="wechat_clawbot",
        file_name="report.pdf",
        payload=b"pdf",
        target="sender-1",
    )
    assert result["sent"] is True


def test_send_report_file_attempts_send_when_media_capability_is_not_advertised() -> None:
    class _NoAdvertisedMediaClient(_FakeChannelClient):
        def channels_capabilities(self, *, channel):  # type: ignore[no-untyped-def]
            _ = channel
            return {}

    bridge = ChannelBridge(_NoAdvertisedMediaClient())
    result = bridge.send_report_file_via_channel(
        request_id="r-no-advertised-media",
        report_id="rp-no-advertised-media",
        channel_kind="wechat_clawbot",
        file_name="report.pdf",
        payload=b"pdf",
        target="sender-1",
    )
    assert result["sent"] is True


def test_send_report_file_returns_file_specific_message_when_not_connected() -> None:
    bridge = ChannelBridge(_FakeChannelClient(connected=False))
    with pytest.raises(UiBoundaryError) as exc:
        bridge.send_report_file_via_channel(
            request_id="r-disconnected",
            report_id="rp-disconnected",
            channel_kind="wechat_clawbot",
            file_name="report.pdf",
            payload=b"pdf",
            target="sender-1",
        )
    assert exc.value.code == "NOTIFICATION_UNAVAILABLE"
    assert exc.value.user_message == "微信文件发送前检查失败，报告没有发出。请稍后重试。"


def test_send_report_file_returns_file_send_unsupported_without_file_sender() -> None:
    class _NoFileSenderClient(_FakeChannelClient):
        def __getattribute__(self, name: str):  # type: ignore[no-untyped-def]
            if name == "channels_send_file":
                raise AttributeError("missing")
            return super().__getattribute__(name)

    bridge = ChannelBridge(_NoFileSenderClient(caps_error=True))
    with pytest.raises(UiBoundaryError) as exc:
        bridge.send_report_file_via_channel(
            request_id="r-1b",
            report_id="rp-1",
            channel_kind="wechat_clawbot",
            file_name="report.pdf",
            payload=b"pdf",
            target="sender-1",
        )
    assert exc.value.code == "FILE_SEND_UNSUPPORTED"


def test_send_report_file_returns_provider_result_without_fake_message_id() -> None:
    bridge = ChannelBridge(_FakeChannelClient())
    result = bridge.send_report_file_via_channel(
        request_id="r-2",
        report_id="rp-2",
        channel_kind="wechat_clawbot",
        file_name="report.pdf",
        payload=b"pdf",
        target="sender-1",
    )
    assert result["sent"] is True
    assert result["messageId"] == "msg-1"


def test_send_report_file_retries_once_after_cdn_server_error() -> None:
    client = _OneTimeCdnFailureFileClient()
    bridge = ChannelBridge(client)
    result = bridge.send_report_file_via_channel(
        request_id="r-cdn",
        report_id="rp-cdn",
        channel_kind="wechat_clawbot",
        file_name="report.pdf",
        payload=b"pdf",
        target="sender-1",
    )
    assert result["sent"] is True
    assert result["messageId"] == "msg-after-cdn-retry"
    assert client.dedupe_keys == ["r-cdn", "r-cdn:cdn-retry-1"]


def test_send_report_file_returns_notification_unavailable_after_cdn_retry_failure() -> None:
    bridge = ChannelBridge(_AlwaysCdnFailureFileClient())
    with pytest.raises(UiBoundaryError) as exc:
        bridge.send_report_file_via_channel(
            request_id="r-cdn-fail",
            report_id="rp-cdn-fail",
            channel_kind="wechat_clawbot",
            file_name="report.pdf",
            payload=b"pdf",
            target="sender-1",
        )
    assert exc.value.code == "NOTIFICATION_UNAVAILABLE"
    assert exc.value.user_message == "微信文件上传失败，报告没有发出。请稍后重试。"


def test_send_report_file_returns_clear_error_after_gateway_timeout() -> None:
    class _TimeoutFileClient(_FakeChannelClient):
        def channels_send_file(self, *, channel, file_name, dedupe_key, to, payload=None, file_path=None, account_id=None):
            _ = (channel, file_name, dedupe_key, to, payload, file_path, account_id)
            raise RuntimeError("Gateway call failed: GatewayTransportError: gateway timeout after 15000ms")

    bridge = ChannelBridge(_TimeoutFileClient())
    with pytest.raises(UiBoundaryError) as exc:
        bridge.send_report_file_via_channel(
            request_id="r-timeout",
            report_id="rp-timeout",
            channel_kind="wechat_clawbot",
            file_name="report.pdf",
            payload=b"pdf",
            target="sender-1",
        )

    assert exc.value.code == "NOTIFICATION_UNAVAILABLE"
    assert exc.value.user_message == "微信文件发送超时，报告没有发出。请稍后重试。"


def test_send_report_file_returns_clear_error_when_wechat_session_is_paused() -> None:
    class _PausedSessionFileClient(_FakeChannelClient):
        def channels_send_file(self, *, channel, file_name, dedupe_key, to, payload=None, file_path=None, account_id=None):
            _ = (channel, file_name, dedupe_key, to, payload, file_path, account_id)
            raise RuntimeError("Gateway call failed: session paused for accountId=acc-1, 60 min remaining (errcode -14)")

    bridge = ChannelBridge(_PausedSessionFileClient())
    with pytest.raises(UiBoundaryError) as exc:
        bridge.send_report_file_via_channel(
            request_id="r-paused",
            report_id="rp-paused",
            channel_kind="wechat_clawbot",
            file_name="report.pdf",
            payload=b"pdf",
            target="sender-1",
        )

    assert exc.value.code == "NOTIFICATION_UNAVAILABLE"
    assert exc.value.user_message == "微信登录已过期或暂停，报告没有发出。请重新连接微信后再试。"


def test_send_report_file_requires_explicit_wechat_target() -> None:
    class _NoTargetFileClient(_FakeChannelClient):
        def __init__(self) -> None:
            super().__init__()
            self.targets: list[object] = []

        def channels_send_file(self, *, channel, file_name, dedupe_key, to=None, payload=None, file_path=None, account_id=None):
            self.targets.append(to)
            return {"sent": True, "messageId": "msg-current"}

    client = _NoTargetFileClient()
    bridge = ChannelBridge(client)
    with pytest.raises(UiBoundaryError) as exc:
        bridge.send_report_file_via_channel(
            request_id="r-current",
            report_id="rp-current",
            channel_kind="wechat_clawbot",
            file_name="report.pdf",
            payload=b"pdf",
    )
    assert exc.value.code == "FILE_SEND_UNSUPPORTED"
    assert exc.value.user_message == "完整报告文件暂不可发送，请在设备界面查看。"
    assert client.targets == []


@pytest.mark.parametrize(
    ("raw_response", "expected_sent", "expected_message_id"),
    [
        ({"messageId": "m-1"}, True, "m-1"),
        ({"message_id": "m-1"}, True, "m-1"),
        ({"sent": True, "messageId": "m-1"}, True, "m-1"),
        ({"sent": False, "messageId": "m-1"}, False, "m-1"),
        ({"ok": False, "error": "boom"}, False, None),
        ({}, False, None),
        (None, False, None),
        ("ok", False, None),
    ],
)
def test_send_report_file_result_matrix(
    raw_response, expected_sent: bool, expected_message_id: str | None
) -> None:  # type: ignore[no-untyped-def]
    bridge = ChannelBridge(_RawFileResponseClient(raw_response))
    if expected_sent:
        result = bridge.send_report_file_via_channel(
            request_id="r-matrix",
            report_id="rp-matrix",
            channel_kind="wechat_clawbot",
            file_name="report.pdf",
            payload=b"pdf",
            target="sender-1",
        )
        assert result["sent"] is True
        assert result["messageId"] == expected_message_id
        return
    with pytest.raises(UiBoundaryError) as exc:
        bridge.send_report_file_via_channel(
            request_id="r-matrix",
            report_id="rp-matrix",
            channel_kind="wechat_clawbot",
            file_name="report.pdf",
            payload=b"pdf",
            target="sender-1",
        )
    assert exc.value.code == "FILE_SEND_UNSUPPORTED"
