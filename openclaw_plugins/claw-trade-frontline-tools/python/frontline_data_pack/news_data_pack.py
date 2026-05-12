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
from .models import (
    BriefInput,
    NewsDomainData,
    NewsItem,
    NewsToolInput,
    PackInput,
    ProviderAttempt,
    ProviderCacheDocument,
    ProviderCacheKey,
    ProviderResult,
    stable_json_bytes,
    to_jsonable,
)
from .mongo_store import (
    COLLECTION_NORMALIZED_NEWS_ITEMS,
    COLLECTION_PROVIDER_ATTEMPTS,
    COLLECTION_PROVIDER_CACHE,
    resolve_optional_mongo_database,
)
from .news_matcher import deduplicate_by_title_url_time, resolve_news_target_profile, classify_news_item
from .normalized_store import upsert_news_items
from .observability import (
    emit_structured_log,
    record_mongo_cache_inspect,
    record_mongo_latency,
    record_mongo_upsert,
    record_news_accepted_total,
    record_news_context_error,
    record_news_raw_total,
    record_pack_build,
    record_span,
)
from .pack_builder import build_pack_envelope
from .provider_executor import ProviderCallable, execute_provider_plan
from .provider_plan import build_provider_plan, build_provider_query
from .providers_akshare_news import AKSHARE_NEWS_CALL_REGISTRY
from .providers_tushare_news import TUSHARE_NEWS_CALL_REGISTRY
from .quality_news import ComputeNewsQuality, NewsQualityInput
from .reader_brief import build_reader_brief
from .runtime_context import ToolRuntimeContext
from .security import (
    normalize_ticker,
    validate_date,
    validate_date_range,
    validate_market,
    validate_runtime_context,
)


_DEFAULT_WINDOW_DAYS = 7
_NEWS_SCHEMA_VERSION = "cn_a_news_pack.v1"
_NEWS_TOOL_NAME = "news_news_data_pack"
_NEWS_WORKER_ID = "news_analyst"
_MAX_BUCKET_ITEMS = 20


