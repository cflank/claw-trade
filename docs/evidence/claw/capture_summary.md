# capture_summary

## run

- command_1: `source .runtime/dev-services/runtime.env && uv run python -m claw_trade.cli.run_control ...`
- command_1_exit_code: `2`
- command_1_result: `BLOCKED: 缺少真实依赖配置: CLAW_TRADE_OPENCLAW_RUNNER`
- command_2: `set -a && source .runtime/dev-services/runtime.env && set +a && uv run python -m claw_trade.cli.run_control ...`
- command_2_exit_code: `0`
- run_id: `run-20260512-110633-4e1cc0ab`
- run_dir: `/home/frank/src/claw-trade/runs/run-20260512-110633-4e1cc0ab`
- report_path: `/home/frank/src/claw-trade/runs/run-20260512-110633-4e1cc0ab/reports/final-report.md`
- final_report_md_source: `/home/frank/src/claw-trade/runs/run-20260512-110633-4e1cc0ab/reports/final-report.md`
- final_report_md_target: `/home/frank/src/claw-trade/docs/evidence/claw/final_report.md`

## per_worker_capture

| worker | final_prompt | llm_back | report | report_source | note |
|---|---|---|---|---|---|
| `market_analyst` | `True` | `True` | `True` | `approved_l1_uri` |  |
| `fundamental_analyst` | `True` | `True` | `True` | `approved_l1_uri` |  |
| `news_analyst` | `True` | `True` | `True` | `approved_l1_uri` |  |
| `social_analyst` | `True` | `True` | `True` | `approved_l1_uri` |  |
| `bull_researcher` | `True` | `True` | `True` | `approved_l1_uri` |  |
| `bear_researcher` | `True` | `True` | `True` | `approved_l1_uri` |  |
| `research_manager` | `True` | `True` | `True` | `approved_l1_uri` |  |
| `trader` | `True` | `True` | `True` | `approved_l1_uri` |  |
| `risk_challenger` | `True` | `True` | `True` | `approved_l1_uri` |  |
| `risk_guardian` | `True` | `True` | `True` | `approved_l1_uri` |  |
| `risk_moderator` | `True` | `True` | `True` | `approved_l1_uri` |  |
| `portfolio_manager` | `True` | `True` | `True` | `approved_l1_uri` |  |

## worker_sources

### market_analyst
- call_id: `run-20260512-110633-4e1cc0ab-frontline-market_analyst-20260512T110633348600Z-868b5f59`
- call_dir: `/home/frank/src/claw-trade/runs/run-20260512-110633-4e1cc0ab/calls/run-20260512-110633-4e1cc0ab-frontline-market_analyst-20260512T110633348600Z-868b5f59`
- provider_request_path: `/home/frank/src/claw-trade/runs/run-20260512-110633-4e1cc0ab/calls/run-20260512-110633-4e1cc0ab-frontline-market_analyst-20260512T110633348600Z-868b5f59/provider-request.json`
- raw_output_path: `/home/frank/src/claw-trade/runs/run-20260512-110633-4e1cc0ab/calls/run-20260512-110633-4e1cc0ab-frontline-market_analyst-20260512T110633348600Z-868b5f59/raw-output.md`
- l1_uri: `viking://resources/workflow/run-20260512-110633-4e1cc0ab/frontline/market_analyst/run-20260512-110633-4e1cc0ab-frontline-market_analyst-20260512T110633348600Z-868b5f59/report.md`
- output_files: `market_analyst_final_prompt.md`, `market_analyst_llm_back.md`, `market_analyst_report.md`

### fundamental_analyst
- call_id: `run-20260512-110633-4e1cc0ab-frontline-fundamental_analyst-20260512T110740282602Z-9d0e8274`
- call_dir: `/home/frank/src/claw-trade/runs/run-20260512-110633-4e1cc0ab/calls/run-20260512-110633-4e1cc0ab-frontline-fundamental_analyst-20260512T110740282602Z-9d0e8274`
- provider_request_path: `/home/frank/src/claw-trade/runs/run-20260512-110633-4e1cc0ab/calls/run-20260512-110633-4e1cc0ab-frontline-fundamental_analyst-20260512T110740282602Z-9d0e8274/provider-request.json`
- raw_output_path: `/home/frank/src/claw-trade/runs/run-20260512-110633-4e1cc0ab/calls/run-20260512-110633-4e1cc0ab-frontline-fundamental_analyst-20260512T110740282602Z-9d0e8274/raw-output.md`
- l1_uri: `viking://resources/workflow/run-20260512-110633-4e1cc0ab/frontline/fundamental_analyst/run-20260512-110633-4e1cc0ab-frontline-fundamental_analyst-20260512T110740282602Z-9d0e8274/report.md`
- output_files: `fundamental_analyst_final_prompt.md`, `fundamental_analyst_llm_back.md`, `fundamental_analyst_report.md`

