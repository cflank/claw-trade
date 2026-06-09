# 定时任务接入 OpenClaw Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把定时报表、价格提醒扫描、后台数据维护接入 OpenClaw cron，让 OpenClaw 负责定时唤醒，claw-trade 负责业务执行和证据。

**Architecture:** OpenClaw cron 是唯一长期定时器；claw-trade 通过一个很薄的 cron adapter 注册、更新、删除、手动运行 cron job。cron 到点后唤醒 OpenClaw agent，agent 再调用 claw-trade 的业务入口；价格判断、报告 DAG、数据下载都留在 claw-trade。

**Tech Stack:** Python `uv`、FastAPI UI backend、OpenClaw Gateway RPC、OpenClaw agent profiles、claw-trade data gateway、pytest。

---

状态：实施计划，未代表代码已完成。
日期：2026-06-07
来源设计：`docs/定时任务接入OpenClaw设计.md`

## 0. 口径冻结

本计划做：

- 定时报表接入 OpenClaw cron。
- 价格提醒改为按市场/频率扫描桶，不是一条提醒一个 cron job。
- A 股、加密币、未来美股后台数据维护共用 OpenClaw cron 唤醒机制。
- 现有 UI API 形状尽量不变。
- 用户 DTO 不暴露 OpenClaw cron job id。
- 所有行情和数据下载必须通过 data gateway 的 gate、缓存、限流、证据链。

本计划不做：

- 不改 OpenClaw core 来承载 claw-trade 业务逻辑。
- 不让 OpenClaw cron 编排 12-worker 报告 DAG。
- 不让 LLM 判断价格是否触发。
- 不做每个价格提醒一个 cron job。
- 不实现未批准的新数据源 fallback。
- 不改 PM 最终投资结论权限。
- 不新增运行时 guard。

## 1. 成功标准

完成后必须同时满足：

- 创建定时报表时，claw-trade 会注册 OpenClaw cron job，并保存内部 mapping。
- 暂停、恢复、删除定时报表时，OpenClaw cron job 和 claw-trade 业务状态一致。
- 定时报表 cron 到点后唤醒 `scheduled_report_runner`，最终进入 `/report` 语义入口。
- 价格提醒创建后只加入扫描桶；同一市场/频率只有一个扫描 cron job。
- 价格扫描按 ticker 合并取 quote；同 ticker 多提醒不重复取行情。
- 价格触发判断由确定性代码完成。
- quote provider 未接通真实 data gateway 时必须 fail closed，不能假成功。
- 后台数据维护 cron job 只触发 data gateway maintenance 入口，不通知普通用户。
- live runtime 验收能看到 OpenClaw cron run history、worker wake、claw-trade 业务执行证据。

## 2. 文件结构计划

### 新增文件

- `src/claw_trade/ui_backend/openclaw_cron_adapter.py`
  OpenClaw cron 协议适配层，只做 `cron.add/update/remove/run/list/status/runs` 调用和 payload 映射。

- `src/claw_trade/ui_backend/scheduled_work_store.py`
  定时报表、价格提醒、扫描桶的持久化 store。第一版使用 JSON 文件或已有 repository 注入，不使用内存作为最终状态。

- `src/claw_trade/ui_backend/price_alert_scan_service.py`
  批量扫描 active price alerts，按市场/频率/ticker 合并 quote，确定性判断触发。

- `src/claw_trade/ui_backend/scheduled_work_runner.py`
  cron wake 后的业务入口：跑定时报表、跑价格扫描、跑数据维护。

- `src/claw_trade/data_gateway/price_quote_provider.py`
  从 data gateway 读取 quote snapshot 或最近 bar，输出 price alert 需要的 quote payload。

- `src/claw_trade/data_gateway/maintenance/scheduled_runner.py`
  把 `data-maintenance:{market}:{jobKind}` 映射到已有 maintenance job。

- `agents/scheduled_report_runner/IDENTITY.md`
- `agents/scheduled_report_runner/USER.md`
- `agents/scheduled_report_runner/SKILLS.md`
- `agents/scheduled_report_runner/STAGES.yaml`

- `agents/price_alert_scan_worker/IDENTITY.md`
- `agents/price_alert_scan_worker/USER.md`
- `agents/price_alert_scan_worker/SKILLS.md`
- `agents/price_alert_scan_worker/STAGES.yaml`

