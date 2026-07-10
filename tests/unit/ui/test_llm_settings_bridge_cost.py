from __future__ import annotations

from decimal import Decimal

from claw_trade.runtime.deepseek_balance import DeepSeekBalance
from claw_trade.ui_backend.llm_settings_bridge import LlmSettingsBridge


class _ConfigClient:
    def config_get(self, *, paths):  # type: ignore[no-untyped-def]
        _ = paths
        return {
            "active_provider": "deepseek",
            "agents": {"defaults": {"model": {"primary": "deepseek/deepseek-chat"}}},
            "models": {
                "providers": {
                    "deepseek": {
                        "apiKey": "sk-test",
                        "baseUrl": "https://api.deepseek.com",
                        "models": [{"id": "deepseek-chat"}],
                    }
                }
            },
        }


def test_capture_report_model_cost_snapshot_reads_deepseek_cny_balance() -> None:
    calls: list[tuple[str, str | None]] = []

    def fetch_balance(*, api_key: str, endpoint_url: str | None = None) -> DeepSeekBalance:
        calls.append((api_key, endpoint_url))
        return DeepSeekBalance(
            currency="CNY",
            total_balance=Decimal("8.88"),
            is_available=True,
            checked_at="2026-07-10T00:00:00+00:00",
        )

    bridge = LlmSettingsBridge(_ConfigClient(), deepseek_balance_fetcher=fetch_balance)

    snapshot = bridge.capture_report_model_cost_snapshot()

    assert snapshot.state == "captured"
    assert snapshot.provider == "deepseek"
    assert snapshot.balance == Decimal("8.88")
    assert calls == [("sk-test", "https://api.deepseek.com")]
