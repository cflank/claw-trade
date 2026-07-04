# Crypto Onchain Data Need Timeout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent `/report BTC` from failing when the generic `链上` data need collides with another in-flight data need and waits until the outer tool timeout kills the process.

**Architecture:** Keep the existing data layer. Cap shared-result waiting inside `ExecutionGate`, remove the confirmed duplicate `coinglass.onchain_exchange_balance` candidate from generic `crypto.onchain_metric`, and write a tiny data-need trace before blocking provider/gate operations so future timeouts identify the provider and endpoint.

**Tech Stack:** Python, pytest, existing claw-trade data gateway planner/execution/report evidence modules.

---

## Scope And Success Criteria

This plan fixes the timeout path only. It does not redesign provider admission, add new data sources, change report prompts, or change OpenClaw runtime behavior.

Semantic decision for this fix:
- Generic `链上` means supplemental on-chain indicators.
- Explicit `交易所余额` owns exchange balance.
- Therefore generic `链上` must not also schedule `coinglass.onchain_exchange_balance`. This is not a provider failure bypass; it removes a duplicate public business-item mapping that creates the confirmed collision.

Success criteria:
- A single-flight waiter cannot wait for the whole 180 second DataNeed budget.
- `交易所余额` still plans to `coinglass.onchain_exchange_balance`.
- Generic `链上` no longer plans to `coinglass.onchain_exchange_balance`.
- A killed or blocked DataNeed tool leaves a JSONL trace showing the last provider/endpoint it entered.
- Existing provider timeout fallback behavior remains intact.

## File Structure

- Modify `src/claw_trade/data_gateway/execution/gate.py`
  - Owns shared-result gate decisions.
  - Add one constant and cap single-flight wait time.

- Modify `tests/unit/data_gateway/test_execution_gate.py`
  - Existing tests already cover waiter behavior.
  - Update/add focused tests for the cap.

- Modify `src/claw_trade/data_gateway/planner/call_planner.py`
  - Owns mapping public data needs to provider calls.
  - Exclude only the confirmed duplicate endpoint for generic `crypto.onchain_metric`.

- Modify `tests/unit/data_gateway/test_data_need_planner.py`
  - Update `链上` planner expectation.
  - Add regression that `交易所余额` still uses the Coinglass balance endpoint.

- Modify `src/claw_trade/data_gateway/report_evidence.py`
  - Owns DataNeed execution.
  - Add a local JSONL trace helper and call it before `execution_gate.enter()` and before provider fetch.

- Modify `tests/unit/reports/test_data_need_bridge.py`
  - Add regressions proving trace lines are written before execution-gate failure and before provider fetch.

- Modify `tests/contracts/test_frontline_tool_protocol.py`
  - Add one real Node plugin subprocess-timeout check proving a trace written before timeout survives process kill.

---

### Task 1: Cap Single-Flight Shared Result Waiting

**Files:**
- Modify: `src/claw_trade/data_gateway/execution/gate.py`
- Test: `tests/unit/data_gateway/test_execution_gate.py`

- [ ] **Step 1: Write the failing cap test**

Update the existing waiter test in `tests/unit/data_gateway/test_execution_gate.py`:

```python
def test_gate_caps_shared_result_wait_below_data_need_deadline() -> None:
    single_flight = _WaiterSingleFlight()
    gate = ExecutionGate(cache=ProviderResultCache(), rate_limiter=RateLimiter(), single_flight=single_flight)  # type: ignore[arg-type]

    decision = gate.enter(_Batch(deadline_at=datetime.now(UTC) + timedelta(seconds=180)))

    assert decision.kind == "shared_result"
    assert decision.refs.dataset_refs == ("dataset:shared",)
    assert single_flight.wait_budget_seconds == 8
```

Add the short-deadline case:

```python
def test_gate_uses_remaining_deadline_when_below_shared_wait_cap() -> None:
    single_flight = _WaiterSingleFlight()
    gate = ExecutionGate(cache=ProviderResultCache(), rate_limiter=RateLimiter(), single_flight=single_flight)  # type: ignore[arg-type]

    decision = gate.enter(_Batch(deadline_at=datetime.now(UTC) + timedelta(seconds=3)))

    assert decision.kind == "shared_result"
    assert single_flight.wait_budget_seconds is not None
    assert 0 < single_flight.wait_budget_seconds <= 3
```

