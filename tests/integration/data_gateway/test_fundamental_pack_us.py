from __future__ import annotations

from claw_trade.data_gateway.models import FreshnessPolicy, Market, PackDomain, PackRequest, RunProviderPlan
from claw_trade.data_gateway.packs.fundamental import FundamentalPackService
from claw_trade.data_gateway.providers.fundamental import build_default_fundamental_adapters


def _request() -> PackRequest:
    return PackRequest(
        run_id="run-us",
        call_id="call-us",
        worker_id="fundamental_analyst",
        market=Market.US,
        domain=PackDomain.FUNDAMENTAL,
        ticker="AAPL",
        company_name="Apple Inc.",
        start_date="2026-05-01",
        end_date="2026-05-17",
        current_date="2026-05-17",
        currency="USD",
        profile="US",
        freshness_policy=FreshnessPolicy(max_age_seconds=300),
    )


def _plan(request: PackRequest, adapters) -> RunProviderPlan:
    specs = []
    for adapter in adapters:
        specs.extend(adapter.build_call_specs(request))
    ordered = tuple(sorted(specs, key=lambda item: (item.priority, item.adapter_id)))
    return RunProviderPlan(
        run_id=request.run_id,
        provider_config_version="cfg-us",
        market=request.market,
        ticker=request.ticker,
        domains=(PackDomain.FUNDAMENTAL,),
        call_specs=ordered,
        shared_call_keys=tuple(item.call_key for item in ordered),
        cache_keys=tuple(f"cache:{item.adapter_id}" for item in ordered),
        rate_limit_plan=(),
        initial_gaps=(),
        generated_at="2026-05-17T00:00:00+00:00",
        remote_prefetch_allowed=False,
    )


def test_us_fundamental_pack_uses_yfinance_path_without_required_key(monkeypatch) -> None:
    request = _request()
    adapters = tuple(item for item in build_default_fundamental_adapters(provider_config_version="cfg-us", env={}) if item.market == Market.US)
    plan = _plan(request, adapters)

    def _fake_yf(*, symbol: str):
        assert symbol == "AAPL"
        return ({"trailingPE": 28.3, "priceToBook": 39.5, "returnOnEquity": 0.44},)

    def _fake_sec(*, symbol: str):
        assert symbol == "AAPL"
        return ({"form": "10-K", "filing_date": "2025-11-01"},)

    monkeypatch.setattr("claw_trade.data_gateway.providers.fundamental._call_openbb_us_fundamental_yfinance", _fake_yf)
    monkeypatch.setattr("claw_trade.data_gateway.providers.fundamental._call_openbb_us_sec_filings", _fake_sec)

    pack = FundamentalPackService(settings=object(), adapters=adapters).get_pack(request, plan)

    assert pack.readiness.status.value == "ready"
    assert not any(gap.reason.value == "credential_missing" for gap in pack.data_gaps)
    assert pack.compact_facts["valuation.pe"] == 28.3
    assert pack.compact_facts["valuation.pb"] == 39.5
    assert pack.compact_facts["financial_indicators.roe"] == 0.44
    assert "BUY" not in pack.reader_brief_md
    assert "HOLD" not in pack.reader_brief_md
    assert "SELL" not in pack.reader_brief_md
