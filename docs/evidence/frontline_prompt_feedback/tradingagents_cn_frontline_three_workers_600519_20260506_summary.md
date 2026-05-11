# TradingAgents-CN frontline 三 worker 捕获汇总 - 600519 - 2026-05-06

本次只运行 TradingAgents-CN 的三个 frontline analyst：fundamentals、news、social。

运行口径：
- 项目：`/home/frank/src/TradingAgents-CN`
- 标的：`600519`
- 日期：`2026-05-06`
- 模型通道：Anthropic 兼容通道，模型返回显示 `MiniMax-M2.5`
- 环境修正：显式使用 `DEBUG=false`，避免 shell 中 `DEBUG=release` 被 CN 配置系统当作布尔值解析失败。

| worker | 状态 | final prompt | LLM back | report | execution |
|---|---|---|---|---|---|
| fundamentals | completed | `tradingagents_cn_fundamentals_600519_20260506_final_prompt.md` | `tradingagents_cn_fundamentals_600519_20260506_llm_back_raw.json` | `tradingagents_cn_fundamentals_600519_20260506_report.md` | `tradingagents_cn_fundamentals_600519_20260506_execution.md` |
| news | stopped_without_report | `tradingagents_cn_news_600519_20260506_final_prompt.md` | `tradingagents_cn_news_600519_20260506_llm_back_raw.json` | `tradingagents_cn_news_600519_20260506_report.md` | `tradingagents_cn_news_600519_20260506_execution.md` |
| social | stopped_after_first_tool_request | `tradingagents_cn_social_600519_20260506_final_prompt.md` | `tradingagents_cn_social_600519_20260506_llm_back_raw.json` | `tradingagents_cn_social_600519_20260506_report.md` | `tradingagents_cn_social_600519_20260506_execution.md` |

## 结果说明

fundamentals 完整跑通，经历两次 LLM 调用：第一次发起 `get_stock_fundamentals_unified`，工具返回后第二次生成报告。

news 没有生成有效报告。真实 LLM 返回是 `get_stock_news_unified` 的工具调用请求，CN 的 news worker 在当前 Anthropic 路径下把工具调用内容当成 `news_report` 返回，没有进入真实新闻工具执行后的报告轮。

social 第一轮真实 LLM 返回是 `get_stock_sentiment_unified` 的工具调用请求。尝试进入第二轮生成报告时，CN 代码再次查询股票名称并卡在数据源降级链路；已停止长等待。本次保留第一轮 final prompt、LLM back 和空 report，状态记为未完成。

## 后续比较口径

后续与 claw-trade 对比时，应按 worker 分开处理：
- fundamentals 可直接比较 final prompt、LLM back、report。
- news 只能比较 “CN 为什么没有 report” 与 claw 的 news 工具/报告链路。
- social 只能比较第一轮 prompt / tool request；完整报告需要先修 CN social 第二轮卡住问题或换用 CN 原本稳定的 provider 路径。
