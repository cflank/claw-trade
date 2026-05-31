from __future__ import annotations

import sys
from types import SimpleNamespace

from claw_trade.data_gateway.models import (
    FreshnessPolicy,
    FreshnessStatus,
    Market,
    PackDomain,
    PackRequest,
    PrioritySource,
    ProviderCallSpec,
    ProviderKind,
    ProviderResult,
    ProviderStatus,
    SourceRole,
)
from claw_trade.data_gateway.packs.hot_money import normalize_hot_money_results
from claw_trade.data_gateway.providers.hot_money import DefaultHotMoneyAdapter, build_default_hot_money_adapters


def _request() -> PackRequest:
    return PackRequest(
        run_id="run-hot-money-unit",
        call_id="call-hot-money-unit",
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


def _spec(coverage_group: str) -> ProviderCallSpec:
    return ProviderCallSpec(
        call_key=f"hot_money:{coverage_group}",
        provider="provider",
        adapter_id=f"hot_money.{coverage_group}",
        provider_kind=ProviderKind.PROJECT_EXTENSION,
        provider_config_version="cfg",
        endpoint=coverage_group,
        source_role=SourceRole.MARKET_DATA,
        market=Market.CN_A,
        domain=PackDomain.HOT_MONEY,
        required=False,
        attempt_required=True,
        coverage_group=coverage_group,
        coverage_quorum=1,
        params={},
        cache_ttl_seconds=300,
        license_policy_id="personal_research",
        expected_schema_id="cn_a.hot_money.v1",
        priority=0,
        priority_source=PrioritySource.SYSTEM_DEFAULT,
        user_preferred=False,
    )


def test_hot_money_adapter_requires_as_of_and_amount_for_fund_flow() -> None:
    adapter = DefaultHotMoneyAdapter(
        adapter_id="hot_money.eastmoney.fund_flow.cn_a",
        provider_id="eastmoney_push2his",
        source_role=SourceRole.MARKET_DATA,
        endpoint="fund_flow",
        expected_schema_id="cn_a.hot_money.fund_flow.v1",
        provider_config_version="cfg",
        rate_limit_policy_id="eastmoney.fund_flow",
        cache_ttl_seconds=600,
        required=True,
        attempt_required=True,
        coverage_group="cn_a_hot_money_fund_flow",
        coverage_quorum=1,
        priority=20,
    )
    spec = adapter.build_call_specs(_request())[0]
    result = adapter.normalize(
        spec,
        type(
            "FetchLike",
            (),
            {
                "payload": {"rows": [{"name": "main_net_inflow", "unit": "CNY"}]},
                "provider_request_id": "req-fund-missing",
                "source_url": "https://eastmoney.example/fund_flow",
                "row_count": 1,
            },
        )(),
    )
    assert result.status == ProviderStatus.FIELD_MISSING
    assert "as_of" in result.missing_fields
    assert "amount" in result.missing_fields


def test_hot_money_normalizer_keeps_metric_rows_as_facts_not_trade_intent() -> None:
    result = ProviderResult(
        spec=_spec("cn_a_hot_money_fund_flow"),
        status=ProviderStatus.REMOTE_SUCCESS,
        request_id="req-1",
        requested_at="2026-05-22T00:00:00+00:00",
        latency_ms=1,
        source_role=SourceRole.MARKET_DATA,
        freshness=FreshnessStatus.FRESH_REMOTE,
        license_note="ok",
        raw_ref="raw://1",
        normalized_ref="norm://1",
        rows=(
            {"as_of": "2026-05-20", "name": "main_net_inflow", "amount": "23000000", "unit": "CNY"},
            {"as_of": "2026-05-21", "name": "main_net_inflow", "amount": "26000000", "unit": "CNY"},
        ),
        row_count=2,
        cache_receipt=None,
        attempt=type("AttemptLike", (), {"attempt_id": "a1"})(),  # type: ignore[arg-type]
    )

    normalized = normalize_hot_money_results([result])

    assert normalized["record_count"] == 2
    assert normalized["dated_amount_points"] == 2
    assert all("intent" not in row for row in normalized["records"])


def test_hot_money_northbound_source_claim_matches_ths_hsgt_url(monkeypatch) -> None:
    adapter = DefaultHotMoneyAdapter(
        adapter_id="hot_money.ths.northbound.cn_a",
        provider_id="ths_hsgt",
        source_role=SourceRole.MARKET_DATA,
        endpoint="northbound",
        expected_schema_id="cn_a.hot_money.northbound.v1",
        provider_config_version="cfg",
        rate_limit_policy_id="ths.northbound",
        cache_ttl_seconds=900,
        required=False,
        attempt_required=True,
        coverage_group="cn_a_hot_money_northbound",
        coverage_quorum=1,
        priority=30,
    )
    request = _request()
    spec = adapter.build_call_specs(request)[0]

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "time": ["09:31", "09:32"],
                "hgt": ["10.0", "12.0"],
                "sgt": ["5.0", "6.0"],
            }

    monkeypatch.setattr(
        "claw_trade.data_gateway.providers.hot_money.requests.get",
        lambda url, headers=None, timeout=None: _Response(),
    )
    fetch = adapter.fetch(spec, request)

    assert adapter.provider_id == "ths_hsgt"
    assert adapter.capabilities()[0].rate_limit_policy_id == "ths.northbound"
    assert fetch.source_url == "https://data.hexin.cn/market/hsgtApi/method/dayChart/"
    assert fetch.row_count == 2
    rows = fetch.payload["rows"]
    assert rows[0]["amount"] == "15.0"


