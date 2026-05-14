# CN_A claw-trade vs TradingAgents-CN Detailed Comparison - 2026-05-14

## Verdict

部分同意：本轮证据足以做 worker 级长度、风格和布局比较；是否达到产品级 parity 还要结合具体事实正确性和 provider/tool 成功证据逐项审查。

## Evidence Scope

- claw-trade evidence: `docs/evidence/trading_claw_trade_cn_a_fresh_live_run-20260514-113338-8a770ce9_md`
- baseline evidence: `docs/evidence/tradingagents_cn_full_cn_a_600519_20260514_1212_md`
- artifacts compared per worker: `final_prompt.md`, `llm_back.md`, `report.md`
- final report compared: `final_report.md`

## Evidence Completeness

| evidence set | final prompts | LLM backs | worker reports | final report | capture summary | extra workers |
|---|---:|---:|---:|---:|---:|---|
| claw-trade | 13 | 13 | 13 | 1 | 1 | report_polisher |
| baseline | 12 | 12 | 12 | 1 | 1 | - |

## Executive Findings

- claw-trade final prompt 明显更长的 worker: `bear_researcher` 1.45x (23976/16539); `research_manager` 1.44x (24191/16841); `trader` 1.40x (5129/3657); `bull_researcher` 1.39x (14355/10334); `risk_challenger` 1.35x (16036/11870)
- claw-trade final prompt 明显更短的 worker: `fundamental_analyst` 0.28x (788/2787); `news_analyst` 0.57x (778/1356); `social_analyst` 0.62x (934/1501)
- claw-trade report 明显更长的 worker: `news_analyst` 97.45x (3216/33); `research_manager` 1.69x (3740/2208); `bull_researcher` 1.55x (4792/3098); `bear_researcher` 1.50x (4855/3247)
- 异常短报告需要单独审查: `news_analyst`/baseline 33 chars

## Worker Metrics

| worker | prompt chars claw/base | prompt ratio | report chars claw/base | report ratio | headings claw/base | tables claw/base | language claw/base |
|---|---:|---:|---:|---:|---:|---:|---|
| `market_analyst` | 1764/1820 (-56) | 0.97x | 4536/3931 (+605) | 1.15x | 15/15 | 49/0 | 中文为主/中文为主 |
| `fundamental_analyst` | 788/2787 (-1999) | 0.28x | 3026/2655 (+371) | 1.14x | 15/16 | 20/43 | 中文为主/中英混合 |
| `news_analyst` | 778/1356 (-578) | 0.57x | 3216/33 (+3183) | 97.45x | 13/0 | 24/0 | 中文为主/中文为主 |
| `social_analyst` | 934/1501 (-567) | 0.62x | 2979/3090 (-111) | 0.96x | 7/18 | 15/58 | 中文为主/中文为主 |
| `bull_researcher` | 14355/10334 (+4021) | 1.39x | 4792/3098 (+1694) | 1.55x | 18/5 | 13/0 | 中文为主/中文为主 |
| `bear_researcher` | 23976/16539 (+7437) | 1.45x | 4855/3247 (+1608) | 1.50x | 17/5 | 0/0 | 中文为主/中文为主 |
| `research_manager` | 24191/16841 (+7350) | 1.44x | 3740/2208 (+1532) | 1.69x | 18/1 | 23/5 | 中文为主/中文为主 |
| `trader` | 5129/3657 (+1472) | 1.40x | 1732/1635 (+97) | 1.06x | 9/0 | 0/0 | 中文为主/中文为主 |
| `risk_challenger` | 16036/11870 (+4166) | 1.35x | 1480/1915 (-435) | 0.77x | 0/0 | 0/0 | 中文为主/中文为主 |
| `risk_guardian` | 18995/15669 (+3326) | 1.21x | 2088/1875 (+213) | 1.11x | 0/0 | 0/0 | 中文为主/中文为主 |
| `risk_moderator` | 23140/19360 (+3780) | 1.20x | 3062/2587 (+475) | 1.18x | 3/0 | 0/0 | 中文为主/中文为主 |
| `portfolio_manager` | 11059/9256 (+1803) | 1.19x | 3087/3219 (-132) | 0.96x | 5/13 | 5/5 | 中文为主/中文为主 |

