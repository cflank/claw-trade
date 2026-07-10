from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from claw_trade.data_gateway.models import Market
from claw_trade.data_gateway.needs import DataNeed, NeedPriority, ProviderCallSpec
from claw_trade.data_gateway.official_catalog import all_endpoints
from claw_trade.data_gateway.planner import build_provider_call_spec, plan_public_data_requests
from claw_trade.data_gateway.public_api import (
    PublicDataRequest,
    PublicRequestPriority,
    public_api_contracts,
)


def _request(**overrides: object) -> PublicDataRequest:
    base = {
        "request_id": "request-1",
        "item": "日线",
        "market": Market.CN_A,
        "instrument": "600519.SH",
        "time_range_start": date(2026, 6, 1),
        "time_range_end": date(2026, 6, 12),
        "granularity": "daily",
        "priority": PublicRequestPriority.NORMAL,
        "requested_by_worker": "market_analyst",
        "purpose": "market_analysis",
        "deadline_at": datetime(2026, 6, 12, 12, 0, tzinfo=UTC),
        "consumer": "report",
    }
    return PublicDataRequest.model_validate({**base, **overrides})


def _need(**overrides: object) -> DataNeed:
    base = {
        "need_id": "need-1",
        "api_id": "cn_a.daily_bar",
        "market": Market.CN_A,
        "instrument": "600519.SH",
        "time_range_start": date(2026, 6, 1),
        "time_range_end": date(2026, 6, 12),
        "granularity": "daily",
        "priority": NeedPriority.NORMAL,
        "requested_by_worker": "market_analyst",
        "purpose": "market_analysis",
        "deadline_at": datetime(2026, 6, 12, 12, 0, tzinfo=UTC),
        "consumer": "report",
    }
    return DataNeed.model_validate({**base, **overrides})


def _instrument_for_market(market: Market) -> str:
    return {
        Market.CN_A: "600519.SH",
        Market.CRYPTO: "BTC",
        Market.US: "AAPL",
        Market.HK: "00700.HK",
    }[market]


def _binance_daily_bar_call(plan: object) -> ProviderCallSpec:
    return next(
        call
        for call in plan.planned_calls  # type: ignore[attr-defined]
        if call.provider_id == "crypto_primary" and call.catalog_endpoint_id == "binance.spot_daily_bar"
    )


def test_public_planner_derives_internal_need_and_catalog_calls_from_business_item() -> None:
    request = _request(item="日线")

    plan = plan_public_data_requests((request,))

    assert plan.skipped_needs == ()
    assert plan.needs[0].api_id == "cn_a.daily_bar"
    assert plan.needs[0].need_id == request.request_id
    assert {call.public_api_id for call in plan.planned_calls} == {"cn_a.daily_bar"}
    assert "tushare.daily" in {call.catalog_endpoint_id for call in plan.planned_calls}
    assert all("provider" not in call.params for call in plan.planned_calls)
    assert all("fields" not in call.params for call in plan.planned_calls)


def test_all_project_public_data_items_plan_to_at_least_one_provider_api() -> None:
    missing: list[str] = []
    for contract in public_api_contracts():
        request = _request(
            request_id=f"request-{contract.api_id}",
            item=contract.api_id.split(".", 1)[1],
            market=contract.market,
            instrument=_instrument_for_market(contract.market),
            granularity=contract.granularities[0],
            purpose="contract_test",
        )

        plan = plan_public_data_requests((request,))

        if plan.skipped_needs or not plan.planned_calls:
            reasons = "; ".join(str(gap.human_readable) for gap in plan.skipped_needs)
            missing.append(f"{contract.api_id}: {reasons or 'no planned calls'}")

    assert missing == []


def test_all_project_public_data_items_keep_provider_groups_contiguous() -> None:
    non_contiguous: list[str] = []
    for contract in public_api_contracts():
        request = _request(
            request_id=f"request-{contract.api_id}",
            item=contract.api_id.split(".", 1)[1],
            market=contract.market,
            instrument=_instrument_for_market(contract.market),
            granularity=contract.granularities[0],
            purpose="contract_test",
        )

        plan = plan_public_data_requests((request,))
        providers = [call.provider_id for call in plan.planned_calls]
        seen: list[str] = []
        reappeared: list[str] = []
        for provider in providers:
            if seen and provider != seen[-1] and provider in seen:
                reappeared.append(provider)
            seen.append(provider)
        if reappeared:
            non_contiguous.append(f"{contract.api_id}: {providers}")

    assert non_contiguous == []


def test_crypto_items_supported_by_public_sources_keep_free_provider_fallbacks() -> None:
    expected = {
        "crypto.valuation_metric": "crypto_coingecko_market",
        "crypto.company_profile": "crypto_coingecko_market",
        "crypto.funding_rate": "crypto_binance_spot_market",
        "crypto.open_interest": "crypto_binance_spot_market",
        "crypto.long_short_ratio": "crypto_binance_spot_market",
        "crypto.taker_buy_sell": "crypto_binance_spot_market",
        "crypto.options_open_interest": "crypto_binance_spot_market",
        "crypto.options_volume": "crypto_binance_spot_market",
    }
    contracts = {contract.api_id: contract for contract in public_api_contracts()}

    for api_id, provider_id in expected.items():
        contract = contracts[api_id]
        request = _request(
            request_id=f"request-{api_id}",
            item=api_id.split(".", 1)[1],
            market=Market.CRYPTO,
            instrument="BTC",
            granularity=contract.granularities[0],
            purpose="contract_test",
        )

        plan = plan_public_data_requests((request,))

        assert provider_id in {call.provider_id for call in plan.planned_calls}, api_id


