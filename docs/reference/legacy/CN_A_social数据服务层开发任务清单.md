# CN_A social 数据服务层开发任务清单

来源详细设计：`docs/CN_A_social数据服务层详细设计.md`

生成日期：2026-05-07

## 1. 任务总览

前置假设：

- 实现落点按 DLD 建议路径执行。
- P1 雪球热度因缺少 HLD 批准 endpoint 与字段映射，只实现配置阻断。
- M2 起 `CACHE_REQUIRED=true`。
- OpenViking L2 写入通过现有 runtime 适配。

任务总数：35 个。

按模块统计：

| 模块 | 任务数 |
|---|---:|
| 工具暴露与 skill 挂载 | 3 |
| 配置与目标画像 | 3 |
| Provider 调度与适配 | 4 |
| MongoDB cache | 3 |
| OpenViking evidence | 3 |
| 匹配分桶 | 3 |
| 质量门 | 1 |
| reader_brief | 1 |
| 资料包编排 | 4 |
| Prompt 与 guard | 3 |
| 可观测性与运维 | 2 |
| 测试、验收、文档 | 5 |

建议开发顺序：

1. 先做配置、ticker 规范化、数据结构和 skill 暴露。
2. 再做 provider、cache、evidence。
3. 然后做匹配、质量门、brief。
4. 最后串起编排器、guard、集成验收。

关键路径：

`T-CFG-001 -> T-CFG-002 -> T-PRO-001 -> T-PRO-002/T-PRO-003 -> T-PRO-004 -> T-EVD-002 -> T-CAC-002 -> T-PKG-003 -> T-PKG-004 -> T-GRD-003 -> T-TST-002 -> T-TST-003`

## 2. 任务清单

### T-POL-001

任务标题：配置 social_analyst 的 CN_A stage policy 与 skill manifest

对应详细设计章节：4.1，3.1，3.3，6.3

前置依赖：无

任务描述：在 `agents/social_analyst/STAGES.yaml` 和 skill manifest 中绑定 CN_A `social_analyst` 可见工具集合。只暴露 `social_social_sentiment_pack` 与 `openviking_write_material`，并注册 `agents/social_analyst/skills/cn-a-social-data/SKILL.md` 和脚本入口。该任务操作 OpenClaw stage policy 与 skill manifest，不接触 provider 原子接口。

交付物：`STAGES.yaml` 更新、`skills/manifest.yaml`、`cn-a-social-data/SKILL.md`。

验收条件：

- 给定 CN_A `social_analyst` stage policy，当读取可见工具列表，则列表精确等于两个批准工具。
- 给定底层 provider 工具名称，当扫描 `social_analyst` 可见工具配置，则没有任何 provider 原子接口。
- 给定非 `social_analyst` worker，当读取该 skill manifest，则不会挂载 `cn-a-social-data` skill。

预估工时：1 天

任务类型：基础设施

### T-POL-002

任务标题：实现可见工具策略解析接口

对应详细设计章节：4.1.2-4.1.8，4.10

前置依赖：T-POL-001

任务描述：实现 `resolve_social_visible_tools(worker_id, market_profile)`，返回 `VisibleToolPolicy`。接口必须校验 worker、market profile、工具集合精确匹配，并返回 DLD 规定的错误码。该逻辑只判断工具暴露策略，不调 LLM、不调 provider。

交付物：`VisibleToolPolicy` 数据结构、`resolve_social_visible_tools` 方法及单元测试。

验收条件：

- 给定 `worker_id=social_analyst` 且 `market_profile=CN_A`，当工具集合精确匹配，则返回 `openviking_access=write`。
- 给定 worker 不是 `social_analyst`，当调用接口，则抛出或返回 `SOCIAL_TOOL_POLICY_WORKER_MISMATCH`。
- 给定工具集合含 `stock_hot_rank_em`，当调用接口，则返回 `SOCIAL_TOOL_POLICY_LEAK`。
- 给定只缺 `openviking_write_material`，当调用接口，则返回 `SOCIAL_TOOL_POLICY_MISSING_WRITE`。

预估工时：1 天

任务类型：接口层

### T-POL-003

任务标题：实现 social 资料包工具适配层

对应详细设计章节：4.1.2-4.1.6，4.2.3，5.1

前置依赖：T-POL-002，T-CFG-001

任务描述：实现 OpenClaw skill 工具入口，将 tool JSON 转为 `SocialToolInput` 与 `SocialToolRuntimeContext`。适配层调用 `build_social_sentiment_pack`，并原样返回 `SocialSentimentPack` JSON，不改写质量状态或投资含义。适配层需要传递 `run_id`、`stage`、`worker_id`、`call_id`、`evidence_root` 和当前时间。

交付物：`scripts/social_data_pack.py` 工具入口、输入校验、适配层测试。

验收条件：

- 给定完整 CN_A 工具请求，当 worker 调用工具，则编排器收到完整 runtime context。
- 给定 `market=HK`，当调用工具，则返回错误码 `SOCIAL_UNSUPPORTED_MARKET` 的 failed pack。
- 给定 `context.stage` 不是 `frontline`，当调用工具，则返回 `SOCIAL_INVALID_INPUT`。
- 给定编排器返回 partial pack，当适配层返回结果，则 `quality.status` 保持 `partial`。

预估工时：1.5 天

任务类型：接口层

### T-CFG-001

任务标题：实现 social 数据配置加载与校验

对应详细设计章节：4.9.2-4.9.8，7，7.1

前置依赖：无

任务描述：实现 `load_social_data_config(env)`，读取 DLD §7 的所有环境变量并生成 `SocialDataConfig`。校验 timeout、并发、TTL、MongoDB 配置、schema version 和 P1 开关范围。`p1_xueqiu_enabled=true` 时必须因缺少批准 endpoint 与字段映射返回配置错误。

交付物：`scripts/config.py`、`SocialDataConfig`、配置校验测试。

验收条件：

- 给定空环境变量，当加载配置，则返回 DLD 默认值且 `schema_version=cn_a_social_pack.v1`。
- 给定 `CN_A_SOCIAL_PROVIDER_TIMEOUT_SECONDS=31`，当加载配置，则返回 `SOCIAL_CONFIG_INVALID`。
- 给定 `CN_A_SOCIAL_CACHE_REQUIRED=true` 且缺 MongoDB URI，当加载配置，则返回 `SOCIAL_CONFIG_SECRET_MISSING`。
- 给定 `CN_A_SOCIAL_P1_XUEQIU_ENABLED=true`，当加载配置，则返回配置校验失败并说明缺少批准 endpoint 与字段映射。

预估工时：1 天

任务类型：基础设施

### T-CFG-002

任务标题：实现 CN_A 股票代码、日期窗口与东方财富代码转换

对应详细设计章节：2，4.3.2，4.9.3-4.9.5

前置依赖：T-CFG-001

任务描述：实现 CN_A ticker 规范化、`ToEastmoneySymbol` 和 `ResolveDateWindow`。支持 `600519`、`600519.SH`、`SH600519`、`000001.SZ`、`SZ000001`，内部统一成 `ticker_plain`、交易所后缀和东方财富 symbol。日期窗口缺省时按运行日期向前 7 天生成。

交付物：`scripts/profile.py` 中 ticker/date 规范化方法及测试。

验收条件：

- 给定 `600519`，当转换东方财富 symbol，则返回 `100.600519`。
- 给定 `SZ000001`，当规范化 ticker，则返回 `000001.SZ` 和 `0.000001`。
- 给定 `12345`，当规范化 ticker，则返回 `SOCIAL_INVALID_TICKER_MARKET_PREFIX`。
- 给定 `start_date=2026-05-08` 且 `end_date=2026-05-07`，当解析日期窗口，则返回 `SOCIAL_INVALID_INPUT`。