## Worker-Level Analysis

### market_analyst

final prompt 篇幅接近（0.97x）；标题层级更少；交易动作词更密集。
LLM back 篇幅接近（1.15x）；表格化更强；交易动作词更少。
worker report 篇幅接近（1.15x）；表格化更强；交易动作词更少。

| artifact | claw chars | base chars | claw headings/tables/bullets | base headings/tables/bullets | action terms claw/base |
|---|---:|---:|---:|---:|---:|
| `final_prompt` | 1764 | 1820 | 16/0/52 | 21/0/45 | 10/7 |
| `llm_back` | 4536 | 3931 | 15/49/4 | 15/0/73 | 4/9 |
| `report` | 4536 | 3931 | 15/49/4 | 15/0/73 | 4/9 |

### fundamental_analyst

final prompt 明显更短（0.28x），信息密度或展开程度偏薄；标题层级更少；交易动作词更少。
LLM back 篇幅接近（1.14x）；表格化更弱；交易动作词更少。
worker report 篇幅接近（1.14x）；表格化更弱；交易动作词更少。

| artifact | claw chars | base chars | claw headings/tables/bullets | base headings/tables/bullets | action terms claw/base |
|---|---:|---:|---:|---:|---:|
| `final_prompt` | 788 | 2787 | 1/0/10 | 18/0/29 | 6/9 |
| `llm_back` | 3026 | 2655 | 15/20/7 | 16/43/9 | 1/3 |
| `report` | 3026 | 2655 | 15/20/7 | 16/43/9 | 1/3 |

### news_analyst

final prompt 明显更短（0.57x），信息密度或展开程度偏薄；布局复杂度接近；决策/辩论词密度接近。
LLM back 明显更长（97.45x），上下文或展开程度更重；标题层级更多、表格化更强；交易动作词更密集。
worker report 明显更长（97.45x），上下文或展开程度更重；标题层级更多、表格化更强；交易动作词更密集。

| artifact | claw chars | base chars | claw headings/tables/bullets | base headings/tables/bullets | action terms claw/base |
|---|---:|---:|---:|---:|---:|
| `final_prompt` | 778 | 1356 | 1/0/12 | 2/0/21 | 0/0 |
| `llm_back` | 3216 | 33 | 13/24/8 | 0/0/0 | 3/0 |
| `report` | 3216 | 33 | 13/24/8 | 0/0/0 | 3/0 |

### social_analyst

final prompt 明显更短（0.62x），信息密度或展开程度偏薄；标题层级更少；交易动作词更少。
LLM back 篇幅接近（0.96x）；标题层级更少、表格化更弱；交易动作词更少、辩论/对抗词更明显。
worker report 篇幅接近（0.96x）；标题层级更少、表格化更弱；交易动作词更少、辩论/对抗词更明显。

| artifact | claw chars | base chars | claw headings/tables/bullets | base headings/tables/bullets | action terms claw/base |
|---|---:|---:|---:|---:|---:|
| `final_prompt` | 934 | 1501 | 1/0/13 | 8/0/25 | 0/6 |
| `llm_back` | 2979 | 3090 | 7/15/6 | 18/58/4 | 1/9 |
| `report` | 2979 | 3090 | 7/15/6 | 18/58/4 | 1/9 |

### bull_researcher

final prompt 明显更长（1.39x），上下文或展开程度更重；表格化更强；交易动作词更少、辩论/对抗词更明显。
LLM back 明显更长（1.55x），上下文或展开程度更重；标题层级更多、表格化更强；交易动作词更密集、辩论/对抗词偏弱。
worker report 明显更长（1.55x），上下文或展开程度更重；标题层级更多、表格化更强；交易动作词更密集、辩论/对抗词偏弱。

