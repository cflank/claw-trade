from __future__ import annotations

from dataclasses import dataclass, replace
import time
from typing import Any, Mapping, Protocol

from claw_trade.data_gateway.errors import DataGatewayError, DataGatewayErrorCode
from claw_trade.data_gateway.models import (
    FreshnessStatus,
    OpenBBProviderHttpEvidence,
    PackRequest,
    ProviderAttempt,
    ProviderCallSpec,
    ProviderFetch,
    ProviderResult,
    ProviderStatus,
    utc_now_iso,
)
from claw_trade.data_gateway.providers.base import ProviderAdapter
from claw_trade.data_gateway.providers.http_capture import CapturedHttpExchange, capture_provider_http
from claw_trade.data_gateway.providers.managed_http import (
    detect_quota_signal,
    has_visible_http_exchange,
    normalize_headers,
)
from claw_trade.data_gateway.providers.execution_wrapper import ProviderExecutionWrapper
from claw_trade.data_gateway.providers.fetcher import ProviderCallSpecBindingError

_ALLOWED_RAW_EXPORT_POLICIES = frozenset({"metadata_only", "redacted", "full"})
_DEFAULT_RAW_EXPORT_POLICY = "metadata_only"


class RawPayloadStoreLike(Protocol):
    def write_raw(
        self,
        *,
        request: PackRequest,
        spec: ProviderCallSpec,
        payload: bytes | str | Mapping[str, Any],
        content_type: str,
        source_url: str | None,
        raw_export_policy: str,
    ) -> str: ...


class NormalizedStoreLike(Protocol):
    def write(self, *, request: PackRequest, spec: ProviderCallSpec, normalized: Any) -> str: ...


class AttemptStoreLike(Protocol):
    def write(self, attempt: ProviderAttempt) -> str: ...


class HttpEvidenceStoreLike(Protocol):
    def write(self, evidence: OpenBBProviderHttpEvidence) -> str: ...


