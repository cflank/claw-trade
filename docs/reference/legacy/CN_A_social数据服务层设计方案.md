# CN_A social 数据服务层设计方案

版本：HLD v0.2

本文参考 `docs/CN_A_news数据服务层设计方案.md` 的资料包服务层范式，但不照搬 news 的业务规则。CN_A social 第一阶段目标是达到原版 TradingAgents 的 social 水平：用真实、可追踪的市场热度、关键词、相关标的和叙事线索支撑 `social_analyst` 写报告。

本方案不把“完整社交舆情平台”或“帖子正文级采集”作为第一阶段前提。第一阶段可把热度、关键词、相关标的、榜单变化等线索视为 social evidence，但报告不得虚构未返回的平台原帖、KOL 观点、散户机构分歧或具体用户观点。

本版补充 frontline 资料共享共识：**MongoDB 负责结构化 provider cache 和去重，OpenViking 负责正式材料、L1/L2 证据与下游已批准读取**。二者可以共同解决重复拉取和证据复用问题，但不能改变 `social_analyst` 的 worker 边界，也不能让 Python 绕过 worker 写报告。

## 1. 结论

采用 **skill-first + 单一资料包工具 + 底层资料共享** 方案。

目标工具：

```text
social_social_sentiment_pack
```

长期代码落点建议：

```text
agents/social_analyst/skills/cn-a-social-data/
  SKILL.md
  scripts/
    social_data_pack.py
    providers.py
    matching.py
    quality.py
    reader_brief.py
    evidence.py
  config/
    alias_rules.yaml
    keyword_categories.yaml
```

短期可以复用当前 `alphaear-stock` 中已经实现的 CN_A social route，但最终 social 数据能力应归属 `social_analyst` 独立 skill，而不是长期放在 market skill 里。

资料共享原则：

- worker 可见层仍保持“一 worker 一资料包入口”，不把四个 frontline worker 合并成一个大工具。
- MongoDB 用于 provider 原始结果的结构化缓存、重复请求去重和新鲜度检查。
- OpenViking 用于保存 `social_analyst` 正式报告、资料包 L2 原始证据、hash、raw refs 和回执。
- 已批准的 worker 报告通过 OpenViking approved manifest 给下游读取；未批准材料不得被其它 worker 使用。
- social 如需使用 news 线索，应读取已批准 news artifact 或其 approved summary，不应重新调用 news provider 抓同一批公司新闻。

暂不做独立 MCP。

原因：

- OpenClaw 当前 worker 已经通过 skill 暴露工具。
- 第一阶段数据对象简单，主要是热度、关键词、相关标的和质量边界。
- 提前做 MCP 会增加部署、调试和版本管理复杂度。
- 资料包 schema 稳定后，可以再将 provider 层封装成 MCP。

## 2. 目标

`social_social_sentiment_pack` 要做的是：

1. 按股票代码、公司名、已批准简称获取目标相关热度与讨论线索。
2. 聚合市场热度、热门关键词、相关热门股票、排名变化和平台来源信息。
3. 对线索做目标匹配、分桶、去重、排序和裁剪。
4. 标明来源、时间、provider、匹配原因和证据类型。
5. 记录每个 provider 的成功、失败、空结果、错误原因和返回条数。
6. 明确数据缺口、来源集中、数据延迟和线索类型限制。
7. 返回一段事实型中文 `reader_brief`，供 `social_analyst` 写报告。
8. 在不改变 worker 可见工具的前提下，复用 MongoDB 中新鲜且 schema 合格的 provider cache，避免同一 run 或相近时间窗口重复拉取。
9. 将 raw provider payload 或可校验证据引用写入 OpenViking L2，供 hard gate、复核和下游追溯使用。

一句话：

**social 数据层只供给热度与情绪线索，不写报告，不下最终投资结论。**

## 3. 非目标

第一版明确不做：

