# CryptoLens 实施任务清单

> **2026-06-13 状态修正**
>
> 本文中“固定 market 数据包 / 历史数据入口构建 / 旧历史请求计划”相关任务已被 `docs/限流重组.md` 取代。CryptoLens 只消费数据层取回并留证的数据；CRYPTO worker 只通过 `claw_request_data` 提交业务数据项和用途，`DataNeed -> planner -> ProviderCallSpec -> 执行闸/限流/入库` 只发生在数据层内部。

状态：实施计划，未代表代码已完成。
日期：2026-05-18
来源设计：`docs/CryptoLens接入方案.md`

## 0. Coverage Contract

本任务清单逐项覆盖 `docs/CryptoLens接入方案.md` 中的定位、架构、组件、证据、迁移、错误处理、测试和最小完成标准。设计文档中的 `T-CL-0：冻结口径` 在本文中拆为本节 Coverage Contract 和 `T-CL-1：口径冻结与当前偏差基线`。

本文不新增未批准架构：

- CryptoLens 仍只是迁入 claw-trade 的 CRYPTO 离线指标分析引擎，不是数据源、provider、外部 MCP、独立 worker、trader 或 portfolio_manager 决策器。
- data_gateway 仍是唯一外部取数入口、唯一 provider 接口层和唯一 provider evidence 记录者；已删除数据网关 本体不是当前目标运行时依赖。
- CRYPTO worker 仍只通过 `claw_request_data` 获取自然语言市场数据结果；worker 不直接读取旧 BB MCP、CryptoLens raw JSON、legacy 已删除数据网关 atomic/admin/discovery tools、Mongo raw/cache/debug envelope 或 OpenViking protocol。
- CryptoLens analysis 只消费 data_gateway normalized crypto bundle，不出网、不读 provider key、不调用 provider/runtime、不访问 Mongo、不写 BUY/HOLD/SELL、仓位、执行建议、PM rating 或最终投资裁决。
- Python 控制层只做 plan、dispatch、证据保存和材料搬运，不写 worker 市场分析结论，不改写 PM 最终裁决。
- 本清单中的测试、import-block 和 live/fresh gate 只验证设计合同，不新增 runtime guard、风格 gate 或投资判断 gate。

成功标准必须可验证：每个任务都要给出文件范围、验收命令或证据文件；不能用 mock/stub/fake/capture-only 证明 live/fresh 完成；不能用当前 canonical BTC final report 代替 CryptoLens 覆盖证据。

## 1. Task Graph

| 任务 | 目标 | 依赖 | 并行性 | 主要产物 |
|---|---|---|---|---|
| T-CL-1 | 冻结口径并记录当前偏差基线 | 无 | 串行第一步 | 口径证据、偏差清单、当前 BTC 不可作为完成证据 |
| T-CL-2 | 只读盘点旧 BB 代码，形成迁入/剔除清单 | T-CL-1 | 可与 T-CL-3 并行 | 可迁入模块表、禁止运行路径表 |
| T-CL-3 | 建立旧路径、旧工具、旧 executor、legacy 已删除数据网关 atomic/admin/discovery import-block 基线 | T-CL-1 | 可与 T-CL-2 并行 | 禁止项扫描、import-block 测试基线 |
| T-CL-4 | 定义 data_gateway normalized crypto bundle 合同 | T-CL-1 | 可与 T-CL-5 前半并行 | bundle/input/status/dat gap/conflict/ref 合同 |
| T-CL-5 | 扩展 data_gateway CRYPTO provider plan 和 adapters | T-CL-4 | 子域可并行 | market/OHLCV/derivatives/liquidation/onchain/macro/events/AHR999 provider evidence |
| T-CL-6 | 按旧 BB 纯分析口径 Python 重写 CryptoLens | T-CL-2、T-CL-4 | 可与 T-CL-5 后半并行 | `src/claw_trade/data_gateway/analysis/crypto_lens/**` |
| T-CL-7 | 实现 CryptoLens adapter、input/output/evidence | T-CL-4、T-CL-6 | 串行于 T-CL-6 后 | `analyze_crypto_lens_data_results`、`crypto_lens_analysis_evidence` / report evidence file |
| T-CL-8 | 接入 CRYPTO 市场数据结果和图表/readiness | T-CL-5、T-CL-7 | 串行集成 | reader_brief、chart readiness、data gaps、data result audit |
| T-CL-9 | 验证 OpenClaw tool schema 和 worker 可见边界 | T-CL-8、T-CL-3 | 可与 T-CL-10 并行 | provider payload scan、数据工具 schema |
| T-CL-10 | 闭合 evidence chain 和 OpenViking relation/export 边界 | T-CL-7、T-CL-8 | 可与 T-CL-9 并行 | final report -> HTTP/raw evidence lineage |
| T-CL-11 | 错误、缓存、缺口、无 silent fallback 合同测试 | T-CL-5、T-CL-8、T-CL-3 | 与 T-CL-9/T-CL-10 部分并行 | failure/cache/data-gap/import-block tests |
| T-CL-12 | BTC fresh/live 验收和完成判定 | T-CL-9、T-CL-10、T-CL-11 | 串行最后一步 | fixed runtime preflight、live report、provider payload、evidence chain |

