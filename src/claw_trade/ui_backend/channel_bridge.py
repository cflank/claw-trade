from __future__ import annotations

import copy
import json
import os
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from claw_trade.ui_backend.settings_service import UiBoundaryError

USER_CHANNEL_KIND = "wechat_clawbot"
_PROVIDER_CHANNEL_ID = "openclaw-weixin"
_DISPLAY_NAME = "微信 ClawBot"
_QR_LOGIN_BACKGROUND_WAIT_TIMEOUT_MS = 480_000
_LIGHT_STATUS_CACHE_TTL_SECONDS = 30.0
_CONFIG_PATCH_STATUS_SETTLE_TIMEOUT_SECONDS = 18.0
_CONFIG_PATCH_STATUS_SETTLE_INTERVAL_SECONDS = 0.5
_FILE_SEND_CDN_SERVER_RETRY_ATTEMPTS = 2


def _qr_login_connected(raw: Mapping[str, Any]) -> bool:
    return raw.get("connected") is True or raw.get("alreadyConnected") is True


def _is_cdn_upload_server_error(exc: BaseException) -> bool:
    current: BaseException | None = exc
    for _ in range(6):
        if current is None:
            return False
        if "CDN upload server error" in str(current):
            return True
        current = current.__cause__ or current.__context__
    return False


