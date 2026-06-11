from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace

from claw_trade.data_gateway.coordination.query_planner import QueryPlanner
from claw_trade.data_gateway.coordination.provider_selector import ProviderSelector
from claw_trade.data_gateway.coordination.service import DataService
from claw_trade.data_gateway.ingest.attempt_log import AttemptLog
from claw_trade.data_gateway.ingest.normalized_store import NormalizedStore
from claw_trade.data_gateway.ingest.normalizer import Normalizer
from claw_trade.data_gateway.ingest.pipeline import IngestPipeline
from claw_trade.data_gateway.ingest.raw_store import RawStore
from claw_trade.data_gateway.models import (
    DataGap,
    DataRequest,
    DataResultStatus,
    FetchResult,
    FetchStatus,
    GapReason,
    GapSeverity,
    GateDecision,
    IngestResult,
    Market,
    ProviderBatchPlan,
    RequiredLevel,
    WarehouseResult,
)
from claw_trade.data_gateway.providers import build_minimal_provider_registry
from claw_trade.data_gateway.providers.credentials import DataSourceCredentialResolver
from claw_trade.data_gateway.warehouse import DatasetRepository


def test_data_service_wave_key_promotes_configured_paid_data_only() -> None:
    official = SimpleNamespace(source_role="official", priority_rank=1)
    compatible_paid = SimpleNamespace(source_role="paid_data", priority_rank=1, configured_paid_data=False)
    configured_paid = SimpleNamespace(source_role="paid_data", priority_rank=100, configured_paid_data=True)

    assert DataService._candidate_wave_key(configured_paid) < DataService._candidate_wave_key(official)
    assert DataService._candidate_wave_key(official) < DataService._candidate_wave_key(compatible_paid)


def _request(request_id: str = "req-service-1") -> DataRequest:
    return DataRequest.model_validate(
        {
            "request_id": request_id,
            "market": "CN_A",
            "symbol_id": "600519.SH",
            "timezone": "Asia/Shanghai",
            "calendar": "CN_A_SSE_SZSE",
            "data_type": "daily_bar",
            "granularity": "daily",
            "fields": ("close",),
            "freshness_policy": "trading_day",
            "consumer": "report",
            "consumer_id": "market_analyst",
            "as_of": datetime(2026, 5, 31, tzinfo=UTC),
        }
    )


def _symbol_request(request_id: str, symbol_id: str) -> DataRequest:
    payload = _request(request_id).model_dump()
    payload["symbol_id"] = symbol_id
    return DataRequest.model_validate(payload)


def _crypto_request(request_id: str = "req-crypto-service-1") -> DataRequest:
    return DataRequest.model_validate(
        {
            "request_id": request_id,
            "market": "CRYPTO",
            "symbol_id": "SOLUSDT",
            "exchange": "BINANCE",
            "currency": "USDT",
            "timezone": "UTC",
            "calendar": "CRYPTO_24_7",
            "base_asset": "SOL",
            "quote_asset": "USDT",
            "data_type": "daily_bar",
            "granularity": "daily",
            "fields": ("open", "high", "low", "close", "volume"),
            "date_range_start": date(2026, 6, 1),
            "date_range_end": date(2026, 6, 7),
            "freshness_policy": "calendar_day",
            "consumer": "report",
            "consumer_id": "market_analyst",
            "as_of": datetime(2026, 6, 7, tzinfo=UTC),
        }
    )


def _crypto_source_state_request(
    *,
    request_id: str,
    data_type: str,
    granularity: str,
    fields: tuple[str, ...],
) -> DataRequest:
    return DataRequest.model_validate(
        {
            "request_id": request_id,
            "market": "CRYPTO",
            "symbol_id": "BTCUSDT",
            "exchange": "BINANCE",
            "currency": "USDT",
            "timezone": "UTC",
            "calendar": "CRYPTO_24_7",
            "base_asset": "BTC",
            "quote_asset": "USDT",
            "data_type": data_type,
            "granularity": granularity,
            "fields": fields,
            "date_range_start": date(2026, 6, 1),
            "date_range_end": date(2026, 6, 7),
            "freshness_policy": "calendar_day",
            "consumer": "report",
            "consumer_id": "market_analyst",
            "as_of": datetime(2026, 6, 7, tzinfo=UTC),
        }
    )


def _credential_resolver(
    records: tuple[dict[str, object], ...],
    secrets: dict[str | None, str | None] | None = None,
) -> DataSourceCredentialResolver:
    secret_values = secrets or {}
    return DataSourceCredentialResolver(
        data_source_store=SimpleNamespace(list_instances=lambda: records),
        secret_store=SimpleNamespace(get=lambda ref: secret_values.get(ref)),
    )


def _real_ingest(repository: DatasetRepository) -> IngestPipeline:
    return IngestPipeline(
        raw_store=RawStore(repository=repository),
        normalizer=Normalizer(),
        normalized_store=NormalizedStore(repository=repository),
        attempt_log=AttemptLog(repository=repository),
    )


def _gap(request_id: str) -> DataGap:
    return DataGap(
        gap_id=f"gap:{request_id}",
        request_id=request_id,
        severity=GapSeverity.BLOCKER,
        reason=GapReason.WAREHOUSE_MISSING,
        market=Market.CN_A,
        symbol_id="600519.SH",
        data_type="daily_bar",
        granularity="daily",
        human_readable="warehouse_missing",
        as_of=datetime(2026, 5, 31, tzinfo=UTC),
    )


def _batch_plan(request_id: str) -> ProviderBatchPlan:
    return ProviderBatchPlan(
        batch_id=f"batch:{request_id}",
        plan_id="plan:1",
        provider_id="provider-1",
        endpoint_id="daily",
        market=Market.CN_A,
        data_type="daily_bar",
        granularity="daily",
        request_ids=(request_id,),
        symbol_ids=("600519.SH",),
        fields_union=("close",),
        params_redacted={"symbol": "600519.SH"},
        priority_rank=1,
        required_level=RequiredLevel.REQUIRED,
        cache_key="cache:1",
        rate_limit_key="rate:1",
        single_flight_key="flight:1",
        provider_config_version="cfg:1",
        as_of=datetime(2026, 5, 31, tzinfo=UTC),
    )


class _Warehouse:
    def __init__(
        self,
        events: list[str],
        *,
        satisfied_on_check: bool,
        attempt_refs: tuple[str, ...] = (),
    ) -> None:
        self._events = events
        self._satisfied_on_check = satisfied_on_check
        self._attempt_refs = attempt_refs

    def check(self, checks, coverage) -> WarehouseResult:
        self._events.append("warehouse.check")
        if self._satisfied_on_check:
            return WarehouseResult(
                satisfied=True,
                rows=({"close": 1.0},),
                dataset_refs=("dataset:warehouse",),
                attempt_refs=self._attempt_refs,
                freshness={"policy": "trading_day"},
            )
        request_id = checks[0].request_id
        return WarehouseResult(satisfied=False, gaps=(_gap(request_id),))

    def recheck(self, checks, coverage) -> WarehouseResult:
        self._events.append("warehouse.recheck")
        return WarehouseResult(
            satisfied=True,
            rows=({"close": 2.0},),
            dataset_refs=("dataset:recheck",),
            freshness={"policy": "trading_day"},
        )


class _AlwaysMissingWarehouse:
    def check(self, checks, coverage) -> WarehouseResult:
        del coverage
        return WarehouseResult(
            satisfied=False,
            gaps=tuple(
                DataGap.by_reason(
                    "warehouse_missing",
                    request_id=check.request_id,
                    market=check.market,
                    data_type=check.data_type,
                    granularity=check.granularity,
                    symbol_id=check.symbol_id,
                    as_of=datetime(2026, 6, 7, tzinfo=UTC),
                )
                for check in checks
            ),
        )

    def recheck(self, checks, coverage) -> WarehouseResult:
        return self.check(checks, coverage)


