# Price Alert Cron Live Evidence - 2026-06-17

## Verdict

Price-alert cron implementation reached a real OpenClaw wake path:

- two CRYPTO alerts were created through the UI API
- one `price-alert-scan:CRYPTO:3m` cron job was created
- OpenClaw cron ran `price_alert_scan_worker`
- the worker called only `claw-trade-scheduled-work-wake`
- the internal UI route handled the wake and produced a scan summary
- live quote data was unavailable, so alerts failed closed with no fake trigger
  and no fake notification

## Runtime Profile

The live proof used the project runtime entry through `scripts/start-research-ui.sh`,
which delegates service startup through `scripts/start-control-runtime.sh`.

Command:

```bash
CLAW_TRADE_ENV_PATH=/home/frank/src/claw-trade/.env.local \
CN_A_MONGODB_PORT=27018 \
CN_A_MONGODB_URI=mongodb://127.0.0.1:27018/claw_trade \
DATA_GATEWAY_MONGODB_URI=mongodb://127.0.0.1:27018/claw_trade \
DATA_GATEWAY_SEED_MONGODB_URI=mongodb://127.0.0.1:27018/claw_trade \
OPENCLAW_STATE_DIR=/tmp/claw-trade-price-alert-cron/.runtime/dev-services/openclaw-state-price-alert-proof-urlfix-20260617 \
UV_CACHE_DIR=/tmp/claw-trade-price-alert-cron/.runtime/dev-services/uv-cache \
UV_LINK_MODE=copy \
scripts/start-research-ui.sh
```

Notes:

- The sandboxed MongoDB startup failed with `set_option: Operation not permitted`;
  the same command was rerun with approved escalation.
- The worktree did not contain `.env.local`, so the command explicitly used
  `/home/frank/src/claw-trade/.env.local`.
- Provider evidence: `CLAW_TRADE_LLM_PROVIDER=deepseek`.
- Model evidence: `CLAW_TRADE_LLM_MODEL=deepseek/deepseek-chat`.
- `OPENVIKING_ENDPOINT=http://127.0.0.1:1933`.
- `OPENCLAW_GATEWAY_URL=ws://127.0.0.1:18789`.
- `CLAW_TRADE_OPENVIKING_MCP_STARTED=0`.
- Internal token value was not copied into this evidence file.

Health checks during the live proof:

```text
OpenViking 1933 health: pass
OpenClaw 18789 health: pass
Research UI backend 8787: pass
```

The long-running runtime received `SIGTERM` after the proof and stopped cleanly.
The server process exit code is not used as proof; durable OpenClaw cron/session
files and UI store files are the proof artifacts.

## Alert Creation

Created through `POST /api/ui/create-price-alert`:

| Alert | Market | Instrument | Condition | State After Create |
| --- | --- | --- | --- | --- |
| `alert-1` | `CRYPTO` | `BTC` | price above `1` | `active` |
| `alert-2` | `CRYPTO` | `BTC` | price below `999999` | `active` |

Backend log evidence:

```text
POST /api/ui/create-price-alert HTTP/1.1" 200 OK
POST /api/ui/create-price-alert HTTP/1.1" 200 OK
```

Store evidence after creation:

- scan bucket: `CRYPTO:3m`
- OpenClaw cron job id: `2d74c259-ce68-4b1e-9c05-19fab6b79673`
- only one cron job was registered for both alerts

## Cron Job Evidence

Durable file:

```text
.runtime/dev-services/openclaw-state-price-alert-proof-urlfix-20260617/cron/jobs.json
```

Job:

```json
{
  "id": "2d74c259-ce68-4b1e-9c05-19fab6b79673",
  "agentId": "price_alert_scan_worker",
  "name": "price-alert-scan:CRYPTO:3m",
  "enabled": true,
  "schedule": {"kind": "every", "everyMs": 180000},
  "payload": {
    "kind": "agentTurn",
    "toolsAllow": ["claw-trade-scheduled-work-wake"]
  },
  "delivery": {"mode": "none"}
}
```

`cron.run` result:

```json
{
  "ok": true,
  "enqueued": true,
  "runId": "manual:2d74c259-ce68-4b1e-9c05-19fab6b79673:1781742105711:1"
}
```

Durable run state:

```text
lastRunStatus: ok
lastDeliveryStatus: not-requested
lastDurationMs: 20055
consecutiveErrors: 0
```

## Worker Wake Evidence

Durable files:

