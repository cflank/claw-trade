# CN_A news 数据服务层开发任务清单

依据：`docs/CN_A_news数据服务层详细设计.md`，DLD v0.2，日期 2026-05-06。

## 第一部分，任务总览

### 拆解假设

- 实现落点采用 DLD 已批准路径：`agents/news_analyst/skills/cn-a-news-data/`。
- 目标运行环境为 Python 3.11+，不新增独立服务端口、不新增数据库表、不新增外部中间件。
- 纯算法验收使用确定性受控输入；provider、health check、单标的集成验收必须走真实 AkShare/Tushare 调用或真实未配置/权限返回。
- OpenClaw skill manifest 与 worker stage policy 的最终文件名以仓库现有约定为准；任务要求保持 worker 只看到 `news_news_data_pack` 与 `openviking_write_material`。

### 任务总数

共 30 个任务。

### 按模块分组统计

| 模块分组 | 任务数量 | 任务编号 |
|---|---:|---|
| 基础结构与模型 | 2 | T-INF-001, T-INF-002 |
| 配置与安全 | 3 | T-CFG-001, T-CFG-002, T-SEC-001 |
| profile 解析 | 2 | T-PRF-001, T-PRF-002 |
| provider 与调度 | 8 | T-PVD-001, T-PVD-002, T-PVD-003, T-PVD-004, T-PVD-005, T-PVD-006, T-SCH-001, T-SCH-002 |
| 匹配、去重、质量、brief、编排 | 8 | T-MAT-001, T-MAT-002, T-KWD-001, T-DED-001, T-DED-002, T-QLT-001, T-BRF-001, T-SVC-001 |
| evidence 与工具适配 | 3 | T-EVD-001, T-ADP-001, T-ADP-002 |
| 可观测性、诊断、发布、集成 | 4 | T-OBS-001, T-OPS-001, T-REL-001, T-INT-001 |

### 建议开发顺序概述

先建立 skill 目录、核心模型、配置和安全工具，再并行实现 profile resolver、provider、匹配、关键词观察、去重、质量门和 brief。随后接入 `NewsDataService.build_pack`、evidence 原子写入、`run_news_data_pack` 工具适配与脚本 I/O。最后完成 metrics、health check、版本发布校验和 600519 单标的集成验证。

关键路径：T-INF-001 -> T-INF-002 -> T-CFG-001 -> T-CFG-002 -> T-PRF-002 -> T-PVD-001 -> T-PVD-002/T-PVD-003/T-PVD-004/T-PVD-005/T-PVD-006 -> T-SCH-001 -> T-SCH-002 -> T-MAT-001 -> T-MAT-002 -> T-DED-001 -> T-DED-002 -> T-QLT-001 -> T-BRF-001 -> T-SVC-001 -> T-EVD-001 -> T-ADP-001 -> T-ADP-002 -> T-INT-001。

## 第二部分，任务清单

### T-INF-001

- 任务标题：建立 CN_A news 独立 skill 目录与元数据文件
- 对应详细设计章节：§2, §3.1, §4.1.9, §4.15, §7
- 前置依赖：无
- 任务描述：创建 `agents/news_analyst/skills/cn-a-news-data/` 目录结构，包含 `SKILL.md`、config 目录、scripts 目录和测试目录。写入 `name`、`version`、`tool`、`entrypoint`、`schema_version` 等元数据，并准备 worker 绑定和 tool 导出的 manifest 配置。此任务不新增数据库表，也不改变 OpenClaw 运行边界。
- 交付物：`SKILL.md`、skill manifest 配置、`config/`、`scripts/`、测试目录骨架、元数据校验测试。
- 验收条件：
  - 给定仓库根目录，当执行元数据校验测试，则 `SKILL.md` 包含 `cn-a-news-data`、`news_news_data_pack`、`scripts/news_data_pack.py` 和 `cn_a_news_pack.v1`。
  - 给定 manifest 配置，当解析 worker 绑定，则只有 `news_analyst` 绑定该 skill。
  - 给定 manifest 配置，当解析导出工具，则只导出 `news_news_data_pack`。
  - 给定仓库差异，当检查数据库迁移目录，则没有新增 CN_A news 数据库迁移文件。
- 预估工时：0.5 天
- 任务类型：基础设施

### T-INF-002

- 任务标题：实现新闻资料包核心模型、错误码和稳定 JSON 序列化
- 对应详细设计章节：§4.1.2, §4.1.3, §4.2.2, §4.2.3, §4.3.2, §4.4.2, §4.5.2, §4.6.2, §4.7.2, §4.8.2, §4.9.2, §6, §11.4
- 前置依赖：T-INF-001
- 任务描述：实现 `ToolRuntimeContext`、`ToolInput`、`NewsDataPackRequest`、`ResolvedProfile`、`QueryPlan`、`ProviderAttempt`、`Quality`、`NewsDataPack`、`ProviderQuery`、`RawNewsItem`、`ProviderFetchResult`、`MatchResult`、`KeywordObservations`、`NewsItem`、`DedupStats`、`KeywordRule`、`KeywordObservationResult`、`QualityInput`、`QualityDecision`、`BriefInput`、`BriefSection`、`EvidenceWriteRequest`、`EvidenceRefs`。实现 `NewsDataError` 和 `E_INVALID_INPUT`、`E_UNSUPPORTED_MARKET`、`E_PROFILE_RESOLVE_FAILED`、`E_CONTEXT_MISMATCH`、`E_EVIDENCE_WRITE_FAILED`、`E_PROVIDER_LAYER_FAILED` 等错误码。提供稳定 JSON 序列化，确保 `schema_version` 固定为 `cn_a_news_pack.v1`。
- 交付物：`scripts/models.py`、`scripts/errors.py`、模型序列化单元测试。
- 验收条件：
  - 给定完整 `NewsDataPack` 实例，当执行稳定 JSON 序列化两次，则两次输出字节内容一致。
  - 给定缺少必填字段的 `ToolRuntimeContext`，当构造或校验模型，则返回或抛出对应字段的校验错误。
  - 给定 `NewsDataPack` 实例，当读取 `schema_version`，则值固定为 `cn_a_news_pack.v1`。
  - 给定 `ProviderAttempt` 的超时场景，当序列化，则包含非负 `elapsed_ms`、`empty_reason="timeout"` 和 `cancelled` 字段。
- 预估工时：2 天
- 任务类型：基础设施

### T-CFG-001

- 任务标题：实现 CN_A news 环境配置加载与默认值解析
- 对应详细设计章节：§3.2, §4.2.4, §4.2.6, §4.5.3, §4.5.8, §7
- 前置依赖：T-INF-001
- 任务描述：实现环境变量读取，覆盖 enabled providers、Tushare token、单 provider timeout、整包 timeout、最大并发、JSON 新闻条数、brief 条数、标题相似阈值、关键词配置路径和别名规则路径。配置对象供 provider 调度、去重裁剪、brief 生成和 provider 初始化使用。Tushare token 只保存在进程内配置对象，不写入普通日志和 evidence。
- 交付物：`scripts/config_loader.py` 中的环境配置加载器及单元测试。
- 验收条件：
  - 给定无 CN_A news 环境变量，当加载配置，则默认 provider 清单为五个 DLD 指定 endpoint，timeout 为 10 秒，整包 timeout 为 20 秒，最大并发为 3。
  - 给定 `CN_A_NEWS_MAX_JSON_ITEMS=80`，当加载配置，则 JSON 新闻上限为 80。
  - 给定 `CN_A_NEWS_TITLE_SIMILARITY_THRESHOLD=abc`，当加载配置，则返回配置错误并指出该字段需要浮点数。
  - 给定配置对象包含 Tushare token，当输出配置摘要，则摘要中不包含 token 原文。
- 预估工时：1 天
- 任务类型：基础设施

### T-CFG-002

- 任务标题：实现关键词词表与别名规则 YAML schema 校验
- 对应详细设计章节：§4.6.2, §4.6.3, §4.10.5, §4.11.1, §4.11.2, §4.11.3, §7, §10
- 前置依赖：T-INF-002, T-CFG-001
- 任务描述：实现 `keyword_categories.yaml` 和 `alias_rules.yaml` 的加载与 schema 校验。关键词规则只接收包含 `CN_A` 的 enabled 规则，分类必须属于 DLD 批准枚举。别名规则读取 ticker、company_name、approved_aliases、approved_historical_names 和 conflicts，为 resolver 和匹配模块提供已校验配置；加载失败时生成可进入失败资料包的配置错误。
- 交付物：`config/keyword_categories.yaml`、`config/alias_rules.yaml`、`scripts/config_loader.py` schema 校验逻辑及单元测试。
- 验收条件：
  - 给定合法关键词 YAML，当加载规则，则返回的每条 `KeywordRule.markets` 均包含 `CN_A`。
  - 给定关键词分类为未批准值，当加载规则，则返回 schema 校验错误，错误摘要不超过 512 字符。
  - 给定 alias conflicts 配置，当加载别名规则，则返回冲突黑名单并保留原始配置顺序。
  - 给定 YAML 语法错误，当服务入口读取配置，则形成 `ok=false`、`quality.status=failed`、`empty_reason=schema_changed` 的失败资料包。
