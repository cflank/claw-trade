from __future__ import annotations

import inspect
import sys
import threading
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_PYTHON_ROOT = REPO_ROOT / "openclaw_plugins" / "claw-trade-frontline-tools" / "python"
if str(SHARED_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(SHARED_PYTHON_ROOT))

from frontline_data_pack.models import ProviderQuery, ProviderQueryParameter, ProviderSpec  # noqa: E402
from frontline_data_pack.provider_executor import (  # noqa: E402
    PROVIDER_ERROR,
    PROVIDER_SCHEMA_INVALID,
    PROVIDER_TIMEOUT,
    ProviderCallError,
    ProviderCallContext,
    execute_provider_attempt,
    execute_provider_plan,
)
from frontline_data_pack.runtime_context import ToolRuntimeContext  # noqa: E402


QUERY_FINGERPRINT = "sha256:" + ("f" * 64)


def test_t_pvd_003_disabled_spec_returns_config_blocked_without_remote_call() -> None:
    called = False

    def _never_call(
        spec: ProviderSpec,
        query: ProviderQuery,
        context: ToolRuntimeContext,
        call_context: ProviderCallContext,
    ) -> object:
        nonlocal called
        _ = spec, query, context, call_context
        called = True
        return {}

    spec = _spec(enabled=False, mode="remote", provider="bocha", endpoint="cn_web_search")
    result = execute_provider_attempt(
        spec,
        _query(),
        _context(),
        call_registry={(spec.provider, spec.endpoint): _never_call},
    )

    assert called is False
    assert result.attempt.status == "config_blocked"
    assert result.attempt.error_code == "PROVIDER_NOT_ENABLED"


def test_t_pvd_003_timeout_maps_to_timeout_status_and_error_code() -> None:
    spec = _spec(timeout_ms=1000)

    def _slow_call(
        spec: ProviderSpec,
        query: ProviderQuery,
        context: ToolRuntimeContext,
        call_context: ProviderCallContext,
    ) -> object:
        _ = spec, query, context, call_context
        return {"rows": [{"close": 1600.0}]}

    ticks = iter([0.0, 1.2, 1.2])

    def _fake_monotonic() -> float:
        return next(ticks)

    result = execute_provider_attempt(
        spec,
        _query(),
        _context(),
        call_registry={(spec.provider, spec.endpoint): _slow_call},
        monotonic=_fake_monotonic,
    )

    assert result.attempt.status == "timeout"
    assert result.attempt.error_code == PROVIDER_TIMEOUT
    assert result.normalized_rows == []


def test_t_pvd_003_schema_invalid_payload_returns_empty_rows() -> None:
    spec = _spec()

    def _bad_payload(
        spec: ProviderSpec,
        query: ProviderQuery,
        context: ToolRuntimeContext,
        call_context: ProviderCallContext,
    ) -> object:
        _ = spec, query, context, call_context
        return {"kline": [{"close": 1600.0}]}

    result = execute_provider_attempt(
        spec,
        _query(),
        _context(),
        call_registry={(spec.provider, spec.endpoint): _bad_payload},
    )

    assert result.attempt.status == "schema_invalid"
    assert result.attempt.error_code == PROVIDER_SCHEMA_INVALID
    assert result.normalized_rows == []


def test_t_pvd_003_remote_empty_and_error_statuses_are_recorded() -> None:
    empty_spec = _spec(endpoint="stock_zh_a_hist")
    error_spec = _spec(endpoint="stock_zh_a_daily")

    def _empty_payload(
        spec: ProviderSpec,
        query: ProviderQuery,
        context: ToolRuntimeContext,
        call_context: ProviderCallContext,
    ) -> object:
        _ = spec, query, context, call_context
        return {"rows": []}

    def _raise_error(
        spec: ProviderSpec,
        query: ProviderQuery,
        context: ToolRuntimeContext,
        call_context: ProviderCallContext,
    ) -> object:
        _ = spec, query, context, call_context
        raise RuntimeError("provider boom")

    empty_result = execute_provider_attempt(
        empty_spec,
        _query(),
        _context(),
        call_registry={(empty_spec.provider, empty_spec.endpoint): _empty_payload},
    )
    error_result = execute_provider_attempt(
        error_spec,
        _query(),
        _context(),
        call_registry={(error_spec.provider, error_spec.endpoint): _raise_error},
    )

    assert empty_result.attempt.status == "empty"
    assert empty_result.attempt.error_code is None
    assert error_result.attempt.status == "error"
    assert error_result.attempt.error_code == PROVIDER_ERROR


