from __future__ import annotations

from urllib.parse import urlsplit

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
from claw_trade.data_gateway.packs.lockup import normalize_lockup_results
from claw_trade.data_gateway.providers.lockup import DefaultLockupAdapter, build_default_lockup_adapters


def _request() -> PackRequest:
    return PackRequest(
        run_id="run-lockup-unit",
        call_id="call-lockup-unit",
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


def _spec(coverage_group: str, source_role: SourceRole = SourceRole.MARKET_DATA) -> ProviderCallSpec:
    return ProviderCallSpec(
        call_key=f"lockup:{coverage_group}",
        provider="provider",
        adapter_id=f"lockup.{coverage_group}",
        provider_kind=ProviderKind.PROJECT_EXTENSION,
        provider_config_version="cfg",
        endpoint=coverage_group,
        source_role=source_role,
        market=Market.CN_A,
        domain=PackDomain.LOCKUP,
        required=False,
        attempt_required=True,
        coverage_group=coverage_group,
        coverage_quorum=1,
        params={},
        cache_ttl_seconds=300,
        license_policy_id="personal_research",
        expected_schema_id="cn_a.lockup.v1",
        priority=0,
        priority_source=PrioritySource.SYSTEM_DEFAULT,
        user_preferred=False,
    )


def test_lockup_adapter_requires_unlock_fields() -> None:
    adapter = DefaultLockupAdapter(
        adapter_id="lockup.eastmoney.unlock.cn_a",
        provider_id="eastmoney_datacenter",
        source_role=SourceRole.MARKET_DATA,
        endpoint="unlock_market",
        expected_schema_id="cn_a.lockup.unlock.market.v1",
        provider_config_version="cfg",
        rate_limit_policy_id="eastmoney.unlock",
        cache_ttl_seconds=900,
        required=True,
        attempt_required=True,
        coverage_group="cn_a_lockup_unlock",
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
                "payload": {"rows": [{"as_of": "2026-05-21", "source": "eastmoney_unlock"}]},
                "provider_request_id": "req-lockup-missing",
                "source_url": "https://eastmoney.example/unlock",
                "row_count": 1,
            },
        )(),
    )
    assert result.status == ProviderStatus.FIELD_MISSING
    assert "unlock_date" in result.missing_fields
    assert "shares" in result.missing_fields


def test_lockup_normalizer_keeps_official_ref_and_no_forced_chip_conclusion() -> None:
    result = ProviderResult(
        spec=_spec("cn_a_lockup_unlock", SourceRole.OFFICIAL_ORIGINAL),
        status=ProviderStatus.REMOTE_SUCCESS,
        request_id="req-1",
        requested_at="2026-05-22T00:00:00+00:00",
        latency_ms=1,
        source_role=SourceRole.OFFICIAL_ORIGINAL,
        freshness=FreshnessStatus.FRESH_REMOTE,
        license_note="ok",
        raw_ref="raw://1",
        normalized_ref="norm://1",
        rows=(
            {"unlock_date": "2026-06-01", "shares": "1000000", "as_of": "2026-05-21", "title": "限售解禁公告"},
        ),
        row_count=1,
        cache_receipt=None,
        attempt=type("AttemptLike", (), {"attempt_id": "a1"})(),  # type: ignore[arg-type]
    )

    normalized = normalize_lockup_results([result])

    assert normalized["record_count"] == 1
    assert normalized["unlock_points"] == 1
    assert normalized["records"][0]["official_ref"] == "限售解禁公告"
    assert all("conclusion" not in row for row in normalized["records"])