```text
.runtime/dev-services/openclaw-state-price-alert-proof-urlfix-20260617/cron/runs/2d74c259-ce68-4b1e-9c05-19fab6b79673.jsonl
.runtime/dev-services/openclaw-state-price-alert-proof-urlfix-20260617/agents/price_alert_scan_worker/sessions/7f42c065-2570-4ad8-8c95-e6b98c63ec0f.jsonl
.runtime/dev-services/openclaw-state-price-alert-proof-urlfix-20260617/agents/price_alert_scan_worker/sessions/7f42c065-2570-4ad8-8c95-e6b98c63ec0f.trajectory.jsonl
```

Run history:

```json
{
  "jobId": "2d74c259-ce68-4b1e-9c05-19fab6b79673",
  "action": "finished",
  "status": "ok",
  "sessionId": "7f42c065-2570-4ad8-8c95-e6b98c63ec0f",
  "sessionKey": "agent:price_alert_scan_worker:cron:2d74c259-ce68-4b1e-9c05-19fab6b79673:run:7f42c065-2570-4ad8-8c95-e6b98c63ec0f",
  "model": "deepseek-chat",
  "provider": "deepseek"
}
```

The worker session shows exactly one tool call:

```json
{
  "name": "claw-trade-scheduled-work-wake",
  "arguments": {
    "kind": "price_alert_scan",
    "bucketKey": "CRYPTO:3m",
    "cronRunId": "auto"
  }
}
```

Backend route evidence:

```text
POST /api/ui/internal/scheduled-work/cron-wake HTTP/1.1" 200 OK
```

## Scan Summary

Persisted in `runs/.ui-scheduled-work.json`:

```json
{
  "bucket_key": "CRYPTO:3m",
  "active_alert_count": 2,
  "grouped_quote_count": 1,
  "triggered_alert_count": 0,
  "skipped_alert_count": 0,
  "failed_alert_count": 2,
  "quote_evidence_refs": [],
  "started_at": "2026-06-18T00:22:03Z",
  "finished_at": "2026-06-18T00:22:03Z"
}
```

Outcome:

- same ticker grouped once: `grouped_quote_count=1`
- no fake quote evidence: `quote_evidence_refs=[]`
- no fake trigger: `triggered_alert_count=0`
- no fake notification: both alerts have `last_notification_result=null`
- both alerts failed closed with user-facing error:
  `价格提醒检查失败，请稍后重试。`

## Deviations And Fixes Found During Live Proof

1. Worktree runtime missed `.env.local`.
   - Fix for proof: explicit `CLAW_TRADE_ENV_PATH=/home/frank/src/claw-trade/.env.local`.
   - Final proof provider: DeepSeek.

2. Concurrent create could provision two cron jobs for one bucket.
   - Fix: serialized `PriceAlertService.create_price_alert()` mutation path.
   - Test: `test_concurrent_price_alert_creation_provisions_one_scan_bucket_cron`.

3. OpenClaw cron RPC parameter shape changed.
   - Fix: gateway cron client now sends current `id`/`patch`/`mode` shapes.
   - Test: `test_cron_methods_use_current_gateway_param_shapes`.

4. Scheduled-work plugin dropped the `/api/ui` route prefix.
   - Fix: internal wake URL now preserves the base UI API prefix.
   - Tests:
     `test_scheduled_work_tool_preserves_ui_api_prefix_for_internal_wake`,
     `test_internal_cron_wake_route_works_with_research_ui_api_prefix`.

## Current Limitation

The cron path is implemented and live-proven. Triggering a real notification
still requires a configured data-gateway quote source that can return a valid
`current_price`, `quote_timestamp`, and `evidence_ref` for the target market.
Without that evidence, the system correctly fails closed.

## Final Regression

Command:

```bash
uv run pytest tests/unit/ui/test_scheduled_work_store.py tests/unit/ui/test_price_alert_service.py tests/unit/ui/test_price_alert_scan_service.py tests/unit/ui/test_openclaw_gateway_rpc_client.py tests/unit/data_gateway/test_price_alert_quote_provider.py tests/contracts/test_scheduled_and_alert_api_contracts.py tests/contracts/test_scheduled_work_tool_contract.py tests/integration/test_openclaw_cron_price_alert_contract.py tests/e2e/ui/test_report_user_flows.py tests/unit/test_start_control_runtime_script.py -q
```

Result:

```text
101 passed in 2.72s
```