- 不要求完整雪球、股吧、微博、知乎正文级舆情。
- 不要求登录态、cookie、验证码或高风险反爬路径。
- 不在工具内部调用 LLM。
- 不让 Python 生成最终看多、看空、买入、卖出结论。
- 不让 Python 预取 social 数据后绕过 `social_analyst`。
- 不把 Stocktwits、Reddit、Yahoo、Google News 等非 CN_A social 来源作为 A 股 social 主路径。
- 不新增独立 social 分析 agent。
- 不修改 OpenClaw workflow authority。
- 不绕过 `social_analyst` 的 OpenClaw worker 运行。
- 不用 MongoDB 替代 OpenViking artifact 权威。
- 不用 OpenViking 替代 MongoDB 做结构化 provider cache 查询。
- 不因为 cache hit 就自动把 `quality.status` 提升为 `complete`。

## 4. 硬规则

这些规则必须进入实现和验收。

### 4.1 禁止工具内部 LLM

`social_social_sentiment_pack` 不得调用 LLM client、chat completion、agent runner 或 prompt template。

允许 Python 做：

- provider 调用
- 字段清洗
- 目标匹配
- 去重
- 统计
- 质量评估
- 事实型 brief

不允许 Python 做：

- 最终情绪结论
- 投资建议
- 价格影响判断
- KOL 观点生成
- 散户机构分歧生成

### 4.2 禁止绕过 worker

正式链路中，social 资料包必须由 `social_analyst` 在 OpenClaw turn 内调用 `social_social_sentiment_pack` 得到。

不允许 control plane 或 Python 预先拉取 social 数据，再把结果直接塞进 worker prompt 冒充工具结果。

### 4.3 线索可以作为 social evidence

第一阶段接受以下内容作为 social evidence：

- 热度排名
- 热度值
- 排名变化
- 热门关键词
- 关键词热度
- 相关热门股票
- 榜单关联
- provider 返回的可见文本线索

这些线索足以支持 `social_analyst` 写“关注升温、降温、分化、证据不足”等报告判断。

但不得把这些线索扩写为未返回的具体平台原帖、用户评论、KOL 观点或散户机构分歧。

### 4.4 目标匹配必须可审计

只有以下命中可进入目标相关 accepted signals：

- 股票代码精确命中
- 带交易所或平台格式的代码命中
- 公司全称命中
- 已批准简称命中
- provider 明确按目标 symbol 返回

行业词、主题词、相关股票只能进入背景或关联线索，不得直接冒充目标公司的投资者情绪。

### 4.5 `partial` / `failed` 门槛写死

- 核心 provider 全失败：`ok=false`，`quality.status=failed`。
- 只有单一弱线索：`ok=true`，`quality.status=partial`。
- 来源集中或缺少趋势字段：`quality.status=partial`，并写 warning。
- 没有 accepted signals：`ok=false`，`quality.status=failed`。
- `quality.status=partial` 时，worker 可以写线索报告，但必须说明证据限制。

### 4.6 MongoDB / OpenViking 权威边界

MongoDB 与 OpenViking 都可以参与资料共享，但权威不同。

MongoDB 负责：

- provider 原始结果的结构化缓存。
- 同一 `market/ticker/provider/endpoint/query/date_window/schema_version` 下的重复请求去重。
- cache 新鲜度、schema、字段完整性和 hash 检查。
- 记录 `cache_hit`、`cache_miss`、`cache_stale`、`cache_schema_invalid` 等诊断。

OpenViking 负责：

- `social_analyst` 正式报告 L1。
- 支撑报告的 L2 原始证据入口，包括 provider raw payload、attempts、pack、hash 和来源说明。
- 已批准材料的下游读取能力。
- 写入回执、内容指纹和审计索引。

禁止：

- 用 MongoDB 中存在的缓存记录证明某份 worker 材料已批准。
- 让 social pack 扫 OpenViking 目录猜“最新新闻”或“最新社交材料”。
- 用 OpenViking compact read 作为资料包主路径。
- 缓存过期、schema 不匹配或缺少 `payload_hash/raw_payload_ref` 时仍当作当前事实线索。

## 5. 架构