预估工时：1 天

任务类型：数据层

### T-CFG-003

任务标题：实现目标画像与已批准简称解析

对应详细设计章节：4.9.1-4.9.6，4.6，6.2，10

前置依赖：T-CFG-002，T-EVD-001

任务描述：实现 `resolve_social_target_profile`、`build_query_keywords` 和 approved artifact refs 读取校验。只从 `profile` 或 `company_identity` 类型的 approved refs 中提取已批准简称，并校验批准状态与 content hash。工具输入中的公司名和行业可用于画像，但行业词不得作为目标命中依据。

交付物：`SocialTargetProfile`、`ApprovedAlias`、目标画像解析测试。

验收条件：

- 给定有效 approved profile ref，当解析目标画像，则 `approved_aliases` 包含 ref 中的简称。
- 给定 ref hash 与实际内容不一致，当解析目标画像，则返回 `SOCIAL_PROFILE_REF_INVALID`。
- 给定没有公司名和简称但 ticker 有效，当解析目标画像，则返回画像并包含缺口 warning。
- 给定 worker 传入未批准 alias 字段，当构建 query keywords，则该 alias 不进入 keywords。

预估工时：2 天

任务类型：数据层

### T-PRO-001

任务标题：定义 provider 规格与查询计划生成

对应详细设计章节：4.3.2-4.3.4，7

前置依赖：T-CFG-001，T-CFG-002

任务描述：实现 `ProviderSpec`、`ProviderQuery` 和 `build_provider_plan`。默认启用 4 个 P0 endpoint，并按配置启用 `stock_hot_up_em`；查询参数必须稳定序列化后生成 `query_fingerprint`。禁止来源不得进入 provider plan。

交付物：`scripts/providers.py` provider spec、plan builder、fingerprint 测试。

验收条件：

- 给定 600519 目标画像，当生成 provider plan，则包含 4 个 P0 endpoint。
- 给定 `p1_hot_up_enabled=true`，当生成 provider plan，则包含 `stock_hot_up_em`。
- 给定禁用来源 endpoint，当加载 provider spec，则该 endpoint 不进入 plan 并返回 `SOCIAL_PROVIDER_FORBIDDEN_SOURCE`。
- 给定相同 endpoint 与参数，当重复生成 plan，则 `query_fingerprint` 完全一致。

预估工时：1.5 天

任务类型：数据层

### T-PRO-002

任务标题：实现 AkShare 三个目标类 P0 endpoint 调用

对应详细设计章节：4.3.1-4.3.8，5.1

前置依赖：T-PRO-001

任务描述：实现 `fetch_provider_payload` 对 `stock_hot_rank_latest_em`、`stock_hot_keyword_em`、`stock_hot_rank_relate_em` 的真实调用。每次调用记录 `RawProviderResult` 的状态、耗时、行数、错误和 payload hash。单 endpoint timeout 固定使用配置中的 10 秒默认值。

交付物：AkShare P0 目标 endpoint adapter、错误脱敏、provider focused 测试。

验收条件：

- 给定有效 `eastmoney_symbol=100.600519`，当调用目标热度 endpoint，则返回 `RawProviderResult` 且包含 elapsed_ms。
- 给定 provider 返回空记录，当执行调用，则 `status=empty` 且 `raw_count=0`。
- 给定 provider 抛异常，当执行调用，则 `status=error` 且 error message 不包含 credential、cookie、token。
- 给定调用超过 10 秒，当执行调用，则 `status=timeout`。

预估工时：2 天

任务类型：集成

### T-PRO-003

任务标题：实现全市场榜单与热度飙升榜过滤调用

对应详细设计章节：4.3.2-4.3.8，4.4.2，10

前置依赖：T-PRO-001

任务描述：实现 `stock_hot_rank_em` 和 `stock_hot_up_em` 调用，并按 `ticker_plain` 精确过滤目标代码。全市场人气榜用于校验目标是否命中，热度飙升榜按配置启用。雪球热度配置为启用时由配置层阻断，不在 provider 调用层建立 endpoint。

交付物：榜单类 provider adapter、目标精确过滤测试。

验收条件：

- 给定全市场榜单包含 `600519`，当归一化榜单结果，则生成目标命中 row。
- 给定全市场榜单不包含目标代码，当归一化结果，则记录未命中缺口，不生成目标 attention signal。
- 给定 `p1_hot_up_enabled=false`，当生成 provider plan，则不包含 `stock_hot_up_em`。
- 给定 provider 返回无日期字段，当抽取 required fields，则 cache 字段校验返回 schema invalid。

预估工时：2 天

任务类型：集成

### T-PRO-004

任务标题：实现 provider payload 归一化与 raw ref 读取校验

对应详细设计章节：4.3.3-4.3.6，4.5，6.2

前置依赖：T-PRO-002，T-PRO-003，T-EVD-002

任务描述：实现 `normalize_provider_payload`、`load_rows_by_raw_payload_ref` 和 `normalize_provider_rows`。将 AkShare payload 转为 `NormalizedProviderRow`，并保留 `raw_payload_ref`、`payload_hash`、`raw_index`。cache hit 路径必须从 OpenViking L2 ref 读取 canonical raw rows 并校验 hash。

交付物：provider normalization、raw ref loader、一致性测试。

验收条件：

- 给定目标热度 raw payload 和 L2 ref，当归一化，则每行包含 `source_kind=heat_rank` 与证据字段。
- 给定 expected hash 与 L2 内容 hash 不一致，当读取 raw ref，则返回 `SOCIAL_RAW_HASH_MISMATCH`。
- 给定 row 缺 `raw_payload_ref`，当汇总 normalized rows，则返回 `SOCIAL_ROW_EVIDENCE_INCOMPLETE`。
- 给定关键词 payload，当归一化，则输出 `source_kind=heat_keyword`。

预估工时：2 天

任务类型：数据层

### T-CAC-001

任务标题：创建 MongoDB cache collection schema 与索引

对应详细设计章节：4.4.7，6.1，6.3，7

前置依赖：无

任务描述：创建 `social_provider_cache` collection 初始化逻辑，包含 JSON schema 与 DLD 规定的 4 个索引。collection 字段包括 cache key、payload hash、raw payload ref、row_count、fields、TTL 和时间戳。该任务只建立结构化缓存，不保存批准状态。

交付物：MongoDB collection 初始化脚本、索引创建测试。

验收条件：

- 给定空 MongoDB database，当执行初始化，则创建 `social_provider_cache` collection。
- 给定重复 `cache_id`，当插入两条记录，则唯一索引拒绝第二条。
- 给定相同主查询字段，当插入两条记录，则 unique 主查询索引拒绝第二条。
- 给定 collection schema，当插入缺 `raw_payload_ref` 的记录，则 schema 校验失败。

预估工时：1.5 天

任务类型：基础设施

### T-CAC-002

任务标题：实现 cache key、TTL 与 schema inspection

对应详细设计章节：4.4.2-4.4.6，5.1，6.2

前置依赖：T-CAC-001，T-CFG-001

任务描述：实现 `CacheKey`、`CacheInspectionResult` 和 `inspect_provider_cache`。按 `market/ticker/provider/endpoint/query_fingerprint/date_window/schema_version` 查询记录，校验 TTL、schema version、required fields、payload hash 和 raw payload ref。miss、stale、schema invalid 都必须要求后续真实 provider 调用。

交付物：`scripts/cache.py` inspection、TTL rules、required fields 校验测试。

验收条件：