- `agents/market_data_maintenance_worker/IDENTITY.md`
- `agents/market_data_maintenance_worker/USER.md`
- `agents/market_data_maintenance_worker/SKILLS.md`
- `agents/market_data_maintenance_worker/STAGES.yaml`

### 修改文件

- `src/claw_trade/web/openclaw_gateway.py`
  给 Gateway client 增加 cron 方法。

- `src/claw_trade/web/state.py`
  组装 cron adapter、持久化 store、runner service，并注入现有 service。

- `src/claw_trade/web/routes_ui.py`
  保持用户 API，必要时增加内部 cron wake route。内部 route 不作为用户 API。

- `src/claw_trade/ui_backend/scheduler_service.py`
  从自有 tick 定时器改为 OpenClaw cron provisioning；保留 run-now 语义。

- `src/claw_trade/ui_backend/price_alert_service.py`
  创建提醒时加入扫描桶；保留单条 run-now；批量扫描交给 scan service。

- `src/claw_trade/data_gateway/source_probe.py`
  `build_price_alert_quote_provider()` 接真实 quote provider，仍保持证据链缺失 fail closed。

### 测试文件

- `tests/unit/ui/test_openclaw_cron_adapter.py`
- `tests/unit/ui/test_scheduled_work_store.py`
- `tests/unit/ui/test_scheduler_openclaw_cron_integration.py`
- `tests/unit/ui/test_price_alert_scan_service.py`
- `tests/unit/ui/test_scheduled_work_runner.py`
- `tests/unit/data_gateway/test_price_alert_quote_provider.py`
- `tests/unit/data_gateway/test_scheduled_data_maintenance_runner.py`
- `tests/contracts/test_scheduled_and_alert_api_contracts.py`
- `tests/integration/test_openclaw_cron_scheduled_work_contract.py`

## 3. Task Graph

| 任务 | 目标 | 依赖 | 并行性 |
| --- | --- | --- | --- |
| TC-00 | 前置审计和当前行为冻结 | 无 | 串行第一步 |
| TC-01 | OpenClaw cron Gateway client 和 adapter | TC-00 | 串行 |
| TC-02 | 持久化业务 store 和内部字段 | TC-01 | 串行 |
| TC-03 | 定时报表注册 cron 和 cron wake 执行 | TC-02 | 串行 |
| TC-04 | runner agent profile 和内部 wake 入口 | TC-03 | 串行 |
| TC-05 | 价格提醒扫描桶和批量扫描 | TC-02 | 可与 TC-04 后半并行 |
| TC-06 | 真实 quote provider 接 data gateway | TC-05 | 串行 |
| TC-07 | 后台数据维护 cron runner | TC-01 | 可与 TC-05 并行 |
| TC-08 | UI state/routes/contracts 收口 | TC-03、TC-05、TC-07 | 串行 |
| TC-09 | 集成测试和 live runtime proof | TC-08 | 串行最后 |
| TC-10 | 文档、证据、memory 收尾 | TC-09 | 串行 |

## 4. Tasks

### TC-00：前置审计和当前行为冻结

目标：开始改代码前固定现有行为，避免把骨架误认为完整实现。

**Files:**

- Read: `AGENTS.md`
- Read: `docs/定时任务接入OpenClaw设计.md`
- Read: `src/claw_trade/ui_backend/scheduler_service.py`
- Read: `src/claw_trade/ui_backend/price_alert_service.py`
- Read: `src/claw_trade/web/openclaw_gateway.py`
- Read: `third_party/openclaw/docs/automation/cron-jobs.md`
- Create: `docs/evidence/openclaw-cron-scheduled-work-preflight-20260607.md`

- [ ] **Step 1: 记录当前实现事实**

在 evidence 文档写清楚：

```text
SchedulerService 当前使用内存 _items。
PriceAlertService 当前使用内存 _items。
tick_scheduled_reports 当前没有生产 cron caller 证据。
build_price_alert_quote_provider 当前 fail closed，没有真实 quote 实现。
OpenClaw Gateway 已暴露 cron.* 方法。
```

- [ ] **Step 2: 跑现有 focused tests**

Run:

```bash
uv run pytest tests/unit/ui/test_scheduler_service.py tests/unit/ui/test_price_alert_service.py tests/contracts/test_scheduled_and_alert_api_contracts.py -q
```

Expected:

```text
PASS，或记录失败原因；失败不能绕过。
```

- [ ] **Step 3: 确认 OpenClaw cron payload 形状**

