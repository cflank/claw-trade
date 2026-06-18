---
name: scheduled-work-wake
version: 0.1.0
description: Price alert scheduled-work wake skill; exposes only the internal wake tool.
tools:
  - claw-trade-scheduled-work-wake
entrypoint: openclaw_plugins/claw-trade-scheduled-work-tools/index.js
schema_version: claw_scheduled_work_wake.v1
---

# scheduled-work-wake

Call only `claw-trade-scheduled-work-wake` with the cron-provided payload.

Do not compare prices, write investment commentary, enqueue report workflows, or
fabricate quote results.
