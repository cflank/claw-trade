from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
import time
from typing import Any, Callable, Mapping

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
    AtrIndicators,
    BollIndicators,
    BriefInput,
    ChartRef,
    EvidenceRef,
    KdjIndicators,
    MacdIndicators,
    MarketDateRange,
    MarketDomainData,
    MarketIndicators,
    MarketPriceHistory,
    MarketPriceRow,
    MarketSupportResistance,
    MarketToolInput,
    MarketVolumeProfile,
    MarketVolumeProfileBucket,
    MovingAverageIndicators,
    PackInput,
    PackEnvelope,
    ProviderAttempt,
    ProviderCacheDocument,
    ProviderCacheKey,
    ProviderResult,
    RsiIndicators,
    SupportResistanceLevel,
    TechlabInputFrame,
    TechlabResult,
    stable_json_bytes,
    to_jsonable,
)
from .mongo_store import (
    COLLECTION_NORMALIZED_MARKET_PRICES,
    COLLECTION_PROVIDER_ATTEMPTS,
    COLLECTION_PROVIDER_CACHE,
    resolve_optional_mongo_database,
)
from .normalized_store import upsert_market_prices
from .normalizer_market import merge_market_rows, normalize_market_rows, validate_ohlcv_rows
from .observability import (
    emit_structured_log,
    record_market_chart,
    record_market_rows,
    record_mongo_cache_inspect,
    record_mongo_latency,
    record_mongo_upsert,
    record_pack_build,
    record_span,
)
from .pack_builder import build_pack_envelope
from .profile import normalize_market_input
from .provider_executor import ProviderCallable, execute_provider_plan
from .provider_plan import build_provider_plan, build_provider_query
from .providers_akshare_market import AKSHARE_MARKET_CALL_REGISTRY
from .providers_eastmoney import EASTMONEY_MARKET_CALL_REGISTRY
from .providers_tushare_market import TUSHARE_MARKET_CALL_REGISTRY
from .quality_market import compute_market_quality
from .reader_brief import build_reader_brief
from .runtime_context import ToolRuntimeContext
from .security import validate_runtime_context
from .techlab_adapter import compute_market_techlab_outputs


_DEFAULT_WINDOW_DAYS = 60
_MARKET_SCHEMA_VERSION = "cn_a_market_pack.v1"
_MARKET_TOOL_NAME = "market_market_data_pack"
_MARKET_WORKER_ID = "market_analyst"
_MARKET_ADJUST = "qfq"
_MARKET_NORMALIZED_TTL_HOURS = 6
_MARKET_CACHE_TTL_HOURS = 6
_TECHLAB_OUTPUT_DIR = "techlab/charts-local"


