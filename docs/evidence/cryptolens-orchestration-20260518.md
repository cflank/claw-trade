# CryptoLens Orchestration Checklist

状态：总管任务总表，未代表实现完成。
日期：2026-05-18
来源：
- `AGENTS.md`
- `docs/CryptoLens接入方案.md`
- `docs/CryptoLens实施任务清单.md`
- `docs/数据源openbb引入方案.md`

## 总原则

- CryptoLens 是 claw-trade 内部 CRYPTO 离线分析模块，不是数据源、provider、外部 MCP、worker、trader 或 PM 决策器。
- 外部取数只走 OpenBB/data_gateway。Python、旧 BB、旧 MCP、旧 provider executor 不得直接出网取数。
- worker 只看自然语言 market pack 和 approved material，不看 raw JSON、debug envelope、Mongo raw/cache object、OpenViking protocol、OpenBB atomic/admin/discovery/raw/debug/cache tools。
- 不新增 fallback、runtime guard、风格 gate 或投资判断 gate。
- live/fresh 不能用 mock/stub/fake/capture-only 证明完成。
- OpenClaw/OpenBB/OpenViking live/fresh 验证必须先过 fixed runtime preflight gate。

## 当前已确认基线

- 当前 canonical BTC 证据目录 `docs/evidence/openbb-canonical-t16-final-20260518T125112Z/crypto_btc/` 只能证明诚实缺口，不能证明 CryptoLens 接入完成。
- 该目录导出的 market report 显示 CRYPTO market source 仍为 `openbb_yfinance/crypto_price_historical`，资金费率、OI、多空比、清算、链上、宏观和 AHR999 未形成 CryptoLens 覆盖证据。
- 当前工作区已有未提交改动。总管和 subagent 不得回退无关改动。
- OpenBB 开工冻结门已前移为第一轮硬门。现有证据 `docs/evidence/openbb-submodule-version.md` 记录了 OpenBB URL、固定 tag/commit、AGPL-3.0-only 许可摘要、personal research 使用边界、extension/shim 放置口径和 D0/T1 执行证据；后续任务仍不得把 provider-specific license/raw export 记录当作已闭合。

## 决策门

| 决策项 | 影响任务 | 当前状态 | 总管动作 |
|---|---|---:|---|
| 每个 provider 的许可、费用、raw export policy 记录落点和导出策略 | T-CL-5、T-CL-11、T-CL-12 | 已获人类批准：按 A 股口径，raw 默认 `metadata_only`，只记录 hash/ref/来源/状态，不导出原始全文；2026-05-18 人类批准 Coinglass/Glassnode/FRED/Tavily 可继续进入验收推进 | T-CL-5/T-CL-12 可继续；证据仍必须记录 adapter_id、license policy、raw export policy 和 live/fresh 结果 |
| OpenViking relation kind：沿用现有 L2 evidence relation kind，或新增 CryptoLens relation kind | T-CL-10、T-CL-12 | 已获人类批准：复用现有证据链关系，不新增 CryptoLens 专用 relation kind | T-CL-10 可继续；关系必须能追到 `crypto_lens_analysis_evidence` |
| CryptoLens analysis evidence 的 Mongo collection 名称 | T-CL-7、T-CL-10、T-CL-12 | 已获人类批准：使用 `crypto_lens_analysis_evidence` | T-CL-7 可继续 |
| 旧 BB 本地 `.env` 增量变量名清单 | T-CL-2、T-CL-5、T-CL-12 | 已获人类批准：以当前 `.env.local` 最后几行旧 BB 设置 + 旧 BB 代码只读扫描闭合；可清理旧 BB runtime 变量，secret 不写入 docs/memory | T-CL-2 follow-up 已完成（见 `docs/evidence/cryptolens-tcl2-old-bb-audit-20260518.md` §10） |
| 旧工具名到 canonical pack 的桥接策略 | T-CL-3、T-CL-9、T-CL-12 | 已获人类批准：旧 runtime/MCP 彻底删除；目标态只保留 canonical `claw_get_*_pack` 和 import-block 证据 | T-CL-3 follow-up 已完成（`start-control-runtime` 删除显式 `bb_crypto_data` 注入分支，55 tests passed） |
| CRYPTO provider 阶段性占位是否可作为完成态 | T-CL-5、T-CL-8、T-CL-11、T-CL-12 | 已获人类明确否定：旧 BB 曾可跑，目标是把旧 provider 取数逻辑接入 OpenBB/data_gateway；phase1 placeholder 只能作为未完成缺口，不可作为完成态 | T-CL-5 worker 已分派：迁移旧 provider 接口参数/字段映射，不恢复旧 BB MCP/runtime/cache/fallback |
| 工具失败语义：`ok:false` 是否可用 exit 0 表达 | T-CL-9、T-CL-11、T-CL-12 | 已获人类批准：严重失败应为真正工具失败，避免上层误判成功 | 已分派工具 worker 修正失败语义和 focused tests |
| OpenViking API 封装映射：未封装 upstream 能力标 `blocked/unavailable` | T-CL-10、T-CL-12 | 未在本轮实现中复核 | T-CL-10 前复核，不能把未封装能力写成已实现 |
| OpenBB/data_gateway 对必需 provider 域无法承载时的缺口表达策略 | T-CL-5、T-CL-8、T-CL-11 | 设计原则明确，具体 provider 需实测 | provider 子任务发现时停止相关域 |
| HK/CRYPTO prompt 或 worker 可见材料边界变化 | T-CL-8、T-CL-9 | 不允许自改 | 若需要变化则停止询问 |