- 给定新鲜且字段完整的 cache 记录，当 inspection，则返回 `status=hit` 和 raw ref/hash。
- 给定 `fetched_at + ttl_seconds < now`，当 inspection，则返回 `status=stale`。
- 给定 raw ref 不以 `viking://` 开头，当 inspection，则返回 `status=schema_invalid`。
- 给定 MongoDB URI 缺失且 `cache_required=true`，当 inspection，则返回 `SOCIAL_CACHE_REQUIRED_MISSING`。

预估工时：2 天

任务类型：数据层

### T-CAC-003

任务标题：实现 provider cache upsert 与本次调用级 MongoDB 异常诊断

对应详细设计章节：4.4.3-4.4.8，5.3，6.2

前置依赖：T-CAC-002

任务描述：实现 `upsert_provider_cache`，仅在 raw L2 写入成功后写 MongoDB cache。处理并发 upsert、write failed 诊断和连续 3 次 MongoDB 查询异常后的 `SOCIAL_CACHE_INSPECTION_BYPASSED`。MongoDB 写入失败不得改变 raw L2 事实。

交付物：cache upsert、并发 upsert 测试、MongoDB 异常诊断测试。

验收条件：

- 给定成功 raw provider result 和 `viking://` ref，当 upsert，则记录包含 payload hash、row_count、fields、ttl_seconds。
- 给定 raw result `ok=false`，当 upsert，则返回 `write_failed` 且原因是 `raw_result_not_success`。
- 给定两个 run 同时写相同 cache key，当 upsert 完成，则 collection 中只有一个 cache_id 记录。
- 给定同一次调用内 MongoDB 连续 3 次查询异常，当检查后续 endpoint，则返回 `SOCIAL_CACHE_INSPECTION_BYPASSED`。

预估工时：2 天

任务类型：数据层

### T-EVD-001

任务标题：实现 evidence target、URI、路径与 canonical hash 工具

对应详细设计章节：4.5.2，4.5.7，6.1，6.2

前置依赖：无

任务描述：实现 `EvidenceWriteTarget`、OpenViking L2 URI 构造、本地审计目录路径构造和 canonical JSON SHA-256。每个 `call_id` 必须使用独立 evidence 目录。该任务提供证据基础工具，不读取 worker report。

交付物：`scripts/evidence.py` 基础类型、URI/path/hash 工具测试。

验收条件：

- 给定 run、stage、worker、call_id，当构建 L2 URI，则路径符合 DLD `viking://resources/workflow/...` 格式。
- 给定同一 body，当重复 canonical hash，则 hash 完全一致。
- 给定缺 `call_id` 的 target，当校验 target，则返回 `SOCIAL_EVIDENCE_TARGET_INVALID`。
- 给定两个不同 call_id，当构建本地路径，则路径互不相同。

预估工时：1 天

任务类型：数据层

### T-EVD-002

任务标题：接入 OpenViking L2 raw payload 写入与 receipt 校验

对应详细设计章节：4.5.1-4.5.6，7.1

前置依赖：T-EVD-001

任务描述：实现 `OpenVikingEvidenceWriter` 生产适配与 `write_raw_payload_evidence`、`try_write_raw_payload_evidence`。写入前执行敏感字段脱敏，写入后校验 `persisted_sha256` 等于请求 hash，并写本地审计副本。认证失败、超时、5xx 和 receipt hash mismatch 按 DLD 错误码返回。

交付物：raw evidence writer、redaction、receipt 校验测试。

验收条件：

- 给定 raw payload 与有效 writer，当写入成功，则返回 `raw_payload_ref`、payload hash 和 row_count。
- 给定 receipt hash 与请求 hash 不一致，当写入 raw payload，则返回 `SOCIAL_OPENVIKING_RECEIPT_HASH_MISMATCH`。
- 给定 raw payload 含 `authorization` 字段，当写入本地审计副本，则该字段被脱敏。
- 给定 OpenViking 返回 401，当写入 raw payload，则返回 `SOCIAL_OPENVIKING_AUTH_FAILED`。

预估工时：2 天

任务类型：集成

### T-EVD-003

任务标题：实现 pack、attempts、cache inspection 证据写入与 signal 证据校验

对应详细设计章节：4.5.3-4.5.8，6.2，8.1

前置依赖：T-EVD-002

任务描述：实现 `write_pack_evidence` 和 `validate_signal_evidence`。每次 pack 结束必须写 `social_sentiment_pack.json`、`provider_attempts.json`、`cache_inspection.json`，并计算最终 content hash。accepted signal 必须能回源到 raw payload ref 或 payload hash。

交付物：pack evidence writer、signal evidence validator、证据完整性测试。

验收条件：

- 给定 pack body、attempts、cache inspections，当写 pack evidence，则返回三个 L2 URI 和最终 content hash。
- 给定 accepted signal 缺 raw ref，当校验 signal，则返回 false。
- 给定 pack evidence 写入失败，当构建 pack，则资料包状态为 failed。
- 给定本地审计副本写入失败但 L2 成功，当写证据，则返回 warning 且 L2 URI 保留。

预估工时：2 天

任务类型：数据层

### T-MAT-001

任务标题：编写 CN_A alias 与 keyword 分类配置

对应详细设计章节：4.6，4.9.7

前置依赖：无

任务描述：创建 `alias_rules.yaml` 与 `keyword_categories.yaml`。alias 规则只声明 approved artifact refs 是简称来源；keyword 分类区分 attention、topic 和 forbidden-as-target-match。配置文件不保存 provider payload、worker report 或密钥。

交付物：`config/alias_rules.yaml`、`config/keyword_categories.yaml`、配置加载测试。

验收条件：

- 给定 alias config，当加载规则，则 `approved_aliases_source=approved_artifact_refs_only`。
- 给定 keyword config，当读取 forbidden target match，则包含行业或主题词分类。
- 给定配置文件包含密钥形态字段名，当执行配置扫描，则扫描失败。
- 给定配置文件缺 `profile=CN_A`，当加载配置，则返回配置错误。

预估工时：0.5 天

任务类型：基础设施

### T-MAT-002

任务标题：实现目标匹配分类规则

对应详细设计章节：4.6.2-4.6.4，4.9

前置依赖：T-MAT-001，T-CFG-003

任务描述：实现 `classify_match(row, profile)`。支持 exact ticker、exchange ticker、company name、approved alias、provider target symbol、related symbol only、industry/topic only 和 unmatched。行业词、主题词和相关标的不得被归类为目标情绪。

交付物：`scripts/matching.py` match classifier、匹配规则测试。

验收条件：

- 给定 row 文本含 `600519`，当分类，则返回 `exact_ticker`。
- 给定 row 来自 target symbol endpoint 且 query symbol 等于目标东方财富 symbol，当分类，则返回 `provider_target_symbol`。
- 给定 row 只命中行业词“白酒”，当分类，则返回 `industry_or_topic_only`。
- 给定 row 是相关标的但未命中目标，当分类，则返回 `related_symbol_only`。

预估工时：1.5 天

任务类型：业务逻辑

### T-MAT-003

任务标题：实现 signal 构建、去重、分桶、排序、裁剪与拒收

对应详细设计章节：4.6.1-4.6.8，8.1

前置依赖：T-MAT-002，T-EVD-003

任务描述：实现 `match_deduplicate_and_bucket` 和 `deduplicate_signals`。将 normalized rows 转为 `SocialSignal`，按 DLD 规则进入 `attention_signals`、`topic_keyword_signals`、`related_symbol_signals`、`narrative_signals` 或 `rejected_signals`。每条 accepted signal 必须包含 `match_type`、`match_evidence_span`、`evidence_type`、`content_hash`、`raw_payload_ref` 和 evidence gap 状态。

交付物：bucketed signal builder、排序裁剪逻辑、rejected reason 测试。

验收条件：