并行说明：

- T-CL-2 和 T-CL-3 可以并行，因为一个盘点旧 BB 代码，一个在当前仓库建立负面证明。
- T-CL-5 的 provider 子域可以拆分并行，但所有子域必须统一走同一个 data_gateway provider contract。
- T-CL-6 只能迁入纯分析逻辑；如果盘点发现某段逻辑和 provider fetch/key/cache/rate-limit 纠缠，必须先剥离，不能整段运行。
- T-CL-12 必须最后执行，并遵守 live runtime preflight gate。

## 2. Tasks

### T-CL-1：口径冻结与当前偏差基线

目标：把设计口径锁成可执行边界，证明当前状态还不能称为 CryptoLens 接入完成。

范围：

- 读取 `docs/CryptoLens接入方案.md`、`docs/数据层详细设计.md`、`docs/数据层实施任务清单.md`、当前 CRYPTO final report 证据目录。
- 不修改运行代码。

交付：

- 记录 CryptoLens 不是数据源、不是 provider、不是 MCP、不是 worker、不是决策器。
- 记录 data_gateway 是唯一外部取数入口，已删除数据网关 本体已删除且只作为历史/禁用路径参考。
- 记录当前 BTC canonical final report 只证明诚实缺口，不证明 CryptoLens 覆盖完成。

验收：

- 证据中明确当前 CRYPTO market source 是否仍只有历史 `removed_data_gateway_yfinance/crypto_price_historical` 或等价窄覆盖。
- 明确后续所有任务不得恢复旧 BB 直连。

### T-CL-2：旧 BB 只读盘点与迁入/剔除清单

目标：从 Win11 旧 BB 项目中只读盘点纯分析逻辑，分清“可迁入代码”和“禁止运行路径”。

范围：

- 可读旧 BB 目录作为迁移来源和历史证据。
- 不把旧 BB 目录作为 report runtime 依赖。

交付：

- 可迁入清单：technical analyzers、多周期结构、AMD/SMC、FVG、OB、123 突破、Vegas、双线反转、谐波、成交量分布、清算解释、derivatives 拥挤度、onchain/macro/AHR999 解释口径、readiness/data_gaps/conflicts 纯计算、非 provider 输出结构。
- 剔除清单：直接 HTTP fetch、API key 读取、provider cache、provider rate limit、CoinGlass/Binance/Bybit/FRED/CoinGecko/Glassnode/DefiLlama/Tavily/Snapshot 直连逻辑、MCP server runtime。
- 目标文件映射：每个可迁入模块映射到 `src/claw_trade/data_gateway/analysis/crypto_lens/**`。
- 旧 BB `.env`（含本地增量）与当前仓库 `.env.example` 差异盘点：新增变量、缺失变量、变量用途、归属 provider、是否已迁入本项目。

验收：

- 清单覆盖旧 BB provider fetch、URL、API key、fallback、cache 逻辑。
- 明确 `/mnt/d/src/BB/mcp/crypto-data-mcp/src/domains/live.ts` 这类 live provider fetch 路径不得进入 report runtime。
- 差异盘点明确“继续保留/迁入/不迁入”决策，不写真实 secret 值。

