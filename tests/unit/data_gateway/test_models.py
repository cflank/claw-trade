from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from claw_trade.data_gateway.models import (
    DataGap,
    DataRequest,
    DataResult,
    DataResultStatus,
    EndpointBatchPolicy,
    FetchResult,
    FetchStatus,
    GapReason,
    GapSeverity,
    HttpVisibility,
    IngestResult,
    LicensePolicy,
    Market,
    ProviderBatchPlan,
    ProviderCapability,
    RateLimitPolicy,
    RequiredLevel,
    SourceRole,
)


def _request(**overrides: object) -> DataRequest:
    base = {
        "request_id": "req-1",
        "market": Market.CN_A,
        "symbol_id": "600519.SH",
        "universe_ref": None,
        "timezone": "Asia/Shanghai",
        "calendar": "CN_A_SSE_SZSE",
        "data_type": "daily_bar",
        "granularity": "daily",
        "fields": ("open", "close"),
        "date_range_start": date(2026, 5, 1),
        "date_range_end": date(2026, 5, 31),
        "freshness_policy": "trading_day",
        "consumer": "report",
        "consumer_id": "market_analyst",
        "as_of": datetime(2026, 5, 31, 12, 0, tzinfo=UTC),
    }
    base.update(overrides)
    return DataRequest.model_validate(base)


def _gap(reason: GapReason = GapReason.WAREHOUSE_MISSING, **overrides: object) -> DataGap:
    base = {
        "gap_id": f"gap:{reason.value}",
        "request_id": "req-1",
        "severity": GapSeverity.BLOCKER,
        "reason": reason,
        "market": Market.CN_A,
        "symbol_id": "600519.SH",
        "data_type": "daily_bar",
        "granularity": "daily",
        "human_readable": reason.value,
        "as_of": datetime(2026, 5, 31, 12, 0, tzinfo=UTC),
    }
    base.update(overrides)
    return DataGap.model_validate(base)


def test_data_request_requires_symbol_or_universe_exclusive() -> None:
    with pytest.raises(ValueError, match="二选一"):
        _request(symbol_id="600519.SH", universe_ref="universe:cn_a:all")

    with pytest.raises(ValueError, match="二选一"):
        _request(symbol_id=None, universe_ref=None)


def test_data_request_crypto_and_stock_pair_rules() -> None:
    with pytest.raises(ValueError, match="CRYPTO 必须提供"):
        _request(market=Market.CRYPTO, symbol_id="BTC/USDT")

    with pytest.raises(ValueError, match="非 CRYPTO 不允许"):
        _request(base_asset="BTC", quote_asset="USDT")

    req = _request(
        market=Market.CRYPTO,
        symbol_id="BTC/USDT",
        timezone="UTC",
        calendar="CRYPTO_24_7",
        base_asset="BTC",
        quote_asset="USDT",
    )
    assert req.market == Market.CRYPTO


def test_data_result_status_contract() -> None:
    with pytest.raises(ValueError, match="ready 必须提供 dataset_refs"):
        DataResult(
            request_id="req-1",
            status=DataResultStatus.READY,
            as_of=datetime(2026, 5, 31, tzinfo=UTC),
        )

    with pytest.raises(ValueError, match="missing/error 必须提供 gaps"):
        DataResult(
            request_id="req-1",
            status=DataResultStatus.MISSING,
            as_of=datetime(2026, 5, 31, tzinfo=UTC),
        )

    ok = DataResult(
        request_id="req-1",
        status=DataResultStatus.READY,
        dataset_refs=("dataset:1",),
        as_of=datetime(2026, 5, 31, tzinfo=UTC),
    )
    assert ok.status == DataResultStatus.READY


def test_data_gap_reason_specific_validator() -> None:
    with pytest.raises(ValueError, match="required_fields"):
        _gap(reason=GapReason.FIELD_MISSING)

    with pytest.raises(ValueError, match="evidence_refs"):
        _gap(reason=GapReason.RATE_LIMITED)

    ok = _gap(
        reason=GapReason.FIELD_MISSING,
        required_fields=("close",),
    )
    assert ok.required_fields == ("close",)


