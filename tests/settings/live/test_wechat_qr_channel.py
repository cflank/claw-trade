from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from claw_trade.ui_backend.channel_bridge import ChannelBridge
from claw_trade.web.openclaw_gateway import OpenClawGatewayRpcClient

_ALLOWED_ROOT_CAUSE_MESSAGES = {
    "请先安装微信 ClawBot 插件。",
    "请先启用微信 ClawBot 插件。",
    "微信登录服务未就绪，请刷新二维码重试。",
    "当前通道未返回二维码，请打开设备界面查看。",
    "请在设备界面批准连接权限后刷新二维码。",
    "登录超时，请重试。",
}
_ALLOWED_ROOT_CAUSE_MESSAGES_WHEN_PLUGIN_READY = {
    "请在设备界面批准连接权限后刷新二维码。",
    "登录超时，请重试。",
}
_FORBIDDEN_SURFACE = ("/report", "报告指令", "Channel ID", "插件包名", "scope", "gateway")
_WEIXIN_PLUGIN_ID = "openclaw-weixin"


@dataclass
class _TracingRpcClient:
    rpc: OpenClawGatewayRpcClient
    web_login_start_calls: list[dict[str, Any]] = field(default_factory=list)

    def __getattr__(self, item: str) -> Any:
        return getattr(self.rpc, item)

    def web_login_start(self, *, force: bool = False, timeout_ms: int = 12_000) -> Any:
        self.web_login_start_calls.append({"force": force, "timeoutMs": timeout_ms})
        return self.rpc.web_login_start(force=force, timeout_ms=timeout_ms)


def _rpc_client() -> OpenClawGatewayRpcClient:
    return OpenClawGatewayRpcClient(
        gateway_call_bin=os.environ.get("OPENCLAW_GATEWAY_CALL_BIN", "openclaw"),
        gateway_ws_url=os.environ.get("OPENCLAW_GATEWAY_URL", "ws://127.0.0.1:18789"),
        timeout_ms=int(os.environ.get("OPENCLAW_GATEWAY_TIMEOUT_MS", "10000")),
        token=os.environ.get("OPENCLAW_GATEWAY_TOKEN") or None,
        password=os.environ.get("OPENCLAW_GATEWAY_PASSWORD") or None,
    )


def _assert_no_forbidden_surface(status: dict[str, Any]) -> None:
    rendered = str(status)
    for keyword in _FORBIDDEN_SURFACE:
        assert keyword not in rendered


def _read_runtime_weixin_declared_enabled() -> bool | None:
    config_path = os.environ.get("OPENCLAW_CONFIG_PATH")
    if not config_path:
        return None
    try:
        parsed = json.loads(Path(config_path).read_text(encoding="utf-8"))
    except Exception:
        return None
    plugins = parsed.get("plugins")
    if not isinstance(plugins, dict):
        return None
    entries = plugins.get("entries")
    if not isinstance(entries, dict):
        return None
    weixin = entries.get(_WEIXIN_PLUGIN_ID)
    if not isinstance(weixin, dict):
        return False
    return bool(weixin.get("enabled", False))


def _find_weixin_plugin(plugins_payload: Any) -> dict[str, Any] | None:
    if isinstance(plugins_payload, dict):
        if isinstance(plugins_payload.get("plugins"), list):
            plugins_payload = plugins_payload.get("plugins")
        elif isinstance(plugins_payload.get("items"), list):
            plugins_payload = plugins_payload.get("items")
    if not isinstance(plugins_payload, list):
        return None
    for item in plugins_payload:
        if isinstance(item, dict) and str(item.get("id") or "").strip() == _WEIXIN_PLUGIN_ID:
            return item
    return None


def test_live_wechat_qr_comes_from_runtime_or_reports_real_root_cause() -> None:
    traced_rpc = _TracingRpcClient(_rpc_client())
    bridge = ChannelBridge(traced_rpc)
    declared_enabled = _read_runtime_weixin_declared_enabled()
    weixin_plugin = _find_weixin_plugin(traced_rpc.plugins_list())
    plugin_ready = weixin_plugin is not None and bool(weixin_plugin.get("enabled", False))

    if declared_enabled is True:
        assert weixin_plugin is not None, "runtime 已声明启用微信插件，但 OpenClaw plugins list 未发现 openclaw-weixin。"
        assert plugin_ready is True, "runtime 已声明启用微信插件，但 plugins list 显示其未启用。"

    initial = bridge.get_channel_status(probe=True, include_qr=True)
    refreshed = bridge.get_channel_status(probe=True, include_qr=True, refresh_qr=True)

    assert initial["channelKind"] == "wechat_clawbot"
    assert refreshed["channelKind"] == "wechat_clawbot"
    assert "providerChannelId" not in initial
    assert "providerChannelId" not in refreshed
    _assert_no_forbidden_surface(initial)
    _assert_no_forbidden_surface(refreshed)

    if refreshed["state"] == "connected":
        assert refreshed["canSendText"] is True
        return

    qr_data_url = refreshed.get("qrCodeImageDataUrl")
    if isinstance(qr_data_url, str) and qr_data_url.startswith("data:image/png;base64,"):
        assert any(call.get("force") is True for call in traced_rpc.web_login_start_calls)
        return

    root_cause = str(refreshed.get("lastErrorMessage") or "")
    if plugin_ready:
        assert any(call.get("force") is True for call in traced_rpc.web_login_start_calls)
        assert root_cause in _ALLOWED_ROOT_CAUSE_MESSAGES_WHEN_PLUGIN_READY, (
            "微信插件已 ready，但仍未返回真实二维码/connected；"
            f"state={refreshed.get('state')} rootCause={root_cause!r}"
        )
        return
    assert root_cause in _ALLOWED_ROOT_CAUSE_MESSAGES
    if traced_rpc.web_login_start_calls:
        assert traced_rpc.web_login_start_calls[-1].get("force") is True
