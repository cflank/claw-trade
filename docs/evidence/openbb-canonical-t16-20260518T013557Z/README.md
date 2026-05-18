# OpenBB Canonical T16 Evidence Batch

Date: 2026-05-18 UTC

Status: accepted canonical T16 evidence batch for the current OpenBB pack tool
boundary.

## Runtime Profile

The batch used `scripts/start-control-runtime.sh -- <command>` and put Mongo
settings in the outer environment so the control process and OpenClaw tool
subprocess used the same run-plan store.

```text
DATA_GATEWAY_MONGODB_URI=mongodb://127.0.0.1:27017/claw_trade_openbb
DATA_GATEWAY_MONGODB_DATABASE=claw_trade_openbb
CLAW_TRADE_OPENBB_TOOL_SCHEMA_ENABLED=true
OpenViking: 127.0.0.1:1933
OpenClaw gateway: 127.0.0.1:18789
```

## Collect-First Result

| case | run id | run exit | audit exit | OpenViking collect exit | exported report | chart assets |
|---|---|---:|---:|---:|---|---:|
| CN_A 600519.SH | `run-20260518-013603-6f876fea` | 0 | 0 | 0 | `cn_a_600519/exported/final_report.md` | 2 |
| HK 00700.HK | `run-20260518-014315-149a9ab3` | 0 | 0 | 0 | `hk_00700/exported/final_report.md` | 2 |
| HK 00005.HK | `run-20260518-015123-3746b2d1` | 0 | 0 | 0 | `hk_00005/exported/final_report.md` | 2 |
| US AAPL | `run-20260518-015850-72402727` | 0 | 0 | 0 | `us_aapl/exported/final_report.md` | 2 |
| CRYPTO BTC | `run-20260518-020629-ee224f78` | 0 | 0 | 0 | `crypto_btc/exported/final_report.md` | 2 |

## Evidence Files

- Batch summary: `collect-first-summary.txt`
- Per-case OpenBB audit:
  `*/openbb-evidence-chain-<run_id>.json`
- Per-case OpenViking runtime evidence:
  `*/openviking-runtime-evidence-<run_id>.json`
- Per-case ovpack:
  `*/ovpack/<run_id>.ovpack`
- Per-case exported report package:
  `*/exported/`

## OpenBB Evidence Counts

| run id | audit passed | attempts | HTTP evidence | raw refs |
|---|---:|---:|---:|---:|
| `run-20260518-013603-6f876fea` | true | 11 | 16 | 7 |
| `run-20260518-014315-149a9ab3` | true | 14 | 27 | 10 |
| `run-20260518-015123-3746b2d1` | true | 14 | 25 | 10 |
| `run-20260518-015850-72402727` | true | 9 | 15 | 8 |
| `run-20260518-020629-ee224f78` | true | 9 | 10 | 9 |

All OpenBB audit files have no invalid raw refs, no missing raw refs, and no
successful HTTP evidence missing source URL.

## Non-Blocking Tool-Call Errors

The batch preserves tool errors rather than hiding them:

| run id | tool | error |
|---|---|---|
| `run-20260518-013603-6f876fea` | `claw_get_fundamental_pack` | `params.freshness_max_age_seconds must be a positive number` |
| `run-20260518-014315-149a9ab3` | `claw_get_market_pack` | `claw_get_market_pack python subprocess timed out` |
| `run-20260518-015123-3746b2d1` | `claw_get_news_pack` | `params.freshness_max_age_seconds must be a positive number` |

These are not silent fallbacks and are not recorded as provider successes. Each
affected run also has a successful canonical pack call for the same domain, and
the per-run OpenBB evidence-chain audit passed.

## Rejected Prior Batch

`docs/evidence/openbb-canonical-t16-20260518T010838Z/` is not accepted for T16.
It was collected with mismatched Mongo visibility: the control process wrote run
plans to `claw_trade_openbb`, while the already-started OpenClaw tool subprocess
looked in another database and returned `run plan not found`.
