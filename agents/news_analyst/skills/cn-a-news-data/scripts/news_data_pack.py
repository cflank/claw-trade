from __future__ import annotations

import hashlib
import json
import re
import sys
from collections import defaultdict
from dataclasses import fields, is_dataclass
from datetime import date, datetime, timedelta
from typing import Any, Mapping

from config_loader import load_cn_a_news_config, load_keyword_rules
from dedup import Deduplicator, SortTrimProcessor
from errors import (
    E_CONTEXT_MISMATCH,
    E_EVIDENCE_WRITE_FAILED,
    E_INVALID_INPUT,
    E_PROFILE_RESOLVE_FAILED,
    E_PROVIDER_LAYER_FAILED,
    E_UNSUPPORTED_MARKET,
    KNOWN_ERROR_CODES,
    NewsDataError,
)
from evidence import EvidenceWriter
from keyword_observations import KeywordObservationExtractor
from matching import MatchEngine, build_query_plan
from models import (
    BriefInput,
    EvidenceWriteRequest,
    KeywordObservations,
    NewsDataPack,
    NewsDataPackRequest,
    NewsItem,
    ProviderAttempt,
    Quality,
    QueryPlan,
    ResolvedProfile,
    ToolInput,
    ToolRuntimeContext,
    QualityInput,
    RawNewsItem,
)
from observability import bind_observability_context, record_metric, trace_span
from provider_scheduler import ensure_attempts_for_all_enabled_providers, run_bounded_parallel
from profile_resolver import ApprovedProfileResolver
from providers import NewsProvider
from quality import QualityGate
from reader_brief import ReaderBriefBuilder
from security import sanitize_error

TOOL_NAME = "news_news_data_pack"
TOOL_WORKER_ID = "news_analyst"
SCHEMA_VERSION = "cn_a_news_pack.v1"
TOOL_ADAPTER_TIMEOUT_SECONDS = 25
SERVICE_TOTAL_TIMEOUT_SECONDS = 20

_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TICKER_PATTERN = re.compile(r"^\d{6}$")
_EXCHANGE_TICKER_PATTERN = re.compile(r"^(?P<ticker>\d{6})\.(?P<exchange>SH|SZ)$", re.IGNORECASE)
_P0_DATA_SOURCES: frozenset[str] = frozenset({"akshare.stock_news_em", "akshare.stock_info_global_cls"})
_REQUIRED_BUCKETS: tuple[str, ...] = ("company_news", "industry_news", "policy_macro_news", "announcements")
_EXIT_SUCCESS = 0
_EXIT_FAILED = 1
_EXIT_PROTOCOL_ERROR = 2