### news_analyst
- call_id: `run-20260512-110633-4e1cc0ab-frontline-news_analyst-20260512T110828534144Z-ad383e21`
- call_dir: `/home/frank/src/claw-trade/runs/run-20260512-110633-4e1cc0ab/calls/run-20260512-110633-4e1cc0ab-frontline-news_analyst-20260512T110828534144Z-ad383e21`
- provider_request_path: `/home/frank/src/claw-trade/runs/run-20260512-110633-4e1cc0ab/calls/run-20260512-110633-4e1cc0ab-frontline-news_analyst-20260512T110828534144Z-ad383e21/provider-request.json`
- raw_output_path: `/home/frank/src/claw-trade/runs/run-20260512-110633-4e1cc0ab/calls/run-20260512-110633-4e1cc0ab-frontline-news_analyst-20260512T110828534144Z-ad383e21/raw-output.md`
- l1_uri: `viking://resources/workflow/run-20260512-110633-4e1cc0ab/frontline/news_analyst/run-20260512-110633-4e1cc0ab-frontline-news_analyst-20260512T110828534144Z-ad383e21/report.md`
- output_files: `news_analyst_final_prompt.md`, `news_analyst_llm_back.md`, `news_analyst_report.md`

### social_analyst
- call_id: `run-20260512-110633-4e1cc0ab-frontline-social_analyst-20260512T110938482818Z-1d5144f0`
- call_dir: `/home/frank/src/claw-trade/runs/run-20260512-110633-4e1cc0ab/calls/run-20260512-110633-4e1cc0ab-frontline-social_analyst-20260512T110938482818Z-1d5144f0`
- provider_request_path: `/home/frank/src/claw-trade/runs/run-20260512-110633-4e1cc0ab/calls/run-20260512-110633-4e1cc0ab-frontline-social_analyst-20260512T110938482818Z-1d5144f0/provider-request.json`
- raw_output_path: `/home/frank/src/claw-trade/runs/run-20260512-110633-4e1cc0ab/calls/run-20260512-110633-4e1cc0ab-frontline-social_analyst-20260512T110938482818Z-1d5144f0/raw-output.md`
- l1_uri: `viking://resources/workflow/run-20260512-110633-4e1cc0ab/frontline/social_analyst/run-20260512-110633-4e1cc0ab-frontline-social_analyst-20260512T110938482818Z-1d5144f0/report.md`
- output_files: `social_analyst_final_prompt.md`, `social_analyst_llm_back.md`, `social_analyst_report.md`

### bull_researcher
- call_id: `run-20260512-110633-4e1cc0ab-investment_debate-bull_researcher-20260512T111025642262Z-8ee9b26a`
- call_dir: `/home/frank/src/claw-trade/runs/run-20260512-110633-4e1cc0ab/calls/run-20260512-110633-4e1cc0ab-investment_debate-bull_researcher-20260512T111025642262Z-8ee9b26a`
- provider_request_path: `/home/frank/src/claw-trade/runs/run-20260512-110633-4e1cc0ab/calls/run-20260512-110633-4e1cc0ab-investment_debate-bull_researcher-20260512T111025642262Z-8ee9b26a/provider-request.json`
- raw_output_path: `/home/frank/src/claw-trade/runs/run-20260512-110633-4e1cc0ab/calls/run-20260512-110633-4e1cc0ab-investment_debate-bull_researcher-20260512T111025642262Z-8ee9b26a/raw-output.md`
- l1_uri: `viking://resources/workflow/run-20260512-110633-4e1cc0ab/investment_debate/bull_researcher/run-20260512-110633-4e1cc0ab-investment_debate-bull_researcher-20260512T111025642262Z-8ee9b26a/report.md`
- output_files: `bull_researcher_final_prompt.md`, `bull_researcher_llm_back.md`, `bull_researcher_report.md`

