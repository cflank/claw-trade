from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from claw_trade.data_gateway.analysis.crypto_lens import CryptoLensAnalysisEvidenceStore
from claw_trade.data_gateway.analysis.crypto_lens import adapter as crypto_lens_adapter
from claw_trade.data_gateway.models import (
    DataGapReason,
    FreshnessPolicy,
    FreshnessStatus,
    Market,
    PackDomain,
    PackRequest,
    ProviderStatus,
    RunProviderPlan,
)
from claw_trade.data_gateway.packs.market import MarketPackBuilder, make_provider_result
from claw_trade.data_gateway.providers.market_adapters import build_default_market_adapters


class _CryptoLensEvidenceCollection:
    name = "crypto_lens_analysis_evidence"

    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}

    def insert_one(self, document: dict[str, Any]) -> None:
        self.docs[str(document["_id"])] = dict(document)


def test_market_pack_crypto_keeps_provider_priority_and_chart_root_cause() -> None:
    request = PackRequest(
        run_id="run-crypto-1",
        call_id="call-crypto-1",
        worker_id="market_analyst",
        market=Market.CRYPTO,
        domain=PackDomain.MARKET,
        ticker="BTC",
        company_name="Bitcoin",
        start_date="2026-04-01",
        end_date="2026-05-17",
        current_date="2026-05-17",
        currency="USDT",
        profile="CRYPTO",
        freshness_policy=FreshnessPolicy(max_age_seconds=300),
    )
    adapters = build_default_market_adapters(provider_config_version="cfg-crypto", env={})
    crypto_adapters = tuple(item for item in adapters if getattr(item, "market", None) == Market.CRYPTO)
    specs = tuple(
        spec
        for adapter in crypto_adapters
        for spec in adapter.build_call_specs(request)
    )
    specs_by_endpoint = {spec.endpoint: spec for spec in specs}
    assert set(specs_by_endpoint.keys()) == {
        "crypto_price_historical",
        "futures_oi_funding",
        "liquidation_heatmap",
        "onchain_signals",
        "macro_regime",
        "catalyst_events",
        "ahr999_index",
    }
    assert specs_by_endpoint["crypto_price_historical"].provider == "openbb_yfinance"
    assert specs_by_endpoint["crypto_price_historical"].params["symbol"] == "BTCUSDT"
    assert all(spec.provider_config_version == "cfg-crypto" for spec in specs)
    assert all(spec.coverage_group for spec in specs)
    assert all(spec.coverage_quorum == 1 for spec in specs)
    assert all(spec.raw_export_policy == "metadata_only" for spec in specs)

    run_plan = RunProviderPlan(
        run_id=request.run_id,
        provider_config_version="cfg-crypto",
        market=request.market,
        ticker=request.ticker,
        domains=(PackDomain.MARKET,),
        call_specs=specs,
        shared_call_keys=tuple(spec.call_key for spec in specs),
        cache_keys=(),
        rate_limit_plan=(),
        initial_gaps=(),
        generated_at="2026-05-17T00:00:00+00:00",
        remote_prefetch_allowed=False,
    )

    crypto_rows = [
        {
            "date": f"2026-05-{day:02d}",
            "open": 80000 + day,
            "high": 81000 + day,
            "low": 79000 + day,
            "close": 80500 + day,
            "volume": 1000 + day,
            "currency": "USDT",
            "timezone": "UTC",
        }
        for day in range(1, 28)
    ]
    yfinance_result = make_provider_result(
        request=request,
        spec=specs_by_endpoint["crypto_price_historical"],
        status=ProviderStatus.REMOTE_SUCCESS,
        rows=tuple(crypto_rows),
        freshness=FreshnessStatus.FRESH_REMOTE,
        raw_ref="raw://crypto-openbb-yfinance",
        normalized_ref="norm://crypto-openbb-yfinance",
    )
    derivative_gap_result = make_provider_result(
        request=request,
        spec=specs_by_endpoint["futures_oi_funding"],
        status=ProviderStatus.CREDENTIAL_MISSING,
        rows=(),
        freshness=FreshnessStatus.NOT_FETCHED,
        error_code="credential_missing",
        error_message="missing credential keys: COINGLASS_API_KEY",
    )
    liquidation_gap_result = make_provider_result(
        request=request,
        spec=specs_by_endpoint["liquidation_heatmap"],
        status=ProviderStatus.REMOTE_ERROR,
        rows=(),
        freshness=FreshnessStatus.NOT_FETCHED,
        error_code="RuntimeError",
        error_message="coinglass liquidation upstream timeout",
    )
    onchain_gap_result = make_provider_result(
        request=request,
        spec=specs_by_endpoint["onchain_signals"],
        status=ProviderStatus.SKIPPED_NOT_CONFIGURED,
        rows=(),
        freshness=FreshnessStatus.NOT_FETCHED,
        error_code="adapter_not_configured",
        error_message="adapter project.crypto.onchain not configured",
    )

    pack = MarketPackBuilder().build(
        request=request,
        run_plan=run_plan,
        results=(
            yfinance_result,
            derivative_gap_result,
            liquidation_gap_result,
            onchain_gap_result,
        ),
    )

    assert pack.compact_facts["schema_id"] == "crypto.market.ohlcv.v1"
    assert pack.compact_facts["latest_close"] == 80527.0
    assert any(asset.root_cause for asset in pack.chart_assets if asset.status.value != "ready")
    assert "openbb_yfinance/crypto_price_historical：远端获取成功" in pack.reader_brief_md
    assert "coinglass/futures_oi_funding：缺少接口凭证" in pack.reader_brief_md
    assert "coinglass/liquidation_heatmap：远端请求失败" in pack.reader_brief_md
    assert "coinglass/onchain_signals：来源未配置" in pack.reader_brief_md
    assert "本次资料就绪度只代表已列明来源的价格历史、OpenBB 归一化资料、CryptoLens 指标分析和图表资产" in pack.reader_brief_md
    assert "资金费率、OI、多空比、清算、链上、宏观或 AHR999" in pack.reader_brief_md
    assert "不得写成已验证事实" in pack.reader_brief_md
    assert "BB/CoinGlass" not in pack.reader_brief_md
    assert "### CryptoLens 指标分析" in pack.reader_brief_md
    assert "衍生品域缺失" in pack.reader_brief_md
    assert "清算图域缺失" in pack.reader_brief_md
    assert "derivatives.funding" in pack.reader_brief_md
    assert "derivatives.oi" in pack.reader_brief_md
    assert "derivatives.long_short_ratio" in pack.reader_brief_md
    assert "derivatives.cvd_proxy" in pack.reader_brief_md
    assert "最大清算簇价格" not in pack.reader_brief_md
    assert "fresh_remote" not in pack.reader_brief_md
    assert "raw://" not in pack.reader_brief_md
    reasons = {gap.reason for gap in pack.data_gaps}
    assert DataGapReason.CREDENTIAL_MISSING in reasons
    assert DataGapReason.PROVIDER_UNAVAILABLE in reasons
    assert DataGapReason.SOURCE_NOT_CONFIGURED in reasons
    assert pack.audit_payload.analysis_evidence_refs == ()


