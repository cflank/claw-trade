# claw-trade fresh live capture - run-20260518-115335-8004eadc

- run_dir: `/home/frank/src/claw-trade/runs/run-20260518-115335-8004eadc`
- output_dir: `docs/evidence/openbb-canonical-t16-tool-call-id-fix-20260518T114437Z/hk_00700/exported`
- worker_count: `13`
- final_report: `final_report.md`
- final_report_assets: `2`
- worker_appendix_files: `13`
- mock/stub/fake/fallback/capture-only: `none`

| worker | stage | status | final prompt | LLM back | report | prompt chars | report chars |
|---|---|---|---|---|---|---:|---:|
| `market_analyst` | `frontline` | `succeeded` | `market_analyst_final_prompt.md` | `market_analyst_llm_back.md` | `market_analyst_report.md` | 3099 | 4799 |
| `fundamental_analyst` | `frontline` | `succeeded` | `fundamental_analyst_final_prompt.md` | `fundamental_analyst_llm_back.md` | `fundamental_analyst_report.md` | 2259 | 3051 |
| `news_analyst` | `frontline` | `succeeded` | `news_analyst_final_prompt.md` | `news_analyst_llm_back.md` | `news_analyst_report.md` | 9571 | 3430 |
| `social_analyst` | `frontline` | `succeeded` | `social_analyst_final_prompt.md` | `social_analyst_llm_back.md` | `social_analyst_report.md` | 1760 | 1548 |
| `bull_researcher` | `investment_debate` | `succeeded` | `bull_researcher_final_prompt.md` | `bull_researcher_llm_back.md` | `bull_researcher_report.md` | 13688 | 4287 |
| `bear_researcher` | `investment_debate` | `succeeded` | `bear_researcher_final_prompt.md` | `bear_researcher_llm_back.md` | `bear_researcher_report.md` | 22282 | 6618 |
| `research_manager` | `investment_decision` | `succeeded` | `research_manager_final_prompt.md` | `research_manager_llm_back.md` | `research_manager_report.md` | 24723 | 3709 |
| `trader` | `trade_decision` | `succeeded` | `trader_final_prompt.md` | `trader_llm_back.md` | `trader_report.md` | 4892 | 2412 |
| `risk_challenger` | `risk_debate` | `succeeded` | `risk_challenger_final_prompt.md` | `risk_challenger_llm_back.md` | `risk_challenger_report.md` | 16007 | 1808 |
| `risk_guardian` | `risk_debate` | `succeeded` | `risk_guardian_final_prompt.md` | `risk_guardian_llm_back.md` | `risk_guardian_report.md` | 19627 | 2076 |
| `risk_moderator` | `risk_debate` | `succeeded` | `risk_moderator_final_prompt.md` | `risk_moderator_llm_back.md` | `risk_moderator_report.md` | 23743 | 2740 |
| `portfolio_manager` | `portfolio_decision` | `succeeded` | `portfolio_manager_final_prompt.md` | `portfolio_manager_llm_back.md` | `portfolio_manager_report.md` | 19786 | 3367 |
| `report_polisher` | `final_report` | `succeeded` | `report_polisher_final_prompt.md` | `report_polisher_llm_back.md` | `report_polisher_report.md` | 42469 | 14004 |

## Notes

- 本目录是 claw-trade `/report` fresh live 证据包，不是原版 TradingAgents 运行结果。
- 每个 worker 的 final prompt 来自最后一条真实 provider payload capture，不来自静态 render 或日志重构。
- 每个 worker 额外保留 `provider_prompt_initial.md` 和 `provider_prompt_final.md`，用于审计多轮工具调用边界。
- `final_report.md` 的图片依赖保存在 `assets/`。
- `worker-appendix/` 保存最终用户包中的 worker 原文附录。
