from __future__ import annotations

from claw_trade.data_gateway.models import (
    FreshnessPolicy,
    FreshnessStatus,
    Market,
    PackDomain,
    PackRequest,
    ProviderStatus,
    RunProviderPlan,
)
from claw_trade.data_gateway.packs.market import MarketPackBuilder, make_provider_result
from claw_trade.data_gateway.providers.market_adapters import (
    build_default_market_adapters,
    normalize_hk_symbol_for_stock_hk_daily,
)


def test_market_pack_hk_enforces_stock_hk_daily_contract_and_root_cause() -> None:
    request = PackRequest(
        run_id="run-hk-1",
        call_id="call-hk-1",
        worker_id="market_analyst",
        market=Market.HK,
        domain=PackDomain.MARKET,
        ticker="00700.HK",
        company_name="腾讯控股",
        start_date="2026-04-01",
        end_date="2026-05-17",
        current_date="2026-05-17",
        currency="HKD",
        profile="HK",
        freshness_policy=FreshnessPolicy(max_age_seconds=300),
    )
    adapters = build_default_market_adapters(provider_config_version="cfg-hk", env={"TUSHARE_TOKEN": "ok"})
    adapter = next(item for item in adapters if getattr(item, "market", None) == Market.HK)
    specs = adapter.build_call_specs(request)
    stock_hk_spec = next(spec for spec in specs if spec.endpoint == "stock_hk_daily")

    assert normalize_hk_symbol_for_stock_hk_daily("00700.HK") == "00700"
    assert stock_hk_spec.params["symbol"] == "00700"
    assert stock_hk_spec.params["adjust"] == "qfq"
    assert stock_hk_spec.params["currency"] == "HKD"
    assert stock_hk_spec.params["timezone"] == "Asia/Hong_Kong"

    run_plan = RunProviderPlan(
        run_id=request.run_id,
        provider_config_version="cfg-hk",
        market=request.market,
        ticker=request.ticker,
        domains=(PackDomain.MARKET,),
        call_specs=specs,
        shared_call_keys=tuple(spec.call_key for spec in specs),
        cache_keys=(),
        rate_limit_plan=(),
        initial_gaps=(),
        generated_at="2026-05-17T00:00:00+00:00",
        remote_prefetch_allowed=False,
    )

    tushare_result = make_provider_result(
        request=request,
        spec=specs[0],
        status=ProviderStatus.RATE_LIMITED,
        rows=(),
        freshness=FreshnessStatus.NOT_FETCHED,
        error_code="rate_limited",
        error_message="tushare hk_daily rate limited",
    )
    hk_rows = [
        {
            "日期": f"2026-05-{day:02d}",
            "开盘": 500 + day,
            "最高": 510 + day,
            "最低": 490 + day,
            "收盘": 505 + day,
            "成交量": 1_000_000 + day,
            "成交额": 2_000_000 + day,
            "currency": "HKD",
            "timezone": "Asia/Hong_Kong",
        }
        for day in range(25, 0, -1)
    ]
    akshare_result = make_provider_result(
        request=request,
        spec=stock_hk_spec,
        status=ProviderStatus.REMOTE_SUCCESS,
        rows=hk_rows,
        freshness=FreshnessStatus.FRESH_REMOTE,
        raw_ref="raw://hk-akshare",
        normalized_ref="norm://hk-akshare",
    )

    pack = MarketPackBuilder().build(
        request=request,
        run_plan=run_plan,
        results=(tushare_result, akshare_result),
    )

    assert pack.compact_facts["schema_id"] == "hk.market.ohlcv.v1"
    assert pack.compact_facts["currency"] == "HKD"
    assert pack.compact_facts["timezone"] == "Asia/Hong_Kong"
    assert pack.compact_facts["latest_close"] == 530.0
    assert all(asset.root_cause for asset in pack.chart_assets if asset.status.value != "ready")
    assert "接口限流" in pack.reader_brief_md
    assert "图表渲染结果缺失" in pack.reader_brief_md
    assert "rate_limited" not in pack.reader_brief_md
    assert "freshness=" not in pack.reader_brief_md
    assert "cache=" not in pack.reader_brief_md
    assert "raw://" not in pack.reader_brief_md
