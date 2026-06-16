# HK 数据层修改方案（当前有效口径）

本文只保留当前有效合同。旧的预取、固定域历史数据入口、候选选择逻辑、manifest domain 覆盖和历史请求数量设计已经废弃，不属于当前实现。

## 当前合同

- HK worker 只通过 `claw_request_data` 请求业务数据项，例如日线、分时、实时价、估值、财报、新闻、公告、宏观、事件日历、社交情绪。
- worker 不传 provider、path、api_name、api_id、内部需求类型字段、fields、source_role 或任何执行细节。
- 数据层内部把业务数据项转为 `DataNeed`，由 planner 精确生成 `ProviderCallSpec`，再经过执行闸、滑动窗口限流、single-flight 合并、fetch、parser、入库和证据记录。
- 数据源能力来自项目数据项到 provider API 的接口目录和 parser 证明，不来自按 worker/domain/section 写窄的硬编码限制。
- 同一业务数据项在 report、select、ui_probe、maintenance 下候选 provider 集合一致；consumer 只作为审计标签，不改变 provider 候选。

## HK 已接入的数据项方向

- 行情：日线、分时、实时价。
- 基本面：估值、财务指标、财报。
- 事件材料：公司新闻、宏观新闻、公告、事件日历、社交情绪线索。

## 实现验收

- `claw_request_data` 的公开 payload 不含 provider/path/api_name/url/header/token/fields/source_role/内部需求类型字段。
- HK 业务数据项能规划到至少一个 provider API。
- 出网调用有 attempt/raw/normalized/cache 证据；失败时返回明确缺口原因，不伪造成功。
- 文档、prompt、测试不得再要求 HK 历史数据入口、启动前预取或旧 候选选择逻辑。

当前细节以 `docs/限流重组.md`、`docs/数据层总体设计.md` 和代码中的 `public_api`/official catalog/tests 为准。
