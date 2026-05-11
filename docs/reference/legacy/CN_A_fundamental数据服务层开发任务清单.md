# CN_A fundamental 数据服务层开发任务清单

来源总体设计：`docs/CN_A_fundamental数据服务层总体设计.md`

来源详细设计：`docs/CN_A_fundamental数据服务层详细设计.md`

生成日期：2026-05-07

## 1. 执行原则

### 1.1 目标

本清单把 CN_A fundamental 数据服务层 DLD 拆成可直接实施的原子任务。目标不是重新设计，而是让工程师按任务顺序完成实现后，可以用统一集成验收验证是否完整覆盖设计、达到预期功能、且没有偏离架构边界。

### 1.2 测试节奏

执行本清单时采用“先完整实施，最后统一集成测试”的节奏：

- 单个功能任务完成后只做代码自查、契约自查、偏离自查，不立即运行对应测试命令。
- 可以在实现过程中同步编写测试文件，但不在每个功能点完成后立刻跑测试。
- 所有功能任务完成后，统一执行第 5 节集成验收任务（T-FND-046、T-FND-047）。
- 如果统一集成测试失败，按失败根因回到对应任务修复，再重新跑集成验收。

### 1.3 不可偏离边界

- Python 数据服务只产出资料、字段来源、诊断和证据，不写投资结论、不写评级、不写目标价。
- worker 可见工具只允许 `fundamental_fundamentals_data_pack` 与 `openviking_write_material`。
- Tushare、AkShare、Baostock、FinanceMCP 原子接口不得暴露给 `fundamental_analyst`。
- MongoDB 是结构化缓存，不是 workflow 权威；OpenViking 保存运行证据和材料。
- control-plane hard gate 由 `claw-trade` 控制面执行，数据服务层只输出可判定输入。
- 不得用 mock、stub、fake、fallback success、硬编码成功、默认假数据绕过缺数据。
- Baostock 不进入 v1 主路径。

### 1.4 任务完成定义

每个实现任务完成时只标记以下状态，不运行测试：

- 代码落点完成。
- DLD 对应章节已实现。
- 输入、输出、错误码、诊断字段与 DLD 一致。
- 没有新增未批准 provider、工具、prompt fallback、Python 投资判断。
- 若发现 DLD 与实现上下文冲突，必须在任务备注中写明偏离并停止继续扩散。

## 2. 任务总览

任务总数：47 个。

| 模块 | 任务数 | 任务 ID | 覆盖设计 |
| --- | ---: | --- | --- |
| 基础边界与配置 | 9 | T-FND-001 至 T-FND-009 | DLD §2, §6 |
| MongoDB cache 与持久化 | 5 | T-FND-010 至 T-FND-013, T-FND-030 | DLD §4.3, §5 |
| Provider 规划与执行 | 13 | T-FND-014 至 T-FND-025, T-FND-042 | DLD §4.3, §4.4, §4.5 |
| 映射与 raw evidence | 4 | T-FND-026 至 T-FND-029 | DLD §4.6, §4.7 |
| 诊断与 DataPack 组装 | 9 | T-FND-031 至 T-FND-038, T-FND-043 | DLD §4.8, §4.9；HLD §12.1 |
| control-plane gate 与运行策略 | 4 | T-FND-039, T-FND-040, T-FND-044, T-FND-045 | DLD §4.10, §6.5, §6.7 |
| Prompt 边界与合同 | 1 | T-FND-041 | HLD §12.2, §18 |
| 统一集成验收 | 2 | T-FND-046, T-FND-047 | DLD §7；HLD §16 |

关键路径：

`T-FND-001 -> T-FND-002 -> T-FND-004 -> T-FND-006 -> T-FND-010 -> T-FND-013 -> T-FND-016 -> T-FND-021 -> T-FND-025 -> T-FND-042 -> T-FND-026 -> T-FND-028 -> T-FND-031 -> T-FND-032 -> T-FND-043 -> T-FND-034 -> T-FND-037 -> T-FND-039 -> T-FND-040 -> T-FND-041 -> T-FND-044 -> T-FND-045 -> T-FND-046 -> T-FND-047`

## 3. 覆盖矩阵

| DLD 章节 | 设计内容 | 任务 ID | 覆盖状态目标 |
| --- | --- | --- | --- |
| §2.1 | 架构边界 | T-FND-001, T-FND-039 | 实现与 gate 输入校验 |
| §2.2 | pymongo 与 tushare 依赖 | T-FND-002 | 依赖文件同步 |
| §2.3 | 运行前置输入 | T-FND-003 | 配置加载与校验 |
| §3 | HLD 覆盖状态 | T-FND-046 | 最终覆盖审查 |
| §4.1 | worker tool surface | T-FND-004, T-FND-005, T-FND-039 | 工具集合精确匹配 |
| §4.2 | Data service orchestrator | T-FND-006, T-FND-037, T-FND-042 | 主流程串联 |
| §4.3 | deterministic provider planner | T-FND-014, T-FND-015, T-FND-016, T-FND-042 | 固定路由与白名单 |
| §4.4 | Tushare fetcher | T-FND-017 至 T-FND-021 | 主源可调用、可诊断 |
| §4.5 | AkShare fetcher | T-FND-022 至 T-FND-025 | 补充源可调用、可诊断 |
| §4.6 | Field Mapping | T-FND-026, T-FND-027 | 字段级来源与冲突 |
| §4.7 | RawPayloadWriter | T-FND-028 至 T-FND-030 | OpenViking L2 raw 证据 |
| §4.8 | 阈值、缺口、诊断 | T-FND-031, T-FND-032, T-FND-043 | quality 状态机 |
| §4.9 | Structured DataPack | T-FND-033 至 T-FND-038, T-FND-043 | 完整 pack schema |
| §4.10 | control-plane claim gate | T-FND-039, T-FND-040, T-FND-044, T-FND-045 | 039/040 产出 gate 输入，044 统一 GateOutcome 出口 |
| §5 | MongoDB DDL、索引、TTL | T-FND-010 至 T-FND-013 | 可建集合、可幂等写 |
| §6 | 安全与运维 | T-FND-007 至 T-FND-009, T-FND-012, T-FND-045 | secret、Mongo URI/auth、health、metrics |
| §7 | 验收说明 | T-FND-046, T-FND-047 | 最终统一验收 |
| HLD §12.1 | 完整基本面最低字段 + 多期趋势 | T-FND-032, T-FND-043, T-FND-047 | 质量门槛与能力降级一致 |
| HLD §12.2 | 缺数降级与评级禁写 | T-FND-041, T-FND-040, T-FND-047 | prompt、guard、验收三层闭环 |
| HLD §16 | 五类最小验收样本 + CN 对照 | T-FND-046, T-FND-047 | 同层证据齐全且可复现 |
| HLD §18 | Phase 0 边界闭合 | T-FND-041, T-FND-043 | 缺数降级与状态机落地 |

## 4. 实施任务

### T-FND-001

任务标题：建立 fundamental 数据服务层实现边界守卫

对应设计：DLD §2.1，HLD §3、§5、§6

前置依赖：无

任务描述：在实现入口和模块说明中固定 fundamental 数据服务层职责，防止后续任务把投资结论、评级、目标价或 workflow hard gate 写进数据服务。

伪码步骤：

```text
DEFINE module boundary constants:
  WORKER_ID = "fundamental_analyst"
  MARKET = "CN_A"
  APPROVED_VISIBLE_TOOLS = {"fundamental_fundamentals_data_pack", "openviking_write_material"}
  FORBIDDEN_PROVIDER_TOOLS = {"tushare.*", "akshare.*", "baostock.*", "FinanceMCP.*"}

DEFINE assert_data_service_boundary(operation):
  IF operation writes rating OR target_price OR buy_hold_sell:
    RAISE FND_BOUNDARY_INVESTMENT_CONCLUSION_FORBIDDEN
  IF operation approves artifact OR rejects workflow:
    RAISE FND_BOUNDARY_CONTROL_GATE_FORBIDDEN
  RETURN ok
```

交付物：

- fundamental 数据服务目录骨架。
- 边界常量与错误码定义。
- 模块 README 或 `SKILL.md` 中的边界说明。

偏离检查：

- 不得新增 Python 报告正文生成逻辑。
- 不得新增 PM rating 或投资建议字段。
- 不得把 control-plane gate 结果写成数据服务内部成功/失败裁决。

### T-FND-002

任务标题：同步 fundamental 依赖与锁文件

对应设计：DLD §2.2

前置依赖：T-FND-001

任务描述：将 DLD 固定依赖写入项目依赖文件，并保持锁文件一致。

伪码步骤：

