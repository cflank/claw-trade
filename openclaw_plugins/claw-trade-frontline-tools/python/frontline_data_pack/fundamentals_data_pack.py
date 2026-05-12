from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
import time
from typing import Any, Mapping

from .cache import (
    build_provider_cache_document_id,
    inspect_provider_cache,
    insert_provider_attempt,
    upsert_provider_cache,
)
from .config import FrontlineProviderConfig, load_frontline_provider_config
from .errors import TOOL_CONTEXT_INCOMPLETE, TOOL_PARAMS_INVALID, TOOL_WORKER_MISMATCH, FrontlineValidationError
from .evidence import OpenVikingEvidenceClient
from .evidence_writer import L2WriteSessionState, commit_l2_write_session, write_pack_evidence, write_provider_attempts, write_raw_payload
from .fundamental_mapper import compute_missing_core_fields, map_fundamental_fields
from .models import (
    BriefInput,
    FieldSource,
    FundamentalDomainData,
    FundamentalField,
    FundamentalToolInput,
    PackInput,
    ProviderAttempt,
    ProviderCacheDocument,
    ProviderCacheKey,
    ProviderResult,
    stable_json_bytes,
    to_jsonable,
)
from .mongo_store import (
    COLLECTION_NORMALIZED_FUNDAMENTAL_FIELDS,
    COLLECTION_PROVIDER_ATTEMPTS,
    COLLECTION_PROVIDER_CACHE,
    resolve_optional_mongo_database,
)
from .normalized_store import upsert_fundamental_fields
from .observability import (
    emit_structured_log,
    record_fundamental_conflict_total,
    record_fundamental_field_mapped_total,
    record_fundamental_missing_core_total,
    record_mongo_cache_inspect,
    record_mongo_latency,
    record_mongo_upsert,
    record_pack_build,
    record_span,
)
from .pack_builder import build_pack_envelope
from .provider_executor import ProviderCallable, execute_provider_plan
from .provider_plan import build_provider_plan, build_provider_query
from .providers_akshare_fundamental import AKSHARE_FUNDAMENTAL_CALL_REGISTRY
from .providers_tushare_fundamental import TUSHARE_FUNDAMENTAL_CALL_REGISTRY
from .quality_fundamental import compute_fundamental_quality
from .reader_brief import build_reader_brief
from .runtime_context import ToolRuntimeContext
from .security import normalize_ticker, validate_date, validate_date_range, validate_market, validate_runtime_context


_FUNDAMENTAL_SCHEMA_VERSION = "cn_a_fundamental_pack.v1"
_FUNDAMENTAL_TOOL_NAME = "fundamental_fundamentals_data_pack"
_FUNDAMENTAL_WORKER_ID = "fundamental_analyst"
_DEFAULT_WINDOW_DAYS = 90
_NORMALIZED_TTL_DAYS_VALUATION = 7
_NORMALIZED_TTL_DAYS_FINANCIAL = 90
_DEFAULT_FUNDAMENTAL_CALL_REGISTRY = {
    **AKSHARE_FUNDAMENTAL_CALL_REGISTRY,
    **TUSHARE_FUNDAMENTAL_CALL_REGISTRY,
}


