# OpenBB T16 Preflight And Current Blockers

Date: 2026-05-17

## Scope

This evidence note records the T16 fixed-runtime preflight and the current
implementation blockers before any four-market fresh/live claim.

This is not a live/fresh pass report.

## Fixed Runtime Profile

| Item | Required profile | Observed |
| --- | --- | --- |
| Startup path | `scripts/start-control-runtime.sh` | used |
| Python environment | claw-trade repo `uv` environment | used by startup script |
| OpenViking | `127.0.0.1:1933` | health passed during command-mode run |
| OpenClaw gateway | `127.0.0.1:18789` | health passed during command-mode run |
| Invest sidecar | disabled | no invest sidecar used |
| OpenViking source config | `~/.openviking/ov.conf` copied into runtime config | script generated runtime config |
| Runtime config/data/cache | `.runtime/dev-services/**` | used |

## Existing Runtime Reuse Check

Before restart:

```text
runtime_env_exists=yes
openviking_1933=fail
openclaw_18789=fail
CLAW_TRADE_OPENVIKING_MCP_MODULE=<unset>
CLAW_TRADE_OPENVIKING_MCP_CWD=<unset>
CLAW_TRADE_OPENVIKING_SERVER_BIN=<unset>
CLAW_TRADE_OPENVIKING_SERVER_CWD=<unset>
CLAW_TRADE_OPENVIKING_MCP_STARTED=0
OPENCLAW_GATEWAY_URL=ws://127.0.0.1:18789
OPENVIKING_ENDPOINT=http://127.0.0.1:1933
OPENVIKING_CONFIG_FILE=/home/frank/src/claw-trade/.runtime/dev-services/openviking/ov.conf
OPENVIKING_DATA_DIR=/home/frank/src/claw-trade/.runtime/dev-services/openviking/data
```

Decision: existing runtime could not be reused because both health checks failed.

## Clean Runtime Preflight

Command:

```bash
scripts/start-control-runtime.sh -- bash -lc '...health checks...'
```

Exit code: 0

Key output:

```text
health_openviking=pass
health_openclaw=pass
CLAW_TRADE_OPENVIKING_MCP_STARTED=0
OPENCLAW_GATEWAY_URL=ws://127.0.0.1:18789
OPENVIKING_ENDPOINT=http://127.0.0.1:1933
OPENVIKING_CONFIG_FILE=/home/frank/src/claw-trade/.runtime/dev-services/openviking/ov.conf
OPENVIKING_DATA_DIR=/home/frank/src/claw-trade/.runtime/dev-services/openviking/data
```

Conclusion: the fixed runtime can start cleanly. This only proves runtime
readiness, not OpenBB provider live/fresh success.

Follow-up script check:

```bash
bash -n scripts/start-control-runtime.sh
bash -n scripts/setup-openbb-dev.sh
scripts/setup-openbb-dev.sh
```

Exit code and key output:

```text
0
0
[openbb-dev-setup] prepared ... install deps: 0
```

The startup script now recreates `.runtime/dev-services/openbb/openbb.env.template`
after runtime cleanup, so fixed-runtime runs do not delete the OpenBB local env
template and leave the workspace in a broken state.

## Current T16 Blockers

### 1. Canonical OpenBB tools were not registered by the live OpenClaw plugin

`src/claw_trade/config/tool_names.py` maps OpenBB-enabled frontline intents to:

```text
claw_get_market_pack
claw_get_fundamental_pack
claw_get_news_pack
claw_get_social_pack
```

Initial finding: `openclaw_plugins/claw-trade-frontline-tools/index.js` and
`openclaw_plugins/claw-trade-frontline-tools/openclaw.plugin.json` registered old tool names such as:

```text
market_market_data_pack
crypto_market_data_pack
get_stock_data
get_indicators
fundamental_fundamentals_data_pack
...
```

This bridge-layer blocker is now closed.

Review result:

- `index.js` registers only the four canonical pack tools when
  `CLAW_TRADE_OPENBB_TOOL_SCHEMA_ENABLED=true`, then returns before registering
  legacy tools.
- The canonical bridge invokes `OpenBBRuntimeWrapper + DomainPackService +
  MongoRunProviderPlanStore`.
- Missing Mongo/run plan/runtime config returns an explicit blocked/error result.
- The canonical bridge slice does not import `frontline_data_pack` or
  `provider_executor`.

Manager verification:

```bash
node --check openclaw_plugins/claw-trade-frontline-tools/index.js
uv run pytest tests/unit/data_gateway/test_openbb_runtime_wrapper.py \
  tests/integration/data_gateway/test_mcp_visible_tools.py \
  tests/integration/data_gateway/test_old_provider_import_block.py
```

Exit code and key output:

```text
0
25 passed in 9.36s
```

Remaining limit: this proves the OpenClaw tool bridge and no-legacy-fallback
boundary. It does not prove live OpenBB provider data.

### 2. Market provider adapters

Initial blocker: `src/claw_trade/data_gateway/providers/market_adapters.py`
had capability/spec construction, but `fetch()` and `normalize()` raised
`NotImplementedError`.

This implementation blocker is now closed at scoped code-test level:

- CN_A/HK have Tushare/AkShare execution paths with explicit remote error
  behavior.
- US/CRYPTO use pinned OpenBB yfinance historical price calls.
- Market execution now writes raw/normalized/attempt Mongo refs through
  `ProviderExecutionEvidenceHelper`.

Manager verification:

```bash
uv run pytest tests/unit/data_gateway/test_provider_execution.py \
  tests/unit/data_gateway/test_domain_pack_service.py \
  tests/unit/data_gateway/test_market_adapters_live_contract.py \
  tests/integration/data_gateway/test_market_pack_cn_a.py \
  tests/integration/data_gateway/test_market_pack_hk.py \
  tests/integration/data_gateway/test_market_pack_us.py \
  tests/integration/data_gateway/test_market_pack_crypto.py
```

Exit code and key output:

```text
0
19 passed in 0.90s
```

Remaining limit: this is not a T16 live/fresh pass. It proves contract-level
execution and evidence refs, but not real four-market fresh provider evidence.

### 3. Fundamental/news/social execution evidence

Initial blocker: these pack paths could consume adapters, but successful
execution did not share the same raw/normalized/attempt evidence helper.

This helper-integration blocker is now closed at scoped code-test level:

```bash
uv run pytest tests/unit/data_gateway/test_domain_pack_service.py \
  tests/unit/data_gateway/test_provider_execution.py \
  tests/integration/data_gateway/test_news_pack_cn_a.py \
  tests/integration/data_gateway/test_social_pack_cn_a.py \
  tests/integration/data_gateway/test_fundamental_pack_us.py
```

Exit code and key output:

```text
0
17 passed in 0.31s
```

Remaining limit: real Fundamental/News/Social adapters and live/fresh provider
evidence are still pending. Current contract adapters are not live proof.

### 4. OpenBB Python runtime was not executable from the current repo `uv` env before manual install

Initial command:

```bash
uv run python - <<'PY'
import importlib.util
for m in ["openbb", "fastmcp"]:
    print(m, bool(importlib.util.find_spec(m)))
PY
```

Observed:

```text
openbb: False
fastmcp: False
```

Additional submodule path probe:

```text
openbb_core/openbb/openbb_mcp_server packages are discoverable if
third_party/openbb/openbb_platform paths are manually added to sys.path, but
runtime imports fail:

import openbb -> ModuleNotFoundError: uuid_extensions
from openbb_core.app.router import Router -> ModuleNotFoundError: uuid_extensions
from openbb_mcp_server.app.app import create_mcp_server -> ModuleNotFoundError: fastmcp
```

Impact: `third_party/openbb` is pinned as a submodule, but the current runtime
environment had not installed the OpenBB Platform / MCP server dependency set
needed for live adapter execution.

Manual investigation install, not a versioned repo change:

```bash
uv pip install -e third_party/openbb/openbb_platform/core \
  -e third_party/openbb/openbb_platform/extensions/mcp_server

uv pip install -e third_party/openbb/openbb_platform/extensions/equity \
  -e third_party/openbb/openbb_platform/extensions/crypto \
  -e third_party/openbb/openbb_platform/extensions/news \
  -e third_party/openbb/openbb_platform/extensions/economy \
  -e third_party/openbb/openbb_platform/providers/yfinance \
  -e third_party/openbb/openbb_platform/providers/fmp \
  -e third_party/openbb/openbb_platform/providers/sec \
  -e third_party/openbb/openbb_platform/providers/fred \
  -e third_party/openbb/openbb_platform/providers/deribit
```

After this local venv install:

```text
import openbb OK
from openbb_core.app.router import Router OK
from openbb_mcp_server.app.app import create_mcp_server OK
from openbb import obb -> has equity/crypto/news/economy
```

This proves the pinned submodule can be used by the local runtime after
dependencies are installed. It is not yet T16 acceptance because the final
four-market `/report` run evidence chain is still missing.

Follow-up: `scripts/setup-openbb-dev.sh --install` now records the explicit
local editable install command for these pinned OpenBB core/MCP/extension/provider
packages. The normal setup path remains command-free; dependencies are installed
only when `--install` is passed.

### 5. Pack runtime adapter set

Initial blocker: the OpenClaw canonical bridge imported and instantiated only:

```text
build_default_market_adapters(...)
```

This wiring blocker is now closed at code-test level:

- `build_default_provider_adapters(...)` now aggregates market, fundamental,
  news, and social adapters.
- The canonical OpenClaw pack bridge uses that aggregate adapter set.
- Focused tool-boundary verification passed:

```bash
node --check openclaw_plugins/claw-trade-frontline-tools/index.js
uv run pytest tests/integration/data_gateway/test_mcp_visible_tools.py \
  tests/integration/data_gateway/test_old_provider_import_block.py \
  tests/unit/data_gateway/test_default_provider_catalog.py \
  tests/unit/data_gateway/test_tool_schema.py
```

Exit code and key output:

```text
0
22 passed in 9.55s
```

## Pack-Level Live Smoke

This smoke used the fixed runtime command mode:

```bash
CLAW_TRADE_OPENBB_TOOL_SCHEMA_ENABLED=true \
  scripts/start-control-runtime.sh -- uv run python - <<'PY'
...
PY
```

Exit code: 0

Scope: pack-level OpenBB wrapper smoke for:

- CN_A `600519.SH`
- HK `00700.HK`
- HK non-Tencent sample `00005.HK`
- US `AAPL`
- CRYPTO `BTC`

This is still not T16 completion because it does not include a full OpenClaw
LLM provider payload, worker tool-call transcript, OpenViking lineage/ovpack,
chart readiness chain, or final report evidence chain.

Key run ids:

```text
t16-pack-smoke-cn_a-600519_SH-20260517172315
t16-pack-smoke-hk-00700_HK-20260517172315
t16-pack-smoke-hk-00005_HK-20260517172315
t16-pack-smoke-us-AAPL-20260517172315
t16-pack-smoke-crypto-BTC-20260517172315
```

Observed pack-level status:

| Market | Market | Fundamental | News | Social |
| --- | --- | --- | --- | --- |
| CN_A 600519.SH | insufficient | insufficient | partial | partial |
| HK 00700.HK | partial | insufficient | ready | insufficient |
| HK 00005.HK | partial | insufficient | ready | insufficient |
| US AAPL | partial | partial | partial | partial |
| CRYPTO BTC | partial | partial | ready | partial |

Important positive evidence:

- OpenBB wrapper loaded the run plan from Mongo and wrote provider attempts.
- US market and CRYPTO market produced `remote_success` with raw/normalized refs.
- HK `stock_hk_daily` produced `remote_success` for both `00700.HK` and `00005.HK`.
- US SEC news bug was fixed and re-smoke produced `remote_success`.
- News packs produced Mongo raw/normalized refs for multiple markets.
- CN_A social aggregate via EastMoney/AkShare produced `remote_success`.
- No old `frontline_data_pack` or `provider_executor` path was used by the canonical OpenBB bridge.

Key blockers found by live smoke:

- CN_A market: Tushare token in env is invalid (`您的token不对，请确认。`) and AkShare/EastMoney calls hit remote disconnects.
- CN_A fundamental: AkShare public fundamental returned empty; Tushare token invalid.
- HK fundamental: public financial data is present but required PE/PB/ROE fields are missing; HK official RSS returned empty for current symbol window.
- HK social: `eastmoney_hk_guba` and `xueqiu_hk` are explicit `remote_error` because no approved live fetch contract exists.
- US fundamental: OpenBB yfinance fundamental rows did not map to PE/PB/ROE; SEC official refs succeeded.
- CRYPTO fundamental: CoinGecko core fields succeeded, but DefiLlama BTC fields are missing.
- CRYPTO social: Alternative.me succeeded; X/LunarCrush credentials are missing; Polymarket returned empty.