### T-CL-3：禁止路径和 import-block 基线

目标：先建立负面证明，防止迁移过程中旧 BB、旧工具、旧 executor 或 legacy 已删除数据网关 atomic/admin/discovery 工具 silent fallback。

范围：

- 当前仓库扫描和测试，不改业务路径。
- 覆盖 `scripts/start-control-runtime.sh` 中旧 BB MCP 注入风险。

交付：

- import-block 测试覆盖旧 `frontline_data_result`、旧 `frontline_data_result.provider_executor`、旧 `claw_request_data.py`、旧 `bb_crypto_data` MCP、旧 worker-visible tool aliases。
- tool schema 测试覆盖 data_gateway 模式下 frontline 只暴露 `claw_request_data` 业务数据工具，下游 worker 不暴露数据工具。
- report worker 的 model-visible tool schema 只允许 `claw_request_data`；provider/admin/discovery/debug 能力不得进入 report worker model-visible schema。
- 启动配置中旧 `bb_crypto_data` MCP 不再是目标 report runtime 必需项。

验收：

- `uv run pytest tests/integration/data_gateway/test_old_provider_import_block.py tests/integration/data_gateway/test_mcp_visible_tools.py tests/unit/data_gateway/test_tool_schema.py`
- 额外扫描证明 data_gateway 模式下没有旧 BB MCP 或 legacy 已删除数据网关 runtime 参与 CRYPTO 数据成功路径。

### T-CL-4：data_gateway normalized crypto bundle 合同

目标：定义 data_gateway 喂给 CryptoLens 的唯一输入合同。

范围：

- `src/claw_trade/data_gateway/models.py` 或专用 crypto bundle 模块。
- tests under `tests/unit/data_gateway/`。

交付：

- `NormalizedCryptoMarketBundle` 或等价合同：run/call/ticker/market/quote/as_of/date/freshness/domains/domain_status/data_gaps/conflicts/source_refs/attempt_refs/raw_refs/normalized_refs。
- `CryptoDomainBundle` 覆盖 market、ohlcv、derivatives、liquidation_map、onchain、macro、events、ahr999。
- domain readiness 能表达 ready/partial/insufficient/missing/error/stale/license_blocked。
- raw refs 只做证据引用，不进入 worker material。
- CryptoLens input 污染边界：禁止把 provider raw payload、debug envelope、OpenClaw provider payload、OpenViking protocol 字段、prompt 材料正文混入 input 合同。

验收：

- 完整 bundle、缺 derivatives、缺 liquidation_map、OHLCV 样本不足、cache stale、provider conflict、raw refs 不进入 worker material 的 focused tests 全部通过。
- input 污染测试通过：输入合同只接受 normalized 结构和 refs，不接受 raw/debug/prompt 污染字段。

### T-CL-5：data_gateway CRYPTO provider plan 和 adapters

目标：把 CRYPTO market 从 yfinance-only 扩展为多域 data_gateway provider plan。

范围：

- `src/claw_trade/data_gateway/providers/**`
- `src/claw_trade/data_gateway/providers/run_plan.py`
- provider store/evidence 相关测试。

最低域：

- spot/market：当前价、市值、成交量、24h 涨跌、dominance、基础 OHLCV。
- OHLCV：日线、4h、1h 或设计批准的多周期样本。
- derivatives：funding、OI、多空比、taker buy/sell、CVD proxy。
- liquidation_map：清算热力图、上下方清算簇、最大清算簇。
- onchain：链上或明确缺口。
- macro：宏观或明确缺口。
- events：事件或明确缺口。
- AHR999：BTC 可用，其它币种 not applicable 或明确缺口。

验收：

- 每个 provider call 都写 ProviderAttempt、HTTP evidence、raw evidence、normalized result、data gap/conflict、cache receipt、rate-limit 记录。
- DataNeed planner 只做本次需求规划，不 prefetch，不写 remote success。
- data_gateway provider 失败时写真实 gap，CryptoLens 不补数。
- DataNeed planner 断言覆盖：本次需求、provider call spec、rate-limit bucket、endpoint metadata 和禁止 prefetch 边界。
- 每个 provider 必须有许可/费用/raw export approval record，且写清“可导出全文/只允许脱敏/禁止导出全文”；文档和证据只记录脱敏状态，不泄漏 secret。