@dataclass
class BuildFundamentalDataPack:
    config: FrontlineProviderConfig | None = None
    provider_call_registry: Mapping[tuple[str, str], ProviderCallable] | None = None
    evidence_client: OpenVikingEvidenceClient | None = None
    provider_cache_collection: Any | None = None
    provider_attempts_collection: Any | None = None
    normalized_fundamental_collection: Any | None = None

    def build(
        self,
        tool_input: FundamentalToolInput | Mapping[str, Any],
        runtime_context: ToolRuntimeContext | Mapping[str, Any],
    ):
        build_started = time.perf_counter()
        context = _coerce_runtime_context(runtime_context)
        _validate_fundamental_context(context)
        normalized_input = _coerce_fundamental_tool_input(tool_input)

        ticker = normalize_ticker(normalized_input.ticker)
        market = validate_market(normalized_input.market)
        start_date, end_date = _normalize_fundamental_date_window(
            start_date=normalized_input.start_date,
            end_date=normalized_input.end_date,
            context=context,
        )
        query = build_provider_query(
            market=market,
            ticker=ticker,
            company_name=normalized_input.company_name,
            industry=None,
            start_date=start_date,
            end_date=end_date,
            adjust=None,
        )

        config = self.config or load_frontline_provider_config()
        cache_collection, attempts_collection, normalized_collection = self._resolve_collections(config)
        plan = build_provider_plan(
            domain="fundamental",
            query=query,
            config=config,
            cache_inspection=[],
        )
        cache_attempts, cache_refs = _inspect_fundamental_cache(
            plan=plan,
            query=query,
            now_utc=context.current_time,
            collection=cache_collection,
            cache_required=config.mongodb.cache_required,
        )

        call_registry = self.provider_call_registry or _DEFAULT_FUNDAMENTAL_CALL_REGISTRY
        provider_results = execute_provider_plan(
            plan,
            query,
            context,
            max_concurrency=config.provider_runtime.max_concurrency,
            total_timeout_ms=config.provider_runtime.total_timeout_ms,
            call_registry=call_registry,
        )

        state = L2WriteSessionState(defer_writes=True)
        diagnostic_flags: list[str] = []
        raw_payload_refs = []
        updated_results: list[ProviderResult] = []
        for index, result in enumerate(provider_results, start=1):
            if result.raw_payload is None:
                updated_results.append(result)
                continue
            write_result = write_raw_payload(
                result=result,
                context=context,
                attempt_seq=index,
                state=state,
                client=self.evidence_client,
            )
            if write_result.ok:
                updated_results.append(write_result.provider_result)
                if write_result.evidence_ref is not None:
                    raw_payload_refs.append(write_result.evidence_ref)
            else:
                updated_results.append(write_result.provider_result)
                if write_result.error is not None:
                    diagnostic_flags.append(write_result.error.diagnostic_flag)

        fields, conflict_diagnostics = map_fundamental_fields(updated_results)
        missing_core_fields = compute_missing_core_fields(fields)
        record_fundamental_field_mapped_total(count=len(fields))
        record_fundamental_missing_core_total(count=len(missing_core_fields))
        record_fundamental_conflict_total(count=len(conflict_diagnostics))
        remote_attempts = _rebuild_attempts_with_accepted_counts(
            attempts=[result.attempt for result in updated_results],
            mapped_fields=fields,
        )
        all_attempts = [*cache_attempts, *remote_attempts]

        attempts_write_result = write_provider_attempts(
            context=context,
            attempts=all_attempts,
            state=state,
            client=self.evidence_client,
        )
        l2_refs = list(raw_payload_refs)
        if attempts_write_result.ok:
            if attempts_write_result.evidence_ref is not None:
                l2_refs.append(attempts_write_result.evidence_ref)
        elif attempts_write_result.error is not None:
            diagnostic_flags.append(attempts_write_result.error.diagnostic_flag)

        quality_started = time.perf_counter()
        quality = compute_fundamental_quality(
            fields,
            missing_core_fields,
            all_attempts,
            as_of_date=end_date,
            diagnostic_flags=diagnostic_flags,
        )
        quality_elapsed_ms = max(0, int((time.perf_counter() - quality_started) * 1000))
        record_span(
            "pack.quality",
            status=quality.status,
            elapsed_ms=quality_elapsed_ms,
            fields={
                "run_id": context.run_id,
                "call_id": context.call_id,
                "worker_id": context.worker_id,
                "tool_name": context.tool_name,
                "domain": "fundamental",
                "status": quality.status,
                "elapsed_ms": quality_elapsed_ms,
            },
        )
        emit_structured_log(
            {
                "run_id": context.run_id,
                "dispatch_id": context.dispatch_id,
                "call_id": context.call_id,
                "stage": context.stage,
                "worker_id": context.worker_id,
                "tool_name": context.tool_name,
                "domain": "fundamental",
                "ticker": ticker,
                "provider": None,
                "endpoint": None,
                "role": None,
                "attempt_status": None,
                "quality_status": quality.status,
                "status": quality.status,
                "elapsed_ms": quality_elapsed_ms,
                "timeout_ms": None,
                "raw_count": None,
                "accepted_count": len(fields),
                "error_code": None,
                "evidence_kind": None,
                "l2_ref_present": bool(l2_refs),
                "mongo_ref_present": bool(cache_refs),
            }
        )

        field_sources = _build_field_sources(fields=fields, observed_at=context.current_time)
        company_profile_fields, valuation_fields, financial_fields, cash_flow_fields = _split_field_groups(fields)
        missing_items = list(missing_core_fields)
        if not company_profile_fields:
            missing_items.append("company_profile")

        brief = build_reader_brief(
            BriefInput(
                domain="fundamental",
                input=PackInput(
                    ticker=ticker,
                    market="CN_A",
                    company_name=normalized_input.company_name,
                    industry=None,
                    start_date=start_date,
                    end_date=end_date,
                ),
                quality=quality,
                provider_attempts=all_attempts,
                accepted_counts={
                    "company_profile": len(company_profile_fields),
                    "valuation": len(valuation_fields),
                    "financial": len(financial_fields),
                    "cash_flow": len(cash_flow_fields),
                },
                missing_items=missing_items,
                conflict_diagnostics=conflict_diagnostics,
                evidence_summary=_build_fundamental_material_summary(
                    company_profile_fields=company_profile_fields,
                    valuation_fields=valuation_fields,
                    financial_fields=financial_fields,
                    cash_flow_fields=cash_flow_fields,
                ),
            )
        )

        now_dt = _normalize_context_time(context.current_time)
        fetched_at = now_dt.isoformat()
        valuation_expires_at = (now_dt + timedelta(days=_NORMALIZED_TTL_DAYS_VALUATION)).isoformat()
        financial_expires_at = (now_dt + timedelta(days=_NORMALIZED_TTL_DAYS_FINANCIAL)).isoformat()
        normalized_refs: list[str] = []
        valuation_upsert = upsert_fundamental_fields(
            ticker=ticker,
            fields=valuation_fields.values(),
            fetched_at=fetched_at,
            expires_at=valuation_expires_at,
            collection=normalized_collection,
        )
        diagnostic_flags.extend(valuation_upsert.diagnostic_flags)
        normalized_refs.extend(valuation_upsert.refs)
        record_mongo_upsert(
            kind="fundamental_fields_valuation",
            status="failure" if valuation_upsert.write_failed else "success",
            count=max(1, valuation_upsert.attempted_count),
        )

        remaining_fields = {**company_profile_fields, **financial_fields, **cash_flow_fields}
        remaining_upsert = upsert_fundamental_fields(
            ticker=ticker,
            fields=remaining_fields.values(),
            fetched_at=fetched_at,
            expires_at=financial_expires_at,
            collection=normalized_collection,
        )
        diagnostic_flags.extend(remaining_upsert.diagnostic_flags)
        normalized_refs.extend(remaining_upsert.refs)
        record_mongo_upsert(
            kind="fundamental_fields_remaining",
            status="failure" if remaining_upsert.write_failed else "success",
            count=max(1, remaining_upsert.attempted_count),
        )

        cache_refs.extend(normalized_refs)
        cache_refs.extend(
            _upsert_provider_cache_documents(
                provider_results=updated_results,
                query=query,
                fetched_at=fetched_at,
                expires_at=valuation_expires_at,
                collection=cache_collection,
                diagnostic_flags=diagnostic_flags,
            )
        )
        _insert_provider_attempt_rows(
            attempts=all_attempts,
            context=context,
            ticker=ticker,
            collection=attempts_collection,
            diagnostic_flags=diagnostic_flags,
        )

        domain_data = FundamentalDomainData(
            schema_version=_FUNDAMENTAL_SCHEMA_VERSION,
            company_profile=company_profile_fields,
            valuation_fields=valuation_fields,
            financial_fields=financial_fields,
            cash_flow_fields=cash_flow_fields,
            missing_core_fields=missing_core_fields,
            conflict_diagnostics=conflict_diagnostics,
        )
        pack = build_pack_envelope(
            domain="fundamental",
            context=context,
            input=PackInput(
                ticker=ticker,
                market="CN_A",
                company_name=normalized_input.company_name,
                industry=None,
                start_date=start_date,
                end_date=end_date,
            ),
            quality=quality,
            provider_attempts=all_attempts,
            field_sources=field_sources,
            raw_payload_refs=raw_payload_refs,
            mongo_cache_refs=_deduplicate_text_refs(cache_refs),
            openviking_l2_refs=l2_refs,
            diagnostic_flags=diagnostic_flags,
            reader_brief=brief,
            domain_data=to_jsonable(domain_data),
        )

        pack_write_result = write_pack_evidence(
            pack=pack,
            context=context,
            state=state,
            client=self.evidence_client,
        )
        if pack_write_result.ok and pack_write_result.evidence_ref is not None:
            final_pack = replace(pack, openviking_l2_refs=[*pack.openviking_l2_refs, pack_write_result.evidence_ref])
        elif pack_write_result.error is None:
            final_pack = pack
        else:
            final_flags = [*diagnostic_flags, pack_write_result.error.diagnostic_flag]
            final_quality_started = time.perf_counter()
            final_quality = compute_fundamental_quality(
                fields,
                missing_core_fields,
                all_attempts,
                as_of_date=end_date,
                diagnostic_flags=final_flags,
            )
            final_quality_elapsed_ms = max(0, int((time.perf_counter() - final_quality_started) * 1000))
            record_span(
                "pack.quality",
                status=final_quality.status,
                elapsed_ms=final_quality_elapsed_ms,
                fields={
                    "run_id": context.run_id,
                    "call_id": context.call_id,
                    "worker_id": context.worker_id,
                    "tool_name": context.tool_name,
                    "domain": "fundamental",
                    "status": final_quality.status,
                    "elapsed_ms": final_quality_elapsed_ms,
                },
            )
            emit_structured_log(
                {
                    "run_id": context.run_id,
                    "dispatch_id": context.dispatch_id,
                    "call_id": context.call_id,
                    "stage": context.stage,
                    "worker_id": context.worker_id,
                    "tool_name": context.tool_name,
                    "domain": "fundamental",
                    "ticker": ticker,
                    "provider": None,
                    "endpoint": None,
                    "role": None,
                    "attempt_status": None,
                    "quality_status": final_quality.status,
                    "status": final_quality.status,
                    "elapsed_ms": final_quality_elapsed_ms,
                    "timeout_ms": None,
                    "raw_count": None,
                    "accepted_count": len(fields),
                    "error_code": None,
                    "evidence_kind": None,
                    "l2_ref_present": bool(l2_refs),
                    "mongo_ref_present": bool(cache_refs),
                }
            )
            final_pack = build_pack_envelope(
                domain="fundamental",
                context=context,
                input=pack.input,
                quality=final_quality,
                provider_attempts=pack.provider_attempts,
                field_sources=pack.field_sources,
                raw_payload_refs=pack.raw_payload_refs,
                mongo_cache_refs=pack.mongo_cache_refs,
                openviking_l2_refs=pack.openviking_l2_refs,
                diagnostic_flags=final_flags,
                reader_brief=pack.reader_brief,
                domain_data=pack.domain_data,
            )

        commit_result = commit_l2_write_session(state=state, client=self.evidence_client)
        if not commit_result.ok and commit_result.error is not None:
            raise FrontlineValidationError(commit_result.error.code, commit_result.error.message)

        build_elapsed_ms = max(0, int((time.perf_counter() - build_started) * 1000))
        record_pack_build(domain="fundamental", status=final_pack.quality.status, elapsed_ms=build_elapsed_ms)
        record_span(
            "frontline_pack.build",
            status=final_pack.quality.status,
            elapsed_ms=build_elapsed_ms,
            fields={
                "run_id": context.run_id,
                "call_id": context.call_id,
                "worker_id": context.worker_id,
                "tool_name": context.tool_name,
                "domain": "fundamental",
                "status": final_pack.quality.status,
                "elapsed_ms": build_elapsed_ms,
            },
        )
        return final_pack

    def _resolve_collections(self, config: FrontlineProviderConfig) -> tuple[Any | None, Any | None, Any | None]:
        cache_collection = self.provider_cache_collection
        attempts_collection = self.provider_attempts_collection
        normalized_collection = self.normalized_fundamental_collection
        if cache_collection is not None and attempts_collection is not None and normalized_collection is not None:
            return cache_collection, attempts_collection, normalized_collection

        database = resolve_optional_mongo_database(
            config.mongodb.uri,
            cache_required=config.mongodb.cache_required,
        )
        return (
            cache_collection if cache_collection is not None else (
                database[COLLECTION_PROVIDER_CACHE] if database is not None else None
            ),
            attempts_collection if attempts_collection is not None else (
                database[COLLECTION_PROVIDER_ATTEMPTS] if database is not None else None
            ),
            normalized_collection
            if normalized_collection is not None
            else (database[COLLECTION_NORMALIZED_FUNDAMENTAL_FIELDS] if database is not None else None),
        )


