# CN_A news 数据服务层设计方案

版本：HLD v0.3

v0.3 决策：

- `news_news_data_pack` 做成 `news_analyst` 独立 skill 内的资料包能力，不放进 claw-trade 核心业务代码。
- 第一版主线使用 AkShare；Tushare 只作为公告增强源，取决于真实 token 和权限。
- provider 采用有限并行、全部收集：不会因为某个 provider 成功就停止调用其他 provider。
- AkShare 财联社电报接口使用当前文档名 `stock_info_global_cls`，不再使用旧名。
- 新增 `ApprovedProfileResolver`：公司名、行业、已批准简称、历史名称只从已批准 profile/fundamentals/market artifact 读取，缺失字段进入 `missing_fields`。
- OpenClaw 只对 `news_analyst` 暴露 `news_news_data_pack` 与 `openviking_write_material`，provider 原子接口不暴露。
- provider timeout 后必须产出 attempt，包含 `elapsed_ms`、`empty_reason`、取消标记；同进程启用冷却与限流，避免短时间重复打坏远端接口。
- 补充 `keyword_categories.yaml` 与 `alias_rules.yaml` 字段级 schema、加载失败行为、运维诊断、版本发布与回滚流程。

本文基于 `docs/stock_news_agent_design.md` 的数据源调研，但不照搬其 agent 方案。

目标不是新增一个会自己分析新闻的 agent，而是为 `news_analyst` 提供一个稳定、可追踪、可复核的 A 股新闻资料包。

## 1. 结论

采用 **skill-first** 方案，先在 `news_analyst` 挂载的独立 skill 内实现 `news_news_data_pack`。

代码落点：

```text
agents/news_analyst/skills/cn-a-news-data/
  SKILL.md
  scripts/
    news_data_pack.py
    providers.py
    matching.py
    dedup.py
    reader_brief.py
    evidence.py
  config/
    keyword_categories.yaml
    alias_rules.yaml
```

暂不做独立 MCP。

原因：

- OpenClaw 当前 worker 本来就是挂载 skill。
- 第一阶段主要问题是数据源、字段、证据边界还没稳定，先做 MCP 会提前引入部署和调试复杂度。
- 未来如果资料包结构稳定，可以把同一套 provider 层再封装成 MCP。

## 2. 目标

`news_news_data_pack` 要做的是：

1. 按股票代码、公司名、行业关键词获取相关新闻。
2. 聚合公司新闻、行业新闻、政策/宏观新闻。
3. 对新闻去重、排序、裁剪。
4. 标明来源、时间、链接、原始摘要、匹配原因。
5. 记录每个 provider 的成功、失败、错误原因和返回条数。
6. 明确数据缺口。
7. 返回一段事实型中文资料摘要，供 `news_analyst` 写报告。

一句话：

**news 数据层只供料，不写报告，不下投资结论。**

## 3. 非目标

第一版明确不做：

- 不在工具内部调用 LLM。
- 不允许 Python 预取新闻后绕过 `news_analyst`，也不允许 Python 把预取新闻塞进 worker prompt 冒充 worker 工具调用。
- 不做独立新闻分析 agent。
- 不把 `stock_news_agent_design.md` 里的 L3 LLM 加工层照搬进来。
- 不让 Python 给最终利好/利空结论。
- 不让 Python 给买入、持有、卖出建议。
- 不接入 `daily_stock_analysis` 作为主流程目标。
- 不接入 pywencai。
- 不修改 OpenClaw workflow authority。
- 不绕过 `news_analyst` 的 OpenClaw worker 运行。

## 4. 五条硬规则

这些是实现和验收必须检查的硬规则，不是建议。

1. **禁止工具内部 LLM**
   - `news_news_data_pack` 的实现不得依赖或调用任何 LLM client、chat completion、agent runner、prompt template。
   - 允许 Python 做确定性取数、字段清洗、去重、关键词匹配和证据统计。
   - 不允许用 LLM 生成摘要、情绪、相关性、影响方向或投资建议。

