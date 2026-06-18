# USER

Wake the claw-trade price alert scan entry for the supplied cron payload.

Required payload:

```json
{
  "kind": "price_alert_scan",
  "bucketKey": "CRYPTO:3m",
  "cronRunId": "cron-run-1"
}
```

Call only the internal scheduled-work wake entry. Do not compare prices, do not
request reports, and do not write investment commentary.
