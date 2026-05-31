from __future__ import annotations

import inspect
import threading
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import unquote, urlparse

from claw_trade.data_gateway.models import (
    AdmissionCheckStatus,
    CredentialStatus,
    DataGapReason,
    FreshnessPolicy,
    Market,
    NormalizedResult,
    PackDomain,
    PackRequest,
    PrioritySource,
    ProviderCallSpec,
    ProviderCapability,
    ProviderFetch,
    ProviderKind,
    ProviderStatus,
    RunProviderPlan,
    SourceRole,
)
from claw_trade.data_gateway.packs import service as service_module
from claw_trade.data_gateway.packs.service import DomainPackService
from claw_trade.data_gateway.providers import market_adapters as market_adapters_module
from claw_trade.data_gateway.providers.defaults import (
    build_default_provider_registry,
    default_provider_config_version,
    load_default_system_capabilities,
)
from claw_trade.data_gateway.providers.execution import ProviderExecutionEvidenceHelper
from claw_trade.data_gateway.providers.market_adapters import build_default_market_adapters
from claw_trade.data_gateway.providers.run_plan import RunProviderPlanner
from claw_trade.data_gateway.store.attempts import MongoAttemptStore
from claw_trade.data_gateway.store.http_evidence import MongoProviderHttpEvidenceStore
from claw_trade.data_gateway.store.mongo import OPENBB_NORMALIZED
from claw_trade.data_gateway.store.normalized import MongoNormalizedStore
from claw_trade.data_gateway.store.raw_payloads import MongoRawPayloadStore


@dataclass
class _Adapter:
    adapter_id: str
    provider_id: str
    domain: PackDomain
    source_role: SourceRole
    rows: tuple[Mapping[str, Any], ...]
    market: Market = Market.US
    endpoint: str | None = None
    status: ProviderStatus = ProviderStatus.REMOTE_SUCCESS
    credential_missing: bool = False
    adapter_kind: str = "project_extension"
    provider_kind: ProviderKind = ProviderKind.PROJECT_EXTENSION

    def capabilities(self) -> tuple[ProviderCapability, ...]:
        if self.endpoint is None:
            return ()
        return (
            ProviderCapability(
                provider=self.provider_id,
                adapter_id=self.adapter_id,
                provider_kind=self.provider_kind,
                market=self.market,
                domain=self.domain,
                endpoint=self.endpoint,
                source_role=self.source_role,
                expected_schema_id=f"{self.market.value.lower()}.{self.domain.value}.v1",
                license_policy_id="personal_research",
                credential_requirements=(),
                rate_limit_policy_id="test",
                cache_ttl_seconds=300,
                required=True,
                attempt_required=True,
                coverage_group=f"{self.market.value.lower()}_{self.domain.value}",
                coverage_quorum=1,
                priority=0,
                priority_source=PrioritySource.SYSTEM_DEFAULT,
            ),
        )

    def validate_credentials(self) -> CredentialStatus:
        if self.credential_missing:
            return CredentialStatus(
                status=AdmissionCheckStatus.MISSING,
                provider=self.provider_id,
                adapter_id=self.adapter_id,
                missing_keys=(f"{self.provider_id.upper()}_KEY",),
                root_cause=f"missing {self.provider_id} key",
            )
        return CredentialStatus(
            status=AdmissionCheckStatus.PASS,
            provider=self.provider_id,
            adapter_id=self.adapter_id,
        )

    def build_call_specs(self, request: PackRequest) -> tuple[ProviderCallSpec, ...]:
        del request
        return ()

    def fetch(self, spec: ProviderCallSpec, request: PackRequest) -> ProviderFetch:
        del spec, request
        return ProviderFetch(
            payload={"rows": list(self.rows)},
            content_type="application/json",
            source_url="https://example.com/provider",
            is_empty=not self.rows,
            row_count=len(self.rows),
            provider_request_id=f"req-{self.provider_id}",
        )

    def normalize(self, spec: ProviderCallSpec, fetch: ProviderFetch) -> NormalizedResult:
        del spec, fetch
        return NormalizedResult(
            status=self.status,
            schema_id=f"{self.provider_id}.{self.domain.value}.v1",
            rows=self.rows,
            compact_facts={},
            row_count=len(self.rows),
            field_units={},
            currency=None,
            timezone=None,
            source_raw_ref=None,
            error_message=None if self.status == ProviderStatus.REMOTE_SUCCESS else self.status.value,
        )


class _Collection:
    def __init__(self, name: str = "test_collection") -> None:
        self.name = name
        self.docs: dict[str, dict[str, Any]] = {}
        self.raise_write: Exception | None = None
        self._lock = threading.RLock()

    def find_one(self, query: dict[str, Any]) -> dict[str, Any] | None:
        with self._lock:
            key = query["_id"]
            doc = self.docs.get(key)
            if doc is None:
                return None
            return dict(doc) if _matches_query(doc, query) else None

    def find(self, query: dict[str, Any]) -> "_Cursor":
        with self._lock:
            return _Cursor([dict(doc) for doc in self.docs.values() if _matches_query(doc, query)])

    def update_one(self, query: dict[str, Any], update: dict[str, Any], upsert: bool = False) -> None:
        with self._lock:
            if self.raise_write is not None:
                raise self.raise_write
            key = query["_id"]
            current = self.docs.get(key)
            inserted = False
            if current is None:
                if not upsert:
                    return
                current = {}
                inserted = True
            elif not _matches_query(current, query):
                return
            if inserted:
                current.update(update.get("$setOnInsert", {}))
            if "$inc" in update:
                for field, delta in update["$inc"].items():
                    current[field] = int(current.get(field, 0)) + int(delta)
            current.update(update.get("$set", {}))
            if "_id" not in current:
                current["_id"] = key
            self.docs[key] = current

    def replace_one(self, query: dict[str, Any], doc: dict[str, Any], upsert: bool = False) -> None:
        del upsert
        with self._lock:
            if self.raise_write is not None:
                raise self.raise_write
            self.docs[query["_id"]] = dict(doc)

    def insert_one(self, doc: dict[str, Any]) -> None:
        with self._lock:
            if self.raise_write is not None:
                raise self.raise_write
            if doc["_id"] in self.docs:
                from pymongo.errors import DuplicateKeyError

                raise DuplicateKeyError("duplicate")
            self.docs[doc["_id"]] = dict(doc)

    def find_one_and_update(
        self,
        query: dict[str, Any],
        update: dict[str, Any],
        *,
        upsert: bool = False,
        return_document: Any | None = None,
    ) -> dict[str, Any] | None:
        del return_document
        with self._lock:
            key = query["_id"]
            current = self.docs.get(key)
            if current is not None and _matches_query(current, query):
                self.update_one(query, update, upsert=False)
                return dict(self.docs[key])
            if not upsert or current is not None:
                return None
            self.update_one(query, update, upsert=True)
            created = self.docs.get(key)
            return dict(created) if created is not None else None


def _matches_query(doc: dict[str, Any], query: dict[str, Any]) -> bool:
    for key, value in query.items():
        if key == "_id":
            if doc.get("_id") != value:
                return False
            continue
        if key == "$or":
            options = value if isinstance(value, list) else []
            return any(_matches_query(doc, option) for option in options)
        if isinstance(value, dict) and "$lt" in value:
            current = doc.get(key)
            if current is None or not current < value["$lt"]:
                return False
            continue
        if isinstance(value, dict) and "$lte" in value:
            current = doc.get(key)
            if current is None or not current <= value["$lte"]:
                return False
            continue
        if isinstance(value, dict) and "$exists" in value:
            exists = key in doc
            if exists is not bool(value["$exists"]):
                return False
            continue
        current = _lookup_path(doc, key)
        if isinstance(current, list):
            if value not in current:
                return False
            continue
        if current != value:
            return False
    return True


def _lookup_path(doc: Mapping[str, Any], key: str) -> Any:
    if "." not in key:
        return doc.get(key)
    current: Any = doc
    for part in key.split("."):
        if isinstance(current, list):
            current = [item.get(part) for item in current if isinstance(item, Mapping)]
            continue
        if not isinstance(current, Mapping):
            return None
        current = current.get(part)
    return current