- 给定 heat_rank 目标命中 row，当分桶，则进入 `attention_signals`。
- 给定 related_symbol_only row，当分桶，则进入 `related_symbol_signals`，不进入目标热度或关键词桶。
- 给定缺 content hash 的 row，当分桶，则进入 `rejected_signals` 且 evidence gap 为缺证据。
- 给定超过 `max_signals_per_bucket` 的候选信号，当分桶，则每个 bucket 条数不超过配置上限。

预估工时：2 天

任务类型：业务逻辑

### T-QLT-001

任务标题：实现 social 质量门决策

对应详细设计章节：4.7.1-4.7.8，5.2，8.1

前置依赖：T-MAT-003，T-PRO-001

任务描述：实现 `evaluate_social_quality(input)`。根据 P0 成功数、accepted signals、bucket 数、来源集中、趋势字段、provider failures 和证据 ref/hash 输出 `QualityDecision`。质量门不得输出最终情绪方向或投资结论。

交付物：`scripts/quality.py`、`QualityDecision`、complete/partial/failed 测试。

验收条件：

- 给定所有 P0 attempts 均失败，当评估质量，则 `ok=false`、`status=failed`、reason 包含 `P0_ALL_FAILED`。
- 给定有目标热度、有关键词且证据完整，当评估质量，则 `status=complete`。
- 给定只有相关标的信号，当评估质量，则 `status=partial` 且 `social_judgment_allowed=false`。
- 给定 accepted signal 缺 raw ref/hash，当评估质量，则 `status=failed` 且 reason 包含 `EVIDENCE_REF_MISSING`。

预估工时：1.5 天

任务类型：业务逻辑

### T-BRF-001

任务标题：实现事实型中文 reader_brief 生成

对应详细设计章节：4.8.1-4.8.8，8.1

前置依赖：T-QLT-001，T-MAT-003

任务描述：实现 `build_reader_brief(input)`。brief 只描述 provider endpoint 数量、成功失败空结果、来源、时间、匹配原因、线索类型、cache 状态和缺口。禁止输出买入、卖出、持有、最终利好利空、未返回原帖、KOL 或用户观点。

交付物：`scripts/reader_brief.py`、禁止声明检查、brief 测试。

验收条件：

- 给定 complete quality 和多类信号，当生成 brief，则文本包含 endpoint 成功数与各 bucket 计数。
- 给定 partial quality，当生成 brief，则文本包含证据限制说明。
- 给定 failed quality，当生成 brief，则文本说明不足以支持社交情绪判断。
- 给定生成文本命中禁止结论词，当检查 brief，则返回固定限制摘要且不调用外部服务。

预估工时：1 天

任务类型：业务逻辑

### T-PKG-001

任务标题：定义 cn_a_social_pack.v1 输出 schema 与序列化

对应详细设计章节：4.2.2-4.2.3，4.3.2，4.5.2，4.6.2，4.7.2，4.8.2，4.9.2

前置依赖：T-CFG-001

任务描述：定义 `DateWindow`、`ProviderAttempt`、`ProviderExecutionResult`、`SocialQuality`、`SocialEvidence`、`SocialSentimentPack` 等数据结构。实现 canonical JSON 序列化，确保 tool result 包含 `schema_version`、`ok`、`profile`、`query_plan`、`provider_attempts`、`data`、`quality`、`reader_brief`、`evidence`。

交付物：pack schema model、序列化测试、schema contract fixture。

验收条件：

- 给定完整 pack 对象，当序列化 JSON，则包含 DLD 所有必需顶层字段。
- 给定 schema version 非 `cn_a_social_pack.v1`，当校验 schema，则校验失败。
- 给定 `provider_attempts` 为空，当校验 pack schema，则校验失败。
- 给定同一 pack body，当 canonical serialization 两次执行，则输出 hash 一致。

预估工时：1.5 天

任务类型：数据层

### T-PKG-002

任务标题：实现资料包编排器输入、画像、日期与查询计划阶段

对应详细设计章节：4.2.1-4.2.5，5.1

前置依赖：T-POL-003，T-CFG-003，T-PRO-001，T-PKG-001

任务描述：实现 `build_social_sentiment_pack` 的前半流程：context 校验、market 校验、目标画像解析、日期窗口解析、provider plan 生成和空 plan 失败。该任务引用 DLD 伪码 `BuildSocialSentimentPack`、`ResolveSocialTargetProfile`、`ResolveDateWindow`、`BuildProviderPlan`。

交付物：编排器前半流程、输入错误 failed pack 测试。

验收条件：

- 给定合法 CN_A 输入，当执行前半流程，则生成目标画像、日期窗口和非空 provider plan。
- 给定非法 ticker，当执行编排器，则返回 failed pack 且错误码为 `SOCIAL_INVALID_TICKER`。
- 给定 provider plan 为空，当执行编排器，则返回 `SOCIAL_PROVIDER_PLAN_EMPTY`。
- 给定 `start_date<=end_date`，当解析日期窗口，则 pack query_plan 中日期范围与输入一致。

预估工时：2 天

任务类型：业务逻辑

### T-PKG-003

任务标题：实现 cache、provider、evidence 与 provider attempts 编排

对应详细设计章节：4.2.4-4.2.8，4.3，4.4，4.5，5.1，5.2，5.3

前置依赖：T-PKG-002，T-CAC-003，T-PRO-004，T-EVD-002

任务描述：实现 `BuildSocialSentimentPack` 中 provider 执行段：每个 query 先 `InspectProviderCache`，cache hit 读取 raw ref；cache 不可复用时调用 `FetchProvider`，成功后写 raw L2，再 upsert cache。所有 provider 都必须生成 `ProviderAttempt`，pack deadline 超出时调用 `MarkUnfinishedQueriesCancelled`。

交付物：provider execution orchestrator、attempt builder、deadline/cancel 测试。

验收条件：

- 给定 cache hit，当执行 query，则不会发起同 endpoint 网络请求并生成 cache hit attempt。
- 给定 cache stale，当执行 query，则继续真实 provider 调用并生成 stale cache status。
- 给定单 provider timeout，当执行整包，则该 attempt 为 `timeout` 且其他 provider 继续。
- 给定整包超过 20 秒，当执行整包，则未完成 query 标记 `cancelled=true`。

预估工时：3 天

任务类型：业务逻辑

### T-PKG-004

任务标题：实现资料包组装、质量评估、brief、证据写入与降级

对应详细设计章节：4.2.4-4.2.8，4.6，4.7，4.8，4.5，6.2

前置依赖：T-PKG-003，T-MAT-003，T-QLT-001，T-BRF-001，T-EVD-003

任务描述：实现 `BuildSocialSentimentPack` 末段：调用 `normalize_provider_rows`、`MatchDeduplicateAndBucket`、`evaluate_social_quality`、`build_reader_brief`、`BuildPackBody` 和 `WritePackEvidence`。计算 pack body hash 与 final content hash。若非 failed pack 中存在 accepted signal 缺 evidence ref，则执行 `DowngradeToFailed`。

交付物：完整 `build_social_sentiment_pack`、pack evidence 集成测试。

验收条件：

- 给定 provider 返回目标热度和关键词且证据完整，当执行整包，则返回 `ok=true` 且 `quality.status=complete`。
- 给定 P0 全失败，当执行整包，则返回 `ok=false` 且 `quality.status=failed`。
- 给定 accepted signal 缺 raw ref，当执行整包，则最终 pack 被降为 failed。
- 给定 pack evidence 写入成功，当返回 pack，则 `evidence.content_hash` 可由 pack evidence 字段复算。

预估工时：3 天

任务类型：业务逻辑

### T-GRD-001

任务标题：更新 CN_A social prompt 合同

