# claw-trade fresh live capture - run-20260519-035922-dfefacc8

- run_dir: `/home/frank/src/claw-trade/runs/run-20260519-035922-dfefacc8`
- output_dir: `docs/evidence/cryptolens-btc-report-polish-internal-clean-20260519T035814Z/exported`
- worker_count: `8`
- final_report: `final_report.md`
- final_report_assets: `2`
- worker_appendix_files: `13`
- mock/stub/fake/fallback/capture-only: `none`

| worker | stage | status | final prompt | LLM back | report | prompt chars | report chars |
|---|---|---|---|---|---|---:|---:|
| `report_polisher` | `final_report` | `succeeded` | `report_polisher_t00_final_prompt.md` | `report_polisher_t00_llm_back.md` | `report_polisher_t00_report.md` | 52200 | 816 |
| `report_polisher` | `final_report` | `succeeded` | `report_polisher_t01_final_prompt.md` | `report_polisher_t01_llm_back.md` | `report_polisher_t01_report.md` | 52207 | 6448 |
| `report_polisher` | `final_report` | `succeeded` | `report_polisher_t02_final_prompt.md` | `report_polisher_t02_llm_back.md` | `report_polisher_t02_report.md` | 52207 | 5434 |
| `report_polisher` | `final_report` | `succeeded` | `report_polisher_t03_final_prompt.md` | `report_polisher_t03_llm_back.md` | `report_polisher_t03_report.md` | 52207 | 2746 |
| `report_polisher` | `final_report` | `succeeded` | `report_polisher_t04_final_prompt.md` | `report_polisher_t04_llm_back.md` | `report_polisher_t04_report.md` | 52207 | 3516 |
| `report_polisher` | `final_report` | `succeeded` | `report_polisher_t05_final_prompt.md` | `report_polisher_t05_llm_back.md` | `report_polisher_t05_report.md` | 52207 | 3457 |
| `report_polisher` | `final_report` | `succeeded` | `report_polisher_t06_final_prompt.md` | `report_polisher_t06_llm_back.md` | `report_polisher_t06_report.md` | 52207 | 3045 |
| `report_polisher` | `final_report` | `succeeded` | `report_polisher_t07_final_prompt.md` | `report_polisher_t07_llm_back.md` | `report_polisher_t07_report.md` | 52207 | 746 |

## Notes

- 本目录是 claw-trade `/report` fresh live 证据包，不是原版 TradingAgents 运行结果。
- 每个 worker 的 final prompt 来自最后一条真实 provider payload capture，不来自静态 render 或日志重构。
- 每个 worker 额外保留 `provider_prompt_initial.md` 和 `provider_prompt_final.md`，用于审计多轮工具调用边界。
- `final_report.md` 的图片依赖保存在 `assets/`。
- `worker-appendix/` 保存最终用户包中的 worker 原文附录。
