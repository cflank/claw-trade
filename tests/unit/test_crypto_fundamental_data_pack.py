from __future__ import annotations

from pathlib import Path
import sys
from typing import Any, Mapping

import pytest
import requests


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_PYTHON_ROOT = REPO_ROOT / "openclaw_plugins" / "claw-trade-frontline-tools" / "python"
if str(SHARED_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(SHARED_PYTHON_ROOT))

from frontline_data_pack.crypto_fundamental_data_pack import build_crypto_fundamental_data_pack  # noqa: E402


def test_crypto_fundamental_pack_returns_coingecko_and_defillama_ready_pack(tmp_path: Path) -> None:
    pack = build_crypto_fundamental_data_pack(
        _tool_input("AAVE"),
        _runtime_context(tmp_path),
        env={"COINGECKO_DEMO_API_KEY": "demo-key", "DEFILLAMA_API_KEY": "llama-key"},
        fetch_json=_fake_fetch_success,
    )

    assert pack["ok"] is True
    assert pack["readiness"]["status"] == "ready"
    assert pack["data"]["coin_metadata"]["id"] == "aave"
    assert pack["data"]["defi_operating_metrics"]["tvl_usd"] == 12_000_000_000.0
    assert pack["data"]["valuation_context"]["fdv_to_tvl"] == 0.5
    assert {attempt["status"] for attempt in pack["provider_attempts"]} == {"success"}
    assert {attempt["auth_mode"] for attempt in pack["provider_attempts"]} == {"demo_key_header", "pro_path_key"}
    assert "CoinGecko" in pack["reader_brief"]
    assert "DefiLlama" in pack["reader_brief"]
    _assert_reader_brief_has_no_internal_fundamental_field_names(pack["reader_brief"])
    assert (tmp_path / "crypto_fundamental_data_pack.json").is_file()
    assert (tmp_path / "provider_raw" / "crypto_fundamental_data_pack" / "raw_payload.json").is_file()


def test_crypto_fundamental_pack_reuses_provider_cache_without_refetching(tmp_path: Path) -> None:
    cache = _FakeCollection()
    env = {"COINGECKO_DEMO_API_KEY": "demo-key", "DEFILLAMA_API_KEY": "llama-key"}

    first = build_crypto_fundamental_data_pack(
        _tool_input("AAVE"),
        _runtime_context(tmp_path / "first"),
        env=env,
        fetch_json=_fake_fetch_success,
        provider_cache_collection=cache,
    )
    assert first["ok"] is True

    second = build_crypto_fundamental_data_pack(
        _tool_input("AAVE"),
        _runtime_context(tmp_path / "second"),
        env=env,
        fetch_json=lambda *_args, **_kwargs: pytest.fail("provider must not be called on fresh cache hit"),
        provider_cache_collection=cache,
    )

    assert second["ok"] is True
    assert {attempt["status"] for attempt in second["provider_attempts"]} == {"cache_hit"}
    assert {attempt["source_role"] for attempt in second["provider_attempts"]}.issuperset(
        {"fundamental_reference", "defi_operating_metrics", "defi_fees_revenue"}
    )


def test_crypto_fundamental_pack_marks_non_defi_asset_partial_without_fake_metrics(tmp_path: Path) -> None:
    pack = build_crypto_fundamental_data_pack(
        _tool_input("BTC"),
        _runtime_context(tmp_path),
        env={"COINGECKO_DEMO_API_KEY": "demo-key"},
        fetch_json=_fake_fetch_btc_without_defillama_protocol,
    )

    assert pack["ok"] is True
    assert pack["readiness"]["status"] == "partial"
    assert "defi_operating_metrics" not in pack["data"]
    assert any("DefiLlama 未匹配" in gap for gap in pack["data_gaps"])
    assert any(
        attempt["provider"] == "DefiLlama" and attempt["endpoint"] == "/protocols" and attempt["status"] == "empty"
        for attempt in pack["provider_attempts"]
    )


def test_crypto_fundamental_pack_returns_insufficient_when_providers_fail(tmp_path: Path) -> None:
    def failing_fetch(*_args: Any, **_kwargs: Any) -> Any:
        raise requests.Timeout("forced timeout")

    pack = build_crypto_fundamental_data_pack(
        _tool_input("AAVE"),
        _runtime_context(tmp_path),
        env={"COINGECKO_DEMO_API_KEY": "demo-key"},
        fetch_json=failing_fetch,
    )

    assert pack["ok"] is False
    assert pack["readiness"]["status"] == "insufficient"
    assert {attempt["status"] for attempt in pack["provider_attempts"]} == {"error"}
    assert {attempt["error_code"] for attempt in pack["provider_attempts"]} == {"timeout"}
    assert "资料包已返回" in pack["reader_brief"]
    _assert_reader_brief_has_no_internal_fundamental_field_names(pack["reader_brief"])


def test_crypto_fundamental_pack_marks_missing_coingecko_key_without_public_fallback(tmp_path: Path) -> None:
    pack = build_crypto_fundamental_data_pack(
        _tool_input("BTC"),
        _runtime_context(tmp_path),
        env={},
        fetch_json=_fake_fetch_btc_without_defillama_protocol,
    )

    assert pack["ok"] is False
    assert pack["readiness"]["status"] == "insufficient"
    coingecko_attempt = pack["provider_attempts"][0]
    assert coingecko_attempt["provider"] == "CoinGecko"
    assert coingecko_attempt["status"] == "config_blocked"
    assert coingecko_attempt["auth_mode"] == "credential_missing"
    assert coingecko_attempt["error_code"] == "COINGECKO_API_KEY_MISSING"


