from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import NON_REMOTE_ATTEMPT_STATUSES, DataGap, IngestResult
from .attempt_log import AttemptLog
from .normalized_store import NormalizedStore
from .normalizer import Normalizer
from .raw_store import RawStore


class IngestPipeline:
    def __init__(
        self,
        *,
        raw_store: RawStore,
        normalizer: Normalizer,
        normalized_store: NormalizedStore,
        attempt_log: AttemptLog,
    ) -> None:
        self.raw_store = raw_store
        self.normalizer = normalizer
        self.normalized_store = normalized_store
        self.attempt_log = attempt_log

    def ingest(self, result: Any, batch: Any) -> IngestResult:
        dataset_refs: tuple[str, ...] = ()
        raw_refs: tuple[str, ...] = ()
        gaps: list[DataGap] = []

        try:
            if _fetch_status(result) == "success":
                raw_refs = self.raw_store.save(result, batch)
                normalized = self.normalizer.normalize(result, batch, raw_refs)
                gaps.extend(normalized.gaps)
                if _normalized_storage_allowed(batch):
                    if normalized.rows:
                        dataset_refs = self.normalized_store.upsert(normalized.rows)
                else:
                    gaps.append(
                        DataGap.by_reason(
                            "license_blocked",
                            request_id=_first_request_id(batch),
                            market=_market_for_gap(batch),
                            data_type=str(getattr(batch, "data_type", "unknown")),
                            granularity=str(getattr(batch, "granularity", "unknown")),
                            evidence_refs=raw_refs,
                            symbol_id=_first_symbol_id(batch),
                        )
                    )
            else:
                gaps.extend(gaps_from_fetch_result(result, batch))
        except Exception as exc:
            gaps.append(DataGap.by_reason("evidence_write_failed", message=str(exc)))
            result = _replace_fetch_status(result, "error")

        status = _fetch_status(result)
        remote_success = status == "success"
        if status in NON_REMOTE_ATTEMPT_STATUSES:
            remote_success = False
        if remote_success and gaps:
            remote_success = False
        try:
            attempt_refs = self.attempt_log.record(
                batch=batch,
                fetch_result=result,
                raw_refs=raw_refs,
                dataset_refs=dataset_refs,
                gaps=tuple(gaps),
                remote_success=remote_success,
            )
        except Exception:
            return IngestResult.failed("evidence_write_failed")

        if remote_success and not attempt_refs:
            return IngestResult.failed("evidence_write_failed")
        if any(g.reason == "evidence_write_failed" for g in gaps):
            return IngestResult.failed("evidence_write_failed", attempt_refs=attempt_refs)
        return IngestResult.from_refs(
            batch_id=str(getattr(batch, "batch_id", "batch:unknown")),
            dataset_refs=dataset_refs,
            raw_refs=raw_refs,
            attempt_refs=attempt_refs,
            gaps=tuple(gaps),
            remote_success=remote_success,
            cache_key=getattr(batch, "cache_key", None),
        )

    def ingest_seed(self, seed_input: Any, context: Any) -> IngestResult:
        batch = _build_seed_batch(seed_input, context)
        payload = _seed_payload(seed_input)
        if payload is None:
            result = _SeedFetchResult(
                provider_id=batch.provider_id,
                endpoint_id=batch.endpoint_id,
                status="error",
                error_code="seed_payload_missing",
                error_message="seed payload is required",
            )
        else:
            result = _SeedFetchResult(
                provider_id=batch.provider_id,
                endpoint_id=batch.endpoint_id,
                status="success",
                payload=_inject_seed_defaults(payload, batch=batch, context=context),
            )
        return self.ingest(result, batch)

    def record_gate_result(self, batch: Any, gate: Any) -> IngestResult:
        refs = _gate_refs(gate)
        if gate.kind == "cache_hit":
            gaps: tuple[DataGap, ...] = ()
        elif gate.kind == "cached_empty":
            gaps = (DataGap.by_reason("cached_empty", evidence_refs=tuple(refs.attempt_refs)),)
        elif gate.kind == "rate_limited":
            gaps = (DataGap.by_reason("rate_limited", evidence_refs=_rate_limit_evidence_refs(batch, gate, refs)),)
        elif gate.kind == "cooldown_skipped":
            gaps = (DataGap.by_reason("cooldown_skipped", evidence_refs=_rate_limit_evidence_refs(batch, gate, refs)),)
        elif gate.kind == "shared_result":
            gaps = ()
        else:
            raise ValueError(f"unsupported non-remote gate kind: {gate.kind}")

        try:
            attempt_refs = self.attempt_log.record(
                batch=batch,
                gate=gate,
                raw_refs=tuple(refs.raw_refs),
                dataset_refs=tuple(refs.dataset_refs),
                gaps=gaps,
                remote_success=False,
            )
        except Exception:
            return IngestResult.failed("evidence_write_failed")

        return IngestResult(
            batch_id=str(getattr(batch, "batch_id", "batch:unknown")),
            status="non_remote_recorded",
            dataset_refs=tuple(refs.dataset_refs),
            raw_refs=tuple(refs.raw_refs),
            attempt_refs=attempt_refs,
            gaps=gaps,
            remote_success=False,
            cache_key=getattr(batch, "cache_key", None),
        )


