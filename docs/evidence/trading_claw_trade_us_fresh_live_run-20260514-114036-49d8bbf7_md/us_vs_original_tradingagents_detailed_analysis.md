# US claw-trade vs Original TradingAgents Detailed Comparison - 2026-05-14

## Verdict

部分同意：本轮证据足以做 worker 级长度、风格和布局比较；是否达到产品级 parity 还要结合具体事实正确性和 provider/tool 成功证据逐项审查。

## Evidence Scope

- claw-trade evidence: `docs/evidence/trading_claw_trade_us_fresh_live_run-20260514-114036-49d8bbf7_md`
- baseline evidence: `docs/evidence/tradingagents_original_us_aapl_20260514_1145_md`
- artifacts compared per worker: `final_prompt.md`, `llm_back.md`, `report.md`
- final report compared: `final_report.md`

## Evidence Completeness

| evidence set | final prompts | LLM backs | worker reports | final report | capture summary | extra workers |
|---|---:|---:|---:|---:|---:|---|
| claw-trade | 13 | 13 | 13 | 1 | 1 | report_polisher |
| baseline | 12 | 12 | 12 | 1 | 1 | - |

## Executive Findings

- claw-trade final prompt 明显更短的 worker: `fundamental_analyst` 0.08x (2362/30660); `social_analyst` 0.09x (1943/21834); `news_analyst` 0.11x (1624/14841); `market_analyst` 0.15x (5109/33284)
- claw-trade report 明显更长的 worker: `news_analyst` 1.60x (10226/6393)
- claw-trade report 明显更短的 worker: `portfolio_manager` 0.62x (2635/4239); `trader` 0.63x (1155/1836)

## Worker Metrics

| worker | prompt chars claw/base | prompt ratio | report chars claw/base | report ratio | headings claw/base | tables claw/base | language claw/base |
|---|---:|---:|---:|---:|---:|---:|---|
| `market_analyst` | 5109/33284 (-28175) | 0.15x | 10668/11339 (-671) | 0.94x | 21/14 | 35/68 | 英文为主/英文为主 |
| `fundamental_analyst` | 2362/30660 (-28298) | 0.08x | 16021/11883 (+4138) | 1.35x | 16/24 | 120/132 | 英文为主/英文为主 |
| `news_analyst` | 1624/14841 (-13217) | 0.11x | 10226/6393 (+3833) | 1.60x | 17/12 | 24/19 | 英文为主/英文为主 |
| `social_analyst` | 1943/21834 (-19891) | 0.09x | 10992/9911 (+1081) | 1.11x | 12/16 | 28/38 | 英文为主/英文为主 |
| `bull_researcher` | 49811/41048 (+8763) | 1.21x | 5782/6717 (-935) | 0.86x | 0/6 | 0/0 | 英文为主/英文为主 |
| `bear_researcher` | 61466/54550 (+6916) | 1.13x | 6372/6509 (-137) | 0.98x | 6/6 | 0/0 | 英文为主/英文为主 |
| `research_manager` | 14018/14628 (-610) | 0.96x | 4305/4165 (+140) | 1.03x | 0/0 | 0/0 | 英文为主/英文为主 |
| `trader` | 5692/5247 (+445) | 1.08x | 1155/1836 (-681) | 0.63x | 0/0 | 0/0 | 英文为主/英文为主 |
| `risk_challenger` | 51329/43182 (+8147) | 1.19x | 5215/3940 (+1275) | 1.32x | 6/0 | 0/0 | 英文为主/英文为主 |
| `risk_guardian` | 61644/50934 (+10710) | 1.21x | 5225/5656 (-431) | 0.92x | 0/0 | 0/0 | 英文为主/英文为主 |
| `risk_moderator` | 72013/62144 (+9869) | 1.16x | 5198/6039 (-841) | 0.86x | 0/0 | 0/0 | 英文为主/英文为主 |
| `portfolio_manager` | 22751/22820 (-69) | 1.00x | 2635/4239 (-1604) | 0.62x | 0/0 | 0/0 | 英文为主/英文为主 |