### T-CL-6：按旧 BB 纯分析口径 Python 重写 CryptoLens

目标：以旧 BB 的纯分析口径、测试样例和历史证据为对照，在 claw-trade 内用 Python 重写 CryptoLens；不保留旧 BB 运行时代码、旧 TS runtime 或 Win11 构建产物依赖。

范围：

- `src/claw_trade/data_gateway/analysis/crypto_lens/**`
- `tests/unit/data_gateway/test_crypto_lens_*.py`

推荐目录：

```text
src/claw_trade/data_gateway/analysis/crypto_lens/
  __init__.py
  input_contract.py
  output_contract.py
  adapter.py
  evidence.py
  engine/
    technical.py
    patterns.py
    derivatives.py
    liquidation.py
    onchain.py
    macro.py
    ahr999.py
    readiness.py
```

验收：

- 本项目内部 import 可用。
- 不引用 `/mnt/d/src/BB` 或 `D:\src\BB`。
- 不依赖旧 BB TS runtime、package、build 产物或 Win11 项目目录。
- 不依赖 `BB_MCP_*`。
- 不读取 provider key。
- 不出网。
- 不访问 Mongo。
- 对 fixture normalized bundle 可输出分析结果。

### T-CL-7：CryptoLens adapter、input/output/evidence

目标：实现 data_gateway normalized rows 到 CryptoLens analysis result 的唯一入口，并写独立 analysis evidence。

接口：

```text
analyze_crypto_lens_data_results(results: Sequence[DataResult], ...) -> CryptoLensAnalysisResult
```

要求：

- 只读入参。
- 输出可 hash。
- 缺口原样传递或进一步细化。
- input 映射必须来自真实 report DataResult 批结果、attempt refs 和 normalized dataset refs，不接受 ticker/market/date 的模型侧覆盖来篡改 runtime 实际值。
- 不调用网络、不读取 provider key、不访问 Mongo、不调用 provider/runtime。
- 不输出 BUY/HOLD/SELL、仓位、执行建议、PM rating 或最终投资裁决。

证据：

- report evidence file 记录 run_id、call_id、worker_id、analysis_id、engine name/version/source hash、input hash、normalized refs、output hash、no-network assertion、data gaps/conflicts、analysis result ref。当前 report path 写入 `evidence_root/data-layer/crypto-lens/<run>/<call>/analysis.json`；CryptoLens analysis runtime 不访问 Mongo。
- 不得把 `crypto_lens_analysis_evidence` 命名或伪装成 data_gateway HTTP/raw provider evidence。

验收：

- no-network、no-key、no-Mongo、no-provider-runtime tests。
- analysis evidence 与 data_gateway provider HTTP/raw evidence 命名隔离 tests。
- CryptoLensAnalysisResult 全字段合同测试通过（包含 success/partial/failure 分支字段完整性）。
- 真实 attempts/normalized dataset refs 进入 CryptoLens input 的集成测试通过。
- input 污染测试通过：raw/debug/protocol/prompt 字段不进入分析入参。

### T-CL-8：CRYPTO 市场数据结果接入

目标：让 `claw_request_data` 的 CRYPTO 路径返回包含 CryptoLens 分析材料的自然语言市场数据结果。

流程：

```text
data_gateway provider execution
  -> report DataResult batch with normalized rows / refs
  -> CryptoLens analysis engine
  -> reader_brief
  -> chart assets
  -> data result audit
```

reader brief 必须包含：

- 数据状态。
- 来源成功/失败摘要。
- 指标覆盖。
- 价格与多周期结构。
- CryptoLens 技术形态解释。
- 衍生品拥挤度。
- 清算压力。
- 链上/宏观/AHR999。
- 图表 readiness。
- data gaps。
- conflicts。
- 条件化观察，但不写最终投资裁决。

验收：

- 市场数据结果不包含 provider raw JSON、CryptoLens raw JSON、Mongo raw/cache object、debug envelope、OpenViking protocol。
- 缺 derivatives 时 funding/OI/多空比/CVD 进入缺口，不写中性。
- 缺 liquidation_map 时不生成清算簇结论。
- OHLCV 样本不足时技术指标状态为 partial/insufficient。