def gaps_from_fetch_result(result: Any, batch: Any) -> tuple[DataGap, ...]:
    status = _fetch_status(result)
    if status == "credential_missing":
        return (_batch_gap(batch, "credential_missing"),)
    if status == "rate_limited":
        evidence_refs = tuple(getattr(obs, "request_key", "") for obs in getattr(result, "http_observations", ()) if getattr(obs, "request_key", ""))
        if not evidence_refs:
            evidence_refs = _derived_rate_limit_evidence_refs(batch)
        return (_batch_gap(batch, "rate_limited", evidence_refs=evidence_refs),)
    if status == "empty":
        return (_batch_gap(batch, "empty_result"),)
    if status == "sdk_http_unknown":
        return (_batch_gap(batch, "sdk_http_unknown"), _batch_gap(batch, "provider_error", message="sdk_http_unknown"))
    if status == "not_applicable":
        return (_batch_gap(batch, "not_applicable"),)
    if status == "error":
        reason = getattr(result, "error_message", None) or getattr(result, "error_code", None) or "provider_error"
        return (_batch_gap(batch, "provider_error", message=str(reason)),)
    return ()


def _batch_gap(
    batch: Any,
    reason: str,
    *,
    evidence_refs: tuple[str, ...] = (),
    message: str | None = None,
) -> DataGap:
    return DataGap.by_reason(
        reason,
        request_id=_first_request_id(batch),
        market=_market_for_gap(batch),
        data_type=str(getattr(batch, "data_type", "unknown")),
        granularity=str(getattr(batch, "granularity", "unknown")),
        evidence_refs=evidence_refs,
        message=message,
        symbol_id=_first_symbol_id(batch),
    )


def _replace_fetch_status(result: Any, status: str) -> Any:
    class _ResultProxy:
        def __init__(self, source: Any, next_status: str) -> None:
            self._source = source
            self.status = next_status

        def __getattr__(self, item: str) -> Any:
            return getattr(self._source, item)

    return _ResultProxy(result, status)


def _fetch_status(result: Any) -> str:
    raw = getattr(result, "status", "")
    return str(getattr(raw, "value", raw))


class _GateRefs:
    def __init__(
        self,
        *,
        dataset_refs: tuple[str, ...] = (),
        raw_refs: tuple[str, ...] = (),
        attempt_refs: tuple[str, ...] = (),
    ) -> None:
        self.dataset_refs = dataset_refs
        self.raw_refs = raw_refs
        self.attempt_refs = attempt_refs


def _gate_refs(gate: Any) -> _GateRefs:
    refs = getattr(gate, "refs", None)
    if refs is not None:
        return _GateRefs(
            dataset_refs=tuple(getattr(refs, "dataset_refs", ()) or ()),
            raw_refs=tuple(getattr(refs, "raw_refs", ()) or ()),
            attempt_refs=tuple(getattr(refs, "attempt_refs", ()) or ()),
        )
    evidence_refs = tuple(getattr(gate, "evidence_refs", ()) or ())
    return _GateRefs(attempt_refs=evidence_refs)


def _rate_limit_evidence_refs(batch: Any, gate: Any, refs: _GateRefs) -> tuple[str, ...]:
    if refs.attempt_refs:
        return tuple(refs.attempt_refs)
    gate_evidence = tuple(getattr(gate, "evidence_refs", ()) or ())
    if gate_evidence:
        return gate_evidence
    return _derived_rate_limit_evidence_refs(batch)