class _Cursor:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self.docs = docs

    def limit(self, count: int) -> "_Cursor":
        self.docs = self.docs[:count]
        return self

    def __iter__(self):
        return iter(self.docs)


class _Database:
    def __init__(self) -> None:
        self.collections: dict[str, _Collection] = {}

    def __getitem__(self, name: str) -> _Collection:
        if name not in self.collections:
            self.collections[name] = _Collection(name)
        return self.collections[name]


class _MongoClient:
    database = _Database()

    def __init__(self, *args: object, **kwargs: object) -> None:
        del args, kwargs

    def get_default_database(self, name: str) -> _Database:
        del name
        return self.database


def _helper(
    *,
    raw_collection: _Collection | None = None,
    normalized_collection: _Collection | None = None,
    attempt_collection: _Collection | None = None,
    http_collection: _Collection | None = None,
) -> ProviderExecutionEvidenceHelper:
    return ProviderExecutionEvidenceHelper(
        raw_store=MongoRawPayloadStore(raw_collection or _Collection()),
        normalized_store=MongoNormalizedStore(normalized_collection or _Collection()),
        attempt_store=MongoAttemptStore(attempt_collection or _Collection()),
        http_evidence_store=MongoProviderHttpEvidenceStore(http_collection or _Collection()),
    )


def _mongo_settings(monkeypatch, **extra: object) -> object:
    _MongoClient.database = _Database()
    monkeypatch.setattr(service_module, "MongoClient", _MongoClient)
    attrs: dict[str, object] = {"mongo_uri": "mongodb://localhost/claw_trade_openbb"}
    attrs.update(extra)
    return type("_Settings", (), attrs)()


def _request(domain: PackDomain, market: Market = Market.US) -> PackRequest:
    return PackRequest(
        run_id=f"run-{domain.value}",
        call_id=f"call-{domain.value}",
        worker_id=f"{domain.value}_analyst",
        market=market,
        domain=domain,
        ticker="AAPL" if market == Market.US else "00700.HK",
        company_name="Apple" if market == Market.US else "腾讯控股",
        start_date="2026-05-01",
        end_date="2026-05-17",
        current_date="2026-05-17",
        currency="USD" if market == Market.US else "HKD",
        profile=market.value,
        freshness_policy=FreshnessPolicy(max_age_seconds=300),
    )


def _spec(
    *,
    request: PackRequest,
    adapter: _Adapter,
    endpoint: str,
    required: bool = True,
) -> ProviderCallSpec:
    adapter.market = request.market
    adapter.endpoint = endpoint
    return ProviderCallSpec(
        call_key=f"{request.domain.value}:{adapter.adapter_id}:{endpoint}",
        provider=adapter.provider_id,
        adapter_id=adapter.adapter_id,
        provider_kind=adapter.provider_kind,
        provider_config_version="cfg-v1",
        endpoint=endpoint,
        source_role=adapter.source_role,
        market=request.market,
        domain=request.domain,
        required=required,
        attempt_required=True,
        coverage_group=f"{request.market.value.lower()}_{request.domain.value}",
        coverage_quorum=1,
        params={"ticker": request.ticker},
        cache_ttl_seconds=300,
        license_policy_id="personal_research",
        expected_schema_id=f"{request.market.value.lower()}.{request.domain.value}.v1",
        priority=0,
        priority_source=PrioritySource.SYSTEM_DEFAULT,
        user_preferred=False,
    )


def _plan(request: PackRequest, specs: tuple[ProviderCallSpec, ...]) -> RunProviderPlan:
    return RunProviderPlan(
        run_id=request.run_id,
        provider_config_version="cfg-v1",
        market=request.market,
        ticker=request.ticker,
        domains=(request.domain,),
        call_specs=specs,
        shared_call_keys=tuple(spec.call_key for spec in specs),
        cache_keys=(),
        rate_limit_plan=(),
        initial_gaps=(),
        generated_at="2026-05-17T00:00:00+00:00",
        remote_prefetch_allowed=False,
    )


def test_domain_pack_service_routes_market_and_records_missing_adapter_without_success() -> None:
    request = _request(PackDomain.MARKET)
    missing = _Adapter(
        adapter_id="missing.market",
        provider_id="missing_market",
        domain=PackDomain.MARKET,
        source_role=SourceRole.MARKET_DATA,
        rows=(),
    )
    spec = _spec(request=request, adapter=missing, endpoint="equity_price_historical")

    pack = DomainPackService(settings=object(), adapters=()).get_pack(request, _plan(request, (spec,)))

    assert {attempt.status for attempt in pack.attempts} == {ProviderStatus.SKIPPED_NOT_CONFIGURED}
    assert pack.readiness.status.value in {"blocked", "insufficient"}
    assert "来源未配置" in pack.reader_brief_md
    assert "raw://" not in pack.reader_brief_md


def test_domain_pack_service_persists_missing_adapter_attempt_without_raw_or_http_evidence() -> None:
    request = _request(PackDomain.MARKET)
    missing = _Adapter(
        adapter_id="missing.market",
        provider_id="missing_market",
        domain=PackDomain.MARKET,
        source_role=SourceRole.MARKET_DATA,
        rows=(),
    )
    spec = _spec(request=request, adapter=missing, endpoint="equity_price_historical")
    attempt_collection = _Collection()
    raw_collection = _Collection()
    normalized_collection = _Collection()
    http_collection = _Collection()
    helper = ProviderExecutionEvidenceHelper(
        raw_store=MongoRawPayloadStore(raw_collection),
        normalized_store=MongoNormalizedStore(normalized_collection),
        attempt_store=MongoAttemptStore(attempt_collection),
        http_evidence_store=MongoProviderHttpEvidenceStore(http_collection),
    )

    pack = DomainPackService(
        settings=object(),
        adapters=(),
        provider_execution_helper=helper,
    ).get_pack(request, _plan(request, (spec,)))

    assert {attempt.status for attempt in pack.attempts} == {ProviderStatus.SKIPPED_NOT_CONFIGURED}
    [attempt_doc] = attempt_collection.docs.values()
    assert attempt_doc["status"] == "skipped_not_configured"
    assert attempt_doc["raw_ref"] is None
    assert attempt_doc["normalized_ref"] is None
    assert raw_collection.docs == {}
    assert normalized_collection.docs == {}
    assert http_collection.docs == {}


def test_domain_pack_service_persists_credential_missing_attempt_without_raw_or_http_evidence() -> None:
    request = _request(PackDomain.MARKET)
    adapter = _Adapter(
        adapter_id="market.openbb.us",
        provider_id="market_openbb",
        domain=PackDomain.MARKET,
        source_role=SourceRole.MARKET_DATA,
        rows=(),
        credential_missing=True,
    )
    spec = _spec(request=request, adapter=adapter, endpoint="equity_price_historical")
    attempt_collection = _Collection()
    raw_collection = _Collection()
    normalized_collection = _Collection()
    http_collection = _Collection()
    helper = ProviderExecutionEvidenceHelper(
        raw_store=MongoRawPayloadStore(raw_collection),
        normalized_store=MongoNormalizedStore(normalized_collection),
        attempt_store=MongoAttemptStore(attempt_collection),
        http_evidence_store=MongoProviderHttpEvidenceStore(http_collection),
    )

    pack = DomainPackService(
        settings=object(),
        adapters=(adapter,),
        provider_execution_helper=helper,
    ).get_pack(request, _plan(request, (spec,)))

    assert {attempt.status for attempt in pack.attempts} == {ProviderStatus.CREDENTIAL_MISSING}
    [attempt_doc] = attempt_collection.docs.values()
    assert attempt_doc["status"] == "credential_missing"
    assert attempt_doc["error_code"] == "credential_missing"
    assert attempt_doc["raw_ref"] is None
    assert attempt_doc["normalized_ref"] is None
    assert raw_collection.docs == {}
    assert normalized_collection.docs == {}
    assert http_collection.docs == {}


