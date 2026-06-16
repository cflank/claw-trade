# claw-trade fresh live capture - run-20260616-122740-d8c2b136

- run_dir: `/home/frank/src/claw-trade/runs/run-20260616-122740-d8c2b136`
- output_dir: `docs/evidence/fullchain-cn-a-600519-20260616-subagent-live`
- worker_count: `22`
- final_report: `final_report.md`
- final_report_assets: `2`
- worker_appendix_files: `16`
- mock/stub/fake/fallback/capture-only: `none`

| worker | stage | status | final prompt | LLM back | report | prompt chars | report chars |
|---|---|---|---|---|---|---:|---:|
| `market_analyst` | `frontline` | `succeeded` | `market_analyst_final_prompt.md` | `market_analyst_llm_back.md` | `market_analyst_report.md` | 5089 | 4793 |
| `fundamental_analyst` | `frontline` | `succeeded` | `fundamental_analyst_final_prompt.md` | `fundamental_analyst_llm_back.md` | `fundamental_analyst_report.md` | 4223 | 3469 |
| `news_analyst` | `frontline` | `succeeded` | `news_analyst_final_prompt.md` | `news_analyst_llm_back.md` | `news_analyst_report.md` | 5389 | 3625 |
| `social_analyst` | `frontline` | `succeeded` | `social_analyst_final_prompt.md` | `social_analyst_llm_back.md` | `social_analyst_report.md` | 2225 | 2317 |
| `bull_researcher` | `investment_debate` | `succeeded` | `bull_researcher_final_prompt.md` | `bull_researcher_llm_back.md` | `bull_researcher_report.md` | 15630 | 4818 |
| `bear_researcher` | `investment_debate` | `succeeded` | `bear_researcher_final_prompt.md` | `bear_researcher_llm_back.md` | `bear_researcher_report.md` | 25303 | 7672 |
| `research_manager` | `investment_decision` | `succeeded` | `research_manager_final_prompt.md` | `research_manager_llm_back.md` | `research_manager_report.md` | 28310 | 2699 |
| `trader` | `trade_decision` | `succeeded` | `trader_final_prompt.md` | `trader_llm_back.md` | `trader_report.md` | 5129 | 2168 |
| `risk_challenger` | `risk_debate` | `succeeded` | `risk_challenger_final_prompt.md` | `risk_challenger_llm_back.md` | `risk_challenger_report.md` | 17747 | 1760 |
| `risk_guardian` | `risk_debate` | `succeeded` | `risk_guardian_final_prompt.md` | `risk_guardian_llm_back.md` | `risk_guardian_report.md` | 21266 | 2267 |
| `risk_moderator` | `risk_debate` | `succeeded` | `risk_moderator_final_prompt.md` | `risk_moderator_llm_back.md` | `risk_moderator_report.md` | 25769 | 2041 |
| `portfolio_manager` | `portfolio_decision` | `succeeded` | `portfolio_manager_final_prompt.md` | `portfolio_manager_llm_back.md` | `portfolio_manager_report.md` | 10458 | 3128 |
| `report_polisher` | `final_report` | `succeeded` | `report_polisher_t00_final_prompt.md` | `report_polisher_t00_llm_back.md` | `report_polisher_t00_report.md` | 57738 | 4426 |
| `report_polisher` | `final_report` | `succeeded` | `report_polisher_t01_final_prompt.md` | `report_polisher_t01_llm_back.md` | `report_polisher_t01_report.md` | 57733 | 4439 |
| `report_polisher` | `final_report` | `succeeded` | `report_polisher_t02_final_prompt.md` | `report_polisher_t02_llm_back.md` | `report_polisher_t02_report.md` | 57733 | 3625 |
| `report_polisher` | `final_report` | `succeeded` | `report_polisher_t03_final_prompt.md` | `report_polisher_t03_llm_back.md` | `report_polisher_t03_report.md` | 57733 | 3277 |
| `report_polisher` | `final_report` | `succeeded` | `report_polisher_t04_final_prompt.md` | `report_polisher_t04_llm_back.md` | `report_polisher_t04_report.md` | 57733 | 4564 |
| `report_polisher` | `final_report` | `succeeded` | `report_polisher_t05_final_prompt.md` | `report_polisher_t05_llm_back.md` | `report_polisher_t05_report.md` | 57733 | 5326 |
| `report_polisher` | `final_report` | `succeeded` | `report_polisher_t06_final_prompt.md` | `report_polisher_t06_llm_back.md` | `report_polisher_t06_report.md` | 57733 | 932 |
| `hot_money_tracker` | `frontline` | `succeeded` | `hot_money_tracker_final_prompt.md` | `hot_money_tracker_llm_back.md` | `hot_money_tracker_report.md` | 7244 | 5407 |
| `lockup_watcher` | `frontline` | `succeeded` | `lockup_watcher_final_prompt.md` | `lockup_watcher_llm_back.md` | `lockup_watcher_report.md` | 5470 | 3918 |
| `policy_analyst` | `frontline` | `succeeded` | `policy_analyst_final_prompt.md` | `policy_analyst_llm_back.md` | `policy_analyst_report.md` | 9736 | 3757 |

## Notes

- 本目录是 claw-trade `/report` fresh live 证据包，不是原版 TradingAgents 运行结果。
- 每个 worker 的 final prompt 来自最后一条真实 provider payload capture，不来自静态 render 或日志重构。
- 每个 worker 额外保留 `provider_prompt_initial.md` 和 `provider_prompt_final.md`，用于审计多轮工具调用边界。
- `final_report.md` 的图片依赖保存在 `assets/`。
- `worker-appendix/` 保存最终用户包中的 worker 原文附录。