class ChannelBridge:
    def __init__(self, openclaw_gateway_client: Any) -> None:
        self._client = openclaw_gateway_client
        self._idempotency: dict[str, Any] = {}
        self._active_qr_data_url: str | None = None
        self._active_qr_session_key: str | None = None
        self._qr_login_generation = 0
        self._qr_wait_inflight_generation: int | None = None
        self._qr_wait_result: dict[str, Any] | None = None
        self._qr_wait_lock = threading.Lock()
        self._light_status_cache: tuple[float, dict[str, Any]] | None = None
        self._light_status_cache_lock = threading.Lock()
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
        cache_light_status = not (probe or include_qr or refresh_qr or poll_login)
        if cache_light_status:
            cached = self._cached_light_channel_status()
            if cached is not None:
                return cached

        def finish(status: dict[str, Any]) -> dict[str, Any]:
            if cache_light_status:
                self._store_light_channel_status(status)
            return status

        if include_qr and poll_login and self._active_qr_data_url and self._active_qr_session_key:
            qr_state = self._request_qr_login(refresh=False, poll_login=True)
            if qr_state.get("connected") is True:
                self._active_qr_data_url = None
                self._active_qr_session_key = None
                self._invalidate_qr_wait_state()
                provider_state: Mapping[str, Any] = {"accountLabel": None, "lastConnectedAt": None}
                try:
                    latest_status = self._client.channels_status(probe=True)
                    provider_state = _extract_provider_status(latest_status, _PROVIDER_CHANNEL_ID)
                except Exception:
                    pass
                return finish(self._connected_channel_status(provider_state))
            return finish(
                _channel_status_for_user(
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
                ),
            )
        try:
            raw_status = self._client.channels_status(probe=probe)
        except Exception:
            return finish(_channel_status_for_user(state="error", message="微信通知暂不可用，请在设备界面查看。"))
        has_provider_channel = _has_provider_channel(raw_status, _PROVIDER_CHANNEL_ID)
        if not has_provider_channel:
            try:
                plugins = self._client.plugins_list()
            except Exception:
                return finish(_channel_status_for_user(state="error", message="微信通知暂不可用，请在设备界面查看。"))
            plugin = _find_channel_plugin(plugins, _PROVIDER_CHANNEL_ID)
            if plugin is None:
                self._active_qr_data_url = None
                self._active_qr_session_key = None
                self._invalidate_qr_wait_state()
                return finish(_channel_status_for_user(state="disconnected", message="请先安装微信 ClawBot 插件。"))
            if not bool(plugin.get("enabled", False)):
                self._active_qr_data_url = None
                self._active_qr_session_key = None
                self._invalidate_qr_wait_state()
                return finish(_channel_status_for_user(state="disconnected", message="请先启用微信 ClawBot 插件。"))
            if self._channel_disabled_by_config():
                self._active_qr_data_url = None
                self._active_qr_session_key = None
                self._invalidate_qr_wait_state()
                return finish(
                    _channel_status_for_user(
                        state="disconnected",
                        message="微信已解除连接，请点击刷新二维码重新扫码。",
                        qr_code_refresh_required=True,
                    )
                )
            self._active_qr_data_url = None
            self._active_qr_session_key = None
            self._invalidate_qr_wait_state()
            return finish(_channel_status_for_user(state="disconnected", message="微信登录服务启动中，请稍后重试。"))
        if include_qr:
            unavailable_status = self._login_provider_unavailable_status(raw_status)
            if unavailable_status is not None:
                return finish(unavailable_status)
        provider_state = _extract_provider_status(raw_status, _PROVIDER_CHANNEL_ID)
        if provider_state.get("state") == "connected":
            if include_qr and refresh_qr:
                qr_state = self._request_qr_login(refresh=True, poll_login=poll_login)
                if qr_state.get("connected") is True:
                    self._active_qr_data_url = None
                    self._active_qr_session_key = None
                    self._invalidate_qr_wait_state()
                    try:
                        latest_status = self._client.channels_status(probe=True)
                        provider_state = _extract_provider_status(latest_status, _PROVIDER_CHANNEL_ID)
                    except Exception:
                        pass
                    return finish(self._connected_channel_status(provider_state))
                if qr_state.get("qrCodeImageDataUrl") or qr_state.get("sessionClosed"):
                    return finish(
                        _channel_status_for_user(
                            state="disconnected",
                            message=(
                                "请用微信扫描二维码完成登录。"
                                if qr_state.get("qrCodeImageDataUrl")
                                else str(qr_state.get("message") or "当前通道未返回二维码，请打开设备界面查看。")
                            ),
                            account_label=provider_state.get("accountLabel"),
                            last_connected_at=provider_state.get("lastConnectedAt"),
                            can_send_text=False,
                            can_send_file=False,
                            qr_code_image_data_url=qr_state.get("qrCodeImageDataUrl"),
                            qr_code_refresh_required=not bool(qr_state.get("qrCodeImageDataUrl")),
                        ),
                    )
            self._active_qr_data_url = None
            self._active_qr_session_key = None
            self._invalidate_qr_wait_state()
            return finish(self._connected_channel_status(provider_state))

        if provider_state.get("state") != "connected":
            qr_state: dict[str, Any] = {"qrCodeImageDataUrl": None, "connected": False}
            if include_qr:
                qr_state = self._request_qr_login(refresh=refresh_qr, poll_login=poll_login)
                if qr_state.get("connected") is True:
                    self._active_qr_data_url = None
                    self._active_qr_session_key = None
                    self._invalidate_qr_wait_state()
                    try:
                        latest_status = self._client.channels_status(probe=True)
                        provider_state = _extract_provider_status(latest_status, _PROVIDER_CHANNEL_ID)
                    except Exception:
                        pass
                    return finish(self._connected_channel_status(provider_state))
            return finish(
                _channel_status_for_user(
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
                ),
            )
        self._active_qr_data_url = None
        self._active_qr_session_key = None
        self._invalidate_qr_wait_state()
        return finish(self._connected_channel_status(provider_state))

    def _cached_light_channel_status(self) -> dict[str, Any] | None:
        with self._light_status_cache_lock:
            cached = self._light_status_cache
            if cached is None:
                return None
            cached_at, status = cached
            if time.monotonic() - cached_at > _LIGHT_STATUS_CACHE_TTL_SECONDS:
                self._light_status_cache = None
                return None
            return copy.deepcopy(status)

    def _store_light_channel_status(self, status: Mapping[str, Any]) -> None:
        with self._light_status_cache_lock:
            self._light_status_cache = (time.monotonic(), copy.deepcopy(dict(status)))

    def _clear_light_channel_status_cache(self) -> None:
        with self._light_status_cache_lock:
            self._light_status_cache = None

    def _connected_channel_status(self, provider_state: Mapping[str, Any]) -> dict[str, Any]:
        can_send_file = False
        file_capability_unverified = False
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
                can_send_file = _has_file_sender(self._client)
                file_capability_unverified = can_send_file
        else:
            can_send_file = _has_file_sender(self._client)
            file_capability_unverified = can_send_file
        file_message = None
        if not can_send_file:
            file_message = "微信文字通知可用，完整 PDF 暂不可发送。"
        elif file_capability_unverified:
            file_message = "微信文件发送能力未验证，收到请求时会尝试发送完整 PDF。"
        return _channel_status_for_user(
            state="connected",
            message=file_message,
            account_label=provider_state.get("accountLabel"),
            last_connected_at=provider_state.get("lastConnectedAt"),
            can_send_text=True,
            can_send_file=can_send_file,
        )

    def _login_provider_unavailable_status(self, raw_status: Mapping[str, Any]) -> dict[str, Any] | None:
        try:
            plugins = self._client.plugins_list()
        except Exception:
            return None
        plugin = _find_channel_plugin(plugins, _PROVIDER_CHANNEL_ID)
        if plugin is None:
            message = (
                "请先启用微信 ClawBot 插件。"
                if _has_provider_channel(raw_status, _PROVIDER_CHANNEL_ID)
                else "请先安装微信 ClawBot 插件。"
            )
        elif bool(plugin.get("enabled", False)):
            return None
        else:
            message = "请先启用微信 ClawBot 插件。"
        self._active_qr_data_url = None
        self._active_qr_session_key = None
        self._invalidate_qr_wait_state()
        return _channel_status_for_user(
            state="disconnected",
            message=message,
            can_send_text=False,
            can_send_file=False,
            qr_code_refresh_required=False,
        )

    def _channel_disabled_by_config(self) -> bool:
        if not hasattr(self._client, "config_get"):
            return False
        try:
            config = self._client.config_get(paths=("channels.openclaw-weixin",))
        except Exception:
            return False
        return _config_weixin_enabled(config) is False

    def _request_qr_login(self, *, refresh: bool, poll_login: bool) -> dict[str, Any]:
        try:
            if (
                not refresh
                and self._active_qr_data_url
            ):
                if poll_login and self._active_qr_session_key:
                    self._start_background_qr_wait_if_needed()
                    wait_result = self._consume_qr_wait_result()
                    if wait_result is not None:
                        return self._apply_qr_wait_result(wait_result)
                return {
                    "qrCodeImageDataUrl": self._active_qr_data_url,
                    "connected": False,
                    "message": None,
                    "sessionClosed": False,
                }
            if refresh or not self._active_qr_data_url:
                self._invalidate_qr_wait_state()
                raw = self._client.web_login_start(force=refresh, timeout_ms=12_000)
        except Exception as exc:
            message = _qr_login_error_message(str(exc))
            if message:
                self._active_qr_data_url = None
                self._active_qr_session_key = None
                self._invalidate_qr_wait_state()
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
        session_key = _optional_str(raw.get("sessionKey") or raw.get("sessionId") or raw.get("loginSessionId"))
        if session_key:
            previous_session = self._active_qr_session_key
            self._active_qr_session_key = session_key
            if session_key != previous_session:
                self._invalidate_qr_wait_state()
        connected = _qr_login_connected(raw)
        message = _optional_str(raw.get("message"))
        session_closed = not connected and not data_url and _is_qr_login_session_closed_message(message)
        if session_closed:
            self._active_qr_data_url = None
            self._active_qr_session_key = None
            self._invalidate_qr_wait_state()
        return {
            "qrCodeImageDataUrl": self._active_qr_data_url,
            "connected": connected,
            "message": message,
            "sessionClosed": session_closed,
        }

    def _start_background_qr_wait_if_needed(self) -> None:
        qr_data_url = self._active_qr_data_url
        session_key = self._active_qr_session_key
        if not qr_data_url or not session_key:
            return
        with self._qr_wait_lock:
            generation = self._qr_login_generation
            if self._qr_wait_inflight_generation == generation:
                return
            self._qr_wait_inflight_generation = generation
        worker = threading.Thread(
            target=self._run_background_qr_wait,
            kwargs={
                "generation": generation,
                "current_qr_data_url": qr_data_url,
                "session_key": session_key,
            },
            daemon=True,
        )
        worker.start()

    def _run_background_qr_wait(
        self,
        *,
        generation: int,
        current_qr_data_url: str,
        session_key: str,
    ) -> None:
        result: dict[str, Any]
        try:
            raw = self._client.web_login_wait(
                timeout_ms=_QR_LOGIN_BACKGROUND_WAIT_TIMEOUT_MS,
                current_qr_data_url=current_qr_data_url,
                session_key=session_key,
            )
            result = {"raw": raw, "error": None}
        except Exception as exc:
            result = {"raw": None, "error": str(exc)}
        with self._qr_wait_lock:
            if generation != self._qr_login_generation:
                return
            self._qr_wait_result = result
            self._qr_wait_inflight_generation = None

    def _consume_qr_wait_result(self) -> dict[str, Any] | None:
        with self._qr_wait_lock:
            result = self._qr_wait_result
            self._qr_wait_result = None
            return result

    def _apply_qr_wait_result(self, wait_result: Mapping[str, Any]) -> dict[str, Any]:
        detail = _optional_str(wait_result.get("error"))
        if detail:
            message = _qr_login_error_message(detail)
            if message:
                self._active_qr_data_url = None
                self._active_qr_session_key = None
                self._invalidate_qr_wait_state()
                return {
                    "qrCodeImageDataUrl": None,
                    "connected": False,
                    "message": message,
                    "sessionClosed": True,
                }
            if self._active_qr_data_url and self._active_qr_session_key:
                self._start_background_qr_wait_if_needed()
            return {"qrCodeImageDataUrl": self._active_qr_data_url, "connected": False}
        raw = wait_result.get("raw")
        if not isinstance(raw, Mapping):
            if self._active_qr_data_url and self._active_qr_session_key:
                self._start_background_qr_wait_if_needed()
            return {"qrCodeImageDataUrl": self._active_qr_data_url, "connected": False}
        data_url = _optional_qr_data_url(raw.get("qrDataUrl") or raw.get("qrCodeImageDataUrl"))
        if data_url:
            self._active_qr_data_url = data_url
        session_key = _optional_str(raw.get("sessionKey") or raw.get("sessionId") or raw.get("loginSessionId"))
        if session_key and session_key != self._active_qr_session_key:
            self._active_qr_session_key = session_key
            self._invalidate_qr_wait_state()
        connected = _qr_login_connected(raw)
        message = _optional_str(raw.get("message"))
        session_closed = not connected and not data_url and _is_qr_login_session_closed_message(message)
        if session_closed:
            self._active_qr_data_url = None
            self._active_qr_session_key = None
            self._invalidate_qr_wait_state()
        elif not connected and self._active_qr_data_url and self._active_qr_session_key:
            self._start_background_qr_wait_if_needed()
        return {
            "qrCodeImageDataUrl": self._active_qr_data_url,
            "connected": connected,
            "message": message,
            "sessionClosed": session_closed,
        }

    def _invalidate_qr_wait_state(self) -> None:
        with self._qr_wait_lock:
            self._qr_login_generation += 1
            self._qr_wait_result = None
            self._qr_wait_inflight_generation = None

    def _clear_weixin_login_state(self) -> None:
        state_dir = _resolve_openclaw_state_dir()
        if state_dir is None:
            return
        weixin_dir = state_dir / "openclaw-weixin"
        accounts_dir = weixin_dir / "accounts"
        _unlink_if_file(weixin_dir / "accounts.json")
        if accounts_dir.is_dir():
            for child in accounts_dir.iterdir():
                if child.is_file() or child.is_symlink():
                    _unlink_if_file(child)
        _unlink_if_file(state_dir / "credentials" / "openclaw-weixin" / "credentials.json")
        _unlink_if_file(state_dir / "agents" / "default" / "sessions" / ".openclaw-weixin-sync" / "default.json")

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
        self._clear_light_channel_status_cache()
        provider_patch = dict(config_patch)
        if provider_patch.get("enabled") is True:
            provider_patch.setdefault("channelConfigUpdatedAt", _now_iso())
        if provider_patch.get("enabled") is False:
            self._active_qr_data_url = None
            self._active_qr_session_key = None
            self._invalidate_qr_wait_state()
            self._clear_weixin_login_state()
        try:
            result = self._client.config_patch(
                expected_settings_version=expected_settings_version,
                patch={"channels": {provider_channel: provider_patch}},
            )
            if provider_patch.get("enabled") is False:
                self._clear_weixin_login_state()
                status = _channel_status_for_user(
                    state="disconnected",
                    message="已解除连接。",
                    can_send_text=False,
                    can_send_file=False,
                    qr_code_refresh_required=True,
                )
            else:
                status = self._get_channel_status_after_config_patch(
                    include_qr=provider_patch.get("enabled") is True,
                    refresh_qr=provider_patch.get("enabled") is True,
                )
            out = {
                "status": status,
                "restartRequired": bool(result.get("restartRequired") or result.get("restart_required")),
            }
        except UiBoundaryError:
            raise
        except Exception as exc:
            raise UiBoundaryError("NOTIFICATION_UNAVAILABLE", "微信通知暂不可用，请在设备界面查看。") from exc
        self._idempotency[request_id] = out
        return out

    def _get_channel_status_after_config_patch(
        self,
        *,
        include_qr: bool,
        refresh_qr: bool,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + _CONFIG_PATCH_STATUS_SETTLE_TIMEOUT_SECONDS
        latest: dict[str, Any] | None = None
        while True:
            latest = self.get_channel_status(
                probe=True,
                include_qr=include_qr,
                refresh_qr=refresh_qr,
            )
            if not _should_retry_after_config_patch(latest):
                return latest
            if time.monotonic() >= deadline:
                return latest
            time.sleep(_CONFIG_PATCH_STATUS_SETTLE_INTERVAL_SECONDS)

    def send_text(
        self,
        *,
        channel_kind: str,
        text: str,
        dedupe_key: str,
        target: str | None = None,
        account_id: str | None = None,
    ) -> dict[str, object]:
        provider_channel = self.resolve_clawbot_channel_id(channel_kind)
        if not target:
            raise UiBoundaryError("NOTIFICATION_UNAVAILABLE", "微信通知暂不可用，请在设备界面查看。")
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
                to=target,
                account_id=account_id,
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
        payload: bytes | None = None,
        file_path: Path | None = None,
        target: str | None = None,
        account_id: str | None = None,
    ) -> dict[str, object]:
        _ = report_id
        if payload is None and file_path is None:
            raise UiBoundaryError("FILE_SEND_UNSUPPORTED", "完整报告文件暂不可发送，请在设备界面查看。")
        resolved_target = str(target or "").strip()
        if not resolved_target:
            raise UiBoundaryError("FILE_SEND_UNSUPPORTED", "完整报告文件暂不可发送，请在设备界面查看。")
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
        for attempt in range(_FILE_SEND_CDN_SERVER_RETRY_ATTEMPTS):
            try:
                raw = sender(
                    channel=provider_channel,
                    file_name=file_name,
                    payload=payload,
                    file_path=file_path,
                    dedupe_key=request_id if attempt == 0 else f"{request_id}:cdn-retry-{attempt}",
                    to=resolved_target,
                    account_id=account_id,
                )
                break
            except Exception as exc:
                if attempt + 1 < _FILE_SEND_CDN_SERVER_RETRY_ATTEMPTS and _is_cdn_upload_server_error(exc):
                    continue
                if _is_cdn_upload_server_error(exc):
                    raise UiBoundaryError("NOTIFICATION_UNAVAILABLE", "微信通知暂不可用，请在设备界面查看。") from exc
                raise UiBoundaryError("FILE_SEND_UNSUPPORTED", "完整报告文件暂不可发送，请在设备界面查看。") from exc
        result = _to_send_result(
            raw,
            default_sent=False,
            message_id_implies_sent=True,
            require_message_id_for_success=True,
        )
        if not bool(result.get("sent")):
            raise UiBoundaryError("FILE_SEND_UNSUPPORTED", "完整报告文件暂不可发送，请在设备界面查看。")
        if attempt > 0:
            _cleanup_superseded_file_delivery_queue_entries(
                channel=provider_channel,
                target=resolved_target,
                account_id=account_id,
                file_name=file_name,
                file_path=file_path,
                original_dedupe_key=request_id,
            )
        return result

    def resolve_default_report_file_target(self, *, channel_kind: str) -> tuple[str, str | None] | None:
        self.resolve_clawbot_channel_id(channel_kind)
        return _single_weixin_report_file_target()


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


