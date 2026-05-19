from __future__ import annotations

from dataclasses import dataclass
import inspect
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
from claw_trade.data_gateway.providers.execution import ProviderExecutionEvidenceHelper
from claw_trade.data_gateway.store.attempts import MongoAttemptStore
from claw_trade.data_gateway.store.http_evidence import MongoProviderHttpEvidenceStore
from claw_trade.data_gateway.store.normalized import MongoNormalizedStore
from claw_trade.data_gateway.store.raw_payloads import MongoRawPayloadStore


@dataclass
class _Adapter:
    adapter_id: str
    provider_id: str
    domain: PackDomain
    source_role: SourceRole
    rows: tuple[Mapping[str, Any], ...]
    status: ProviderStatus = ProviderStatus.REMOTE_SUCCESS
    credential_missing: bool = False
    adapter_kind: str = "project_extension"
    provider_kind: ProviderKind = ProviderKind.PROJECT_EXTENSION

    def capabilities(self) -> tuple[ProviderCapability, ...]:
        return ()

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

    def update_one(self, query: dict[str, Any], update: dict[str, Any], upsert: bool = False) -> None:
        del upsert
        if self.raise_write is not None:
            raise self.raise_write
        key = query["_id"]
        current = self.docs.get(key, {})
        current.update(update.get("$setOnInsert", {}))
        current.update(update.get("$set", {}))
        if "_id" not in current:
            current["_id"] = key
        self.docs[key] = current

    def insert_one(self, doc: dict[str, Any]) -> None:
        if self.raise_write is not None:
            raise self.raise_write
        self.docs[doc["_id"]] = dict(doc)


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


def test_domain_pack_service_routes_market_success_to_market_builder() -> None:
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

    pack = DomainPackService(settings=object(), adapters=(adapter,)).get_pack(request, _plan(request, (spec,)))

    assert pack.compact_facts["ohlcv_row_count"] == 24
    assert {attempt.status for attempt in pack.attempts} == {ProviderStatus.REMOTE_SUCCESS}
    assert "远端获取成功" in pack.reader_brief_md


def test_domain_pack_service_market_success_generates_chart_images(tmp_path: Path) -> None:
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
    settings = type("_Settings", (), {"object_store_uri": tmp_path.as_uri()})()

    pack = DomainPackService(settings=settings, adapters=(adapter,)).get_pack(request, _plan(request, (spec,)))

    assert {asset.status.value for asset in pack.chart_assets} == {"ready"}
    for asset in pack.chart_assets:
        assert asset.image_ref is not None
        image_path = Path(unquote(urlparse(asset.image_ref).path))
        assert image_path.exists()
        assert image_path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


def test_domain_pack_service_market_pack_attempts_include_mongo_refs() -> None:
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

    helper = ProviderExecutionEvidenceHelper(
        raw_store=MongoRawPayloadStore(_Collection()),
        normalized_store=MongoNormalizedStore(_Collection()),
        attempt_store=MongoAttemptStore(_Collection()),
    )
    service = DomainPackService(
        settings=object(),
        adapters=(adapter,),
        provider_execution_helper=helper,
    )
    pack = service.get_pack(request, _plan(request, (spec,)))

    assert len(pack.attempts) == 1
    attempt = pack.attempts[0]
    assert attempt.status == ProviderStatus.REMOTE_SUCCESS
    assert attempt.raw_ref is not None and attempt.raw_ref.startswith("mongo://openbb_raw_payloads/")
    assert attempt.normalized_ref is not None and attempt.normalized_ref.startswith("mongo://openbb_normalized/")
    assert pack.raw_refs == (attempt.raw_ref,)
    assert pack.normalized_refs == (attempt.normalized_ref,)


def test_domain_pack_service_crypto_market_pack_writes_crypto_lens_evidence_when_mongo_configured(monkeypatch) -> None:
    _MongoClient.database = _Database()
    monkeypatch.setattr(service_module, "MongoClient", _MongoClient)
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
        currency="USD",
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
            "currency": "USD",
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
            "currency": "USD",
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
    settings = type("_Settings", (), {"mongo_uri": "mongodb://localhost/claw_trade_openbb"})()

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


def test_domain_pack_service_market_evidence_write_failure_maps_to_evidence_write_failed() -> None:
    request = _request(PackDomain.MARKET)
    adapter = _Adapter(
        adapter_id="market.openbb.us",
        provider_id="market_openbb",
        domain=PackDomain.MARKET,
        source_role=SourceRole.MARKET_DATA,
        rows=({"date": "2026-05-17", "close": 101},),
    )
    spec = _spec(request=request, adapter=adapter, endpoint="equity_price_historical")
    raw_collection = _Collection()
    raw_collection.raise_write = RuntimeError("raw write failed")
    helper = ProviderExecutionEvidenceHelper(
        raw_store=MongoRawPayloadStore(raw_collection),
        normalized_store=MongoNormalizedStore(_Collection()),
        attempt_store=MongoAttemptStore(_Collection()),
    )
    service = DomainPackService(
        settings=object(),
        adapters=(adapter,),
        provider_execution_helper=helper,
    )

    pack = service.get_pack(request, _plan(request, (spec,)))

    assert len(pack.attempts) == 1
    assert pack.attempts[0].status == ProviderStatus.EVIDENCE_WRITE_FAILED
    assert pack.attempts[0].error_code == "evidence_write_failed"


def test_domain_pack_service_routes_fundamental_news_and_social() -> None:
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

    service = DomainPackService(settings=object(), adapters=(fundamental, news, social))

    fundamental_pack = service.get_pack(fundamental_request, _plan(fundamental_request, (fundamental_spec,)))
    news_pack = service.get_pack(news_request, _plan(news_request, (news_spec,)))
    social_pack = service.get_pack(social_request, _plan(social_request, (social_spec,)))

    assert fundamental_pack.compact_facts["valuation.pe"] == 30.1
    assert "官方原文" in news_pack.reader_brief_md
    assert "原始社交样本" in social_pack.reader_brief_md


def test_domain_pack_service_fundamental_news_social_success_include_evidence_refs() -> None:
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

    helper = ProviderExecutionEvidenceHelper(
        raw_store=MongoRawPayloadStore(_Collection()),
        normalized_store=MongoNormalizedStore(_Collection()),
        attempt_store=MongoAttemptStore(_Collection()),
    )
    service = DomainPackService(
        settings=object(),
        adapters=(fundamental, news, social),
        provider_execution_helper=helper,
    )

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


def test_domain_pack_service_fundamental_news_social_evidence_write_failure_maps_to_failed() -> None:
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
        raw_collection = _Collection()
        raw_collection.raise_write = RuntimeError("raw write failed")
        helper = ProviderExecutionEvidenceHelper(
            raw_store=MongoRawPayloadStore(raw_collection),
            normalized_store=MongoNormalizedStore(_Collection()),
            attempt_store=MongoAttemptStore(_Collection()),
        )
        service = DomainPackService(settings=object(), adapters=(adapter,), provider_execution_helper=helper)

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