```text
claw-trade workflow
  -> OpenClaw 唤醒 social_analyst
  -> social_analyst 调用 social_social_sentiment_pack
  -> social_analyst skill 内的 CN_A social data service
  -> MongoDB cache inspector 检查可复用 provider payload
  -> 多 provider 获取热度、关键词、相关标的
  -> 新 provider payload 写入 OpenViking L2 raw evidence，并按规则 upsert MongoDB cache
  -> 目标匹配、去重、排序、质量评估
  -> 返回结构化 social 资料包
  -> social_analyst 写热度与投资者情绪线索报告
  -> openviking_write_material 写入 report
```

关键边界：

- workflow 仍由 `claw-trade` 控制。
- 单 worker turn 仍由 OpenClaw 执行。
- `social_social_sentiment_pack` 只提供资料。
- `social_analyst` 负责最终报告表达和线索解释。
- provider 层封装在 social 独立 skill 内。
- OpenClaw 只暴露单一 social 资料包工具，不暴露底层 provider 原子接口。
- MongoDB cache 命中只代表 provider payload 可复用，不代表资料包质量自动通过。
- OpenViking 中存在材料不代表已批准；只有 approved manifest 中的材料才能进入下游。

## 6. 工具暴露面

worker 只看到：

```text
social_social_sentiment_pack
openviking_write_material
```

不要暴露底层 provider，例如：

- `stock_hot_rank_latest_em`
- `stock_hot_keyword_em`
- `stock_hot_rank_relate_em`
- `stock_hot_rank_em`
- `stock_hot_follow_xq`
- `stock_hot_tweet_xq`
- `stock_hot_deal_xq`

原因：

- worker 不应该自己拼 social 资料包。
- provider 成败必须统一进入 `provider_attempts`。
- 工具列表越大，worker 越容易自行选择错误路径。
- 资料包 schema 和质量门应该由数据服务层统一维护。

## 7. 数据源策略

### 7.1 第一版数据源

第一版优先复用当前已验证方向的 AkShare / 东方财富热度能力。

| 优先级 | 数据源 | 角色 | 说明 |
|---|---|---|---|
| P0 | AkShare `stock_hot_rank_latest_em` | 目标热度快照 | 返回目标个股最新热度字段 |
| P0 | AkShare `stock_hot_keyword_em` | 目标热门关键词 | 返回目标个股讨论关键词和热词线索 |
| P0 | AkShare `stock_hot_rank_relate_em` | 相关热门股票 | 返回相关热股，支持主题联动分析 |
| P0 | AkShare `stock_hot_rank_em` | 全市场人气榜校验 | 用于确认目标是否进入全市场热度榜 |
| P1 | AkShare `stock_hot_up_em` | 热度飙升榜增强 | 可用于关注度升温线索 |
| P1 | AkShare 雪球热度接口 | 雪球关注/讨论/交易热度增强 | 仅作为热度线索，不视为正文舆情 |

第一版不强制接入雪球/微博/股吧正文 crawler。

### 7.2 provider 调度规则

- 对每个 provider 先做 MongoDB cache inspection。
- cache 命中新鲜、schema 合格、hash 与 raw ref 完整时，可复用缓存，不再发起同 endpoint 网络请求。
- cache 未命中、过期、schema 不合格或缺少证据引用时，才调用真实 provider。
- 启用 provider 全部生成 attempt，不因某个 provider 成功就停止整体资料包。
- 单 provider timeout 默认 10 秒。
- 整包 timeout 默认 20 秒。
- 每个 provider 必须生成 attempt，attempt 中必须说明 `cache_status`。
- provider 失败不能包装成成功。
- 如果 P1 成功但 P0 全失败，不能把资料包包装成 complete。

### 7.3 禁止来源

CN_A social 第一版禁止把以下来源作为正式 A 股 social 路径：

- Stocktwits
- Reddit
- Yahoo
- Google News
- 非 CN_A 市场 profile 的社交源

这些来源可以出现在 US profile，不得进入 CN_A social 资料包。

## 8. 资料共享与缓存设计

### 8.1 共享目标

资料共享解决两个问题：

