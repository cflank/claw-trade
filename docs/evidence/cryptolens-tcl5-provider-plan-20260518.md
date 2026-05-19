# T-CL-5 第二阶段：OpenBB CRYPTO provider plan/adapters 六域真实调用接入证据

状态：DONE（六域已接真实调用；人类已批准继续验收，live/fresh 仍待 T-CL-12）  
日期：2026-05-18  
任务：T-CL-5（provider plan/adapters 六域落地）

## 1) 本阶段边界与结论

- 在既有 provider plan/call spec 不变前提下，去掉六域 phase1 placeholder，改为真实 fetch + 映射归一化：
  - `derivatives` -> Coinglass（OI/Funding/LongShort/TakerBuySell/Liquidation history）
  - `liquidation_map` -> Coinglass heatmap
  - `onchain` -> Glassnode metrics
  - `macro` -> FRED series
  - `events` -> Tavily search
  - `ahr999` -> Coinglass AHR999（BTC only）
- 缺凭证时不发请求，仍走 `credential_missing`（由 `required_env_keys` + 调度层 credential gate 保证）。
- HTTP evidence 捕获 URL query 与 URL userinfo 已统一脱敏（敏感键如 `api_key/apikey/key/token/access_token/accessToken/x-api-key/api-key/secret/authorization/auth` 不落明文值）。
- CRYPTO provider helper 已统一读取 adapter 注入 env（不再直接依赖进程级 `os.environ`）。
- 未实现 CryptoLens engine/adapter/evidence。
- 未改 MarketPackBuilder 去消费 CryptoLens analysis engine 输出。
- 未执行旧 BB MCP 或旧 TS runtime。

## 2) 旧 BB 只读映射 -> 当前 call specs

来源对照：`docs/evidence/cryptolens-tcl2-old-bb-audit-20260518.md`。

| 目标域 | adapter_id | provider/endpoint | source_role | coverage_group/quorum | license_policy_id | raw_export_policy |
|---|---|---|---|---|---|---|
| market+OHLCV | `project.crypto.market` | `openbb_yfinance/crypto_price_historical` | `market_data` | `crypto_market_ohlcv/1` | `personal_research` | `metadata_only` |
| derivatives | `project.crypto.derivatives` | `coinglass/futures_oi_funding` | `derivative_market_data` | `crypto_derivatives/1` | `coinglass.pending_review` | `metadata_only` |
| liquidation_map | `project.crypto.liquidation_map` | `coinglass/liquidation_heatmap` | `derivative_market_data` | `crypto_liquidation_map/1` | `coinglass.pending_review` | `metadata_only` |
| onchain | `project.crypto.onchain` | `glassnode/onchain_signals` | `macro_data` | `crypto_onchain/1` | `glassnode.pending_review` | `metadata_only` |
| macro | `project.crypto.macro` | `fred/macro_regime` | `macro_data` | `crypto_macro/1` | `fred.pending_review` | `metadata_only` |
| events | `project.crypto.events` | `tavily/catalyst_events` | `event_expectation` | `crypto_events/1` | `tavily.pending_review` | `metadata_only` |
| ahr999 | `project.crypto.ahr999` | `coinglass/ahr999_index` | `market_data` | `crypto_ahr999/1` | `coinglass.pending_review` | `metadata_only` |

## 3) 缺口表达（当前合同）

- 保留 `openbb_yfinance` OHLCV 真实可用路径，不改现有成功语义。
- 六域已接真实 fetch，不再使用 phase1 placeholder。  
- 仍保留真实阻断/缺口表达：  
  - 缺 key：`credential_missing`（不发请求）。  
  - provider 响应错误/结构异常：`remote_error`。  
  - adapter 缺失：`skipped_not_configured`。  
  - `ahr999` 非 BTC：plan 阶段跳过（not applicable），不记为远端错误。
- 无 silent fallback，无 `remote_success` 伪造。

## 4) 许可/费用/raw export 初始审批状态

- 全部 call spec 默认 `raw_export_policy=metadata_only`（A 股口径）。
- `openbb_yfinance`：`personal_research`（当前沿用）。
- `coinglass/glassnode/fred/tavily`：2026-05-18 人类回复“可以，继续”，批准继续进入验收推进。
- 说明：该批准只解除 T-CL-5 的许可/费用推进阻塞；真实可用性仍必须由 T-CL-12 live/fresh 证据证明。