def run_fundamentals_data_pack(
    tool_input: FundamentalToolInput | Mapping[str, Any],
    runtime_context: ToolRuntimeContext | Mapping[str, Any],
):
    started = time.perf_counter()
    status = "success"
    context: ToolRuntimeContext | None = None
    try:
        context = _coerce_runtime_context(runtime_context)
        request = _coerce_fundamental_tool_input(tool_input)
        return BuildFundamentalDataPack().build(request, context)
    except Exception:
        status = "error"
        raise
    finally:
        elapsed_ms = max(0, int((time.perf_counter() - started) * 1000))
        record_span(
            "frontline_tool.execute",
            status=status,
            elapsed_ms=elapsed_ms,
            fields={
                "run_id": context.run_id if context else None,
                "call_id": context.call_id if context else None,
                "worker_id": context.worker_id if context else None,
                "tool_name": context.tool_name if context else None,
                "domain": "fundamental",
                "status": status,
                "elapsed_ms": elapsed_ms,
            },
        )
        record_span(
            "frontline_tool.python_subprocess",
            status=status,
            elapsed_ms=elapsed_ms,
            fields={
                "run_id": context.run_id if context else None,
                "call_id": context.call_id if context else None,
                "worker_id": context.worker_id if context else None,
                "tool_name": context.tool_name if context else None,
                "domain": "fundamental",
                "status": status,
                "elapsed_ms": elapsed_ms,
            },
        )


