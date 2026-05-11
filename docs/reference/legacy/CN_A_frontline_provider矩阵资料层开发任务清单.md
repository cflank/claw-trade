# CN_A frontline provider 矩阵资料层开发任务清单

来源详细设计：`docs/CN_A_frontline_provider矩阵资料层详细设计.md`

生成日期：2026-05-08

## 第一部分，任务总览

### 拆解假设

- 本清单以当前详细设计文件正文为准，历史版本号差异不影响任务拆分。
- 2026-05-09 人工裁决：矩阵内 provider 默认进入启用目标；不得把增强源默认关闭。缺密钥、权限、endpoint/response/rate-limit 合同或调用器时必须产生显式失败 attempt，不能进入成功判定。
- 2026-05-10 人工裁决：正式 workflow handoff 不允许 JSON。worker 之间只能通过自然语言材料或自然语言 report 传递信息；JSON 只允许作为工具协议、provider 内部处理、MongoDB/cache、evidence/log 使用，不进入 worker 可见正文，不作为 OpenViking L1 主材料或 downstream handoff。
- Python 资料层只负责供料、结构化、证据、诊断和 brief；不写报告、不评级、不生成目标价、不改 PM 结论。
- OpenClaw 仍负责真实单 worker turn；本清单不修改 OpenClaw 源码。
- 验收中的远端 provider 集成必须使用真实调用或真实配置错误；单元级边界可使用确定性输入对象验证纯函数。

### 任务总数

共 39 个任务。

### 按模块分组统计

| 模块分组 | 任务数量 | 任务编号 |
|---|---:|---|
| 基础结构、配置与安全 | 3 | T-INF-001, T-CFG-001, T-SEC-001 |
| OpenClaw 工具注册与协议 | 2 | T-TOOL-001, T-TOOL-002 |
| 统一 pack 模型与质量状态 | 2 | T-PACK-001, T-PACK-002 |
| provider 计划与真实调用 | 8 | T-PVD-001 至 T-PVD-008 |
| MongoDB 结构化缓存 | 3 | T-MDB-001 至 T-MDB-003 |
| OpenViking L2 证据 | 2 | T-L2-001, T-L2-002 |
| 通用 brief 与 gate 输入 | 1 | T-BRF-001 |
| Market 与技术图表 | 3 | T-MKT-001, T-TECH-001, T-MKT-002 |
| News 资料包 | 2 | T-NEWS-001, T-NEWS-002 |
| Social 资料包 | 2 | T-SOC-001, T-SOC-002 |
| Fundamental 资料包 | 2 | T-FND-001, T-FND-002 |
| 可观测性与运维 | 2 | T-OBS-001, T-OPS-001 |
| 迁移与旧依赖剥离 | 1 | T-MIG-001 |
| 测试、live gate 与质量评估 | 6 | T-TEST-001 至 T-TEST-005, T-EVAL-001 |

### 建议开发顺序概述

先完成公共基础：目录骨架、配置、安全、工具注册、统一 pack 模型。随后并行落地 provider plan、MongoDB、OpenViking L2，再实现 AkShare/EastMoney 真实 provider。公共供料链路稳定后，按 market、news、social、fundamental 四条领域线并行实施，最后接 observability、运维 health、迁移剥离、合同测试、集成测试、live gate 和报告质量评估。

关键路径（含 live gate 强前置）：T-INF-001 -> T-CFG-001 -> T-SEC-001 -> T-TOOL-001 -> T-TOOL-002 -> T-PACK-001 -> T-PACK-002 -> T-PVD-001 -> T-PVD-002 -> T-PVD-003 -> T-L2-001 -> T-L2-002 -> T-MDB-001 -> T-MDB-002 -> T-BRF-001 -> T-MKT-001 -> T-TECH-001 -> T-MKT-002 -> T-TEST-003 -> T-TEST-004 -> T-MIG-001 -> T-OBS-001 -> T-OPS-001 -> T-TEST-005 -> T-EVAL-001。

## 第二部分，任务清单

### T-INF-001

- 任务标题：建立 frontline 资料包实现目录与共享入口骨架
- 对应详细设计章节：§1, §2, §3, §4, §4.6.1
- 前置依赖：无
- 任务描述：创建或整理 `openclaw_plugins/claw-trade-frontline-tools/` 与四个 worker skill 目录下的 `scripts/` 入口位置，固定 `agents/{worker}/skills/cn-a-*-data/scripts/` 落点。建立共享 Python 模块位置，用于承载 `ToolRuntimeContext`、`PackEnvelope`、provider、MongoDB、OpenViking 和 brief 代码。该任务只建立真实实现边界和可导入模块，不新增 provider 调用逻辑。
- 交付物：frontline tools plugin 目录、四个 skill 目录入口、共享 Python 包初始化文件、导入 smoke test。
- 验收条件：
  - 给定仓库根目录，当执行导入检查，则共享 Python 模块和四个 `scripts/{domain}_data_pack.py` 均可被 Python 3.12 导入。
  - 给定四个 worker skill 目录，当读取元数据，则分别声明本领域资料包工具名。
  - 给定源码扫描，当查找 OpenClaw 源码改动，则本任务没有修改 `third_party/openclaw`。
  - 给定目录结构，当执行 `rg "alphaear-reporter|alphaear-predictor"` 于新增目录，则没有命中。
- 预估工时：1 天
- 任务类型：基础设施

### T-CFG-001

- 任务标题：实现 frontline provider 环境配置加载与校验
- 对应详细设计章节：§3.2, §3.3, §4.3.3, §4.3.8, §4.4.3, §4.5.3, §7.1, §7.2, §7.3, §10
- 前置依赖：T-INF-001
- 任务描述：实现 `load_frontline_provider_config` 和配置校验，读取 MongoDB、OpenViking、provider timeout、总预算、并发、各 provider 开关和 secret 环境变量。校验 bool、timeout、concurrency、MongoDB URI database path、OpenViking auth mode，并在启用 key provider 但缺 key 时返回 `PROVIDER_KEY_MISSING`。配置对象供 `build_provider_plan`、MongoDB client、L2 client 和 Node plugin timeout 计算使用。
- 交付物：`scripts/config.py`、配置错误码、配置单元测试。
- 验收条件：
  - 给定 `CN_A_PROVIDER_DEFAULT_TIMEOUT_MS=10000` 且 `CN_A_PROVIDER_TOTAL_TIMEOUT_MS=30000`，当加载配置，则单 provider timeout 为 10000ms、总预算为 30000ms。
  - 给定 `CN_A_PROVIDER_MAX_CONCURRENCY=7`，当加载配置，则返回配置错误并指出允许范围为 1 到 6。
  - 给定 `CN_A_MONGODB_URI=mongodb://host`，当加载配置，则返回 `MONGO_CONFIG_INVALID`。
  - 给定 `CLAW_TRADE_OPENVIKING_AUTH_MODE=bearer` 但未提供 token，当加载配置，则返回 OpenViking 配置错误且日志摘要不含 secret 值。
- 预估工时：1.5 天
- 任务类型：基础设施

### T-SEC-001

- 任务标题：实现输入校验、路径规范化与脱敏工具
- 对应详细设计章节：§2.3, §3.3, §4.1.3, §4.1.8, §4.2.2, §4.10.3, §4.14.1, §4.14.2, §4.14.3
- 前置依赖：T-INF-001
- 任务描述：实现 ticker、market、日期、runtime context、tool params、MongoDB URI、L2 target path 和 URL query 的校验工具。实现 `redact_secret(text)`，覆盖 Authorization、api_key、token、password、MongoDB user/password 和 URL query 中的 key/signature，并将 provider 错误摘要限制到 500 字符。该工具被 Node 协议层、provider、MongoDB、L2、brief 和日志模块复用。
- 交付物：`scripts/security.py`、校验与脱敏单元测试。
- 验收条件：
  - 给定 `600519`、`600519.SH`、`SH600519`，当执行 ticker 校验，则均规范化为 `600519.SH`。
  - 给定 `market=US`，当构造 CN_A `ProviderQuery`，则返回 market 校验错误。
  - 给定 L2 relative path 为 `../a.json`，当执行路径校验，则返回 `L2_TARGET_INVALID`。
  - 给定含 `mongodb://user:pass@host/db?authSource=admin` 的错误文本，当脱敏后写日志，则输出不含 `user`、`pass` 和 query secret 原文。
- 预估工时：1.5 天
- 任务类型：非功能性

### T-TOOL-001

- 任务标题：注册四个 OpenClaw frontline 资料包工具
- 对应详细设计章节：§3.1, §3.3, §4.1.1, §4.1.3, §4.1.7, §8.2
- 前置依赖：T-INF-001
- 任务描述：在 `openclaw_plugins/claw-trade-frontline-tools/index.js` 注册 `market_market_data_pack`、`fundamental_fundamentals_data_pack`、`news_news_data_pack`、`social_social_sentiment_pack`。为每个工具绑定唯一 expected worker，确保 worker 可见工具只有本领域资料包工具和 `openviking_write_material`。不得注册 AkShare、EastMoney、MongoDB、OpenViking L2 raw 等 provider 原子能力为 worker tool。
- 交付物：`index.js` 工具注册、worker-tool 映射、工具可见性合同测试。
- 验收条件：
  - 给定 `market_analyst` stage policy，当读取 visible tools，则只包含 `market_market_data_pack` 与 `openviking_write_material`。
  - 给定 `news_analyst` stage policy，当读取 visible tools，则只包含 `news_news_data_pack` 与 `openviking_write_material`。
  - 给定 provider 名称扫描，当读取 visible tools，则不存在 AkShare、EastMoney、MongoDB 或 L2 raw provider tool。
  - 给定 plugin 加载，当列出工具，则四个工具名全部存在且 schema 接收 `PackToolParams` 字段。
- 预估工时：1 天
- 任务类型：接口层

### T-TOOL-002

- 任务标题：实现 Node 运行上下文校验与 Python stdin/stdout 协议
- 对应详细设计章节：§4.1.2, §4.1.3, §4.1.4, §4.1.5, §4.1.6, §4.1.8, §5.1, §5.2
- 前置依赖：T-CFG-001, T-SEC-001, T-TOOL-001
- 任务描述：实现 `readCommand`、`buildToolInput`、`buildRuntimeContext`、`runPythonJson` 和 `ExecuteFrontlineTool` 流程。Node 从 `ctx.singleWorkerCommand` 读取 `run_id/stage/worker_id/call_id/evidence_dir/runtime_vars`，构造 `PythonToolPayload`，通过 stdin JSON 调用对应 Python 入口。处理 `TOOL_RUNTIME_CONTEXT_MISSING`、`TOOL_PARAMS_INVALID`、`TOOL_WORKER_MISMATCH`、`TOOL_CONTEXT_INCOMPLETE`、`TOOL_SUBPROCESS_TIMEOUT`、`TOOL_PROTOCOL_ERROR`。
- 交付物：`index.js` 协议实现、Node 单元测试、Python 协议 smoke script。
- 验收条件：
  - 给定缺失 `ctx.singleWorkerCommand`，当调用任一工具，则返回 `isError=true` 且 code 为 `TOOL_RUNTIME_CONTEXT_MISSING`。
  - 给定 `worker_id=market_analyst` 调用 `news_news_data_pack`，当执行 `readCommand`，则返回 `TOOL_WORKER_MISMATCH`。
  - 给定 `stage` 不是 `frontline`，当执行 `readCommand`，则返回 `TOOL_CONTEXT_INCOMPLETE` 且不启动 Python 子进程。
  - 给定 Python stdout 非 JSON，当执行 `runPythonJson`，则返回 `TOOL_PROTOCOL_ERROR` 并附带 2000 字符内脱敏 stderr 摘要。
  - 给定领域总 timeout 为 30000ms，当执行工具，则子进程 timeout 至少为 35000ms。
