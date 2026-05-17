from __future__ import annotations

from datetime import date, timedelta
import json
from pathlib import Path
import sys
from typing import Any, Mapping

import pandas as pd
import pytest
import requests
from pymongo.errors import DuplicateKeyError


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_PYTHON_ROOT = REPO_ROOT / "openclaw_plugins" / "claw-trade-frontline-tools" / "python"
if str(SHARED_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(SHARED_PYTHON_ROOT))

from frontline_data_pack.crypto_market_data_pack import build_crypto_market_data_pack  # noqa: E402


def test_crypto_market_pack_returns_ohlcv_indicators_and_chart_assets(tmp_path: Path) -> None:
    pack = build_crypto_market_data_pack(
        _tool_input("BTC"),
        _runtime_context(tmp_path),
        env={},
        fetch_json=_fake_binance_klines,
        chart_render=_fake_chart_render,
        bb_trade_context_fetch=_fake_bb_trade_context,
    )

    assert pack["ok"] is True
    assert pack["readiness"]["status"] == "ready"
    assert pack["data"]["source_role"] == "bb_market_structure_plus_chart_ohlcv_supplement"
    assert pack["data"]["bb_trade_context"]["available"] is True
    assert pack["data"]["bb_trade_context"]["derivatives"]["funding_rate"] == 0.00004955
    assert pack["data"]["latest_price"]["close"] is not None
    assert pack["data"]["indicators"]["macd"]["macd"] is not None
    assert len(pack["data"]["chart_files"]) == 2
    for chart_path in pack["data"]["chart_files"]:
        assert Path(chart_path).is_file()
    assert [attempt["provider"] for attempt in pack["provider_attempts"]][-2:] == ["BB", "Binance"]
    assert pack["provider_attempts"][-1]["role"] == "chart_ohlcv_supplement"
    assert "BB/CoinGlass 摘要来源已转写为详细自然语言材料" in pack["reader_brief"]
    assert "## 指标逐项材料" in pack["reader_brief"]
    assert "## 条件化操作框架（BB 技术材料，不是最终投资裁决）" in pack["reader_brief"]
    assert "资金费率资料包原始读数为 4.955e-05" in pack["reader_brief"]
    assert "不得自行换算百分比" in pack["reader_brief"]
    _assert_reader_brief_has_no_internal_crypto_field_names(pack["reader_brief"])
    assert len(pack["reader_brief"].encode("utf-8")) > 8_000
    assert "more characters truncated" not in pack["reader_brief"]
    assert (tmp_path / "crypto_market_data_pack.json").is_file()
    raw_path = tmp_path / "provider_raw" / "crypto_market_data_pack" / "raw_payload.json"
    assert raw_path.is_file()
    raw_payload = json.loads(raw_path.read_text(encoding="utf-8"))
    assert raw_payload["bb_trade_context"]["data"]["derivatives"]["funding_rate"] == 0.00004955


def test_crypto_market_pack_uses_fresh_cache_without_refetching_provider(tmp_path: Path) -> None:
    cache = _FakeCollection()
    first = build_crypto_market_data_pack(
        _tool_input("BTC"),
        _runtime_context(tmp_path / "first"),
        env={},
        fetch_json=_fake_binance_klines,
        chart_render=_fake_chart_render,
        bb_trade_context_fetch=_fake_bb_trade_context,
        provider_cache_collection=cache,
    )
    assert [attempt["status"] for attempt in first["provider_attempts"]] == [
        "cache_miss",
        "success",
        "cache_miss",
        "success",
    ]

    second = build_crypto_market_data_pack(
        _tool_input("BTC"),
        _runtime_context(tmp_path / "second"),
        env={},
        fetch_json=_fail_if_called,
        chart_render=_fake_chart_render,
        bb_trade_context_fetch=_fail_if_called,
        provider_cache_collection=cache,
    )

    assert second["ok"] is True
    assert second["provider_attempts"][0]["status"] == "cache_hit"
    assert second["provider_attempts"][0]["cache_is_openviking"] is False
    assert second["provider_attempts"][1]["status"] == "cache_hit"
    assert "viking://" not in repr(cache.docs)