Follow-up re-smoke after two deterministic fixes:

```text
t16-pack-resmoke-hk-00700_HK-20260517172643
t16-pack-resmoke-us-AAPL-20260517172643
```

Results:

- US news became `ready`: SEC, Google News discovery, and macro all wrote
  `remote_success` attempts with raw/normalized refs.
- HK fundamental still `insufficient`: HK official path no longer crashes, but
  returned empty; HK financial still lacks required fields.

Follow-up re-smoke after HK/US yfinance field mapping fix:

```text
t16-fundamental-resmoke-hk-00700_HK-20260517161129
t16-fundamental-resmoke-hk-00005_HK-20260517161129
t16-fundamental-resmoke-us-AAPL-20260517161129
```

Command mode:

```bash
CLAW_TRADE_OPENBB_TOOL_SCHEMA_ENABLED=true \
  scripts/start-control-runtime.sh -- uv run python - <<'PY'
...
PY
```

Exit code: 0

Results:

| Sample | Fundamental readiness | Blocking gaps | PE | PB | ROE | Evidence |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| HK `00700.HK` | partial | 0 | 16.393679 | 3.1787221 | 21.134746439818 | attempts=5, http evidence written |
| HK `00005.HK` | partial | 0 | 14.704641 | 1.749413 | 11.013627419767 | attempts=5, http evidence written |
| US `AAPL` | ready | 0 | 36.347458 | 41.353996 | 1.4147099 | attempts=2, http evidence written |

Mongo evidence count for this scoped re-smoke:

```text
attempt_count=12
http_evidence_count=12
```

Interpretation: the earlier HK/US PE/PB/ROE gap was an implementation mapping
gap. OpenBB/yfinance returned snake_case fields (`pe_ratio`, `price_to_book`,
`return_on_equity`), while the adapter only mapped the older raw Yahoo-style
keys. HK Tushare still returns `您的token不对，请确认。`; it remains a visible
failed attempt and is not hidden by fallback.

Follow-up after Tushare proxy initialization fix:

The project uses a Tushare proxy endpoint in runtime. The Tushare initializer is
now centralized in `src/claw_trade/data_gateway/providers/tushare_client.py`:

- create `pro` through `ts.pro_api(token)`;
- set `pro._DataApi__http_url` to the configured proxy URL;
- use `ts.pro_bar(api=pro, ...)` for CN_A market bars.

The token remains runtime configuration and is not written into repository code.

Standalone verification with the configured proxy returned live rows:

```text
index_basic_shape=(5, 8)
pro_bar_shape=(3, 11)
```

Fixed-runtime CN_A re-smoke:

```text
t16-cn-a-tushare-proxy-resmoke-600519_SH-20260517162929
```

Results:

| Domain | Readiness | Provider evidence |
| --- | --- | --- |
| market | insufficient | Tushare `daily` remote_success, 18 OHLCV rows, latest_close=1332.95 |
| fundamental | partial | Tushare fundamental remote_success, PE=20.1803, PB=6.1619, ROE=10.5687 |

Mongo evidence count for this scoped re-smoke:

```text
attempt_count=5
http_evidence_count=5
```

Interpretation: the earlier `您的token不对，请确认。` result was caused by not
using the proxy URL in the migration path. CN_A market is still not ready in
this scoped run because the selected date range produced only 18 trading rows,
below the chart readiness minimum of 20 rows. This is a chart/window issue, not
a Tushare provider failure.

Wide regression after adapter and bridge fixes:

```bash
uv run pytest tests/unit/data_gateway tests/integration/data_gateway
```

Exit code and key output:

```text
0
141 passed, 3 skipped, 3 warnings in 21.44s
```

Follow-up after CN_A non-blocking Tushare policy and real chart rendering:

Code changes:

- CN_A market Tushare and AkShare now share coverage group `cn_a_ohlcv`,
  quorum `1`; Tushare failure is an explicit gap, not a blocking condition when
  another approved same-group provider covers OHLCV.
- Market pack now renders chart PNG assets from real OHLCV and computed
  indicator rows when `object_store_uri` is a local `file://` store.
- The OpenClaw pack bridge default `DATA_GATEWAY_OBJECT_STORE_URI` now resolves
  to this repo's `.runtime/dev-services/openbb-evidence` directory instead of an
  accidental root-level `/.runtime` URI.

Focused and scoped regression:

```bash
uv run pytest \
  tests/unit/data_gateway/test_domain_pack_service.py::test_domain_pack_service_market_success_generates_chart_images \
  tests/integration/data_gateway/test_market_pack_cn_a.py::test_market_pack_cn_a_tushare_failure_does_not_block_when_akshare_covers_group \
  tests/integration/data_gateway/test_market_pack_hk.py::test_market_pack_hk_enforces_stock_hk_daily_contract_and_root_cause
```

Exit code and key output:

```text
0
3 passed in 1.51s
```

Wide data gateway regression:

```bash
uv run pytest tests/unit/data_gateway tests/integration/data_gateway
```

Exit code and key output:

```text
0
144 passed, 3 skipped, 3 warnings in 21.50s
```

Fixed-runtime pack live smoke:

```text
Preflight before command mode:
runtime.env exists: true
1933 health/healthz: false
18789 health: false
```

Because the existing runtime health checks failed, the smoke was run through the
required command mode:

```bash
CLAW_TRADE_OPENBB_TOOL_SCHEMA_ENABLED=true \
DATA_GATEWAY_MONGODB_URI=mongodb://127.0.0.1:27017/claw_trade_openbb \
  scripts/start-control-runtime.sh -- uv run python - <<'PY'
...
PY
```

Runtime preflight table inside command mode:

```text
CLAW_TRADE_OPENVIKING_MCP_MODULE=<unset>
CLAW_TRADE_OPENVIKING_MCP_CWD=<unset>
CLAW_TRADE_OPENVIKING_SERVER_BIN=<unset>
CLAW_TRADE_OPENVIKING_SERVER_CWD=<unset>
OPENVIKING_CONFIG_FILE=/home/frank/src/claw-trade/.runtime/dev-services/openviking/ov.conf
OPENVIKING_DATA_DIR=/home/frank/src/claw-trade/.runtime/dev-services/openviking/data
CLAW_TRADE_OPENVIKING_MCP_STARTED=0
OPENVIKING_ENDPOINT=http://127.0.0.1:1933
OPENCLAW_GATEWAY_URL=ws://127.0.0.1:18789
OPENVIKING_HEALTH=True
OPENCLAW_HEALTH=True
```

Exit code and key output:

```text
0
```

Run ids:

```text
t16-pack-smoke-chart-cn_a-600519_SH-20260517210237
t16-pack-smoke-chart-hk-00700_HK-20260517210237
t16-pack-smoke-chart-hk-00005_HK-20260517210237
t16-pack-smoke-chart-us-AAPL-20260517210237
t16-pack-smoke-chart-crypto-BTC-20260517210237
```

Market chart readiness:

| Sample | Market readiness | OHLCV rows | Chart files | Blocking gaps | Provider evidence |
| --- | --- | ---: | ---: | ---: | --- |
| CN_A `600519.SH` | partial | 65 | 2 | 0 | Tushare `daily` remote_success; AkShare/EastMoney remote_error as non-blocking gaps |
| HK `00700.HK` | partial | 68 | 2 | 0 | Tushare `hk_daily` remote_success; AkShare `stock_hk_daily` remote_success; EastMoney quote remote_error |
| HK `00005.HK` | partial | 68 | 2 | 0 | Tushare `hk_daily` remote_success; AkShare `stock_hk_daily` remote_success; EastMoney quote remote_error |
| US `AAPL` | ready | 73 | 2 | 0 | OpenBB/yfinance historical price remote_success |
| CRYPTO `BTC` | ready | 106 | 2 | 0 | OpenBB/yfinance crypto historical price remote_success |

Mongo evidence count for this pack-level smoke:

```text
openbb_run_provider_plans=5
openbb_provider_attempts=57
openbb_provider_http_evidence=57
openbb_raw_payloads=40
openbb_normalized=40
```

Remaining pack-level gaps from this smoke:

- CN_A: market no longer has blocking gaps, but readiness is still `partial`
  because AkShare/EastMoney failed as non-blocking candidates; news has one
  blocking empty CNInfo official source; social has Google News empty and Xueqiu
  remote error.
- HK: market chart readiness is fixed; HK social remains `insufficient` because
  EastMoney HK guba and Xueqiu HK live fetch paths return remote errors.
- US: market/fundamental/news are ready; social remains `partial` because
  Stocktwits returns remote error.
- CRYPTO: market/news are ready; fundamental remains `partial` because
  DefiLlama BTC fields are missing; social remains `partial` due Polymarket
  empty plus missing LunarCrush/X credentials.

Interpretation: chart image assets are now real files generated from successful
OpenBB-provider-normalized OHLCV/indicator rows. This closes the previous common
chart-readiness implementation gap at pack level, but it does not close full
T16 because the full `/report` OpenClaw payload/tool-call/final-report evidence
chain has not yet been collected.

## US/AAPL Full `/report` Exporter Proof

After the pack-level chart renderer fix, the first full US/AAPL `/report` run
still failed at final export:

```text
run_id=run-20260517-211419-f18657ec
status=failed
reason=export_report_assets: 报告导出失败：未找到可复制的图表资产
```

Root cause: OpenBB pack bridge generated real PNG files under:

```text
runs/<run_id>/calls/<market_call>/pack-tool-evidence/techlab/charts-local/runs/<run_id>/<call_id>/charts/*.png
```

but the exporter only scanned the first level under `charts-local`.

Focused fix:

- `src/claw_trade/reports/exporter.py`: recurse under approved
  `charts-local` roots and keep only files.
- `tests/contracts/test_final_report_exporter.py`: change the pack-tool chart
  discovery contract to the same nested path shape produced by the real OpenBB
  bridge.

Focused verification:

```bash
uv run pytest tests/contracts/test_final_report_exporter.py::test_export_final_report_discovers_frontline_pack_tool_chart_assets
uv run pytest tests/contracts/test_final_report_exporter.py
```

Exit code and key output:

```text
0
1 passed in 0.24s

0
16 passed in 0.27s
```

Fixed-runtime full `/report` rerun:

```text
Preflight before command mode:
runtime.env exists: true
1933 health/healthz: false
18789 health: false
```

Command mode runtime profile:

```text
fixed_runtime_profile=start-control-runtime command mode
repo_uv_environment=uv run
openviking_expected_port=1933
openclaw_gateway_expected_port=18789
invest_sidecar=disabled
runtime_data_cache=.runtime/dev-services
CLAW_TRADE_OPENVIKING_MCP_MODULE=<unset>
CLAW_TRADE_OPENVIKING_MCP_CWD=<unset>
CLAW_TRADE_OPENVIKING_SERVER_BIN=<unset>
CLAW_TRADE_OPENVIKING_SERVER_CWD=<unset>
OPENVIKING_CONFIG_FILE=/home/frank/src/claw-trade/.runtime/dev-services/openviking/ov.conf
OPENVIKING_DATA_DIR=/home/frank/src/claw-trade/.runtime/dev-services/openviking/data
CLAW_TRADE_OPENVIKING_MCP_STARTED=0
OPENVIKING_ENDPOINT=http://127.0.0.1:1933
OPENCLAW_GATEWAY_URL=ws://127.0.0.1:18789
health http://127.0.0.1:1933/health 200
health http://127.0.0.1:1933/healthz 404
health http://127.0.0.1:18789/health 200
```

Full run result:

```text
COMPLETED run_id=run-20260517-212354-12b257b8
output_dir=/home/frank/src/claw-trade/docs/evidence/trading_claw_trade_us_fresh_live_run-20260517-212354-12b257b8_md
workers=13
final_report=final_report.md
exit_code=0
```

Run-state and export evidence:

```text
runs/run-20260517-212354-12b257b8/state.json
status=completed
failure_reason=null

runs/run-20260517-212354-12b257b8/reports/export-result.json
status=passed
failure=null
unsupported_claims=[]
```

Final report chart assets:

```text
runs/run-20260517-212354-12b257b8/reports/assets/market-01-indicator_panels-49f5ad9692da677e.png
runs/run-20260517-212354-12b257b8/reports/assets/market-02-market_structure-6d5dc754a7f9b077.png
```