def _coerce_runtime_context(runtime_context: ToolRuntimeContext | Mapping[str, Any]) -> ToolRuntimeContext:
    if isinstance(runtime_context, ToolRuntimeContext):
        return runtime_context
    _validate_runtime_context_market(runtime_context)
    return validate_runtime_context(runtime_context)


def _validate_fundamental_context(context: ToolRuntimeContext) -> None:
    if context.stage != "frontline":
        raise FrontlineValidationError(TOOL_WORKER_MISMATCH, "runtime_context.stage 必须为 frontline")
    if context.worker_id != _FUNDAMENTAL_WORKER_ID:
        raise FrontlineValidationError(
            TOOL_WORKER_MISMATCH,
            f"runtime_context.worker_id 必须为 {_FUNDAMENTAL_WORKER_ID}",
        )
    if context.tool_name != _FUNDAMENTAL_TOOL_NAME:
        raise FrontlineValidationError(
            TOOL_WORKER_MISMATCH,
            f"runtime_context.tool_name 必须为 {_FUNDAMENTAL_TOOL_NAME}",
        )


def _validate_runtime_context_market(runtime_context: Mapping[str, Any]) -> None:
    if "market" not in runtime_context:
        return
    raw_market = runtime_context.get("market")
    if not isinstance(raw_market, str) or not raw_market.strip():
        raise FrontlineValidationError(
            TOOL_CONTEXT_INCOMPLETE,
            "runtime_context.market 必须是非空字符串",
        )
    if raw_market.strip().upper() != "CN_A":
        raise FrontlineValidationError(
            TOOL_WORKER_MISMATCH,
            "runtime_context.market 必须为 CN_A",
        )


