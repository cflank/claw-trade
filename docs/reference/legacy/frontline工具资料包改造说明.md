# frontline 工具资料包改造说明（C1）

日期：2026-05-06

## 改造目标

把 `fundamental_analyst` / `news_analyst` / `social_analyst` 的前线工具入口改成和 `market_market_data_pack` 一样的“单入口资料包”模式，避免 worker 暴露零散 generic 工具。

## 工具入口变更

- `fundamental_analyst`：`fundamental_fundamentals_data_pack`
- `news_analyst`：`news_news_data_pack`
- `social_analyst`：`social_social_sentiment_pack`

对应调整：

- `src/claw_trade/config/tool_names.py`：tool intent -> provider-visible 工具名映射更新
- 三个 worker 的 `STAGES.yaml`：US/CN_A 均改为资料包 intent
- 三个 worker 的 prompt：调用规则改为资料包入口

## runtime 接入方式

沿用 `market_market_data_pack` 的 single-worker runtime 本地注册方式，在：

- `third_party/openclaw/src/agents/pi-embedded-runner/run/frontline-tools.ts`

新增三类资料包工具注册，并保留旧工具注册兼容（`fundamentals_data/company_news/macro_news/social_sentiment`）。

## CN_A profile 行为

CN_A 新闻和热度资料包已按批准策略接入真实中文数据源：

- `news_news_data_pack`：通过 `alphaear-stock` skill 调用 AkShare 的东方财富个股新闻和 CCTV 宏观新闻。
- `social_social_sentiment_pack`：通过 `alphaear-stock` skill 调用 AkShare 东方财富热度、关键词、相关热股数据。

注意：CN_A social 当前拿到的是“市场热度/关键词”证据，不是逐条帖子级别的多空情绪文本。工具会在 `quality.warnings` 中明示 `heat_not_text_sentiment`，报告不能把它写成已经读取了完整社交舆情。

## 成功/失败判断

- 入口脚本外层成功不等于业务数据成功。
- 如果 AkShare 或东方财富返回空数据、非 JSON、断连，资料包必须返回 `ok=false` 或 `quality.status=partial/failed`。
- 不允许把 provider 异常包装成成功资料。
- 不允许用 Yahoo/Stocktwits 等非 CN_A 路径冒充 A 股资料。

## 仍需后续改造

- social worker 的 prompt 需要把“热度证据”和“真实情绪证据”分开写，不能把热度直接等同于情绪。
- 如果后续批准雪球/股吧正文级数据源，再把 `social_social_sentiment_pack` 扩展为帖子级舆情包。
