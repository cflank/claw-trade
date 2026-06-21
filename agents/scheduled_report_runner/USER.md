# USER

Wake the claw-trade scheduled report entry for the supplied cron payload.

Example payload shape:

```json
{
  "kind": "scheduled_report",
  "scheduledReportId": "scheduled-report-1",
  "cronRunId": "auto"
}
```

Call only the internal scheduled-work wake entry with the supplied cron payload.
Do not generate a report, do not write investment commentary, and do not write
notifications.