def test_crypto_fundamental_pack_prefers_runtime_dates_over_tool_dates(tmp_path: Path) -> None:
    context = _runtime_context(tmp_path)
    context["start_date"] = "2026-04-01"
    context["end_date"] = "2026-05-16"
    tool_input = _tool_input("AAVE")
    tool_input["start_date"] = "2025-01-01"
    tool_input["end_date"] = "2025-12-31"

    pack = build_crypto_fundamental_data_pack(
        tool_input,
        context,
        env={"COINGECKO_DEMO_API_KEY": "demo-key", "DEFILLAMA_API_KEY": "llama-key"},
        fetch_json=_fake_fetch_success,
    )

    assert pack["input"]["start_date"] == "2026-04-01"
    assert pack["input"]["end_date"] == "2026-05-16"


def test_crypto_fundamental_pack_rejects_wrong_worker_context(tmp_path: Path) -> None:
    context = _runtime_context(tmp_path)
    context["worker_id"] = "market_analyst"

    with pytest.raises(ValueError, match="worker_id=fundamental_analyst"):
        build_crypto_fundamental_data_pack(
            _tool_input("AAVE"),
            context,
            env={},
            fetch_json=_fake_fetch_success,
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
        "worker_id": "fundamental_analyst",
        "call_id": "call-1",
        "tool_name": "crypto_fundamental_data_pack",
        "evidence_root": str(tmp_path),
        "current_date": "2026-05-15",
    }


def _fake_fetch_success(
    url: str,
    *,
    params: Mapping[str, Any],
    headers: Mapping[str, str],
    timeout: int,
    method: str = "GET",
    json_body: Mapping[str, Any] | None = None,
) -> Any:
    _ = params, headers, timeout, method, json_body
    if url.endswith("/search"):
        return {"coins": [{"id": "aave", "symbol": "aave", "name": "Aave", "market_cap_rank": 50}]}
    if "/coins/aave" in url:
        return _coingecko_detail("aave", "aave", "Aave", market_cap=4_000_000_000, fdv=6_000_000_000)
    if url.endswith("/api/protocols"):
        return [{"slug": "aave", "symbol": "AAVE", "name": "Aave", "tvl": 12_000_000_000, "gecko_id": "aave"}]
    if url.endswith("/api/protocol/aave"):
        return {
            "slug": "aave",
            "symbol": "AAVE",
            "name": "Aave",
            "category": "Lending",
            "chains": ["Ethereum", "Base"],
            "tvl": [{"date": 1_715_731_200, "totalLiquidityUSD": 12_000_000_000}],
            "currentChainTvls": {"Ethereum": 9_000_000_000, "Base": 3_000_000_000},
        }
    if url.endswith("/api/summary/fees/aave"):
        return {"total24h": 1_000_000, "total30d": 20_000_000}
    raise AssertionError(url)


def _fake_fetch_btc_without_defillama_protocol(
    url: str,
    *,
    params: Mapping[str, Any],
    headers: Mapping[str, str],
    timeout: int,
    method: str = "GET",
    json_body: Mapping[str, Any] | None = None,
) -> Any:
    _ = params, headers, timeout, method, json_body
    if url.endswith("/search"):
        return {"coins": [{"id": "bitcoin", "symbol": "btc", "name": "Bitcoin", "market_cap_rank": 1}]}
    if "/coins/bitcoin" in url:
        return _coingecko_detail("bitcoin", "btc", "Bitcoin", market_cap=2_000_000_000_000, fdv=2_100_000_000_000)
    if url.endswith("/protocols"):
        return []
    raise AssertionError(url)


def _coingecko_detail(coin_id: str, symbol: str, name: str, *, market_cap: int, fdv: int) -> dict[str, Any]:
    return {
        "id": coin_id,
        "symbol": symbol,
        "name": name,
        "web_slug": coin_id,
        "categories": ["DeFi", "Lending"] if coin_id == "aave" else ["Layer 1 (L1)"],
        "description": {"en": "<p>Protocol description.</p>"},
        "links": {"homepage": [f"https://{coin_id}.example"], "whitepaper": f"https://{coin_id}.example/whitepaper.pdf"},
        "market_data": {
            "current_price": {"usd": 100},
            "market_cap": {"usd": market_cap},
            "fully_diluted_valuation": {"usd": fdv},
            "total_volume": {"usd": 100_000_000},
            "circulating_supply": 40_000_000,
            "total_supply": 60_000_000,
            "max_supply": 100_000_000,
            "ath": {"usd": 600},
            "atl": {"usd": 1},
        },
        "last_updated": "2026-05-15T12:00:00Z",
    }


def _assert_reader_brief_has_no_internal_fundamental_field_names(text: str) -> None:
    forbidden = (
        "readiness=",
        "data_gap",
        "data_gaps",
        "provider_attempts",
        "crypto_fundamental_data_pack",
        "partial",
        "insufficient",
        "ready",
        "provider ",
        "provider口径",
        "fees/revenue",
        "价格USD=",
        "市值USD=",
        "流通量=",
        "总供应量=",
        "类别=",
        "TVL USD=",
        "24h=",
        "30d=",
    )
    for token in forbidden:
        assert token not in text


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

    def insert_one(self, document: Mapping[str, Any]) -> None:
        self.inserted.append(dict(document))