1. 避免同一 provider、同一 ticker、同一日期窗口被重复拉取。
2. 让报告中的 social 线索能回到 raw provider payload、attempt 和内容 hash。

它不解决也不应该解决：

- worker 调度顺序。
- worker 报告批准。
- 投资结论生成。
- social/news/fundamental/market 的职责合并。

### 8.2 MongoDB cache key

建议第一版 cache key：

```text
market
ticker
provider
endpoint
query_fingerprint
date_window
schema_version
payload_hash
```

其中：

- `query_fingerprint` 由规范化后的 endpoint 参数生成。
- `date_window` 对没有明确日期的热度接口可使用 `as_of_date`。
- `payload_hash` 用于幂等和证据一致性，不作为查询唯一条件的唯一来源。

### 8.3 cache record 最小字段

```json
{
  "ticker": "600519",
  "market": "CN_A",
  "provider": "akshare",
  "endpoint": "stock_hot_keyword_em",
  "query_fingerprint": "sha256:...",
  "as_of_date": "2026-05-07",
  "fetched_at": "2026-05-07T01:20:00-04:00",
  "schema_version": "cn_a_social_pack.v1",
  "payload_hash": "sha256:...",
  "raw_payload_ref": "viking://resources/workflow/.../l2/provider_raw/...",
  "row_count": 20,
  "fields": {}
}
```

cache 体检规则：

- 缺 `fetched_at`：不可判断新鲜度，视为 miss。
- 缺 `provider/endpoint/query_fingerprint`：不可作为可复用 provider 结果。
- 缺 `payload_hash/raw_payload_ref`：不可作为可追溯证据。
- schema version 不匹配：不可直接命中。
- 热度/关键词类接口默认新鲜度窗口短于财报字段，具体 TTL 由配置控制。
- cache hit 只说明可以复用 raw payload，不直接推出 `ok=true` 或 `quality.status=complete`。

### 8.4 OpenViking L2 证据

每次资料包调用必须形成可追溯 L2 证据：

- social pack JSON。
- provider attempts JSON。
- provider raw payload 或 raw payload 引用。
- MongoDB cache inspection 结果。
- 每条 accepted signal 到 raw payload 的 hash/ref。

如果 provider payload 来自 MongoDB cache，资料包仍必须返回原始 `raw_payload_ref` 和 `payload_hash`，不能只返回“来自缓存”。

## 9. 信号分桶

第一版 social 资料包不使用“帖子正文”作为必要字段，而使用 signal 分桶。

建议分桶：

```text
attention_signals
topic_keyword_signals
related_symbol_signals
narrative_signals
rejected_signals
```

含义：

- `attention_signals`：热度排名、热度值、排名变化、人气榜命中。
- `topic_keyword_signals`：热门关键词、关键词热度、关键词变化。
- `related_symbol_signals`：相关热门股票、主题联动、行业关联。
- `narrative_signals`：由关键词、热度变化和可见文本组合出的事实型叙事线索。
- `rejected_signals`：未能匹配目标、来源不合格、字段缺失或不可信的线索。

分桶规则：

- 目标 symbol 直接返回的热度可进入 `attention_signals`。
- 目标 symbol 直接返回的关键词可进入 `topic_keyword_signals`。
- 相关股票不得直接进入目标情绪，只能进入 `related_symbol_signals`。
- 行业或主题词不得直接冒充目标情绪。
- 无法匹配目标的内容进入 `rejected_signals` 或统计缺口。

## 10. 输出 schema

建议 `social_social_sentiment_pack` 返回：

