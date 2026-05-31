from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Mapping

from claw_trade.data_gateway.providers import market_adapters
from claw_trade.data_gateway.models import (
    AdmissionCheckStatus,
    CredentialStatus,
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
    SourceRole,
    utc_now_iso,
)
from claw_trade.data_gateway.providers.execution import ProviderExecutionEvidenceHelper
from claw_trade.data_gateway.store.attempts import MongoAttemptStore
from claw_trade.data_gateway.store.http_evidence import MongoProviderHttpEvidenceStore
from claw_trade.data_gateway.store.normalized import MongoNormalizedStore
from claw_trade.data_gateway.store.raw_payloads import MongoRawPayloadStore


class _Collection:
    def __init__(self) -> None:
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
        key = doc["_id"]
        self.docs[key] = dict(doc)


@dataclass
class _ContractAdapter:
    adapter_id: str = "market.openbb.us"
    provider_id: str = "market_openbb"
    adapter_kind: str = "project_extension"
    provider_kind: ProviderKind = ProviderKind.PROJECT_EXTENSION
    rows: tuple[Mapping[str, Any], ...] = (
        {"date": "2026-05-17", "close": 101.2, "volume": 1000},
    )

    def capabilities(self) -> tuple[ProviderCapability, ...]:
        return (
            ProviderCapability(
                provider=self.provider_id,
                adapter_id=self.adapter_id,
                provider_kind=self.provider_kind,
                market=Market.US,
                domain=PackDomain.MARKET,
                endpoint="equity_price_historical",
                source_role=SourceRole.MARKET_DATA,
                expected_schema_id="us.market.ohlcv.v1",
                license_policy_id="personal_research",
                credential_requirements=(),
                rate_limit_policy_id="default",
                cache_ttl_seconds=300,
                required=True,
                attempt_required=True,
                coverage_group="us_market",
                coverage_quorum=1,
                priority=0,
                priority_source=PrioritySource.SYSTEM_DEFAULT,
            ),
        )

    def validate_credentials(self) -> CredentialStatus:
        return CredentialStatus(
            status=AdmissionCheckStatus.PASS,
            provider=self.provider_id,
            adapter_id=self.adapter_id,
        )

    def build_call_specs(self, request: PackRequest) -> tuple[object, ...]:
        del request
        return ()

    def fetch(self, spec: ProviderCallSpec, request: PackRequest) -> ProviderFetch:
        del spec, request
        return ProviderFetch(
            payload={"rows": list(self.rows)},
            content_type="application/json",
            source_url="https://api.example.com/market",
            is_empty=False,
            row_count=len(self.rows),
            provider_request_id="req-contract-1",
            response_status_code=200,
            response_headers_summary={"content-type": "application/json"},
        )

    def normalize(self, spec: ProviderCallSpec, fetch: ProviderFetch) -> NormalizedResult:
        del spec, fetch
        return NormalizedResult(
            status=ProviderStatus.REMOTE_SUCCESS,
            schema_id="us.market.ohlcv.v1",
            rows=self.rows,
            compact_facts={"close": 101.2},
            row_count=len(self.rows),
            field_units={"close": "USD"},
            currency="USD",
            timezone="America/New_York",
            source_raw_ref=None,
        )


@dataclass
class _RequestsAdapter(_ContractAdapter):
    def fetch(self, spec: ProviderCallSpec, request: PackRequest) -> ProviderFetch:
        del spec, request
        import requests

        response = requests.get("https://api.example.com/live", params={"symbol": "AAPL"}, timeout=10)
        return ProviderFetch(
            payload={"rows": list(self.rows)},
            content_type="application/json",
            source_url=response.url,
            is_empty=False,
            row_count=len(self.rows),
            provider_request_id="req-contract-transport",
        )