对应详细设计章节：4.10.1，4.10.2，8.1

前置依赖：T-POL-001

任务描述：更新 `agents/social_analyst/prompts/CN_A.md`，明确 worker 只能基于 `social_social_sentiment_pack` 的热度、关键词、相关标的和可见线索写报告。prompt 必须声明没有原帖、KOL、散户机构分层样本时不得声称已读取；正式报告仍由 worker 调用 `openviking_write_material` 写入。

交付物：CN_A social prompt、prompt contract 文档片段。

验收条件：

- 给定 CN_A prompt 文本，当扫描 required tool，则包含 `social_social_sentiment_pack`。
- 给定 CN_A prompt 文本，当扫描正式写入要求，则包含 `openviking_write_material`。
- 给定 prompt 文本，当扫描禁止声明，则包含不得声称未返回原帖/KOL/分层观点的约束。
- 给定 prompt 文本，当扫描 Python 代写报告要求，则不存在让 Python 写正式报告的指令。

预估工时：1 天

任务类型：接口层

### T-GRD-002

任务标题：实现可见工具、tool registry 与 frontline prompt 合同测试

对应详细设计章节：4.10.3-4.10.8，8.1

前置依赖：T-POL-003，T-GRD-001

任务描述：实现 `validate_social_visible_tools` 及 `tests/contracts/test_frontline_prompt_write_contract.py`、`test_stage_tool_policy_contract.py`、`test_tool_registry_contract.py`。合同测试读取 OpenClaw runtime evidence 或配置证据，验证 CN_A social turn 只暴露两个批准工具。

交付物：visible tools validator、三类合同测试。

验收条件：

- 给定可见工具为两个批准工具，当运行合同测试，则测试通过。
- 给定可见工具多出 `stock_hot_keyword_em`，当运行合同测试，则失败并返回 `SOCIAL_VISIBLE_TOOL_SET_INVALID`。
- 给定 prompt 未要求 write tool，当运行 prompt contract test，则测试失败。
- 给定 `social_analyst` 未挂载 skill manifest，当运行 registry contract test，则测试失败。

预估工时：1.5 天

任务类型：非功能性

### T-GRD-003

任务标题：实现 pack schema 与报告内容 hard gate 校验

对应详细设计章节：4.10.2-4.10.8，8.1，8.3

前置依赖：T-PKG-004，T-GRD-001

任务描述：实现 `validate_social_pack_schema` 和 `validate_social_report_against_pack`。guard 校验 schema version、provider attempts、证据 refs、禁用来源、failed pack 过度表述和 unsupported source claim。guard 只批准或拒绝 artifact，不改写报告。

交付物：pack schema guard、report guard、hard gate 测试。

验收条件：

- 给定 pack 缺 `provider_attempts`，当执行 guard，则返回 `SOCIAL_PROVIDER_ATTEMPTS_MISSING`。
- 给定 attempt endpoint 属于禁用来源，当执行 guard，则返回 `SOCIAL_FORBIDDEN_SOURCE_USED`。
- 给定 failed pack 和完整情绪判断报告，当执行 guard，则返回 `SOCIAL_FAILED_PACK_OVERSTATED`。
- 给定报告声称读取未返回 KOL 观点，当执行 guard，则返回 `SOCIAL_REPORT_UNSUPPORTED_SOURCE_CLAIM`。

预估工时：2 天

任务类型：非功能性

### T-OBS-001

任务标题：接入 social 数据服务日志、metrics 与 trace spans

对应详细设计章节：3.2，4.1.8，4.2.8，4.3.8，4.4.8，4.5.8，4.6.8，4.7.8，4.8.8，4.9.8，4.10.8，7.1

前置依赖：T-CFG-001

任务描述：为 policy、pack、provider、cache、evidence、matching、quality、brief、config 和 guard 接入 DLD 指定 metrics、JSON logs 与 trace span。日志字段至少包含 `ts`,`level`,`run_id`,`call_id`,`worker_id`,`endpoint`,`event`,`code`，并禁止输出 secret 与 raw payload 大字段正文。

交付物：observability helper、各模块埋点、日志脱敏测试。

验收条件：

- 给定一次 pack 调用，当执行成功，则产生 `social.pack.call.count` 和 `social.pack.duration_ms`。
- 给定 provider timeout，当记录日志，则日志包含 endpoint 和 error code。
- 给定日志输入包含 MongoDB URI，当写日志，则输出不包含连接串。
- 给定 trace enabled，当执行 quality 门，则存在 `social.quality.evaluate` span。

预估工时：2 天

任务类型：非功能性

### T-OPS-001

任务标题：实现运行健康检查、告警事件与优雅停机钩子

对应详细设计章节：7.1，3.2，5.1，5.2

前置依赖：T-CFG-001，T-EVD-002，T-CAC-002，T-PRO-002

任务描述：实现启动检查、运行检查、告警事件和停机处理。启动检查覆盖 MongoDB 连通、OpenVikingEvidenceWriter 绑定状态和 AkShare 基础可达性；运行检查记录连续错误计数与最近成功时间；停机时停止接收新工具调用，并允许已开始 pack 在 `pack_timeout_seconds` 内完成证据写入。

交付物：health check module、alert event mapping、shutdown hook 测试。

验收条件：

- 给定 MongoDB 不可连接且 `cache_required=true`，当执行启动检查，则返回失败。
- 给定 OpenViking auth failed，当记录告警事件，则生成高优先级告警。
- 给定 provider timeout，当记录告警事件，则生成中优先级告警。
- 给定停机信号，当已有 pack 未完成，则最多等待 `pack_timeout_seconds` 后将未完成 endpoint 标记 cancelled。

预估工时：2 天

任务类型：非功能性

### T-TST-001

任务标题：编写 focused unit 测试套件

对应详细设计章节：8.1，8.3

前置依赖：T-CFG-003，T-CAC-003，T-QLT-001，T-BRF-001，T-GRD-003

任务描述：为 ticker 规范化、cache key、TTL、质量门、brief 禁止声明、guard validator 编写 focused unit tests。测试必须覆盖正常路径和错误路径，并可在本地不依赖 LLM runtime 的情况下运行。

交付物：`tests/unit/test_cn_a_social_*.py`、`tests/contracts/test_cn_a_social_*.py`。

验收条件：

- 给定本地测试环境，当运行 focused unit tests，则全部通过。
- 给定非法 ticker fixture，当运行 ticker 测试，则断言错误码为 `SOCIAL_INVALID_TICKER`。
- 给定 cache hit fixture，当运行 cache 测试，则断言 raw ref/hash 完整。
- 给定含禁止投资建议的 brief fixture，当运行 brief 测试，则断言输出被限制。

预估工时：2 天

任务类型：非功能性

### T-TST-002

任务标题：编写 MongoDB、OpenViking 与 provider 集成测试

对应详细设计章节：5，6，7.1，8.2，8.3

前置依赖：T-PKG-004，T-OPS-001，T-TST-001

任务描述：编写集成测试覆盖 cache miss -> 真实 provider -> L2 raw -> cache upsert -> pack evidence，以及 cache hit -> raw ref/hash 读取。测试环境必须连接真实 MongoDB、真实 OpenViking L2 写入能力和可访问 AkShare provider。

交付物：`tests/integration/test_cn_a_social_pack_integration.py`、集成测试运行说明。

验收条件：

- 给定空 cache，当执行 600519 social pack 集成测试，则产生 raw L2 ref 并 upsert cache。
- 给定已有新鲜 cache，当再次执行同一 query，则 provider attempt 的 `cache_status=hit`。
- 给定 OpenViking receipt hash mismatch 测试条件，当执行集成测试，则 pack failed。
- 给定 provider 单 endpoint timeout 条件，当执行集成测试，则其他 endpoint attempts 仍被收集。

