# Live `/select` + Four-Market `/report` Validation Summary

Local run window: 2026-06-06 evening America/Toronto. Run IDs and provider evidence use UTC 2026-06-07 timestamps.

## Runtime Preflight Reference

Manager-provided preflight was treated as authoritative. This subagent only recorded actual status before running:

- Fixed profile: `scripts/start-control-runtime.sh`, repo `uv`, OpenViking `http://127.0.0.1:1933`, OpenClaw gateway `ws://127.0.0.1:18789`, no invest sidecar.
- `.runtime/dev-services/runtime.env` existed.
- `1933/health` returned healthy; `18789/health` returned live.
- Current shell did not expose `CLAW_TRADE_OPENVIKING_MCP_MODULE`, `CLAW_TRADE_OPENVIKING_MCP_CWD`, `CLAW_TRADE_OPENVIKING_SERVER_BIN`, or `CLAW_TRADE_OPENVIKING_SERVER_CWD`.

## Command Log

### `/select`

First sandboxed attempt:

```bash
set -a; source .runtime/dev-services/runtime.env; set +a; uv run python scripts/validation/run_real_select_full_chain.py --trade-date 2026-06-05 --selection-run-id sel-live-20260606T214515 --request-id select-live-20260606T214515 --output-json docs/evidence/live-select-four-market-report-20260606T214515/select-fullchain.json
```

- Exit code: `2`
- Result: data job completed, OpenClaw probe failed with loopback sandbox error: `urlopen error [Errno 1] Operation not permitted`.

Escalated live attempt:

```bash
set -a; source .runtime/dev-services/runtime.env; set +a; uv run python scripts/validation/run_real_select_full_chain.py --trade-date 2026-06-05 --selection-run-id sel-live-20260606T214515-v2 --request-id select-live-20260606T214515-v2 --output-json docs/evidence/live-select-four-market-report-20260606T214515/select-fullchain-v2.json
```

- Exit code: `0`
- Output: `docs/evidence/live-select-four-market-report-20260606T214515/select-fullchain-v2.json`

### `/report`

CN_A:

```bash
set -a; source .runtime/dev-services/runtime.env; set +a; uv run python scripts/run_claw_trade_fresh_report.py --ticker 600519.SH --company-name 贵州茅台 --market CN_A --profile CN_A --currency CNY --currency-symbol ¥ --current-date 2026-06-06 --start-date 2025-06-06 --end-date 2026-06-06 --output-dir docs/evidence/live-select-four-market-report-20260606T214515/report-cn_a
```

- Exit code: `0`
- Run: `run-20260607-015125-f929a61d`

US:

```bash
set -a; source .runtime/dev-services/runtime.env; set +a; uv run python scripts/run_claw_trade_fresh_report.py --ticker AAPL --company-name "Apple Inc." --market US --profile US --currency USD --currency-symbol $ --current-date 2026-06-06 --start-date 2025-06-06 --end-date 2026-06-06 --output-dir docs/evidence/live-select-four-market-report-20260606T214515/report-us
```

- Exit code: `0`
- Run: `run-20260607-020702-488c55ce`

HK:

```bash
set -a; source .runtime/dev-services/runtime.env; set +a; uv run python scripts/run_claw_trade_fresh_report.py --ticker 00700.HK --company-name "Tencent Holdings" --market HK --profile HK --currency HKD --currency-symbol HK$ --current-date 2026-06-06 --start-date 2025-06-06 --end-date 2026-06-06 --output-dir docs/evidence/live-select-four-market-report-20260606T214515/report-hk
```

- Exit code: `1`
- Run: `run-20260607-021545-08c995e7`
- Failure: `stage_batch: tool_calls: workers=['social_analyst']; reasons=['frontline 资料包工具调用失败: claw_get_social_pack']`

CRYPTO:

```bash
set -a; source .runtime/dev-services/runtime.env; set +a; uv run python scripts/run_claw_trade_fresh_report.py --ticker BTC --company-name Bitcoin --market CRYPTO --profile CRYPTO --currency USD --currency-symbol $ --current-date 2026-06-06 --start-date 2025-06-06 --end-date 2026-06-06 --output-dir docs/evidence/live-select-four-market-report-20260606T214515/report-crypto
```

- Exit code: `0`
- Run: `run-20260607-021913-3ca93bc2`

