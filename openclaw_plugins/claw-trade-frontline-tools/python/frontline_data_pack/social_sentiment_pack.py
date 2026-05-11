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
from .evidence_writer import L2WriteSessionState, write_pack_evidence, write_provider_attempts, write_raw_payload
from .models import (
    BriefInput,
    NewsTargetProfile,
    PackInput,
    ProviderAttempt,
    ProviderCacheDocument,
    ProviderCacheKey,
    ProviderResult,
    SocialDomainData,
    SocialSignal,
    SocialToolInput,
    stable_json_bytes,
    to_jsonable,
)
from .mongo_store import (
    COLLECTION_NORMALIZED_SOCIAL_SIGNALS,
    COLLECTION_PROVIDER_ATTEMPTS,
    COLLECTION_PROVIDER_CACHE,
    create_mongo_store,
)
from .normalized_store import upsert_social_signals
from .observability import (
    emit_structured_log,
    record_mongo_cache_inspect,
    record_mongo_latency,
    record_mongo_upsert,
    record_pack_build,
    record_social_evidence_write_failed_total,
    record_social_signal_accepted_total,
    record_social_signal_rejected_total,
    record_span,
)
from .pack_builder import build_pack_envelope
from .provider_executor import ProviderCallable, execute_provider_plan
from .provider_plan import build_provider_plan, build_provider_query
from .providers_akshare_social import AKSHARE_SOCIAL_CALL_REGISTRY
from .quality_social import compute_social_quality
from .reader_brief import build_reader_brief
from .runtime_context import ToolRuntimeContext
from .security import normalize_ticker, validate_date, validate_date_range, validate_market, validate_runtime_context
from .social_matcher import has_text_evidence, match_and_bucket_social_signals


_DEFAULT_WINDOW_DAYS = 7
_SOCIAL_SCHEMA_VERSION = "cn_a_social_pack.v1"
_SOCIAL_TOOL_NAME = "social_social_sentiment_pack"
_SOCIAL_WORKER_ID = "social_analyst"
_MAX_BUCKET_ITEMS = 50