- 预估工时：1.5 天
- 任务类型：基础设施

### T-SEC-001

- 任务标题：实现错误脱敏和外部 URL 校验
- 对应详细设计章节：§4.3.5, §4.3.7, §4.3.9, §4.9.4, §4.13, §9
- 前置依赖：T-INF-001
- 任务描述：实现 `sanitize_error`，屏蔽 token、api_key、authorization、cookie、passwd 等键对应值，并将错误串限制到 512 字符。实现 URL 校验，只允许 http 和 https，拒绝控制字符，非法 URL 不进入 `NewsItem.url` 并写入 `evidence_gap="invalid_url"`。该模块被 provider、evidence 和工具适配层复用。
- 交付物：`scripts/security.py`、脱敏和 URL 校验单元测试。
- 验收条件：
  - 给定包含 `token=abc123` 的错误 URL，当执行 `sanitize_error`，则输出保留 `token` 键名且值为 `***`。
  - 给定超过 512 字符的错误串，当执行 `sanitize_error`，则输出长度不超过 523 字符并以 `[truncated]` 结尾。
  - 给定 `ftp://example.com/a`，当执行 URL 校验，则返回非法并给出 `invalid_url` 缺口。
  - 给定包含控制字符的 https URL，当执行 URL 校验，则返回非法并且原 URL 不进入 `NewsItem.url`。
- 预估工时：1 天
- 任务类型：非功能性

### T-PRF-001

- 任务标题：实现 ApprovedProfileResolver artifact 读取与字段优先级
- 对应详细设计章节：§4.1.1, §4.1.4, §4.10.1, §4.10.2, §4.10.3, §4.10.4
- 前置依赖：T-INF-002, T-CFG-001
- 任务描述：实现 `ApprovedProfileResolver.resolve`，只从已批准 runtime profile、fundamentals、market artifact refs 读取公司名、行业、已批准简称和历史名称。标量字段按 runtime profile、fundamentals、market 的优先级选择第一个非空值，列表字段按优先级拼接。冲突详情写结构化运行日志，不让低优先级来源覆盖高优先级非空值。
- 交付物：`scripts/profile_resolver.py` artifact 读取与优先级逻辑、resolver 单元测试。
- 验收条件：
  - 给定三个 artifact 均含不同 `company_name`，当执行 `resolve`，则输出 runtime profile 中的非空公司名。
  - 给定 runtime profile 的 `industry` 为空且 fundamentals 有 `sector`，当执行 `resolve`，则输出 fundamentals 的行业值。
  - 给定 worker 请求中携带未批准 alias，当执行 `resolve`，则该 alias 不进入 `ResolvedProfile.approved_aliases`。
  - 给定 artifact 引用不可读，当执行 `resolve`，则返回 `E_PROFILE_RESOLVE_FAILED` 或包含对应缺口的解析结果，且不访问 Python 运行内存历史缓存。
- 预估工时：2 天
- 任务类型：数据层

### T-PRF-002

- 任务标题：实现 alias 去重、冲突黑名单和 missing_fields 归档
- 对应详细设计章节：§4.4.4, §4.10.5, §4.10.6, §4.11.2, §7
- 前置依赖：T-PRF-001, T-CFG-002
- 任务描述：在 `ApprovedProfileResolver.resolve` 输出前处理 `approved_aliases` 与 `approved_historical_names`，执行去首尾空白、删除空项、稳定去重、应用 `alias_rules.yaml` 冲突黑名单。实现 `missing_fields` 归档，只允许写入 `company_name`、`industry`、`approved_aliases`、`approved_historical_names`。输出结果供 `build_query_plan`、质量门和 brief 使用。
- 交付物：`scripts/profile_resolver.py` alias 处理逻辑、缺口归档测试。
- 验收条件：
  - 给定 alias 列表 `["茅台", " 茅台 ", ""]`，当执行 resolver，则输出只包含一个 `茅台`。
  - 给定 alias 在冲突黑名单中，当执行 resolver，则该 alias 不进入 `approved_aliases`。
  - 给定三类 artifact 均无行业字段，当执行 resolver，则 `missing_fields` 包含且只包含允许键中的 `industry`。
  - 给定列表字段经去重和黑名单处理后为空，当执行 resolver，则 `missing_fields` 包含对应列表字段名。
- 预估工时：1 天
- 任务类型：业务逻辑

### T-PVD-001

- 任务标题：实现 provider 基类、统一 attempt 生成和原始新闻规范化工具
- 对应详细设计章节：§4.2.2, §4.3.1, §4.3.2, §4.3.3, §4.3.5, §4.3.6, §4.3.7, §4.3.9
- 前置依赖：T-INF-002, T-CFG-001, T-SEC-001
- 任务描述：实现 `NewsProvider.fetch` 基类协议、`ProviderQuery` 构造辅助、`ProviderFetchResult` 工厂、计时器、`ProviderAttempt.empty_reason` 分类和 `RawNewsItem.raw_id` 派生。封装 provider 错误到 `ProviderFetchResult(ok=False)`，只有无法构造 attempt 的代码错误才抛出。该任务不直接访问远端来源，只为后续 provider 实现提供统一真实调用框架。
- 交付物：`scripts/providers.py` 基类与公共工具、provider 公共单元测试。
- 验收条件：
  - 给定 provider 返回空表信号，当调用公共结果工厂，则得到 `ok=false`、`empty_reason="no_result"`、`raw_count=0`。
  - 给定超时异常，当调用错误转换函数，则得到 `empty_reason="timeout"` 且 `cancelled=false`。
  - 给定权限异常文本，当调用错误转换函数，则得到 `empty_reason="permission_missing"` 且错误内容经过脱敏。
  - 给定无原始 id 的新闻字段，当生成 `raw_id`，则由 endpoint、url、title、publish_time 的确定性组合派生。
- 预估工时：1.5 天
- 任务类型：集成

### T-PVD-002

- 任务标题：实现 AkShare stock_news_em provider
- 对应详细设计章节：§4.3.1, §4.3.3, §4.3.4, §4.3.5, §4.3.7, §8.3, §10
- 前置依赖：T-PVD-001
- 任务描述：实现 `AkshareStockNewsEmProvider.fetch`，真实调用 `ak.stock_news_em(symbol=query.ticker)`。将新闻标题、新闻内容、发布时间、文章来源、新闻链接、关键词映射为 `RawNewsItem` 字段，并按空表、超时、字段缺失和其他异常生成 `ProviderAttempt`。此 provider 为 P0，每个 pack 调用一次。
- 交付物：`scripts/providers.py` 中 `AkshareStockNewsEmProvider` 及字段映射测试。
- 验收条件：
  - 给定 AkShare 返回含标题、内容、发布时间、文章来源、链接的表格，当执行 `fetch`，则每行转换为 `RawNewsItem` 且 `data_source="akshare.stock_news_em"`。
  - 给定 AkShare 返回空表，当执行 `fetch`，则 `ProviderFetchResult.ok=false` 且 `empty_reason="no_result"`。
  - 给定返回表缺少标题列且无法恢复，当执行 `fetch`，则 `empty_reason="schema_changed"`。
  - 给定 provider 抛出普通异常，当执行 `fetch`，则返回 `ok=false`、`empty_reason="provider_error"` 且错误已脱敏。
- 预估工时：1.5 天
- 任务类型：集成

### T-PVD-003

- 任务标题：实现 AkShare stock_info_global_cls provider
- 对应详细设计章节：§4.3.1, §4.3.3, §4.3.4, §4.3.5, §4.3.7, §8.3, §10
- 前置依赖：T-PVD-001
- 任务描述：实现 `AkshareStockInfoGlobalClsProvider.fetch`，真实调用 `ak.stock_info_global_cls(symbol="全部")`。映射标题、内容、发布日期、发布时间，来源缺失时写入 `财联社电报`，URL 固定为 `None`。本地只做硬匹配前筛选所需字段保留，最终分桶仍交给匹配模块。
- 交付物：`scripts/providers.py` 中 `AkshareStockInfoGlobalClsProvider` 及字段映射测试。
- 验收条件：
  - 给定 AkShare 返回标题、内容、发布日期、发布时间，当执行 `fetch`，则 `publish_time` 优先由日期和时间拼接。
  - 给定来源字段缺失，当执行 `fetch`，则 `RawNewsItem.source="财联社电报"`。
  - 给定 AkShare 返回空表，当执行 `fetch`，则 `empty_reason="no_result"`。
  - 给定接口字段结构变化，当执行 `fetch`，则返回 `ok=false`、`empty_reason="schema_changed"`。
