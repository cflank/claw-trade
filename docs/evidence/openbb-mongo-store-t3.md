# OpenBB Mongo Store T3 Evidence

Status: T3 implementation evidence collected

Date: 2026-05-17

## Runtime

Command:

```bash
scripts/start-local-mongodb.sh
```

Exit code: 0

Key output:

```text
MongoDB started
export CN_A_MONGODB_URI=mongodb://127.0.0.1:27017
```

## Tests

Command:

```bash
DATA_GATEWAY_MONGODB_URI=mongodb://127.0.0.1:27017 \
  uv run pytest \
    tests/unit/data_gateway/test_cache_state_machine.py \
    tests/integration/data_gateway/test_mongo_attempts.py \
    tests/integration/data_gateway/test_run_provider_plan_snapshot.py
```

Exit code: 0

Key output:

```text
5 passed, 4 warnings in 1.13s
```

Warnings were limited to unregistered `integration` pytest marks and Python multiprocessing fork deprecation warnings. No Mongo integration test was skipped in this run.

## Sample Documents

Evidence database:

```text
claw_trade_t3_evidence_ab4936a1
```

Index collection count:

```text
9
```

Indexed collections:

```text
openbb_cache_entries
openbb_normalized
openbb_provider_attempts
openbb_provider_manifests
openbb_provider_validation_receipts
openbb_rate_limits
openbb_raw_payloads
openbb_run_provider_plans
openbb_single_flight_calls
```

Raw payload sample:

```json
{
  "payload_hash": "sha256:6a6e350e27bfa22981d5f1e95bfaa5bcbed93e216796ae9609669df7756a71c5",
  "payload_storage": "redacted",
  "payload_is_none": true,
  "raw_export_policy": "redacted",
  "redacted_snapshot_has_secret": false
}
```

Cache receipt statuses:

```json
["cache_hit", "cached_empty"]
```

Single-flight attempt samples:

```json
[
  {
    "_id": "call-consumer:project.tushare:consumer",
    "status": "shared_result",
    "single_flight_role": "consumer",
    "shared_from_attempt_id": "attempt-owner",
    "raw_ref": "mongo://openbb_raw_payloads/sha256:6a6e350e27bfa22981d5f1e95bfaa5bcbed93e216796ae9609669df7756a71c5",
    "normalized_ref": "mongo://openbb_normalized/sha256:fd1d579c439e4df5225f229edc5af182fce0dc16a8fc1c9f1d294320d52d8f87"
  },
  {
    "_id": "attempt-owner",
    "status": "remote_success",
    "single_flight_role": "owner",
    "shared_from_attempt_id": null,
    "raw_ref": "mongo://openbb_raw_payloads/sha256:6a6e350e27bfa22981d5f1e95bfaa5bcbed93e216796ae9609669df7756a71c5",
    "normalized_ref": "mongo://openbb_normalized/sha256:fd1d579c439e4df5225f229edc5af182fce0dc16a8fc1c9f1d294320d52d8f87"
  }
]
```

Single-flight call sample:

```json
{
  "_id": "run-evidence-a4f6bf97:market:tushare:daily",
  "status": "succeeded",
  "owner_attempt_id": "attempt-owner",
  "raw_ref": "mongo://openbb_raw_payloads/sha256:6a6e350e27bfa22981d5f1e95bfaa5bcbed93e216796ae9609669df7756a71c5",
  "normalized_ref": "mongo://openbb_normalized/sha256:fd1d579c439e4df5225f229edc5af182fce0dc16a8fc1c9f1d294320d52d8f87"
}
```

RunProviderPlan sample:

```json
{
  "_id": "run-evidence-a4f6bf97",
  "provider_config_version": "cfg-v1",
  "remote_prefetch_allowed": false,
  "call_specs": [
    {
      "adapter_id": "project.tushare"
    }
  ]
}
```

Validation receipt sample:

```json
{
  "status": "enabled_candidate",
  "sample_raw_ref": "mongo://openbb_raw_payloads/sha256:6a6e350e27bfa22981d5f1e95bfaa5bcbed93e216796ae9609669df7756a71c5",
  "sample_normalized_ref": "mongo://openbb_normalized/sha256:fd1d579c439e4df5225f229edc5af182fce0dc16a8fc1c9f1d294320d52d8f87"
}
```

## Manager Review Fixes

- Fixed stale `cached_empty` handling so both `CacheDecision.status` and `CacheReceipt.status` are `cache_stale`.
- Fixed raw payload upsert to avoid Mongo `$setOnInsert` / `$set` path conflict on `run_id`.
- Fixed `raw_export_policy=redacted` so raw payload content is not stored inline.
- Added run-level evidence-chain audit so final acceptance checks raw payloads by
  dereferencing `attempt.raw_ref` / `openbb_provider_http_evidence.raw_ref`
  into `openbb_raw_payloads`, instead of misusing `openbb_raw_payloads.run_id`
  counts for content-addressed raw documents.

Evidence-chain audit proof:

```text
run_id=run-20260517-220630-c9edcab2
passed=true
attempt_count=9
openbb_provider_http_evidence=15
raw_ref_count=8
missing_raw_refs=[]
success_http_missing_source_url=[]
success_http_missing_response_status=[]
success_http_missing_headers=[]
```

Command:

```bash
DATA_GATEWAY_MONGODB_URI=mongodb://127.0.0.1:27017/claw_trade_openbb \
  uv run python scripts/audit_openbb_evidence_chain.py --run-id run-20260517-220630-c9edcab2
```

Focused tests:

```text
uv run pytest tests/unit/data_gateway/test_evidence_chain.py tests/unit/data_gateway/test_provider_execution.py
7 passed
```

## Remaining Risk

- This evidence proves T3 store contracts and persistent Mongo single-flight behavior in local dev Mongo.
- It does not prove live provider data collection, OpenBB pack endpoint behavior, or four-market fresh runs; those remain T5/T8/T9/T10/T16 scope.