def test_cn_a_order_book_plans_paid_and_free_order_book_interfaces() -> None:
    request = _request(request_id="cn-a-order-book", item="盘口", market=Market.CN_A, granularity="realtime")

    plan = plan_public_data_requests((request,))

    assert plan.skipped_needs == ()
    calls = {(call.provider_id, call.catalog_endpoint_id) for call in plan.planned_calls}
    assert ("cn_a_tushare_realtime", "tushare_sdk.realtime_order_book") in calls
    assert ("cn_a_mootdx_market", "mootdx.order_book_snapshot") in calls


def test_cn_a_official_announcement_alias_plans_to_official_filing_interfaces() -> None:
    request = _request(
        request_id="cn-a-official-announcement",
        item="官方公告",
        market=Market.CN_A,
        granularity="event",
        purpose="policy_report",
    )

    plan = plan_public_data_requests((request,))

    assert plan.skipped_needs == ()
    calls = {(call.provider_id, call.catalog_endpoint_id, call.public_api_id) for call in plan.planned_calls}
    assert ("official_api_tushare", "tushare.anns_d", "cn_a.official_filing") in calls
    assert ("cn_a_cninfo_events", "cninfo.official_filing", "cn_a.official_filing") in calls


def test_cn_a_news_candidates_keep_priority_source_group_before_public_fallback() -> None:
    request = _request(
        request_id="cn-a-company-news",
        item="公司新闻",
        market=Market.CN_A,
        granularity="event",
        purpose="news_report",
    )

    plan = plan_public_data_requests((request,))

    assert plan.skipped_needs == ()
    providers = [call.provider_id for call in plan.planned_calls]
    first_public_fallback = next(index for index, provider in enumerate(providers) if provider != "official_api_tushare")
    assert first_public_fallback > 0
    assert all(provider == "official_api_tushare" for provider in providers[:first_public_fallback])
    assert "official_api_tushare" not in providers[first_public_fallback:]


def test_cn_a_macro_news_does_not_plan_proxy_unsupported_tushare_monetary_policy() -> None:
    request = _request(
        request_id="cn-a-macro-news",
        item="宏观",
        market=Market.CN_A,
        granularity="event",
        purpose="policy_report",
    )

    plan = plan_public_data_requests((request,))

    endpoint_ids = {call.catalog_endpoint_id for call in plan.planned_calls}
    assert "tushare.raw_monetary_policy" not in endpoint_ids
    assert {"tushare.raw_cctv_news", "tushare.raw_major_news", "tushare.raw_news", "tushare.raw_npr"} <= endpoint_ids


def test_crypto_plain_macro_plans_structured_fred_series() -> None:
    request = _request(
        request_id="crypto-macro",
        item="宏观",
        market=Market.CRYPTO,
        instrument="BTC/USDT",
        granularity="event",
        purpose="market_report",
    )

    plan = plan_public_data_requests((request,))

    assert plan.skipped_needs == ()
    assert plan.needs[0].api_id == "crypto.macro_series"
    fred_calls = [call for call in plan.planned_calls if call.provider_id == "official_api_fred"]
    assert {call.catalog_endpoint_id for call in fred_calls} >= {
        "fred.series_fedfunds",
        "fred.series_dgs10",
        "fred.series_walcl",
        "fred.series_m2sl",
        "fred.series_cpiaucsl",
    }
    assert {call.params.get("series_id") for call in fred_calls} >= {
        "FEDFUNDS",
        "DGS10",
        "WALCL",
        "M2SL",
        "CPIAUCSL",
    }
    assert {getattr(call.execution_group_kind, "value", call.execution_group_kind) for call in fred_calls} == {"composition_group"}


def test_cn_a_social_candidates_keep_priority_source_group_before_public_fallback() -> None:
    request = _request(
        request_id="cn-a-social",
        item="社交情绪",
        market=Market.CN_A,
        granularity="event",
        purpose="social_report",
    )

    plan = plan_public_data_requests((request,))

    assert plan.skipped_needs == ()
    providers = [call.provider_id for call in plan.planned_calls]
    first_public_fallback = next(index for index, provider in enumerate(providers) if provider != "official_api_tushare")
    assert first_public_fallback > 0
    assert all(provider == "official_api_tushare" for provider in providers[:first_public_fallback])
    assert "official_api_tushare" not in providers[first_public_fallback:]


def test_crypto_market_sentiment_alias_maps_to_social_signal() -> None:
    request = _request(
        request_id="crypto-market-sentiment",
        item="市场情绪指标",
        market=Market.CRYPTO,
        instrument="BTC/USDT",
        granularity="event",
        purpose="social_report",
    )

    plan = plan_public_data_requests((request,))

    assert plan.skipped_needs == ()
    assert plan.needs[0].api_id == "crypto.social_signal"
    assert plan.planned_calls