- 预估工时：1.5 天
- 任务类型：集成

### T-PVD-004

- 任务标题：实现 AkShare stock_info_global_em provider
- 对应详细设计章节：§4.3.1, §4.3.3, §4.3.4, §4.3.5, §4.3.7, §8.3, §10
- 前置依赖：T-PVD-001
- 任务描述：实现 `AkshareStockInfoGlobalEmProvider.fetch`，真实调用 `ak.stock_info_global_em()`。映射标题、摘要、发布时间、链接，来源缺失时写入 `东方财富快讯`。该 provider 为 P1，结果后续按 query plan 的公司、行业、宏观关键词过滤，无命中新闻由匹配模块拒收。
- 交付物：`scripts/providers.py` 中 `AkshareStockInfoGlobalEmProvider` 及字段映射测试。
- 验收条件：
  - 给定 AkShare 返回标题、摘要、发布时间、链接，当执行 `fetch`，则每行转换为 `RawNewsItem`。
  - 给定来源字段缺失，当执行 `fetch`，则 `RawNewsItem.source="东方财富快讯"`。
  - 给定返回空表，当执行 `fetch`，则 `empty_reason="no_result"`。
  - 给定调用超时，当执行 `fetch`，则 `empty_reason="timeout"` 且 `elapsed_ms` 为非负整数。
- 预估工时：1.5 天
- 任务类型：集成

### T-PVD-005

- 任务标题：实现 AkShare news_cctv provider
- 对应详细设计章节：§4.3.1, §4.3.3, §4.3.4, §4.3.5, §4.3.7, §8.3, §10
- 前置依赖：T-PVD-001
- 任务描述：实现 `AkshareNewsCctvProvider.fetch`，真实调用 `ak.news_cctv(date=end_date_yyyymmdd)`。映射 date、title、content，source 固定为 `新闻联播`，URL 固定为 `None`。该 provider 的结果只能进入 `policy_macro_news`，不得进入公司直连桶。
- 交付物：`scripts/providers.py` 中 `AkshareNewsCctvProvider` 及字段映射测试。
- 验收条件：
  - 给定 `end_date=2026-05-06`，当构造 provider 查询，则 AkShare 参数为 `date=20260506`。
  - 给定 AkShare 返回 date、title、content，当执行 `fetch`，则 `RawNewsItem.source="新闻联播"` 且 URL 为 `None`。
  - 给定空表，当执行 `fetch`，则 `empty_reason="no_result"`。
  - 给定字段缺失，当执行 `fetch`，则 `empty_reason="schema_changed"`。
- 预估工时：1 天
- 任务类型：集成

### T-PVD-006

- 任务标题：实现 Tushare anns_d 公告 provider
- 对应详细设计章节：§4.3.1, §4.3.3, §4.3.4, §4.3.5, §4.3.7, §7, §8.3, §10
- 前置依赖：T-PVD-001, T-CFG-001
- 任务描述：实现 `TushareAnnouncementsProvider.fetch`，token 缺失时不发远端请求并记录 `not_configured`。token 存在时真实调用 `ts.pro_api(token)` 和 `pro.anns_d(ts_code=exchange_ticker, start_date=YYYYMMDD, end_date=YYYYMMDD)`。映射 ann_date、ts_code、name、title、url、rec_time，并将权限不足、积分不足、超时、字段变化和其他异常归类到 `ProviderAttempt.empty_reason`。
- 交付物：`scripts/providers.py` 中 `TushareAnnouncementsProvider` 及 token、权限、字段映射测试。
- 验收条件：
  - 给定未配置 `CN_A_NEWS_TUSHARE_TOKEN`，当执行 `fetch`，则不调用远端并返回 `empty_reason="not_configured"`。
  - 给定 token 存在且返回公告表，当执行 `fetch`，则 `RawNewsItem.source="tushare.anns_d"`。
  - 给定 `ann_date` 缺失但 `rec_time` 存在，当执行 `fetch`，则使用 `rec_time` 作为发布时间后备值。
  - 给定 Tushare 返回权限或积分不足，当执行 `fetch`，则 `empty_reason="permission_missing"`。
- 预估工时：2 天
- 任务类型：集成

### T-SCH-001

- 任务标题：实现 provider 有限并行调度、总超时和 attempt 补齐
- 对应详细设计章节：§3.2, §4.2.4, §4.2.6, §4.3.5, §4.12, §5.1, §7, §8.2
- 前置依赖：T-PVD-002, T-PVD-003, T-PVD-004, T-PVD-005, T-PVD-006, T-CFG-001
- 任务描述：实现 `run_bounded_parallel` 和 `ensure_attempts_for_all_enabled_providers`。按 enabled provider 清单调用全部 provider，最大并发默认 3，单 provider timeout 10 秒，整包 timeout 20 秒；任何 provider 未完成时必须生成 attempt，总超时取消的 provider 标记 `cancelled=true`。调度不因 P0 或 P1 成功而跳过其他已启用来源。
- 交付物：`scripts/provider_scheduler.py`、并发调度单元测试和超时测试。
- 验收条件：
  - 给定 5 个 enabled provider，当执行调度，则输出 5 条 `ProviderAttempt`。
  - 给定最大并发为 3，当执行调度，则同时处于调用状态的 provider 数量不超过 3。
  - 给定总 timeout 触发且某 provider 未完成，当执行调度，则该 provider 的 attempt 为 `empty_reason="timeout"` 且 `cancelled=true`。
  - 给定一个 provider 成功且另一个 provider 失败，当执行调度，则两个结果都进入返回集合。
- 预估工时：2 天
- 任务类型：业务逻辑

### T-SCH-002

- 任务标题：实现同进程失败计数和 provider 冷却
- 对应详细设计章节：§4.2.6, §4.12, §7
- 前置依赖：T-SCH-001
- 任务描述：在 provider 调度层维护 `provider_fail_counter[(provider, endpoint)]` 与 `cooldown_until`。同一 endpoint 60 秒内连续失败达到默认阈值 3 次后进入冷却窗口；冷却期内不发远端请求，直接写 `empty_reason="rate_limited"`、`elapsed_ms=0`、`cancelled=false` 的 attempt。成功结果清理对应失败计数。
- 交付物：`scripts/provider_scheduler.py` 冷却逻辑及单元测试。
- 验收条件：
  - 给定同一 endpoint 连续 3 次失败，当第 4 次调度发生在 60 秒内，则不发远端请求并生成 `rate_limited` attempt。
  - 给定冷却窗口已过，当再次调度，则 provider 重新进入真实调用路径。
  - 给定 provider 成功返回，当更新调度状态，则该 endpoint 的连续失败计数归零。
  - 给定冷却期 attempt，当读取 `elapsed_ms` 和 `cancelled`，则分别为 0 和 false。
- 预估工时：1 天
- 任务类型：业务逻辑

### T-MAT-001

- 任务标题：实现 CN_A 查询计划生成
- 对应详细设计章节：§4.2.4, §4.4.1, §4.4.2, §4.4.3, §4.4.4, §4.10.5, §4.11, §7, §8.4
- 前置依赖：T-INF-002, T-PRF-002, T-CFG-002
- 任务描述：实现 `QueryPlanBuilder.build` 和伪码 `build_query_plan`。把 ticker、exchange_ticker、company_name、resolver 输出的 approved aliases、historical names 写入公司关键词；把行业写入行业关键词；从配置读取 CN_A 宏观词。不得从 worker 输入自行生成简称，黑名单 alias 不进入 `QueryPlan.company_keywords`。
- 交付物：`scripts/matching.py` 中 `QueryPlanBuilder` 及查询计划测试。
- 验收条件：
  - 给定 ticker `600519` 和 exchange ticker `600519.SH`，当生成 query plan，则两个代码均进入 `company_keywords`。
  - 给定 resolver 输出公司全称和已批准简称，当生成 query plan，则二者均进入公司关键词。
  - 给定 industry 为空，当生成 query plan，则行业关键词为空且不报错。
  - 给定黑名单 alias，当生成 query plan，则该 alias 不进入公司关键词。
- 预估工时：1 天
- 任务类型：业务逻辑

### T-MAT-002