def test_domain_pack_service_fail_closed_without_provider_call_gate() -> None:
    request = _request(PackDomain.MARKET)
    adapter = _Adapter(
        adapter_id="market.openbb.us",
        provider_id="market_openbb",
        domain=PackDomain.MARKET,
        source_role=SourceRole.MARKET_DATA,
        rows=({"date": "2026-05-17", "close": 101, "volume": 1_000_000},),
    )
    spec = _spec(request=request, adapter=adapter, endpoint="equity_price_historical")
    raw_collection = _Collection()
    normalized_collection = _Collection()
    helper = ProviderExecutionEvidenceHelper(
        raw_store=MongoRawPayloadStore(raw_collection),
        normalized_store=MongoNormalizedStore(normalized_collection),
        attempt_store=MongoAttemptStore(_Collection()),
    )

    pack = DomainPackService(
        settings=object(),
        adapters=(adapter,),
        provider_execution_helper=helper,
    ).get_pack(request, _plan(request, (spec,)))

    assert len(pack.attempts) == 1
    assert pack.attempts[0].status == ProviderStatus.EVIDENCE_WRITE_FAILED
    assert pack.attempts[0].error_code == "provider_call_gate_missing"
    assert raw_collection.docs == {}
    assert normalized_collection.docs == {}


def test_domain_pack_service_market_success_runs_through_provider_call_gate(monkeypatch) -> None:
    request = _request(PackDomain.MARKET)
    adapter = _Adapter(
        adapter_id="market.openbb.us",
        provider_id="market_openbb",
        domain=PackDomain.MARKET,
        source_role=SourceRole.MARKET_DATA,
        rows=({"date": "2026-05-17", "close": 101, "volume": 1_000_000},),
    )
    spec = _spec(request=request, adapter=adapter, endpoint="equity_price_historical")
    settings = _mongo_settings(monkeypatch)

    pack = DomainPackService(settings=settings, adapters=(adapter,)).get_pack(request, _plan(request, (spec,)))

    assert len(pack.attempts) == 1
    assert pack.attempts[0].status == ProviderStatus.REMOTE_SUCCESS
    collections = _MongoClient.database.collections
    assert collections["openbb_cache_entries"].docs
    assert collections["openbb_rate_limits"].docs
    assert collections["openbb_single_flight_calls"].docs
    assert collections["openbb_provider_attempts"].docs


def test_domain_pack_service_routes_market_success_to_market_builder(monkeypatch) -> None:
    request = _request(PackDomain.MARKET)
    rows = tuple(
        {
            "date": f"2026-05-{day:02d}",
            "open": 100 + day,
            "high": 101 + day,
            "low": 99 + day,
            "close": 100.5 + day,
            "volume": 1_000_000 + day,
            "currency": "USD",
            "timezone": "America/New_York",
        }
        for day in range(1, 25)
    )
    adapter = _Adapter(
        adapter_id="market.openbb.us",
        provider_id="market_openbb",
        domain=PackDomain.MARKET,
        source_role=SourceRole.MARKET_DATA,
        rows=rows,
    )
    spec = _spec(request=request, adapter=adapter, endpoint="equity_price_historical")

    pack = DomainPackService(settings=_mongo_settings(monkeypatch), adapters=(adapter,)).get_pack(
        request,
        _plan(request, (spec,)),
    )

    assert pack.compact_facts["ohlcv_row_count"] == 24
    assert {attempt.status for attempt in pack.attempts} == {ProviderStatus.REMOTE_SUCCESS}
    assert "远端获取成功" in pack.reader_brief_md


def test_domain_pack_service_cn_a_tushare_missing_token_keeps_gap_but_not_blocked_when_kline_group_covered(
    monkeypatch,
) -> None:
    request = PackRequest(
        run_id="run-cn-a-market",
        call_id="call-cn-a-market",
        worker_id="market_analyst",
        market=Market.CN_A,
        domain=PackDomain.MARKET,
        ticker="600519.SH",
        company_name="贵州茅台",
        start_date="2026-04-01",
        end_date="2026-05-17",
        current_date="2026-05-17",
        currency="CNY",
        profile="CN_A",
        freshness_policy=FreshnessPolicy(max_age_seconds=300),
    )
    capabilities = load_default_system_capabilities()
    provider_config_version = default_provider_config_version(capabilities)
    registry = build_default_provider_registry()
    adapters = tuple(
        item
        for item in build_default_market_adapters(provider_config_version=provider_config_version, env={})
        if getattr(item, "market", None) == Market.CN_A
    )
    planner = RunProviderPlanner(adapters_by_id={item.adapter_id: item for item in adapters})
    run_plan = planner.build_run_plan(
        run_id=request.run_id,
        market=request.market,
        ticker=request.ticker,
        company_name=request.company_name,
        currency=request.currency,
        profile=request.profile,
        current_date=request.current_date,
        start_date=request.start_date,
        end_date=request.end_date,
        domains=(PackDomain.MARKET,),
        registry=registry,
        provider_config_version=provider_config_version,
    )
    assert any(gap.reason == DataGapReason.CREDENTIAL_MISSING for gap in run_plan.initial_gaps)

    def _fake_tencent_row(*, symbol: str, fallback_date: str) -> Mapping[str, Any]:
        return {
            "symbol": symbol,
            "date": fallback_date,
            "open": 2000.0,
            "high": 2010.0,
            "low": 1990.0,
            "close": 2005.0,
            "volume": 10000.0,
        }

    def _fake_baidu_rows(*, symbol: str, start_date: str) -> tuple[Mapping[str, Any], ...]:
        del symbol, start_date
        return tuple(
            {
                "date": f"2026-04-{day:02d}",
                "open": 1500 + day,
                "high": 1510 + day,
                "low": 1490 + day,
                "close": 1505 + day,
                "volume": 1000000 + day,
            "amount": 2000000 + day,
        }
        for day in range(1, 26)
    )

    def _fake_mootdx_row(*, symbol: str, fallback_date: str) -> Mapping[str, Any]:
        del symbol, fallback_date
        raise RuntimeError("mootdx tcp 7709 unavailable in test fixture")

    monkeypatch.setattr(market_adapters_module, "_call_mootdx_quote_row", _fake_mootdx_row)
    monkeypatch.setattr(market_adapters_module, "_call_tencent_quote_row", _fake_tencent_row)
    monkeypatch.setattr(market_adapters_module, "_call_baidu_kline_with_ma", _fake_baidu_rows)

    pack = DomainPackService(settings=_mongo_settings(monkeypatch), adapters=adapters).get_pack(request, run_plan)

    assert pack.readiness.status.value != "blocked"
    assert pack.readiness.coverage["group:cn_a_market_kline"] == "1/1"
    assert pack.readiness.coverage["group:cn_a_market_quote"] == "1/1"
    assert pack.readiness.coverage["group:cn_a_market_orderbook"] == "1/1"
    assert any(gap.reason == DataGapReason.CREDENTIAL_MISSING for gap in pack.data_gaps)
    assert any(
        "TUSHARE_TOKEN" in gap.root_cause
        for gap in pack.data_gaps
        if gap.reason == DataGapReason.CREDENTIAL_MISSING
    )
    assert any(
        gap.reason == DataGapReason.PROVIDER_UNAVAILABLE
        and "mootdx tcp 7709 unavailable in test fixture" in gap.root_cause
        and "mootdx_quote" in gap.provider_candidates
        for gap in pack.data_gaps
    )
    assert any(
        gap.reason == DataGapReason.PROVIDER_UNAVAILABLE
        and "mootdx tcp 7709 unavailable in test fixture" in gap.root_cause
        and "mootdx_orderbook" in gap.provider_candidates
        for gap in pack.data_gaps
    )
    assert any(
        attempt.provider == "tushare_kline_fallback" and attempt.status == ProviderStatus.CREDENTIAL_MISSING
        for attempt in pack.attempts
    )
    assert any(
        attempt.provider == "mootdx_quote" and attempt.status == ProviderStatus.REMOTE_ERROR
        for attempt in pack.attempts
    )
    assert any(
        attempt.provider == "mootdx_orderbook" and attempt.status == ProviderStatus.REMOTE_ERROR
        for attempt in pack.attempts
    )
    assert any(
        attempt.provider == "tencent_quote" and attempt.status == ProviderStatus.REMOTE_SUCCESS
        for attempt in pack.attempts
    )
    assert any(
        attempt.provider == "tencent_orderbook" and attempt.status == ProviderStatus.REMOTE_SUCCESS
        for attempt in pack.attempts
    )