2. **禁止 Python 代调后绕过 worker**
   - 正式链路中，新闻资料包必须由 `news_analyst` 在 OpenClaw turn 内调用 `news_news_data_pack` 得到。
   - 不允许 control plane 或 Python 预先拉新闻，再把新闻直接塞进 worker prompt 里。
   - 不允许用预取结果冒充 OpenClaw tool call。

3. **公司直连新闻必须有硬匹配证据**
   - 只有股票代码精确命中、公司全称命中、已批准简称命中、公告主体命中，才能进入 `company_news`。
   - 行业词命中只能进入 `industry_news` 或 `policy_macro_news`，不能进入 `company_news`。
   - 每条新闻必须给出命中的原文片段。

4. **关键词字段必须中性**
   - 不使用 `positive_keywords` / `negative_keywords` 这类方向性字段。
   - 可以保留中性标签，例如 `keyword_categories: [经营, 财报, 政策, 风险, 渠道, 价格]`。
   - 这些标签只说明文本主题，不代表利好、利空或情绪判断。

5. **`partial` / `failed` 门槛写死**
   - 两个 P0 数据源都失败时：`ok=false`，`quality.status=failed`。
   - 没有任何公司直连新闻、只有行业/宏观新闻时：`ok=true`，`quality.status=partial`，并写明“不可用于公司方向性新闻判断”。
   - `quality.status=partial` 时，`news_analyst` 只能基于可见证据写限制报告，不得给方向性新闻结论。

## 5. 架构

```text
claw-trade workflow
  -> OpenClaw 唤醒 news_analyst
  -> news_analyst 调用 news_news_data_pack
  -> news_analyst skill 内的 CN_A news data service
  -> 多 provider 获取新闻
  -> 去重、排序、匹配、缺口检测
  -> 返回结构化新闻资料包
  -> news_analyst 写新闻分析报告
  -> openviking_write_material 写入 report
```

关键边界：

- workflow 仍由 `claw-trade` 控制。
- 单 worker turn 仍由 OpenClaw 执行。
- `news_news_data_pack` 只提供资料。
- `news_analyst` 负责最终报告表达和新闻影响判断。
- provider 层封装在独立 skill 内，skill 对 OpenClaw 暴露单一工具入口，避免把新闻取数逻辑写进 claw-trade 控制面。

## 6. 工具暴露面

worker 只看到少量工具：

```text
news_news_data_pack
openviking_write_material
```

挂载要求：

- `agents/news_analyst/skills/cn-a-news-data/SKILL.md` 必须声明工具名 `news_news_data_pack`，脚本入口为 `scripts/news_data_pack.py`。
- skill manifest 挂载时必须显式声明仅向 `news_analyst` 暴露 `news_news_data_pack`；运行策略层再追加 `openviking_write_material`。
- 脚本入口读取 JSON（stdin），输出 JSON（stdout），退出码非 0 时返回结构化错误并保留 attempt 证据。

不要把底层 provider 暴露给 worker：

- 不暴露 `stock_news_em`
- 不暴露 `stock_info_global_cls`
- 不暴露 `stock_info_global_em`
- 不暴露 `news_cctv`
- 不暴露 Tushare `anns_d`
- 不暴露新浪/财联社/同花顺等原子接口

原因：

- 工具列表太大会让 worker 自己选工具，行为不可控。
- worker 不应该自己拼新闻资料包。
- provider 成败应该在资料包里统一记录，而不是散落在多次工具调用里。

## 7. 数据源策略

### 7.1 第一版数据源

| 优先级 | 数据源 | 角色 | 说明 |
|---|---|---|---|
| P0 | AkShare `stock_news_em` | 个股新闻主源 | 东方财富个股新闻，免费，按股票代码查询 |
| P0 | AkShare `stock_info_global_cls` | 快讯补充 | 财联社电报，适合盘中/近期事件；只有硬匹配命中才能进入公司新闻，行业词只能作为背景 |
| P1 | AkShare `stock_info_global_em` | 行业/宏观补充 | 东方财富全球财经快讯，按关键词过滤 |
| P1 | AkShare `news_cctv` | 政策/宏观补充 | 新闻联播文字稿，适合政策和宏观背景 |
| P1 | Tushare `anns_d` | 公告增强 | 可申请 token/权限后接入，公告对 A 股新闻质量很重要 |