class _NoRemoteCoalescer:
    def coalesce(self, gaps, candidates, capabilities):
        del gaps, candidates, capabilities
        raise AssertionError("unconfigured credential source must not reach coalescer")


class _NoRemoteBatchPlanner:
    def build_batches(self, groups, capabilities):
        del groups, capabilities
        raise AssertionError("unconfigured credential source must not reach batch planner")


class _NoRemoteExecutionGate:
    def enter(self, batch):
        del batch
        raise AssertionError("unconfigured credential source must not reach execution gate")

    def publish_shared_result(self, single_flight_key: str, owner_token: str, ingest: IngestResult) -> None:
        del single_flight_key, owner_token, ingest
        raise AssertionError("unconfigured credential source must not publish shared result")


class _NoRemoteFetchEngine:
    def fetch(self, batch):
        del batch
        raise AssertionError("unconfigured credential source must not fetch")


class _CryptoLocalEmptyWarehouse:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    def check(self, checks, coverage) -> WarehouseResult:
        del coverage
        self._events.append("warehouse.check")
        check = checks[0]
        return WarehouseResult(
            satisfied=False,
            gaps=(
                DataGap.by_reason(
                    GapReason.WAREHOUSE_MISSING,
                    request_id=check.request_id,
                    market=Market.CRYPTO,
                    data_type=check.data_type,
                    granularity=check.granularity,
                    symbol_id=check.symbol_id,
                    message="warehouse_missing",
                    as_of=datetime(2026, 6, 7, tzinfo=UTC),
                ),
            ),
        )

    def recheck(self, checks, coverage) -> WarehouseResult:
        del checks, coverage
        self._events.append("warehouse.recheck")
        return WarehouseResult(
            satisfied=True,
            rows=(
                {
                    "dataset": "daily_bar",
                    "market": "CRYPTO",
                    "symbol_id": "SOLUSDT",
                    "exchange": "BINANCE",
                    "base_asset": "SOL",
                    "quote_asset": "USDT",
                    "granularity": "daily",
                    "period_start": "2026-06-01",
                    "period_end": "2026-06-07",
                    "open": 149.0,
                    "high": 155.0,
                    "low": 148.0,
                    "close": 150.0,
                    "volume": 1000.0,
                },
            ),
            dataset_refs=("dataset:daily_bar:CRYPTO:SOLUSDT:2026-06-01:2026-06-07",),
            attempt_refs=("attempt:local-seed",),
            freshness={"policy": "calendar_day"},
        )


class _CryptoDateRangeMissingWarehouse:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    def check(self, checks, coverage) -> WarehouseResult:
        del coverage
        self._events.append("warehouse.check")
        check = checks[0]
        return WarehouseResult(
            satisfied=False,
            dataset_refs=("dataset:daily_bar:CRYPTO:spot:SOLUSDT:daily:2026-06-01:2026-06-03",),
            attempt_refs=("attempt:local-seed-partial",),
            gaps=(
                DataGap.by_reason(
                    GapReason.DATE_RANGE_MISSING,
                    request_id=check.request_id,
                    market=Market.CRYPTO,
                    data_type=check.data_type,
                    granularity=check.granularity,
                    symbol_id=check.symbol_id,
                    message="date_range_missing: local CRYPTO seed covers 2026-06-01..2026-06-03 only.",
                    as_of=datetime(2026, 6, 7, tzinfo=UTC),
                ),
            ),
        )

    def recheck(self, checks, coverage) -> WarehouseResult:
        del checks, coverage
        self._events.append("warehouse.recheck")
        return WarehouseResult(
            satisfied=True,
            rows=(
                {
                    "dataset": "daily_bar",
                    "market": "CRYPTO",
                    "symbol_id": "SOLUSDT",
                    "exchange": "BINANCE",
                    "base_asset": "SOL",
                    "quote_asset": "USDT",
                    "granularity": "daily",
                    "period_start": "2026-06-01",
                    "period_end": "2026-06-07",
                    "open": 149.0,
                    "high": 155.0,
                    "low": 148.0,
                    "close": 150.0,
                    "volume": 1000.0,
                },
            ),
            dataset_refs=("dataset:daily_bar:CRYPTO:SOLUSDT:2026-06-01:2026-06-07",),
            attempt_refs=("attempt:local-seed-partial",),
            freshness={"policy": "calendar_day"},
        )


class _MetadataWarehouse(_Warehouse):
    def check_coverage(self, checks, coverage) -> WarehouseResult:
        del checks, coverage
        self._events.append("warehouse.check_coverage")
        return WarehouseResult(
            satisfied=True,
            rows=(),
            dataset_refs=("dataset:metadata-only",),
            freshness={"policy": "warehouse_only"},
        )


class _MultiSymbolWarehouse:
    def check(self, checks, coverage) -> WarehouseResult:
        del coverage
        return WarehouseResult(satisfied=False, gaps=tuple(_gap(check.request_id) for check in checks))

    def recheck(self, checks, coverage) -> WarehouseResult:
        del checks, coverage
        return WarehouseResult(
            satisfied=True,
            rows=(
                {
                    "dataset": "daily_bar",
                    "market": "CN_A",
                    "symbol_id": "600519.SH",
                    "granularity": "daily",
                    "close": 2.0,
                },
                {
                    "dataset": "daily_bar",
                    "market": "CN_A",
                    "symbol_id": "000001.SZ",
                    "granularity": "daily",
                    "close": 3.0,
                },
            ),
            dataset_refs=("dataset:600519", "dataset:000001"),
            freshness={"policy": "trading_day", "checked_requests": ("req-a", "req-b")},
        )


class _PartialBatchWarehouse:
    def check(self, checks, coverage) -> WarehouseResult:
        del coverage
        return WarehouseResult(satisfied=False, gaps=tuple(_gap(check.request_id) for check in checks))

    def recheck(self, checks, coverage) -> WarehouseResult:
        del checks, coverage
        return WarehouseResult(
            satisfied=False,
            rows=(
                {
                    "dataset": "daily_bar",
                    "market": "CN_A",
                    "symbol_id": "600519.SH",
                    "granularity": "daily",
                    "close": 2.0,
                },
            ),
            dataset_refs=("dataset:600519",),
            gaps=(_gap("req-b"),),
            freshness={"policy": "trading_day", "checked_requests": ("req-a", "req-b")},
        )


class _MixedCoverageWarehouse:
    def check(self, checks, coverage) -> WarehouseResult:
        del coverage
        return WarehouseResult(
            satisfied=False,
            rows=({"open": 10.0, "high": 12.0, "low": 9.0, "close": 11.0, "volume": 1000},),
            dataset_refs=("dataset:daily_bar:CN_A:row-1",),
            gaps=(
                DataGap.by_reason(
                    "warehouse_missing",
                    request_id="req-news",
                    market=Market.CN_A,
                    data_type="company_news",
                    granularity="event",
                    required_fields=("title", "published_at", "source", "summary", "url"),
                    symbol_id="600519.SH",
                    as_of=datetime(2026, 5, 31, tzinfo=UTC),
                ),
            ),
            freshness={
                "policy": "warehouse_only",
                "checked_requests": ("req-market", "req-news"),
                "coverage_by_request": (
                    {
                        "request_id": "req-market",
                        "data_type": "daily_bar",
                        "dataset_refs": ("dataset:daily_bar:CN_A:row-1",),
                    },
                    {
                        "request_id": "req-news",
                        "data_type": "company_news",
                        "dataset_refs": (),
                    },
                ),
            },
        )