def test_domain_pack_service_skips_configured_optional_market_candidate_after_quorum(monkeypatch) -> None:
    request = _request(PackDomain.MARKET)
    rows = tuple(
        {
            "date": f"2026-05-{day:02d}",
            "open": 100 + day,
            "high": 101 + day,
            "low": 99 + day,
            "close": 100.5 + day,
            "volume": 1_000_000 + day,
        }
        for day in range(1, 25)
    )
    primary = _Adapter(
        adapter_id="market.primary",
        provider_id="primary_market",
        domain=PackDomain.MARKET,
        source_role=SourceRole.MARKET_DATA,
        rows=rows,
    )
    optional = _Adapter(
        adapter_id="market.optional",
        provider_id="optional_market",
        domain=PackDomain.MARKET,
        source_role=SourceRole.MARKET_DATA,
        rows=rows,
    )
    primary_spec = _spec(request=request, adapter=primary, endpoint="daily")
    optional_spec = _spec(request=request, adapter=optional, endpoint="daily_optional", required=False)

    pack = DomainPackService(settings=_mongo_settings(monkeypatch), adapters=(primary, optional)).get_pack(
        request,
        _plan(request, (primary_spec, optional_spec)),
    )

    assert pack.compact_facts["ohlcv_row_count"] == 24
    assert any(attempt.provider == "primary_market" and attempt.status == ProviderStatus.REMOTE_SUCCESS for attempt in pack.attempts)
    assert any(
        attempt.provider == "optional_market"
        and attempt.status == ProviderStatus.NOT_APPLICABLE
        and attempt.raw_ref is None
        and attempt.normalized_ref is None
        for attempt in pack.attempts
    )
    assert "coverage quorum already satisfied" in pack.reader_brief_md


def test_domain_pack_service_does_not_skip_user_preferred_optional_market_candidate(monkeypatch) -> None:
    request = _request(PackDomain.MARKET)
    rows = tuple(
        {
            "date": f"2026-05-{day:02d}",
            "open": 100 + day,
            "high": 101 + day,
            "low": 99 + day,
            "close": 100.5 + day,
            "volume": 1_000_000 + day,
        }
        for day in range(1, 25)
    )
    primary = _Adapter(
        adapter_id="market.primary",
        provider_id="primary_market",
        domain=PackDomain.MARKET,
        source_role=SourceRole.MARKET_DATA,
        rows=rows,
    )
    preferred = _Adapter(
        adapter_id="market.preferred",
        provider_id="preferred_market",
        domain=PackDomain.MARKET,
        source_role=SourceRole.MARKET_DATA,
        rows=rows,
    )
    primary_spec = _spec(request=request, adapter=primary, endpoint="daily")
    preferred_spec = replace(
        _spec(request=request, adapter=preferred, endpoint="daily_preferred", required=False),
        priority_source=PrioritySource.USER_PREFERRED,
        user_preferred=True,
    )

    pack = DomainPackService(settings=_mongo_settings(monkeypatch), adapters=(primary, preferred)).get_pack(
        request,
        _plan(request, (primary_spec, preferred_spec)),
    )

    assert any(
        attempt.provider == "preferred_market" and attempt.status == ProviderStatus.REMOTE_SUCCESS
        for attempt in pack.attempts
    )
    assert "optional provider preferred_market/daily_preferred was not requested" not in pack.reader_brief_md


def test_domain_pack_service_market_cache_hit_hydrates_normalized_rows(monkeypatch) -> None:
    request = _request(PackDomain.MARKET)
    rows = tuple(
        {
            "date": f"2026-05-{day:02d}",
            "open": 100 + day,
            "high": 101 + day,
            "low": 99 + day,
            "close": 100.5 + day,
            "volume": 1_000_000 + day,
        }
        for day in range(1, 25)
    )
    adapter = _Adapter(
        adapter_id="market.openbb.us",
        provider_id="market_openbb",
        domain=PackDomain.MARKET,
        source_role=SourceRole.MARKET_DATA,
        rows=rows,
    )
    spec = _spec(request=request, adapter=adapter, endpoint="equity_price_historical")
    service = DomainPackService(settings=_mongo_settings(monkeypatch), adapters=(adapter,))

    first = service.get_pack(request, _plan(request, (spec,)))
    second = service.get_pack(request, _plan(request, (spec,)))

    assert any(attempt.status == ProviderStatus.REMOTE_SUCCESS for attempt in first.attempts)
    assert any(attempt.status == ProviderStatus.CACHE_HIT for attempt in second.attempts)
    assert second.compact_facts["ohlcv_row_count"] == 24


def test_domain_pack_service_cn_a_market_uses_mongo_warehouse_before_provider(monkeypatch) -> None:
    request = PackRequest(
        run_id="run-cn-a-warehouse",
        call_id="call-cn-a-warehouse",
        worker_id="market_analyst",
        market=Market.CN_A,
        domain=PackDomain.MARKET,
        ticker="600519.SH",
        company_name="贵州茅台",
        start_date="2026-05-01",
        end_date="2026-05-24",
        current_date="2026-05-24",
        currency="CNY",
        profile="CN_A",
        freshness_policy=FreshnessPolicy(max_age_seconds=300),
    )
    settings = _mongo_settings(monkeypatch)
    warehouse_rows = tuple(
        {
            "ticker": "600519.SH",
            "trade_date": f"2026-05-{day:02d}",
            "open": 100 + day,
            "high": 101 + day,
            "low": 99 + day,
            "close": 100.5 + day,
            "volume": 1_000_000 + day,
            "currency": "CNY",
            "timezone": "Asia/Shanghai",
        }
        for day in range(1, 25)
    )
    _MongoClient.database[OPENBB_NORMALIZED].docs["norm-cn-a-qfq"] = {
        "_id": "norm-cn-a-qfq",
        "schema_id": "cn_a.baostock.qfq_daily.v1",
        "market": Market.CN_A.value,
        "domain": PackDomain.SELECT_FEATURE.value,
        "provider": "local-baostock",
        "endpoint": "seed_import:qfq_daily",
        "rows": list(warehouse_rows),
        "row_count": len(warehouse_rows),
        "source_raw_ref": "mongo://openbb_raw_payloads/raw-cn-a-qfq",
        "status": ProviderStatus.NOT_APPLICABLE.value,
        "created_at": "2026-05-24T00:00:00+00:00",
    }

    adapter = _Adapter(
        adapter_id="project.cn_a.market",
        provider_id="baidu_kline",
        domain=PackDomain.MARKET,
        source_role=SourceRole.MARKET_DATA,
        rows=(),
        market=Market.CN_A,
    )

    def _blocked_fetch(spec: ProviderCallSpec, request: PackRequest) -> ProviderFetch:
        del spec, request
        raise AssertionError("provider should not be called when CN_A warehouse is fresh")

    adapter.fetch = _blocked_fetch  # type: ignore[method-assign]
    spec = ProviderCallSpec(
        call_key="market:project.cn_a.market:kline_baidu",
        provider="baidu_kline",
        adapter_id="project.cn_a.market",
        provider_kind=ProviderKind.PROJECT_EXTENSION,
        provider_config_version="cfg-v1",
        endpoint="kline_baidu",
        source_role=SourceRole.MARKET_DATA,
        market=Market.CN_A,
        domain=PackDomain.MARKET,
        required=True,
        attempt_required=True,
        coverage_group="cn_a_market_kline",
        coverage_quorum=1,
        params={"ticker": request.ticker},
        cache_ttl_seconds=300,
        license_policy_id="personal_research",
        expected_schema_id="cn_a.market.ohlcv.v1",
        priority=10,
        priority_source=PrioritySource.SYSTEM_DEFAULT,
        user_preferred=False,
    )

    pack = DomainPackService(settings=settings, adapters=(adapter,)).get_pack(request, _plan(request, (spec,)))

    assert pack.compact_facts["ohlcv_row_count"] == 24
    assert pack.normalized_refs == ("mongo://openbb_normalized/norm-cn-a-qfq",)
    assert [attempt.status for attempt in pack.attempts] == [ProviderStatus.WAREHOUSE_HIT]
    assert pack.attempts[0].raw_ref == "mongo://openbb_raw_payloads/raw-cn-a-qfq"
    assert "使用主仓库数据" in pack.reader_brief_md


