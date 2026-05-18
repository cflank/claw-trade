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
from claw_trade.data_gateway.providers.market_adapters import build_default_market_adapters


def test_market_pack_cn_a_builds_ohlcv_indicators_and_natural_language_brief() -> None:
    request = PackRequest(
        run_id="run-cn-a-1",
        call_id="call-cn-a-1",
        worker_id="market_analyst",
        market=Market.CN_A,
        domain=PackDomain.MARKET,
        ticker="600519.SH",
        company_name="贵州茅台",
        start_date="2026-04-01",
        end_date="2026-05-17",
        current_date="2026-05-17",
        currency="CNY",
        profile="CN_A",
        freshness_policy=FreshnessPolicy(max_age_seconds=300),
    )
    adapters = build_default_market_adapters(provider_config_version="cfg-cn-a", env={"TUSHARE_TOKEN": "ok"})
    adapter = next(item for item in adapters if getattr(item, "market", None) == Market.CN_A)
    specs = adapter.build_call_specs(request)
    assert [spec.endpoint for spec in specs] == ["daily", "stock_zh_a_hist", "quote"]

    run_plan = RunProviderPlan(
        run_id=request.run_id,
        provider_config_version="cfg-cn-a",
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

    rows = [
        {
            "date": f"2026-04-{day:02d}",
            "open": 1500 + day,
            "high": 1510 + day,
            "low": 1490 + day,
            "close": 1505 + day,
            "volume": 1000000 + day,
            "amount": 2000000 + day,
            "currency": "CNY",
            "timezone": "Asia/Shanghai",
        }
        for day in range(1, 31)
    ]
    result = make_provider_result(
        request=request,
        spec=specs[0],
        status=ProviderStatus.REMOTE_SUCCESS,
        rows=rows,
        freshness=FreshnessStatus.FRESH_REMOTE,
        raw_ref="raw://cn-a-tushare",
        normalized_ref="norm://cn-a-tushare",
    )
    backup = make_provider_result(
        request=request,
        spec=specs[1],
        status=ProviderStatus.REMOTE_ERROR,
        rows=(),
        freshness=FreshnessStatus.NOT_FETCHED,
        error_code="upstream_error",
        error_message="akshare upstream timeout",
    )

    builder = MarketPackBuilder()
    pack = builder.build(
        request=request,
        run_plan=run_plan,
        results=(result, backup),
        chart_image_refs={
            f"{request.ticker}:market_structure": "ov://charts/cn-a-market-structure.png",
            f"{request.ticker}:indicator_panels": "ov://charts/cn-a-indicator-panels.png",
        },
    )

    assert pack.compact_facts["schema_id"] == "cn_a.market.ohlcv.v1"
    assert pack.compact_facts["latest_close"] is not None
    assert pack.readiness.status.value in {"ready", "partial"}
    assert pack.chart_assets[0].status.value == "ready"
    assert "## 数据资料包：" in pack.reader_brief_md
    assert "### 来源和缺口" in pack.reader_brief_md
    assert "akshare upstream timeout" in pack.reader_brief_md
    assert "material_id" not in pack.reader_brief_md
    assert "Mongo" not in pack.reader_brief_md
    assert "remote_success" not in pack.reader_brief_md
    assert "fresh_remote" not in pack.reader_brief_md
    assert "raw://" not in pack.reader_brief_md
    assert pack.reader_brief_md.lstrip().startswith("## ")


def test_market_pack_cn_a_tushare_failure_does_not_block_when_akshare_covers_group() -> None:
    request = PackRequest(
        run_id="run-cn-a-quorum",
        call_id="call-cn-a-quorum",
        worker_id="market_analyst",
        market=Market.CN_A,
        domain=PackDomain.MARKET,
        ticker="600519.SH",
        company_name="贵州茅台",
        start_date="2026-03-01",
        end_date="2026-05-17",
        current_date="2026-05-17",
        currency="CNY",
        profile="CN_A",
        freshness_policy=FreshnessPolicy(max_age_seconds=300),
    )
    adapter = next(
        item
        for item in build_default_market_adapters(provider_config_version="cfg-cn-a", env={})
        if getattr(item, "market", None) == Market.CN_A
    )
    specs = adapter.build_call_specs(request)
    tushare = next(item for item in specs if item.provider == "tushare")
    akshare = next(item for item in specs if item.provider == "akshare")
    run_plan = RunProviderPlan(
        run_id=request.run_id,
        provider_config_version="cfg-cn-a",
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
    rows = [
        {
            "date": f"2026-04-{day:02d}",
            "open": 1500 + day,
            "high": 1510 + day,
            "low": 1490 + day,
            "close": 1505 + day,
            "volume": 1000000 + day,
            "currency": "CNY",
            "timezone": "Asia/Shanghai",
        }
        for day in range(1, 26)
    ]
    failed_tushare = make_provider_result(
        request=request,
        spec=tushare,
        status=ProviderStatus.REMOTE_ERROR,
        rows=(),
        freshness=FreshnessStatus.NOT_FETCHED,
        error_code="upstream_error",
        error_message="tushare transient upstream error",
    )
    covered_akshare = make_provider_result(
        request=request,
        spec=akshare,
        status=ProviderStatus.REMOTE_SUCCESS,
        rows=rows,
        freshness=FreshnessStatus.FRESH_REMOTE,
        raw_ref="raw://cn-a-akshare",
        normalized_ref="norm://cn-a-akshare",
    )

    pack = MarketPackBuilder().build(
        request=request,
        run_plan=run_plan,
        results=(failed_tushare, covered_akshare),
        chart_image_refs={
            f"{request.ticker}:market_structure": "ov://charts/cn-a-market-structure.png",
            f"{request.ticker}:indicator_panels": "ov://charts/cn-a-indicator-panels.png",
        },
    )

    assert pack.readiness.status.value == "partial"
    assert pack.readiness.blocking_gap_ids == ()
    assert pack.readiness.coverage["group:cn_a_ohlcv"] == "1/1"
    assert any("tushare transient upstream error" in gap.root_cause for gap in pack.data_gaps)
    assert pack.compact_facts["ohlcv_row_count"] == 25
