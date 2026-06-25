from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, Mapping, Sequence

from claw_trade.data_gateway.models import DataResultStatus
from claw_trade.ui_contracts.enums import MarketProfile
from claw_trade.web import state as web_state


class _FakeDataAPI:
    def __init__(self) -> None:
        self.requests: list[Mapping[str, Any]] = []

    def request_data(self, requests: Sequence[Mapping[str, Any]]) -> Sequence[Any]:
        self.requests.extend(requests)
        now = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        return [
            SimpleNamespace(
                status=DataResultStatus.READY,
                rows=(
                    {
                        "price": 71000.0,
                        "timestamp": now,
                        "evidence_ref": "dataset://quote/BTC",
                    },
                ),
                dataset_refs=(),
            )
        ]


class _FakeChannelBridge:
    def __init__(self, default_target: tuple[str, str | None] | None = ("sender-1", "account-1")) -> None:
        self.default_target = default_target
        self.calls: list[dict[str, object]] = []

    def resolve_default_report_file_target(self, *, channel_kind: str) -> tuple[str, str | None] | None:
        assert channel_kind == "wechat_clawbot"
        return self.default_target

    def send_text(
        self,
        *,
        channel_kind: str,
        text: str,
        dedupe_key: str,
        target: str,
        account_id: str | None,
    ) -> dict[str, object]:
        self.calls.append(
            {
                "channelKind": channel_kind,
                "text": text,
                "dedupeKey": dedupe_key,
                "target": target,
                "accountId": account_id,
            }
        )
        return {"sent": True}


def test_lazy_price_alert_quote_provider_uses_data_gateway_runtime(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    data_api = _FakeDataAPI()
    monkeypatch.setattr(web_state, "build_data_gateway_runtime_from_env", lambda: SimpleNamespace(data_api=data_api))

    quote = web_state._LazyPriceAlertQuoteProvider()("BTC/USDT", MarketProfile.CRYPTO)

    assert quote["current_price"] == 71000.0
    assert quote["evidence_ref"] == "dataset://quote/BTC"
    assert data_api.requests[0]["consumer"] == "price_alert"


def test_price_alert_wechat_send_uses_default_channel_target_when_notification_has_no_target() -> None:
    bridge = _FakeChannelBridge()

    result = web_state._send_price_alert_channel_text(
        bridge,  # type: ignore[arg-type]
        "BTC 已触发价格提醒。",
        {"channel": "wechat_clawbot", "dedupeKey": "alert-1"},
    )

    assert result == {"sent": True}
    assert bridge.calls == [
        {
            "channelKind": "wechat_clawbot",
            "text": "BTC 已触发价格提醒。",
            "dedupeKey": "alert-1",
            "target": "sender-1",
            "accountId": "account-1",
        }
    ]


def test_price_alert_wechat_send_uses_stored_target_over_default_channel_target() -> None:
    bridge = _FakeChannelBridge()

    result = web_state._send_price_alert_channel_text(
        bridge,  # type: ignore[arg-type]
        "BTC 已触发价格提醒。",
        {
            "channel": "wechat_clawbot",
            "dedupeKey": "alert-30",
            "target": "sender-codex",
            "accountId": "account-codex",
        },
    )

    assert result == {"sent": True}
    assert bridge.calls == [
        {
            "channelKind": "wechat_clawbot",
            "text": "BTC 已触发价格提醒。",
            "dedupeKey": "alert-30",
            "target": "sender-codex",
            "accountId": "account-codex",
        }
    ]