### bear_researcher
- call_id: `run-20260512-110633-4e1cc0ab-investment_debate-bear_researcher-20260512T111114690618Z-980e3602`
- call_dir: `/home/frank/src/claw-trade/runs/run-20260512-110633-4e1cc0ab/calls/run-20260512-110633-4e1cc0ab-investment_debate-bear_researcher-20260512T111114690618Z-980e3602`
- provider_request_path: `/home/frank/src/claw-trade/runs/run-20260512-110633-4e1cc0ab/calls/run-20260512-110633-4e1cc0ab-investment_debate-bear_researcher-20260512T111114690618Z-980e3602/provider-request.json`
- raw_output_path: `/home/frank/src/claw-trade/runs/run-20260512-110633-4e1cc0ab/calls/run-20260512-110633-4e1cc0ab-investment_debate-bear_researcher-20260512T111114690618Z-980e3602/raw-output.md`
- l1_uri: `viking://resources/workflow/run-20260512-110633-4e1cc0ab/investment_debate/bear_researcher/run-20260512-110633-4e1cc0ab-investment_debate-bear_researcher-20260512T111114690618Z-980e3602/report.md`
- output_files: `bear_researcher_final_prompt.md`, `bear_researcher_llm_back.md`, `bear_researcher_report.md`

### research_manager
- call_id: `run-20260512-110633-4e1cc0ab-investment_decision-research_manager-20260512T111200003392Z-f615ce22`
- call_dir: `/home/frank/src/claw-trade/runs/run-20260512-110633-4e1cc0ab/calls/run-20260512-110633-4e1cc0ab-investment_decision-research_manager-20260512T111200003392Z-f615ce22`
- provider_request_path: `/home/frank/src/claw-trade/runs/run-20260512-110633-4e1cc0ab/calls/run-20260512-110633-4e1cc0ab-investment_decision-research_manager-20260512T111200003392Z-f615ce22/provider-request.json`
- raw_output_path: `/home/frank/src/claw-trade/runs/run-20260512-110633-4e1cc0ab/calls/run-20260512-110633-4e1cc0ab-investment_decision-research_manager-20260512T111200003392Z-f615ce22/raw-output.md`
- l1_uri: `viking://resources/workflow/run-20260512-110633-4e1cc0ab/investment_decision/research_manager/run-20260512-110633-4e1cc0ab-investment_decision-research_manager-20260512T111200003392Z-f615ce22/report.md`
- output_files: `research_manager_final_prompt.md`, `research_manager_llm_back.md`, `research_manager_report.md`

### trader
- call_id: `run-20260512-110633-4e1cc0ab-trade_decision-trader-20260512T111232374848Z-dbfc68ff`
- call_dir: `/home/frank/src/claw-trade/runs/run-20260512-110633-4e1cc0ab/calls/run-20260512-110633-4e1cc0ab-trade_decision-trader-20260512T111232374848Z-dbfc68ff`
- provider_request_path: `/home/frank/src/claw-trade/runs/run-20260512-110633-4e1cc0ab/calls/run-20260512-110633-4e1cc0ab-trade_decision-trader-20260512T111232374848Z-dbfc68ff/provider-request.json`
- raw_output_path: `/home/frank/src/claw-trade/runs/run-20260512-110633-4e1cc0ab/calls/run-20260512-110633-4e1cc0ab-trade_decision-trader-20260512T111232374848Z-dbfc68ff/raw-output.md`
- l1_uri: `viking://resources/workflow/run-20260512-110633-4e1cc0ab/trade_decision/trader/run-20260512-110633-4e1cc0ab-trade_decision-trader-20260512T111232374848Z-dbfc68ff/report.md`
- output_files: `trader_final_prompt.md`, `trader_llm_back.md`, `trader_report.md`

### risk_challenger
- call_id: `run-20260512-110633-4e1cc0ab-risk_debate-risk_challenger-20260512T111259168897Z-183a2b33`
- call_dir: `/home/frank/src/claw-trade/runs/run-20260512-110633-4e1cc0ab/calls/run-20260512-110633-4e1cc0ab-risk_debate-risk_challenger-20260512T111259168897Z-183a2b33`
- provider_request_path: `/home/frank/src/claw-trade/runs/run-20260512-110633-4e1cc0ab/calls/run-20260512-110633-4e1cc0ab-risk_debate-risk_challenger-20260512T111259168897Z-183a2b33/provider-request.json`
- raw_output_path: `/home/frank/src/claw-trade/runs/run-20260512-110633-4e1cc0ab/calls/run-20260512-110633-4e1cc0ab-risk_debate-risk_challenger-20260512T111259168897Z-183a2b33/raw-output.md`
- l1_uri: `viking://resources/workflow/run-20260512-110633-4e1cc0ab/risk_debate/risk_challenger/run-20260512-110633-4e1cc0ab-risk_debate-risk_challenger-20260512T111259168897Z-183a2b33/report.md`
- output_files: `risk_challenger_final_prompt.md`, `risk_challenger_llm_back.md`, `risk_challenger_report.md`