@dataclass
class _AiohttpAdapter(_ContractAdapter):
    def fetch(self, spec: ProviderCallSpec, request: PackRequest) -> ProviderFetch:
        del spec, request

        async def _fetch() -> Any:
            import aiohttp

            async with aiohttp.ClientSession() as session:
                return await session._request("GET", "https://api.example.com/aiohttp")

        response = asyncio.run(_fetch())
        return ProviderFetch(
            payload={"rows": list(self.rows)},
            content_type="application/json",
            source_url=str(response.url),
            is_empty=False,
            row_count=len(self.rows),
            provider_request_id="req-contract-aiohttp",
        )


@dataclass
class _RequestsFailureAdapter(_ContractAdapter):
    def fetch(self, spec: ProviderCallSpec, request: PackRequest) -> ProviderFetch:
        del spec, request
        market_adapters._http_get_json(
            "https://user:very-secret@api.example.com/fail?symbol=AAPL"
            "&api_key=secret-token&auth=top-secret"
            "&accessToken=camel-secret&x-api-key=x-secret&api-key=dash-secret"
        )
        raise AssertionError("expected _http_get_json to raise before this line")


@dataclass
class _SdkInvisibleManagedHttpAdapter(_ContractAdapter):
    def fetch(self, spec: ProviderCallSpec, request: PackRequest) -> ProviderFetch:
        del spec, request
        return ProviderFetch(
            payload={"rows": list(self.rows)},
            content_type="application/json",
            source_url=None,
            is_empty=False,
            row_count=len(self.rows),
            provider_request_id="req-sdk-invisible",
            response_status_code=None,
            response_headers_summary=None,
        )


@dataclass
class _ManagedHttp429Adapter(_ContractAdapter):
    def fetch(self, spec: ProviderCallSpec, request: PackRequest) -> ProviderFetch:
        del spec, request
        return ProviderFetch(
            payload={"error": "rate limited"},
            content_type="application/json",
            source_url="https://api.example.com/market?symbol=AAPL",
            is_empty=True,
            row_count=0,
            provider_request_id="req-managed-http-429",
            response_status_code=429,
            response_headers_summary={"retry-after": "60", "content-type": "application/json"},
        )


@dataclass
class _ManagedHttp429NoHeadersAdapter(_ContractAdapter):
    def fetch(self, spec: ProviderCallSpec, request: PackRequest) -> ProviderFetch:
        del spec, request
        return ProviderFetch(
            payload={"error": "rate limited"},
            content_type="application/json",
            source_url="https://api.example.com/market?symbol=AAPL",
            is_empty=True,
            row_count=0,
            provider_request_id="req-managed-http-429-no-headers",
            response_status_code=429,
            response_headers_summary={},
        )


@dataclass
class _ManagedHttp5xxNoHeadersAdapter(_ContractAdapter):
    def fetch(self, spec: ProviderCallSpec, request: PackRequest) -> ProviderFetch:
        del spec, request
        return ProviderFetch(
            payload={"error": "server error"},
            content_type="application/json",
            source_url="https://api.example.com/market?symbol=AAPL",
            is_empty=True,
            row_count=0,
            provider_request_id="req-managed-http-5xx-no-headers",
            response_status_code=503,
            response_headers_summary={},
        )


@dataclass
class _ManagedHttpUrlOnlyAdapter(_ContractAdapter):
    def fetch(self, spec: ProviderCallSpec, request: PackRequest) -> ProviderFetch:
        del spec, request
        return ProviderFetch(
            payload={"rows": list(self.rows)},
            content_type="application/json",
            source_url="https://api.example.com/market?symbol=AAPL",
            is_empty=False,
            row_count=len(self.rows),
            provider_request_id="req-managed-http-url-only",
            response_status_code=None,
            response_headers_summary={},
        )


@dataclass
class _CredentialMissingValidateAdapter(_ContractAdapter):
    def validate_credentials(self) -> CredentialStatus:
        return CredentialStatus(
            status=AdmissionCheckStatus.MISSING,
            provider=self.provider_id,
            adapter_id=self.adapter_id,
            missing_keys=("API_KEY",),
            root_cause="missing credential keys: API_KEY",
        )


@dataclass
class _CredentialMissingFetchAdapter(_ContractAdapter):
    def fetch(self, spec: ProviderCallSpec, request: PackRequest) -> ProviderFetch:
        del spec, request
        raise RuntimeError("missing credential keys: API_KEY")