```text
OPEN pyproject dependency section
ADD pymongo >=4.10,<5
ADD tushare >=1.4.24,<2
KEEP existing akshare dependency if present
RUN lock refresh command only once after dependency edits
VERIFY lock file contains pymongo and tushare resolved versions
```

交付物：

- `pyproject.toml`
- 锁文件

偏离检查：

- 不升级无关依赖。
- 不把 FinanceMCP、Baostock 或未批准 MCP 服务加入 v1 依赖。

### T-FND-003

任务标题：实现运行配置加载与 secret 校验

对应设计：DLD §2.3，§6.1，§6.2，§6.7

前置依赖：T-FND-002

任务描述：实现 `load_fundamental_data_config(env)`，读取 Tushare、MongoDB、OpenViking 与禁用开关配置。配置加载只从环境或 secret 注入读取，不写本地 secret 文件。

伪码步骤：

```text
READ env:
  TUSHARE_TOKEN
  CN_A_MONGODB_URI
  CN_A_MONGODB_DATABASE
  CN_A_MONGODB_CACHE_COLLECTION
  OPENVIKING_ENDPOINT
  OPENVIKING_API_KEY
  OPENVIKING_WORKSPACE
  CN_A_FUNDAMENTAL_TOOL_ENABLED
  CN_A_FUNDAMENTAL_DISABLE_TUSHARE
  CN_A_FUNDAMENTAL_DISABLE_AKSHARE
  CN_A_FUNDAMENTAL_CACHE_READ_ONLY

VALIDATE:
  mongo uri present when cache enabled
  mongodb URI scheme is mongodb or mongodb+srv
  authSource explicit when URI contains username/password
  openviking endpoint/api key/workspace present when raw write enabled
  disabled provider returns config flag, not fake success

RETURN FundamentalDataConfig
```

交付物：

- `config.py`
- `FundamentalDataConfig`
- 配置错误码枚举

偏离检查：

- 不打印 token、密码、连接串。
- `CN_A_FUNDAMENTAL_DISABLE_TUSHARE=true` 只能产生明确缺口，不能用 AkShare 或本地默认值冒充主源成功。

### T-FND-004

任务标题：配置 fundamental worker 的 stage policy 与 skill manifest

对应设计：DLD §4.1，HLD §6

前置依赖：T-FND-001

任务描述：配置 `fundamental_analyst` 在 CN_A frontline 阶段只挂载 fundamental data pack 入口与 OpenViking 写材料工具，并注册 `cn-a-fundamental-data` skill。

伪码步骤：

```text
OPEN agents/fundamental_analyst/STAGES.yaml
SET CN_A frontline visible tools exactly:
  - fundamental_fundamentals_data_pack
  - openviking_write_material

OPEN agents/fundamental_analyst/skills/manifest.yaml
REGISTER cn-a-fundamental-data skill path
DO NOT register tushare.*, akshare.*, baostock.*, FinanceMCP.*
```

交付物：

- `agents/fundamental_analyst/STAGES.yaml`
- `agents/fundamental_analyst/skills/manifest.yaml`
- `agents/fundamental_analyst/skills/cn-a-fundamental-data/SKILL.md`

偏离检查：

- 工具集合必须精确匹配，不允许多一个诊断工具。
- HK/US/CRYPTO profile 不得静默复用 CN_A data skill。

### T-FND-005

任务标题：实现 visible tool policy 解析

对应设计：DLD §4.1.1，§4.1.2，§4.10.4

前置依赖：T-FND-004

任务描述：实现 `resolve_fundamental_visible_tools(worker_id, market_profile)` 与 visible tools 快照校验。

伪码步骤：

```text
FUNCTION resolve_fundamental_visible_tools(worker_id, market_profile):
  IF worker_id != "fundamental_analyst":
    RAISE TOOL_POLICY_WORKER_MISMATCH
  IF market_profile != "CN_A":
    RAISE TOOL_POLICY_PROFILE_MISMATCH
  RETURN VisibleToolPolicy(
    worker_id="fundamental_analyst",
    market_profile="CN_A",
    tool_names=("fundamental_fundamentals_data_pack", "openviking_write_material")
  )

FUNCTION is_visible_tool_snapshot_invalid_v1(snapshot):
  RETURN snapshot.worker_id != "fundamental_analyst"
      OR snapshot.market_profile != "CN_A"
      OR set(snapshot.tools) != APPROVED_VISIBLE_TOOLS
```

交付物：

- policy 模块
- visible tool snapshot 校验函数

偏离检查：

- 不读取 LLM prompt 推断工具权限。
- 不把 provider 原子工具作为临时例外暴露给 worker。

### T-FND-006

任务标题：定义核心数据结构与数据服务主入口

对应设计：DLD §4.2.1，§4.2.2

前置依赖：T-FND-003，T-FND-005

任务描述：实现 `DataPackRequest`、`NormalizedInput`、`ProviderAttempt`、`ProviderResult` 等核心数据结构，并建立 `BuildCnAFundamentalPack(request)` 主流程壳。此任务只串接接口，不实现 provider 细节。

伪码步骤：

```text
DEFINE PackStatus and ProviderStatus Literals
DEFINE dataclasses:
  DataPackRequest
  NormalizedInput
  ProviderAttempt
  ApiCallSpec
  ProviderResult

FUNCTION BuildCnAFundamentalPack(request):
  normalized = NormalizeInput(request)
  cache_result = MongoCacheInspector.Inspect(normalized)
  plan = BuildProviderPlan(normalized, cache_result)
  provider_results = ExecuteProviderPlan(plan, normalized)
  mapped = FieldSourceMapper.Map(cache_result, provider_results)
  diagnostics = MissingFieldDiagnostics.Compute(mapped, provider_results)
  pack = DataPackBuilder.build(...)
  RETURN pack
```

交付物：

- `models.py`
- `fundamental_data_pack.py`
- 主入口函数

偏离检查：

- 主入口不得调用 LLM。
- 主入口不得生成报告正文。
- 主入口不得在缺字段时返回假成功。

### T-FND-007

任务标题：实现统一脱敏函数

对应设计：DLD §4.4.5，§6.3

前置依赖：T-FND-003

任务描述：实现 `sanitize_error(message)`，用于 provider、MongoDB、OpenViking、配置错误的日志与 pack error 输出。

伪码步骤：

```text
FUNCTION sanitize_error(message):
  text = stringify(message)
  REPLACE sensitive key values where key matches token|secret|password|passwd|api_key|authorization|cookie|session
  REPLACE alnum runs length >= 20 with first4 + "***" + last2
  REPLACE sensitive URL query parameter values with "***"
  TRUNCATE to 1000 chars
  RETURN text
```

交付物：

- `security.py`
- provider/cache/writer 可复用脱敏函数

偏离检查：

- 不把原始异常字符串直接写入 pack。
- 不在日志中输出 `TUSHARE_TOKEN`、`OPENVIKING_API_KEY`、MongoDB 密码。

### T-FND-008

任务标题：实现 readiness 与 liveness 检查

对应设计：DLD §6.4

前置依赖：T-FND-003

任务描述：实现 fundamental 数据服务健康检查。liveness 只看进程和配置解析；readiness 检查 MongoDB、OpenViking 与 Tushare token 存在性。

伪码步骤：

```text
FUNCTION liveness():
  RETURN ok if config parsed

FUNCTION readiness():
  mongo_ok = MongoDB.ping(timeout_ms=500)
  openviking_ok = OpenViking.health(timeout_ms=1000)
  token_ok = config.tushare_token is not empty
  RETURN status with per-dependency details
```

交付物：

- health 模块
- readiness 结果结构

偏离检查：

- readiness 失败不得改写 data pack 为成功。
- token 检查只判断存在性，不把 token 打到日志。

### T-FND-009

任务标题：定义 metrics 与审计日志口径

对应设计：DLD §6.5，§6.6，§6.7

前置依赖：T-FND-006，T-FND-007

任务描述：为 pack quality、provider attempts、raw payload writer、gate fail 定义统一指标标签、固定告警阈值和审计日志字段，并把阈值常量化到代码配置。

伪码步骤：

```text
DEFINE metric labels:
  worker_id, market, provider, api_name, status, quality_status

ON provider attempt complete:
  increment fundamental_provider_attempt_total(labels)
  observe duration_ms

ON pack built:
  increment fundamental_pack_quality_status_total

ON raw write complete:
  increment fundamental_raw_payload_write_total

DEFINE alert threshold constants:
  failed_ratio_5m > 5%
  missing_token_15m > 0
  raw_write_error_5m > 0
  unsupported_claim_10m continuous increase

EMIT alert events with reason and run_id/dispatch_id only

ON shutdown:
  stop accepting new requests
  wait max 30s
  write unfinished requests as failed diagnostics
  close mongo pool
  flush metrics and audit logs
```