class NewsDataService:
    def __init__(self, *, provider_overrides: Mapping[str, NewsProvider] | None = None) -> None:
        self._provider_overrides = provider_overrides
        self._match_engine = MatchEngine()
        self._keyword_extractor = KeywordObservationExtractor()
        self._quality_gate = QualityGate()
        self._brief_builder = ReaderBriefBuilder()

    def build_pack(self, request: NewsDataPackRequest) -> NewsDataPack:
        with trace_span("news_data_pack.service"):
            _validate_request(request)
            try:
                config = load_cn_a_news_config()
                query_plan = build_query_plan(request)
                provider_results = run_bounded_parallel(
                    query_plan=query_plan,
                    enabled_providers=config.enabled_providers,
                    max_concurrency=config.max_concurrency,
                    per_provider_timeout_seconds=config.timeout_seconds,
                    total_timeout_seconds=config.total_timeout_seconds,
                    provider_overrides=self._provider_overrides,
                )
                attempts = ensure_attempts_for_all_enabled_providers(
                    attempts=[result.attempt for result in provider_results],
                    provider_results=provider_results,
                    enabled_providers=config.enabled_providers,
                    mark_timeout_cancelled=True,
                )

                raw_items: list[RawNewsItem] = []
                for result in provider_results:
                    if result.ok:
                        raw_items.extend(result.raw_items)
                record_metric("news_pack_provider_raw_count", labels=None, value=len(raw_items))

                keyword_rules = load_keyword_rules(config.keyword_rules_path)
                accepted_items: list[NewsItem] = []
                accepted_counts_by_provider: dict[str, int] = defaultdict(int)
                rejected_count = 0
                for raw_item in raw_items:
                    match_result = self._match_engine.classify(raw_item, query_plan)
                    if match_result.bucket == "rejected":
                        rejected_count += 1
                        continue
                    if match_result.match_evidence_span is None or match_result.match_evidence_span.strip() == "":
                        raise NewsDataError(
                            code=E_PROVIDER_LAYER_FAILED,
                            message="match_evidence_span is empty for accepted item",
                            details={"raw_id": raw_item.raw_id, "bucket": match_result.bucket},
                        )
                    observation_result = self._keyword_extractor.extract(raw_item, keyword_rules)
                    accepted_items.append(
                        NewsItem(
                            news_id=raw_item.raw_id,
                            title=raw_item.title,
                            summary=raw_item.summary,
                            source=raw_item.source,
                            publish_time=raw_item.publish_time,
                            url=raw_item.url,
                            data_source=raw_item.data_source,
                            matched_keywords=match_result.matched_keywords,
                            match_type=match_result.match_type,
                            match_evidence_span=match_result.match_evidence_span,
                            match_confidence=match_result.match_confidence,
                            bucket=match_result.bucket,
                            keyword_observations=KeywordObservations(
                                matched_terms=observation_result.matched_terms,
                                keyword_categories=observation_result.keyword_categories,
                                method=observation_result.method,
                                is_sentiment_judgment=observation_result.is_sentiment_judgment,
                            ),
                            source_fetch_time=raw_item.source_fetch_time,
                            content_hash=_compute_content_hash(raw_item),
                            is_primary_source=raw_item.data_source in _P0_DATA_SOURCES,
                            merged_from=[],
                            evidence_gap=None,
                        )
                    )
                    accepted_counts_by_provider[raw_item.data_source] += 1

                attempts = _rebuild_attempts_with_accepted_counts(attempts, accepted_counts_by_provider)
                deduped_items, _ = Deduplicator(
                    title_similarity_threshold=config.title_similarity_threshold
                ).deduplicate(accepted_items)
                trimmed_items = SortTrimProcessor(
                    max_json_items=config.max_json_items,
                    max_brief_items=config.max_brief_items,
                ).sort_and_trim(deduped_items, request)
                quality_decision = self._quality_gate.evaluate(
                    QualityInput(
                        provider_attempts=attempts,
                        items=trimmed_items,
                        total_raw_count=len(raw_items),
                        after_dedup_count=len(deduped_items),
                        rejected_count=rejected_count,
                        request=request,
                    )
                )
                reader_brief = self._brief_builder.build(
                    BriefInput(
                        quality=quality_decision.quality,
                        provider_attempts=attempts,
                        items=trimmed_items,
                        query_plan=query_plan,
                    )
                )
                return NewsDataPack(
                    ok=quality_decision.quality.status != "failed",
                    profile={
                        "company_name": request.company_name,
                        "industry": request.industry,
                    },
                    query_plan=query_plan,
                    provider_attempts=attempts,
                    data=_group_items_by_bucket(trimmed_items),
                    quality=quality_decision.quality,
                    reader_brief=reader_brief,
                )
            except NewsDataError:
                raise
            except Exception as error:
                raise NewsDataError(
                    code=E_PROVIDER_LAYER_FAILED,
                    message="news data pack orchestration failed",
                    details={"error": sanitize_error(error)},
                ) from error


def _validate_request(request: NewsDataPackRequest) -> None:
    if request.market != "CN_A":
        raise NewsDataError(code=E_INVALID_INPUT, message="market must be CN_A")
    start_date = _parse_request_date_or_raise(request.start_date, "start_date")
    end_date = _parse_request_date_or_raise(request.end_date, "end_date")
    if start_date > end_date:
        raise NewsDataError(code=E_INVALID_INPUT, message="start_date must be <= end_date")