def test_cn_a_lockup_event_plans_paid_and_public_unlock_sources() -> None:
    request = _request(
        request_id="cn-a-lockup",
        item="解禁",
        market=Market.CN_A,
        granularity="event",
        purpose="lockup_report",
    )

    plan = plan_public_data_requests((request,))

    assert plan.skipped_needs == ()
    calls = {(call.provider_id, call.catalog_endpoint_id, call.public_api_id) for call in plan.planned_calls}
    assert ("official_api_tushare", "tushare.share_float", "cn_a.lockup_event") in calls
    assert ("cn_a_eastmoney_events", "eastmoney.np_weblist.lockup_event", "cn_a.lockup_event") in calls


def test_cn_a_northbound_flow_plans_to_ths_hsgt_api() -> None:
    request = _request(
        request_id="cn-a-northbound-flow",
        item="北向资金",
        market=Market.CN_A,
        granularity="realtime",
        purpose="hot_money_report",
    )

    plan = plan_public_data_requests((request,))

    assert plan.skipped_needs == ()
    calls = {(call.provider_id, call.catalog_endpoint_id, call.public_api_id) for call in plan.planned_calls}
    assert ("cn_a_astock_signal_social", "ths.northbound_flow", "cn_a.northbound_flow") in calls
    assert all("provider" not in call.params for call in plan.planned_calls)
    assert all("api_name" not in call.params for call in plan.planned_calls)


def test_cn_a_margin_trading_plans_to_eastmoney_margin_detail() -> None:
    request = _request(
        request_id="cn-a-margin-trading",
        item="融资融券",
        market=Market.CN_A,
        granularity="daily",
        purpose="lockup_report",
    )

    plan = plan_public_data_requests((request,))

    assert plan.skipped_needs == ()
    calls = {(call.provider_id, call.catalog_endpoint_id, call.public_api_id) for call in plan.planned_calls}
    assert ("cn_a_eastmoney_market_data", "eastmoney.datacenter.margin_trading_detail", "cn_a.margin_trading") in calls
    assert all("provider" not in call.params for call in plan.planned_calls)
    assert all("api_name" not in call.params for call in plan.planned_calls)


def test_cn_a_sector_flow_plans_only_to_sector_fund_flow_interfaces() -> None:
    request = _request(
        request_id="cn-a-sector-flow",
        item="板块资金",
        market=Market.CN_A,
        granularity="event",
        purpose="hot_money_report",
    )

    plan = plan_public_data_requests((request,))

    assert plan.skipped_needs == ()
    calls = {(call.provider_id, call.catalog_endpoint_id, call.public_api_id) for call in plan.planned_calls}
    assert ("official_api_tushare", "tushare.moneyflow_ind_dc", "cn_a.sector_flow") in calls
    assert ("cn_a_akshare_social_news", "akshare.stock_sector_fund_flow_rank", "cn_a.sector_flow") in calls
    assert ("cn_a_eastmoney_market_data", "eastmoney.datacenter.sector_fund_flow_rank", "cn_a.sector_flow") in calls
    assert ("cn_a_baostock_market", "baostock.stock_industry", "cn_a.sector_flow") not in calls
    ordered = [(call.provider_id, call.catalog_endpoint_id) for call in plan.planned_calls]
    assert ordered[:3] == [
        ("official_api_tushare", "tushare.moneyflow_ind_dc"),
        ("official_api_tushare", "tushare.moneyflow_ind_ths"),
        ("official_api_tushare", "tushare.moneyflow_cnt_ths"),
    ]
    params_by_endpoint = {call.catalog_endpoint_id: call.params for call in plan.planned_calls}
    assert params_by_endpoint["tushare.moneyflow_ind_dc"] == {"trade_date": "20260612", "content_type": "行业"}
    assert params_by_endpoint["tushare.moneyflow_ind_ths"] == {"trade_date": "20260612"}
    assert params_by_endpoint["tushare.moneyflow_cnt_ths"] == {"trade_date": "20260612"}


def test_cn_a_sector_flow_uses_latest_completed_trading_day_for_intraday_request() -> None:
    request = _request(
        request_id="cn-a-sector-flow-intraday",
        item="板块资金",
        market=Market.CN_A,
        time_range_start=date(2025, 6, 15),
        time_range_end=date(2026, 6, 15),
        deadline_at=datetime(2026, 6, 15, 2, 0, tzinfo=UTC),
        granularity="event",
        purpose="hot_money_report",
    )

    plan = plan_public_data_requests((request,))

    params_by_endpoint = {call.catalog_endpoint_id: call.params for call in plan.planned_calls}
    assert params_by_endpoint["tushare.moneyflow_ind_dc"] == {"trade_date": "20260612", "content_type": "行业"}
    assert params_by_endpoint["tushare.moneyflow_ind_ths"] == {"trade_date": "20260612"}
    assert params_by_endpoint["tushare.moneyflow_cnt_ths"] == {"trade_date": "20260612"}