## 5) 代码改动清单

- `src/claw_trade/data_gateway/providers/market_adapters.py`
  - CRYPTO market 从单一 yfinance 扩展到 7 个 adapter/call spec。
  - 六域替换为真实调用函数，迁入旧 BB 接口 URL/参数/字段映射。
  - 新增 crypto 域归一化分支（非 OHLCV）并保留 existing OHLCV 归一化路径。
  - capability seed 支持 `license_policy_id/raw_export_policy`。
- `src/claw_trade/data_gateway/models.py`
  - `ProviderCapability/ProviderCallSpec` 新增 `raw_export_policy`（默认 `metadata_only`）。
- `src/claw_trade/data_gateway/providers/run_plan.py`
  - planner 生成 call spec 时传递 `raw_export_policy`。
- `src/claw_trade/data_gateway/providers/defaults.py`
  - provider config hash 纳入 `raw_export_policy`。
- `src/claw_trade/data_gateway/providers/registry.py`
  - declarative manifest -> capability 传递 `raw_export_policy`。
- `src/claw_trade/data_gateway/providers/execution.py`
  - raw export policy 解析优先 spec，provider_settings 可覆盖。
- `src/claw_trade/data_gateway/store/run_plans.py`
  - run plan call spec 序列化/反序列化纳入 `raw_export_policy`。

## 6) 测试与命令

执行命令：

```bash
uv run pytest tests/unit/data_gateway/test_provider_execution.py tests/unit/data_gateway/test_market_adapters_live_contract.py tests/integration/data_gateway/test_market_pack_crypto.py tests/unit/data_gateway/test_run_provider_plan.py
```

结果：`29 passed`（exit code 0）。

本轮新增/更新合同点：

- `tests/unit/data_gateway/test_market_adapters_live_contract.py`
  - CRYPTO plan 不再 yfinance-only。
  - 每个 spec 断言 `provider_config_version/source_role/coverage/license/raw_export_policy`。
  - 缺 key -> `credential_missing` 合同。
  - 新增六域 fetch+normalize 单元测试（monkeypatch 网络层，验证 URL 参数与字段归一化）。
- `tests/integration/data_gateway/test_market_pack_crypto.py`
  - BTC market plan 覆盖 7 个 CRYPTO endpoint。
  - pack 中显式出现 `credential_missing/remote_error/skipped_not_configured` 缺口。
- `tests/unit/data_gateway/test_run_provider_plan.py`
  - run plan call spec 元字段合同补齐（含 `raw_export_policy=metadata_only`）。

本轮复跑命令（2026-05-18）：

```bash
uv run pytest tests/unit/data_gateway/test_provider_execution.py tests/unit/data_gateway/test_market_adapters_live_contract.py tests/integration/data_gateway/test_market_pack_crypto.py tests/unit/data_gateway/test_run_provider_plan.py
```

结果：`29 passed`（exit code 0）。

本轮新增/更新边界断言：

- `tests/unit/data_gateway/test_provider_execution.py`
  - 覆盖 response.url 含 `api_key/auth` 时，写入 `openbb_provider_http_evidence.source_url` 不得包含 secret，且保留非敏感参数（如 `symbol`）。
- `tests/unit/data_gateway/test_market_adapters_live_contract.py`
  - 覆盖 Coinglass 从 adapter env 读取 `COINGLASS_API_KEY/COINGLASS_API_BASE/COINGLASS_API_HEADER_NAME`，不依赖全局 `os.environ`。
  - 覆盖缺 key 时显式 `missing credential keys: COINGLASS_API_KEY`，并断言不触发 HTTP 请求。

## 7) 偏差与后续

- 偏差1：本轮是单元/集成合同验证，不是 live/fresh provider 可用性证明。  
- 说明：secret URL query/userinfo 明文泄漏缺口已在 HTTP evidence capture 与 provider error message 层闭合；raw export 仍维持 `metadata_only`。  
- 后续：进入 provider 许可闭环与 live/fresh collect-first 验收，补齐真实运行证据链。

## 8) T-CL-5 blocker 修正（2026-05-18）

本轮仅修两项 blocker，未扩展架构：