def _config_weixin_enabled(config: Mapping[str, Any]) -> bool | None:
    channels = config.get("channels")
    if isinstance(channels, Mapping):
        channel = channels.get(_PROVIDER_CHANNEL_ID)
        if isinstance(channel, Mapping) and channel.get("enabled") is False:
            return False
    parsed = config.get("parsed")
    if isinstance(parsed, Mapping):
        nested = _config_weixin_enabled(parsed)
        if nested is not None:
            return nested
    nested_config = config.get("config")
    if isinstance(nested_config, Mapping):
        nested = _config_weixin_enabled(nested_config)
        if nested is not None:
            return nested
    return None


def _resolve_openclaw_state_dir() -> Path | None:
    state_dir = _optional_str(os.environ.get("OPENCLAW_STATE_DIR"))
    if state_dir:
        return Path(state_dir)
    config_path = _optional_str(os.environ.get("OPENCLAW_CONFIG_PATH"))
    if config_path:
        return Path(config_path).parent
    return None


def _single_weixin_report_file_target() -> tuple[str, str | None] | None:
    state_dir = _resolve_openclaw_state_dir()
    if state_dir is None:
        default_state_dir = Path(".runtime/dev-services/openclaw-state")
        state_dir = default_state_dir if default_state_dir.is_dir() else None
    if state_dir is None:
        return None
    accounts_dir = state_dir / "openclaw-weixin" / "accounts"
    if not accounts_dir.is_dir():
        return None
    candidates: set[tuple[str, str | None]] = set()
    for path in sorted(accounts_dir.glob("*.context-tokens.json")):
        account_id = path.name.removesuffix(".context-tokens.json").strip()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, Mapping):
            continue
        for raw_user_id, raw_token in data.items():
            user_id = str(raw_user_id or "").strip()
            token = str(raw_token or "").strip()
            if user_id.endswith("@im.wechat") and token:
                candidates.add((user_id, account_id or None))
    if len(candidates) != 1:
        return None
    return next(iter(candidates))