def test_cn_a_trade_date_only_tushare_call_uses_previous_day_at_1540_bjt() -> None:
    request = _request(
        request_id="cn-a-sector-flow-1540-bjt",
        item="板块资金",
        market=Market.CN_A,
        time_range_start=date(2026, 7, 1),
        time_range_end=date(2026, 7, 10),
        deadline_at=datetime(2026, 7, 10, 7, 40, tzinfo=UTC),
        granularity="event",
        purpose="hot_money_report",
    )

    plan = plan_public_data_requests((request,))

    params_by_endpoint = {call.catalog_endpoint_id: call.params for call in plan.planned_calls}
    assert params_by_endpoint["tushare.moneyflow_ind_ths"] == {"trade_date": "20260709"}


def test_cn_a_single_day_trade_date_only_tushare_call_uses_previous_day_at_1540_bjt() -> None:
    request = _request(
        request_id="cn-a-sector-flow-single-day-1540-bjt",
        item="板块资金",
        market=Market.CN_A,
        time_range_start=date(2026, 7, 10),
        time_range_end=date(2026, 7, 10),
        deadline_at=datetime(2026, 7, 10, 7, 40, tzinfo=UTC),
        granularity="event",
        purpose="hot_money_report",
    )

    plan = plan_public_data_requests((request,))

    params_by_endpoint = {call.catalog_endpoint_id: call.params for call in plan.planned_calls}
    assert params_by_endpoint["tushare.moneyflow_ind_ths"] == {"trade_date": "20260709"}


def test_cn_a_trade_date_only_tushare_call_uses_today_at_1600_bjt() -> None:
    request = _request(
        request_id="cn-a-sector-flow-1600-bjt",
        item="板块资金",
        market=Market.CN_A,
        time_range_start=date(2026, 7, 1),
        time_range_end=date(2026, 7, 10),
        deadline_at=datetime(2026, 7, 10, 8, 0, tzinfo=UTC),
        granularity="event",
        purpose="hot_money_report",
    )

    plan = plan_public_data_requests((request,))

    params_by_endpoint = {call.catalog_endpoint_id: call.params for call in plan.planned_calls}
    assert params_by_endpoint["tushare.moneyflow_ind_ths"] == {"trade_date": "20260710"}


def test_cn_a_annual_financial_metric_uses_report_period_interfaces() -> None:
    request = _request(
        request_id="cn-a-financial-metric-annual",
        item="财务指标",
        market=Market.CN_A,
        granularity="year",
        time_range_start=None,
        time_range_end=None,
        purpose="fundamental_report",
    )

    plan = plan_public_data_requests((request,))

    assert plan.skipped_needs == ()
    calls = {(call.provider_id, call.catalog_endpoint_id, call.public_api_id) for call in plan.planned_calls}
    assert ("official_api_tushare", "tushare.fina_indicator", "cn_a.financial_metric") in calls
    assert ("cn_a_eastmoney_market_data", "eastmoney.securities.financial_analysis_indicator", "cn_a.financial_metric") in calls


def test_cn_a_quarter_alias_financial_metric_uses_report_period_interfaces() -> None:
    request = _request(
        request_id="cn-a-financial-metric-quarter",
        item="财务指标",
        market=Market.CN_A,
        granularity="quarter",
        time_range_start=None,
        time_range_end=None,
        purpose="fundamental_report",
    )

    plan = plan_public_data_requests((request,))

    assert plan.skipped_needs == ()
    calls = {(call.provider_id, call.catalog_endpoint_id, call.public_api_id) for call in plan.planned_calls}
    assert ("official_api_tushare", "tushare.fina_indicator", "cn_a.financial_metric") in calls
    assert ("cn_a_eastmoney_market_data", "eastmoney.securities.financial_analysis_indicator", "cn_a.financial_metric") in calls


@pytest.mark.parametrize(
    ("item", "expected_provider", "expected_endpoint"),
    [
        ("借贷利率", "official_api_coinglass", "coinglass.raw_borrow_interest_rate_history"),
        ("宏观", "official_api_fred", "fred.series_fedfunds"),
        ("事件日历", "official_api_coinglass", "coinglass.raw_calendar_economic_data"),
    ],
)
def test_crypto_project_items_plan_to_paid_source_first(item: str, expected_provider: str, expected_endpoint: str) -> None:
    request = _request(
        request_id=f"crypto-{item}",
        item=item,
        market=Market.CRYPTO,
        instrument="BTC",
        granularity="realtime" if item == "借贷利率" else "event",
        purpose="market_report",
    )

    plan = plan_public_data_requests((request,))

    assert plan.skipped_needs == ()
    assert plan.planned_calls[0].provider_id == expected_provider
    assert plan.planned_calls[0].catalog_endpoint_id == expected_endpoint
    assert plan.execution_groups
    assert plan.initial_scheduled_calls
    assert plan.initial_scheduled_calls[0].call_id == plan.planned_calls[0].call_id
    assert all(call.call_id not in {item.call_id for item in plan.initial_scheduled_calls} for call in plan.deferred_calls)