### T-CL-9：OpenClaw tool schema 和 worker 可见边界

目标：证明 worker 只看到 `claw_request_data` 业务数据工具和自然语言材料。

范围：

- `agents/market_analyst/**`
- `openclaw_plugins/claw-trade-frontline-tools/**`
- provider payload capture/export scan。

验收：

- CRYPTO `market_analyst` provider payload 的 visible tool schema 只含 `claw_request_data`。
- downstream workers 不看到数据工具。
- provider payload 不含旧 BB MCP 原子工具、legacy 已删除数据网关 atomic/admin/discovery/raw/debug/cache tools。
- worker prompt 不包含 provider raw payload、CryptoLens raw JSON、Mongo raw/cache/debug envelope、OpenViking protocol 正文。
- model-visible schema 不接受 ticker/market/date 的模型侧覆盖来替代 runtime 真实 run 上下文。
- `agents/market_analyst/skills/crypto-trading-analysis/**` 审计通过：skill 只解释已给材料，不取数、不调 provider、不计算指标、不写最终投资结论。

### T-CL-10：Evidence chain、OpenViking relation 和 exporter 边界

目标：让 final report claim 可追溯到 CryptoLens analysis evidence 和 data_gateway provider HTTP/raw evidence。

必须闭合：

```text
final report claim
  -> PM L1
  -> market_analyst L1
  -> CRYPTO data result audit
  -> crypto_lens_analysis_evidence
  -> data_gateway normalized refs
  -> provider attempts
  -> HTTP evidence
  -> raw evidence
```

验收：

- OpenViking relations 包含 CryptoLens analysis evidence 节点。
- OpenViking relations 显式记录并测试人类批准口径：沿用现有 L2 evidence relation kind，或新增 CryptoLens relation kind（二选一必须在任务证据中写明）。
- downstream approved L1/L2 evidence 关系断言通过：final report claim 至少可追到 PM L1、frontline L1、L2 evidence、data result audit、provider attempts/refs。
- exporter 只搬运 approved material 和图表引用，不新增 unsupported 投资结论。
- final report 中 CryptoLens 指标分析材料能追到 data result audit 和 normalized refs。
- chart assets 能被 exporter 复制并在导出物中保持引用可追溯。

### T-CL-11：错误、缓存、缺口和无 fallback 合同测试

目标：验证失败语义真实，防止 silent fallback。

场景：

- data_gateway 某域失败：写 attempt、HTTP/raw evidence if request sent、data gap，bundle 该域 missing/partial/error，CryptoLens 只基于剩余域分析。
- CryptoLens analysis 失败：data_gateway evidence 保留，`crypto_lens_analysis_evidence` 写失败状态，市场数据结果 readiness 降级，worker 不伪造指标分析。
- data_gateway 数据不足：指标 partial/insufficient，不把未计算写成中性。
- AHR999 在非 BTC 场景写 not applicable 或真实缺口，不伪装为已覆盖。
- cache hit/stale/empty/error：状态真实，不能伪装 fresh remote success。
- 旧 BB/旧 provider 被 import-block 时，目标态 CRYPTO 市场数据结果不走旧 fallback。
- data_gateway adapter 读取本地 key 成功但 evidence 不泄漏 key 值。
- DataNeed planner 边界场景：source role 只排序或记录事实，不越权成业务硬编码限制。

验收：

- focused unit/contract/integration tests 覆盖以上场景。
- 设置 `BB_MCP_SERVER_PATH` 和 `BB_MCP_CWD` 为空或无效时，目标态 CRYPTO 市场数据结果不因此失败，也不启动外部 BB MCP。
- 批次回归和 live/fresh 报告必须附 `Collect-first compliance` 段落。

### T-CL-12：BTC fresh/live 验收和完成判定

目标：用 fresh/live BTC 样本证明 CryptoLens 接入完成。

前置：

- 必须执行 live runtime preflight gate。
- 使用 fixed runtime profile：`scripts/start-control-runtime.sh`、claw-trade repo `uv`、OpenViking `1933`、OpenClaw gateway `18789`、no invest sidecar、OpenViking config from `~/.openviking/ov.conf`、runtime config/data/cache under `.runtime/dev-services`。
- 需要 clean runtime 的命令必须通过 `scripts/start-control-runtime.sh -- <command>` 启动和关闭服务。
- 开工冻结门证据齐备：data_gateway 合同、provider plugins/adapters、license/raw policy 和 legacy 已删除数据网关 删除/禁用证据已落在设计证据目录。