## 任务总表

| 任务 | 状态 | 设计来源 | 文件范围 | 验收标准 | 测试命令 | evidence 要求 | 人类决策 | 并行性 |
|---|---|---|---|---|---|---|---|---|
| T-CL-1 口径冻结与当前偏差基线 | completed | CryptoLens 1、2、14、16、17；OpenBB 1-4、9、13.0 | `docs/evidence/**`、`memory/2026-05-18.md`；不改运行代码 | 证明当前 BTC 不等于完成态；记录 CryptoLens/OpenBB/worker 边界；记录旧 BB 不得恢复；确认 OpenBB 开工冻结门证据存在 | 只读扫描命令；必要时 `rg`/`sed` 证据 | `docs/evidence/cryptolens-tcl1-baseline-20260518.md`；`docs/evidence/openbb-submodule-version.md` | 否 | 串行第一步 |
| T-CL-2 旧 BB 只读盘点与迁入/剔除清单 | completed | CryptoLens 9.1、12/T-CL-1、13、15、16；OpenBB 4.10、13.18 | 只读 `/mnt/d/src/BB/**` 或旧 BB 路径；输出到 `docs/evidence/**`、`memory/2026-05-18.md`；`.env.local` 只读/清理变量名，不泄漏值 | 覆盖 provider fetch、URL、API key、fallback、cache；列出可迁入纯分析模块和禁止运行路径；盘点旧 `.env` 与本项目 `.env.example` 差异且不泄漏 secret | 只读 `rg`/`find`/`sed`；不得执行旧 BB | `docs/evidence/cryptolens-tcl2-old-bb-audit-20260518.md`（已补 `.env.local` 变量名盘点） | 否 | 已完成 |
| T-CL-3 禁止路径和 import-block 基线 | completed | CryptoLens 0、9.4、13、15；OpenBB 4.10、8.2、13.1.1、13.17、13.18 | `tests/integration/data_gateway/test_old_provider_import_block.py`、`tests/integration/data_gateway/test_mcp_visible_tools.py`、`tests/unit/data_gateway/test_tool_schema.py`、`scripts/start-control-runtime.sh`、OpenClaw frontend plugin config、`.env.local` 旧 BB runtime 变量清理 | 旧 BB MCP、旧 executor、旧 `crypto_market_data_pack.py`、US atomics、OpenBB atomic/admin/discovery/debug/cache tools 不能进入 report worker schema 或 fallback 成功路径；workflow/controller/CLI/exporter 不得直连 provider fetch；旧 `bb_crypto_data` runtime 分支彻底删除 | `uv run pytest tests/integration/data_gateway/test_old_provider_import_block.py tests/integration/data_gateway/test_mcp_visible_tools.py tests/unit/data_gateway/test_tool_schema.py tests/unit/test_start_control_runtime_script.py` | import-block 与脚本测试复核：`55 passed in 10.73s` | 否 | 已完成 |
| T-CL-4 OpenBB normalized crypto bundle 合同 | completed | CryptoLens 6.2、6.3、12/T-CL-3、13；OpenBB 5、13.2、13.3、13.19 | `src/claw_trade/data_gateway/models.py` 或专用 crypto bundle 模块；`tests/unit/data_gateway/test_crypto_lens_*.py`、`tests/unit/data_gateway/test_models.py` | bundle/status/gap/conflict/ref 合同齐备；raw refs 仅作证据引用；污染字段不能进入 worker material 或 input | `uv run pytest tests/unit/data_gateway/test_crypto_lens_*.py tests/unit/data_gateway/test_models.py` | bundle 合同测试、input 污染测试 | 否 | 已完成合同层；未接入 runtime |
| T-CL-5 OpenBB CRYPTO provider plan 和 adapters | completed | CryptoLens 6.1、8、9.2、10、16；OpenBB 6、7、8.5、13.6、13.8、13.9、13.16、13.19 | `src/claw_trade/data_gateway/providers/**`、`providers/run_plan.py`、store/evidence tests | market/OHLCV/derivatives/liquidation/onchain/macro/events/AHR999 plan；每 call 有 attempt、HTTP/raw evidence、normalized result、gap/conflict、cache receipt、rate-limit；RunProviderPlan 不 prefetch；raw 默认 `metadata_only` | focused provider/unit tests；adapter contract tests；later integration | provider approval record、adapter_id、provider_config_version、run_provider_plan_id、raw export policy | 人类已批准 `coinglass/glassnode/fred/tavily` 可继续验收；live/fresh 仍需 T-CL-12 真实证明 | 子域已实现；后续接 T-CL-7/T-CL-8/T-CL-12 |
| T-CL-6 Python 重写 CryptoLens 纯分析模块 | completed | CryptoLens 6.4、9.1、12/T-CL-2、13、15、16；OpenBB 13.1.1 | `src/claw_trade/data_gateway/analysis/crypto_lens/**`、`tests/unit/data_gateway/test_crypto_lens_*.py` | 项目内部 import 可用；不引用旧 BB 路径；不依赖旧 TS runtime、BB_MCP、provider key、网络、Mongo；fixture normalized bundle 可输出分析结果 | `uv run pytest tests/unit/data_gateway/test_crypto_lens_*.py` | 旧 BB 纯分析对照、source hash、no-network/no-key/no-Mongo 证明 | 否 | 已完成代码/合同层；live proof 归 T-CL-12 |
| T-CL-7 CryptoLens adapter/input/output/evidence | completed | CryptoLens 6.3、6.4、6.5、7、12/T-CL-5、13；OpenBB 13.4、13.5、13.14、13.19 | `analysis/crypto_lens/adapter.py`、`input_contract.py`、`output_contract.py`、`evidence.py`、store/evidence tests | 唯一入口 `analyze_openbb_crypto_lens_bundle(input)`；只读入参；输出可 hash；独立 `crypto_lens_analysis_evidence`；不输出投资裁决；失败写 failure evidence | `uv run pytest tests/unit/data_gateway/test_crypto_lens_*.py tests/unit/data_gateway/test_evidence_chain.py` | analysis_id、engine version/source hash、input/output hash、normalized refs、data gaps/conflicts/failure reason | 否，collection/evidence 名称已批准为 `crypto_lens_analysis_evidence` | 已完成代码/合同层；live evidence 归 T-CL-12 |
| T-CL-8 CRYPTO MarketPackBuilder 接入 | completed | CryptoLens 5.1、6.6、10、11、12/T-CL-6、13；OpenBB 4.7、4.8、13.11、13.15 | `src/claw_trade/data_gateway/packs/market.py`、`packs/reader_brief.py`、`packs/charts.py`、integration tests | CRYPTO `claw_get_market_pack` 返回含 CryptoLens 分析的自然语言 pack；缺 derivatives/liquidation/OHLCV 样本不足真实进 gaps/readiness；不写最终裁决 | `uv run pytest tests/integration/data_gateway/test_market_pack_crypto.py tests/unit/data_gateway/test_domain_pack_service.py` | pack audit、chart readiness、reader_brief、data gaps/conflicts | 否 | 已完成代码/合同层；BTC fresh/live 归 T-CL-12 |
| T-CL-9 OpenClaw tool schema 和 worker 可见边界 | completed | CryptoLens 4、5.1、11、13；OpenBB 4.7、4.8、13.12、13.17 | `agents/market_analyst/**`、`openclaw_plugins/claw-trade-frontline-tools/**`、tool schema tests、provider payload scan scripts | CRYPTO market analyst provider payload 只含 `claw_get_market_pack`；下游 worker 不见 OpenBB 数据工具；prompt 不含 raw/debug/protocol | `uv run pytest tests/integration/data_gateway/test_mcp_visible_tools.py tests/unit/data_gateway/test_tool_schema.py tests/contracts/test_stage_tool_policy_contract.py`；BTC live provider payload scan | provider payload、visible tool schema、skill audit | 否 | 本地 schema/scan 合同通过；BTC live 自包含 evidence 见 `docs/evidence/cryptolens-btc-live-20260518T193938Z/provider-visible-tools.json` |
| T-CL-10 Evidence chain、OpenViking relation 和 exporter 边界 | completed | CryptoLens 5.1、7、12/T-CL-7、13、16；OpenBB 4.5、13.13、13.14、13.19 | `src/claw_trade/data_gateway/openviking/**`、`store/evidence_chain.py`、exporter tests、lineage integration tests | final report claim 能追到 PM L1、market L1、pack audit、CryptoLens analysis evidence、normalized refs、attempts、HTTP/raw evidence；exporter 不新增结论；复用现有 relation kind，不新增 CryptoLens 专用箭头 | `uv run pytest tests/unit/data_gateway/test_openviking_relations.py tests/integration/data_gateway/test_openviking_lineage.py tests/integration/data_gateway/test_openviking_evidence_bundle.py` | relation graph、ovpack、chart ref 追溯 | 否，relation 口径已批准 | 已完成代码/合同层；真实 final report evidence chain 归 T-CL-12 |
| T-CL-11 错误、缓存、缺口、无 fallback 合同测试 | completed | CryptoLens 10、13、15；OpenBB 4.4、5、8.2、13.19 | tests under `tests/unit/data_gateway/**`、`tests/integration/data_gateway/**`、provider/cache/run-plan tests | OpenBB 失败、CryptoLens 失败、数据不足、AHR999 非 BTC、cache hit/stale/empty/error、旧 BB import-block、secret 脱敏、RunProviderPlan 边界全部有测试 | focused unit/contract/integration test set；最终批次附 collect-first | failure evidence、cache receipts、Collect-first compliance | 否 | 已完成本地合同层；live collect-first 报告归 T-CL-12 |
| T-CL-12 BTC fresh/live 验收和完成判定 | completed | CryptoLens 12/T-CL-8、13、14、17；OpenBB 9.2、9.3、13.19 live/fresh；AGENTS 12.1、13 | runtime scripts、evidence directories、live gate scripts | fixed runtime preflight；BTC live report 含 CryptoLens 分析密度或真实缺口；provider payload/tool schema/tool calls/OpenBB evidence/CryptoLens evidence/OpenViking lineage/chart/final chain 全闭合 | `scripts/start-control-runtime.sh -- <BTC live command>`；evidence-validation command | `docs/evidence/cryptolens-btc-live-20260518T193938Z/` | 否 | 初始 live wrapper exit `1` 仅因 post-run summary `tools=null` bug；report/audit/OpenViking evidence 已先落盘，补充自包含证据后 evidence-validation exit `0`，review PASS |