The final report uses relative asset references:

```text
![market-chart-1](assets/market-01-indicator_panels-49f5ad9692da677e.png)
![market-chart-2](assets/market-02-market_structure-6d5dc754a7f9b077.png)
```

OpenClaw payload/tool boundary evidence:

```text
provider-request.json files: 13
visible-tools.json files: 13
tool-calls.json files: 13
```

Visible tool schema in this run:

```text
market_analyst:claw_get_market_pack
fundamental_analyst:claw_get_fundamental_pack
news_analyst:claw_get_news_pack
social_analyst:claw_get_social_pack
bull_researcher:<none>
bear_researcher:<none>
research_manager:<none>
trader:<none>
risk_challenger:<none>
risk_guardian:<none>
risk_moderator:<none>
portfolio_manager:<none>
report_polisher:<none>
```

Mongo evidence counts for `run-20260517-212354-12b257b8`:

```text
openbb_run_provider_plans=1
openbb_provider_attempts=9
openbb_provider_http_evidence=9
openbb_raw_payloads=1
openbb_normalized=3
openbb_cache_entries=0
openbb_singleflight_locks=0
openbb_validation_receipts=0
```

Provider attempts in this US/AAPL full run:

```text
market/openbb_yfinance/equity_price_historical: remote_success rows=73
fundamental/openbb_yfinance/equity_fundamentals_yfinance: remote_success rows=1
fundamental/openbb_sec/company_facts+filings: remote_success rows=1
news/fred/macro_news: remote_success rows=4
news/google_news/search_discovery: remote_success rows=20
news/sec/filings: remote_success rows=20
social/google_news/search_discovery: remote_success rows=20
social/reddit/social_metrics: remote_success rows=20
social/stocktwits/posts: remote_error error_code=HTTPError
```

OpenViking approved material evidence:

```text
runs/run-20260517-212354-12b257b8/openviking/approved-manifest.json
materials=13
workers=social_analyst, news_analyst, market_analyst, fundamental_analyst,
        bull_researcher, bear_researcher, research_manager, trader,
        risk_challenger, risk_guardian, risk_moderator, portfolio_manager,
        report_polisher
```

Important deviation from final T16 evidence requirements:

- `openbb_provider_http_evidence` currently records source URL, latency,
  provider request id, error code, and raw refs, but in this run
  `response_status_code` is `None` and `response_headers_summary` is `{}` for
  the recorded attempts. This is not sufficient for final HTTP evidence
  acceptance.
- `openbb_raw_payloads=1` while attempts/http evidence are 9; raw evidence
  completeness still needs review before final acceptance.
- This is one full US/AAPL run only. It does not satisfy four-market full
  live/fresh coverage.
- OpenViking tree/grep/glob/relations/ovpack/runtime-health live evidence was
  not collected in this full run output.

## HTTP Evidence Capture Follow-Up

Root cause review:

- `openbb_raw_payloads=1` for the full US/AAPL run was a query issue, not raw
  evidence loss. Raw payloads are content-addressed by payload hash. Existing
  raw docs keep their original `run_id` and update `last_seen_run_id`, so
  per-run completeness must be checked from `attempt.raw_ref` or
  `http_evidence.raw_ref` and then dereferenced into `openbb_raw_payloads`.
- Missing `response_status_code` / `response_headers_summary` was an
  implementation gap. Real adapters did not expose transport response metadata
  unless the adapter manually filled `ProviderFetch`.

Implementation follow-up:

- Added transport capture for `requests`, `curl_cffi.requests`, `urllib`, and
  `aiohttp`/`aiohttp_client_cache`.
- HTTP headers are allowlisted before persistence. Sensitive headers such as
  cookies or authorization are not written.
- Multiple HTTP exchanges inside one provider attempt now produce multiple
  `openbb_provider_http_evidence` rows with deterministic suffixes.
- No default `200` or synthetic headers are written. If a transport cannot be
  captured, the evidence remains explicitly incomplete.

Focused verification:

```bash
uv run pytest tests/unit/data_gateway/test_provider_execution.py
uv run pytest tests/unit/data_gateway tests/integration/data_gateway
```

Exit code and key output:

```text
0
5 passed in 0.99s

0
146 passed, 3 skipped, 3 warnings in 20.71s
```

Fixed-runtime US/AAPL pack smoke after HTTP capture:

```text
Preflight before command mode:
runtime.env exists: true
1933 health/healthz: false
18789 health: false
```

Command mode runtime profile:

```text
CLAW_TRADE_OPENVIKING_MCP_MODULE=<unset>
CLAW_TRADE_OPENVIKING_MCP_CWD=<unset>
CLAW_TRADE_OPENVIKING_SERVER_BIN=<unset>
CLAW_TRADE_OPENVIKING_SERVER_CWD=<unset>
OPENVIKING_CONFIG_FILE=/home/frank/src/claw-trade/.runtime/dev-services/openviking/ov.conf
OPENVIKING_DATA_DIR=/home/frank/src/claw-trade/.runtime/dev-services/openviking/data
CLAW_TRADE_OPENVIKING_MCP_STARTED=0
OPENVIKING_ENDPOINT=http://127.0.0.1:1933
OPENCLAW_GATEWAY_URL=ws://127.0.0.1:18789
health http://127.0.0.1:1933/health 200
health http://127.0.0.1:1933/healthz 404
health http://127.0.0.1:18789/health 200
```

US/AAPL all-domain pack smoke:

```text
run_id=t16-http-evidence-us-AAPL-20260517214429
market ready openbb_yfinance:equity_price_historical:remote_success:rows=73
fundamental ready openbb_sec:company_facts+filings:remote_success:rows=1,openbb_yfinance:equity_fundamentals_yfinance:remote_success:rows=1
news ready sec:filings:remote_success:rows=20,fred:macro_news:remote_success:rows=4,google_news:search_discovery:remote_success:rows=20
social partial google_news:search_discovery:remote_success:rows=20,reddit:social_metrics:remote_success:rows=20,stocktwits:posts:remote_error:rows=0
openbb_provider_attempts=9
openbb_provider_http_evidence=13
attempt_raw_refs=8
missing_raw_docs=0
http_missing_success_status=1
http_missing_success_headers=1
```