预估工时：3 天

任务类型：集成

### T-TST-003

任务标题：执行 OpenClaw 600519 CN_A fresh run 验收链路

对应详细设计章节：4.10，5.1，8.2，8.3，10

前置依赖：T-TST-002，T-GRD-002，T-GRD-003

任务描述：建立并执行 600519 CN_A `social_analyst` fresh run 验收脚本。验收必须保存 final prompt、visible tools、tool calls、tool result、report、OpenViking receipt 和 provider evidence。验收后运行 guard，失败不得进入 approved manifest。

交付物：fresh run 验收脚本、运行证据目录、验收报告。

验收条件：

- 给定 OpenClaw runtime 可用，当执行 600519 fresh run，则 evidence 中包含 final prompt 和 visible tools。
- 给定 worker 完成 report 写入，当检查 evidence，则存在 OpenViking L1 receipt。
- 给定 tool result，当运行 pack schema guard，则 guard 通过或给出明确 reason code。
- 给定 report 含未返回来源声明，当运行 report guard，则验收失败且不批准 artifact。

预估工时：3 天

任务类型：集成

### T-ACC-001

任务标题：实现实施覆盖矩阵与验收清单检查

对应详细设计章节：8，9，10

前置依赖：T-TST-001，T-GRD-003

任务描述：建立实施覆盖矩阵，把 DLD 章节、HLD 条目、任务编号、测试编号和验收证据路径关联起来。增加 CI 或本地检查，确保每个 DLD 章节至少有任务和验证项。开放问题必须以明确状态记录。

交付物：`docs/CN_A_social数据服务层实施覆盖矩阵.md`、覆盖检查脚本或合同测试。

验收条件：

- 给定覆盖矩阵文件，当运行覆盖检查，则每个 DLD 章节编号都有对应任务编号。
- 给定某章节任务编号为空，当运行覆盖检查，则检查失败。
- 给定开放问题没有状态，当运行覆盖检查，则检查失败。
- 给定所有测试编号和证据路径存在，当运行覆盖检查，则检查通过。

预估工时：1.5 天

任务类型：非功能性

### T-DOC-001

任务标题：编写部署配置、容量评审与开放决议记录

对应详细设计章节：3.1，3.2，7，7.1，10

前置依赖：T-CFG-001，T-OPS-001

任务描述：编写部署清单，记录 OpenClaw、OpenViking、MongoDB 的端口、认证、数据库名、secret 注入方式、QPS/日调用量假设、并发 run 容量和运维告警。对 DLD §10 的未定项记录处理顺序、负责人和验收前置条件。

交付物：部署配置文档、容量评审表、开放决议记录。

验收条件：

- 给定 staging 环境部署前检查，当读取文档，则 MongoDB URI、database、collection 权限均有配置来源说明。
- 给定生产容量评审，当读取文档，则包含每日 run 数、并发 run 数和 provider 并发上限。
- 给定 P1 雪球热度需求，当读取决议记录，则显示启用前必须补 endpoint 与字段映射。
- 给定 news summary 复用需求，当读取决议记录，则显示启用前必须定义自然语言 summary schema。

预估工时：1 天

任务类型：非功能性

## 3. 依赖关系图

第一层，可并行开始：`T-POL-001`、`T-CFG-001`、`T-CAC-001`、`T-EVD-001`、`T-MAT-001`。

第二层，可并行：

- `T-POL-002` 依赖 `T-POL-001`。
- `T-CFG-002` 依赖 `T-CFG-001`。
- `T-CAC-002` 依赖 `T-CAC-001,T-CFG-001`。
- `T-EVD-002` 依赖 `T-EVD-001`。
- `T-OBS-001` 依赖 `T-CFG-001`。

第三层，可并行：

- `T-CFG-003` 依赖 `T-CFG-002,T-EVD-001`。
- `T-PRO-001` 依赖 `T-CFG-001,T-CFG-002`。
- `T-POL-003` 依赖 `T-POL-002,T-CFG-001`。
- `T-CAC-003` 依赖 `T-CAC-002`。
- `T-EVD-003` 依赖 `T-EVD-002`。
- `T-GRD-001` 依赖 `T-POL-001`。

第四层，可并行：

- `T-PRO-002`、`T-PRO-003` 依赖 `T-PRO-001`。
- `T-MAT-002` 依赖 `T-MAT-001,T-CFG-003`。
- `T-GRD-002` 依赖 `T-POL-003,T-GRD-001`。

第五层，可并行：

- `T-PRO-004` 依赖 `T-PRO-002,T-PRO-003,T-EVD-002`。
- `T-MAT-003` 依赖 `T-MAT-002,T-EVD-003`。
- `T-PKG-001` 依赖 `T-CFG-001`。
- `T-OPS-001` 依赖 `T-CFG-001,T-EVD-002,T-CAC-002,T-PRO-002`。

第六层，可并行：

- `T-QLT-001` 依赖 `T-MAT-003,T-PRO-001`。
- `T-PKG-002` 依赖 `T-POL-003,T-CFG-003,T-PRO-001,T-PKG-001`。

第七层，可并行：

- `T-BRF-001` 依赖 `T-QLT-001,T-MAT-003`。
- `T-PKG-003` 依赖 `T-PKG-002,T-CAC-003,T-PRO-004,T-EVD-002`。

第八层，可并行：

- `T-PKG-004` 依赖 `T-PKG-003,T-MAT-003,T-QLT-001,T-BRF-001,T-EVD-003`。
- `T-DOC-001` 依赖 `T-CFG-001,T-OPS-001`。

第九层，可并行：

- `T-GRD-003` 依赖 `T-PKG-004,T-GRD-001`。
- `T-TST-001` 依赖 `T-CFG-003,T-CAC-003,T-QLT-001,T-BRF-001,T-GRD-003`。

第十层，可并行：

- `T-TST-002` 依赖 `T-PKG-004,T-OPS-001,T-TST-001`。
- `T-ACC-001` 依赖 `T-TST-001,T-GRD-003`。

第十一层：

- `T-TST-003` 依赖 `T-TST-002,T-GRD-002,T-GRD-003`。

依赖无循环。

## 4. 里程碑建议

| 里程碑 | 包含任务 | 达成条件 | 单人日历天 |
|---|---|---|---:|
| M1 基础边界与配置 | T-POL-001,T-POL-002,T-CFG-001,T-CFG-002,T-EVD-001,T-MAT-001 | 可解析工具策略、配置、ticker/date、基础 evidence URI；底层 provider 工具不暴露 | 5 |
| M2 Provider、cache、evidence | T-CFG-003,T-PRO-001,T-PRO-002,T-PRO-003,T-PRO-004,T-CAC-001,T-CAC-002,T-CAC-003,T-EVD-002,T-EVD-003 | provider attempts、MongoDB cache inspection/upsert、OpenViking L2 raw/pack evidence 可运行 | 15 |
| M3 信号处理与资料包 | T-MAT-002,T-MAT-003,T-QLT-001,T-BRF-001,T-PKG-001,T-PKG-002,T-PKG-003,T-PKG-004 | `build_social_sentiment_pack` 输出 `cn_a_social_pack.v1`，覆盖 complete/partial/failed | 14 |
| M4 Prompt、guard 与运维 | T-POL-003,T-GRD-001,T-GRD-002,T-GRD-003,T-OBS-001,T-OPS-001,T-DOC-001 | worker 工具入口、prompt 合同、hard gate、metrics/logs/health checks 就绪 | 9 |
| M5 验收闭环 | T-TST-001,T-TST-002,T-TST-003,T-ACC-001 | focused、integration、600519 fresh run 和覆盖矩阵检查通过 | 10 |