@dataclass
class BuildNewsDataPack:
    config: FrontlineProviderConfig | None = None
    provider_call_registry: Mapping[tuple[str, str], ProviderCallable] | None = None
    evidence_client: OpenVikingEvidenceClient | None = None
    provider_cache_collection: Any | None = None
    provider_attempts_collection: Any | None = None
    normalized_news_collection: Any | None = None

    def build(
        self,
        tool_input: NewsToolInput | Mapping[str, Any],
        runtime_context: ToolRuntimeContext | Mapping[str, Any],
    ):
        build_started = time.perf_counter()
        context = _coerce_runtime_context(runtime_context)
        _validate_news_context(context)
        normalized_input = _coerce_news_tool_input(tool_input)

        ticker = normalize_ticker(normalized_input.ticker)
        market = validate_market(normalized_input.market)
        start_date, end_date = _normalize_news_date_window(
            start_date=normalized_input.start_date,
            end_date=normalized_input.end_date,
            context=context,
        )

        profile = _resolve_profile(normalized_input=normalized_input, ticker=ticker)
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
            domain="news",
            query=query,
            config=config,
            cache_inspection=[],
        )
        cache_attempts, cache_refs = _inspect_news_cache(
            plan=plan,
            query=query,
            now_utc=context.current_time,
            collection=cache_collection,
            cache_required=config.mongodb.cache_required,
        )

        call_registry = self.provider_call_registry or _default_news_call_registry()
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

        all_classified, rejected_count = _classify_news_rows(
            provider_results=updated_results,
            profile=profile,
        )
        record_news_raw_total(count=sum(len(result.normalized_rows) for result in updated_results))
        deduped = deduplicate_by_title_url_time(all_classified)
        buckets = _bucketize_news_items(deduped)
        capped_company = _cap_bucket(buckets["company_news"], limit=_MAX_BUCKET_ITEMS)
        capped_industry = _cap_bucket(buckets["industry_macro"], limit=_MAX_BUCKET_ITEMS)
        capped_announcements = _cap_bucket(buckets["announcement"], limit=_MAX_BUCKET_ITEMS)

        company_items = [_to_news_item(item) for item in capped_company]
        industry_items = [_to_news_item(item) for item in capped_industry]
        announcement_items = [_to_news_item(item) for item in capped_announcements]
        accepted_items = [*company_items, *industry_items, *announcement_items]
        record_news_accepted_total(bucket="company_news", count=len(company_items))
        record_news_accepted_total(bucket="industry_macro", count=len(industry_items))
        record_news_accepted_total(bucket="announcement", count=len(announcement_items))

        remote_attempts = _rebuild_attempts_with_accepted_counts(
            attempts=[result.attempt for result in updated_results],
            accepted_items=accepted_items,
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
        quality = ComputeNewsQuality().compute(
            NewsQualityInput(
                company_news_count=len(company_items),
                industry_macro_count=len(industry_items),
                announcement_count=len(announcement_items),
                rejected_count=rejected_count,
                raw_total_count=sum(len(result.normalized_rows) for result in updated_results),
                provider_attempts=all_attempts,
                publish_times=[item.publish_time for item in accepted_items],
                as_of_date=end_date,
                diagnostic_flags=diagnostic_flags,
            )
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
                "domain": "news",
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
                "domain": "news",
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
                "accepted_count": len(accepted_items),
                "error_code": None,
                "evidence_kind": None,
                "l2_ref_present": bool(l2_refs),
                "mongo_ref_present": bool(cache_refs),
            }
        )

        brief = build_reader_brief(
            BriefInput(
                domain="news",
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
                    "company_news": len(company_items),
                    "industry_macro": len(industry_items),
                    "announcement": len(announcement_items),
                },
                missing_items=_build_missing_items(
                    company_count=len(company_items),
                    background_count=len(industry_items) + len(announcement_items),
                ),
                conflict_diagnostics=[],
                evidence_summary=_build_news_material_summary(accepted_items),
            )
        )

        fetched_at = _normalize_context_time(context.current_time).isoformat()
        expires_at = (_normalize_context_time(context.current_time) + timedelta(hours=6)).isoformat()

        upsert_result = upsert_news_items(
            ticker=ticker,
            items=accepted_items,
            fetched_at=fetched_at,
            expires_at=expires_at,
            collection=normalized_collection,
        )
        diagnostic_flags.extend(upsert_result.diagnostic_flags)
        record_mongo_upsert(
            kind="news_items",
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

        domain_data = NewsDomainData(
            schema_version="cn_a_news_pack.v1",
            company_news=company_items,
            industry_macro_news=industry_items,
            announcements=announcement_items,
            rejected_count=rejected_count,
        )
        pack = build_pack_envelope(
            domain="news",
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
            final_flags = [*diagnostic_flags, pack_write_result.error.diagnostic_flag]
            final_quality_started = time.perf_counter()
            final_quality = ComputeNewsQuality().compute(
                NewsQualityInput(
                    company_news_count=len(company_items),
                    industry_macro_count=len(industry_items),
                    announcement_count=len(announcement_items),
                    rejected_count=rejected_count,
                    raw_total_count=sum(len(result.normalized_rows) for result in updated_results),
                    provider_attempts=all_attempts,
                    publish_times=[item.publish_time for item in accepted_items],
                    as_of_date=end_date,
                    diagnostic_flags=final_flags,
                )
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
                    "domain": "news",
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
                    "domain": "news",
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
                    "accepted_count": len(accepted_items),
                    "error_code": None,
                    "evidence_kind": None,
                    "l2_ref_present": bool(l2_refs),
                    "mongo_ref_present": bool(cache_refs),
                }
            )
            final_pack = build_pack_envelope(
                domain="news",
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
        record_pack_build(domain="news", status=final_pack.quality.status, elapsed_ms=build_elapsed_ms)
        record_span(
            "frontline_pack.build",
            status=final_pack.quality.status,
            elapsed_ms=build_elapsed_ms,
            fields={
                "run_id": context.run_id,
                "call_id": context.call_id,
                "worker_id": context.worker_id,
                "tool_name": context.tool_name,
                "domain": "news",
                "status": final_pack.quality.status,
                "elapsed_ms": build_elapsed_ms,
            },
        )
        return final_pack

    def _resolve_collections(self, config: FrontlineProviderConfig) -> tuple[Any | None, Any | None, Any | None]:
        cache_collection = self.provider_cache_collection
        attempts_collection = self.provider_attempts_collection
        normalized_collection = self.normalized_news_collection
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
            normalized_collection if normalized_collection is not None else (
                database[COLLECTION_NORMALIZED_NEWS_ITEMS] if database is not None else None
            ),
        )


def run_news_data_pack(
    tool_input: NewsToolInput | Mapping[str, Any],
    runtime_context: ToolRuntimeContext | Mapping[str, Any],
):
    started = time.perf_counter()
    status = "success"
    context: ToolRuntimeContext | None = None
    try:
        context = _coerce_runtime_context(runtime_context)
        request = _coerce_news_tool_input(tool_input)
        return BuildNewsDataPack().build(request, context)
    except FrontlineValidationError as exc:
        if exc.code in {TOOL_CONTEXT_INCOMPLETE, TOOL_WORKER_MISMATCH}:
            record_news_context_error()
        status = "error"
        raise
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
                "domain": "news",
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
                "domain": "news",
                "status": status,
                "elapsed_ms": elapsed_ms,
            },
        )


