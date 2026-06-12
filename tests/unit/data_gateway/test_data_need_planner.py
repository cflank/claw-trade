from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from claw_trade.data_gateway.models import GapReason, Market
from claw_trade.data_gateway.needs import DataNeed
from claw_trade.data_gateway.planner import build_provider_call_spec, plan_data_needs, resolve_need
from claw_trade.data_gateway.planner.call_planner import build_batch_key
from claw_trade.data_gateway.official_catalog import all_endpoints
from claw_trade.data_gateway.official_catalog.models import endpoint, no_batch


def _need(**overrides: object) -> DataNeed:
    base = {
        "need_id": "need-1",
        "need_kind": "funding_rate",
        "market": Market.CRYPTO,
        "instrument": "BTC/USDT",
        "time_range_start": date(2026, 6, 1),
        "time_range_end": date(2026, 6, 12),
        "granularity": "hourly",
        "requested_by_worker": "market_analyst",
        "purpose": "market_analysis",
        "deadline_at": datetime(2026, 6, 12, 12, 0, tzinfo=UTC),
        "consumer": "report",
    }
    return DataNeed.model_validate({**base, **overrides})


def test_resolver_maps_need_to_catalog_endpoint_candidates_without_winner() -> None:
    resolved = resolve_need(_need())

    assert resolved.gap is None
    assert [endpoint.endpoint_id for endpoint in resolved.candidate_endpoints] == ["coinglass.futures_funding_rate"]
    assert {endpoint.provider_id for endpoint in resolved.candidate_endpoints} == {"official_api_coinglass"}


def test_resolver_ignores_worker_consumer_and_purpose_scope_labels() -> None:
    variants = [
        _need(need_id="report", requested_by_worker="market_analyst", consumer="report", purpose="report"),
        _need(need_id="select", requested_by_worker="fundamental_analyst", consumer="select", purpose="selection"),
        _need(need_id="maintenance", requested_by_worker="news_analyst", consumer="maintenance", purpose="repair"),
    ]

    candidate_sets = [tuple(endpoint.endpoint_id for endpoint in resolve_need(need).candidate_endpoints) for need in variants]

    assert candidate_sets == [candidate_sets[0], candidate_sets[0], candidate_sets[0]]


def test_resolver_respects_explicit_empty_catalog() -> None:
    resolved = resolve_need(_need(), catalog_endpoints=())

    assert resolved.candidate_endpoints == ()
    assert resolved.gap is not None
    assert resolved.gap.reason == GapReason.RESOLVER_MAPPING_MISSING


def test_unmapped_need_returns_resolver_mapping_gap_not_provider_gap() -> None:
    plan = plan_data_needs((_need(need_kind="unmapped_semantic_need"),))

    assert plan.planned_calls == ()
    assert len(plan.skipped_needs) == 1
    assert plan.skipped_needs[0].reason == GapReason.RESOLVER_MAPPING_MISSING
    assert plan.skipped_needs[0].provider_ids_tried == ()


def test_call_planner_builds_specs_from_official_catalog_metadata() -> None:
    plan = plan_data_needs((_need(),))

    assert len(plan.planned_calls) == 1
    call = plan.planned_calls[0]
    assert call.provider_id == "official_api_coinglass"
    assert call.catalog_endpoint_id == "coinglass.futures_funding_rate"
    assert call.official_path_or_api_name == "/api/futures/funding-rate/oi-weight-history"
    assert call.auth_scope == "coinglass_header"
    assert call.params == {
        "symbol": "BTC",
        "interval": "1h",
        "start_time": 1780272000,
        "end_time": 1781222400,
    }
    assert call.need_ids == ("need-1",)
    assert not {"path", "api_name", "url", "header", "token"} & set(call.params)


def test_planned_calls_include_all_catalog_required_params() -> None:
    catalog_by_id = {endpoint.endpoint_id: endpoint for endpoint in all_endpoints()}

    plan = plan_data_needs(
        (
            _need(need_id="crypto-market", need_kind="market_price", market=Market.CRYPTO),
            _need(need_id="us-financial", need_kind="financial_metric", market=Market.US, instrument="AAPL"),
            _need(need_id="hk-financial", need_kind="financial_metric", market=Market.HK, instrument="0700.HK"),
        )
    )

    assert plan.skipped_needs == ()
    for call in plan.planned_calls:
        catalog_endpoint = catalog_by_id[call.catalog_endpoint_id]
        assert set(catalog_endpoint.required_params) <= set(call.params), call.catalog_endpoint_id