- 预估工时：2 天
- 任务类型：接口层

### T-PACK-001

- 任务标题：实现统一资料包数据结构与稳定 JSON 序列化
- 对应详细设计章节：§4.2.1, §4.2.2, §4.3.2, §4.4.2, §4.5.2, §4.6.2, §4.7.2, §4.8.2, §4.9.2, §4.10.2, §4.11.2, §11.4
- 前置依赖：T-INF-001
- 任务描述：实现 `PackInput`、`Quality`、`ProviderAttempt`、`FieldSource`、`EvidenceRef`、`PackEnvelope`、`ProviderSpec`、`ProviderQuery`、`ProviderResult`、`ProviderCacheKey`、`ProviderCacheDocument`、`CacheInspection`、`L2WriteTarget`、`L2WriteRequest`、`L2WriteReceipt`、四个领域 domain data 和 `BriefInput/GateInput`。所有对象必须可 JSON 序列化，schema version 固定为 `cn_a_frontline_pack.v1` 与领域子版本。冻结对象的证据更新使用 `replace_attempt_evidence` 返回新对象。
- 交付物：`scripts/models.py`、序列化工具、模型单元测试。
- 验收条件：
  - 给定完整 `PackEnvelope`，当序列化两次，则两次 JSON 字节完全一致。
  - 给定 `Quality.coverage_score=1.2`，当构造 pack，则返回 schema 校验错误。
  - 给定 frozen `ProviderResult`，当调用 `replace_attempt_evidence`，则原对象不变且新对象包含 payload hash 与 raw payload ref。
  - 给定 `reader_brief` 含内部 URI 主体，当构造 pack，则返回 `PACK_SCHEMA_INVALID`。
- 预估工时：2 天
- 任务类型：基础设施

### T-PACK-002

- 任务标题：实现 PackEnvelope 构造、质量状态转换和 field source 校验
- 对应详细设计章节：§4.2.3, §4.2.4, §4.2.5, §4.2.6, §4.2.7, §4.2.8, §5.4, §6.2
- 前置依赖：T-PACK-001, T-SEC-001
- 任务描述：实现 `build_pack_envelope`、attempt 去重、field source 指向校验、`ok=true` 语义和 `complete/partial/failed` 转换约束。构造成功的外壳写入 `normalized_pack.json` 前必须通过 secret/URI 噪音扫描。provider 失败不直接抛到 OpenClaw，必须先进入 `provider_attempts`，再由领域质量规则计算状态。
- 交付物：`scripts/pack_builder.py`、pack schema 测试、field source 校验测试。
- 验收条件：
  - 给定 `FieldSource.raw_payload_ref` 不在 `raw_payload_refs` 中，当构造 pack，则返回 `PACK_FIELD_SOURCE_INVALID`。
  - 给定同一 `(provider, endpoint, query_fingerprint, started_at)` 出现两次，当构造 pack，则返回 `PACK_SCHEMA_INVALID`。
  - 给定核心 L2 写入失败且无可审计 raw ref，当计算质量，则最高状态为 `failed`。
  - 给定可用证据但 MongoDB upsert 失败诊断，当构造 pack，则状态不高于 `partial`。
- 预估工时：1.5 天
- 任务类型：业务逻辑

### T-PVD-001

- 任务标题：实现四领域 provider spec 矩阵与全源启用目标规则
- 对应详细设计章节：§4.3.1, §4.3.2, §4.3.3, §4.6.3, §4.7.3, §4.8.3, §4.9.3, §4.13.5, §10
- 前置依赖：T-CFG-001, T-PACK-001
- 任务描述：实现 `load_market_provider_specs`、`load_news_provider_specs`、`load_social_provider_specs` 和 `load_fundamental_provider_specs`。把 HLD 矩阵中的 P0/P1/P2 provider 固化为 `ProviderSpec`，矩阵内 provider 默认进入启用目标。缺密钥、权限、endpoint/response/rate-limit 合同或调用器时必须保留 spec，并由后续 `execute_provider_attempt` 生成显式失败 attempt。
- 交付物：`scripts/provider_specs.py`、四领域 spec 表、provider spec 单元测试。
- 验收条件：
  - 给定默认配置，当加载 market specs，则 AkShare、EastMoney direct、Sina、Tencent、Baostock、efinance、Tushare 均进入 enabled target。
  - 给定默认配置，当加载 news specs，则 AkShare `stock_news_em`、`stock_info_global_cls`、Bocha/Tavily/Jina/NewsNow/MiniMax/Tushare 均进入 enabled target。
  - 给定默认配置，当加载 fundamental specs，则 AkShare company info、financial abstract、realtime、EastMoney valuation、Baostock、efinance、Tushare 均进入 enabled target。
  - 给定未在矩阵中的 provider 名称，当构建 plan，则返回 `PROVIDER_NOT_APPROVED`。
- 预估工时：2 天
- 任务类型：业务逻辑

### T-PVD-002

- 任务标题：实现 provider plan、查询指纹和参数映射
- 对应详细设计章节：§2.2, §4.3.2, §4.3.3, §4.3.4, §4.13.1, §4.13.2
- 前置依赖：T-PVD-001, T-SEC-001
- 任务描述：实现 `build_provider_plan`、`ProviderQuery` 构造、稳定 JSON 查询指纹和 `ProviderQueryParameter` 参数来源映射。参数只允许由规范化 ticker、date window、company name、industry、fixed value 和 config 派生，不接受 worker 上送 provider 名。EastMoney secid 规则按沪市 `1.{code}`、深市 `0.{code}` 生成。
- 交付物：`scripts/provider_plan.py`、query fingerprint 测试、参数映射测试。
- 验收条件：
  - 给定相同查询参数但字典键顺序不同，当生成 query fingerprint，则输出相同 `sha256:<hex>`。
  - 给定 `600519.SH`，当生成 EastMoney secid，则输出 `1.600519`。
  - 给定 `000001.SZ`，当生成 EastMoney secid，则输出 `0.000001`。
  - 给定工具参数中包含 `provider=akshare`，当构建 provider plan，则该字段不影响 plan。
- 预估工时：1.5 天
- 任务类型：业务逻辑

### T-PVD-003

- 任务标题：实现 provider attempt 执行、超时和状态归档
- 对应详细设计章节：§4.3.3, §4.3.4, §4.3.5, §4.3.6, §4.3.7, §4.3.8, §5.1
- 前置依赖：T-PVD-002, T-PACK-002
- 任务描述：实现 `execute_provider_attempt` 和 `normalize_provider_payload` 的公共分派框架，覆盖 cache、remote、timeout、empty、schema_invalid、error、auth_missing、contract_missing、not_implemented 等状态；`config_blocked` 仅作旧证据兼容。一次 spec 在一个 call 内最多生成一个 attempt；provider 失败继续执行同领域其他 enabled target provider，直到领域总预算耗尽。执行结果产出 `ProviderResult`，供 L2 writer 写 raw payload 后补齐证据 ref。
- 交付物：`scripts/provider_executor.py`、attempt 状态测试、并发预算测试。
- 验收条件：
  - 给定 provider 缺密钥、权限、合同或调用器，当执行 attempt，则不伪装成功，并返回显式失败状态与错误码。
  - 给定 provider 调用超过 `timeout_ms`，当执行 attempt，则返回 `status=timeout`、`error_code=PROVIDER_TIMEOUT`。
  - 给定 provider 返回字段不满足 spec，当执行 attempt，则返回 `status=schema_invalid` 且 normalized rows 为空。
  - 给定 5 个 provider spec 且并发上限为 3，当执行 plan，则同时运行的 provider 数不超过 3。
- 预估工时：2 天
- 任务类型：业务逻辑

### T-PVD-004

- 任务标题：实现 AkShare market 行情 provider 适配
- 对应详细设计章节：§4.6.3, §4.13.1, §8.1
- 前置依赖：T-PVD-003
- 任务描述：实现 `call_akshare_stock_zh_a_hist`、`call_akshare_sina_daily`、`call_akshare_tencent_hist_tx`，分别调用 AkShare `stock_zh_a_hist`、`stock_zh_a_daily`、`stock_zh_a_hist_tx`。映射日期、开盘、收盘、最高、最低、成交量、成交额和 qfq 口径，字段缺失返回 `PROVIDER_SCHEMA_INVALID`，空 DataFrame 返回 `PROVIDER_EMPTY`。结果交给 market normalizer 统一校验。
- 交付物：`scripts/providers_akshare_market.py`、AkShare market 字段合同测试。
- 验收条件：
  - 给定 `stock_zh_a_hist` 返回含 OHLCV 的 DataFrame，当执行适配，则 raw_count 大于 0 且 rows 可映射到 `MarketPriceRow`。
  - 给定 DataFrame 缺少 `收盘` 字段，当执行适配，则 attempt 为 `schema_invalid`。
  - 给定空 DataFrame，当执行适配，则 attempt 为 `empty` 且 accepted_count 为 0。
  - 给定 AkShare 抛异常，当执行适配，则 attempt 为 `error` 且错误信息已脱敏。
- 预估工时：2 天
- 任务类型：集成

### T-PVD-005

- 任务标题：实现 EastMoney direct market 行情 provider 适配
- 对应详细设计章节：§4.6.3, §4.13.2, §8.1
- 前置依赖：T-PVD-003
- 任务描述：实现 `call_eastmoney_push2his_kline`，通过 HTTPS GET 调用 `https://push2his.eastmoney.com/api/qt/stock/kline/get`。请求参数包含 `secid`、`fields1`、`fields2`、`klt=101`、`fqt=1`、`beg`、`end`，响应 JSON `data.klines` 解析为 OHLCV rows。HTTP 4xx、5xx、JSON 结构变更分别映射为设计错误码。
- 交付物：`scripts/providers_eastmoney.py`、EastMoney direct 合同测试。
- 验收条件：
  - 给定 `600519.SH` 与日期窗口，当构造请求，则包含 `secid=1.600519`、`klt=101`、`fqt=1`。
  - 给定响应 `data.klines` 可解析，当执行适配，则 accepted_count 等于可解析 kline 条数。
  - 给定 HTTP 500，当执行适配，则 attempt 为 `error` 且 error_code 为 `PROVIDER_HTTP_SERVER_ERROR`。
  - 给定 JSON 缺少 `data.klines`，当执行适配，则 attempt 为 `schema_invalid`。