def test_market_pack_crypto_renders_crypto_lens_sections_without_raw_or_decision_terms() -> None:
    request, run_plan, specs_by_endpoint = _crypto_request_plan()
    results = (
        make_provider_result(
            request=request,
            spec=specs_by_endpoint["crypto_price_historical"],
            status=ProviderStatus.REMOTE_SUCCESS,
            rows=tuple(_crypto_rows(40)),
            freshness=FreshnessStatus.FRESH_REMOTE,
            raw_ref="mongo://openbb_raw_payloads/raw-price",
            normalized_ref="mongo://openbb_normalized/norm-price",
        ),
        make_provider_result(
            request=request,
            spec=specs_by_endpoint["futures_oi_funding"],
            status=ProviderStatus.REMOTE_SUCCESS,
            rows=(
                {
                    "funding_rates": {
                        "latest": {
                            "stablecoin_margin_list": (
                                {"exchange": "Binance"},
                                {"exchange": "OKX", "funding_rate": "0.0002"},
                            )
                        },
                        "value": None,
                    },
                    "open_interest": {"latest": {"open_interest_usd": "1200000"}, "value": None},
                    "long_short_ratio": {"latest": {"global_account_long_short_ratio": "1.12"}, "value": None},
                    "cvd_proxy": {"cumulative_delta": -230.0},
                },
            ),
            freshness=FreshnessStatus.FRESH_REMOTE,
            raw_ref="mongo://openbb_raw_payloads/raw-derivatives",
            normalized_ref="mongo://openbb_normalized/norm-derivatives",
        ),
        make_provider_result(
            request=request,
            spec=specs_by_endpoint["liquidation_heatmap"],
            status=ProviderStatus.REMOTE_SUCCESS,
            rows=({"largest_clusters": ({"price": 69000.0, "liquidation_value": 2000000.0},)},),
            freshness=FreshnessStatus.FRESH_REMOTE,
            raw_ref="mongo://openbb_raw_payloads/raw-liquidation",
            normalized_ref="mongo://openbb_normalized/norm-liquidation",
        ),
        make_provider_result(
            request=request,
            spec=specs_by_endpoint["onchain_signals"],
            status=ProviderStatus.REMOTE_SUCCESS,
            rows=(
                {
                    "stablecoin_flows": {"aggregate_netflow": -3100000.0},
                    "btc_indicators": {
                        "active_addresses": {"latest": {"active_address_count": "925000"}},
                        "mvrv": {"value": "2.41"},
                        "sth_sopr": {"latest": {"sth_sopr": "1.018"}},
                        "lth_sopr": {"latest": {"lth_sopr": "1.072"}},
                        "nupl": {"latest": {"net_unpnl": "0.55"}},
                    },
                    "whale_activity": {"large_tx_count": 48, "large_tx_volume": 9800.0},
                },
            ),
            freshness=FreshnessStatus.FRESH_REMOTE,
            raw_ref="mongo://openbb_raw_payloads/raw-onchain",
            normalized_ref="mongo://openbb_normalized/norm-onchain",
        ),
    )

    pack = MarketPackBuilder().build(request=request, run_plan=run_plan, results=results)

    assert "价格/多周期结构" in pack.reader_brief_md
    assert "技术形态" in pack.reader_brief_md
    assert "维加斯通道" in pack.reader_brief_md
    assert "双线反转" in pack.reader_brief_md
    assert "FVG" in pack.reader_brief_md
    assert "KD(9,3,3)" in pack.reader_brief_md
    assert "TD Sequential" in pack.reader_brief_md
    assert "衍生品拥挤度" in pack.reader_brief_md
    assert "清算压力" in pack.reader_brief_md
    assert "funding=0.000200" in pack.reader_brief_md
    assert "OI=1200000.00" in pack.reader_brief_md
    assert "多空比=1.120" in pack.reader_brief_md
    assert "CVD代理=-230.00" in pack.reader_brief_md
    assert "最大清算簇价格 69000.00" in pack.reader_brief_md
    assert "活跃地址数=925000" in pack.reader_brief_md
    assert "MVRV=2.410" in pack.reader_brief_md
    assert "STH-SOPR=1.018" in pack.reader_brief_md
    assert "LTH-SOPR=1.072" in pack.reader_brief_md
    assert "NUPL=0.550" in pack.reader_brief_md
    assert "大额转账样本=48" in pack.reader_brief_md
    assert "derivatives.funding" not in pack.reader_brief_md
    assert "derivatives.oi" not in pack.reader_brief_md
    assert "derivatives.long_short_ratio" not in pack.reader_brief_md
    assert "derivatives.cvd_proxy" not in pack.reader_brief_md
    forbidden = ("BUY", "HOLD", "SELL", "仓位", "执行建议", "PM rating", "final_decision")
    assert all(token not in pack.reader_brief_md for token in forbidden)
    assert "mongo://openbb_raw_payloads" not in pack.reader_brief_md


