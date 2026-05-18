# OpenBB MCP Pack Endpoints T5 Evidence

Status: T5 wrapper and exposure evidence collected

Date: 2026-05-17

## Scope

This evidence proves only the OpenBB/FastAPI/MCP pack endpoint wrapper and tool exposure boundary.

It does not prove live provider data collection, pack domain coverage, OpenClaw visible schema, or four-market fresh runs.

## Tests

Command:

```bash
uv run pytest \
  tests/unit/data_gateway/test_openbb_runtime_wrapper.py \
  tests/integration/data_gateway/test_mcp_pack_endpoints.py
```

Exit code: 0

Key output:

```text
7 passed in 4.73s
```

## Pack Routes

Command:

```bash
uv run python - <<'PY'
from claw_trade.data_gateway.mcp.runtime_wrapper import PACK_ENDPOINTS, PACK_TOOL_NAMES
print('endpoints', sorted(PACK_ENDPOINTS.values()))
print('tools', sorted(PACK_TOOL_NAMES.values()))
PY
```

Exit code: 0

Key output:

```text
endpoints ['/api/v1/claw/get_fundamental_pack', '/api/v1/claw/get_market_pack', '/api/v1/claw/get_news_pack', '/api/v1/claw/get_social_pack']
tools ['claw_get_fundamental_pack', 'claw_get_market_pack', 'claw_get_news_pack', 'claw_get_social_pack']
```

## OpenBB MCP Tool Exposure

The integration test builds a real OpenBB MCP server from the local pinned submodule source:

```bash
uv run \
  --with ./third_party/openbb/openbb_platform/core \
  --with ./third_party/openbb/openbb_platform/extensions/mcp_server \
  --with fastapi \
  python -c '<pack-only MCP smoke>'
```

Observed route list:

```json
[
  "/api/v1/claw/get_fundamental_pack",
  "/api/v1/claw/get_market_pack",
  "/api/v1/claw/get_news_pack",
  "/api/v1/claw/get_social_pack"
]
```

Observed MCP tool list:

```json
[
  "claw_get_fundamental_pack",
  "claw_get_market_pack",
  "claw_get_news_pack",
  "claw_get_social_pack"
]
```

## Boundary Checks

- `enable_tool_discovery=True` is rejected for the pack-only runtime.
- The wrapper creates a pack-only FastAPI app and then prunes any non-pack MCP tool after OpenBB MCP server creation.
- Unconfigured pack service returns `503 pack_service_unavailable`; it does not return fake `reader_brief_md`.
- Successful wrapper response returns only `reader_brief_md` and `status`.
- The wrapper source does not import `frontline_data_pack.provider_executor`.
- Required tool input fields such as `ticker`, `run_id`, `call_id`, and `worker_id` return `422 invalid_input` when missing.

## Remaining Risk

- D1/T5 does not implement domain pack provider fetching.
- D2/T7 still must prove OpenClaw model-visible tool schema and `openclaw_llm_provider_payload` boundaries.
- F/G/H still must implement real pack data collection and coverage.
