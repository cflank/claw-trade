from __future__ import annotations

import threading
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, TimeoutError as FuturesTimeoutError, wait
from typing import Callable, Mapping, Sequence

from models import ProviderAttempt, ProviderFetchResult, QueryPlan
from observability import record_event, record_metric, trace_span
from providers import (
    AkshareNewsCctvProvider,
    AkshareStockInfoGlobalClsProvider,
    AkshareStockInfoGlobalEmProvider,
    AkshareStockNewsEmProvider,
    NewsProvider,
    ProviderFetchResultFactory,
    TushareAnnouncementsProvider,
    build_provider_query,
)

DEFAULT_MAX_CONCURRENCY = 3
DEFAULT_PER_PROVIDER_TIMEOUT_SECONDS = 10
DEFAULT_TOTAL_TIMEOUT_SECONDS = 20
DEFAULT_FAIL_THRESHOLD = 3
DEFAULT_COOLDOWN_SECONDS = 60

ProviderFactory = Callable[[], NewsProvider]

_DEFAULT_PROVIDER_FACTORIES: dict[str, ProviderFactory] = {
    "akshare.stock_news_em": AkshareStockNewsEmProvider,
    "akshare.stock_info_global_cls": AkshareStockInfoGlobalClsProvider,
    "akshare.stock_info_global_em": AkshareStockInfoGlobalEmProvider,
    "akshare.news_cctv": AkshareNewsCctvProvider,
    "tushare.anns_d": TushareAnnouncementsProvider,
}
provider_fail_counter: dict[tuple[str, str], int] = {}
provider_fail_started_at: dict[tuple[str, str], float] = {}
cooldown_until: dict[tuple[str, str], float] = {}
_cooldown_lock = threading.Lock()


def run_bounded_parallel(
    *,
    query_plan: QueryPlan,
    enabled_providers: Sequence[str],
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
    per_provider_timeout_seconds: int = DEFAULT_PER_PROVIDER_TIMEOUT_SECONDS,
    total_timeout_seconds: int = DEFAULT_TOTAL_TIMEOUT_SECONDS,
    provider_overrides: Mapping[str, NewsProvider] | None = None,
    observability_fields: Mapping[str, object] | None = None,
) -> list[ProviderFetchResult]:
    if max_concurrency <= 0:
        raise ValueError("max_concurrency must be positive")
    if per_provider_timeout_seconds <= 0:
        raise ValueError("per_provider_timeout_seconds must be positive")
    if total_timeout_seconds <= 0:
        raise ValueError("total_timeout_seconds must be positive")
    if not enabled_providers:
        return []

    provider_entries = _resolve_provider_entries(
        enabled_providers=enabled_providers,
        provider_overrides=provider_overrides,
    )
    dispatch_started = time.monotonic()
    deadline = dispatch_started + float(total_timeout_seconds)
    results_by_provider: dict[str, ProviderFetchResult] = {}
    futures: dict[object, tuple[str, NewsProvider, str]] = {}

    with ThreadPoolExecutor(max_workers=min(max_concurrency, len(provider_entries))) as pool:
        for provider_id, provider in provider_entries:
            query = build_provider_query(endpoint=provider.endpoint, query_plan=query_plan)
            query_summary = _build_query_summary(query)
            if _is_under_cooldown(provider=provider.name, endpoint=provider.endpoint):
                result = ProviderFetchResultFactory.empty(
                    provider=provider.name,
                    endpoint=provider.endpoint,
                    query=query_summary,
                    elapsed_ms=0,
                    reason="rate_limited",
                    error="provider_cooldown_active",
                    cancelled=False,
                )
                results_by_provider[provider_id] = result
                _record_provider_attempt_observability(result, observability_fields=observability_fields)
                continue
            future = pool.submit(
                _fetch_provider_with_timeout,
                provider,
                query,
                query_summary,
                per_provider_timeout_seconds,
                observability_fields,
            )
            futures[future] = (provider_id, provider, query_summary)

        pending = set(futures.keys())
        while pending:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break

            done, not_done = wait(pending, timeout=remaining, return_when=FIRST_COMPLETED)
            if not done:
                pending = not_done
                break

            for future in done:
                provider_id, provider, query_summary = futures[future]
                try:
                    result = future.result()
                except Exception as error:
                    result = ProviderFetchResultFactory.from_exception(
                        provider=provider.name,
                        endpoint=provider.endpoint,
                        query=query_summary,
                        elapsed_ms=_elapsed_ms(dispatch_started),
                        error=error,
                        cancelled=False,
                    )
                results_by_provider[provider_id] = result
                _record_provider_attempt_observability(result, observability_fields=observability_fields)
                _update_cooldown_state(result)
            pending = not_done

        if pending:
            timeout_elapsed_ms = _elapsed_ms(dispatch_started)
            for future in pending:
                provider_id, provider, query_summary = futures[future]
                future.cancel()
                result = ProviderFetchResultFactory.timeout_cancelled(
                    provider=provider.name,
                    endpoint=provider.endpoint,
                    query=query_summary,
                    elapsed_ms=timeout_elapsed_ms,
                )
                results_by_provider[provider_id] = result
                _record_provider_attempt_observability(result, observability_fields=observability_fields)
                _update_cooldown_state(result)

    return [results_by_provider[provider_id] for provider_id in enabled_providers]


