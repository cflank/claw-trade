# Price Alert Current State Evidence - 2026-06-17

This file records the price-alert baseline before the OpenClaw cron implementation.
Because the evidence file was written after implementation work started, baseline
facts below are reconstructed from `HEAD:<path>` for the branch start point.

## Baseline Source Snapshot

- `HEAD:src/claw_trade/ui_backend/price_alert_service.py`
- `HEAD:src/claw_trade/data_gateway/source_probe.py`
- `HEAD:src/claw_trade/web/routes_ui.py`
- `HEAD:tests/unit/ui/test_price_alert_service.py`
- `HEAD:tests/contracts/test_scheduled_and_alert_api_contracts.py`
- `web/research-ui/src/api/client.ts`

## Facts

### PriceAlertService Storage

Baseline `PriceAlertService` stored alerts in process memory:

- `_items: dict[str, PriceAlert] = {}`
- `_idempotency: dict[str, Any] = {}`
- `_seq = 0`

There was no persisted scheduled-work store, no scan bucket record, and no
OpenClaw cron job id stored with the alert.

### Default Quote Provider Behavior

`build_price_alert_quote_provider()` failed closed:

- missing evidence-chain dependencies raised
  `datasource_test_failed: evidence_chain_unavailable`
- present evidence-chain dependencies still raised
  `datasource_test_failed: price_alert_quote_provider_unimplemented`

It did not call third-party HTTP directly. It also did not yet route through a
real data-gateway quote API.

### Existing API Routes

Baseline UI backend routes existed for manual price-alert actions:

- `POST /api/ui/create-price-alert`
- `POST /api/ui/pause-price-alert`
- `POST /api/ui/resume-price-alert`
- `POST /api/ui/delete-price-alert`
- `POST /api/ui/run-price-alert-now`

There was no internal scheduled-work cron wake route for price-alert scans.

### Existing Front-End Call Sites

`web/research-ui/src/api/client.ts` already called the same user-facing price
alert routes listed above.

### Existing Fake-Quote Tests

Baseline tests used in-process fake quote providers for service behavior:

- threshold and percent-change creation
- market-specific code normalization
- manual run triggering and closing
- paused alert skip
- quote-provider failure setting error state
- user DTO contract safety

### Missing Pieces

The baseline did not have:

- persistent alert and scan-bucket JSON store
- market/frequency scan buckets such as `CRYPTO:3m`
- OpenClaw cron adapter for price-alert scan jobs
- real data-gateway quote provider for `consumer=price_alert`
- batch scan service grouping multiple alerts by instrument
- notification dedupe result persistence
- `price_alert_scan_worker` OpenClaw profile
- internal authenticated cron wake route
- live runtime proof that OpenClaw cron wakes the scan worker

## Baseline Test Evidence

The exact pre-edit test run was not captured before implementation began. The
baseline assertions above come from `HEAD:` source inspection. The same contract
surface is covered by the final regression run recorded in
`docs/evidence/price-alert-cron-live-20260617.md`.
