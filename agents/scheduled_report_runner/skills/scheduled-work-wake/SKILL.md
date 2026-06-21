---
name: scheduled-work-wake
version: 0.1.0
description: Scheduled report wake skill; exposes only the internal wake tool.
tools:
  - claw-trade-scheduled-work-wake
entrypoint: openclaw_plugins/claw-trade-scheduled-work-tools/index.js
schema_version: claw_scheduled_work_wake.v1
---

# scheduled-work-wake

Call only `claw-trade-scheduled-work-wake` with the cron-provided payload.

Do not generate reports, write investment commentary, choose instruments, write
notifications, or fabricate report execution results.