- 预估工时：1.5 天
- 任务类型：集成

### T-PVD-006

- 任务标题：实现 AkShare news provider 适配
- 对应详细设计章节：§4.7.3, §4.13.1, §8.1
- 前置依赖：T-PVD-003
- 任务描述：实现 AkShare `stock_news_em` 与 `stock_info_global_cls` 适配。`stock_news_em` 用 6 位代码获取公司新闻，必须映射标题、来源、发布时间、链接；`stock_info_global_cls` 按日期窗口过滤财联社快讯，作为宏观或行业背景。搜索类矩阵源不得默认关闭；未实现合同/密钥/调用器时由 provider attempt 显式失败。
- 交付物：`scripts/providers_akshare_news.py`、news provider 字段合同测试。
- 验收条件：
  - 给定 `stock_news_em` 返回含标题、来源、发布时间、链接的数据，当执行适配，则 raw items 包含这些字段。
  - 给定 `stock_news_em` 缺少标题字段，当执行适配，则 attempt 为 `schema_invalid`。
  - 给定 `stock_info_global_cls` 返回快讯，当按日期窗口过滤，则窗口外条目不进入 normalized rows。
  - 给定 Bocha spec 但缺 key 或合同，当执行 attempt，则返回 `auth_missing`/`contract_missing` 或等价显式失败。
- 预估工时：1.5 天
- 任务类型：集成

### T-PVD-007

- 任务标题：实现 AkShare social 热度 provider 适配
- 对应详细设计章节：§4.8.3, §4.13.1, §8.1
- 前置依赖：T-PVD-003
- 任务描述：实现 AkShare 东方财富热度相关 endpoint：`stock_hot_rank_latest_em`、`stock_hot_keyword_em`、`stock_hot_rank_relate_em`、`stock_hot_rank_em`、`stock_hot_up_em`。将排名、热度、关键词、相关标的、榜单来源映射为 raw social signals，目标匹配交给 social matcher。AkShare 个股热度 `symbol` 必须使用官方带市场代码格式（沪市 `SH600519`、深市 `SZ000665`），不能使用 `100.600519`。搜索、雪球和股吧专门来源不得默认关闭；未实现合同/密钥/调用器时显式失败。
- 交付物：`scripts/providers_akshare_social.py`、social provider 字段合同测试。
- 验收条件：
  - 给定目标热度 endpoint 返回排名和热度，当执行适配，则 raw signal 包含 rank 或 heat_value。
  - 给定关键词 endpoint 返回关键词，当执行适配，则 raw signal 的 signal_type 为 `topic_keyword`。
  - 给定全市场榜单不含目标代码，当执行适配，则 raw rows 可保留为背景，但 accepted target signal 计数为 0。
  - 给定 endpoint 字段结构变化，当执行适配，则 attempt 为 `schema_invalid`。
- 预估工时：2.5 天
- 任务类型：集成

### T-PVD-008

- 任务标题：实现 AkShare fundamental provider 适配
- 对应详细设计章节：§4.9.3, §4.13.1, §8.1, §10
- 前置依赖：T-PVD-003
- 任务描述：实现 AkShare `stock_individual_info_em`、`stock_financial_abstract_ths`、`stock_zh_a_spot_em` 适配。映射公司名称、行业、主营、报告期、净利润、营业总收入、每股收益、净资产收益率、最新价、总市值、市盈率和市净率。EastMoney valuation、Baostock、efinance、Tushare Pro 必须作为估值/财务备源进入执行目标；缺合同、密钥、权限或调用器时显式失败，不得生成关闭 attempt。
- 交付物：`scripts/providers_akshare_fundamental.py`、fundamental provider 字段合同测试。
- 验收条件：
  - 给定公司信息 endpoint 返回公司名称和行业，当执行适配，则 normalized rows 包含 `company_profile.industry` 或主体信息。
  - 给定 financial abstract 返回报告期、净利润、营业总收入、每股收益、净资产收益率中至少两项，当执行适配，则 accepted_count 大于 0。
  - 给定实时快照返回目标代码行，当执行适配，则映射最新价、总市值、市盈率或市净率。
  - 给定 Tushare Pro 缺 token 或权限，当执行 attempt，则返回 `auth_missing` 或等价显式失败。
- 预估工时：2 天
- 任务类型：集成

### T-MDB-001

- 任务标题：实现 MongoDB 连接、集合创建和索引初始化
- 对应详细设计章节：§4.4.1, §4.4.3, §4.4.7, §4.4.8, §6.1, §7.1, §4.13.3
- 前置依赖：T-CFG-001, T-SEC-001
- 任务描述：实现 MongoDB client 初始化，database 名必须来自 `CN_A_MONGODB_URI` path，连接参数为 `maxPoolSize=5`、connect timeout 2s、server selection timeout 2s。创建 `cn_a_provider_cache`、`cn_a_provider_attempts`、`cn_a_normalized_market_prices`、`cn_a_normalized_news_items`、`cn_a_normalized_social_signals`、`cn_a_normalized_fundamental_fields` 的索引和 TTL。该模块不保存 approval 状态。
- 交付物：`scripts/mongo_store.py`、索引初始化函数、MongoDB integration 测试。
- 验收条件：
  - 给定合法 MongoDB URI，当执行索引初始化，则设计列出的所有 unique、查询和 TTL 索引存在。
  - 给定 URI path 为空，当初始化 client，则返回 `MONGO_CONFIG_INVALID`。
  - 给定 MongoDB ping 超过 200ms 或失败，当执行 health check，则返回不健康状态。
  - 给定集合扫描，当查找 approval 字段，则 MongoDB collections 不包含 workflow approval 状态字段。
- 预估工时：2 天
- 任务类型：基础设施

### T-MDB-002

- 任务标题：实现 provider cache inspect、upsert 和 attempt 写入
- 对应详细设计章节：§4.4.2, §4.4.3, §4.4.4, §4.4.5, §4.4.6, §4.4.7, §6.2, §4.13.3
- 前置依赖：T-MDB-001, T-PACK-001
- 任务描述：实现 `inspect_provider_cache`、`upsert_provider_cache`、`insert_provider_attempt`。cache inspect 按 `ProviderCacheKey` 查询，区分 `cache_hit`、`cache_miss`、`cache_stale`、`schema_invalid`、`error`；upsert 在 L2 raw 写入成功后执行；attempt `_id` 由 run/call/provider/endpoint/started_at 派生。MongoDB 不可用时根据 `CN_A_PROVIDER_CACHE_REQUIRED` 决定继续远端 provider 或返回 failed pack。
- 交付物：`scripts/cache.py` cache/attempt 方法、cache 状态测试。
- 验收条件：
  - 给定 fresh cache doc 且 hash/ref/schema 合格，当 inspect cache，则返回 `cache_hit` 和 mongo ref。
  - 给定 `expires_at <= now`，当 inspect cache，则返回 `cache_stale`。
  - 给定 cache doc 缺少 `raw_payload_ref`，当 inspect cache，则返回 `schema_invalid`。
  - 给定 MongoDB 不可用且 `CN_A_PROVIDER_CACHE_REQUIRED=true`，当执行 cache inspect，则 pack 质量最终为 `failed`。
- 预估工时：2 天
- 任务类型：数据层

### T-MDB-003

- 任务标题：实现四领域 normalized collection 写入
- 对应详细设计章节：§4.3.7, §4.4.7, §4.6.7, §4.7.7, §4.8.7, §4.9.7, §6.1, §6.2
- 前置依赖：T-MDB-001, T-PACK-001
- 任务描述：实现 `upsert_market_prices`、`upsert_news_items`、`upsert_social_signals`、`upsert_fundamental_fields`。写入字段必须包含 market、ticker、provider、endpoint、payload_hash、raw_payload_ref、fetched_at、expires_at 和领域 unique key。MongoDB 写入失败不删除 L2 evidence，但要生成诊断并让质量状态最高为 `partial`。
- 交付物：`scripts/normalized_store.py`、四领域 upsert 方法、TTL 与 unique key 测试。
- 验收条件：
  - 给定同一 market price unique key 写入两次，当执行 upsert，则集合中只保留一条当前记录。
  - 给定 news item 具有相同 `sha256(title|url|publish_time|provider)`，当写入两次，则不会产生重复记录。
  - 给定 social signal unique id 相同，当写入两次，则不会产生重复记录。
  - 给定 MongoDB 写入失败，当构造 pack，则 diagnostic flags 包含写入失败且状态不高于 `partial`。
- 预估工时：2 天
- 任务类型：数据层

### T-L2-001

- 任务标题：实现 OpenViking L2 client、URI 构造和写后校验
- 对应详细设计章节：§4.5.1, §4.5.2, §4.5.3, §4.5.4, §4.5.5, §4.5.8, §4.13.4
- 前置依赖：T-CFG-001, T-SEC-001, T-PACK-001
- 任务描述：实现 `build_l2_uri`、`write_l2_evidence` 和 OpenViking client 配置。L2 path 必须为 `viking://resources/workflow/{run_id}/frontline/{worker_id}/{call_id}/evidence/...`，relative path 禁止越界。写入后执行 stat 或 read-back 校验 sha256 与 size，认证支持 `none` 与 `bearer`，token 只进 Authorization header。
- 交付物：`scripts/evidence.py` L2 client 与 URI 构造、L2 合同测试。
- 验收条件：
  - 给定合法 target，当构造 URI，则路径包含 run_id、frontline、worker_id、call_id 和 evidence prefix。
  - 给定 content bytes，当写入成功并 stat 一致，则返回 `readback_verified=true` 且 sha256 与本地计算一致。
  - 给定 stat size 与本地 size 不一致，当写入后校验，则返回 `L2_HASH_MISMATCH`。
  - 给定 `auth_mode=bearer`，当发起写入，则 Authorization header 使用 token 且日志不含 token 原文。
- 预估工时：2 天
- 任务类型：集成

### T-L2-002

- 任务标题：实现 raw payload、attempts、normalized pack 和 chart manifest 证据写入
- 对应详细设计章节：§4.2.7, §4.5.3, §4.5.4, §4.5.5, §4.5.6, §4.5.7, §5.1, §5.4
- 前置依赖：T-L2-001, T-PACK-002
- 任务描述：实现 `write_raw_payload`、`write_provider_attempts`、`write_pack_evidence` 和 `write_chart_evidence`。raw payload 写入成功后用 `replace_attempt_evidence` 返回带 payload hash 与 raw ref 的结果；`provider_attempts.json` 和 `normalized_pack.json` 在 pack 收尾写入；chart 只写入薄 manifest/reference，不写整图 bytes，且 chart refs 只在 manifest 写入并校验成功后进入 pack。单个 L2 target 在一个 call 内只写一次。
- 交付物：`scripts/evidence_writer.py`、证据写入单元测试、L2 failure 质量影响测试。
- 验收条件：
  - 给定 provider raw payload 写入成功，当更新 result，则 attempt 包含 `payload_hash` 与 `raw_payload_ref`。
  - 给定 raw payload L2 写入失败，当生成 field sources，则该 payload 不进入 field sources。
  - 给定 `provider_attempts.json` 写入失败，当构造 pack，则质量至少降为 `partial`。
  - 给定 chart manifest 写入失败，当构造 market pack，则 chart ref 不进入 `domain_data.chart_refs`。
  - 给定报告后 chart 生命周期清理，当执行 cleanup，则 chart manifest 变更为 `lifecycle=cleared`，且不得继续暴露旧 chart ref。