@dataclass
class ProviderExecutionEvidenceHelper:
    raw_store: RawPayloadStoreLike
    normalized_store: NormalizedStoreLike
    attempt_store: AttemptStoreLike
    http_evidence_store: HttpEvidenceStoreLike | None = None
    provider_settings: Mapping[str, Mapping[str, Any]] | None = None

    def record_attempt_only(self, result: ProviderResult) -> ProviderResult:
        try:
            self.attempt_store.write(result.attempt)
        except Exception as exc:  # noqa: BLE001
            gateway_error = self._as_gateway_error(exc)
            evidence_failed_attempt = replace(
                result.attempt,
                status=ProviderStatus.EVIDENCE_WRITE_FAILED,
                row_count=0,
                raw_ref=None,
                normalized_ref=None,
                error_code=gateway_error.code.value,
                error_message=gateway_error.root_cause,
            )
            return replace(
                result,
                status=ProviderStatus.EVIDENCE_WRITE_FAILED,
                freshness=FreshnessStatus.NOT_FETCHED,
                raw_ref=None,
                normalized_ref=None,
                rows=(),
                row_count=0,
                attempt=evidence_failed_attempt,
                error_code=gateway_error.code.value,
                error_message=gateway_error.root_cause,
            )
        return result

    def execute(
        self,
        *,
        request: PackRequest,
        spec: ProviderCallSpec,
        adapter: ProviderAdapter,
        started_at: str,
    ) -> ProviderResult:
        t0 = time.perf_counter()
        wrapper = ProviderExecutionWrapper(adapter=adapter)
        credential_status = wrapper.credential_status()
        if credential_status.missing:
            return self._result(
                request=request,
                spec=spec,
                adapter=adapter,
                started_at=started_at,
                status=ProviderStatus.CREDENTIAL_MISSING,
                freshness=FreshnessStatus.NOT_FETCHED,
                rows=(),
                row_count=0,
                raw_ref=None,
                normalized_ref=None,
                request_id=None,
                error_code=ProviderStatus.CREDENTIAL_MISSING.value,
                error_message=credential_status.root_cause or "credential missing",
                latency_ms=_elapsed_ms(t0),
            )

        captured_http: tuple[CapturedHttpExchange, ...] = ()
        try:
            with capture_provider_http() as http_capture:
                fetch = wrapper.fetch(spec=spec, request=request)
            captured_http = tuple(http_capture.exchanges)
            fetch = _merge_captured_http(fetch=fetch, exchanges=captured_http)
        except Exception as exc:  # noqa: BLE001
            captured_http = tuple(http_capture.exchanges) if "http_capture" in locals() else ()
            status, error_code = _status_from_fetch_exception(exc)
            latency_ms = _elapsed_ms(t0)
            http_error = self._write_http_evidence(
                request=request,
                spec=spec,
                started_at=started_at,
                finished_at=utc_now_iso(),
                latency_ms=latency_ms,
                status=status,
                request_id=None,
                fetch=None,
                captured_http=captured_http,
                raw_ref=None,
                error_code=error_code,
                error_message=str(exc),
            )
            if http_error is not None:
                return self._evidence_failed_result(
                    request=request,
                    spec=spec,
                    adapter=adapter,
                    started_at=started_at,
                    request_id=None,
                    raw_ref=None,
                    normalized_ref=None,
                    latency_ms=latency_ms,
                    error=http_error,
                )
            return self._result(
                request=request,
                spec=spec,
                adapter=adapter,
                started_at=started_at,
                status=status,
                freshness=FreshnessStatus.NOT_FETCHED,
                rows=(),
                row_count=0,
                raw_ref=None,
                normalized_ref=None,
                request_id=None,
                error_code=error_code,
                error_message=str(exc),
                latency_ms=latency_ms,
            )

        http_status, quota_signal = _status_from_http(fetch=fetch, captured_http=captured_http)
        if http_status in {ProviderStatus.RATE_LIMITED, ProviderStatus.REMOTE_ERROR}:
            latency_ms = _elapsed_ms(t0)
            error_code = quota_signal if http_status == ProviderStatus.RATE_LIMITED else None
            http_error = self._write_http_evidence(
                request=request,
                spec=spec,
                started_at=started_at,
                finished_at=utc_now_iso(),
                latency_ms=latency_ms,
                status=http_status,
                request_id=fetch.provider_request_id,
                fetch=fetch,
                captured_http=captured_http,
                raw_ref=None,
                error_code=error_code,
                error_message=None,
            )
            if http_error is not None:
                return self._evidence_failed_result(
                    request=request,
                    spec=spec,
                    adapter=adapter,
                    started_at=started_at,
                    request_id=fetch.provider_request_id,
                    raw_ref=None,
                    normalized_ref=None,
                    latency_ms=latency_ms,
                    error=http_error,
                )
            return self._result(
                request=request,
                spec=spec,
                adapter=adapter,
                started_at=started_at,
                status=http_status,
                freshness=FreshnessStatus.NOT_FETCHED,
                rows=(),
                row_count=0,
                raw_ref=None,
                normalized_ref=None,
                request_id=fetch.provider_request_id,
                error_code=error_code,
                error_message=None,
                latency_ms=latency_ms,
            )

        if spec.managed_http_required and not _has_visible_http_metadata(fetch=fetch, captured_http=captured_http):
            latency_ms = _elapsed_ms(t0)
            return self._result(
                request=request,
                spec=spec,
                adapter=adapter,
                started_at=started_at,
                status=ProviderStatus.SDK_HTTP_UNKNOWN,
                freshness=FreshnessStatus.NOT_FETCHED,
                rows=(),
                row_count=0,
                raw_ref=None,
                normalized_ref=None,
                request_id=fetch.provider_request_id,
                error_code=ProviderStatus.SDK_HTTP_UNKNOWN.value,
                error_message="managed_http_required but HTTP evidence is not visible",
                latency_ms=latency_ms,
            )

        try:
            raw_ref = self.raw_store.write_raw(
                request=request,
                spec=spec,
                payload=fetch.payload,
                content_type=fetch.content_type,
                source_url=fetch.source_url,
                raw_export_policy=self._resolve_raw_export_policy(spec),
            )
        except Exception as exc:  # noqa: BLE001
            latency_ms = _elapsed_ms(t0)
            self._write_http_evidence(
                request=request,
                spec=spec,
                started_at=started_at,
                finished_at=utc_now_iso(),
                latency_ms=latency_ms,
                status=ProviderStatus.EMPTY if fetch.is_empty else ProviderStatus.REMOTE_SUCCESS,
                request_id=fetch.provider_request_id,
                fetch=fetch,
                captured_http=captured_http,
                raw_ref=None,
                error_code=type(exc).__name__,
                error_message=str(exc),
            )
            return self._evidence_failed_result(
                request=request,
                spec=spec,
                adapter=adapter,
                started_at=started_at,
                request_id=fetch.provider_request_id,
                raw_ref=None,
                normalized_ref=None,
                latency_ms=latency_ms,
                error=self._as_gateway_error(exc),
            )

        try:
            normalized = wrapper.normalize(spec=spec, fetch=fetch)
        except Exception as exc:  # noqa: BLE001
            status, error_code = _status_from_fetch_exception(exc)
            latency_ms = _elapsed_ms(t0)
            http_error = self._write_http_evidence(
                request=request,
                spec=spec,
                started_at=started_at,
                finished_at=utc_now_iso(),
                latency_ms=latency_ms,
                status=status,
                request_id=fetch.provider_request_id,
                fetch=fetch,
                captured_http=captured_http,
                raw_ref=raw_ref,
                error_code=error_code,
                error_message=str(exc),
            )
            if http_error is not None:
                return self._evidence_failed_result(
                    request=request,
                    spec=spec,
                    adapter=adapter,
                    started_at=started_at,
                    request_id=fetch.provider_request_id,
                    raw_ref=raw_ref,
                    normalized_ref=None,
                    latency_ms=latency_ms,
                    error=http_error,
                )
            return self._result(
                request=request,
                spec=spec,
                adapter=adapter,
                started_at=started_at,
                status=status,
                freshness=FreshnessStatus.NOT_FETCHED,
                rows=(),
                row_count=0,
                raw_ref=raw_ref,
                normalized_ref=None,
                request_id=fetch.provider_request_id,
                error_code=error_code,
                error_message=str(exc),
                latency_ms=latency_ms,
            )

        normalized_for_write = replace(normalized, source_raw_ref=raw_ref)

        try:
            normalized_ref = self.normalized_store.write(
                request=request,
                spec=spec,
                normalized=normalized_for_write,
            )
        except Exception as exc:  # noqa: BLE001
            latency_ms = _elapsed_ms(t0)
            self._write_http_evidence(
                request=request,
                spec=spec,
                started_at=started_at,
                finished_at=utc_now_iso(),
                latency_ms=latency_ms,
                status=normalized_for_write.status,
                request_id=fetch.provider_request_id,
                fetch=fetch,
                captured_http=captured_http,
                raw_ref=raw_ref,
                error_code=type(exc).__name__,
                error_message=str(exc),
            )
            return self._evidence_failed_result(
                request=request,
                spec=spec,
                adapter=adapter,
                started_at=started_at,
                request_id=fetch.provider_request_id,
                raw_ref=raw_ref,
                normalized_ref=None,
                latency_ms=latency_ms,
                error=self._as_gateway_error(exc),
            )

        status = normalized_for_write.status
        if fetch.is_empty and status == ProviderStatus.REMOTE_SUCCESS:
            status = ProviderStatus.EMPTY
        if status == ProviderStatus.REMOTE_SUCCESS and normalized_for_write.row_count <= 0:
            status = ProviderStatus.EMPTY
        success = status == ProviderStatus.REMOTE_SUCCESS

        if success and (not raw_ref or not normalized_ref):
            return self._evidence_failed_result(
                request=request,
                spec=spec,
                adapter=adapter,
                started_at=started_at,
                request_id=fetch.provider_request_id,
                raw_ref=raw_ref,
                normalized_ref=normalized_ref,
                latency_ms=_elapsed_ms(t0),
                error=DataGatewayError(
                    DataGatewayErrorCode.EVIDENCE_WRITE_FAILED,
                    "remote_success requires raw_ref and normalized_ref",
                ),
            )

        latency_ms = _elapsed_ms(t0)
        http_error = self._write_http_evidence(
            request=request,
            spec=spec,
            started_at=started_at,
            finished_at=utc_now_iso(),
            latency_ms=latency_ms,
            status=status,
            request_id=fetch.provider_request_id,
            fetch=fetch,
            captured_http=captured_http,
            raw_ref=raw_ref,
            error_code=normalized_for_write.error_code,
            error_message=normalized_for_write.error_message,
        )
        if http_error is not None:
            return self._evidence_failed_result(
                request=request,
                spec=spec,
                adapter=adapter,
                started_at=started_at,
                request_id=fetch.provider_request_id,
                raw_ref=raw_ref,
                normalized_ref=normalized_ref,
                latency_ms=latency_ms,
                error=http_error,
            )

        return self._result(
            request=request,
            spec=spec,
            adapter=adapter,
            started_at=started_at,
            status=status,
            freshness=FreshnessStatus.FRESH_REMOTE if success else FreshnessStatus.NOT_FETCHED,
            rows=normalized_for_write.rows if success else (),
            row_count=normalized_for_write.row_count if success else 0,
            raw_ref=raw_ref,
            normalized_ref=normalized_ref,
            request_id=fetch.provider_request_id,
            error_code=normalized_for_write.error_code,
            error_message=normalized_for_write.error_message,
            latency_ms=latency_ms,
            source_metadata=_source_metadata_for_attempt(
                spec=spec,
                normalized=normalized_for_write,
                fetch=fetch,
                captured_http=captured_http,
                status=status,
            ),
        )

    def _resolve_raw_export_policy(self, spec: ProviderCallSpec) -> str:
        spec_policy = spec.raw_export_policy if spec.raw_export_policy in _ALLOWED_RAW_EXPORT_POLICIES else _DEFAULT_RAW_EXPORT_POLICY
        settings = self.provider_settings or {}
        candidates: tuple[object, ...] = (
            settings.get(spec.adapter_id),
            settings.get(spec.provider),
            settings.get(spec.license_policy_id),
        )
        for candidate in candidates:
            if not isinstance(candidate, Mapping):
                continue
            policy = candidate.get("raw_export_policy")
            if isinstance(policy, str) and policy in _ALLOWED_RAW_EXPORT_POLICIES:
                return policy
        return spec_policy

    def _result(
        self,
        *,
        request: PackRequest,
        spec: ProviderCallSpec,
        adapter: ProviderAdapter,
        started_at: str,
        status: ProviderStatus,
        freshness: FreshnessStatus,
        rows: tuple[Mapping[str, Any], ...],
        row_count: int,
        raw_ref: str | None,
        normalized_ref: str | None,
        request_id: str | None,
        error_code: str | None,
        error_message: str | None,
        latency_ms: int,
        source_metadata: Mapping[str, Any] | None = None,
    ) -> ProviderResult:
        attempt = ProviderAttempt(
            attempt_id=self._attempt_id(request, spec),
            run_id=request.run_id,
            call_id=request.call_id,
            worker_id=request.worker_id,
            pack=request.domain.value,
            provider=spec.provider,
            adapter_id=spec.adapter_id,
            adapter_kind=getattr(adapter, "adapter_kind", spec.provider_kind.value),
            provider_kind=spec.provider_kind,
            provider_config_version=spec.provider_config_version,
            endpoint=spec.endpoint,
            source_role=spec.source_role,
            started_at=started_at,
            finished_at=utc_now_iso(),
            status=status,
            required=spec.required,
            attempt_required=spec.attempt_required,
            coverage_group=spec.coverage_group,
            coverage_quorum=spec.coverage_quorum,
            priority_source=spec.priority_source,
            user_preferred=spec.user_preferred,
            from_cache=False,
            cache_status=None,
            single_flight_role="none",
            shared_from_attempt_id=None,
            latency_ms=max(0, latency_ms),
            row_count=row_count,
            raw_ref=raw_ref,
            normalized_ref=normalized_ref,
            error_code=error_code,
            error_message=error_message,
            schema_id=spec.expected_schema_id,
            license_note="approved",
            source_metadata=dict(source_metadata) if source_metadata else _source_metadata_from_spec(spec=spec),
        )
        try:
            self.attempt_store.write(attempt)
        except Exception as exc:  # noqa: BLE001
            gateway_error = self._as_gateway_error(exc)
            evidence_failed_attempt = replace(
                attempt,
                status=ProviderStatus.EVIDENCE_WRITE_FAILED,
                row_count=0,
                error_code=gateway_error.code.value,
                error_message=gateway_error.root_cause,
            )
            return ProviderResult(
                spec=spec,
                status=ProviderStatus.EVIDENCE_WRITE_FAILED,
                request_id=request_id,
                requested_at=started_at,
                latency_ms=max(0, latency_ms),
                source_role=spec.source_role,
                freshness=FreshnessStatus.NOT_FETCHED,
                license_note="approved",
                raw_ref=raw_ref,
                normalized_ref=normalized_ref,
                rows=(),
                row_count=0,
                cache_receipt=None,
                attempt=evidence_failed_attempt,
                error_code=gateway_error.code.value,
                error_message=gateway_error.root_cause,
            )

        return ProviderResult(
            spec=spec,
            status=status,
            request_id=request_id,
            requested_at=started_at,
            latency_ms=max(0, latency_ms),
            source_role=spec.source_role,
            freshness=freshness,
            license_note="approved",
            raw_ref=raw_ref,
            normalized_ref=normalized_ref,
            rows=rows,
            row_count=row_count,
            cache_receipt=None,
            attempt=attempt,
            error_code=error_code,
            error_message=error_message,
        )

    def _evidence_failed_result(
        self,
        *,
        request: PackRequest,
        spec: ProviderCallSpec,
        adapter: ProviderAdapter,
        started_at: str,
        request_id: str | None,
        raw_ref: str | None,
        normalized_ref: str | None,
        latency_ms: int,
        error: DataGatewayError,
    ) -> ProviderResult:
        return self._result(
            request=request,
            spec=spec,
            adapter=adapter,
            started_at=started_at,
            status=ProviderStatus.EVIDENCE_WRITE_FAILED,
            freshness=FreshnessStatus.NOT_FETCHED,
            rows=(),
            row_count=0,
            raw_ref=raw_ref,
            normalized_ref=normalized_ref,
            request_id=request_id,
            error_code=error.code.value,
            error_message=error.root_cause,
            latency_ms=latency_ms,
        )

    @staticmethod
    def _attempt_id(request: PackRequest, spec: ProviderCallSpec) -> str:
        return f"{request.run_id}:{request.call_id}:{spec.adapter_id}:{spec.endpoint}"

    def _write_http_evidence(
        self,
        *,
        request: PackRequest,
        spec: ProviderCallSpec,
        started_at: str,
        finished_at: str,
        latency_ms: int,
        status: ProviderStatus,
        request_id: str | None,
        fetch: ProviderFetch | None,
        raw_ref: str | None,
        error_code: str | None,
        error_message: str | None,
        captured_http: tuple[CapturedHttpExchange, ...] = (),
    ) -> DataGatewayError | None:
        if self.http_evidence_store is None:
            return None
        try:
            records = _http_evidence_records(fetch=fetch, captured_http=captured_http)
            if not records:
                return None
            for index, record in enumerate(records):
                self.http_evidence_store.write(
                    OpenBBProviderHttpEvidence(
                        evidence_id=_http_evidence_id(
                            attempt_id=self._attempt_id(request, spec),
                            index=index,
                            record_count=len(records),
                        ),
                        run_id=request.run_id,
                        call_id=request.call_id,
                        worker_id=request.worker_id,
                        pack=request.domain.value,
                        provider=spec.provider,
                        adapter_id=spec.adapter_id,
                        provider_kind=spec.provider_kind,
                        provider_config_version=spec.provider_config_version,
                        endpoint=spec.endpoint,
                        source_role=spec.source_role,
                        requested_at=started_at,
                        finished_at=finished_at,
                        http_method=record.http_method,
                        source_url=record.source_url,
                        response_status_code=record.response_status_code,
                        response_headers_summary=record.response_headers_summary,
                        provider_request_id=request_id,
                        latency_ms=max(0, latency_ms),
                        status=status,
                        error_code=error_code,
                        error_message=error_message,
                        raw_ref=raw_ref,
                    )
                )
        except Exception as exc:  # noqa: BLE001
            return self._as_gateway_error(exc)
        return None

    @staticmethod
    def _as_gateway_error(exc: Exception) -> DataGatewayError:
        if isinstance(exc, DataGatewayError):
            return exc
        return DataGatewayError(
            DataGatewayErrorCode.EVIDENCE_WRITE_FAILED,
            f"provider evidence write failed: {exc}",
        )