class _MissingCoverageItemWarehouse:
    def check(self, checks, coverage) -> WarehouseResult:
        del checks, coverage
        return WarehouseResult(
            satisfied=False,
            rows=({"open": 10.0, "high": 12.0, "low": 9.0, "close": 11.0, "volume": 1000},),
            dataset_refs=("dataset:daily_bar:CN_A:row-1",),
            gaps=(
                DataGap.by_reason(
                    "warehouse_missing",
                    request_id="req-news",
                    market=Market.CN_A,
                    data_type="company_news",
                    granularity="event",
                    required_fields=("title", "published_at", "source", "summary", "url"),
                    symbol_id="600519.SH",
                    as_of=datetime(2026, 5, 31, tzinfo=UTC),
                ),
            ),
            freshness={
                "policy": "warehouse_only",
                "checked_requests": ("req-market", "req-news"),
                "coverage_by_request": (
                    {
                        "request_id": "req-market",
                        "data_type": "daily_bar",
                        "dataset_refs": ("dataset:daily_bar:CN_A:row-1",),
                    },
                ),
            },
        )


class _CanonicalRefMixedWarehouse:
    def check(self, checks, coverage) -> WarehouseResult:
        del checks, coverage
        return WarehouseResult(
            satisfied=True,
            rows=(
                {"open": 10.0, "high": 12.0, "low": 9.0, "close": 11.0, "volume": 1000},
                {"pe": 30.0, "pb": 5.0, "ps": 10.0, "market_cap": 1000000.0},
            ),
            dataset_refs=(
                "dataset:daily_bar:CN_A:row-1",
                "dataset:valuation_metric:CN_A:row-2",
            ),
            freshness={"policy": "trading_day"},
        )

    def recheck(self, checks, coverage) -> WarehouseResult:
        raise AssertionError("warehouse hit must not fetch or recheck")


class _NameLookupWarehouse:
    def resolve_company_names(self, *, market, symbol_ids, dataset="daily_bar"):
        del market, symbol_ids, dataset
        return {}

    def check(self, checks, coverage) -> WarehouseResult:
        del coverage
        assert checks[0].data_type == "quote_snapshot"
        assert checks[0].fields == ("symbol_id", "name", "company_name")
        return WarehouseResult(
            satisfied=True,
            rows=(
                {
                    "dataset": "quote_snapshot",
                    "market": "CN_A",
                    "symbol_id": "688017.SH",
                    "granularity": "realtime",
                    "name": "绿的谐波",
                    "company_name": "绿的谐波",
                },
            ),
            dataset_refs=("dataset:quote_snapshot:CN_A:688017.SH",),
            freshness={"policy": "trading_day"},
        )


class _Selector:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    def select_candidates(self, gaps, plan):
        self._events.append("provider_selector.select_candidates")
        return ({"provider_id": "provider-1"},)

    def read_capabilities(self, candidates):
        self._events.append("provider_selector.read_capabilities")
        return {"count": len(candidates)}


class _Coalescer:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    def coalesce(self, gaps, candidates, capabilities):
        self._events.append("coalescer.coalesce")
        assert capabilities["count"] == 1
        return ({"group_id": "group-1", "request_id": gaps[0].request_id},)


class _BatchPlanner:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    def build_batches(self, groups, capabilities):
        self._events.append("batch_planner.build_batches")
        assert capabilities["count"] == 1
        return (_batch_plan(groups[0]["request_id"]),)


class _Scheduler:
    def __init__(self, events: list[str]) -> None:
        self._events = events
        self.context = None

    def schedule(self, batches, context):
        self._events.append("scheduler.schedule")
        self.context = context
        return batches


class _ExecutionGate:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    def enter(self, batch):
        self._events.append("execution_gate.enter")
        return GateDecision(kind="owner", owner_token="owner-1")

    def publish_shared_result(self, single_flight_key: str, owner_token: str, ingest: IngestResult) -> None:
        self._events.append("execution_gate.publish_shared_result")


class _RetryAfterRateLimitExecutionGate(_ExecutionGate):
    def wait_after_rate_limited_fetch(self, batch, fetch_result):
        del batch
        status = getattr(getattr(fetch_result, "status", None), "value", getattr(fetch_result, "status", None))
        self._events.append("execution_gate.wait_after_rate_limited_fetch")
        return status == "rate_limited"


class _FetchEngine:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    def fetch(self, batch):
        self._events.append("fetch_engine.fetch")
        return FetchResult(
            fetch_id="fetch-1",
            batch_id=batch.batch_id,
            provider_id=batch.provider_id,
            endpoint_id=batch.endpoint_id,
            market=batch.market,
            symbol_ids=batch.symbol_ids,
            status=FetchStatus.SUCCESS,
            payload={"rows": [{"close": 2.0}]},
            row_count=1,
            fetched_at=datetime(2026, 5, 31, tzinfo=UTC),
        )


class _Ingest:
    def __init__(self, events: list[str]) -> None:
        self._events = events
        self.selector_skip_ingests: list[IngestResult] = []
        self.selector_skips: list[tuple[object, object]] = []

    def ingest(self, result, batch):
        self._events.append("ingest.ingest")
        return IngestResult(
            ingest_id="ingest-1",
            batch_id=batch.batch_id,
            status="ingested",
            dataset_refs=("dataset:ingest",),
            raw_refs=("raw:1",),
            attempt_refs=("attempt:1",),
            remote_success=True,
            created_at=datetime(2026, 5, 31, tzinfo=UTC),
        )

    def record_gate_result(self, batch, gate):
        self._events.append("ingest.record_gate_result")
        return IngestResult(
            ingest_id="ingest-gate",
            batch_id=batch.batch_id,
            status="non_remote_recorded",
            attempt_refs=("attempt:gate",),
            remote_success=False,
            created_at=datetime(2026, 5, 31, tzinfo=UTC),
        )

    def record_selector_skip(self, batch, selector_skip):
        self._events.append("ingest.record_selector_skip")
        result = IngestResult(
            ingest_id=f"ingest-skip-{batch.provider_id}",
            batch_id=batch.batch_id,
            status="non_remote_recorded",
            attempt_refs=(f"attempt:skip:{batch.provider_id}",),
            gaps=(
                DataGap.by_reason(
                    "credential_missing",
                    request_id=batch.request_ids[0],
                    market=batch.market,
                    data_type=batch.data_type,
                    granularity=batch.granularity,
                    symbol_id=batch.symbol_ids[0] if batch.symbol_ids else None,
                ),
            ),
            remote_success=False,
            created_at=datetime(2026, 5, 31, tzinfo=UTC),
        )
        self.selector_skips.append((batch, selector_skip))
        self.selector_skip_ingests.append(result)
        return result


class _IngestWithSupersededGap(_Ingest):
    def ingest(self, result, batch):
        self._events.append("ingest.ingest")
        return IngestResult(
            ingest_id="ingest-superseded-gap",
            batch_id=batch.batch_id,
            status="partial",
            dataset_refs=("dataset:backup",),
            raw_refs=("raw:backup",),
            attempt_refs=("attempt:backup",),
            gaps=(
                DataGap.by_reason(
                    "field_missing",
                    request_id=batch.request_ids[0],
                    market=batch.market,
                    data_type=batch.data_type,
                    granularity=batch.granularity,
                    required_fields=("volume",),
                    symbol_id=batch.symbol_ids[0],
                ),
            ),
            remote_success=False,
            created_at=datetime(2026, 5, 31, tzinfo=UTC),
        )


class _IngestWithoutDatasetRefs(_Ingest):
    def ingest(self, result, batch):
        self._events.append("ingest.ingest")
        return IngestResult(
            ingest_id="ingest-no-dataset",
            batch_id=batch.batch_id,
            status="failed",
            raw_refs=("raw:failed",),
            attempt_refs=("attempt:failed",),
            gaps=(
                DataGap.by_reason(
                    "empty_result",
                    request_id=batch.request_ids[0],
                    market=batch.market,
                    data_type=batch.data_type,
                    granularity=batch.granularity,
                    symbol_id=batch.symbol_ids[0],
                ),
            ),
            remote_success=False,
            created_at=datetime(2026, 5, 31, tzinfo=UTC),
        )