- 预估工时：2 天
- 任务类型：集成

### T-BRF-001

- 任务标题：实现四领域 reader brief 与 hard gate 输入构造
- 对应详细设计章节：§4.10.1, §4.10.2, §4.10.3, §4.10.4, §4.10.5, §4.10.6, §4.10.7, §4.10.8, §5.1, §8.5
- 前置依赖：T-PACK-002
- 任务描述：实现 `build_reader_brief`、`validate_reader_brief`、`build_gate_input`，生成 500 到 3000 中文字符事实型 brief。brief 必须写清资料范围、质量状态、覆盖分、来源概况、证据缺口和冲突诊断，不写评级、买卖建议、目标价，不堆砌 URI/JSON/provider 内部字段。gate input 输出 quality、attempt count、verified L2 count、missing core fields、unsupported claim risk fields 和 diagnostic flags。
- 交付物：`scripts/reader_brief.py`、brief policy 测试、gate input 测试。
- 验收条件：
  - 给定 complete pack，当生成 brief，则包含资料范围、质量状态、来源概况且长度在 500 到 3000 中文字符之间。
  - 给定 brief 文本含目标价或买卖建议词，当执行校验，则返回 `BRIEF_UNSUPPORTED_CONCLUSION`。
  - 给定 brief 文本含 L2 URI 主体，当执行校验，则返回 `BRIEF_MACHINE_NOISE`。
  - 给定 pack 含 3 个 verified L2 ref，当构造 gate input，则 `l2_verified_count=3`。
- 预估工时：1.5 天
- 任务类型：业务逻辑

### T-MKT-001

- 任务标题：实现 Market 输入规范化、OHLCV 校验和多源合并
- 对应详细设计章节：§4.6.2, §4.6.3, §4.6.4, §4.6.6, §4.6.7, §4.13.1, §8.1
- 前置依赖：T-PACK-001, T-PVD-002
- 任务描述：实现 `normalize_market_input`、`normalize_ticker`、`normalize_market_rows`、`validate_ohlcv_rows`、`merge_market_rows` 和 `BuildMarketProviderQuery`。输入支持 `600519`、`600519.SH`、`SH600519`，默认 60 自然日窗口，复权口径固定 qfq。合并规则按 P0、P1 顺序保留每个 trade_date 的第一条可用记录，行级校验覆盖 OHLCV 数值关系。
- 交付物：`scripts/profile.py`、`scripts/normalizer_market.py`、market normalizer 测试。
- 验收条件：
  - 给定 `SH600519` 且无日期，当规范化输入，则 ticker 为 `600519.SH` 且 start_date 为 end_date 前 60 自然日。
  - 给定 row 的 high 小于 close，当执行 OHLCV 校验，则该 row 被拒收并记录 schema 诊断。
  - 给定 P0 与 P1 对同一 trade_date 都有数据，当合并 rows，则保留 P0 row。
  - 给定 accepted rows 为空，当构造 market quality 输入，则状态候选为 `failed`。
- 预估工时：2 天
- 任务类型：业务逻辑

### T-TECH-001

- 任务标题：接入 AlphaEar techlab 指标与图表计算
- 对应详细设计章节：§3.3, §4.6.1, §4.6.2, §4.11.1, §4.11.2, §4.11.3, §4.11.4, §4.11.5, §4.11.6, §4.11.7, §4.11.8
- 前置依赖：T-MKT-001, T-L2-002
- 任务描述：实现 `compute_market_techlab_outputs` 和 `validate_techlab_input`，输入只能来自 market provider matrix 的 normalized OHLCV。调用 `alphaear-techlab` 的 `indicator_engine.py` 与 `chart_engine.py` 计算 MA、MACD、RSI、BOLL、KDJ、ATR 和 1 到 3 张 PNG 图。chart 文件只写入当前 call 本地临时审计目录；L2 仅写入 `charts/{chart_kind}.manifest.json` 引用，不写整图 bytes。
- 交付物：`scripts/techlab_adapter.py`、techlab adapter 测试、chart manifest L2 写入测试。
- 验收条件：
  - 给定按日期升序且至少 20 行 OHLCV，当执行 techlab adapter，则返回指标对象并至少生成一张 chart ref。
  - 给定 OHLCV 行数不足，当执行 adapter，则返回 `TECHLAB_INPUT_INVALID` 诊断且不调用 chart 渲染。
  - 给定 chart PNG 大于 2 MiB，当写入前校验，则返回 chart 诊断并且该 chart 不进入 pack。
  - 给定 chart manifest read-back，当检查内容，则包含图像 hash/size 与 `lifecycle=ephemeral` 且不含 base64 图片数据。
  - 给定 output_dir 指向当前 call 目录外，当执行校验，则返回路径错误。
- 预估工时：2 天
- 任务类型：集成

### T-MKT-002

- 任务标题：实现 Market 资料包编排、质量计算和存储接线
- 对应详细设计章节：§4.6.1, §4.6.3, §4.6.4, §4.6.5, §4.6.6, §4.6.7, §4.6.8, §5.1, §6.3
- 前置依赖：T-PVD-004, T-PVD-005, T-MDB-002, T-MDB-003, T-L2-002, T-BRF-001, T-TECH-001
- 任务描述：实现 `run_market_data_pack`、`build_market_data_pack`、`compute_market_quality`。流程包括 context 校验、输入规范化、cache inspect、provider plan、bounded parallel provider 执行、OHLCV 合并校验、techlab 指标图表、raw/attempt/pack/chart manifest L2 写入、MongoDB cache 和 normalized rows upsert、brief 与 `MarketDomainData` 输出。质量规则按 complete、partial、failed 状态机执行。
- 交付物：`scripts/market_data_pack.py`、`scripts/quality_market.py`、market pack 单元与集成测试。
- 验收条件：
  - 给定至少一个 P0/P1 行情源成功且 L2、指标和至少一张 chart 成功，当运行 market pack，则 `quality.status=complete`。
  - 给定有 OHLCV 但 chart manifest 写入失败，当运行 market pack，则 `quality.status=partial` 且 brief 写明图表证据缺口。
  - 给定报告后执行 chart cleanup，当生成下游材料，则 `domain_data.chart_refs` 与 `openviking_l2_refs` 不再暴露 stale chart ref。
  - 给定 P0/P1 行情源全部失败，当运行 market pack，则 `quality.status=failed` 且 `domain_data.price_history` 不支撑行情结论。
  - 给定默认 60 自然日窗口，当 pack 返回，则 `recent_rows` 最多 10 条且按 trade_date 升序。
- 预估工时：3 天
- 任务类型：业务逻辑

### T-NEWS-001

- 任务标题：实现 News 目标 profile 解析、硬匹配和新闻分桶
- 对应详细设计章节：§4.7.1, §4.7.2, §4.7.3, §4.7.4, §4.7.6, §5.2, §8.1
- 前置依赖：T-PACK-001, T-PVD-002
- 任务描述：实现 `resolve_news_target_profile`、`classify_news_item`、`DeduplicateByTitleUrlTime` 和 company/industry/macro/announcement 分桶。公司新闻只允许股票代码、公司全称、已批准简称或公告主体硬匹配进入 accepted；行业词只能进入行业/宏观背景。搜索 provider 摘要不得作为公司事实，URL 缺失不得进入 company news accepted。
- 交付物：`scripts/news_matcher.py`、news matching 单元测试。
- 验收条件：
  - 给定标题含 `600519`，当分类新闻，则 bucket 可为 `company_news` 且 match_type 为 `code`。
  - 给定标题只含行业词“白酒”但无目标公司硬匹配，当分类新闻，则不得进入 `company_news`。
  - 给定 company news 候选缺少 `match_evidence_span`，当分类新闻，则进入 rejected 计数。
  - 给定相同 title、url、publish_time 的两条新闻，当去重后，则只保留一条。
- 预估工时：2 天
- 任务类型：业务逻辑

### T-NEWS-002

- 任务标题：实现 News 资料包编排、质量计算和存储接线
- 对应详细设计章节：§4.7.3, §4.7.4, §4.7.5, §4.7.6, §4.7.7, §4.7.8, §5.1, §5.2
- 前置依赖：T-PVD-006, T-NEWS-001, T-MDB-002, T-MDB-003, T-L2-002, T-BRF-001
- 任务描述：实现 `run_news_data_pack`、`BuildNewsDataPack`、`ComputeNewsQuality`。流程包括 runtime context 校验、7 天默认窗口、target profile 解析、cache inspect、provider plan、AkShare news/CLS 真实调用、新闻硬匹配、去重、L2 evidence、MongoDB normalized news upsert、brief 和 `NewsDomainData` 输出。runtime context 缺失是工具上下文错误，不归因于 provider。
- 交付物：`scripts/news_data_pack.py`、`scripts/quality_news.py`、news pack 测试。
- 验收条件：
  - 给定合法 context 且 AkShare 公司新闻有硬匹配，当运行 news pack，则 `company_news` 至少一条且每条有 raw_payload_ref。
  - 给定只有行业/宏观背景且无公司新闻硬命中，当运行 news pack，则 `quality.status` 不高于 `partial`。
  - 给定 context 缺失 `worker_id`，当调用 `news_news_data_pack`，则返回 `TOOL_CONTEXT_INCOMPLETE` 而不是 provider attempt。
  - 给定 accepted news 超过 20 条，当 pack 返回，则每个 bucket 最多 20 条，brief 最多列 8 条。
- 预估工时：3 天
- 任务类型：业务逻辑

### T-SOC-001

- 任务标题：实现 Social target matcher、信号规范化和分桶
- 对应详细设计章节：§4.8.1, §4.8.2, §4.8.3, §4.8.4, §4.8.6, §8.1
- 前置依赖：T-PACK-001, T-PVD-002
- 任务描述：实现 `match_social_signal`、`BucketSocialSignals`、`CountTargetSignals`、`HasTextEvidence`。只接受匹配目标 ticker、公司名或已批准 alias 的信号；泛市场热榜未命中目标时不得进入 target accepted signals。narrative signal 必须有可审计正文或原文链接，否则拒收。
- 交付物：`scripts/social_matcher.py`、social matcher 单元测试。
- 验收条件：
  - 给定 raw signal 含 `600519`，当匹配目标，则输出 `matched_target=true` 且 match evidence 非空。
  - 给定全市场榜单行不含目标代码和公司名，当匹配目标，则返回 None 并增加 rejected_count。
  - 给定 narrative 候选无原文链接且无正文片段，当匹配信号，则不得进入 `narrative_signals`。
  - 给定只有平台热度无正文证据，当计算 evidence strength，则 `social_judgment_allowed=false`。
