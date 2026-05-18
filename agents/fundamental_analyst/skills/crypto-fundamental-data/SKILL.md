---
name: crypto-fundamental-data
version: 0.1.0
description: CRYPTO 基本面资料包数据服务 skill，仅向 fundamental_analyst 提供 CoinGecko 与 DefiLlama 资料包能力。
tool: claw_get_fundamental_pack
tool_name: claw_get_fundamental_pack
schema_version: openbb_fundamental_pack.v1
---

# crypto-fundamental-data

该 skill 只负责提供 `claw_get_fundamental_pack` OpenBB 基本面资料包能力，不负责生成最终报告正文、投资结论、评级或目标价。

使用边界：

- CoinGecko 只作为币种基础资料、元数据、市值、FDV、供应量和价格快照来源。
- CoinGecko 必须有 `COINGECKO_DEMO_API_KEY` 或 `COINGECKO_PRO_API_KEY`；缺 key 时记录认证缺口，不走 public no-key fallback。
- DefiLlama 只作为 DeFi 协议 TVL、fees/revenue 等链上经营指标来源；没有匹配协议时必须保留数据缺口。
- 不把缺失的解锁、治理、收入、链上活跃度、开发者活跃度或公告数据补写成事实。