def test_lockup_provider_source_claim_matches_official_and_datacenter_domains(monkeypatch) -> None:
    import claw_trade.data_gateway.providers.lockup as lockup_mod

    adapters = build_default_lockup_adapters(provider_config_version="cfg-lockup-source-claim")
    request = _request()
    called_get: list[str] = []
    called_post: list[str] = []

    class _Response:
        def __init__(self, *, json_body) -> None:
            self._json_body = json_body

        def raise_for_status(self) -> None:
            return None

        def json(self):
            return self._json_body

    def _fake_post(url, data=None, headers=None, timeout=None):  # noqa: ANN001
        del data, headers, timeout
        called_post.append(url)
        return _Response(
            json_body={
                "announcements": [
                    {
                        "announcementTitle": "官方公告",
                        "announcementTime": "2026-05-20",
                    }
                ]
            }
        )

    def _fake_get(url, headers=None, timeout=None):  # noqa: ANN001
        del headers, timeout
        called_get.append(url)
        if "datacenter-web.eastmoney.com" in url:
            return _Response(
                json_body={
                    "result": {
                        "data": [
                            {
                                "TRADE_DATE": "2026-05-21",
                                "LIFT_DATE": "2026-06-01",
                                "LIFT_NUM": "1000000",
                                "END_DATE": "2026-04-30",
                                "HOLDER_NUM": "100000",
                                "EX_DIVIDEND_DATE": "2026-05-15",
                                "ASSIGN_DESC": "10派20",
                            }
                        ]
                    }
                }
            )
        if "push2his.eastmoney.com" in url:
            return _Response(json_body={"data": {"klines": ["2026-05-21,12000000,0"]}})
        raise AssertionError(f"unexpected GET url: {url}")

    monkeypatch.setattr(lockup_mod.requests, "post", _fake_post)
    monkeypatch.setattr(lockup_mod.requests, "get", _fake_get)

    target_adapter_ids = {
        "lockup.cninfo.unlock.official.cn_a": ("cninfo", SourceRole.OFFICIAL_ORIGINAL, "www.cninfo.com.cn"),
        "lockup.eastmoney.unlock.cn_a": ("eastmoney_datacenter", SourceRole.MARKET_DATA, "datacenter-web.eastmoney.com"),
        "lockup.cninfo.shareholder.official.cn_a": ("cninfo", SourceRole.OFFICIAL_ORIGINAL, "www.cninfo.com.cn"),
        "lockup.eastmoney.shareholder.cn_a": ("eastmoney_datacenter", SourceRole.FUNDAMENTAL_DATA, "datacenter-web.eastmoney.com"),
        "lockup.cninfo.dividend.official.cn_a": ("cninfo", SourceRole.OFFICIAL_ORIGINAL, "www.cninfo.com.cn"),
        "lockup.eastmoney.dividend.cn_a": ("eastmoney_datacenter", SourceRole.FUNDAMENTAL_DATA, "datacenter-web.eastmoney.com"),
    }

    for adapter in adapters:
        if adapter.adapter_id not in target_adapter_ids:
            continue
        provider_id, source_role, expected_host = target_adapter_ids[adapter.adapter_id]
        spec = adapter.build_call_specs(request)[0]
        fetch = adapter.fetch(spec, request)

        assert adapter.provider_id == provider_id
        assert adapter.source_role == source_role
        assert urlsplit(fetch.source_url).netloc == expected_host

    assert any("cninfo.com.cn" in url for url in called_post)
    assert any("datacenter-web.eastmoney.com" in url for url in called_get)


def test_lockup_tushare_120d_flow_fetches_and_normalizes_when_configured(monkeypatch) -> None:
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
            assert start_date == "20251123"
            assert end_date == "20260522"
            return _Frame()

    monkeypatch.setattr(
        "claw_trade.data_gateway.providers.lockup.create_tushare_pro",
        lambda *, token, env: _Pro(),
    )

    adapter = next(
        item
        for item in build_default_lockup_adapters(provider_config_version="cfg", env={"TUSHARE_TOKEN": "token"})
        if item.adapter_id == "lockup.tushare.flow120d.cn_a"
    )
    spec = adapter.build_call_specs(_request())[0]
    fetch = adapter.fetch(spec, _request())
    result = adapter.normalize(spec, fetch)

    assert fetch.source_url == "https://api.tushare.pro#moneyflow"
    assert result.status == ProviderStatus.REMOTE_SUCCESS
    assert result.rows[0]["as_of"] == "20260521"
    assert result.rows[0]["amount"] == "200000.0"