def _elapsed_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


@dataclass(frozen=True)
class _HttpEvidenceRecord:
    http_method: str
    source_url: str | None
    response_status_code: int | None
    response_headers_summary: Mapping[str, str]


def _merge_captured_http(*, fetch: ProviderFetch, exchanges: tuple[CapturedHttpExchange, ...]) -> ProviderFetch:
    if not exchanges:
        return fetch
    last = exchanges[-1]
    return replace(
        fetch,
        http_method=fetch.http_method or last.method,
        source_url=last.url or fetch.source_url,
        response_status_code=fetch.response_status_code
        if fetch.response_status_code is not None
        else last.response_status_code,
        response_headers_summary=dict(fetch.response_headers_summary or last.response_headers_summary),
    )


def _http_evidence_records(
    *,
    fetch: ProviderFetch | None,
    captured_http: tuple[CapturedHttpExchange, ...],
) -> tuple[_HttpEvidenceRecord, ...]:
    if captured_http:
        return tuple(
            _HttpEvidenceRecord(
                http_method=exchange.method or "GET",
                source_url=exchange.url,
                response_status_code=exchange.response_status_code,
                response_headers_summary=dict(exchange.response_headers_summary),
            )
            for exchange in captured_http
        )
    if fetch is None:
        return ()
    if not has_visible_http_exchange(
        source_url=fetch.source_url,
        response_status_code=fetch.response_status_code,
        response_headers=fetch.response_headers_summary,
    ) and not _is_explicit_http_failure_without_headers(fetch=fetch):
        return ()
    return (
        _HttpEvidenceRecord(
            http_method=fetch.http_method,
            source_url=fetch.source_url,
            response_status_code=fetch.response_status_code,
            response_headers_summary=dict(fetch.response_headers_summary or {}),
        ),
    )


