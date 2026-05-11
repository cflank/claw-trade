from __future__ import annotations

import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_PYTHON_ROOT = REPO_ROOT / "openclaw_plugins" / "claw-trade-frontline-tools" / "python"
if str(SHARED_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(SHARED_PYTHON_ROOT))

from frontline_data_pack.errors import PACK_SCHEMA_INVALID, FrontlineValidationError  # noqa: E402
from frontline_data_pack.models import (  # noqa: E402
    AtrIndicators,
    BollIndicators,
    ChartRef,
    EvidenceRef,
    FieldSource,
    KdjIndicators,
    MacdIndicators,
    MarketDateRange,
    MarketDomainData,
    MarketIndicators,
    MarketPriceHistory,
    MarketPriceRow,
    MarketSupportResistance,
    MarketVolumeProfile,
    MovingAverageIndicators,
    PackEnvelope,
    PackInput,
    ProviderAttempt,
    ProviderQueryParameter,
    ProviderResult,
    ProviderSpec,
    Quality,
    RsiIndicators,
    SupportResistanceLevel,
    replace_attempt_evidence,
)


SHA = "sha256:" + ("a" * 64)
QUERY_FINGERPRINT = "sha256:" + ("b" * 64)


def test_t_pack_001_pack_envelope_stable_json_bytes() -> None:
    pack = _build_pack()
    first = pack.to_stable_json_bytes()
    second = pack.to_stable_json_bytes()
    assert first == second


def test_t_pack_001_quality_coverage_out_of_range_raises_schema_invalid() -> None:
    with pytest.raises(FrontlineValidationError) as error:
        _build_pack(
            quality=Quality(
                status="complete",
                coverage_score=1.2,
                freshness_status="fresh",
                warnings=[],
            )
        )
    assert error.value.code == PACK_SCHEMA_INVALID


def test_t_pack_001_replace_attempt_evidence_returns_new_frozen_result() -> None:
    spec = ProviderSpec(
        domain="market",
        priority="P0",
        provider="akshare",
        endpoint="stock_zh_a_hist",
        role="p0_price_history",
        enabled=True,
        mode="remote",
        timeout_ms=10000,
        required_for_complete=True,
        query_parameters=[
            ProviderQueryParameter(
                name="symbol",
                source="ticker_code_6",
                required=True,
                fixed_value=None,
            )
        ],
    )
    attempt = ProviderAttempt(
        provider="akshare",
        endpoint="stock_zh_a_hist",
        role="p0_price_history",
        status="success",
        started_at="2026-05-08T12:00:00Z",
        finished_at="2026-05-08T12:00:01Z",
        elapsed_ms=1000,
        timeout_ms=10000,
        query_fingerprint=QUERY_FINGERPRINT,
        raw_count=1,
        accepted_count=1,
        payload_hash=None,
        raw_payload_ref=None,
        error_code=None,
        error_message_redacted=None,
    )
    result = ProviderResult(
        spec=spec,
        attempt=attempt,
        raw_payload={"rows": [1]},
        normalized_rows=[{"close": 100.0}],
        field_sources={},
    )

    new_result = replace_attempt_evidence(
        result,
        payload_hash=SHA,
        raw_payload_ref="viking://resources/workflow/run-1/frontline/market_analyst/call-1/evidence/provider_raw/akshare/stock_zh_a_hist/1.json",
    )

    assert result is not new_result
    assert result.attempt.payload_hash is None
    assert result.attempt.raw_payload_ref is None
    assert new_result.attempt.payload_hash == SHA
    assert new_result.attempt.raw_payload_ref is not None


def test_t_pack_001_reader_brief_with_internal_uri_raises_schema_invalid() -> None:
    with pytest.raises(FrontlineValidationError) as error:
        _build_pack(reader_brief="证据写在 viking://resources/workflow/run-1")
    assert error.value.code == PACK_SCHEMA_INVALID