- 任务标题：实现新闻硬匹配分桶与证据片段提取
- 对应详细设计章节：§4.4.1, §4.4.2, §4.4.3, §4.4.4, §4.4.5, §4.4.6, §4.4.7, §4.4.8, §8.4
- 前置依赖：T-MAT-001
- 任务描述：实现 `MatchEngine.classify`，按 ticker、exchange_ticker、company full name、approved alias、公告主体、行业关键词、宏观关键词顺序硬匹配。每条 accepted item 必须输出 `match_type`、原文连续片段 `match_evidence_span`、`match_confidence`、`bucket` 和实际命中词。无法匹配时返回 `bucket="rejected"`，行业词不得进入 `company_news`。
- 交付物：`scripts/matching.py` 中 `MatchEngine`、`span_from_text` 及匹配测试。
- 验收条件：
  - 给定标题包含 `600519`，当执行 `classify`，则 bucket 为 `company_news` 或公告桶，`match_type="ticker_exact"`。
  - 给定摘要只包含行业关键词，当执行 `classify`，则 bucket 为 `industry_news`，不进入 `company_news`。
  - 给定文本只包含宏观关键词，当执行 `classify`，则 bucket 为 `policy_macro_news`。
  - 给定文本无可接受关键词，当执行 `classify`，则 bucket 为 `rejected` 且 `match_evidence_span` 为空。
  - 给定 accepted item，当读取 `match_evidence_span`，则该片段是标题或摘要中的连续原文。
- 预估工时：2 天
- 任务类型：业务逻辑

### T-KWD-001

- 任务标题：实现中性关键词观察提取
- 对应详细设计章节：§2, §4.6.1, §4.6.2, §4.6.3, §4.6.4, §4.6.5, §4.6.6, §4.6.7, §4.6.8, §8.6
- 前置依赖：T-INF-002, T-CFG-002
- 任务描述：实现 `KeywordObservationExtractor.extract` 和伪码 `extract`。基于已校验 `KeywordRule` 对标题和摘要做确定性关键词匹配，输出去重保序的 `matched_terms` 和 `keyword_categories`。输出固定 `method="keyword_match"` 与 `is_sentiment_judgment=false`，不得生成方向性结论字段。
- 交付物：`scripts/keyword_observations.py`、关键词观察单元测试。
- 验收条件：
  - 给定标题包含 `分红` 和 `公告`，当执行 `extract`，则返回对应分类且保留首次命中顺序。
  - 给定规则列表为空，当执行 `extract`，则返回空主题列表且不失败。
  - 给定文本为空，当执行 `extract`，则返回空主题列表。
  - 给定任何输入，当读取结果，则 `method="keyword_match"` 且 `is_sentiment_judgment=false`。
- 预估工时：1 天
- 任务类型：业务逻辑

### T-DED-001

- 任务标题：实现新闻字段规范化和确定性去重
- 对应详细设计章节：§4.5.1, §4.5.2, §4.5.3, §4.5.4, §4.5.5, §4.5.6, §4.5.7, §4.5.8, §8.5
- 前置依赖：T-MAT-002, T-KWD-001, T-SEC-001, T-CFG-001
- 任务描述：实现 `Deduplicator.deduplicate` 和伪码 `deduplicate`。完成 URL、标题、来源、时间桶规范化，按 URL 精确、标题精确、标题+来源+时间桶、标题相似四级顺序合并重复项。合并时保留 `merged_from`、`DedupStats.removed_count`、`merged_raw_ids` 和 `merge_reasons`，相似标题阈值默认 0.92。
- 交付物：`scripts/dedup.py` 中 `Deduplicator`、规范化函数、去重测试。
- 验收条件：
  - 给定两条 URL 完全相同的新闻，当执行去重，则输出 1 条且 merge reason 包含 `url_exact`。
  - 给定两条规范化标题相同的新闻，当执行去重，则输出 1 条且 merge reason 包含 `title_exact`。
  - 给定标题、来源和时间桶相同的新闻，当执行去重，则输出 1 条且 merge reason 包含 `title_source_time`。
  - 给定标题相似度为 0.93 的两条新闻，当阈值为 0.92 并执行去重，则合并并记录 `title_similarity`。
  - 给定标题相似度低于阈值，当执行去重，则两条新闻均保留。
- 预估工时：2 天
- 任务类型：业务逻辑

### T-DED-002

- 任务标题：实现时间窗口过滤、排序和输出裁剪
- 对应详细设计章节：§4.5.1, §4.5.3, §4.5.4, §4.5.5, §4.5.6, §4.5.8, §7, §8.5
- 前置依赖：T-DED-001, T-CFG-001
- 任务描述：实现 `SortTrimProcessor.sort_and_trim` 和伪码 `sort_and_trim`。在不扩大日期窗口的前提下按闭区间过滤新闻，可解析发布时间按 bucket 优先级和发布时间倒序排列，缺失发布时间的条目保留在同 bucket 后部并写 `evidence_gap`。执行 pack JSON 上限和 reader brief 引用上限的裁剪，默认分别为 100 条和 20 条。
- 交付物：`scripts/dedup.py` 中 `SortTrimProcessor` 及排序裁剪测试。
- 验收条件：
  - 给定发布时间在窗口外的新闻，当执行排序裁剪，则该新闻不进入输出列表。
  - 给定发布时间缺失的 accepted 新闻，当执行排序裁剪，则该新闻保留并写入 `publish_time_missing` 缺口。
  - 给定 120 条 accepted 新闻且 JSON 上限为 100，当执行排序裁剪，则输出不超过 100 条。
  - 给定日期窗口最近 7 天，当执行排序裁剪，则处理过程不改变 request 的起止日期。
- 预估工时：1.5 天
- 任务类型：业务逻辑

### T-QLT-001

- 任务标题：实现质量门和失败分类
- 对应详细设计章节：§4.2.4, §4.2.6, §4.7.1, §4.7.2, §4.7.3, §4.7.4, §4.7.5, §4.7.6, §4.7.7, §4.7.8, §5.2, §5.3, §8.2
- 前置依赖：T-DED-002, T-SCH-001
- 任务描述：实现 `QualityGate.evaluate` 和伪码 `evaluate`。按 P0 全失败、公司直连缺失但背景存在、无可接受新闻、存在公司直连或公告四类规则输出 `complete`、`partial` 或 `failed`。填写 `directional_judgment_allowed`、计数、`missing_fields` 和 `warnings`，且 P0 全失败时 `ok=false`。
- 交付物：`scripts/quality.py`、质量门单元测试。
- 验收条件：
  - 给定两个 P0 attempt 均失败，当执行 `evaluate`，则 `quality.status="failed"` 且 `directional_judgment_allowed=false`。
  - 给定 P0 至少一个成功且只有行业或宏观新闻，当执行 `evaluate`，则 `quality.status="partial"`。
  - 给定 P0 至少一个成功且公司直连新闻数量大于 0，当执行 `evaluate`，则 `quality.status="complete"` 且 `directional_judgment_allowed=true`。
  - 给定 `provider_attempts` 缺少 P0 记录，当执行 `evaluate`，则按 provider 错误参与失败规则计算。
  - 给定 item 中出现方向性字段，当执行 `evaluate`，则返回失败资料包质量并写入 warning。
- 预估工时：1.5 天
- 任务类型：业务逻辑

### T-BRF-001

- 任务标题：实现 reader_brief 事实摘要生成
- 对应详细设计章节：§2, §4.8.1, §4.8.2, §4.8.3, §4.8.4, §4.8.5, §4.8.6, §4.8.7, §4.8.8, §5.2, §5.3, §8.6
- 前置依赖：T-QLT-001, T-CFG-001
- 任务描述：实现 `ReaderBriefBuilder.build` 和伪码 `build`。brief 只能使用 `BriefInput` 内的计数、来源、时间、匹配原因和缺口，输出中文事实摘要。对 partial 和 failed 明确说明限制；items 为空时只写失败原因和缺口；发布时间或来源缺失时写字段缺失，不编造新闻事实。
- 交付物：`scripts/reader_brief.py`、brief 模板和单元测试。
- 验收条件：
  - 给定 complete 质量结果，当生成 brief，则包含覆盖日期、原始条数、去重条数、接受条数和 provider 返回数量。
  - 给定 partial 质量结果，当生成 brief，则包含“不可用于公司方向性新闻判断”的限制说明。
  - 给定 failed 且 items 为空，当生成 brief，则只包含失败原因、provider 状态和缺口说明。
  - 给定发布时间缺失的新闻，当生成 brief，则写“发布时间缺失”。
  - 给定任意 brief，当统计中文字符数，则不超过 4000。
- 预估工时：1.5 天
- 任务类型：业务逻辑

### T-SVC-001

