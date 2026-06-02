from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from claw_trade.data_gateway.coordination.batch_planner import ProviderBatchPlanner
from claw_trade.data_gateway.coordination.coalescer import RequestCoalescer
from claw_trade.data_gateway.coordination.provider_selector import ProviderSelector
from claw_trade.data_gateway.coordination.query_planner import QueryPlanner
from claw_trade.data_gateway.coordination.service import DataService
from claw_trade.data_gateway.execution.fetch_engine import FetchResult
from claw_trade.data_gateway.ingest.attempt_log import AttemptLog
from claw_trade.data_gateway.ingest.normalizer import Normalizer
from claw_trade.data_gateway.ingest.normalized_store import NormalizedStore
from claw_trade.data_gateway.ingest.pipeline import IngestPipeline
from claw_trade.data_gateway.ingest.raw_store import RawStore
from claw_trade.data_gateway.models import (
    DataRequest,
    DataResultStatus,
    GapReason,
    IngestResult,
)
from claw_trade.data_gateway.providers.registry import CapabilitySnapshot, ProviderRegistry
from claw_trade.data_gateway.warehouse import DatasetRepository, Warehouse


@dataclass(frozen=True)
class BatchPolicy:
    supports_batch: bool
    batch_by: str
    max_symbols_per_call: int | None = None
    max_days_per_call: int | None = None
    mergeable_fields: tuple[str, ...] = ()
    pagination_policy: object = "none"
    split_policy: object = "strict"


@dataclass(frozen=True)
class CredentialPolicy:
    credential_required: bool
    credential_names: tuple[str, ...]
    credential_scope: str | None
    missing_behavior: str


@dataclass(frozen=True)
class LicensePolicy:
    raw_storage_mode: str
    normalized_storage_allowed: bool
    redistribution_allowed: bool
    retention_days: int | None


@dataclass(frozen=True)
class EndpointCapability:
    endpoint_id: str
    market: str
    data_type: str
    source_role: str
    supported_granularities: tuple[str, ...]
    coverage_fields: tuple[str, ...]
    freshness_supported: tuple[str, ...]
    http_visibility: str
    batch_policy: BatchPolicy
    priority_rank: int | None = None
    rate_limit_policy: object | None = None
    license_policy: LicensePolicy | None = None
    can_be_formal_fact_source: bool | None = None


@dataclass(frozen=True)
class ProviderCapabilities:
    provider_id: str
    plugin_version: str
    endpoints: tuple[EndpointCapability, ...]
    credentials: CredentialPolicy
    license_policy: LicensePolicy
    default_rate_limit_policy: object
    default_priority_rank: int = 100


class InMemoryCapabilityPlugin:
    def __init__(self, capabilities: ProviderCapabilities) -> None:
        self._capabilities = capabilities
        self.plugin_id = capabilities.provider_id
        self.version = capabilities.plugin_version

    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    def build_fetch_tasks(self, batch: object) -> tuple[object, ...]:
        del batch
        return ()

    def fetch(self, task: object, ctx: object) -> object:
        del task
        del ctx
        raise NotImplementedError


def _plugin() -> InMemoryCapabilityPlugin:
    capabilities = ProviderCapabilities(
        provider_id="official_feed",
        plugin_version="1.0.0",
        endpoints=(
            EndpointCapability(
                endpoint_id="daily",
                market="CN_A",
                data_type="daily_bar",
                source_role="official",
                supported_granularities=("daily",),
                coverage_fields=("close",),
                freshness_supported=("trading_day",),
                http_visibility="managed_http",
                batch_policy=BatchPolicy(
                    supports_batch=True,
                    batch_by="symbol",
                    max_symbols_per_call=10,
                    mergeable_fields=("close",),
                ),
                priority_rank=1,
            ),
        ),
        credentials=CredentialPolicy(
            credential_required=False,
            credential_names=(),
            credential_scope=None,
            missing_behavior="credential_missing",
        ),
        license_policy=LicensePolicy(
            raw_storage_mode="metadata_only",
            normalized_storage_allowed=True,
            redistribution_allowed=False,
            retention_days=30,
        ),
        default_rate_limit_policy={"window_seconds": 60, "max_calls": 20},
    )
    return InMemoryCapabilityPlugin(capabilities)


class TrackingCoalescer(RequestCoalescer):
    def __init__(self) -> None:
        super().__init__()
        self.capabilities_seen: CapabilitySnapshot | None = None

    def coalesce(self, gaps, candidates, capabilities):  # type: ignore[override]
        self.capabilities_seen = capabilities
        return super().coalesce(gaps, candidates, capabilities)


class TrackingBatchPlanner(ProviderBatchPlanner):
    def __init__(self) -> None:
        super().__init__()
        self.capabilities_seen: CapabilitySnapshot | None = None

    def build_batches(self, groups, capabilities):  # type: ignore[override]
        self.capabilities_seen = capabilities
        return super().build_batches(groups, capabilities)


class _Gate:
    def enter(self, batch):
        del batch
        return type("Gate", (), {"kind": "cache_hit", "owner_token": None, "evidence_refs": ()})()

    def publish_shared_result(self, single_flight_key: str, owner_token: str, ingest: IngestResult) -> None:
        del single_flight_key
        del owner_token
        del ingest


