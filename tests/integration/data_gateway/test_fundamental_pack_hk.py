from __future__ import annotations

from claw_trade.data_gateway.models import FreshnessPolicy, Market, PackDomain, PackRequest, RunProviderPlan
from claw_trade.data_gateway.packs.fundamental import FundamentalPackService
from claw_trade.data_gateway.providers.fundamental import build_default_fundamental_adapters


def _request() -> PackRequest:
    return PackRequest(
        run_id="run-hk",
        call_id="call-hk",
        worker_id="fundamental_analyst",
        market=Market.HK,
        domain=PackDomain.FUNDAMENTAL,
        ticker="00700.HK",
        company_name="腾讯控股",
        start_date="2026-05-01",
        end_date="2026-05-17",
        current_date="2026-05-17",
        currency="HKD",
        profile="HK",
        freshness_policy=FreshnessPolicy(max_age_seconds=300),
    )


def _plan(request: PackRequest, adapters) -> RunProviderPlan:
    specs = []
    for adapter in adapters:
        specs.extend(adapter.build_call_specs(request))
    ordered = tuple(sorted(specs, key=lambda item: (item.priority, item.adapter_id)))
    return RunProviderPlan(
        run_id=request.run_id,
        provider_config_version="cfg-hk",
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


def test_hk_fundamental_pack_prioritizes_official_original_and_keeps_raw_ref(monkeypatch) -> None:
    request = _request()
    adapters = tuple(item for item in build_default_fundamental_adapters(provider_config_version="cfg-hk", env={}) if item.market == Market.HK)
    plan = _plan(request, adapters)

    def _fake_hk_financial(*, symbol: str):
        assert symbol == "00700"
        return ({"pe_ttm": 19.8, "pb": 4.2, "roe": 0.21},)

    def _fake_hk_official(*, symbol: str):
        assert symbol == "00700"
        return ({"report_date": "2025-12-31", "title": "年度报告"},)

    monkeypatch.setattr("claw_trade.data_gateway.providers.fundamental._call_akshare_hk_fundamental", _fake_hk_financial)
    monkeypatch.setattr("claw_trade.data_gateway.providers.fundamental._call_akshare_hk_income", lambda *, symbol: ())
    monkeypatch.setattr("claw_trade.data_gateway.providers.fundamental._call_hk_official_filings", _fake_hk_official)
    monkeypatch.setattr("claw_trade.data_gateway.providers.fundamental._call_yahoo_quote_summary_fundamental", lambda *, symbol: ())

    pack = FundamentalPackService(settings=object(), adapters=adapters).get_pack(request, plan)

    assert pack.compact_facts["valuation.pe"] == 19.8
    assert not any(item.field_path == "valuation.pe" for item in pack.conflicts)
    assert "官方原文引用已保留在审计证据中" in pack.reader_brief_md
    assert "source_role=" not in pack.reader_brief_md
    assert "raw://" not in pack.reader_brief_md
    assert any(ref.startswith("raw://hk_official_filing/") for ref in pack.raw_refs)


def test_hk_fundamental_pack_uses_yahoo_quote_summary_supplement_for_missing_pe_pb_roe(monkeypatch) -> None:
    request = _request()
    adapters = tuple(item for item in build_default_fundamental_adapters(provider_config_version="cfg-hk", env={}) if item.market == Market.HK)
    plan = _plan(request, adapters)

    monkeypatch.setattr("claw_trade.data_gateway.providers.fundamental._call_akshare_hk_fundamental", lambda *, symbol: ())
    monkeypatch.setattr("claw_trade.data_gateway.providers.fundamental._call_akshare_hk_income", lambda *, symbol: ())
    monkeypatch.setattr("claw_trade.data_gateway.providers.fundamental._call_hk_official_filings", lambda *, symbol: ())

    def _fake_yahoo(*, symbol: str):
        assert symbol == "0700.HK"
        return ({"trailingPE": 18.7, "priceToBook": 3.9, "returnOnEquity": 0.214},)

    monkeypatch.setattr("claw_trade.data_gateway.providers.fundamental._call_yahoo_quote_summary_fundamental", _fake_yahoo)

    pack = FundamentalPackService(settings=object(), adapters=adapters).get_pack(request, plan)

    assert pack.compact_facts["valuation.pe"] == 18.7
    assert pack.compact_facts["valuation.pb"] == 3.9
    assert pack.compact_facts["financial_indicators.roe"] == 0.214
    missing_field_paths = {gap.field_path for gap in pack.data_gaps if gap.reason.value == "field_missing"}
    assert "valuation.pe" not in missing_field_paths
    assert "valuation.pb" not in missing_field_paths
    assert "financial_indicators.roe" not in missing_field_paths
    assert "yahoo_quote_summary.quote_summary：远端获取成功。" in pack.reader_brief_md
