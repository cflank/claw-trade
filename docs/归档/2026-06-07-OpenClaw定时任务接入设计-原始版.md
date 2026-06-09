# OpenClaw 定时任务接入设计

日期：2026-06-07

状态：草案

范围：定时报表、价格提醒、A 股/加密币/未来美股后台数据下载。

本文是新设计文档。它不修改既有 UI 设计文档和任务清单，避免把旧方案和新方案混在一起。

## 结论

OpenClaw 管“什么时候叫醒任务”。

claw-trade 管“醒来之后做什么、怎么取数据、怎么判断、怎么触发报告、怎么通知用户”。

不要在 claw-trade 里重新造一套定时器。也不要把价格判断、行情下载、12-worker 报告流程塞进 OpenClaw。

## 已确认事实

### OpenClaw 已有能力

OpenClaw cron 已经支持：

- 一次性任务、固定间隔任务、cron 表达式任务。
- 持久化任务定义和运行状态。
- 到点后唤醒 agent。
- 记录后台任务运行历史。
- 支持 webhook、chat announce、none 等交付方式。
- Gateway 暴露 `cron.add`、`cron.update`、`cron.remove`、`cron.run`、`cron.list`、`cron.runs` 等方法。

主要证据：

- `third_party/openclaw/docs/automation/cron-jobs.md`
- `third_party/openclaw/src/cron/types.ts`
- `third_party/openclaw/src/cron/service-contract.ts`
- `third_party/openclaw/src/gateway/server-methods/cron.ts`
- `third_party/openclaw/src/gateway/server-cron.ts`
- `third_party/openclaw/src/cron/service/timer.ts`

### claw-trade 现有代码状态

claw-trade 已经有定时报表和价格提醒的 service 骨架：

- `src/claw_trade/ui_backend/scheduler_service.py`
- `src/claw_trade/ui_backend/price_alert_service.py`

但当前核心问题是：

- 存储主要是内存态，不适合真实长期任务。
- `tick_scheduled_reports` 是 claw-trade 自己轮询 due item 的思路。
- 价格提醒有创建、暂停、恢复、删除、手动检查，但没有真正接入 OpenClaw cron 自动扫描。
- 价格 quote provider 还没有真实实现，只会在缺证据时失败，或报 `price_alert_quote_provider_unimplemented`。

所以现有代码不是完整实现，更像业务 API 和测试骨架。

## 设计原则

1. OpenClaw 只做通用定时唤醒。
2. claw-trade 保存业务任务、用户配置、运行证据和状态。
3. worker 是 OpenClaw agent，不是 Python 函数。
4. LLM 不判断价格是否触发，价格判断必须是确定性代码。
5. OpenClaw cron 不编排 12-worker 报告 DAG。
6. 后台数据下载和用户价格提醒共用 OpenClaw cron 作为时钟，但业务执行留在 claw-trade。
7. 不绕过数据源限流、出网 gate、缓存、证据链和失败记录。

## 总体结构

```text
用户 / UI
  |
  v
claw-trade API
  |
  | 保存业务任务、校验、幂等、权限
  v
claw-trade 业务任务表
  |
  | 注册 / 更新 / 删除 cron job
  v
OpenClaw cron
  |
  | 到点唤醒 agent
  v
OpenClaw worker turn
  |
  | 调用 claw-trade 工具 / API
  v
claw-trade 业务执行层
  |
  | 行情、报告、下载、通知、证据
  v
结果 / 通知 / 审计记录
```

## 职责边界

| 模块 | 负责 | 不负责 |
| --- | --- | --- |
| OpenClaw cron | 计时、持久化 cron job、到点唤醒 agent、运行日志、基础重试 | 价格判断、行情下载策略、报告 DAG、用户业务状态 |
| claw-trade API | 接收用户创建/暂停/删除任务，维护业务记录和幂等 | 自己长期轮询 |
| claw-trade cron adapter | 把业务任务注册到 OpenClaw cron | 持有业务判断逻辑 |
| OpenClaw worker | 被 cron 唤醒，执行一次 agent turn | 自己决定报告 DAG 或发明业务规则 |
| claw-trade 执行层 | 查数据、判断触发、发报告、发通知、保存证据 | 伪造数据、绕过 gate、替 LLM 写报告内容 |