- 任务标题：实现 NewsDataService.build_pack 编排主流程
- 对应详细设计章节：§4.2.1, §4.2.2, §4.2.3, §4.2.4, §4.2.5, §4.2.6, §4.2.7, §4.2.8, §5.1, §5.2, §5.3, §6, §8.2
- 前置依赖：T-SCH-002, T-MAT-001, T-MAT-002, T-KWD-001, T-DED-001, T-DED-002, T-QLT-001, T-BRF-001
- 任务描述：实现 `NewsDataService.build_pack` 和 DLD 伪码 `build_pack`。校验 request，生成 query plan，按调度器收集全部 provider 结果，匹配、添加关键词观察、去重、排序裁剪、质量评估、生成 reader_brief，并返回完整 `NewsDataPack`。服务层自身不写 evidence，不调用 LLM，不把 P1/P2 结果包装成 P0 成功。
- 交付物：`scripts/news_data_pack.py` 或独立 service 模块中的 `NewsDataService`、编排单元测试。
- 验收条件：
  - 给定 P0 至少一个成功且公司直连新闻存在，当执行 `build_pack`，则返回 `ok=true`、`quality.status="complete"`。
  - 给定 P0 至少一个成功且只有行业或宏观背景，当执行 `build_pack`，则返回 `ok=true`、`quality.status="partial"`。
  - 给定两个 P0 均失败，当执行 `build_pack`，则返回 `ok=false`、`quality.status="failed"`，且 P1/P2 attempts 仍被记录。
  - 给定非法日期窗口，当执行 `build_pack`，则返回或抛出 `E_INVALID_INPUT`。
  - 给定启用 provider 清单，当执行 `build_pack`，则 `provider_attempts` 条数等于启用 provider 数量。
- 预估工时：3 天
- 任务类型：业务逻辑

### T-EVD-001

- 任务标题：实现 evidence 原子写入、内容校验和 call_id 隔离
- 对应详细设计章节：§4.1.7, §4.9.1, §4.9.2, §4.9.3, §4.9.4, §4.9.5, §4.9.6, §4.9.7, §4.9.8, §5.4, §6, §8.1
- 前置依赖：T-INF-002, T-SEC-001
- 任务描述：实现 `EvidenceWriter.write_pack` 和伪码 `write_pack`。按 `{evidence_root}/{run_id}/{stage}/{worker_id}/{call_id}/` 写入 `news_data_pack.json`、`provider_attempts.json` 和 provider raw refs，先写临时文件再原子替换。计算 pack JSON 内容哈希，验证 raw refs 位于 evidence 根目录内，同一 call 目录已存在时失败，防止证据覆盖。
- 交付物：`scripts/evidence.py`、原子写入和路径安全测试。
- 验收条件：
  - 给定合法 pack 和 runtime context，当执行 `write_pack`，则生成 `news_data_pack.json`、`provider_attempts.json` 和非空 `content_hash`。
  - 给定 raw ref 位于 evidence 根目录外，当执行 `write_pack`，则抛出 `E_EVIDENCE_WRITE_FAILED`。
  - 给定同一 `run_id/stage/worker_id/call_id` 重复写入，当第二次执行 `write_pack`，则失败且不覆盖已有文件。
  - 给定 100 MiB 以下 pack JSON，当执行写入，则 P95 写入时间目标为 1 秒内。
  - 给定 pack 中含密钥样式字段，当写入 evidence，则落盘文件中不含密钥原值。
- 预估工时：2 天
- 任务类型：数据层

### T-ADP-001

- 任务标题：实现 run_news_data_pack 工具适配逻辑
- 对应详细设计章节：§4.1.1, §4.1.2, §4.1.3, §4.1.4, §4.1.5, §4.1.6, §4.1.8, §5.1, §5.2, §5.3, §8.1
- 前置依赖：T-PRF-002, T-SVC-001, T-EVD-001
- 任务描述：实现 `run_news_data_pack(tool_input, context)`。校验 `worker_id`、`tool_name`、market、ticker 和日期，调用 `ApprovedProfileResolver.resolve`，执行 `normalize_tool_input` 形成 `NewsDataPackRequest`，调用 `NewsDataService.build_pack`，再调用 `EvidenceWriter.write_pack` 并把 evidence refs 写回 pack。上下文不匹配和 evidence 写失败抛给 runtime，市场或入参错误返回失败资料包。
- 交付物：`scripts/news_data_pack.py` 中工具适配函数及适配层测试。
- 验收条件：
  - 给定 `context.worker_id!="news_analyst"`，当调用 `run_news_data_pack`，则抛出 `E_CONTEXT_MISMATCH`。
  - 给定 `tool_input.market!="CN_A"`，当调用 `run_news_data_pack`，则返回 `ok=false` 的失败资料包。
  - 给定非法 ticker，当调用 `run_news_data_pack`，则返回包含 `E_INVALID_INPUT` 的失败资料包并写缺口证据。
  - 给定 service 返回 pack 且 evidence 写入成功，当调用 `run_news_data_pack`，则返回 dict 中包含 `evidence.pack_path` 和 `content_hash`。
  - 给定 evidence 写入失败，当调用 `run_news_data_pack`，则抛出 `E_EVIDENCE_WRITE_FAILED`，不返回成功资料包。
- 预估工时：2 天
- 任务类型：接口层

### T-ADP-002

- 任务标题：实现 news_data_pack.py JSON I/O 脚本入口和结构化错误返回
- 对应详细设计章节：§4.1.3, §4.1.6, §4.1.8, §4.1.9, §5.1, §8.1
- 前置依赖：T-ADP-001, T-INF-001
- 任务描述：实现 `scripts/news_data_pack.py` 的 stdin/stdout 协议：stdin 接收包含 `tool_input` 与 `runtime_context` 的单个 JSON 对象，stdout 输出单个 JSON 响应对象。成功时 exit code 为 0，失败时 exit code 非 0 且 stderr 输出可审计短错误；失败 JSON 保持结构化错误码、状态和缺口信息。工具适配层 timeout 目标为 25 秒，大于整包 timeout。
- 交付物：`scripts/news_data_pack.py` CLI main、脚本 I/O 测试。
- 验收条件：
  - 给定合法 stdin JSON，当执行脚本，则 stdout 是单个可解析 JSON 对象且 exit code 为 0。
  - 给定非法 JSON stdin，当执行脚本，则 exit code 非 0，stderr 包含短错误，stdout 不输出破碎 JSON。
  - 给定 market 非 CN_A 的 stdin，当执行脚本，则 stdout 包含结构化失败资料包。
  - 给定整包 timeout 为 20 秒，当检查脚本适配层 timeout 配置，则工具 timeout 为 25 秒。
- 预估工时：1.5 天
- 任务类型：接口层

### T-OBS-001

- 任务标题：实现 metrics、结构化日志和 trace 埋点
- 对应详细设计章节：§4.1.8, §4.2.8, §4.4.8, §4.5.8, §4.6.8, §4.7.8, §4.9.8, §4.14
- 前置依赖：T-SVC-001, T-EVD-001, T-ADP-001
- 任务描述：在工具适配、服务编排、provider 调度、匹配、去重、关键词观察、质量门和 evidence 写入路径加入 DLD 指定指标、结构化日志字段和 trace span。普通日志只记录 run_id、stage、worker_id、call_id、tool_name、endpoint、状态、数量、错误类型、路径和大小，不记录新闻全文和密钥。指标包括 provider attempt、provider 耗时、pack status、missing field 和公司直连数量。
- 交付物：`scripts/observability.py`、各模块埋点接入、可观测性测试。
- 验收条件：
  - 给定一次 provider 成功 attempt，当执行服务编排，则递增 `news_provider_attempt_total{provider,endpoint,status,empty_reason}`。
  - 给定质量状态为 failed 的 pack，当执行质量评估，则递增 `news_pack_status_total{status="failed"}`。
  - 给定普通日志输出，当检查日志内容，则不包含新闻正文和密钥原值。
  - 给定一次工具调用，当检查 trace，则包含 `news_data_pack.tool_adapter` 和 `news_data_pack.service` span。
- 预估工时：2 天
- 任务类型：非功能性

### T-OPS-001

- 任务标题：实现 provider 健康诊断脚本
- 对应详细设计章节：§4.14, §7, §8.3, §8.7, §10
- 前置依赖：T-PVD-002, T-PVD-003, T-PVD-004, T-PVD-005, T-PVD-006, T-CFG-001, T-SEC-001
- 任务描述：实现 `scripts/health_check.py`，支持 AkShare `stock_news_em`、`stock_info_global_cls`、Tushare `anns_d` 等命令参数。脚本真实调用对应 provider，输出 `cn_a_news_health_check.v1` JSON，保存到 evidence 或 `docs/evidence/cn_a_news/provider-health-check/{yyyy-mm-dd}/`。exit code 0 表示 provider 返回可解析结果，1 表示诊断执行完成但 provider 结果不可用于通过，2 表示参数错误或运行异常且未形成有效 JSON。
- 交付物：`scripts/health_check.py`、诊断脚本测试和命令说明。
- 验收条件：
  - 给定 `--provider akshare.stock_news_em --ticker 600519`，当执行脚本且 provider 返回可解析结果，则 exit code 为 0 且 JSON `schema_version="cn_a_news_health_check.v1"`。
  - 给定 Tushare token 未配置，当执行 `--provider tushare.anns_d --ts-code 600519.SH`，则 exit code 为 1 且 `attempt.empty_reason="not_configured"`。
  - 给定缺少必填参数，当执行脚本，则 exit code 为 2 且错误信息不含密钥。
  - 给定指定 evidence 输出目录，当执行脚本，则生成 `provider-health-check` JSON 文件。