def _coerce_fundamental_tool_input(tool_input: FundamentalToolInput | Mapping[str, Any]) -> FundamentalToolInput:
    if isinstance(tool_input, FundamentalToolInput):
        return tool_input
    if not isinstance(tool_input, Mapping):
        raise FrontlineValidationError(TOOL_PARAMS_INVALID, "tool_input 必须是 object")
    try:
        return FundamentalToolInput(
            ticker=str(tool_input.get("ticker", "")),
            market=str(tool_input.get("market", "")),  # type: ignore[arg-type]
            company_name=_optional_string(tool_input.get("company_name")),
            start_date=_optional_string(tool_input.get("start_date")),
            end_date=_optional_string(tool_input.get("end_date")),
        )
    except Exception as exc:  # noqa: BLE001
        raise FrontlineValidationError(TOOL_PARAMS_INVALID, f"tool_input 无法解析: {exc}") from exc


def _normalize_fundamental_date_window(
    *,
    start_date: str | None,
    end_date: str | None,
    context: ToolRuntimeContext,
) -> tuple[str, str]:
    context_now = _normalize_context_time(context.current_time)
    if end_date is None:
        end = context_now.date().isoformat()
    else:
        end = validate_date(end_date, field_name="end_date")
    if start_date is None:
        start_day = datetime.strptime(end, "%Y-%m-%d").date() - timedelta(days=_DEFAULT_WINDOW_DAYS - 1)
        start = start_day.isoformat()
    else:
        start = validate_date(start_date, field_name="start_date")
    return validate_date_range(start, end)


