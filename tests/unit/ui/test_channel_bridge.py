from __future__ import annotations

import pytest

from claw_trade.ui_backend.channel_bridge import ChannelBridge, USER_CHANNEL_KIND
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
        self.wait_message = wait_message
        self.start_exception = start_exception
        self.wait_exception = wait_exception
        self.plugins_list_calls = 0
        self.config_patch_calls: list[dict[str, object]] = []
        self.channels_status_calls: list[dict[str, object]] = []
        self.web_login_start_calls: list[dict[str, object]] = []
        self.web_login_wait_calls: list[dict[str, object]] = []

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

    def web_login_start(self, *, force=False, timeout_ms=12000):
        self.web_login_start_calls.append({"force": force, "timeoutMs": timeout_ms})
        if self.start_exception:
            raise self.start_exception
        return {"qrDataUrl": self.qr_data_url, "message": "scan qr", "sessionKey": self.qr_session_key}

    def web_login_wait(self, *, timeout_ms=1500, current_qr_data_url=None, session_key=None):
        self.web_login_wait_calls.append(
            {"timeoutMs": timeout_ms, "currentQrDataUrl": current_qr_data_url, "sessionKey": session_key}
        )
        if self.wait_exception:
            raise self.wait_exception
        return {"connected": self.wait_connected, "qrDataUrl": self.qr_data_url, "message": self.wait_message}

    def channels_send_text(self, *, channel, text, dedupe_key):
        if not self.has_text_api:
            raise AttributeError("missing")
        assert channel == "openclaw-weixin"
        assert text
        assert dedupe_key
        return {"sent": True}

    def channels_send_file(self, *, channel, file_name, payload, dedupe_key):
        if not self.has_file_api:
            raise AttributeError("missing")
        assert channel == "openclaw-weixin"
        assert file_name
        assert payload
        assert dedupe_key
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


def test_get_channel_status_uses_conservative_file_capability_when_probe_unavailable() -> None:
    bridge = ChannelBridge(_FakeChannelClient(caps_error=True))
    payload = bridge.get_channel_status(probe=True)
    assert payload["canSendText"] is True
    assert payload["canSendFile"] is False
    assert "暂不可发送" in (payload["lastErrorMessage"] or "")


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


def test_get_channel_status_polls_qr_login_with_session_key_until_connected() -> None:
    client = _FakeChannelClient(
        connected=False,
        qr_data_url="data:image/png;base64,real-qr",
        qr_session_key="login-session-1",
        wait_connected=True,
    )
    bridge = ChannelBridge(client)

    first = bridge.get_channel_status(probe=True, include_qr=True)
    second = bridge.get_channel_status(probe=True, include_qr=True, poll_login=True)

    assert first["state"] == "disconnected"
    assert second["state"] == "connected"
    assert client.web_login_start_calls == [{"force": False, "timeoutMs": 12000}]
    assert client.web_login_wait_calls == [
        {
            "timeoutMs": 8000,
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
    assert client.plugins_list_calls == 0
    assert client.channels_status_calls == [{"probe": True}]
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
    second = bridge.get_channel_status(probe=True, include_qr=True, poll_login=True)
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
    second = bridge.get_channel_status(probe=True, include_qr=True, poll_login=True)
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
    result = bridge.send_text(channel_kind="wechat_clawbot", text="报告完成", dedupe_key="d-1")
    assert result["sent"] is True
    assert result["messageId"] is None


def test_send_text_returns_notification_unavailable_when_not_connected() -> None:
    bridge = ChannelBridge(_FakeChannelClient(connected=False))
    with pytest.raises(UiBoundaryError) as exc:
        bridge.send_text(channel_kind="wechat_clawbot", text="报告完成", dedupe_key="d-2")
    assert exc.value.code == "NOTIFICATION_UNAVAILABLE"


def test_send_report_file_returns_file_send_unsupported_without_media_capability() -> None:
    bridge = ChannelBridge(_FakeChannelClient(caps_error=True))
    with pytest.raises(UiBoundaryError) as exc:
        bridge.send_report_file_via_channel(
            request_id="r-1",
            report_id="rp-1",
            channel_kind="wechat_clawbot",
            file_name="report.pdf",
            payload=b"pdf",
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
    )
    assert result["sent"] is True
    assert result["messageId"] == "msg-1"
