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


def test_market_pack_us_uses_openbb_yfinance_without_required_api_key() -> None:
    request = PackRequest(
        run_id="run-us-1",
        call_id="call-us-1",
        worker_id="market_analyst",
        market=Market.US,
        domain=PackDomain.MARKET,
        ticker="AAPL",
        company_name="Apple Inc.",
        start_date="2026-04-01",
        end_date="2026-05-17",
        current_date="2026-05-17",
        currency="USD",
        profile="US",
        freshness_policy=FreshnessPolicy(max_age_seconds=300),
    )
    adapters = build_default_market_adapters(provider_config_version="cfg-us", env={})
    adapter = next(item for item in adapters if getattr(item, "market", None) == Market.US)
    specs = adapter.build_call_specs(request)

    run_plan = RunProviderPlan(
        run_id=request.run_id,
        provider_config_version="cfg-us",
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

    assert len(specs) == 1
    assert specs[0].provider == "openbb_yfinance"
    assert specs[0].endpoint == "equity_price_historical"
    rows = tuple(
        {
            "date": f"2026-05-{day:02d}",
            "open": 200.0 + day,
            "high": 202.0 + day,
            "low": 199.0 + day,
            "close": 201.0 + day,
            "volume": 9_000_000 + day,
            "currency": "USD",
            "timezone": "America/New_York",
        }
        for day in range(1, 26)
    )
    yfinance_result = make_provider_result(
        request=request,
        status=ProviderStatus.REMOTE_SUCCESS,
        spec=specs[0],
        rows=rows,
        freshness=FreshnessStatus.FRESH_REMOTE,
        raw_ref="raw://us-openbb-yfinance",
        normalized_ref="norm://us-openbb-yfinance",
    )

    pack = MarketPackBuilder().build(
        request=request,
        run_plan=run_plan,
        results=(yfinance_result,),
    )

    assert pack.readiness.status.value in {"ready", "partial"}
    assert pack.compact_facts["schema_id"] == "us.market.ohlcv.v1"
    assert pack.compact_facts["latest_close"] == 226.0
    assert not any(gap.reason.value == "credential_missing" for gap in pack.data_gaps)
    assert "provider_attempts" not in pack.reader_brief_md
    assert "openbb_yfinance/equity_price_historical：远端获取成功" in pack.reader_brief_md
    assert "freshness=" not in pack.reader_brief_md
    assert "raw://" not in pack.reader_brief_md
    assert "{" not in pack.reader_brief_md.splitlines()[0]