```json
{
  "schema_version": "cn_a_social_pack.v1",
  "ok": true,
  "profile": {
    "ticker": "600519",
    "company_name": "贵州茅台",
    "market": "CN_A",
    "industry": "白酒"
  },
  "query_plan": {
    "start_date": "2026-05-01",
    "end_date": "2026-05-07",
    "keywords": ["600519", "600519.SH", "贵州茅台", "茅台"]
  },
  "provider_attempts": [
    {
      "provider": "akshare",
      "endpoint": "stock_hot_keyword_em",
      "query": "symbol=100.600519",
      "ok": true,
      "elapsed_ms": 1200,
      "raw_count": 20,
      "accepted_count": 20,
      "cache_status": "hit|miss|stale|schema_invalid|write_failed|not_configured",
      "cache_key": "sha256:...",
      "payload_hash": "sha256:...",
      "raw_payload_ref": "viking://resources/workflow/.../l2/provider_raw/...",
      "empty_reason": null,
      "error": null,
      "cancelled": false
    }
  ],
  "data": {
    "attention_signals": [],
    "topic_keyword_signals": [],
    "related_symbol_signals": [],
    "narrative_signals": [],
    "rejected_signals": []
  },
  "quality": {
    "status": "complete",
    "attention_signal_count": 1,
    "topic_keyword_count": 12,
    "related_symbol_count": 8,
    "narrative_signal_count": 3,
    "accepted_count": 24,
    "missing_fields": [],
    "social_judgment_allowed": true,
    "warnings": []
  },
  "reader_brief": "事实型中文线索摘要",
  "evidence": {
    "pack_path": "...",
    "provider_attempts_path": "...",
    "cache_inspection_path": "...",
    "raw_payload_refs": [],
    "content_hash": "sha256:..."
  }
}
```

单条 signal 建议结构：

```json
{
  "signal_id": "akshare-stock_hot_keyword_em-600519-001",
  "signal_type": "keyword|heat_rank|related_symbol|narrative",
  "platform": "东方财富",
  "provider": "akshare",
  "endpoint": "stock_hot_keyword_em",
  "title": null,
  "keyword": "分红",
  "symbol": "600519",
  "value": 12345,
  "rank": null,
  "rank_change": null,
  "source_time": null,
  "source_fetch_time": "2026-05-07T10:01:00+08:00",
  "matched_target": true,
  "match_type": "provider_target_symbol",
  "match_evidence_span": "symbol=100.600519",
  "evidence_type": "heat_keyword",
  "content_hash": "sha256:...",
  "evidence_gap": null
}
```

说明：

- `signal_type` 是证据形态，不是最终情绪结论。
- `rank_change`、`value` 只表达机械变化，不代表 Python 已经判断利好利空。
- 最终报告里的“升温、降温、分化、证据不足”由 `social_analyst` 基于资料包写出。

## 11. 质量门

建议质量状态：

### complete

满足以下条件：

- 至少一个 P0 provider 成功。
- 有目标相关 `attention_signals`。
- 至少还有 `topic_keyword_signals` 或 `related_symbol_signals` 中的一类。
- `accepted_count > 0`。
- accepted signals 均有 `payload_hash` 或 `raw_payload_ref`。
- cache hit 记录已通过新鲜度和 schema 体检。

此时：

```text
ok=true
quality.status=complete
quality.social_judgment_allowed=true
```

### partial

任一情况成立：

- 只有一类有效线索。
- 只有相关股票，没有目标热度或目标关键词。
- 来源集中且缺少趋势字段。
- provider 部分失败，但仍有可用目标线索。
- 有 cache hit 但缺少部分 raw refs，仍有足够 signal 可写限制性报告。

此时：

```text
ok=true
quality.status=partial
quality.social_judgment_allowed=true 或 false，按线索强弱决定
```

如果只剩弱线索，`social_judgment_allowed=false`，worker 只能写限制性线索报告。

### failed

任一情况成立：

- P0 provider 全失败。
- 没有 accepted signals。
- 输入 market 非 CN_A。
- ticker 无法规范化。
- provider 返回结构不可解析且无法形成有效资料包。
- cache 命中但过期、schema 不合格，且真实 provider 也失败。
- accepted signals 无法回源到 payload hash 或 raw ref。

此时：

```text
ok=false
quality.status=failed
quality.social_judgment_allowed=false
```

## 12. reader_brief 边界

`reader_brief` 可以写：

```text
本次共调用 4 个 social provider，2 个成功、1 个空结果、1 个失败。东方财富热度快照返回目标个股数据，关键词接口返回 12 条关键词，相关热股接口返回 8 条相关标的。当前资料可用于判断关注度和讨论焦点，但未覆盖雪球/微博/股吧正文。
```