def test_t_pvd_005_provider_call_error_keeps_typed_error_code() -> None:
    spec = _spec(endpoint="push2his_kline", provider="eastmoney_direct")

    def _raise_typed_error(
        spec: ProviderSpec,
        query: ProviderQuery,
        context: ToolRuntimeContext,
        call_context: ProviderCallContext,
    ) -> object:
        _ = spec, query, context, call_context
        raise ProviderCallError("PROVIDER_HTTP_SERVER_ERROR", "eastmoney push2his http 500")

    result = execute_provider_attempt(
        spec,
        _query(),
        _context(),
        call_registry={(spec.provider, spec.endpoint): _raise_typed_error},
    )

    assert result.attempt.status == "error"
    assert result.attempt.error_code == "PROVIDER_HTTP_SERVER_ERROR"
    assert result.attempt.error_message_redacted is not None


def test_t_pvd_003_cache_status_mapping_covers_hit_miss_stale_schema_invalid_error() -> None:
    cache_spec = _spec(mode="cache", provider="mongodb", endpoint="fresh_cache")

    payloads = {
        "cache_hit": {"status": "cache_hit", "normalized_rows": [{"k": "v"}]},
        "cache_miss": {"status": "cache_miss"},
        "cache_stale": {"status": "cache_stale"},
        "schema_invalid": {"status": "schema_invalid"},
        "error": {"status": "error", "error_code": "MONGO_UNAVAILABLE", "reason": "MONGO_UNAVAILABLE:down"},
    }

    for expected_status, payload in payloads.items():
        result = execute_provider_attempt(
            cache_spec,
            _query(),
            _context(),
            call_registry={(cache_spec.provider, cache_spec.endpoint): _return(payload)},
        )
        assert result.attempt.status == expected_status


def test_t_pvd_003_plan_continues_after_provider_error_and_respects_max_concurrency() -> None:
    specs = [
        _spec(endpoint="endpoint_0"),
        _spec(endpoint="endpoint_1"),
        _spec(endpoint="endpoint_2"),
        _spec(endpoint="endpoint_3"),
        _spec(endpoint="endpoint_4"),
    ]

    lock = threading.Lock()
    running = 0
    peak_running = 0
    executed_endpoints: list[str] = []

    def _runner(
        spec: ProviderSpec,
        query: ProviderQuery,
        context: ToolRuntimeContext,
        call_context: ProviderCallContext,
    ) -> object:
        nonlocal running, peak_running
        _ = query, context, call_context
        with lock:
            running += 1
            if running > peak_running:
                peak_running = running
        try:
            time.sleep(0.03)
            executed_endpoints.append(spec.endpoint)
            if spec.endpoint == "endpoint_1":
                raise RuntimeError("forced provider failure")
            return {"rows": [{"endpoint": spec.endpoint}]}
        finally:
            with lock:
                running -= 1

    call_registry = {(spec.provider, spec.endpoint): _runner for spec in specs}
    results = execute_provider_plan(
        specs,
        _query(),
        _context(),
        max_concurrency=3,
        total_timeout_ms=1_000,
        call_registry=call_registry,
    )

    assert peak_running <= 3
    assert len(results) == 5
    assert sorted(executed_endpoints) == ["endpoint_0", "endpoint_1", "endpoint_2", "endpoint_3", "endpoint_4"]
    assert sum(1 for result in results if result.attempt.status == "error") == 1
    assert sum(1 for result in results if result.attempt.status == "success") == 4


def test_t_pvd_003_plan_total_budget_uses_cooperative_cancellation() -> None:
    specs = [
        _spec(endpoint="timeout_endpoint_0"),
        _spec(endpoint="timeout_endpoint_1"),
        _spec(endpoint="timeout_endpoint_2"),
        _spec(endpoint="timeout_endpoint_3"),
        _spec(endpoint="timeout_endpoint_4"),
    ]
    started_endpoints: list[str] = []
    lock = threading.Lock()

    def _cooperative_slow_runner(
        spec: ProviderSpec,
        query: ProviderQuery,
        context: ToolRuntimeContext,
        call_context: ProviderCallContext,
    ) -> object:
        _ = query, context
        with lock:
            started_endpoints.append(spec.endpoint)
        while True:
            call_context.raise_if_cancelled()
            time.sleep(0.01)

    call_registry = {(spec.provider, spec.endpoint): _cooperative_slow_runner for spec in specs}
    started_at = time.perf_counter()
    results = execute_provider_plan(
        specs,
        _query(),
        _context(),
        max_concurrency=3,
        total_timeout_ms=120,
        call_registry=call_registry,
    )
    elapsed_ms = (time.perf_counter() - started_at) * 1000

    assert len(results) == 5
    assert elapsed_ms <= 500.0
    assert all(result.attempt.status == "timeout" for result in results)
    assert all(result.attempt.error_code == PROVIDER_TIMEOUT for result in results)
    assert all(result.normalized_rows == [] for result in results)
    assert [result.spec.endpoint for result in results] == [spec.endpoint for spec in specs]
    assert len(started_endpoints) <= 3
    assert set(started_endpoints).issubset({"timeout_endpoint_0", "timeout_endpoint_1", "timeout_endpoint_2"})
    for result in results:
        if result.spec.endpoint in started_endpoints:
            assert result.attempt.elapsed_ms > 0
        else:
            assert result.attempt.elapsed_ms == 0