def test_domain_pack_service_cn_a_warehouse_only_covers_daily_kline_group(monkeypatch) -> None:
    request = PackRequest(
        run_id="run-cn-a-warehouse-scope",
        call_id="call-cn-a-warehouse-scope",
        worker_id="market_analyst",
        market=Market.CN_A,
        domain=PackDomain.MARKET,
        ticker="600519.SH",
        company_name="贵州茅台",
        start_date="2026-05-01",
        end_date="2026-05-24",
        current_date="2026-05-24",
        currency="CNY",
        profile="CN_A",
        freshness_policy=FreshnessPolicy(max_age_seconds=300),
    )
    settings = _mongo_settings(monkeypatch)
    _MongoClient.database[OPENBB_NORMALIZED].docs["norm-cn-a-qfq-scope"] = {
        "_id": "norm-cn-a-qfq-scope",
        "schema_id": "cn_a.baostock.qfq_daily.v1",
        "market": Market.CN_A.value,
        "domain": PackDomain.SELECT_FEATURE.value,
        "rows": [
            {
                "ticker": "600519.SH",
                "trade_date": f"2026-05-{day:02d}",
                "open": 100 + day,
                "high": 101 + day,
                "low": 99 + day,
                "close": 100.5 + day,
                "volume": 1_000_000 + day,
            }
            for day in range(1, 25)
        ],
        "source_raw_ref": "mongo://openbb_raw_payloads/raw-cn-a-qfq-scope",
        "created_at": "2026-05-24T00:00:00+00:00",
    }
    kline_spec = ProviderCallSpec(
        call_key="market:project.cn_a.market:kline_baidu",
        provider="baidu_kline",
        adapter_id="project.cn_a.market",
        provider_kind=ProviderKind.PROJECT_EXTENSION,
        provider_config_version="cfg-v1",
        endpoint="kline_baidu",
        source_role=SourceRole.MARKET_DATA,
        market=Market.CN_A,
        domain=PackDomain.MARKET,
        required=True,
        attempt_required=True,
        coverage_group="cn_a_market_kline",
        coverage_quorum=1,
        params={"ticker": request.ticker},
        cache_ttl_seconds=300,
        license_policy_id="personal_research",
        expected_schema_id="cn_a.market.ohlcv.v1",
        priority=10,
        priority_source=PrioritySource.SYSTEM_DEFAULT,
        user_preferred=False,
    )
    quote_spec = replace(
        kline_spec,
        call_key="market:project.cn_a.market:stock_quote",
        provider="mootdx_quote",
        adapter_id="project.cn_a.market.quote",
        endpoint="stock_quote",
        coverage_group="cn_a_market_quote",
        priority=0,
    )

    pack = DomainPackService(settings=settings, adapters=()).get_pack(request, _plan(request, (quote_spec, kline_spec)))

    assert pack.readiness.coverage["group:cn_a_market_kline"] == "1/1"
    assert pack.readiness.coverage["group:cn_a_market_quote"] == "0/1"
    assert any(attempt.status == ProviderStatus.WAREHOUSE_HIT for attempt in pack.attempts)
    assert any(
        attempt.provider == "mootdx_quote" and attempt.status == ProviderStatus.SKIPPED_NOT_CONFIGURED
        for attempt in pack.attempts
    )


def test_domain_pack_service_cn_a_market_warehouse_rejects_wrong_domain(monkeypatch) -> None:
    request = PackRequest(
        run_id="run-cn-a-warehouse-wrong-domain",
        call_id="call-cn-a-warehouse-wrong-domain",
        worker_id="market_analyst",
        market=Market.CN_A,
        domain=PackDomain.MARKET,
        ticker="600519.SH",
        company_name="贵州茅台",
        start_date="2026-05-01",
        end_date="2026-05-24",
        current_date="2026-05-24",
        currency="CNY",
        profile="CN_A",
        freshness_policy=FreshnessPolicy(max_age_seconds=300),
    )
    settings = _mongo_settings(monkeypatch)
    _MongoClient.database[OPENBB_NORMALIZED].docs["norm-cn-a-qfq-wrong-domain"] = {
        "_id": "norm-cn-a-qfq-wrong-domain",
        "schema_id": "cn_a.baostock.qfq_daily.v1",
        "market": Market.CN_A.value,
        "domain": PackDomain.NEWS.value,
        "rows": [
            {
                "ticker": "600519.SH",
                "trade_date": f"2026-05-{day:02d}",
                "open": 100 + day,
                "high": 101 + day,
                "low": 99 + day,
                "close": 100.5 + day,
                "volume": 1_000_000 + day,
            }
            for day in range(1, 25)
        ],
        "source_raw_ref": "mongo://openbb_raw_payloads/raw-cn-a-qfq-wrong-domain",
        "created_at": "2026-05-24T00:00:00+00:00",
    }
    spec = ProviderCallSpec(
        call_key="market:project.cn_a.market:kline_baidu",
        provider="baidu_kline",
        adapter_id="project.cn_a.market",
        provider_kind=ProviderKind.PROJECT_EXTENSION,
        provider_config_version="cfg-v1",
        endpoint="kline_baidu",
        source_role=SourceRole.MARKET_DATA,
        market=Market.CN_A,
        domain=PackDomain.MARKET,
        required=True,
        attempt_required=True,
        coverage_group="cn_a_market_kline",
        coverage_quorum=1,
        params={"ticker": request.ticker},
        cache_ttl_seconds=300,
        license_policy_id="personal_research",
        expected_schema_id="cn_a.market.ohlcv.v1",
        priority=10,
        priority_source=PrioritySource.SYSTEM_DEFAULT,
        user_preferred=False,
    )

    pack = DomainPackService(settings=settings, adapters=()).get_pack(request, _plan(request, (spec,)))

    assert all(attempt.status != ProviderStatus.WAREHOUSE_HIT for attempt in pack.attempts)
    assert pack.normalized_refs == ()


def test_domain_pack_service_records_mongo_missing_when_warehouse_is_unconfigured() -> None:
    request = PackRequest(
        run_id="run-cn-a-mongo-missing",
        call_id="call-cn-a-mongo-missing",
        worker_id="market_analyst",
        market=Market.CN_A,
        domain=PackDomain.MARKET,
        ticker="600519.SH",
        company_name="贵州茅台",
        start_date="2026-05-01",
        end_date="2026-05-24",
        current_date="2026-05-24",
        currency="CNY",
        profile="CN_A",
        freshness_policy=FreshnessPolicy(max_age_seconds=300),
    )
    adapter = _Adapter(
        adapter_id="project.cn_a.market",
        provider_id="baidu_kline",
        domain=PackDomain.MARKET,
        source_role=SourceRole.MARKET_DATA,
        rows=(),
        market=Market.CN_A,
    )
    spec = ProviderCallSpec(
        call_key="market:project.cn_a.market:kline_baidu",
        provider="baidu_kline",
        adapter_id="project.cn_a.market",
        provider_kind=ProviderKind.PROJECT_EXTENSION,
        provider_config_version="cfg-v1",
        endpoint="kline_baidu",
        source_role=SourceRole.MARKET_DATA,
        market=Market.CN_A,
        domain=PackDomain.MARKET,
        required=True,
        attempt_required=True,
        coverage_group="cn_a_market_kline",
        coverage_quorum=1,
        params={"ticker": request.ticker},
        cache_ttl_seconds=300,
        license_policy_id="personal_research",
        expected_schema_id="cn_a.market.ohlcv.v1",
        priority=10,
        priority_source=PrioritySource.SYSTEM_DEFAULT,
        user_preferred=False,
    )

    pack = DomainPackService(settings=object(), adapters=(adapter,)).get_pack(request, _plan(request, (spec,)))

    assert any(gap.reason == DataGapReason.MONGO_MISSING for gap in pack.data_gaps)
    assert pack.readiness.status.value in {"blocked", "insufficient"}