class _Planner(QueryPlanner):
    def __init__(self, events: list[str]) -> None:
        super().__init__()
        self._events = events

    def validate_and_normalize(self, request: DataRequest):
        self._events.append("query_planner.validate_and_normalize")
        return super().validate_and_normalize(request)

    def validate_and_normalize_many(self, requests):
        self._events.append("query_planner.validate_and_normalize_many")
        return super().validate_and_normalize_many(requests)


def test_data_service_warehouse_hit_does_not_fetch_remote() -> None:
    events: list[str] = []
    service = DataService(
        query_planner=_Planner(events),
        warehouse=_Warehouse(events, satisfied_on_check=True),
        provider_selector=_Selector(events),
        coalescer=_Coalescer(events),
        batch_planner=_BatchPlanner(events),
        execution_gate=_ExecutionGate(events),
        fetch_engine=_FetchEngine(events),
        ingest=_Ingest(events),
    )
    result = service.get_data(_request("req-hit"))
    assert result.status == DataResultStatus.READY
    assert result.dataset_refs == ("dataset:warehouse",)
    assert events == ["query_planner.validate_and_normalize", "warehouse.check"]


def test_data_service_warehouse_hit_returns_attempt_refs() -> None:
    events: list[str] = []
    service = DataService(
        query_planner=_Planner(events),
        warehouse=_Warehouse(events, satisfied_on_check=True, attempt_refs=("attempt:warehouse",)),
        provider_selector=_Selector(events),
        coalescer=_Coalescer(events),
        batch_planner=_BatchPlanner(events),
        execution_gate=_ExecutionGate(events),
        fetch_engine=_FetchEngine(events),
        ingest=_Ingest(events),
    )

    result = service.get_data(_request("req-hit-lineage"))

    assert result.status == DataResultStatus.READY
    assert result.dataset_refs == ("dataset:warehouse",)
    assert result.attempt_refs == ("attempt:warehouse",)
    assert events == ["query_planner.validate_and_normalize", "warehouse.check"]


def test_data_service_follows_fixed_ten_step_order_when_warehouse_missing() -> None:
    events: list[str] = []
    service = DataService(
        query_planner=_Planner(events),
        warehouse=_Warehouse(events, satisfied_on_check=False),
        provider_selector=_Selector(events),
        coalescer=_Coalescer(events),
        batch_planner=_BatchPlanner(events),
        execution_gate=_ExecutionGate(events),
        fetch_engine=_FetchEngine(events),
        ingest=_Ingest(events),
    )
    result = service.get_data(_request("req-miss"))
    assert result.status == DataResultStatus.READY
    assert "dataset:recheck" in result.dataset_refs
    assert "raw:1" in result.raw_refs
    assert "attempt:1" in result.attempt_refs
    assert events == [
        "query_planner.validate_and_normalize",
        "warehouse.check",
        "provider_selector.select_candidates",
        "provider_selector.read_capabilities",
        "coalescer.coalesce",
        "batch_planner.build_batches",
        "execution_gate.enter",
        "fetch_engine.fetch",
        "ingest.ingest",
        "execution_gate.publish_shared_result",
        "warehouse.recheck",
    ]


def test_data_service_warehouse_only_does_not_fetch_remote_when_missing() -> None:
    events: list[str] = []
    service = DataService(
        query_planner=_Planner(events),
        warehouse=_Warehouse(events, satisfied_on_check=False),
        provider_selector=_Selector(events),
        coalescer=_Coalescer(events),
        batch_planner=_BatchPlanner(events),
        execution_gate=_ExecutionGate(events),
        fetch_engine=_FetchEngine(events),
        ingest=_Ingest(events),
    )
    request = _request("req-warehouse-only").model_copy(update={"freshness_policy": "warehouse_only"})

    result = service.get_data(request)

    assert result.status == DataResultStatus.MISSING
    assert result.dataset_refs == ()
    assert [gap.reason for gap in result.gaps] == [GapReason.WAREHOUSE_MISSING]
    assert events == ["query_planner.validate_and_normalize", "warehouse.check"]


def test_data_service_warehouse_only_uses_metadata_coverage_without_rows() -> None:
    events: list[str] = []
    service = DataService(
        query_planner=_Planner(events),
        warehouse=_MetadataWarehouse(events, satisfied_on_check=True),
        provider_selector=_Selector(events),
        coalescer=_Coalescer(events),
        batch_planner=_BatchPlanner(events),
        execution_gate=_ExecutionGate(events),
        fetch_engine=_FetchEngine(events),
        ingest=_Ingest(events),
    )
    request = _request("req-warehouse-only-ready").model_copy(
        update={"freshness_policy": "warehouse_only", "consumer": "select", "consumer_id": "select-run:coverage_check"}
    )

    result = service.get_data(request)

    assert result.status == DataResultStatus.READY
    assert result.rows == ()
    assert result.dataset_refs == ("dataset:metadata-only",)
    assert events == ["query_planner.validate_and_normalize", "warehouse.check_coverage"]


def test_data_service_does_not_slice_daily_rows_into_news_request() -> None:
    events: list[str] = []
    service = DataService(
        query_planner=_Planner(events),
        warehouse=_MixedCoverageWarehouse(),
        provider_selector=_Selector(events),
        coalescer=_Coalescer(events),
        batch_planner=_BatchPlanner(events),
        execution_gate=_ExecutionGate(events),
        fetch_engine=_FetchEngine(events),
        ingest=_Ingest(events),
    )
    market_request = _request("req-market").model_copy(update={"freshness_policy": "warehouse_only"})
    news_payload = _request("req-news").model_dump()
    news_payload.update(
        {
            "data_type": "company_news",
            "granularity": "event",
            "fields": ("title", "published_at", "source", "summary", "url"),
            "freshness_policy": "warehouse_only",
            "consumer_id": "news_analyst",
        }
    )

    market_result, news_result = service.get_data_batch((market_request, DataRequest.model_validate(news_payload)))

    assert market_result.status == DataResultStatus.READY
    assert market_result.rows == ({"open": 10.0, "high": 12.0, "low": 9.0, "close": 11.0, "volume": 1000},)
    assert market_result.dataset_refs == ("dataset:daily_bar:CN_A:row-1",)
    assert news_result.status == DataResultStatus.MISSING
    assert news_result.rows == ()
    assert news_result.dataset_refs == ()
    assert [gap.reason for gap in news_result.gaps] == [GapReason.WAREHOUSE_MISSING]
    assert news_result.freshness["coverage_by_request"] == (
        {
            "request_id": "req-news",
            "data_type": "company_news",
            "dataset_refs": (),
        },
    )


def test_data_service_missing_coverage_item_is_empty_for_request() -> None:
    events: list[str] = []
    service = DataService(
        query_planner=_Planner(events),
        warehouse=_MissingCoverageItemWarehouse(),
        provider_selector=_Selector(events),
        coalescer=_Coalescer(events),
        batch_planner=_BatchPlanner(events),
        execution_gate=_ExecutionGate(events),
        fetch_engine=_FetchEngine(events),
        ingest=_Ingest(events),
    )
    market_request = _request("req-market").model_copy(update={"freshness_policy": "warehouse_only"})
    news_payload = _request("req-news").model_dump()
    news_payload.update(
        {
            "data_type": "company_news",
            "granularity": "event",
            "fields": ("title", "published_at", "source", "summary", "url"),
            "freshness_policy": "warehouse_only",
            "consumer_id": "news_analyst",
        }
    )

    market_result, news_result = service.get_data_batch((market_request, DataRequest.model_validate(news_payload)))

    assert market_result.status == DataResultStatus.READY
    assert news_result.status == DataResultStatus.MISSING
    assert news_result.rows == ()
    assert news_result.dataset_refs == ()
    assert [gap.reason for gap in news_result.gaps] == [GapReason.WAREHOUSE_MISSING]