def test_crypto_daily_bar_plans_to_real_price_history_interfaces() -> None:
    request = _request(
        request_id="crypto-daily",
        item="日线",
        market=Market.CRYPTO,
        instrument="BTC",
        granularity="daily",
        purpose="market_report",
    )

    plan = plan_public_data_requests((request,))

    assert plan.skipped_needs == ()
    calls = {(call.provider_id, call.catalog_endpoint_id, call.public_api_id) for call in plan.planned_calls}
    assert ("crypto_primary", "binance.spot_daily_bar", "crypto.daily_bar") in calls
    assert ("official_api_coinglass", "coinglass.futures_price_history", "crypto.daily_bar") in calls
    assert ("official_api_coinglass", "coinglass.spot_price_history", "crypto.daily_bar") in calls


def test_crypto_binance_daily_bar_batch_key_includes_date_window() -> None:
    first = _request(
        request_id="crypto-daily-1",
        item="日线",
        market=Market.CRYPTO,
        instrument="BTCUSDT",
        time_range_start=date(2026, 6, 26),
        time_range_end=date(2026, 6, 26),
        granularity="daily",
        purpose="scheduled_data_maintenance",
    )
    second = _request(
        request_id="crypto-daily-2",
        item="日线",
        market=Market.CRYPTO,
        instrument="BTCUSDT",
        time_range_start=date(2026, 6, 27),
        time_range_end=date(2026, 6, 27),
        granularity="daily",
        purpose="scheduled_data_maintenance",
    )

    first_call = _binance_daily_bar_call(plan_public_data_requests((first,)))
    second_call = _binance_daily_bar_call(plan_public_data_requests((second,)))

    assert "startTime" in first_call.params
    assert "endTime" in first_call.params
    assert first_call.params["startTime"] != second_call.params["startTime"]
    assert first_call.params["endTime"] != second_call.params["endTime"]
    assert first_call.batch_key != second_call.batch_key


def test_crypto_intraday_coinglass_price_history_uses_supported_hourly_interval() -> None:
    request = _request(
        request_id="crypto-intraday",
        item="分时",
        market=Market.CRYPTO,
        instrument="BTC",
        granularity="intraday",
        purpose="market_report",
    )

    plan = plan_public_data_requests((request,))

    coinglass_calls = [call for call in plan.planned_calls if call.catalog_endpoint_id in {"coinglass.futures_price_history", "coinglass.spot_price_history"}]
    assert coinglass_calls
    assert {call.params["interval"] for call in coinglass_calls} == {"1h"}


def test_crypto_options_coinglass_interfaces_use_official_unit_and_range_params() -> None:
    oi_request = _request(
        request_id="crypto-options-oi",
        item="期权持仓",
        market=Market.CRYPTO,
        instrument="BTC",
        granularity="realtime",
        purpose="market_report",
    )
    volume_request = _request(
        request_id="crypto-options-volume",
        item="期权成交",
        market=Market.CRYPTO,
        instrument="BTC",
        granularity="realtime",
        purpose="market_report",
    )

    plan = plan_public_data_requests((oi_request, volume_request))

    oi = next(call for call in plan.planned_calls if call.catalog_endpoint_id == "coinglass.options_open_interest")
    volume = next(call for call in plan.planned_calls if call.catalog_endpoint_id == "coinglass.options_volume")
    assert oi.params["unit"] == "USD"
    assert oi.params["range"] == "all"
    assert volume.params["unit"] == "USD"
    assert "range" not in volume.params


def test_crypto_order_book_does_not_use_coinglass_history_depth_as_realtime_book() -> None:
    request = _request(
        request_id="crypto-order-book",
        item="盘口",
        market=Market.CRYPTO,
        instrument="BTC",
        granularity="realtime",
        purpose="market_report",
    )

    plan = plan_public_data_requests((request,))

    assert not any(call.provider_id == "official_api_coinglass" for call in plan.planned_calls)


@pytest.mark.parametrize(
    ("item", "api_id"),
    [
        ("资金费率", "crypto.funding_rate"),
        ("OI", "crypto.open_interest"),
    ],
)
def test_crypto_derivative_planner_does_not_use_pairs_market_snapshot_for_specific_metrics(
    item: str,
    api_id: str,
) -> None:
    request = _request(
        request_id=f"crypto-{api_id}",
        item=item,
        market=Market.CRYPTO,
        instrument="BTC",
        granularity="realtime",
        purpose="market_report",
    )

    plan = plan_public_data_requests((request,))

    assert plan.skipped_needs == ()
    assert {call.public_api_id for call in plan.planned_calls} == {api_id}
    assert "coinglass.futures_pairs_markets" not in {call.catalog_endpoint_id for call in plan.planned_calls}
    assert "crypto_binance_spot_market" in {call.provider_id for call in plan.planned_calls}


def test_coinglass_raw_cvd_uses_contract_symbol_for_pair_endpoint() -> None:
    request = _request(
        request_id="crypto-cvd",
        item="CVD",
        market=Market.CRYPTO,
        instrument="BTC",
        granularity="realtime",
        purpose="market_report",
    )

    plan = plan_public_data_requests((request,))

    raw_futures_cvd = next(call for call in plan.planned_calls if call.catalog_endpoint_id == "coinglass.raw_futures_cvd_history")
    assert raw_futures_cvd.params["symbol"] == "BTCUSDT"