def test_provider_capability_contract_validator() -> None:
    with pytest.raises(ValueError, match="priority_rank"):
        ProviderCapability(
            provider_id="p1",
            plugin_version="1.0",
            endpoint_id="daily",
            market=Market.CN_A,
            data_type="daily_bar",
            source_role=SourceRole.OFFICIAL,
            supported_granularities=("daily",),
            coverage_fields=("close",),
            priority_rank=-1,
            credential_required=False,
            http_visibility=HttpVisibility.MANAGED_HTTP,
            can_be_formal_fact_source=True,
            license_policy=LicensePolicy(),
            rate_limit_policy=RateLimitPolicy(),
            batch_policy=EndpointBatchPolicy(supports_batch=False, batch_by="none"),
        )

    with pytest.raises(ValueError, match="不能作为正式事实源"):
        ProviderCapability(
            provider_id="p1",
            plugin_version="1.0",
            endpoint_id="daily",
            market=Market.CN_A,
            data_type="daily_bar",
            source_role=SourceRole.DISCOVERY,
            supported_granularities=("daily",),
            coverage_fields=("close",),
            priority_rank=1,
            credential_required=False,
            http_visibility=HttpVisibility.MANAGED_HTTP,
            can_be_formal_fact_source=True,
            license_policy=LicensePolicy(),
            rate_limit_policy=RateLimitPolicy(),
            batch_policy=EndpointBatchPolicy(supports_batch=False, batch_by="none"),
        )


def test_provider_batch_plan_validator() -> None:
    with pytest.raises(ValueError, match="request_ids"):
        ProviderBatchPlan(
            batch_id="batch-1",
            plan_id="plan-1",
            provider_id="prov",
            endpoint_id="daily",
            market=Market.CN_A,
            data_type="daily_bar",
            granularity="daily",
            request_ids=(),
            symbol_ids=("600519.SH",),
            fields_union=("close",),
            params_redacted={},
            priority_rank=1,
            required_level=RequiredLevel.REQUIRED,
            cache_key="cache",
            rate_limit_key="rate",
            single_flight_key="flight",
            provider_config_version="cfg-v1",
            as_of=datetime(2026, 5, 31, tzinfo=UTC),
        )

    with pytest.raises(ValueError, match="not_applicable"):
        ProviderBatchPlan(
            batch_id="batch-1",
            plan_id="plan-1",
            provider_id="prov",
            endpoint_id="daily",
            market=Market.CN_A,
            data_type="daily_bar",
            granularity="daily",
            request_ids=("req-1",),
            symbol_ids=("600519.SH",),
            fields_union=("close",),
            params_redacted={},
            priority_rank=1,
            required_level=RequiredLevel.NOT_APPLICABLE,
            cache_key="cache",
            rate_limit_key="rate",
            single_flight_key="flight",
            provider_config_version="cfg-v1",
            as_of=datetime(2026, 5, 31, tzinfo=UTC),
        )


def test_fetch_result_contract_validator() -> None:
    with pytest.raises(ValueError, match="payload"):
        FetchResult(
            fetch_id="fetch-1",
            batch_id="batch-1",
            provider_id="prov",
            endpoint_id="daily",
            market=Market.CN_A,
            symbol_ids=("600519.SH",),
            status=FetchStatus.SUCCESS,
            fetched_at=datetime(2026, 5, 31, tzinfo=UTC),
        )

    with pytest.raises(ValueError, match="row_count"):
        FetchResult(
            fetch_id="fetch-2",
            batch_id="batch-1",
            provider_id="prov",
            endpoint_id="daily",
            market=Market.CN_A,
            symbol_ids=("600519.SH",),
            status=FetchStatus.EMPTY,
            row_count=10,
            fetched_at=datetime(2026, 5, 31, tzinfo=UTC),
        )


def test_fetch_result_empty_keeps_http_observations() -> None:
    batch = ProviderBatchPlan(
        batch_id="batch-1",
        plan_id="plan-1",
        provider_id="prov",
        endpoint_id="daily",
        market=Market.CN_A,
        data_type="daily_bar",
        granularity="daily",
        request_ids=("req-1",),
        symbol_ids=("600519.SH",),
        fields_union=("close",),
        params_redacted={},
        priority_rank=1,
        required_level=RequiredLevel.REQUIRED,
        cache_key="cache",
        rate_limit_key="rate",
        single_flight_key="flight",
        provider_config_version="cfg-v1",
        as_of=datetime(2026, 5, 31, tzinfo=UTC),
    )

    result = FetchResult.from_empty(
        batch,
        error=RuntimeError("empty_result"),
        http_observations=({"request_key": "http:1", "status_code": 200},),
    )

    assert result.status == FetchStatus.EMPTY
    assert result.http_observations[0].request_key == "http:1"
    assert result.http_observations[0].status_code == 200


def test_ingest_result_contract_validator() -> None:
    with pytest.raises(ValueError, match="attempt_refs"):
        IngestResult(
            ingest_id="ing-1",
            batch_id="batch-1",
            status="ingested",
            remote_success=False,
            created_at=datetime(2026, 5, 31, tzinfo=UTC),
        )

    with pytest.raises(ValueError, match="raw_refs"):
        IngestResult(
            ingest_id="ing-2",
            batch_id="batch-1",
            status="partial",
            attempt_refs=("attempt:1",),
            remote_success=True,
            created_at=datetime(2026, 5, 31, tzinfo=UTC),
        )