def test_data_service_slices_by_canonical_dataset_ref_when_rows_lack_scope_metadata() -> None:
    events: list[str] = []
    service = DataService(
        query_planner=_Planner(events),
        warehouse=_CanonicalRefMixedWarehouse(),
        provider_selector=_Selector(events),
        coalescer=_Coalescer(events),
        batch_planner=_BatchPlanner(events),
        execution_gate=_ExecutionGate(events),
        fetch_engine=_FetchEngine(events),
        ingest=_Ingest(events),
    )

    result = service.get_data(_request("req-canonical-ref-slice"))

    assert result.status == DataResultStatus.READY
    assert result.rows == ({"open": 10.0, "high": 12.0, "low": 9.0, "close": 11.0, "volume": 1000},)
    assert result.dataset_refs == ("dataset:daily_bar:CN_A:row-1",)


def test_data_service_company_name_resolver_uses_quote_snapshot_when_local_name_missing() -> None:
    events: list[str] = []
    service = DataService(
        query_planner=_Planner(events),
        warehouse=_NameLookupWarehouse(),
        provider_selector=_Selector(events),
        coalescer=_Coalescer(events),
        batch_planner=_BatchPlanner(events),
        execution_gate=_ExecutionGate(events),
        fetch_engine=_FetchEngine(events),
        ingest=_Ingest(events),
    )

    names = service.resolve_company_names(market="CN_A", symbol_ids=("688017.SH",))

    assert names == {"688017.SH": "绿的谐波"}


def test_data_service_select_universe_refresh_skips_pre_refresh_warehouse_check() -> None:
    events: list[str] = []
    service = DataService(
        query_planner=_Planner(events),
        warehouse=_Warehouse(events, satisfied_on_check=False),
        provider_selector=_Selector(events),
        coalescer=_Coalescer(events),
        batch_planner=_BatchPlanner(events),
        execution_gate=_ExecutionGate(events),
        fetch_engine=_FetchEngine(events),
        ingest=_Ingest(events),
    )
    payload = _request("sel-unit:selection:universe_refresh:1:all_a_shares:daily_bar").model_dump()
    payload.update({"symbol_id": None, "universe_ref": "all_a_shares", "consumer": "select", "consumer_id": "sel-unit"})

    result = service.get_data(DataRequest.model_validate(payload))

    assert result.status == DataResultStatus.READY
    assert "warehouse.check" not in events
    assert events == [
        "query_planner.validate_and_normalize",
        "provider_selector.select_candidates",
        "provider_selector.read_capabilities",
        "coalescer.coalesce",
        "batch_planner.build_batches",
        "execution_gate.enter",
        "fetch_engine.fetch",
        "ingest.ingest",
        "execution_gate.publish_shared_result",
        "warehouse.recheck",
    ]


def test_data_service_ready_result_drops_superseded_ingest_gaps() -> None:
    events: list[str] = []
    service = DataService(
        query_planner=_Planner(events),
        warehouse=_Warehouse(events, satisfied_on_check=False),
        provider_selector=_Selector(events),
        coalescer=_Coalescer(events),
        batch_planner=_BatchPlanner(events),
        execution_gate=_ExecutionGate(events),
        fetch_engine=_FetchEngine(events),
        ingest=_IngestWithSupersededGap(events),
    )

    result = service.get_data(_request("req-superseded-gap"))

    assert result.status == DataResultStatus.READY
    assert result.dataset_refs == ("dataset:recheck",)
    assert result.raw_refs == ("raw:backup",)
    assert result.attempt_refs == ("attempt:backup",)
    assert result.gaps == ()


def test_data_service_drops_crypto_local_empty_gap_after_provider_fill() -> None:
    events: list[str] = []
    service = DataService(
        query_planner=_Planner(events),
        warehouse=_CryptoLocalEmptyWarehouse(events),
        provider_selector=_Selector(events),
        coalescer=_Coalescer(events),
        batch_planner=_BatchPlanner(events),
        execution_gate=_ExecutionGate(events),
        fetch_engine=_FetchEngine(events),
        ingest=_Ingest(events),
    )

    result = service.get_data(_crypto_request("req-crypto-local-empty"))

    assert result.status == DataResultStatus.READY
    assert result.dataset_refs == ("dataset:daily_bar:CRYPTO:SOLUSDT:2026-06-01:2026-06-07",)
    assert result.attempt_refs == ("attempt:local-seed", "attempt:1")
    assert result.gaps == ()


def test_data_service_drops_crypto_date_range_gap_after_provider_fill() -> None:
    events: list[str] = []
    service = DataService(
        query_planner=_Planner(events),
        warehouse=_CryptoDateRangeMissingWarehouse(events),
        provider_selector=_Selector(events),
        coalescer=_Coalescer(events),
        batch_planner=_BatchPlanner(events),
        execution_gate=_ExecutionGate(events),
        fetch_engine=_FetchEngine(events),
        ingest=_Ingest(events),
    )

    result = service.get_data(_crypto_request("req-crypto-date-range-missing"))

    assert result.status == DataResultStatus.READY
    assert result.dataset_refs == ("dataset:daily_bar:CRYPTO:SOLUSDT:2026-06-01:2026-06-07",)
    assert result.attempt_refs == ("attempt:local-seed-partial", "attempt:1")
    assert result.gaps == ()


def test_data_service_slices_multi_metric_rows_by_requested_fields() -> None:
    funding_request = DataRequest.model_validate(
        {
            "request_id": "req-funding",
            "market": "CRYPTO",
            "symbol_id": "SOLUSDT",
            "exchange": "BINANCE",
            "currency": "USDT",
            "timezone": "UTC",
            "calendar": "CRYPTO_24_7",
            "base_asset": "SOL",
            "quote_asset": "USDT",
            "data_type": "crypto_derivative_metric",
            "granularity": "1h",
            "fields": ("funding_rate", "timestamp", "symbol_id"),
            "date_range_start": date(2026, 6, 1),
            "date_range_end": date(2026, 6, 7),
            "freshness_policy": "trading_day",
            "consumer": "report",
            "consumer_id": "market_analyst",
            "as_of": datetime(2026, 6, 7, tzinfo=UTC),
        }
    )

    assert DataService._row_matches_request(
        {
            "dataset": "crypto_derivative_metric",
            "market": "CRYPTO",
            "symbol_id": "SOLUSDT",
            "granularity": "1h",
            "period_start": "2026-06-02",
            "period_end": "2026-06-02",
            "funding_rate": 0.0001,
            "timestamp": "2026-06-02T00:00:00Z",
        },
        funding_request,
    )
    assert not DataService._row_matches_request(
        {
            "dataset": "crypto_derivative_metric",
            "market": "CRYPTO",
            "symbol_id": "SOLUSDT",
            "granularity": "1h",
            "period_start": "2026-06-02",
            "period_end": "2026-06-02",
            "long_short_ratio": 1.2,
            "timestamp": "2026-06-02T00:00:00Z",
        },
        funding_request,
    )