def _request() -> PackRequest:
    return PackRequest(
        run_id="run-provider-exec",
        call_id="call-provider-exec",
        worker_id="market_analyst",
        market=Market.US,
        domain=PackDomain.MARKET,
        ticker="AAPL",
        company_name="Apple",
        start_date="2026-05-01",
        end_date="2026-05-17",
        current_date="2026-05-17",
        currency="USD",
        profile="US",
        freshness_policy=FreshnessPolicy(max_age_seconds=300),
    )


def _spec() -> ProviderCallSpec:
    return ProviderCallSpec(
        call_key="market:openbb:equity_price_historical",
        provider="market_openbb",
        adapter_id="market.openbb.us",
        provider_kind=ProviderKind.PROJECT_EXTENSION,
        provider_config_version="cfg-v1",
        endpoint="equity_price_historical",
        source_role=SourceRole.MARKET_DATA,
        market=Market.US,
        domain=PackDomain.MARKET,
        required=True,
        attempt_required=True,
        coverage_group="us_market",
        coverage_quorum=1,
        params={"ticker": "AAPL"},
        cache_ttl_seconds=300,
        license_policy_id="personal_research",
        expected_schema_id="us.market.ohlcv.v1",
        priority=0,
        priority_source=PrioritySource.SYSTEM_DEFAULT,
        user_preferred=False,
        managed_http_required=False,
    )


def _helper(
    *,
    raw_collection: _Collection,
    normalized_collection: _Collection,
    attempt_collection: _Collection,
    http_collection: _Collection | None = None,
) -> ProviderExecutionEvidenceHelper:
    return ProviderExecutionEvidenceHelper(
        raw_store=MongoRawPayloadStore(raw_collection),
        normalized_store=MongoNormalizedStore(normalized_collection),
        attempt_store=MongoAttemptStore(attempt_collection),
        http_evidence_store=MongoProviderHttpEvidenceStore(http_collection) if http_collection is not None else None,
    )


def test_success_writes_raw_normalized_attempt_refs() -> None:
    normalized_collection = _Collection()
    http_collection = _Collection()
    helper = _helper(
        raw_collection=_Collection(),
        normalized_collection=normalized_collection,
        attempt_collection=_Collection(),
        http_collection=http_collection,
    )
    adapter = _ContractAdapter()
    result = helper.execute(
        request=_request(),
        spec=_spec(),
        adapter=adapter,
        started_at=utc_now_iso(),
    )

    assert result.status == ProviderStatus.REMOTE_SUCCESS
    assert result.raw_ref is not None and result.raw_ref.startswith("mongo://openbb_raw_payloads/")
    assert result.normalized_ref is not None and result.normalized_ref.startswith("mongo://openbb_normalized/")
    assert result.attempt.raw_ref == result.raw_ref
    assert result.attempt.normalized_ref == result.normalized_ref
    assert result.attempt.status == ProviderStatus.REMOTE_SUCCESS
    normalized_docs = tuple(normalized_collection.docs.values())
    assert len(normalized_docs) == 1
    assert normalized_docs[0]["source_raw_ref"] == result.raw_ref
    http_docs = tuple(http_collection.docs.values())
    assert len(http_docs) == 1
    assert http_docs[0]["provider"] == "market_openbb"
    assert http_docs[0]["status"] == ProviderStatus.REMOTE_SUCCESS.value
    assert http_docs[0]["source_url"] == "https://api.example.com/market"
    assert http_docs[0]["response_status_code"] == 200
    assert http_docs[0]["response_headers_summary"] == {"content-type": "application/json"}
    assert http_docs[0]["provider_request_id"] == "req-contract-1"
    assert http_docs[0]["raw_ref"] == result.raw_ref