第一版不把 Tushare `news` / `major_news` / `cctv_news` 放入主路径。原因是新闻类 Tushare 接口受 token、积分、单独权限影响更大，且 AkShare 已覆盖第一版需要的公司新闻、快讯和宏观背景。

字段映射基线（详细映射见 DLD）：

- `stock_news_em(symbol=ticker)`：`新闻标题/新闻内容/发布时间/文章来源/新闻链接/关键词`。
- `stock_info_global_cls(symbol="全部")`：`标题/内容/发布日期/发布时间`，本地硬匹配后再分桶。
- `stock_info_global_em()`：`标题/摘要/发布时间/链接`，本地关键词过滤后再分桶。
- `news_cctv(date=YYYYMMDD)`：`date/title/content`，只允许进入 `policy_macro_news`。
- `anns_d(ts_code,start_date,end_date)`：`ann_date/ts_code/name/title/url/rec_time`，未配置 token 或权限不足分别记录 `not_configured`/`permission_missing`。

### 7.2 provider 调度规则

- 主线是 AkShare。
- Tushare 只做公告增强源。
- 启用 provider 必须全部调用并全部记录结果，不允许某个 provider 成功后停止调用其他 provider。
- 调用方式采用有限并行，默认 `max_concurrency=3`。
- 单个 provider 默认 `timeout=10s`，整包默认 `timeout=20s`。
- 任一 provider 失败都必须写入 `provider_attempts`，不能包装成成功。

### 7.3 接口依据

- AkShare 官方文档中 `stock_news_em` 是东方财富指定个股新闻接口，输入为股票代码或关键词，返回新闻标题、内容、时间、来源、链接。
- AkShare 官方文档中 `stock_info_global_cls` 是财联社电报接口；AkShare changelog 记录旧接口名已变更为当前接口名。
- AkShare 官方文档中 `stock_info_global_em` 是东方财富全球财经快讯接口。
- AkShare 官方文档中 `news_cctv` 是新闻联播文字稿接口。
- Tushare 官方文档中 `anns_d` 是上市公司全量公告接口，提供公告标题和 PDF 链接，但需要单独权限。

参考：

- https://akshare.akfamily.xyz/data/stock/stock.html
- https://akshare.akfamily.xyz/data/others/others.html
- https://akshare.akfamily.xyz/changelog.html
- https://www.tushare.pro/document/2?doc_id=176

### 7.4 不使用 pywencai

第一版不考虑 pywencai。

原因：

- 需要 cookie/登录态。
- 高频可能被封。
- 返回结构不稳定。
- 不是当前闭环的必要依赖。

## 8. 匹配与预处理

### 8.1 查询关键词

每个标的至少使用：

- 股票代码：例如 `600519`
- 带交易所代码：例如 `600519.SH`
- 公司名称：例如 `贵州茅台`
- 常用简称：例如 `茅台`
- 行业关键词：例如 `白酒`、`高端白酒`

公司名、简称、行业应由 `ApprovedProfileResolver` 从已批准的 profile / fundamentals / market artifact 读取，不能由 worker 直接上送任意 alias，也不能凭 Python 记忆硬写。

简称和行业词必须分层管理：

- 公司全称：用于公司直连匹配。
- 已批准简称：可用于公司直连匹配，但必须有冲突词黑名单。
- 历史名称：可用于公司直连匹配，但必须来自批准来源。
- 行业词：只能用于行业/背景匹配。
- 宏观词：只能用于宏观背景匹配。

第一版批准的中性主题分类：

- 财报
- 公告
- 分红
- 经营
- 渠道
- 价格
- 产能
- 政策
- 风险
- 行业竞争
- 宏观消费

第一版简称规则：

- `贵州茅台` 可作为公司全称。
- `茅台` 可作为已批准简称，但遇到 `茅台镇`、`酱香酒`、`茅台集团` 时不得单独作为上市公司直连证据。
- 行业词如 `白酒`、`高端白酒`、`酱香酒`、`消费品` 只能进入行业或宏观背景。

### 8.2 去重

去重顺序：

1. 先规范化文本：全半角、空白、标点、站点尾缀、追踪参数。
2. URL 精确去重。
3. 标题精确去重。
4. 标题 + 来源 + 时间桶去重。
5. 标题相似度去重。