### Per-Run Evidence Commands

For each successful or failed report run:

```bash
uv run python scripts/collect_openviking_runtime_evidence.py --run-id <run_id> --output-dir <evidence_dir>/openviking
```

- Exit code: `1` for CN_A, US, HK, and CRYPTO.
- Root cause: the script successfully exported an ovpack, then crashed at `export_receipt.portability_status` because the current HTTP backend returns a `dict`, not an object with attributes.
- Manual no-code OpenViking supplement was collected in `openviking-manual-evidence-summary.json`.

```bash
uv run python scripts/audit_removed_data_gateway_evidence_chain.py --run-id <run_id>
```

- Exit code: `1` for CN_A, US, HK, and CRYPTO.
- Root cause: stale script import: `ModuleNotFoundError: No module named 'claw_trade.data_gateway.store'`.

Provider attempt window commands:

```bash
uv run python scripts/validation/summarize_provider_attempts_window.py --skip-count <before_count> --market <market> --run-id <run_id> --report-output-dir <output_dir> --output <provider-attempt-window.json>
```

- CN_A exit `0`, skip `20817`, output `report-cn_a/provider-attempt-window.json`
- US exit `0`, skip `20855`, output `report-us/provider-attempt-window.json`
- HK exit `0`, skip `20870`, output `report-hk-provider-attempt-window.json`
- CRYPTO exit `0`, skip `20885`, output `report-crypto/provider-attempt-window.json`

Additional structure audit:

```bash
uv run python -c "<generated no-code run structure audit>"
```

- Exit code: `0`
- Output: `docs/evidence/live-select-four-market-report-20260606T214515/run-structure-audit.json`

Manual OpenViking supplement:

```bash
uv run python -c "<generated no-code OpenViking tree/grep/glob/relations/runtime health collector>"
```

- Exit code: `0`
- Output: `docs/evidence/live-select-four-market-report-20260606T214515/openviking-manual-evidence-summary.json`

## `/select` Result

- Status: completed on escalated live run.
- Workflow run: `select-20260607T014714-select-live-20260606T214515-v2`
- Evidence: `runs/selection/workflows/select-20260607T014714-select-live-20260606T214515-v2/selection-workflow-evidence.json`
- Reader report: `runs/selection/workflows/select-20260607T014714-select-live-20260606T214515-v2/select-reader-report.md`
- Reader report size: non-empty full report, manually checked as containing strategy, skeptical review, manager/PM decision, ranking basis, risks, and trigger conditions. It is not only a conclusion.

Data job facts:

- Status: completed.
- Trade date: `2026-06-05`.
- Provider attempt refs: `attempt://data-provider/attempt:cn_a_primary:daily_bar_by_trade_date:b3649efb3396`
- `normalized_ref_count`: `200`
- `top20_tickers`: `688017.SH`, `600869.SH`, `600367.SH`, `600552.SH`, `603078.SH`, `300835.SZ`, `603045.SH`, `688059.SH`, `603608.SH`, `605358.SH`, `688146.SH`, `002636.SZ`, `688777.SH`, `603890.SH`, `002421.SZ`, `300503.SZ`, `688403.SH`, `000887.SZ`, `300328.SZ`, `301458.SZ`
- Candidate pack material: `selection-candidate-pack-sel-live-20260606T214515-v2-d9ca6e965da5`
- Candidate pack L1 URI: `local://selection/sel-live-20260606T214515-v2/candidate-pack/approved/candidate-pack.md`
- Data gap: `selection_strategy_variant_disabled` for missing private-placement fields; marked as not interpreted as no event.

Decision:

- Enter `/report`: `688017.SH`, `300835.SZ`, `688777.SH`
- Watch: 17 names
- Reject: none

OpenClaw selection dispatch evidence:

- `dispatch-01-selection_strategist`: visible tool `claw_get_selection_candidate_pack`, tool call success.
- `dispatch-02-selection_skeptic`: visible tool `claw_get_selection_candidate_pack`, tool call success.
- `dispatch-03-selection_manager`: no visible tools, no tool calls.
- `dispatch-04-selection_portfolio_manager`: no visible tools, no tool calls.
- Provider payload/tool schema/tool calls are under `runs/selection/workflows/select-20260607T014714-select-live-20260606T214515-v2/dispatches/*/{provider-request.json,visible-tools.json,tool-calls.json}`.