不能写：

```text
投资者明显看多，建议买入。
```

边界：

- 只能总结数量、来源、时间、匹配原因、线索类型和缺口。
- 可以说明本次哪些 provider 来自缓存、哪些来自实时请求。
- 不写买入、持有、卖出。
- 不写最终利好或利空。
- 不替 worker 写完整社交分析报告。

## 13. Prompt 调整要求

现有 CN_A social prompt 可以保留报告结构，但应调整目标 1 的表达。

建议将“没有帖子正文或观点样本时，必须明确说明无法判断文本级多空情绪”改为：

```text
可以基于工具返回的热度、关键词、相关标的和可见线索判断关注度、讨论焦点和情绪倾向。
如果工具未返回具体平台原帖、KOL 或散户/机构分层样本，不得声称已读取这些内容。
```

报告可写：

- 关注度升温 / 降温 / 分化
- 关键词反映的讨论焦点
- 相关标的反映的主题联动
- 短期情绪线索
- 证据缺口

报告不得写：

- 未返回的雪球原帖
- 未返回的股吧评论
- 未返回的微博话题正文
- 未返回的 KOL 名字或观点
- 未返回的散户机构分歧

## 14. 与 CN_A news 资料包的关系

social 不复制 news 的业务职责，但复用其工程范式。

| CN_A news 范式 | CN_A social 迁移 |
|---|---|
| 独立 skill | `agents/social_analyst/skills/cn-a-social-data/` |
| 单一资料包工具 | `social_social_sentiment_pack` |
| provider attempts | 每个热度/关键词 provider 都记录 |
| hard matching | signal 必须匹配 ticker/company/approved alias |
| rejected evidence | 无关线索进入 rejected，不进 accepted |
| quality.status | complete / partial / failed |
| reader_brief | 事实型线索摘要 |
| evidence | 保存 pack、attempts、raw refs、hash |
| MongoDB cache | provider 原始结果可按 key 复用，但不替代 artifact approval |
| OpenViking L2 | 保存 raw payload refs、pack、attempts 和 hash |
| 集成验收 | 600519 fresh run 保存完整证据 |

news 负责事实事件层：

```text
公司新闻、行业新闻、政策/宏观背景
```

social 负责热度与情绪线索层：

```text
关注度、关键词、相关标的、讨论焦点、叙事线索
```

两个 worker 可以共用类似的资料包质量规则，但不应合并为同一个 worker。

关于重复拉取：

- news 拉事实事件，social 拉热度线索，第一阶段二者不应调用同一批公司新闻接口。
- 如果 social 后续需要使用 news 线索，应优先读取已批准 `news_analyst` artifact 或 approved summary。
- 若确实需要调用同一底层 provider，必须先走 MongoDB cache inspection，并在 provider attempts 中记录是否复用。

## 15. evidence 设计

每次工具调用应落地：

```text
{evidence_root}/{run_id}/{stage}/social_analyst/{call_id}/
  social_sentiment_pack.json
  provider_attempts.json
  cache_inspection.json
  provider_raw/
    {provider}_{endpoint}_{index}.json
```

要求：

- pack 和 provider attempts 必须可追溯。
- provider raw 需脱敏。
- 每个 call_id 独立目录，避免覆盖。
- pack 写入 content hash。
- evidence refs 回填到工具返回。
- cache 命中和未命中都要落地诊断。
- raw payload ref 优先采用 OpenViking L2 URI；本地路径只能作为审计副本。

## 16. 验收标准

第一阶段完成标准：