## 最小完成标准映射

1. CRYPTO `claw_get_market_pack` 真实走 OpenBB provider plan：T-CL-5、T-CL-8、T-CL-12。
2. 所有外部 provider 请求都有 OpenBB HTTP/raw evidence：T-CL-5、T-CL-10、T-CL-12。
3. CryptoLens 不出网、不读 key、不访问 Mongo、不调用 OpenBB：T-CL-6、T-CL-7、T-CL-11。
4. CryptoLens 来自 claw-trade 内部代码且不依赖旧 BB runtime：T-CL-2、T-CL-6、T-CL-11、T-CL-12。
5. CryptoLens input 只来自 OpenBB normalized bundle：T-CL-4、T-CL-7。
6. worker 只看到自然语言 market pack：T-CL-8、T-CL-9、T-CL-12。
7. BTC fresh/live 恢复 CryptoLens 指标分析密度或真实缺口：T-CL-12。
8. 缺失域真实进入 data gaps：T-CL-5、T-CL-8、T-CL-11、T-CL-12。
9. final report evidence chain 闭合：T-CL-10、T-CL-12。
10. import-block 证明旧 BB/旧 provider 不 silent fallback：T-CL-3、T-CL-11、T-CL-12。
11. focused tests 和 live/fresh 验收通过：T-CL-3 至 T-CL-12。