def _http_evidence_id(*, attempt_id: str, index: int, record_count: int) -> str:
    if record_count == 1:
        return f"{attempt_id}:http"
    return f"{attempt_id}:http:{index + 1:02d}"


def _source_metadata_from_spec(*, spec: ProviderCallSpec) -> Mapping[str, Any] | None:
    metadata_keys = (
        "selection_candidate_type",
        "universe_source",
        "quote_source",
        "mixed_source_chain",
        "partial_chunk_error_count",
        "partial_chunk_errors",
        "failed_chunk_symbols_sample",
    )
    metadata = {key: spec.params.get(key) for key in metadata_keys if key in spec.params}
    return metadata or None


def _source_metadata_for_attempt(
    *,
    spec: ProviderCallSpec,
    normalized: Any,
    fetch: ProviderFetch,
    captured_http: tuple[CapturedHttpExchange, ...],
    status: ProviderStatus,
) -> Mapping[str, Any] | None:
    metadata = dict(_source_metadata_from_spec(spec=spec) or {})
    compact_facts = getattr(normalized, "compact_facts", None)
    if isinstance(compact_facts, Mapping):
        for key in ("partial_chunk_error_count", "partial_chunk_errors", "failed_chunk_symbols_sample"):
            if key in compact_facts:
                metadata[key] = compact_facts.get(key)
    if _http_evidence_expected_for_attempt(
        spec=spec,
        fetch=fetch,
        captured_http=captured_http,
        status=status,
    ):
        metadata["http_evidence_expected"] = True
    return metadata or None