def test_market_pack_crypto_marks_partial_onchain_signals() -> None:
    request, run_plan, specs_by_endpoint = _crypto_request_plan()
    results = (
        make_provider_result(
            request=request,
            spec=specs_by_endpoint["crypto_price_historical"],
            status=ProviderStatus.REMOTE_SUCCESS,
            rows=tuple(_crypto_rows(40)),
            freshness=FreshnessStatus.FRESH_REMOTE,
            raw_ref="mongo://openbb_raw_payloads/raw-price",
            normalized_ref="mongo://openbb_normalized/norm-price",
        ),
        make_provider_result(
            request=request,
            spec=specs_by_endpoint["onchain_signals"],
            status=ProviderStatus.REMOTE_SUCCESS,
            rows=(
                {
                    "whale_activity": {"large_tx_count": 816, "large_tx_volume": 21502107800.27},
                    "warnings": ("coinglass btc indicators rate limited",),
                },
            ),
            freshness=FreshnessStatus.FRESH_REMOTE,
            raw_ref="mongo://openbb_raw_payloads/raw-onchain",
            normalized_ref="mongo://openbb_normalized/norm-onchain",
        ),
    )

    pack = MarketPackBuilder().build(request=request, run_plan=run_plan, results=results)

    assert "链上=部分覆盖" in pack.reader_brief_md
    assert "链上指标：部分覆盖" in pack.reader_brief_md
    assert "大额转账样本=816" in pack.reader_brief_md
    assert "onchain.active_addresses" in pack.reader_brief_md
    assert "onchain.sth_sopr" in pack.reader_brief_md
    assert "onchain.nupl" in pack.reader_brief_md
    assert any(gap.field_path == "onchain.active_addresses" for gap in pack.data_gaps)


