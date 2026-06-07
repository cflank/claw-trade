from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from claw_trade.data_gateway.api import DataAPI
from claw_trade.data_gateway.coordination.batch_planner import ProviderBatchPlanner
from claw_trade.data_gateway.coordination.coalescer import RequestCoalescer
from claw_trade.data_gateway.coordination.provider_selector import ProviderSelector
from claw_trade.data_gateway.coordination.query_planner import QueryPlanner
from claw_trade.data_gateway.coordination.service import DataService
from claw_trade.data_gateway.execution import ProviderResultCache
from claw_trade.data_gateway.execution.fetch_engine import FetchEngine
from claw_trade.data_gateway.execution.gate import ExecutionGate
from claw_trade.data_gateway.execution.rate_limit_policy import RateLimitPolicyResolver
from claw_trade.data_gateway.execution.rate_limiter import RateLimiter
from claw_trade.data_gateway.execution.single_flight import SingleFlight
from claw_trade.data_gateway.ingest.attempt_log import AttemptLog
from claw_trade.data_gateway.ingest.normalized_store import NormalizedStore
from claw_trade.data_gateway.ingest.normalizer import Normalizer
from claw_trade.data_gateway.ingest.pipeline import IngestPipeline
from claw_trade.data_gateway.ingest.raw_store import RawStore
from claw_trade.data_gateway.providers.credentials import DataSourceCredentialResolver
from claw_trade.data_gateway.providers.plugins import iter_minimal_market_plugins
from claw_trade.data_gateway.providers.registry import ProviderRegistry
from claw_trade.data_gateway.settings_store import build_data_source_settings_stores
from claw_trade.data_gateway.warehouse import DatasetRepository, Warehouse
from claw_trade.data_gateway.warehouse.normalized_columnar import NormalizedColumnarWarehouse


@dataclass(frozen=True)
class DataGatewayRuntime:
    data_api: DataAPI
    repository: DatasetRepository
    provider_candidates: tuple[str, ...]


def build_data_api_from_env() -> DataAPI:
    return build_data_gateway_runtime_from_env().data_api


def build_data_gateway_runtime_from_env() -> DataGatewayRuntime:
    database = open_data_gateway_database_from_env()
    repository = DatasetRepository.from_database(
        database,
        normalized_columnar=NormalizedColumnarWarehouse.from_env(),
    )

    registry = ProviderRegistry()
    plugins = iter_minimal_market_plugins()
    for plugin in plugins:
        registry.register(plugin)
    provider_candidates = _provider_candidates_from_plugins(plugins)

    settings_stores = build_data_source_settings_stores(database)
    credential_resolver = DataSourceCredentialResolver(
        data_source_store=settings_stores.data_source_store,
        secret_store=settings_stores.secret_store,
    )
    ingest = IngestPipeline(
        raw_store=RawStore(repository=repository),
        normalizer=Normalizer(),
        normalized_store=NormalizedStore(repository=repository),
        attempt_log=AttemptLog(repository=repository),
    )
    service = DataService(
        query_planner=QueryPlanner(),
        warehouse=Warehouse(repository),
        provider_selector=ProviderSelector(registry, credential_resolver=credential_resolver),
        coalescer=RequestCoalescer(),
        batch_planner=ProviderBatchPlanner(
            rate_limit_policy_resolver=RateLimitPolicyResolver(data_source_settings=credential_resolver),
        ),
        execution_gate=ExecutionGate(
            cache=ProviderResultCache(repository),
            rate_limiter=RateLimiter(repository),
            single_flight=SingleFlight(repository),
        ),
        fetch_engine=FetchEngine(registry, credential_resolver=credential_resolver),
        ingest=ingest,
    )
    return DataGatewayRuntime(
        data_api=DataAPI(service),
        repository=repository,
        provider_candidates=provider_candidates,
    )


def _provider_candidates_from_plugins(plugins: tuple[object, ...]) -> tuple[str, ...]:
    provider_ids: list[str] = []
    for plugin in plugins:
        caps = plugin.capabilities()
        provider_id = str(getattr(caps, "provider_id", "")).strip()
        if provider_id:
            provider_ids.append(provider_id)
    return tuple(dict.fromkeys(provider_ids))


def open_data_gateway_database_from_env() -> Any:
    uri = (os.environ.get("DATA_GATEWAY_MONGODB_URI", "").strip() or os.environ.get("CN_A_MONGODB_URI", "").strip())
    if not uri:
        raise RuntimeError("DATA_GATEWAY_MONGODB_URI is required for data layer access")
    database_name = (
        os.environ.get("DATA_GATEWAY_MONGODB_DATABASE", "").strip()
        or os.environ.get("CN_A_MONGODB_DATABASE", "").strip()
        or _database_name_from_uri(uri)
    )
    from pymongo import MongoClient

    client = MongoClient(uri, serverSelectionTimeoutMS=5000)
    return client[database_name]


def _database_name_from_uri(uri: str) -> str:
    parsed = urlparse(uri)
    path_name = parsed.path.strip("/")
    if path_name:
        return path_name.split("/", 1)[0]
    return "claw_trade"