- 预估工时：2 天
- 任务类型：非功能性

### T-REL-001

- 任务标题：实现 skill 版本发布、回滚和可见工具校验
- 对应详细设计章节：§4.1.9, §4.15, §5.1, §8.1, §8.7, §9, §10
- 前置依赖：T-ADP-002, T-OBS-001
- 任务描述：实现版本一致性校验和发布回滚辅助命令，校验 `SKILL.md` 与 manifest 中 `version`、`skill_version`、`tool_exports`、`workers`、`entrypoint`、`schema_version` 一致。加入可见工具运行证据检查，断言 `news_analyst` 当前 turn 只看到 `news_news_data_pack` 与 `openviking_write_material`。回滚辅助命令能将 manifest 指向上一个稳定版本并触发装载校验。
- 交付物：版本校验脚本、回滚辅助脚本或命令、manifest 可见工具校验测试。
- 验收条件：
  - 给定 `SKILL.md` 与 manifest 版本一致，当执行版本校验，则 exit code 为 0。
  - 给定 `SKILL.md` 与 manifest 版本不一致，当执行版本校验，则 exit code 非 0 并指出不一致字段。
  - 给定运行证据 visible tools 多出第三个工具，当执行可见工具校验，则校验失败并列出多出的工具名。
  - 给定上一个稳定版本号，当执行回滚辅助命令，则 manifest 的 `skill_version` 指向该版本并通过装载校验。
- 预估工时：1.5 天
- 任务类型：非功能性

### T-INT-001

- 任务标题：实现 600519 单标的集成验证与 evidence 收集
- 对应详细设计章节：§3, §4.1.9, §5.1, §5.2, §5.3, §5.4, §6, §8.7, §9, §10, §11
- 前置依赖：T-ADP-002, T-OPS-001, T-REL-001
- 任务描述：实现并运行 DLD 指定样本集成验证：ticker 为 600519，公司名为贵州茅台，market 为 CN_A，日期为 2026-05-06，窗口为最近 7 天。验证 OpenClaw turn 内可见工具、`news_news_data_pack` raw output、provider attempts、accepted news list、missing fields、final prompt、LLM back、report、tool calls、visible tools 和 OpenViking receipt 均保存到 evidence。并发重试场景中验证不同 call_id 写入不同目录，workflow 只接受当前 dispatch 对应 call_id。
- 交付物：集成验证脚本或测试、600519 验证 evidence、集成验证报告。
- 验收条件：
  - 给定 600519 集成验证输入，当执行验证脚本，则 evidence 中存在 `news_data_pack.json`、`provider_attempts.json` 和 accepted news list。
  - 给定运行证据，当检查 visible tools，则只包含 `news_news_data_pack` 与 `openviking_write_material`。
  - 给定同一 run 产生两个不同 call_id，当检查 evidence 目录，则两个 call 写入不同目录且互不覆盖。
  - 给定 P0 全失败的真实运行结果，当检查 pack，则 `ok=false`、`quality.status="failed"`，reader_brief 只说明失败原因和缺口。
  - 给定公司直连缺失但背景资料存在的真实或受控集成输入，当检查 pack，则 `quality.status="partial"` 且 `directional_judgment_allowed=false`。
- 预估工时：3 天
- 任务类型：集成

## 第三部分，依赖关系图

### 第 1 层：可立即并行开始

- 可并行：T-INF-001

### 第 2 层：基础模型、配置、安全

- 依赖 T-INF-001 后可并行：T-INF-002, T-CFG-001, T-SEC-001

### 第 3 层：配置 schema、profile 首段、provider 公共层

- 依赖第 2 层后可并行：T-CFG-002, T-PRF-001, T-PVD-001

### 第 4 层：profile 完整规则与五个 provider

- 依赖 T-PRF-001/T-CFG-002：T-PRF-002
- 依赖 T-PVD-001 后可并行：T-PVD-002, T-PVD-003, T-PVD-004, T-PVD-005, T-PVD-006

### 第 5 层：provider 调度与查询计划

- 依赖五个 provider 和配置：T-SCH-001
- 依赖 T-PRF-002/T-CFG-002/T-INF-002：T-MAT-001

### 第 6 层：冷却、匹配、关键词观察

- 依赖 T-SCH-001：T-SCH-002
- 依赖 T-MAT-001：T-MAT-002
- 依赖 T-CFG-002/T-INF-002：T-KWD-001

### 第 7 层：去重、排序、质量、brief

- 依赖 T-MAT-002/T-KWD-001/T-SEC-001/T-CFG-001：T-DED-001
- 依赖 T-DED-001/T-CFG-001：T-DED-002
- 依赖 T-DED-002/T-SCH-001：T-QLT-001
- 依赖 T-QLT-001/T-CFG-001：T-BRF-001

### 第 8 层：服务编排与 evidence

- 依赖调度和业务模块：T-SVC-001
- 依赖模型和安全：T-EVD-001
- T-SVC-001 与 T-EVD-001 可并行，只要各自依赖已完成。

### 第 9 层：工具适配与脚本入口

- 依赖 T-PRF-002/T-SVC-001/T-EVD-001：T-ADP-001
- 依赖 T-ADP-001/T-INF-001：T-ADP-002

### 第 10 层：可观测性、诊断、发布、集成

- 依赖 T-SVC-001/T-EVD-001/T-ADP-001：T-OBS-001
- 依赖五个 provider/T-CFG-001/T-SEC-001：T-OPS-001
- 依赖 T-ADP-002/T-OBS-001：T-REL-001
- 依赖 T-ADP-002/T-OPS-001/T-REL-001：T-INT-001

依赖图无循环。

## 第四部分，里程碑建议

### 里程碑 M1：skill 基础与可信输入

- 包含任务：T-INF-001, T-INF-002, T-CFG-001, T-CFG-002, T-SEC-001, T-PRF-001, T-PRF-002
- 达成条件：skill 目录和元数据存在；核心模型可稳定序列化；配置和 YAML schema 可校验；resolver 只从已批准 artifact 生成 profile，并输出 missing_fields。
- 建议完成时间：单人开发 6 个日历天。

### 里程碑 M2：真实 provider 与调度

- 包含任务：T-PVD-001, T-PVD-002, T-PVD-003, T-PVD-004, T-PVD-005, T-PVD-006, T-SCH-001, T-SCH-002
- 达成条件：五个 provider 均可返回 `ProviderFetchResult`；所有 enabled provider 均有 attempt；并发、timeout、取消和冷却规则通过测试。
- 建议完成时间：单人开发 10 个日历天。

### 里程碑 M3：资料包业务流水线

- 包含任务：T-MAT-001, T-MAT-002, T-KWD-001, T-DED-001, T-DED-002, T-QLT-001, T-BRF-001, T-SVC-001
- 达成条件：`NewsDataService.build_pack` 可输出完整 `NewsDataPack`；complete、partial、failed 三类质量状态可复现；brief 只包含事实型资料摘要。
- 建议完成时间：单人开发 12 个日历天。

### 里程碑 M4：工具接入与 evidence 闭环

- 包含任务：T-EVD-001, T-ADP-001, T-ADP-002, T-OBS-001
- 达成条件：OpenClaw 工具入口可通过 JSON I/O 调用；每次调用有 evidence refs；普通日志不含新闻正文和密钥；关键 metrics 和 trace span 可观测。
- 建议完成时间：单人开发 7 个日历天。

### 里程碑 M5：运维验证与集成验收

- 包含任务：T-OPS-001, T-REL-001, T-INT-001
- 达成条件：health check 可诊断真实 provider；版本发布和回滚校验可执行；600519 单标的集成验证保存 DLD 要求 evidence。
- 建议完成时间：单人开发 6 个日历天。

## 第五部分，风险与阻塞项

### 显式标记提取

| 标记 | 结果 |
|---|---|
| HLD-GAP | DLD 全文未发现 |
| BLOCKED | DLD 全文未发现 |

### 隐含外部依赖与不确定项

