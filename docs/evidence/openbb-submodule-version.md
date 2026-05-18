# OpenBB Submodule Version Evidence

Status: T0 approved for D0/T1

Date: 2026-05-17

## Human Decisions

- Repository URL: `https://github.com/OpenBB-finance/OpenBB`
- Fixed version: OpenBB Platform `v4.7.0`
- Fixed commit: `dddc3b328284bb953b6e5468b167b39c78490001`
- Do not pin: floating `develop`, moving `ODP`, or moving `Open-Data-Platform-v1.0.2`
- Use posture: personal research use approved by human on 2026-05-17
- Commercial posture: not approved for commercial distribution or SaaS operation without a later license review
- Extension placement: project provider/pack code lives under `src/claw_trade/data_gateway/**` and is loaded by OpenBB runtime as a project extension/package
- `third_party/openbb` placement: vendor runtime core only; it may contain at most a thin generic shim if OpenBB requires a tree-local extension hook

## Version Evidence

Command:

```bash
git ls-remote --tags https://github.com/OpenBB-finance/OpenBB.git refs/tags/v4.7.0 refs/tags/ODP refs/tags/Open-Data-Platform-v1.0.2
```

Key output:

```text
a08d5d75d00564f10503f915e4aab64119c50a13 refs/tags/ODP
a08d5d75d00564f10503f915e4aab64119c50a13 refs/tags/Open-Data-Platform-v1.0.2
dddc3b328284bb953b6e5468b167b39c78490001 refs/tags/v4.7.0
```

Local fixed-source verification:

```bash
git clone --depth 1 --branch v4.7.0 https://github.com/OpenBB-finance/OpenBB.git /tmp/claw-openbb-v4.7.0
git -C /tmp/claw-openbb-v4.7.0 rev-parse HEAD
git -C /tmp/claw-openbb-v4.7.0 tag --points-at HEAD
```

Key output:

```text
dddc3b328284bb953b6e5468b167b39c78490001
v4.7.0
```

## License Evidence

OpenBB repository license at `v4.7.0`: AGPL-3.0-only.

Sources:

- https://raw.githubusercontent.com/OpenBB-finance/OpenBB/v4.7.0/LICENSE
- https://openbb.co/blog/license-change-openbb-platform-goes-agpl/

Human-approved use boundary:

- Current use is personal research.
- If this project is later distributed, hosted for others, or commercialized, stop and run a new license/commercial-use review.
- Provider raw payload export is conservative by default: `metadata_only` unless the specific provider terms allow raw snapshot export.

## Extension And MCP Capability Evidence

Official docs:

- OpenBB Python extensions: https://docs.openbb.co/odp/python/extensions
- OpenBB MCP server: https://docs.openbb.co/odp/python/extensions/interface/openbb-mcp

Source evidence from `v4.7.0`:

- `openbb_platform/core/openbb_core/app/extension_loader.py` defines extension groups including `openbb_core_extension` and `openbb_provider_extension`.
- `openbb_platform/core/openbb_core/app/router.py` loads routers from installed extensions.
- Existing OpenBB extension packages use `[tool.poetry.plugins."openbb_core_extension"]`.
- Existing OpenBB provider packages use `[tool.poetry.plugins."openbb_provider_extension"]`.
- `openbb_platform/extensions/mcp_server/openbb_mcp_server/app/app.py` exposes `create_mcp_server(settings, fastapi_app, ...)`.
- `openbb_platform/extensions/mcp_server/openbb_mcp_server/utils/app_import.py` supports `--app <module.path:app_instance>` or file path.

Runtime capability smoke command:

