# Data Need Rate Limit Rework Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace fixed report data packs and business-scope provider whitelists with DataNeed-driven planning, official endpoint metadata, dynamic sliding-window provider limits, and evidence-backed data gaps.

**Architecture:** Frontline workers submit provider-agnostic `DataNeed` objects. The data layer resolves needs to catalog-backed provider call specs, merges requests only when official endpoint metadata allows it, schedules calls against provider-level sliding windows and task deadlines, and returns raw/normalized evidence. Existing pack tools become cache/summary readers and must not generate remote request lists.

**Tech Stack:** Python 3, Pydantic models, pytest contract/unit/integration tests, Mongo-backed data gateway repository, OpenClaw frontend tool plugin in Node.js.

---

## Scope

This plan implements `docs/限流重组.md`. Current implementation is non-conforming and must be treated as legacy during this work.

Non-negotiable constraints:

- Worker/tool input must not expose `provider`, `path`, `api_name`, `url`, `header`, or `token`.
- Remote requests must originate from `DataNeed` planner output, not `_CN_A_DOMAIN_DATASETS`, `_CRYPTO_DOMAIN_DATASETS`, or fixed `DataRequest(data_type, fields)` pack lists.
- `fields` and `coverage_fields` are result validation inputs only; they must not deny provider candidates.
- `consumer`, `worker_id`, `domain`, and `source_role_required` must not become business allowlists.
- Provider rate limit config is only `max_calls/window_seconds`; waiting is decided by `deadline_at`.
- Official raw calls must be generated from catalog endpoint IDs by the planner, not from user/worker path or api_name input.

## File Structure

- Create `src/claw_trade/data_gateway/needs.py`: `DataNeed`, `NeedKind`, `NeedPriority`, `NeedInstrument`, `NeedPlan`, `ScheduledCall`, `DataNeedGap`.
- Create `src/claw_trade/data_gateway/official_catalog/`: machine-readable official/public endpoint metadata.
- Create `src/claw_trade/data_gateway/planner/need_resolver.py`: maps `DataNeed` to catalog-backed candidate endpoints.
- Create `src/claw_trade/data_gateway/planner/call_planner.py`: builds `ProviderCallSpec`, merge keys, and evidence.
- Modify `src/claw_trade/data_gateway/coordination/provider_selector.py`: stop using fields/source_role_required/consumer/domain as candidate denial.
- Modify `src/claw_trade/data_gateway/coordination/coalescer.py`: group official raw calls by catalog endpoint, not generic `official_api_call`.
- Modify `src/claw_trade/data_gateway/coordination/scheduler.py`: implement deadline-aware scheduling and cross-worker de-duplication.
- Modify `src/claw_trade/data_gateway/execution/rate_limiter.py`: sliding-window reserve by deadline, no provider wait timeout.
- Modify `src/claw_trade/data_gateway/execution/gate.py` and `fetch_engine.py`: pass `deadline_at` into gate/managed HTTP.
- Modify `src/claw_trade/data_gateway/models.py`: close gap/status enums for raw success, parser missing, permission denied, budget-limited rate limits, resolver mapping missing.
- Modify `src/claw_trade/data_gateway/providers/plugins/official_api.py`: consume planner-generated `ProviderCallSpec`; remove freeform caller-supplied path/api_name behavior from report path.
- Modify `src/claw_trade/reports/data_pack_bridge.py`: make packs cache/summary readers; remove remote fixed request generation.
- Modify `openclaw_plugins/claw-trade-frontline-tools/index.js`: expose provider-agnostic data need tool schema.
- Modify `src/claw_trade/runtime/openclaw_client.py` and tool config files as needed: mount the DataNeed tool for frontline workers.
- Create/update contract tests listed in Task 1.

## Implementation Tasks

### Task 0: Commit The Approved Design Contract

**Files:**
- Track: `docs/限流重组.md`
- Track: `docs/superpowers/plans/2026-06-12-data-need-rate-limit-rework.md`

- [ ] **Step 1: Confirm only design docs are staged**

Run:

```bash
git status --short docs/限流重组.md docs/superpowers/plans/2026-06-12-data-need-rate-limit-rework.md
```

Expected: both files are untracked or modified, no runtime code staged.

- [ ] **Step 2: Commit design contract**

Run:

```bash
git add docs/限流重组.md docs/superpowers/plans/2026-06-12-data-need-rate-limit-rework.md
git commit -m "docs: define DataNeed data-layer rework plan"
```

