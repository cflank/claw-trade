from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace

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
    ProviderCapability,
    RateLimitPolicy,
    RequiredLevel,
    SourceRole,
)
from claw_trade.data_gateway.needs import (
    DataNeed,
    DataNeedGap,
    MergeEvidence,
    NeedInstrument,
    NeedPlan,
    NeedPriority,
    ProviderCallSpec,
    RateLimitEvidence,
    ScheduledCall,
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

    with pytest.raises(ValueError, match="evidence_refs"):
        _gap(reason=GapReason.RATE_LIMITED_BY_TOOL_BUDGET)

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
            fields=("close",),
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
            fields=("close",),
            priority_rank=1,
            credential_required=False,
            http_visibility=HttpVisibility.MANAGED_HTTP,
            can_be_formal_fact_source=True,
            license_policy=LicensePolicy(),
            rate_limit_policy=RateLimitPolicy(),
            batch_policy=EndpointBatchPolicy(supports_batch=False, batch_by="none"),
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
    batch = SimpleNamespace(
        batch_id="batch-1",
        provider_id="prov",
        endpoint_id="daily",
        market=Market.CN_A,
        symbol_ids=("600519.SH",),
    )

    result = FetchResult.from_empty(
        batch,
        error=RuntimeError("empty_result"),
        http_observations=({"request_key": "http:1", "status_code": 200},),
    )

    assert result.status == FetchStatus.EMPTY
    assert result.http_observations[0].request_key == "http:1"
    assert result.http_observations[0].status_code == 200


def test_fetch_result_keeps_managed_http_request_evidence() -> None:
    batch = SimpleNamespace(
        batch_id="batch-1",
        provider_id="prov",
        endpoint_id="daily",
        market=Market.CN_A,
        symbol_ids=("600519.SH",),
    )
    observation = SimpleNamespace(
        request_key="http:1",
        method="GET",
        host="https://api.example.com",
        path="/daily",
        request_headers_redacted={"authorization": "<redacted>"},
        sent_at=datetime(2026, 5, 31, tzinfo=UTC),
        status_code=None,
        response_headers_redacted=None,
        response_body_hash=None,
        error_code="connection_error",
        quota_signal=None,
        elapsed_ms=1000,
        sdk_internal_unknown=False,
    )

    result = FetchResult.from_error(batch, status="error", error=RuntimeError("connection_error"), http_observations=(observation,))

    captured = result.http_observations[0]
    assert captured.method == "GET"
    assert captured.host == "https://api.example.com"
    assert captured.path == "/daily"
    assert captured.request_headers_redacted == {"authorization": "<redacted>"}
    assert captured.error_code == "connection_error"


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


def test_data_need_accepts_strings_and_defaults() -> None:
    need = DataNeed.model_validate(
        {
            "need_id": "need-1",
            "api_id": "cn_a.capital_flow",
            "market": "CN_A",
            "instrument": "600519.SH",
            "time_range_start": date(2026, 5, 1),
            "time_range_end": date(2026, 5, 31),
            "requested_by_worker": "market_analyst",
            "purpose": "market_analysis",
            "deadline_at": datetime(2026, 5, 31, 12, 0, tzinfo=UTC),
        }
    )

    assert need.api_id == "cn_a.capital_flow"
    assert need.priority == NeedPriority.NORMAL
    assert need.consumer == "report"


def test_data_need_validates_required_fields_deadline_and_range() -> None:
    base = {
        "need_id": "need-1",
        "api_id": "crypto.funding_rate",
        "market": Market.CRYPTO,
        "instrument": "BTC/USDT",
        "time_range_start": date(2026, 6, 1),
        "time_range_end": date(2026, 6, 10),
        "requested_by_worker": "market_analyst",
        "purpose": "derivatives_crowding",
        "deadline_at": datetime(2026, 6, 10, 12, 0, tzinfo=UTC),
    }

    with pytest.raises(ValueError, match="need_id 不能为空"):
        DataNeed.model_validate({**base, "need_id": " "})

    with pytest.raises(ValueError, match="deadline_at 必须有 timezone"):
        DataNeed.model_validate({**base, "deadline_at": datetime(2026, 6, 10, 12, 0)})

    with pytest.raises(ValueError, match="time_range_start"):
        DataNeed.model_validate(
            {
                **base,
                "time_range_start": date(2026, 6, 11),
                "time_range_end": date(2026, 6, 10),
            }
        )

    with pytest.raises(ValueError, match="time_range_start"):
        DataNeed.model_validate(
            {
                **base,
                "granularity": "hourly",
                "time_range_start": datetime(2026, 6, 10, 23, 0, tzinfo=UTC),
                "time_range_end": datetime(2026, 6, 10, 1, 0, tzinfo=UTC),
            }
        )


def test_provider_call_spec_contract_validator() -> None:
    base = {
        "call_id": "call-1",
        "method": "GET",
        "provider_id": "tushare",
        "catalog_endpoint_id": "tushare.daily",
        "official_path_or_api_name": "daily",
        "params": {"ts_code": "600519.SH"},
        "auth_scope": "tushare:token",
        "rate_limit_bucket": "ratelimit:tushare",
        "http_visibility": "managed_http",
        "parser_status": "normalized",
        "batch_key": "tushare:daily:ts_code",
        "official_doc_ref": "https://tushare.pro/document/2?doc_id=27",
        "deadline_at": datetime(2026, 5, 31, 12, 0, tzinfo=UTC),
        "need_ids": ("need-1",),
    }

    spec = ProviderCallSpec.model_validate(base)
    assert spec.provider_id == "tushare"
    assert spec.need_ids == ("need-1",)

    with pytest.raises(ValueError, match="need_ids 不能为空"):
        ProviderCallSpec.model_validate({**base, "need_ids": ()})

    with pytest.raises(ValueError, match="provider_id 不能为空"):
        ProviderCallSpec.model_validate({**base, "provider_id": ""})

    with pytest.raises(ValueError, match="deadline_at 必须有 timezone"):
        ProviderCallSpec.model_validate({**base, "deadline_at": datetime(2026, 5, 31, 12, 0)})

    with pytest.raises(ValueError, match="内部执行或业务范围字段"):
        ProviderCallSpec.model_validate({**base, "params": {"query": {"symbol": "600519.SH", "only_for_social": "social"}}})

    with pytest.raises(ValueError, match="内部执行或业务范围字段"):
        ProviderCallSpec.model_validate({**base, "params": {"consumer": "report", "ts_code": "600519.SH"}})

    with pytest.raises(ValueError, match="内部执行或业务范围字段"):
        ProviderCallSpec.model_validate({**base, "params": {"query": {"symbol": "600519.SH", "allowed_news": "news"}}})


def test_need_planner_models_are_lightweight_contracts() -> None:
    deadline = datetime(2026, 5, 31, 12, 0, tzinfo=UTC)
    call = ProviderCallSpec(
        call_id="call-1",
        provider_id="coinglass",
        catalog_endpoint_id="coinglass.funding",
        official_path_or_api_name="/api/funding",
        params={"symbol": "BTC"},
        auth_scope="coinglass:pro",
        rate_limit_bucket="ratelimit:coinglass",
        http_visibility="managed_http",
        parser_status="parser_missing",
        batch_key="coinglass:funding:symbol",
        official_doc_ref="https://docs.coinglass.com/reference/funding",
        deadline_at=deadline,
        need_ids=("need-1",),
    )
    scheduled = ScheduledCall(
        call_id="call-1",
        need_ids=("need-1",),
        provider_id="coinglass",
        catalog_endpoint_id="coinglass.funding",
        params={"symbol": "BTC"},
        batch_key="coinglass:funding:symbol",
        rate_limit_bucket="ratelimit:coinglass",
        earliest_start_at=deadline,
        deadline_at=deadline,
        priority="required",
    )

    instrument = NeedInstrument(symbol="BTC/USDT", base_asset="BTC", quote_asset="USDT")
    need = DataNeed(
        need_id="need-1",
        api_id="crypto.funding_rate",
        market=Market.CRYPTO,
        instrument=instrument.symbol,
        requested_by_worker="market_analyst",
        purpose="derivatives_crowding",
        deadline_at=deadline,
    )
    gap = DataNeedGap(need_id="need-2", reason=GapReason.CATALOG_MATCH_MISSING)
    merge = MergeEvidence(batch_key="coinglass:funding:symbol", need_ids=("need-1",), merged=False)
    rate_limit = RateLimitEvidence(
        rate_limit_bucket="ratelimit:coinglass",
        provider_id="coinglass",
        outcome="reserved",
    )
    plan = NeedPlan(
        plan_id="plan-1",
        needs=(need,),
        planned_calls=(call,),
        scheduled_calls=(scheduled,),
        skipped_needs=(gap,),
        merge_evidence=(merge,),
        rate_limit_evidence=(rate_limit,),
        created_at=deadline,
    )

    assert plan.planned_calls[0].batch_key == "coinglass:funding:symbol"
    assert plan.scheduled_calls[0].priority == NeedPriority.REQUIRED
    assert plan.skipped_needs[0].reason == GapReason.CATALOG_MATCH_MISSING


def test_data_need_gap_requires_evidence_for_tool_budget_rate_limit() -> None:
    with pytest.raises(ValueError, match="evidence_refs"):
        DataNeedGap(need_id="need-1", reason=GapReason.RATE_LIMITED_BY_TOOL_BUDGET)

    gap = DataNeedGap(
        need_id="need-1",
        reason=GapReason.RATE_LIMITED_BY_TOOL_BUDGET,
        evidence_refs=("rate_limit:ratelimit:coinglass",),
    )

    assert gap.evidence_refs == ("rate_limit:ratelimit:coinglass",)


def test_scheduled_call_validates_identity_need_ids_and_time_budget() -> None:
    deadline = datetime(2026, 5, 31, 12, 0, tzinfo=UTC)
    base = {
        "call_id": "call-1",
        "need_ids": ("need-1",),
        "provider_id": "coinglass",
        "catalog_endpoint_id": "coinglass.funding",
        "params": {"symbol": "BTC"},
        "batch_key": "coinglass:funding:symbol",
        "rate_limit_bucket": "ratelimit:coinglass",
        "earliest_start_at": deadline,
        "deadline_at": deadline,
        "priority": "required",
    }

    with pytest.raises(ValueError, match="call_id 不能为空"):
        ScheduledCall.model_validate({**base, "call_id": " "})

    with pytest.raises(ValueError, match="need_ids 不能为空"):
        ScheduledCall.model_validate({**base, "need_ids": ()})

    with pytest.raises(ValueError, match="earliest_start_at 必须有 timezone"):
        ScheduledCall.model_validate({**base, "earliest_start_at": datetime(2026, 5, 31, 12, 0)})

    with pytest.raises(ValueError, match="earliest_start_at"):
        ScheduledCall.model_validate(
            {
                **base,
                "earliest_start_at": datetime(2026, 5, 31, 12, 1, tzinfo=UTC),
                "deadline_at": deadline,
            }
        )