## Worker-Level Analysis

### market_analyst

final prompt 明显更短（0.15x），信息密度或展开程度偏薄；标题层级更少；交易动作词更少。
LLM back 篇幅接近（0.94x）；标题层级更多、表格化更弱；辩论/对抗词更明显。
worker report 篇幅接近（0.94x）；标题层级更多、表格化更弱；辩论/对抗词更明显。

| artifact | claw chars | base chars | claw headings/tables/bullets | base headings/tables/bullets | action terms claw/base |
|---|---:|---:|---:|---:|---:|
| `final_prompt` | 5109 | 33284 | 1/0/12 | 36/0/13 | 7/8 |
| `llm_back` | 10668 | 11339 | 21/35/19 | 14/68/37 | 3/3 |
| `report` | 10668 | 11339 | 21/35/19 | 14/68/37 | 3/3 |

### fundamental_analyst

final prompt 明显更短（0.08x），信息密度或展开程度偏薄；标题层级更少；交易动作词更少。
LLM back 篇幅接近（1.35x）；标题层级更少、表格化更弱；交易动作词更少、辩论/对抗词偏弱。
worker report 篇幅接近（1.35x）；标题层级更少、表格化更弱；交易动作词更少、辩论/对抗词偏弱。

| artifact | claw chars | base chars | claw headings/tables/bullets | base headings/tables/bullets | action terms claw/base |
|---|---:|---:|---:|---:|---:|
| `final_prompt` | 2362 | 30660 | 1/0/0 | 27/0/0 | 0/8 |
| `llm_back` | 16021 | 11883 | 16/120/58 | 24/132/13 | 1/4 |
| `report` | 16021 | 11883 | 16/120/58 | 24/132/13 | 1/4 |

### news_analyst

final prompt 明显更短（0.11x），信息密度或展开程度偏薄；标题层级更少；交易动作词更少、辩论/对抗词偏弱。
LLM back 明显更长（1.60x），上下文或展开程度更重；标题层级更多、表格化更强；交易动作词更少、辩论/对抗词偏弱。
worker report 明显更长（1.60x），上下文或展开程度更重；标题层级更多、表格化更强；交易动作词更少、辩论/对抗词偏弱。

| artifact | claw chars | base chars | claw headings/tables/bullets | base headings/tables/bullets | action terms claw/base |
|---|---:|---:|---:|---:|---:|
| `final_prompt` | 1624 | 14841 | 1/0/0 | 43/0/0 | 0/8 |
| `llm_back` | 10226 | 6393 | 17/24/23 | 12/19/14 | 0/2 |
| `report` | 10226 | 6393 | 17/24/23 | 12/19/14 | 0/2 |

### social_analyst

final prompt 明显更短（0.09x），信息密度或展开程度偏薄；标题层级更少；交易动作词更少、辩论/对抗词偏弱。
LLM back 篇幅接近（1.11x）；标题层级更少、表格化更弱；交易动作词更少、辩论/对抗词偏弱。
worker report 篇幅接近（1.11x）；标题层级更少、表格化更弱；交易动作词更少、辩论/对抗词偏弱。

| artifact | claw chars | base chars | claw headings/tables/bullets | base headings/tables/bullets | action terms claw/base |
|---|---:|---:|---:|---:|---:|
| `final_prompt` | 1943 | 21834 | 1/0/0 | 50/0/0 | 0/8 |
| `llm_back` | 10992 | 9911 | 12/28/16 | 16/38/4 | 0/4 |
| `report` | 10992 | 9911 | 12/28/16 | 16/38/4 | 0/4 |

### bull_researcher

final prompt 篇幅接近（1.21x）；表格化更弱；交易动作词更少、辩论/对抗词偏弱。
LLM back 篇幅接近（0.86x）；标题层级更少；辩论/对抗词偏弱。
worker report 篇幅接近（0.86x）；标题层级更少；辩论/对抗词偏弱。