def test_planner_skips_endpoint_when_required_param_cannot_be_built() -> None:
    unsupported_endpoint = endpoint(
        provider_id="official_api_coinglass",
        source_type="coinglass",
        endpoint_id="coinglass.futures_funding_rate",
        official_path_or_api_name="/api/futures/funding-rate/oi-weight-history",
        method="GET",
        required_params=("unknown_required_param",),
        auth="coinglass_header",
        rate_limit_bucket="ratelimit:coinglass",
        batch_policy=no_batch(),
        official_doc_ref="https://docs.coinglass.com/reference",
    )

    plan = plan_data_needs((_need(),), catalog_endpoints=(unsupported_endpoint,))

    assert plan.planned_calls == ()
    assert len(plan.skipped_needs) == 1
    assert plan.skipped_needs[0].reason == GapReason.RESOLVER_MAPPING_MISSING


def test_only_planner_writes_official_path_not_worker_params() -> None:
    endpoint = next(endpoint for endpoint in all_endpoints() if endpoint.endpoint_id == "coinglass.futures_funding_rate")

    with pytest.raises(ValueError, match="worker/tool input cannot provide"):
        build_provider_call_spec(need=_need(), endpoint=endpoint, params={"symbol": "BTC", "path": "/caller/path"})


def test_batch_key_includes_endpoint_official_path_auth_and_param_shape() -> None:
    base = {
        "provider_id": "official_api_tushare",
        "auth_scope": "tushare_body_token",
        "params": {"ts_code": "600519.SH", "start_date": "20260601"},
    }

    daily = build_batch_key(
        **base,
        catalog_endpoint_id="tushare.daily",
        official_path_or_api_name="daily",
    )
    moneyflow = build_batch_key(
        **base,
        catalog_endpoint_id="tushare.moneyflow",
        official_path_or_api_name="moneyflow",
    )
    different_shape = build_batch_key(
        provider_id=base["provider_id"],
        auth_scope=base["auth_scope"],
        catalog_endpoint_id="tushare.daily",
        official_path_or_api_name="daily",
        params={"ts_code": "600519.SH"},
    )

    assert daily != moneyflow
    assert daily != different_shape
    assert "provider=official_api_tushare" in daily
    assert "endpoint=tushare.daily" in daily
    assert "official=daily" in daily
    assert "auth=tushare_body_token" in daily
    assert "params=start_date:str,ts_code:str" in daily


def test_identical_semantic_needs_merge_need_ids_on_each_candidate_call() -> None:
    first = _need(need_id="need-1", requested_by_worker="market_analyst", consumer="report")
    second = _need(need_id="need-2", requested_by_worker="news_analyst", consumer="maintenance")

    plan = plan_data_needs((first, second))

    assert len(plan.planned_calls) == 1
    assert plan.planned_calls[0].need_ids == ("need-1", "need-2")
    assert any(evidence.merged and evidence.reason == "same_semantic_need" for evidence in plan.merge_evidence)


def test_identical_semantic_need_call_id_does_not_depend_on_input_order() -> None:
    first = _need(need_id="need-1", requested_by_worker="market_analyst", consumer="report")
    second = _need(need_id="need-2", requested_by_worker="news_analyst", consumer="maintenance")

    forward = plan_data_needs((first, second))
    reverse = plan_data_needs((second, first))

    assert forward.planned_calls[0].call_id == reverse.planned_calls[0].call_id


def test_different_semantic_need_time_range_does_not_merge() -> None:
    first = _need(need_id="need-1", time_range_start=date(2026, 6, 1), time_range_end=date(2026, 6, 12))
    second = _need(need_id="need-2", time_range_start=date(2026, 5, 1), time_range_end=date(2026, 6, 12))

    plan = plan_data_needs((first, second))

    assert len(plan.planned_calls) == 2
    assert [call.need_ids for call in plan.planned_calls] == [("need-1",), ("need-2",)]