Add a timeout behavior case:

```python
class _TimeoutSingleFlight(_WaiterSingleFlight):
    def wait(self, key: str, timeout_seconds: int) -> SimpleNamespace:
        self.wait_budget_seconds = timeout_seconds
        raise TimeoutError("single-flight wait timed out")


def test_gate_shared_result_wait_timeout_returns_rate_limited() -> None:
    single_flight = _TimeoutSingleFlight()
    gate = ExecutionGate(cache=ProviderResultCache(), rate_limiter=RateLimiter(), single_flight=single_flight)  # type: ignore[arg-type]

    decision = gate.enter(_Batch(deadline_at=datetime.now(UTC) + timedelta(seconds=180)))

    assert decision.kind == "rate_limited"
    assert decision.reason == "rate_limited_by_tool_budget"
    assert single_flight.wait_budget_seconds == 8
```

- [ ] **Step 2: Run the focused failing tests**

Run:

```bash
uv run pytest tests/unit/data_gateway/test_execution_gate.py::test_gate_caps_shared_result_wait_below_data_need_deadline tests/unit/data_gateway/test_execution_gate.py::test_gate_uses_remaining_deadline_when_below_shared_wait_cap tests/unit/data_gateway/test_execution_gate.py::test_gate_shared_result_wait_timeout_returns_rate_limited -q
```

Expected before implementation:
- First test fails because current code passes the full remaining deadline to `single_flight.wait()`.

- [ ] **Step 3: Implement the minimal cap**

Modify `src/claw_trade/data_gateway/execution/gate.py`:

```python
_SINGLE_FLIGHT_WAIT_CAP_SECONDS = 8
```

Replace `_single_flight_wait_budget_seconds` with:

```python
def _single_flight_wait_budget_seconds(batch: GateBatchPlan, *, now: datetime) -> int:
    deadline_at = _deadline_at(batch)
    if deadline_at is None:
        return 0
    remaining = int(max((deadline_at - now).total_seconds(), 0))
    return min(remaining, _SINGLE_FLIGHT_WAIT_CAP_SECONDS)
```

Update the caller in `ExecutionGate.enter()`:

```python
wait_budget_seconds = _single_flight_wait_budget_seconds(batch, now=self._now())
```

No env var. This is a hard safety cap, not a customer tuning knob.

- [ ] **Step 4: Run the focused tests**

Run:

```bash
uv run pytest tests/unit/data_gateway/test_execution_gate.py::test_gate_caps_shared_result_wait_below_data_need_deadline tests/unit/data_gateway/test_execution_gate.py::test_gate_uses_remaining_deadline_when_below_shared_wait_cap tests/unit/data_gateway/test_execution_gate.py::test_gate_shared_result_wait_timeout_returns_rate_limited -q
```

Expected:
- `3 passed`

- [ ] **Step 5: Run execution gate regression**

Run:

```bash
uv run pytest tests/unit/data_gateway/test_execution_gate.py -q
```

Expected:
- All tests in the file pass.

- [ ] **Step 6: Commit**

```bash
git add src/claw_trade/data_gateway/execution/gate.py tests/unit/data_gateway/test_execution_gate.py
git commit -m "fix: cap data need shared-result wait"
```

---

### Task 2: Remove Confirmed Duplicate Balance Endpoint From Generic `链上`

**Files:**
- Modify: `src/claw_trade/data_gateway/planner/call_planner.py`
- Test: `tests/unit/data_gateway/test_data_need_planner.py`

- [ ] **Step 1: Write the planner regression tests**

Update `test_crypto_onchain_project_item_plans_to_coinglass_onchain_interfaces` in `tests/unit/data_gateway/test_data_need_planner.py`:

```python
def test_crypto_onchain_project_item_plans_to_onchain_interfaces_without_exchange_balance_duplicate() -> None:
    request = _request(
        request_id="crypto-onchain",
        item="链上",
        market=Market.CRYPTO,
        instrument="BTC",
        granularity=None,
        purpose="fundamental_report",
    )

    plan = plan_public_data_requests((request,))

    calls = {(call.provider_id, call.catalog_endpoint_id, call.public_api_id) for call in plan.planned_calls}
    assert ("official_api_coinglass", "coinglass.onchain_exchange_balance", "crypto.onchain_metric") not in calls
    assert ("official_api_coinglass", "coinglass.onchain_whale_transfer", "crypto.onchain_metric") in calls
    assert ("official_api_coinglass", "coinglass.raw_index_bitcoin_active_addresses", "crypto.onchain_metric") in calls
    assert ("official_api_coinglass", "coinglass.raw_index_bitcoin_sth_sopr", "crypto.onchain_metric") in calls
    assert ("official_api_coinglass", "coinglass.raw_index_bitcoin_lth_sopr", "crypto.onchain_metric") in calls
    assert ("official_api_coinglass", "coinglass.raw_index_bitcoin_net_unrealized_profit_loss", "crypto.onchain_metric") in calls
```

Add a separate test below it:

```python
def test_crypto_exchange_balance_item_still_plans_to_coinglass_balance_endpoint() -> None:
    request = _request(
        request_id="crypto-exchange-balance",
        item="交易所余额",
        market=Market.CRYPTO,
        instrument="BTC",
        granularity=None,
        purpose="market_report",
    )

    plan = plan_public_data_requests((request,))

    calls = {(call.provider_id, call.catalog_endpoint_id, call.public_api_id) for call in plan.planned_calls}
    assert ("official_api_coinglass", "coinglass.onchain_exchange_balance", "crypto.exchange_balance") in calls
```

- [ ] **Step 2: Run the focused failing tests**

Run:

```bash
uv run pytest tests/unit/data_gateway/test_data_need_planner.py::test_crypto_onchain_project_item_plans_to_onchain_interfaces_without_exchange_balance_duplicate tests/unit/data_gateway/test_data_need_planner.py::test_crypto_exchange_balance_item_still_plans_to_coinglass_balance_endpoint -q
```

Expected before implementation:
- First test fails because generic `链上` still includes `coinglass.onchain_exchange_balance`.
- Second test passes.

- [ ] **Step 3: Implement the minimal planner exclusion**

In `src/claw_trade/data_gateway/planner/call_planner.py`, add this helper near `_public_candidates_for_request`:

```python
def _endpoint_excluded_for_public_request(endpoint: OfficialEndpoint, request: PublicDataRequest) -> bool:
    if request.api_id == "crypto.onchain_metric" and endpoint.endpoint_id == "coinglass.onchain_exchange_balance":
        return True
    return False
```

Then call it inside `_public_candidates_for_request`, before iterating endpoint outputs:

```python
    for index, endpoint in enumerate(catalog_endpoints):
        if _endpoint_excluded_for_public_request(endpoint, request):
            continue
        best_rank = None
```

Do not edit the Coinglass catalog. `coinglass.onchain_exchange_balance` must remain available for the explicit `交易所余额` business item.

- [ ] **Step 4: Run the focused tests**

Run:

```bash
uv run pytest tests/unit/data_gateway/test_data_need_planner.py::test_crypto_onchain_project_item_plans_to_onchain_interfaces_without_exchange_balance_duplicate tests/unit/data_gateway/test_data_need_planner.py::test_crypto_exchange_balance_item_still_plans_to_coinglass_balance_endpoint -q
```

Expected:
- `2 passed`

- [ ] **Step 5: Run planner regression**

Run:

```bash
uv run pytest tests/unit/data_gateway/test_data_need_planner.py -q
```