| 风险或不确定项 | 影响任务 | 建议处理顺序 | 临时应对方案 |
|---|---|---|---|
| AkShare 远端接口字段可能变化或访问不稳定 | T-PVD-002, T-PVD-003, T-PVD-004, T-PVD-005, T-SCH-001, T-OPS-001, T-INT-001 | 先完成 provider 字段映射测试，再实现 health check，最后跑单标的集成 | 真实返回结构变化时记录 `schema_changed` attempt，并用 health check evidence 定位字段差异 |
| Tushare token 或权限可能缺失 | T-PVD-006, T-OPS-001, T-INT-001 | 先实现 `not_configured` 和 `permission_missing` 分类，再接入集成验收 | token 缺失时不发远端请求，真实记录 `not_configured` attempt，资料包不得把公告来源记为成功 |
| OpenClaw manifest/stage policy 文件位置需按仓库现状确认 | T-INF-001, T-REL-001, T-INT-001 | 开工首日定位现有 manifest 约定，再写版本和可见工具校验 | 用版本校验脚本读取实际 manifest 文件，校验失败则阻止发布 |
| evidence 根路径由 runtime 注入，DLD 未定义新增服务协议 | T-EVD-001, T-ADP-001, T-INT-001 | 先实现 `ToolRuntimeContext.evidence_root` 必填校验，再做写入路径测试 | 缺少 evidence_root 时抛出 `E_EVIDENCE_WRITE_FAILED`，不返回成功资料包 |
| metrics 后端类型未在 DLD 中指定 | T-OBS-001, T-REL-001 | 先查仓库现有 metrics 封装，再实现 DLD 指标名 | 若仓库无统一后端，先落结构化指标事件和 health check JSON，并保持指标名与标签稳定 |
| 600519 指定日期窗口在真实运行时可能无公司直连新闻 | T-INT-001 | 先跑 provider health check，再跑完整 OpenClaw turn | 真实结果若为 partial 或 failed，按质量门如实保存 evidence 和 reader_brief，不改变日期窗口 |

## 第六部分，覆盖矩阵