只读检查：

```bash
rg -n "type CronPayload|cron.add|agentTurn|systemEvent" third_party/openclaw/src/cron third_party/openclaw/src/gateway/server-methods/cron.ts
```

Expected:

```text
确认 cron.add 支持到点唤醒 agent turn。
```

Stop conditions：

- 发现 OpenClaw cron 不能唤醒 agent。
- 需要把 claw-trade 业务逻辑写进 OpenClaw core。
- 现有 tests 红且无法解释根因。

### TC-01：OpenClaw cron Gateway client 和 adapter

目标：让 claw-trade 能通过 Gateway 注册、更新、删除、手动运行 OpenClaw cron job。

**Files:**

- Modify: `src/claw_trade/web/openclaw_gateway.py`
- Create: `src/claw_trade/ui_backend/openclaw_cron_adapter.py`
- Test: `tests/unit/ui/test_openclaw_cron_adapter.py`

- [ ] **Step 1: 写 adapter 测试**

测试覆盖：

```python
def test_add_scheduled_report_cron_job_maps_to_gateway_cron_add():
    calls = []
    adapter = OpenClawCronAdapter(gateway=FakeCronGateway(calls))
    result = adapter.ensure_scheduled_report_job(
        task_id="schedule-1",
        user_id="user-1",
        schedule={"type": "every", "intervalMs": 86_400_000},
        agent_id="scheduled_report_runner",
        payload={"scheduledReportId": "schedule-1"},
    )
    assert calls[0]["method"] == "cron.add"
    assert calls[0]["params"]["name"] == "scheduled-report:user-1:schedule-1"
    assert result.openclaw_cron_job_id
```

- [ ] **Step 2: 测试 pause/resume/delete/run 映射**

覆盖：

```text
pause -> cron.update enabled=false
resume -> cron.update enabled=true
delete -> cron.remove
run-now -> cron.run
```

- [ ] **Step 3: 给 Gateway client 加 cron 方法**

在 `OpenClawGatewayRpcClient` 增加：

```python
def cron_add(self, params: Mapping[str, Any]) -> Any:
    return self._call("cron.add", params, timeout_ms=15_000)

def cron_update(self, params: Mapping[str, Any]) -> Any:
    return self._call("cron.update", params, timeout_ms=15_000)

def cron_remove(self, *, job_id: str) -> Any:
    return self._call("cron.remove", {"jobId": job_id}, timeout_ms=15_000)

def cron_run(self, *, job_id: str, idempotency_key: str | None = None) -> Any:
    params = {"jobId": job_id}
    if idempotency_key:
        params["idempotencyKey"] = idempotency_key
    return self._call("cron.run", params, timeout_ms=30_000)
```

如果 OpenClaw 实际参数名不是 `jobId`，按 `third_party/openclaw/src/gateway/server-methods/cron.ts` 修正测试和实现。

- [ ] **Step 4: 实现 adapter**

adapter 只返回结构化结果：

```python
@dataclass(frozen=True)
class CronProvisionResult:
    openclaw_cron_job_id: str
    raw: Mapping[str, Any]
```

不在 adapter 里判断价格、不 enqueue report、不下载数据。

- [ ] **Step 5: 跑测试**

Run:

```bash
uv run pytest tests/unit/ui/test_openclaw_cron_adapter.py -q
```

Expected:

```text
PASS
```

Stop conditions：

- OpenClaw cron API 参数和文档不一致，且无法从源码确认。
- adapter 需要知道 price/report/data 业务细节。

### TC-02：持久化业务 store 和内部字段

目标：让业务任务从内存骨架升级为可恢复状态，但用户 DTO 不暴露 cron id。

**Files:**

- Create: `src/claw_trade/ui_backend/scheduled_work_store.py`
- Modify: `src/claw_trade/ui_backend/scheduler_service.py`
- Modify: `src/claw_trade/ui_backend/price_alert_service.py`
- Test: `tests/unit/ui/test_scheduled_work_store.py`
- Test: `tests/contracts/test_scheduled_and_alert_api_contracts.py`

- [ ] **Step 1: 写 store 测试**

覆盖：

```text
save/load scheduled report
save/load price alert
save/load scan bucket
idempotency survives service reconstruction
user DTO 不包含 openclawCronJobId
```

- [ ] **Step 2: 新增 store 协议**

`scheduled_work_store.py` 包含：

