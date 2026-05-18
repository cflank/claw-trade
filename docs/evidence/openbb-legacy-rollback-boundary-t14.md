# OpenBB Legacy Rollback Boundary T14 Evidence

Date: 2026-05-17

Scope:

- T14 old-provider rollback boundary after the human selected Option A.
- Default and explicit-env OpenBB tool exposure.
- Runtime quarantine of the old provider path from Python registry, OpenClaw plugin registration, and worker skill manifests.

## Human Decision

The human selected Option A on 2026-05-17:

```text
A
```

Meaning recorded for implementation:

- Do not keep a runnable old provider rollback switch in the current mainline.
- Rollback is a Git/VCS operational procedure only.
- The current runtime must keep `data_gateway=openbb`.
- `CLAW_TRADE_LEGACY_ROLLBACK_ENABLED=true` must not expose old provider tools or mark the request as `legacy_rollback`.

## Implemented Boundary

Runtime changes:

- `src/claw_trade/config/tool_names.py`
  - Removed the legacy rollback branch.
  - All frontline intents now resolve only to:
    `claw_get_market_pack`, `claw_get_fundamental_pack`,
    `claw_get_news_pack`, `claw_get_social_pack`.
- `src/claw_trade/cli/run_control.py`
  - `_data_gateway_mode_from_env()` now always returns `openbb`.
  - The old env flag no longer changes request metadata.
- `openclaw_plugins/claw-trade-frontline-tools/index.js`
  - Removed the old tool registration branch.
  - Removed old tool names and old `frontline_data_pack` script wiring from the plugin entry.
  - The plugin registers only the four canonical OpenBB pack tools.
- `openclaw_plugins/claw-trade-frontline-tools/openclaw.plugin.json`
  - Manifest description no longer advertises a legacy runtime branch.
- Frontline worker skill manifests and visible skill text now export/reference only canonical OpenBB pack tools.

Physical deletion/quarantine progress:

- Deleted retired US atomic implementation:
  `openclaw_plugins/claw-trade-frontline-tools/python/frontline_data_pack/us_data_pack.py`.
- Deleted retired US atomic parity test:
  `tests/unit/test_us_data_pack_original_parity.py`.
- Deleted the old frontline preflight runtime entry:
  `scripts/frontline_preflight.py`.
- Deleted the old frontline health module and tests that imported the old provider executor:
  `openclaw_plugins/claw-trade-frontline-tools/python/frontline_data_pack/health.py`,
  `tests/unit/test_frontline_health.py`,
  `tests/unit/test_frontline_preflight.py`.
- Rewrote `tests/contracts/test_frontline_tool_protocol.py` so protocol coverage now exercises only the four canonical OpenBB pack tools.
- Deleted the historical shared old provider package:
  `openclaw_plugins/claw-trade-frontline-tools/python/frontline_data_pack/**`.
- Deleted old CN_A market/fundamental/news/social direct data-service scripts and their retired tests.
  Only non-provider policy helpers remain:
  `agents/fundamental_analyst/skills/cn-a-fundamental-data/scripts/boundary.py`,
  `agents/fundamental_analyst/skills/cn-a-fundamental-data/scripts/policy.py`,
  `agents/social_analyst/skills/cn-a-social-data/scripts/policy.py`.
- Deleted the retired `src/claw_trade/providers/**` namespace and its old unit test.
  The active Tushare helper is now scoped under `src/claw_trade/data_gateway/providers/tushare_client.py`.

Documentation changes:

- `docs/数据源openbb引入方案.md`
- `docs/数据源修改实施方案.md`

Both now record the human decision: rollback is Git/VCS only, not a live dual provider path.

## Verification

Focused T14 runtime-boundary command:

```bash
uv run pytest tests/contracts/test_tool_registry_contract.py \
  tests/contracts/test_frontline_tool_contract.py \
  tests/contracts/test_stage_tool_policy_contract.py \
  tests/contracts/test_cn_a_news_skill_manifest_contract.py \
  tests/contracts/test_cn_a_fundamental_skill_manifest_contract.py \
  tests/contracts/test_cn_a_social_skill_manifest_contract.py \
  tests/unit/test_run_control_cli.py \
  tests/integration/data_gateway/test_old_provider_import_block.py \
  tests/integration/data_gateway/test_mcp_visible_tools.py -q
```

Exit code: `0`

Key output:

```text
80 passed in 12.62s
```

T14/tool-boundary regression command:

```bash
uv run pytest tests/unit/test_run_claw_trade_fresh_report_export.py \
  tests/unit/test_run_control_cli.py tests/unit/test_workflow_store.py \
  tests/unit/data_gateway/test_run_provider_plan.py \
  tests/contracts/test_tool_registry_contract.py \
  tests/contracts/test_stage_tool_policy_contract.py \
  tests/contracts/test_frontline_tool_contract.py \
  tests/contracts/test_frontline_prompt_write_contract.py \
  tests/contracts/test_worker_prompt_alignment_policy.py::test_approved_crypto_market_prompt_uses_compact_pack_boundary \
  tests/contracts/test_worker_prompt_alignment_policy.py::test_crypto_frontline_prompts_force_missing_data_into_worker_l1_reports \
  tests/contracts/test_worker_prompt_alignment_policy.py::test_hk_frontline_prompts_reuse_existing_domain_pack_tools \
  tests/contracts/test_worker_prompt_alignment_policy.py::test_us_fundamental_prompt_requires_quarterly_and_annual_statement_history \
  tests/contracts/test_cn_a_news_skill_manifest_contract.py \
  tests/contracts/test_cn_a_fundamental_skill_manifest_contract.py \
  tests/contracts/test_cn_a_social_skill_manifest_contract.py \
  tests/contracts/test_tool_calls_guard.py tests/unit/data_gateway/test_tool_schema.py \
  tests/unit/test_control_runner.py::test_crypto_market_worker_reaches_openclaw_with_compact_market_pack \
  tests/unit/test_openclaw_client.py::test_crypto_single_pack_frontline_command_sets_initial_tool_choice \
  tests/unit/test_openclaw_client.py::test_crypto_single_market_pack_command_forces_initial_tool_choice \
  tests/integration/data_gateway/test_old_provider_import_block.py \
  tests/integration/data_gateway/test_mcp_visible_tools.py -q
```

Exit code: `0`

Key output:

```text
113 passed in 10.83s
```

Plugin syntax check:

```bash
node --check openclaw_plugins/claw-trade-frontline-tools/index.js
```

Exit code: `0`

Runtime entry old-token sweep:

```bash
rg -n 'CLAW_TRADE_LEGACY_ROLLBACK_ENABLED|legacy_rollback|frontline_data_pack|market_market_data_pack|fundamental_fundamentals_data_pack|news_news_data_pack|social_social_sentiment_pack|crypto_market_data_pack|crypto_fundamental_data_pack|crypto_news_data_pack|crypto_social_sentiment_pack|get_stock_data|get_indicators|get_fundamentals|get_balance_sheet|get_cashflow|get_income_statement|get_global_news' \
  openclaw_plugins/claw-trade-frontline-tools/index.js \
  src/claw_trade/config/tool_names.py \
  src/claw_trade/cli/run_control.py
```

Exit code: `1`

Key output: no matches.

Worker skill old-token sweep:

```bash
rg -n '\b(market_market_data_pack|fundamental_fundamentals_data_pack|news_news_data_pack|social_social_sentiment_pack|crypto_market_data_pack|crypto_fundamental_data_pack|crypto_news_data_pack|crypto_social_sentiment_pack|get_stock_data|get_indicators|get_fundamentals|get_balance_sheet|get_cashflow|get_income_statement|get_global_news|get_news)\b' \
  agents/*/skills -g 'SKILL.md' -g 'manifest.yaml' -g 'openai.yaml' \
  -g 'quick-ref.md' -g 'source-boundaries.md'
```