## Four-Market `/report` Results

| Market | Status | Run ID | Output / final report |
| --- | --- | --- | --- |
| CN_A | completed, but reader truthfulness review failed | `run-20260607-015125-f929a61d` | `docs/evidence/live-select-four-market-report-20260606T214515/report-cn_a/final_report.md` |
| US | completed, partial compliance | `run-20260607-020702-488c55ce` | `docs/evidence/live-select-four-market-report-20260606T214515/report-us/final_report.md` |
| HK | failed during frontline | `run-20260607-021545-08c995e7` | no exported final report |
| CRYPTO | completed, sampled reader review acceptable with data-gap caveats | `run-20260607-021913-3ca93bc2` | `docs/evidence/live-select-four-market-report-20260606T214515/report-crypto/final_report.md` |

## Data Layer / Columnar + Mongo Observations

Correction: the earlier data-layer wording was incomplete. Mongo is not the whole data layer. Mongo stores provider attempts, raw refs, cache/rate/singleflight state, and the `dataset_manifests` index. The normalized report records are backed by Parquet manifests under `.runtime/dev-services/data-gateway/normalized`, and `/select` uses a separate selection columnar warehouse under `.runtime/selection/columnar`.

Detailed supplement: `docs/evidence/live-select-four-market-report-20260606T214515/data-layer-columnar-supplement.md`.

`/select` columnar proof:

- Selection data run: `sel-live-20260606T214515-v2`.
- Warehouse check: `warehouse-check://selection-columnar/CN_A/CN_A/2026-06-05`.
- Columnar manifest: `columnar://selection/market_data/market=CN_A/profile=CN_A/trade_date=2026-06-05/manifest.json`.
- Manifest sha256: `a3e74a5e604ac82f724fa2b9cbef072db13563b6178c8dbec119434dbe1d8cc0`.
- Physical directory: `.runtime/selection/columnar/market_data/market=CN_A/profile=CN_A/trade_date=2026-06-05`.
- Size / rows: `113M`, `38` daily Parquet partitions with `1,854,035` rows, `6` feature partitions with `5,373` rows.

Four-market `/report` normalized columnar proof:

| Market | Run ID | Prefetch refs | Active Parquet manifests | Matched manifests | Matched refs |
| --- | --- | ---: | ---: | ---: | ---: |
| CN_A | `run-20260607-015125-f929a61d` | 1009 | 414 | 14 | 1009 |
| US | `run-20260607-020702-488c55ce` | 582 | 15 | 14 | 582 |
| HK | `run-20260607-021545-08c995e7` | 553 | 8 | 5 | 553 |
| CRYPTO | `run-20260607-021913-3ca93bc2` | 244 | 18 | 15 | 244 |

Correct data-layer flow:

```text
report prefetch request
-> query planner / warehouse lookup
-> Mongo dataset_manifests index for active Parquet manifests
-> NormalizedColumnarWarehouse Parquet read/write
-> provider execution gate/cache/rate/singleflight
-> provider attempt + raw refs in Mongo
-> normalized Parquet partition + manifest index update
-> report data pack
-> OpenViking approved material
-> OpenClaw worker tool call / provider payload
-> final report export
```

已确认事实:

| Market | Prefetch | Provider attempts window | Key providers/status |
| --- | --- | --- | --- |
| CN_A | partial; domains `market,fundamental,news,social`; requests `19`; dataset refs `1009`; attempt refs `44` | attempts `38`; success `21`; provider_error `11`; empty_result `6` | `cn_a_primary`, `cn_a_baostock_market`, `cn_a_tushare_fundamental`, `cn_a_eastmoney_market_data`, `cn_a_akshare_social_news`, `cn_a_google_news`, `cn_a_cninfo_events` |
| US | partial; domains `market,fundamental,news,social`; requests `14`; dataset refs `582`; attempt refs `14` | attempts `15`; success `15` | `us_yahoo_finance`, `us_sec_official`, `us_finnhub_data`, `us_google_news`, `us_stocktwits_social`, `us_fred_macro` |
| HK | partial; domains `market,fundamental,news`; requests `9`; dataset refs `553`; attempt refs `11` | attempts `15`; success `9`; provider_error `3`; empty_result `3` | `hk_yahoo_finance`, `hk_tushare`, `hk_sina_public`, `hk_akshare_fundamental`, `hk_google_news`, `hk_finnhub_data`, `hk_hkexnews_official`; no social domain material in prefetch |
| CRYPTO | partial; domains `market,fundamental,news,social`; requests `21`; dataset refs `244`; attempt refs `20` | attempts `24`; success `19`; cooldown_skipped `3`; empty_result `1`; rate_limited `1` | `crypto_primary`, `crypto_binance_spot_market`, `crypto_coinglass_derivatives`, `crypto_google_news`, `crypto_alternative_me_sentiment`, `crypto_defillama_defi` |