Expected: commit succeeds. This prevents subagents from reading stale untracked docs.

### Task 1: Freeze Contracts Before Runtime Changes

**Files:**
- Create: `tests/contracts/test_data_need_tool_contract.py`
- Create: `tests/contracts/test_worker_provider_payload_no_provider_leak.py`
- Create: `tests/contracts/test_provider_selector_no_business_whitelist.py`
- Create: `tests/contracts/test_official_provider_catalog_contract.py`
- Create: `tests/contracts/test_data_gap_reason_contract.py`
- Create: `tests/unit/data_gateway/test_official_call_batch_keys.py`
- Create: `tests/unit/data_gateway/test_sdk_internal_http_risk_inventory.py`

- [ ] **Step 1: Add failing contract for worker-visible schema**

Test intent:

```python
FORBIDDEN = {"provider", "path", "api_name", "url", "header", "token"}

def test_frontline_data_need_tool_schema_hides_provider_details():
    schema = load_openclaw_frontline_tool_schema("claw_request_data")
    assert not forbidden_keys_present(schema, FORBIDDEN)
```

Expected before implementation: fail because `claw_request_data` does not exist.

- [ ] **Step 2: Add failing contract for provider selector**

Test intent:

```python
def test_same_need_has_same_candidates_across_consumers():
    consumers = ("report", "select", "ui_probe", "maintenance")
    candidates = [select_candidates_for_need(consumer=item) for item in consumers]
    assert all(item == candidates[0] for item in candidates)

def test_fields_do_not_deny_provider_candidates():
    candidates_without_fields = select_candidates_for_need(fields=())
    candidates_with_unknown_field = select_candidates_for_need(fields=("not_declared",))
    assert provider_ids(candidates_with_unknown_field) == provider_ids(candidates_without_fields)
```

Expected before implementation: fail while selector still uses `fields/coverage_fields`.

- [ ] **Step 3: Add official catalog contract**

Test intent:

```python
FORBIDDEN_CATALOG_KEYS = {"allowed_worker", "allowed_domain", "allowed_report_section", "provider_scope"}

def test_official_catalog_has_endpoint_granularity_and_no_business_scope():
    for endpoint in iter_official_catalog_endpoints():
        assert endpoint.endpoint_id != "official_api_call"
        assert not FORBIDDEN_CATALOG_KEYS & endpoint.raw_keys
        assert endpoint.official_doc_ref
```

Expected before implementation: fail because catalog package does not exist.

- [ ] **Step 4: Add gap reason contract**

Test intent:

```python
def test_required_data_need_gap_reasons_exist():
    required = {
        "permission_denied",
        "provider_empty",
        "parser_missing",
        "resolver_mapping_missing",
        "rate_limited_by_tool_budget",
    }
    assert required <= {item.value for item in GapReason}
```

Expected before implementation: fail.

- [ ] **Step 5: Add official raw batch-key contract**

Test intent:

```python
def test_different_tushare_api_names_do_not_share_batch_key():
    daily = call_spec(provider="tushare", endpoint="tushare.daily")
    moneyflow = call_spec(provider="tushare", endpoint="tushare.moneyflow")
    assert daily.batch_key != moneyflow.batch_key
```

Expected before implementation: fail while raw calls use generic endpoint.

- [ ] **Step 6: Run contract tests to confirm red**

Run:

```bash
uv run pytest \
  tests/contracts/test_data_need_tool_contract.py \
  tests/contracts/test_worker_provider_payload_no_provider_leak.py \
  tests/contracts/test_provider_selector_no_business_whitelist.py \
  tests/contracts/test_official_provider_catalog_contract.py \
  tests/contracts/test_data_gap_reason_contract.py \
  tests/unit/data_gateway/test_official_call_batch_keys.py \
  tests/unit/data_gateway/test_sdk_internal_http_risk_inventory.py -q
```

Expected: fail for missing implementation, not syntax errors.

### Task 2: Add DataNeed And Planner Models

**Files:**
- Create: `src/claw_trade/data_gateway/needs.py`
- Modify: `src/claw_trade/data_gateway/__init__.py`
- Modify: `src/claw_trade/data_gateway/models.py`
- Test: `tests/unit/data_gateway/test_models.py`
- Test: `tests/contracts/test_data_gap_reason_contract.py`

- [ ] **Step 1: Implement `DataNeed` model**