## 第一轮分派策略

- 先分派 T-CL-1、T-CL-2、T-CL-3、T-CL-4 和只读设计复审。
- 暂不分派 T-CL-5 provider 实现，直到 provider 许可/费用/raw export record 口径明确。
- 暂不分派 T-CL-10 实现，直到 OpenViking relation kind 口径明确。
- T-CL-6 等 T-CL-2 和 T-CL-4 产物回来后再拆分，避免未读旧 BB 纯分析来源就重写。

## 2026-05-18 工具失败语义修正（frontline plugin）

- 决策落地：`ok:false`（即使子进程 `exit 0`）必须在 OpenClaw/plugin 层返回 `isError=true`，不能再被上层当成功。
- 代码变更：
  - `openclaw_plugins/claw-trade-frontline-tools/index.js`
    - 新增 `shouldMarkToolResultAsError(payload)`。
    - `executeFrontlineTool` 对已解析 JSON 不再无条件 `isError=false`；当 `payload.ok===false` 或含 `payload.error` 时返回失败语义。
  - `tests/contracts/test_frontline_tool_protocol.py`
    - 新增 `test_pack_runtime_blocked_exit_zero_is_reported_as_tool_error`，覆盖 `ok:false + exit 0`。
    - 将“资料不足但工具成功返回摘要”的测试改为 `ok:true + readiness=insufficient`，保留“成功调用但资料不完整”的原有语义。
