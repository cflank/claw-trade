# OpenBB Old Provider Import-Block T13 Evidence

Date: 2026-05-17

Scope:

- T13 old provider path inventory and OpenBB flag import-block proof.
- No deletion was performed here; deletion belongs to T14 and requires live/fresh pack evidence.

## Legacy Path Inventory

Key old path hits:

- `openclaw_plugins/claw-trade-frontline-tools/python/frontline_data_pack/provider_executor.py`
- `openclaw_plugins/claw-trade-frontline-tools/python/frontline_data_pack/market_data_pack.py`
- `openclaw_plugins/claw-trade-frontline-tools/python/frontline_data_pack/fundamentals_data_pack.py`
- `openclaw_plugins/claw-trade-frontline-tools/python/frontline_data_pack/news_data_pack.py`
- `openclaw_plugins/claw-trade-frontline-tools/python/frontline_data_pack/social_sentiment_pack.py`
- `openclaw_plugins/claw-trade-frontline-tools/python/frontline_data_pack/crypto_market_data_pack.py`
- `openclaw_plugins/claw-trade-frontline-tools/python/frontline_data_pack/crypto_fundamental_data_pack.py`
- `openclaw_plugins/claw-trade-frontline-tools/python/frontline_data_pack/crypto_news_data_pack.py`
- `openclaw_plugins/claw-trade-frontline-tools/python/frontline_data_pack/crypto_social_sentiment_pack.py`
- `openclaw_plugins/claw-trade-frontline-tools/python/frontline_data_pack/**`
- agent skill legacy entries under `agents/*/skills/*/scripts/*_data_pack.py`

The current `index.js` and plugin manifest no longer register these old tools.
The remaining hits are not accepted as OpenBB runtime fallback. They remain T14
physical deletion/archive work after live/fresh evidence.

T14 progress after the import-block proof:

- The old US atomic runtime module `frontline_data_pack/us_data_pack.py` was removed.
- The old `scripts/frontline_preflight.py` entry and `frontline_data_pack.health` module were removed because they imported and validated the retired provider executor path.
- `tests/contracts/test_frontline_tool_protocol.py` now checks only canonical OpenBB pack tools, not old US atomics or historical domain pack tool names.
- The historical shared provider package `openclaw_plugins/claw-trade-frontline-tools/python/frontline_data_pack/**` was removed.
- Old CN_A market/fundamental/news/social direct data-service scripts and tests were removed; only non-provider policy helpers remain for stage-policy contract checks.
- The old `src/claw_trade/providers/**` namespace and its retired Tushare helper test were removed. Active Tushare access now lives only under `src/claw_trade/data_gateway/providers/tushare_client.py`.

## Import-Block Proof

Test file:

- `tests/integration/data_gateway/test_old_provider_import_block.py`

What it proves:

- In a clean subprocess, old modules such as `frontline_data_pack` and `frontline_data_pack.provider_executor` are blocked by an import hook.
- With `CLAW_TRADE_OPENBB_TOOL_SCHEMA_ENABLED=true`, canonical tool resolution returns `claw_get_market_pack`.
- `OpenBBRuntimeWrapper + DomainPackService` still returns an explicit pack response when old modules are unimportable.
- The response is explicit `blocked/insufficient` with a source-not-configured gap, not a legacy fallback success.
- OpenBB gateway modules do not import `frontline_data_pack` or `provider_executor`.

Command:

```bash
uv run pytest tests/integration/data_gateway/test_mcp_visible_tools.py \
  tests/integration/data_gateway/test_old_provider_import_block.py
```

Exit code: `0`

Key output:

```text
17 passed
```

Broader regression including T13/T15:

```bash
uv run pytest tests/unit/data_gateway tests/integration/data_gateway \
  tests/unit/test_run_control_cli.py tests/contracts/test_final_report_exporter.py
```

Exit code: `0`

Key output:

```text
134 items collected
131 passed, 3 skipped, 3 warnings
```

Mongo-gated skipped tests were rerun with real Mongo:

```bash
DATA_GATEWAY_MONGODB_URI=mongodb://127.0.0.1:27017 \
uv run pytest tests/integration/data_gateway/test_mongo_attempts.py \
  tests/integration/data_gateway/test_run_provider_plan_snapshot.py
```

Exit code: `0`

Key output:

```text
3 passed, 5 warnings
```

## Boundary Status

- Deviation status: T13 import-block proof implemented; no old provider fallback allowed in OpenBB flag path.
- Real implementation status: negative import-block tests and static source scans are implemented.
- Mock/stub/fake/fallback status: import hook is only a negative proof; no fake provider success is used.
- Coverage: MCP visible tool schema, old module import block, canonical tool registry, OpenBB wrapper/pack service explicit failure path.
- Remaining risk: T14 physical deletion/quarantine has removed the current old provider runtime package, direct data-service wrappers, and old `claw_trade.providers` namespace. The baseline capture script was archived to `docs/reference/legacy/scripts/capture_tradingagents_us.py` after human approval. T17 still needs the final old-token `rg` explanation table. T16 live/fresh evidence is now available. The human selected Git/VCS-only rollback, so no real `legacy_rollback` runtime sample is expected in the current mainline.