| artifact | claw chars | base chars | claw headings/tables/bullets | base headings/tables/bullets | action terms claw/base |
|---|---:|---:|---:|---:|---:|
| `final_prompt` | 49811 | 41048 | 67/207/121 | 66/257/73 | 4/13 |
| `llm_back` | 5782 | 6703 | 0/0/0 | 6/0/20 | 0/0 |
| `report` | 5782 | 6717 | 0/0/0 | 6/0/20 | 0/0 |

### bear_researcher

final prompt 篇幅接近（1.13x）；标题层级更少、表格化更弱；交易动作词更少、辩论/对抗词偏弱。
LLM back 篇幅接近（0.98x）；布局复杂度接近；辩论/对抗词偏弱。
worker report 篇幅接近（0.98x）；布局复杂度接近；辩论/对抗词偏弱。

| artifact | claw chars | base chars | claw headings/tables/bullets | base headings/tables/bullets | action terms claw/base |
|---|---:|---:|---:|---:|---:|
| `final_prompt` | 61466 | 54550 | 67/207/121 | 78/257/113 | 4/13 |
| `llm_back` | 6372 | 6495 | 6/0/6 | 6/0/14 | 0/0 |
| `report` | 6372 | 6509 | 6/0/6 | 6/0/14 | 0/0 |

### research_manager

final prompt 篇幅接近（0.96x）；标题层级更少；辩论/对抗词偏弱。
LLM back 篇幅接近（1.03x）；布局复杂度接近；辩论/对抗词偏弱。
worker report 篇幅接近（1.03x）；布局复杂度接近；辩论/对抗词偏弱。

| artifact | claw chars | base chars | claw headings/tables/bullets | base headings/tables/bullets | action terms claw/base |
|---|---:|---:|---:|---:|---:|
| `final_prompt` | 14018 | 14628 | 7/0/6 | 12/0/34 | 0/0 |
| `llm_back` | 4305 | 4165 | 0/0/0 | 0/0/2 | 0/0 |
| `report` | 4305 | 4165 | 0/0/0 | 0/0/2 | 0/0 |

### trader

final prompt 篇幅接近（1.08x）；布局复杂度接近；交易动作词更密集、辩论/对抗词偏弱。
LLM back 明显更短（0.63x），信息密度或展开程度偏薄；布局复杂度接近；决策/辩论词密度接近。
worker report 明显更短（0.63x），信息密度或展开程度偏薄；布局复杂度接近；决策/辩论词密度接近。

| artifact | claw chars | base chars | claw headings/tables/bullets | base headings/tables/bullets | action terms claw/base |
|---|---:|---:|---:|---:|---:|
| `final_prompt` | 5692 | 5247 | 1/0/0 | 2/0/2 | 7/4 |
| `llm_back` | 1155 | 1836 | 0/0/0 | 0/0/0 | 2/2 |
| `report` | 1155 | 1836 | 0/0/0 | 0/0/0 | 2/2 |

### risk_challenger

final prompt 篇幅接近（1.19x）；表格化更弱；交易动作词更少、辩论/对抗词偏弱。
LLM back 篇幅接近（1.33x）；标题层级更多；交易动作词更少。
worker report 篇幅接近（1.32x）；标题层级更多；交易动作词更少、辩论/对抗词偏弱。

| artifact | claw chars | base chars | claw headings/tables/bullets | base headings/tables/bullets | action terms claw/base |
|---|---:|---:|---:|---:|---:|
| `final_prompt` | 51329 | 43182 | 67/207/116 | 66/257/68 | 6/15 |
| `llm_back` | 5215 | 3920 | 6/0/0 | 0/0/0 | 2/4 |
| `report` | 5215 | 3940 | 6/0/0 | 0/0/0 | 2/4 |

### risk_guardian

