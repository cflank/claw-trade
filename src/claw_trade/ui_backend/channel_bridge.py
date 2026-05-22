from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Mapping

from claw_trade.ui_backend.settings_service import UiBoundaryError

USER_CHANNEL_KIND = "wechat_clawbot"
_PROVIDER_CHANNEL_ID = "openclaw-weixin"
_DISPLAY_NAME = "微信 ClawBot"


class ChannelBridge:
    def __init__(self, openclaw_gateway_client: Any) -> None:
        self._client = openclaw_gateway_client
        self._idempotency: dict[str, Any] = {}
        self._active_qr_data_url: str | None = None
        self._active_qr_session_key: str | None = None
        self._channel_capabilities_supported = bool(
            getattr(openclaw_gateway_client, "supports_channel_capabilities", True)
        )

    def resolve_clawbot_channel_id(self, channel_kind: str) -> str:
        if channel_kind != USER_CHANNEL_KIND:
            raise UiBoundaryError("INVALID_INPUT", "当前只支持微信 ClawBot。")
        return _PROVIDER_CHANNEL_ID

    def get_channel_status(
        self,
        *,
        probe: bool = False,
        include_qr: bool = False,
        refresh_qr: bool = False,
        poll_login: bool = False,
    ) -> dict[str, Any]:
        if (
            include_qr
            and not refresh_qr
            and not poll_login
            and self._active_qr_data_url
            and self._active_qr_session_key
        ):
            return _channel_status_for_user(
                state="disconnected",
                message="请用微信扫描二维码完成登录。",
                can_send_text=False,
                can_send_file=False,
                qr_code_image_data_url=self._active_qr_data_url,
                qr_code_refresh_required=False,
            )
        if include_qr and poll_login and self._active_qr_data_url and self._active_qr_session_key:
            qr_state = self._request_qr_login(refresh=False, poll_login=True)
            if qr_state.get("connected") is True:
                self._active_qr_data_url = None
                self._active_qr_session_key = None
                provider_state: Mapping[str, Any] = {"accountLabel": None, "lastConnectedAt": None}
                try:
                    latest_status = self._client.channels_status(probe=True)
                    provider_state = _extract_provider_status(latest_status, _PROVIDER_CHANNEL_ID)
                except Exception:
                    pass
                return self._connected_channel_status(provider_state)
            return _channel_status_for_user(
                state="disconnected",
                message=(
                    "请用微信扫描二维码完成登录。"
                    if qr_state.get("qrCodeImageDataUrl")
                    else str(
                        qr_state.get("message")
                        if qr_state.get("sessionClosed")
                        else "当前通道未返回二维码，请打开设备界面查看。"
                    )
                ),
                can_send_text=False,
                can_send_file=False,
                qr_code_image_data_url=qr_state.get("qrCodeImageDataUrl"),
                qr_code_refresh_required=not bool(qr_state.get("qrCodeImageDataUrl")),
            )
        try:
            raw_status = self._client.channels_status(probe=probe)
        except Exception:
            return _channel_status_for_user(state="error", message="微信通知暂不可用，请在设备界面查看。")
        if not _has_provider_channel(raw_status, _PROVIDER_CHANNEL_ID):
            try:
                plugins = self._client.plugins_list()
            except Exception:
                return _channel_status_for_user(state="error", message="微信通知暂不可用，请在设备界面查看。")
            plugin = _find_channel_plugin(plugins, _PROVIDER_CHANNEL_ID)
            if plugin is None:
                return _channel_status_for_user(state="disconnected", message="请先安装微信 ClawBot 插件。")
            if not bool(plugin.get("enabled", False)):
                return _channel_status_for_user(state="disconnected", message="请先启用微信 ClawBot 插件。")
        provider_state = _extract_provider_status(raw_status, _PROVIDER_CHANNEL_ID)
        if provider_state.get("state") != "connected":
            qr_state: dict[str, Any] = {"qrCodeImageDataUrl": None, "connected": False}
            if include_qr:
                qr_state = self._request_qr_login(refresh=refresh_qr, poll_login=poll_login)
                if qr_state.get("connected") is True:
                    self._active_qr_data_url = None
                    self._active_qr_session_key = None
                    try:
                        latest_status = self._client.channels_status(probe=True)
                        provider_state = _extract_provider_status(latest_status, _PROVIDER_CHANNEL_ID)
                    except Exception:
                        pass
                    return self._connected_channel_status(provider_state)
            return _channel_status_for_user(
                state="disconnected",
                message=(
                    "请用微信扫描二维码完成登录。"
                    if qr_state.get("qrCodeImageDataUrl")
                    else str(
                        qr_state.get("message")
                        if qr_state.get("sessionClosed")
                        else "当前通道未返回二维码，请打开设备界面查看。"
                    )
                ),
                account_label=provider_state.get("accountLabel"),
                last_connected_at=provider_state.get("lastConnectedAt"),
                can_send_text=False,
                can_send_file=False,
                qr_code_image_data_url=qr_state.get("qrCodeImageDataUrl"),
                qr_code_refresh_required=not bool(qr_state.get("qrCodeImageDataUrl")),
            )
        self._active_qr_data_url = None
        return self._connected_channel_status(provider_state)

    def _connected_channel_status(self, provider_state: Mapping[str, Any]) -> dict[str, Any]:
        can_send_file = False
        if self._channel_capabilities_supported:
            try:
                capabilities = self._client.channels_capabilities(channel=_PROVIDER_CHANNEL_ID)
                can_send_file = bool(
                    capabilities.get("media")
                    or capabilities.get("file")
                    or capabilities.get("sendMedia")
                    or capabilities.get("can_send_file")
                )
            except Exception as exc:
                if "unknown method: channels.capabilities" in str(exc):
                    self._channel_capabilities_supported = False
                # B-02 保守路径：能力探测未确认时，不宣称文件发送可用。
                can_send_file = False
        return _channel_status_for_user(
            state="connected",
            message=None if can_send_file else "微信文字通知可用，完整 PDF 暂不可发送。",
            account_label=provider_state.get("accountLabel"),
            last_connected_at=provider_state.get("lastConnectedAt"),
            can_send_text=True,
            can_send_file=can_send_file,
        )

    def _request_qr_login(self, *, refresh: bool, poll_login: bool) -> dict[str, Any]:
        try:
            if (
                not poll_login
                and not refresh
                and self._active_qr_data_url
                and self._active_qr_session_key
            ):
                return {
                    "qrCodeImageDataUrl": self._active_qr_data_url,
                    "connected": False,
                    "message": None,
                    "sessionClosed": False,
                }
            if refresh or not self._active_qr_data_url or not self._active_qr_session_key:
                raw = self._client.web_login_start(force=refresh, timeout_ms=12_000)
            else:
                raw = self._client.web_login_wait(
                    timeout_ms=8_000,
                    current_qr_data_url=self._active_qr_data_url,
                    session_key=self._active_qr_session_key,
                )
        except Exception as exc:
            message = _qr_login_error_message(str(exc))
            if message:
                self._active_qr_data_url = None
                self._active_qr_session_key = None
                return {
                    "qrCodeImageDataUrl": None,
                    "connected": False,
                    "message": message,
                    "sessionClosed": True,
                }
            return {"qrCodeImageDataUrl": self._active_qr_data_url, "connected": False}
        if not isinstance(raw, Mapping):
            return {"qrCodeImageDataUrl": self._active_qr_data_url, "connected": False}
        data_url = _optional_qr_data_url(raw.get("qrDataUrl") or raw.get("qrCodeImageDataUrl"))
        if data_url:
            self._active_qr_data_url = data_url
        session_key = _optional_str(raw.get("sessionKey"))
        if session_key:
            self._active_qr_session_key = session_key
        connected = raw.get("connected") is True
        message = _optional_str(raw.get("message"))
        session_closed = not connected and not data_url and _is_qr_login_session_closed_message(message)
        if session_closed:
            self._active_qr_data_url = None
            self._active_qr_session_key = None
        return {
            "qrCodeImageDataUrl": self._active_qr_data_url,
            "connected": connected,
            "message": message,
            "sessionClosed": session_closed,
        }

    def save_channel_config_via_openclaw(
        self,
        *,
        request_id: str,
        channel_kind: str,
        config_patch: Mapping[str, Any],
        expected_settings_version: str | None = None,
    ) -> dict[str, Any]:
        if request_id in self._idempotency:
            return self._idempotency[request_id]
        provider_channel = self.resolve_clawbot_channel_id(channel_kind)
        provider_patch = dict(config_patch)
        if provider_patch.get("enabled") is True:
            provider_patch.setdefault("channelConfigUpdatedAt", _now_iso())
        try:
            result = self._client.config_patch(
                expected_settings_version=expected_settings_version,
                patch={"channels": {provider_channel: provider_patch}},
            )
            out = {
                "status": self.get_channel_status(probe=True, include_qr=True, refresh_qr=True),
                "restartRequired": bool(result.get("restartRequired") or result.get("restart_required")),
            }
        except UiBoundaryError:
            raise
        except Exception as exc:
            raise UiBoundaryError("NOTIFICATION_UNAVAILABLE", "微信通知暂不可用，请在设备界面查看。") from exc
        self._idempotency[request_id] = out
        return out

    def send_text(self, *, channel_kind: str, text: str, dedupe_key: str) -> dict[str, object]:
        provider_channel = self.resolve_clawbot_channel_id(channel_kind)
        status = self.get_channel_status(probe=True)
        if str(status.get("state")) != "connected" or not bool(status.get("canSendText")):
            raise UiBoundaryError("NOTIFICATION_UNAVAILABLE", "微信通知暂不可用，请在设备界面查看。")
        try:
            sender = _resolve_text_sender(self._client)
        except AttributeError as exc:
            raise UiBoundaryError("NOTIFICATION_UNAVAILABLE", "微信通知暂不可用，请在设备界面查看。") from exc
        try:
            raw = sender(
                channel=provider_channel,
                text=text,
                dedupe_key=dedupe_key,
            )
        except Exception as exc:
            raise UiBoundaryError("NOTIFICATION_UNAVAILABLE", "微信通知暂不可用，请在设备界面查看。") from exc
        return _to_send_result(raw, default_sent=True)

    def send_report_file_via_channel(
        self,
        *,
        request_id: str,
        report_id: str,
        channel_kind: str,
        file_name: str,
        payload: bytes,
    ) -> dict[str, object]:
        _ = report_id
        provider_channel = self.resolve_clawbot_channel_id(channel_kind)
        status = self.get_channel_status(probe=True)
        if str(status.get("state")) != "connected":
            raise UiBoundaryError("NOTIFICATION_UNAVAILABLE", "微信通知暂不可用，请在设备界面查看。")
        if not bool(status.get("canSendFile")):
            raise UiBoundaryError("FILE_SEND_UNSUPPORTED", "完整报告文件暂不可发送，请在设备界面查看。")
        try:
            sender = _resolve_file_sender(self._client)
        except AttributeError as exc:
            raise UiBoundaryError("FILE_SEND_UNSUPPORTED", "完整报告文件暂不可发送，请在设备界面查看。") from exc
        try:
            raw = sender(
                channel=provider_channel,
                file_name=file_name,
                payload=payload,
                dedupe_key=request_id,
            )
        except Exception as exc:
            raise UiBoundaryError("FILE_SEND_UNSUPPORTED", "完整报告文件暂不可发送，请在设备界面查看。") from exc
        result = _to_send_result(raw, default_sent=False)
        if not bool(result.get("sent")):
            raise UiBoundaryError("FILE_SEND_UNSUPPORTED", "完整报告文件暂不可发送，请在设备界面查看。")
        return result