- 预估工时：2 天
- 任务类型：业务逻辑

### T-SOC-002

- 任务标题：实现 Social 资料包编排、质量计算和存储接线
- 对应详细设计章节：§4.8.3, §4.8.4, §4.8.5, §4.8.6, §4.8.7, §4.8.8, §5.1
- 前置依赖：T-PVD-007, T-SOC-001, T-MDB-002, T-MDB-003, T-L2-002, T-BRF-001
- 任务描述：实现 `run_social_sentiment_pack`、`BuildSocialSentimentPack`、`compute_social_quality`。流程包括 context 校验、target profile、cache inspect、provider plan、AkShare 热度 endpoint 真实调用、信号匹配分桶、L2 evidence、MongoDB normalized social upsert、brief 和 `SocialDomainData` 输出。accepted target signal 为 0 时必须 failed；只有平台热度时最高 partial。
- 交付物：`scripts/social_sentiment_pack.py`、`scripts/quality_social.py`、social pack 测试。
- 验收条件：
  - 给定至少一个目标 accepted signal 且 L2 完整，当运行 social pack，则 `quality.status` 为 `complete` 或 `partial`，并包含对应 bucket。
  - 给定 target accepted signal 为 0，当运行 social pack，则 `quality.status=failed`。
  - 给定有目标热度但无正文证据，当运行 social pack，则 `quality.status=partial` 且 `social_judgment_allowed=false`。
  - 给定 core signal 的 L2 写入失败，当运行 social pack，则 `quality.status=failed`。
- 预估工时：3 天
- 任务类型：业务逻辑

### T-FND-001

- 任务标题：实现 Fundamental 字段映射、冲突诊断和核心缺口计算
- 对应详细设计章节：§4.9.1, §4.9.2, §4.9.3, §4.9.4, §4.9.6, §8.1
- 前置依赖：T-PACK-001, T-PVD-008
- 任务描述：实现 `map_fundamental_fields`、`CanonicalFieldName`、`ValuesConflict` 和 missing core fields 计算。映射公司画像、估值字段、价格上下文、ROE、收入、净利润、EPS、经营现金流，并保留 provider、endpoint、raw ref、payload hash、report period、unit。字段冲突不自动覆盖，写入 `conflict_diagnostics` 并降低 coverage。
- 交付物：`scripts/fundamental_mapper.py`、field mapping 测试、冲突诊断测试。
- 验收条件：
  - 给定 provider rows 含 PE、PB、ROE、营收、净利润、经营现金流，当映射字段，则六个核心字段均可查询。
  - 给定 PE 字段两个 provider 值冲突，当映射字段，则保留第一优先级字段并写入 conflict diagnostic。
  - 给定 PE/PB/ROE 缺失，当计算 missing core fields，则列表包含对应字段名。
  - 给定字段没有 raw_payload_ref，当映射字段，则该字段不得进入 `FundamentalDomainData`。
- 预估工时：2 天
- 任务类型：业务逻辑

### T-FND-002

- 任务标题：实现 Fundamental 资料包编排、质量计算和存储接线
- 对应详细设计章节：§4.9.3, §4.9.4, §4.9.5, §4.9.6, §4.9.7, §4.9.8, §5.1
- 前置依赖：T-FND-001, T-MDB-002, T-MDB-003, T-L2-002, T-BRF-001
- 任务描述：实现 `run_fundamentals_data_pack`、`BuildFundamentalDataPack`、`compute_fundamental_quality`。流程包括 context 校验、ticker 规范化、cache inspect、provider plan、AkShare company info/financial abstract/realtime 真实调用、字段映射与冲突诊断、L2 evidence、MongoDB normalized fundamental upsert、brief 和 `FundamentalDomainData` 输出。PE/PB/ROE/营收/净利润/现金流缺失时保留缺口，不构造无来源字段。
- 交付物：`scripts/fundamentals_data_pack.py`、`scripts/quality_fundamental.py`、fundamental pack 测试。
- 验收条件：
  - 给定公司信息、估值核心字段和至少一组财务/现金流核心字段均有可审计来源，当运行 pack，则 `quality.status=complete`。
  - 给定有公司信息但 PE/PB/ROE/营收/净利润/现金流存在缺口，当运行 pack，则 `quality.status=partial` 且 `missing_core_fields` 列出缺口。
  - 给定核心 provider 全失败，当运行 pack，则 `quality.status=failed`。
  - 给定默认配置，当运行 pack，则 Tushare 不参与核心成功判定且产生关闭 attempt。
- 预估工时：3 天
- 任务类型：业务逻辑

### T-OBS-001

- 任务标题：接入结构化日志、metrics 和 trace span
- 对应详细设计章节：§4.1.8, §4.2.8, §4.3.8, §4.4.8, §4.5.8, §4.6.8, §4.7.8, §4.8.8, §4.9.8, §4.10.8, §4.11.8, §4.12.2, §4.12.3
- 前置依赖：T-PACK-002, T-PVD-003, T-MDB-002, T-L2-002
- 任务描述：实现 metrics 与结构化日志字段，覆盖 `pack_build_total`、`pack_latency_ms`、`provider_attempt_total`、`provider_elapsed_ms`、`mongo_cache_inspect_total`、`mongo_latency_ms`、`l2_write_total`、`l2_readback_failed_total`、`brief_validation_failed_total` 和各领域指标。实现 span `frontline_tool.execute`、`frontline_tool.python_subprocess`、`frontline_pack.build`、`provider.plan`、`provider.execute`、`mongo.inspect`、`l2.write`、`pack.quality`、`market.techlab.*`。日志只记录 run/call/provider/status/计数字段，不打印 raw payload。
- 交付物：`scripts/observability.py`、metrics 注册、日志字段测试。
- 验收条件：
  - 给定一次 provider attempt，当记录 metrics，则 `provider_attempt_total{domain,provider,endpoint,status}` 增加 1。
  - 给定一次 L2 写入失败，当记录 metrics，则 `l2_write_total{kind,status="failure"}` 增加 1。
  - 给定结构化日志 payload，当输出日志，则包含 run_id、call_id、worker_id、tool_name、domain、provider、status、elapsed_ms。
  - 给定日志内容扫描，当包含 Authorization 或 token 原文，则测试失败。
- 预估工时：2 天
- 任务类型：非功能性

### T-OPS-001

- 任务标题：实现 health check、优雅停机和部署前检查
- 对应详细设计章节：§4.12.1, §4.12.4, §7.2, §8.3
- 前置依赖：T-TOOL-002, T-MDB-001, T-L2-001, T-PVD-001, T-OBS-001
- 任务描述：实现 `frontline_tool.health`、`frontline_data_pack.health`、`frontline_l2.health`、`frontline_provider.health`、`frontline_evidence_dir.health`。SIGTERM 时停止启动新的 provider 调用，已完成 attempt 写入 `provider_attempts`，未开始的主线 spec 写入 `error_code=PACK_TERMINATED` attempt。部署前检查必须验证索引、health、四工具注册和 stage policy 合同。
- 交付物：health check 命令或模块、SIGTERM handler、部署前检查脚本、运维测试。
- 验收条件：
  - 给定四个工具未全部注册，当执行 `frontline_tool.health`，则返回 `tool_registration_error`。
  - 给定 MongoDB ping 小于 200ms 且索引存在，当执行 data pack health，则返回 healthy。
  - 给定 OpenViking L2 小 JSON 写入 stat 校验失败，当执行 L2 health，则返回不健康并阻断核心证据成功。
  - 给定进程收到 SIGTERM 且有未开始 provider spec，当结束 pack，则 attempts 包含 `PACK_TERMINATED`。
- 预估工时：2 天
- 任务类型：非功能性

### T-MIG-001

- 任务标题：迁移 Market 运行路径并剥离 alphaear-stock 正式取数依赖
- 对应详细设计章节：§1, §3.3, §4.6.1, §4.11.1, §6.3, §10, §11.1
- 前置依赖：T-MKT-002, T-TECH-001, T-TEST-004
- 任务描述：把 market 正式运行路径收束到 CN_A provider matrix，`alphaear-techlab` 只保留指标和图表计算。旧 SQLite 或 AlphaEar 本地 evidence 不能进入新 pack、cache、approved manifest 或质量计分。完成 market pack 集成验证后，用 repo 扫描证明 `alphaear-stock` 不再作为 market 正式取数路径。
- 交付物：market 入口接线调整、旧路径依赖扫描报告、迁移说明。
- 验收条件：
  - 给定运行 `market_market_data_pack`，当抓取 provider attempts，则行情来源为 CN_A provider matrix 中的 provider。
  - 给定 `rg "alphaear-stock"` 扫描正式 market 运行路径，当检查命中，则没有取数权威路径依赖。
  - 给定历史 SQLite evidence，当运行新 market pack，则该 evidence 不进入 `raw_payload_refs`、MongoDB cache 或 approved manifest。
  - 给定 techlab adapter 调用，当检查输入来源，则只来自 normalized OHLCV。
- 预估工时：1.5 天
- 任务类型：业务逻辑

### T-TEST-001

- 任务标题：编写公共模型、配置、安全、provider 和存储单元测试
- 对应详细设计章节：§8.1, §11.2, §11.3, §11.4
- 前置依赖：T-CFG-001, T-SEC-001, T-PACK-002, T-PVD-003, T-MDB-002, T-L2-002
- 任务描述：实现公共单元测试集，覆盖 ticker normalization、provider plan enabled target、provider auth/contract/not_implemented/timeout/empty/schema invalid attempt、Mongo cache hit/stale/schema invalid、OpenViking L2 write success/failure、PackEnvelope schema 和 field source 校验。测试必须验证真实实现函数，不把测试路径写入生产成功逻辑。
- 交付物：`tests/test_frontline_common_models.py`、`tests/test_provider_specs.py`、`tests/test_cache.py`、`tests/test_evidence.py`。
- 验收条件：
  - 给定测试命令，当运行公共单元测试，则所有用例通过且覆盖上述状态。
  - 给定 provider timeout 场景，当运行测试，则断言 attempt status 为 `timeout`。
  - 给定 L2 read-back mismatch 场景，当运行测试，则断言返回 `L2_HASH_MISMATCH`。
  - 给定 PackEnvelope field source 指向缺失 ref，当运行测试，则断言 `PACK_FIELD_SOURCE_INVALID`。
- 预估工时：2 天
- 任务类型：非功能性

### T-TEST-002