def test_crypto_liquidation_plans_project_bound_coinglass_interfaces_not_directory_endpoints() -> None:
    request = _request(
        request_id="crypto-liquidation",
        item="清算",
        market=Market.CRYPTO,
        instrument="BTC",
        granularity=None,
        purpose="market_report",
    )

    plan = plan_public_data_requests((request,))

    coinglass_calls = [call.catalog_endpoint_id for call in plan.planned_calls if call.provider_id == "official_api_coinglass"]
    assert "coinglass.futures_liquidation" in coinglass_calls
    assert "coinglass.raw_futures_liquidation_history" in coinglass_calls
    assert "coinglass.futures_pairs_markets" not in coinglass_calls
    assert "coinglass.raw_futures_liquidation_coin_list" not in coinglass_calls
    assert "coinglass.raw_futures_liquidation_exchange_list" not in coinglass_calls
    assert "coinglass.raw_futures_liquidation_order" not in coinglass_calls


def test_crypto_liquidation_heatmap_does_not_plan_every_coinglass_map_model() -> None:
    request = _request(
        request_id="crypto-liquidation-map",
        item="清算地图",
        market=Market.CRYPTO,
        instrument="BTC",
        granularity=None,
        purpose="market_report",
    )

    plan = plan_public_data_requests((request,))

    coinglass_calls = [call.catalog_endpoint_id for call in plan.planned_calls if call.provider_id == "official_api_coinglass"]
    assert coinglass_calls == ["coinglass.futures_liquidation_heatmap"]


def test_crypto_cvd_missing_granularity_uses_public_contract_hourly_coinglass_interval() -> None:
    request = _request(
        request_id="crypto-cvd-default-granularity",
        item="CVD",
        market=Market.CRYPTO,
        instrument="BTC",
        granularity=None,
        purpose="market_report",
    )

    plan = plan_public_data_requests((request,))

    assert plan.needs[0].granularity == "realtime"
    coinglass_calls = [call for call in plan.planned_calls if call.provider_id == "official_api_coinglass"]
    assert coinglass_calls
    assert {call.params["interval"] for call in coinglass_calls if "interval" in call.params} == {"1h"}


def test_crypto_open_interest_without_granularity_starts_with_hourly_history_endpoint() -> None:
    request = _request(
        request_id="crypto-oi-default-granularity",
        item="OI",
        market=Market.CRYPTO,
        instrument="BTC",
        granularity=None,
        purpose="market_report",
    )

    plan = plan_public_data_requests((request,))

    assert plan.needs[0].granularity == "hourly"
    assert plan.initial_scheduled_calls
    assert plan.initial_scheduled_calls[0].catalog_endpoint_id == "coinglass.futures_open_interest_aggregated_history"
    coinglass_calls = [call.catalog_endpoint_id for call in plan.planned_calls if call.provider_id == "official_api_coinglass"]
    assert coinglass_calls[0] == "coinglass.futures_open_interest_aggregated_history"
    assert "coinglass.futures_open_interest" not in coinglass_calls


def test_crypto_ahr999_public_contract_matches_coinglass_onchain_rows() -> None:
    contract = {item.api_id: item for item in public_api_contracts()}["crypto.ahr999"]

    assert contract.output_contract["dataset"] == "crypto_onchain_metric"
    assert contract.output_contract["required_fields"] == ("ahr999", "value", "timestamp")


def test_coinglass_realtime_interval_requests_use_official_hourly_interval() -> None:
    request = _request(
        request_id="crypto-long-short",
        item="多空比",
        market=Market.CRYPTO,
        instrument="BTC",
        granularity="realtime",
        purpose="market_report",
    )

    plan = plan_public_data_requests((request,))

    main = next(call for call in plan.planned_calls if call.catalog_endpoint_id == "coinglass.futures_long_short_ratio")
    assert main.params["interval"] == "1h"


def test_coinglass_exchange_specific_long_short_symbol_rules_do_not_use_keyword_guessing() -> None:
    request = _request(
        request_id="crypto-long-short-symbols",
        item="多空比",
        market=Market.CRYPTO,
        instrument="BTC",
        granularity="realtime",
        purpose="market_report",
    )

    plan = plan_public_data_requests((request,))

    params_by_endpoint = {call.catalog_endpoint_id: call.params for call in plan.planned_calls}
    assert params_by_endpoint["coinglass.futures_long_short_ratio"]["symbol"] == "BTCUSDT"
    assert params_by_endpoint["coinglass.raw_futures_top_long_short_account_ratio_history"]["symbol"] == "BTCUSDT"
    assert params_by_endpoint["coinglass.raw_hyperliquid_global_long_short_account_ratio_history"]["symbol"] == "BTC"
    assert "coinglass.raw_bitfinex_margin_long_short" not in params_by_endpoint