def test_validate_credentials_missing_maps_to_credential_missing_without_fetch() -> None:
    http_collection = _Collection()
    result = _helper(
        raw_collection=_Collection(),
        normalized_collection=_Collection(),
        attempt_collection=_Collection(),
        http_collection=http_collection,
    ).execute(
        request=_request(),
        spec=_spec(),
        adapter=_CredentialMissingValidateAdapter(),
        started_at=utc_now_iso(),
    )

    assert result.status == ProviderStatus.CREDENTIAL_MISSING
    assert result.attempt.status == ProviderStatus.CREDENTIAL_MISSING
    assert result.raw_ref is None
    assert result.normalized_ref is None
    assert result.error_code == ProviderStatus.CREDENTIAL_MISSING.value
    assert not http_collection.docs


def test_fetch_credential_missing_exception_maps_to_credential_missing() -> None:
    http_collection = _Collection()
    result = _helper(
        raw_collection=_Collection(),
        normalized_collection=_Collection(),
        attempt_collection=_Collection(),
        http_collection=http_collection,
    ).execute(
        request=_request(),
        spec=_spec(),
        adapter=_CredentialMissingFetchAdapter(),
        started_at=utc_now_iso(),
    )

    assert result.status == ProviderStatus.CREDENTIAL_MISSING
    assert result.attempt.status == ProviderStatus.CREDENTIAL_MISSING
    assert result.raw_ref is None
    assert result.normalized_ref is None
    assert result.error_code == ProviderStatus.CREDENTIAL_MISSING.value


def test_success_captures_real_requests_http_metadata_when_fetch_omits_it(monkeypatch: Any) -> None:
    def _request_transport(_session: Any, method: str, url: str, **_: Any) -> SimpleNamespace:
        assert method.upper() == "GET"
        return SimpleNamespace(
            status_code=202,
            headers={
                "Content-Type": "application/json",
                "X-Request-Id": "transport-req-1",
                "Set-Cookie": "must-not-enter-evidence",
            },
            url=(
                "https://user:super-secret@api.example.com/live?"
                "symbol=AAPL&api_key=secret-token&auth=top-secret"
                "&accessToken=camel-secret&x-api-key=x-secret&api-key=dash-secret"
            ),
        )

    import requests

    monkeypatch.setattr(requests.sessions.Session, "request", _request_transport)
    http_collection = _Collection()
    result = _helper(
        raw_collection=_Collection(),
        normalized_collection=_Collection(),
        attempt_collection=_Collection(),
        http_collection=http_collection,
    ).execute(request=_request(), spec=_spec(), adapter=_RequestsAdapter(), started_at=utc_now_iso())

    assert result.status == ProviderStatus.REMOTE_SUCCESS
    http_docs = tuple(http_collection.docs.values())
    assert len(http_docs) == 1
    assert "symbol=AAPL" in http_docs[0]["source_url"]
    assert "api_key=" in http_docs[0]["source_url"]
    assert "auth=" in http_docs[0]["source_url"]
    assert "accessToken=" in http_docs[0]["source_url"]
    assert "x-api-key=" in http_docs[0]["source_url"]
    assert "api-key=" in http_docs[0]["source_url"]
    assert "[REDACTED]@api.example.com" in http_docs[0]["source_url"]
    assert "secret-token" not in http_docs[0]["source_url"]
    assert "top-secret" not in http_docs[0]["source_url"]
    assert "camel-secret" not in http_docs[0]["source_url"]
    assert "x-secret" not in http_docs[0]["source_url"]
    assert "dash-secret" not in http_docs[0]["source_url"]
    assert "user:super-secret@" not in http_docs[0]["source_url"]
    assert http_docs[0]["response_status_code"] == 202
    assert http_docs[0]["response_headers_summary"] == {
        "content-type": "application/json",
        "x-request-id": "transport-req-1",
    }
    assert "set-cookie" not in http_docs[0]["response_headers_summary"]


