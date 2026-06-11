# claw-trade fresh live capture - run-20260611-055746-d38135b9

- run_dir: `/home/frank/src/claw-trade/runs/run-20260611-055746-d38135b9`
- output_dir: `docs/evidence/fullchain-crypto-btc-bnb-after-source-priority-20260611/btc`
- worker_count: `20`
- final_report: `final_report.md`
- final_report_assets: `2`
- worker_appendix_files: `13`
- mock/stub/fake/fallback/capture-only: `none`

| worker | stage | status | final prompt | LLM back | report | prompt chars | report chars |
|---|---|---|---|---|---|---:|---:|
| `market_analyst` | `frontline` | `succeeded` | `market_analyst_final_prompt.md` | `market_analyst_llm_back.md` | `market_analyst_report.md` | 12834 | 6496 |
| `fundamental_analyst` | `frontline` | `succeeded` | `fundamental_analyst_final_prompt.md` | `fundamental_analyst_llm_back.md` | `fundamental_analyst_report.md` | 5715 | 1782 |
| `news_analyst` | `frontline` | `succeeded` | `news_analyst_final_prompt.md` | `news_analyst_llm_back.md` | `news_analyst_report.md` | 3961 | 1931 |
| `social_analyst` | `frontline` | `succeeded` | `social_analyst_final_prompt.md` | `social_analyst_llm_back.md` | `social_analyst_report.md` | 2971 | 1225 |
| `bull_researcher` | `investment_debate` | `succeeded` | `bull_researcher_final_prompt.md` | `bull_researcher_llm_back.md` | `bull_researcher_report.md` | 14650 | 2457 |
| `bear_researcher` | `investment_debate` | `succeeded` | `bear_researcher_final_prompt.md` | `bear_researcher_llm_back.md` | `bear_researcher_report.md` | 19570 | 2962 |
| `research_manager` | `investment_decision` | `succeeded` | `research_manager_final_prompt.md` | `research_manager_llm_back.md` | `research_manager_report.md` | 20008 | 3257 |
| `trader` | `trade_decision` | `succeeded` | `trader_final_prompt.md` | `trader_llm_back.md` | `trader_report.md` | 7610 | 3229 |
| `risk_challenger` | `risk_debate` | `succeeded` | `risk_challenger_final_prompt.md` | `risk_challenger_llm_back.md` | `risk_challenger_report.md` | 17940 | 2578 |
| `risk_guardian` | `risk_debate` | `succeeded` | `risk_guardian_final_prompt.md` | `risk_guardian_llm_back.md` | `risk_guardian_report.md` | 23051 | 2041 |
| `risk_moderator` | `risk_debate` | `succeeded` | `risk_moderator_final_prompt.md` | `risk_moderator_llm_back.md` | `risk_moderator_report.md` | 27121 | 2159 |
| `portfolio_manager` | `portfolio_decision` | `succeeded` | `portfolio_manager_final_prompt.md` | `portfolio_manager_llm_back.md` | `portfolio_manager_report.md` | 17352 | 3041 |
| `report_polisher` | `final_report` | `succeeded` | `report_polisher_t00_final_prompt.md` | `report_polisher_t00_llm_back.md` | `report_polisher_t00_report.md` | 43280 | 1123 |
| `report_polisher` | `final_report` | `succeeded` | `report_polisher_t01_final_prompt.md` | `report_polisher_t01_llm_back.md` | `report_polisher_t01_report.md` | 43287 | 3904 |
| `report_polisher` | `final_report` | `succeeded` | `report_polisher_t02_final_prompt.md` | `report_polisher_t02_llm_back.md` | `report_polisher_t02_report.md` | 43287 | 2544 |
| `report_polisher` | `final_report` | `succeeded` | `report_polisher_t03_final_prompt.md` | `report_polisher_t03_llm_back.md` | `report_polisher_t03_report.md` | 43287 | 971 |
| `report_polisher` | `final_report` | `succeeded` | `report_polisher_t04_final_prompt.md` | `report_polisher_t04_llm_back.md` | `report_polisher_t04_report.md` | 43287 | 1412 |
| `report_polisher` | `final_report` | `succeeded` | `report_polisher_t05_final_prompt.md` | `report_polisher_t05_llm_back.md` | `report_polisher_t05_report.md` | 43287 | 2132 |
| `report_polisher` | `final_report` | `succeeded` | `report_polisher_t06_final_prompt.md` | `report_polisher_t06_llm_back.md` | `report_polisher_t06_report.md` | 43287 | 3120 |
| `report_polisher` | `final_report` | `succeeded` | `report_polisher_t07_final_prompt.md` | `report_polisher_t07_llm_back.md` | `report_polisher_t07_report.md` | 43287 | 1000 |

## Notes

- 本目录是 claw-trade `/report` fresh live 证据包，不是原版 TradingAgents 运行结果。
- 每个 worker 的 final prompt 来自最后一条真实 provider payload capture，不来自静态 render 或日志重构。
- 每个 worker 额外保留 `provider_prompt_initial.md` 和 `provider_prompt_final.md`，用于审计多轮工具调用边界。
- `final_report.md` 的图片依赖保存在 `assets/`。
- `worker-appendix/` 保存最终用户包中的 worker 原文附录。