Required shape:

```python
class DataNeed(BaseModel):
    need_id: str
    need_kind: str
    market: Market
    instrument: str
    time_range_start: date | datetime | None = None
    time_range_end: date | datetime | None = None
    granularity: str | None = None
    priority: Literal["required", "normal", "optional", "expensive"] = "normal"
    requested_by_worker: str
    purpose: str
    freshness_policy: str = "trading_day"
    deadline_at: datetime
    consumer: str = "report"
```

- [ ] **Step 2: Add planner output models**

Add `ProviderCallSpec`, `NeedPlan`, `ScheduledCall`, `DataNeedGap`, `MergeEvidence`, `RateLimitEvidence`.

Required fields for `ProviderCallSpec`:

```python
provider_id
catalog_endpoint_id
official_path_or_api_name
params
auth_scope
rate_limit_bucket
http_visibility
parser_status
batch_key
official_doc_ref
deadline_at
need_ids
```

- [ ] **Step 3: Extend gap/status enums**

Add `GapReason` values:

```text
permission_denied
provider_empty
parser_missing
resolver_mapping_missing
rate_limited_by_tool_budget
```

If adding `DataResultStatus.READY_RAW` is too invasive, use existing `PARTIAL` plus `parser_missing` gap for raw-only results. Do not map these cases to generic `provider_error`.

- [ ] **Step 4: Run model tests**

Run:

```bash
uv run pytest tests/unit/data_gateway/test_models.py tests/contracts/test_data_gap_reason_contract.py -q
```

Expected: pass.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/claw_trade/data_gateway/needs.py src/claw_trade/data_gateway/__init__.py src/claw_trade/data_gateway/models.py tests/unit/data_gateway/test_models.py tests/contracts/test_data_gap_reason_contract.py
git commit -m "feat: add DataNeed planning models"
```

### Task 3: Build Official Endpoint Catalog

**Files:**
- Create: `src/claw_trade/data_gateway/official_catalog/__init__.py`
- Create: `src/claw_trade/data_gateway/official_catalog/models.py`
- Create: `src/claw_trade/data_gateway/official_catalog/tushare.py`
- Create: `src/claw_trade/data_gateway/official_catalog/coinglass.py`
- Create: `src/claw_trade/data_gateway/official_catalog/finnhub.py`
- Create: `src/claw_trade/data_gateway/official_catalog/fred.py`
- Create: `src/claw_trade/data_gateway/official_catalog/glassnode.py`
- Create: `src/claw_trade/data_gateway/official_catalog/coingecko_pro.py`
- Create: `src/claw_trade/data_gateway/official_catalog/public_sources.py`
- Test: `tests/contracts/test_official_provider_catalog_contract.py`

- [ ] **Step 1: Define endpoint metadata model**

Minimum:

```python
class OfficialEndpoint(BaseModel):
    provider_id: str
    source_type: str
    endpoint_id: str
    official_path_or_api_name: str
    method: Literal["GET", "POST"]
    required_params: tuple[str, ...] = ()
    optional_params: tuple[str, ...] = ()
    auth: str
    rate_limit_bucket: str
    batch_policy: BatchPolicy
    parser_status: Literal["normalized", "raw_only", "parser_missing"]
    official_doc_ref: str
    http_visibility: HttpVisibility
```

- [ ] **Step 2: Add six paid provider catalogs**

Cover at least current report/focused needs:

- Tushare: daily, moneyflow, fina_indicator, income/balancesheet/cashflow, anns_d.
- Coinglass: funding, open interest, long/short, liquidation, options OI/volume, exchange netflow.
- Finnhub: company news, quote, financial metrics if existing provider uses it.
- FRED: series observations.
- Glassnode: core on-chain metric endpoint pattern.
- CoinGecko Pro: market data and asset metadata endpoints used by current crypto flows.

Do not add `allowed_worker`, `allowed_domain`, or report section fields.

- [ ] **Step 3: Add public/SDK endpoint metadata**

Add metadata for currently retained public/SDK providers so old capability tables do not become hidden whitelists:

- AkShare
- EastMoney
- mootdx
- Yahoo
- Binance
- existing HK/US public providers currently registered

For SDK-internal endpoints, set `http_visibility=sdk_internal_unknown` and include risk inventory data in Task 9.

- [ ] **Step 4: Run catalog contract**

Run:

```bash
uv run pytest tests/contracts/test_official_provider_catalog_contract.py -q
```

Expected: pass.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/claw_trade/data_gateway/official_catalog tests/contracts/test_official_provider_catalog_contract.py
git commit -m "feat: add official endpoint catalog"
```