def _inspect_fundamental_cache(
    *,
    plan: list[Any],
    query: Any,
    now_utc: str,
    collection: Any | None,
    cache_required: bool,
) -> tuple[list[ProviderAttempt], list[str]]:
    attempts: list[ProviderAttempt] = []
    refs: list[str] = []
    for index, spec in enumerate(plan, start=1):
        key = ProviderCacheKey(
            market="CN_A",
            domain="fundamental",
            ticker=query.ticker,
            provider=spec.provider,
            endpoint=spec.endpoint,
            query_fingerprint=query.query_fingerprint,
            schema_version=_FUNDAMENTAL_SCHEMA_VERSION,
        )
        started = time.perf_counter()
        inspection = inspect_provider_cache(
            key,
            now_utc=now_utc,
            collection=collection,
            cache_required=cache_required,
        )
        inspect_elapsed_ms = max(0, int((time.perf_counter() - started) * 1000))
        record_mongo_cache_inspect(status=inspection.status)
        record_mongo_latency(operation="cache_inspect", elapsed_ms=inspect_elapsed_ms)
        record_span(
            "mongo.inspect",
            status=inspection.status,
            elapsed_ms=inspect_elapsed_ms,
            fields={
                "domain": "fundamental",
                "provider": spec.provider,
                "endpoint": spec.endpoint,
                "status": inspection.status,
                "elapsed_ms": inspect_elapsed_ms,
            },
        )
        if inspection.cache_ref is not None:
            refs.append(inspection.cache_ref)
        started_at = (_normalize_context_time(now_utc) + timedelta(microseconds=index)).isoformat()
        attempts.append(
            ProviderAttempt(
                provider=spec.provider,
                endpoint=spec.endpoint,
                role=f"{spec.role}_cache_inspect",
                status=inspection.status,
                started_at=started_at,
                finished_at=started_at,
                elapsed_ms=0,
                timeout_ms=1,
                query_fingerprint=query.query_fingerprint,
                raw_count=0,
                accepted_count=0,
                payload_hash=None,
                raw_payload_ref=None,
                error_code=_extract_cache_error_code(inspection.reason),
                error_message_redacted=inspection.reason,
            )
        )
    return attempts, refs


def _extract_cache_error_code(reason: str | None) -> str | None:
    if reason is None:
        return None
    text = reason.strip()
    if not text:
        return None
    return text.split(":", 1)[0]


def _build_field_sources(
    *,
    fields: Mapping[str, FundamentalField],
    observed_at: str,
) -> dict[str, FieldSource]:
    output: dict[str, FieldSource] = {}
    for field_name, field in fields.items():
        output[field_name] = FieldSource(
            field_path=field_name,
            provider=field.provider,
            endpoint=field.endpoint,
            payload_hash=field.payload_hash,
            raw_payload_ref=field.raw_payload_ref,
            observed_at=observed_at,
            source_time=field.source_time,
        )
    return output


def _split_field_groups(
    fields: Mapping[str, FundamentalField],
) -> tuple[dict[str, FundamentalField], dict[str, FundamentalField], dict[str, FundamentalField], dict[str, FundamentalField]]:
    company_profile: dict[str, FundamentalField] = {}
    valuation_fields: dict[str, FundamentalField] = {}
    financial_fields: dict[str, FundamentalField] = {}
    cash_flow_fields: dict[str, FundamentalField] = {}
    for field_name, field in fields.items():
        if field_name.startswith("company_profile."):
            company_profile[field_name] = field
        elif field_name.startswith("valuation.") or field_name.startswith("price_context."):
            valuation_fields[field_name] = field
        elif field_name.startswith("income_statement.") or field_name.startswith("financial_indicators."):
            financial_fields[field_name] = field
        elif field_name.startswith("cash_flow."):
            cash_flow_fields[field_name] = field
    return company_profile, valuation_fields, financial_fields, cash_flow_fields


def _rebuild_attempts_with_accepted_counts(
    *,
    attempts: list[ProviderAttempt],
    mapped_fields: Mapping[str, FundamentalField],
) -> list[ProviderAttempt]:
    counts: dict[tuple[str, str], int] = {}
    for field in mapped_fields.values():
        key = (field.provider, field.endpoint)
        counts[key] = counts.get(key, 0) + 1
    rebuilt: list[ProviderAttempt] = []
    for attempt in attempts:
        accepted_count = counts.get((attempt.provider, attempt.endpoint), 0)
        rebuilt.append(replace(attempt, accepted_count=min(accepted_count, attempt.raw_count)))
    return rebuilt