交付物：

- metrics helper
- alert threshold constants 与触发函数
- audit log schema
- graceful shutdown hook

偏离检查：

- metrics label 不得包含 ticker、token、连接串、raw payload。
- 告警阈值不得使用“人工口头约定”，必须可在代码中定位。
- graceful shutdown 不得吞掉未完成请求，必须写失败诊断。

### T-FND-010

任务标题：创建 MongoDB collection 与 JSON schema validator

对应设计：DLD §5.1

前置依赖：T-FND-003

任务描述：实现 `ensure_fundamental_cache_collection(db)`，按 DLD 创建 `cn_a_fundamental_cache` 集合与 JSON schema validator。

伪码步骤：

```text
FUNCTION ensure_fundamental_cache_collection(db):
  IF collection not exists:
    createCollection("cn_a_fundamental_cache", validator=json_schema)
  ELSE:
    collMod validator to expected schema if drift detected

REQUIRED fields:
  ticker, market, provider, api_name, fetched_at,
  schema_version, payload_hash, raw_payload_ref,
  metric_definition_version, fields, created_at, updated_at
```

交付物：

- `cache_schema.py`
- collection 初始化函数

偏离检查：

- `ticker` pattern 必须是 `^[0-9]{6}\\.(SH|SZ)$`。
- `provider` 只允许 `tushare`、`akshare`。
- `schema_version` 固定 `cn_a_fundamental_pack.v1`。

### T-FND-011

任务标题：创建 MongoDB 索引

对应设计：DLD §5.2

前置依赖：T-FND-010

任务描述：实现 `ensure_fundamental_cache_indexes(collection)`，创建 DLD 规定的四个索引。

伪码步骤：

```text
CREATE idx_ticker_api_period on:
  ticker, market, api_name, report_period desc, as_of desc

CREATE unique uk_provider_api_payload on:
  provider, api_name, ticker, report_period, announce_date, as_of, payload_hash

CREATE idx_payload_hash on payload_hash
CREATE ttl_expire_at on expires_at expireAfterSeconds=0
```

交付物：

- MongoDB 索引初始化函数

偏离检查：

- 唯一索引必须用于幂等写入。
- TTL 索引只能作用于 `expires_at`。

### T-FND-012

任务标题：实现 MongoDB 连接池与鉴权配置

对应设计：DLD §6.2

前置依赖：T-FND-003，T-FND-010

任务描述：使用 `pymongo` 创建 MongoDB client，校验 URI、鉴权参数、连接池和超时参数。

伪码步骤：

```text
FUNCTION create_mongo_client(config):
  ASSERT config.uri scheme is mongodb or mongodb+srv
  IF config.uri contains username/password:
    ASSERT authSource configured
  RETURN MongoClient(
    uri,
    maxPoolSize=10,
    minPoolSize=1,
    serverSelectionTimeoutMS=3000,
    connectTimeoutMS=3000
  )
```

交付物：

- MongoDB client factory

偏离检查：

- 不允许将 URI 明文写日志。
- 带用户名/密码的 MongoDB URI 必须显式配置 `authSource`。

### T-FND-013

任务标题：实现 MongoDB cache inspect 与 upsert

对应设计：DLD §4.3.4，§5.1-§5.3，HLD §8

前置依赖：T-FND-010，T-FND-011，T-FND-012

任务描述：实现缓存读取体检和幂等写入。cache hit 只能返回可复用字段，不能直接推出 `ok=true`。

伪码步骤：

```text
FUNCTION Inspect(normalized):
  records = query by ticker, market, api_name order report_period/as_of desc
  FOR each record:
    REQUIRE provider, api_name, report_period, announce_date, fetched_at,
            schema_version, payload_hash, raw_payload_ref exist or explicit null per DLD
    IF schema_version mismatch: mark schema_invalid
    IF raw_payload_ref or payload_hash missing: mark untrusted
    IF provider/api_name not approved list: mark untrusted
    IF unit or scale missing for numeric field: mark untrusted
    IF price_context or valuation expired: mark stale
    IF report_period version conflict:
      keep candidates sorted by report_period desc, announce_date desc, fetched_at desc
      choose one version only when unit/scale and metric_definition_version compatible
    ELSE add reusable field with field_source
  RETURN CacheInspectionResult(attempt, reusable_fields, diagnostics)

FUNCTION Upsert(provider_result):
  IF CACHE_READ_ONLY: return skipped diagnostic
  FOR each raw-written provider payload:
    build record with provider/api_name/report_period/announce_date/as_of/fetched_at
    build record with payload_hash/raw_payload_ref/schema_version/fields/unit/scale
    set expires_at only for price_context/valuation
    upsert by uk_provider_api_payload key
```

交付物：

- `cache.py`
- `CacheInspectionResult`
- cache upsert 函数

偏离检查：

- cache miss/stale/schema_invalid 必须进入 `provider_attempts` 或 `diagnostic_flags`。
- stale/invalid/untrusted 记录不得进入 `facts`，最多进入候选冲突诊断。
- 不同 provider 同字段不得覆盖；交给 FieldSourceMapper 冲突策略处理。

### T-FND-014

任务标题：实现 provider API 白名单常量

对应设计：DLD §4.3.2，§4.3.3，HLD §7.2

前置依赖：T-FND-006

任务描述：定义 Tushare 与 AkShare v1 白名单，包括 api_name、参数模板、timeout、retry、字段族。Baostock 不进入白名单。

伪码步骤：

```text
DEFINE TUSHARE_V1_SPECS:
  stock_company, stock_basic, daily_basic, fina_indicator,
  income, balancesheet, cashflow, fina_mainbz, dividend,
  top10_holders, top10_floatholders

DEFINE AKSHARE_V1_SPECS:
  stock_individual_info_em, stock_zh_a_spot_em, stock_zh_a_hist,
  stock_financial_abstract_ths, stock_history_dividend_detail

ASSERT no baostock spec
ASSERT no FinanceMCP spec
```

交付物：

- `provider_specs.py`

偏离检查：

- 不允许从 worker 输入动态选择任意 `api_name`。
- 不允许引入 health router。

### T-FND-015

任务标题：实现 CN_A ticker、日期与报告期规范化

对应设计：DLD §4.2.1，§4.3.2，§4.3.3

前置依赖：T-FND-006

任务描述：实现 `NormalizeInput(request)`，将输入转换为 `canonical_code`、`tushare_code`、`akshare_symbol`、交易所、日期窗口和 latest report period。

伪码步骤：

```text
FUNCTION NormalizeInput(request):
  VALIDATE worker_id == "fundamental_analyst"
  VALIDATE market == "CN_A"
  PARSE ticker:
    600519 -> 600519.SH if prefix 6/5/9
    000001 -> 000001.SZ if prefix 0/1/2/3
    600519.SH -> canonical
    SH600519 -> canonical
  SET tushare_code = "600519.SH"
  SET akshare_symbol = "600519"
  RESOLVE start_date/end_date/current_date
  RESOLVE latest_report_period from current_date
  RETURN NormalizedInput
```

交付物：

- `profile.py`
- input validation error codes

偏离检查：

- 不支持的 market 必须失败，不得 fallback 到 CN_A。
- 日期逆序必须失败。

### T-FND-016

任务标题：实现 deterministic provider plan

对应设计：DLD §4.3.1-§4.3.4，HLD §7.2

前置依赖：T-FND-013，T-FND-014，T-FND-015

任务描述：实现 `BuildProviderPlan(normalized, cache_result)` 的第一阶段计划：固定顺序为 Mongo inspect、全量 Tushare；AkShare 仅放入“静态未覆盖字段族”。所有“因 Tushare 失败触发的 AkShare 补充”必须由后续基于真实执行结果的二阶段任务处理，不允许只看 plan 推断。

伪码步骤：

```text
FUNCTION BuildProviderPlan(normalized, cache_result):
  phase1_calls = []
  IF TUSHARE disabled:
    add diagnostic missing primary source
  ELSE:
    FOR spec in TUSHARE_V1_SPECS:
      phase1_calls.append(render_spec(spec, normalized))

  FOR spec in AKSHARE_V1_SPECS:
    IF field_family not covered by TUSHARE_V1_SPECS:
      phase1_calls.append(render_spec(spec, normalized))

  ASSERT all phase1_calls have provider, api_name, required, field_family, parameters, timeout_ms, retry_limit
  RETURN ProviderPlan(phase1_calls, requires_runtime_phase2=true)
```

交付物：

- `planner.py`
- rendered `ApiCallSpec`

偏离检查：