```python
class ScheduledWorkStore(Protocol):
    def save_scheduled_report(self, item: ScheduledReport) -> None: ...
    def get_scheduled_report(self, item_id: str) -> ScheduledReport | None: ...
    def list_scheduled_reports(self) -> tuple[ScheduledReport, ...]: ...
    def save_price_alert(self, item: PriceAlert) -> None: ...
    def get_price_alert(self, item_id: str) -> PriceAlert | None: ...
    def list_price_alerts(self, *, market: MarketProfile | None = None, state: str | None = None) -> tuple[PriceAlert, ...]: ...
    def save_scan_bucket(self, bucket: PriceAlertScanBucket) -> None: ...
    def get_scan_bucket(self, key: str) -> PriceAlertScanBucket | None: ...
```

- [ ] **Step 3: 增加内部字段**

`ScheduledReport` 增加内部字段：

```text
openclaw_cron_job_id
last_cron_run_id
sync_error_message
```

`PriceAlert` 增加内部字段：

```text
scan_bucket
condition_version
last_quote_evidence_ref
notification_dedupe_key
```

这些字段不得进入 `to_scheduled_report_for_user()` 或 `to_price_alert_for_user()`。

- [ ] **Step 4: 实现第一版 JSON store**

推荐路径：

```text
<run_root>/.ui-scheduled-work.json
```

要求：

- 原子写入。
- load 失败 fail closed。
- 不吞掉 JSON parse error。
- 保留 created/updated 时间。

- [ ] **Step 5: 替换 service 内存读写**

`SchedulerService` 和 `PriceAlertService` 通过 store 读写。

保留 `_idempotency` 可以作为进程内优化，但持久化状态必须来自 store。

- [ ] **Step 6: 跑测试**

Run:

```bash
uv run pytest tests/unit/ui/test_scheduled_work_store.py tests/contracts/test_scheduled_and_alert_api_contracts.py -q
```

Expected:

```text
PASS
```

Stop conditions：

- 为了通过测试把 cron id 暴露到用户 DTO。
- store load 失败时静默回到空状态。
- 需要改已有 report repository 大结构。

### TC-03：定时报表接入 OpenClaw cron

目标：定时报表创建、暂停、恢复、删除、run-now 都通过 OpenClaw cron 保持一致。

**Files:**

- Modify: `src/claw_trade/ui_backend/scheduler_service.py`
- Modify: `src/claw_trade/web/state.py`
- Test: `tests/unit/ui/test_scheduler_openclaw_cron_integration.py`
- Test: `tests/unit/ui/test_scheduler_queue_integration.py`

- [ ] **Step 1: 写创建任务测试**

测试：

```text
create_scheduled_report -> state provisioning -> cron.add -> state active
cron.add 失败 -> 返回 UiServiceError，不返回 active DTO
```

- [ ] **Step 2: 注入 cron adapter**

`SchedulerService.__init__` 增加可选依赖：

```python
cron_adapter: OpenClawCronAdapter | None = None
user_id_provider: Callable[[], str] | None = None
```

没有 cron adapter 时只允许测试显式传 `allow_local_tick_for_tests=True`。

- [ ] **Step 3: create 时注册 OpenClaw cron**

流程：

```text
build ScheduledReport(state=provisioning)
store.save
cron_adapter.ensure_scheduled_report_job(...)
写入 openclaw_cron_job_id
state=active
store.save
return user DTO
```

- [ ] **Step 4: pause/resume/delete 同步 OpenClaw**

规则：

```text
pause: cron.update enabled=false 成功后 state=paused
resume: cron.update enabled=true 成功后 state=active
delete: cron.remove 成功后 state=deleted
失败: 保留原用户可见状态，写 sync_error_message，返回 UiServiceError
```

- [ ] **Step 5: run-now 使用 cron.run**

任务详情页 run-now：

```text
如果有 openclaw_cron_job_id -> cron.run
如果没有 -> fail closed，不直接绕过 cron
```

聊天 `/report` 不受影响，仍走报告入口。

- [ ] **Step 6: 保留内部 cron wake handler**

新增方法：

```python
def handle_scheduled_report_cron_wake(self, *, scheduled_report_id: str, cron_run_id: str, request_id: str) -> dict[str, Any]:
    ...
```

它负责 enqueue report task，来源是 `scheduled`。

- [ ] **Step 7: 降级 `tick_scheduled_reports`**

`tick_scheduled_reports` 不能再作为生产路径。