| 详细设计章节编号 | 章节摘要 | 对应任务编号列表 | 覆盖状态 |
|---|---|---|---|
| §1 | 文档元信息和版本 | T-INF-001, T-REL-001 | 已覆盖 |
| §2 | 术语表与内部约定 | T-INF-001, T-INF-002, T-KWD-001, T-BRF-001 | 已覆盖 |
| §3 | 系统上下文与部署视图 | T-INF-001, T-ADP-001, T-ADP-002, T-INT-001 | 已覆盖 |
| §3.1 | 部署拓扑、节点职责、代码落点 | T-INF-001, T-PVD-001, T-EVD-001, T-ADP-001 | 已覆盖 |
| §3.2 | 资源预估和 provider 调度默认值 | T-CFG-001, T-SCH-001, T-EVD-001 | 已覆盖 |
| §4 | 模块详细设计总览 | T-INF-002, T-SVC-001, T-INT-001 | 已覆盖 |
| §4.1 | OpenClaw 工具适配模块 | T-INF-001, T-ADP-001, T-ADP-002 | 已覆盖 |
| §4.1.1 | 工具职责与边界 | T-ADP-001, T-PRF-001 | 已覆盖 |
| §4.1.2 | ToolRuntimeContext 与 ToolInput | T-INF-002 | 已覆盖 |
| §4.1.3 | run_news_data_pack 接口和错误码 | T-INF-002, T-ADP-001, T-ADP-002 | 已覆盖 |
| §4.1.4 | run_news_data_pack 核心算法 | T-ADP-001 | 已覆盖 |
| §4.1.5 | 工具适配状态机 | T-ADP-001, T-ADP-002 | 已覆盖 |
| §4.1.6 | 工具适配错误处理 | T-ADP-001, T-ADP-002 | 已覆盖 |
| §4.1.7 | 工具证据文件设计 | T-EVD-001 | 已覆盖 |
| §4.1.8 | 工具非功能性设计 | T-OBS-001, T-ADP-002, T-EVD-001 | 已覆盖 |
| §4.1.9 | OpenClaw skill 注册与部署细节 | T-INF-001, T-ADP-002, T-REL-001 | 已覆盖 |
| §4.2 | CN_A news data service 编排模块 | T-SVC-001 | 已覆盖 |
| §4.2.1 | 编排职责与边界 | T-SVC-001 | 已覆盖 |
| §4.2.2 | 编排核心数据结构 | T-INF-002 | 已覆盖 |
| §4.2.3 | NewsDataService.build_pack 接口 | T-SVC-001 | 已覆盖 |
| §4.2.4 | build_pack 核心算法 | T-SVC-001, T-SCH-001, T-MAT-002, T-DED-001, T-DED-002, T-QLT-001, T-BRF-001 | 已覆盖 |
| §4.2.5 | 编排状态机 | T-SVC-001 | 已覆盖 |
| §4.2.6 | 编排错误处理与冷却策略 | T-SVC-001, T-SCH-002, T-QLT-001 | 已覆盖 |
| §4.2.7 | 编排数据存储设计 | T-EVD-001 | 已覆盖 |
| §4.2.8 | 编排非功能性设计 | T-OBS-001 | 已覆盖 |
| §4.3 | Provider 访问模块 | T-PVD-001, T-PVD-002, T-PVD-003, T-PVD-004, T-PVD-005, T-PVD-006 | 已覆盖 |
| §4.3.1 | Provider 职责与边界 | T-PVD-001, T-PVD-002, T-PVD-003, T-PVD-004, T-PVD-005, T-PVD-006 | 已覆盖 |
| §4.3.2 | ProviderQuery、RawNewsItem、ProviderFetchResult | T-INF-002, T-PVD-001 | 已覆盖 |
| §4.3.3 | NewsProvider.fetch 接口 | T-PVD-001 | 已覆盖 |
| §4.3.4 | 五个 provider 调用与字段映射 | T-PVD-002, T-PVD-003, T-PVD-004, T-PVD-005, T-PVD-006 | 已覆盖 |
| §4.3.5 | provider.fetch 核心算法 | T-PVD-001, T-PVD-002, T-PVD-003, T-PVD-004, T-PVD-005, T-PVD-006, T-SCH-001 | 已覆盖 |
| §4.3.6 | provider 状态机 | T-PVD-001, T-SCH-001 | 已覆盖 |
| §4.3.7 | provider 错误处理 | T-PVD-001, T-SEC-001, T-PVD-002, T-PVD-003, T-PVD-004, T-PVD-005, T-PVD-006 | 已覆盖 |
| §4.3.8 | provider 证据存储设计 | T-EVD-001 | 已覆盖 |
| §4.3.9 | provider 非功能性设计 | T-SEC-001, T-OBS-001, T-OPS-001 | 已覆盖 |
| §4.4 | 查询计划与匹配模块 | T-MAT-001, T-MAT-002 | 已覆盖 |
| §4.4.1 | 匹配职责与边界 | T-MAT-001, T-MAT-002 | 已覆盖 |
| §4.4.2 | MatchResult 数据结构 | T-INF-002 | 已覆盖 |
| §4.4.3 | QueryPlanBuilder 与 MatchEngine 接口 | T-MAT-001, T-MAT-002 | 已覆盖 |
| §4.4.4 | build_query_plan 与 classify 算法 | T-MAT-001, T-MAT-002 | 已覆盖 |
| §4.4.5 | 匹配状态机 | T-MAT-002 | 已覆盖 |
| §4.4.6 | 匹配错误处理 | T-MAT-002, T-QLT-001 | 已覆盖 |
| §4.4.7 | 匹配数据存储设计 | T-INF-002, T-EVD-001 | 已覆盖 |
| §4.4.8 | 匹配非功能性设计 | T-OBS-001 | 已覆盖 |
| §4.5 | 去重、排序与裁剪模块 | T-DED-001, T-DED-002 | 已覆盖 |
| §4.5.1 | 去重排序职责与边界 | T-DED-001, T-DED-002 | 已覆盖 |
| §4.5.2 | KeywordObservations、NewsItem、DedupStats | T-INF-002 | 已覆盖 |
| §4.5.3 | Deduplicator 与 SortTrimProcessor 接口 | T-DED-001, T-DED-002 | 已覆盖 |
| §4.5.4 | deduplicate 与 sort_and_trim 算法 | T-DED-001, T-DED-002 | 已覆盖 |
| §4.5.5 | 去重排序状态机 | T-DED-001, T-DED-002 | 已覆盖 |
| §4.5.6 | 去重排序错误处理 | T-DED-001, T-DED-002 | 已覆盖 |
| §4.5.7 | 去重统计存储 | T-EVD-001, T-SVC-001 | 已覆盖 |
| §4.5.8 | 去重排序非功能性设计 | T-CFG-001, T-OBS-001 | 已覆盖 |
| §4.6 | 中性关键词观察模块 | T-KWD-001 | 已覆盖 |
| §4.6.1 | 关键词观察职责与边界 | T-KWD-001 | 已覆盖 |
| §4.6.2 | KeywordRule 与 KeywordObservationResult | T-INF-002, T-KWD-001 | 已覆盖 |
| §4.6.3 | KeywordObservationExtractor 接口 | T-KWD-001 | 已覆盖 |
| §4.6.4 | extract 算法 | T-KWD-001 | 已覆盖 |
| §4.6.5 | 关键词观察状态机 | T-KWD-001 | 已覆盖 |
| §4.6.6 | 关键词观察错误处理 | T-KWD-001 | 已覆盖 |
| §4.6.7 | 关键词观察存储 | T-INF-002, T-EVD-001 | 已覆盖 |
| §4.6.8 | 关键词观察非功能性设计 | T-OBS-001 | 已覆盖 |
| §4.7 | 质量门与失败分类模块 | T-QLT-001 | 已覆盖 |
| §4.7.1 | 质量门职责与边界 | T-QLT-001 | 已覆盖 |
| §4.7.2 | QualityInput 与 QualityDecision | T-INF-002 | 已覆盖 |
| §4.7.3 | QualityGate.evaluate 接口 | T-QLT-001 | 已覆盖 |
| §4.7.4 | evaluate 算法 | T-QLT-001 | 已覆盖 |
| §4.7.5 | 质量状态机 | T-QLT-001 | 已覆盖 |
| §4.7.6 | 质量门错误处理 | T-QLT-001 | 已覆盖 |
| §4.7.7 | 质量结果存储 | T-INF-002, T-EVD-001 | 已覆盖 |
| §4.7.8 | 质量门非功能性设计 | T-OBS-001 | 已覆盖 |
| §4.8 | reader_brief 生成模块 | T-BRF-001 | 已覆盖 |
| §4.8.1 | brief 职责与边界 | T-BRF-001 | 已覆盖 |
| §4.8.2 | BriefInput 与 BriefSection | T-INF-002 | 已覆盖 |
| §4.8.3 | ReaderBriefBuilder.build 接口 | T-BRF-001 | 已覆盖 |
| §4.8.4 | build 算法 | T-BRF-001 | 已覆盖 |
| §4.8.5 | brief 状态机 | T-BRF-001 | 已覆盖 |
| §4.8.6 | brief 错误处理 | T-BRF-001 | 已覆盖 |
| §4.8.7 | brief 存储 | T-INF-002, T-EVD-001 | 已覆盖 |
| §4.8.8 | brief 非功能性设计 | T-BRF-001, T-OBS-001 | 已覆盖 |
| §4.9 | 证据落地模块 | T-EVD-001 | 已覆盖 |
| §4.9.1 | evidence 职责与边界 | T-EVD-001 | 已覆盖 |
| §4.9.2 | EvidenceWriteRequest 与 EvidenceRefs | T-INF-002, T-EVD-001 | 已覆盖 |
| §4.9.3 | EvidenceWriter.write_pack 接口 | T-EVD-001 | 已覆盖 |
| §4.9.4 | write_pack 算法 | T-EVD-001 | 已覆盖 |
| §4.9.5 | evidence 状态机 | T-EVD-001 | 已覆盖 |
| §4.9.6 | evidence 错误处理 | T-EVD-001 | 已覆盖 |
| §4.9.7 | evidence 文件结构和生命周期 | T-EVD-001 | 已覆盖 |
| §4.9.8 | evidence 非功能性设计 | T-EVD-001, T-OBS-001 | 已覆盖 |
| §4.10 | ApprovedProfileResolver 模块 | T-PRF-001, T-PRF-002 | 已覆盖 |
| §4.10.1 | resolver 职责与边界 | T-PRF-001 | 已覆盖 |
| §4.10.2 | resolver 接口 | T-PRF-001 | 已覆盖 |
| §4.10.3 | 字段解析优先级 | T-PRF-001 | 已覆盖 |
| §4.10.4 | 决策顺序、冲突处理、空值规则 | T-PRF-001 | 已覆盖 |
| §4.10.5 | alias 去重、黑名单、worker 边界 | T-PRF-002 | 已覆盖 |
| §4.10.6 | missing_fields 归档规则 | T-PRF-002 | 已覆盖 |
| §4.11 | 配置加载与 schema 校验模块 | T-CFG-001, T-CFG-002 | 已覆盖 |
| §4.11.1 | keyword_categories.yaml schema | T-CFG-002 | 已覆盖 |
| §4.11.2 | alias_rules.yaml schema | T-CFG-002 | 已覆盖 |
| §4.11.3 | 配置加载失败行为 | T-CFG-002, T-SVC-001 | 已覆盖 |
| §4.12 | timeout、取消与同进程冷却模块 | T-SCH-001, T-SCH-002 | 已覆盖 |
| §4.13 | sanitize_error 与 URL 校验模块 | T-SEC-001 | 已覆盖 |
| §4.14 | metrics、告警与健康诊断 | T-OBS-001, T-OPS-001 | 已覆盖 |
| §4.15 | skill 版本发布与回滚 | T-INF-001, T-REL-001 | 已覆盖 |
| §5 | 模块间交互设计 | T-SVC-001, T-EVD-001, T-ADP-001, T-INT-001 | 已覆盖 |
| §5.1 | 正常流程 | T-SVC-001, T-EVD-001, T-ADP-001, T-INT-001 | 已覆盖 |
| §5.2 | P0 全失败流程 | T-QLT-001, T-BRF-001, T-SVC-001, T-INT-001 | 已覆盖 |
| §5.3 | 公司直连缺失但背景存在 | T-MAT-002, T-QLT-001, T-BRF-001, T-SVC-001, T-INT-001 | 已覆盖 |
| §5.4 | 并发竞争场景 | T-EVD-001, T-INT-001 | 已覆盖 |
| §6 | 数据库总体设计 | T-INF-002, T-EVD-001, T-SVC-001 | 已覆盖 |
| §7 | 配置与环境管理 | T-CFG-001, T-CFG-002, T-PVD-006, T-SCH-001, T-DED-002 | 已覆盖 |
| §8 | 测试策略指引 | 全部任务的验收条件, T-INT-001 | 已覆盖 |
| §8.1 | 工具适配模块测试 | T-ADP-001, T-ADP-002, T-EVD-001, T-REL-001 | 已覆盖 |
| §8.2 | 服务编排模块测试 | T-SVC-001, T-QLT-001 | 已覆盖 |
| §8.3 | Provider 模块测试 | T-PVD-002, T-PVD-003, T-PVD-004, T-PVD-005, T-PVD-006, T-OPS-001 | 已覆盖 |
| §8.4 | 匹配模块测试 | T-MAT-001, T-MAT-002 | 已覆盖 |
| §8.5 | 去重排序模块测试 | T-DED-001, T-DED-002 | 已覆盖 |
| §8.6 | reader_brief 模块测试 | T-KWD-001, T-BRF-001 | 已覆盖 |
| §8.7 | 集成验证 | T-REL-001, T-OPS-001, T-INT-001 | 已覆盖 |
| §9 | DLD 对 HLD 的覆盖矩阵 | T-INT-001, T-REL-001 | 已覆盖 |
| §10 | 开放问题与未决项 | T-INF-001, T-CFG-001, T-PVD-006, T-SCH-001, T-DED-001, T-EVD-001 | 已覆盖 |
| §11 | DLD 自检报告 | T-INT-001, 全部任务的验收条件 | 已覆盖 |
| §11.1 | HLD 名词和概念覆盖 | 覆盖矩阵全部任务 | 已覆盖 |
| §11.2 | 禁用词检查 | 自检清单 | 已覆盖 |
| §11.3 | 模块伪码覆盖 | T-ADP-001, T-SVC-001, T-PVD-001, T-MAT-001, T-MAT-002, T-DED-001, T-DED-002, T-KWD-001, T-QLT-001, T-BRF-001, T-EVD-001 | 已覆盖 |
| §11.4 | 数据结构字段级覆盖 | T-INF-002 | 已覆盖 |
| §11.5 | 最终覆盖率 | 覆盖矩阵全部任务 | 已覆盖 |

## 自检清单

- 已确认：详细设计文档中的每个模块都有对应任务。
- 已确认：任务描述未包含用户列出的替代真实实现的禁用表达。
- 已确认：每个任务都有明确验收条件，且验收条件使用“给定、当、则”描述，可客观判断。
- 已确认：每个任务预估工时均不超过 3 天。
- 已确认：依赖关系无循环。
- 已确认：覆盖矩阵无空行。
- 已确认：一个开发者拿到任一任务后，在其前置依赖完成的条件下，可以独立开始工作，不需要额外澄清。
