# TradingAgents-CN capture summary

## Run

- project: `TradingAgents-CN`
- source_root: `/home/frank/src/TradingAgents-CN`
- ticker: `600519`
- company_name: `贵州茅台`
- trade_date: `2026-05-12`
- market: `CN_A`
- provider/model: `deepseek` / `deepseek-chat`
- base_url: `https://api.deepseek.com`
- llm_call_mode: `live_external_llm`
- captured_at_utc: `2026-05-12T11:17:25.735169+00:00` -> `2026-05-12T11:21:52.736068+00:00`
- command_exit_code: `0`
- mock/stub/fake/fallback/capture-only: `none observed in final capture`; raw JSON remains only under `.runtime/tmp_cn_capture_20260512/` and was not copied into `docs/evidence/cn/`.

## Per Worker Capture

| worker | status | final prompt | LLM back | report | note |
|---|---:|---:|---:|---:|---|
| `market_analyst` | `OK` | `True` | `True` | `True` |  |
| `fundamental_analyst` | `OK` | `True` | `True` | `True` |  |
| `news_analyst` | `OK` | `True` | `True` | `True` | report/llm_back is very short; compare must treat as CN content gap, not full report parity. |
| `social_analyst` | `OK` | `True` | `True` | `True` |  |
| `bull_researcher` | `OK` | `True` | `True` | `True` |  |
| `bear_researcher` | `OK` | `True` | `True` | `True` |  |
| `research_manager` | `OK` | `True` | `True` | `True` |  |
| `trader` | `OK` | `True` | `True` | `True` |  |
| `risk_challenger` | `OK` | `True` | `True` | `True` |  |
| `risk_guardian` | `OK` | `True` | `True` | `True` |  |
| `risk_moderator` | `OK` | `True` | `True` | `True` |  |
| `portfolio_manager` | `OK` | `True` | `True` | `True` |  |

## Final Report

- source: `final_trade_decision`
- chars: `2772`
- note: TradingAgents-CN direct run exposes final_trade_decision; no extra exporter report was generated in this capture.

## Node Trace

- `Market Analyst` -> keys: `market_report, market_tool_call_count, messages`
- `Msg Clear Market` -> keys: `messages`
- `Social Analyst` -> keys: `messages, sentiment_report, sentiment_tool_call_count`
- `tools_social` -> keys: `messages`
- `Social Analyst` -> keys: `messages, sentiment_report, sentiment_tool_call_count`
- `Msg Clear Social` -> keys: `messages`
- `News Analyst` -> keys: `messages, news_report, news_tool_call_count`
- `Msg Clear News` -> keys: `messages`
- `Fundamentals Analyst` -> keys: `messages`
- `tools_fundamentals` -> keys: `messages`
- `Fundamentals Analyst` -> keys: `fundamentals_report, fundamentals_tool_call_count, messages`
- `Msg Clear Fundamentals` -> keys: `messages`
- `Bull Researcher` -> keys: `investment_debate_state`
- `Bear Researcher` -> keys: `investment_debate_state`
- `Research Manager` -> keys: `investment_debate_state, investment_plan`
- `Trader` -> keys: `messages, sender, trader_investment_plan`
- `Risky Analyst` -> keys: `risk_debate_state`
- `Safe Analyst` -> keys: `risk_debate_state`
- `Neutral Analyst` -> keys: `risk_debate_state`
- `Risk Judge` -> keys: `final_trade_decision, risk_debate_state`