def _coerce_runtime_context(runtime_context: ToolRuntimeContext | Mapping[str, Any]) -> ToolRuntimeContext:
    if isinstance(runtime_context, ToolRuntimeContext):
        return runtime_context
    _validate_runtime_context_market(runtime_context)
    return validate_runtime_context(runtime_context)


def _validate_news_context(context: ToolRuntimeContext) -> None:
    if context.stage != "frontline":
        raise FrontlineValidationError(TOOL_WORKER_MISMATCH, "runtime_context.stage 必须为 frontline")
    if context.worker_id != _NEWS_WORKER_ID:
        raise FrontlineValidationError(
            TOOL_WORKER_MISMATCH,
            f"runtime_context.worker_id 必须为 {_NEWS_WORKER_ID}",
        )
    if context.tool_name != _NEWS_TOOL_NAME:
        raise FrontlineValidationError(
            TOOL_WORKER_MISMATCH,
            f"runtime_context.tool_name 必须为 {_NEWS_TOOL_NAME}",
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


def _coerce_news_tool_input(tool_input: NewsToolInput | Mapping[str, Any]) -> NewsToolInput:
    if isinstance(tool_input, NewsToolInput):
        return tool_input
    if not isinstance(tool_input, Mapping):
        raise FrontlineValidationError(TOOL_PARAMS_INVALID, "tool_input 必须是 object")
    try:
        return NewsToolInput(
            ticker=str(tool_input.get("ticker", "")),
            market=str(tool_input.get("market", "")),
            company_name=_optional_string(tool_input.get("company_name")),
            industry=_optional_string(tool_input.get("industry")),
            start_date=_optional_string(tool_input.get("start_date")),
            end_date=_optional_string(tool_input.get("end_date")),
            aliases=_coerce_aliases(tool_input.get("aliases"), field_name="aliases"),
            approved_aliases=_coerce_aliases(tool_input.get("approved_aliases"), field_name="approved_aliases"),
            approved_artifact_refs=_coerce_approved_refs(tool_input.get("approved_artifact_refs")),
        )
    except Exception as exc:  # noqa: BLE001
        raise FrontlineValidationError(TOOL_PARAMS_INVALID, f"tool_input 无法解析: {exc}") from exc


def _coerce_approved_refs(value: Any) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError("approved_artifact_refs 必须是 list")
    refs: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"approved_artifact_refs[{index}] 必须是非空字符串")
        refs.append(item.strip())
    return refs


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