def _parse_request_date_or_raise(value: str, field_name: str) -> datetime:
    if not isinstance(value, str) or not _DATE_PATTERN.fullmatch(value):
        raise NewsDataError(code=E_INVALID_INPUT, message=f"{field_name} must be YYYY-MM-DD")
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except ValueError as error:
        raise NewsDataError(code=E_INVALID_INPUT, message=f"{field_name} must be YYYY-MM-DD") from error


def _compute_content_hash(item: RawNewsItem) -> str:
    key = "|".join(
        [
            item.title.strip(),
            (item.summary or "").strip(),
            (item.source or "").strip(),
            (item.publish_time or "").strip(),
            (item.url or "").strip(),
            item.data_source.strip(),
        ]
    )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _group_items_by_bucket(items: list[NewsItem]) -> dict[str, list[NewsItem]]:
    grouped: dict[str, list[NewsItem]] = {bucket: [] for bucket in _REQUIRED_BUCKETS}
    for item in items:
        if item.bucket not in grouped:
            grouped[item.bucket] = []
        grouped[item.bucket].append(item)
    return grouped


def _rebuild_attempts_with_accepted_counts(
    attempts: list[ProviderAttempt],
    accepted_counts_by_provider: Mapping[str, int],
) -> list[ProviderAttempt]:
    rebuilt: list[ProviderAttempt] = []
    for attempt in attempts:
        provider_id = f"{attempt.provider}.{attempt.endpoint}"
        rebuilt.append(
            ProviderAttempt(
                provider=attempt.provider,
                endpoint=attempt.endpoint,
                query=attempt.query,
                ok=attempt.ok,
                elapsed_ms=attempt.elapsed_ms,
                raw_count=attempt.raw_count,
                accepted_count=accepted_counts_by_provider.get(provider_id, attempt.accepted_count),
                empty_reason=attempt.empty_reason,
                error=attempt.error,
                cancelled=attempt.cancelled,
            )
        )
    return rebuilt


def run_news_data_pack(tool_input: ToolInput | Mapping[str, Any] | Any, context: ToolRuntimeContext | Mapping[str, Any] | Any) -> dict[str, Any]:
    runtime_context = _coerce_tool_runtime_context(context)
    _validate_runtime_context(runtime_context)

    observability_fields = {
        "run_id": runtime_context.run_id,
        "stage": runtime_context.stage,
        "worker_id": runtime_context.worker_id,
        "call_id": runtime_context.call_id,
        "tool_name": runtime_context.tool_name,
    }
    with bind_observability_context(observability_fields):
        with trace_span("news_data_pack.tool_adapter", fields=observability_fields):
            try:
                normalized_tool_input = _coerce_tool_input(tool_input)
                request = normalize_tool_input(
                    tool_input=normalized_tool_input,
                    context=runtime_context,
                    resolver=ApprovedProfileResolver(),
                )
                pack = NewsDataService().build_pack(request)
                _write_evidence_or_raise(runtime_context, pack, provider_raw_refs=[])
                return pack.to_dict()
            except NewsDataError as error:
                if error.code in {E_CONTEXT_MISMATCH, E_EVIDENCE_WRITE_FAILED}:
                    raise
                failed_pack = _build_failed_pack(
                    code=error.code,
                    message=error.message,
                    tool_input=tool_input,
                    request=locals().get("request"),
                )
                _write_evidence_or_raise(runtime_context, failed_pack, provider_raw_refs=[])
                return failed_pack.to_dict()


def normalize_tool_input(
    *,
    tool_input: ToolInput,
    context: ToolRuntimeContext,
    resolver: ApprovedProfileResolver,
) -> NewsDataPackRequest:
    if tool_input.market != "CN_A":
        raise NewsDataError(
            code=E_UNSUPPORTED_MARKET,
            message="market must be CN_A",
            details={"market": tool_input.market},
        )

    ticker, exchange_ticker = _normalize_ticker(tool_input.ticker)
    start_date, end_date = _normalize_date_window(
        start_date=tool_input.start_date,
        end_date=tool_input.end_date,
    )
    resolved_profile = _resolve_profile(tool_input=tool_input, context=context, ticker=ticker, resolver=resolver)

    return NewsDataPackRequest(
        ticker=ticker,
        exchange_ticker=exchange_ticker,
        market="CN_A",
        company_name=resolved_profile.company_name,
        industry=resolved_profile.industry,
        start_date=start_date,
        end_date=end_date,
        approved_aliases=resolved_profile.approved_aliases,
        approved_historical_names=resolved_profile.approved_historical_names,
        profile_missing_fields=resolved_profile.missing_fields,
    )