def to_channel_status_for_user(status: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "channelKind": USER_CHANNEL_KIND,
        "onboardingState": str(status.get("onboardingState", "onboarding")),
        "state": str(status.get("state", "unknown")),
        "displayName": _DISPLAY_NAME,
        "accountLabel": status.get("accountLabel"),
        "lastConnectedAt": status.get("lastConnectedAt"),
        "lastErrorMessage": status.get("lastErrorMessage"),
        "canSendText": bool(status.get("canSendText", False)),
        "canSendFile": bool(status.get("canSendFile", False)),
        "qrCodeImageDataUrl": _optional_qr_data_url(status.get("qrCodeImageDataUrl")),
        "qrCodeExpiresAt": _optional_str(status.get("qrCodeExpiresAt")),
        "qrCodeRefreshRequired": bool(status.get("qrCodeRefreshRequired", False)),
    }


def _channel_status_for_user(
    *,
    state: str,
    message: str | None,
    account_label: str | None = None,
    last_connected_at: str | None = None,
    can_send_text: bool = False,
    can_send_file: bool = False,
    qr_code_image_data_url: Any = None,
    qr_code_expires_at: Any = None,
    qr_code_refresh_required: bool = False,
) -> dict[str, Any]:
    return {
        "channelKind": USER_CHANNEL_KIND,
        "onboardingState": "completed",
        "state": state,
        "displayName": _DISPLAY_NAME,
        "accountLabel": account_label,
        "lastConnectedAt": last_connected_at,
        "lastErrorMessage": message,
        "canSendText": can_send_text,
        "canSendFile": can_send_file,
        "qrCodeImageDataUrl": _optional_qr_data_url(qr_code_image_data_url),
        "qrCodeExpiresAt": _optional_str(qr_code_expires_at),
        "qrCodeRefreshRequired": bool(qr_code_refresh_required),
        "updatedAt": _now_iso(),
    }


