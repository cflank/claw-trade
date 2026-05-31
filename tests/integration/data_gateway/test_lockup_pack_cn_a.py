from __future__ import annotations

from claw_trade.data_gateway.models import FreshnessPolicy, Market, PackDomain, PackRequest, RunProviderPlan, SourceRole
from claw_trade.data_gateway.packs.lockup import LockupPackBuilder
from claw_trade.data_gateway.providers.lockup import build_default_lockup_adapters
from tests.fakes.data_gateway_in_memory import build_gate_controlled_executor


def _request() -> PackRequest:
    return PackRequest(
        run_id="run-lockup-cn-a",
        call_id="call-lockup-cn-a",
        worker_id="lockup_watcher",
        market=Market.CN_A,
        domain=PackDomain.LOCKUP,
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
        provider_config_version="cfg-lockup-cn-a",
        market=request.market,
        ticker=request.ticker,
        domains=(PackDomain.LOCKUP,),
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


def test_lockup_pack_cn_a_covers_all_groups_and_keeps_chart_root_cause(monkeypatch) -> None:
    request = _request()
    adapters = build_default_lockup_adapters(provider_config_version="cfg-lockup-cn-a", env={})
    plan = _plan(request, adapters)
    groups = {spec.coverage_group for spec in plan.call_specs}
    assert groups == {
        "cn_a_lockup_unlock",
        "cn_a_lockup_shareholder_count",
        "cn_a_lockup_block_trade",
        "cn_a_lockup_margin_financing",
        "cn_a_lockup_dividend",
        "cn_a_lockup_120d_flow",
    }

    monkeypatch.setattr(
        "claw_trade.data_gateway.providers.lockup._fetch_cninfo_unlock_official",
        lambda params: (
            (
                {
                    "unlock_date": "2026-06-01",
                    "shares": "1000000",
                    "as_of": "2026-05-21",
                    "title": "限售解禁公告",
                    "source": "cninfo_unlock",
                },
            ),
            "https://cninfo.example/unlock",
        ),
    )
    monkeypatch.setattr(
        "claw_trade.data_gateway.providers.lockup._fetch_cn_a_unlock",
        lambda params, role: (
            (
                {
                    "unlock_date": "2026-06-01",
                    "shares": "1000000",
                    "as_of": "2026-05-21",
                    "source": "eastmoney_unlock",
                },
            ),
            "https://eastmoney.example/unlock",
        )
        if role != SourceRole.OFFICIAL_ORIGINAL
        else (
            (
                {
                    "unlock_date": "2026-06-01",
                    "shares": "1000000",
                    "as_of": "2026-05-21",
                    "title": "限售解禁公告",
                    "source": "cninfo_unlock",
                },
            ),
            "https://cninfo.example/unlock",
        ),
    )
    monkeypatch.setattr(
        "claw_trade.data_gateway.providers.lockup._fetch_cn_a_shareholder_count",
        lambda params, source_role: (
            (
                {"as_of": "2026-03-31", "shareholder_count": "98000", "source": "eastmoney_shareholder"},
                {"as_of": "2026-04-30", "shareholder_count": "101000", "source": "eastmoney_shareholder"},
            ),
            "https://eastmoney.example/shareholder",
        ),
    )
    monkeypatch.setattr(
        "claw_trade.data_gateway.providers.lockup._fetch_cn_a_block_trade",
        lambda params: (({"as_of": "2026-05-21", "amount": "78000000", "source": "eastmoney_block_trade"},), "https://eastmoney.example/block"),
    )
    monkeypatch.setattr(
        "claw_trade.data_gateway.providers.lockup._fetch_cn_a_margin_financing",
        lambda params: (({"as_of": "2026-05-21", "amount": "64000000", "source": "eastmoney_margin"},), "https://eastmoney.example/margin"),
    )
    monkeypatch.setattr(
        "claw_trade.data_gateway.providers.lockup._fetch_cn_a_dividend",
        lambda params, source_role: (({"as_of": "2026-05-15", "dividend_plan": "10派20", "source": "eastmoney_dividend"},), "https://eastmoney.example/dividend"),
    )
    monkeypatch.setattr(
        "claw_trade.data_gateway.providers.lockup._fetch_cn_a_120d_flow",
        lambda params: (({"as_of": "2026-05-21", "amount": "12000000", "source": "eastmoney_flow120d"},), "https://eastmoney.example/flow120d"),
    )

    pack = LockupPackBuilder().build(
        request=request,
        run_plan=plan,
        adapters_by_id={adapter.adapter_id: adapter for adapter in adapters},
        provider_execution_helper=_helper(),
    )

    assert pack.readiness.status.value in {"partial", "ready"}
    assert len(pack.attempts) == len(plan.call_specs)
    assert set(attempt.coverage_group for attempt in pack.attempts) == groups
    success_attempts = [attempt for attempt in pack.attempts if attempt.status.value == "remote_success"]
    assert len(pack.raw_refs) >= len(success_attempts)
    assert len(pack.normalized_refs) >= len(success_attempts)
    assert all(ref.startswith("mongo://openbb_raw_payloads/") for ref in pack.raw_refs)
    assert all(ref.startswith("mongo://openbb_normalized/") for ref in pack.normalized_refs)
    assert "不直接推导确定买卖意图" in pack.reader_brief_md
    assert any(asset.status.value == "insufficient" for asset in pack.chart_assets)
    assert any("缺少图表资产引用" in (asset.root_cause or "") for asset in pack.chart_assets)


def test_lockup_pack_cn_a_official_failure_for_official_groups_forces_partial(monkeypatch) -> None:
    request = _request()
    adapters = build_default_lockup_adapters(provider_config_version="cfg-lockup-cn-a", env={})
    plan = _plan(request, adapters)

    monkeypatch.setattr(
        "claw_trade.data_gateway.providers.lockup._fetch_cn_a_unlock",
        lambda params, source_role: (
            (_ for _ in ()).throw(RuntimeError("cninfo unlock timeout"))
            if source_role == SourceRole.OFFICIAL_ORIGINAL
            else (
                (
                    {
                        "unlock_date": "2026-06-01",
                        "shares": "1000000",
                        "as_of": "2026-05-21",
                        "source": "eastmoney_unlock",
                    },
                ),
                "https://eastmoney.example/unlock",
            )
        ),
    )
    monkeypatch.setattr(
        "claw_trade.data_gateway.providers.lockup._fetch_cn_a_shareholder_count",
        lambda params, source_role: (
            (_ for _ in ()).throw(RuntimeError("cninfo shareholder timeout"))
            if source_role == SourceRole.OFFICIAL_ORIGINAL
            else (
                (
                    {"as_of": "2026-03-31", "shareholder_count": "98000", "source": "eastmoney_shareholder"},
                    {"as_of": "2026-04-30", "shareholder_count": "101000", "source": "eastmoney_shareholder"},
                ),
                "https://eastmoney.example/shareholder",
            )
        ),
    )
    monkeypatch.setattr(
        "claw_trade.data_gateway.providers.lockup._fetch_cn_a_dividend",
        lambda params, source_role: (
            (_ for _ in ()).throw(RuntimeError("cninfo dividend timeout"))
            if source_role == SourceRole.OFFICIAL_ORIGINAL
            else (({"as_of": "2026-05-15", "dividend_plan": "10派20", "source": "eastmoney_dividend"},), "https://eastmoney.example/dividend")
        ),
    )
    monkeypatch.setattr(
        "claw_trade.data_gateway.providers.lockup._fetch_cn_a_block_trade",
        lambda params: (({"as_of": "2026-05-21", "amount": "78000000", "source": "eastmoney_block_trade"},), "https://eastmoney.example/block"),
    )
    monkeypatch.setattr(
        "claw_trade.data_gateway.providers.lockup._fetch_cn_a_margin_financing",
        lambda params: (({"as_of": "2026-05-21", "amount": "64000000", "source": "eastmoney_margin"},), "https://eastmoney.example/margin"),
    )
    monkeypatch.setattr(
        "claw_trade.data_gateway.providers.lockup._fetch_cn_a_120d_flow",
        lambda params: (({"as_of": "2026-05-21", "amount": "12000000", "source": "eastmoney_flow120d"},), "https://eastmoney.example/flow120d"),
    )

    pack = LockupPackBuilder().build(
        request=request,
        run_plan=plan,
        adapters_by_id={adapter.adapter_id: adapter for adapter in adapters},
        provider_execution_helper=_helper(),
    )

    assert pack.readiness.status.value == "partial"
    assert "official_original failed for lockup groups" in (pack.readiness.root_cause or "")
    official_attempts = [item for item in pack.attempts if item.source_role == SourceRole.OFFICIAL_ORIGINAL]
    assert official_attempts
    assert all(item.status.value == "remote_error" for item in official_attempts)
    assert any("cninfo unlock timeout" in (gap.root_cause or "") for gap in pack.data_gaps)
