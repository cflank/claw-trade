from __future__ import annotations

from claw_trade.data_gateway.models import FreshnessPolicy, Market, PackDomain, PackRequest, RunProviderPlan
from claw_trade.data_gateway.packs.fundamental import FundamentalPackService
from claw_trade.data_gateway.providers.fundamental import build_default_fundamental_adapters


def _request() -> PackRequest:
    return PackRequest(
        run_id="run-crypto",
        call_id="call-crypto",
        worker_id="fundamental_analyst",
        market=Market.CRYPTO,
        domain=PackDomain.FUNDAMENTAL,
        ticker="BTC",
        company_name="Bitcoin",
        start_date="2026-05-01",
        end_date="2026-05-17",
        current_date="2026-05-17",
        currency="USD",
        profile="CRYPTO",
        freshness_policy=FreshnessPolicy(max_age_seconds=300),
    )


def _plan(request: PackRequest, adapters) -> RunProviderPlan:
    specs = []
    for adapter in adapters:
        specs.extend(adapter.build_call_specs(request))
    ordered = tuple(sorted(specs, key=lambda item: (item.priority, item.adapter_id)))
    return RunProviderPlan(
        run_id=request.run_id,
        provider_config_version="cfg-crypto",
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


def test_crypto_fundamental_pack_covers_tvl_revenue_fees_supply_and_market_cap(monkeypatch) -> None:
    request = _request()
    adapters = tuple(item for item in build_default_fundamental_adapters(provider_config_version="cfg-crypto", env={}) if item.market == Market.CRYPTO)
    plan = _plan(request, adapters)

    def _fake_cg(*, coin_id: str, demo_api_key: str | None):
        assert coin_id == "bitcoin"
        assert demo_api_key is None
        return {
            "valuation.market_cap_usd": 2_100_000_000_000,
            "supply.circulating": 19_900_000,
            "supply.total": 19_900_000,
            "security.score": 87,
            "funding.last_round": "N/A",
        }

    def _fake_llama(*, protocol_slug: str):
        assert protocol_slug == "bitcoin"
        return {
            "defi.tvl_usd": 95_000_000_000,
            "defi.fees_24h_usd": 12_500_000,
            "defi.revenue_24h_usd": 4_100_000,
        }

    monkeypatch.setattr("claw_trade.data_gateway.providers.fundamental._call_coingecko_coin_fundamental", _fake_cg)
    monkeypatch.setattr("claw_trade.data_gateway.providers.fundamental._call_defillama_fundamental", _fake_llama)

    pack = FundamentalPackService(settings=object(), adapters=adapters).get_pack(request, plan)

    assert pack.readiness.status.value == "ready"
    assert not any(gap.reason.value == "field_missing" for gap in pack.data_gaps)
    assert pack.compact_facts["valuation.market_cap_usd"] == 2_100_000_000_000
    assert pack.compact_facts["defi.tvl_usd"] == 95_000_000_000
    assert "BUY" not in pack.reader_brief_md
    assert "HOLD" not in pack.reader_brief_md
    assert "SELL" not in pack.reader_brief_md
