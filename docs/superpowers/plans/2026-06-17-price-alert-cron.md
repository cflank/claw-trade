# Price Alert Cron Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the price alert product loop with persistent alerts, market/frequency scan buckets, real data-gateway quotes, deterministic trigger checks, and user-visible notifications.

**Architecture:** OpenClaw cron owns the clock only. claw-trade owns alert state, scan bucket state, quote requests, trigger comparisons, notification decisions, and user-facing DTOs. Price alerts never invoke the report workflow, never let an LLM decide triggers, and never create one cron job per alert.

**Tech Stack:** Python `uv`, FastAPI UI backend, OpenClaw Gateway RPC cron methods, claw-trade data gateway, JSON store or existing repository injection, pytest.

---

## Scope Freeze

This plan implements price alerts only. Scheduled report cron and background data maintenance can share the same adapter/store foundations, but they are not acceptance criteria for this plan unless a task explicitly says so.

First-version product rules:

- Supported alert conditions: `price_threshold`, `percent_change`.
- Unsupported: technical indicators, AI explanation, automatic report generation, report confirmation cards.
- Supported markets for automatic scan: `CRYPTO`, `CN_A`.
- `US` scan bucket may exist but must return `skipped` until quote and calendar live evidence exists.
- `HK` price alerts are rejected with `INVALID_INPUT` until HK quote, calendar, and symbol strategy are separately approved.
- Triggered alerts send one notification and then close by default.
- If the configured Channel is unavailable, write an in-app notification; do not claim a WeChat send succeeded.

## File Structure

- Modify: `src/claw_trade/ui_backend/price_alert_service.py`
  Owns alert CRUD, deterministic comparison, notification dedupe, and DTO conversion.

- Create: `src/claw_trade/ui_backend/scheduled_work_store.py`
  Persistent store for price alerts and scan buckets. First implementation may use atomic JSON under the UI run root.

- Create: `src/claw_trade/ui_backend/price_alert_scan_service.py`
  Loads active alerts from one bucket, groups by instrument, fetches one quote per instrument, evaluates alerts, and saves scan summaries.

- Create or modify: `src/claw_trade/ui_backend/openclaw_cron_adapter.py`
  Thin wrapper around OpenClaw Gateway cron methods. It must not know price alert business rules.

- Create or modify: `src/claw_trade/ui_backend/scheduled_work_runner.py`
  Internal cron wake entry. For `kind=price_alert_scan`, calls `PriceAlertScanService.scan_bucket`.

- Create: `src/claw_trade/data_gateway/price_quote_provider.py`
  Reads quote payloads through data gateway APIs and validates `current_price`, `quote_timestamp`, and `evidence_ref`.

- Modify: `src/claw_trade/data_gateway/source_probe.py`
  `build_price_alert_quote_provider()` only assembles the real provider; it must not directly call third-party HTTP.

- Modify: `src/claw_trade/web/state.py`
  Wires store, cron adapter, quote provider, scan service, and price alert service.

- Modify: `src/claw_trade/web/routes_ui.py`
  Keep existing user APIs stable. Add an internal cron wake route only if needed, with internal authentication.

- Create: `agents/price_alert_scan_worker/IDENTITY.md`
- Create: `agents/price_alert_scan_worker/USER.md`
- Create: `agents/price_alert_scan_worker/SKILLS.md`
- Create: `agents/price_alert_scan_worker/STAGES.yaml`
  Worker profile only wakes the scan entry; it must not judge prices, write reports, or give investment advice.

- Test: `tests/unit/ui/test_scheduled_work_store.py`
- Test: `tests/unit/ui/test_price_alert_scan_service.py`
- Test: `tests/unit/ui/test_price_alert_service.py`
- Test: `tests/unit/data_gateway/test_price_alert_quote_provider.py`
- Test: `tests/integration/test_openclaw_cron_price_alert_contract.py`
- Test: `tests/contracts/test_scheduled_and_alert_api_contracts.py`

## Task 1: Freeze Current Behavior

**Files:**
- Read: `AGENTS.md`
- Read: `docs/UI需求分析.md`
- Read: `docs/UI详细设计.md`
- Read: `docs/定时任务接入OpenClaw设计.md`
- Read: `src/claw_trade/ui_backend/price_alert_service.py`
- Read: `src/claw_trade/data_gateway/source_probe.py`
- Create: `docs/evidence/price-alert-current-state-20260617.md`

