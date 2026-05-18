# OpenBB T17 Residual Risk Review

Date: 2026-05-18

Status: current evidence converged for the OpenBB data-source migration slice. This file records what was proven and what remains visible as residual risk; it is not a license/commercial-use legal opinion.

## Canonical T16 Live Evidence

Final evidence root after the OpenViking lineage fix, tool-call scoped OpenBB attempt IDs, provider-request false-positive fix, and US prompt preamble cleanup:

```text
docs/evidence/openbb-canonical-t16-final-20260518T125112Z/
```

Fixed runtime command mode was used:

```text
scripts/start-control-runtime.sh -- <collect-first batch>
DATA_GATEWAY_MONGODB_URI=mongodb://127.0.0.1:27017/claw_trade_openbb
DATA_GATEWAY_MONGODB_DATABASE=claw_trade_openbb
CLAW_TRADE_OPENBB_TOOL_SCHEMA_ENABLED=true
```

Preflight outcome:

- old `runtime.env` existed, but `1933/healthz` and `18789/health` were not reachable, so the batch started a clean runtime through `scripts/start-control-runtime.sh`.
- `CLAW_TRADE_OPENVIKING_MCP_MODULE`, `CLAW_TRADE_OPENVIKING_MCP_CWD`, `CLAW_TRADE_OPENVIKING_SERVER_BIN`, and `CLAW_TRADE_OPENVIKING_SERVER_CWD` were unset.
- runtime generated `CLAW_TRADE_OPENVIKING_MCP_STARTED=0`.
- OpenViking ran on `127.0.0.1:1933`; OpenClaw gateway ran on `127.0.0.1:18789`.

Collect-first summary:

| case | run id | run exit | audit | OpenViking collect | export | charts | provider prompts | evidence write failed |
|---|---|---:|---:|---:|---|---:|---:|---:|
| CN_A 600519.SH | `run-20260518-125138-cd49cb72` | 0 | 0 | 0 | passed | 2 | 13 | 0 |
| HK 00700.HK | `run-20260518-130056-134774a3` | 0 | 0 | 0 | passed | 2 | 13 | 0 |
| HK 00005.HK | `run-20260518-131128-a1390c95` | 0 | 0 | 0 | passed | 2 | 13 | 0 |
| US AAPL | `run-20260518-132154-638523b9` | 0 | 0 | 0 | passed | 2 | 13 | 0 |
| CRYPTO BTC | `run-20260518-133019-6fce75c5` | 0 | 0 | 0 | passed | 2 | 13 | 0 |

OpenBB evidence-chain audit summary:

| run id | passed | attempts | HTTP evidence | raw refs |
|---|---:|---:|---:|---:|
| `run-20260518-125138-cd49cb72` | true | 17 | 22 | 7 |
| `run-20260518-130056-134774a3` | true | 30 | 39 | 11 |
| `run-20260518-131128-a1390c95` | true | 14 | 21 | 10 |
| `run-20260518-132154-638523b9` | true | 30 | 43 | 8 |
| `run-20260518-133019-6fce75c5` | true | 15 | 16 | 9 |

All five audit files report:

- `invalid_raw_refs=[]`
- `missing_raw_refs=[]`
- `success_http_missing_source_url=[]`

RunProviderPlan proof:

- All five final run plans exist in Mongo.
- All five have `remote_prefetch_allowed=false`.
- This proves the Python control layer generated plans but did not prefetch provider data.

OpenViking evidence:

- Each case has `openviking-runtime-evidence-<run_id>.json`.
- Each case has an `ovpack/<run_id>.ovpack` export and import check.
- OpenViking relations are non-empty for every case.
- Relation kinds include final report -> PM L1, PM L1 -> worker L1, worker L1 -> L2 evidence, L2 evidence -> pack audit, pack audit -> provider attempt, provider attempt -> raw payload, provider attempt -> normalized result, and worker L1 -> chart asset.
- Semantic/vector queue is recorded as `blocked`, not faked as available.
- Runtime health is recorded as `blocked` because semantic/vector queue and some metrics/recovery public APIs are unavailable; this is an explicit runtime-health result, not a success disguise.

## Runtime Fallback Review