def test_market_pack_crypto_marks_short_ohlcv_as_insufficient_for_crypto_lens() -> None:
    request, run_plan, specs_by_endpoint = _crypto_request_plan()
    result = make_provider_result(
        request=request,
        spec=specs_by_endpoint["crypto_price_historical"],
        status=ProviderStatus.REMOTE_SUCCESS,
        rows=tuple(_crypto_rows(12)),
        freshness=FreshnessStatus.FRESH_REMOTE,
        raw_ref="mongo://openbb_raw_payloads/raw-price",
        normalized_ref="mongo://openbb_normalized/norm-price",
    )

    pack = MarketPackBuilder().build(request=request, run_plan=run_plan, results=(result,))

    assert "OHLCV 样本不足" in pack.reader_brief_md
    assert "ohlcv.rows" in pack.reader_brief_md
    assert any(gap.field_path == "ohlcv.rows" for gap in pack.data_gaps)


def test_market_pack_crypto_lens_failure_writes_failure_evidence_and_preserves_openbb_refs(monkeypatch) -> None:
    request, run_plan, specs_by_endpoint = _crypto_request_plan()
    result = make_provider_result(
        request=request,
        spec=specs_by_endpoint["crypto_price_historical"],
        status=ProviderStatus.REMOTE_SUCCESS,
        rows=tuple(_crypto_rows(40)),
        freshness=FreshnessStatus.FRESH_REMOTE,
        raw_ref="mongo://openbb_raw_payloads/raw-price",
        normalized_ref="mongo://openbb_normalized/norm-price",
    )

    collection = _CryptoLensEvidenceCollection()

    def _raise(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise RuntimeError("crypto lens boom")

    monkeypatch.setattr(crypto_lens_adapter, "analyze_crypto_lens", _raise)
    pack = MarketPackBuilder().build(
        request=request,
        run_plan=run_plan,
        results=(result,),
        crypto_lens_evidence_store=CryptoLensAnalysisEvidenceStore(collection),
    )

    assert pack.raw_refs == ("mongo://openbb_raw_payloads/raw-price",)
    assert pack.normalized_refs == ("mongo://openbb_normalized/norm-price",)
    assert pack.audit_payload.analysis_evidence_refs == (
        "mongo://crypto_lens_analysis_evidence/crypto_lens:run-crypto-1:call-crypto-1:failure",
    )
    [document] = collection.docs.values()
    assert document["status"] == "failure"
    assert document["failure_reason"] == "crypto lens boom"
    assert document["referenced_normalized_refs"] == ("mongo://openbb_normalized/norm-price",)
    assert document["analysis_result_ref"] is None
    assert document["failure_result_ref"].startswith("crypto_lens_analysis_failure://")
    assert "CryptoLens 分析失败：crypto lens boom" in pack.reader_brief_md
    assert "未生成" in pack.reader_brief_md
    assert "不得补写资金费率" in pack.reader_brief_md


def _crypto_request_plan() -> tuple[PackRequest, RunProviderPlan, dict[str, object]]:
    request = PackRequest(
        run_id="run-crypto-1",
        call_id="call-crypto-1",
        worker_id="market_analyst",
        market=Market.CRYPTO,
        domain=PackDomain.MARKET,
        ticker="BTC",
        company_name="Bitcoin",
        start_date="2026-04-01",
        end_date="2026-05-17",
        current_date="2026-05-17",
        currency="USDT",
        profile="CRYPTO",
        freshness_policy=FreshnessPolicy(max_age_seconds=300),
    )
    adapters = build_default_market_adapters(provider_config_version="cfg-crypto", env={})
    crypto_adapters = tuple(item for item in adapters if getattr(item, "market", None) == Market.CRYPTO)
    specs = tuple(spec for adapter in crypto_adapters for spec in adapter.build_call_specs(request))
    run_plan = RunProviderPlan(
        run_id=request.run_id,
        provider_config_version="cfg-crypto",
        market=request.market,
        ticker=request.ticker,
        domains=(PackDomain.MARKET,),
        call_specs=specs,
        shared_call_keys=tuple(spec.call_key for spec in specs),
        cache_keys=(),
        rate_limit_plan=(),
        initial_gaps=(),
        generated_at="2026-05-17T00:00:00+00:00",
        remote_prefetch_allowed=False,
    )
    return request, run_plan, {spec.endpoint: spec for spec in specs}


def _crypto_rows(count: int) -> list[dict[str, object]]:
    start = date(2026, 4, 1)
    return [
        {
            "date": (start + timedelta(days=day)).isoformat(),
            "open": 80000 + day,
            "high": 81000 + day,
            "low": 79000 + day,
            "close": 80500 + day,
            "volume": 1000 + day,
            "currency": "USDT",
            "timezone": "UTC",
        }
        for day in range(count)
    ]
