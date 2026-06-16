# A 股扩展详细设计（当前有效口径）

本文已清理旧历史数据入口方案。当前 A 股数据层设计不再使用 `历史数据请求`、`旧历史请求计划`、历史数据入口构建器、domain 历史数据入口、候选选择逻辑 或固定远端预取。

## 目标

A 股报告和选股需要的数据，由 worker 通过统一数据工具按业务数据项请求。数据层负责把这些业务数据项转成真实 provider API 调用，并返回标准数据、证据和缺口。

## 工具边界

worker 只能看到：

```json
{
  "item": "日线/盘口/财报/公告/宏观/事件日历...",
  "purpose": "market_report/fundamental_report/news_report/social_report/selection_refresh",
  "instrument": "600519.SH",
  "market": "CN_A",
  "time_range_start": "2026-06-01",
  "time_range_end": "2026-06-14",
  "granularity": "daily/realtime/event",
  "priority": "required/normal/optional"
}
```

worker 不知道 provider、path、api_name、api_id、内部需求类型字段、fields、source_role、headers、token。

## 数据层内部流程

1. 校验公开请求。
2. 生成内部 `DataNeed`。
3. 根据项目数据项和官方/公共接口目录生成 `ProviderCallSpec`。
4. 按 paid/configured source 优先排序，同等功能失败后继续 fallback。
5. 执行闸先查缓存，再 single-flight 合并，再按 source 级滑动窗口预约真实 HTTP。
6. provider/plugin 调用真实接口。
7. parser 写标准行；不能结构化时返回 raw/parser_missing 缺口。
8. ingest 写 attempt/raw/normalized/cache 证据。
9. 工具返回数据摘要、refs 和缺口。

## A 股项目数据项

- 行情：日线、分时、实时价、盘口。
- 基本面：财报、财务指标、估值。
- 资金：资金流、游资、板块。
- 事件：公司新闻、宏观新闻、公告、事件日历、公司行动、解禁。
- 舆情：社交情绪和公开讨论线索。

## 数据源原则

- Tushare 有 key 时优先尝试等价付费接口。
- AkShare、mootdx、EastMoney、CNInfo、Google News 等免费/公共 provider 可作为 fallback。
- 选择依据是“能否满足同一项目业务数据项和标准输出口径”，不是按 worker 或 domain 做硬编码限制。
- 测试失败、临时 403、权限不足或空结果不能删除接口能力；只能记录该次 attempt 的真实失败。

## 验收

- public API contract 全部能规划到 provider call。
- A 股盘口有 planner 和 parser 成功样本。
- Tushare、AkShare、mootdx 等 provider 的调用参数有测试覆盖。
- 限流为配置驱动的滑动窗口；默认值不是硬编码在业务逻辑里。
- report/select 共用同一数据层合同，selection 合并调用结果必须回填给所有 need。

当前实现细节以 `docs/限流重组.md`、`docs/数据层总体设计.md`、`src/claw_trade/data_gateway/public_api.py`、official catalog 和对应测试为准。