def _find_channel_plugin(plugins: Any, provider_channel_id: str) -> Mapping[str, Any] | None:
    if not isinstance(plugins, (list, tuple)):
        return None
    for item in plugins:
        if not isinstance(item, Mapping):
            continue
        channels = item.get("channels")
        if isinstance(channels, (list, tuple)) and provider_channel_id in channels:
            return item
        plugin_id = str(item.get("id", ""))
        if plugin_id == provider_channel_id:
            return item
    return None


def _has_provider_channel(raw: Mapping[str, Any], provider_channel_id: str) -> bool:
    channel_order = raw.get("channelOrder")
    if isinstance(channel_order, (list, tuple)) and provider_channel_id in channel_order:
        return True
    channels = raw.get("channels")
    if isinstance(channels, Mapping) and provider_channel_id in channels:
        return True
    channel_accounts = raw.get("channelAccounts")
    return isinstance(channel_accounts, Mapping) and provider_channel_id in channel_accounts


def _extract_provider_status(raw: Mapping[str, Any], provider_channel_id: str) -> dict[str, Any]:
    channel_accounts = raw.get("channelAccounts") if isinstance(raw, Mapping) else None
    if isinstance(channel_accounts, Mapping):
        accounts = channel_accounts.get(provider_channel_id)
        if isinstance(accounts, (list, tuple)):
            default_account_id = _default_account_id(raw, provider_channel_id)
            item = _find_default_account_snapshot(accounts, default_account_id)
            if item is not None:
                return {
                    "state": _account_state(item),
                    "accountLabel": _optional_str(
                        item.get("accountLabel")
                        or item.get("account_label")
                        or item.get("name")
                        or item.get("displayName")
                    ),
                    "lastConnectedAt": _optional_connected_at(
                        item.get("lastConnectedAt") or item.get("last_connected_at")
                    ),
                }
    channels = raw.get("channels") if isinstance(raw, Mapping) else None
    if isinstance(channels, Mapping):
        item = channels.get(provider_channel_id)
        if isinstance(item, Mapping):
            connected = item.get("connected") is True
            state = str(item.get("state", "connected" if connected else "unknown"))
            return {
                "state": state,
                "accountLabel": _optional_str(item.get("accountLabel") or item.get("account_label")),
                "lastConnectedAt": _optional_connected_at(
                    item.get("lastConnectedAt") or item.get("last_connected_at")
                ),
            }
    return {"state": "unknown", "accountLabel": None, "lastConnectedAt": None}