def test_success_captures_real_aiohttp_metadata_when_fetch_omits_it(monkeypatch: Any) -> None:
    async def _request_transport(_session: Any, method: str, url: str, **_: Any) -> SimpleNamespace:
        assert method.upper() == "GET"
        return SimpleNamespace(
            status=206,
            headers={
                "Content-Type": "application/json",
                "Server": "aiohttp-test",
                "Authorization": "must-not-enter-evidence",
            },
            url="https://user:plain-secret@api.example.com/aiohttp",
        )

    import aiohttp

    monkeypatch.setattr(aiohttp.ClientSession, "_request", _request_transport)
    http_collection = _Collection()
    result = _helper(
        raw_collection=_Collection(),
        normalized_collection=_Collection(),
        attempt_collection=_Collection(),
        http_collection=http_collection,
    ).execute(request=_request(), spec=_spec(), adapter=_AiohttpAdapter(), started_at=utc_now_iso())

    assert result.status == ProviderStatus.REMOTE_SUCCESS
    http_docs = tuple(http_collection.docs.values())
    assert len(http_docs) == 1
    assert http_docs[0]["source_url"] == "https://[REDACTED]@api.example.com/aiohttp"
    assert "plain-secret" not in http_docs[0]["source_url"]
    assert http_docs[0]["response_status_code"] == 206
    assert http_docs[0]["response_headers_summary"] == {
        "content-type": "application/json",
        "server": "aiohttp-test",
    }
    assert "authorization" not in http_docs[0]["response_headers_summary"]


def test_raw_normalized_and_attempt_write_failures_map_to_evidence_write_failed() -> None:
    request = _request()
    spec = _spec()
    adapter = _ContractAdapter()

    raw_collection = _Collection()
    raw_collection.raise_write = RuntimeError("raw write failed")
    raw_http_collection = _Collection()
    raw_failure = _helper(
        raw_collection=raw_collection,
        normalized_collection=_Collection(),
        attempt_collection=_Collection(),
        http_collection=raw_http_collection,
    ).execute(request=request, spec=spec, adapter=adapter, started_at=utc_now_iso())
    assert raw_failure.status == ProviderStatus.EVIDENCE_WRITE_FAILED
    assert raw_failure.error_code == "evidence_write_failed"
    raw_http_docs = tuple(raw_http_collection.docs.values())
    assert len(raw_http_docs) == 1
    assert raw_http_docs[0]["status"] == ProviderStatus.REMOTE_SUCCESS.value
    assert raw_http_docs[0]["raw_ref"] is None

    normalized_collection = _Collection()
    normalized_collection.raise_write = RuntimeError("normalized write failed")
    normalized_http_collection = _Collection()
    normalized_failure = _helper(
        raw_collection=_Collection(),
        normalized_collection=normalized_collection,
        attempt_collection=_Collection(),
        http_collection=normalized_http_collection,
    ).execute(request=request, spec=spec, adapter=adapter, started_at=utc_now_iso())
    assert normalized_failure.status == ProviderStatus.EVIDENCE_WRITE_FAILED
    assert normalized_failure.error_code == "evidence_write_failed"
    normalized_http_docs = tuple(normalized_http_collection.docs.values())
    assert len(normalized_http_docs) == 1
    assert normalized_http_docs[0]["status"] == ProviderStatus.REMOTE_SUCCESS.value
    assert normalized_http_docs[0]["raw_ref"] == normalized_failure.raw_ref

    attempt_collection = _Collection()
    attempt_collection.raise_write = RuntimeError("attempt write failed")
    attempt_failure = _helper(
        raw_collection=_Collection(),
        normalized_collection=_Collection(),
        attempt_collection=attempt_collection,
    ).execute(request=request, spec=spec, adapter=adapter, started_at=utc_now_iso())
    assert attempt_failure.status == ProviderStatus.EVIDENCE_WRITE_FAILED
    assert attempt_failure.error_code == "evidence_write_failed"


def test_http_evidence_write_failure_maps_to_evidence_write_failed() -> None:
    http_collection = _Collection()
    http_collection.raise_write = RuntimeError("http evidence failed")
    result = _helper(
        raw_collection=_Collection(),
        normalized_collection=_Collection(),
        attempt_collection=_Collection(),
        http_collection=http_collection,
    ).execute(request=_request(), spec=_spec(), adapter=_ContractAdapter(), started_at=utc_now_iso())

    assert result.status == ProviderStatus.EVIDENCE_WRITE_FAILED
    assert result.error_code == "evidence_write_failed"
    assert result.raw_ref is not None
    assert result.normalized_ref is not None