## 定时报表设计

### 用户创建

用户在 UI 创建每日/每周报告任务。

claw-trade 做：

1. 校验 ticker、market、schedule、profile。
2. 保存一条业务任务记录，状态先是 `provisioning`。
3. 调 OpenClaw Gateway 创建 cron job。
4. 成功后保存 `openclawCronJobId`，把业务任务改为 `active`。
5. 失败则返回明确失败，不假装任务已经生效。

### cron 到点

OpenClaw cron 到点后唤醒 `scheduled_report_runner`。

该 worker 只负责把“现在该跑这个报告任务了”交给 claw-trade 的报告入口。

claw-trade 再按现有报告架构执行：

```text
/report 语义入口
  -> claw-trade workflow state machine
  -> 逐个 OpenClaw wake 12 个报告 worker
  -> artifact 校验
  -> portfolio manager 最终决策
  -> 导出/通知
```

关键点：

- cron 不能直接调度 12 个 worker。
- OpenClaw 不拥有 TradingAgents DAG。
- 定时报表必须走和 `/report` 一致的报告入口语义，避免定时任务和手动报告变成两套产品。

### 手动 run-now

用户点击立即运行时：

- claw-trade 可以直接触发同一个报告入口。
- 也可以调用 OpenClaw `cron.run` 运行对应 job。

推荐：

- 如果用户是在“任务详情页”点击 run-now，用 `cron.run`，这样运行历史归到同一个 cron job。
- 如果用户是在聊天里输入 `/report`，直接走报告入口，不绑定 cron job。

## 价格提醒设计

### 核心结论

不要“一个价格提醒一个定时器”。

也不要只做“一个全局定时器扫所有东西”。

推荐按市场和频率做少量扫描任务：

```text
price-alert-scan:CRYPTO:3m
price-alert-scan:CN_A:3m
price-alert-scan:US:3m
```

以后如果产品允许不同频率，再扩展成：

```text
price-alert-scan:{market}:{frequency}
```

### 为什么不用每个提醒一个 cron job

用户增加 1000 个提醒，不应该生成 1000 个 OpenClaw cron job。

每个提醒一个 job 的问题：

- Gateway cron job 数量膨胀。
- 同一 ticker 会重复取行情。
- 限流和缓存难做。
- 同一时间大量 job 到点，容易形成峰值。
- 失败重试会分散在很多 job 里，不利于统一治理。

价格提醒天然适合批量扫描：

- 同市场、同频率的提醒可以一起查。
- 同 ticker 可以合并成一次 quote。
- 触发判断是确定性比较，不需要 LLM 逐条参与。

### 用户创建价格提醒

用户创建提醒时，claw-trade 做：

1. 校验 ticker、market、condition、target price。
2. 保存提醒记录。
3. 计算它属于哪个扫描桶，比如 `CRYPTO:3m`。
4. 确保这个扫描桶对应的 OpenClaw cron job 已存在。
5. 返回提醒已创建。

新增提醒不会新增一个 cron job。

它只是加入对应扫描桶。

### cron 扫描流程

OpenClaw cron 到点后唤醒 `price_alert_scan_worker`。

worker 调 claw-trade 的批量扫描入口。

claw-trade 执行：

1. 读取该市场/频率下所有 active alert。
2. 按 ticker/provider 分组。
3. 通过 data gateway 批量取 quote。
4. 记录 quote 证据、provider、时间戳、错误。
5. 用确定性代码比较条件。
6. 对触发的提醒发送通知。
7. 触发后关闭一次性提醒，或按产品规则更新下一次触发状态。
8. 保存 scan run 结果。

### 价格判断规则

LLM 不参与价格比较。

比较规则必须是代码：

```text
above: latest_price >= target_price
below: latest_price <= target_price
```

如果 quote 缺失：

- 不触发提醒。
- 记录错误和证据链缺口。
- 不伪造价格。
- 不把失败降级成成功。

### 扫描频率

第一版建议：

| 市场 | 频率 | 时间范围 |
| --- | --- | --- |
| CRYPTO | 3 分钟 | 24/7 |
| CN_A | 3 分钟 | A 股交易时段 |
| US | 3 分钟 | 美股交易时段，未来启用 |