- 任务标题：编写四领域资料包单元测试
- 对应详细设计章节：§4.6.5, §4.7.5, §4.8.5, §4.9.5, §8.1
- 前置依赖：T-MKT-002, T-NEWS-002, T-SOC-002, T-FND-002
- 任务描述：实现 market、news、social、fundamental 四领域单元测试。Market 覆盖 OHLCV、quality、techlab；News 覆盖 context、硬匹配、rejected 分桶、provider 全失败；Social 覆盖 target accepted signal、无 accepted signal、evidence failure；Fundamental 覆盖 Tushare 缺 token/权限显式失败、字段映射、核心缺口和冲突诊断。
- 交付物：`tests/test_market_data_pack.py`、`tests/test_news_data_pack.py`、`tests/test_social_sentiment_pack.py`、`tests/test_fundamentals_data_pack.py`。
- 验收条件：
  - 给定 market complete 输入，当运行测试，则断言 `quality.status=complete`。
  - 给定 news 行业词命中但公司硬匹配缺失，当运行测试，则断言该条不进入 company_news。
  - 给定 social target accepted signal 为 0，当运行测试，则断言 `quality.status=failed`。
  - 给定 fundamental PE/PB/ROE 缺失，当运行测试，则断言 `missing_core_fields` 包含对应字段。
- 预估工时：2.5 天
- 任务类型：非功能性

### T-TEST-003

- 任务标题：编写 OpenClaw 工具可见性与 Node 协议合同测试
- 对应详细设计章节：§4.1, §5.2, §8.2
- 前置依赖：T-TOOL-002, T-MKT-002, T-NEWS-002, T-SOC-002, T-FND-002
- 任务描述：实现合同测试，验证 skill manifest 只导出本 worker 资料包工具，stage policy 只暴露资料包工具与 `openviking_write_material`，provider 原子接口不出现在 visible tools，`openviking_write_material` 对普通 worker 只暴露 `content`，Node `readCommand` 完整校验 worker_id、tool_name、stage、run_id、call_id、evidence_dir。
- 交付物：`tests/test_frontline_tool_contract.py`、Node plugin contract 测试。
- 验收条件：
  - 给定四个 frontline worker，当读取 visible tools，则每个 worker 只看到本领域 pack 工具与 `openviking_write_material`。
  - 给定 provider 原子接口名称，当扫描 visible tools，则无命中。
  - 给定 `openviking_write_material` schema，当检查普通 worker 视图，则只暴露 `content`。
  - 给定缺失 `evidence_dir` 的 command，当执行 `readCommand`，则返回 `TOOL_CONTEXT_INCOMPLETE`。
  - 给定 `stage` 非 `frontline` 或 `market` 非 `CN_A` 的 command，当执行工具协议合同测试，则返回结构化 context 或 params 错误且不产生 provider attempt。
- 预估工时：2 天
- 任务类型：非功能性

### T-TEST-004

- 任务标题：执行 MongoDB、OpenViking 和 OpenClaw plugin 集成测试
- 对应详细设计章节：§5.1, §5.3, §5.4, §6.1, §6.2, §8.3
- 前置依赖：T-TEST-001, T-TEST-002, T-TEST-003
- 任务描述：实现并执行集成测试，覆盖资料包 CLI stdin/stdout contract、MongoDB local collection/index/cache inspect/upsert/TTL、OpenViking L2 raw payload/attempts/normalized pack/chart manifest write-readback（不保存整图 bytes/base64）、final report 导出时图像复制到 `reports/assets/` 并使用相对路径引用（无可复制图像时导出必须失败）、报告后 chart 生命周期 cleanup（清理本地图像时同步清理 Viking 引用且不删除报告资产副本，cleanup 失败必须显式失败）、OpenClaw plugin 四个工具收到 runtime_context。并发 cache upsert 测试必须验证 unique key 防重复、L2 path 由 call_id 隔离。
- 交付物：`tests/integration/test_frontline_data_pack_cli.py`、MongoDB integration、L2 integration、OpenClaw plugin execution 测试报告。
- 验收条件：
  - 给定 CLI stdin JSON payload，当执行四个 Python 入口，则 stdout 为合法 `PackEnvelope` JSON 或结构化 tool error。
  - 给定本地 MongoDB，当执行索引初始化与 cache upsert，则索引存在且重复 key 不产生重复当前缓存。
  - 给定 OpenViking L2 可用，当写 raw payload、attempts、normalized pack、chart manifest，则 stat sha/size 均一致。
  - 给定 chart manifest read-back，当检查内容，则不含整图 bytes/base64，且 `EvidenceRef.kind=chart_manifest`。
  - 给定 final report 导出完成，当检查 `final-report.md` 的图片引用，则仅指向 `assets/...` 相对路径；对应图像副本存在于 `reports/assets/`。
  - 给定无可复制图像资产，当执行 final report 导出，则返回失败结果，不能生成“成功报告+无图占位文案”。
  - 给定 chart 生命周期 cleanup 完成，当检查下游 pack/manifest/openviking_l2_refs，则不再暴露 stale chart ref；若保留审计记录，只能是 `chart_manifest_cleanup`。
  - 给定 cleanup 完成且临时图已删除，当检查 `reports/assets/`，则最终报告图像副本仍存在且可被 Markdown 引用。
  - 给定 cleanup 过程写入或删除失败，当执行导出，则失败结果需显式暴露 cleanup 错误，不可静默吞掉。
  - 给定 OpenClaw plugin 调用四个工具，当检查 Python payload，则包含完整 runtime_context。
  - 给定两个相同 ticker/query 但不同 `call_id` 的并发 pack 调用，当写入 L2 evidence，则两个 URI 分别包含各自 `call_id` 且互不覆盖。
- 预估工时：3 天
- 任务类型：非功能性

### T-TEST-005

- 任务标题：执行 CN_A 600519.SH frontline live gate collect-first 验收
- 对应详细设计章节：§8.4, §5.1, §5.2, §10, §11.5
- 前置依赖：T-TEST-004, T-MIG-001, T-OPS-001
- 任务描述：以 `/report`、CN_A、`600519.SH` 运行到 `frontline_ready` stop point，collect-first 覆盖 market、fundamental、news、social 四个 worker。捕获 final_prompt、llm_back、report、assistant_return_after_submit、visible_tools、tool_calls、openviking_receipts、provider_attempts、L2 refs。测试报告必须列出 batch scope、completed items、failures collected、early-stop exception、batch fix grouping。
- 交付物：live gate evidence 目录、collect-first 测试报告、失败分组报告。
- 验收条件：
  - 给定 live gate 执行完成，当读取报告，则四个 frontline worker 均有 completed item 或明确 failure evidence。
  - 给定任一 provider 失败，当读取 provider_attempts，则包含 provider、endpoint、status、elapsed_ms、error_code 或 row counts。
  - 给定任一 L2 ref，当执行 hash 校验，则 sha256 与 size 可验证。
  - 给定 collect-first 报告，当检查字段，则包含 batch scope、completed items、failures collected、early-stop exception used 和 batch fix grouping。
- 预估工时：3 天
- 任务类型：非功能性

### T-EVAL-001

- 任务标题：执行 TradingAgents-CN 报告质量评估与 conformance review
- 对应详细设计章节：§8.5, §9, §11.1, §11.2, §11.3, §11.4, §11.5
- 前置依赖：T-TEST-005
- 任务描述：对 live gate 产物执行报告质量评估，检查中文投研报告结构、证据驱动、无工具日志/JSON/URI 噪音、缺口表述不过度防御、无 unsupported PE/PB/ROE/目标价/新闻/情绪；并检查图像口径：最终报告如含图仅引用 `reports/assets/...`，不得引用临时图路径或 Viking chart manifest URI，且图像副本在 cleanup 后仍可用。执行覆盖矩阵复核、禁用替代实现词汇扫描、模块伪码到实现映射、数据结构字段映射和架构边界复核。结论只能使用符合、部分符合、不符合、文档未规定。
- 交付物：`docs/evidence/.../frontline_provider_matrix_eval_report.md`、conformance review 报告、扫描命令输出。
- 验收条件：
  - 给定四个 worker report，当执行质量评估，则每份 report 都有结构、证据、噪音、unsupported claim 检查结果。
  - 给定 provider pack 与 report claims，当发现 unsupported PE/PB/ROE/新闻/情绪断言，则评估报告标为不符合并引用证据路径。
  - 给定包含图像的 final-report.md，当检查图片链接，则全部为 `assets/...` 相对路径且对应 `reports/assets/` 文件存在。
  - 给定源码和文档，当执行禁用替代实现词汇扫描，则任务描述和生产代码无命中。
  - 给定覆盖矩阵，当复核 DLD 章节行，则每行均有至少一个任务编号。
- 预估工时：2 天
- 任务类型：非功能性

## 第三部分，依赖关系图

### 第 1 层：可立即并行开始

可并行：T-INF-001。

### 第 2 层：基础配置与安全

依赖 T-INF-001。可并行：T-CFG-001、T-SEC-001、T-TOOL-001、T-PACK-001。

### 第 3 层：协议、pack 和 provider 矩阵

依赖第 2 层。可并行：T-TOOL-002、T-PACK-002、T-PVD-001、T-MDB-001、T-L2-001。

### 第 4 层：provider plan、MongoDB 与 L2 写入

依赖第 3 层。可并行：T-PVD-002、T-MDB-002、T-MDB-003、T-L2-002。

### 第 5 层：provider 真实适配与 brief

依赖第 4 层。可并行：T-PVD-003、T-BRF-001；T-PVD-004 至 T-PVD-008 在 T-PVD-003 后可并行。

### 第 6 层：领域基础逻辑

依赖 provider 与公共 brief。可并行：T-MKT-001、T-NEWS-001、T-SOC-001、T-FND-001。

### 第 7 层：领域 pack 编排

依赖第 6 层与对应 provider。可并行：T-TECH-001、T-NEWS-002、T-SOC-002、T-FND-002；T-MKT-002 依赖 T-TECH-001。

### 第 8 层：运维与领域单测

依赖领域 pack。可并行：T-OBS-001、T-TEST-001、T-TEST-002；T-OPS-001 依赖 T-OBS-001。

### 第 9 层：合同、集成、迁移、live gate 与评估

串行主线：T-TEST-003 -> T-TEST-004 -> T-MIG-001 -> T-TEST-005 -> T-EVAL-001。T-OPS-001 必须在 T-TEST-005 前完成。

## 第四部分，里程碑建议

### M1：公共链路闭环

- 包含任务：T-INF-001, T-CFG-001, T-SEC-001, T-TOOL-001, T-TOOL-002, T-PACK-001, T-PACK-002
- 达成条件：四个资料包工具可注册，Node 能传递 runtime context 给 Python，Python 能返回合法 `PackEnvelope` 或结构化 tool error。
- 建议完成时间：单人 9 日历天。

### M2：证据与 provider 底座闭环

- 包含任务：T-PVD-001 至 T-PVD-008, T-MDB-001 至 T-MDB-003, T-L2-001, T-L2-002, T-BRF-001
- 达成条件：主线 provider 可真实调用或产生关闭 attempt；MongoDB cache/normalized 写入可用；OpenViking L2 raw/attempt/pack/chart manifest 写入可验 hash/size；brief 与 gate input 可构造。
- 建议完成时间：单人 22 日历天。

### M3：四领域资料包闭环