def test_data_service_skips_recheck_when_remote_attempt_writes_no_dataset_refs() -> None:
    events: list[str] = []
    service = DataService(
        query_planner=_Planner(events),
        warehouse=_Warehouse(events, satisfied_on_check=False),
        provider_selector=_Selector(events),
        coalescer=_Coalescer(events),
        batch_planner=_BatchPlanner(events),
        execution_gate=_ExecutionGate(events),
        fetch_engine=_FetchEngine(events),
        ingest=_IngestWithoutDatasetRefs(events),
    )

    result = service.get_data(_request("req-no-dataset"))

    assert result.status == DataResultStatus.MISSING
    assert result.dataset_refs == ()
    assert result.raw_refs == ("raw:failed",)
    assert result.attempt_refs == ("attempt:failed",)
    assert "warehouse.recheck" not in events


def test_data_service_dedupes_identical_gaps() -> None:
    gap = DataGap.by_reason(
        "field_missing",
        request_id="req-gap",
        market=Market.CN_A,
        data_type="daily_bar",
        granularity="daily",
        required_fields=("volume",),
        symbol_id="600519.SH",
    )
    duplicate = gap.model_copy(update={"gap_id": "gap:req-gap:field_missing:duplicate"})

    assert DataService._dedupe_gaps((gap, duplicate)) == [gap]


def test_data_service_coerces_data_gap_request_id_to_target_result() -> None:
    original = DataGap.by_reason(
        "empty_result",
        request_id="batch:req",
        market=Market.CRYPTO,
        data_type="crypto_derivative_metric",
        granularity="1h",
        symbol_id="SOLUSDT",
    )

    coerced = DataService._coerce_gap(original, request_id="result:req")

    assert coerced.request_id == "result:req"
    assert coerced.gap_id.startswith("gap:result:req:empty_result:")
    assert original.request_id == "batch:req"


def test_data_service_drops_field_missing_gap_when_final_rows_have_required_fields() -> None:
    gap = DataGap.by_reason(
        "field_missing",
        request_id="req-onchain",
        market=Market.CRYPTO,
        data_type="crypto_onchain_metric",
        granularity="daily",
        required_fields=("value_unit",),
        symbol_id="BTCUSDT",
    )

    assert DataService._drop_satisfied_field_missing_gaps(
        (gap,),
        (
            {
                "dataset": "crypto_onchain_metric",
                "market": "CRYPTO",
                "symbol_id": "BTCUSDT",
                "granularity": "daily",
                "metric": "ahr999",
                "value": 0.318,
                "value_unit": "dimensionless",
                "field_set": ("metric", "value", "value_unit"),
            },
        ),
    ) == []


def test_data_service_drops_field_missing_gap_when_no_rows_and_concrete_provider_gap_exists() -> None:
    field_gap = DataGap.by_reason(
        "field_missing",
        request_id="req-taker",
        market=Market.CRYPTO,
        data_type="crypto_derivative_metric",
        granularity="1h",
        required_fields=("taker_buy_sell_ratio",),
        symbol_id="SOLUSDT",
    )
    rate_limited = DataGap.by_reason(
        "rate_limited",
        request_id="req-taker",
        market=Market.CRYPTO,
        data_type="crypto_derivative_metric",
        granularity="1h",
        symbol_id="SOLUSDT",
        evidence_refs=("rate_limit:coinglass",),
    )

    assert DataService._drop_shadowed_field_missing_gaps((field_gap, rate_limited), ()) == [rate_limited]


def test_data_service_keeps_field_missing_gap_when_it_is_the_only_evidence() -> None:
    field_gap = DataGap.by_reason(
        "field_missing",
        request_id="req-taker",
        market=Market.CRYPTO,
        data_type="crypto_derivative_metric",
        granularity="1h",
        required_fields=("taker_buy_sell_ratio",),
        symbol_id="SOLUSDT",
    )

    assert DataService._drop_shadowed_field_missing_gaps((field_gap,), ()) == [field_gap]


def test_data_service_drops_local_warehouse_empty_gap_when_rows_are_filled() -> None:
    gap = DataGap.by_reason(
        "warehouse_missing",
        request_id="req-intraday",
        market=Market.CRYPTO,
        data_type="intraday_bar",
        granularity="1h",
        symbol_id="BTCUSDT",
        message="local_warehouse_empty: local CRYPTO seed warehouse has no bars for BTCUSDT",
    )

    assert DataService._drop_filled_local_warehouse_empty_gaps(
        (gap,),
        (
            {
                "dataset": "intraday_bar",
                "market": "CRYPTO",
                "symbol_id": "BTCUSDT",
                "granularity": "1h",
                "period_start": "2026-06-01T00:00:00Z",
            },
        ),
    ) == []


def test_data_service_batch_uses_unified_ten_step_flow_once() -> None:
    events: list[str] = []
    service = DataService(
        query_planner=_Planner(events),
        warehouse=_Warehouse(events, satisfied_on_check=False),
        provider_selector=_Selector(events),
        coalescer=_Coalescer(events),
        batch_planner=_BatchPlanner(events),
        execution_gate=_ExecutionGate(events),
        fetch_engine=_FetchEngine(events),
        ingest=_Ingest(events),
    )
    req1 = _request("req-batch-1")
    req2 = _request("req-batch-2")
    results = service.get_data_batch((req1, req2))
    assert [item.request_id for item in results] == ["req-batch-1", "req-batch-2"]
    assert all(item.status == DataResultStatus.READY for item in results)
    assert events == [
        "query_planner.validate_and_normalize_many",
        "warehouse.check",
        "provider_selector.select_candidates",
        "provider_selector.read_capabilities",
        "coalescer.coalesce",
        "batch_planner.build_batches",
        "execution_gate.enter",
        "fetch_engine.fetch",
        "ingest.ingest",
        "execution_gate.publish_shared_result",
        "warehouse.recheck",
    ]


def test_data_service_execute_plan_passes_plan_start_to_scheduler() -> None:
    events: list[str] = []
    scheduler = _Scheduler(events)
    service = DataService(
        query_planner=_Planner(events),
        warehouse=_Warehouse(events, satisfied_on_check=False),
        provider_selector=_Selector(events),
        coalescer=_Coalescer(events),
        batch_planner=_BatchPlanner(events),
        scheduler=scheduler,
        execution_gate=_ExecutionGate(events),
        fetch_engine=_FetchEngine(events),
        ingest=_Ingest(events),
    )
    plan = service.plan_batch((_request("req-scheduled"),))

    service.execute_plan(plan)

    assert scheduler.context is not None
    assert scheduler.context.run_id == plan.plan_id
    assert scheduler.context.run_started_at == plan.created_at
    assert events == [
        "query_planner.validate_and_normalize_many",
        "warehouse.check",
        "provider_selector.select_candidates",
        "provider_selector.read_capabilities",
        "coalescer.coalesce",
        "batch_planner.build_batches",
        "scheduler.schedule",
        "execution_gate.enter",
        "fetch_engine.fetch",
        "ingest.ingest",
        "execution_gate.publish_shared_result",
        "warehouse.recheck",
    ]


def test_data_service_batch_slices_recheck_dataset_refs_per_request() -> None:
    events: list[str] = []
    service = DataService(
        query_planner=_Planner(events),
        warehouse=_MultiSymbolWarehouse(),
        provider_selector=_Selector(events),
        coalescer=_Coalescer(events),
        batch_planner=_BatchPlanner(events),
        execution_gate=_ExecutionGate(events),
        fetch_engine=_FetchEngine(events),
        ingest=_Ingest(events),
    )

    results = service.get_data_batch(
        (
            _symbol_request("req-a", "600519.SH"),
            _symbol_request("req-b", "000001.SZ"),
            _symbol_request("req-c", "999999.SH"),
        )
    )

    assert results[0].dataset_refs == ("dataset:600519",)
    assert results[0].rows == (
        {
            "dataset": "daily_bar",
            "market": "CN_A",
            "symbol_id": "600519.SH",
            "granularity": "daily",
            "close": 2.0,
        },
    )
    assert results[1].dataset_refs == ("dataset:000001",)
    assert results[1].rows == (
        {
            "dataset": "daily_bar",
            "market": "CN_A",
            "symbol_id": "000001.SZ",
            "granularity": "daily",
            "close": 3.0,
        },
    )
    assert results[2].status == DataResultStatus.MISSING
    assert results[2].dataset_refs == ()
    assert results[2].gaps[0].reason == GapReason.WAREHOUSE_MISSING