def test_domain_pack_service_non_market_domains_use_mongo_warehouse_before_provider(monkeypatch) -> None:
    cases = (
        (
            PackDomain.FUNDAMENTAL,
            SourceRole.FUNDAMENTAL_DATA,
            "profile+ratios",
            {
                "ticker": "600519.SH",
                "valuation": {"pe": 30.1, "pb": 12.4},
                "financial_indicators": {"roe": 0.31},
            },
            lambda pack: pack.compact_facts["valuation.pe"] == 30.1,
        ),
        (
            PackDomain.NEWS,
            SourceRole.OFFICIAL_ORIGINAL,
            "company_filings",
            {"ticker": "600519.SH", "title": "贵州茅台发布年度报告", "url": "https://example.com/filing"},
            lambda pack: len(pack.compact_facts["news_facts"]) == 1,
        ),
        (
            PackDomain.SOCIAL,
            SourceRole.SOCIAL_ORIGINAL_SAMPLE,
            "social_posts",
            {"ticker": "600519.SH", "title": "投资者讨论样本", "url": "https://example.com/social"},
            lambda pack: len(pack.compact_facts["social_original_samples"]) == 1,
        ),
    )

    for domain, source_role, endpoint, row, assertion in cases:
        request = PackRequest(
            run_id=f"run-cn-a-{domain.value}-warehouse",
            call_id=f"call-cn-a-{domain.value}-warehouse",
            worker_id=f"{domain.value}_analyst",
            market=Market.CN_A,
            domain=domain,
            ticker="600519.SH",
            company_name="贵州茅台",
            start_date="2026-05-01",
            end_date="2026-05-24",
            current_date="2026-05-24",
            currency="CNY",
            profile="CN_A",
            freshness_policy=FreshnessPolicy(max_age_seconds=300),
        )
        settings = _mongo_settings(monkeypatch)
        adapter = _Adapter(
            adapter_id=f"{domain.value}.provider",
            provider_id=f"{domain.value}_provider",
            domain=domain,
            source_role=source_role,
            rows=(),
            market=Market.CN_A,
        )
        spec = _spec(request=request, adapter=adapter, endpoint=endpoint)
        _MongoClient.database[OPENBB_NORMALIZED].docs[f"norm-cn-a-{domain.value}"] = {
            "_id": f"norm-cn-a-{domain.value}",
            "schema_id": spec.expected_schema_id,
            "market": Market.CN_A.value,
            "domain": domain.value,
            "ticker": "600519.SH",
            "rows": [row],
            "row_count": 1,
            "source_raw_ref": f"mongo://openbb_raw_payloads/raw-cn-a-{domain.value}",
            "period_end": "2026-05-24",
            "created_at": "2026-05-24T00:00:00+00:00",
        }

        pack = DomainPackService(settings=settings, adapters=()).get_pack(request, _plan(request, (spec,)))

        assert [attempt.status for attempt in pack.attempts] == [ProviderStatus.WAREHOUSE_HIT]
        assert pack.attempts[0].provider == "mongo_warehouse"
        assert pack.raw_refs == (f"mongo://openbb_raw_payloads/raw-cn-a-{domain.value}",)
        assert pack.normalized_refs == (f"mongo://openbb_normalized/norm-cn-a-{domain.value}",)
        assert pack.readiness.status.value == "ready"
        assert assertion(pack)


def test_domain_pack_service_rejects_warehouse_doc_when_required_fields_are_missing(monkeypatch) -> None:
    request = PackRequest(
        run_id="run-cn-a-fundamental-field-check",
        call_id="call-cn-a-fundamental-field-check",
        worker_id="fundamental_analyst",
        market=Market.CN_A,
        domain=PackDomain.FUNDAMENTAL,
        ticker="600519.SH",
        company_name="贵州茅台",
        start_date="2026-05-01",
        end_date="2026-05-24",
        current_date="2026-05-24",
        currency="CNY",
        profile="CN_A",
        freshness_policy=FreshnessPolicy(max_age_seconds=300),
    )
    settings = _mongo_settings(monkeypatch)
    adapter = _Adapter(
        adapter_id="fundamental.provider",
        provider_id="fundamental_provider",
        domain=PackDomain.FUNDAMENTAL,
        source_role=SourceRole.FUNDAMENTAL_DATA,
        rows=(),
        market=Market.CN_A,
    )
    spec = _spec(request=request, adapter=adapter, endpoint="profile+ratios")
    _MongoClient.database[OPENBB_NORMALIZED].docs["norm-cn-a-fundamental-partial"] = {
        "_id": "norm-cn-a-fundamental-partial",
        "schema_id": spec.expected_schema_id,
        "market": Market.CN_A.value,
        "domain": PackDomain.FUNDAMENTAL.value,
        "ticker": "600519.SH",
        "rows": [{"ticker": "600519.SH", "valuation": {"pe": 30.1}}],
        "row_count": 1,
        "source_raw_ref": "mongo://openbb_raw_payloads/raw-cn-a-fundamental-partial",
        "period_end": "2026-05-24",
        "created_at": "2026-05-24T00:00:00+00:00",
    }

    pack = DomainPackService(settings=settings, adapters=()).get_pack(request, _plan(request, (spec,)))

    assert all(attempt.status != ProviderStatus.WAREHOUSE_HIT for attempt in pack.attempts)
    missing_fields = {gap.field_path for gap in pack.data_gaps if gap.reason == DataGapReason.FIELD_MISSING}
    assert {"valuation.pb", "financial_indicators.roe"} <= missing_fields


def test_domain_pack_service_reports_spec_required_field_gaps(monkeypatch) -> None:
    request = PackRequest(
        run_id="run-cn-a-fundamental-spec-field-check",
        call_id="call-cn-a-fundamental-spec-field-check",
        worker_id="fundamental_analyst",
        market=Market.CN_A,
        domain=PackDomain.FUNDAMENTAL,
        ticker="600519.SH",
        company_name="贵州茅台",
        start_date="2026-05-01",
        end_date="2026-05-24",
        current_date="2026-05-24",
        currency="CNY",
        profile="CN_A",
        freshness_policy=FreshnessPolicy(max_age_seconds=300),
    )
    settings = _mongo_settings(monkeypatch)
    adapter = _Adapter(
        adapter_id="fundamental.provider",
        provider_id="fundamental_provider",
        domain=PackDomain.FUNDAMENTAL,
        source_role=SourceRole.FUNDAMENTAL_DATA,
        rows=(),
        market=Market.CN_A,
    )
    spec = _spec(request=request, adapter=adapter, endpoint="profile+cashflow")
    spec = replace(spec, params={**dict(spec.params), "required_fields": ("valuation.pe", "cashflow.free_cash_flow")})
    _MongoClient.database[OPENBB_NORMALIZED].docs["norm-cn-a-fundamental-spec-partial"] = {
        "_id": "norm-cn-a-fundamental-spec-partial",
        "schema_id": spec.expected_schema_id,
        "market": Market.CN_A.value,
        "domain": PackDomain.FUNDAMENTAL.value,
        "ticker": "600519.SH",
        "rows": [{"ticker": "600519.SH", "valuation": {"pe": 30.1}}],
        "row_count": 1,
        "source_raw_ref": "mongo://openbb_raw_payloads/raw-cn-a-fundamental-spec-partial",
        "period_end": "2026-05-24",
        "created_at": "2026-05-24T00:00:00+00:00",
    }

    pack = DomainPackService(settings=settings, adapters=()).get_pack(request, _plan(request, (spec,)))

    assert all(attempt.status != ProviderStatus.WAREHOUSE_HIT for attempt in pack.attempts)
    missing_fields = {gap.field_path for gap in pack.data_gaps if gap.reason == DataGapReason.FIELD_MISSING}
    assert "cashflow.free_cash_flow" in missing_fields