The remaining missing success HTTP metadata in that all-domain smoke was
`openbb_sec/company_facts+filings`, which uses OpenBB's SEC provider over
`aiohttp_client_cache`.

Focused SEC/yfinance FundamentalPack re-smoke after adding aiohttp capture:

```text
run_id=t16-http-evidence-us-fundamental-AAPL-20260517214745
fundamental ready openbb_sec:company_facts+filings:remote_success:rows=1,openbb_yfinance:equity_fundamentals_yfinance:remote_success:rows=1
http_count=6
http_missing_success_status=0
http_missing_success_headers=0
```

Representative captured HTTP evidence from the focused re-smoke:

```text
openbb_sec company_facts+filings https://data.sec.gov/submissions/CIK0000320193.json status=200 headers include content-type/cache-control/date
openbb_sec company_facts+filings https://www.sec.gov/files/company_tickers.json status=200 headers include content-type/content-length/x-request-id
openbb_yfinance equity_fundamentals_yfinance query1/query2.finance.yahoo.com status=200 headers include content-type/cache-control/date
```

Interpretation: HTTP status/header capture is now proven at pack level for
direct HTTP providers, OpenBB/yfinance (`curl_cffi.requests`), and OpenBB/SEC
(`aiohttp_client_cache`). Final T16 still requires a fresh full `/report` chain
and four-market collect-first evidence with these fixes in place.

## US/AAPL Full `/report` With HTTP Audit And OpenViking Runtime Evidence

Date: 2026-05-17

Important root-cause note: collecting OpenViking tree/glob/ovpack for an old
completed run after restarting `scripts/start-control-runtime.sh` is invalid,
because command-mode startup clears `.runtime/dev-services`. The valid evidence
must be collected in the same runtime process immediately after the `/report`
run writes OpenViking material. The run below does that.

Command mode preflight:

```text
runtime.env exists: true
1933 health/healthz before restart: false
18789 health before restart: false
CLAW_TRADE_OPENVIKING_MCP_MODULE=unset
CLAW_TRADE_OPENVIKING_MCP_CWD=unset
CLAW_TRADE_OPENVIKING_SERVER_BIN=unset
CLAW_TRADE_OPENVIKING_SERVER_CWD=unset
OPENVIKING_CONFIG_FILE=/home/frank/src/claw-trade/.runtime/dev-services/openviking/ov.conf
OPENVIKING_DATA_DIR=/home/frank/src/claw-trade/.runtime/dev-services/openviking/data
CLAW_TRADE_OPENVIKING_MCP_STARTED=0
OPENVIKING_ENDPOINT=http://127.0.0.1:1933
OPENCLAW_GATEWAY_URL=ws://127.0.0.1:18789
1933/health=200
1933/healthz=404
18789/health=200
```

Run:

```text
run_id=run-20260517-220630-c9edcab2
market=US
ticker=AAPL
status=completed
export_status=passed
provider-request.json count=13
visible-tools.json count=13
tool-calls.json count=13
```

Visible tool schema summary:

```text
market_analyst:claw_get_market_pack
fundamental_analyst:claw_get_fundamental_pack
news_analyst:claw_get_news_pack
social_analyst:claw_get_social_pack
downstream workers/report_polisher:<none>
```

Chart readiness/export evidence:

```text
runs/run-20260517-220630-c9edcab2/reports/assets/market-01-indicator_panels-49f5ad9692da677e.png 58703 bytes
runs/run-20260517-220630-c9edcab2/reports/assets/market-02-market_structure-6d5dc754a7f9b077.png 101296 bytes
```

Mongo evidence-chain audit:

```text
docs/evidence/openbb-us-full-chain-http-openviking-20260517T220627Z/openbb-evidence-chain-run-20260517-220630-c9edcab2.json
passed=true
attempt_count=9
openbb_provider_http_evidence=15
raw_ref_count=8
missing_raw_refs=[]
success_http_missing_response_status=[]
success_http_missing_headers=[]
success_http_missing_source_url=[]
```

OpenViking runtime evidence collected before runtime shutdown:

```text
docs/evidence/openbb-us-full-chain-http-openviking-20260517T220627Z/openviking/openviking-runtime-evidence-run-20260517-220630-c9edcab2.json
tree.status=ok
tree.node_count=33
grep.status=ok
glob.status=ok
ovpack_export.portability_status=metadata_verified
ovpack_import.import_status=ok
context_index semantic.status=blocked
runtime_health.status=blocked
```

`runtime_health.status=blocked` is expected for this runtime profile because
OpenViking semantic/vector queue is disabled and recovery public API is not
confirmed. It is not treated as `ok`.

Implementation/testing added for this evidence path:

```text
uv run pytest tests/unit/data_gateway/test_evidence_chain.py tests/unit/data_gateway/test_provider_execution.py
7 passed

uv run pytest tests/unit/test_openviking_backend_http.py tests/unit/data_gateway/test_openviking_context_index.py tests/unit/data_gateway/test_openviking_runtime_health.py tests/integration/data_gateway/test_openviking_evidence_bundle.py
22 passed

uv run pytest tests/unit/data_gateway tests/integration/data_gateway tests/unit/test_openviking_backend_http.py tests/contracts/test_final_report_exporter.py
179 passed, 3 skipped, 3 warnings

DATA_GATEWAY_MONGODB_URI=mongodb://127.0.0.1:27017/claw_trade_openbb uv run pytest tests/integration/data_gateway/test_mongo_attempts.py tests/integration/data_gateway/test_run_provider_plan_snapshot.py
3 passed, 5 warnings
```

This closes the US/AAPL full-chain sample with the new HTTP/raw audit and
OpenViking runtime evidence. It is not four-market T16 completion.

## CN_A 600519 Full `/report` With HTTP Audit And OpenViking Runtime Evidence

Date: 2026-05-17

Command mode preflight:

```text
runtime.env exists: true
1933 health/healthz before restart: false
18789 health before restart: false
CLAW_TRADE_OPENVIKING_MCP_MODULE=unset
CLAW_TRADE_OPENVIKING_MCP_CWD=unset
CLAW_TRADE_OPENVIKING_SERVER_BIN=unset
CLAW_TRADE_OPENVIKING_SERVER_CWD=unset
OPENVIKING_CONFIG_FILE=/home/frank/src/claw-trade/.runtime/dev-services/openviking/ov.conf
OPENVIKING_DATA_DIR=/home/frank/src/claw-trade/.runtime/dev-services/openviking/data
CLAW_TRADE_OPENVIKING_MCP_STARTED=0
OPENVIKING_ENDPOINT=http://127.0.0.1:1933
OPENCLAW_GATEWAY_URL=ws://127.0.0.1:18789
1933/health=200
1933/healthz=404
18789/health=200
```

