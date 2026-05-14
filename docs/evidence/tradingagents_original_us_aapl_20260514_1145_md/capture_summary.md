# Original TradingAgents US capture - AAPL

- project: `/home/frank/src/TradingAgents`
- market: `US`
- ticker: `AAPL`
- trade_date: `2026-05-14`
- provider/model: `deepseek` / `deepseek-chat`
- base_url: `https://api.deepseek.com`
- llm_call_mode: `live_external_llm`
- mock/stub/fake/fallback/capture-only: `none`

## Captures

| worker | original node | status | LLM calls | tool calls | final prompt | LLM back | report | report chars |
|---|---|---:|---:|---:|---|---|---|---:|
| `market_analyst` | Market Analyst | completed | 4 | 13 | `market_analyst_final_prompt.md` | `market_analyst_llm_back.md` | `market_analyst_report.md` | 11339 |
| `social_analyst` | Social Analyst | completed | 3 | 2 | `social_analyst_final_prompt.md` | `social_analyst_llm_back.md` | `social_analyst_report.md` | 9911 |
| `news_analyst` | News Analyst | completed | 2 | 2 | `news_analyst_final_prompt.md` | `news_analyst_llm_back.md` | `news_analyst_report.md` | 6393 |
| `fundamental_analyst` | Fundamentals Analyst | completed | 3 | 7 | `fundamental_analyst_final_prompt.md` | `fundamental_analyst_llm_back.md` | `fundamental_analyst_report.md` | 11883 |
| `bull_researcher` | Bull Researcher | completed | 1 | 0 | `bull_researcher_final_prompt.md` | `bull_researcher_llm_back.md` | `bull_researcher_report.md` | 6717 |
| `bear_researcher` | Bear Researcher | completed | 1 | 0 | `bear_researcher_final_prompt.md` | `bear_researcher_llm_back.md` | `bear_researcher_report.md` | 6509 |
| `research_manager` | Research Manager | completed | 1 | 0 | `research_manager_final_prompt.md` | `research_manager_llm_back.md` | `research_manager_report.md` | 4165 |
| `trader` | Trader | completed | 1 | 0 | `trader_final_prompt.md` | `trader_llm_back.md` | `trader_report.md` | 1836 |
| `risk_challenger` | Aggressive Analyst | completed | 1 | 0 | `risk_challenger_final_prompt.md` | `risk_challenger_llm_back.md` | `risk_challenger_report.md` | 3940 |
| `risk_guardian` | Conservative Analyst | completed | 1 | 0 | `risk_guardian_final_prompt.md` | `risk_guardian_llm_back.md` | `risk_guardian_report.md` | 5656 |
| `risk_moderator` | Neutral Analyst | completed | 1 | 0 | `risk_moderator_final_prompt.md` | `risk_moderator_llm_back.md` | `risk_moderator_report.md` | 6039 |
| `portfolio_manager` | Portfolio Manager | completed | 1 | 0 | `portfolio_manager_final_prompt.md` | `portfolio_manager_llm_back.md` | `portfolio_manager_report.md` | 4239 |

## Notes

- 本目录只用于美股 prompt 对齐取证，不是 claw-trade 产品运行结果。
- 本轮按原版 TradingAgents 默认 analyst 顺序运行：market -> social -> news -> fundamentals。
- 因用户未指定美股标的，本轮使用 `AAPL`；如需其它 ticker，可用同脚本重跑。
- `all_llm_calls_raw.json` 保留每次 LLM 调用，包括 frontline 工具调用前的中间回合。
