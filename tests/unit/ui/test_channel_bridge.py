from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest
from claw_trade.ui_backend import channel_bridge as channel_bridge_module
from claw_trade.ui_backend.channel_bridge import USER_CHANNEL_KIND, ChannelBridge
from claw_trade.ui_backend.settings_service import UiBoundaryError


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
    assert payload["lastErrorMessage"] == "微信已解除连接，请点击刷新二维码重新扫码。"
    assert client.web_login_start_calls == []
    assert client.config_get_calls == [("channels.openclaw-weixin",)]


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
    assert result["status"]["lastErrorMessage"] == "已解除连接。"
    assert client.web_login_start_calls == []


def test_save_channel_config_disabled_clears_weixin_login_state(tmp_path: Path, monkeypatch) -> None:
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
    assert not (state_dir / "openclaw-weixin" / "accounts.json").exists()
    assert not (accounts_dir / "acc-1.json").exists()
    assert not (accounts_dir / "acc-1.sync.json").exists()
    assert not (accounts_dir / "acc-1.context-tokens.json").exists()
    assert not legacy_credentials.exists()
    assert not legacy_sync.exists()


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


def test_resolve_default_report_file_target_ignores_login_user_without_context_token(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state_dir = tmp_path / "openclaw-state"
    accounts_dir = state_dir / "openclaw-weixin" / "accounts"
    accounts_dir.mkdir(parents=True)
    (state_dir / "openclaw-weixin" / "accounts.json").write_text('["acc-1"]', encoding="utf-8")
    (accounts_dir / "acc-1.json").write_text(
        '{"userId":"sender-login@im.wechat","token":"secret"}',
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENCLAW_STATE_DIR", str(state_dir))
    bridge = ChannelBridge(_FakeChannelClient(connected=True))

    assert bridge.resolve_default_report_file_target(channel_kind=USER_CHANNEL_KIND) is None


def test_resolve_default_report_file_target_ignores_login_users_without_context_tokens(
    tmp_path: Path,
    monkeypatch,
) -> None:
    state_dir = tmp_path / "openclaw-state"
    accounts_dir = state_dir / "openclaw-weixin" / "accounts"
    accounts_dir.mkdir(parents=True)
    (state_dir / "openclaw-weixin" / "accounts.json").write_text('["acc-1","acc-2"]', encoding="utf-8")
    (accounts_dir / "acc-1.json").write_text('{"userId":"sender-1@im.wechat"}', encoding="utf-8")
    (accounts_dir / "acc-2.json").write_text('{"userId":"sender-2@im.wechat"}', encoding="utf-8")
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


def test_send_report_file_cleans_superseded_cdn_retry_queue_entry(tmp_path: Path, monkeypatch) -> None:
    state_dir = tmp_path / "openclaw-state"
    queue_dir = state_dir / "delivery-queue"
    queue_dir.mkdir(parents=True)
    pdf_path = tmp_path / "report.pdf"
    pdf_path.write_bytes(b"%PDF-1.7\nreport")
    stale_queue_path = queue_dir / "stale-delivery.json"
    stale_queue_path.write_text(
        json.dumps(
            {
                "id": "stale-delivery",
                "channel": "openclaw-weixin",
                "to": "sender-1",
                "accountId": "acc-1",
                "payloads": [{"text": "report.pdf", "mediaUrl": str(pdf_path)}],
                "mirror": {
                    "idempotencyKey": "r-cdn-cleanup",
                    "text": "report.pdf",
                    "mediaUrls": [str(pdf_path)],
                },
                "retryCount": 1,
                "lastError": "CDN upload server error: status 500",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENCLAW_STATE_DIR", str(state_dir))

    client = _OneTimeCdnFailureFileClient()
    bridge = ChannelBridge(client)
    result = bridge.send_report_file_via_channel(
        request_id="r-cdn-cleanup",
        report_id="rp-cdn-cleanup",
        channel_kind="wechat_clawbot",
        file_name="report.pdf",
        file_path=pdf_path,
        target="sender-1",
        account_id="acc-1",
    )

    assert result["sent"] is True
    assert not stale_queue_path.exists()
    assert list(queue_dir.glob("*.json")) == []
    assert len(list(queue_dir.glob("*.json.superseded-*"))) == 1


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