def test_domain_pack_service_rejects_warehouse_doc_when_stale_ticker_or_schema_mismatch(monkeypatch) -> None:
    cases = (
        ("stale", "600519.SH", "cn_a.fundamental.v1", "2026-05-01"),
        ("ticker-mismatch", "000001.SZ", "cn_a.fundamental.v1", "2026-05-24"),
        ("schema-mismatch", "600519.SH", "wrong.schema.v1", "2026-05-24"),
    )

    for case_id, doc_ticker, schema_id, period_end in cases:
        request = PackRequest(
            run_id=f"run-cn-a-fundamental-{case_id}",
            call_id=f"call-cn-a-fundamental-{case_id}",
            worker_id="fundamental_analyst",
            market=Market.CN_A,
            domain=PackDomain.FUNDAMENTAL,
            ticker="600519.SH",
            company_name="贵州茅台",
            start_date="2026-05-01",
            end_date="2026-05-24",
            current_date="2026-05-24",
            currency="CNY",
            profile="CN_A",
            freshness_policy=FreshnessPolicy(max_age_seconds=300),
        )
        settings = _mongo_settings(monkeypatch)
        adapter = _Adapter(
            adapter_id="fundamental.provider",
            provider_id="fundamental_provider",
            domain=PackDomain.FUNDAMENTAL,
            source_role=SourceRole.FUNDAMENTAL_DATA,
            rows=(),
            market=Market.CN_A,
        )
        spec = _spec(request=request, adapter=adapter, endpoint="profile+ratios")
        _MongoClient.database[OPENBB_NORMALIZED].docs[f"norm-cn-a-fundamental-{case_id}"] = {
            "_id": f"norm-cn-a-fundamental-{case_id}",
            "schema_id": schema_id,
            "market": Market.CN_A.value,
            "domain": PackDomain.FUNDAMENTAL.value,
            "ticker": doc_ticker,
            "rows": [
                {
                    "ticker": doc_ticker,
                    "valuation": {"pe": 30.1, "pb": 12.4},
                    "financial_indicators": {"roe": 0.31},
                }
            ],
            "row_count": 1,
            "source_raw_ref": f"mongo://openbb_raw_payloads/raw-cn-a-fundamental-{case_id}",
            "period_end": period_end,
            "created_at": f"{period_end}T00:00:00+00:00",
        }

        pack = DomainPackService(settings=settings, adapters=()).get_pack(request, _plan(request, (spec,)))

        assert all(attempt.status != ProviderStatus.WAREHOUSE_HIT for attempt in pack.attempts)
        assert pack.normalized_refs == ()


def test_domain_pack_service_warehouse_field_aliases_count_for_news_and_social(monkeypatch) -> None:
    cases = (
        (PackDomain.NEWS, SourceRole.OFFICIAL_ORIGINAL, "company_filings", {"ticker": "600519.SH", "headline": "公告标题"}),
        (PackDomain.SOCIAL, SourceRole.SOCIAL_ORIGINAL_SAMPLE, "social_posts", {"ticker": "600519.SH", "summary": "讨论摘要"}),
    )

    for domain, source_role, endpoint, row in cases:
        request = PackRequest(
            run_id=f"run-cn-a-{domain.value}-alias",
            call_id=f"call-cn-a-{domain.value}-alias",
            worker_id=f"{domain.value}_analyst",
            market=Market.CN_A,
            domain=domain,
            ticker="600519.SH",
            company_name="贵州茅台",
            start_date="2026-05-01",
            end_date="2026-05-24",
            current_date="2026-05-24",
            currency="CNY",
            profile="CN_A",
            freshness_policy=FreshnessPolicy(max_age_seconds=300),
        )
        settings = _mongo_settings(monkeypatch)
        adapter = _Adapter(
            adapter_id=f"{domain.value}.provider",
            provider_id=f"{domain.value}_provider",
            domain=domain,
            source_role=source_role,
            rows=(),
            market=Market.CN_A,
        )
        spec = _spec(request=request, adapter=adapter, endpoint=endpoint)
        _MongoClient.database[OPENBB_NORMALIZED].docs[f"norm-cn-a-{domain.value}-alias"] = {
            "_id": f"norm-cn-a-{domain.value}-alias",
            "schema_id": spec.expected_schema_id,
            "market": Market.CN_A.value,
            "domain": domain.value,
            "ticker": "600519.SH",
            "rows": [row],
            "row_count": 1,
            "source_raw_ref": f"mongo://openbb_raw_payloads/raw-cn-a-{domain.value}-alias",
            "period_end": "2026-05-24",
            "created_at": "2026-05-24T00:00:00+00:00",
        }

        pack = DomainPackService(settings=settings, adapters=()).get_pack(request, _plan(request, (spec,)))

        assert [attempt.status for attempt in pack.attempts] == [ProviderStatus.WAREHOUSE_HIT]
        assert pack.readiness.status.value == "ready"


def test_domain_pack_service_market_success_generates_chart_images(monkeypatch, tmp_path: Path) -> None:
    request = _request(PackDomain.MARKET)
    rows = tuple(
        {
            "date": f"2026-05-{day:02d}",
            "open": 100 + day,
            "high": 101 + day,
            "low": 99 + day,
            "close": 100.5 + day,
            "volume": 1_000_000 + day,
            "currency": "USD",
            "timezone": "America/New_York",
        }
        for day in range(1, 25)
    )
    adapter = _Adapter(
        adapter_id="market.openbb.us",
        provider_id="market_openbb",
        domain=PackDomain.MARKET,
        source_role=SourceRole.MARKET_DATA,
        rows=rows,
    )
    spec = _spec(request=request, adapter=adapter, endpoint="equity_price_historical")
    settings = _mongo_settings(monkeypatch, object_store_uri=tmp_path.as_uri())

    pack = DomainPackService(
        settings=settings,
        adapters=(adapter,),
    ).get_pack(request, _plan(request, (spec,)))

    assert {asset.status.value for asset in pack.chart_assets} == {"ready"}
    for asset in pack.chart_assets:
        assert asset.image_ref is not None
        image_path = Path(unquote(urlparse(asset.image_ref).path))
        assert image_path.exists()
        assert image_path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


def test_domain_pack_service_market_pack_attempts_include_mongo_refs(monkeypatch) -> None:
    request = _request(PackDomain.MARKET)
    adapter = _Adapter(
        adapter_id="market.openbb.us",
        provider_id="market_openbb",
        domain=PackDomain.MARKET,
        source_role=SourceRole.MARKET_DATA,
        rows=(
            {
                "date": "2026-05-17",
                "open": 100,
                "high": 102,
                "low": 99,
                "close": 101,
                "volume": 1_000_000,
            },
        ),
    )
    spec = _spec(request=request, adapter=adapter, endpoint="equity_price_historical")

    service = DomainPackService(settings=_mongo_settings(monkeypatch), adapters=(adapter,))
    pack = service.get_pack(request, _plan(request, (spec,)))

    assert len(pack.attempts) == 1
    attempt = pack.attempts[0]
    assert attempt.status == ProviderStatus.REMOTE_SUCCESS
    assert attempt.raw_ref is not None and attempt.raw_ref.startswith("mongo://openbb_raw_payloads/")
    assert attempt.normalized_ref is not None and attempt.normalized_ref.startswith("mongo://openbb_normalized/")
    assert pack.raw_refs == (attempt.raw_ref,)
    assert pack.normalized_refs == (attempt.normalized_ref,)


