# OpenBB Packs And OpenViking T8-T11 Evidence

Date: 2026-05-17

Scope:

- T8 MarketPack for CN_A/HK/US/CRYPTO.
- T9 FundamentalPack for CN_A/HK/US/CRYPTO.
- T10 NewsPack/SocialPack source-role boundaries.
- T11 OpenViking material/context/runtime health wrappers.
- Pack service integration between OpenBB pack endpoint wrapper and the four domain pack builders.

## Implementation Evidence

Relevant files:

- `src/claw_trade/data_gateway/packs/market.py`
- `src/claw_trade/data_gateway/packs/charts.py`
- `src/claw_trade/data_gateway/packs/fundamental.py`
- `src/claw_trade/data_gateway/packs/news.py`
- `src/claw_trade/data_gateway/packs/social.py`
- `src/claw_trade/data_gateway/packs/service.py`
- `src/claw_trade/data_gateway/providers/market.py`
- `src/claw_trade/data_gateway/providers/market_adapters.py`
- `src/claw_trade/data_gateway/providers/fundamental.py`
- `src/claw_trade/data_gateway/providers/news.py`
- `src/claw_trade/data_gateway/providers/news_source_roles.py`
- `src/claw_trade/data_gateway/providers/social.py`
- `src/claw_trade/data_gateway/providers/social_source_roles.py`
- `src/claw_trade/data_gateway/openviking/**`
- `src/claw_trade/artifacts/openviking_client.py`

Key contracts:

- MarketPack normalizes OHLCV, computes indicators, emits chart readiness, and keeps HK `stock_hk_daily` as `00700.HK -> 00700`, `qfq`, `HKD`, `Asia/Hong_Kong`.
- MarketPack reader brief no longer exposes `remote_success`, `fresh_remote`, `cache_hit`, `freshness=`, `cache=`, `raw://`, or Mongo/OpenViking protocol refs.
- FundamentalPack preserves official-original priority/conflicts and keeps raw refs in audit fields, not reader brief.
- News search providers are constrained to discovery only; search results cannot become official/news facts.
- Social search providers are constrained to discovery only; Alternative.me remains market-level sentiment, Polymarket remains event expectation.
- Default provider catalog now covers every market/domain pair for `MARKET/FUNDAMENTAL/NEWS/SOCIAL`.
- `DomainPackService` routes four pack domains from one pack service without importing `frontline_data_pack` or `provider_executor`.
- OpenViking semantic index cannot report `ok` while the report runtime vector/semantic queue is disabled; relation write failure now blocks instead of being silently swallowed.
- `OpenVikingHttpBackend` maps control-plane wrappers to real OpenViking HTTP
  endpoints for tree, grep, glob, relations, ovpack export/import, observer
  health, and semantic queue status. The semantic/vector queue remains reported
  as `blocked` in the report runtime, not `ok`.

## Verification Commands

Focused T8/T10/T11 regression:

```bash
uv run pytest tests/unit/data_gateway/test_default_provider_catalog.py \
  tests/unit/data_gateway/test_source_roles.py \
  tests/integration/data_gateway/test_market_pack_cn_a.py \
  tests/integration/data_gateway/test_market_pack_hk.py \
  tests/integration/data_gateway/test_market_pack_us.py \
  tests/integration/data_gateway/test_market_pack_crypto.py \
  tests/integration/data_gateway/test_news_pack_cn_a.py \
  tests/integration/data_gateway/test_news_pack_hk.py \
  tests/integration/data_gateway/test_news_pack_us.py \
  tests/integration/data_gateway/test_news_pack_crypto.py \
  tests/integration/data_gateway/test_social_pack_cn_a.py \
  tests/integration/data_gateway/test_social_pack_hk.py \
  tests/integration/data_gateway/test_social_pack_us.py \
  tests/integration/data_gateway/test_social_pack_crypto.py \
  tests/unit/data_gateway/test_openviking_context_index.py \
  tests/unit/data_gateway/test_openviking_relations.py
```

Exit code: `0`

Key output:

```text
28 passed in 0.53s
```

Pack service focused regression:

```bash
uv run pytest tests/unit/data_gateway/test_domain_pack_service.py \
  tests/unit/data_gateway/test_default_provider_catalog.py \
  tests/unit/data_gateway/test_source_roles.py \
  tests/integration/data_gateway/test_market_pack_cn_a.py \
  tests/integration/data_gateway/test_market_pack_hk.py \
  tests/integration/data_gateway/test_market_pack_us.py \
  tests/integration/data_gateway/test_market_pack_crypto.py \
  tests/integration/data_gateway/test_news_pack_cn_a.py \
  tests/integration/data_gateway/test_social_pack_cn_a.py
```

Exit code: `0`

Key output:

```text
21 passed in 0.25s
```

Data gateway and CLI regression:

```bash
uv run pytest tests/unit/data_gateway tests/integration/data_gateway tests/unit/test_run_control_cli.py
```

Exit code: `0`

Key output:

```text
111 items collected
108 passed, 3 skipped, 3 warnings
```

The three skipped tests are Mongo-gated tests when the command is run without `DATA_GATEWAY_MONGODB_URI`. They were rerun against real Mongo:

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

Additional OpenViking HTTP backend and runtime evidence checks:

```bash
uv run pytest tests/unit/test_openviking_backend_http.py \
  tests/unit/data_gateway/test_openviking_context_index.py \
  tests/unit/data_gateway/test_openviking_runtime_health.py \
  tests/integration/data_gateway/test_openviking_evidence_bundle.py
```

Exit code: `0`

Key output:

```text
22 passed
```

Same-runtime US/AAPL evidence collection:

```text
run_id=run-20260517-220630-c9edcab2
tree.status=ok
tree.node_count=33
grep.status=ok
glob.status=ok
ovpack_export.portability_status=metadata_verified
ovpack_import.import_status=ok
context_index semantic.status=blocked
runtime_health.status=blocked
```

Evidence file:

```text
docs/evidence/openbb-us-full-chain-http-openviking-20260517T220627Z/openviking/openviking-runtime-evidence-run-20260517-220630-c9edcab2.json
```

`runtime_health.status=blocked` is expected because semantic/vector queue is
disabled and recovery API is not confirmed. This is truthful blocked evidence,
not a fake healthy status.

## Boundary Status

- Deviation status: no boundary deviation found in scoped T8-T11 code after manager fixes; old provider import-block and deletion remain T13/T14.
- Real implementation status: pack builders, domain service, provider catalog, OpenViking wrappers, and contract/integration tests are implemented. Current pack provider execution is still contract-level unless real adapters/keys/runtime are wired for T16.
- Mock/stub/fake/fallback status: no fake success or silent fallback used. Tests use contract adapters only as unit/integration doubles and are not live provider evidence.
- Coverage: CN_A/HK/US/CRYPTO pack contracts, HK `stock_hk_daily`, chart readiness, source-role boundaries, semantic index disabled state, relation write failure, default capability coverage, and domain service routing.
- Remaining risk: four-market live/fresh evidence, OpenClaw provider payload, OpenBB HTTP/raw evidence, old path import-block, worker/exporter boundary, and final evidence chain are not collected yet.