- 不根据 LLM 指令改变 provider 顺序。
- 不把 cache hit 当成整体成功。
- 不得以“tushare plan 覆盖”替代“tushare result 覆盖”。

### T-FND-017

任务标题：实现 Tushare SDK 分发调用

对应设计：DLD §4.4.1，§4.4.2

前置依赖：T-FND-003，T-FND-014

任务描述：实现 `invoke_tushare_api_by_dispatch` 与 timeout/retry 包装，只允许调用白名单 api。

伪码步骤：

```text
FUNCTION invoke_tushare_api_by_dispatch(token, api_name, parameters, timeout_ms):
  IF api_name not in TUSHARE_V1_SPECS:
    RAISE FND_TUSHARE_API_NOT_APPROVED
  pro = ts.pro_api(token)
  fn = getattr(pro, api_name)
  CALL fn(**parameters) with timeout
  RETURN dataframe
```

交付物：

- `tushare_fetcher.py`
- approved dispatch 函数

偏离检查：

- 不允许任意 `getattr(pro, user_input)` 绕过白名单。
- 不把 token 缺失包装成空表成功。

### T-FND-018

任务标题：实现 Tushare 返回列校验

对应设计：DLD §4.4.3，§4.4.4

前置依赖：T-FND-017

任务描述：按 DLD 列出每个 Tushare api 的必需返回列，并实现 `ValidateTushareColumns`。

伪码步骤：

```text
DEFINE TUSHARE_EXPECTED_COLUMNS by api_name

FUNCTION ValidateTushareColumns(api_name, columns):
  missing = expected[api_name] - set(columns)
  IF missing not empty:
    RETURN schema_invalid with missing columns
  RETURN ok
```

交付物：

- Tushare expected columns 常量
- schema changed 判定

偏离检查：

- 缺列必须是 `schema_invalid/schema_changed`，不得继续映射。
- 空表与缺列必须区分。

### T-FND-019

任务标题：实现 Tushare fetcher 错误分类与 attempt 填充

对应设计：DLD §4.4.4，§4.4.5，§4.4.6

前置依赖：T-FND-017，T-FND-018，T-FND-007

任务描述：实现 `TushareFetcher.Fetch` 的错误路径、重试、脱敏和 `ProviderAttempt` 填充。

伪码步骤：

```text
FUNCTION TushareFetcher.Fetch(input_data, spec):
  started = now_iso()
  IF token missing:
    RETURN ProviderResult(attempt status=skipped reason=missing_token)
  TRY:
    df = dispatch call with retry_limit=spec.retry_limit
  CATCH Timeout:
    RETURN attempt status=timeout reason=timeout
  CATCH PermissionOrQuota:
    RETURN attempt status=error reason=permission_or_quota
  CATCH Exception err:
    RETURN attempt status=error reason=provider_error message=sanitize_error(err)
  IF df.empty:
    RETURN attempt status=empty reason=empty_response
  IF columns invalid:
    RETURN attempt status=schema_invalid reason=schema_changed
  CONTINUE to raw write and mapping
```

交付物：

- `TushareFetcher`
- Tushare provider attempt builder

偏离检查：

- 每个失败 attempt 必须有 started_at、ended_at、duration_ms、retry_count。
- request params 不得包含 token。

### T-FND-020

任务标题：实现 Tushare 字段抽取

对应设计：DLD §4.4.3，§4.6.2

前置依赖：T-FND-018，T-FND-026

任务描述：实现 `map_tushare_columns_to_pack_fields_v1(api_name, dataframe, ref_hash, ref_uri)`，输出字段路径、值、字段来源。

伪码步骤：

```text
FUNCTION map_tushare_columns_to_pack_fields_v1(api_name, dataframe, ref_hash, ref_uri):
  rows = normalize dataframe records
  mapping_rules = rules where provider="tushare" and api_name matches
  FOR each rule:
    value = select source_column from latest relevant row
    IF value present:
      emit ExtractedField(
        field_path=rule.pack_field_path,
        value=value,
        unit_scale=rule.unit_scale,
        provider="tushare",
        api_name=api_name,
        payload_hash=ref_hash,
        raw_payload_ref=ref_uri
      )
  RETURN extracted fields
```

交付物：

- Tushare mapper

偏离检查：

- 非空字段没有 `field_sources` 不得进入 facts。
- 单位换算必须记录在字段来源。

### T-FND-021

任务标题：贯通 Tushare raw 写入与 ProviderResult 成功路径

对应设计：DLD §4.4.2，§4.4.6，§4.7

前置依赖：T-FND-019，T-FND-020，T-FND-028

任务描述：在 Tushare 成功取得 dataframe 后写 OpenViking raw payload，回填 `attempt_seq`、`content_hash`、`raw_payload_ref`，再返回 `ProviderResult`。

伪码步骤：

```text
planned_seq = reserve_provider_attempt_seq_v1(run_id, dispatch_id, "tushare", spec.api_name)
raw_write = write_raw_payload_to_openviking(
  provider="tushare",
  api_name=spec.api_name,
  attempt_seq=planned_seq,
  payload=df.to_dict("records"),
  timeout_ms=5000,
  retry_limit=1
)
IF raw_write.ok is false:
  RETURN ProviderResult status=error reason=raw_payload_write_failed
fields = map_tushare_columns_to_pack_fields_v1(spec.api_name, df, raw_write.content_hash, raw_write.uri)
attempt = BuildProviderAttemptWithFinalSeq(spec, started, raw_write.attempt_seq)
RETURN ProviderResult(attempt, fields, raw_write refs)
```

交付物：

- Tushare 成功路径完整实现

偏离检查：

- raw 写入失败时字段不得进入 facts 或 Mongo cache。
- `ProviderAttempt.attempt_seq` 必须等于 raw URI 尾号。

### T-FND-022

任务标题：实现 AkShare SDK 分发调用

对应设计：DLD §4.5.1，§4.5.4

前置依赖：T-FND-014

任务描述：实现 `invoke_akshare_api_by_dispatch`，仅允许 AkShare v1 白名单接口。

伪码步骤：

```text
FUNCTION invoke_akshare_api_by_dispatch(api_name, parameters, timeout_ms):
  IF api_name not in AKSHARE_V1_SPECS:
    RAISE FND_AKSHARE_API_NOT_APPROVED
  fn = getattr(ak, api_name)
  CALL fn(**parameters) with timeout
  RETURN dataframe
```

交付物：

- `akshare_fetcher.py`

偏离检查：

- 不允许 AkShare 任意接口透传。
- 不允许 AkShare 覆盖 Tushare 成功且口径明确的核心财务字段。

### T-FND-023

任务标题：实现 AkShare 返回列校验

对应设计：DLD §4.5.2

前置依赖：T-FND-022

任务描述：按 DLD 预期列校验 `stock_individual_info_em`、`stock_zh_a_spot_em`、`stock_zh_a_hist`、`stock_financial_abstract_ths`、`stock_history_dividend_detail`。

伪码步骤：

```text
DEFINE AKSHARE_EXPECTED_COLUMNS by api_name

FUNCTION ValidateAkShareColumns(api_name, columns, target_code):
  IF expected columns missing:
    RETURN schema_invalid reason=schema_changed
  IF api_name == stock_zh_a_spot_em AND target_code not in column "代码":
    RETURN empty reason=empty_response
  RETURN ok
```

交付物：

- AkShare expected columns 常量
- schema changed 与 empty response 判定

偏离检查：

- 全市场 spot 返回后必须按目标代码过滤。
- 缺列和无目标代码不得混为同一错误。

### T-FND-024

任务标题：实现 AkShare 字段抽取

对应设计：DLD §4.5.3，§4.6.2

前置依赖：T-FND-023，T-FND-026

任务描述：实现 `map_akshare_columns_to_pack_fields_v1`，覆盖 DLD 指定三类列级映射和单位口径。

伪码步骤：

```text
FUNCTION map_akshare_columns_to_pack_fields_v1(api_name, dataframe, ref_hash, ref_uri):
  mapping_rules = rules where provider="akshare" and api_name matches
  FOR each rule:
    value = select source_column from target row
    IF api_name == stock_zh_a_spot_em AND source_column == "总市值":
      unit_scale = "cny"
      do not convert to 10k_cny
    EMIT field with source_ref_path and raw refs
  RETURN extracted fields
```

交付物：

- AkShare mapper

偏离检查：

- `总市值` 按人民币元 `cny` 入库，不做万元换算。
- 与 Tushare 同字段冲突时不得覆盖。

### T-FND-025

任务标题：实现 AkShare fetcher 错误分类与成功路径

对应设计：DLD §4.5.4，§4.7