去重必须保留：

- 被去掉的数量统计。
- 被合并的原始新闻 id 列表。
- 合并原因。

### 8.3 排序与裁剪

- 默认保留最近 7 天新闻。
- 按发布时间倒序。
- 对 worker 可读摘要最多输出 20 条高相关新闻。
- 结构化 JSON 可以保留更多条，但要有上限，避免 prompt 膨胀。
- 当公司直连新闻低于阈值时，不自动扩大窗口冒充覆盖完整；必须明确写缺口，由上游决定是否扩大窗口重跑。

### 8.4 公司直连匹配

数据层不输出“相关性强/弱”这类分析标签，改为输出可审计匹配证据。

建议字段：

```json
{
  "match_type": "ticker_exact|company_full_name|approved_alias|announcement_subject|industry_keyword|macro_keyword|unknown",
  "match_evidence_span": "标题片段或正文片段",
  "match_confidence": "high|medium|low",
  "bucket": "company_news|industry_news|policy_macro_news|announcements"
}
```

规则：

- `ticker_exact`、`company_full_name`、`approved_alias`、`announcement_subject` 可进入 `company_news`。
- `industry_keyword` 只能进入 `industry_news`。
- `macro_keyword` 只能进入 `policy_macro_news`。
- `unknown` 不能进入已接受新闻池，只能进入 rejected / ignored 统计。

`ApprovedProfileResolver` 约束：

- 输入：run_id、stage、ticker、profile artifact ref、fundamentals artifact ref、market artifact ref。
- 输出：`company_name`、`industry`、`approved_aliases`、`approved_historical_names`、`missing_fields`。
- 若上述字段缺失，不允许自行补写；必须在 `quality.missing_fields` 中记录并按缺口输出受限资料包。

## 9. 情绪与影响边界

第一版不在数据层输出最终利好/利空结论。

可以输出中性关键词分类，例如：

```json
{
  "keyword_observations": {
    "matched_terms": ["提价", "渠道", "库存"],
    "keyword_categories": ["价格", "渠道", "经营"],
    "method": "keyword_match",
    "is_sentiment_judgment": false
  }
}
```

但不能输出：

```json
{
  "final_sentiment": "bullish",
  "investment_advice": "buy",
  "price_impact": "短期上涨"
}
```

最终新闻影响、利好利空、投资含义由 `news_analyst` 基于资料包写入报告。

## 10. 输出 schema

建议 `news_news_data_pack` 返回：

```json
{
  "schema_version": "cn_a_news_pack.v1",
  "ok": true,
  "profile": {
    "ticker": "600519",
    "company_name": "贵州茅台",
    "market": "CN_A",
    "industry": "白酒"
  },
  "query_plan": {
    "start_date": "2026-04-29",
    "end_date": "2026-05-06",
    "keywords": ["600519", "600519.SH", "贵州茅台", "茅台", "白酒"]
  },
  "provider_attempts": [
    {
      "provider": "akshare",
      "endpoint": "stock_news_em",
      "query": "600519",
      "ok": true,
      "elapsed_ms": 1320,
      "raw_count": 12,
      "accepted_count": 8,
      "empty_reason": null,
      "error": null,
      "cancelled": false
    }
  ],
  "data": {
    "company_news": [],
    "industry_news": [],
    "policy_macro_news": [],
    "announcements": []
  },
  "quality": {
    "status": "complete|partial|failed",
    "company_direct_news_count": 0,
    "industry_background_count": 0,
    "policy_macro_count": 0,
    "total_raw_count": 0,
    "after_dedup_count": 0,
    "accepted_count": 0,
    "missing_fields": [],
    "directional_judgment_allowed": false,
    "warnings": []
  },
  "reader_brief": "事实型中文资料摘要"
}
```

单条新闻结构：