Exit code: `1`

Key output: no matches.

Old provider physical-deletion regression:

```bash
uv run pytest tests/contracts/test_frontline_tool_protocol.py \
  tests/contracts/test_frontline_tool_contract.py \
  tests/contracts/test_frontline_prompt_write_contract.py \
  tests/contracts/test_cn_a_fundamental_skill_manifest_contract.py \
  tests/contracts/test_cn_a_news_skill_manifest_contract.py \
  tests/contracts/test_cn_a_social_skill_manifest_contract.py \
  tests/contracts/test_fundamental_gate_input_adapter.py \
  tests/contracts/test_tool_registry_contract.py \
  tests/contracts/test_stage_tool_policy_contract.py \
  tests/unit/data_gateway \
  tests/integration/data_gateway/test_old_provider_import_block.py \
  tests/integration/data_gateway/test_mcp_visible_tools.py -q
```

Exit code: `0`

Key output:

```text
215 passed, 3 skipped, 3 warnings in 23.51s
```

Old preflight runtime sweep:

```bash
rg -n 'frontline_preflight|run_frontline_preflight|frontline_data_pack\.health|frontline_tool_health|frontline_data_pack_health' \
  . -g '!docs/evidence/**' -g '!memory/**' -g '!third_party/openbb/**'
```

Exit code: `0`

Remaining output is limited to `docs/reference/legacy/**`; there is no active script, source module, or test import left for the old preflight entry.

## Remaining Risk

Runtime quarantine and the old provider runtime package have been removed from the current mainline.

Known remaining explained surfaces:

- `docs/reference/legacy/scripts/capture_tradingagents_us.py`, which is an archived baseline capture script and still names original TradingAgents US atomic tools; it is not a current OpenBB runtime fallback.
- negative tests that intentionally list old tool names as forbidden strings.
- `docs/reference/legacy/**` and historical evidence files from pre-OpenBB runs.

These are not registered by the current OpenClaw plugin and are not selected by the current tool registry.

Known broader regression blockers outside this T14 deletion slice:

- `tests/contracts/test_cn_a_fundamental_acceptance_assets_contract.py::test_cn_a_fundamental_acceptance_assets_contract`
  is now green after adding the missing acceptance template/manifest/index as pending evidence slots, not fake live proof.
- `tests/contracts/test_guard_change_requires_approval.py::test_runtime_guard_file_contents_are_frozen_without_human_approval`
  previously failed because `src/claw_trade/guards/tool_calls.py` had a modified hash. The human approved updating the guard hash on 2026-05-17.

Post-approval broad regression:

```bash
uv run pytest tests/unit tests/contracts tests/integration/data_gateway -q
```

Exit code: `0`

Key output:

```text
973 passed, 4 skipped, 3 warnings in 38.74s
```

Canonical T16 rerun after this T14 boundary:

- Evidence root: `docs/evidence/openbb-canonical-t16-20260518T013557Z/`
- CN_A `600519.SH`, HK `00700.HK`, HK `00005.HK`, US `AAPL`, and CRYPTO `BTC`
  all completed with `run_exit=0`, `audit_exit=0`, `openviking_collect_exit=0`,
  `final_report.md`, and two copied chart assets.

Latest broad regression command:

```bash
uv run pytest tests/unit tests/contracts tests/integration/data_gateway -q
```

Exit code: `1`

Key output:

```text
1 failed, 972 passed, 4 skipped, 3 warnings in 40.47s
```

The only failure is the guard-freeze hash mismatch for `src/claw_trade/guards/tool_calls.py`.

## Status

T14 executable rollback conflict is resolved by removing the executable rollback branch and the old provider runtime package.

Do not claim full OpenBB migration complete yet: T17 still needs the final `rg` explanation table, explicit baseline-capture archive/exempt decision, conformance review, and the broader regression blockers above.