保留时必须：

```text
只给旧测试或迁移诊断使用
文档/注释写明 deprecated
不能被 web state 自动启动
```

- [ ] **Step 8: 跑测试**

Run:

```bash
uv run pytest tests/unit/ui/test_scheduler_openclaw_cron_integration.py tests/unit/ui/test_scheduler_queue_integration.py -q
```

Expected:

```text
PASS
```

Stop conditions：

- run-now 需要绕过 cron 才能成功。
- cron.add 失败但 UI 仍显示 active。
- 定时报表到点执行不走 `/report` 语义入口。

### TC-04：runner agent profile 和内部 wake 入口

目标：OpenClaw cron 到点后唤醒 agent，agent 再调用 claw-trade 内部入口。

**Files:**

- Create: `src/claw_trade/ui_backend/scheduled_work_runner.py`
- Modify: `src/claw_trade/web/routes_ui.py`
- Create: `agents/scheduled_report_runner/IDENTITY.md`
- Create: `agents/scheduled_report_runner/USER.md`
- Create: `agents/scheduled_report_runner/SKILLS.md`
- Create: `agents/scheduled_report_runner/STAGES.yaml`
- Test: `tests/unit/ui/test_scheduled_work_runner.py`

- [ ] **Step 1: 写 runner service 测试**

测试：

```text
kind=scheduled_report -> 调 SchedulerService.handle_scheduled_report_cron_wake
kind=price_alert_scan -> 调 PriceAlertScanService.scan_bucket
kind=data_maintenance -> 调 maintenance runner
unknown kind -> fail closed
```

- [ ] **Step 2: 实现 `ScheduledWorkRunner`**

输入 payload：

```python
{
    "kind": "scheduled_report",
    "scheduledReportId": "schedule-1",
    "cronRunId": "run-1"
}
```

输出必须包含：

```text
kind
business id
cronRunId
status
evidence/ref 或 task id
error
```

- [ ] **Step 3: 新增内部 route**

建议 route：

```text
POST /internal/scheduled-work/cron-wake
```

要求：

- 只接受内部调用 token 或本地 runtime 标记。
- 不作为用户 API 文档暴露。
- 不返回用户 DTO。
- 不写投资报告内容。

- [ ] **Step 4: 创建 `scheduled_report_runner` agent profile**

profile 要求：

```text
身份：定时报表唤醒 worker。
任务：收到 cron payload 后调用 claw-trade 内部 wake 工具/API。
禁止：分析股票、写报告、决定下一步 worker、改 PM 结论。
```

- [ ] **Step 5: 明确工具暴露方式**

如果现有 OpenClaw agent 无法调用 claw-trade 内部 API，停下来确认。

允许方案：

```text
通过已有 OpenClaw/Gateway 工具机制暴露一个 generic internal HTTP call。
```

不允许方案：

```text
把定时报表业务逻辑写进 third_party/openclaw。
```

- [ ] **Step 6: 跑测试**

Run:

```bash
uv run pytest tests/unit/ui/test_scheduled_work_runner.py -q
```

Expected:

```text
PASS
```

Stop conditions：

- 需要 OpenClaw core 承担 claw-trade 报告业务。
- agent 无工具可调用 claw-trade 且需要改动超出通用 runtime seam。

### TC-05：价格提醒扫描桶和批量扫描

目标：价格提醒自动检查从“单条 run-now”升级为“市场/频率扫描桶”。

**Files:**

- Modify: `src/claw_trade/ui_backend/price_alert_service.py`
- Create: `src/claw_trade/ui_backend/price_alert_scan_service.py`
- Modify: `src/claw_trade/ui_backend/scheduled_work_store.py`
- Create: `agents/price_alert_scan_worker/IDENTITY.md`
- Create: `agents/price_alert_scan_worker/USER.md`
- Create: `agents/price_alert_scan_worker/SKILLS.md`
- Create: `agents/price_alert_scan_worker/STAGES.yaml`
- Test: `tests/unit/ui/test_price_alert_scan_service.py`
- Test: `tests/unit/ui/test_price_alert_service.py`

- [ ] **Step 1: 写扫描桶测试**

测试：

```text
CRYPTO alert -> scan bucket CRYPTO:3m
CN_A alert -> scan bucket CN_A:3m
同 bucket 多 alert -> 只 ensure 一个 cron job
新增 alert 不新增 per-alert cron job
```