```json
{
  "news_id": "akshare-stock_news_em-600519-001",
  "title": "新闻标题",
  "summary": "原始摘要或正文截断",
  "source": "证券时报",
  "publish_time": "2026-05-06T09:30:00",
  "url": "https://example.com/news",
  "data_source": "akshare.stock_news_em",
  "matched_keywords": ["贵州茅台"],
  "match_type": "company_full_name",
  "match_evidence_span": "标题直接提到贵州茅台",
  "match_confidence": "high",
  "bucket": "company_news",
  "keyword_observations": {
    "matched_terms": [],
    "keyword_categories": [],
    "method": "keyword_match",
    "is_sentiment_judgment": false
  },
  "source_fetch_time": "2026-05-06T09:35:00+08:00",
  "content_hash": "sha256:...",
  "is_primary_source": false,
  "merged_from": [],
  "evidence_gap": null
}
```

## 11. reader_brief 边界

`reader_brief` 可以写：

```text
过去 7 天共获取 18 条原始新闻，去重后 11 条。其中 6 条直接提到贵州茅台，3 条涉及白酒行业，2 条为消费政策/宏观背景。东方财富个股新闻成功，财联社电报成功，Tushare 公告未配置。
```

不能写：

```text
整体偏利好，建议持有。
```

边界：

- 必须使用可审计模板或抽取式生成，不能自由发挥。
- 可以总结数量、来源、时间、匹配原因、事件脉络、缺口。
- 不做最终影响判断。
- 不写投资建议。
- 不替 worker 写新闻分析报告。
- 可以按时间线列出事实，例如“5月3日，证券时报发布 X；5月5日，东方财富发布 Y”。
- 不能写“因此偏利好”“市场可能上涨”。

## 12. 失败处理

### 12.1 provider 失败

如果 provider 失败，必须写入 `provider_attempts`：

```json
{
  "provider": "akshare",
  "endpoint": "stock_news_em",
  "query": "600519",
  "ok": false,
  "elapsed_ms": 10012,
  "raw_count": 0,
  "accepted_count": 0,
  "empty_reason": "provider_error",
  "error": "JSONDecodeError: ...",
  "cancelled": false
}
```

`empty_reason` 取值建议：

- `no_result`
- `provider_error`
- `permission_missing`
- `rate_limited`
- `timeout`
- `schema_changed`
- `not_configured`

timeout 与取消规则：

- 单 provider 到 `timeout=10s` 时必须立即落一条 attempt，`ok=false`、`empty_reason=timeout`、`elapsed_ms` 为真实耗时。
- 如果总超时先触发，尚未结束的 provider attempt 标记 `cancelled=true`，并写 `empty_reason=timeout`。
- attempt 必须一 provider 一条，不得漏记。

同进程冷却/限流策略：

- 同一 provider 连续失败达到阈值时进入冷却窗口（默认 60 秒），冷却期内直接返回 attempt（`empty_reason=rate_limited`）并记录 `elapsed_ms=0`。
- 限流策略只影响该 provider，不影响同批次其他 provider 的调用与记录。

### 12.2 部分成功

如果公司新闻失败，但行业/宏观成功：

- `ok` 可以为 `true`
- `quality.status` 必须是 `partial`
- `missing_fields` 必须写明缺少公司直接新闻
- `quality.directional_judgment_allowed=false`
- `reader_brief` 必须写明：该资料包不可支持公司方向性新闻判断

如果没有公司直连新闻、只有行业/宏观新闻：

- `ok=true`
- `quality.status=partial`
- `quality.company_direct_news_count=0`
- `quality.directional_judgment_allowed=false`
- `news_analyst` 只能写限制报告，不得给方向性新闻结论

### 12.3 P0 全部失败

如果 AkShare `stock_news_em` 和 AkShare `stock_info_global_cls` 两个 P0 都失败：

- `ok=false`
- `quality.status=failed`
- 不得用 P1/P2 行业/宏观结果包装成成功
- 可以返回 P1/P2 的 provider_attempts 作为诊断信息，但不能让资料包呈现为可用于正式新闻判断

### 12.4 全部失败

如果所有 provider 都失败：

- `ok=false`
- `quality.status=failed`
- `data` 中各新闻列表为空
- `reader_brief` 只能说明失败原因和缺口
- 不得返回假新闻或示例新闻

## 13. 证据落地要求

`news_news_data_pack` 的原始输出必须进入本轮 evidence。

至少保留：

- run_id
- stage
- worker_id
- call_id
- tool_name
- query_plan
- provider_attempts
- raw output path
- content hash