def _build_pack(
    *,
    quality: Quality | None = None,
    reader_brief: str = "资料覆盖完整，包含行情、图表与关键指标。",
) -> PackEnvelope:
    attempt = ProviderAttempt(
        provider="akshare",
        endpoint="stock_zh_a_hist",
        role="p0_price_history",
        status="success",
        started_at="2026-05-08T12:00:00Z",
        finished_at="2026-05-08T12:00:01Z",
        elapsed_ms=1000,
        timeout_ms=10000,
        query_fingerprint=QUERY_FINGERPRINT,
        raw_count=1,
        accepted_count=1,
        payload_hash=SHA,
        raw_payload_ref="viking://resources/workflow/run-1/frontline/market_analyst/call-1/evidence/provider_raw/akshare/stock_zh_a_hist/1.json",
        error_code=None,
        error_message_redacted=None,
    )
    field_source = FieldSource(
        field_path="domain_data.price_history.recent_rows[0].close",
        provider="akshare",
        endpoint="stock_zh_a_hist",
        payload_hash=SHA,
        raw_payload_ref="viking://resources/workflow/run-1/frontline/market_analyst/call-1/evidence/provider_raw/akshare/stock_zh_a_hist/1.json",
        observed_at="2026-05-08T12:00:01Z",
        source_time="2026-05-08",
    )
    evidence = EvidenceRef(
        uri="viking://resources/workflow/run-1/frontline/market_analyst/call-1/evidence/provider_raw/akshare/stock_zh_a_hist/1.json",
        sha256=SHA,
        size_bytes=128,
        kind="provider_raw",
        readback_verified=True,
    )
    market_domain = _build_market_domain_data()

    return PackEnvelope(
        ok=True,
        schema_version="cn_a_frontline_pack.v1",
        domain="market",
        run_id="run-1",
        stage="frontline",
        worker_id="market_analyst",
        call_id="call-1",
        tool_name="market_market_data_pack",
        input=PackInput(
            ticker="600519.SH",
            market="CN_A",
            company_name="贵州茅台",
            industry="白酒",
            start_date="2026-03-01",
            end_date="2026-05-08",
        ),
        quality=quality
        or Quality(
            status="complete",
            coverage_score=0.95,
            freshness_status="fresh",
            warnings=[],
        ),
        provider_attempts=[attempt],
        field_sources={"price_close_latest": field_source},
        raw_payload_refs=[evidence],
        mongo_cache_refs=["cn_a_provider_cache:abc123"],
        openviking_l2_refs=[evidence],
        diagnostic_flags=[],
        reader_brief=reader_brief,
        domain_data=market_domain.__dict__,
    )


def _build_market_domain_data() -> MarketDomainData:
    row = MarketPriceRow(
        trade_date="2026-05-08",
        open=1600.0,
        high=1610.0,
        low=1590.0,
        close=1605.0,
        volume=200000.0,
        amount=320000000.0,
        adjust="qfq",
        source_ref="price_close_latest",
    )
    return MarketDomainData(
        schema_version="cn_a_market_pack.v1",
        price_history=MarketPriceHistory(
            ticker="600519.SH",
            adjust="qfq",
            row_count=1,
            date_range=MarketDateRange(start_date="2026-05-08", end_date="2026-05-08"),
            recent_rows=[row],
            source_refs=["price_close_latest"],
        ),
        technical_indicators=MarketIndicators(
            ma=MovingAverageIndicators(ma5=1600.0, ma10=1590.0, ma20=1580.0, ma60=None),
            macd=MacdIndicators(dif=1.0, dea=0.8, macd=0.4),
            rsi=RsiIndicators(rsi6=55.0, rsi12=52.0, rsi24=50.0),
            boll=BollIndicators(mid=1595.0, upper=1620.0, lower=1570.0),
            kdj=KdjIndicators(k=60.0, d=58.0, j=64.0),
            atr=AtrIndicators(atr14=12.0),
        ),
        chart_refs=[
            ChartRef(
                kind="indicator",
                path="charts/indicator.png",
                openviking_ref="viking://resources/workflow/run-1/frontline/market_analyst/call-1/evidence/charts/indicator.png",
                sha256=SHA,
            )
        ],
        support_resistance=MarketSupportResistance(
            levels=[
                SupportResistanceLevel(
                    kind="support",
                    price=1580.0,
                    basis="moving_average",
                    window_days=20,
                    source_refs=["price_close_latest"],
                )
            ],
            calculation_window_days=20,
            diagnostics=[],
        ),
        volume_profile=MarketVolumeProfile(
            buckets=[],
            dominant_price_low=None,
            dominant_price_high=None,
            source_refs=["price_close_latest"],
            diagnostics=[],
        ),
    )