class _Fetch:
    def fetch(self, batch):
        raise AssertionError(f"cache_hit should skip fetch, got {batch}")


class _Ingest:
    def record_gate_result(self, batch, gate):
        del gate
        return IngestResult(
            ingest_id="ingest:gate",
            batch_id=batch.batch_id,
            status="non_remote_recorded",
            attempt_refs=("attempt:gate",),
            remote_success=False,
            created_at=datetime(2026, 5, 31, tzinfo=UTC),
        )

    def ingest(self, result, batch):
        raise AssertionError(f"cache_hit should skip ingest, got {result} {batch}")


def _request() -> DataRequest:
    return DataRequest.model_validate(
        {
            "request_id": "req-real-flow",
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
            "as_of": datetime.now(tz=UTC),
        }
    )


def test_real_components_can_be_composed_with_capability_snapshot_contract() -> None:
    registry = ProviderRegistry()
    registry.register(_plugin())

    coalescer = TrackingCoalescer()
    batch_planner = TrackingBatchPlanner()
    service = DataService(
        query_planner=QueryPlanner(),
        warehouse=Warehouse(DatasetRepository(records=())),
        provider_selector=ProviderSelector(registry),
        coalescer=coalescer,
        batch_planner=batch_planner,
        execution_gate=_Gate(),
        fetch_engine=_Fetch(),
        ingest=_Ingest(),
    )

    result = service.get_data(_request())
    assert result.status == DataResultStatus.MISSING
    assert result.attempt_refs == ("attempt:gate",)
    assert any(gap.reason == GapReason.WAREHOUSE_MISSING for gap in result.gaps)
    assert coalescer.capabilities_seen is not None
    assert batch_planner.capabilities_seen is not None


class _OwnerGate:
    def enter(self, batch):
        del batch
        return type("Gate", (), {"kind": "owner", "owner_token": "owner-1", "evidence_refs": ()})()

    def publish_shared_result(self, single_flight_key: str, owner_token: str, ingest) -> None:
        del single_flight_key
        del owner_token
        del ingest


class _FetchSuccess:
    def fetch(self, batch):
        return FetchResult.from_success(
            batch,
            payload=[
                {
                    "dataset": "daily_bar",
                    "symbol_id": "600519.SH",
                    "close": 1888.0,
                    "exchange": "SSE",
                    "currency": "CNY",
                    "timezone": "Asia/Shanghai",
                    "calendar": "CN_A_SSE_SZSE",
                }
            ],
            row_count=1,
        )


def test_real_fetch_ingest_recheck_closure_returns_ready_with_auditable_refs() -> None:
    repository = DatasetRepository(records=())
    ingest = IngestPipeline(
        raw_store=RawStore(repository=repository),
        normalizer=Normalizer(),
        normalized_store=NormalizedStore(repository=repository),
        attempt_log=AttemptLog(repository=repository),
    )
    registry = ProviderRegistry()
    registry.register(_plugin())
    service = DataService(
        query_planner=QueryPlanner(),
        warehouse=Warehouse(repository),
        provider_selector=ProviderSelector(registry),
        coalescer=RequestCoalescer(),
        batch_planner=ProviderBatchPlanner(),
        execution_gate=_OwnerGate(),
        fetch_engine=_FetchSuccess(),
        ingest=ingest,
    )

    result = service.get_data(_request())
    assert result.status == DataResultStatus.READY
    assert result.dataset_refs
    assert result.raw_refs
    assert result.attempt_refs

    raw_doc = repository.get_raw_payload(result.raw_refs[0])
    assert raw_doc is not None
    attempt_doc = repository.get_provider_attempt(result.attempt_refs[0])
    assert attempt_doc is not None
    assert attempt_doc["remote_success"] is True


class _RateLimitedGate:
    def enter(self, batch):
        del batch
        return type("Gate", (), {"kind": "rate_limited", "owner_token": None, "evidence_refs": ("attempt:rate",)})()

    def publish_shared_result(self, single_flight_key: str, owner_token: str, ingest) -> None:
        del single_flight_key
        del owner_token
        del ingest


class _FetchMustNotRun:
    def fetch(self, batch):
        raise AssertionError(f"rate_limited should skip fetch, got {batch}")


def test_non_remote_rate_limited_path_through_service_and_ingest_does_not_crash() -> None:
    repository = DatasetRepository(records=())
    ingest = IngestPipeline(
        raw_store=RawStore(repository=repository),
        normalizer=Normalizer(),
        normalized_store=NormalizedStore(repository=repository),
        attempt_log=AttemptLog(repository=repository),
    )
    registry = ProviderRegistry()
    registry.register(_plugin())
    service = DataService(
        query_planner=QueryPlanner(),
        warehouse=Warehouse(repository),
        provider_selector=ProviderSelector(registry),
        coalescer=RequestCoalescer(),
        batch_planner=ProviderBatchPlanner(),
        execution_gate=_RateLimitedGate(),
        fetch_engine=_FetchMustNotRun(),
        ingest=ingest,
    )

    result = service.get_data(_request())
    assert result.status == DataResultStatus.MISSING
    assert result.dataset_refs == ()
    assert result.attempt_refs
    attempt_doc = repository.get_provider_attempt(result.attempt_refs[0])
    assert attempt_doc is not None
    assert attempt_doc["remote_success"] is False