| artifact | claw chars | base chars | claw headings/tables/bullets | base headings/tables/bullets | action terms claw/base |
|---|---:|---:|---:|---:|---:|
| `final_prompt` | 14355 | 10334 | 47/108/30 | 47/101/91 | 9/21 |
| `llm_back` | 4792 | 3084 | 18/13/19 | 5/0/6 | 8/6 |
| `report` | 4792 | 3098 | 18/13/19 | 5/0/6 | 8/6 |

### bear_researcher

final prompt 明显更长（1.45x），上下文或展开程度更重；标题层级更多、表格化更强；交易动作词更少、辩论/对抗词偏弱。
LLM back 明显更长（1.50x），上下文或展开程度更重；标题层级更多；交易动作词更密集、辩论/对抗词偏弱。
worker report 明显更长（1.50x），上下文或展开程度更重；标题层级更多；交易动作词更密集、辩论/对抗词偏弱。

| artifact | claw chars | base chars | claw headings/tables/bullets | base headings/tables/bullets | action terms claw/base |
|---|---:|---:|---:|---:|---:|
| `final_prompt` | 23976 | 16539 | 81/134/68 | 57/101/103 | 25/33 |
| `llm_back` | 4855 | 3233 | 17/0/6 | 5/0/6 | 4/1 |
| `report` | 4855 | 3247 | 17/0/6 | 5/0/6 | 4/1 |

### research_manager

final prompt 明显更长（1.44x），上下文或展开程度更重；标题层级更多、表格化更强；交易动作词更少、辩论/对抗词偏弱。
LLM back 明显更长（1.69x），上下文或展开程度更重；标题层级更多、表格化更强；交易动作词更密集。
worker report 明显更长（1.69x），上下文或展开程度更重；标题层级更多、表格化更强；交易动作词更密集。

| artifact | claw chars | base chars | claw headings/tables/bullets | base headings/tables/bullets | action terms claw/base |
|---|---:|---:|---:|---:|---:|
| `final_prompt` | 24191 | 16841 | 80/121/56 | 57/101/104 | 26/33 |
| `llm_back` | 3740 | 2208 | 18/23/18 | 1/5/7 | 12/7 |
| `report` | 3740 | 2208 | 18/23/18 | 1/5/7 | 12/7 |

### trader

final prompt 明显更长（1.40x），上下文或展开程度更重；标题层级更多、表格化更强；交易动作词更密集。
LLM back 篇幅接近（1.06x）；标题层级更多；交易动作词更少、辩论/对抗词更明显。
worker report 篇幅接近（1.06x）；标题层级更多；交易动作词更少、辩论/对抗词更明显。

| artifact | claw chars | base chars | claw headings/tables/bullets | base headings/tables/bullets | action terms claw/base |
|---|---:|---:|---:|---:|---:|
| `final_prompt` | 5129 | 3657 | 18/23/35 | 3/5/24 | 26/21 |
| `llm_back` | 1732 | 1635 | 9/0/14 | 0/0/7 | 8/9 |
| `report` | 1732 | 1635 | 9/0/14 | 0/0/7 | 8/9 |

### risk_challenger

final prompt 明显更长（1.35x），上下文或展开程度更重；标题层级更多、表格化更强；交易动作词更少、辩论/对抗词更明显。
LLM back 篇幅接近（0.78x）；布局复杂度接近；交易动作词更少。
worker report 篇幅接近（0.77x）；布局复杂度接近；交易动作词更少。

| artifact | claw chars | base chars | claw headings/tables/bullets | base headings/tables/bullets | action terms claw/base |
|---|---:|---:|---:|---:|---:|
| `final_prompt` | 16036 | 11870 | 56/108/39 | 47/101/93 | 17/30 |
| `llm_back` | 1480 | 1900 | 0/0/0 | 0/0/0 | 6/8 |
| `report` | 1480 | 1915 | 0/0/0 | 0/0/0 | 6/8 |