必须收集：

- `openclaw_llm_provider_payload`。
- visible tool schema only `claw_request_data`。
- data_gateway provider HTTP/raw evidence。
- data_gateway normalized refs。
- CryptoLens analysis evidence。
- data_gateway runtime/config marker 或等价 run evidence。
- Mongo attempts/raw/cache/normalized/run-plan/single-flight/validation receipt。
- adapter_id、provider_config_version、run_provider_plan_id。
- OpenViking lineage/ovpack/runtime health。
- chart readiness。
- final report evidence chain。
- `/mnt/d/src/BB` 不存在或不可访问不影响目标态运行，且不启动外部 BB MCP。

验收：

- BTC fresh/live report 包含 CryptoLens 指标分析密度：多周期结构、技术形态、derivatives、liquidation、onchain/macro/AHR999 覆盖或真实缺口。
- 缺失域真实写 data gaps。
- 终稿没有把缺口写成成功覆盖。
- import-block 证明旧 BB/旧 provider 不能 silent fallback。
- collect-first compliance 报告完整。

## 3. Coverage Matrix

| 设计要求类别 | 来源章节 | 覆盖任务 |
|---|---|---|
| CryptoLens 定位：分析引擎，不是数据源/provider/MCP/worker/决策器 | 1、3、4 | T-CL-1、T-CL-6、T-CL-7、T-CL-12 |
| data_gateway 唯一外部数据入口和 provider evidence 边界 | 1、4、5、6.1、7 | T-CL-4、T-CL-5、T-CL-10、T-CL-12 |
| 禁止旧 BB MCP、旧 BB 直连、CryptoLens 再调 provider/runtime | 1、4、9.4、15 | T-CL-2、T-CL-3、T-CL-6、T-CL-11 |
| `/report` 到 final report 端到端时序 | 5.1 | T-CL-8、T-CL-9、T-CL-10、T-CL-12 |
| data_gateway CRYPTO 多域 provider adapters | 6.1、8 | T-CL-5、T-CL-11、T-CL-12 |
| data_gateway normalized rows / DataResult refs 作为唯一输入 | 6.2、6.3 | T-CL-4、T-CL-7 |
| CryptoLens analysis adapter 和代码迁入 | 6.4、9.1 | T-CL-2、T-CL-6、T-CL-7 |
| CryptoLens output 边界：不写最终投资裁决 | 6.5 | T-CL-7、T-CL-8、T-CL-10 |
| 市场数据结果自然语言 reader_brief | 6.6、11 | T-CL-8、T-CL-9 |
| evidence 命名隔离 | 7 | T-CL-7、T-CL-10 |
| DataNeed planner 只计划本次需求、不 prefetch、不写 remote success | 8 | T-CL-5、T-CL-11 |
| 配置迁移和 secret 边界 | 9.2、9.3 | T-CL-5、T-CL-11 |
| 旧 BB MCP runtime 配置边界 | 9.4 | T-CL-3、T-CL-11、T-CL-12 |
| data_gateway provider 失败、CryptoLens 失败、数据不足、cache 命中 | 10 | T-CL-8、T-CL-11 |
| worker skill 只解释材料，不取数、不算指标、不决策 | 11 | T-CL-8、T-CL-9 |
| 迁移步骤 | 12 | T-CL-1 至 T-CL-12 |
| 单元/合同/import-block/集成/live 测试矩阵 | 13 | T-CL-3、T-CL-4、T-CL-7、T-CL-8、T-CL-9、T-CL-10、T-CL-11、T-CL-12 |
| 当前已知偏差 | 14 | T-CL-1、T-CL-12 |
| 不做事项 | 15 | T-CL-3、T-CL-6、T-CL-7、T-CL-9、T-CL-11 |
| 人类决策状态和仍需代码证据关闭的问题 | 16 | T-CL-1、T-CL-2、T-CL-5、T-CL-7、T-CL-10 |
| 最小完成标准 | 17 | T-CL-12 |