@dataclass
class BuildSocialSentimentPack:
    config: FrontlineProviderConfig | None = None
    provider_call_registry: Mapping[tuple[str, str], ProviderCallable] | None = None
    evidence_client: OpenVikingEvidenceClient | None = None
    provider_cache_collection: Any | None = None
    provider_attempts_collection: Any | None = None
    normalized_social_collection: Any | None = None

    def build(
        self,
        tool_input: SocialToolInput | Mapping[str, Any],
        runtime_context: ToolRuntimeContext | Mapping[str, Any],
    ):
        build_started = time.perf_counter()
        context = _coerce_runtime_context(runtime_context)
        _validate_social_context(context)
        normalized_input = _coerce_social_tool_input(tool_input)

        ticker = normalize_ticker(normalized_input.ticker)
        market = validate_market(normalized_input.market)
        start_date, end_date = _normalize_social_date_window(
            start_date=normalized_input.start_date,
            end_date=normalized_input.end_date,
            context=context,
        )
        profile = _resolve_social_target_profile(normalized_input=normalized_input, ticker=ticker)
        query = build_provider_query(
            market=market,
            ticker=ticker,
            company_name=profile.company_name,
            industry=profile.industry,
            start_date=start_date,
            end_date=end_date,
            adjust=None,
        )

        config = self.config or load_frontline_provider_config()
        cache_collection, attempts_collection, normalized_collection = self._resolve_collections(config)

        plan = build_provider_plan(
            domain="social",
            query=query,
            config=config,
            cache_inspection=[],
        )
        cache_attempts, cache_refs = _inspect_social_cache(
            plan=plan,
            query=query,
            now_utc=context.current_time,
            collection=cache_collection,
            cache_required=config.mongodb.cache_required,
        )

        call_registry = dict(AKSHARE_SOCIAL_CALL_REGISTRY)
        if self.provider_call_registry is not None:
            call_registry.update(self.provider_call_registry)
        provider_results = execute_provider_plan(
            plan,
            query,
            context,
            max_concurrency=config.provider_runtime.max_concurrency,
            total_timeout_ms=config.provider_runtime.total_timeout_ms,
            call_registry=call_registry,
        )

        state = L2WriteSessionState()
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
                    record_social_evidence_write_failed_total()

        buckets = match_and_bucket_social_signals(_flatten_social_rows(updated_results), profile)
        materialized_buckets, conversion_rejected = _materialize_social_buckets(
            buckets=buckets,
            max_items_per_bucket=_MAX_BUCKET_ITEMS,
        )
        rejected_count = buckets.rejected_count + conversion_rejected
        accepted_signals = [
            *materialized_buckets["attention_signals"],
            *materialized_buckets["topic_keyword_signals"],
            *materialized_buckets["related_symbol_signals"],
            *materialized_buckets["narrative_signals"],
        ]
        record_social_signal_accepted_total(count=len(accepted_signals))
        record_social_signal_rejected_total(count=rejected_count)

        remote_attempts = _rebuild_attempts_with_accepted_counts(
            attempts=[result.attempt for result in updated_results],
            accepted_signals=accepted_signals,
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
            record_social_evidence_write_failed_total()

        quality_started = time.perf_counter()
        quality = compute_social_quality(
            accepted_signals,
            all_attempts,
            l2_refs,
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
                "domain": "social",
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
                "domain": "social",
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
                "accepted_count": len(accepted_signals),
                "error_code": None,
                "evidence_kind": None,
                "l2_ref_present": bool(l2_refs),
                "mongo_ref_present": bool(cache_refs),
            }
        )
        social_judgment_allowed = has_text_evidence(buckets.to_dict()) and quality.status == "complete"

        brief = build_reader_brief(
            BriefInput(
                domain="social",
                input=PackInput(
                    ticker=ticker,
                    market="CN_A",
                    company_name=profile.company_name,
                    industry=profile.industry,
                    start_date=start_date,
                    end_date=end_date,
                ),
                quality=quality,
                provider_attempts=all_attempts,
                accepted_counts={
                    "attention": len(materialized_buckets["attention_signals"]),
                    "topic_keyword": len(materialized_buckets["topic_keyword_signals"]),
                    "related_symbol": len(materialized_buckets["related_symbol_signals"]),
                    "narrative": len(materialized_buckets["narrative_signals"]),
                },
                missing_items=_build_missing_items(
                    target_count=len(accepted_signals),
                    has_text=social_judgment_allowed,
                ),
                conflict_diagnostics=[],
                evidence_summary=_build_social_material_summary(accepted_signals),
            )
        )

        fetched_at = _normalize_context_time(context.current_time).isoformat()
        expires_at = (_normalize_context_time(context.current_time) + timedelta(hours=1)).isoformat()
        upsert_result = upsert_social_signals(
            ticker=ticker,
            signals=accepted_signals,
            fetched_at=fetched_at,
            expires_at=expires_at,
            collection=normalized_collection,
        )
        diagnostic_flags.extend(upsert_result.diagnostic_flags)
        record_mongo_upsert(
            kind="social_signals",
            status="failure" if upsert_result.write_failed else "success",
            count=max(1, upsert_result.attempted_count),
        )

        cache_refs.extend(
            _upsert_provider_cache_documents(
                provider_results=updated_results,
                query=query,
                fetched_at=fetched_at,
                expires_at=expires_at,
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

        domain_data = SocialDomainData(
            schema_version="cn_a_social_pack.v1",
            attention_signals=materialized_buckets["attention_signals"],
            topic_keyword_signals=materialized_buckets["topic_keyword_signals"],
            related_symbol_signals=materialized_buckets["related_symbol_signals"],
            narrative_signals=materialized_buckets["narrative_signals"],
            rejected_count=rejected_count,
            social_judgment_allowed=social_judgment_allowed,
        )
        pack = build_pack_envelope(
            domain="social",
            context=context,
            input=PackInput(
                ticker=ticker,
                market="CN_A",
                company_name=profile.company_name,
                industry=profile.industry,
                start_date=start_date,
                end_date=end_date,
            ),
            quality=quality,
            provider_attempts=all_attempts,
            field_sources={},
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
            record_social_evidence_write_failed_total()
            final_flags = [*diagnostic_flags, pack_write_result.error.diagnostic_flag]
            final_quality_started = time.perf_counter()
            final_quality = compute_social_quality(
                accepted_signals,
                all_attempts,
                l2_refs,
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
                    "domain": "social",
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
                    "domain": "social",
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
                    "accepted_count": len(accepted_signals),
                    "error_code": None,
                    "evidence_kind": None,
                    "l2_ref_present": bool(l2_refs),
                    "mongo_ref_present": bool(cache_refs),
                }
            )
            final_pack = build_pack_envelope(
                domain="social",
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
        build_elapsed_ms = max(0, int((time.perf_counter() - build_started) * 1000))
        record_pack_build(domain="social", status=final_pack.quality.status, elapsed_ms=build_elapsed_ms)
        record_span(
            "frontline_pack.build",
            status=final_pack.quality.status,
            elapsed_ms=build_elapsed_ms,
            fields={
                "run_id": context.run_id,
                "call_id": context.call_id,
                "worker_id": context.worker_id,
                "tool_name": context.tool_name,
                "domain": "social",
                "status": final_pack.quality.status,
                "elapsed_ms": build_elapsed_ms,
            },
        )
        return final_pack

    def _resolve_collections(self, config: FrontlineProviderConfig) -> tuple[Any | None, Any | None, Any | None]:
        cache_collection = self.provider_cache_collection
        attempts_collection = self.provider_attempts_collection
        normalized_collection = self.normalized_social_collection
        if cache_collection is not None and attempts_collection is not None and normalized_collection is not None:
            return cache_collection, attempts_collection, normalized_collection

        store = create_mongo_store(config.mongodb.uri)
        database = store.database
        return (
            cache_collection if cache_collection is not None else database[COLLECTION_PROVIDER_CACHE],
            attempts_collection if attempts_collection is not None else database[COLLECTION_PROVIDER_ATTEMPTS],
            normalized_collection if normalized_collection is not None else database[COLLECTION_NORMALIZED_SOCIAL_SIGNALS],
        )


def run_social_sentiment_pack(
    tool_input: SocialToolInput | Mapping[str, Any],
    runtime_context: ToolRuntimeContext | Mapping[str, Any],
):
    started = time.perf_counter()
    status = "success"
    context: ToolRuntimeContext | None = None
    try:
        context = _coerce_runtime_context(runtime_context)
        request = _coerce_social_tool_input(tool_input)
        return BuildSocialSentimentPack().build(request, context)
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
                "domain": "social",
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
                "domain": "social",
                "status": status,
                "elapsed_ms": elapsed_ms,
            },
        )


def _coerce_runtime_context(runtime_context: ToolRuntimeContext | Mapping[str, Any]) -> ToolRuntimeContext:
    if isinstance(runtime_context, ToolRuntimeContext):
        return runtime_context
    _validate_runtime_context_market(runtime_context)
    return validate_runtime_context(runtime_context)


def _validate_social_context(context: ToolRuntimeContext) -> None:
    if context.stage != "frontline":
        raise FrontlineValidationError(TOOL_WORKER_MISMATCH, "runtime_context.stage 必须为 frontline")
    if context.worker_id != _SOCIAL_WORKER_ID:
        raise FrontlineValidationError(
            TOOL_WORKER_MISMATCH,
            f"runtime_context.worker_id 必须为 {_SOCIAL_WORKER_ID}",
        )
    if context.tool_name != _SOCIAL_TOOL_NAME:
        raise FrontlineValidationError(
            TOOL_WORKER_MISMATCH,
            f"runtime_context.tool_name 必须为 {_SOCIAL_TOOL_NAME}",
        )


def _validate_runtime_context_market(runtime_context: Mapping[str, Any]) -> None:
    if "market" not in runtime_context:
        return
    raw_market = runtime_context.get("market")
    if not isinstance(raw_market, str) or not raw_market.strip():
        raise FrontlineValidationError(TOOL_CONTEXT_INCOMPLETE, "runtime_context.market 必须是非空字符串")
    if raw_market.strip().upper() != "CN_A":
        raise FrontlineValidationError(TOOL_WORKER_MISMATCH, "runtime_context.market 必须为 CN_A")


def _coerce_social_tool_input(tool_input: SocialToolInput | Mapping[str, Any]) -> SocialToolInput:
    if isinstance(tool_input, SocialToolInput):
        return tool_input
    if not isinstance(tool_input, Mapping):
        raise FrontlineValidationError(TOOL_PARAMS_INVALID, "tool_input 必须是 object")
    try:
        return SocialToolInput(
            ticker=str(tool_input.get("ticker", "")),
            market=str(tool_input.get("market", "")),
            company_name=_optional_string(tool_input.get("company_name")),
            industry=_optional_string(tool_input.get("industry")),
            start_date=_optional_string(tool_input.get("start_date")),
            end_date=_optional_string(tool_input.get("end_date")),
            aliases=_coerce_aliases(tool_input.get("aliases"), field_name="aliases"),
            approved_aliases=_coerce_aliases(tool_input.get("approved_aliases"), field_name="approved_aliases"),
        )
    except Exception as exc:  # noqa: BLE001
        raise FrontlineValidationError(TOOL_PARAMS_INVALID, f"tool_input 无法解析: {exc}") from exc


def _coerce_aliases(value: Any, *, field_name: str) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError(f"{field_name} 必须是 list")
    aliases: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{field_name}[{index}] 必须是非空字符串")
        aliases.append(item.strip())
    return aliases


def _resolve_social_target_profile(*, normalized_input: SocialToolInput, ticker: str) -> NewsTargetProfile:
    company_name = _optional_string(normalized_input.company_name)
    if company_name is None:
        raise FrontlineValidationError(TOOL_PARAMS_INVALID, "social_input.company_name 必须是非空字符串")
    approved_aliases = list(normalized_input.approved_aliases or [])
    approved_aliases = [alias for alias in approved_aliases if alias not in {company_name, ticker.split(".", 1)[0]}]
    deduped_approved_aliases = _deduplicate_text_refs(approved_aliases)
    return NewsTargetProfile(
        ticker=ticker,
        company_name=company_name,
        approved_aliases=deduped_approved_aliases,
        industry=_optional_string(normalized_input.industry),
    )


def _normalize_social_date_window(
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


def _inspect_social_cache(
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
            domain="social",
            ticker=query.ticker,
            provider=spec.provider,
            endpoint=spec.endpoint,
            query_fingerprint=query.query_fingerprint,
            schema_version=_SOCIAL_SCHEMA_VERSION,
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
                "domain": "social",
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


def _flatten_social_rows(provider_results: list[ProviderResult]) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for result in provider_results:
        for row in result.normalized_rows:
            item = dict(row)
            item["provider"] = result.attempt.provider
            item["endpoint"] = result.attempt.endpoint
            item["raw_payload_ref"] = result.attempt.raw_payload_ref
            item["payload_hash"] = result.attempt.payload_hash
            flattened.append(item)
    return flattened


def _materialize_social_buckets(
    *,
    buckets: Any,
    max_items_per_bucket: int,
) -> tuple[dict[str, list[SocialSignal]], int]:
    rejected = 0
    output: dict[str, list[SocialSignal]] = {
        "attention_signals": [],
        "topic_keyword_signals": [],
        "related_symbol_signals": [],
        "narrative_signals": [],
    }
    for bucket_name in output:
        raw_signals = getattr(buckets, bucket_name, [])
        for raw_signal in raw_signals:
            signal = _to_social_signal(raw_signal)
            if signal is None:
                rejected += 1
                continue
            output[bucket_name].append(signal)
        if len(output[bucket_name]) > max_items_per_bucket:
            rejected += len(output[bucket_name]) - max_items_per_bucket
            output[bucket_name] = output[bucket_name][:max_items_per_bucket]
    return output, rejected


def _to_social_signal(payload: Mapping[str, Any]) -> SocialSignal | None:
    raw_payload_ref = _optional_string(payload.get("raw_payload_ref"))
    payload_hash = _optional_string(payload.get("payload_hash"))
    observed_at = _optional_string(payload.get("observed_at"))
    if not _is_valid_l2_ref(raw_payload_ref):
        return None
    if not _is_sha256(payload_hash):
        return None
    if observed_at is None:
        return None
    try:
        return SocialSignal(
            signal_id=str(payload["signal_id"]),
            signal_type=str(payload["signal_type"]),  # type: ignore[arg-type]
            source_platform=str(payload.get("source_platform") or "eastmoney"),  # type: ignore[arg-type]
            observed_at=observed_at,
            target_ticker=str(payload["target_ticker"]),
            matched_target=bool(payload.get("matched_target", False)),
            match_evidence_span=str(payload.get("match_evidence_span") or ""),
            rank=_to_optional_int(payload.get("rank")),
            heat_value=_to_optional_float(payload.get("heat_value")),
            keyword=_optional_string(payload.get("keyword")),
            related_ticker=_optional_string(payload.get("related_ticker")),
            text_excerpt=_optional_string(payload.get("text_excerpt")),
            provider=str(payload["provider"]),
            endpoint=str(payload["endpoint"]),
            raw_payload_ref=raw_payload_ref,
            payload_hash=payload_hash,
        )
    except Exception:
        return None


def _to_optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return None


def _to_optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _rebuild_attempts_with_accepted_counts(
    *,
    attempts: list[ProviderAttempt],
    accepted_signals: list[SocialSignal],
) -> list[ProviderAttempt]:
    counts: dict[tuple[str, str], int] = {}
    for signal in accepted_signals:
        key = (signal.provider, signal.endpoint)
        counts[key] = counts.get(key, 0) + 1
    rebuilt: list[ProviderAttempt] = []
    for attempt in attempts:
        accepted_count = counts.get((attempt.provider, attempt.endpoint), 0)
        rebuilt.append(replace(attempt, accepted_count=min(accepted_count, attempt.raw_count)))
    return rebuilt


def _build_social_material_summary(signals: list[SocialSignal]) -> list[str]:
    if not signals:
        return []
    lines: list[str] = []
    for signal in signals[:10]:
        pieces = [
            f"类型为{_social_signal_label(signal.signal_type)}",
            f"观察时间 {signal.observed_at}",
            f"匹配依据：{signal.match_evidence_span}",
        ]
        if signal.rank is not None:
            pieces.append(f"热度排名第 {signal.rank}")
        if signal.heat_value is not None:
            pieces.append(f"热度值 {_format_social_number(signal.heat_value)}")
        if signal.keyword:
            pieces.append(f"关键词“{signal.keyword}”")
        if signal.related_ticker:
            pieces.append(f"关联标的 {signal.related_ticker}")
        if signal.text_excerpt:
            pieces.append(f"文本摘录：{signal.text_excerpt}")
        lines.append("社交材料：" + "，".join(pieces) + "。")
    if len(signals) > len(lines):
        lines.append(f"另有 {len(signals) - len(lines)} 条社交材料未在正文中展开。")
    lines.extend(_format_social_sources(signals))
    return lines


def _social_signal_label(signal_type: str) -> str:
    labels = {
        "attention": "关注热度",
        "topic_keyword": "话题关键词",
        "related_symbol": "关联标的",
        "narrative": "文本讨论",
    }
    return labels.get(signal_type, signal_type)


def _format_social_number(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _format_social_sources(signals: list[SocialSignal]) -> list[str]:
    grouped: dict[tuple[str, str], int] = {}
    for signal in signals:
        key = (signal.provider, signal.endpoint)
        grouped[key] = grouped.get(key, 0) + 1
    return [
        f"来源 {provider}/{endpoint} 提供 {count} 条社交材料。"
        for (provider, endpoint), count in sorted(grouped.items(), key=lambda item: item[1], reverse=True)[:4]
    ]


def _build_missing_items(*, target_count: int, has_text: bool) -> list[str]:
    items: list[str] = []
    if target_count == 0:
        items.append("social_target_accepted_signal_zero")
    if target_count > 0 and not has_text:
        items.append("social_text_evidence_missing")
    return items


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
        if not _is_sha256(result.attempt.payload_hash):
            continue
        raw_ref = result.attempt.raw_payload_ref
        if not _is_valid_l2_ref(raw_ref):
            continue
        key = ProviderCacheKey(
            market="CN_A",
            domain="social",
            ticker=query.ticker,
            provider=result.attempt.provider,
            endpoint=result.attempt.endpoint,
            query_fingerprint=query.query_fingerprint,
            schema_version=_SOCIAL_SCHEMA_VERSION,
        )
        try:
            ref = upsert_provider_cache(
                ProviderCacheDocument(
                    _id=build_provider_cache_document_id(key),
                    key=key,
                    fetched_at=fetched_at,
                    expires_at=expires_at,
                    payload_hash=result.attempt.payload_hash or "",
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
                domain="social",
                worker_id=context.worker_id,
                collection=collection,
            )
            record_mongo_upsert(kind="provider_attempt", status="success")
        except FrontlineValidationError as exc:
            diagnostic_flags.append(f"{exc.code}:provider_attempt_insert_failed")
            record_mongo_upsert(kind="provider_attempt", status="failure")


def _extract_cache_error_code(reason: str | None) -> str | None:
    if reason is None:
        return None
    text = reason.strip()
    if not text:
        return None
    return text.split(":", 1)[0]


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
    tail = value[7:]
    return all(ch in "0123456789abcdef" for ch in tail)


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
    "BuildSocialSentimentPack",
    "run_social_sentiment_pack",
]