- 包含任务：T-MKT-001, T-TECH-001, T-MKT-002, T-NEWS-001, T-NEWS-002, T-SOC-001, T-SOC-002, T-FND-001, T-FND-002
- 达成条件：market、news、social、fundamental 四个 pack 都能按 DLD 状态机返回 complete/partial/failed，且每个 accepted field/item/signal 都有可追踪 L2 ref 或明确缺口。
- 建议完成时间：单人 21 日历天。

### M4：运维、迁移与集成验收

- 包含任务：T-OBS-001, T-OPS-001, T-MIG-001, T-TEST-001, T-TEST-002, T-TEST-003, T-TEST-004
- 达成条件：metrics/log/span/health 可用；market 正式取数路径不依赖旧 AlphaEar stock；公共、领域、合同、MongoDB、L2、OpenClaw plugin 集成测试通过。
- 建议完成时间：单人 14 日历天。

### M5：live gate 与报告质量收口

- 包含任务：T-TEST-005, T-EVAL-001
- 达成条件：CN_A `/report` 600519.SH frontline collect-first 验收完成，四 worker 证据齐全或失败可归因；TradingAgents-CN 报告质量评估和 conformance review 完成。
- 建议完成时间：单人 5 日历天。

## 第五部分，风险与阻塞项

### 显式标记提取

详细设计当前正文未发现 `HLD-GAP` 或 `BLOCKED` 标记。需要管理的风险来自隐含外部依赖和增强源启用前合同。

### 风险清单

| 风险项 | 影响任务 | 建议处理顺序 | 当前处置方案 |
|---|---|---|---|
| OpenViking L2 endpoint/auth 未在运行环境提供 | T-L2-001, T-L2-002, T-TEST-004, T-TEST-005 | 优先级 1 | 启动期校验 `CLAW_TRADE_OPENVIKING_BASE_URI` 与 auth mode；L2 不健康时核心证据 pack failed，保留错误证据。 |
| MongoDB URI 缺 database path 或目标库不可达 | T-MDB-001, T-MDB-002, T-OPS-001 | 优先级 1 | URI path 为空返回 `MONGO_CONFIG_INVALID`；cache required 为 true 时 pack failed，否则记录 `cache_unavailable` 并继续远端 provider。 |
| AkShare endpoint 字段结构变化 | T-PVD-004, T-PVD-006, T-PVD-007, T-PVD-008 | 优先级 2 | schema guard 返回 `schema_invalid` attempt，该 payload 不进入 normalized data，并由质量规则降级。 |
| EastMoney direct 请求被限流或结构变化 | T-PVD-005, T-MKT-002 | 优先级 2 | HTTP/JSON 错误映射为 provider attempt，继续 AkShare/Sina/Tencent 主线来源；不把失败隐藏。 |
| `alphaear-techlab` 图表后端不可用 | T-TECH-001, T-MKT-002 | 优先级 2 | OHLCV evidence 保持真实；chart/indicator 失败写诊断，market quality 最高 partial。 |
| OpenClaw `ctx.singleWorkerCommand` 字段与设计不一致 | T-TOOL-002, T-NEWS-002, T-TEST-003 | 优先级 1 | Node 层返回 context tool error，不把 context 错误归因到 provider；合同测试覆盖字段缺失和 mismatch。 |
| 搜索增强源 endpoint/auth/response/rate limit 未闭合 | T-PVD-001, T-PVD-003, T-NEWS-002, T-SOC-002, T-EVAL-001 | 优先级 1 | 增强源不得保持 disabled；缺 endpoint、auth、params、response、rate limit 或 contract test 时生成显式失败 attempt，并纳入质量诊断。 |
| 真实 provider 网络不稳定导致 live gate 部分失败 | T-TEST-005, T-EVAL-001 | 优先级 2 | collect-first 运行完整批次，按 context、provider、evidence、quality 分组修复；命中数据真实性或边界风险时才早停。 |

## 第六部分，覆盖矩阵

