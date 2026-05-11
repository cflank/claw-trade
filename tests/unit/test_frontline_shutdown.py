from __future__ import annotations

import sys
from pathlib import Path
import time


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_PYTHON_ROOT = REPO_ROOT / "openclaw_plugins" / "claw-trade-frontline-tools" / "python"
if str(SHARED_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(SHARED_PYTHON_ROOT))

from frontline_data_pack.models import ProviderQuery, ProviderQueryParameter, ProviderSpec  # noqa: E402
from frontline_data_pack.provider_executor import execute_provider_plan  # noqa: E402
from frontline_data_pack.runtime_context import ToolRuntimeContext  # noqa: E402
from frontline_data_pack.shutdown import PACK_TERMINATED, request_termination, reset_termination_flag  # noqa: E402


def test_t_ops_001_sigterm_marks_unstarted_required_specs_as_pack_terminated_attempts() -> None:
    try:
        request_termination()
        specs = [
            _spec(endpoint="endpoint_0"),
            _spec(endpoint="endpoint_1"),
            _spec(endpoint="endpoint_2"),
        ]
        results = execute_provider_plan(
            specs,
            _query(),
            _context(),
            max_concurrency=2,
            total_timeout_ms=1_000,
            call_registry={},
        )
    finally:
        reset_termination_flag()

    assert len(results) == 3
    assert all(result.attempt.error_code == PACK_TERMINATED for result in results)
    assert all(result.attempt.status == "error" for result in results)


def test_t_ops_001_sigterm_keeps_completed_attempts_and_marks_only_unstarted_specs() -> None:
    specs = [
        _spec(endpoint="endpoint_0"),
        _spec(endpoint="endpoint_1"),
        _spec(endpoint="endpoint_2"),
    ]
    started_endpoints: list[str] = []
    completed_endpoints: list[str] = []

    def _runner(
        spec: ProviderSpec,
        query: ProviderQuery,
        context: ToolRuntimeContext,
        call_context,
    ) -> object:
        _ = query, context, call_context
        started_endpoints.append(spec.endpoint)
        if spec.endpoint == "endpoint_0":
            time.sleep(0.01)
            completed_endpoints.append(spec.endpoint)
            request_termination()
            return {"rows": [{"endpoint": spec.endpoint}]}
        raise AssertionError(f"unexpected started endpoint: {spec.endpoint}")

    call_registry = {(spec.provider, spec.endpoint): _runner for spec in specs}
    try:
        results = execute_provider_plan(
            specs,
            _query(),
            _context(),
            max_concurrency=1,
            total_timeout_ms=1_000,
            call_registry=call_registry,
        )
    finally:
        reset_termination_flag()

    assert started_endpoints == ["endpoint_0"]
    assert completed_endpoints == ["endpoint_0"]
    assert [result.attempt.status for result in results] == ["success", "error", "error"]
    assert results[0].attempt.error_code is None
    assert results[1].attempt.error_code == PACK_TERMINATED
    assert results[2].attempt.error_code == PACK_TERMINATED


def _spec(*, endpoint: str) -> ProviderSpec:
    return ProviderSpec(
        domain="market",
        priority="P0",
        provider="akshare",
        endpoint=endpoint,
        role="p0_role",
        enabled=True,
        mode="remote",
        timeout_ms=1000,
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
        start_date="2026-05-01",
        end_date="2026-05-09",
        adjust="qfq",
        query_fingerprint="sha256:" + ("f" * 64),
    )


def _context() -> ToolRuntimeContext:
    return ToolRuntimeContext(
        run_id="run-shutdown",
        stage="frontline",
        worker_id="market_analyst",
        call_id="call-shutdown",
        dispatch_id="dispatch-shutdown",
        tool_name="market_market_data_pack",
        evidence_root="runs/run-shutdown/frontline/market_analyst/call-shutdown/evidence",
        current_time="2026-05-09T12:00:00Z",
        current_date="2026-05-09",
    )