@dataclass
class BuildMarketDataPack:
    config: FrontlineProviderConfig | None = None
    provider_call_registry: Mapping[tuple[str, str], ProviderCallable] | None = None
    evidence_client: OpenVikingEvidenceClient | None = None
    provider_cache_collection: Any | None = None
    provider_attempts_collection: Any | None = None
    normalized_market_collection: Any | None = None
    techlab_compute: Callable[..., TechlabResult] | None = None

    def build(
        self,
        tool_input: MarketToolInput | Mapping[str, Any],
        runtime_context: ToolRuntimeContext | Mapping[str, Any],
    ):
        build_started = time.perf_counter()
        context = _coerce_runtime_context(runtime_context)
        _validate_market_context(context)
        market_input = _coerce_market_tool_input(tool_input)
        normalized_input = _normalize_market_input(
            market_input=market_input,
            context=context,
            window_days=_DEFAULT_WINDOW_DAYS,
        )
        query = build_provider_query(
            market="CN_A",
            ticker=normalized_input.ticker,
            company_name=normalized_input.company_name,
            industry=None,
            start_date=normalized_input.start_date,
            end_date=normalized_input.end_date,
            adjust=normalized_input.adjust,
        )

        config = self.config or load_frontline_provider_config()
        cache_collection, attempts_collection, normalized_collection = self._resolve_collections(config)
        plan = build_provider_plan(
            domain="market",
            query=query,
            config=config,
            cache_inspection=[],
        )
        cache_attempts, cache_refs = _inspect_market_cache(
            plan=plan,
            query=query,
            now_utc=context.current_time,
            collection=cache_collection,
            cache_required=config.mongodb.cache_required,
        )

        execution_specs = [spec for spec in plan if spec.mode == "remote"]
        call_registry = self.provider_call_registry or _default_market_call_registry()
        provider_results = execute_provider_plan(
            execution_specs,
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

        normalized_rows, normalize_diagnostics = normalize_market_rows(updated_results)
        diagnostic_flags.extend(normalize_diagnostics)
        merged_rows = merge_market_rows(normalized_rows)
        accepted_rows, validate_diagnostics = validate_ohlcv_rows(merged_rows)
        diagnostic_flags.extend(validate_diagnostics)
        market_rows = _materialize_market_rows(accepted_rows)
        record_market_rows(count=len(market_rows))

        techlab_result = _compute_techlab_outputs(
            rows=market_rows,
            ticker=normalized_input.ticker,
            context=context,
            compute_fn=self.techlab_compute,
            l2_client=self.evidence_client,
            state=state,
        )
        diagnostic_flags.extend(techlab_result.diagnostics)
        indicators = techlab_result.indicators
        chart_refs = list(techlab_result.chart_refs)
        chart_evidence_refs = list(techlab_result.chart_evidence_refs)
        if chart_refs and not chart_evidence_refs:
            diagnostic_flags.append("market_chart_l2_evidence_missing")
            chart_refs = []
        if chart_refs:
            record_market_chart(status="success", count=len(chart_refs))
        else:
            record_market_chart(status="failure", count=1)

        remote_attempts = _rebuild_attempts_with_accepted_counts(
            attempts=[item.attempt for item in updated_results],
            accepted_rows=accepted_rows,
        )
        all_attempts = [*cache_attempts, *remote_attempts]

        attempts_write_result = write_provider_attempts(
            context=context,
            attempts=all_attempts,
            state=state,
            client=self.evidence_client,
        )
        l2_refs = [*raw_payload_refs, *chart_evidence_refs]
        if attempts_write_result.ok:
            if attempts_write_result.evidence_ref is not None:
                l2_refs.append(attempts_write_result.evidence_ref)
        elif attempts_write_result.error is not None:
            diagnostic_flags.append(attempts_write_result.error.diagnostic_flag)

        quality_started = time.perf_counter()
        quality = compute_market_quality(
            market_rows,
            indicators,
            chart_refs,
            all_attempts,
            l2_refs,
            diagnostic_flags=diagnostic_flags,
            as_of_date=normalized_input.end_date,
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
                "domain": "market",
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
                "domain": "market",
                "ticker": normalized_input.ticker,
                "provider": None,
                "endpoint": None,
                "role": None,
                "attempt_status": None,
                "quality_status": quality.status,
                "status": quality.status,
                "elapsed_ms": quality_elapsed_ms,
                "timeout_ms": None,
                "raw_count": None,
                "accepted_count": len(market_rows),
                "error_code": None,
                "evidence_kind": None,
                "l2_ref_present": bool(l2_refs),
                "mongo_ref_present": bool(cache_refs),
            }
        )

        brief = build_reader_brief(
            BriefInput(
                domain="market",
                input=PackInput(
                    ticker=normalized_input.ticker,
                    market="CN_A",
                    company_name=normalized_input.company_name,
                    industry=None,
                    start_date=normalized_input.start_date,
                    end_date=normalized_input.end_date,
                ),
                quality=quality,
                provider_attempts=all_attempts,
                accepted_counts={"price_rows": len(market_rows), "charts": len(chart_refs)},
                missing_items=_build_missing_items(
                    has_rows=bool(market_rows),
                    has_indicators=indicators is not None,
                    has_chart=bool(chart_refs),
                    p0_p1_success=_has_p0_p1_success(all_attempts),
                ),
                conflict_diagnostics=[],
                evidence_summary=_build_evidence_summary(
                    rows=market_rows,
                    accepted_rows=accepted_rows,
                    indicators=indicators,
                    chart_refs=chart_refs,
                    attempts=all_attempts,
                ),
            )
        )

        now_dt = _normalize_context_time(context.current_time)
        fetched_at = now_dt.isoformat()
        normalized_expires_at = (now_dt + timedelta(hours=_MARKET_NORMALIZED_TTL_HOURS)).isoformat()
        cache_expires_at = (now_dt + timedelta(hours=_MARKET_CACHE_TTL_HOURS)).isoformat()

        normalized_refs, normalized_diagnostics = _upsert_market_rows(
            market_rows=market_rows,
            attempts=remote_attempts,
            ticker=normalized_input.ticker,
            fetched_at=fetched_at,
            expires_at=normalized_expires_at,
            collection=normalized_collection,
        )
        diagnostic_flags.extend(normalized_diagnostics)
        cache_refs.extend(normalized_refs)

        cache_refs.extend(
            _upsert_provider_cache_documents(
                provider_results=updated_results,
                query=query,
                fetched_at=fetched_at,
                expires_at=cache_expires_at,
                collection=cache_collection,
                diagnostic_flags=diagnostic_flags,
            )
        )
        _insert_provider_attempt_rows(
            attempts=all_attempts,
            context=context,
            ticker=normalized_input.ticker,
            collection=attempts_collection,
            diagnostic_flags=diagnostic_flags,
        )

        domain_data = _build_market_domain_data(
            ticker=normalized_input.ticker,
            request_start_date=normalized_input.start_date,
            request_end_date=normalized_input.end_date,
            rows=market_rows,
            indicators=indicators,
            chart_refs=chart_refs,
        )
        pack = build_pack_envelope(
            domain="market",
            context=context,
            input=PackInput(
                ticker=normalized_input.ticker,
                market="CN_A",
                company_name=normalized_input.company_name,
                industry=None,
                start_date=normalized_input.start_date,
                end_date=normalized_input.end_date,
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
            final_quality = compute_market_quality(
                market_rows,
                indicators,
                chart_refs,
                all_attempts,
                l2_refs,
                diagnostic_flags=final_flags,
                as_of_date=normalized_input.end_date,
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
                    "domain": "market",
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
                    "domain": "market",
                    "ticker": normalized_input.ticker,
                    "provider": None,
                    "endpoint": None,
                    "role": None,
                    "attempt_status": None,
                    "quality_status": final_quality.status,
                    "status": final_quality.status,
                    "elapsed_ms": final_quality_elapsed_ms,
                    "timeout_ms": None,
                    "raw_count": None,
                    "accepted_count": len(market_rows),
                    "error_code": None,
                    "evidence_kind": None,
                    "l2_ref_present": bool(l2_refs),
                    "mongo_ref_present": bool(cache_refs),
                }
            )
            final_pack = build_pack_envelope(
                domain="market",
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
        record_pack_build(domain="market", status=final_pack.quality.status, elapsed_ms=build_elapsed_ms)
        record_span(
            "frontline_pack.build",
            status=final_pack.quality.status,
            elapsed_ms=build_elapsed_ms,
            fields={
                "run_id": context.run_id,
                "call_id": context.call_id,
                "worker_id": context.worker_id,
                "tool_name": context.tool_name,
                "domain": "market",
                "status": final_pack.quality.status,
                "elapsed_ms": build_elapsed_ms,
            },
        )
        return final_pack

    def _resolve_collections(self, config: FrontlineProviderConfig) -> tuple[Any | None, Any | None, Any | None]:
        cache_collection = self.provider_cache_collection
        attempts_collection = self.provider_attempts_collection
        normalized_collection = self.normalized_market_collection
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
                database[COLLECTION_NORMALIZED_MARKET_PRICES] if database is not None else None
            ),
        )