Run:

```text
run_id=run-20260517-221647-c47fc16a
market=CN_A
ticker=600519.SH
company=贵州茅台
status=completed
export_status=passed
provider-request.json count=13
visible-tools.json count=13
tool-calls.json count=13
```

Visible tool schema summary:

```text
market_analyst:claw_get_market_pack
fundamental_analyst:claw_get_fundamental_pack
news_analyst:claw_get_news_pack
social_analyst:claw_get_social_pack
downstream workers/report_polisher:<none>
```

Chart readiness/export evidence:

```text
runs/run-20260517-221647-c47fc16a/reports/assets/market-01-indicator_panels-eba878154daf299f.png 53037 bytes
runs/run-20260517-221647-c47fc16a/reports/assets/market-02-market_structure-721f805f19806733.png 101446 bytes
```

Mongo evidence-chain audit:

```text
docs/evidence/openbb-cn-a-full-chain-http-openviking-20260517T221640Z/openbb-evidence-chain-run-20260517-221647-c47fc16a.json
passed=true
attempt_count=11
openbb_provider_http_evidence=16
raw_ref_count=7
missing_raw_refs=[]
success_http_missing_response_status=[]
success_http_missing_headers=[]
success_http_missing_source_url=[]
```

OpenViking runtime evidence:

```text
docs/evidence/openbb-cn-a-full-chain-http-openviking-20260517T221640Z/openviking/openviking-runtime-evidence-run-20260517-221647-c47fc16a.json
tree.status=ok
tree.node_count=33
grep.status=ok
glob.status=ok
ovpack_export.portability_status=metadata_verified
ovpack_import.import_status=ok
context_index semantic.status=blocked
runtime_health.status=blocked
```

This closes the CN_A 600519 full-chain sample with the new HTTP/raw audit and
OpenViking runtime evidence. It is not four-market T16 completion.

## HK 00700.HK Full `/report` With HTTP Audit And OpenViking Runtime Evidence

Date: 2026-05-17

Run:

```text
run_id=run-20260517-222532-5846c641
market=HK
ticker=00700.HK
company=Tencent Holdings
status=completed
export_status=passed
provider-request.json count=13
visible-tools.json count=13
tool-calls.json count=13
```

Visible tool schema summary:

```text
market_analyst:claw_get_market_pack
fundamental_analyst:claw_get_fundamental_pack
news_analyst:claw_get_news_pack
social_analyst:claw_get_social_pack
downstream workers/report_polisher:<none>
```

Chart readiness/export evidence:

```text
docs/evidence/trading_claw_trade_hk_fresh_live_run-20260517-222532-5846c641_md/assets/market-01-indicator_panels-5dae3808ddabd36d.png
docs/evidence/trading_claw_trade_hk_fresh_live_run-20260517-222532-5846c641_md/assets/market-02-market_structure-ef42dc0534581e03.png
```

Mongo evidence-chain audit:

```text
docs/evidence/openbb-hk-00700-full-chain-http-openviking-20260517T222526Z/openbb-evidence-chain-run-20260517-222532-5846c641.json
passed=true
attempt_count=14
openbb_provider_http_evidence=21
raw_ref_count=10
missing_raw_refs=[]
success_http_missing_response_status=[]
success_http_missing_headers=[]
success_http_missing_source_url=[]
```

OpenViking runtime evidence:

```text
docs/evidence/openbb-hk-00700-full-chain-http-openviking-20260517T222526Z/openviking/openviking-runtime-evidence-run-20260517-222532-5846c641.json
tree.status=ok
tree.node_count=33
grep.status=ok
glob.status=ok
ovpack_export.portability_status=metadata_verified
ovpack_import.import_status=ok
context_index semantic.status=blocked
runtime_health.status=blocked
```

This closes the required HK Tencent sample evidence collection.

## HK 00005.HK Non-Tencent Full `/report` With HTTP Audit And OpenViking Runtime Evidence

Date: 2026-05-17

Rejected attempt note:

```text
run_id=run-20260517-223353-2ac45886
report_exit=0
audit_exit=0
openviking_collect_exit=2
root_cause=collector CLI was invoked with --out-dir instead of --output-dir
acceptance_status=rejected for T16 because OpenViking runtime evidence was not collected before shutdown
```

Accepted rerun:

```text
run_id=run-20260517-224213-a14ac5fe
market=HK
ticker=00005.HK
company=HSBC Holdings
status=completed
export_status=passed
provider-request.json count=13
visible-tools.json count=13
tool-calls.json count=13
```

Visible tool schema summary:

```text
market_analyst:claw_get_market_pack
fundamental_analyst:claw_get_fundamental_pack
news_analyst:claw_get_news_pack
social_analyst:claw_get_social_pack
downstream workers/report_polisher:<none>
```

Chart readiness/export evidence:

```text
docs/evidence/trading_claw_trade_hk_fresh_live_run-20260517-224213-a14ac5fe_md/assets/market-01-indicator_panels-ac43d082594b2ecb.png 67541 bytes
docs/evidence/trading_claw_trade_hk_fresh_live_run-20260517-224213-a14ac5fe_md/assets/market-02-market_structure-db62e5f383a285fb.png 106591 bytes
```

Mongo evidence-chain audit:

```text
docs/evidence/openbb-hk-00005-full-chain-http-openviking-rerun-20260517T224207Z/openbb-evidence-chain-run-20260517-224213-a14ac5fe.json
passed=true
attempt_count=14
openbb_provider_http_evidence=21
raw_ref_count=10
missing_raw_refs=[]
success_http_missing_response_status=[]
success_http_missing_headers=[]
success_http_missing_source_url=[]
```

OpenViking runtime evidence:

```text
docs/evidence/openbb-hk-00005-full-chain-http-openviking-rerun-20260517T224207Z/openviking/openviking-runtime-evidence-run-20260517-224213-a14ac5fe.json
tree.status=ok
tree.node_count=33
grep.status=ok
glob.status=ok
ovpack_export.portability_status=metadata_verified
ovpack_import.import_status=ok
context_index semantic.status=blocked
runtime_health.status=blocked
```