def test_crypto_long_short_starts_with_single_primary_paid_interface() -> None:
    request = _request(
        request_id="crypto-long-short-primary",
        item="多空比",
        market=Market.CRYPTO,
        instrument="BTC",
        granularity="realtime",
        purpose="market_report",
    )

    plan = plan_public_data_requests((request,))

    coinglass_calls = [
        call
        for call in plan.planned_calls
        if call.public_api_id == "crypto.long_short_ratio" and call.source_group_id == "coinglass"
    ]
    assert {getattr(call.execution_group_kind, "value", call.execution_group_kind) for call in coinglass_calls} == {
        "fallback_chain"
    }
    group = next(group for group in plan.execution_groups if group.business_api_id == "crypto.long_short_ratio")
    initial_endpoints = [call.catalog_endpoint_id for call in plan.planned_calls if call.call_id in set(group.initial_call_ids)]
    assert initial_endpoints == ["coinglass.futures_long_short_ratio"]
    assert "binance.futures_long_short_ratio" not in initial_endpoints


def test_crypto_valuation_uses_coinglass_composition_for_price_and_supply() -> None:
    request = _request(
        request_id="crypto-valuation-composition",
        item="估值",
        market=Market.CRYPTO,
        instrument="BTC",
        granularity="realtime",
        purpose="fundamental_report",
    )

    plan = plan_public_data_requests((request,))

    group = next(group for group in plan.execution_groups if group.business_api_id == "crypto.valuation_metric")
    assert group.kind.value == "composition_group"
    initial_endpoints = {call.catalog_endpoint_id for call in plan.planned_calls if call.call_id in set(group.initial_call_ids)}
    assert {"coinglass.spot_coins_markets", "coinglass.coin_market_data_history"} <= initial_endpoints


def test_crypto_onchain_project_item_plans_to_coinglass_onchain_interfaces() -> None:
    request = _request(
        request_id="crypto-onchain",
        item="链上",
        market=Market.CRYPTO,
        instrument="BTC",
        granularity=None,
        purpose="fundamental_report",
    )

    plan = plan_public_data_requests((request,))

    calls = {(call.provider_id, call.catalog_endpoint_id, call.public_api_id) for call in plan.planned_calls}
    assert ("official_api_coinglass", "coinglass.onchain_exchange_balance", "crypto.onchain_metric") not in calls
    assert ("official_api_coinglass", "coinglass.onchain_whale_transfer", "crypto.onchain_metric") in calls
    assert ("official_api_coinglass", "coinglass.raw_index_bitcoin_active_addresses", "crypto.onchain_metric") in calls
    assert ("official_api_coinglass", "coinglass.raw_index_bitcoin_sth_sopr", "crypto.onchain_metric") in calls
    assert ("official_api_coinglass", "coinglass.raw_index_bitcoin_lth_sopr", "crypto.onchain_metric") in calls
    assert ("official_api_coinglass", "coinglass.raw_index_bitcoin_net_unrealized_profit_loss", "crypto.onchain_metric") in calls


def test_crypto_exchange_balance_item_still_plans_to_coinglass_balance_endpoint() -> None:
    request = _request(
        request_id="crypto-exchange-balance",
        item="交易所余额",
        market=Market.CRYPTO,
        instrument="BTC",
        granularity="realtime",
        purpose="fundamental_report",
    )

    plan = plan_public_data_requests((request,))

    calls = {(call.provider_id, call.catalog_endpoint_id, call.public_api_id) for call in plan.planned_calls}
    assert ("official_api_coinglass", "coinglass.onchain_exchange_balance", "crypto.exchange_balance") in calls


def test_crypto_institutional_product_flow_alias_plans_to_etf_flow_interfaces() -> None:
    request = _request(
        request_id="crypto-institutional-product-flow",
        item="机构产品资金流",
        market=Market.CRYPTO,
        instrument="BTC",
        granularity="daily",
        purpose="fundamental_report",
    )

    plan = plan_public_data_requests((request,))

    assert request.api_id == "crypto.etf_flow"
    assert plan.skipped_needs == ()
    assert {call.public_api_id for call in plan.planned_calls} == {"crypto.etf_flow"}


def test_worker_consumer_labels_do_not_change_provider_candidates() -> None:
    consumers = ("report", "select", "ui_probe", "maintenance")
    candidate_sets = []
    for consumer in consumers:
        request = _request(request_id=f"request-{consumer}", item="日线", consumer=consumer)
        plan = plan_public_data_requests((request,))
        candidate_sets.append(tuple((call.provider_id, call.catalog_endpoint_id, call.public_api_id) for call in plan.planned_calls))

    assert candidate_sets == [candidate_sets[0]] * len(candidate_sets)


@pytest.mark.parametrize("forbidden_key", ["api_id", "provider", "path", "api_name", "url", "headers", "token", "fields"])
def test_public_request_rejects_execution_details(forbidden_key: str) -> None:
    payload = _request().model_dump(mode="python")
    payload[forbidden_key] = "leak"

    with pytest.raises(ValueError):
        PublicDataRequest.model_validate(payload)


@pytest.mark.parametrize("forbidden_key", ["provider", "path", "api_name", "fields"])
def test_public_request_rejects_forbidden_nested_params(forbidden_key: str) -> None:
    payload = _request().model_dump(mode="python")
    payload["params"] = {forbidden_key: "leak"}

    with pytest.raises(ValueError):
        PublicDataRequest.model_validate(payload)