def run_market_data_pack(
    tool_input: MarketToolInput | Mapping[str, Any],
    runtime_context: ToolRuntimeContext | Mapping[str, Any],
):
    started = time.perf_counter()
    status = "success"
    context: ToolRuntimeContext | None = None
    try:
        config = load_frontline_provider_config()
        context = _coerce_runtime_context(runtime_context)
        request = _coerce_market_tool_input(tool_input)
        return build_market_data_pack(request, context, config)
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
                "domain": "market",
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
                "domain": "market",
                "status": status,
                "elapsed_ms": elapsed_ms,
            },
        )


def build_market_data_pack(
    request: MarketToolInput,
    context: ToolRuntimeContext,
    config: FrontlineProviderConfig,
):
    return BuildMarketDataPack(config=config).build(request, context)


def _coerce_runtime_context(runtime_context: ToolRuntimeContext | Mapping[str, Any]) -> ToolRuntimeContext:
    if isinstance(runtime_context, ToolRuntimeContext):
        return runtime_context
    _validate_runtime_context_market(runtime_context)
    return validate_runtime_context(runtime_context)


def _validate_market_context(context: ToolRuntimeContext) -> None:
    if context.stage != "frontline":
        raise FrontlineValidationError(TOOL_WORKER_MISMATCH, "runtime_context.stage 必须为 frontline")
    if context.worker_id != _MARKET_WORKER_ID:
        raise FrontlineValidationError(
            TOOL_WORKER_MISMATCH,
            f"runtime_context.worker_id 必须为 {_MARKET_WORKER_ID}",
        )
    if context.tool_name != _MARKET_TOOL_NAME:
        raise FrontlineValidationError(
            TOOL_WORKER_MISMATCH,
            f"runtime_context.tool_name 必须为 {_MARKET_TOOL_NAME}",
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


def _coerce_market_tool_input(tool_input: MarketToolInput | Mapping[str, Any]) -> MarketToolInput:
    if isinstance(tool_input, MarketToolInput):
        return tool_input
    if not isinstance(tool_input, Mapping):
        raise FrontlineValidationError(TOOL_PARAMS_INVALID, "tool_input 必须是 object")
    try:
        return MarketToolInput(
            ticker=str(tool_input.get("ticker", "")),
            market=str(tool_input.get("market", "")),
            company_name=_optional_string(tool_input.get("company_name")),
            start_date=_optional_string(tool_input.get("start_date")),
            end_date=_optional_string(tool_input.get("end_date")),
        )
    except Exception as exc:  # noqa: BLE001
        raise FrontlineValidationError(TOOL_PARAMS_INVALID, f"tool_input 无法解析: {exc}") from exc


def _normalize_market_input(
    *,
    market_input: MarketToolInput,
    context: ToolRuntimeContext,
    window_days: int,
):
    today = _resolve_context_today(context)
    try:
        return normalize_market_input(
            market_input,
            today=today,
            window_days=window_days,
            adjust=_MARKET_ADJUST,
        )
    except FrontlineValidationError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise FrontlineValidationError(TOOL_PARAMS_INVALID, f"market 参数规范化失败: {exc}") from exc


def _resolve_context_today(context: ToolRuntimeContext) -> date:
    if isinstance(context.current_date, str) and context.current_date.strip():
        try:
            return datetime.strptime(context.current_date.strip(), "%Y-%m-%d").date()
        except ValueError as exc:
            raise FrontlineValidationError(TOOL_CONTEXT_INCOMPLETE, "runtime_context.current_date 非法") from exc
    return _normalize_context_time(context.current_time).date()


def _inspect_market_cache(
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
            domain="market",
            ticker=query.ticker,
            provider=spec.provider,
            endpoint=spec.endpoint,
            query_fingerprint=query.query_fingerprint,
            schema_version=_MARKET_SCHEMA_VERSION,
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
                "domain": "market",
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


def _compute_techlab_outputs(
    *,
    rows: list[MarketPriceRow],
    ticker: str,
    context: ToolRuntimeContext,
    compute_fn: Callable[..., TechlabResult] | None,
    l2_client: OpenVikingEvidenceClient | None,
    state: L2WriteSessionState,
) -> TechlabResult:
    if not rows:
        return TechlabResult(
            indicators=None,
            chart_paths=[],
            chart_refs=[],
            diagnostics=[],
            failed=True,
        )
    frame = TechlabInputFrame(
        ticker=ticker,
        rows=rows,
        output_dir=_TECHLAB_OUTPUT_DIR,
    )
    runner = compute_fn or compute_market_techlab_outputs
    try:
        return runner(
            frame=frame,
            context=context,
            l2_client=l2_client,
            write_state=state,
        )
    except TypeError:
        return runner(frame, context)


def _materialize_market_rows(rows: list[Mapping[str, Any]]) -> list[MarketPriceRow]:
    materialized: list[MarketPriceRow] = []
    for row in rows:
        provider = str(row.get("provider", "unknown"))
        endpoint = str(row.get("endpoint", "unknown"))
        trade_date = str(row["trade_date"])
        source_ref = f"{provider}/{endpoint}:{trade_date}"
        materialized.append(
            MarketPriceRow(
                trade_date=trade_date,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
                amount=None if row.get("amount") is None else float(row["amount"]),
                adjust=_MARKET_ADJUST,
                source_ref=source_ref,
            )
        )
    materialized.sort(key=lambda item: item.trade_date)
    return materialized


def _rebuild_attempts_with_accepted_counts(
    *,
    attempts: list[ProviderAttempt],
    accepted_rows: list[Mapping[str, Any]],
) -> list[ProviderAttempt]:
    counts: dict[tuple[str, str], int] = {}
    for row in accepted_rows:
        key = (str(row.get("provider", "unknown")), str(row.get("endpoint", "unknown")))
        counts[key] = counts.get(key, 0) + 1

    rebuilt: list[ProviderAttempt] = []
    for attempt in attempts:
        accepted_count = counts.get((attempt.provider, attempt.endpoint), 0)
        rebuilt.append(replace(attempt, accepted_count=min(accepted_count, attempt.raw_count)))
    return rebuilt


def _upsert_market_rows(
    *,
    market_rows: list[MarketPriceRow],
    attempts: list[ProviderAttempt],
    ticker: str,
    fetched_at: str,
    expires_at: str,
    collection: Any | None,
) -> tuple[list[str], list[str]]:
    if not market_rows:
        return [], []
    attempt_by_provider_endpoint = {(attempt.provider, attempt.endpoint): attempt for attempt in attempts}
    grouped_rows: dict[tuple[str, str], list[MarketPriceRow]] = {}
    for row in market_rows:
        provider_endpoint, _, _ = row.source_ref.partition(":")
        provider, _, endpoint = provider_endpoint.partition("/")
        key = (provider, endpoint)
        grouped_rows.setdefault(key, []).append(row)

    refs: list[str] = []
    diagnostics: list[str] = []
    for (provider, endpoint), rows in grouped_rows.items():
        attempt = attempt_by_provider_endpoint.get((provider, endpoint))
        if attempt is None:
            continue
        if not _is_sha256(attempt.payload_hash):
            diagnostics.append("MONGO_WRITE_FAILED:market_rows_upsert_skipped_payload_hash_missing")
            continue
        if not _is_valid_l2_ref(attempt.raw_payload_ref):
            diagnostics.append("MONGO_WRITE_FAILED:market_rows_upsert_skipped_raw_ref_missing")
            continue
        upsert_result = upsert_market_prices(
            ticker=ticker,
            rows=rows,
            provider=provider,
            endpoint=endpoint,
            payload_hash=attempt.payload_hash or "",
            raw_payload_ref=attempt.raw_payload_ref or "",
            fetched_at=fetched_at,
            expires_at=expires_at,
            collection=collection,
        )
        refs.extend(upsert_result.refs)
        diagnostics.extend(upsert_result.diagnostic_flags)
        record_mongo_upsert(
            kind="market_prices",
            status="failure" if upsert_result.write_failed else "success",
            count=max(1, upsert_result.attempted_count),
        )
    return refs, diagnostics


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
            domain="market",
            ticker=query.ticker,
            provider=result.attempt.provider,
            endpoint=result.attempt.endpoint,
            query_fingerprint=query.query_fingerprint,
            schema_version=_MARKET_SCHEMA_VERSION,
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
                domain="market",
                worker_id=context.worker_id,
                collection=collection,
            )
            record_mongo_upsert(kind="provider_attempt", status="success")
        except FrontlineValidationError as exc:
            diagnostic_flags.append(f"{exc.code}:provider_attempt_insert_failed")
            record_mongo_upsert(kind="provider_attempt", status="failure")


def _build_market_domain_data(
    *,
    ticker: str,
    request_start_date: str,
    request_end_date: str,
    rows: list[MarketPriceRow],
    indicators: MarketIndicators | None,
    chart_refs: list[ChartRef],
) -> MarketDomainData:
    if rows:
        date_range = MarketDateRange(
            start_date=rows[0].trade_date,
            end_date=rows[-1].trade_date,
        )
        source_refs = _deduplicate_text_refs([row.source_ref for row in rows])
        recent_rows = rows[-10:]
    else:
        date_range = MarketDateRange(start_date=request_start_date, end_date=request_end_date)
        source_refs = []
        recent_rows = []

    price_history = MarketPriceHistory(
        ticker=ticker,
        adjust=_MARKET_ADJUST,
        row_count=len(rows),
        date_range=date_range,
        recent_rows=recent_rows,
        source_refs=source_refs,
    )
    return MarketDomainData(
        schema_version=_MARKET_SCHEMA_VERSION,
        price_history=price_history,
        technical_indicators=indicators or _empty_market_indicators(),
        chart_refs=chart_refs,
        support_resistance=_build_support_resistance(rows),
        volume_profile=_build_volume_profile(rows),
    )


def _build_support_resistance(rows: list[MarketPriceRow]) -> MarketSupportResistance:
    if not rows:
        return MarketSupportResistance(levels=[], calculation_window_days=1, diagnostics=["market_rows_empty"])
    support = min(rows, key=lambda row: row.low)
    resistance = max(rows, key=lambda row: row.high)
    window_days = max(1, len(rows))
    levels = [
        SupportResistanceLevel(
            kind="support",
            price=support.low,
            basis="high_low_window",
            window_days=window_days,
            source_refs=[support.source_ref],
        ),
        SupportResistanceLevel(
            kind="resistance",
            price=resistance.high,
            basis="high_low_window",
            window_days=window_days,
            source_refs=[resistance.source_ref],
        ),
    ]
    return MarketSupportResistance(
        levels=levels,
        calculation_window_days=window_days,
        diagnostics=[],
    )


def _build_volume_profile(rows: list[MarketPriceRow]) -> MarketVolumeProfile:
    if not rows:
        return MarketVolumeProfile(
            buckets=[],
            dominant_price_low=None,
            dominant_price_high=None,
            source_refs=[],
            diagnostics=["market_rows_empty"],
        )
    price_low = min(row.low for row in rows)
    price_high = max(row.high for row in rows)
    total_volume = sum(row.volume for row in rows)
    total_amount = sum(row.amount for row in rows if row.amount is not None)
    bucket = MarketVolumeProfileBucket(
        price_low=price_low,
        price_high=price_high,
        volume=total_volume,
        amount=total_amount,
        trade_days=len(rows),
    )
    return MarketVolumeProfile(
        buckets=[bucket],
        dominant_price_low=price_low,
        dominant_price_high=price_high,
        source_refs=_deduplicate_text_refs([row.source_ref for row in rows]),
        diagnostics=[],
    )


def _empty_market_indicators() -> MarketIndicators:
    return MarketIndicators(
        ma=MovingAverageIndicators(ma5=None, ma10=None, ma20=None, ma60=None),
        macd=MacdIndicators(dif=None, dea=None, macd=None),
        rsi=RsiIndicators(rsi6=None, rsi12=None, rsi24=None),
        boll=BollIndicators(mid=None, upper=None, lower=None),
        kdj=KdjIndicators(k=None, d=None, j=None),
        atr=AtrIndicators(atr14=None),
    )


def _build_missing_items(
    *,
    has_rows: bool,
    has_indicators: bool,
    has_chart: bool,
    p0_p1_success: bool,
) -> list[str]:
    missing: list[str] = []
    if not has_rows:
        missing.append("market_price_history_missing")
    if not p0_p1_success:
        missing.append("market_p0_p1_sources_missing")
    if has_rows and not has_indicators:
        missing.append("market_indicators_missing")
    if has_rows and not has_chart:
        missing.append("market_chart_evidence_gap")
    return missing


def _build_evidence_summary(
    *,
    rows: list[MarketPriceRow],
    accepted_rows: list[Mapping[str, Any]],
    indicators: MarketIndicators | None,
    chart_refs: list[ChartRef],
    attempts: list[ProviderAttempt],
) -> list[str]:
    summary: list[str] = []
    if rows:
        first = rows[0]
        latest = rows[-1]
        summary.append(
            f"行情区间为 {first.trade_date} 至 {latest.trade_date}，共 {len(rows)} 个交易日，复权口径为 {_MARKET_ADJUST}。"
        )
        summary.append(
            "最新交易日 "
            f"{latest.trade_date}：开盘 {_format_number(latest.open)}，最高 {_format_number(latest.high)}，"
            f"最低 {_format_number(latest.low)}，收盘 {_format_number(latest.close)}，"
            f"成交量 {_format_number(latest.volume)}，成交额 {_format_optional_number(latest.amount)}。"
        )
        summary.append(
            _build_latest_change_summary(
                rows=rows,
                accepted_rows=accepted_rows,
            )
        )
        window_high = max(row.high for row in rows)
        window_low = min(row.low for row in rows)
        total_volume = sum(row.volume for row in rows)
        summary.append(
            f"区间最高价 {_format_number(window_high)}，区间最低价 {_format_number(window_low)}，"
            f"区间累计成交量 {_format_number(total_volume)}。"
        )
    if indicators is not None:
        summary.extend(_format_indicator_lines(indicators))
    for attempt in attempts:
        if attempt.status not in {"success", "cache_hit"}:
            continue
        if attempt.accepted_count <= 0:
            continue
        summary.append(
            f"来源 {attempt.provider}/{attempt.endpoint} 提供 {attempt.accepted_count} 条可用行情记录。"
        )
        if len(summary) >= 8:
            break
    if chart_refs:
        kinds = "、".join(_deduplicate_text_refs([ref.kind for ref in chart_refs]))
        summary.append(f"已生成 {len(chart_refs)} 张技术图，图表类型包括 {kinds}；最终报告应复制图像资产后引用。")
    return summary


def _format_indicator_lines(indicators: MarketIndicators) -> list[str]:
    lines: list[str] = []
    ma = indicators.ma
    if any(value is not None for value in (ma.ma5, ma.ma10, ma.ma20, ma.ma60)):
        lines.append(
            "均线指标："
            f"MA5 {_format_optional_number(ma.ma5)}，MA10 {_format_optional_number(ma.ma10)}，"
            f"MA20 {_format_optional_number(ma.ma20)}，MA60 {_format_optional_number(ma.ma60)}。"
        )
    macd = indicators.macd
    if any(value is not None for value in (macd.dif, macd.dea, macd.macd)):
        lines.append(
            "MACD 指标："
            f"DIF {_format_optional_number(macd.dif)}，DEA {_format_optional_number(macd.dea)}，"
            f"MACD {_format_optional_number(macd.macd)}。"
        )
    rsi = indicators.rsi
    if any(value is not None for value in (rsi.rsi6, rsi.rsi12, rsi.rsi24)):
        lines.append(
            "RSI 指标："
            f"RSI6 {_format_optional_number(rsi.rsi6)}，RSI12 {_format_optional_number(rsi.rsi12)}，"
            f"RSI24 {_format_optional_number(rsi.rsi24)}。"
        )
    boll = indicators.boll
    if any(value is not None for value in (boll.mid, boll.upper, boll.lower)):
        lines.append(
            "布林线指标："
            f"中轨 {_format_optional_number(boll.mid)}，上轨 {_format_optional_number(boll.upper)}，"
            f"下轨 {_format_optional_number(boll.lower)}。"
        )
    return lines[:4]


def _format_optional_number(value: float | None) -> str:
    if value is None:
        return "未取得"
    return _format_number(value)


def _format_number(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _build_latest_change_summary(
    *,
    rows: list[MarketPriceRow],
    accepted_rows: list[Mapping[str, Any]],
) -> str:
    latest = rows[-1]
    latest_raw = accepted_rows[-1] if accepted_rows else {}
    pre_close = _to_float_or_none(latest_raw.get("pre_close"))
    change = _to_float_or_none(latest_raw.get("change"))
    pct_chg = _to_float_or_none(latest_raw.get("pct_chg"))

    if change is None and pre_close is not None:
        change = latest.close - pre_close
    if pct_chg is None and change is not None and pre_close not in {None, 0.0}:
        pct_chg = (change / pre_close) * 100.0

    if (change is None or pct_chg is None) and len(rows) >= 2:
        previous_close = rows[-2].close
        if previous_close > 0:
            fallback_change = latest.close - previous_close
            fallback_pct_chg = (fallback_change / previous_close) * 100.0
            if change is None:
                change = fallback_change
            if pct_chg is None:
                pct_chg = fallback_pct_chg
            if pre_close is None:
                pre_close = previous_close

    if change is None or pct_chg is None:
        return "日涨跌说明：涨跌幅字段未提供，且缺少可计算日涨跌所需基准收盘数据；禁止使用占位描述。"

    return (
        "日涨跌说明："
        f"前收 {_format_number(pre_close) if pre_close is not None else '未取得'}，"
        f"日涨跌额 {_format_number(change)}，日涨跌幅 {_format_number(pct_chg)}%。"
    )


def _to_float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if text == "":
        return None
    if text.endswith("%"):
        text = text[:-1].strip()
    if text == "":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _has_p0_p1_success(attempts: list[ProviderAttempt]) -> bool:
    allowed = {
        ("akshare", "stock_zh_a_hist"),
        ("eastmoney_direct", "push2his_kline"),
        ("sina", "stock_zh_a_daily"),
        ("tencent", "stock_zh_a_hist_tx"),
    }
    for attempt in attempts:
        if (attempt.provider, attempt.endpoint) in allowed and attempt.status == "success":
            return True
    return False


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


def _default_market_call_registry() -> dict[tuple[str, str], ProviderCallable]:
    registry: dict[tuple[str, str], ProviderCallable] = {}
    registry.update(AKSHARE_MARKET_CALL_REGISTRY)
    registry.update(EASTMONEY_MARKET_CALL_REGISTRY)
    registry.update(TUSHARE_MARKET_CALL_REGISTRY)
    return registry


def clear_market_chart_references(
    pack: PackEnvelope,
    *,
    cleanup_evidence_ref: EvidenceRef | None = None,
) -> PackEnvelope:
    if pack.domain != "market":
        return pack
    domain_data = dict(pack.domain_data)
    domain_data["chart_refs"] = []
    retained_l2_refs = [ref for ref in pack.openviking_l2_refs if ref.kind != "chart_manifest"]
    if cleanup_evidence_ref is not None:
        retained_l2_refs.append(cleanup_evidence_ref)
    diagnostic_flags = list(pack.diagnostic_flags)
    if "market_chart_refs_cleared" not in diagnostic_flags:
        diagnostic_flags.append("market_chart_refs_cleared")
    return replace(
        pack,
        openviking_l2_refs=retained_l2_refs,
        diagnostic_flags=diagnostic_flags,
        domain_data=domain_data,
    )


__all__ = [
    "BuildMarketDataPack",
    "build_market_data_pack",
    "clear_market_chart_references",
    "run_market_data_pack",
]