前置依赖：T-FND-022，T-FND-023，T-FND-024，T-FND-028

任务描述：实现 `AkShareFetcher.Fetch`，包括 timeout、empty、schema_invalid、permission_or_access、provider_error、raw 写入和映射。

伪码步骤：

```text
FUNCTION AkShareFetcher.Fetch(input_data, spec):
  started = now_iso()
  TRY:
    df = invoke_akshare_api_by_dispatch(spec.api_name, spec.parameters, spec.timeout_ms)
  CATCH Timeout:
    RETURN attempt status=timeout reason=timeout
  CATCH AccessDenied:
    RETURN attempt status=error reason=permission_or_access
  CATCH Exception err:
    RETURN attempt status=error reason=provider_error message=sanitize_error(err)
  VALIDATE columns and target row
  IF invalid or empty: RETURN corresponding attempt
  raw_write = write_raw_payload_to_openviking(...)
  IF raw_write failed: RETURN raw_payload_write_failed
  fields = map_akshare_columns_to_pack_fields_v1(...)
  RETURN ProviderResult
```

交付物：

- `AkShareFetcher`

偏离检查：

- AkShare 是 supplement，不得替代 Tushare 主源职责。
- raw 写入失败不得进入 facts。

### T-FND-026

任务标题：实现字段映射规则表

对应设计：DLD §4.6.1，§4.6.2

前置依赖：T-FND-006

任务描述：实现 `CN_A_FUNDAMENTAL_FIELD_MAPPING_V1`，每条规则固定 8 列。

伪码步骤：

```text
DEFINE FieldMappingRule(
  provider,
  api_name,
  source_column,
  pack_field_path,
  domain,
  unit_scale,
  source_ref_kind,
  source_ref_path
)

LOAD all DLD mapping rows
ASSERT len(rule) == 8 for every rule
ASSERT all source_ref_kind == "raw"
ASSERT every source_ref_path starts with "field_sources."
```

交付物：

- `field_mapping_v1.py`
- `FieldMappingRule`

偏离检查：

- 不得新增 DLD 未批准字段路径作为核心 facts。
- 不得用字符串拼接随意生成字段路径。

### T-FND-027

任务标题：实现 FieldSourceMapper 与跨源冲突策略

对应设计：DLD §4.6.3，HLD §7.4，§11

前置依赖：T-FND-013，T-FND-020，T-FND-024，T-FND-026

任务描述：把 cache 与 provider extracted fields 合并为 `facts` 与 `field_sources`。同字段多来源冲突时不覆盖，记录 `cross_provider_conflict`。

伪码步骤：

```text
FUNCTION FieldSourceMapper.Map(cache_result, provider_results):
  candidates = collect fields from cache and successful providers
  FOR each field_path:
    IF one trusted candidate:
      put into facts and field_sources
    ELSE IF multiple candidates same value and compatible unit:
      choose freshest but keep source refs
    ELSE IF conflict:
      remove field from facts
      add missing_fields reason=cross_provider_conflict
      add diagnostic_flags code=cross_provider_conflict
      reduce evidence_capability for affected domain
  RETURN mapped facts and sources
```

交付物：

- `field_source_mapper.py`

偏离检查：

- 不得静默选择一个冲突值。
- PE/PB/ROE、净利润、现金流等核心字段冲突必须降级。

### T-FND-028

任务标题：实现 OpenViking raw payload writer

对应设计：DLD §4.7.1-§4.7.4

前置依赖：T-FND-003，T-FND-007

任务描述：实现 `write_raw_payload_to_openviking(request)`，负责 canonical JSON、hash、URI、create-only 写入、timeout retry、冲突序号重试。

伪码步骤：

```text
FUNCTION write_raw_payload_to_openviking(request):
  started = now_iso()
  canonical_bytes = to_canonical_json_bytes(request.payload)
  content_hash = "sha256:" + sha256_hex(canonical_bytes)
  seq = allocate_raw_attempt_seq_v1(run_id, dispatch_id, provider, api_name, request.attempt_seq)
  uri = build uri with seq
  write_result = create_only_put(uri, body, timeout_ms, retry_limit=request.retry_limit)
  IF object_already_exists:
    seq = seq + 1
    retry_uri = build uri with seq
    retry_result = create_only_put(retry_uri, body, timeout_ms, retry_limit=request.retry_limit)
    IF object_already_exists:
      RETURN ok=false reason=raw_payload_write_conflict
  RETURN RawPayloadWriteResult(ok, uri, seq, content_hash, bytes_written, timings)
```

交付物：

- `raw_payload_writer.py`
- URI builder
- canonical JSON hash helper

偏离检查：

- 不允许覆盖已存在 raw evidence。
- 二次冲突不得返回伪成功。
- `attempt_seq` 必须与 URI 尾号一致。

### T-FND-029

任务标题：实现 raw attempt sequence 分配器

对应设计：DLD §4.7.2

前置依赖：T-FND-028

任务描述：实现 `reserve_provider_attempt_seq_v1` 与 `allocate_raw_attempt_seq_v1`，保证同一 `run_id + dispatch_id + provider + api_name` 下单调递增。

伪码步骤：

```text
FUNCTION reserve_provider_attempt_seq_v1(run_id, dispatch_id, provider, api_name):
  key = tuple(run_id, dispatch_id, provider, api_name)
  current = in_memory_or_store_counter.get(key, 0)
  next = current + 1
  store counter
  RETURN next

FUNCTION allocate_raw_attempt_seq_v1(..., expected_min_seq):
  seq = max(reserve_next_seq(key), expected_min_seq)
  RETURN seq
```

交付物：

- attempt sequence allocator

偏离检查：

- 不回退、不复用序号。
- 并发冲突仍由 OpenViking create-only 兜底，不靠本地计数假定成功。

### T-FND-030

任务标题：实现 raw evidence 到 Mongo cache 的写入门槛

对应设计：DLD §4.7.4，§5.3

前置依赖：T-FND-013，T-FND-028

任务描述：确保只有 raw payload 写入成功且字段映射成功的 provider result 才能进入 Mongo cache。

伪码步骤：

```text
FUNCTION maybe_write_cache(provider_result):
  IF provider_result.raw_payload_ref is None OR raw_payload_hash is None:
    RETURN skipped reason=raw_payload_missing
  IF provider_result.extracted_fields empty:
    RETURN skipped reason=no_mapped_fields
  IF cache_read_only:
    RETURN skipped reason=cache_read_only
  Upsert(provider_result)
```

交付物：

- cache write gate

偏离检查：

- raw 写入失败时不得写 MongoDB。
- MongoDB 写失败不得改写 provider 成功状态，只能追加 cache 写入诊断。

### T-FND-031

任务标题：实现 freshness checker 与缺口原因枚举

对应设计：DLD §4.8.1，§4.8.2，HLD §12

前置依赖：T-FND-027

任务描述：实现字段新鲜度检查、核心字段组阈值和缺口原因枚举。

伪码步骤：

```text
DEFINE missing reason enum:
  missing_token, empty_response, timeout, schema_changed,
  provider_error, cache_stale, cross_provider_conflict,
  field_source_missing

FUNCTION FreshnessChecker.Check(field_sources, current_date):
  FOR each field:
    IF domain in price_context/valuation AND as_of older than 7 days:
      stale_flag = true
    IF financial report stale for current report use:
      mark stale but keep raw ref
  RETURN freshness map
```

交付物：

- `diagnostics.py`
- freshness map
- missing reason enum

偏离检查：

- 价格与估值新鲜度窗口默认 7 天。
- 财报 raw 引用不因 TTL 删除。

### T-FND-032

任务标题：实现 MissingFieldDiagnostics 与 quality 状态机

对应设计：DLD §4.8，§4.9.3，HLD §12、§15

前置依赖：T-FND-031

任务描述：根据核心字段门槛、field_sources、provider_attempts、freshness 计算 `missing_fields`、`diagnostic_flags`、`quality.status` 与 `ok`。

伪码步骤：

```text
FUNCTION ComputeQuality(mapped, freshness, provider_attempts):
  IF provider_attempts empty:
    RETURN failed reason=provider_attempts_missing
  IF any fact field has no field_source:
    RETURN failed reason=field_source_missing
  valuation_ok = count valid pe_ttm/pb/total_mv >= 2
  indicators_ok = count valid roe/roa/gross_margin/netprofit_margin/debt_to_assets >= 3
  statement_ok = income has 1 AND balance has 1 AND cashflow has 1
  IF not all core groups ok:
    RETURN ok=false status=failed
  IF missing business_segments OR dividend OR shareholders OR trend:
    RETURN ok=true status=partial
  RETURN ok=true status=ok
```

交付物：