## 4. Prohibited Paths / Import Block Matrix

| 禁止项 | 具体 token / 路径 | 为什么禁止 | 验证任务 |
|---|---|---|---|
| Win11 旧 BB runtime 目录 | `D:\src\BB`、`/mnt/d/src/BB` | 目标态必须与旧项目运行目录无关 | T-CL-2、T-CL-6、T-CL-11、T-CL-12 |
| 旧 BB MCP server | `bb_crypto_data`、`bb_crypto_data__build_trade_context`、`BB_MCP_SERVER_PATH`、`BB_MCP_CWD`、`BB_MCP_HTTP_*` | 旧 BB 会自己出网取数，破坏 data_gateway 唯一入口 | T-CL-3、T-CL-11、T-CL-12 |
| 旧 BB live provider fetch | `mcp/crypto-data-mcp/src/domains/live.ts`、CoinGlass/Binance/Bybit/FRED/CoinGecko direct fetch | provider 取数必须进入 data_gateway evidence chain | T-CL-2、T-CL-5、T-CL-6、T-CL-11 |
| 旧数据结果 fallback | `claw_request_data.py`、`claw_request_data`、`claw_request_data`、`frontline_data_result` | 不能在 data_gateway 失败后 silent fallback | T-CL-3、T-CL-8、T-CL-11 |
| 旧 provider executor | `frontline_data_result.provider_executor`、旧 `provider_executor` import slice、旧 `claw_trade.providers` | 目标态使用 data_gateway provider contract，不复用旧 executor | T-CL-3、T-CL-5、T-CL-11 |
| US atomic tools | `get_stock_data`、`get_indicators`、`get_fundamentals`、`get_balance_sheet`、`get_cashflow`、`get_income_statement`、`get_news`、`get_global_news` | worker 可见工具必须收敛为 canonical data need | T-CL-3、T-CL-9、T-CL-12 |
| legacy 已删除数据网关 atomic/admin/discovery/debug tools（model-visible） | `provider.`、`admin.`、`discovery.`、`activate_tools`、`execute_prompt`、`list_providers`、`cache`、`raw`、`debug` | report worker 模型不能直接调用这些工具；控制层/CLI/UI 调试入口可存在但不进 worker schema | T-CL-3、T-CL-9、T-CL-12 |
| raw/cache/debug material | provider raw JSON、CryptoLens raw JSON、Mongo raw/cache object、debug envelope、OpenViking protocol | worker 只看自然语言数据结果和 approved L1 | T-CL-8、T-CL-9、T-CL-10 |
| CryptoLens 出网或读 key | `fetch`、`requests`、`httpx`、`axios`、provider key env reads inside `analysis/crypto_lens` | CryptoLens 是离线分析层，不是 provider | T-CL-6、T-CL-7、T-CL-11 |
| CryptoLens 再调 provider/runtime | CryptoLens module import/call legacy 已删除数据网关 runtime、旧历史数据入口 endpoint、provider adapters | 会形成 provider -> CryptoLens -> provider 绕圈 | T-CL-7、T-CL-11 |
| CryptoLens 写最终投资裁决 | BUY/HOLD/SELL、position size、execution order、PM rating/final decision fields | PM 拥有最终投资裁决，CryptoLens 只供 market analysis | T-CL-7、T-CL-8、T-CL-10 |
| fake 验收 | mock/stub/fake/capture-only success | 不能证明 live/fresh provider 和 evidence 行为 | T-CL-11、T-CL-12 |
| secret 泄漏 | 真实 API key 写入 docs/memory/evidence/final report/prompt/material | 只能记录 credential present/missing 和脱敏 evidence | T-CL-5、T-CL-11、T-CL-12 |

## 5. Acceptance Gate

只有同时满足以下条件，才能说 CryptoLens 接入完成：