### Task 4: Implement DataNeed Resolver And Planner

**Files:**
- Create: `src/claw_trade/data_gateway/planner/__init__.py`
- Create: `src/claw_trade/data_gateway/planner/need_resolver.py`
- Create: `src/claw_trade/data_gateway/planner/call_planner.py`
- Test: `tests/unit/data_gateway/test_data_need_planner.py`
- Test: `tests/unit/data_gateway/test_official_call_batch_keys.py`

- [ ] **Step 1: Implement resolver**

Resolver maps `DataNeed.need_kind` to catalog endpoint candidates.

Rules:

- Resolver returns candidate endpoints, not final provider winner.
- Resolver must not use worker/domain/report section.
- If no mapping exists, return `DataNeedGap(reason=resolver_mapping_missing)`.
- Unmapped need does not mean provider lacks ability.

- [ ] **Step 2: Implement call planner**

Planner builds `ProviderCallSpec` from catalog endpoint metadata and normalized need params.

Rules:

- Only planner can fill `official_path_or_api_name`.
- Worker input cannot pass this field.
- `batch_key` includes provider, catalog endpoint ID, official path/api name, auth scope, and params shape.

- [ ] **Step 3: Implement merge evidence**

For identical semantic needs:

```text
semantic_need_key = market + instrument + need_kind + granularity + normalized_time_range
```

Planner emits one call and records all `need_ids`.

- [ ] **Step 4: Run planner tests**

Run:

```bash
uv run pytest tests/unit/data_gateway/test_data_need_planner.py tests/unit/data_gateway/test_official_call_batch_keys.py -q
```

Expected: pass.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/claw_trade/data_gateway/planner tests/unit/data_gateway/test_data_need_planner.py tests/unit/data_gateway/test_official_call_batch_keys.py
git commit -m "feat: plan provider calls from DataNeed"
```

### Task 5: Remove Provider Candidate Business Whitelists

**Files:**
- Modify: `src/claw_trade/data_gateway/coordination/provider_selector.py`
- Modify: `src/claw_trade/data_gateway/providers/base.py`
- Test: `tests/contracts/test_provider_selector_no_business_whitelist.py`
- Test: `tests/unit/data_gateway/test_provider_selector.py`

- [ ] **Step 1: Make fields non-denying**

Remove candidate denial based on `fields` and `coverage_fields`.

Keep `coverage_fields` as provider metadata for parser/result quality checks only.

- [ ] **Step 2: Remove `source_role_required` hard filter**

`source_role` may sort candidates, but must not express business domain allowlist.

If a true legal/license block exists, represent it as license policy, not source role.

- [ ] **Step 3: Prove consumer labels do not change candidates**

Add/enable tests for `report/select/ui_probe/maintenance`.

- [ ] **Step 4: Run selector tests**

Run:

```bash
uv run pytest tests/contracts/test_provider_selector_no_business_whitelist.py tests/unit/data_gateway/test_provider_selector.py -q
```

Expected: pass.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/claw_trade/data_gateway/coordination/provider_selector.py src/claw_trade/data_gateway/providers/base.py tests/contracts/test_provider_selector_no_business_whitelist.py tests/unit/data_gateway/test_provider_selector.py
git commit -m "fix: stop provider selector business whitelisting"
```

### Task 6: Rework Official Raw Executor

**Files:**
- Modify: `src/claw_trade/data_gateway/providers/plugins/official_api.py`
- Modify: `src/claw_trade/data_gateway/providers/plugins/__init__.py`
- Test: `tests/unit/data_gateway/test_official_api_plugin.py`
- Test: `tests/contracts/test_official_provider_catalog_contract.py`

- [ ] **Step 1: Replace freeform path/api_name input**

Executor accepts planner-generated `ProviderCallSpec` or equivalent internal call payload.

Report/worker tool inputs must not be able to pass raw `path` or `api_name`.

- [ ] **Step 2: Register endpoint-granular capabilities**

Do not register every raw official call as one generic `official_api_call`.

Capabilities should reference catalog endpoint IDs.

- [ ] **Step 3: Preserve SSRF boundary**

Keep official base URL validation:

- no absolute user URL
- no `..`
- only provider base host
- redact keys in logs