- quality calculator
- missing fields builder

偏离检查：

- 合法组合只能是 `ok=false,status=failed`、`ok=true,status=partial`、`ok=true,status=ok`。
- `ok=true` 不代表 worker 报告通过。

### T-FND-033

任务标题：实现 DataPack schema dataclasses

对应设计：DLD §4.9.1，HLD §10

前置依赖：T-FND-006

任务描述：实现 DataPack 完整结构，包括 profile、query、facts、field_sources、provider_attempts、missing_fields、freshness、evidence_capabilities、diagnostic_flags、derived_summary、quality、evidence。

伪码步骤：

```text
DEFINE dataclasses:
  ProfileInfo
  QueryInfo
  ValuationFacts
  FinancialIndicatorsFacts
  IncomeStatementFacts
  BalanceSheetFacts
  CashFlowFacts
  Facts
  DataPackQuality
  DerivedSummaryItem
  DataPackEvidence
  DataPack

SET schema_version literal "cn_a_fundamental_pack.v1"
SET currency literal "CNY"
```

交付物：

- `pack_schema.py`

偏离检查：

- 不使用 `analysis_permissions` 字段。
- 不添加投资建议、评级、目标价字段。

### T-FND-034

任务标题：实现 evidence capability classifier

对应设计：DLD §4.2.2，§4.9.1，HLD §9、§10

前置依赖：T-FND-032，T-FND-033，T-FND-043

任务描述：根据事实字段、缺口、冲突、新鲜度生成 `evidence_capabilities`，只描述证据能力和禁写声明类型。

伪码步骤：

```text
FUNCTION EvidenceCapabilityClassifier.Classify(mapped, freshness, diagnostics):
  company_profile.status = available/partial/blocked
  financial_snapshot.status = available if core financial fields pass else blocked
  financial_trend.status = available only if comparable periods >= 2 and same口径
  valuation_snapshot.status = available if valuation threshold pass else blocked
  valuation_judgment.status = blocked
  target_price.status = blocked
  rating.status = blocked
  RETURN capabilities
```

交付物：

- `evidence_capabilities.py`

偏离检查：

- capability 不得回答“是否买入/持有/卖出”。
- 高估/低估、目标价、rating 必须 blocked。

### T-FND-035

任务标题：实现 DerivedSummaryBuilder

对应设计：DLD §4.9.4，HLD §13

前置依赖：T-FND-033，T-FND-034

任务描述：实现固定模板 `derived.summary`，最多 8 条，每条必须有可解析 source_ref，禁止投资结论和公司评价。

伪码步骤：

```text
TEMPLATE_WHITELIST = {
  financial_snapshot_obtained,
  valuation_snapshot_obtained,
  missing_core_field,
  provider_call_failed,
  cross_source_conflict
}

FUNCTION DerivedSummaryBuilder.Build(mapped, diagnostics):
  items = select facts and gaps by priority
  FOR each candidate:
    render text from approved template only
    attach source_ref
  VALIDATE len <= 8
  VALIDATE source_ref resolvable
  VALIDATE no forbidden words or claims
  IF validation fails:
    RETURN invalid marker for DataPackBuilder to fail pack
  RETURN items
```

交付物：

- `derived_summary.py`

偏离检查：

- 不调用 LLM。
- 不生成章节标题或报告正文。
- 命中“建议/买入/持有/卖出/目标价/高估/低估/护城河/行业龙头”等词必须失败。

### T-FND-036

任务标题：实现 DataPackBuilder 与 content hash

对应设计：DLD §4.9.2，§4.9.3

前置依赖：T-FND-032，T-FND-033，T-FND-034，T-FND-035

任务描述：组装完整 DataPack，计算 canonical hash，校验字段来源与状态组合。

伪码步骤：

```text
FUNCTION DataPackBuilder.build(...):
  pack_dict = compose_pack_payload_dict_v1(...)
  ASSERT all required top-level sections present
  ASSERT every non-null fact has field_sources same path
  ASSERT ok/status legal combo
  IF derived_summary invalid:
    set derived_summary = []
    add diagnostic flag derived_summary_invalid severity=fail
    set quality.status=failed
    set ok=false
  content_hash = sha256(canonical_json(pack_dict))
  RETURN typed DataPack with evidence.content_hash
```

交付物：

- `data_pack_builder.py`
- canonical pack hash helper

偏离检查：

- 不允许省略核心段。
- hash 必须基于稳定排序 JSON。

### T-FND-037

任务标题：贯通 BuildCnAFundamentalPack 全链路

对应设计：DLD §4.2.2，全 §4

前置依赖：T-FND-016，T-FND-021，T-FND-025，T-FND-027，T-FND-036，T-FND-042

任务描述：把 normalizer、cache、planner、fetchers、mapper、diagnostics、capabilities、summary、builder 串成一个真实资料包入口。

伪码步骤：

```text
FUNCTION BuildCnAFundamentalPack(request):
  assert_data_service_boundary("pack")
  normalized = NormalizeInput(request)
  cache_result = MongoCacheInspector.Inspect(normalized)
  phase1_plan = BuildProviderPlan(normalized, cache_result)
  tushare_results = execute phase1 tushare calls
  phase2_plan = BuildAkshareSupplementPlanFromTushareResult(tushare_results, cache_result, normalized)
  akshare_results = execute phase2 akshare calls
  provider_results = tushare_results + akshare_results
  FOR result in provider_results:
    maybe_write_cache(result)
  mapped = FieldSourceMapper.Map(cache_result, provider_results)
  freshness = FreshnessChecker.Check(mapped.field_sources, normalized.current_date)
  diagnostics = MissingFieldDiagnostics.Compute(mapped, freshness, provider_results)
  capabilities = EvidenceCapabilityClassifier.Classify(mapped, freshness, diagnostics)
  summary = DerivedSummaryBuilder.Build(mapped, diagnostics)
  RETURN DataPackBuilder.build(...)
```

交付物：

- 完整 `fundamental_fundamentals_data_pack` service function

偏离检查：

- provider 调用由资料包内部执行，不由 LLM 选择。
- service 返回 pack，不写 report。

### T-FND-038

任务标题：实现 OpenClaw skill 工具入口适配层

对应设计：DLD §4.1，§4.2，HLD §5、§6

前置依赖：T-FND-037

任务描述：实现 skill 脚本入口，将 OpenClaw tool JSON 转为 `DataPackRequest` 和 runtime context，调用 `BuildCnAFundamentalPack` 后原样返回 DataPack JSON。

伪码步骤：

```text
FUNCTION tool_entrypoint(payload, runtime_context):
  policy = resolve_fundamental_visible_tools(runtime_context.worker_id, payload.market)
  request = DataPackRequest(
    ticker=payload.ticker,
    start_date=payload.start_date,
    end_date=payload.end_date,
    current_date=runtime_context.current_date,
    run_id=runtime_context.run_id,
    dispatch_id=runtime_context.dispatch_id,
    worker_id=runtime_context.worker_id,
    market=payload.market
  )
  pack = BuildCnAFundamentalPack(request)
  RETURN serialize(pack)
```

交付物：

- `scripts/fundamental_data_pack.py`
- skill tool JSON adapter

偏离检查：

- adapter 不得改写 `quality.status`。
- adapter 不得补写结论文本。

### T-FND-039

任务标题：实现 control-plane visible tools 与 receipt gate

对应设计：DLD §4.10.3，§4.10.4，§4.10.5

前置依赖：T-FND-005，T-FND-038

任务描述：在 `claw-trade` 控制面 gate 适配层实现 OpenViking receipt 与 visible tools 快照校验。T-FND-039 只产出可供后续决策使用的 gate 输入（receipt/visible-tools 校验结果与 reason code），不在本任务声明 `reject/rerun/terminate/continue` 最终结果；唯一结果出口统一由 T-FND-044 决策。

伪码步骤：

```text
FUNCTION is_openviking_receipt_invalid_v1(receipt):
  IF missing file OR invalid json: RETURN true
  IF status != success: RETURN true
  IF required fields missing: RETURN true
  IF worker_id != fundamental_analyst: RETURN true
  IF content_hash mismatch: RETURN true
  RETURN false

FUNCTION build_material_gate_input(inputs):
  visible_tools_ok = NOT is_visible_tool_snapshot_invalid_v1(inputs.visible_tools_snapshot)
  receipt_ok = NOT is_openviking_receipt_invalid_v1(inputs.receipt)
  reason_codes = []
  IF NOT visible_tools_ok:
    reason_codes.append("visible_tools_invalid")
  IF NOT receipt_ok:
    reason_codes.append("openviking_receipt_invalid")
  RETURN MaterialGateInput(
    visible_tools_ok=visible_tools_ok,
    receipt_ok=receipt_ok,
    reason_codes=reason_codes
  )

FUNCTION build_fundamental_gate_inputs(inputs):
  material_gate_input = build_material_gate_input(inputs)
  claim_gate_input = build_claim_gate_input(inputs.report, inputs.pack)
  RETURN GateInputs(material_gate=material_gate_input, claim_gate=claim_gate_input)
```