推断:

- Fresh report currently does not emit one complete query-planner -> warehouse manifest hit/miss -> exact Parquet manifest -> provider-selector -> provider-attempt trace as a single timeline. The flow above is reconstructed from `report-prefetch.json`, Mongo `dataset_manifests`, Mongo `provider_attempts`, worker data-pack calls, and provider attempt windows.
- The current evidence proves the four report runs' prefetch `dataset_refs` are backed by active Parquet manifests. It does not fully prove an ordered per-request warehouse hit/miss timeline before every provider fetch.
- `/select` warehouse behavior is proven separately by explicit `warehouse_check_ref`, `columnar_manifest_ref`, manifest sha, and physical Parquet partitions.

## OpenViking

Scripted collector:

- CN_A, US, HK, and CRYPTO all exported `.ovpack` before crashing.
- Ovpack files:
  - `report-cn_a/openviking/ovpack/run-20260607-015125-f929a61d.ovpack`
  - `report-us/openviking/ovpack/run-20260607-020702-488c55ce.ovpack`
  - `report-hk-openviking/ovpack/run-20260607-021545-08c995e7.ovpack`
  - `report-crypto/openviking/ovpack/run-20260607-021913-3ca93bc2.ovpack`

Manual no-code supplement:

- `docs/evidence/live-select-four-market-report-20260606T214515/openviking-manual-evidence-summary.json`
- Four runs: `tree_status=ok`, `glob_status=ok`.
- Per-run detailed files contain `tree`, `grep_portfolio_manager`, `glob_approved_manifest`, `find_approved_materials`, `relations`, and runtime health call results.
- Import check from the original collector did not run because the script crashed before import. This remains a tooling blocker, not a runtime success proof.

## OpenClaw / Provider Payload / Tool Schema

已确认事实:

- All report worker calls in the structure audit have `provider_request_exists=true` and `provider_requests_jsonl_exists=true`.
- Successful CN_A run exported 21 worker turns: standard workers plus CN_A-specific frontline workers and six report polisher turns.
- Successful US and CRYPTO runs exported 18 turns each: 12 core workers plus six report polisher turns.
- HK reached four frontline workers before failing.

Stage tool exposure:

- CN_A frontline exposed only stage-specific pack tools: `claw_get_market_pack`, `claw_get_fundamental_pack`, `claw_get_news_pack`, `claw_get_social_pack`, `claw_get_policy_pack`, `claw_get_hot_money_pack`, `claw_get_lockup_pack`; all succeeded.
- US frontline exposed `claw_get_market_pack`, `claw_get_fundamental_pack`, `claw_get_news_pack`, `claw_get_social_pack`; all succeeded.
- HK frontline exposed the same four core tools; `market`, `fundamental`, and `news` succeeded; `social` failed twice with `manifest does not contain domain: social`.
- CRYPTO frontline exposed `claw_get_market_pack`, `claw_get_fundamental_pack`, `claw_get_news_pack`, `claw_get_social_pack`; all succeeded.
- Downstream workers and report polisher turns had no visible tools and no tool calls in the successful runs. This satisfies the pure-LLM downstream tool-narrowing expectation for these runs.

Prompt leakage check:

- Focused grep over CN_A/US provider prompt exports for `RuntimeTarget`, `[ApprovedMaterials]`, `material_id`, `capability`, `viking://`, `OpenViking`, and related protocol strings found no hits in the sampled provider prompt exports.

## Final Report Reader-Side Review

### CN_A

Verdict: 不符合 truthfulness hard gates despite successful run/export.

Confirmed evidence:

- `export-result.json` status: `passed`; `unsupported_claims: []`.
- Charts exist under `report-cn_a/assets/`.
- Report has Chinese reader-facing structure, analyst coverage, debate flow, portfolio decision, risk and execution sections.

Blocking content findings:

- `fundamental_analyst_report.md` states valuation data including PE/PB/PEG/current price is missing, then gives ROE ranges, historical PE/PB ranges, current PE estimates, PEG estimates, target-price framework, and reasonable price scenarios.
- `bear_researcher_report.md` and `final_report.md` contain unsupported or source-like factual claims such as batch-price paths, i茅台中签率 changes, Northbound holding changes, youth drinking white-paper numbers, global comparable valuation numbers, target ranges, and PE compression scenarios.
- Final report repeats these as actionable sell/target logic. This is not just analyst voice; it includes factual/valuation/source claims that were not tied to the collected evidence in this validation.
- Export guards did not catch these unsupported claims.

### US

Verdict: 部分符合.

Confirmed evidence:

- Completed run, final report exported, charts present.
- Provider attempts window had 15 successful attempts.
- Provider prompt leakage scan had no sampled hits.
- Fundamental report acknowledges missing fundamentals and mostly avoids unsupported PE/PB/ROE numeric fabrication in the sampled review.

Residual risk:

- Final action is a strong technical sell/clear-out recommendation. That is not disallowed by AGENTS.md when evidence-supported, but some wording such as confirmed institutional distribution appears to present an inference as a confirmed fact. Treat as wording risk, not a hard failure from this sample.

### HK

Verdict: 不符合 because the live workflow failed before final report.

Confirmed failure:

- `state.json` failure: `stage_batch: tool_calls: workers=['social_analyst']; reasons=['frontline 资料包工具调用失败: claw_get_social_pack']`
- Terminal stage: frontline.
- Social analyst evidence: `runs/run-20260607-021545-08c995e7/calls/run-20260607-021545-08c995e7-frontline-t00-social_analyst-20260607T021727429026Z-3dd0bf84/tool-calls.json`
- Tool error: `manifest does not contain domain: social`.

Root component guess:

- HK prefetch created only `market`, `fundamental`, and `news` domains while workflow still woke `social_analyst` with `claw_get_social_pack`.
- The failure is a manifest/domain coverage mismatch, not an OpenClaw provider payload absence.

### CRYPTO

Verdict: 部分符合 / sampled acceptable.

Confirmed evidence:

- Completed run, final report exported, charts present.
- Provider attempts show real gaps: cooldown skips, rate limit, and empty result in crypto providers.
- Final report explicitly states many missing domains and constrains the recommendation to price/technical evidence.

Residual risk:

- The action is strong (`sell`, conditional short), but the sampled report repeatedly states data limitations and does not appear to invent missing news/social/on-chain facts. No blocking fabrication was found in the sampled review.

## Collect-First Compliance

- Batch scope: `/select` plus `/report` for CN_A, US, HK, CRYPTO.
- Completed items: `/select`, CN_A, US, CRYPTO.
- Failed items collected: HK report.
- Early-stop exception used: no.
- Failure evidence collected for HK: run state, terminal stage, social analyst provider/tool evidence, provider attempt window, OpenViking partial evidence.
- Batch fix grouping: not fixed in this subagent task. Root causes grouped as:
  - HK report domain-manifest mismatch for social pack.
  - CN_A content truthfulness/guard miss.
  - Evidence tooling drift: OpenViking collector dict/object mismatch; 已删除数据网关 audit stale import path.

## Mock / Stub / Fake / Fallback Status

- No mock, stub, fake, fallback, or capture-only substitute was used to make validation pass.
- The first `/select` command failed due sandbox loopback permission; it was rerun with escalated live runtime access.
- OpenViking manual supplement is explicitly marked as supplement after collector failure, not as proof that the original collector passed.

## Overall Verdict

- `/select`: 符合本次 live validation 要求.
- CN_A `/report`: runtime completed, but final report does not meet truthfulness requirements.
- US `/report`: runtime completed; sampled report is partially compliant with noted inference-wording risk.
- HK `/report`: failed; not compliant.
- CRYPTO `/report`: runtime completed; sampled report is partially compliant with clear data-gap caveats.

Confidence: medium-high for runtime/tool evidence, medium for manual report compliance review because it is sampled human review rather than exhaustive claim-by-claim source audit.