```bash
uv run \
  --with /tmp/claw-openbb-v4.7.0/openbb_platform/core \
  --with /tmp/claw-openbb-v4.7.0/openbb_platform/extensions/mcp_server \
  --with fastapi \
  python -c "from fastapi import FastAPI; from openbb_mcp_server.app.app import create_mcp_server; from openbb_mcp_server.models.settings import MCPSettings; app=FastAPI(); \
for name in ['market_pack','fundamental_pack','news_pack','social_pack']: app.get('/api/v1/claw/'+name, tags=['claw'])(lambda name=name: {'reader_brief_md': name}); \
settings=MCPSettings(default_tool_categories=['claw'], allowed_tool_categories=['claw'], enable_tool_discovery=False, api_prefix='/api/v1'); \
mcp=create_mcp_server(settings, app); print(type(mcp).__name__); print('discovery', settings.enable_tool_discovery); print('route_count', len([r for r in app.routes if getattr(r, 'path', '').startswith('/api/v1/claw/')]))"
```

Exit code: 0

Key output:

```text
FastMCPOpenAPI
discovery False
route_count 4
```

Interpretation:

- OpenBB `v4.7.0` can load custom FastAPI routes into its MCP server path.
- A project FastAPI app can expose four domain pack endpoints and turn them into MCP tools.
- Tool discovery can be disabled for a fixed toolset.
- This is enough to proceed with D0/T1 and D1 design implementation.
- D1/T5 must not rely on OpenBB category settings alone as the exposure boundary. It must expose a pack-only FastAPI app, keep discovery disabled, and verify the visible tool list contains only the approved pack endpoints.

Limit:

- This is not proof that the four claw-trade packs are implemented.
- This is not proof of live provider fetch, Mongo evidence, OpenViking lineage, or OpenClaw provider payload.
- Those remain T2-T17 scope.

## Runtime Marker

Command:

```bash
uv run --with ./third_party/openbb/openbb_platform/core \
  python -c "import importlib.metadata as md; import openbb_core; print('openbb-core', md.version('openbb-core')); print('module', openbb_core.__name__)"
```

Exit code: 0

Key output:

```text
openbb-core 1.6.0
module openbb_core
```

## Upgrade And Rollback

Upgrade rule:

- Any OpenBB version change must update this file, update the submodule pointer, and rerun extension/MCP smoke, source-role tests, tool-schema tests, and four-market fresh/live evidence.

Rollback rule:

- Rollback means pinning a previous approved OpenBB commit/tag in `third_party/openbb`.
- Rollback must not re-enable old provider executor or old MCP silent fallback inside the same `/report` run.

## D0/T1 Execution Evidence (Submodule Vendor Core)

Date: 2026-05-17

Executed commands:

```bash
git submodule add https://github.com/OpenBB-finance/OpenBB third_party/openbb
git submodule add --depth 1 https://github.com/OpenBB-finance/OpenBB third_party/openbb
git -C third_party/openbb fetch --depth 1 origin tag v4.7.0
git -C third_party/openbb checkout dddc3b328284bb953b6e5468b167b39c78490001
git submodule status third_party/openbb
git -C third_party/openbb rev-parse HEAD
git -C third_party/openbb tag --points-at HEAD
git -C third_party/openbb describe --tags --always
scripts/setup-openbb-dev.sh
```

Key output:

```text
+dddc3b328284bb953b6e5468b167b39c78490001 third_party/openbb (v4.7.0)
dddc3b328284bb953b6e5468b167b39c78490001 third_party/openbb (v4.7.0)
dddc3b328284bb953b6e5468b167b39c78490001
v4.7.0
v4.7.0
[openbb-dev-setup] prepared:
- runtime dir: /home/frank/src/claw-trade/.runtime/dev-services/openbb
- template: /home/frank/src/claw-trade/.runtime/dev-services/openbb/openbb.env.template
- env file: /home/frank/src/claw-trade/.runtime/dev-services/openbb/openbb.env
```

Notes:

- The first full-history `git submodule add` was interrupted during large clone transfer in this environment; D0 then used shallow submodule add plus explicit tag fetch and fixed commit checkout.
- Final submodule pointer is pinned to approved commit/tag; no floating branch pin is used.
- The initial `+` in the submodule status was before the parent index was aligned. Manager review reran `git submodule status third_party/openbb` and got the fixed pointer without the `+` prefix.
- T1 submodule/runtime-marker evidence is complete. Provider credential diagnostics such as `credential_missing` require the T2 adapter/settings contract and remain a downstream acceptance item, not a D0 submodule claim.