def _default_account_id(raw: Mapping[str, Any], provider_channel_id: str) -> str | None:
    defaults = raw.get("channelDefaultAccountId")
    if not isinstance(defaults, Mapping):
        return None
    return _optional_str(defaults.get(provider_channel_id))


def _find_default_account_snapshot(
    accounts: tuple[Any, ...] | list[Any],
    default_account_id: str | None,
) -> Mapping[str, Any] | None:
    fallback: Mapping[str, Any] | None = None
    for account in accounts:
        if not isinstance(account, Mapping):
            continue
        if fallback is None:
            fallback = account
        if default_account_id and _optional_str(account.get("accountId")) == default_account_id:
            return account
    return fallback


def _account_state(item: Mapping[str, Any]) -> str:
    if item.get("connected") is True:
        return "connected"
    if item.get("running") is True and item.get("configured") is True:
        return "connected"
    if item.get("running") is True:
        return "connecting"
    if item.get("enabled") is False:
        return "disconnected"
    return "disconnected"


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _is_qr_login_session_closed_message(message: str | None) -> bool:
    if not message:
        return False
    return any(
        marker in message
        for marker in (
            "当前没有进行中的登录",
            "二维码已过期",
            "登录超时",
            "连接流程已停止",
        )
    )


def _qr_login_error_message(detail: str) -> str | None:
    if "web login provider is not available" in detail:
        return "微信登录服务未就绪，请刷新二维码重试。"
    if _is_qr_login_session_closed_message(detail):
        return detail
    return None


def _optional_connected_at(value: Any) -> str | None:
    if isinstance(value, (int, float)) and value > 0:
        return (
            datetime.fromtimestamp(value / 1000, UTC)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
    return _optional_str(value)


def _optional_qr_data_url(value: Any) -> str | None:
    text = _optional_str(value)
    if not text:
        return None
    if not text.startswith("data:image/png;base64,"):
        return None
    if len(text) > 16_384:
        return None
    return text


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _resolve_text_sender(client: Any) -> Any:
    if hasattr(client, "channels_send_text"):
        return getattr(client, "channels_send_text")
    if hasattr(client, "channels_send_message"):
        return getattr(client, "channels_send_message")
    if hasattr(client, "channels_send"):
        return getattr(client, "channels_send")
    raise AttributeError("text sender unavailable")


def _resolve_file_sender(client: Any) -> Any:
    if hasattr(client, "channels_send_file"):
        return getattr(client, "channels_send_file")
    if hasattr(client, "channels_send_media"):
        return getattr(client, "channels_send_media")
    if hasattr(client, "channels_send_attachment"):
        return getattr(client, "channels_send_attachment")
    raise AttributeError("file sender unavailable")


def _to_send_result(raw: Any, *, default_sent: bool) -> dict[str, object]:
    if isinstance(raw, Mapping):
        sent = bool(raw.get("sent", default_sent))
        message_id = raw.get("messageId") or raw.get("message_id")
        return {"sent": sent, "messageId": message_id}
    return {"sent": default_sent, "messageId": None}
