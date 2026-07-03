from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
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


def test_crypto_history_root_prefers_explicit_release_seed(monkeypatch, tmp_path: Path) -> None:
    crypto_root = tmp_path / "release-crypto-history"
    runtime_root = tmp_path / "runtime-normalized"
    monkeypatch.setenv("CLAW_TRADE_CRYPTO_HISTORY_COLUMNAR_ROOT", str(crypto_root))
    monkeypatch.setenv("DATA_GATEWAY_COLUMNAR_ROOT", str(runtime_root))

    assert web_state._crypto_history_columnar_root_for_maintenance() == crypto_root


def test_crypto_history_root_uses_factory_seed_only_when_complete(monkeypatch, tmp_path: Path) -> None:
    factory_root = tmp_path / "factory-crypto"
    runtime_root = tmp_path / "runtime-normalized"
    daily_dir = factory_root / "market=CRYPTO" / "dataset=daily_bar" / "granularity=daily"
    daily_dir.mkdir(parents=True)
    (daily_dir / "part.parquet").write_bytes(b"not read in this test")
    complete_symbols = tuple(f"SYM{index:03d}USDT" for index in range(310))
    monkeypatch.setenv("DATA_GATEWAY_COLUMNAR_ROOT", str(runtime_root))
    monkeypatch.setattr(web_state, "_CRYPTO_HISTORY_FACTORY_COLUMNAR_ROOT", factory_root)
    monkeypatch.setattr(web_state, "_read_crypto_history_latest_rows", lambda _root: tuple((symbol, "2026-06-06") for symbol in complete_symbols))
    monkeypatch.setattr(web_state, "_load_binance_spot_trading_usdt_symbols", lambda: complete_symbols)
    monkeypatch.setattr(web_state, "_crypto_history_rows_are_complete", lambda _rows: False)

    assert web_state._crypto_history_columnar_root_for_maintenance() == runtime_root

    monkeypatch.setattr(web_state, "_crypto_history_rows_are_complete", lambda _rows: True)

    assert web_state._crypto_history_columnar_root_for_maintenance() == factory_root


def test_crypto_history_root_uses_runtime_when_factory_seed_misses_current_trading_coverage(
    monkeypatch,
    tmp_path: Path,
) -> None:
    factory_root = tmp_path / "factory-crypto"
    runtime_root = tmp_path / "runtime-normalized"
    daily_dir = factory_root / "market=CRYPTO" / "dataset=daily_bar" / "granularity=daily"
    daily_dir.mkdir(parents=True)
    (daily_dir / "part.parquet").write_bytes(b"not read in this test")
    covered_symbols = tuple(f"OLD{index:03d}USDT" for index in range(310))
    current_symbols = covered_symbols + tuple(f"NEW{index:03d}USDT" for index in range(40))
    monkeypatch.setenv("DATA_GATEWAY_COLUMNAR_ROOT", str(runtime_root))
    monkeypatch.setattr(web_state, "_CRYPTO_HISTORY_FACTORY_COLUMNAR_ROOT", factory_root)
    monkeypatch.setattr(web_state, "_read_crypto_history_latest_rows", lambda _root: tuple((symbol, "2026-06-06") for symbol in covered_symbols))
    monkeypatch.setattr(web_state, "_load_binance_spot_trading_usdt_symbols", lambda: current_symbols)
    monkeypatch.setattr(web_state, "_crypto_history_rows_are_complete", lambda _rows: True)

    assert web_state._crypto_history_columnar_root_for_maintenance() == runtime_root