- [ ] **Step 4: Run official API tests**

Run:

```bash
uv run pytest tests/unit/data_gateway/test_official_api_plugin.py tests/contracts/test_official_provider_catalog_contract.py -q
```

Expected: pass.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/claw_trade/data_gateway/providers/plugins/official_api.py src/claw_trade/data_gateway/providers/plugins/__init__.py tests/unit/data_gateway/test_official_api_plugin.py tests/contracts/test_official_provider_catalog_contract.py
git commit -m "fix: route official raw calls through catalog specs"
```

### Task 7: Implement Deadline-Aware Scheduler And Rate Limiter

**Files:**
- Modify: `src/claw_trade/data_gateway/coordination/scheduler.py`
- Modify: `src/claw_trade/data_gateway/execution/rate_limiter.py`
- Modify: `src/claw_trade/data_gateway/execution/gate.py`
- Modify: `src/claw_trade/data_gateway/execution/fetch_engine.py`
- Modify: `src/claw_trade/data_gateway/execution/rate_limit_policy.py`
- Modify: `src/claw_trade/runtime/settings_projection.py`
- Test: `tests/unit/data_gateway/test_rate_limiter.py`
- Test: `tests/unit/data_gateway/test_rate_limit_policy.py`
- Test: `tests/unit/data_gateway/test_execution_gate.py`
- Test: `tests/unit/data_gateway/test_fetch_engine.py`
- Create: `tests/unit/data_gateway/test_dynamic_rate_limit_scheduler.py`
- Create: `tests/integration/data_gateway/test_sliding_window_rate_limit.py`

- [ ] **Step 1: Remove provider wait timeout semantics**

`RateLimitPolicy` keeps:

```python
window_seconds: int
max_requests: int | None
safety_margin: int = 0
```

Do not use provider `wait_timeout_seconds` to decide wait behavior.

If settings still contain `rate_limit_wait_timeout_seconds`, ignore it or migrate it to task budget config. Do not feed it into provider policy.

- [ ] **Step 2: Add deadline-aware reserve**

Implement one of:

```python
reserve(key, policy, deadline_at: datetime | None)
```

or:

```python
peek_next_available(key, policy)
reserve_now(key, policy)
```

Required behavior:

- If within budget, wait until next sliding-window slot and reserve.
- If beyond deadline, return `rate_limited_by_tool_budget` without出网.

- [ ] **Step 3: Implement scheduler**

Scheduler input includes planned calls and `deadline_at`.

Scheduler output includes:

- scheduled calls
- skipped needs with `rate_limited_by_tool_budget`
- rate limit evidence
- merge evidence

- [ ] **Step 4: Ensure structured and official raw share bucket**

`crypto_coinglass_derivatives` and `official_api_coinglass` both use `ratelimit:coinglass`.

`cn_a_primary`, Tushare structured providers, and `official_api_tushare` all use `ratelimit:tushare`.

- [ ] **Step 5: Run rate limit tests**

Run:

```bash
uv run pytest \
  tests/unit/data_gateway/test_rate_limiter.py \
  tests/unit/data_gateway/test_rate_limit_policy.py \
  tests/unit/data_gateway/test_execution_gate.py \
  tests/unit/data_gateway/test_fetch_engine.py \
  tests/unit/data_gateway/test_dynamic_rate_limit_scheduler.py \
  tests/integration/data_gateway/test_sliding_window_rate_limit.py -q