交付物：

- control-plane gate input adapter（material_gate / claim_gate 输入）
- receipt validator integration

偏离检查：

- 不把 gate 放进数据服务层直接决定 workflow。
- T-FND-039 不得直接输出 `reject/rerun/terminate/continue`；最终结果只能在 T-FND-044 产生。
- receipt hash 不匹配必须在 gate input 中产出失败 reason code，供 T-FND-044 执行终态决策。

### T-FND-040

任务标题：实现 fundamental report claim gate

对应设计：DLD §4.10.1，§4.10.2，§4.10.5

前置依赖：T-FND-034，T-FND-039

任务描述：实现基于规则的 claim 抽取和 unsupported claim 判定，用 DataPack 的 facts、field_sources、evidence_capabilities 判定报告声明是否缺证据。

伪码步骤：

```text
FUNCTION parse_report_claims_by_rules_v1(report_text):
  extract metric claims by regex and keyword dictionary
  extract conclusion claims by fixed phrase dictionary
  extract narrative claims by phrase dictionary plus 30 char context
  RETURN claims

FUNCTION is_claim_unbacked_by_evidence_v1(claim, pack):
  IF required facts missing: RETURN true
  IF field_sources missing: RETURN true
  IF trend claim and financial_trend not available: RETURN true
  IF target_price/rating/overvalued/undervalued: RETURN true
  IF moat/industry leader and business_segments insufficient: RETURN true
  RETURN false

FUNCTION gate_report(report, pack):
  claims = parse_report_claims_by_rules_v1(report)
  unsupported = filter claims where is_claim_unbacked_by_evidence_v1
  IF unsupported not empty: reject artifact
```

交付物：

- claim parser
- unsupported claim gate

偏离检查：

- 正则 gate 不是让 Python 重写报告，只能拒绝或请求 rerun。
- 不得把 unsupported claim 降级为 warning 来通过。

### T-FND-041

任务标题：补齐 CN_A fundamental prompt 缺数降级规则与合同约束

对应设计：HLD §12.2，§18 Phase 0

前置依赖：T-FND-004，T-FND-005，T-FND-040

任务描述：在 `fundamental_analyst` 的 CN_A prompt 资产中补齐“核心数据缺失时允许明确写证据不足且不得给评级”的规则，并编写 prompt/guard 合同测试文件。此任务只编写与落地配置，不执行测试命令。

伪码步骤：

```text
OPEN CN_A fundamental prompt template/profile
ADD explicit downgrade clause:
  WHEN core financial/valuation evidence missing:
    must allow sentence "证据不足，无法给出基本面评级"
    must forbid buy/hold/sell/watch rating output

ADD prompt contract assertions fixture:
  assert downgrade clause exists exactly once
  assert no fallback-to-rating wording remains
  assert claim-gate dictionary can match "无法给出基本面评级" as compliant downgrade

WRITE/UPDATE contract test files only (no test execution in this task)
```

交付物：

- CN_A fundamental prompt 配置更新。
- prompt 合同测试文件与样例夹具（仅编写）。

偏离检查：

- 不得通过 prompt fallback 伪装“可评级”。
- 不得引入 Python 侧评级补写逻辑。

### T-FND-042

任务标题：实现基于 Tushare 实际结果的 AkShare 二阶段补充编排

对应设计：DLD §4.3.4，HLD §7.2，§14.2

前置依赖：T-FND-016，T-FND-021，T-FND-025

任务描述：实现 `BuildAkshareSupplementPlanFromTushareResult(...)` 或等效执行链路钩子，按 Tushare 实际执行结果（success/empty/error/timeout/schema_invalid）补充 AkShare 字段族，修复“只按 tushare plan 判断补充”的偏差。

伪码步骤：

```text
FUNCTION BuildAkshareSupplementPlanFromTushareResult(tushare_results, cache_result, normalized):
  unresolved_families = []
  FOR each tushare field_family result:
    IF result.status in {empty, error, timeout, schema_invalid, skipped}:
      unresolved_families.add(field_family)
    ELSE IF mapped fields still below family threshold:
      unresolved_families.add(field_family)

  phase2_calls = []
  FOR spec in AKSHARE_V1_SPECS:
    IF spec.field_family in unresolved_families OR spec.field_family not covered by tushare whitelist:
      phase2_calls.append(render_spec(spec, normalized))
  RETURN phase2_calls

IN orchestrator:
  run phase1 (tushare)
  build phase2 from actual tushare results
  run phase2 (akshare)
```

交付物：

- 二阶段补充计划函数或等效编排模块。
- orchestrator 调用点与调度日志字段（phase1/phase2）。

偏离检查：

- 不得仅根据 Tushare 白名单计划判断“已覆盖”。
- 不得把 phase2 缺失包装成主源成功。

### T-FND-043

任务标题：实现 HLD §12.1 完整字段门槛与多期趋势能力判定

对应设计：HLD §12.1，DLD §4.8.1，§4.9.3

前置依赖：T-FND-031，T-FND-032，T-FND-033

任务描述：把“完整基本面报告最低字段”与“趋势可写前提”单独固化为质量门槛校验器，并把不满足条件时的能力降级写入 `evidence_capabilities` 与 `missing_fields`。

伪码步骤：

```text
DEFINE required domains for complete report:
  company_profile, price_context, valuation, financial_indicators,
  income_statement, balance_sheet, cash_flow, business_segments,
  dividend, shareholders

FUNCTION EvaluateFundamentalCompleteness(mapped, freshness):
  FOR each required domain:
    mark pass/fail with missing reasons
  trend_ready = has comparable periods >=2
             AND annual comparable periods >=2
             AND no cross-provider metric conflict on trend fields

  IF any core domain failed:
    quality = failed
  ELSE IF trend_ready is false OR supplemental domains partial:
    quality = partial
  ELSE:
    quality = ok

  SET capabilities:
    financial_snapshot blocked unless all core financial domains pass
    valuation_snapshot blocked unless valuation threshold pass
    financial_trend blocked when trend_ready false
```

交付物：

- 完整字段门槛校验器。
- 趋势能力判定器与能力降级映射。

偏离检查：

- 不满足完整门槛时不得输出 `quality.status=ok`。
- 趋势能力不可用时必须 `financial_trend.status=blocked`。

### T-FND-044

任务标题：补齐 control-plane gate outcome：reject / rerun / terminate

对应设计：DLD §4.10.5

前置依赖：T-FND-039，T-FND-040

任务描述：在控制面 gate 适配层实现明确结果分发，不只“拒绝或继续”。必须可输出 `reject artifact`、`request rerun`、`terminate worker output` 三种结果，并记录 reason code。

伪码步骤：

```text
DEFINE GateOutcome = reject | rerun | terminate | continue

FUNCTION decide_gate_outcome(material_gate, claim_gate, retry_budget_state):
  IF material_gate.receipt_ok is false OR material_gate.visible_tools_ok is false:
    RETURN terminate reason=runtime_evidence_invalid
  IF unsupported_claims not empty AND retry_budget_state.can_retry:
    RETURN rerun reason=unsupported_claim_with_retry
  IF unsupported_claims not empty AND retry_budget exhausted:
    RETURN terminate reason=unsupported_claim_retry_exhausted
  IF hard data fabrication signal:
    RETURN reject reason=fabrication_or_untrusted_facts
  RETURN continue
```

交付物：

- gate outcome 枚举与决策函数。
- gate 审计日志 reason code 表。

偏离检查：

- 不得把 `terminate` 偷换成 `continue`。
- rerun 必须受控制面重试预算约束，不能无限重试。

### T-FND-045

任务标题：补齐禁用开关全链路行为、timeout 策略与 claim parser 词典资产

对应设计：DLD §4.10.1，§6.5，§6.7，HLD §16

前置依赖：T-FND-003，T-FND-009，T-FND-017，T-FND-022，T-FND-040

任务描述：把禁用开关、provider timeout 策略、claim parser 词典版本资产化为可执行任务，并保证开关触发时链路输出缺口而不是假成功。此任务只实现逻辑和测试文件编写，不执行测试命令。

伪码步骤：