def _normalize_news_date_window(
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


def _resolve_profile(*, normalized_input: NewsToolInput, ticker: str):
    profile_input = {
        "ticker": ticker,
        "company_name": normalized_input.company_name,
        "industry": normalized_input.industry,
        "aliases": normalized_input.aliases or [],
        "approved_aliases": normalized_input.approved_aliases or [],
        "approved_artifact_refs": normalized_input.approved_artifact_refs or [],
    }
    try:
        return resolve_news_target_profile(profile_input, normalized_input.approved_artifact_refs or [])
    except Exception as exc:  # noqa: BLE001
        raise FrontlineValidationError(TOOL_PARAMS_INVALID, f"news target profile 解析失败: {exc}") from exc


def _inspect_news_cache(
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
            domain="news",
            ticker=query.ticker,
            provider=spec.provider,
            endpoint=spec.endpoint,
            query_fingerprint=query.query_fingerprint,
            schema_version=_NEWS_SCHEMA_VERSION,
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
                "domain": "news",
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


def _classify_news_rows(
    *,
    provider_results: list[ProviderResult],
    profile: Any,
) -> tuple[list[dict[str, Any]], int]:
    accepted: list[dict[str, Any]] = []
    rejected_count = 0
    for result in provider_results:
        for row in result.normalized_rows:
            raw_item = dict(row)
            raw_item["provider"] = result.attempt.provider
            raw_item["endpoint"] = result.attempt.endpoint
            raw_item["raw_payload_ref"] = result.attempt.raw_payload_ref
            raw_item["payload_hash"] = result.attempt.payload_hash

            classified = classify_news_item(raw_item, profile)
            if classified["bucket"] == "rejected":
                rejected_count += 1
                continue
            if not classified["match_evidence_span"]:
                rejected_count += 1
                continue
            if not _is_valid_l2_ref(classified["raw_payload_ref"]):
                rejected_count += 1
                continue
            if not _is_sha256(classified["payload_hash"]):
                rejected_count += 1
                continue
            accepted.append(classified)
    return accepted, rejected_count


def _bucketize_news_items(items: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    buckets: dict[str, list[dict[str, Any]]] = {
        "company_news": [],
        "industry_macro": [],
        "announcement": [],
    }
    for item in items:
        bucket = item.get("bucket")
        if bucket in buckets:
            buckets[bucket].append(item)
    return buckets


def _cap_bucket(items: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    ordered = sorted(
        items,
        key=lambda item: _sort_key(item.get("publish_time"), item.get("title")),
        reverse=True,
    )
    return ordered[:limit]


def _sort_key(publish_time: Any, title: Any) -> tuple[str, str]:
    publish_text = _optional_string(publish_time) or ""
    title_text = _optional_string(title) or ""
    return publish_text, title_text


def _to_news_item(item: Mapping[str, Any]) -> NewsItem:
    return NewsItem(
        news_id=str(item["news_id"]),
        title=str(item["title"]),
        summary=_optional_string(item.get("summary")),
        source=str(item.get("source") or "unknown"),
        publish_time=_optional_string(item.get("publish_time")),
        url=_optional_string(item.get("url")),
        bucket=str(item["bucket"]),  # type: ignore[arg-type]
        match_type=str(item["match_type"]),  # type: ignore[arg-type]
        match_evidence_span=str(item["match_evidence_span"]),
        provider=str(item["provider"]),
        endpoint=str(item["endpoint"]),
        raw_payload_ref=str(item["raw_payload_ref"]),
        payload_hash=str(item["payload_hash"]),
    )


def _rebuild_attempts_with_accepted_counts(
    *,
    attempts: list[ProviderAttempt],
    accepted_items: list[NewsItem],
) -> list[ProviderAttempt]:
    counts: dict[tuple[str, str], int] = {}
    for item in accepted_items:
        key = (item.provider, item.endpoint)
        counts[key] = counts.get(key, 0) + 1

    rebuilt: list[ProviderAttempt] = []
    for attempt in attempts:
        accepted_count = counts.get((attempt.provider, attempt.endpoint), 0)
        rebuilt.append(replace(attempt, accepted_count=min(accepted_count, attempt.raw_count)))
    return rebuilt


def _build_news_material_summary(items: list[NewsItem]) -> list[str]:
    if not items:
        return []
    lines: list[str] = []
    for item in items[:10]:
        bucket_label = {
            "company_news": "公司新闻",
            "industry_macro": "行业/宏观新闻",
            "announcement": "公告",
            "rejected": "未采用新闻",
        }.get(item.bucket, item.bucket)
        time_text = item.publish_time or "时间未取得"
        summary = f"；摘要：{item.summary.strip()}" if item.summary else ""
        lines.append(
            f"{bucket_label}：{time_text}，{item.source}《{item.title}》{summary}。"
            f"匹配依据：{item.match_type}，{item.match_evidence_span}。"
        )
    if len(items) > len(lines):
        lines.append(f"另有 {len(items) - len(lines)} 条新闻材料未在正文中展开。")
    lines.extend(_format_news_sources(items))
    return lines


def _format_news_sources(items: list[NewsItem]) -> list[str]:
    grouped: dict[tuple[str, str], int] = {}
    for item in items:
        key = (item.provider, item.endpoint)
        grouped[key] = grouped.get(key, 0) + 1
    return [
        f"来源 {provider}/{endpoint} 提供 {count} 条新闻材料。"
        for (provider, endpoint), count in sorted(grouped.items(), key=lambda item: item[1], reverse=True)[:4]
    ]


def _build_missing_items(*, company_count: int, background_count: int) -> list[str]:
    items: list[str] = []
    if company_count == 0:
        items.append("company_news_hard_match")
    if company_count == 0 and background_count == 0:
        items.append("news_background_missing")
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
            domain="news",
            ticker=query.ticker,
            provider=result.attempt.provider,
            endpoint=result.attempt.endpoint,
            query_fingerprint=query.query_fingerprint,
            schema_version=_NEWS_SCHEMA_VERSION,
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
                domain="news",
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


def _default_news_call_registry() -> dict[tuple[str, str], ProviderCallable]:
    registry: dict[tuple[str, str], ProviderCallable] = {}
    registry.update(AKSHARE_NEWS_CALL_REGISTRY)
    registry.update(TUSHARE_NEWS_CALL_REGISTRY)
    return registry


__all__ = [
    "BuildNewsDataPack",
    "run_news_data_pack",
]