- [ ] **Step 2: create alert 时 ensure bucket cron**

流程：

```text
normalize alert
bucket_key = "{market}:3m"
ensure scan bucket record
如果 bucket 没有 openclawCronJobId -> cron.add price-alert-scan:{market}:3m
save alert(scan_bucket=bucket_key)
```

- [ ] **Step 3: 实现批量扫描**

`PriceAlertScanService.scan_bucket(bucket_key, cron_run_id)`：

```text
读取 active alerts
按 instrument_code + market 分组
每组只取一次 quote
逐条调用 PriceAlertService 的确定性判断
触发则通知并关闭
失败则记录 alert last_error_message
保存 scan summary
```

- [ ] **Step 4: 通知幂等**

dedupe key：

```text
alert_id + condition_version + quote_timestamp + trigger_side
```

同一个 quote 不能重复发通知。

- [ ] **Step 5: 非交易时段 skip**

第一版：

```text
CRYPTO: 不 skip
CN_A: 非交易时段 skipped，不取行情
US: 预留，未启用时 skipped
```

如果市场日历缺失，记录 `market_calendar_unavailable`，不伪装成成功扫描。

- [ ] **Step 6: 创建 `price_alert_scan_worker` profile**

profile 要求：

```text
只负责唤醒扫描入口。
不判断价格。
不写投资建议。
不生成报告。
```

- [ ] **Step 7: 跑测试**

Run:

```bash
uv run pytest tests/unit/ui/test_price_alert_service.py tests/unit/ui/test_price_alert_scan_service.py -q
```

Expected:

```text
PASS
```

Stop conditions：

- 实现变成每条 alert 一个 cron job。
- LLM 参与价格比较。
- quote 失败时触发通知。

### TC-06：真实 quote provider 接 data gateway

目标：把 `build_price_alert_quote_provider()` 从 unimplemented 接到真实 data gateway 读取路径。

**Files:**

- Create: `src/claw_trade/data_gateway/price_quote_provider.py`
- Modify: `src/claw_trade/data_gateway/source_probe.py`
- Modify: `src/claw_trade/web/state.py`
- Test: `tests/unit/data_gateway/test_price_alert_quote_provider.py`
- Test: `tests/unit/ui/test_price_alert_service.py`

- [ ] **Step 1: 写 fail-closed 测试**

覆盖：

```text
缺 evidence_helper/cache/rate_limit/single_flight/attempt_store -> evidence_chain_unavailable
data_api 无 quote 路径 -> price_alert_quote_unavailable
provider 返回缺 current_price -> invalid_quote_payload
```

- [ ] **Step 2: 写 successful quote 测试**

使用 fake data_api 返回：

```python
{
    "current_price": 71000.0,
    "percent_change": 2.5,
    "percent_change_24h": 2.5,
    "quote_timestamp": "2026-05-19T12:00:00Z",
    "evidence_ref": "dataset://normalized/quote_snapshot/..."
}
```

- [ ] **Step 3: 实现 provider**

优先顺序：

```text
quote_snapshot
最近 intraday_bar close
最近 daily_bar close
```

要求：

- 通过 `DataAPI.get_data()` 或 `get_data_batch()`。
- request consumer 写 `price_alert`。
- 保留 gaps 和 evidence ref。
- 没有数据时 fail closed。

- [ ] **Step 4: `source_probe.py` 接入**

`build_price_alert_quote_provider()` 只负责组装 provider。

不能在这里直接用裸 HTTP 绕过 data gateway。

- [ ] **Step 5: 跑测试**

Run:

```bash
uv run pytest tests/unit/data_gateway/test_price_alert_quote_provider.py tests/unit/ui/test_price_alert_service.py -q
```

Expected:

```text
PASS
```

Stop conditions：

- data gateway 当前没有 approved quote/bar 查询能力。
- 需要硬编码第三方 HTTP 源绕过 gate。
- 需要伪造 quote_timestamp 或 evidence_ref。

### TC-07：后台数据维护 cron runner

目标：A 股和加密币后台数据下载/维护也由 OpenClaw cron 唤醒，但执行仍在 data gateway。

**Files:**