def test_domain_pack_service_crypto_market_pack_writes_crypto_lens_evidence_when_mongo_configured(monkeypatch) -> None:
    request = PackRequest(
        run_id="run-crypto-market",
        call_id="call-crypto-market",
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
    rows = tuple(
        {
            "date": f"2026-04-{day:02d}",
            "open": 80000 + day,
            "high": 81000 + day,
            "low": 79000 + day,
            "close": 80500 + day,
            "volume": 1000 + day,
            "currency": "USDT",
            "timezone": "UTC",
        }
        for day in range(1, 29)
    ) + tuple(
        {
            "date": f"2026-05-{day:02d}",
            "open": 81000 + day,
            "high": 82000 + day,
            "low": 80000 + day,
            "close": 81500 + day,
            "volume": 1100 + day,
            "currency": "USDT",
            "timezone": "UTC",
        }
        for day in range(1, 12)
    )
    adapter = _Adapter(
        adapter_id="market.openbb.crypto",
        provider_id="openbb_yfinance",
        domain=PackDomain.MARKET,
        source_role=SourceRole.MARKET_DATA,
        rows=rows,
    )
    spec = _spec(request=request, adapter=adapter, endpoint="crypto_price_historical")
    settings = _mongo_settings(monkeypatch)

    service = DomainPackService(settings=settings, adapters=(adapter,))
    pack = service.get_pack(request, _plan(request, (spec,)))

    assert len(pack.audit_payload.analysis_evidence_refs) == 1
    [analysis_ref] = pack.audit_payload.analysis_evidence_refs
    assert analysis_ref.startswith("mongo://crypto_lens_analysis_evidence/")
    assert analysis_ref not in pack.reader_brief_md
    evidence_docs = _MongoClient.database.collections["crypto_lens_analysis_evidence"].docs
    [document] = evidence_docs.values()
    assert document["referenced_normalized_refs"] == pack.normalized_refs
    assert "raw_ref" not in document


def test_domain_pack_service_market_evidence_write_failure_maps_to_evidence_write_failed(monkeypatch) -> None:
    request = _request(PackDomain.MARKET)
    adapter = _Adapter(
        adapter_id="market.openbb.us",
        provider_id="market_openbb",
        domain=PackDomain.MARKET,
        source_role=SourceRole.MARKET_DATA,
            rows=(
                {
                    "date": "2026-05-17",
                    "open": 100,
                    "high": 102,
                    "low": 99,
                    "close": 101,
                    "volume": 1_000_000,
                },
            ),
    )
    spec = _spec(request=request, adapter=adapter, endpoint="equity_price_historical")
    settings = _mongo_settings(monkeypatch)
    _MongoClient.database["openbb_raw_payloads"].raise_write = RuntimeError("raw write failed")
    service = DomainPackService(settings=settings, adapters=(adapter,))

    pack = service.get_pack(request, _plan(request, (spec,)))

    assert len(pack.attempts) == 1
    assert pack.attempts[0].status == ProviderStatus.EVIDENCE_WRITE_FAILED
    assert pack.attempts[0].error_code == "evidence_write_failed"


def test_domain_pack_service_routes_fundamental_news_and_social(monkeypatch) -> None:
    fundamental_request = _request(PackDomain.FUNDAMENTAL)
    fundamental = _Adapter(
        adapter_id="fundamental.yfinance.us",
        provider_id="openbb_yfinance",
        domain=PackDomain.FUNDAMENTAL,
        source_role=SourceRole.FUNDAMENTAL_DATA,
        rows=(
            {
                "valuation.pe": 30.1,
                "valuation.pb": 12.4,
                "financial_indicators.roe": 0.31,
            },
        ),
        provider_kind=ProviderKind.OPENBB_NATIVE,
    )
    fundamental_spec = _spec(request=fundamental_request, adapter=fundamental, endpoint="profile+ratios")

    news_request = _request(PackDomain.NEWS)
    news = _Adapter(
        adapter_id="news.sec.us",
        provider_id="sec",
        domain=PackDomain.NEWS,
        source_role=SourceRole.OFFICIAL_ORIGINAL,
        rows=({"title": "10-K filed", "url": "https://example.com/sec"},),
        provider_kind=ProviderKind.OPENBB_NATIVE,
    )
    news_spec = _spec(request=news_request, adapter=news, endpoint="filings")

    social_request = _request(PackDomain.SOCIAL)
    social = _Adapter(
        adapter_id="social.reddit.us",
        provider_id="reddit",
        domain=PackDomain.SOCIAL,
        source_role=SourceRole.SOCIAL_ORIGINAL_SAMPLE,
        rows=({"title": "Discussion sample", "url": "https://example.com/reddit"},),
    )
    social_spec = _spec(request=social_request, adapter=social, endpoint="posts")

    service = DomainPackService(settings=_mongo_settings(monkeypatch), adapters=(fundamental, news, social))

    fundamental_pack = service.get_pack(fundamental_request, _plan(fundamental_request, (fundamental_spec,)))
    news_pack = service.get_pack(news_request, _plan(news_request, (news_spec,)))
    social_pack = service.get_pack(social_request, _plan(social_request, (social_spec,)))

    assert fundamental_pack.compact_facts["valuation.pe"] == 30.1
    assert "官方原文" in news_pack.reader_brief_md
    assert "原始社交样本" in social_pack.reader_brief_md


def test_domain_pack_service_fundamental_news_social_success_include_evidence_refs(monkeypatch) -> None:
    fundamental_request = _request(PackDomain.FUNDAMENTAL)
    fundamental = _Adapter(
        adapter_id="fundamental.yfinance.us",
        provider_id="openbb_yfinance",
        domain=PackDomain.FUNDAMENTAL,
        source_role=SourceRole.FUNDAMENTAL_DATA,
        rows=(
            {
                "valuation.pe": 30.1,
                "valuation.pb": 12.4,
                "financial_indicators.roe": 0.31,
            },
        ),
    )
    fundamental_spec = _spec(request=fundamental_request, adapter=fundamental, endpoint="profile+ratios")

    news_request = _request(PackDomain.NEWS)
    news = _Adapter(
        adapter_id="news.sec.us",
        provider_id="sec",
        domain=PackDomain.NEWS,
        source_role=SourceRole.OFFICIAL_ORIGINAL,
        rows=({"title": "10-K filed", "url": "https://example.com/sec"},),
    )
    news_spec = _spec(request=news_request, adapter=news, endpoint="filings")

    social_request = _request(PackDomain.SOCIAL)
    social = _Adapter(
        adapter_id="social.reddit.us",
        provider_id="reddit",
        domain=PackDomain.SOCIAL,
        source_role=SourceRole.SOCIAL_ORIGINAL_SAMPLE,
        rows=({"title": "Discussion sample", "url": "https://example.com/reddit"},),
    )
    social_spec = _spec(request=social_request, adapter=social, endpoint="posts")

    service = DomainPackService(settings=_mongo_settings(monkeypatch), adapters=(fundamental, news, social))

    fundamental_pack = service.get_pack(fundamental_request, _plan(fundamental_request, (fundamental_spec,)))
    news_pack = service.get_pack(news_request, _plan(news_request, (news_spec,)))
    social_pack = service.get_pack(social_request, _plan(social_request, (social_spec,)))

    for pack in (fundamental_pack, news_pack, social_pack):
        assert len(pack.attempts) == 1
        attempt = pack.attempts[0]
        assert attempt.status == ProviderStatus.REMOTE_SUCCESS
        assert attempt.raw_ref is not None and attempt.raw_ref.startswith("mongo://openbb_raw_payloads/")
        assert attempt.normalized_ref is not None and attempt.normalized_ref.startswith("mongo://openbb_normalized/")
        assert pack.raw_refs == (attempt.raw_ref,)
        assert pack.normalized_refs == (attempt.normalized_ref,)


def test_domain_pack_service_fundamental_news_social_evidence_write_failure_maps_to_failed(monkeypatch) -> None:
    for domain, source_role in (
        (PackDomain.FUNDAMENTAL, SourceRole.FUNDAMENTAL_DATA),
        (PackDomain.NEWS, SourceRole.OFFICIAL_ORIGINAL),
        (PackDomain.SOCIAL, SourceRole.SOCIAL_ORIGINAL_SAMPLE),
    ):
        request = _request(domain)
        adapter = _Adapter(
            adapter_id=f"{domain.value}.provider",
            provider_id=f"{domain.value}_provider",
            domain=domain,
            source_role=source_role,
            rows=({"k": "v"},),
        )
        spec = _spec(request=request, adapter=adapter, endpoint="endpoint")
        settings = _mongo_settings(monkeypatch)
        _MongoClient.database["openbb_raw_payloads"].raise_write = RuntimeError("raw write failed")
        service = DomainPackService(settings=settings, adapters=(adapter,))

        pack = service.get_pack(request, _plan(request, (spec,)))

        assert len(pack.attempts) == 1
        assert pack.attempts[0].status == ProviderStatus.EVIDENCE_WRITE_FAILED
        assert pack.attempts[0].error_code == "evidence_write_failed"


def test_domain_pack_service_empty_news_and_social_specs_emit_explicit_gaps() -> None:
    service = DomainPackService(settings=object(), adapters=())
    news_request = _request(PackDomain.NEWS)
    social_request = _request(PackDomain.SOCIAL)

    news_pack = service.get_pack(news_request, _plan(news_request, ()))
    social_pack = service.get_pack(social_request, _plan(social_request, ()))

    assert [gap.reason for gap in news_pack.data_gaps] == [DataGapReason.SOURCE_NOT_CONFIGURED]
    assert [gap.reason for gap in social_pack.data_gaps] == [DataGapReason.SOURCE_NOT_CONFIGURED]
    assert news_pack.readiness.status.value == "insufficient"
    assert social_pack.readiness.status.value == "insufficient"


def test_domain_pack_service_does_not_import_legacy_provider_paths() -> None:
    source = inspect.getsource(service_module)

    assert "frontline_data_pack" not in source
    assert "provider_executor" not in source
    assert "run_provider_call_gate" in source