## 5. 风险与阻塞项

文档中未出现字面 `HLD-GAP` 或 `BLOCKED` 标注。隐含风险如下：

| 风险/阻塞项 | 影响任务 | 建议处理顺序 | 阶段性应对方案 |
|---|---|---|---|
| OpenClaw、OpenViking、MongoDB 生产端口、容器编排、认证方式、数据库名未规定 | T-CFG-001,T-CAC-001,T-EVD-002,T-OPS-001,T-DOC-001,T-TST-002,T-TST-003 | M2 编码前确认 | 用环境变量和部署清单显式注入；staging/prod 缺配置时启动检查失败 |
| HLD 未规定 QPS、日调用量、并发 run 数 | T-OBS-001,T-OPS-001,T-DOC-001 | M2 前完成容量评审 | 按 workflow 并发 run 数配置 provider 并发上限；超过单机预算时进入架构评审 |
| OpenViking L2 writer 需绑定现有 runtime | T-EVD-002,T-EVD-003,T-TST-002,T-TST-003 | evidence 开发前确认 adapter 入口 | 实现 `OpenVikingEvidenceWriter` 适配层；未绑定时 M2/M5 验收失败 |
| P1 雪球热度缺精确 endpoint 与字段 | T-CFG-001,T-PRO-001,T-DOC-001,T-ACC-001 | M1 即阻断配置误开 | `p1_xueqiu_enabled=true` 时配置校验失败；启用前先补 HLD 字段映射 |
| approved news summary schema 未定义 | T-CFG-003,T-MAT-003,T-DOC-001 | 进入 news 复用前处理 | 第一版只校验 approved ref，不读取 news summary 内容；复用前定义自然语言 summary schema |
| MongoDB 生产权限与 secret 注入未确认 | T-CAC-001,T-CAC-002,T-CAC-003,T-OPS-001 | M2 前完成 | `cache_required=true` 且缺 secret 时 pack failed；dev 若关闭 cache 必须写 `not_configured` attempts |
| social 情绪评分责任边界未定 | T-GRD-001,T-GRD-003,T-DOC-001 | M4 前确认 | 数据层不输出评分；worker 若评分必须由 prompt/guard 限定证据引用和不足时表达 |

## 6. 覆盖矩阵