def _derived_rate_limit_evidence_refs(batch: Any) -> tuple[str, ...]:
    rate_limit_key = getattr(batch, "rate_limit_key", None)
    if rate_limit_key:
        return (f"rate_limit:{rate_limit_key}",)
    provider_id = getattr(batch, "provider_id", "unknown_provider")
    endpoint_id = getattr(batch, "endpoint_id", "unknown_endpoint")
    return (f"rate_limit:{provider_id}:{endpoint_id}",)


def _normalized_storage_allowed(batch: Any) -> bool:
    license_policy = getattr(batch, "license_policy", None)
    if isinstance(license_policy, dict):
        value = license_policy.get("normalized_storage_allowed", True)
    elif license_policy is not None:
        value = getattr(license_policy, "normalized_storage_allowed", True)
    else:
        value = True
    return bool(value)


def _first_request_id(batch: Any) -> str:
    request_ids = tuple(getattr(batch, "request_ids", ()) or ())
    if request_ids:
        return str(request_ids[0])
    return str(getattr(batch, "request_id", "ingest"))


def _first_symbol_id(batch: Any) -> str | None:
    symbol_ids = tuple(getattr(batch, "symbol_ids", ()) or ())
    if symbol_ids:
        return str(symbol_ids[0])
    value = getattr(batch, "symbol_id", None)
    return None if value is None else str(value)


def _market_for_gap(batch: Any) -> Any:
    from claw_trade.data_gateway.models import Market

    raw = getattr(batch, "market", None)
    if isinstance(raw, Market):
        return raw
    try:
        return Market(str(raw))
    except ValueError:
        return Market.CN_A


@dataclass(frozen=True)
class _SeedBatch:
    provider_id: str
    endpoint_id: str
    market: str
    data_type: str
    fields_union: tuple[str, ...]
    required_fields: tuple[str, ...]


@dataclass(frozen=True)
class _SeedFetchResult:
    provider_id: str
    endpoint_id: str
    status: str
    payload: Any = None
    error_code: str | None = None
    error_message: str | None = None
    http_observations: tuple[Any, ...] = ()


def _build_seed_batch(seed_input: Any, context: Any) -> _SeedBatch:
    market = _seed_value(seed_input, "market", getattr(context, "market", "UNKNOWN"))
    data_type = _seed_value(seed_input, "data_type", getattr(context, "dataset_scope", "unknown_dataset"))
    required_fields = _seed_fields(seed_input)
    return _SeedBatch(
        provider_id=str(_seed_value(seed_input, "provider_id", "maintenance_seed")),
        endpoint_id=str(_seed_value(seed_input, "endpoint_id", "seed_import")),
        market=str(market),
        data_type=str(data_type),
        fields_union=required_fields,
        required_fields=required_fields,
    )


def _seed_payload(seed_input: Any) -> Any:
    payload = _seed_value(seed_input, "payload", None)
    if payload is not None:
        return payload
    rows = _seed_value(seed_input, "rows", None)
    return rows


def _seed_fields(seed_input: Any) -> tuple[str, ...]:
    raw = _seed_value(seed_input, "required_fields", ())
    if raw is None:
        return ()
    if isinstance(raw, tuple):
        return tuple(str(item) for item in raw)
    if isinstance(raw, list):
        return tuple(str(item) for item in raw)
    return (str(raw),)


def _seed_value(seed_input: Any, key: str, default: Any) -> Any:
    if isinstance(seed_input, dict):
        return seed_input.get(key, default)
    return getattr(seed_input, key, default)


def _inject_seed_defaults(payload: Any, *, batch: _SeedBatch, context: Any) -> Any:
    dataset_name = getattr(context, "dataset_scope", batch.data_type)
    if isinstance(payload, dict):
        return _inject_seed_defaults_into_row(payload, dataset_name=dataset_name)
    if isinstance(payload, list):
        mapped: list[Any] = []
        for item in payload:
            if isinstance(item, dict):
                mapped.append(_inject_seed_defaults_into_row(item, dataset_name=dataset_name))
            else:
                mapped.append(item)
        return mapped
    return payload


def _inject_seed_defaults_into_row(row: dict[str, Any], *, dataset_name: str) -> dict[str, Any]:
    mapped = dict(row)
    mapped.setdefault("dataset", dataset_name)
    return mapped
