# OpenBB Canonical T16 Lineage Evidence Batch

Date: 2026-05-18 UTC

Status: accepted canonical T16 evidence batch for the current OpenBB-only data gateway and OpenViking lineage boundary.

## Runtime Profile

The batch used fixed command mode:

```text
CN_A_MONGODB_URI=mongodb://127.0.0.1:27017/claw_trade_openbb
DATA_GATEWAY_MONGODB_URI=mongodb://127.0.0.1:27017/claw_trade_openbb
DATA_GATEWAY_MONGODB_DATABASE=claw_trade_openbb
CLAW_TRADE_OPENBB_TOOL_SCHEMA_ENABLED=true
scripts/start-control-runtime.sh -- <collect-first batch>
```

OpenViking ran on `127.0.0.1:1933`, OpenClaw gateway on `127.0.0.1:18789`, with no invest sidecar.

## Collect-First Result

`batch-summary.json` reports `failed_cases=[]`.

| case | run id | run exit | audit exit | OpenViking collect exit | export | charts | provider prompts | relations |
|---|---|---:|---:|---:|---|---:|---:|---|
| CN_A 600519.SH | `run-20260518-104120-6de59a97` | 0 | 0 | 0 | passed | 2 | 13 | true |
| HK 00700.HK | `run-20260518-104843-c9f3d205` | 0 | 0 | 0 | passed | 2 | 13 | true |
| HK 00005.HK | `run-20260518-105729-a8501fde` | 0 | 0 | 0 | passed | 2 | 13 | true |
| US AAPL | `run-20260518-110549-03749f00` | 0 | 0 | 0 | passed | 2 | 13 | true |
| CRYPTO BTC | `run-20260518-111317-de27cff2` | 0 | 0 | 0 | passed | 2 | 13 | true |

## OpenBB Evidence Counts

| case | attempts | HTTP evidence | raw refs | audit passed |
|---|---:|---:|---:|---|
| CN_A 600519.SH | 11 | 16 | 7 | true |
| HK 00700.HK | 14 | 21 | 10 | true |
| HK 00005.HK | 14 | 21 | 10 | true |
| US AAPL | 9 | 15 | 8 | true |
| CRYPTO BTC | 9 | 10 | 9 | true |

All five OpenBB audits have no invalid raw refs, no missing raw refs, and no successful HTTP evidence missing source URL.

## OpenViking Lineage

Every case has non-empty OpenViking relations. The relation graph includes:

- `final_report_claim_to_pm_l1`
- `pm_l1_to_worker_l1`
- `worker_l1_to_l2_evidence`
- `l2_evidence_to_pack_audit`
- `pack_audit_to_provider_attempt`
- `provider_attempt_to_raw_payload`
- `provider_attempt_to_normalized_result`
- `worker_l1_to_chart_asset`

Semantic/vector index status is `blocked` for every run, which is the required state while the semantic queue is disabled.

## Evidence Files

- Batch summary: `batch-summary.json`
- Collect-first log: `collect-first-summary.txt`
- Per-case summary: `*/case-summary.json`
- Per-case OpenBB audit: `*/openbb-evidence-chain-<run_id>.json`
- Per-case OpenViking runtime evidence: `*/openviking-runtime-evidence-<run_id>.json`
- Per-case OpenViking collector stdout/stderr: `*/openviking-collector-<run_id>.json`, `*/openviking-collector.err`
- Per-case ovpack: `*/ovpack/<run_id>.ovpack`
- Per-case exported report and worker evidence: `*/exported/`