def test_t_pvd_003_budget_timeout_keeps_finished_results_and_only_timeouts_remaining() -> None:
    specs = [
        _spec(endpoint="mixed_endpoint_0"),
        _spec(endpoint="mixed_endpoint_1"),
        _spec(endpoint="mixed_endpoint_2"),
        _spec(endpoint="mixed_endpoint_3"),
    ]

    started_endpoints: list[str] = []

    def _runner(
        spec: ProviderSpec,
        query: ProviderQuery,
        context: ToolRuntimeContext,
        call_context: ProviderCallContext,
    ) -> object:
        _ = query, context
        started_endpoints.append(spec.endpoint)
        if spec.endpoint == "mixed_endpoint_0":
            time.sleep(0.01)
            return {"rows": [{"endpoint": spec.endpoint}]}
        if spec.endpoint == "mixed_endpoint_1":
            time.sleep(0.01)
            raise RuntimeError("forced provider failure")
        while True:
            call_context.raise_if_cancelled()
            time.sleep(0.01)

    call_registry = {(spec.provider, spec.endpoint): _runner for spec in specs}
    results = execute_provider_plan(
        specs,
        _query(),
        _context(),
        max_concurrency=1,
        total_timeout_ms=80,
        call_registry=call_registry,
    )

    assert [result.attempt.status for result in results] == ["success", "error", "timeout", "timeout"]
    assert results[0].attempt.error_code is None
    assert results[1].attempt.error_code == PROVIDER_ERROR
    assert results[2].attempt.error_code == PROVIDER_TIMEOUT
    assert results[3].attempt.error_code == PROVIDER_TIMEOUT
    assert results[2].attempt.elapsed_ms > 0
    assert results[3].attempt.elapsed_ms == 0
    assert started_endpoints == ["mixed_endpoint_0", "mixed_endpoint_1", "mixed_endpoint_2"]


def test_t_pvd_003_provider_executor_does_not_use_private_daemon_thread_pool() -> None:
    source = inspect.getsource(sys.modules["frontline_data_pack.provider_executor"])
    assert "concurrent.futures.thread" not in source
    assert "_BudgetAwareThreadPoolExecutor" not in source
    assert "daemon=True" not in source


def _return(payload: object):
    def _runner(
        spec: ProviderSpec,
        query: ProviderQuery,
        context: ToolRuntimeContext,
        call_context: ProviderCallContext,
    ) -> object:
        _ = spec, query, context, call_context
        return payload

    return _runner


def _spec(
    *,
    enabled: bool = True,
    mode: str = "remote",
    provider: str = "akshare",
    endpoint: str = "stock_zh_a_hist",
    timeout_ms: int = 1000,
) -> ProviderSpec:
    return ProviderSpec(
        domain="market",
        priority="P0",
        provider=provider,
        endpoint=endpoint,
        role="p0_role",
        enabled=enabled,
        mode=mode,  # type: ignore[arg-type]
        timeout_ms=timeout_ms,
        required_for_complete=True,
        query_parameters=[
            ProviderQueryParameter(
                name="symbol",
                source="ticker_code_6",
                required=True,
                fixed_value=None,
            )
        ],
    )


def _query() -> ProviderQuery:
    return ProviderQuery(
        market="CN_A",
        ticker="600519.SH",
        company_name="贵州茅台",
        industry="白酒",
        start_date="2026-04-01",
        end_date="2026-05-08",
        adjust="qfq",
        query_fingerprint=QUERY_FINGERPRINT,
    )


def _context() -> ToolRuntimeContext:
    return ToolRuntimeContext(
        run_id="run-pvd-003",
        stage="frontline",
        worker_id="market_analyst",
        call_id="call-pvd-003",
        dispatch_id="dispatch-pvd-003",
        tool_name="market_market_data_pack",
        evidence_root="viking://resources/workflow/run-pvd-003/frontline/market_analyst/call-pvd-003/evidence",
        current_time="2026-05-08T12:00:00Z",
        current_date="2026-05-08",
    )