def _has_visible_http_metadata(
    *,
    fetch: ProviderFetch | None,
    captured_http: tuple[CapturedHttpExchange, ...],
) -> bool:
    if captured_http:
        return True
    if fetch is None:
        return False
    return has_visible_http_exchange(
        source_url=fetch.source_url,
        response_status_code=fetch.response_status_code,
        response_headers=fetch.response_headers_summary,
    )


def _http_evidence_expected_for_attempt(
    *,
    spec: ProviderCallSpec,
    fetch: ProviderFetch,
    captured_http: tuple[CapturedHttpExchange, ...],
    status: ProviderStatus,
) -> bool:
    if status != ProviderStatus.REMOTE_SUCCESS:
        return False
    if spec.managed_http_required:
        return True
    return _has_visible_http_metadata(fetch=fetch, captured_http=captured_http)


def _status_from_http(
    *,
    fetch: ProviderFetch | None,
    captured_http: tuple[CapturedHttpExchange, ...],
) -> tuple[ProviderStatus, str | None]:
    if fetch is None and not captured_http:
        return ProviderStatus.REMOTE_SUCCESS, None
    status_code: int | None = None
    headers: Mapping[str, str] | None = None
    if fetch is not None:
        status_code = fetch.response_status_code
        headers = fetch.response_headers_summary
    if captured_http:
        last = captured_http[-1]
        if status_code is None:
            status_code = last.response_status_code
        merged = dict(normalize_headers(headers or {}))
        merged.update(normalize_headers(last.response_headers_summary))
        headers = merged
    if status_code is not None and status_code >= 500:
        return ProviderStatus.REMOTE_ERROR, None
    quota_signal = detect_quota_signal(response_status_code=status_code, response_headers=headers)
    if quota_signal is not None:
        return ProviderStatus.RATE_LIMITED, quota_signal
    return ProviderStatus.REMOTE_SUCCESS, None