### risk_guardian
- call_id: `run-20260512-110633-4e1cc0ab-risk_debate-risk_guardian-20260512T111323338988Z-c02ebdb7`
- call_dir: `/home/frank/src/claw-trade/runs/run-20260512-110633-4e1cc0ab/calls/run-20260512-110633-4e1cc0ab-risk_debate-risk_guardian-20260512T111323338988Z-c02ebdb7`
- provider_request_path: `/home/frank/src/claw-trade/runs/run-20260512-110633-4e1cc0ab/calls/run-20260512-110633-4e1cc0ab-risk_debate-risk_guardian-20260512T111323338988Z-c02ebdb7/provider-request.json`
- raw_output_path: `/home/frank/src/claw-trade/runs/run-20260512-110633-4e1cc0ab/calls/run-20260512-110633-4e1cc0ab-risk_debate-risk_guardian-20260512T111323338988Z-c02ebdb7/raw-output.md`
- l1_uri: `viking://resources/workflow/run-20260512-110633-4e1cc0ab/risk_debate/risk_guardian/run-20260512-110633-4e1cc0ab-risk_debate-risk_guardian-20260512T111323338988Z-c02ebdb7/report.md`
- output_files: `risk_guardian_final_prompt.md`, `risk_guardian_llm_back.md`, `risk_guardian_report.md`

### risk_moderator
- call_id: `run-20260512-110633-4e1cc0ab-risk_debate-risk_moderator-20260512T111356275988Z-2122dbac`
- call_dir: `/home/frank/src/claw-trade/runs/run-20260512-110633-4e1cc0ab/calls/run-20260512-110633-4e1cc0ab-risk_debate-risk_moderator-20260512T111356275988Z-2122dbac`
- provider_request_path: `/home/frank/src/claw-trade/runs/run-20260512-110633-4e1cc0ab/calls/run-20260512-110633-4e1cc0ab-risk_debate-risk_moderator-20260512T111356275988Z-2122dbac/provider-request.json`
- raw_output_path: `/home/frank/src/claw-trade/runs/run-20260512-110633-4e1cc0ab/calls/run-20260512-110633-4e1cc0ab-risk_debate-risk_moderator-20260512T111356275988Z-2122dbac/raw-output.md`
- l1_uri: `viking://resources/workflow/run-20260512-110633-4e1cc0ab/risk_debate/risk_moderator/run-20260512-110633-4e1cc0ab-risk_debate-risk_moderator-20260512T111356275988Z-2122dbac/report.md`
- output_files: `risk_moderator_final_prompt.md`, `risk_moderator_llm_back.md`, `risk_moderator_report.md`

### portfolio_manager
- call_id: `run-20260512-110633-4e1cc0ab-portfolio_decision-portfolio_manager-20260512T111427724444Z-9ad7b577`
- call_dir: `/home/frank/src/claw-trade/runs/run-20260512-110633-4e1cc0ab/calls/run-20260512-110633-4e1cc0ab-portfolio_decision-portfolio_manager-20260512T111427724444Z-9ad7b577`
- provider_request_path: `/home/frank/src/claw-trade/runs/run-20260512-110633-4e1cc0ab/calls/run-20260512-110633-4e1cc0ab-portfolio_decision-portfolio_manager-20260512T111427724444Z-9ad7b577/provider-request.json`
- raw_output_path: `/home/frank/src/claw-trade/runs/run-20260512-110633-4e1cc0ab/calls/run-20260512-110633-4e1cc0ab-portfolio_decision-portfolio_manager-20260512T111427724444Z-9ad7b577/raw-output.md`
- l1_uri: `viking://resources/workflow/run-20260512-110633-4e1cc0ab/portfolio_decision/portfolio_manager/run-20260512-110633-4e1cc0ab-portfolio_decision-portfolio_manager-20260512T111427724444Z-9ad7b577/report.md`
- output_files: `portfolio_manager_final_prompt.md`, `portfolio_manager_llm_back.md`, `portfolio_manager_report.md`