def _cleanup_superseded_file_delivery_queue_entries(
    *,
    channel: str,
    target: str,
    account_id: str | None,
    file_name: str,
    file_path: Path | None,
    original_dedupe_key: str,
) -> None:
    state_dir = _resolve_openclaw_state_dir()
    if state_dir is None:
        default_state_dir = Path(".runtime/dev-services/openclaw-state")
        state_dir = default_state_dir if default_state_dir.is_dir() else None
    if state_dir is None:
        return
    queue_dir = state_dir / "delivery-queue"
    if not queue_dir.is_dir():
        return
    expected_media_url = str(file_path) if file_path is not None else None
    for queue_path in sorted(queue_dir.glob("*.json")):
        try:
            entry = json.loads(queue_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(entry, Mapping):
            continue
        if not _queued_delivery_matches_superseded_retry(
            entry,
            channel=channel,
            target=target,
            account_id=account_id,
            file_name=file_name,
            expected_media_url=expected_media_url,
            original_dedupe_key=original_dedupe_key,
        ):
            continue
        _mark_delivery_queue_entry_superseded(queue_path)


def _queued_delivery_matches_superseded_retry(
    entry: Mapping[str, Any],
    *,
    channel: str,
    target: str,
    account_id: str | None,
    file_name: str,
    expected_media_url: str | None,
    original_dedupe_key: str,
) -> bool:
    if str(entry.get("channel") or "") != channel:
        return False
    if str(entry.get("to") or "") != target:
        return False
    if account_id is not None and str(entry.get("accountId") or "") != account_id:
        return False
    if "CDN upload server error" not in str(entry.get("lastError") or ""):
        return False
    mirror = entry.get("mirror")
    if not isinstance(mirror, Mapping):
        return False
    if str(mirror.get("idempotencyKey") or "") != original_dedupe_key:
        return False
    if expected_media_url and _queued_delivery_has_media_url(entry, expected_media_url):
        return True
    return _queued_delivery_has_file_name(entry, file_name)


def _queued_delivery_has_media_url(entry: Mapping[str, Any], expected_media_url: str) -> bool:
    payloads = entry.get("payloads")
    if isinstance(payloads, list):
        for payload in payloads:
            if isinstance(payload, Mapping) and str(payload.get("mediaUrl") or "") == expected_media_url:
                return True
    mirror = entry.get("mirror")
    if not isinstance(mirror, Mapping):
        return False
    media_urls = mirror.get("mediaUrls")
    return isinstance(media_urls, list) and expected_media_url in {str(item) for item in media_urls}


def _queued_delivery_has_file_name(entry: Mapping[str, Any], file_name: str) -> bool:
    payloads = entry.get("payloads")
    if isinstance(payloads, list):
        for payload in payloads:
            if isinstance(payload, Mapping) and str(payload.get("text") or "") == file_name:
                return True
    mirror = entry.get("mirror")
    return isinstance(mirror, Mapping) and str(mirror.get("text") or "") == file_name


def _mark_delivery_queue_entry_superseded(queue_path: Path) -> None:
    marker = queue_path.with_name(f"{queue_path.name}.superseded-{int(time.time() * 1000)}")
    try:
        queue_path.replace(marker)
    except OSError:
        pass


def _unlink_if_file(path: Path) -> None:
    try:
        if path.is_file() or path.is_symlink():
            path.unlink()
    except OSError:
        pass


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
    lowered = detail.lower()
    if "scope upgrade pending approval" in lowered or "pairing required" in lowered:
        return "请在设备界面批准连接权限后刷新二维码。"
    if _is_qr_login_session_closed_message(detail):
        return detail
    return None


def _should_retry_after_config_patch(status: Mapping[str, Any]) -> bool:
    if str(status.get("state")) == "error":
        return True
    message = _optional_str(status.get("lastErrorMessage"))
    return message in {
        "微信通知暂不可用，请在设备界面查看。",
        "微信登录服务未就绪，请刷新二维码重试。",
        "请先安装微信 ClawBot 插件。",
        "请先启用微信 ClawBot 插件。",
        "微信登录服务启动中，请稍后重试。",
    }


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


def _has_file_sender(client: Any) -> bool:
    return any(
        hasattr(client, name)
        for name in ("channels_send_file", "channels_send_media", "channels_send_attachment")
    )


def _to_send_result(
    raw: Any,
    *,
    default_sent: bool,
    message_id_implies_sent: bool = False,
    require_message_id_for_success: bool = False,
) -> dict[str, object]:
    if isinstance(raw, Mapping):
        message_id = raw.get("messageId") or raw.get("message_id")
        if isinstance(message_id, str):
            message_id = message_id.strip() or None
        else:
            message_id = None
        if raw.get("ok") is False:
            sent = False
        elif "sent" in raw:
            sent = bool(raw.get("sent"))
        elif message_id_implies_sent and message_id:
            sent = True
        else:
            sent = default_sent
        if require_message_id_for_success and sent and not message_id:
            sent = False
        return {"sent": sent, "messageId": message_id}
    return {"sent": default_sent, "messageId": None}
