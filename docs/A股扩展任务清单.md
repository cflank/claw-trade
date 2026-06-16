# A 股扩展任务清单（当前有效口径）

旧的 `旧历史请求计划`、`历史数据请求`、七域固定包、历史数据入口构建器、启动前预取和 候选选择逻辑 任务已经废弃。A 股扩展只按项目业务数据项接入。

## P0

- A 股 worker 只调用 `claw_request_data`。
- worker 请求“日线、分时、实时价、盘口、财报、财务指标、估值、资金流、公司新闻、宏观新闻、公告、社交情绪、事件日历、游资、解禁、板块、公司行动”等业务数据项。
- 数据层内部精确匹配 provider API，按已配置 key 的付费源优先，失败后继续尝试后续等价接口。
- 不按 worker/domain/section/fields/source_role 做 provider 硬编码限制。

## P1

- 补齐 A 股项目数据项到 Tushare、AkShare、mootdx、EastMoney、CNInfo、Google News 等 provider API 的接口目录。
- 每个项目数据项至少有一个 planner 测试。
- 盘口必须覆盖 Tushare 实时 SDK、Tushare 竞价/盘口相关 raw API 和 mootdx 免费兜底。
- 财报、财务指标、估值、资金流、公告、新闻、宏观、事件日历必须有 parser 或 raw/parser_missing 缺口证据。
- 选股缓存只保存候选结果和数据证据，不触发历史数据入口。

## P2

- 补 live 证据时按 collect-first 跑 A 股，不在局部修复阶段反复跑 full-chain。
- 如果 provider 返回空、403、权限不足或字段变化，记录真实响应和缺口，不伪造成功。

## 验收

- 公开工具 payload 不含 provider/API 执行细节。
- A 股每个 public API contract 都能规划到至少一个 provider call。
- 任意连续 60 秒内同 source 真实 HTTP 数不超过配置值。
- 同一真实 provider call 被 single-flight 合并，selection/report 都能把结果回填给全部 need。

当前实现细节以 `docs/限流重组.md`、`docs/数据层总体设计.md`、`src/claw_trade/data_gateway/public_api.py`、official catalog 和测试为准。