```

Expected: pass.

- [ ] **Step 6: Commit**

Run:

```bash
git add src/claw_trade/data_gateway/coordination/scheduler.py src/claw_trade/data_gateway/execution/rate_limiter.py src/claw_trade/data_gateway/execution/gate.py src/claw_trade/data_gateway/execution/fetch_engine.py src/claw_trade/data_gateway/execution/rate_limit_policy.py src/claw_trade/runtime/settings_projection.py tests/unit/data_gateway/test_rate_limiter.py tests/unit/data_gateway/test_rate_limit_policy.py tests/unit/data_gateway/test_execution_gate.py tests/unit/data_gateway/test_fetch_engine.py tests/unit/data_gateway/test_dynamic_rate_limit_scheduler.py tests/integration/data_gateway/test_sliding_window_rate_limit.py
git commit -m "feat: schedule provider calls by sliding-window budget"
```

### Task 8: Replace Fixed Remote Packs With DataNeed Flow

**Files:**
- Modify: `src/claw_trade/reports/data_pack_bridge.py`
- Modify: `openclaw_plugins/claw-trade-frontline-tools/index.js`
- Modify: `openclaw_plugins/claw-trade-frontline-tools/openclaw.plugin.json`
- Modify: `src/claw_trade/runtime/openclaw_client.py`
- Modify: `src/claw_trade/config/tool_names.py`
- Test: `tests/contracts/test_data_need_tool_contract.py`
- Test: `tests/contracts/test_worker_provider_payload_no_provider_leak.py`
- Test: `tests/contracts/test_frontline_tool_protocol.py`
- Test: `tests/unit/reports/test_data_pack_bridge.py`
- Create: `tests/integration/data_gateway/test_btc_market_data_need_no_fixed_17_pack.py`

- [ ] **Step 1: Add `claw_request_data` frontend tool**

Tool schema accepts `need_kind`, `instrument`, `market`, `time_range`, `granularity`, `purpose`, and optional `priority`.

It must not accept provider/path/api_name/url/header/token.

- [ ] **Step 2: Route `claw_request_data` to DataNeed planner**

Node plugin passes tool input to Python bridge.

Python bridge builds `DataNeed`, injects `deadline_at`, and calls planner/DataAPI execution.

- [ ] **Step 3: Make old pack tools cache/summary only**

Existing pack tools may stay temporarily for compatibility, but they must:

- read cache/summary/results
- not call `_build_requests`
- not generate fixed remote request lists
- not trigger `_CN_A_DOMAIN_DATASETS` or `_CRYPTO_DOMAIN_DATASETS` as remote source

- [ ] **Step 4: Remove CRYPTO market fixed 17-request remote path**

BTC market worker must request specific needs such as funding, OI, long/short, options, on-chain netflow through `DataNeed`.

Test must fail if one market pack call generates the old fixed request count.

- [ ] **Step 5: Decide manifest compatibility**

`report_prefetch_manifest_path` may remain only as a read-only cache/summary source.

It must not be a hidden prefetch/remote request entry.

- [ ] **Step 6: Run frontend/tool tests**

Run:

```bash
uv run pytest \
  tests/contracts/test_data_need_tool_contract.py \
  tests/contracts/test_worker_provider_payload_no_provider_leak.py \
  tests/contracts/test_frontline_tool_protocol.py \
  tests/unit/reports/test_data_pack_bridge.py \
  tests/integration/data_gateway/test_btc_market_data_need_no_fixed_17_pack.py -q
