# OpenBB RunProviderPlan T6 Evidence

Date: 2026-05-17

Scope:

- T6 RunProviderPlan workflow 接入。
- `/report` run 在 `WorkflowStore.create_run()` 后、第一次 worker wake 前写入 `RunProviderPlan`。
- pack endpoint runtime 在调用 pack service 前加载本 run plan；缺 plan 或 `provider_config_version` mismatch 显式失败。
- Python 控制层不调用 `ProviderAdapter.fetch()`，`remote_prefetch_allowed=false`。

## Implementation Evidence

Relevant files:

- `src/claw_trade/workflow/runner.py`
- `src/claw_trade/data_gateway/providers/run_plan.py`
- `src/claw_trade/data_gateway/mcp/runtime_wrapper.py`
- `src/claw_trade/data_gateway/store/run_plans.py`
- `tests/unit/data_gateway/test_run_provider_plan.py`
- `tests/integration/data_gateway/test_run_provider_plan_snapshot.py`
- `tests/unit/data_gateway/test_openbb_runtime_wrapper.py`

Key contracts:

- `ControlRunner.run()` calls `_initialize_report_run_plan()` immediately after `store.create_run(request)` and before `openviking.ensure_namespace()` / controller decisions / worker wake.
- `build_report_run_plan()` returns `None` for non-`report_command` entry points.
- `RunProviderPlanner.build_run_plan()` records `call_specs`, `shared_call_keys`, `cache_keys`, `rate_limit_plan`, `initial_gaps`, and `remote_prefetch_allowed=False`.
- `load_plan_for_pack_runtime()` rejects missing plan via the store and rejects config mismatch / `remote_prefetch_allowed=True`.
- `OpenBBRuntimeWrapper.get_pack()` loads the run plan using `settings.provider_config_version` before `pack_service.get_pack(request, run_plan)`.
- `claw_trade.cli.run_control._build_runner()` injects `RunProviderPlanner`, Mongo `RunProviderPlanStore`, `ProviderRegistry`, and `provider_config_version_resolver`; missing `DATA_GATEWAY_MONGODB_URI`/`CN_A_MONGODB_URI` blocks explicitly.

## Mongo Evidence Sample

Command:

```bash
DATA_GATEWAY_MONGODB_URI=mongodb://127.0.0.1:27017 uv run python - <<'PY'
# Builds a plan with a no-fetch adapter, writes it to Mongo, and prints the stored plan summary.
PY
```

Exit code: `0`

Key output:

```text
db_name claw_trade_t6_evidence_d06aa443
collection openbb_run_provider_plans
validate_calls 1
fetch_calls 0
remote_prefetch_allowed False
provider_config_version cfg-v1
call_specs 1
shared_call_keys ['market:project.tushare:daily']
cache_keys_count 1
rate_limit_count 1
initial_gap_reasons ['credential_missing', 'source_not_configured']
```

Evidence meaning:

- Plan document was written to real Mongo collection `openbb_run_provider_plans`.
- Credential presence was checked once.
- Provider fetch was not called.
- Plan carries a fixed config version and no remote prefetch permission.
- Missing credential and missing domain provider are recorded as explicit gaps, not hidden fallback.

## Verification Commands

T6 scoped command with real Mongo:

```bash
DATA_GATEWAY_MONGODB_URI=mongodb://127.0.0.1:27017 \
uv run pytest tests/unit/data_gateway/test_run_provider_plan.py \
  tests/integration/data_gateway/test_run_provider_plan_snapshot.py
```

Exit code: `0`

Key output:

```text
collected 8 items
tests/unit/data_gateway/test_run_provider_plan.py ......                 [ 75%]
tests/integration/data_gateway/test_run_provider_plan_snapshot.py ..     [100%]
8 passed, 2 warnings
```

Pack runtime + run plan load regression:

```bash
DATA_GATEWAY_MONGODB_URI=mongodb://127.0.0.1:27017 \
uv run pytest tests/unit/data_gateway/test_openbb_runtime_wrapper.py \
  tests/integration/data_gateway/test_mcp_pack_endpoints.py \
  tests/unit/data_gateway/test_run_provider_plan.py \
  tests/integration/data_gateway/test_run_provider_plan_snapshot.py
```

Exit code: `0`

Key output:

```text
collected 17 items
17 passed, 2 warnings
```

T2-T7 combined data gateway regression:

```bash
DATA_GATEWAY_MONGODB_URI=mongodb://127.0.0.1:27017 \
uv run pytest tests/unit/data_gateway/test_models.py \
  tests/unit/data_gateway/test_source_roles.py \
  tests/unit/data_gateway/test_provider_adapter_contract.py \
  tests/unit/data_gateway/test_cache_state_machine.py \
  tests/integration/data_gateway/test_mongo_attempts.py \
  tests/integration/data_gateway/test_run_provider_plan_snapshot.py \
  tests/unit/data_gateway/test_provider_admission.py \
  tests/unit/data_gateway/test_provider_admission_security.py \
  tests/unit/data_gateway/test_provider_registry.py \
  tests/integration/data_gateway/test_declarative_provider_admission.py \
  tests/unit/data_gateway/test_openbb_runtime_wrapper.py \
  tests/integration/data_gateway/test_mcp_pack_endpoints.py \
  tests/unit/data_gateway/test_tool_schema.py \
  tests/integration/data_gateway/test_mcp_visible_tools.py \
  tests/unit/data_gateway/test_run_provider_plan.py
```

Exit code: `0`

Key output:

```text
collected 58 items
58 passed, 5 warnings
```

T6 CLI wiring regression:

```bash
uv run pytest tests/unit/test_run_control_cli.py tests/unit/data_gateway/test_run_provider_plan.py
```

Exit code: `0`

Key output:

```text
collected 17 items
17 passed
```

T2-T7 combined regression after CLI wiring:

```bash
DATA_GATEWAY_MONGODB_URI=mongodb://127.0.0.1:27017 \
uv run pytest tests/unit/test_run_control_cli.py \
  tests/unit/data_gateway/test_models.py \
  tests/unit/data_gateway/test_source_roles.py \
  tests/unit/data_gateway/test_provider_adapter_contract.py \
  tests/unit/data_gateway/test_cache_state_machine.py \
  tests/integration/data_gateway/test_mongo_attempts.py \
  tests/integration/data_gateway/test_run_provider_plan_snapshot.py \
  tests/unit/data_gateway/test_provider_admission.py \
  tests/unit/data_gateway/test_provider_admission_security.py \
  tests/unit/data_gateway/test_provider_registry.py \
  tests/integration/data_gateway/test_declarative_provider_admission.py \
  tests/unit/data_gateway/test_openbb_runtime_wrapper.py \
  tests/integration/data_gateway/test_mcp_pack_endpoints.py \
  tests/unit/data_gateway/test_tool_schema.py \
  tests/integration/data_gateway/test_mcp_visible_tools.py \
  tests/unit/data_gateway/test_run_provider_plan.py
```

Exit code: `0`

Key output:

```text
collected 69 items
69 passed, 5 warnings
```

Warnings:

- `pytest.mark.integration` is not registered in `pyproject.toml`.
- Python multiprocessing warns about `fork()` in a multithreaded process during the persistent single-flight integration test.

## Boundary Status

- Deviation status: no T6 boundary deviation found in scoped implementation.
- Real implementation status: real Mongo store, real plan serialization, real wrapper runtime load contract; domain pack implementations are still pending T8/T9/T10.
- Mock/stub/fake/fallback status: no provider fetch mock-success or fallback success used for this evidence. Unit tests use contract test doubles only to prove no-fetch and endpoint failure behavior; integration evidence uses real Mongo.
- Remaining risk: live `/report` four-market evidence is not collected yet; old provider import-block and OpenBB-only pack implementation remain T8-T17 work.