1. `claw_request_data` 在 CRYPTO 下真实走 DataNeed planner 取数，planner 不 prefetch、不写 remote success。
2. 所有外部 provider 请求都有 data_gateway HTTP/raw evidence，并有 attempt、normalized ref、cache receipt、rate-limit 或错误记录。
3. CryptoLens analysis engine 来自 claw-trade 仓库内部代码，在 report runtime 中不出网、不读 provider key、不访问 Mongo、不调用 provider/runtime。
4. CryptoLens analysis input 只来自 data_gateway normalized rows、DataResult 批结果和 refs。
5. CryptoLens analysis evidence 独立命名为 `crypto_lens_analysis_evidence`，不能替代 data_gateway HTTP/raw provider evidence。
6. worker 只看到 `claw_request_data` 和自然语言市场数据结果，不看到旧 BB MCP、legacy 已删除数据网关 atomic/admin/discovery/raw/debug/cache tools、raw JSON、Mongo raw/cache/debug envelope 或 OpenViking protocol。
7. BTC fresh/live report 恢复 CryptoLens 指标分析密度；缺失域真实写 data gaps，不能把未覆盖写成成功覆盖。
8. final report evidence chain 能追到 PM L1、market L1、CRYPTO data result audit、CryptoLens analysis evidence、data_gateway normalized refs、provider attempts、HTTP evidence、raw evidence。
9. `/mnt/d/src/BB` 不存在或不可访问、`BB_MCP_SERVER_PATH`/`BB_MCP_CWD` 为空或无效时，目标态 CRYPTO 市场数据结果仍能运行且不启动外部 BB MCP。
10. data_gateway 真实缺口只通过 readiness/data gaps 表达；不得把“旧 BB 路径不可用”包装成完成口径。
11. import-block 证明旧 BB/旧 provider/旧工具不能 silent fallback。
12. focused tests、contract tests、integration tests、BTC fresh/live 验收全部通过。
13. live/fresh 运行遵守 fixed runtime preflight gate，并保存 runtime profile、health、provider payload、ovpack、evidence chain。

建议最终验收命令集合：

```bash
uv run pytest \
  tests/integration/data_gateway/test_old_provider_import_block.py \
  tests/integration/data_gateway/test_mcp_visible_tools.py \
  tests/unit/data_gateway/test_tool_schema.py \
  tests/contracts/test_frontline_tool_protocol.py \
  tests/unit/reports/test_data_need_bridge.py
```

CryptoLens 新增测试落地后，最终 gate 还必须加入：

```bash
uv run pytest \
  tests/unit/data_gateway/test_crypto_lens_*.py \
  tests/integration/data_gateway/test_btc_market_data_need_no_fixed_17_bundle.py \
  tests/integration/data_gateway/test_openviking_relations.py
```

BTC live/fresh gate 必须另行执行，且不能用静态 render、exporter output、旧 canonical report 或 stale dist/evidence 代替。

## 6. Stop Conditions

遇到以下情况必须停止并问人：

- 需要让 CryptoLens 自己出网、读取 provider key、调用 provider/runtime、访问 Mongo，才能补齐指标。
- 需要恢复旧 BB MCP、旧 `claw_request_data.py`、旧 provider executor 或旧 Win11 BB runtime 路径，才能让 CRYPTO report 通过。
- 旧 BB 纯分析代码无法获得，必须改为重新实现分析引擎；这属于范围和证据来源变化。
- 某 provider 的许可、费用、商业使用边界或 raw export policy 不明确。
- data_gateway 无法承载某个必需 provider 域，且没有批准的缺口表达策略。
- 需要把 legacy 已删除数据网关 atomic/admin/discovery/raw/debug/cache tools 暴露给 worker。
- 需要让 worker 直接读 raw JSON、Mongo raw/cache object、debug envelope 或 OpenViking protocol 正文。
- 需要 Python 写 worker 市场分析结论、PM rating 或最终投资裁决。
- 需要新增或收紧 runtime guard、风格 gate、投资判断 gate 才能让报告“看起来正确”。
- 需要恢复 `third_party/removed_data_gateway` 或把 CryptoLens 代码放进外部 runtime 写 claw-trade 业务逻辑。
- 无法取得真实 provider payload capture，却要声称 worker 可见边界已验证。
- live/fresh 运行未通过 fixed runtime preflight gate。
- 同一 gate 类别经过两次 focused fix 后仍失败。
- 任何方案会把 cache hit/stale/empty/error 伪装成 fresh remote success。
- 任何验收只能靠 mock/stub/fake/capture-only 成功。