- focused tests：
  - `uv run pytest tests/contracts/test_frontline_tool_protocol.py`，exit code `0`，`24 passed`。
  - `uv run pytest tests/contracts/test_frontline_tool_contract.py tests/unit/data_gateway/test_tool_schema.py`，exit code `0`，`10 passed`。
- 偏离状态：无；未改 provider adapters、未改 CryptoLens engine、未新增 gate/fallback。

## 2026-05-18 T-CL-5 六域 provider 与脱敏复审

- provider 实现状态：`derivatives`、`liquidation_map`、`onchain`、`macro`、`events`、`ahr999` 已从 phase1 placeholder 改为 data_gateway provider fetch + normalize；缺凭证仍显式 `credential_missing`，不发请求；无旧 BB MCP/runtime/cache/fallback。
- AHR999 适用性：BTC 生成 call spec；非 BTC 在 plan 阶段跳过，不再伪装成远端错误。
- 证据脱敏：provider HTTP evidence 和 provider error message 会脱敏敏感 query 值与 URL userinfo，覆盖 `api_key/apikey/key/token/access_token/accessToken/x-api-key/api-key/auth/authorization/secret` 等写法；query 参数名会保留，值为 `[REDACTED]`。
- 总管复跑命令：
  - `uv run pytest tests/unit/data_gateway/test_provider_execution.py tests/unit/data_gateway/test_market_adapters_live_contract.py tests/integration/data_gateway/test_market_pack_crypto.py tests/unit/data_gateway/test_run_provider_plan.py tests/contracts/test_frontline_tool_protocol.py tests/contracts/test_frontline_tool_contract.py tests/unit/data_gateway/test_tool_schema.py tests/unit/data_gateway/test_crypto_lens_*.py tests/unit/data_gateway/test_models.py tests/integration/data_gateway/test_old_provider_import_block.py tests/integration/data_gateway/test_mcp_visible_tools.py tests/unit/test_start_control_runtime_script.py`
  - exit code `0`，`146 passed in 14.49s`。
