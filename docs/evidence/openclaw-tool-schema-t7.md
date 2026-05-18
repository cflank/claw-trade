# OpenClaw Tool Schema T7 Evidence

Status: T7 tool schema boundary evidence collected

Date: 2026-05-17

## Scope

This evidence proves the OpenBB-enabled model-visible tool schema boundary and payload-scan boundary.

It does not prove live OpenClaw execution or four-market provider data collection.

## Tests

Command:

```bash
uv run pytest \
  tests/unit/data_gateway/test_tool_schema.py \
  tests/integration/data_gateway/test_mcp_visible_tools.py
```

Exit code: 0

Key output:

```text
14 passed in 2.93s
```

## OpenBB Flag Boundary

When `CLAW_TRADE_OPENBB_TOOL_SCHEMA_ENABLED=true`, all frontline market/profile tool intents resolve to canonical pack tools only:

```text
claw_get_market_pack
claw_get_fundamental_pack
claw_get_news_pack
claw_get_social_pack
```

The same test checks that US atomics do not appear in the model-visible tool set:

```text
get_stock_data
get_indicators
get_fundamentals
get_balance_sheet
get_cashflow
get_income_statement
get_news
get_global_news
```

The same test also rejects OpenBB atomic/admin/discovery/raw/debug/cache-style tool names.

## Downstream Boundary

For non-frontline workers, resolved tools are empty under the OpenBB tool-schema flag.

This covers downstream workers such as debate, research manager, trader, risk, and portfolio manager roles.

## Payload Boundary

The `openclaw_llm_provider_payload` scan accepts only payloads with:

- `source=provider_request_capture`
- model-visible `messages`
- model-visible `tools`

The scan rejects payloads whose source is:

```text
openbb_provider_http_evidence
openbb_provider_raw_evidence
```

This keeps the evidence names separate:

- `openclaw_llm_provider_payload`: OpenClaw request sent to the LLM provider.
- `openbb_provider_http_evidence`: OpenBB/adapter HTTP evidence from data provider calls.
- `openbb_provider_raw_evidence`: raw payload/hash/ref evidence from data providers.

## Remaining Risk

- Legacy tools remain registered in the existing OpenClaw plugin contract while the migration flag is disabled.
- T13/T14 must still remove or import-block migrated old paths and prove they cannot silently fallback after OpenBB failure.
- Live provider payload capture still belongs to later runtime/live validation.
