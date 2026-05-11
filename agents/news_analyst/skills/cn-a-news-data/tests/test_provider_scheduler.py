from __future__ import annotations

from pathlib import Path
import sys
import threading
import time
from dataclasses import dataclass
from typing import Literal

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from models import ProviderQuery, QueryPlan  # noqa: E402
from provider_scheduler import (  # noqa: E402
    cooldown_until,
    ensure_attempts_for_all_enabled_providers,
    provider_fail_counter,
    provider_fail_started_at,
    run_bounded_parallel,
)
from providers import NewsProvider, build_raw_news_item  # noqa: E402


class _ConcurrencyTracker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.current = 0
        self.max_seen = 0

    def enter(self) -> None:
        with self._lock:
            self.current += 1
            if self.current > self.max_seen:
                self.max_seen = self.current

    def leave(self) -> None:
        with self._lock:
            self.current -= 1


@dataclass
class _ProviderBehavior:
    delay_seconds: float
    mode: Literal["success", "error", "empty"]


class _ControlledProvider(NewsProvider):
    priority = "P0"

    def __init__(
        self,
        *,
        provider_name: str,
        endpoint: str,
        behavior: _ProviderBehavior,
        tracker: _ConcurrencyTracker | None = None,
    ) -> None:
        self.name = provider_name
        self.endpoint = endpoint
        self._behavior = behavior
        self._tracker = tracker
        self.calls = 0

    def _call_remote(self, query: ProviderQuery) -> object:
        self.calls += 1
        if self._tracker is not None:
            self._tracker.enter()
        try:
            if self._behavior.delay_seconds > 0:
                time.sleep(self._behavior.delay_seconds)
            if self._behavior.mode == "error":
                raise RuntimeError(f"{self.endpoint} raised controlled error")
            if self._behavior.mode == "empty":
                return []
            return [
                {
                    "title": f"{query.ticker}-{self.endpoint}",
                    "summary": "news",
                    "source": "test",
                    "publish_time": "2026-05-07 09:00:00",
                    "url": "https://example.com/news",
                }
            ]
        finally:
            if self._tracker is not None:
                self._tracker.leave()

    def _normalize_rows(self, raw_table: object, query: ProviderQuery):
        rows = list(raw_table)
        fetch_time = self.default_source_fetch_time()
        return [
            build_raw_news_item(
                endpoint=query.endpoint,
                raw_id=None,
                title=row["title"],
                summary=row["summary"],
                source=row["source"],
                publish_time=row["publish_time"],
                url=row["url"],
                data_source=f"{self.name}.{self.endpoint}",
                raw_payload_ref=None,
                source_fetch_time=fetch_time,
            )
            for row in rows
        ]


def _build_plan() -> QueryPlan:
    return QueryPlan(
        ticker="600519",
        exchange_ticker="600519.SH",
        company_name="贵州茅台",
        industry="白酒",
        start_date="2026-05-01",
        end_date="2026-05-07",
        company_keywords=["600519", "贵州茅台"],
        industry_keywords=["白酒"],
        macro_keywords=["消费"],
    )


@pytest.fixture(autouse=True)
def _reset_scheduler_state() -> None:
    provider_fail_counter.clear()
    provider_fail_started_at.clear()
    cooldown_until.clear()


def test_scheduler_calls_all_enabled_providers_and_ensures_attempts() -> None:
    enabled_provider_ids = [
        "akshare.stock_news_em",
        "akshare.stock_info_global_cls",
        "akshare.stock_info_global_em",
        "akshare.news_cctv",
        "tushare.anns_d",
    ]
    provider_overrides = {
        "akshare.stock_news_em": _ControlledProvider(
            provider_name="akshare",
            endpoint="stock_news_em",
            behavior=_ProviderBehavior(delay_seconds=0.01, mode="success"),
        ),
        "akshare.stock_info_global_cls": _ControlledProvider(
            provider_name="akshare",
            endpoint="stock_info_global_cls",
            behavior=_ProviderBehavior(delay_seconds=0.01, mode="error"),
        ),
        "akshare.stock_info_global_em": _ControlledProvider(
            provider_name="akshare",
            endpoint="stock_info_global_em",
            behavior=_ProviderBehavior(delay_seconds=0.01, mode="empty"),
        ),
        "akshare.news_cctv": _ControlledProvider(
            provider_name="akshare",
            endpoint="news_cctv",
            behavior=_ProviderBehavior(delay_seconds=0.01, mode="success"),
        ),
        "tushare.anns_d": _ControlledProvider(
            provider_name="tushare",
            endpoint="anns_d",
            behavior=_ProviderBehavior(delay_seconds=0.01, mode="success"),
        ),
    }

    results = run_bounded_parallel(
        query_plan=_build_plan(),
        enabled_providers=enabled_provider_ids,
        max_concurrency=3,
        per_provider_timeout_seconds=10,
        total_timeout_seconds=20,
        provider_overrides=provider_overrides,
    )
    attempts = ensure_attempts_for_all_enabled_providers(
        attempts=[],
        provider_results=results,
        enabled_providers=enabled_provider_ids,
        mark_timeout_cancelled=True,
    )

    assert len(results) == 5
    assert len(attempts) == 5
    assert [f"{attempt.provider}.{attempt.endpoint}" for attempt in attempts] == enabled_provider_ids
    assert all(provider.calls == 1 for provider in provider_overrides.values())