- 只读复审结论：T-CL-3 符合合同层；T-CL-5 部分符合（代码/本地测试层通过，provider 许可/费用与 live/fresh 未闭合）；T-CL-6 部分符合（纯分析骨架通过，evidence/final chain 未接）；`ok:false + exit 0` 工具失败语义符合。
- 仍不能声称完成：`coinglass/glassnode/fred/tavily` 的许可/费用审批、真实 live/fresh provider evidence、CryptoLens `crypto_lens_analysis_evidence`、OpenViking lineage/ovpack/final report chain。

## 2026-05-18 Provider 许可/费用继续验收批准

- 人类回复“可以，继续”，总管理解为：`coinglass/glassnode/fred/tavily` 可继续进入本项目验收推进。
- 保留边界：raw export 仍为 `metadata_only`；provider evidence 不写 secret；未跑 live/fresh 前不得宣称真实 provider 验收通过。
- 状态调整：T-CL-5 本地代码与合同测试状态改为 `completed`；T-CL-12 仍需 fixed runtime preflight 与真实 live/fresh evidence。

## 2026-05-18 T-CL-7/8/10/11 本地闭环与复审

- 实现口径：
  - CryptoLens 唯一公开入口为 `analyze_openbb_crypto_lens_bundle`。
  - 成功写 `crypto_lens_analysis_evidence` success evidence；失败写 `crypto_lens_analysis_evidence` failure evidence，不伪造 analysis success。
  - CRYPTO market pack 以自然语言暴露 CryptoLens summary/readiness/gaps/conflicts，不暴露 raw JSON、Mongo raw/cache、debug envelope 或 OpenViking protocol。
  - CryptoLens evidence 通过现有 `worker_l1_to_l2_evidence` 进入 OpenViking 关系，不新增 CryptoLens 专用 relation kind。
  - 缺 adapter/缺 credential 只写 ProviderAttempt，不写 HTTP/raw/normalized，不伪装 remote success。
- 总管复跑命令：
  - `uv run pytest tests/unit/data_gateway/test_crypto_lens_*.py tests/integration/data_gateway/test_market_pack_crypto.py tests/unit/data_gateway/test_domain_pack_service.py tests/unit/data_gateway/test_openviking_relations.py tests/unit/data_gateway/test_openviking_lineage_writer.py tests/unit/data_gateway/test_evidence_chain.py`
  - exit code `0`，`57 passed in 2.78s`。
  - `uv run pytest tests/unit/data_gateway/test_provider_execution.py tests/unit/data_gateway/test_market_adapters_live_contract.py tests/integration/data_gateway/test_market_pack_crypto.py tests/unit/data_gateway/test_run_provider_plan.py tests/contracts/test_frontline_tool_protocol.py tests/contracts/test_frontline_tool_contract.py tests/unit/data_gateway/test_tool_schema.py tests/unit/data_gateway/test_crypto_lens_*.py tests/unit/data_gateway/test_models.py tests/integration/data_gateway/test_old_provider_import_block.py tests/integration/data_gateway/test_mcp_visible_tools.py tests/unit/test_start_control_runtime_script.py tests/unit/data_gateway/test_domain_pack_service.py tests/unit/data_gateway/test_openviking_relations.py tests/unit/data_gateway/test_openviking_lineage_writer.py tests/unit/data_gateway/test_evidence_chain.py tests/integration/data_gateway/test_openviking_lineage.py tests/integration/data_gateway/test_openviking_evidence_bundle.py`
  - exit code `0`，`183 passed in 14.79s`。
  - `uv run pytest tests/integration/data_gateway/test_mcp_visible_tools.py tests/unit/data_gateway/test_tool_schema.py tests/contracts/test_stage_tool_policy_contract.py`
  - exit code `0`，`49 passed in 7.18s`。
- 只读复审结论：review subagent 判定 T-CL-7/T-CL-8/T-CL-10/T-CL-11 代码/合同层 `PASS`；上轮 3 个 blocker 已闭合；未发现新的代码级 blocker。
- 未闭合边界：T-CL-9 仍缺真实 OpenClaw provider payload/visible tool schema 证据；T-CL-12 仍缺 BTC fresh/live、OpenBB HTTP/raw/normalized evidence、CryptoLens live evidence、OpenViking lineage/ovpack/final report evidence chain。