def _resolve_profile(
    *,
    tool_input: ToolInput,
    context: ToolRuntimeContext,
    ticker: str,
    resolver: ApprovedProfileResolver,
) -> ResolvedProfile:
    try:
        return resolver.resolve(
            ticker=ticker,
            run_id=context.run_id,
            stage=context.stage,
            profile_ref=tool_input.profile_artifact_ref,
            fundamentals_ref=tool_input.fundamentals_artifact_ref,
            market_ref=tool_input.market_artifact_ref,
        )
    except NewsDataError:
        raise
    except Exception as exc:
        raise NewsDataError(
            code=E_PROFILE_RESOLVE_FAILED,
            message="profile resolve failed",
            details={"error": sanitize_error(exc)},
        ) from exc


def _coerce_tool_runtime_context(context: ToolRuntimeContext | Mapping[str, Any] | Any) -> ToolRuntimeContext:
    if isinstance(context, ToolRuntimeContext):
        return context
    try:
        payload = _to_payload(context, "context")
    except NewsDataError as exc:
        raise NewsDataError(
            code=E_CONTEXT_MISMATCH,
            message="invalid tool runtime context",
            details={"error": sanitize_error(exc)},
        ) from exc
    try:
        return ToolRuntimeContext(**payload)
    except Exception as exc:
        raise NewsDataError(
            code=E_CONTEXT_MISMATCH,
            message="invalid tool runtime context",
            details={"error": sanitize_error(exc)},
        ) from exc


def _coerce_tool_input(tool_input: ToolInput | Mapping[str, Any] | Any) -> ToolInput:
    if isinstance(tool_input, ToolInput):
        return tool_input
    payload = _to_payload(tool_input, "tool_input")
    try:
        return ToolInput(**payload)
    except Exception as exc:
        raise NewsDataError(
            code=E_INVALID_INPUT,
            message="invalid tool input",
            details={"error": sanitize_error(exc)},
        ) from exc


def _to_payload(value: Mapping[str, Any] | Any, field_name: str) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if is_dataclass(value) and not isinstance(value, type):
        return {item_field.name: getattr(value, item_field.name) for item_field in fields(value)}
    raise NewsDataError(
        code=E_INVALID_INPUT,
        message=f"{field_name} must be dict or dataclass",
    )


def _validate_runtime_context(context: ToolRuntimeContext) -> None:
    if context.worker_id != TOOL_WORKER_ID:
        raise NewsDataError(
            code=E_CONTEXT_MISMATCH,
            message="worker_id mismatch",
            details={"expected": TOOL_WORKER_ID, "actual": context.worker_id},
        )
    if context.tool_name != TOOL_NAME:
        raise NewsDataError(
            code=E_CONTEXT_MISMATCH,
            message="tool_name mismatch",
            details={"expected": TOOL_NAME, "actual": context.tool_name},
        )


def _normalize_ticker(raw_ticker: str) -> tuple[str, str]:
    ticker_text = raw_ticker.strip().upper()
    exchange_match = _EXCHANGE_TICKER_PATTERN.fullmatch(ticker_text)
    if exchange_match is not None:
        ticker = exchange_match.group("ticker")
        exchange = exchange_match.group("exchange").upper()
        return ticker, f"{ticker}.{exchange}"

    if _TICKER_PATTERN.fullmatch(ticker_text) is None:
        raise NewsDataError(
            code=E_INVALID_INPUT,
            message="ticker must be six digits or with .SH/.SZ suffix",
            details={"ticker": raw_ticker},
        )
    return ticker_text, f"{ticker_text}.{_infer_exchange_suffix(ticker_text)}"


def _infer_exchange_suffix(ticker: str) -> str:
    if ticker.startswith(("5", "6", "9")):
        return "SH"
    return "SZ"


