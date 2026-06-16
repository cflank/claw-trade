from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from claw_trade.data_gateway.models import GateDecision, Market
from claw_trade.data_gateway.execution.rate_limiter import RateLimitPolicy
from claw_trade.data_gateway import report_evidence
from claw_trade.data_gateway.planner import plan_public_data_requests
from claw_trade.data_gateway.public_api import PublicDataRequest, PublicRequestPriority
from claw_trade.reports import data_need_bridge


def test_fixed_market_bundle_runtime_entrypoint_is_deleted() -> None:
    assert not hasattr(data_need_bridge, "run_frontline_data_" + "pack")


def test_btc_funding_rate_need_uses_planner_not_fixed_market_bundle(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    class _PolicyResolver:
        def resolve(self, **_kwargs):  # type: ignore[no-untyped-def]
            return RateLimitPolicy(window_seconds=60, max_requests=10)

    class _FetchEngine:
        def fetch(self, batch):  # type: ignore[no-untyped-def]
            return SimpleNamespace(
                status="success",
                batch_id=batch.batch_id,
                provider_id=batch.provider_id,
                payload={
                    "rows": (
                        {
                            "dataset": "crypto_derivative_metric",
                            "symbol_id": "BTC/USDT",
                            "timestamp": datetime.now(tz=UTC),
                            "funding_rate": -0.001,
                        },
                    )
                },
            )

    class _OwnerExecutionGate:
        def enter(self, _batch):  # type: ignore[no-untyped-def]
            return GateDecision.owner("owner-token")

        def publish_shared_result(self, _single_flight_key, _owner_token, _ingest, *, batch=None):  # type: ignore[no-untyped-def]
            return True

        def wait_after_rate_limited_fetch(self, _batch, _fetch_result):  # type: ignore[no-untyped-def]
            return False

        def mark_cooldown_after_fetch(self, _batch, _fetch_result):  # type: ignore[no-untyped-def]
            return None

    class _Ingest:
        def ingest(self, _fetch_result, _batch):  # type: ignore[no-untyped-def]
            return SimpleNamespace(
                status="ingested",
                dataset_refs=("dataset:btc-funding",),
                raw_refs=("raw:btc-funding",),
                attempt_refs=("attempt:btc-funding",),
                gaps=(),
                remote_success=True,
            )

    monkeypatch.setattr(
        report_evidence,
        "build_data_gateway_runtime_from_env",
        lambda: SimpleNamespace(
            rate_limit_policy_resolver=_PolicyResolver(),
            data_service=SimpleNamespace(execution_gate=_OwnerExecutionGate()),
            fetch_engine=_FetchEngine(),
            ingest=_Ingest(),
        ),
    )

    payload = data_need_bridge.run_claw_request_data(
        {
            "item": "资金费率",
            "instrument": "BTC/USDT",
            "market": "CRYPTO",
            "time_range": {"lookback_days": 30},
            "granularity": "hourly",
            "purpose": "derivatives_crowding",
            "priority": "required",
        },
        {
            "run_id": "run-btc",
            "call_id": "call-need",
            "worker_id": "market_analyst",
            "tool_name": "claw_request_data",
            "current_time": datetime.now(tz=UTC).isoformat(),
            "current_date": "2026-06-12",
        },
    )

    assert payload["ok"] is True
    assert payload["schema_version"] == "data_need_result.v1"
    assert payload["request"]["item"] == "资金费率"
    assert "api_id" not in payload["request"]
    assert payload["planned_calls_count"] > 1
    assert payload["scheduled_calls_count"] < payload["planned_calls_count"]
    assert payload["planned_calls_count"] != 17
    assert set(payload["raw_refs"]) == {"raw:btc-funding"}


def test_btc_market_common_needs_are_individual_data_needs_not_fixed_17_pack() -> None:
    now = datetime(2026, 6, 12, 12, 0, tzinfo=UTC)
    common_items = (
        ("日线", "daily"),
        ("资金费率", "hourly"),
        ("OI", "hourly"),
        ("多空比", "hourly"),
        ("清算", "hourly"),
        ("清算地图", "hourly"),
        ("主动买卖量差", "hourly"),
        ("盘口", "hourly"),
    )
    requests = tuple(
        PublicDataRequest(
            request_id=f"btc-market:{idx}",
            item=item,
            market=Market.CRYPTO,
            instrument="BTC/USDT",
            time_range_start=now - timedelta(days=7),
            time_range_end=now,
            granularity=granularity,
            purpose="market_report",
            priority=PublicRequestPriority.REQUIRED,
            requested_by_worker="market_analyst",
            deadline_at=now + timedelta(seconds=120),
            consumer="report",
        )
        for idx, (item, granularity) in enumerate(common_items, start=1)
    )

    plan = plan_public_data_requests(requests)
    planned_need_ids = {need_id for call in plan.planned_calls for need_id in call.need_ids}

    assert plan.skipped_needs == ()
    assert len(plan.planned_calls) != 17
    assert planned_need_ids == {request.request_id for request in requests}
    assert any(call.catalog_endpoint_id == "coinglass.futures_funding_rate" for call in plan.planned_calls)
    assert any(call.catalog_endpoint_id == "coinglass.futures_open_interest_aggregated_history" for call in plan.planned_calls)
    assert any(call.catalog_endpoint_id == "coinglass.futures_liquidation_heatmap" for call in plan.planned_calls)