This closes the required HK non-Tencent sample evidence collection.

## CRYPTO BTC Full `/report` With HTTP Audit And OpenViking Runtime Evidence

Date: 2026-05-17

Run:

```text
run_id=run-20260517-225048-30bcbc33
market=CRYPTO
ticker=BTC
company=Bitcoin
status=completed
export_status=passed
provider-request.json count=13
visible-tools.json count=13
tool-calls.json count=13
```

Visible tool schema summary:

```text
market_analyst:claw_get_market_pack
fundamental_analyst:claw_get_fundamental_pack
news_analyst:claw_get_news_pack
social_analyst:claw_get_social_pack
downstream workers/report_polisher:<none>
```

Chart readiness/export evidence:

```text
docs/evidence/trading_claw_trade_crypto_fresh_live_run-20260517-225048-30bcbc33_md/assets/market-01-indicator_panels-5ac9dce2ae5f84b0.png 55109 bytes
docs/evidence/trading_claw_trade_crypto_fresh_live_run-20260517-225048-30bcbc33_md/assets/market-02-market_structure-d57cec21cbbc66cd.png 122045 bytes
```

Mongo evidence-chain audit:

```text
docs/evidence/openbb-crypto-btc-full-chain-http-openviking-20260517T225042Z/openbb-evidence-chain-run-20260517-225048-30bcbc33.json
passed=true
attempt_count=9
openbb_provider_http_evidence=10
raw_ref_count=9
missing_raw_refs=[]
success_http_missing_response_status=[]
success_http_missing_headers=[]
success_http_missing_source_url=[]
```

OpenViking runtime evidence:

```text
docs/evidence/openbb-crypto-btc-full-chain-http-openviking-20260517T225042Z/openviking/openviking-runtime-evidence-run-20260517-225048-30bcbc33.json
tree.status=ok
tree.node_count=33
grep.status=ok
glob.status=ok
ovpack_export.portability_status=metadata_verified
ovpack_import.import_status=ok
context_index semantic.status=blocked
runtime_health.status=blocked
```

Content residual risk:

```text
final_report path=docs/evidence/trading_claw_trade_crypto_fresh_live_run-20260517-225048-30bcbc33_md/final_report.md
observed=the final report mostly preserves data-gap caveats for derivatives, on-chain, macro, AHR999, and news/event coverage.
risk=the report also includes several public-knowledge or search-discovery-style ETF/institution/mining narrative statements that need claim-source review before final T17 closure.
status=does not block evidence collection, but blocks claiming the whole migration complete.
```

## CRYPTO BTC Post-Tightening Runtime Proof

Date: 2026-05-17

Purpose: prove the CRYPTO claim-source prompt/pack tightening changed real
runtime behavior after the first accepted BTC evidence run.

Run:

```text
run_id=run-20260517-234339-7c0e760e
market=CRYPTO
ticker=BTC
status=completed
request_data_gateway=openbb
export_status=passed
provider-request.json count=13
visible-tools.json count=13
tool-calls.json count=13
```

Mongo/OpenViking evidence:

```text
docs/evidence/openbb-crypto-btc-post-tightening-full-chain-http-openviking-20260517T234333Z/openbb-evidence-chain-run-20260517-234339-7c0e760e.json
passed=true
attempt_count=9
openbb_provider_http_evidence=10
missing_raw_refs=[]
success_http_missing_response_status=[]
success_http_missing_headers=[]

docs/evidence/openbb-crypto-btc-post-tightening-full-chain-http-openviking-20260517T234333Z/openviking/openviking-runtime-evidence-run-20260517-234339-7c0e760e.json
tree.status=ok
grep.status=ok
glob.status=ok
ovpack_export.portability_status=metadata_verified
ovpack_import.import_status=ok
runtime_health.status=blocked
```

Claim-source recheck:

```text
docs/evidence/openbb-crypto-btc-post-tightening-full-chain-http-openviking-20260517T234333Z/btc-claim-source-grep.txt
final report labels ETF/institution, news, social consensus, derivatives,
on-chain, macro, and AHR999 gaps as missing, zero-row, search-discovery, or not
verified where applicable.
```

## Collect-First Summary

Batch scope:

```text
CN_A 600519.SH
HK 00700.HK
HK 00005.HK
US AAPL
CRYPTO BTC
```

Completed items:

```text
CN_A 600519.SH: run-20260517-221647-c47fc16a
HK 00700.HK: run-20260517-222532-5846c641
HK 00005.HK: run-20260517-224213-a14ac5fe
US AAPL: run-20260517-220630-c9edcab2
CRYPTO BTC: run-20260517-225048-30bcbc33
```

Failures collected:

```text
HK 00005.HK first attempt run-20260517-223353-2ac45886 had openviking_collect_exit=2 because the collector argument was wrong.
The run was rejected for T16 evidence and rerun with --output-dir.
```

Early-stop exception used: no.

Batch fix grouping:

```text
OpenViking collection command was corrected from --out-dir to --output-dir.
Future evidence commands now fail non-zero if report, Mongo audit, or OpenViking collection fails.
```

## Deviation Status

T16 evidence collection is complete for the required market samples listed
above, but the overall OpenBB migration is not complete and T17 final closure is
not claimed.

No mock, stub, fake, fallback, or capture-only evidence was used to claim
provider success.

Known remaining risks:

```text
1. CRYPTO claim-source review is complete, prompt/pack wording has been tightened, and post-tightening BTC runtime proof has been collected in `run-20260517-234339-7c0e760e`; residual risk remains only around strong public-context/debate appendix wording, not evidence-chain failure.
2. OpenViking runtime_health remains blocked, not ok, because semantic/vector queue is disabled and public recovery API is not confirmed.
3. T14 default OpenBB boundary is implemented and the executable old-provider rollback branch has been removed; old provider physical deletion/archive is still pending.
4. T17 final evidence convergence, Git/VCS rollback docs, and adversarial conformance review are not done.
```

## Next Smallest Actions

1. Complete T14 pack-by-pack legacy provider physical deletion/archive using the accepted T16 evidence as the cutoff.
2. Complete T17 documentation, Git/VCS rollback evidence, and final conformance review.