def _normalize_date_window(*, start_date: str | None, end_date: str | None) -> tuple[str, str]:
    resolved_end = _normalize_date("end_date", end_date) if end_date is not None else datetime.now().date()
    resolved_start = (
        _normalize_date("start_date", start_date) if start_date is not None else resolved_end - timedelta(days=6)
    )
    if resolved_start > resolved_end:
        raise NewsDataError(code=E_INVALID_INPUT, message="start_date must be <= end_date")
    return resolved_start.isoformat(), resolved_end.isoformat()


def _normalize_date(field_name: str, raw_value: str) -> date:
    if _DATE_PATTERN.fullmatch(raw_value.strip()) is None:
        raise NewsDataError(
            code=E_INVALID_INPUT,
            message=f"{field_name} must be YYYY-MM-DD",
            details={field_name: raw_value},
        )
    try:
        return datetime.strptime(raw_value.strip(), "%Y-%m-%d").date()
    except ValueError as exc:
        raise NewsDataError(
            code=E_INVALID_INPUT,
            message=f"{field_name} must be YYYY-MM-DD",
            details={field_name: raw_value},
        ) from exc


def _build_failed_pack(
    *,
    code: str,
    message: str,
    tool_input: ToolInput | Mapping[str, Any] | Any,
    request: NewsDataPackRequest | None,
) -> NewsDataPack:
    ticker, exchange_ticker = _best_effort_ticker_fields(tool_input=tool_input, request=request)
    start_date, end_date = _best_effort_date_fields(tool_input=tool_input, request=request)

    profile_company_name = request.company_name if request is not None else None
    profile_industry = request.industry if request is not None else None
    missing_fields = request.profile_missing_fields if request is not None else []

    query_plan = QueryPlan(
        ticker=ticker,
        exchange_ticker=exchange_ticker,
        company_name=profile_company_name,
        industry=profile_industry,
        start_date=start_date,
        end_date=end_date,
        company_keywords=[ticker, exchange_ticker],
        industry_keywords=[profile_industry] if profile_industry else [],
        macro_keywords=[],
    )
    quality = Quality(
        status="failed",
        company_direct_news_count=0,
        industry_background_count=0,
        policy_macro_count=0,
        total_raw_count=0,
        after_dedup_count=0,
        accepted_count=0,
        missing_fields=missing_fields,
        directional_judgment_allowed=False,
        warnings=[f"{code}: {message}"],
    )
    return NewsDataPack(
        ok=False,
        profile={"company_name": profile_company_name, "industry": profile_industry},
        query_plan=query_plan,
        provider_attempts=[],
        data={bucket: [] for bucket in _REQUIRED_BUCKETS},
        quality=quality,
        reader_brief=f"资料包失败：{code}。{message}",
    )


def _best_effort_ticker_fields(
    *,
    tool_input: ToolInput | Mapping[str, Any] | Any,
    request: NewsDataPackRequest | None,
) -> tuple[str, str]:
    if request is not None:
        return request.ticker, request.exchange_ticker

    raw_ticker = _extract_optional_str(tool_input, "ticker")
    if raw_ticker is not None:
        normalized = raw_ticker.strip().upper()
        exchange_match = _EXCHANGE_TICKER_PATTERN.fullmatch(normalized)
        if exchange_match is not None:
            ticker = exchange_match.group("ticker")
            exchange = exchange_match.group("exchange").upper()
            return ticker, f"{ticker}.{exchange}"
        if _TICKER_PATTERN.fullmatch(normalized):
            return normalized, f"{normalized}.{_infer_exchange_suffix(normalized)}"
    return "000000", "000000.SH"


def _best_effort_date_fields(
    *,
    tool_input: ToolInput | Mapping[str, Any] | Any,
    request: NewsDataPackRequest | None,
) -> tuple[str, str]:
    if request is not None:
        return request.start_date, request.end_date
    default_end = datetime.now().date()
    default_start = default_end - timedelta(days=6)

    raw_end = _extract_optional_str(tool_input, "end_date")
    raw_start = _extract_optional_str(tool_input, "start_date")
    end_date = _try_parse_optional_date(raw_end) or default_end
    start_date = _try_parse_optional_date(raw_start) or (end_date - timedelta(days=6))
    if start_date > end_date:
        return default_start.isoformat(), default_end.isoformat()
    return start_date.isoformat(), end_date.isoformat()