final prompt 篇幅接近（1.21x）；标题层级更多、表格化更弱；交易动作词更少、辩论/对抗词偏弱。
LLM back 篇幅接近（0.93x）；布局复杂度接近；辩论/对抗词更明显。
worker report 篇幅接近（0.92x）；布局复杂度接近；辩论/对抗词更明显。

| artifact | claw chars | base chars | claw headings/tables/bullets | base headings/tables/bullets | action terms claw/base |
|---|---:|---:|---:|---:|---:|
| `final_prompt` | 61644 | 50934 | 79/207/116 | 66/257/68 | 10/23 |
| `llm_back` | 5225 | 5634 | 0/0/0 | 0/0/4 | 1/1 |
| `report` | 5225 | 5656 | 0/0/0 | 0/0/4 | 1/1 |

### risk_moderator

final prompt 篇幅接近（1.16x）；标题层级更多、表格化更弱；交易动作词更少、辩论/对抗词偏弱。
LLM back 篇幅接近（0.86x）；布局复杂度接近；交易动作词更少、辩论/对抗词偏弱。
worker report 篇幅接近（0.86x）；布局复杂度接近；交易动作词更少、辩论/对抗词偏弱。

| artifact | claw chars | base chars | claw headings/tables/bullets | base headings/tables/bullets | action terms claw/base |
|---|---:|---:|---:|---:|---:|
| `final_prompt` | 72013 | 62144 | 79/207/116 | 66/257/76 | 12/25 |
| `llm_back` | 5198 | 6022 | 0/0/3 | 0/0/0 | 0/2 |
| `report` | 5198 | 6039 | 0/0/3 | 0/0/0 | 0/2 |

### portfolio_manager

final prompt 篇幅接近（1.00x）；标题层级更多；交易动作词更少、辩论/对抗词偏弱。
LLM back 明显更短（0.62x），信息密度或展开程度偏薄；布局复杂度接近；辩论/对抗词偏弱。
worker report 明显更短（0.62x），信息密度或展开程度偏薄；布局复杂度接近；辩论/对抗词偏弱。

| artifact | claw chars | base chars | claw headings/tables/bullets | base headings/tables/bullets | action terms claw/base |
|---|---:|---:|---:|---:|---:|
| `final_prompt` | 22751 | 22820 | 7/0/11 | 0/0/14 | 5/9 |
| `llm_back` | 2635 | 4239 | 0/0/0 | 0/0/4 | 0/0 |
| `report` | 2635 | 4239 | 0/0/0 | 0/0/4 | 0/0 |

## Final Report

| metric | claw-trade | baseline | ratio/delta |
|---|---:|---:|---:|
| chars | 14383 | 49972 | 0.29x / -35589 |
| headings | 36 | 74 | -38 |
| markdown table lines | 66 | 257 | -191 |
| bullet lines | 126 | 74 | +52 |
| image refs | 2 | 0 | +2 |
| action terms | 54 | 15 | +39 |

final report 明显更短（0.29x），信息密度或展开程度偏薄；标题层级更少、表格化更弱、包含更多图片/图表引用；交易动作词更密集、辩论/对抗词偏弱。

## Professional Readout

- 长度差异主要说明材料边界和展开程度，不单独证明质量高低；final prompt 过长可能带来基线外控制面或上游材料过载，过短则可能丢失原版任务语气或证据链。
- 布局差异主要看标题、表格、列表、图表引用。卖方研究报告感通常需要稳定章节、数据表和关键条件，但 debate worker 过度表格化会削弱辩论室语气。
- 风格差异主要看动作词、辩论词、角色声线和最终建议是否明确。强观点本身不是问题，前提是来自 worker 证据而不是 Python/exporter 改写。
- `llm_back` 与 `report` 在 claw-trade 证据中通常相同，因为 worker L1 report 由模型 raw output 批准后保存；原版脚本中也按节点 state report 保存，两者可比较但不是同一 runtime 机制。