Expected:
- All planner tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/claw_trade/data_gateway/planner/call_planner.py tests/unit/data_gateway/test_data_need_planner.py
git commit -m "fix: avoid duplicate exchange balance in generic onchain need"
```

---

### Task 3: Persist DataNeed Progress Before Blocking Calls

**Files:**
- Modify: `src/claw_trade/data_gateway/report_evidence.py`
- Test: `tests/unit/reports/test_data_need_bridge.py`

- [ ] **Step 1: Write the failing trace test**

Add this test near the provider-timeout tests in `tests/unit/reports/test_data_need_bridge.py`:

```python
def test_data_need_writes_trace_before_execution_gate_wait(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    import claw_trade.data_gateway.report_evidence as report_evidence

    class BlockingGate:
        def enter(self, batch):  # type: ignore[no-untyped-def]
            raise RuntimeError("blocked in execution gate")

    rate_limit_policy = RateLimitPolicy(window_seconds=60, max_requests=None)
    runtime = SimpleNamespace(
        rate_limit_policy_resolver=SimpleNamespace(resolve=lambda **kwargs: rate_limit_policy),
        data_service=SimpleNamespace(execution_gate=BlockingGate()),
        fetch_engine=SimpleNamespace(fetch=lambda batch: FetchResult.from_success(batch, payload={"rows": []})),
        ingest=SimpleNamespace(ingest=lambda result, batch: IngestResult.failed(GapReason.PROVIDER_ERROR, batch_id=batch.batch_id)),
    )

    def fake_plan(needs):  # type: ignore[no-untyped-def]
        need = _internal_need_from_public_request(tuple(needs)[0])
        call = _provider_call("call-trace", need)
        return NeedPlan(
            plan_id="plan-trace",
            needs=(need,),
            planned_calls=(call,),
            created_at=datetime.now(tz=UTC),
        )

    monkeypatch.setattr(report_evidence, "plan_public_data_requests", fake_plan)
    monkeypatch.setattr(report_evidence, "build_data_gateway_runtime_from_env", lambda: runtime)

    payload = run_claw_request_data(
        {
            "item": "日线",
            "purpose": "market_report",
            "instrument": "600519.SH",
            "market": "CN_A",
            "time_range": {"start": "2026-06-12", "end": "2026-06-12"},
        },
        {
            "worker_id": "market_analyst",
            "run_id": "run",
            "call_id": "call",
            "current_date": "2026-06-12",
            "current_time": datetime.now(tz=UTC).isoformat(),
            "evidence_root": str(tmp_path),
        },
    )

    assert payload["ok"] is False
    trace_path = tmp_path / "data-layer" / "data-need-trace" / "run" / "call" / "events.jsonl"
    trace = trace_path.read_text(encoding="utf-8")
    assert '"event": "execution_gate_enter"' in trace
    assert '"provider_id": "official_api_slow"' in trace
    assert '"catalog_endpoint_id": "tushare.daily"' in trace
    assert '"provider_call_id": "call-trace"' in trace
```

Add a fetch-start trace test:

```python
def test_data_need_writes_trace_before_provider_fetch(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    import claw_trade.data_gateway.report_evidence as report_evidence

    def blocked_fetch(batch):  # type: ignore[no-untyped-def]
        raise RuntimeError("blocked in provider fetch")

    rate_limit_policy = RateLimitPolicy(window_seconds=60, max_requests=None)
    runtime = SimpleNamespace(
        rate_limit_policy_resolver=SimpleNamespace(resolve=lambda **kwargs: rate_limit_policy),
        data_service=SimpleNamespace(execution_gate=_OwnerExecutionGate()),
        fetch_engine=SimpleNamespace(fetch=blocked_fetch),
        ingest=SimpleNamespace(ingest=lambda result, batch: IngestResult.failed(GapReason.PROVIDER_ERROR, batch_id=batch.batch_id)),
    )

    def fake_plan(needs):  # type: ignore[no-untyped-def]
        need = _internal_need_from_public_request(tuple(needs)[0])
        call = _provider_call("call-fetch-trace", need)
        return NeedPlan(
            plan_id="plan-fetch-trace",
            needs=(need,),
            planned_calls=(call,),
            created_at=datetime.now(tz=UTC),
        )

    monkeypatch.setattr(report_evidence, "plan_public_data_requests", fake_plan)
    monkeypatch.setattr(report_evidence, "build_data_gateway_runtime_from_env", lambda: runtime)

    payload = run_claw_request_data(
        {
            "item": "日线",
            "purpose": "market_report",
            "instrument": "600519.SH",
            "market": "CN_A",
            "time_range": {"start": "2026-06-12", "end": "2026-06-12"},
        },
        {
            "worker_id": "market_analyst",
            "run_id": "run",
            "call_id": "call",
            "current_date": "2026-06-12",
            "current_time": datetime.now(tz=UTC).isoformat(),
            "evidence_root": str(tmp_path),
        },
    )

    assert payload["ok"] is False
    trace_path = tmp_path / "data-layer" / "data-need-trace" / "run" / "call" / "events.jsonl"
    trace = trace_path.read_text(encoding="utf-8")
    assert '"event": "execution_gate_enter"' in trace
    assert '"event": "provider_fetch_start"' in trace
    assert '"provider_call_id": "call-fetch-trace"' in trace
```

Add a bridge-level no-hidden-fallback test:

```python
def test_data_need_gate_wait_timeout_advances_to_next_candidate_with_attempt_evidence(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import claw_trade.data_gateway.report_evidence as report_evidence

    fetched: list[str] = []

    class TimeoutThenOwnerGate(_OwnerExecutionGate):
        def __init__(self) -> None:
            self.calls = 0

        def enter(self, batch):  # type: ignore[no-untyped-def]
            self.calls += 1
            if self.calls == 1:
                return GateDecision.rate_limited(batch.deadline_at, "rate_limited_by_tool_budget")
            return GateDecision.owner("owner-token")

    gate = TimeoutThenOwnerGate()

    def fetch(batch):  # type: ignore[no-untyped-def]
        fetched.append(batch.batch_id)
        return FetchResult.from_success(
            batch,
            payload={"rows": [{"dataset": "daily_bar", "symbol_id": "600519.SH", "date": "2026-06-12", "close": 1455.0}]},
            row_count=1,
        )

    def ingest(result, batch):  # type: ignore[no-untyped-def]
        return IngestResult.from_refs(
            batch_id=batch.batch_id,
            dataset_refs=(f"dataset:{batch.batch_id}",),
            raw_refs=(f"raw:{batch.batch_id}",),
            attempt_refs=(f"attempt:{batch.batch_id}",),
            gaps=(),
            remote_success=True,
        )

    def record_gate_result(batch, gate):  # type: ignore[no-untyped-def]
        return IngestResult.from_refs(
            batch_id=batch.batch_id,
            dataset_refs=(),
            raw_refs=(),
            attempt_refs=(f"attempt:{batch.batch_id}:gate",),
            gaps=(
                DataGap.by_reason(
                    GapReason.RATE_LIMITED_BY_TOOL_BUDGET,
                    request_id=batch.batch_id,
                    market=Market.CN_A,
                    data_type=batch.data_type,
                ),
            ),
            remote_success=False,
        )

    rate_limit_policy = RateLimitPolicy(window_seconds=60, max_requests=None)
    runtime = SimpleNamespace(
        rate_limit_policy_resolver=SimpleNamespace(resolve=lambda **kwargs: rate_limit_policy),
        data_service=SimpleNamespace(execution_gate=gate),
        fetch_engine=SimpleNamespace(fetch=fetch),
        ingest=SimpleNamespace(record_gate_result=record_gate_result, ingest=ingest),
    )

    def fake_plan(needs):  # type: ignore[no-untyped-def]
        need = _internal_need_from_public_request(tuple(needs)[0])
        first_call = _provider_call("call-shared-wait-timeout", need)
        second_call = _provider_call("call-next-candidate", need).model_copy(
            update={
                "provider_id": "official_api_next",
                "catalog_endpoint_id": "next.daily",
                "official_path_or_api_name": "next_daily",
                "rate_limit_bucket": "ratelimit:next",
                "batch_key": "batch:call-next-candidate",
            }
        )
        return NeedPlan(
            plan_id="plan-gate-timeout-next",
            needs=(need,),
            planned_calls=(first_call, second_call),
            created_at=datetime.now(tz=UTC),
        )

    monkeypatch.setattr(report_evidence, "plan_public_data_requests", fake_plan)
    monkeypatch.setattr(report_evidence, "build_data_gateway_runtime_from_env", lambda: runtime)

    payload = run_claw_request_data(
        {
            "item": "日线",
            "purpose": "market_report",
            "instrument": "600519.SH",
            "market": "CN_A",
            "time_range": {"start": "2026-06-12", "end": "2026-06-12"},
        },
        {
            "worker_id": "market_analyst",
            "run_id": "run",
            "call_id": "call",
            "current_date": "2026-06-12",
            "current_time": datetime.now(tz=UTC).isoformat(),
        },
    )

    assert payload["ok"] is True
    assert payload["status"] == "ready"
    assert fetched == ["call-next-candidate"]
    assert [item["call_id"] for item in payload["provider_attempts_summary"]] == [
        "call-shared-wait-timeout",
        "call-next-candidate",
    ]
    assert payload["provider_attempts_summary"][0]["gate_kind"] == "rate_limited"
    assert payload["provider_attempts_summary"][0]["gap_reasons"] == [GapReason.RATE_LIMITED_BY_TOOL_BUDGET.value]
```

- [ ] **Step 2: Run the failing trace test**

Run:

```bash
uv run pytest tests/unit/reports/test_data_need_bridge.py::test_data_need_writes_trace_before_execution_gate_wait tests/unit/reports/test_data_need_bridge.py::test_data_need_writes_trace_before_provider_fetch tests/unit/reports/test_data_need_bridge.py::test_data_need_gate_wait_timeout_advances_to_next_candidate_with_attempt_evidence -q
```

Expected before implementation:
- Fails because `events.jsonl` does not exist.

- [ ] **Step 3: Add the minimal trace helper**

In `src/claw_trade/data_gateway/report_evidence.py`, add imports:

```python
import json
from pathlib import Path
```

Add helpers near `_runtime_now`:

```python
def _append_data_need_trace(
    runtime_context: Mapping[str, Any],
    *,
    event: str,
    call: ProviderCallSpec,
    batch: Any | None = None,
) -> None:
    path = _data_need_trace_path(runtime_context)
    if path is None:
        return
    payload = {
        "ts": datetime.now(UTC).isoformat(),
        "event": event,
        "run_id": str(runtime_context.get("run_id") or "run"),
        "call_id": str(runtime_context.get("call_id") or "call"),
        "tool_call_id": runtime_context.get("tool_call_id"),
        "provider_id": call.provider_id,
        "catalog_endpoint_id": call.catalog_endpoint_id,
        "provider_call_id": call.call_id,
        "public_api_id": call.public_api_id,
        "business_api_id": call.business_api_id,
        "single_flight_key": getattr(batch, "single_flight_key", call.batch_key),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))
        handle.write("\n")


def _data_need_trace_path(runtime_context: Mapping[str, Any]) -> Path | None:
    evidence_root_value = str(runtime_context.get("evidence_root") or "").strip()
    if not evidence_root_value:
        return None
    evidence_root = Path(evidence_root_value).expanduser()
    run_id = _safe_identifier(str(runtime_context.get("run_id") or "run"))
    call_id = _safe_identifier(str(runtime_context.get("call_id") or "call"))
    return evidence_root / "data-layer" / "data-need-trace" / run_id / call_id / "events.jsonl"
```

Call it in `run_data_need_tool_request` immediately after `_provider_call_batch(...)`:

```python
                    _append_data_need_trace(runtime_context, event="execution_gate_enter", call=call, batch=batch)
                    gate = runtime.data_service.execution_gate.enter(batch)
```

Call it again immediately before each `runtime.fetch_engine.fetch(batch)`:

```python
                            _append_data_need_trace(runtime_context, event="provider_fetch_start", call=call, batch=batch)
                            fetch_result = runtime.fetch_engine.fetch(batch)
```

Also add the same line before the retry fetch inside `wait_after_rate_limited_fetch`.

- [ ] **Step 4: Run the focused trace test**

Run:

```bash
uv run pytest tests/unit/reports/test_data_need_bridge.py::test_data_need_writes_trace_before_execution_gate_wait tests/unit/reports/test_data_need_bridge.py::test_data_need_writes_trace_before_provider_fetch tests/unit/reports/test_data_need_bridge.py::test_data_need_gate_wait_timeout_advances_to_next_candidate_with_attempt_evidence -q
```

Expected:
- `3 passed`

- [ ] **Step 5: Run DataNeed regression**

Run:

```bash
uv run pytest tests/unit/reports/test_data_need_bridge.py::test_data_need_provider_timeout_becomes_formal_attempt_not_subprocess_timeout tests/unit/reports/test_data_need_bridge.py::test_data_need_provider_timeout_does_not_block_fallback_provider tests/unit/reports/test_data_need_bridge.py::test_data_need_writes_trace_before_execution_gate_wait tests/unit/reports/test_data_need_bridge.py::test_data_need_writes_trace_before_provider_fetch tests/unit/reports/test_data_need_bridge.py::test_data_need_gate_wait_timeout_advances_to_next_candidate_with_attempt_evidence -q
```

Expected:
- `5 passed`

- [ ] **Step 6: Commit**

```bash
git add src/claw_trade/data_gateway/report_evidence.py tests/unit/reports/test_data_need_bridge.py
git commit -m "fix: trace data need progress before blocking calls"
```

---

### Task 4: Prove Trace Survives Real Node Subprocess Timeout

**Files:**
- Modify: `tests/contracts/test_frontline_tool_protocol.py`

- [ ] **Step 1: Write the subprocess-timeout trace proof test**

Add this test near `test_data_need_subprocess_timeout_returns_when_descendant_keeps_pipe_open` in `tests/contracts/test_frontline_tool_protocol.py`:

```python
def test_data_need_subprocess_timeout_preserves_trace_file(tmp_path: Path) -> None:
    evidence_dir = tmp_path / "evidence"
    probe_python = tmp_path / "probe_python.sh"
    _write_executable(
        probe_python,
        """#!/usr/bin/env bash
python3 -c 'import json, pathlib, sys, time
payload = json.load(sys.stdin)
ctx = payload["runtime_context"]
trace_path = pathlib.Path(ctx["evidence_root"]) / "data-layer" / "data-need-trace" / ctx["run_id"] / ctx["call_id"] / "events.jsonl"
trace_path.parent.mkdir(parents=True, exist_ok=True)
trace_path.write_text("{\\"event\\":\\"probe_before_sleep\\",\\"provider_id\\":\\"probe\\",\\"catalog_endpoint_id\\":\\"probe.endpoint\\"}\\n", encoding="utf-8")
time.sleep(60)'
""",
    )

    ctx = _runtime_ctx(
        worker_id="market_analyst",
        runtime_vars={
            "ticker": "BTC",
            "market": "CRYPTO",
            "profile": "CRYPTO",
            "company_name": "Bitcoin",
            "currency": "USDT",
        },
    )
    ctx["singleWorkerCommand"]["evidence_dir"] = str(evidence_dir)  # type: ignore[index]

    result = _run_tool(
        tool_name="claw_request_data",
        ctx=ctx,
        params={"item": "链上", "purpose": "market_report"},
        env_overrides={
            "CLAW_TRADE_FRONTLINE_TOOL_PYTHON": str(probe_python),
            "CLAW_TRADE_DATA_NEED_TOOL_BUDGET_SECONDS": "1",
            "CN_A_PROVIDER_TOTAL_TIMEOUT_MS": "1",
        },
    )

    assert result.get("isError") is True
    assert _error_code(result) == "TOOL_SUBPROCESS_TIMEOUT"
    trace_files = list((evidence_dir / "data-need-tool-evidence" / "data-layer" / "data-need-trace" / "run-1").glob("*/events.jsonl"))
    assert len(trace_files) == 1
    assert "probe_before_sleep" in trace_files[0].read_text(encoding="utf-8")
```

This test uses the real Node plugin timeout path. It intentionally does not call the Python DataNeed implementation; it proves that any trace written before subprocess kill remains on disk.

- [ ] **Step 2: Run the focused contract test**

Run:

```bash
uv run pytest tests/contracts/test_frontline_tool_protocol.py::test_data_need_subprocess_timeout_preserves_trace_file -q
```

Expected before implementation:
- This may already pass if the probe writes the file directly. If it fails, fix only the test harness or trace path, not production code.

- [ ] **Step 3: Run frontline timeout protocol regression**

Run:

```bash
uv run pytest tests/contracts/test_frontline_tool_protocol.py::test_data_need_subprocess_timeout_returns_when_descendant_keeps_pipe_open tests/contracts/test_frontline_tool_protocol.py::test_data_need_subprocess_timeout_preserves_trace_file -q
```

Expected:
- `2 passed`

- [ ] **Step 4: Commit**

```bash
git add tests/contracts/test_frontline_tool_protocol.py
git commit -m "test: prove data need trace survives subprocess timeout"
```

---

### Task 5: Regression And Live-Style Verification

**Files:**
- No code changes.
- Uses tests changed in Tasks 1-4.

- [ ] **Step 1: Run focused unit suite**

Run:

```bash
uv run pytest tests/unit/data_gateway/test_execution_gate.py tests/unit/data_gateway/test_data_need_planner.py tests/unit/reports/test_data_need_bridge.py::test_data_need_provider_timeout_becomes_formal_attempt_not_subprocess_timeout tests/unit/reports/test_data_need_bridge.py::test_data_need_provider_timeout_does_not_block_fallback_provider tests/unit/reports/test_data_need_bridge.py::test_data_need_writes_trace_before_execution_gate_wait tests/unit/reports/test_data_need_bridge.py::test_data_need_writes_trace_before_provider_fetch tests/unit/reports/test_data_need_bridge.py::test_data_need_gate_wait_timeout_advances_to_next_candidate_with_attempt_evidence tests/contracts/test_frontline_tool_protocol.py::test_data_need_subprocess_timeout_preserves_trace_file -q
```

Expected:
- All selected tests pass.

- [ ] **Step 2: Run a local planning proof for BTC**

Run:

```bash
uv run python - <<'PY'
from datetime import UTC, date, datetime
from claw_trade.data_gateway.models import Market
from claw_trade.data_gateway.public_api import PublicDataRequest, PublicRequestPriority
from claw_trade.data_gateway.planner import plan_public_data_requests

for item in ["交易所余额", "链上"]:
    request = PublicDataRequest.model_validate({
        "request_id": f"probe-{item}",
        "item": item,
        "market": Market.CRYPTO,
        "instrument": "BTC",
        "time_range_start": date(2025, 7, 4),
        "time_range_end": date(2026, 7, 4),
        "granularity": None,
        "priority": PublicRequestPriority.NORMAL,
        "requested_by_worker": "market_analyst",
        "purpose": "market_report",
        "deadline_at": datetime(2026, 7, 4, 12, 0, tzinfo=UTC),
        "consumer": "report",
    })
    endpoints = [call.catalog_endpoint_id for call in plan_public_data_requests((request,)).planned_calls]
    print(item, endpoints)
PY
```

Expected:
- `交易所余额` includes `coinglass.onchain_exchange_balance`.
- `链上` does not include `coinglass.onchain_exchange_balance`.

- [ ] **Step 3: Run diff check**

Run:

```bash
git diff --check
```

Expected:
- No output.

- [ ] **Step 4: Commit verification note if the project expects memory updates**

If this task is implemented by a subagent, append a short entry to `memory/2026-07-04.md` with:
- changed files
- commands run
- whether live Chrome validation was run
- remaining live-test risk

Then commit it separately only if memory updates are required for the current workflow.

```bash
git add memory/2026-07-04.md
git commit -m "docs: record data need timeout fix evidence"
```

---

## Rollback Plan

Revert the four commits in reverse order:

```bash
git revert <subprocess-timeout-trace-test-commit>
git revert <trace-commit>
git revert <planner-commit>
git revert <gate-commit>
```

If only the planner exclusion causes unexpected coverage loss, revert Task 2 only. Task 1 is still safe because it prevents a waiter from consuming the whole DataNeed budget.

## Out Of Scope

- Do not add a new provider.
- Do not change OpenClaw plugin timeout values.
- Do not make `链上` a new prompt item.
- Do not add user-facing configuration for the wait cap.
- Do not turn this into a broad provider-plan redesign.