OpenViking 仍只写 `news_analyst` 的正式报告；资料包证据由 control/runtime evidence 保存，不允许用 reader report 反推资料包。

安全脱敏规则：

- 错误信息必须脱敏：token、cookie、header、query 中的密钥字段。
- URL 校验只允许 `http`/`https`，并拒绝包含控制字符的链接。
- 脱敏后错误信息进入 `provider_attempts.error` 与运行日志。

## 14. 与 `docs/stock_news_agent_design.md` 的取舍

保留：

- 数据源调研。
- 多源采集思路。
- URL/标题去重。
- 时间排序。
- 新闻 schema 思路。
- 数据缺口检测。

不保留：

- L3 LLM 加工层。
- DeepSeek/Qwen/Claude 在工具内部做分类和情绪判断。
- `daily_stock_analysis` 作为主消费者。
- 直接注册成 OpenClaw SOUL auto-approve MCP tool 的实现方向。
- pywencai。

## 15. 验证计划

第一轮只验证一个标的：

```text
ticker=600519
company_name=贵州茅台
market=CN_A
date=2026-05-06
window=最近 7 天
```

必须保存：

- `news_news_data_pack` raw output
- provider attempts
- accepted news list
- missing fields
- `news_analyst` final prompt
- LLM back
- report
- tool calls
- visible tools
- OpenViking receipt

验收标准：

1. worker 只看到 `news_news_data_pack` 和 `openviking_write_material`。
2. 至少一个真实 provider 有真实成功或真实失败证据。
3. 返回新闻必须能解释为什么和 `600519 / 贵州茅台 / 白酒` 相关。
4. provider 失败不能被包装成成功。
5. `news_analyst` 报告不能编造新闻标题、来源、时间或投资结论。
6. `news_news_data_pack` 实现不得导入或调用 LLM client。
7. 如果没有公司直连新闻，报告必须是限制报告，不得给方向性新闻结论。
8. `match_evidence_span` 必须来自原始标题或正文片段，不能由 Python 自由生成。

运维验收补充：

9. metrics 必须包含 provider 成功/失败计数、耗时分布、状态分布与缺口计数，并带 `provider`、`endpoint`、`status` 标签。
10. 必须提供 provider 健康诊断命令与输出样例路径。
11. 必须提供 skill 版本发布与回滚步骤，确保 manifest 与 `SKILL.md` 可追溯。

## 16. 主要风险

### 16.1 数据源风险

- AkShare 依赖网页/接口，可能受东方财富接口变化和反爬影响。
- 财联社电报是全市场快讯，个股匹配可能漏报或误报。
- Tushare 需要 token、积分和接口权限。

### 16.2 架构风险

- 如果把 LLM 放进工具层，会变成隐藏分析 agent，破坏 worker 边界。
- 如果 Python 预取新闻并塞进 prompt，会绕过 OpenClaw tool call 证据链。
- 如果直接做 MCP，会提前增加服务部署和权限复杂度。
- 如果暴露太多底层工具，worker 会自己拼资料，行为不可控。
- 如果 provider 层写死在 skill 内，未来迁 MCP 会形成重写债。

### 16.3 产品风险

- 新闻数量多不等于质量高。
- 强相关新闻少时，报告应明确数据缺口，而不是硬写新闻研判。
- 行业/宏观新闻只能作为背景，不能冒充公司直接新闻。

## 17. 后续演进

第一阶段：

- skill-first
- 不内部调用 LLM
- 不接 pywencai
- 重点跑通东方财富个股新闻 + 财联社快讯 + 来源/时间/相关性证据

第二阶段：

- 接入 Tushare `anns_d` 公告
- 增加公司简称/历史名称/行业关键词库
- 增强新闻相关性过滤

第三阶段：

- schema 稳定后评估是否封装为 MCP
- 扩展到 HK / US，但不得自动套用 CN_A 策略

## 18. 最终建议

把 `docs/stock_news_agent_design.md` 定位为“新闻数据源调研文档”。

正式 claw-trade 实施应以本文为准：

```text
news_news_data_pack 是新闻资料包工具，不是新闻分析 agent。
```