- Create: `src/claw_trade/data_gateway/maintenance/scheduled_runner.py`
- Create: `agents/market_data_maintenance_worker/IDENTITY.md`
- Create: `agents/market_data_maintenance_worker/USER.md`
- Create: `agents/market_data_maintenance_worker/SKILLS.md`
- Create: `agents/market_data_maintenance_worker/STAGES.yaml`
- Modify: `src/claw_trade/ui_backend/scheduled_work_runner.py`
- Test: `tests/unit/data_gateway/test_scheduled_data_maintenance_runner.py`

- [ ] **Step 1: 写 job mapping 测试**

覆盖：

```text
data-maintenance:CN_A:eod -> run_daily_incremental market=CN_A dataset_scope=daily_bar
data-maintenance:CRYPTO:kline-refresh -> crypto maintenance entry
data-maintenance:US:eod -> future disabled/skipped
unknown jobKind -> fail closed
```

- [ ] **Step 2: 实现 scheduled runner**

输入：

```python
{
    "market": "CN_A",
    "jobKind": "eod",
    "cronRunId": "run-1"
}
```

输出：

```text
job id
market
dataset scope
status
manifest ref
error
```

- [ ] **Step 3: 接已有 maintenance job**

复用：

- `src/claw_trade/data_gateway/maintenance/jobs.py`
- `src/claw_trade/data_gateway/maintenance/incremental.py`
- `src/claw_trade/data_gateway/runtime.py`

不新增平行下载框架。

- [ ] **Step 4: 创建 worker profile**

profile 要求：

```text
只触发数据维护入口。
不通知普通用户。
不写报告。
不替 data gateway 选择未批准数据源。
```

- [ ] **Step 5: 跑测试**

Run:

```bash
uv run pytest tests/unit/data_gateway/test_scheduled_data_maintenance_runner.py -q
```

Expected:

```text
PASS
```

Stop conditions：

- 需要新建绕过 data gateway 的下载器。
- 后台维护失败被普通用户提醒通道消费。

### TC-08：UI state/routes/contracts 收口

目标：把 adapter、store、runner、quote provider 组装进真实 UI backend。

**Files:**

- Modify: `src/claw_trade/web/state.py`
- Modify: `src/claw_trade/web/routes_ui.py`
- Modify: `tests/contracts/test_scheduled_and_alert_api_contracts.py`
- Create: `tests/integration/test_openclaw_cron_scheduled_work_contract.py`

- [ ] **Step 1: state 组装 cron adapter**

`build_ui_http_services()`：

```text
创建 OpenClawGatewayRpcClient
创建 OpenClawCronAdapter
创建 ScheduledWorkStore
创建 SchedulerService(cron_adapter=...)
创建 PriceAlertService(store=...)
创建 PriceAlertScanService(...)
创建 ScheduledWorkRunner(...)
```

- [ ] **Step 2: route 保持用户 API**

用户 API 不新增 OpenClaw 细节：

```text
/create-scheduled-report
/pause-scheduled-report
/resume-scheduled-report
/delete-scheduled-report
/run-scheduled-report-now
/create-price-alert
/run-price-alert-now
```

DTO 字段保持 contract 测试定义。

- [ ] **Step 3: 内部 wake route 只给 cron worker**

新增内部 route 时必须验证：

```text
缺 token -> 403
payload kind unknown -> error
成功 -> 返回业务执行摘要
```

- [ ] **Step 4: 写 integration contract**

使用 fake Gateway client 验证：

```text
create scheduled report calls cron.add
pause calls cron.update
delete calls cron.remove
run-now calls cron.run
create two CRYPTO alerts only creates one price-alert-scan cron job
```

- [ ] **Step 5: 跑测试**

Run:

```bash
uv run pytest tests/contracts/test_scheduled_and_alert_api_contracts.py tests/integration/test_openclaw_cron_scheduled_work_contract.py -q
```

Expected:

```text
PASS
```

Stop conditions：

- 为了集成方便改用户 DTO。
- 内部 wake route 无认证。
- state 里重新启动 Python timer。

### TC-09：集成测试和 live runtime proof

目标：证明不是只写了骨架，OpenClaw cron 真的能叫醒 worker，worker 真的进入 claw-trade。

**Files:**

- Create: `docs/evidence/openclaw-cron-scheduled-work-live-20260607.md`
- No source edits unless测试暴露真实 bug。

- [ ] **Step 1: 跑 focused regression**

Run:

```bash
uv run pytest tests/unit/ui/test_openclaw_cron_adapter.py tests/unit/ui/test_scheduler_openclaw_cron_integration.py tests/unit/ui/test_price_alert_scan_service.py tests/unit/data_gateway/test_price_alert_quote_provider.py tests/unit/data_gateway/test_scheduled_data_maintenance_runner.py tests/contracts/test_scheduled_and_alert_api_contracts.py -q
```