def test_crypto_market_pack_cache_hit_attempt_write_is_idempotent(tmp_path: Path) -> None:
    cache = _FakeCollection()
    attempts = _DuplicateCheckingCollection()
    build_crypto_market_data_pack(
        _tool_input("BTC"),
        _runtime_context(tmp_path / "first"),
        env={},
        fetch_json=_fake_binance_klines,
        chart_render=_fake_chart_render,
        bb_trade_context_fetch=_fake_bb_trade_context,
        provider_cache_collection=cache,
        provider_attempts_collection=attempts,
    )

    second = build_crypto_market_data_pack(
        _tool_input("BTC"),
        _runtime_context(tmp_path / "second"),
        env={},
        fetch_json=_fail_if_called,
        chart_render=_fake_chart_render,
        bb_trade_context_fetch=_fail_if_called,
        provider_cache_collection=cache,
        provider_attempts_collection=attempts,
    )

    assert second["ok"] is True
    assert [attempt["status"] for attempt in second["provider_attempts"]] == ["cache_hit", "cache_hit"]
    assert attempts.duplicate_count >= 1


def test_crypto_market_pack_preserves_bb_liquidation_map_clusters(tmp_path: Path) -> None:
    pack = build_crypto_market_data_pack(
        _tool_input("BTC"),
        _runtime_context(tmp_path),
        env={},
        fetch_json=_fake_binance_klines,
        chart_render=_fake_chart_render,
        bb_trade_context_fetch=_fake_bb_trade_context_with_liquidation_lists,
    )

    liquidation_map = pack["data"]["bb_trade_context"]["liquidation_map"]
    assert liquidation_map["clusters"][0]["price"] == 82300.24
    assert liquidation_map["nearest_above"]["price"] == 82300.24
    assert liquidation_map["nearest_below"]["price"] == 77126.73
    assert "价格 82300.2" in pack["reader_brief"]
    assert "价格 77126.7" in pack["reader_brief"]
    assert "价格跌破 82300.2" not in pack["reader_brief"]
    assert "价格有效跌破 82300.2" not in pack["reader_brief"]
    assert "价格跌破 77126.7" in pack["reader_brief"]
    assert "价格有效跌破 77126.7" in pack["reader_brief"]
    assert "清算簇：缺失" not in pack["reader_brief"]


def test_crypto_market_pack_cached_empty_is_reported_as_gap(tmp_path: Path) -> None:
    cache = _FakeCollection()
    first = build_crypto_market_data_pack(
        _tool_input("BTC"),
        _runtime_context(tmp_path / "empty-first"),
        env={},
        fetch_json=lambda *_args, **_kwargs: [],
        chart_render=_fake_chart_render,
        bb_trade_context_fetch=_fake_bb_trade_context,
        provider_cache_collection=cache,
    )
    assert first["ok"] is True
    assert first["provider_attempts"][-1]["status"] == "empty"

    second = build_crypto_market_data_pack(
        _tool_input("BTC"),
        _runtime_context(tmp_path / "empty-second"),
        env={},
        fetch_json=_fail_if_called,
        chart_render=_fake_chart_render,
        bb_trade_context_fetch=_fail_if_called,
        provider_cache_collection=cache,
    )

    assert second["ok"] is True
    assert second["provider_attempts"][0]["status"] == "cache_hit"
    assert second["provider_attempts"][1]["status"] == "cache_hit"
    assert second["provider_attempts"][1]["accepted_count"] == 0
    assert any("accepted_count=0" in gap for gap in second["data_gaps"])


