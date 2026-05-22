from __future__ import annotations

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
from claw_trade.data_gateway.providers.hot_money import DefaultHotMoneyAdapter


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