def test_data_service_batch_ready_request_not_downgraded_by_other_request_gap() -> None:
    events: list[str] = []
    service = DataService(
        query_planner=_Planner(events),
        warehouse=_PartialBatchWarehouse(),
        provider_selector=_Selector(events),
        coalescer=_Coalescer(events),
        batch_planner=_BatchPlanner(events),
        execution_gate=_ExecutionGate(events),
        fetch_engine=_FetchEngine(events),
        ingest=_IngestWithSupersededGap(events),
    )

    results = service.get_data_batch(
        (
            _symbol_request("req-a", "600519.SH"),
            _symbol_request("req-b", "000001.SZ"),
        )
    )

    assert results[0].status == DataResultStatus.READY
    assert results[0].dataset_refs == ("dataset:600519",)
    assert results[0].gaps == ()
    assert results[1].status == DataResultStatus.MISSING
    assert results[1].gaps[0].request_id == "req-b"


def test_data_service_rechecks_between_provider_priority_waves_and_skips_slow_fallback() -> None:
    events: list[str] = []
    service = DataService(
        query_planner=_Planner(events),
        warehouse=_WaveWarehouse(events),
        provider_selector=_FallbackSelector(events),
        coalescer=_PassThroughCoalescer(events),
        batch_planner=_CandidateBatchPlanner(events),
        execution_gate=_ExecutionGate(events),
        fetch_engine=_ProviderTrackingFetchEngine(events),
        ingest=_Ingest(events),
    )

    [result] = service.get_data_batch((_request("req-wave"),))

    assert result.status == DataResultStatus.READY
    assert result.dataset_refs == ("dataset:wave",)
    assert "fetch:provider-fast" in events
    assert "fetch:provider-slow" not in events


def test_data_service_tries_next_provider_after_remote_rate_limit_without_user_limit() -> None:
    events: list[str] = []
    service = DataService(
        query_planner=_Planner(events),
        warehouse=_WaveWarehouse(events),
        provider_selector=_FallbackSelector(events),
        coalescer=_PassThroughCoalescer(events),
        batch_planner=_CandidateBatchPlanner(events),
        execution_gate=_ExecutionGate(events),
        fetch_engine=_RateLimitThenSuccessFetchEngine(events),
        ingest=_RateLimitAwareIngest(events),
    )

    [result] = service.get_data_batch((_request("req-rate-limit-next-provider"),))

    assert result.status == DataResultStatus.READY
    assert result.dataset_refs == ("dataset:wave",)
    assert "fetch:provider-fast" in events
    assert "fetch:provider-slow" in events
    assert events.index("fetch:provider-fast") < events.index("fetch:provider-slow")


def test_data_service_retries_same_provider_after_remote_rate_limit_when_user_policy_waits() -> None:
    events: list[str] = []
    service = DataService(
        query_planner=_Planner(events),
        warehouse=_WaveWarehouse(events),
        provider_selector=_FallbackSelector(events),
        coalescer=_PassThroughCoalescer(events),
        batch_planner=_CandidateBatchPlanner(events),
        execution_gate=_RetryAfterRateLimitExecutionGate(events),
        fetch_engine=_RateLimitThenRetrySuccessFetchEngine(events),
        ingest=_RateLimitAwareIngest(events),
    )

    [result] = service.get_data_batch((_request("req-rate-limit-retry-provider"),))

    assert result.status == DataResultStatus.READY
    assert result.dataset_refs == ("dataset:wave",)
    assert events.count("fetch:provider-fast") == 2
    assert "execution_gate.wait_after_rate_limited_fetch" in events
    assert "fetch:provider-slow" not in events


def test_data_service_records_credential_missing_selector_skip_when_public_provider_succeeds() -> None:
    events: list[str] = []
    ingest = _Ingest(events)
    service = DataService(
        query_planner=_Planner(events),
        warehouse=_Warehouse(events, satisfied_on_check=False),
        provider_selector=_CredentialSkipSelector(events),
        coalescer=_PassThroughCoalescer(events),
        batch_planner=_CandidateBatchPlanner(events),
        execution_gate=_ExecutionGate(events),
        fetch_engine=_ProviderTrackingFetchEngine(events),
        ingest=ingest,
    )

    result = service.get_data(_request("req-credential-skip"))

    assert result.status == DataResultStatus.READY
    assert result.attempt_refs == ("attempt:skip:paid-provider", "attempt:1")
    assert "fetch:paid-provider" not in events
    assert "fetch:public-provider" in events
    assert "ingest.record_selector_skip" in events
    assert events.index("ingest.record_selector_skip") < events.index("provider_selector.read_capabilities")
    assert len(ingest.selector_skip_ingests) == 1
    skip_ingest = ingest.selector_skip_ingests[0]
    assert skip_ingest.status == "non_remote_recorded"
    assert skip_ingest.remote_success is False
    assert [gap.reason for gap in skip_ingest.gaps] == [GapReason.CREDENTIAL_MISSING]
    skip_batch, selector_skip = ingest.selector_skips[0]
    assert skip_batch.provider_id == "paid-provider"
    assert selector_skip.remote_attempted is False


def test_data_service_does_not_report_unconfigured_crypto_paid_sources_as_credential_gaps() -> None:
    repository = DatasetRepository()
    service = DataService(
        query_planner=QueryPlanner(),
        warehouse=_AlwaysMissingWarehouse(),
        provider_selector=ProviderSelector(
            build_minimal_provider_registry(),
            credential_resolver=_credential_resolver(()),
        ),
        coalescer=_NoRemoteCoalescer(),
        batch_planner=_NoRemoteBatchPlanner(),
        execution_gate=_NoRemoteExecutionGate(),
        fetch_engine=_NoRemoteFetchEngine(),
        ingest=_real_ingest(repository),
    )
    requests = tuple(
        DataRequest.model_validate({**request.model_dump(), "source_role_required": "paid_data"})
        for request in (
            _crypto_source_state_request(
                request_id="req-crypto-glassnode",
                data_type="crypto_onchain_metric",
                granularity="daily",
                fields=("timestamp", "metric", "value", "value_unit", "chain", "source_metric"),
            ),
        )
    )

    results = service.get_data_batch(requests)

    assert {result.request_id for result in results} == {
        "req-crypto-glassnode",
    }
    for result in results:
        assert result.status == DataResultStatus.MISSING
        assert [gap.reason for gap in result.gaps] == [GapReason.WAREHOUSE_MISSING]
        assert result.attempt_refs
    attempts = tuple(
        repository.get_provider_attempt(attempt_ref)
        for result in results
        for attempt_ref in result.attempt_refs
    )
    attempt_statuses = {
        (attempt["provider"], attempt["endpoint"], attempt["status"], attempt["remote_attempted"], attempt["gap_codes"])
        for attempt in attempts
        if attempt is not None
    }
    assert {
        ("crypto_glassnode_onchain", "deep_onchain_metrics", "source_not_configured", False, ()),
    }.issubset(attempt_statuses)
    assert all(status == "source_not_configured" for _provider, _endpoint, status, _attempted, _gaps in attempt_statuses)