def test_crypto_market_pack_marks_chart_gap_without_fake_chart_path(tmp_path: Path) -> None:
    pack = build_crypto_market_data_pack(
        _tool_input("BTC"),
        _runtime_context(tmp_path),
        env={},
        fetch_json=_fake_binance_klines,
        chart_render=_empty_chart_render,
        bb_trade_context_fetch=_fake_bb_trade_context,
    )

    assert pack["ok"] is True
    assert pack["readiness"]["status"] == "partial"
    assert pack["data"]["chart_files"] == []
    assert any("技术图表生成未产出" in gap for gap in pack["data_gaps"])


def test_crypto_market_pack_prefers_runtime_dates_over_tool_dates(tmp_path: Path) -> None:
    context = _runtime_context(tmp_path)
    context["start_date"] = "2026-04-01"
    context["end_date"] = "2026-05-16"
    tool_input = _tool_input("BTC")
    tool_input["start_date"] = "2025-01-01"
    tool_input["end_date"] = "2025-12-31"

    pack = build_crypto_market_data_pack(
        tool_input,
        context,
        env={},
        fetch_json=_fake_binance_klines,
        chart_render=_fake_chart_render,
        bb_trade_context_fetch=_fake_bb_trade_context,
    )

    assert pack["input"]["start_date"] == "2026-04-01"
    assert pack["input"]["end_date"] == "2026-05-16"


def test_crypto_market_pack_returns_partial_when_ohlcv_provider_fails_but_bb_is_available(tmp_path: Path) -> None:
    def failing_fetch(*_args: Any, **_kwargs: Any) -> Any:
        raise requests.Timeout("forced timeout")

    pack = build_crypto_market_data_pack(
        _tool_input("BTC"),
        _runtime_context(tmp_path),
        env={},
        fetch_json=failing_fetch,
        chart_render=_fake_chart_render,
        bb_trade_context_fetch=_fake_bb_trade_context,
    )

    assert pack["ok"] is True
    assert pack["readiness"]["status"] == "partial"
    assert pack["data"]["chart_files"] == []
    assert pack["provider_attempts"][-1]["status"] == "error"
    assert pack["provider_attempts"][-1]["error_code"] == "timeout"


def test_crypto_market_pack_returns_insufficient_when_bb_and_ohlcv_fail(tmp_path: Path) -> None:
    def failing_fetch(*_args: Any, **_kwargs: Any) -> Any:
        raise requests.Timeout("forced timeout")

    pack = build_crypto_market_data_pack(
        _tool_input("BTC"),
        _runtime_context(tmp_path),
        env={"BB_MCP_CWD": str(tmp_path / "missing-bb")},
        fetch_json=failing_fetch,
        chart_render=_fake_chart_render,
    )

    assert pack["ok"] is False
    assert pack["readiness"]["status"] == "insufficient"
    assert any("BB MCP 本地 build 输出不可用" in gap for gap in pack["data_gaps"])


def test_crypto_market_pack_rejects_wrong_worker_context(tmp_path: Path) -> None:
    context = _runtime_context(tmp_path)
    context["worker_id"] = "fundamental_analyst"

    with pytest.raises(ValueError, match="worker_id=market_analyst"):
        build_crypto_market_data_pack(
            _tool_input("BTC"),
            context,
            env={},
            fetch_json=_fake_binance_klines,
            chart_render=_fake_chart_render,
            bb_trade_context_fetch=_fake_bb_trade_context,
        )


def _tool_input(ticker: str) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "market": "CRYPTO",
        "company_name": ticker,
        "start_date": "2026-05-01",
        "end_date": "2026-05-15",
    }


def _runtime_context(tmp_path: Path) -> dict[str, str]:
    return {
        "run_id": "run-1",
        "stage": "frontline",
        "worker_id": "market_analyst",
        "call_id": "call-1",
        "tool_name": "crypto_market_data_pack",
        "evidence_root": str(tmp_path),
        "current_date": "2026-05-15",
    }