| DLD章节 | 章节摘要 | 对应任务编号 | 覆盖状态 |
|---|---|---|---|
| 1 | 文档元信息、边界、HLD M1-M5 范围 | T-DOC-001,T-ACC-001 | 已覆盖 |
| 2 | 术语表、schema、ticker、时间、attempt 约定 | T-CFG-002,T-PKG-001,T-PRO-004,T-EVD-003 | 已覆盖 |
| 3 | 系统上下文与部署视图 | T-POL-001,T-POL-003,T-DOC-001,T-OPS-001 | 已覆盖 |
| 3.1 | 部署拓扑 | T-POL-001,T-POL-003,T-PKG-004,T-DOC-001 | 已覆盖 |
| 3.2 | 节点资源与性能预算 | T-CFG-001,T-OBS-001,T-OPS-001,T-DOC-001 | 已覆盖 |
| 3.3 | 网络分区与安全边界 | T-POL-001,T-POL-002,T-GRD-002,T-GRD-003 | 已覆盖 |
| 4 | 模块详细设计总览 | T-PKG-004,T-ACC-001 | 已覆盖 |
| 4.1 | OpenClaw 工具暴露与 skill 挂载 | T-POL-001,T-POL-002,T-POL-003,T-GRD-002 | 已覆盖 |
| 4.1.1 | 职责与边界 | T-POL-001,T-POL-002 | 已覆盖 |
| 4.1.2 | 核心数据结构 | T-POL-002,T-POL-003 | 已覆盖 |
| 4.1.3 | 接口定义 | T-POL-002,T-POL-003 | 已覆盖 |
| 4.1.4 | 核心算法 | T-POL-002 | 已覆盖 |
| 4.1.5 | 状态机 | T-POL-003,T-GRD-002 | 已覆盖 |
| 4.1.6 | 错误处理 | T-POL-002,T-POL-003 | 已覆盖 |
| 4.1.7 | 存储设计 | T-EVD-003,T-TST-003 | 已覆盖 |
| 4.1.8 | 非功能设计 | T-OBS-001,T-GRD-002 | 已覆盖 |
| 4.2 | 资料包编排 | T-PKG-001,T-PKG-002,T-PKG-003,T-PKG-004 | 已覆盖 |
| 4.2.1 | 职责与边界 | T-PKG-002,T-PKG-003,T-PKG-004 | 已覆盖 |
| 4.2.2 | 核心数据结构 | T-PKG-001 | 已覆盖 |
| 4.2.3 | 接口定义 | T-PKG-002,T-PKG-004 | 已覆盖 |
| 4.2.4 | 核心算法 | T-PKG-002,T-PKG-003,T-PKG-004 | 已覆盖 |
| 4.2.5 | 状态机 | T-PKG-002,T-PKG-003,T-PKG-004 | 已覆盖 |
| 4.2.6 | 错误处理 | T-PKG-002,T-PKG-003,T-PKG-004 | 已覆盖 |
| 4.2.7 | 存储设计 | T-CAC-003,T-EVD-003 | 已覆盖 |
| 4.2.8 | 非功能设计 | T-OBS-001,T-TST-002 | 已覆盖 |
| 4.3 | Provider 调度与适配 | T-PRO-001,T-PRO-002,T-PRO-003,T-PRO-004 | 已覆盖 |
| 4.3.1 | 职责与边界 | T-PRO-001,T-PRO-002,T-PRO-003 | 已覆盖 |
| 4.3.2 | 核心数据结构 | T-PRO-001,T-PKG-001 | 已覆盖 |
| 4.3.3 | 接口定义 | T-PRO-001,T-PRO-002,T-PRO-004 | 已覆盖 |
| 4.3.4 | 核心算法 | T-PRO-001,T-PRO-002,T-PRO-003,T-PRO-004 | 已覆盖 |
| 4.3.5 | 状态机 | T-PKG-003,T-PRO-004 | 已覆盖 |
| 4.3.6 | 错误处理 | T-PRO-002,T-PRO-003,T-PKG-003 | 已覆盖 |
| 4.3.7 | 存储设计 | T-EVD-002,T-CAC-003 | 已覆盖 |
| 4.3.8 | 非功能设计 | T-OBS-001,T-OPS-001 | 已覆盖 |
| 4.4 | MongoDB cache | T-CAC-001,T-CAC-002,T-CAC-003 | 已覆盖 |
| 4.4.1 | 职责与边界 | T-CAC-002,T-CAC-003 | 已覆盖 |
| 4.4.2 | 核心数据结构 | T-CAC-002,T-PKG-001 | 已覆盖 |
| 4.4.3 | 接口定义 | T-CAC-002,T-CAC-003 | 已覆盖 |
| 4.4.4 | 核心算法 | T-CAC-002,T-CAC-003 | 已覆盖 |
| 4.4.5 | 状态机 | T-CAC-002,T-PKG-003 | 已覆盖 |
| 4.4.6 | 错误处理 | T-CAC-003,T-PKG-003 | 已覆盖 |
| 4.4.7 | 数据存储设计 | T-CAC-001 | 已覆盖 |
| 4.4.8 | 非功能设计 | T-OBS-001,T-TST-002 | 已覆盖 |
| 4.5 | OpenViking evidence | T-EVD-001,T-EVD-002,T-EVD-003 | 已覆盖 |
| 4.5.1 | 职责与边界 | T-EVD-002,T-EVD-003 | 已覆盖 |
| 4.5.2 | 核心数据结构 | T-EVD-001,T-PKG-001 | 已覆盖 |
| 4.5.3 | 接口定义 | T-EVD-002,T-EVD-003 | 已覆盖 |
| 4.5.4 | 核心算法 | T-EVD-002,T-EVD-003 | 已覆盖 |
| 4.5.5 | 状态机 | T-EVD-002,T-PKG-003 | 已覆盖 |
| 4.5.6 | 错误处理 | T-EVD-002,T-EVD-003 | 已覆盖 |
| 4.5.7 | 数据存储设计 | T-EVD-001,T-EVD-003 | 已覆盖 |
| 4.5.8 | 非功能设计 | T-OBS-001,T-OPS-001 | 已覆盖 |
| 4.6 | 目标匹配、去重与分桶 | T-MAT-001,T-MAT-002,T-MAT-003 | 已覆盖 |
| 4.6.1 | 职责与边界 | T-MAT-002,T-MAT-003 | 已覆盖 |
| 4.6.2 | 核心数据结构 | T-MAT-003,T-PKG-001 | 已覆盖 |
| 4.6.3 | 接口定义 | T-MAT-002,T-MAT-003 | 已覆盖 |
| 4.6.4 | 核心算法 | T-MAT-002,T-MAT-003 | 已覆盖 |
| 4.6.5 | 状态机 | T-MAT-003 | 已覆盖 |
| 4.6.6 | 错误处理 | T-MAT-003,T-QLT-001 | 已覆盖 |
| 4.6.7 | 存储设计 | T-EVD-003 | 已覆盖 |
| 4.6.8 | 非功能设计 | T-OBS-001,T-TST-001 | 已覆盖 |
| 4.7 | 质量门 | T-QLT-001 | 已覆盖 |
| 4.7.1 | 职责与边界 | T-QLT-001 | 已覆盖 |
| 4.7.2 | 核心数据结构 | T-QLT-001,T-PKG-001 | 已覆盖 |
| 4.7.3 | 接口定义 | T-QLT-001 | 已覆盖 |
| 4.7.4 | 核心算法 | T-QLT-001 | 已覆盖 |
| 4.7.5 | 状态机 | T-QLT-001 | 已覆盖 |
| 4.7.6 | 错误处理 | T-QLT-001,T-GRD-003 | 已覆盖 |
| 4.7.7 | 存储设计 | T-EVD-003 | 已覆盖 |
| 4.7.8 | 非功能设计 | T-OBS-001,T-TST-001 | 已覆盖 |
| 4.8 | reader_brief | T-BRF-001 | 已覆盖 |
| 4.8.1 | 职责与边界 | T-BRF-001 | 已覆盖 |
| 4.8.2 | 核心数据结构 | T-BRF-001,T-PKG-001 | 已覆盖 |
| 4.8.3 | 接口定义 | T-BRF-001 | 已覆盖 |
| 4.8.4 | 核心算法 | T-BRF-001 | 已覆盖 |
| 4.8.5 | 状态机 | T-BRF-001 | 已覆盖 |
| 4.8.6 | 错误处理 | T-BRF-001,T-GRD-003 | 已覆盖 |
| 4.8.7 | 存储设计 | T-EVD-003 | 已覆盖 |
| 4.8.8 | 非功能设计 | T-OBS-001,T-TST-001 | 已覆盖 |
| 4.9 | 目标画像与配置 | T-CFG-001,T-CFG-002,T-CFG-003,T-MAT-001 | 已覆盖 |
| 4.9.1 | 职责与边界 | T-CFG-003 | 已覆盖 |
| 4.9.2 | 核心数据结构 | T-CFG-001,T-CFG-003,T-PKG-001 | 已覆盖 |
| 4.9.3 | 接口定义 | T-CFG-001,T-CFG-002,T-CFG-003 | 已覆盖 |
| 4.9.4 | 核心算法 | T-CFG-002,T-CFG-003 | 已覆盖 |
| 4.9.5 | 状态机 | T-CFG-001,T-CFG-003 | 已覆盖 |
| 4.9.6 | 错误处理 | T-CFG-001,T-CFG-003 | 已覆盖 |
| 4.9.7 | 存储设计 | T-MAT-001 | 已覆盖 |
| 4.9.8 | 非功能设计 | T-OBS-001,T-TST-001 | 已覆盖 |
| 4.10 | Prompt、guard 与验收 | T-GRD-001,T-GRD-002,T-GRD-003,T-TST-003 | 已覆盖 |
| 4.10.1 | 职责与边界 | T-GRD-001,T-GRD-003 | 已覆盖 |
| 4.10.2 | 核心数据结构 | T-GRD-003 | 已覆盖 |
| 4.10.3 | 接口定义 | T-GRD-002,T-GRD-003 | 已覆盖 |
| 4.10.4 | 核心算法 | T-GRD-003 | 已覆盖 |
| 4.10.5 | 状态机 | T-TST-003,T-GRD-003 | 已覆盖 |
| 4.10.6 | 错误处理 | T-GRD-003 | 已覆盖 |
| 4.10.7 | 存储设计 | T-TST-003,T-EVD-003 | 已覆盖 |
| 4.10.8 | 非功能设计 | T-OBS-001,T-TST-001 | 已覆盖 |
| 5 | 模块间交互 | T-PKG-003,T-PKG-004,T-TST-002 | 已覆盖 |
| 5.1 | 正常流程 | T-PKG-003,T-PKG-004,T-TST-002 | 已覆盖 |
| 5.2 | P0 全失败异常流程 | T-PKG-003,T-QLT-001,T-TST-001 | 已覆盖 |
| 5.3 | 并发 cache upsert 场景 | T-CAC-003,T-TST-002 | 已覆盖 |
| 6 | 数据库总体设计 | T-CAC-001,T-EVD-001,T-EVD-003 | 已覆盖 |
| 6.1 | ER 图 | T-CAC-001,T-EVD-001 | 已覆盖 |
| 6.2 | 跨模块一致性 | T-PKG-003,T-PKG-004,T-CAC-003,T-EVD-003 | 已覆盖 |
| 6.3 | 数据迁移策略 | T-POL-001,T-PKG-004,T-CAC-001,T-EVD-003 | 已覆盖 |
| 7 | 配置与环境管理 | T-CFG-001,T-OPS-001,T-DOC-001 | 已覆盖 |
| 7.1 | 运行依赖与运维约束 | T-OPS-001,T-OBS-001,T-DOC-001 | 已覆盖 |
| 8 | 测试策略 | T-TST-001,T-TST-002,T-TST-003 | 已覆盖 |
| 8.1 | 模块关键测试场景 | T-TST-001,T-GRD-002,T-GRD-003 | 已覆盖 |
| 8.2 | 集成测试依赖 | T-TST-002,T-TST-003,T-DOC-001 | 已覆盖 |
| 8.3 | 验收测试顺序 | T-TST-001,T-TST-002,T-TST-003 | 已覆盖 |
| 9 | DLD 覆盖矩阵 | T-ACC-001 | 已覆盖 |
| 10 | 开放问题与决议输入项 | T-DOC-001,T-ACC-001 | 已覆盖 |

## 7. 自检清单

- 已确认：详细设计文档中的每个模块都有对应任务。
- 已确认：任务描述没有使用 `mock`、`stub`、`fake`、`placeholder`、`暂不实现`、`后续补充`。
- 已确认：每个任务都有客观验收条件，并覆盖正常路径和至少一条异常路径。
- 已确认：每个任务预估工时为 0.5 到 3 天。
- 已确认：依赖关系无循环。
- 已确认：覆盖矩阵无空行。
- 已确认：任一开发者拿到单个任务，都能根据任务编号、依赖、交付物和验收条件独立开工。