_CREDENTIAL_HINTS = (
    "missing credential",
    "credential missing",
    "missing credential keys",
    "token missing",
    "api key missing",
)

_RATE_LIMIT_HINTS = (
    "rate limit",
    "rate-limit",
    "quota",
    "too many requests",
    "http 429",
    "status code 429",
)


def _status_from_fetch_exception(exc: Exception) -> tuple[ProviderStatus, str]:
    if isinstance(exc, ProviderCallSpecBindingError):
        return ProviderStatus.REMOTE_ERROR, "provider_call_spec_mismatch"
    if isinstance(exc, TimeoutError):
        return ProviderStatus.REMOTE_ERROR, "timeout"
    text = str(exc).lower()
    if any(hint in text for hint in _CREDENTIAL_HINTS):
        return ProviderStatus.CREDENTIAL_MISSING, ProviderStatus.CREDENTIAL_MISSING.value
    if any(hint in text for hint in _RATE_LIMIT_HINTS) or "429" in text:
        return ProviderStatus.RATE_LIMITED, "http_429" if "429" in text else "rate_limited"
    return ProviderStatus.REMOTE_ERROR, type(exc).__name__


def _is_explicit_http_failure_without_headers(*, fetch: ProviderFetch) -> bool:
    status_code = fetch.response_status_code
    if status_code is None:
        return False
    if status_code == 429:
        return True
    if status_code >= 500:
        return True
    return False