def test_scheduler_limits_concurrency_to_configured_maximum() -> None:
    tracker = _ConcurrencyTracker()
    enabled_provider_ids = [
        "akshare.stock_news_em",
        "akshare.stock_info_global_cls",
        "akshare.stock_info_global_em",
        "akshare.news_cctv",
        "tushare.anns_d",
    ]
    provider_overrides = {
        provider_id: _ControlledProvider(
            provider_name=provider_id.split(".", maxsplit=1)[0],
            endpoint=provider_id.split(".", maxsplit=1)[1],
            behavior=_ProviderBehavior(delay_seconds=0.05, mode="success"),
            tracker=tracker,
        )
        for provider_id in enabled_provider_ids
    }

    _ = run_bounded_parallel(
        query_plan=_build_plan(),
        enabled_providers=enabled_provider_ids,
        max_concurrency=3,
        per_provider_timeout_seconds=10,
        total_timeout_seconds=20,
        provider_overrides=provider_overrides,
    )

    assert tracker.max_seen <= 3


def test_scheduler_marks_total_timeout_unfinished_providers_as_cancelled() -> None:
    enabled_provider_ids = [
        "akshare.stock_news_em",
        "akshare.stock_info_global_cls",
        "akshare.stock_info_global_em",
    ]
    provider_overrides = {
        "akshare.stock_news_em": _ControlledProvider(
            provider_name="akshare",
            endpoint="stock_news_em",
            behavior=_ProviderBehavior(delay_seconds=0.02, mode="success"),
        ),
        "akshare.stock_info_global_cls": _ControlledProvider(
            provider_name="akshare",
            endpoint="stock_info_global_cls",
            behavior=_ProviderBehavior(delay_seconds=0.6, mode="success"),
        ),
        "akshare.stock_info_global_em": _ControlledProvider(
            provider_name="akshare",
            endpoint="stock_info_global_em",
            behavior=_ProviderBehavior(delay_seconds=0.6, mode="success"),
        ),
    }

    results = run_bounded_parallel(
        query_plan=_build_plan(),
        enabled_providers=enabled_provider_ids,
        max_concurrency=3,
        per_provider_timeout_seconds=10,
        total_timeout_seconds=0.1,
        provider_overrides=provider_overrides,
    )
    attempts = ensure_attempts_for_all_enabled_providers(
        attempts=[],
        provider_results=results,
        enabled_providers=enabled_provider_ids,
        mark_timeout_cancelled=True,
    )
    timeout_cancelled_attempts = [
        attempt for attempt in attempts if attempt.empty_reason == "timeout" and attempt.cancelled is True
    ]

    assert timeout_cancelled_attempts


def test_scheduler_returns_success_and_failure_results_together() -> None:
    enabled_provider_ids = [
        "akshare.stock_news_em",
        "akshare.stock_info_global_cls",
    ]
    provider_overrides = {
        "akshare.stock_news_em": _ControlledProvider(
            provider_name="akshare",
            endpoint="stock_news_em",
            behavior=_ProviderBehavior(delay_seconds=0.01, mode="success"),
        ),
        "akshare.stock_info_global_cls": _ControlledProvider(
            provider_name="akshare",
            endpoint="stock_info_global_cls",
            behavior=_ProviderBehavior(delay_seconds=0.01, mode="error"),
        ),
    }

    results = run_bounded_parallel(
        query_plan=_build_plan(),
        enabled_providers=enabled_provider_ids,
        max_concurrency=3,
        per_provider_timeout_seconds=10,
        total_timeout_seconds=20,
        provider_overrides=provider_overrides,
    )
    result_map = {f"{result.attempt.provider}.{result.attempt.endpoint}": result for result in results}

    assert set(result_map.keys()) == set(enabled_provider_ids)
    assert result_map["akshare.stock_news_em"].ok is True
    assert result_map["akshare.stock_info_global_cls"].ok is False