如果 OpenClaw cron 本身不理解交易时段，做法是：

- cron 仍按固定间隔唤醒。
- claw-trade 扫描入口先判断当前是否在该市场可扫描时间。
- 非交易时段记录 skipped，不取行情。

## 后台数据下载设计

后台数据下载也使用 OpenClaw cron 当时钟。

但它不是价格提醒，也不是用户报告。

建议独立任务：

```text
data-maintenance:CN_A:eod
data-maintenance:CRYPTO:kline-refresh
data-maintenance:CRYPTO:daily-universe
data-maintenance:US:eod
```

OpenClaw cron 到点后唤醒 `market_data_maintenance_worker`。

worker 调 claw-trade 数据维护入口。

claw-trade 数据层负责：

- 市场日历。
- 数据源选择。
- 限流和冷却。
- 增量下载。
- 数据质量校验。
- 失败重试策略。
- 证据和审计记录。

默认不通知普通用户。

失败通知应该走系统运维通道，而不是用户价格提醒通道。

## 需要新增或调整的内部对象

### 业务任务记录

定时报表记录需要保存：

- 业务任务 id。
- 用户 id。
- ticker / market / profile。
- schedule。
- 状态：`provisioning`、`active`、`paused`、`deleted`、`error`。
- `openclawCronJobId`。
- 最近一次 cron run id。
- 最近一次报告 run id。
- 最近错误。

价格提醒记录需要保存：

- alert id。
- 用户 id。
- ticker / market。
- condition。
- target price。
- scan bucket。
- 状态：`active`、`paused`、`triggered`、`deleted`、`error`。
- last checked at。
- last quote evidence ref。
- notification idempotency key。

扫描桶记录需要保存：

- market。
- frequency。
- OpenClaw cron job id。
- enabled 状态。
- last scan run id。
- last scan summary。

### OpenClaw cron adapter

claw-trade 需要一个很薄的适配层，负责调用 OpenClaw Gateway：

- add job。
- update job。
- remove job。
- run job。
- list/status 校验。

这个 adapter 只做协议转换。

它不判断价格，不调度报告 DAG，不下载行情。

## 幂等和一致性

### 创建任务

创建定时报表或扫描桶时：

1. claw-trade 先创建内部 `provisioning` 记录。
2. 调 OpenClaw `cron.add`。
3. 成功后保存 cron job id 并置为 `active`。
4. 失败则保留错误记录并返回失败。

不能在 cron 创建失败时告诉用户“任务已创建”。

### 暂停/恢复/删除

暂停、恢复、删除要同时影响：

- claw-trade 业务状态。
- OpenClaw cron job 状态。

推荐规则：

- OpenClaw 更新成功后，再更新用户可见业务状态。
- 如果 OpenClaw 更新失败，保留原状态并返回错误。
- 如果发生部分成功，记录 `sync_error`，后台修复，不对用户隐瞒。

### 通知幂等

价格提醒通知 key 建议：

```text
alert_id + condition_version + quote_timestamp + trigger_side
```

同一个 quote 触发同一个提醒，不应重复通知。

## 与现有代码的关系

### 保留

现有 service 里的这些能力可以保留：

- UI DTO。
- 参数校验。
- 幂等 key 思路。
- price condition 比较函数。
- run-now API 形状。

### 改造

这些部分需要改造：

- in-memory store 改为持久化 store。
- `tick_scheduled_reports` 不再是长期轮询核心。
- price alert 的自动检查改为 OpenClaw cron 唤醒扫描 worker。
- quote provider 必须接入真实 data gateway 和证据链。
- 定时报表到点执行必须走 `/report` 语义入口。

### 删除或降级

这些不能作为最终架构：

- claw-trade 自己常驻 timer。
- 每个 alert 一个 Python timer。
- 每个 alert 一个 OpenClaw cron job。
- 用 LLM 判断价格是否达到。
- cron 直接编排 12-worker 报告流程。

## Worker 建议

需要新增或确认这些 agent：

```text
scheduled_report_runner
price_alert_scan_worker
market_data_maintenance_worker
```

