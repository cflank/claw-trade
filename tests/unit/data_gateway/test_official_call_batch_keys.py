from __future__ import annotations

import importlib
import importlib.util
from datetime import UTC, datetime
from typing import Any


def test_different_tushare_api_names_do_not_share_official_raw_batch_key() -> None:
    daily = _batch_key(
        provider_id="official_api_tushare",
        catalog_endpoint_id="tushare.daily",
        official_path_or_api_name="daily",
    )
    moneyflow = _batch_key(
        provider_id="official_api_tushare",
        catalog_endpoint_id="tushare.moneyflow",
        official_path_or_api_name="moneyflow",
    )

    assert daily != moneyflow


def test_different_coinglass_paths_do_not_share_official_raw_batch_key() -> None:
    funding = _batch_key(
        provider_id="official_api_coinglass",
        catalog_endpoint_id="coinglass.futures_funding_rate_history",
        official_path_or_api_name="/api/futures/funding-rate/history",
    )
    open_interest = _batch_key(
        provider_id="official_api_coinglass",
        catalog_endpoint_id="coinglass.futures_open_interest_history",
        official_path_or_api_name="/api/futures/open-interest/history",
    )

    assert funding != open_interest


def _batch_key(*, provider_id: str, catalog_endpoint_id: str, official_path_or_api_name: str) -> str:
    try:
        spec = importlib.util.find_spec("claw_trade.data_gateway.planner.call_planner")
    except ModuleNotFoundError:
        spec = None
    assert spec is not None, "DataNeed call planner is missing: claw_trade.data_gateway.planner.call_planner"

    planner = importlib.import_module("claw_trade.data_gateway.planner.call_planner")
    if hasattr(planner, "build_batch_key"):
        return str(
            planner.build_batch_key(
                provider_id=provider_id,
                catalog_endpoint_id=catalog_endpoint_id,
                official_path_or_api_name=official_path_or_api_name,
                auth_scope="provider_token",
                params={"symbol": "BTCUSDT", "interval": "1h"},
            )
        )

    for name in ("call_spec", "build_call_spec", "build_provider_call_spec", "plan_call_spec"):
        builder = getattr(planner, name, None)
        if callable(builder):
            return _batch_key_from_spec(
                builder(
                    provider_id=provider_id,
                    catalog_endpoint_id=catalog_endpoint_id,
                    official_path_or_api_name=official_path_or_api_name,
                    auth_scope="provider_token",
                    params={"symbol": "BTCUSDT", "interval": "1h"},
                    deadline_at=datetime(2026, 6, 12, tzinfo=UTC),
                    need_ids=("need-1",),
                )
            )

    raise AssertionError("call planner must expose build_batch_key or a ProviderCallSpec builder with batch_key")


def _batch_key_from_spec(value: Any) -> str:
    batch_key = value.get("batch_key") if isinstance(value, dict) else getattr(value, "batch_key", None)
    assert batch_key, f"ProviderCallSpec must expose a non-empty batch_key, got {value!r}"
    return str(batch_key)