```

Expected: pass.

- [ ] **Step 7: Commit**

Run:

```bash
git add src/claw_trade/reports/data_pack_bridge.py openclaw_plugins/claw-trade-frontline-tools/index.js openclaw_plugins/claw-trade-frontline-tools/openclaw.plugin.json src/claw_trade/runtime/openclaw_client.py src/claw_trade/config/tool_names.py tests/contracts/test_data_need_tool_contract.py tests/contracts/test_worker_provider_payload_no_provider_leak.py tests/contracts/test_frontline_tool_protocol.py tests/unit/reports/test_data_pack_bridge.py tests/integration/data_gateway/test_btc_market_data_need_no_fixed_17_pack.py
git commit -m "feat: route frontline data through DataNeed"
```

### Task 9: Add SDK Internal HTTP Risk Inventory

**Files:**
- Create: `src/claw_trade/data_gateway/providers/sdk_risk_inventory.py`
- Test: `tests/unit/data_gateway/test_sdk_internal_http_risk_inventory.py`
- Potentially modify provider plugin metadata files under `src/claw_trade/data_gateway/providers/plugins/**`

- [ ] **Step 1: Inventory all `sdk_internal_unknown` endpoints**

Inventory fields:

```python
provider_id
endpoint_id
why_sdk_internal_unknown
outer_gate_covered
subrequest_visibility
migration_priority
live_evidence_ref
residual_risk
```

- [ ] **Step 2: Fail if unknown endpoint lacks inventory**

Every capability with `http_visibility=sdk_internal_unknown` must have an inventory row.

- [ ] **Step 3: Prioritize migration to managed_http**

Mark high-priority endpoints for A股 report/select and BTC report first.

- [ ] **Step 4: Run inventory test**

Run:

```bash
uv run pytest tests/unit/data_gateway/test_sdk_internal_http_risk_inventory.py -q
```

Expected: pass.

- [ ] **Step 5: Commit**

Run:

```bash
git add src/claw_trade/data_gateway/providers/sdk_risk_inventory.py tests/unit/data_gateway/test_sdk_internal_http_risk_inventory.py src/claw_trade/data_gateway/providers/plugins
git commit -m "chore: inventory sdk-internal provider http risks"
```

### Task 10: Focused Regression And Live Proof

**Files:**
- Update evidence under `docs/evidence/data-need-rate-limit-rework-20260612/`
- Update `memory/2026-06-12.md`

- [ ] **Step 1: Run focused contract suite**

Run:

```bash
uv run pytest \
  tests/contracts/test_data_need_tool_contract.py \
  tests/contracts/test_worker_provider_payload_no_provider_leak.py \
  tests/contracts/test_provider_selector_no_business_whitelist.py \
  tests/contracts/test_official_provider_catalog_contract.py \
  tests/contracts/test_data_gap_reason_contract.py \
  tests/unit/data_gateway/test_data_need_planner.py \
  tests/unit/data_gateway/test_dynamic_rate_limit_scheduler.py \
  tests/unit/data_gateway/test_official_call_batch_keys.py \
  tests/integration/data_gateway/test_sliding_window_rate_limit.py \
  tests/integration/data_gateway/test_btc_market_data_need_no_fixed_17_pack.py -q
```

Expected: all pass.

- [ ] **Step 2: Run A股 focused live**

Use fixed runtime preflight from AGENTS:

```bash
scripts/start-control-runtime.sh -- uv run python scripts/run_claw_trade_fresh_report.py \
  --ticker 600519.SH \
  --company-name 贵州茅台 \
  --market CN_A \
  --profile CN_A \
  --currency CNY \
  --currency-symbol ¥ \
  --current-date 2026-06-12 \
  --start-date 2025-06-12 \
  --end-date 2026-06-12 \
  --output-dir docs/evidence/data-need-rate-limit-rework-20260612/cn_a_600519
```

Required evidence:

- provider payload tool schema has no provider/path/api_name/url/header/token
- data needs for capital flow, filings, order book, financial metrics
- consumer label does not change provider candidates
- no data-layer terminal `no_candidate`

- [ ] **Step 3: Run BTC focused live**

```bash
scripts/start-control-runtime.sh -- uv run python scripts/run_claw_trade_fresh_report.py \
  --ticker BTC \
  --company-name Bitcoin \
  --market CRYPTO \
  --profile CRYPTO \
  --currency USDT \
  --currency-symbol USDT \
  --current-date 2026-06-12 \
  --start-date 2025-06-12 \
  --end-date 2026-06-12 \
  --output-dir docs/evidence/data-need-rate-limit-rework-20260612/crypto_btc
```

Required evidence:

- market worker does not generate old fixed 17 remote requests
- data needs cover funding/OI/long-short/options/on-chain netflow
- Coinglass/Glassnode/CoinGecko Pro selected by data layer, not worker prompt
- rate limit bucket evidence visible

- [ ] **Step 4: Run forced rate-limit proof**

Configure a test source:

```text
coinglass: 10 / 60s
```

Trigger more than 10 same-provider calls.

Required evidence:

- any continuous 60 seconds has at most 10 true HTTP calls
- 11th call waits if `deadline_at` permits
- otherwise gap is `rate_limited_by_tool_budget`
- structured and official raw call share same `ratelimit:<source>`

- [ ] **Step 5: Update memory**

Append `memory/2026-06-12.md` with:

```text
- completed tasks
- changed files
- commands and exit codes
- live run IDs
- evidence paths
- remaining gaps
- confirmation that no mock/stub/fake/fallback was used as live proof
```

- [ ] **Step 6: Commit evidence**

Run:

```bash
git add docs/evidence/data-need-rate-limit-rework-20260612 memory/2026-06-12.md
git commit -m "test: prove DataNeed rate-limit rework"
```

## Final Acceptance

Before claiming completion:

- Run `git status --short`.
- Run the focused contract suite from Task 10 Step 1.
- Confirm A股 and BTC live evidence exists.
- Confirm provider payload schemas contain no provider/path/api_name/url/header/token.
- Confirm no final data-layer gap uses terminal `no_candidate`.
- Confirm sliding-window proof shows任意连续 60 秒不超过配置值.

Do not claim full completion if live runs are skipped. Mark as `NOT VERIFIED LIVE`.
