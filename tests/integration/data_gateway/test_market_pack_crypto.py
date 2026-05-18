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


def test_market_pack_crypto_keeps_provider_priority_and_chart_root_cause() -> None:
    request = PackRequest(
        run_id="run-crypto-1",
        call_id="call-crypto-1",
        worker_id="market_analyst",
        market=Market.CRYPTO,
        domain=PackDomain.MARKET,
        ticker="BTC",
        company_name="Bitcoin",
        start_date="2026-04-01",
        end_date="2026-05-17",
        current_date="2026-05-17",
        currency="USD",
        profile="CRYPTO",
        freshness_policy=FreshnessPolicy(max_age_seconds=300),
    )
    adapters = build_default_market_adapters(provider_config_version="cfg-crypto", env={})
    adapter = next(item for item in adapters if getattr(item, "market", None) == Market.CRYPTO)
    specs = adapter.build_call_specs(request)
    assert len(specs) == 1
    assert specs[0].provider == "openbb_yfinance"
    assert specs[0].endpoint == "crypto_price_historical"
    assert specs[0].params["symbol"] == "BTCUSD"

    run_plan = RunProviderPlan(
        run_id=request.run_id,
        provider_config_version="cfg-crypto",
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

    crypto_rows = [
        {
            "date": f"2026-05-{day:02d}",
            "open": 80000 + day,
            "high": 81000 + day,
            "low": 79000 + day,
            "close": 80500 + day,
            "volume": 1000 + day,
            "currency": "USD",
            "timezone": "UTC",
        }
        for day in range(1, 28)
    ]
    yfinance_result = make_provider_result(
        request=request,
        spec=specs[0],
        status=ProviderStatus.REMOTE_SUCCESS,
        rows=tuple(crypto_rows),
        freshness=FreshnessStatus.FRESH_REMOTE,
        raw_ref="raw://crypto-openbb-yfinance",
        normalized_ref="norm://crypto-openbb-yfinance",
    )

    pack = MarketPackBuilder().build(
        request=request,
        run_plan=run_plan,
        results=(yfinance_result,),
    )

    assert pack.compact_facts["schema_id"] == "crypto.market.ohlcv.v1"
    assert pack.compact_facts["latest_close"] == 80527.0
    assert any(asset.root_cause for asset in pack.chart_assets if asset.status.value != "ready")
    assert "openbb_yfinance/crypto_price_historical：远端获取成功" in pack.reader_brief_md
    assert "本次资料就绪度只代表已列明来源的价格历史" in pack.reader_brief_md
    assert "资金费率、OI、多空比、清算、链上、宏观或 AHR999" in pack.reader_brief_md
    assert "不得写成已验证事实" in pack.reader_brief_md
    assert "fresh_remote" not in pack.reader_brief_md
    assert "raw://" not in pack.reader_brief_md