def _build_fundamental_material_summary(
    *,
    company_profile_fields: dict[str, FundamentalField],
    valuation_fields: dict[str, FundamentalField],
    financial_fields: dict[str, FundamentalField],
    cash_flow_fields: dict[str, FundamentalField],
) -> list[str]:
    summary: list[str] = []
    profile_line = _format_field_group(
        "公司资料",
        company_profile_fields,
        ("company_profile.industry", "company_profile.main_business"),
    )
    if profile_line:
        summary.append(profile_line)
    valuation_line = _format_field_group(
        "估值数据",
        valuation_fields,
        ("valuation.pe_ttm", "valuation.pb", "valuation.total_mv", "price_context.close", "price_context.trade_date"),
    )
    if valuation_line:
        summary.append(valuation_line)
    financial_line = _format_field_group(
        "财务指标",
        financial_fields,
        (
            "financial_indicators.roe",
            "financial_indicators.roa",
            "financial_indicators.gross_margin",
            "financial_indicators.netprofit_margin",
            "financial_indicators.debt_to_assets",
            "income_statement.revenue",
            "income_statement.net_profit",
            "income_statement.eps",
        ),
    )
    if financial_line:
        summary.append(financial_line)
    cash_flow_line = _format_field_group(
        "现金流数据",
        cash_flow_fields,
        ("cash_flow.operating_cash_flow",),
    )
    if cash_flow_line:
        summary.append(cash_flow_line)
    valuation_basis_note = _build_fundamental_valuation_basis_note(
        valuation_fields=valuation_fields,
        financial_fields=financial_fields,
    )
    if valuation_basis_note:
        summary.append(valuation_basis_note)
    sources = _format_fundamental_sources(
        [*company_profile_fields.values(), *valuation_fields.values(), *financial_fields.values(), *cash_flow_fields.values()]
    )
    summary.extend(sources)
    return summary


def _format_field_group(label: str, fields: dict[str, FundamentalField], ordered_names: tuple[str, ...]) -> str | None:
    parts: list[str] = []
    for name in ordered_names:
        field = fields.get(name)
        if field is None:
            continue
        parts.append(f"{_field_label(name)}为{_format_fundamental_value(field)}")
    if not parts:
        return None
    return f"{label}：" + "，".join(parts) + "。"


def _build_fundamental_valuation_basis_note(
    *,
    valuation_fields: dict[str, FundamentalField],
    financial_fields: dict[str, FundamentalField],
) -> str | None:
    pe_field = valuation_fields.get("valuation.pe_ttm")
    eps_field = financial_fields.get("income_statement.eps")
    notes: list[str] = []
    if pe_field is not None:
        notes.append("PE TTM/动态市盈率是估值倍数口径")
    if eps_field is not None:
        notes.append("每股收益来自财报摘要对应报告期，不自动折算为 TTM EPS")
    if pe_field is not None and eps_field is not None:
        notes.append("若资料包未单列 TTM EPS，不要把报告期 EPS 与 PE TTM 直接相乘；估值推算需先说明口径不一致")
    elif pe_field is not None:
        notes.append("本资料包未单列 TTM EPS 时，只能按 PE TTM 做相对估值讨论")
    notes.append(
        "本资料包当前未提供历史 PE/PB 分位、行业估值中位数、TTM EPS 或 DCF 参数；如需给出价格区间测算结论，只能作为分析师情景假设，不得表述为数据源事实"
    )
    notes.append("若使用全年 EPS、行业估值或历史估值进行测算，必须明确写出假设前提与推演口径")
    if not notes:
        return None
    return "估值口径说明：" + "；".join(notes) + "。"


def _field_label(name: str) -> str:
    labels = {
        "company_profile.industry": "所属行业",
        "company_profile.main_business": "主营业务",
        "valuation.pe_ttm": "PE TTM",
        "valuation.pb": "PB",
        "valuation.total_mv": "总市值",
        "price_context.close": "收盘价",
        "price_context.trade_date": "交易日",
        "financial_indicators.roe": "ROE",
        "financial_indicators.roa": "ROA",
        "financial_indicators.gross_margin": "毛利率",
        "financial_indicators.netprofit_margin": "净利率",
        "financial_indicators.debt_to_assets": "资产负债率",
        "income_statement.revenue": "营业收入",
        "income_statement.net_profit": "净利润",
        "income_statement.eps": "每股收益",
        "cash_flow.operating_cash_flow": "经营现金流",
    }
    return labels.get(name, name)