1. `social_analyst` 当前 turn 只看到 `social_social_sentiment_pack` 和 `openviking_write_material`。
2. `social_social_sentiment_pack` 返回 `cn_a_social_pack.v1`。
3. 每个启用 provider 都有 `provider_attempts`。
4. 成功运行时至少返回热度、关键词或相关标的中的一类 accepted signals。
5. provider 失败时返回 `partial` 或 `failed`，不得伪装 complete。
6. 报告可写关注度、关键词、主题联动和情绪线索。
7. 报告不得编造未返回的平台原帖、KOL、散户机构分歧。
8. 600519 单标的 fresh run 保存 final prompt、tool calls、visible tools、tool result、report、receipt 和 provider evidence。
9. CN_A social 不再走 Stocktwits/Yahoo/Reddit 等非 CN_A social 主路径。
10. provider attempts 明确记录 cache hit/miss/stale/schema_invalid/write_failed。
11. cache hit 不直接导致 `quality.status=complete`，质量仍由 accepted signals 和证据完整性决定。
12. raw provider payload 可回源到 OpenViking L2 或明确说明缺口。
13. social 不重复调用 news 的公司新闻接口；如复用 news 线索，只能通过 approved artifact。

## 17. 建议里程碑

### M1：包装现有能力

- 将当前 AkShare 东方财富热度、关键词、相关热股输出整理为 `cn_a_social_pack.v1`。
- 增加 `provider_attempts`、`quality`、`reader_brief`。
- 保持现有工具名 `social_social_sentiment_pack`。

### M2：接入资料共享

- 增加 MongoDB cache inspector。
- 增加 cache key、TTL、schema_version 和 payload hash。
- provider raw payload 写入 OpenViking L2，MongoDB 只保存结构化 cache 和 raw ref。
- provider attempts 增加 cache 状态字段。

### M3：迁移到 social 独立 skill

- 新建 `agents/social_analyst/skills/cn-a-social-data/`。
- 将 provider、schema、quality、brief 从 `alphaear-stock` 中剥离。
- manifest 只绑定 `social_analyst`。

### M4：prompt 与 guard 对齐

- 调整 CN_A social prompt，使目标 1 明确接受热度/关键词/相关标的作为 social evidence。
- 增加 `social_judgment_allowed=false` 时的报告约束。
- 增加“不编造平台原帖/KOL/散户机构分歧”的检查。

### M5：集成验收

- 对 600519 跑 fresh OpenClaw turn。
- 保存完整 evidence。
- 检查 provider attempts 中 cache 状态、raw payload refs 和 content hash。
- 对比原版 TradingAgents social 能力，确认已达到新闻/热度/情绪线索型报告水平。

## 18. Stop Conditions

以下情况必须停止并重新评审：

- Python 在 social pack 内写最终看多/看空、买入/卖出结论。
- provider 失败但 `quality.status=complete`。
- 无目标匹配的相关股票被写成目标公司情绪。
- CN_A social 继续使用 Stocktwits/Yahoo/Reddit 作为主路径。
- 报告声称读取了未返回的雪球/股吧/微博原帖。
- 报告编造 KOL、散户机构分歧或具体平台观点。
- provider attempts 缺失，无法复核数据来源。
- `social_analyst` 被绕过，control plane 或 Python 直接生成 social 报告。
- MongoDB cache stale/schema invalid 却被当作当前事实线索。
- OpenViking 中未批准材料被 social 或其它 worker 读取。
- social 为了补线索重新抓 news company provider，且没有走 approved artifact 或 cache 复用策略。

## 19. 开放问题

| 问题 | 建议 |
|---|---|
| 是否第一阶段就迁移出 `alphaear-stock` | 可以先包装现有实现，随后迁移到 social 独立 skill |
| 是否接入雪球热度接口 | 可作为 P1 增强，但不作为目标 1 阻塞项 |
| 是否接入股吧正文 crawler | 不属于目标 1，后续作为目标 2 单独评审 |
| 是否和 news 共用 profile resolver | 建议共用设计原则，但实现可以先独立，避免跨 worker 耦合 |
| 是否输出情绪评分 | 可以由 `social_analyst` 基于资料包给出线索评分；数据层不直接给最终评分 |
| 是否第一阶段强制接入 MongoDB | 建议 M2 接入；M1 可先无缓存包装现有能力，但必须保留 schema 字段 |
| cache TTL 如何定 | 热度/关键词默认短 TTL，具体数值进入 DLD 或配置任务确认 |