它们都是 OpenClaw agent。

但它们不是报告链里的 12 个投资研究 worker。

它们的职责是“被定时叫醒后，把任务交给 claw-trade 对应入口执行”。

## OpenClaw cron job 命名

建议命名：

```text
scheduled-report:{userId}:{taskId}
price-alert-scan:{market}:{frequency}
data-maintenance:{market}:{jobKind}
```

说明：

- 定时报表是用户任务，可以一任务一 cron job。
- 价格提醒是批量扫描，不是一提醒一 cron job。
- 数据维护是系统任务，按市场和任务类型拆分。

## 失败处理

### 行情失败

- 本次扫描记录失败。
- 具体 alert 记录 last error。
- 不触发通知。
- 不伪造价格。
- 不自动换未批准的数据源。

### 报告失败

- 保留报告 run id 和失败 stage。
- 不导出假报告。
- 不让 Python 补写 PM 结论。
- 后续可支持重跑，但必须重新进入报告 workflow。

### cron 失败

- OpenClaw cron 负责基础运行记录和 retry。
- claw-trade 保存业务层失败原因。
- 用户可见状态不能和真实 cron 状态矛盾。

## 测试和验收

### 单元测试

- schedule 到 OpenClaw cron payload 的映射。
- price condition 比较。
- scan bucket 选择。
- notification 幂等 key。
- 非交易时段 skipped。

### 集成测试

- 创建定时报表会调用 OpenClaw `cron.add`。
- 暂停/恢复/删除会调用 `cron.update` 或 `cron.remove`。
- cron 唤醒 `scheduled_report_runner` 后进入 `/report` 语义入口。
- cron 唤醒 `price_alert_scan_worker` 后批量扫描 active alerts。
- 同 ticker 多个 alert 只取一次 quote。

### 运行时验收

实现后，OpenClaw runtime 相关改动必须按项目规则验证：

1. 启动 `scripts/start-control-runtime.sh`。
2. 确认 OpenViking 和 OpenClaw Gateway 健康。
3. 创建真实 cron job。
4. 观察 cron 到点唤醒 worker。
5. 检查 run history。
6. 检查 claw-trade 业务记录和证据。

如果没有经过这套 live proof，不能宣称运行时完成。

## 分阶段实施建议

### Phase 1：接 OpenClaw cron adapter

- 新增 claw-trade 到 OpenClaw Gateway cron API 的适配层。
- 保持现有 UI API 不大改。
- 定时报表创建时能注册 OpenClaw cron job。

### Phase 2：定时报表跑通

- 新增 `scheduled_report_runner`。
- cron 到点后进入 `/report` 语义入口。
- run-now 和 cron-run 运行历史能对应起来。

### Phase 3：价格提醒扫描

- 新增 scan bucket。
- 新增 `price_alert_scan_worker`。
- 接真实 quote provider。
- 批量扫描、确定性触发、通知幂等。

### Phase 4：后台数据维护

- 新增 `market_data_maintenance_worker`。
- A 股和加密币先接。
- 美股按同一机制预留，不提前假实现。

### Phase 5：持久化和恢复

- 定时报表、价格提醒、扫描桶、cron job mapping 全部持久化。
- 进程重启后能从 OpenClaw cron 和 claw-trade store 恢复一致状态。

## 停止条件

遇到下面情况必须停下来重新确认：

- 需要把价格判断写进 OpenClaw core。
- 需要让 OpenClaw cron 编排 12-worker 报告 DAG。
- 需要让 LLM 判断价格是否触发。
- 需要绕过 data gateway 限流或证据链。
- 需要把 PM 最终投资结论交给 Python 改写。
- 需要引入每个 alert 一个 cron job 的策略。
- OpenClaw cron API 不能满足唤醒 worker 的基本需求。

## 当前判断

现有 claw-trade service 代码不是完整实现。

它有 UI-facing 的业务骨架，但还没有按这个新架构接入 OpenClaw cron，也没有真实行情 provider 和自动扫描链路。

下一步应该先做 OpenClaw cron adapter 和定时报表接入，因为这能最直接验证“OpenClaw 管时钟、claw-trade 管报告入口”的边界。