- [ ] **Step 1: Record current facts**

Write the evidence file with:

```text
PriceAlertService storage is in-memory or persistent.
Default quote provider behavior.
Existing API routes.
Existing front-end call sites.
Existing tests that use fake quotes.
Missing scan bucket / cron / quote provider pieces.
```

- [ ] **Step 2: Run current focused tests**

Run:

```bash
uv run pytest tests/unit/ui/test_price_alert_service.py tests/contracts/test_scheduled_and_alert_api_contracts.py -q
```

Expected:

```text
PASS, or record failures without bypassing them.
```

## Task 2: Persistent Store And Alert Schema

**Files:**
- Create: `src/claw_trade/ui_backend/scheduled_work_store.py`
- Modify: `src/claw_trade/ui_backend/price_alert_service.py`
- Test: `tests/unit/ui/test_scheduled_work_store.py`
- Test: `tests/unit/ui/test_price_alert_service.py`

- [ ] **Step 1: Write store tests**

Cover:

```text
save/load price alert
list active alerts by market
save/load scan bucket
save scan summary
JSON parse failure fails closed
user DTO excludes openclaw cron id, scan bucket, evidence refs, dedupe key
```

- [ ] **Step 2: Define internal records**

Required internal alert fields:

```text
id
instrument_code
instrument_name
market
condition
condition_version
notification
state
scan_bucket
last_checked_at
triggered_at
last_error_message
last_quote_evidence_ref
last_scan_run_id
notification_dedupe_key
created_at
updated_at
```

Required scan bucket fields:

```text
bucket_key
market
frequency
enabled
openclaw_cron_job_id
last_scan_run_id
last_scan_summary
last_error_message
skipped_reason
created_at
updated_at
```

- [ ] **Step 3: Implement atomic JSON store**

Use the UI run root path:

```text
<run_root>/.ui-scheduled-work.json
```

Requirements:

```text
atomic write
no swallowed JSON parse errors
no successful load from corrupt state
created_at / updated_at preserved
```

- [ ] **Step 4: Replace in-memory alert state**

`PriceAlertService` reads and writes through the store. Process-local idempotency may remain only as an optimization.

- [ ] **Step 5: Run tests**

Run:

```bash
uv run pytest tests/unit/ui/test_scheduled_work_store.py tests/unit/ui/test_price_alert_service.py -q
```

Expected:

```text
PASS
```

Stop conditions:

- Store corruption is silently ignored.
- User DTO exposes cron ids, evidence refs, or dedupe keys.

## Task 3: Cron Adapter And Scan Bucket Provisioning

**Files:**
- Create or modify: `src/claw_trade/ui_backend/openclaw_cron_adapter.py`
- Modify: `src/claw_trade/ui_backend/price_alert_service.py`
- Test: `tests/integration/test_openclaw_cron_price_alert_contract.py`

- [ ] **Step 1: Write adapter contract tests**

Use a fake Gateway client and assert:

```text
CRYPTO alert creates/uses bucket CRYPTO:3m
CN_A alert creates/uses bucket CN_A:3m
two CRYPTO alerts create one price-alert-scan:CRYPTO:3m cron job
US alert bucket exists but scan is skipped until enabled
HK alert returns INVALID_INPUT and creates no alert or cron job
```

- [ ] **Step 2: Implement thin cron adapter**

Adapter methods:

```python
add_job(...)
update_job(...)
remove_job(...)
run_job(...)
list_jobs(...)
status(...)
runs(...)
```

The adapter only maps protocol payloads.

- [ ] **Step 3: Ensure bucket on create**

On alert creation:

```text
validate market
normalize condition
bucket_key = "{market}:3m"
ensure scan bucket
create OpenClaw cron job only if bucket has no cron id
save alert with scan_bucket
```

- [ ] **Step 4: Run tests**

Run:

```bash
uv run pytest tests/integration/test_openclaw_cron_price_alert_contract.py tests/unit/ui/test_price_alert_service.py -q
```

Expected:

```text
PASS
```

Stop conditions:

- One cron job per alert.
- Cron adapter contains price alert business rules.
- HK silently falls back to another market.

## Task 4: Real Quote Provider

**Files:**
- Create: `src/claw_trade/data_gateway/price_quote_provider.py`
- Modify: `src/claw_trade/data_gateway/source_probe.py`
- Modify: `src/claw_trade/web/state.py`
- Test: `tests/unit/data_gateway/test_price_alert_quote_provider.py`

- [ ] **Step 1: Write fail-closed tests**

Cover:

```text
missing evidence chain -> evidence_chain_unavailable
data API has no quote capability -> price_alert_quote_unavailable
missing current_price -> invalid_quote_payload
missing quote_timestamp -> invalid_quote_payload
missing evidence_ref -> invalid_quote_payload
stale quote -> stale_quote_unavailable
24h percent alert missing percent_change_24h -> invalid_quote_payload
intraday percent alert missing percent_change_intraday -> invalid_quote_payload
```

- [ ] **Step 2: Write success tests**

Fake data gateway returns:

```python
{
    "current_price": 71000.0,
    "percent_change_24h": 2.5,
    "percent_change_intraday": 1.2,
    "quote_timestamp": "2026-06-17T12:00:00Z",
    "evidence_ref": "dataset://normalized/quote_snapshot/...",
    "source_market_session": "continuous",
}
```

- [ ] **Step 3: Implement provider**

Rules:

```text
use DataAPI.get_data or get_data_batch
consumer = price_alert
prefer quote_snapshot
use recent intraday bar only when approved by data gateway evidence
do not use daily close as current price fallback
preserve gaps and evidence refs
```

- [ ] **Step 4: Wire source_probe**

`build_price_alert_quote_provider()` constructs `PriceAlertQuoteProvider`; it must not call bare HTTP.

- [ ] **Step 5: Run tests**

Run:

```bash
uv run pytest tests/unit/data_gateway/test_price_alert_quote_provider.py tests/unit/ui/test_price_alert_service.py -q
```

Expected:

```text
PASS
```

Stop conditions:

- Need to hard-code a new provider source.
- Need to fake `quote_timestamp` or `evidence_ref`.
- Need to use yesterday close as current price.

## Task 5: Batch Scan Service

**Files:**
- Create: `src/claw_trade/ui_backend/price_alert_scan_service.py`
- Modify: `src/claw_trade/ui_backend/price_alert_service.py`
- Test: `tests/unit/ui/test_price_alert_scan_service.py`

- [ ] **Step 1: Write scan tests**

Cover:

```text
loads active alerts for bucket
groups same instrument into one quote request
triggered alert closes and records evidence
non-triggered alert remains active
quote failure records alert error and scan failure count
CRYPTO never market-skips
CN_A skips outside trading hours without quote request
US skipped until enabled
```

- [ ] **Step 2: Implement scan service**

Core method:

```python
scan_bucket(bucket_key: str, cron_run_id: str) -> PriceAlertScanSummary
```

Summary fields:

```text
scan_run_id
bucket_key
cron_run_id
active_alert_count
grouped_quote_count
triggered_alert_count
skipped_alert_count
failed_alert_count
quote_evidence_refs
started_at
finished_at
```

- [ ] **Step 3: Reuse PriceAlertService comparison**

Do not duplicate trigger comparison logic. Expose a narrow internal method if needed.

- [ ] **Step 4: Run tests**

Run:

```bash
uv run pytest tests/unit/ui/test_price_alert_scan_service.py tests/unit/ui/test_price_alert_service.py -q
```

Expected:

```text
PASS
```

Stop conditions:

- Scan service calls LLM.
- Scan service generates reports.
- Same ticker causes repeated quote fetches in one scan.

## Task 6: Notification Delivery And In-App Fallback

**Files:**
- Modify: `src/claw_trade/ui_backend/price_alert_service.py`
- Modify or create: notification/message store integration near existing UI backend notification services
- Test: `tests/unit/ui/test_price_alert_service.py`
- Test: `tests/e2e/ui/test_report_user_flows.py`

- [ ] **Step 1: Write notification tests**

Cover:

```text
configured channel send success records notification result
channel unavailable writes in-app message
in-app success is not reported as WeChat success
same alert_id + condition_version + quote_timestamp + trigger_side does not notify twice
triggered alert does not create report confirmation card
```

- [ ] **Step 2: Implement notification decision**

Rules:

```text
if notification.channel enabled and available -> send through ChannelBridge
else -> append in-app notification/chat message
always persist dedupe key
do not change triggered into not-triggered when channel fails
```

- [ ] **Step 3: Run tests**

Run:

```bash
uv run pytest tests/unit/ui/test_price_alert_service.py tests/e2e/ui/test_report_user_flows.py -q
```

Expected:

```text
PASS
```

Stop conditions:

- Fake message id is treated as real channel success.
- Price alert trigger creates or enqueues a report.

## Task 7: Worker Profile And Internal Wake Route

**Files:**
- Create: `agents/price_alert_scan_worker/IDENTITY.md`
- Create: `agents/price_alert_scan_worker/USER.md`
- Create: `agents/price_alert_scan_worker/SKILLS.md`
- Create: `agents/price_alert_scan_worker/STAGES.yaml`
- Modify: `src/claw_trade/ui_backend/scheduled_work_runner.py`
- Modify: `src/claw_trade/web/routes_ui.py`
- Test: `tests/integration/test_openclaw_cron_price_alert_contract.py`

- [ ] **Step 1: Write wake route tests**

Cover:

```text
missing internal token -> 403
unknown kind -> INVALID_INPUT
kind=price_alert_scan calls PriceAlertScanService.scan_bucket
response includes business id, scan summary, and error status
```

- [ ] **Step 2: Create worker profile**

Profile must say:

```text
wake scan entry only
do not judge price
do not call report workflow
do not write investment advice
do not fabricate quote results
```

- [ ] **Step 3: Implement runner dispatch**

Supported wake payload:

```python
{
    "kind": "price_alert_scan",
    "bucketKey": "CRYPTO:3m",
    "cronRunId": "cron-run-1"
}
```

- [ ] **Step 4: Run tests**

Run:

```bash
uv run pytest tests/integration/test_openclaw_cron_price_alert_contract.py -q
```

Expected:

```text
PASS
```

Stop conditions:

- Worker prompt contains investment/report instructions.
- Wake route is public without internal auth.

## Task 8: Live Runtime Proof

**Files:**
- Create: `docs/evidence/price-alert-cron-live-20260617.md`
- Modify: `memory/2026-06-17.md`

- [ ] **Step 1: Run runtime preflight**

Use the project-required runtime entry:

```bash
scripts/start-control-runtime.sh -- <focused price alert cron proof command>
```

Expected:

```text
OpenViking health pass
OpenClaw gateway health pass
runtime.env generated
```

- [ ] **Step 2: Create two CRYPTO alerts**

Expected:

```text
one price-alert-scan:CRYPTO:3m cron job
two active alerts in same bucket
```

- [ ] **Step 3: Force cron run**

Expected:

```text
OpenClaw cron run history contains run id
price_alert_scan_worker wake evidence exists
scan summary exists
same ticker quote grouped once
```

- [ ] **Step 4: Record quote outcome**

If live quote data is unavailable, expected result is fail closed:

```text
provider/quote evidence or fail-closed reason recorded
no fake trigger
no fake notification
```

- [ ] **Step 5: Write evidence**

Evidence file must include:

```text
commands
exit codes
cron job id
cron run id
worker id
alert ids
scan summary
quote evidence refs or fail-closed reason
notification result
deviation
```

Stop conditions:

- Runtime was not started through `scripts/start-control-runtime.sh`.
- No real OpenClaw worker wake evidence.
- Fake Gateway tests are used as live proof.

## Final Verification

Run:

```bash
uv run pytest tests/unit/ui/test_scheduled_work_store.py tests/unit/ui/test_price_alert_service.py tests/unit/ui/test_price_alert_scan_service.py tests/unit/data_gateway/test_price_alert_quote_provider.py tests/contracts/test_scheduled_and_alert_api_contracts.py tests/integration/test_openclaw_cron_price_alert_contract.py -q
```

Expected:

```text
PASS
```

Do not commit automatically. If the user later asks to commit, inspect `git status --short` first and commit only the relevant files.