```text
IMPLEMENT disable-switch behavior:
  CN_A_FUNDAMENTAL_TOOL_ENABLED=false -> tool entry hard fail
  CN_A_FUNDAMENTAL_DISABLE_TUSHARE=true -> skip tushare attempts + missing_token_or_disabled diagnostics
  CN_A_FUNDAMENTAL_DISABLE_AKSHARE=true -> skip akshare attempts + unresolved fields diagnostics
  CN_A_FUNDAMENTAL_CACHE_READ_ONLY=true -> inspect allowed, upsert skipped with reason

IMPLEMENT timeout policy constants:
  use spec.timeout_ms per api
  retry_limit per spec
  writer timeout=5000 retry=1
  timeout must emit attempt status=timeout with retry_count/duration

IMPLEMENT claim parser dictionaries as versioned assets:
  metric_keywords_v1
  conclusion_phrases_v1
  narrative_phrases_v1
  keep dictionary revision id in gate logs

WRITE contract tests for switches/timeouts/dictionaries (no execution in this task)
```

交付物：

- 开关全链路行为实现。
- timeout 策略常量与落地点。
- claim parser 词典文件与版本号。
- 对应合同测试文件（仅编写）。

偏离检查：

- 禁用开关不得触发 fallback success。
- timeout 不得吞掉失败并伪造 success。
- claim parser 词典不得在代码中散落硬编码。

## 5. 最终集成验收任务

### T-FND-046

任务标题：执行设计覆盖与偏离审查

对应设计：DLD §3，§7，全 HLD

前置依赖：T-FND-001 至 T-FND-045 全部完成

任务描述：在所有实现任务完成后，统一审查实现是否覆盖 DLD/HLD，并检查 HLD §16 五类验收样本与 TradingAgents-CN 同层对照材料是否已准备齐全（只审查覆盖，不执行测试命令）。
`T-FND-046` 不是“实施完成”或“验收通过”结论，只是进入 `T-FND-047` 的前置审查关口。

伪码步骤：

```text
FOR each DLD section in coverage matrix:
  FIND implementation file/function/config
  MARK covered if:
    fields, inputs, outputs, error paths, boundary rules implemented
  ELSE mark gap with task id

SCAN implementation for forbidden patterns:
  mock/stub/fake/dummy/placeholder
  fallback success
  TODO/FIXME/HACK/XXX in production path
  direct_llm report path
  provider tool exposure to worker
  Python investment conclusion/rating/target price

COMPARE:
  HLD provider route == implemented route
  DLD tool surface == stage policy
  DLD schema == pack output
  DLD Mongo DDL == created schema
  DLD gate rules == control-plane validators
  HLD §16 five-sample assets == test fixtures and report template prepared
  TradingAgents-CN same-ticker compare inputs == prompt/tool-result/llm-back/report all present
```

交付物：

- 覆盖审查报告
- 偏离清单

通过条件：

- 覆盖矩阵每一行都有实现证据。
- 无未批准 provider、工具、fallback success、Python 投资结论。
- 若有偏离，必须有 DLD/HLD 章节证据和修复任务，不得直接进入 T-FND-047。
- 仅当以上条件满足时，允许进入 T-FND-047；不得据此宣称“实施完成”或“验收通过”。

### T-FND-047

任务标题：执行统一集成测试与真实依赖验收

对应设计：DLD §7，全 DLD

前置依赖：T-FND-046 完成且允许进入 T-FND-047

任务描述：统一运行所有测试和真实依赖验收。该任务是本清单唯一测试执行阶段。

伪码步骤：

```text
RUN static forbidden scans
RUN unit and contract tests for:
  config, policy, planner, cache, fetchers, mapper,
  raw writer, diagnostics, pack builder, derived summary,
  control gates

RUN minimal credible set with real or approved dev dependencies:
  sample_1: 600519 failure sample
  sample_2: one CN_A success sample
  sample_3: structural failure sample (missing_token/timeout/non_json)
  sample_4: guard sample (tool failure then claim attempts)
  sample_5: TradingAgents-CN same ticker/date compare set

ASSERT pack:
  schema_version == cn_a_fundamental_pack.v1
  provider_attempts not empty
  every non-null fact has field_sources
  raw_payload_refs have sha256 hash and OpenViking URI
  quality status in allowed state machine
  complete fundamental threshold follows HLD §12.1
  trend capability only available with comparable multi-period evidence
  derived_summary <= 8 and no forbidden claims
  worker visible tools exactly approved set
  report claim gate rejects unsupported target price/rating/valuation judgment
  when core fields missing, report allows "证据不足，无法给出基本面评级"
  failed_ratio_5m > 5% triggers alert
  missing_token_15m > 0 triggers alert
  raw_write_error_5m > 0 triggers alert
  unsupported_claim_10m continuous increase triggers alert
  metric labels exclude ticker/token/raw payload in alert and metrics outputs

FOR each sample report, include evidence bundle:
  visible tools
  provider request
  tool calls
  data pack raw output
  provider_attempts
  field_sources
  missing_fields
  OpenViking receipt
  guard result
  report

RUN TradingAgents-CN same-layer compare:
  compare final prompt
  compare tool result
  compare LLM back
  compare report

IF failures:
  group by root cause
  map back to task ID
  fix task
  rerun full integration test

PRINT collect-first compliance block:
  batch scope
  completed items
  failures collected
  early-stop exception used yes/no
  exception evidence if any
  batch fix grouping
```

建议命令模板：

```bash
rg -n -i "mock|stub|fake|dummy|placeholder|fallback|TODO|FIXME|HACK|XXX|暂不实现|后续补充|此处省略|待定|待确认|TBD|简单起见|为了演示|示例数据|模拟" agents/fundamental_analyst src tests
rg -n "tushare\\.|akshare\\.|baostock\\.|FinanceMCP" agents/fundamental_analyst/STAGES.yaml agents/fundamental_analyst/skills/manifest.yaml
rg -n "目标价|买入|持有|卖出|高估|低估|护城河|行业龙头" agents/fundamental_analyst/skills/cn-a-fundamental-data src/claw_trade
uv run pytest tests/contracts tests/unit tests/integration
```

通过条件：

- 所有静态扫描无生产路径违规命中。
- 所有单元、契约、集成测试通过。
- 五类最小样本全部产出真实 provider attempts、raw evidence、Mongo cache 记录和 DataPack。
- 每个样本均附带 10 项证据字段（visible tools 到 report）。
- TradingAgents-CN 同标的同日期对照完成 final prompt/tool result/LLM back/report 同层比较。
- control-plane gate 能按 reject/rerun/terminate 三种结果执行。
- 告警阈值语义断言全部通过：`failed_ratio_5m > 5%`、`missing_token_15m > 0`、`raw_write_error_5m > 0`、`unsupported_claim_10m` 连续增长。
- metrics/alert 标签不包含 ticker、token、raw payload。

## 6. 任务执行顺序

建议一次性完成实现任务后再进入测试：

1. 基础与边界：T-FND-001 至 T-FND-009。
2. MongoDB cache：T-FND-010 至 T-FND-013。
3. Provider plan：T-FND-014 至 T-FND-016。
4. Tushare 主源：T-FND-017 至 T-FND-021。
5. AkShare 补充源：T-FND-022 至 T-FND-025。
6. 二阶段补充编排：T-FND-042。
7. 映射与 evidence：T-FND-026 至 T-FND-030。
8. 诊断与 DataPack：T-FND-031 至 T-FND-038，T-FND-043。
9. 控制面 gate：T-FND-039 至 T-FND-040，T-FND-044，T-FND-045。
10. Prompt 边界与降级：T-FND-041。
11. 统一集成验收：T-FND-046 至 T-FND-047。

## 7. 偏离登记模板

实施中发现偏离时，必须登记，不得静默修正：

```text
偏离 ID：
发现任务：
对应 DLD/HLD 章节：
原设计要求：
实现现状：
偏离性质：覆盖缺失 / 接口漂移 / 数据流漂移 / 技术选型漂移 / 安全风险 / 运维风险
严重程度：阻塞 / 重要 / 轻微
是否继续实施：是/否
修复任务：
```

## 8. 完整性验收口径

最终验收只接受以下结论：

- `通过`：T-FND-001 至 T-FND-047 全部完成，T-FND-046 无阻塞偏离，T-FND-047 全部通过。
- `有条件通过`：无阻塞偏离，但真实外部依赖不可用；必须列出缺失依赖和替代证据，不能声称 runtime 完整通过。
- `不通过`：存在任一伪实现、fallback success、provider 工具泄露、Python 投资结论、Mongo/OpenViking 证据链缺失、或 DLD 覆盖缺口。
- 补充口径：`T-FND-046` 不能单独作为进入“通过/有条件通过”的依据；若 `T-FND-047` 未执行或未通过，整体结论必须为 `不通过`。