def test_ensure_attempts_populates_missing_attempt_with_non_empty_query() -> None:
    attempts = ensure_attempts_for_all_enabled_providers(
        attempts=[],
        provider_results=[],
        enabled_providers=["akshare.stock_news_em"],
        mark_timeout_cancelled=True,
    )

    assert len(attempts) == 1
    attempt = attempts[0]
    assert attempt.provider == "akshare"
    assert attempt.endpoint == "stock_news_em"
    assert attempt.ok is False
    assert attempt.empty_reason == "timeout"
    assert attempt.cancelled is True
    assert attempt.query.strip() != ""


def test_scheduler_enters_cooldown_after_three_consecutive_failures() -> None:
    enabled_provider_ids = ["akshare.stock_news_em"]
    provider = _ControlledProvider(
        provider_name="akshare",
        endpoint="stock_news_em",
        behavior=_ProviderBehavior(delay_seconds=0.0, mode="error"),
    )
    provider_overrides = {"akshare.stock_news_em": provider}

    for _ in range(3):
        run_bounded_parallel(
            query_plan=_build_plan(),
            enabled_providers=enabled_provider_ids,
            max_concurrency=3,
            per_provider_timeout_seconds=10,
            total_timeout_seconds=20,
            provider_overrides=provider_overrides,
        )

    results = run_bounded_parallel(
        query_plan=_build_plan(),
        enabled_providers=enabled_provider_ids,
        max_concurrency=3,
        per_provider_timeout_seconds=10,
        total_timeout_seconds=20,
        provider_overrides=provider_overrides,
    )

    assert provider.calls == 3
    assert len(results) == 1
    attempt = results[0].attempt
    assert attempt.empty_reason == "rate_limited"
    assert attempt.elapsed_ms == 0
    assert attempt.cancelled is False
    assert attempt.ok is False


def test_scheduler_retries_remote_call_after_cooldown_window_passed() -> None:
    enabled_provider_ids = ["akshare.stock_news_em"]
    provider = _ControlledProvider(
        provider_name="akshare",
        endpoint="stock_news_em",
        behavior=_ProviderBehavior(delay_seconds=0.0, mode="error"),
    )
    provider_overrides = {"akshare.stock_news_em": provider}

    for _ in range(3):
        run_bounded_parallel(
            query_plan=_build_plan(),
            enabled_providers=enabled_provider_ids,
            max_concurrency=3,
            per_provider_timeout_seconds=10,
            total_timeout_seconds=20,
            provider_overrides=provider_overrides,
        )

    cooldown_until[("akshare", "stock_news_em")] = time.monotonic() - 1.0
    provider._behavior = _ProviderBehavior(delay_seconds=0.0, mode="success")

    results = run_bounded_parallel(
        query_plan=_build_plan(),
        enabled_providers=enabled_provider_ids,
        max_concurrency=3,
        per_provider_timeout_seconds=10,
        total_timeout_seconds=20,
        provider_overrides=provider_overrides,
    )

    assert provider.calls == 4
    assert results[0].ok is True


def test_scheduler_resets_failure_counter_after_success() -> None:
    enabled_provider_ids = ["akshare.stock_news_em"]
    provider = _ControlledProvider(
        provider_name="akshare",
        endpoint="stock_news_em",
        behavior=_ProviderBehavior(delay_seconds=0.0, mode="error"),
    )
    provider_overrides = {"akshare.stock_news_em": provider}

    run_bounded_parallel(
        query_plan=_build_plan(),
        enabled_providers=enabled_provider_ids,
        max_concurrency=3,
        per_provider_timeout_seconds=10,
        total_timeout_seconds=20,
        provider_overrides=provider_overrides,
    )

    assert provider_fail_counter[("akshare", "stock_news_em")] == 1
    provider._behavior = _ProviderBehavior(delay_seconds=0.0, mode="success")

    run_bounded_parallel(
        query_plan=_build_plan(),
        enabled_providers=enabled_provider_ids,
        max_concurrency=3,
        per_provider_timeout_seconds=10,
        total_timeout_seconds=20,
        provider_overrides=provider_overrides,
    )

    assert ("akshare", "stock_news_em") not in provider_fail_counter
    assert ("akshare", "stock_news_em") not in cooldown_until