Read-only scan plus focused tests found no current `/report` runtime path that registers or calls the deleted old provider executor, direct provider executor, or US atomic tools as fallback.

Focused proof:

```bash
DATA_GATEWAY_MONGODB_URI=mongodb://127.0.0.1:27017/claw_trade_openbb uv run pytest \
  tests/integration/data_gateway/test_old_provider_import_block.py \
  tests/integration/data_gateway/test_mcp_visible_tools.py \
  tests/contracts/test_frontline_tool_contract.py \
  tests/contracts/test_tool_registry_contract.py \
  tests/unit/data_gateway/test_tool_schema.py -q
```

Exit code: `0`

Key output:

```text
43 passed in 11.33s
```

Old-token hits still exist in negative tests, forbidden lists, historical design files, and `docs/reference/legacy/**`. They are not current runtime fallback registrations.

## Guard And Prompt Fixes

Provider request guard:

- Root cause: the previous guard rejected any substring `profile:`, which falsely blocked natural English phrases such as `available evidence profile:` and `risk-reward profile:`.
- Fix: `profile:`, `profile_status:`, `worker_id:`, and `stage:` are still rejected when they appear as standalone prompt front-matter field lines, but natural-language phrases are allowed.
- `tests/contracts/test_guard_change_requires_approval.py` hash was updated for this approved false-positive fix.

US prompt preamble cleanup:

- US frontline prompts now instruct workers to start directly with the report heading or first analytical section.
- US report polisher now omits pure chat/progress preambles while preserving substantive analyst voice and PM authority.
- Latest US final canonical output has no hits for `Excellent`, `Let me analyze`, `Let me compile`, `Let me write`, `Now let me`, `I have the data`, `I have comprehensive`, `Thank you`, or `available evidence profile:` in `final_report.md` and the four US frontline report files.

## Residuals

Non-blocking invalid tool-call attempts remain visible in provider prompt/tool transcripts:

- US AAPL market worker tried `market=NASDAQ` once and the tool rejected it as `TOOL_PARAMS_INVALID`; the run later used successful canonical OpenBB pack calls and passed audit/export.
- HK 00700/HK 00005/CRYPTO BTC news or fundamental workers tried non-positive `freshness_max_age_seconds` in some redundant calls; those calls are recorded as failures and are not counted as successful provider fetches.

Why this is not hidden fallback:

- The failures remain visible in provider prompt transcripts.
- The successful evidence chain is based on later successful OpenBB pack calls.
- Final reports do not contain `TOOL_PARAMS_INVALID`, `attempt id conflict`, or `evidence_write_failed`.
- Mongo attempt summaries show `evidence_write_failed_count=0` for all final cases.

Follow-up hardening:

- Improve model-visible pack schema/prompt wording so redundant tool calls do not submit invalid market/freshness parameters.
- This is prompt/tool ergonomics hardening, not a blocker to the current OpenBB evidence-chain proof.

## Regression

Prompt/guard focused proof:

```bash
uv run pytest tests/contracts/test_guard_change_requires_approval.py tests/contracts/test_provider_request_guard.py -q
uv run pytest tests/contracts/test_provider_request_guard.py tests/contracts/test_worker_prompt_alignment_policy.py tests/unit/test_prompt_materials.py -q
```

Exit codes: `0`

Key output:

```text
59 passed in 0.66s
223 passed, 1 skipped in 0.84s
```

Broad proof:

```bash
DATA_GATEWAY_MONGODB_URI=mongodb://127.0.0.1:27017/claw_trade_openbb uv run pytest tests/unit tests/contracts tests/integration/data_gateway -q
```

Exit code: `0`

Key output:

```text
990 passed, 1 skipped, 5 warnings in 40.75s
```

## Current Conclusion

The latest canonical batch proves the current `/report` path uses OpenBB pack tools as the data-source entry, records OpenClaw provider payloads/tool schema/tool calls, records OpenBB HTTP/raw/normalized evidence, links evidence through Mongo and OpenViking lineage/ovpack, exports chart-ready final reports, and does not silently fall back to old providers.

The remaining visible invalid tool-call attempts are recorded failures, not mocked success, not fallback, and not hidden from evidence. They should be hardened next, but they do not invalidate the final evidence-chain proof above.