def test_remote_error_message_and_http_source_url_redact_secrets(monkeypatch: Any) -> None:
    import requests

    def _request_transport(_session: Any, method: str, url: str, **_: Any) -> SimpleNamespace:
        del method
        raise requests.RequestException(f"403 Client Error: Forbidden for url: {url}")

    monkeypatch.setattr(requests.sessions.Session, "request", _request_transport)
    http_collection = _Collection()
    result = _helper(
        raw_collection=_Collection(),
        normalized_collection=_Collection(),
        attempt_collection=_Collection(),
        http_collection=http_collection,
    ).execute(request=_request(), spec=_spec(), adapter=_RequestsFailureAdapter(), started_at=utc_now_iso())

    assert result.status == ProviderStatus.REMOTE_ERROR
    assert result.error_message is not None
    assert "secret-token" not in result.error_message
    assert "top-secret" not in result.error_message
    assert "camel-secret" not in result.error_message
    assert "x-secret" not in result.error_message
    assert "dash-secret" not in result.error_message
    assert "api_key=" in result.error_message
    assert "auth=" in result.error_message
    assert "accessToken=" in result.error_message
    assert "x-api-key=" in result.error_message
    assert "api-key=" in result.error_message
    assert "[REDACTED]@api.example.com" in result.error_message
    assert "user:very-secret@" not in result.error_message

    http_docs = tuple(http_collection.docs.values())
    assert len(http_docs) == 1
    assert "symbol=AAPL" in http_docs[0]["source_url"]
    assert "api_key=" in http_docs[0]["source_url"]
    assert "auth=" in http_docs[0]["source_url"]
    assert "accessToken=" in http_docs[0]["source_url"]
    assert "x-api-key=" in http_docs[0]["source_url"]
    assert "api-key=" in http_docs[0]["source_url"]
    assert "[REDACTED]@api.example.com" in http_docs[0]["source_url"]
    assert "secret-token" not in http_docs[0]["source_url"]
    assert "top-secret" not in http_docs[0]["source_url"]
    assert "camel-secret" not in http_docs[0]["source_url"]
    assert "x-secret" not in http_docs[0]["source_url"]
    assert "dash-secret" not in http_docs[0]["source_url"]
    assert "user:very-secret@" not in http_docs[0]["source_url"]


def test_managed_http_required_without_visible_http_evidence_maps_to_sdk_unknown() -> None:
    spec = _spec()
    spec = ProviderCallSpec(**{**spec.__dict__, "managed_http_required": True})
    http_collection = _Collection()
    result = _helper(
        raw_collection=_Collection(),
        normalized_collection=_Collection(),
        attempt_collection=_Collection(),
        http_collection=http_collection,
    ).execute(
        request=_request(),
        spec=spec,
        adapter=_SdkInvisibleManagedHttpAdapter(),
        started_at=utc_now_iso(),
    )

    assert result.status == ProviderStatus.SDK_HTTP_UNKNOWN
    assert result.attempt.status == ProviderStatus.SDK_HTTP_UNKNOWN
    assert result.raw_ref is None
    assert result.normalized_ref is None
    assert result.error_code == ProviderStatus.SDK_HTTP_UNKNOWN.value
    assert not http_collection.docs


def test_http_429_never_maps_to_remote_success() -> None:
    spec = _spec()
    spec = ProviderCallSpec(**{**spec.__dict__, "managed_http_required": True})
    http_collection = _Collection()
    result = _helper(
        raw_collection=_Collection(),
        normalized_collection=_Collection(),
        attempt_collection=_Collection(),
        http_collection=http_collection,
    ).execute(
        request=_request(),
        spec=spec,
        adapter=_ManagedHttp429Adapter(),
        started_at=utc_now_iso(),
    )

    assert result.status == ProviderStatus.RATE_LIMITED
    assert result.attempt.status == ProviderStatus.RATE_LIMITED
    assert result.raw_ref is None
    assert result.normalized_ref is None
    assert result.error_code == "http_429"
    http_docs = tuple(http_collection.docs.values())
    assert len(http_docs) == 1
    assert http_docs[0]["status"] == ProviderStatus.RATE_LIMITED.value
    assert http_docs[0]["response_status_code"] == 429