def test_hot_money_tushare_moneyflow_fetches_and_normalizes_when_configured(monkeypatch) -> None:
    class _Frame:
        def to_dict(self, orient: str):  # noqa: ANN001
            assert orient == "records"
            return [
                {
                    "trade_date": "20260521",
                    "buy_elg_amount": "100",
                    "buy_lg_amount": "10",
                    "buy_md_amount": "0",
                    "buy_sm_amount": "0",
                    "sell_elg_amount": "70",
                    "sell_lg_amount": "20",
                    "sell_md_amount": "0",
                    "sell_sm_amount": "0",
                }
            ]

    class _Pro:
        def moneyflow(self, *, ts_code, start_date, end_date):  # noqa: ANN001
            assert ts_code == "600519.SH"
            assert start_date == "20260501"
            assert end_date == "20260522"
            return _Frame()

    monkeypatch.setattr(
        "claw_trade.data_gateway.providers.hot_money.create_tushare_pro",
        lambda *, token, env: _Pro(),
    )

    adapter = next(
        item
        for item in build_default_hot_money_adapters(provider_config_version="cfg", env={"TUSHARE_TOKEN": "token"})
        if item.adapter_id == "hot_money.tushare.moneyflow.cn_a"
    )
    spec = adapter.build_call_specs(_request())[0]
    fetch = adapter.fetch(spec, _request())
    result = adapter.normalize(spec, fetch)

    assert fetch.source_url == "https://api.tushare.pro#moneyflow"
    assert result.status == ProviderStatus.REMOTE_SUCCESS
    assert result.rows[0]["as_of"] == "20260521"
    assert result.rows[0]["amount"] == "200000.0"


def test_hot_money_akshare_individual_fund_flow_fetches_and_normalizes(monkeypatch) -> None:
    class _Frame:
        def to_dict(self, orient: str):  # noqa: ANN001
            assert orient == "records"
            return [
                {
                    "日期": "2026-05-21",
                    "主力净流入-净额": "23000000",
                    "收盘价": "1660.0",
                }
            ]

    captured: dict[str, str] = {}

    def _fake_fund_flow(*, stock: str, market: str) -> _Frame:
        captured["stock"] = stock
        captured["market"] = market
        return _Frame()

    monkeypatch.setitem(sys.modules, "akshare", SimpleNamespace(stock_individual_fund_flow=_fake_fund_flow))

    adapter = next(
        item
        for item in build_default_hot_money_adapters(provider_config_version="cfg", env={})
        if item.adapter_id == "hot_money.akshare.individual_fund_flow.cn_a"
    )
    spec = adapter.build_call_specs(_request())[0]
    fetch = adapter.fetch(spec, _request())
    result = adapter.normalize(spec, fetch)

    assert captured == {"stock": "600519", "market": "sh"}
    assert fetch.source_url == "https://akshare.akfamily.xyz/data/stock/stock.html"
    assert result.status == ProviderStatus.REMOTE_SUCCESS
    assert result.rows[0]["as_of"] == "2026-05-21"
    assert result.rows[0]["amount"] == "23000000"


def test_hot_money_akshare_rank_and_sector_flow_fetches_and_normalizes(monkeypatch) -> None:
    class _Frame:
        def __init__(self, rows):  # noqa: ANN001
            self._rows = rows

        def to_dict(self, orient: str):  # noqa: ANN001
            assert orient == "records"
            return self._rows

    calls: list[tuple[str, str]] = []

    def _fake_rank(*, indicator: str) -> _Frame:
        calls.append(("rank", indicator))
        return _Frame([{"名称": "贵州茅台", "今日主力净流入-净额": "1000"}])

    def _fake_sector(*, indicator: str, sector_type: str) -> _Frame:
        calls.append((indicator, sector_type))
        return _Frame([{"名称": sector_type, "今日主力净流入-净额": "2000"}])

    monkeypatch.setitem(
        sys.modules,
        "akshare",
        SimpleNamespace(
            stock_individual_fund_flow_rank=_fake_rank,
            stock_sector_fund_flow_rank=_fake_sector,
        ),
    )

    request = _request()
    adapters = {
        item.adapter_id: item
        for item in build_default_hot_money_adapters(provider_config_version="cfg", env={})
        if item.adapter_id.startswith("hot_money.akshare.")
    }

    for adapter_id in (
        "hot_money.akshare.individual_fund_flow_rank.cn_a",
        "hot_money.akshare.sector_fund_flow_industry.cn_a",
        "hot_money.akshare.sector_fund_flow_concept.cn_a",
    ):
        adapter = adapters[adapter_id]
        spec = adapter.build_call_specs(request)[0]
        fetch = adapter.fetch(spec, request)
        result = adapter.normalize(spec, fetch)
        assert result.status == ProviderStatus.REMOTE_SUCCESS
        assert result.rows[0]["as_of"] == "2026-05-22"

    assert ("rank", "今日") in calls
    assert ("今日", "行业资金流") in calls
    assert ("今日", "概念资金流") in calls
