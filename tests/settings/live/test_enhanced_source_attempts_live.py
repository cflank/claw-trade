from __future__ import annotations

from claw_trade.data_gateway.models import (
    DataGapReason,
    FreshnessPolicy,
    Market,
    PackDomain,
    PackRequest,
    ProviderStatus,
    SourceRole,
)
from claw_trade.data_gateway.packs.service import DomainPackService
from claw_trade.data_gateway.providers.defaults import (
    build_default_provider_registry,
    build_default_market_adapters,
    default_provider_config_version,
    load_default_system_capabilities,
)
from claw_trade.data_gateway.providers import market_adapters as market_adapters_module
from claw_trade.data_gateway.providers.run_plan import RunProviderPlanner


def _market_request() -> PackRequest:
    return PackRequest(
        run_id="s05-live-attempts",
        call_id="s05-live-attempts:market",
        worker_id="settings_s05_live",
        market=Market.CN_A,
        domain=PackDomain.MARKET,
        ticker="600519.SH",
        company_name="贵州茅台",
        start_date="2026-05-01",
        end_date="2026-05-23",
        current_date="2026-05-23",
        currency="CNY",
        profile="CN_A",
        freshness_policy=FreshnessPolicy(max_age_seconds=300),
    )


def test_live_enhanced_source_failed_attempt_keeps_default_source_coverage_and_no_masking(monkeypatch) -> None:
    env = {"TUSHARE_TOKEN": "", "TUSHARE_HTTP_URL": "http://127.0.0.1:9"}
    capabilities = load_default_system_capabilities()
    provider_config_version = default_provider_config_version(capabilities)
    adapters = tuple(
        item
        for item in build_default_market_adapters(provider_config_version=provider_config_version, env=env)
        if getattr(item, "market", None) == Market.CN_A
    )
    planner = RunProviderPlanner(adapters_by_id={item.adapter_id: item for item in adapters})
    registry = build_default_provider_registry()
    registry.apply_user_preferred(
        adapter_id="project.cn_a.market.tushare_fallback",
        market=Market.CN_A,
        domain=PackDomain.MARKET,
        source_role=SourceRole.MARKET_DATA,
        coverage_group="cn_a_market_kline",
    )
    plan = planner.build_run_plan(
        run_id="s05-live-attempts",
        market=Market.CN_A,
        ticker="600519.SH",
        company_name="贵州茅台",
        currency="CNY",
        profile="CN_A",
        current_date="2026-05-23",
        start_date="2026-05-01",
        end_date="2026-05-23",
        domains=(PackDomain.MARKET,),
        registry=registry,
        provider_config_version=provider_config_version,
    )

    kline_specs = tuple(spec for spec in plan.call_specs if spec.coverage_group == "cn_a_market_kline")
    assert kline_specs, "expected cn_a_market_kline specs in run plan"
    assert kline_specs[0].provider == "tushare_kline_fallback"

    def _fake_tencent_row(*, symbol: str, fallback_date: str):
        return {
            "symbol": symbol,
            "date": fallback_date,
            "open": 2000.0,
            "high": 2010.0,
            "low": 1990.0,
            "close": 2005.0,
            "volume": 10000.0,
        }

    def _fake_baidu_rows(*, symbol: str, start_date: str):
        del symbol, start_date
        return tuple(
            {
                "date": f"2026-04-{day:02d}",
                "open": 1500 + day,
                "high": 1510 + day,
                "low": 1490 + day,
                "close": 1505 + day,
                "volume": 1000000 + day,
                "amount": 2000000 + day,
            }
            for day in range(1, 26)
        )

    def _fake_mootdx_row(*, symbol: str, fallback_date: str):
        del symbol, fallback_date
        raise RuntimeError("mootdx tcp 7709 unavailable in s05 live fixture")

    monkeypatch.setattr(market_adapters_module, "_call_mootdx_quote_row", _fake_mootdx_row)
    monkeypatch.setattr(market_adapters_module, "_call_tencent_quote_row", _fake_tencent_row)
    monkeypatch.setattr(market_adapters_module, "_call_baidu_kline_with_ma", _fake_baidu_rows)

    pack = DomainPackService(settings=object(), adapters=adapters).get_pack(_market_request(), plan)
    tushare_attempts = [attempt for attempt in pack.attempts if attempt.provider == "tushare_kline_fallback"]
    assert tushare_attempts, "expected approved enhanced source attempt in CN_A market scope"
    assert any(item.status == ProviderStatus.CREDENTIAL_MISSING for item in tushare_attempts)
    assert all(item.status != ProviderStatus.REMOTE_SUCCESS for item in tushare_attempts)

    assert any(
        gap.reason == DataGapReason.CREDENTIAL_MISSING and "TUSHARE_TOKEN" in gap.root_cause
        for gap in pack.data_gaps
    )

    assert any(
        attempt.provider == "baidu_kline" and attempt.status == ProviderStatus.REMOTE_SUCCESS
        for attempt in pack.attempts
    )
    assert pack.readiness.status.value != "blocked"
    assert pack.readiness.coverage["group:cn_a_market_kline"] == "1/1"

    success_attempts = [attempt for attempt in pack.attempts if attempt.status == ProviderStatus.REMOTE_SUCCESS]
    assert success_attempts, "expected default-source success attempts"
    assert all(item.provider != "tushare_kline_fallback" for item in success_attempts)
