from __future__ import annotations

from claw_trade.data_gateway.models import FreshnessPolicy, Market, PackDomain, PackRequest, RunProviderPlan
from claw_trade.data_gateway.packs.fundamental import FundamentalPackService
from claw_trade.data_gateway.providers.fundamental import build_default_fundamental_adapters


def _request() -> PackRequest:
    return PackRequest(
        run_id="run-cn-a",
        call_id="call-cn-a",
        worker_id="fundamental_analyst",
        market=Market.CN_A,
        domain=PackDomain.FUNDAMENTAL,
        ticker="600519.SH",
        company_name="贵州茅台",
        start_date="2026-05-01",
        end_date="2026-05-17",
        current_date="2026-05-17",
        currency="CNY",
        profile="CN_A",
        freshness_policy=FreshnessPolicy(max_age_seconds=300),
    )


def _plan(request: PackRequest, adapters) -> RunProviderPlan:
    specs = []
    for adapter in adapters:
        specs.extend(adapter.build_call_specs(request))
    ordered = tuple(sorted(specs, key=lambda item: (item.priority, item.adapter_id)))
    return RunProviderPlan(
        run_id=request.run_id,
        provider_config_version="cfg-cn-a",
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


def test_cn_a_fundamental_pack_uses_public_source_and_keeps_explicit_field_gaps(monkeypatch) -> None:
    request = _request()
    adapters = tuple(item for item in build_default_fundamental_adapters(provider_config_version="cfg-cn-a", env={}) if item.market == Market.CN_A)
    plan = _plan(request, adapters)

    def _fake_akshare(*, symbol: str):
        assert symbol == "600519"
        return ({"pe_ttm": 31.2, "pb": 8.9, "peg": 1.4},)

    monkeypatch.setattr("claw_trade.data_gateway.providers.fundamental._call_akshare_cn_a_fundamental", _fake_akshare)

    pack = FundamentalPackService(settings=object(), adapters=adapters).get_pack(request, plan)

    assert pack.readiness.status.value == "partial"
    assert any(gap.reason.value == "field_missing" and gap.field_path == "financial_indicators.roe" for gap in pack.data_gaps)
    assert pack.compact_facts.get("valuation.peg") == 1.4
    assert "BUY" not in pack.reader_brief_md
    assert "HOLD" not in pack.reader_brief_md
    assert "SELL" not in pack.reader_brief_md
