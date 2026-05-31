from __future__ import annotations

from claw_trade.data_gateway.models import FreshnessPolicy, Market, PackDomain, PackRequest, RunProviderPlan
from claw_trade.data_gateway.packs.hot_money import HotMoneyPackBuilder
from claw_trade.data_gateway.providers.hot_money import build_default_hot_money_adapters
from tests.fakes.data_gateway_in_memory import build_gate_controlled_executor


def _request() -> PackRequest:
    return PackRequest(
        run_id="run-hot-money-cn-a",
        call_id="call-hot-money-cn-a",
        worker_id="hot_money_tracker",
        market=Market.CN_A,
        domain=PackDomain.HOT_MONEY,
        ticker="600519.SH",
        company_name="贵州茅台",
        start_date="2026-05-01",
        end_date="2026-05-22",
        current_date="2026-05-22",
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
        provider_config_version="cfg-hot-money-cn-a",
        market=request.market,
        ticker=request.ticker,
        domains=(PackDomain.HOT_MONEY,),
        call_specs=ordered,
        shared_call_keys=tuple(item.call_key for item in ordered),
        cache_keys=tuple(f"cache:{item.adapter_id}" for item in ordered),
        rate_limit_plan=(),
        initial_gaps=(),
        generated_at="2026-05-22T00:00:00+00:00",
        remote_prefetch_allowed=False,
    )


def _helper():
    return build_gate_controlled_executor()


def test_hot_money_pack_cn_a_covers_all_groups_and_uses_openbb_evidence_chain(monkeypatch) -> None:
    request = _request()
    adapters = build_default_hot_money_adapters(provider_config_version="cfg-hot-money-cn-a", env={})
    plan = _plan(request, adapters)
    groups = {spec.coverage_group for spec in plan.call_specs}
    assert groups == {
        "cn_a_hot_money_dragon_tiger",
        "cn_a_hot_money_fund_flow",
        "cn_a_hot_money_northbound",
        "cn_a_hot_money_sector_flow",
    }

    monkeypatch.setattr(
        "claw_trade.data_gateway.providers.hot_money._fetch_cn_a_dragon_tiger",
        lambda params: (
            (
                {"as_of": "2026-05-21", "name": "机构席位A", "amount": "12000000", "unit": "CNY"},
            ),
            "https://eastmoney.example/dragon_tiger",
        ),
    )
    monkeypatch.setattr(
        "claw_trade.data_gateway.providers.hot_money._fetch_cn_a_fund_flow",
        lambda params: (
            (
                {"as_of": "2026-05-20", "name": "main_net_inflow", "amount": "23000000", "unit": "CNY"},
                {"as_of": "2026-05-21", "name": "main_net_inflow", "amount": "26000000", "unit": "CNY"},
            ),
            "https://eastmoney.example/fund_flow",
        ),
    )
    monkeypatch.setattr(
        "claw_trade.data_gateway.providers.hot_money._fetch_cn_a_northbound",
        lambda params: (
            (
                {"as_of": "2026-05-21", "name": "northbound_net_flow", "amount": "110000000", "unit": "CNY"},
            ),
            "https://ths.example/northbound",
        ),
    )
    monkeypatch.setattr(
        "claw_trade.data_gateway.providers.hot_money._fetch_cn_a_sector_flow",
        lambda params: (
            (
                {"as_of": None, "name": "白酒", "amount": "45000000", "unit": "CNY"},
            ),
            "https://eastmoney.example/sector_flow",
        ),
    )
    pack = HotMoneyPackBuilder().build(
        request=request,
        run_plan=plan,
        adapters_by_id={adapter.adapter_id: adapter for adapter in adapters},
        provider_execution_helper=_helper(),
    )

    assert pack.readiness.status.value in {"partial", "ready"}
    assert len(pack.attempts) == len(plan.call_specs)
    assert set(attempt.coverage_group for attempt in pack.attempts) == groups
    assert len(pack.raw_refs) == 4
    assert len(pack.normalized_refs) == 4
    tushare_attempts = [attempt for attempt in pack.attempts if attempt.provider.startswith("tushare")]
    assert len(tushare_attempts) == 5
    assert all(attempt.status.value == "credential_missing" for attempt in tushare_attempts)
    assert all(ref.startswith("mongo://openbb_raw_payloads/") for ref in pack.raw_refs)
    assert all(ref.startswith("mongo://openbb_normalized/") for ref in pack.normalized_refs)
    assert "不等于确定买卖意图" in pack.reader_brief_md
    assert pack.chart_assets[0].status.value == "insufficient"
    assert "缺少图表资产引用" in (pack.chart_assets[0].root_cause or "")