Expected:

```text
PASS
```

- [ ] **Step 2: live runtime preflight**

按项目规则执行：

```bash
scripts/start-control-runtime.sh -- uv run pytest tests/integration/test_openclaw_cron_scheduled_work_contract.py -q
```

如果该测试需要真实 Gateway health，先打印 preflight 表：

```text
OpenViking 1933 health
OpenClaw Gateway 18789 health
runtime.env
OPENVIKING_CONFIG_FILE
OPENVIKING_DATA_DIR
CLAW_TRADE_OPENVIKING_MCP_STARTED
```

- [ ] **Step 3: 创建真实短间隔 cron job**

使用 UI/API 创建一个测试定时报表，或用内部测试 fixture 注册：

```text
scheduled-report:test-user:schedule-live
```

Expected:

```text
OpenClaw cron.list 能看到 job
cron.runs 能看到 run
claw-trade store 能看到 last_cron_run_id
报告队列能看到 scheduled task
```

- [ ] **Step 4: 创建价格扫描 job**

创建两个同 ticker CRYPTO alert。

Expected:

```text
只有一个 price-alert-scan:CRYPTO:3m cron job
scan summary 显示 grouped quote count = 1
quote provider 若无真实数据源，必须记录 fail closed，不触发通知
```

- [ ] **Step 5: 写 live evidence**

`docs/evidence/openclaw-cron-scheduled-work-live-20260607.md` 包含：

```text
命令
exit code
OpenClaw cron job id
cron run id
worker id
claw-trade business id
provider/quote evidence 或 fail-closed reason
deviation
```

Stop conditions：

- runtime 没按 `scripts/start-control-runtime.sh` 启动。
- OpenClaw cron run 没有实际 worker wake 证据。
- 只靠 fake Gateway 测试就宣称完成。

### TC-10：文档、证据、memory 收尾

目标：把完成状态说清楚，不 overclaim。

**Files:**

- Modify: `docs/定时任务接入OpenClaw设计.md` only if user approves design doc updates
- Modify: `docs/定时任务接入OpenClaw实施计划.md`
- Create/Modify: `memory/2026-06-07.md`
- Create: `docs/evidence/openclaw-cron-scheduled-work-final-20260607.md`

- [ ] **Step 1: 汇总实现状态**

记录：

```text
完成了哪些 task
哪些只是测试/fake
哪些经过 live runtime
哪些未完成
```

- [ ] **Step 2: 更新 memory**

写入：

```text
OpenClaw cron owns clock.
claw-trade owns business execution.
price alert uses market/frequency scan buckets.
No per-alert cron job.
No LLM price comparison.
```

- [ ] **Step 3: 最终验证命令**

Run:

```bash
uv run pytest tests/unit/ui/test_openclaw_cron_adapter.py tests/unit/ui/test_scheduler_openclaw_cron_integration.py tests/unit/ui/test_price_alert_scan_service.py tests/unit/data_gateway/test_price_alert_quote_provider.py tests/contracts/test_scheduled_and_alert_api_contracts.py -q
```

Expected:

```text
PASS
```

- [ ] **Step 4: 不自动提交**

本 repo 当前规则：除非用户明确要求，不自动 `git commit`。

如果用户要求提交，再执行：

```bash
git status --short
git add <changed files>
git commit -m "feat: wire scheduled work to OpenClaw cron"
```

## 5. 明确禁止

实现时不得：

- 在 claw-trade 里启动长期 Python timer 替代 OpenClaw cron。
- 为每条 price alert 创建一个 OpenClaw cron job。
- 让 OpenClaw cron 调度 12-worker 报告链。
- 让 LLM 判断价格是否达到阈值。
- 绕过 data gateway 直接裸调行情源。
- quote 失败时发提醒。
- cron.add 失败时返回 active。
- 把 OpenClaw cron job id 暴露给用户 DTO。
- 只跑 fake tests 就声称 live 完成。

## 6. 计划审查备注

本计划按 `superpowers:writing-plans` 格式编写。

当前会话没有用户明确授权启动 subagent，因此没有执行 plan-document-reviewer subagent 审查。实施前如需严格按 Superpowers review loop 走，需要用户明确允许我启动审查 subagent。
