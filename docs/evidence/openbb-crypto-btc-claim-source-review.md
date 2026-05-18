# OpenBB CRYPTO BTC Claim-Source Review

Date: 2026-05-17

Scope:

```text
task=T17-pre BTC claim-source review
run_id=run-20260517-225048-30bcbc33
evidence_dir=docs/evidence/trading_claw_trade_crypto_fresh_live_run-20260517-225048-30bcbc33_md
mode=read-only review
```

## Verdict

T16 evidence collection for the BTC run is accepted, but the BTC report is not
ready for T17 final closure without a content-boundary follow-up.

The final report usually preserves the major data gaps, especially derivatives,
on-chain, macro, AHR999, and news/event coverage. However, several public
knowledge or Google News search-discovery claims still appear in reader-facing
material. These are not mock/stub/fake/fallback evidence, but they are residual
claim-source risks.

## Findings

### High: Search-Discovery ETF And Institution Claims Need Stronger Boundary

Evidence:

```text
docs/evidence/trading_claw_trade_crypto_fresh_live_run-20260517-225048-30bcbc33_md/social_analyst_report.md:47
states Google News search records are not verified social consensus.

docs/evidence/trading_claw_trade_crypto_fresh_live_run-20260517-225048-30bcbc33_md/social_analyst_report.md:50-51
states multiple news items mention BTC ETF net inflow recovery and BlackRock/Fidelity continued accumulation.

docs/evidence/trading_claw_trade_crypto_fresh_live_run-20260517-225048-30bcbc33_md/final_report.md:530
repeats ETF/institution buying narrative from 20 Google News search records.
```

Assessment:

This is not silently fabricated from nowhere because it is tied to Google News
search-discovery material and the report states that search lines do not replace
social consensus. The remaining risk is that it still reads like a market fact
unless the raw source list and source role are made more visible.

Required follow-up:

```text
CRYPTO news/social reader material should label this as search_discovery only,
not confirmed official/primary news or verified institutional flow data.
Final report should avoid using ETF/BlackRock/Fidelity statements as decisive
portfolio evidence unless backed by official ETF flow/provider evidence.
```

### High: Fundamental Public-Knowledge Claims Are Clearly Labeled But Not Provider-Backed

Evidence:

```text
docs/evidence/trading_claw_trade_crypto_fresh_live_run-20260517-225048-30bcbc33_md/final_report.md:402
states supply analysis is based on known public Bitcoin network characteristics,
not returned supply fields.

docs/evidence/trading_claw_trade_crypto_fresh_live_run-20260517-225048-30bcbc33_md/final_report.md:404-406
states total supply, estimated mined supply, inflation, halving, lost-coin range.

docs/evidence/trading_claw_trade_crypto_fresh_live_run-20260517-225048-30bcbc33_md/final_report.md:409
states ETF daily buying during institution accumulation windows can reach
thousands of BTC, then caveats that chain/on-exchange pressure is missing.
```

Assessment:

The report marks these claims as public-known or theoretical and notes that
provider data is missing. That is better than unsupported certainty. Still,
T17 final closure should not treat these as OpenBB provider facts.

Required follow-up:

```text
Either add approved provider evidence for BTC supply/halving/ETF flow claims, or
make the final-report wording consistently say these are public context, not
OpenBB-collected evidence.
```

### Medium: Final Report Has A Confusing Readiness Statement For CRYPTO Market Data

Evidence:

```text
docs/evidence/trading_claw_trade_crypto_fresh_live_run-20260517-225048-30bcbc33_md/final_report.md:61
says data readiness is ready and coverage has 0 gaps for OpenBB/yFinance price data,
then says derivatives, liquidation, on-chain, and funding/OI are missing.

docs/evidence/trading_claw_trade_crypto_fresh_live_run-20260517-225048-30bcbc33_md/final_report.md:244-246
later clarifies that readiness only means price-history coverage, while
derivatives/on-chain/macro modules are not covered.
```

Assessment:

This is internally clarified later, but the first readiness statement can be
misread as whole-market CRYPTO coverage being complete.

Required follow-up:

```text
CRYPTO MarketPack should distinguish price_history_readiness from
crypto_market_structure_readiness in the worker-visible brief.
```

### Medium: Worker Debate Contains Unsupported Aggressive Claims, But Final Report Mostly Rejects Them

Evidence:

```text
docs/evidence/trading_claw_trade_crypto_fresh_live_run-20260517-225048-30bcbc33_md/bull_researcher_report.md:31
asserts BlackRock/Fidelity continued accumulation.

docs/evidence/trading_claw_trade_crypto_fresh_live_run-20260517-225048-30bcbc33_md/bull_researcher_report.md:63-65
asserts hash rate, exchange-balance trend, and long-term-holder behavior while
admitting on-chain data was not returned.

docs/evidence/trading_claw_trade_crypto_fresh_live_run-20260517-225048-30bcbc33_md/bull_researcher_report.md:77-80
states ETF flow, Fed cut expectations, stablecoin supply, and liquidation-map
logic without current provider data.

docs/evidence/trading_claw_trade_crypto_fresh_live_run-20260517-225048-30bcbc33_md/risk_moderator_report.md:3
explicitly rejects the unsupported liquidation-map assumption.

docs/evidence/trading_claw_trade_crypto_fresh_live_run-20260517-225048-30bcbc33_md/final_report.md:11-17
frames data gaps as the dominant risk and rejects aggressive assumptions.
```

Assessment:

The debate process surfaces unsupported claims and later challenges them, which
matches the product's debate style. The residual risk is not that the final
decision silently adopted the bull claims; it mostly did not. The risk is that
unsupported claims still exist in worker appendix material and could be cited by
readers.

Required follow-up:

```text
Worker appendix/export should preserve debate voice but make unsupported-claim
status easy to see, or the CRYPTO bull/bear prompts should require stronger
phrasing when using public context not present in the approved material.
Do not add a new investment/style gate without human approval.
```

### Low: Bear Report Also Uses External Historical Analogies Without Provider Evidence

Evidence:

```text
docs/evidence/trading_claw_trade_crypto_fresh_live_run-20260517-225048-30bcbc33_md/bear_researcher_report.md:18-21
uses FTX/MicroStrategy historical analogies.

docs/evidence/trading_claw_trade_crypto_fresh_live_run-20260517-225048-30bcbc33_md/bear_researcher_report.md:28-35
discusses long-term-holder distribution, exchange inflow, and ETF outflow risk
while acknowledging missing current on-chain/ETF data.
```

Assessment:

These are framed as cautionary analogies rather than current provider facts, but
they should not be treated as T16 evidence.

## Supported Facts

The following are sufficiently supported by the accepted T16 evidence chain:

```text
run completed and exported: runs/run-20260517-225048-30bcbc33/state.json and reports/export-result.json
OpenClaw payload/tool schema/tool calls count: 13/13/13
OpenBB evidence-chain audit passed: docs/evidence/openbb-crypto-btc-full-chain-http-openviking-20260517T225042Z/openbb-evidence-chain-run-20260517-225048-30bcbc33.json
OpenViking tree/grep/glob/ovpack collected in same runtime:
  docs/evidence/openbb-crypto-btc-full-chain-http-openviking-20260517T225042Z/openviking/openviking-runtime-evidence-run-20260517-225048-30bcbc33.json
chart assets exported:
  market-01-indicator_panels-5ac9dce2ae5f84b0.png
  market-02-market_structure-d57cec21cbbc66cd.png
```

## Deviation Status

```text
T16 evidence collection: accepted for CRYPTO BTC.
T17 final closure: blocked pending post-tightening runtime proof or explicit residual-risk acceptance.
mock/stub/fake/fallback status: none found in this review.
capture-only status: not used as provider success.
```

## Follow-Up Implementation

After this review, CRYPTO pack reader briefs and CRYPTO worker prompts were
tightened so future runs must distinguish OpenBB-collected provider facts from
search-discovery lines, public context, unsupported ETF/institutional flow
narratives, derivatives gaps, on-chain gaps, liquidation gaps, and macro/AHR999
gaps.

Changed surfaces:

```text
src/claw_trade/data_gateway/packs/market.py
src/claw_trade/data_gateway/packs/news.py
src/claw_trade/data_gateway/packs/social.py
agents/*/prompts/CRYPTO.md for CRYPTO downstream/frontline workers
tests/integration/data_gateway/test_market_pack_crypto.py
tests/integration/data_gateway/test_news_pack_cn_a.py
tests/integration/data_gateway/test_social_pack_cn_a.py
tests/contracts/test_worker_prompt_alignment_policy.py
```

Verification:

```bash
uv run pytest tests/integration/data_gateway/test_market_pack_crypto.py \
  tests/integration/data_gateway/test_news_pack_cn_a.py::test_news_pack_crypto_search_discovery_does_not_prove_etf_or_institutional_facts \
  tests/integration/data_gateway/test_social_pack_cn_a.py::test_social_pack_crypto_search_discovery_does_not_prove_consensus_or_institutional_facts \
  tests/contracts/test_worker_prompt_alignment_policy.py::test_approved_crypto_market_prompt_uses_compact_pack_boundary \
  tests/contracts/test_worker_prompt_alignment_policy.py::test_crypto_frontline_prompts_force_missing_data_into_worker_l1_reports \
  tests/contracts/test_worker_prompt_alignment_policy.py::test_crypto_downstream_prompts_condition_on_upstream_data_gaps_without_filling_facts \
  -q
```

Exit code: `0`

Key output:

```text
6 passed
```

Limit:

```text
The accepted BTC run predates the tightening. A future BTC runtime proof is still
needed before claiming the new wording changed live reader-facing behavior.
```

## Post-Tightening Runtime Proof

The BTC `/report` run was rerun after the prompt/pack wording changes under the
fixed runtime command mode.

Run summary:

```text
run_id=run-20260517-234339-7c0e760e
output_dir=docs/evidence/trading_claw_trade_crypto_fresh_live_run-20260517-234339-7c0e760e_md
state_status=completed
request_data_gateway=openbb
export_status=passed
provider_request_count=13
visible_tools_count=13
tool_calls_count=13
openbb_audit_passed=true
openbb_attempt_count=9
openbb_provider_http_evidence=10
openbb_missing_raw_refs=[]
success_http_missing_response_status=[]
success_http_missing_headers=[]
openviking_tree_status=ok
openviking_grep_status=ok
openviking_glob_status=ok
openviking_ovpack_export_status=metadata_verified
openviking_ovpack_import_status=ok
openviking_runtime_health_status=blocked
```

Evidence paths:

```text
docs/evidence/openbb-crypto-btc-post-tightening-full-chain-http-openviking-20260517T234333Z/post-tightening-summary.json
docs/evidence/openbb-crypto-btc-post-tightening-full-chain-http-openviking-20260517T234333Z/openbb-evidence-chain-run-20260517-234339-7c0e760e.json
docs/evidence/openbb-crypto-btc-post-tightening-full-chain-http-openviking-20260517T234333Z/openviking/openviking-runtime-evidence-run-20260517-234339-7c0e760e.json
docs/evidence/openbb-crypto-btc-post-tightening-full-chain-http-openviking-20260517T234333Z/btc-claim-source-grep.txt
```

Claim-source recheck:

```text
The new final report labels derivative, on-chain, macro, AHR999, ETF/institution,
news, and social-consensus coverage as missing, zero-row, search-discovery, or
not verified where applicable.

The social report explicitly says search-discovery lines are not social
consensus or verified facts.

The final report no longer repeats BlackRock/Fidelity-style institutional buying
claims as verified portfolio evidence.
```

Remaining content risk:

```text
Bull/bear debate appendix material still contains strong public-context and
counterfactual crypto narratives. The final report mostly rejects or conditions
those claims, and adding a new runtime style/investment gate would require human
approval under AGENTS.md. This is therefore tracked as residual review risk, not
as evidence-chain failure.
```

## Commands

```text
rg -n "BlackRock|Fidelity|ETF|链上|资金费率|OI|清算|MicroStrategy|300-400|交易所余额|哈希率|现货" docs/evidence/trading_claw_trade_crypto_fresh_live_run-20260517-225048-30bcbc33_md/final_report.md docs/evidence/trading_claw_trade_crypto_fresh_live_run-20260517-225048-30bcbc33_md/*_report.md
nl -ba docs/evidence/trading_claw_trade_crypto_fresh_live_run-20260517-225048-30bcbc33_md/final_report.md
nl -ba docs/evidence/trading_claw_trade_crypto_fresh_live_run-20260517-225048-30bcbc33_md/social_analyst_report.md
nl -ba docs/evidence/trading_claw_trade_crypto_fresh_live_run-20260517-225048-30bcbc33_md/bull_researcher_report.md
nl -ba docs/evidence/trading_claw_trade_crypto_fresh_live_run-20260517-225048-30bcbc33_md/bear_researcher_report.md
```

Exit code: 0 for all read commands.

## Next Action

Do not change final report evidence files. The next implementation action is to
finish T14 legacy deletion/quarantine and T17 conformance review without adding
new style or investment-judgment gates.