def ensure_attempts_for_all_enabled_providers(
    *,
    attempts: Sequence[ProviderAttempt],
    provider_results: Sequence[ProviderFetchResult],
    enabled_providers: Sequence[str],
    mark_timeout_cancelled: bool = True,
) -> list[ProviderAttempt]:
    attempts_by_provider: dict[str, ProviderAttempt] = {}

    for attempt in attempts:
        key = _provider_key(provider=attempt.provider, endpoint=attempt.endpoint)
        if key not in attempts_by_provider:
            attempts_by_provider[key] = attempt

    for result in provider_results:
        attempt = result.attempt
        key = _provider_key(provider=attempt.provider, endpoint=attempt.endpoint)
        if key not in attempts_by_provider:
            attempts_by_provider[key] = attempt

    ensured_attempts: list[ProviderAttempt] = []
    for provider_id in enabled_providers:
        existing_attempt = attempts_by_provider.get(provider_id)
        if existing_attempt is not None:
            ensured_attempts.append(existing_attempt)
            continue

        provider_name, endpoint = _split_provider_id(provider_id)
        ensured_attempts.append(
            ProviderAttempt(
                provider=provider_name,
                endpoint=endpoint,
                query=f"provider={provider_name};endpoint={endpoint};reason=missing_attempt",
                ok=False,
                elapsed_ms=0,
                raw_count=0,
                accepted_count=0,
                empty_reason="timeout",
                error="missing_attempt",
                cancelled=mark_timeout_cancelled,
            )
        )

    return ensured_attempts


def _resolve_provider_entries(
    *,
    enabled_providers: Sequence[str],
    provider_overrides: Mapping[str, NewsProvider] | None,
) -> list[tuple[str, NewsProvider]]:
    entries: list[tuple[str, NewsProvider]] = []
    for provider_id in enabled_providers:
        if provider_overrides is not None and provider_id in provider_overrides:
            entries.append((provider_id, provider_overrides[provider_id]))
            continue

        factory = _DEFAULT_PROVIDER_FACTORIES.get(provider_id)
        if factory is None:
            raise ValueError(f"unsupported provider endpoint: {provider_id}")
        entries.append((provider_id, factory()))
    return entries