### risk_guardian

final prompt 篇幅接近（1.21x）；标题层级更多、表格化更强；交易动作词更少、辩论/对抗词更明显。
LLM back 篇幅接近（1.12x）；布局复杂度接近；交易动作词更密集、辩论/对抗词更明显。
worker report 篇幅接近（1.11x）；布局复杂度接近；交易动作词更密集、辩论/对抗词更明显。

| artifact | claw chars | base chars | claw headings/tables/bullets | base headings/tables/bullets | action terms claw/base |
|---|---:|---:|---:|---:|---:|
| `final_prompt` | 18995 | 15669 | 56/108/39 | 47/101/93 | 29/46 |
| `llm_back` | 2088 | 1861 | 0/0/0 | 0/0/0 | 9/8 |
| `report` | 2088 | 1875 | 0/0/0 | 0/0/0 | 9/8 |

### risk_moderator

final prompt 篇幅接近（1.20x）；标题层级更多、表格化更强；交易动作词更少、辩论/对抗词更明显。
LLM back 篇幅接近（1.19x）；标题层级更多；辩论/对抗词更明显。
worker report 篇幅接近（1.18x）；标题层级更多；辩论/对抗词更明显。

| artifact | claw chars | base chars | claw headings/tables/bullets | base headings/tables/bullets | action terms claw/base |
|---|---:|---:|---:|---:|---:|
| `final_prompt` | 23140 | 19360 | 56/108/39 | 47/101/93 | 47/62 |
| `llm_back` | 3062 | 2570 | 3/0/2 | 0/0/0 | 17/17 |
| `report` | 3062 | 2587 | 3/0/2 | 0/0/0 | 17/17 |

### portfolio_manager

final prompt 篇幅接近（1.19x）；标题层级更多、表格化更强；交易动作词更密集、辩论/对抗词更明显。
LLM back 篇幅接近（0.96x）；标题层级更少；交易动作词更密集、辩论/对抗词偏弱。
worker report 篇幅接近（0.96x）；标题层级更少；交易动作词更密集、辩论/对抗词偏弱。

| artifact | claw chars | base chars | claw headings/tables/bullets | base headings/tables/bullets | action terms claw/base |
|---|---:|---:|---:|---:|---:|
| `final_prompt` | 11059 | 9256 | 21/23/22 | 1/5/9 | 54/50 |
| `llm_back` | 3087 | 3219 | 5/5/23 | 13/5/23 | 17/14 |
| `report` | 3087 | 3219 | 5/5/23 | 13/5/23 | 17/14 |

## Final Report

| metric | claw-trade | baseline | ratio/delta |
|---|---:|---:|---:|
| chars | 13669 | 16879 | 0.81x / -3210 |
| headings | 41 | 71 | -30 |
| markdown table lines | 91 | 111 | -20 |
| bullet lines | 92 | 123 | -31 |
| image refs | 2 | 0 | +2 |
| action terms | 44 | 51 | -7 |

final report 篇幅接近（0.81x）；标题层级更少、表格化更弱、包含更多图片/图表引用；交易动作词更少、辩论/对抗词偏弱。

## Professional Readout

- 长度差异主要说明材料边界和展开程度，不单独证明质量高低；final prompt 过长可能带来基线外控制面或上游材料过载，过短则可能丢失原版任务语气或证据链。
- 布局差异主要看标题、表格、列表、图表引用。卖方研究报告感通常需要稳定章节、数据表和关键条件，但 debate worker 过度表格化会削弱辩论室语气。
- 风格差异主要看动作词、辩论词、角色声线和最终建议是否明确。强观点本身不是问题，前提是来自 worker 证据而不是 Python/exporter 改写。
- `llm_back` 与 `report` 在 claw-trade 证据中通常相同，因为 worker L1 report 由模型 raw output 批准后保存；原版脚本中也按节点 state report 保存，两者可比较但不是同一 runtime 机制。

