# USER

Wake the claw-trade market data maintenance entry for the supplied cron payload.

Example payload shape:

```json
{
  "kind": "data_maintenance",
  "market": "CN_A",
  "jobKind": "provider_cache_refresh",
  "cronRunId": "auto"
}
```

Call only the internal scheduled-work wake entry with the supplied cron payload.
Do not choose providers, do not judge markets, do not write reports, do not
fabricate maintenance success, and do not notify users.