def _fetch_provider_with_timeout(
    provider: NewsProvider,
    query,
    query_summary: str,
    per_provider_timeout_seconds: int,
    observability_fields: Mapping[str, object] | None = None,
) -> ProviderFetchResult:
    started = time.monotonic()
    nested_pool = ThreadPoolExecutor(max_workers=1)
    future = nested_pool.submit(provider.fetch, query)
    span_fields = dict(observability_fields or {})
    span_fields.update({"provider": provider.name, "endpoint": provider.endpoint})
    try:
        with trace_span(f"news_data_pack.provider.{provider.endpoint}", fields=span_fields):
            try:
                return future.result(timeout=float(per_provider_timeout_seconds))
            except FuturesTimeoutError:
                return ProviderFetchResultFactory.empty(
                    provider=provider.name,
                    endpoint=provider.endpoint,
                    query=query_summary,
                    elapsed_ms=max(_elapsed_ms(started), per_provider_timeout_seconds * 1000),
                    reason="timeout",
                    error="provider_timeout",
                    cancelled=False,
                )
            except Exception as error:
                return ProviderFetchResultFactory.from_exception(
                    provider=provider.name,
                    endpoint=provider.endpoint,
                    query=query_summary,
                    elapsed_ms=_elapsed_ms(started),
                    error=error,
                    cancelled=False,
                )
    finally:
        nested_pool.shutdown(wait=False, cancel_futures=True)


def _build_query_summary(query) -> str:
    return (
        f"ticker={query.ticker};exchange_ticker={query.exchange_ticker};"
        f"start_date={query.start_date};end_date={query.end_date}"
    )


def _is_under_cooldown(*, provider: str, endpoint: str) -> bool:
    key = (provider, endpoint)
    now = time.monotonic()
    with _cooldown_lock:
        cooling_until = cooldown_until.get(key)
        if cooling_until is None:
            return False
        if now < cooling_until:
            return True

        cooldown_until.pop(key, None)
        provider_fail_counter.pop(key, None)
        provider_fail_started_at.pop(key, None)
        return False


def _update_cooldown_state(result: ProviderFetchResult) -> None:
    attempt = result.attempt
    key = (attempt.provider, attempt.endpoint)
    now = time.monotonic()

    with _cooldown_lock:
        if result.ok:
            provider_fail_counter.pop(key, None)
            provider_fail_started_at.pop(key, None)
            cooldown_until.pop(key, None)
            return

        started_at = provider_fail_started_at.get(key)
        if started_at is None or now - started_at > DEFAULT_COOLDOWN_SECONDS:
            provider_fail_started_at[key] = now
            provider_fail_counter[key] = 1
        else:
            provider_fail_counter[key] = provider_fail_counter.get(key, 0) + 1

        if provider_fail_counter[key] >= DEFAULT_FAIL_THRESHOLD:
            cooldown_until[key] = now + float(DEFAULT_COOLDOWN_SECONDS)


def _provider_key(*, provider: str, endpoint: str) -> str:
    return f"{provider}.{endpoint}"


def _split_provider_id(provider_id: str) -> tuple[str, str]:
    provider_name, separator, endpoint = provider_id.partition(".")
    if separator == "" or endpoint.strip() == "":
        raise ValueError(f"invalid provider endpoint: {provider_id}")
    return provider_name, endpoint


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


def _record_provider_attempt_observability(
    result: ProviderFetchResult,
    *,
    observability_fields: Mapping[str, object] | None,
) -> None:
    attempt = result.attempt
    status = "success" if attempt.ok else "failed"
    empty_reason = attempt.empty_reason or "none"
    labels = {
        "provider": attempt.provider,
        "endpoint": attempt.endpoint,
        "status": status,
        "empty_reason": empty_reason,
    }
    fields = dict(observability_fields or {})
    fields.update(
        {
            "provider": attempt.provider,
            "endpoint": attempt.endpoint,
            "status": status,
            "count": attempt.raw_count,
            "error_code": empty_reason if empty_reason != "none" else None,
        }
    )
    record_metric("news_provider_attempt_total", labels=labels, value=1)
    record_metric(
        "news_provider_elapsed_ms_bucket",
        labels={"provider": attempt.provider, "endpoint": attempt.endpoint},
        value=attempt.elapsed_ms,
    )
    record_event("news_provider_attempt", fields=fields)