def test_managed_http_required_with_url_only_does_not_remote_success() -> None:
    spec = _spec()
    spec = ProviderCallSpec(**{**spec.__dict__, "managed_http_required": True})
    http_collection = _Collection()
    result = _helper(
        raw_collection=_Collection(),
        normalized_collection=_Collection(),
        attempt_collection=_Collection(),
        http_collection=http_collection,
    ).execute(
        request=_request(),
        spec=spec,
        adapter=_ManagedHttpUrlOnlyAdapter(),
        started_at=utc_now_iso(),
    )

    assert result.status == ProviderStatus.SDK_HTTP_UNKNOWN
    assert result.attempt.status == ProviderStatus.SDK_HTTP_UNKNOWN
    assert result.raw_ref is None
    assert result.normalized_ref is None
    assert not http_collection.docs


def test_managed_http_required_429_without_headers_is_still_rate_limited() -> None:
    spec = _spec()
    spec = ProviderCallSpec(**{**spec.__dict__, "managed_http_required": True})
    http_collection = _Collection()
    result = _helper(
        raw_collection=_Collection(),
        normalized_collection=_Collection(),
        attempt_collection=_Collection(),
        http_collection=http_collection,
    ).execute(
        request=_request(),
        spec=spec,
        adapter=_ManagedHttp429NoHeadersAdapter(),
        started_at=utc_now_iso(),
    )

    assert result.status == ProviderStatus.RATE_LIMITED
    assert result.attempt.status == ProviderStatus.RATE_LIMITED
    assert result.error_code == "http_429"
    http_docs = tuple(http_collection.docs.values())
    assert len(http_docs) == 1
    assert http_docs[0]["status"] == ProviderStatus.RATE_LIMITED.value
    assert http_docs[0]["response_status_code"] == 429


def test_managed_http_required_5xx_without_headers_is_remote_error() -> None:
    spec = _spec()
    spec = ProviderCallSpec(**{**spec.__dict__, "managed_http_required": True})
    http_collection = _Collection()
    result = _helper(
        raw_collection=_Collection(),
        normalized_collection=_Collection(),
        attempt_collection=_Collection(),
        http_collection=http_collection,
    ).execute(
        request=_request(),
        spec=spec,
        adapter=_ManagedHttp5xxNoHeadersAdapter(),
        started_at=utc_now_iso(),
    )

    assert result.status == ProviderStatus.REMOTE_ERROR
    assert result.attempt.status == ProviderStatus.REMOTE_ERROR
    http_docs = tuple(http_collection.docs.values())
    assert len(http_docs) == 1
    assert http_docs[0]["status"] == ProviderStatus.REMOTE_ERROR.value
    assert http_docs[0]["response_status_code"] == 503


def test_mismatched_call_spec_is_explicit_remote_error_not_empty_success() -> None:
    bad_spec = _spec()
    bad_spec = ProviderCallSpec(**{**bad_spec.__dict__, "adapter_id": "market.some-other-adapter"})
    http_collection = _Collection()
    result = _helper(
        raw_collection=_Collection(),
        normalized_collection=_Collection(),
        attempt_collection=_Collection(),
        http_collection=http_collection,
    ).execute(
        request=_request(),
        spec=bad_spec,
        adapter=_ContractAdapter(),
        started_at=utc_now_iso(),
    )

    assert result.status == ProviderStatus.REMOTE_ERROR
    assert result.attempt.status == ProviderStatus.REMOTE_ERROR
    assert result.error_code == "provider_call_spec_mismatch"
    assert result.raw_ref is None
    assert result.normalized_ref is None