def test_crypto_detail_items_plan_only_requested_items_not_fixed_market_bundle() -> None:
    items = ("资金费率", "OI", "多空比", "清算", "清算地图", "主动买卖量差")
    requests = tuple(
        _request(
            request_id=f"request-{index}",
            item=item,
            market=Market.CRYPTO,
            instrument="BTC",
            granularity="realtime",
            purpose="market_report",
        )
        for index, item in enumerate(items)
    )

    plan = plan_public_data_requests(requests)

    expected_api_ids = {
        "crypto.funding_rate",
        "crypto.open_interest",
        "crypto.long_short_ratio",
        "crypto.liquidation",
        "crypto.liquidation_heatmap",
        "crypto.cvd",
    }
    assert {need.api_id for need in plan.needs} == expected_api_ids
    assert {call.public_api_id for call in plan.planned_calls} <= expected_api_ids
    assert "crypto.exchange_netflow" not in {call.public_api_id for call in plan.planned_calls}
    assert "crypto.options_open_interest" not in {call.public_api_id for call in plan.planned_calls}


def test_hk_project_required_market_items_plan_to_tushare_hk_interfaces() -> None:
    requests = (
        _request(
            request_id="hk-intraday",
            item="分时",
            market=Market.HK,
            instrument="00700.HK",
            granularity="intraday",
            purpose="market_report",
        ),
        _request(
            request_id="hk-realtime",
            item="实时价",
            market=Market.HK,
            instrument="00700.HK",
            granularity="realtime",
            purpose="market_report",
        ),
    )

    plan = plan_public_data_requests(requests)

    assert plan.skipped_needs == ()
    calls = {(call.public_api_id, call.catalog_endpoint_id) for call in plan.planned_calls}
    assert ("hk.intraday_bar", "tushare.hk_mins") in calls
    assert ("hk.realtime_quote", "tushare.rt_hk_k") in calls


def test_hk_project_required_financial_statement_plans_all_tushare_statement_interfaces() -> None:
    request = _request(
        request_id="hk-financial-statement",
        item="财报",
        market=Market.HK,
        instrument="00700.HK",
        granularity="quarterly",
        purpose="fundamental_report",
    )

    plan = plan_public_data_requests((request,))

    assert plan.skipped_needs == ()
    statement_endpoints = {
        call.catalog_endpoint_id
        for call in plan.planned_calls
        if call.provider_id == "official_api_tushare" and call.public_api_id == "hk.financial_statement"
    }
    assert {"tushare.hk_income", "tushare.hk_balancesheet", "tushare.hk_cashflow"} <= statement_endpoints


def test_hk_valuation_plans_to_valuation_adapter_not_financial_adapter() -> None:
    request = _request(
        request_id="hk-valuation",
        item="估值",
        market=Market.HK,
        instrument="00700.HK",
        granularity="realtime",
        purpose="fundamental_report",
    )

    plan = plan_public_data_requests((request,))

    assert plan.skipped_needs == ()
    yahoo_calls = tuple(call for call in plan.planned_calls if call.provider_id == "hk_yahoo_finance")
    assert any(call.catalog_endpoint_id == "yahoo_finance.hk_quote_summary_valuation" for call in yahoo_calls)
    assert all(call.catalog_endpoint_id != "yahoo_finance.hk_quote_summary_financial" for call in yahoo_calls)
    assert all(call.catalog_endpoint_id != "tushare.hk_daily_adj" for call in plan.planned_calls)
    valuation_call = next(call for call in yahoo_calls if call.catalog_endpoint_id == "yahoo_finance.hk_quote_summary_valuation")
    assert valuation_call.params["type"] == "trailingPeRatio,trailingMarketCap"


def test_build_provider_call_spec_uses_catalog_endpoint_identity() -> None:
    endpoint = next(endpoint for endpoint in all_endpoints() if endpoint.endpoint_id == "tushare.daily")
    need = _need()

    call = build_provider_call_spec(need=need, endpoint=endpoint, public_api_id=need.api_id)

    assert call.public_api_id == "cn_a.daily_bar"
    assert call.provider_id == "official_api_tushare"
    assert call.catalog_endpoint_id == "tushare.daily"
    assert call.official_path_or_api_name == "daily"
    assert call.rate_limit_bucket == "ratelimit:tushare"
    assert call.need_ids == ("need-1",)


@pytest.mark.parametrize("forbidden_key", ["provider", "path", "api_name", "fields"])
def test_provider_call_spec_rejects_caller_scope_keys(forbidden_key: str) -> None:
    payload = {
        "call_id": "call-1",
        "provider_id": "official_api_tushare",
        "catalog_endpoint_id": "tushare.daily",
        "official_path_or_api_name": "daily",
        "params": {forbidden_key: "leak"},
        "auth_scope": "tushare_token",
        "rate_limit_bucket": "ratelimit:tushare",
        "http_visibility": "managed_http",
        "parser_status": "normalized",
        "batch_key": "batch-1",
        "official_doc_ref": "https://tushare.pro/document/2?doc_id=27",
        "deadline_at": datetime(2026, 6, 12, 12, 0, tzinfo=UTC),
        "need_ids": ("need-1",),
    }

    with pytest.raises(ValueError):
        ProviderCallSpec.model_validate(payload)