def _fake_binance_klines(
    url: str,
    *,
    params: Mapping[str, Any],
    headers: Mapping[str, str],
    timeout: int,
    method: str = "GET",
    json_body: Mapping[str, Any] | None = None,
) -> list[list[Any]]:
    _ = url, params, headers, timeout, method, json_body
    start = date(2025, 9, 1)
    rows: list[list[Any]] = []
    for index in range(260):
        day = start + timedelta(days=index)
        close = 50_000 + index * 100
        rows.append(
            [
                int(pd.Timestamp(day.isoformat(), tz="UTC").timestamp() * 1000),
                str(close - 50),
                str(close + 120),
                str(close - 180),
                str(close),
                str(1000 + index),
            ]
        )
    return rows


def _fake_bb_trade_context(
    url: str,
    *,
    params: Mapping[str, Any],
    headers: Mapping[str, str],
    timeout: int,
    method: str = "POST",
    json_body: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    _ = url, params, headers, timeout, method, json_body
    return {
        "status": "success",
        "summary": "BTC compact trade context ready",
        "as_of": "2026-05-15T12:00:00Z",
        "confidence": "high",
        "readiness": {
            "overall_score": 89,
            "overall_level": "ready",
            "domains": {
                "market": "ready",
                "technical": "partial",
                "derivatives": "ready",
                "liquidation_map": "ready",
                "onchain": "partial",
                "macro": "ready",
                "ahr999": "ready",
            },
        },
        "sources": [{"provider": "CoinGecko"}, {"provider": "CoinGlass"}],
        "data_gaps": ["4h AMD 多周期对齐不足"],
        "conflicts": [],
        "data": {
            "market": {
                "price": 78190.0,
                "change_24h_pct": -1.2,
                "volume_24h": 32_000_000_000,
                "market_cap": 1_540_000_000_000,
            },
            "technical": {
                "timeframes": {
                    "4h": {
                        "last_close": 78190.0,
                        "indicators": {
                            "rsi14": 8.4,
                            "macd_12_26_9": {"histogram": -186.0},
                            "bollinger": {"lower": 77330.0, "mid": 79510.0, "upper": 81690.0},
                            "td_sequential": {"countdown": 13, "direction": "buy"},
                            "kd": {"k": 12.0, "d": 18.0},
                        },
                        "vegas": {"state": "below_blue_band"},
                        "patterns": {
                            "fvg": {"support": "77588-78081", "resistance": "78528-78970"},
                            "order_block": {"bearish": "81020-81999", "bullish": "78753-80558"},
                            "volume_profile": {"poc": 78179.0, "value_area": "75769-81623"},
                            "amd": {"phase": "accumulation", "range": "77601-82460"},
                            "harmonic": {"candidates": []},
                        },
                    },
                    "1d": {
                        "last_close": 78190.0,
                        "indicators": {
                            "rsi14": 48.0,
                            "macd_12_26_9": {"histogram": -22.0},
                            "bollinger": {"mid": 79292.0},
                            "td_sequential": {"countdown": 13, "direction": "buy"},
                        },
                        "vegas": {"major": "bearish"},
                        "patterns": {"123": {"trigger": 82828.7, "invalid": 74868.0}},
                    },
                }
            },
            "derivatives": {
                "funding_rate": 0.00004955,
                "open_interest": 57_650_000_000,
                "long_short_ratio": 1.248,
                "cvd_proxy": {"value": -5639, "bias": "sell_pressure"},
            },
            "liquidation_map": {
                "clusters": [
                    {"price": 82300, "liquidation_value": 247_200_000, "side": "above"},
                    {"price": 77126, "liquidation_value": 79_000_000, "side": "below"},
                ]
            },
            "onchain": {
                "mvrv": 1.4576,
                "exchange_flows": {"net_btc": -662, "confidence": "low"},
            },
            "macro": {"risk_window": "medium", "series": {"DGS10": 4.47}},
            "ahr999": {"value": 0.4909, "dca_zone": True},
        },
    }


def _fake_bb_trade_context_with_liquidation_lists(
    url: str,
    *,
    params: Mapping[str, Any],
    headers: Mapping[str, str],
    timeout: int,
    method: str = "POST",
    json_body: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload = _fake_bb_trade_context(
        url,
        params=params,
        headers=headers,
        timeout=timeout,
        method=method,
        json_body=json_body,
    )
    payload["data"]["liquidation_map"] = {
        "current_price": 78236.7,
        "heatmap_model": "coinglass-v4-model1",
        "above_price_liquidity": [
            {"price": 82300.24, "liquidation_value": 247_200_218.34, "side": "above", "time_index": 214},
        ],
        "below_price_liquidity": [
            {"price": 77126.73, "liquidation_value": 78_987_580.81, "side": "below", "time_index": 255},
        ],
        "largest_clusters": [
            {"price": 82300.24, "liquidation_value": 247_200_218.34, "side": "above", "time_index": 214},
            {"price": 77126.73, "liquidation_value": 78_987_580.81, "side": "below", "time_index": 255},
        ],
    }
    return payload


def _fake_chart_render(chart_frame: pd.DataFrame, ticker: str, output_dir: Path) -> tuple[Path, Path]:
    assert {"date", "open", "high", "low", "close", "volume", "macd", "rsi14", "k"}.issubset(chart_frame.columns)
    output_dir.mkdir(parents=True, exist_ok=True)
    first = output_dir / f"{ticker}_market_structure.png"
    second = output_dir / f"{ticker}_indicator_panels.png"
    first.write_bytes(b"\x89PNG\r\n\x1a\ncrypto-market-structure")
    second.write_bytes(b"\x89PNG\r\n\x1a\ncrypto-indicator-panels")
    return first, second


def _assert_reader_brief_has_no_internal_crypto_field_names(text: str) -> None:
    forbidden = (
        "readiness=",
        "data_gap",
        "data_gaps",
        "provider payload",
        "provider_attempts",
        "BB compact context",
        "nearest_above",
        "nearest_below",
        "value_area_low",
        "volume_pct",
        "cumulative_delta",
        "latest_delta",
        "MACD_hist",
        "histogram",
        "funding_rate",
        "overall_level",
        "overall_score",
        "status=",
        "confidence=",
        "phase=",
        "direction=",
        "signal=",
        "open_count",
        "point_1",
        "point_2",
        "point_3",
        "lower=",
        "upper=",
        "mid=",
    )
    for token in forbidden:
        assert token not in text


def _empty_chart_render(chart_frame: pd.DataFrame, ticker: str, output_dir: Path) -> tuple[Path, ...]:
    _ = chart_frame, ticker, output_dir
    return ()


def _fail_if_called(*_args: Any, **_kwargs: Any) -> Any:
    raise AssertionError("provider fetch should not be called on fresh cache hit")


class _FakeCollection:
    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}
        self.inserted: list[dict[str, Any]] = []

    def find_one(self, query: Mapping[str, Any]) -> dict[str, Any] | None:
        if "_id" in query:
            return self.docs.get(str(query["_id"]))
        for doc in self.docs.values():
            if all(doc.get(key) == value for key, value in query.items()):
                return doc
        return None

    def update_one(self, query: Mapping[str, Any], update: Mapping[str, Any], *, upsert: bool = False) -> None:
        key = str(query["_id"])
        doc = self.docs.get(key)
        if doc is None:
            if not upsert:
                return
            doc = {"_id": key}
            doc.update(update.get("$setOnInsert", {}))
            self.docs[key] = doc
        doc.update(update.get("$set", {}))
        for field, increment in update.get("$inc", {}).items():
            doc[field] = int(doc.get(field, 0)) + int(increment)

    def insert_one(self, document: Mapping[str, Any]) -> None:
        self.inserted.append(dict(document))


class _DuplicateCheckingCollection(_FakeCollection):
    def __init__(self) -> None:
        super().__init__()
        self.duplicate_count = 0

    def insert_one(self, document: Mapping[str, Any]) -> None:
        key = str(document["_id"])
        if key in self.docs:
            self.duplicate_count += 1
            raise DuplicateKeyError("duplicate provider attempt")
        self.docs[key] = dict(document)
        self.inserted.append(dict(document))