def _format_fundamental_value(field: FundamentalField) -> str:
    value = field.value
    if value is None:
        text = "未取得"
    elif isinstance(value, float):
        text = f"{value:.4f}".rstrip("0").rstrip(".")
    else:
        text = str(value)
    if field.unit and field.unit not in {"raw", "text", "date"} and text != "未取得":
        text = f"{text}{field.unit}"
    period = field.report_period or field.source_time
    if period:
        text = f"{text}（{period}）"
    return text


def _format_fundamental_sources(fields: list[FundamentalField]) -> list[str]:
    grouped: dict[tuple[str, str], int] = {}
    for field in fields:
        key = (field.provider, field.endpoint)
        grouped[key] = grouped.get(key, 0) + 1
    lines: list[str] = []
    for (provider, endpoint), count in sorted(grouped.items(), key=lambda item: item[1], reverse=True)[:4]:
        lines.append(f"来源 {provider}/{endpoint} 提供 {count} 个基本面字段。")
    return lines


def _upsert_provider_cache_documents(
    *,
    provider_results: list[ProviderResult],
    query: Any,
    fetched_at: str,
    expires_at: str,
    collection: Any | None,
    diagnostic_flags: list[str],
) -> list[str]:
    refs: list[str] = []
    for result in provider_results:
        if result.raw_payload is None:
            continue
        payload_hash = result.attempt.payload_hash
        raw_ref = result.attempt.raw_payload_ref
        if not _is_sha256(payload_hash) or not _is_valid_l2_ref(raw_ref):
            continue
        key = ProviderCacheKey(
            market="CN_A",
            domain="fundamental",
            ticker=query.ticker,
            provider=result.attempt.provider,
            endpoint=result.attempt.endpoint,
            query_fingerprint=query.query_fingerprint,
            schema_version=_FUNDAMENTAL_SCHEMA_VERSION,
        )
        try:
            ref = upsert_provider_cache(
                ProviderCacheDocument(
                    _id=build_provider_cache_document_id(key),
                    key=key,
                    fetched_at=fetched_at,
                    expires_at=expires_at,
                    payload_hash=payload_hash or "",
                    raw_payload_ref=raw_ref or "",
                    raw_payload_size_bytes=len(stable_json_bytes(result.raw_payload)),
                    normalized_ref=None,
                    raw_count=result.attempt.raw_count,
                    accepted_count=result.attempt.accepted_count,
                    schema_validated_at=fetched_at,
                ),
                collection=collection,
            )
            refs.append(ref)
            record_mongo_upsert(kind="provider_cache", status="success")
        except FrontlineValidationError as exc:
            diagnostic_flags.append(f"{exc.code}:provider_cache_upsert_failed")
            record_mongo_upsert(kind="provider_cache", status="failure")
    return refs


def _insert_provider_attempt_rows(
    *,
    attempts: list[ProviderAttempt],
    context: ToolRuntimeContext,
    ticker: str,
    collection: Any | None,
    diagnostic_flags: list[str],
) -> None:
    for attempt in attempts:
        try:
            insert_provider_attempt(
                attempt,
                run_id=context.run_id,
                call_id=context.call_id,
                ticker=ticker,
                domain="fundamental",
                worker_id=context.worker_id,
                collection=collection,
            )
            record_mongo_upsert(kind="provider_attempt", status="success")
        except FrontlineValidationError as exc:
            diagnostic_flags.append(f"{exc.code}:provider_attempt_insert_failed")
            record_mongo_upsert(kind="provider_attempt", status="failure")


def _normalize_context_time(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise FrontlineValidationError(TOOL_PARAMS_INVALID, "runtime_context.current_time 非法") from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    text = str(value).strip()
    return text or None


def _is_sha256(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    if len(value) != 71:
        return False
    if not value.startswith("sha256:"):
        return False
    return all(ch in "0123456789abcdef" for ch in value[7:])


def _is_valid_l2_ref(value: Any) -> bool:
    return isinstance(value, str) and value.startswith("viking://") and bool(value.strip())


def _deduplicate_text_refs(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        output.append(value)
    return output


__all__ = [
    "BuildFundamentalDataPack",
    "run_fundamentals_data_pack",
]