def test_crypto_history_runtime_bootstrap_does_not_require_factory_write_flags(monkeypatch, tmp_path: Path) -> None:
    target_root = tmp_path / "runtime-normalized"
    raw_root = tmp_path / "runtime-binance"
    bootstrap_dir = tmp_path / "bootstrap"
    calls: list[tuple[str, ...]] = []
    monkeypatch.delenv("CLAW_TRADE_ALLOW_FACTORY_COLUMNAR_WRITE", raising=False)
    monkeypatch.delenv("CLAW_TRADE_ALLOW_FACTORY_MONGO_WRITE", raising=False)
    monkeypatch.setenv("DATA_GATEWAY_MONGODB_URI", "mongodb://127.0.0.1:27017/claw_trade")
    monkeypatch.setenv("DATA_GATEWAY_MONGODB_DATABASE", "claw_trade")
    monkeypatch.setattr(web_state, "_CRYPTO_HISTORY_FACTORY_DATA_ROOT", tmp_path / "factory-data")
    monkeypatch.setattr(web_state, "_CRYPTO_HISTORY_RUNTIME_RAW_ROOT", raw_root)
    monkeypatch.setattr(web_state, "_CRYPTO_HISTORY_BOOTSTRAP_DIR", bootstrap_dir)
    monkeypatch.setattr(web_state, "_run_crypto_history_bootstrap_command", lambda command: calls.append(tuple(command)))

    web_state._bootstrap_crypto_history_columnar_root(target_root, date(2026, 7, 1))

    assert len(calls) == 2
    download_cmd, import_cmd = calls
    assert "scripts/crypto/download_binance_public_data.py" in download_cmd
    assert download_cmd[download_cmd.index("--output-root") + 1] == str(raw_root)
    assert import_cmd[import_cmd.index("--spot-root") + 1] == str(raw_root / "data")
    assert import_cmd[import_cmd.index("--mongo-uri") + 1] == "mongodb://127.0.0.1:27017/claw_trade"
    assert import_cmd[import_cmd.index("--mongo-database") + 1] == "claw_trade"
    assert import_cmd[import_cmd.index("--columnar-root") + 1] == str(target_root)


def test_crypto_history_runtime_bootstrap_rejects_seed_mongo_database(monkeypatch, tmp_path: Path) -> None:
    target_root = tmp_path / "runtime-normalized"
    monkeypatch.delenv("CLAW_TRADE_ALLOW_FACTORY_COLUMNAR_WRITE", raising=False)
    monkeypatch.delenv("CLAW_TRADE_ALLOW_FACTORY_MONGO_WRITE", raising=False)
    monkeypatch.setenv("DATA_GATEWAY_MONGODB_URI", "mongodb://127.0.0.1:27017/claw_trade_crypto_history_usdt_20260608")
    monkeypatch.delenv("DATA_GATEWAY_MONGODB_DATABASE", raising=False)
    monkeypatch.delenv("CN_A_MONGODB_DATABASE", raising=False)
    monkeypatch.delenv("CRYPTO_MONGODB_DATABASE", raising=False)
    monkeypatch.setattr(web_state, "_CRYPTO_HISTORY_FACTORY_DATA_ROOT", tmp_path / "factory-data")
    monkeypatch.setattr(web_state, "_run_crypto_history_bootstrap_command", lambda _command: None)

    try:
        web_state._bootstrap_crypto_history_columnar_root(target_root, date(2026, 7, 1))
    except RuntimeError as exc:
        assert "refuses to write seed Mongo database" in str(exc)
    else:
        raise AssertionError("runtime bootstrap should reject seed Mongo database")


def test_crypto_history_factory_bootstrap_still_requires_explicit_write_flags(monkeypatch, tmp_path: Path) -> None:
    factory_data_root = tmp_path / "factory-data"
    factory_columnar = factory_data_root / "normalized-columnar-usdt-only"
    monkeypatch.delenv("CLAW_TRADE_ALLOW_FACTORY_COLUMNAR_WRITE", raising=False)
    monkeypatch.delenv("CLAW_TRADE_ALLOW_FACTORY_MONGO_WRITE", raising=False)
    monkeypatch.setattr(web_state, "_CRYPTO_HISTORY_FACTORY_DATA_ROOT", factory_data_root)
    monkeypatch.setattr(web_state, "_run_crypto_history_bootstrap_command", lambda _command: None)

    try:
        web_state._bootstrap_crypto_history_columnar_root(factory_columnar, date(2026, 7, 1))
    except RuntimeError as exc:
        assert "CLAW_TRADE_ALLOW_FACTORY_COLUMNAR_WRITE=1" in str(exc)
    else:
        raise AssertionError("factory bootstrap should require explicit write flags")
