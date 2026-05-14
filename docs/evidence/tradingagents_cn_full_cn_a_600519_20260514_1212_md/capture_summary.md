# TradingAgents-CN full CN_A capture - 600519

- project: `/home/frank/src/TradingAgents-CN`
- market: `CN_A`
- ticker/company: `600519` / `贵州茅台`
- trade_date: `2026-05-14`
- provider/model: `deepseek` / `deepseek-chat`
- llm_call_mode: `live_external_llm`
- mock/stub/fake/fallback/capture-only: `none` when llm_call_mode is live_external_llm

| worker | original node | status | LLM calls | tool calls | final prompt | LLM back | report | report chars |
|---|---|---:|---:|---:|---|---|---|---:|
| `market_analyst` | Market Analyst | completed | 2 | 0 | `market_analyst_final_prompt.md` | `market_analyst_llm_back.md` | `market_analyst_report.md` | 3931 |
| `social_analyst` | Social Media Analyst | completed | 2 | 1 | `social_analyst_final_prompt.md` | `social_analyst_llm_back.md` | `social_analyst_report.md` | 3090 |
| `news_analyst` | News Analyst | completed | 1 | 0 | `news_analyst_final_prompt.md` | `news_analyst_llm_back.md` | `news_analyst_report.md` | 33 |
| `fundamental_analyst` | Fundamentals Analyst | completed | 2 | 1 | `fundamental_analyst_final_prompt.md` | `fundamental_analyst_llm_back.md` | `fundamental_analyst_report.md` | 2655 |
| `bull_researcher` | Bull Researcher | completed | 1 | 0 | `bull_researcher_final_prompt.md` | `bull_researcher_llm_back.md` | `bull_researcher_report.md` | 3098 |
| `bear_researcher` | Bear Researcher | completed | 1 | 0 | `bear_researcher_final_prompt.md` | `bear_researcher_llm_back.md` | `bear_researcher_report.md` | 3247 |
| `research_manager` | Research Manager | completed | 1 | 0 | `research_manager_final_prompt.md` | `research_manager_llm_back.md` | `research_manager_report.md` | 2208 |
| `trader` | Trader | completed | 1 | 0 | `trader_final_prompt.md` | `trader_llm_back.md` | `trader_report.md` | 1635 |
| `risk_challenger` | Risky Analyst | completed | 1 | 0 | `risk_challenger_final_prompt.md` | `risk_challenger_llm_back.md` | `risk_challenger_report.md` | 1915 |
| `risk_guardian` | Safe Analyst | completed | 1 | 0 | `risk_guardian_final_prompt.md` | `risk_guardian_llm_back.md` | `risk_guardian_report.md` | 1875 |
| `risk_moderator` | Neutral Analyst | completed | 1 | 0 | `risk_moderator_final_prompt.md` | `risk_moderator_llm_back.md` | `risk_moderator_report.md` | 2587 |
| `portfolio_manager` | Risk Judge | completed | 1 | 0 | `portfolio_manager_final_prompt.md` | `portfolio_manager_llm_back.md` | `portfolio_manager_report.md` | 3219 |

## Notes

- 本目录只用于 CN prompt 对齐取证，不是 claw-trade 产品运行结果。
- `portfolio_manager` 对应 TradingAgents-CN 的 `Risk Judge`。
- `all_llm_calls_raw.json` 保留每次 LLM 调用，包括前台工具调用和补救/强制分析回合。