1. **Provider 远端错误文本脱敏**
   - 修复点：`src/claw_trade/data_gateway/providers/market_adapters.py`
   - 变更：
     - `_http_get_json/_http_post_json` 不再透传 `requests` 原始异常文本；
     - 新增 URL/错误文本脱敏，敏感 query key（`api_key/apikey/key/token/access_token/accessToken/x-api-key/api-key/secret/authorization/auth`）和值、以及 URL userinfo 统一替换为 `[REDACTED]`；
     - JSON decode 失败错误中的 `url=` 也改为脱敏 URL。
   - 合同结果：
     - `RuntimeError` 文本不含 secret；
     - 执行层 `ProviderResult.error_message` 与 market pack `reader_brief` 不再传播明文 key URL；
     - `openbb_provider_http_evidence.source_url` 保留结构化 URL（含非敏感参数）并脱敏敏感参数值。

2. **CRYPTO 非 BTC 的 AHR999 不再记为 REMOTE_ERROR**
   - 修复点：`src/claw_trade/data_gateway/providers/market_adapters.py`
   - 变更：
     - 在 `build_call_specs` 阶段对 `ahr999_index` 做适用性判断；
     - 非 BTC（例如 ETH）直接不生成 AHR999 call spec，按“跳过/不适用”语义处理；
     - 不再抛 runtime error，不再落 `REMOTE_ERROR -> provider_unavailable`。

附带清理：
- `tests/integration/data_gateway/test_market_pack_crypto.py` 删除过时 fixture 文案 `"phase1 not implemented"`。

验证命令（本轮）：

```bash
uv run pytest tests/unit/data_gateway/test_market_adapters_live_contract.py tests/unit/data_gateway/test_provider_execution.py tests/integration/data_gateway/test_market_pack_crypto.py tests/unit/data_gateway/test_run_provider_plan.py
```

结果：`31 passed`（exit code 0）。

## 9) T-CL-5 脱敏边角收口与总管复跑（2026-05-18）

本轮只修复 provider evidence/error message 的密钥脱敏边角，未改 provider 许可、费用、raw export 或架构边界：

- `src/claw_trade/data_gateway/providers/http_capture.py`
  - 敏感 query key 先规范化再判断，覆盖 `accessToken`、`x-api-key`、`api-key` 等写法；
  - URL userinfo（如 `user:pass@host`）写入 evidence 前改为 `[REDACTED]@host`。
- `src/claw_trade/data_gateway/providers/market_adapters.py`
  - provider HTTP 异常文本和 URL 走同一脱敏口径；
  - `ProviderResult.error_message` 不保留 secret 明文。
- focused 验证命令：

```bash
uv run pytest tests/unit/data_gateway/test_market_adapters_live_contract.py tests/unit/data_gateway/test_provider_execution.py tests/integration/data_gateway/test_market_pack_crypto.py tests/unit/data_gateway/test_run_provider_plan.py
```

结果：`31 passed`（exit code 0）。

- 总管组合回归命令：

```bash
uv run pytest tests/unit/data_gateway/test_provider_execution.py tests/unit/data_gateway/test_market_adapters_live_contract.py tests/integration/data_gateway/test_market_pack_crypto.py tests/unit/data_gateway/test_run_provider_plan.py tests/contracts/test_frontline_tool_protocol.py tests/contracts/test_frontline_tool_contract.py tests/unit/data_gateway/test_tool_schema.py tests/unit/data_gateway/test_crypto_lens_*.py tests/unit/data_gateway/test_models.py tests/integration/data_gateway/test_old_provider_import_block.py tests/integration/data_gateway/test_mcp_visible_tools.py tests/unit/test_start_control_runtime_script.py
```

结果：`146 passed in 14.49s`（exit code 0）。

只读复审状态：

- 旧 BB runtime/MCP/provider executor/`crypto_market_data_pack`：未见恢复，未见 fallback 回流。
- 六域 provider：已接 data_gateway fetch，不是 phase1 placeholder。
- 仍不能声称 live/fresh 通过：`coinglass/glassnode/fred/tavily` 许可/费用仍是 `*.pending_review`，且尚未收集真实 provider payload、OpenBB HTTP/raw evidence、CryptoLens analysis evidence、OpenViking lineage 与 final report chain。