def _extract_optional_str(source: ToolInput | Mapping[str, Any] | Any, key: str) -> str | None:
    if isinstance(source, ToolInput):
        value = getattr(source, key, None)
    elif isinstance(source, Mapping):
        value = source.get(key)
    elif is_dataclass(source) and not isinstance(source, type):
        value = getattr(source, key, None)
    else:
        value = None
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if normalized == "":
        return None
    return normalized


def _try_parse_optional_date(value: str | None) -> date | None:
    if value is None or _DATE_PATTERN.fullmatch(value) is None:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        return None


def _write_evidence_or_raise(
    context: ToolRuntimeContext,
    pack: NewsDataPack,
    *,
    provider_raw_refs: list[str],
) -> None:
    evidence_refs = EvidenceWriter().write_pack(
        EvidenceWriteRequest(
            context=context,
            pack=pack,
            provider_raw_refs=provider_raw_refs,
        )
    )
    pack.evidence = {
        "pack_path": evidence_refs.pack_path,
        "provider_attempts_path": evidence_refs.provider_attempts_path,
        "provider_raw_paths": evidence_refs.provider_raw_paths,
        "content_hash": evidence_refs.content_hash,
    }


def _parse_stdin_payload(stdin_text: str) -> dict[str, Any]:
    try:
        payload = json.loads(stdin_text)
    except json.JSONDecodeError as exc:
        raise NewsDataError(code=E_INVALID_INPUT, message="stdin must be valid JSON object") from exc
    if not isinstance(payload, dict):
        raise NewsDataError(code=E_INVALID_INPUT, message="stdin JSON must be an object")
    if "tool_input" not in payload or "runtime_context" not in payload:
        raise NewsDataError(
            code=E_INVALID_INPUT,
            message="stdin JSON must include tool_input and runtime_context",
        )
    return payload


def _build_runtime_error_response(error: NewsDataError) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "failed",
        "error": error.to_dict(),
        "quality": {
            "status": "failed",
            "missing_fields": [],
        },
        "evidence_gap": "runtime_error",
    }


def _extract_failure_code(response: Mapping[str, Any]) -> str | None:
    quality = response.get("quality")
    if not isinstance(quality, Mapping):
        return None
    warnings = quality.get("warnings")
    if not isinstance(warnings, list) or not warnings:
        return None
    first_warning = warnings[0]
    if not isinstance(first_warning, str):
        return None
    maybe_code = first_warning.split(":", 1)[0].strip()
    if maybe_code in KNOWN_ERROR_CODES:
        return maybe_code
    return None


def _write_stdout_json(payload: Mapping[str, Any]) -> None:
    sys.stdout.write(json.dumps(dict(payload), ensure_ascii=False))
    sys.stdout.write("\n")


def _write_stderr_short(message: str) -> None:
    sys.stderr.write(f"news_data_pack failed: {sanitize_error(message)}\n")


def main() -> int:
    try:
        payload = _parse_stdin_payload(sys.stdin.read())
        response = run_news_data_pack(
            tool_input=payload["tool_input"],
            context=payload["runtime_context"],
        )
        _write_stdout_json(response)
        if bool(response.get("ok")):
            return _EXIT_SUCCESS
        failure_code = _extract_failure_code(response)
        if failure_code is not None:
            _write_stderr_short(f"{failure_code} business failure")
        else:
            _write_stderr_short("business failure")
        return _EXIT_FAILED
    except NewsDataError as error:
        response = _build_runtime_error_response(error)
        _write_stdout_json(response)
        _write_stderr_short(f"{error.code}: {error.message}")
        return _EXIT_FAILED
    except Exception as error:
        response = _build_runtime_error_response(
            NewsDataError(
                code=E_INVALID_INPUT,
                message="invalid stdin payload",
                details={"error": sanitize_error(error)},
            )
        )
        _write_stdout_json(response)
        _write_stderr_short(str(error))
        return _EXIT_PROTOCOL_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