| 详细设计章节编号 | 章节摘要 | 对应任务编号列表 | 覆盖状态 |
|---|---|---|---|
| §1 | 文档元信息、范围和设计边界 | T-INF-001, T-MIG-001, T-EVAL-001 | 已覆盖 |
| §2 | 术语表与约定 | T-INF-001, T-PACK-001, T-CFG-001 | 已覆盖 |
| §2.1 | 延用 HLD 术语 | T-INF-001, T-PACK-001 | 已覆盖 |
| §2.2 | 新增内部术语 | T-PACK-001, T-PVD-001, T-L2-001 | 已覆盖 |
| §2.3 | 通用约定 | T-CFG-001, T-SEC-001, T-PACK-001 | 已覆盖 |
| §3 | 系统上下文与部署视图 | T-INF-001, T-TOOL-001, T-MDB-001, T-L2-001, T-OPS-001 | 已覆盖 |
| §3.1 | 部署拓扑 | T-TOOL-001, T-TOOL-002, T-MKT-002, T-NEWS-002, T-SOC-002, T-FND-002 | 已覆盖 |
| §3.2 | 节点与资源预估 | T-CFG-001, T-MDB-001, T-OBS-001, T-OPS-001 | 已覆盖 |
| §3.3 | 网络与安全边界 | T-SEC-001, T-TOOL-001, T-PVD-001, T-MDB-001, T-L2-001 | 已覆盖 |
| §4 | 模块详细设计 | T-CFG-001, T-SEC-001, T-TOOL-001, T-TOOL-002, T-PACK-001, T-PACK-002, T-PVD-001, T-PVD-002, T-PVD-003, T-PVD-004, T-PVD-005, T-PVD-006, T-PVD-007, T-PVD-008, T-MDB-001, T-MDB-002, T-MDB-003, T-L2-001, T-L2-002, T-MKT-001, T-TECH-001, T-MKT-002, T-NEWS-001, T-NEWS-002, T-SOC-001, T-SOC-002, T-FND-001, T-FND-002, T-BRF-001, T-OBS-001, T-OPS-001 | 已覆盖 |
| §4.1 | 工具注册与运行上下文模块 | T-TOOL-001, T-TOOL-002, T-TEST-003 | 已覆盖 |
| §4.1.1 | 职责与边界 | T-TOOL-001 | 已覆盖 |
| §4.1.2 | 核心数据结构 | T-PACK-001, T-TOOL-002 | 已覆盖 |
| §4.1.3 | 接口定义 | T-TOOL-001, T-TOOL-002 | 已覆盖 |
| §4.1.4 | 核心算法与业务流程 | T-TOOL-002 | 已覆盖 |
| §4.1.5 | 状态机 | T-TOOL-002, T-TEST-003 | 已覆盖 |
| §4.1.6 | 错误处理策略 | T-TOOL-002, T-OPS-001 | 已覆盖 |
| §4.1.7 | 数据存储设计 | T-L2-002, T-TEST-003 | 已覆盖 |
| §4.1.8 | 非功能性设计 | T-TOOL-002, T-OBS-001 | 已覆盖 |
| §4.2 | 统一资料包外壳与质量状态模块 | T-PACK-001, T-PACK-002, T-BRF-001 | 已覆盖 |
| §4.2.1 | 职责与边界 | T-PACK-001, T-PACK-002 | 已覆盖 |
| §4.2.2 | 核心数据结构 | T-PACK-001 | 已覆盖 |
| §4.2.3 | 接口定义 | T-PACK-002 | 已覆盖 |
| §4.2.4 | 核心算法与业务流程 | T-PACK-002 | 已覆盖 |
| §4.2.5 | 状态机 | T-PACK-002, T-MKT-002, T-NEWS-002, T-SOC-002, T-FND-002 | 已覆盖 |
| §4.2.6 | 错误处理策略 | T-PACK-002, T-L2-002 | 已覆盖 |
| §4.2.7 | 数据存储设计 | T-L2-002, T-MDB-002 | 已覆盖 |
| §4.2.8 | 非功能性设计 | T-OBS-001 | 已覆盖 |
| §4.3 | Provider 计划、尝试与真实调用模块 | T-PVD-001, T-PVD-002, T-PVD-003 | 已覆盖 |
| §4.3.1 | 职责与边界 | T-PVD-001, T-PVD-003 | 已覆盖 |
| §4.3.2 | 核心数据结构 | T-PACK-001, T-PVD-001 | 已覆盖 |
| §4.3.3 | 接口定义 | T-PVD-002, T-PVD-003 | 已覆盖 |
| §4.3.4 | 核心算法与业务流程 | T-PVD-002, T-PVD-003 | 已覆盖 |
| §4.3.5 | 状态机 | T-PVD-003 | 已覆盖 |
| §4.3.6 | 错误处理策略 | T-PVD-003, T-MKT-002, T-NEWS-002, T-SOC-002, T-FND-002 | 已覆盖 |
| §4.3.7 | 数据存储设计 | T-MDB-002, T-MDB-003, T-L2-002 | 已覆盖 |
| §4.3.8 | 非功能性设计 | T-CFG-001, T-OBS-001 | 已覆盖 |
| §4.4 | MongoDB 结构化缓存模块 | T-MDB-001, T-MDB-002, T-MDB-003 | 已覆盖 |
| §4.4.1 | 职责与边界 | T-MDB-001, T-MDB-002 | 已覆盖 |
| §4.4.2 | 核心数据结构 | T-PACK-001 | 已覆盖 |
| §4.4.3 | 接口定义 | T-MDB-002 | 已覆盖 |
| §4.4.4 | 核心算法与业务流程 | T-MDB-002 | 已覆盖 |
| §4.4.5 | 状态机 | T-MDB-002 | 已覆盖 |
| §4.4.6 | 错误处理策略 | T-MDB-002, T-MDB-003 | 已覆盖 |
| §4.4.7 | 数据存储设计 | T-MDB-001, T-MDB-003 | 已覆盖 |
| §4.4.8 | 非功能性设计 | T-MDB-001, T-OBS-001 | 已覆盖 |
| §4.5 | OpenViking L2 证据写入模块 | T-L2-001, T-L2-002 | 已覆盖 |
| §4.5.1 | 职责与边界 | T-L2-001, T-L2-002 | 已覆盖 |
| §4.5.2 | 核心数据结构 | T-PACK-001 | 已覆盖 |
| §4.5.3 | 接口定义 | T-L2-001 | 已覆盖 |
| §4.5.4 | 核心算法与业务流程 | T-L2-001, T-L2-002 | 已覆盖 |
| §4.5.5 | 状态机 | T-L2-002 | 已覆盖 |
| §4.5.6 | 错误处理策略 | T-L2-002, T-MKT-002, T-SOC-002 | 已覆盖 |
| §4.5.7 | 数据存储设计 | T-L2-002, T-MDB-003 | 已覆盖 |
| §4.5.8 | 非功能性设计 | T-L2-001, T-OBS-001 | 已覆盖 |
| §4.6 | Market 资料包模块 | T-MKT-001, T-TECH-001, T-MKT-002 | 已覆盖 |
| §4.6.1 | 职责与边界 | T-INF-001, T-MIG-001, T-MKT-002 | 已覆盖 |
| §4.6.2 | 核心数据结构 | T-PACK-001, T-MKT-001 | 已覆盖 |
| §4.6.3 | 接口定义 | T-PVD-001, T-PVD-004, T-PVD-005, T-MKT-002 | 已覆盖 |
| §4.6.4 | 核心算法与业务流程 | T-MKT-001, T-MKT-002 | 已覆盖 |
| §4.6.5 | 状态机 | T-MKT-002 | 已覆盖 |
| §4.6.6 | 错误处理策略 | T-MKT-002, T-TECH-001 | 已覆盖 |
| §4.6.7 | 数据存储设计 | T-MDB-003, T-L2-002 | 已覆盖 |
| §4.6.8 | 非功能性设计 | T-OBS-001, T-TEST-005 | 已覆盖 |
| §4.7 | News 资料包模块 | T-PVD-006, T-NEWS-001, T-NEWS-002 | 已覆盖 |
| §4.7.1 | 职责与边界 | T-NEWS-001, T-NEWS-002 | 已覆盖 |
| §4.7.2 | 核心数据结构 | T-PACK-001, T-NEWS-001 | 已覆盖 |
| §4.7.3 | 接口定义 | T-PVD-001, T-PVD-006, T-NEWS-002 | 已覆盖 |
| §4.7.4 | 核心算法与业务流程 | T-NEWS-001, T-NEWS-002 | 已覆盖 |
| §4.7.5 | 状态机 | T-NEWS-002 | 已覆盖 |
| §4.7.6 | 错误处理策略 | T-NEWS-001, T-NEWS-002 | 已覆盖 |
| §4.7.7 | 数据存储设计 | T-MDB-003, T-L2-002 | 已覆盖 |
| §4.7.8 | 非功能性设计 | T-OBS-001, T-TEST-005 | 已覆盖 |
| §4.8 | Social 资料包模块 | T-PVD-007, T-SOC-001, T-SOC-002 | 已覆盖 |
| §4.8.1 | 职责与边界 | T-SOC-001, T-SOC-002 | 已覆盖 |
| §4.8.2 | 核心数据结构 | T-PACK-001, T-SOC-001 | 已覆盖 |
| §4.8.3 | 接口定义 | T-PVD-001, T-PVD-007, T-SOC-002 | 已覆盖 |
| §4.8.4 | 核心算法与业务流程 | T-SOC-001, T-SOC-002 | 已覆盖 |
| §4.8.5 | 状态机 | T-SOC-002 | 已覆盖 |
| §4.8.6 | 错误处理策略 | T-SOC-001, T-SOC-002 | 已覆盖 |
| §4.8.7 | 数据存储设计 | T-MDB-003, T-L2-002 | 已覆盖 |
| §4.8.8 | 非功能性设计 | T-OBS-001, T-TEST-005 | 已覆盖 |
| §4.9 | Fundamental 资料包模块 | T-PVD-008, T-FND-001, T-FND-002 | 已覆盖 |
| §4.9.1 | 职责与边界 | T-FND-001, T-FND-002 | 已覆盖 |
| §4.9.2 | 核心数据结构 | T-PACK-001, T-FND-001 | 已覆盖 |
| §4.9.3 | 接口定义 | T-PVD-001, T-PVD-008, T-FND-002 | 已覆盖 |
| §4.9.4 | 核心算法与业务流程 | T-FND-001, T-FND-002 | 已覆盖 |
| §4.9.5 | 状态机 | T-FND-002 | 已覆盖 |
| §4.9.6 | 错误处理策略 | T-FND-001, T-FND-002 | 已覆盖 |
| §4.9.7 | 数据存储设计 | T-MDB-003, T-L2-002 | 已覆盖 |
| §4.9.8 | 非功能性设计 | T-OBS-001, T-TEST-005 | 已覆盖 |
| §4.10 | Reader Brief、报告边界与 hard gate 输入模块 | T-BRF-001, T-EVAL-001 | 已覆盖 |
| §4.10.1 | 职责与边界 | T-BRF-001 | 已覆盖 |
| §4.10.2 | 核心数据结构 | T-PACK-001 | 已覆盖 |
| §4.10.3 | 接口定义 | T-BRF-001 | 已覆盖 |
| §4.10.4 | 核心算法与业务流程 | T-BRF-001 | 已覆盖 |
| §4.10.5 | 状态机 | T-BRF-001 | 已覆盖 |
| §4.10.6 | 错误处理策略 | T-BRF-001, T-EVAL-001 | 已覆盖 |
| §4.10.7 | 数据存储设计 | T-L2-002, T-BRF-001 | 已覆盖 |
| §4.10.8 | 非功能性设计 | T-OBS-001, T-BRF-001 | 已覆盖 |
| §4.11 | AlphaEar 技术指标与图表适配模块 | T-TECH-001, T-MIG-001 | 已覆盖 |
| §4.11.1 | 职责与边界 | T-TECH-001, T-MIG-001 | 已覆盖 |
| §4.11.2 | 核心数据结构 | T-PACK-001, T-TECH-001 | 已覆盖 |
| §4.11.3 | 接口定义 | T-TECH-001 | 已覆盖 |
| §4.11.4 | 核心算法与业务流程 | T-TECH-001 | 已覆盖 |
| §4.11.5 | 状态机 | T-TECH-001 | 已覆盖 |
| §4.11.6 | 错误处理策略 | T-TECH-001, T-MKT-002 | 已覆盖 |
| §4.11.7 | 数据存储设计 | T-TECH-001, T-L2-002 | 已覆盖 |
| §4.11.8 | 非功能性设计 | T-TECH-001, T-OBS-001 | 已覆盖 |
| §4.12 | 可观测性与运维模块 | T-OBS-001, T-OPS-001 | 已覆盖 |
| §4.12.1 | Health check | T-OPS-001 | 已覆盖 |
| §4.12.2 | Metrics 与阈值 | T-OBS-001 | 已覆盖 |
| §4.12.3 | 日志、span 与告警字段 | T-OBS-001 | 已覆盖 |
| §4.12.4 | 优雅停机、部署与回滚 | T-OPS-001 | 已覆盖 |
| §4.13 | Provider 外部调用参数合同 | T-PVD-004, T-PVD-005, T-PVD-006, T-PVD-007, T-PVD-008, T-MDB-001, T-L2-001 | 已覆盖 |
| §4.13.1 | AkShare | T-PVD-004, T-PVD-006, T-PVD-007, T-PVD-008 | 已覆盖 |
| §4.13.2 | EastMoney direct | T-PVD-005 | 已覆盖 |
| §4.13.3 | MongoDB | T-MDB-001, T-MDB-002, T-MDB-003 | 已覆盖 |
| §4.13.4 | OpenViking L2 | T-L2-001, T-L2-002 | 已覆盖 |
| §4.13.5 | 搜索增强源启用前合同 | T-PVD-001, T-PVD-003, T-NEWS-002, T-SOC-002 | 已覆盖 |
| §4.14 | 安全设计 | T-SEC-001, T-TOOL-002, T-BRF-001, T-EVAL-001 | 已覆盖 |
| §4.14.1 | 输入校验入口 | T-SEC-001, T-TOOL-002 | 已覆盖 |
| §4.14.2 | Secret 脱敏与敏感配置 | T-SEC-001, T-CFG-001, T-OBS-001 | 已覆盖 |
| §4.14.3 | API 错误响应边界 | T-TOOL-002, T-BRF-001, T-SEC-001 | 已覆盖 |
| §5 | 模块间交互设计 | T-TOOL-002, T-PVD-003, T-MDB-002, T-L2-002, T-TEST-004 | 已覆盖 |
| §5.1 | 正常流程 | T-MKT-002, T-NEWS-002, T-SOC-002, T-FND-002, T-TEST-004 | 已覆盖 |
| §5.2 | News runtime context 缺失 | T-TOOL-002, T-NEWS-002, T-TEST-003 | 已覆盖 |
| §5.3 | 同一 query cache upsert 并发竞争 | T-MDB-001, T-MDB-002, T-TEST-004 | 已覆盖 |
| §5.4 | L2 写入失败流程 | T-L2-002, T-PACK-002, T-TEST-001 | 已覆盖 |
| §6 | 数据库总体设计 | T-MDB-001, T-MDB-002, T-MDB-003, T-MIG-001 | 已覆盖 |
| §6.1 | ER / 关联关系 | T-MDB-001, T-MDB-003 | 已覆盖 |
| §6.2 | 跨模块一致性方案 | T-L2-002, T-MDB-002, T-PACK-002 | 已覆盖 |
| §6.3 | 数据迁移策略 | T-MIG-001 | 已覆盖 |
| §7 | 配置与环境管理 | T-CFG-001, T-SEC-001, T-OPS-001 | 已覆盖 |
| §7.1 | 可配置项清单 | T-CFG-001 | 已覆盖 |
| §7.2 | 环境差异处理 | T-CFG-001, T-OPS-001 | 已覆盖 |
| §7.3 | 敏感配置管理 | T-SEC-001, T-CFG-001, T-OBS-001 | 已覆盖 |
| §8 | 测试策略指引 | T-TEST-001, T-TEST-002, T-TEST-003, T-TEST-004, T-TEST-005, T-EVAL-001 | 已覆盖 |
| §8.1 | Unit tests | T-TEST-001, T-TEST-002 | 已覆盖 |
| §8.2 | Contract tests | T-TEST-003 | 已覆盖 |
| §8.3 | Integration tests | T-TEST-004 | 已覆盖 |
| §8.4 | Live gate | T-TEST-005 | 已覆盖 |
| §8.5 | Eval / report quality | T-EVAL-001 | 已覆盖 |
| §9 | 覆盖矩阵 | T-EVAL-001 | 已覆盖 |
| §10 | 主线决策闭合表 | T-PVD-001, T-CFG-001, T-MIG-001, T-EVAL-001 | 已覆盖 |
| §11 | 自检报告 | T-EVAL-001 | 已覆盖 |
| §11.1 | 名词/概念覆盖检查 | T-EVAL-001 | 已覆盖 |
| §11.2 | 禁用关键词检查 | T-EVAL-001 | 已覆盖 |
| §11.3 | 模块伪码/算法覆盖检查 | T-EVAL-001 | 已覆盖 |
| §11.4 | 数据结构字段级定义检查 | T-PACK-001, T-EVAL-001 | 已覆盖 |
| §11.5 | 自检清单 | T-EVAL-001 | 已覆盖 |

## 自检清单

- 已确认：详细设计文档中的每个模块都有对应任务。
- 已确认：任务描述未包含禁用替代实现词汇。
- 已确认：每个任务都有可客观判断的验收条件，且包含正常路径和异常路径。
- 已确认：每个任务预估工时均不超过 3 天。
- 已确认：依赖关系按层级展开，无循环依赖。
- 已确认：覆盖矩阵每一行均有任务编号，无空行。
- 已确认：任一开发者拿到单个任务后，可以根据前置依赖、交付物和验收条件开始实施。