## 2026-05-18 T-CL-12 BTC fresh/live 证据闭环

- evidence path：`docs/evidence/cryptolens-btc-live-20260518T193938Z/`。
- run：`run-20260518-194156-ea87cf5f`，`exported/state.json` 显示 `status=completed`，13 个 provider payload/worker report 导出。
- fixed runtime preflight：`preflight.md` 中 unset/no-sidecar、`.runtime/dev-services` config/data、`CLAW_TRADE_OPENVIKING_MCP_STARTED=0`、1933/18789 health 全部 `pass`。
- live command：
  - `env -u CLAW_TRADE_OPENVIKING_MCP_MODULE -u CLAW_TRADE_OPENVIKING_MCP_CWD -u CLAW_TRADE_OPENVIKING_SERVER_BIN -u CLAW_TRADE_OPENVIKING_SERVER_CWD scripts/start-control-runtime.sh -- bash -s`
  - exit code `1`；原因是 `/report` completed、OpenBB audit 和 OpenViking collect 已落盘之后，post-run summary 脚本未处理 provider `tools=null`。该失败不代表 live report 或 provider evidence 失败。
- 补充 evidence commands：
  - 生成 `crypto_lens_analysis_evidence.json`、`provider-visible-tools.json`、`openviking-runtime-health-note.md`，exit code `0`。
  - 只读 evidence-validation，exit code `0`，输出 `{"status":"passed","run_id":"run-20260518-194156-ea87cf5f","evidence_dir":"docs/evidence/cryptolens-btc-live-20260518T193938Z"}`。
- OpenBB evidence：
  - `openbb-evidence-audit.json`：`passed=true`，attempts `18`、HTTP evidence `27`、raw refs `14`、missing/invalid raw refs 为空。
  - `cryptolens-live-summary.json`：provider_config_version 为 `sha256:9ec12069be5df12e73d0c65218db039f874d08b1013182b37548d2eee9324bbc`，run_provider_plan_id 为 run_id，raw export policy 为 `metadata_only`。
- CryptoLens evidence：
  - `crypto_lens_analysis_evidence.json`：1 条 `crypto_lens_analysis_evidence`，no-network assertion 为真，normalized refs 为 6，output hash 为 `sha256:d2161296b20036909516707610cc8a05fdcdab9f58ac7c8a6371f9a37d35b416`。
  - 结果不是“全覆盖成功”：analysis readiness/report 明确写 `insufficient`、GLASSNODE 缺凭证、衍生品字段映射缺口；终稿没有把缺口写成成功覆盖。
- Provider payload/tools：
  - `provider-visible-tools.json`：`market_analyst` 只见 `claw_get_market_pack`；fundamental/news/social 前线只见各自 canonical pack；later-stage workers tools 为空；forbidden tool hits 为空。
- OpenViking/final chain：
  - `openviking-runtime-evidence-run-20260518-194156-ea87cf5f.json`：relations 非空，ovpack export `metadata_verified`，import `ok`。
  - relation kind 使用现有 `worker_l1_to_l2_evidence`、`pack_audit_to_provider_attempt` 等；未新增 CryptoLens 专用 relation kind。
  - runtime health note 明确：lineage/ovpack 可用；semantic/vector queue `blocked`，不能声明 OpenViking semantic runtime 全绿。
- Final report：
  - `exported/final_report.md` 含 CryptoLens、AHR999、真实 data gap 说明；assets 2 张。
- Collect-first compliance：
  - batch scope：single BTC CRYPTO fresh/live report。
  - completed items：`run-20260518-194156-ea87cf5f`。
  - failures collected：无产品/runtime 失败；post-run summary bug 已记录为非阻塞收口缺陷。
  - early-stop exception used：no。
- 只读复审结论：review subagent 最终判定 `PASS`；仍建议后续修复 post-run summary `tools=null` bug，并考虑把 evidence export 的 status 命名改得更精确，避免与 `analysis_result.status=insufficient` 混淆。