class _WaveWarehouse:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    def check(self, checks, coverage) -> WarehouseResult:
        del coverage
        self._events.append("warehouse.check")
        return WarehouseResult(satisfied=False, gaps=(_gap(checks[0].request_id),))

    def recheck(self, checks, coverage) -> WarehouseResult:
        del checks, coverage
        self._events.append("warehouse.recheck")
        if "fetch:provider-fast" not in self._events:
            return WarehouseResult(satisfied=False, gaps=(_gap("req-wave"),))
        return WarehouseResult(
            satisfied=True,
            rows=({"close": 9.0},),
            dataset_refs=("dataset:wave",),
            freshness={"policy": "trading_day"},
        )


class _FallbackSelector:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    def select_candidates(self, gaps, plan):
        del plan
        self._events.append("provider_selector.select_candidates")
        request_id = gaps[0].request_id
        return (
            {
                "request_id": request_id,
                "provider_id": "provider-fast",
                "endpoint_id": "daily",
                "market": Market.CN_A,
                "data_type": "daily_bar",
                "granularity": "daily",
                "source_role": "built_in_public",
                "priority_rank": 1,
                "symbol_id": "600519.SH",
                "fields": ("close",),
            },
            {
                "request_id": request_id,
                "provider_id": "provider-slow",
                "endpoint_id": "daily",
                "market": Market.CN_A,
                "data_type": "daily_bar",
                "granularity": "daily",
                "source_role": "built_in_public",
                "priority_rank": 9,
                "symbol_id": "600519.SH",
                "fields": ("close",),
            },
        )

    def read_capabilities(self, candidates):
        self._events.append("provider_selector.read_capabilities")
        return {"count": len(candidates)}


class _CredentialSkipSelector:
    def __init__(self, events: list[str]) -> None:
        self._events = events
        self.skipped_candidates: tuple[SimpleNamespace, ...] = ()

    def select_candidates(self, gaps, plan):
        del plan
        self._events.append("provider_selector.select_candidates")
        request_id = gaps[0].request_id
        self.skipped_candidates = (
            SimpleNamespace(
                request_id=request_id,
                provider_id="paid-provider",
                endpoint_id="daily",
                market=Market.CN_A,
                data_type="daily_bar",
                granularity="daily",
                source_role="paid_data",
                reason="credential_missing",
                remote_attempted=False,
                credential_names=("data_source:paid",),
            ),
        )
        return (
            {
                "request_id": request_id,
                "provider_id": "public-provider",
                "endpoint_id": "daily",
                "market": Market.CN_A,
                "data_type": "daily_bar",
                "granularity": "daily",
                "source_role": "built_in_public",
                "priority_rank": 10,
                "symbol_id": "600519.SH",
                "fields": ("close",),
            },
        )

    def read_capabilities(self, candidates):
        self._events.append("provider_selector.read_capabilities")
        return {"count": len(candidates)}


class _PassThroughCoalescer:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    def coalesce(self, gaps, candidates, capabilities):
        del gaps, capabilities
        self._events.append("coalescer.coalesce")
        return tuple(candidates)


class _CandidateBatchPlanner:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    def build_batches(self, groups, capabilities):
        del capabilities
        self._events.append("batch_planner.build_batches")
        return tuple(
            _batch_plan(group["request_id"]).model_copy(
                update={
                    "batch_id": f"batch:{group['provider_id']}",
                    "provider_id": group["provider_id"],
                    "endpoint_id": group["endpoint_id"],
                    "priority_rank": group["priority_rank"],
                }
            )
            for group in groups
        )


class _ProviderTrackingFetchEngine:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    def fetch(self, batch):
        self._events.append(f"fetch:{batch.provider_id}")
        return FetchResult(
            fetch_id=f"fetch:{batch.provider_id}",
            batch_id=batch.batch_id,
            provider_id=batch.provider_id,
            endpoint_id=batch.endpoint_id,
            market=batch.market,
            symbol_ids=batch.symbol_ids,
            status=FetchStatus.SUCCESS,
            payload={"rows": [{"close": 9.0}]},
            row_count=1,
            fetched_at=datetime(2026, 5, 31, tzinfo=UTC),
        )


class _RateLimitThenSuccessFetchEngine:
    def __init__(self, events: list[str]) -> None:
        self._events = events

    def fetch(self, batch):
        self._events.append(f"fetch:{batch.provider_id}")
        if batch.provider_id == "provider-fast":
            return FetchResult(
                fetch_id=f"fetch:{batch.provider_id}",
                batch_id=batch.batch_id,
                provider_id=batch.provider_id,
                endpoint_id=batch.endpoint_id,
                market=batch.market,
                symbol_ids=batch.symbol_ids,
                status=FetchStatus.RATE_LIMITED,
                error_code="http_429",
                error_message="rate_limited",
                fetched_at=datetime(2026, 5, 31, tzinfo=UTC),
            )
        return FetchResult(
            fetch_id=f"fetch:{batch.provider_id}",
            batch_id=batch.batch_id,
            provider_id=batch.provider_id,
            endpoint_id=batch.endpoint_id,
            market=batch.market,
            symbol_ids=batch.symbol_ids,
            status=FetchStatus.SUCCESS,
            payload={"rows": [{"close": 9.0}]},
            row_count=1,
            fetched_at=datetime(2026, 5, 31, tzinfo=UTC),
        )


class _RateLimitThenRetrySuccessFetchEngine:
    def __init__(self, events: list[str]) -> None:
        self._events = events
        self._attempts_by_provider: dict[str, int] = {}

    def fetch(self, batch):
        self._events.append(f"fetch:{batch.provider_id}")
        attempt = self._attempts_by_provider.get(batch.provider_id, 0)
        self._attempts_by_provider[batch.provider_id] = attempt + 1
        if batch.provider_id == "provider-fast" and attempt == 0:
            return FetchResult(
                fetch_id=f"fetch:{batch.provider_id}:rate-limited",
                batch_id=batch.batch_id,
                provider_id=batch.provider_id,
                endpoint_id=batch.endpoint_id,
                market=batch.market,
                symbol_ids=batch.symbol_ids,
                status=FetchStatus.RATE_LIMITED,
                error_code="http_429",
                error_message="rate_limited",
                fetched_at=datetime(2026, 5, 31, tzinfo=UTC),
            )
        return FetchResult(
            fetch_id=f"fetch:{batch.provider_id}:success",
            batch_id=batch.batch_id,
            provider_id=batch.provider_id,
            endpoint_id=batch.endpoint_id,
            market=batch.market,
            symbol_ids=batch.symbol_ids,
            status=FetchStatus.SUCCESS,
            payload={"rows": [{"close": 9.0}]},
            row_count=1,
            fetched_at=datetime(2026, 5, 31, tzinfo=UTC),
        )


class _RateLimitAwareIngest(_Ingest):
    def ingest(self, result, batch):
        self._events.append("ingest.ingest")
        if result.status == FetchStatus.RATE_LIMITED:
            return IngestResult(
                ingest_id=f"ingest:{batch.provider_id}",
                batch_id=batch.batch_id,
                status="failed",
                attempt_refs=(f"attempt:{batch.provider_id}",),
                gaps=(
                    DataGap.by_reason(
                        "rate_limited",
                        request_id=batch.request_ids[0],
                        market=batch.market,
                        data_type=batch.data_type,
                        granularity=batch.granularity,
                        symbol_id=batch.symbol_ids[0] if batch.symbol_ids else None,
                        evidence_refs=(f"attempt:{batch.provider_id}",),
                    ),
                ),
                remote_success=False,
                created_at=datetime(2026, 5, 31, tzinfo=UTC),
            )
        return IngestResult(
            ingest_id=f"ingest:{batch.provider_id}",
            batch_id=batch.batch_id,
            status="ingested",
            dataset_refs=(f"dataset:{batch.provider_id}",),
            raw_refs=(f"raw:{batch.provider_id}",),
            attempt_refs=(f"attempt:{batch.provider_id}",),
            remote_success=True,
            created_at=datetime(2026, 5, 31, tzinfo=UTC),
        )
