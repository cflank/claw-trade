# OpenBB Worker And Exporter Boundary T15 Evidence

Date: 2026-05-17

Scope:

- Worker-visible material boundary for OpenBB packs.
- OpenBB MCP response boundary.
- Frontline versus downstream tool schema boundary.
- Final report exporter approved-material boundary.

## Implementation Evidence

Test file:

- `tests/integration/data_gateway/test_worker_exporter_boundary.py`

What it proves:

- `DomainPackResult.worker_primary_material_md` is exactly the natural-language `reader_brief_md`.
- Pack audit refs, Mongo refs, raw refs, cache receipts, and OpenViking protocol refs remain in audit fields and do not enter worker primary material.
- OpenBB pack endpoint responses expose only `reader_brief_md` and `status`; attempts/raw/cache/audit payloads are not returned to the worker tool response.
- With `CLAW_TRADE_OPENBB_TOOL_SCHEMA_ENABLED=true`, only the four frontline analysts receive canonical pack tools.
- Downstream workers in this boundary test receive no OpenBB/OpenViking/Mongo raw/debug tools.
- `FinalReportExporter` source reads approved L1 material via `read_approved_l1` and does not import Mongo/data_gateway store/raw/cache readers.
- Rendering preserves PM text instead of rewriting the PM final decision.

## Verification Commands

Focused T15 command:

```bash
uv run pytest tests/integration/data_gateway/test_worker_exporter_boundary.py
```

Exit code: `0`

Key output:

```text
5 passed
```

T13/T15 combined command:

```bash
uv run pytest tests/integration/data_gateway/test_mcp_visible_tools.py \
  tests/integration/data_gateway/test_old_provider_import_block.py \
  tests/integration/data_gateway/test_worker_exporter_boundary.py
```

Exit code: `0`

Key output:

```text
19 passed
```

Broader regression including exporter contracts:

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

- Deviation status: no T15 boundary drift found in focused tests.
- Real implementation status: response/material/exporter boundary tests are implemented; live OpenClaw provider payload evidence remains T16 work.
- Mock/stub/fake/fallback status: no fake success. Tests use local contract objects only to prove visibility boundaries.
- Coverage: worker primary material, MCP response shape, tool schema, exporter source boundary, PM text preservation.
- Remaining risk: live run evidence still needs true `openclaw_llm_provider_payload`, tool calls, OpenBB HTTP/raw evidence, OpenViking lineage, ovpack, runtime health, chart readiness, and final report chain.
