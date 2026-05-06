# control 详细设计实施任务清单

> 来源：`docs/control详细设计方案.md`。
>
> 本清单用于把详细设计拆成可以直接执行的原子任务。每个任务都必须能独立收口：改哪些文件、补哪些函数、产出什么证据、如何判断达到预期，都要写清楚。
>
> 本清单不声明已经全量覆盖设计。它提供逐条覆盖追踪方法：每个任务完成时只能写 `达到预期`、`未达到预期`、`无法判断` 或 `BLOCKED`。

## 0. 执行规则

### 0.1 固定工程目录和代码根

- 工程目录和当前工作区固定为 `/home/frank/src/claw-trade`，这个目录已经存在，任何任务都不能创建、移动、嵌套或重命名它。
- 本清单里的 `./src/` 指 `/home/frank/src/claw-trade/src/`。
- 本清单里的 `./src/claw_trade/` 指 `/home/frank/src/claw-trade/src/claw_trade/`。
- `./src/` 下的目录结构参考已有的 `./src-bak/`；例如目标包名 `claw_trade` 来自 `./src-bak/claw_trade/`，不是另起一套名字。
- T00 只重建已有工程目录下面的 `./src/` 子目录，并按 `./src-bak/` 的结构重建代码包目录，不创建新的工程目录。
- `memory/` 已经存在，只能追加任务记录，不能作为任务创建对象，也不能新建另一个工作区。
- `./src-bak/` 只是只读结构参考，不能作为实现目标、导入路径、成功路径或整目录复制来源。

### 0.2 任务状态

每个任务只允许这些状态：

- `TODO`：未开始。
- `DOING`：正在做。
- `达到预期`：真实证据齐全，设计边界没有被破坏。
- `未达到预期`：证据存在但不满足设计，或发现越权、假成功、unsupported claim。
- `无法判断`：缺少足够证据判断，且不能合理归类为达到或未达到。
- `BLOCKED`：真实依赖或人类决策缺失，继续做会引入假成功或越权。

### 0.3 每个任务完成时必须记录

```text
任务编号：
状态：
设计引用：
改动文件：
新增/修改函数：
执行命令：
关键输出：
真实证据路径：
偏离设计：
禁止路径扫描结果：
中文注释覆盖：
下一步：
```

### 0.4 禁止实现路径

- 不允许 fake provider。
- 不允许 fake OpenViking receipt。
- 不允许把 `content/read` 展示文本当成正式 hash/size 口径；正式 SHA/size 统一以 OpenViking `content/download` 原始字节为准。
- 不允许 mock/stub/fake/fallback 成功路径。
- 不允许 fallback market profile。
- 不允许 compact/latest/list/目录扫描/裸 URI read 作为正式材料路径。
- 不允许用日志、renderer output、export report 或 Python 重构文本冒充 provider request。
- 不允许 OpenClaw 接管 12 worker DAG、批准、hard gate 或报告导出。
- 不允许 OpenViking 接管流程推进、批准、重试或最终结论。
- 不允许 Python 写业务分析正文、替 worker 调工具、改写 PM 结论。

### 0.5 中文注释要求

新增代码在这些位置必须有中文注释：

- 控制权边界：controller、runner、request_builder、openclaw_client、approval、exporter。
- hard gate：provider request、visible tools、tool calls、OpenViking receipt、runtime reads、L1/L2、claim、PM owner、export truthfulness。
- 越权防线：OpenClaw/OpenViking/Python 不该做但可能被误写的地方。
- 本地审计副本：`runs/<run>/openviking/` 不是正式材料权威。

注释要说明为什么必须阻断，以及防止谁越权。

## 1. 设计覆盖追踪矩阵

| 设计章节 | 任务覆盖 | 完成判定 |
| --- | --- | --- |
| 1. 设计结论 | T00, T01, T64, T65, T66 | 三方边界扫描无偏离 |
| 2. 从零构建策略 | T00-T03, T58-T63 | 从已有工程目录 `/home/frank/src/claw-trade` 下重建 `./src/` 子目录开始，按真实链路顺序拿到证据或 BLOCKED |
| 3. 模块划分与结构框架 | T00, T03, T16, T65 | 依赖方向和文件边界符合设计 |
| 4. 核心数据对象 | T04-T09 | 对象字段、序列化、结果语义一致 |
| 5. 固定 12 worker 流程 | T10-T16, T51, T51A, T52-T55, T53A-T53D | controller 只决策，runner 执行 |
| 6. Worker workspace 和 stage policy | T17-T21, T29A, T40 | Python 不生成 worker 文案，OpenClaw 产出真实 workspace evidence，HK/CRYPTO 未批准时失败 |
| 7. OpenClaw 单 worker runtime 接缝 | T27-T36, T29A, T34A, T38, T39 | 单 worker 证据齐全，OpenClaw 不接管 DAG |
| 8. OpenViking 材料合同 | T22-T26, T44-T46, T49A | L1/L2/receipt/capability/manifest 可验证；receipt 不等于批准 |
| 9. claw-trade 调 OpenClaw | T37, T37A, T38, T39 | 适配层只翻译和读证据 |
| 10. hard gate 设计 | T25A, T40-T49, T47A, T48 | 所有 hard gate 有输入、输出、失败停机语义 |
| 11. 运行循环 | T50, T50A, T51, T51A, T52-T55, T53A-T53D | 状态保存顺序、审计持久化和 collect-first 语义一致 |
| 12. 报告导出 | T56, T56A, T56B, T47, T47A, T48, T63, T63A | exporter 只整理 approved materials，live final report truth gate 使用真实 run 证据 |
| 13. CLI 和证据目录 | T50, T50A, T57, T59-T63A | CLI 参数、run dir、call dir、guard/audit/index 结构符合设计 |
| 14. 真实链路里程碑 | T58-T63A | 顺序执行，不补假成功 |
| 15. 代码评审清单 | T64-T66 | 越权、假成功、unsupported claim 扫描完成 |
| 16. 停止条件 | 所有任务 | 命中停止条件时记录 BLOCKED 并问人类 |
| 17. 完成判定 | T67 | 只基于真实证据收口 |

### 1.1 并行实施批次

并行只按“写集隔离”和“接口已稳定”来判断。同一批次可以并行派给不同 owner；同一核心文件上的任务即使在同一批，也建议由一个 owner 串行完成。

| 批次 | 可并行任务 | 串行前置 | 并行边界 |
| --- | --- | --- | --- |
| B0 | T01 | 无 | 只追加 memory 记录模板，不碰源码主线。 |
| B1 | T00 | 无 | 必须先重建 `./src/`，其他源码任务不要抢跑。 |
| B2 | T03 | T00 | 先建模块目录，后续任务才能分文件落地。 |
| B3 | T04-T07、T08、T09 | T03 | T04-T07 都改 workflow 核心对象，建议一个 owner 串行；T08、T09 可并行。 |
| B4 | T17-T21、T22-T24、T25A、T27 | T04-T09 | config、OpenViking 基础合同、结构化 claim 合同、OpenClaw 入口定位可以并行。 |
| B5 | T10-T16 | T04-T08 | controller 强边界任务由一个 owner 串行完成。 |
| B6 | T28、T29、T29A、T30-T34A、T35 | T27 | OpenClaw 接缝可分 owner，但 T27 的入口定位结果是强输入。 |
| B7 | T37、T37A、T38、T39、T50、T50A | B3-B6 的接口稳定 | request_builder、client、evidence_reader、store 写集不同，可以并行。 |
| B8 | T40-T48、T47A | T25A、T29A、T34A、T39 | 每个 guard 一个 owner；claim/PM/export 只能解析结构化块，不能猜正文。 |
| B9 | T49、T49A、T26 | T40-T48 | guard 组合、approval 骨架和最终接线收口；最终写 manifest 必须放在 hard gate 后。 |
| B10 | T51、T51A、T52-T55、T53A-T53D | T37-T50A、T49A | runner 同文件强耦合，建议一个 owner 串行或小步交付。 |
| B11 | T56、T56A、T56B、T57 | T51A、T47A、T48 | exporter 和 CLI 可并行，CLI 最终要等 runner 接口稳定。 |
| B12 | T58-T63A | 实现完成 | 里程碑按顺序执行，不能用并行替代证据顺序。 |
| B13 | T64、T65 | T62、T63A | 扫描和架构评审可并行。 |
| B14 | T66、T67 | T64、T65 | 覆盖复核和完成判定必须最后串行。 |

并行停止条件：

- 两个任务需要改同一核心文件且接口未定。
- OpenClaw 入口定位结果未写入 T27 记录，却开始改 T28-T35。
- OpenViking read/write 合同未收紧，却开始接下游读取。
- 需要 mock/stub/fake/fallback 成功路径才能让某批任务看起来完成。
- Python 开始解释业务正文、改写 PM 结论或替 worker 调工具。

## 2. Phase A：在已有工程目录下重建 `./src/`

### T00  重建已有工程目录下的 `./src/`

设计引用：§3.8、§14.1。

目标：在已经存在的工程目录 `/home/frank/src/claw-trade` 下面重建原来的 `./src/` 子目录。`./src/` 下的结构参考 `./src-bak/`；当前参考结构里有 `./src-bak/claw_trade/`，所以目标代码包是 `./src/claw_trade/`。后续所有实现都写到 `/home/frank/src/claw-trade/src/claw_trade/`。不能创建新的工程目录，不能创建嵌套项目目录，不能移动当前工作区，不能把 `./src-bak/` 作为实现目标、导入路径、成功路径或整目录复制来源。

文件范围：

- `pyproject.toml`
- `src/`
- `src/claw_trade/`
- `src-bak/` 只读结构参考
- `tests/conftest.py`

函数级动作：

```python
def create_clean_source_tree(workspace_root: Path) -> SourceRootStatus:
    assert workspace_root == Path("/home/frank/src/claw-trade")
    reference_root = workspace_root / "src-bak"
    mkdir(workspace_root / "src")
    for package_dir in required_top_level_packages(reference_root):
        mkdir(workspace_root / "src" / package_dir.name)
        write_text(workspace_root / "src" / package_dir.name / "__init__.py", "")
    configure_pyproject_for_src_layout(workspace_root / "pyproject.toml")
    return SourceRootStatus(ok=True, root="src/claw_trade")


def require_clean_import_path() -> SourceRootStatus:
    imported = import_module_path("claw_trade")
    if not imported.is_relative_to(Path("/home/frank/src/claw-trade/src/claw_trade")):
        return SourceRootStatus(ok=False, reason="claw_trade 必须从全新 src/claw_trade 导入")
    return SourceRootStatus(ok=True, root="src/claw_trade")
```

验收证据：

```bash
test -d src
test -d src/claw_trade
rg --files src/claw_trade
uv run python -c "import claw_trade; print(claw_trade.__file__)"
```

偏离检查：

- `claw_trade.__file__` 必须位于 `src/claw_trade`。
- `./src/claw_trade` 必须对应参考结构里的 `./src-bak/claw_trade`。
- `./src-bak/` 只能只读查看，不能作为 import path、实现目标或整目录复制来源。
- 如果导入不是来自 `/home/frank/src/claw-trade/src/claw_trade`，状态写 `BLOCKED`，先修 `./src/` 子目录和导入配置。

### T01  建立任务执行记录模板

设计引用：§15、§17。

目标：每个任务完成后能把真实证据、偏离状态和禁止路径扫描结果追加到已有 `memory/` 目录里的日期文件。

文件范围：

- 已存在的 `memory/YYYY-MM-DD.md`
- 后续任务自己的实施记录。

函数级动作：

```text
append_task_record(task_id, status, changed_files, commands, evidence_paths, deviation_status)
```

验收证据：

- 已存在的 `memory/YYYY-MM-DD.md` 有本轮追加记录。
- 记录使用 `达到预期 / 未达到预期 / 无法判断 / BLOCKED`。
- 不能创建新的工程目录、工作区目录或第二套 `memory/`。

### T02  建立干净代码基线

设计引用：§2、§14.1。

目标：在 `/home/frank/src/claw-trade/src/claw_trade` 创建后，确认空包、测试入口、OpenClaw 候选入口和 12 个 worker workspace 可被后续任务使用。这里不是继承旧实现，只建立从零开始的代码基线。

文件范围：

- `src/claw_trade/__init__.py`
- `tests/`
- `agents/`
- `third_party/openclaw/`

命令：

```bash
uv run pytest tests/unit tests/contracts -q
rg -n "openclaw-runtime|run_worker|provider-request|visible-tools|workspace-evidence" third_party/openclaw src/claw_trade
rg --files agents
```

验收证据：

- 全新 `src/claw_trade` 可以被导入。
- OpenClaw 单 worker 入口或候选入口。
- 12 个 worker workspace 是否齐。
- 缺代码、缺配置、缺真实依赖、缺验收命令的清单。

偏离检查：

- 命令失败不能补假成功。
- 真实依赖缺失写 `BLOCKED`。
- 如果需要参考旧代码，只允许另行只读查看，不能把它加入实现路径或导入路径。

## 3. Phase B：模块骨架和核心对象

### T03  在 `src/claw_trade` 下重建模块目录架构

设计引用：§3.1、§3.8。

目标：在 `/home/frank/src/claw-trade/src/claw_trade/` 下重建设计允许的模块目录架构。目录名和包层级参考 `./src-bak/claw_trade/`，实现内容按详细设计从零写，不放业务正文，不跨边界提前实现。

文件范围：

```text
src/claw_trade/workflow/
src/claw_trade/config/
src/claw_trade/runtime/
src/claw_trade/artifacts/
src/claw_trade/guards/
src/claw_trade/reports/
src/claw_trade/cli/
```

函数级动作：

```text
ensure_module(path)
ensure_init(path)
```

验收证据：

```bash
rg --files src/claw_trade
uv run python -c "import claw_trade"
```

偏离检查：

- `workflow.controller` 不能导入 runtime/OpenViking/reports。
- `third_party/openclaw` 不能导入 `claw_trade.workflow.controller`。

### T04  实现流程基础枚举和 RunRequest/WorkflowState

设计引用：§4.1、§4.2。

文件范围：

- `src/claw_trade/workflow/models.py`

新增/修改对象：

```python
class StopPoint(str, Enum): ...
class RunStatus(str, Enum): ...
class Stage(str, Enum): ...
@dataclass(frozen=True)
class RunRequest: ...
@dataclass(frozen=True)
class WorkflowState: ...
```

函数级动作：

```python
def normalize_stop_point(value: str | None) -> StopPoint: ...
def is_terminal_status(status: RunStatus) -> bool: ...
```

验收证据：

- 字段包含 `target_worker_id` 和 `target_stage`。
- HK/CRYPTO 未批准不能在这里 fallback。
- `WorkflowState` 不保存正式材料正文。

### T05  实现固定 12 worker 表和 StagePlan

设计引用：§4.3、§5.1。

文件范围：

- `src/claw_trade/workflow/workers.py`
- `src/claw_trade/workflow/models.py`

新增/修改对象：

```python
@dataclass(frozen=True)
class WorkerSpec:
    id: str
    stage: Stage

@dataclass(frozen=True)
class StagePlan: ...
STAGE_PLANS: tuple[StagePlan, ...]
```

函数级动作：

```python
def all_worker_ids() -> tuple[str, ...]: ...
def worker_by_id_or_none(worker_id: str) -> WorkerSpec | None: ...
def worker_by_id(worker_id: str) -> WorkerSpec: ...
def stage_plan(stage: Stage) -> StagePlan: ...
def stage_for_running_status(status: RunStatus) -> Stage: ...
def stage_for_ready_status(status: RunStatus) -> Stage: ...
def is_running_status(status: RunStatus) -> bool: ...
def is_ready_status(status: RunStatus) -> bool: ...
```

验收证据：

- 12 个 worker 固定，顺序与设计一致。
- LLM 没有任何决定下一步 worker 的入口。

### T06  实现 Decision、StageBatch、WorkerResult、FailureRecord、ExportResult

设计引用：§4.3、§4.7、§5.2。

文件范围：

- `src/claw_trade/workflow/models.py`

新增/修改对象：

```python
class BatchScope(str, Enum): ...
class DecisionKind(str, Enum): ...
@dataclass(frozen=True)
class StageBatch: ...
@dataclass(frozen=True)
class Decision: ...
class WorkerStatus(str, Enum): ...
@dataclass(frozen=True)
class FailureRecord: ...
@dataclass(frozen=True)
class WorkerResult: ...
@dataclass(frozen=True)
class StageBatchResult: ...
@dataclass(frozen=True)
class ExportResult: ...
```

函数级动作：

```python
def export_passed(state: WorkflowState, final_report_path: Path, guard_path: Path) -> ExportResult: ...
def export_failed(state: WorkflowState, category: str, reason: str, paths: tuple[Path, ...]) -> ExportResult: ...
def failed_worker_result(call: WorkerCall, category: str, reason: str, paths: tuple[Path, ...]) -> WorkerResult: ...
def blocked_worker_result(run_id: str, worker_id: str, stage: Stage, failure: FailureRecord) -> WorkerResult: ...
```

验收证据：

- `StageBatch` 不包含 `WorkerCall`。
- `ExportResult.status="passed"` 是进入 `COMPLETED` 的必要条件。

### T07  实现 WorkerCall 和 OpenClawCommand 数据对象

设计引用：§4.4、§4.5。

文件范围：

- `src/claw_trade/workflow/models.py`
- `src/claw_trade/runtime/openclaw_client.py`

新增/修改对象：

```python
@dataclass(frozen=True)
class ReadPolicy: ...
@dataclass(frozen=True)
class WorkerCall: ...
@dataclass(frozen=True)
class OpenClawCommand: ...
```

函数级动作：

```python
def build_openclaw_command(call: WorkerCall) -> OpenClawCommand: ...
def serialize_read_policy(policy: ReadPolicy) -> dict[str, object]: ...
```

验收证据：

- `OpenClawCommand.agent == OpenClawCommand.worker_id`。
- command 只翻译 `WorkerCall`，不新增业务正文。

### T08  实现 OpenViking 材料和 manifest 数据对象

设计引用：§4.8、§4.9、§4.10、§8。

文件范围：

- `src/claw_trade/artifacts/refs.py`
- `src/claw_trade/artifacts/manifest.py`

新增/修改对象：

```python
VikingUri = str
@dataclass(frozen=True)
class MaterialTarget: ...
@dataclass(frozen=True)
class MaterialReadRef: ...
@dataclass(frozen=True)
class OpenVikingReadCapability: ...
@dataclass(frozen=True)
class L2Entry: ...
@dataclass(frozen=True)
class L2Index: ...
@dataclass(frozen=True)
class L1Claim: ...
@dataclass(frozen=True)
class MaterialReceipt: ...
@dataclass(frozen=True)
class ApprovedMaterial: ...
class ApprovedManifest: ...
```

函数级动作：

```python
def make_material_target(run_id: str, stage: Stage, worker_id: str, call_id: str, target_name: str) -> MaterialTarget: ...
def make_material_id(call: WorkerCall, receipt: MaterialReceipt) -> str: ...
def manifest_entry_sha256(material: ApprovedMaterial) -> str: ...
```

验收证据：

- URI 形状为 `viking://resources/workflow/<run>/<stage>/<worker>/<call>/...`。
- capability 绑定 material id、L1 URI、L1 SHA、L2 prefix、manifest entry SHA。
- `MaterialReceipt/OpenVikingReadCapability/ApprovedMaterial/L2Index` 中所有 SHA/size 字段都标注并执行同一口径：OpenViking `content/download` 原始字节。

### T09  实现证据和通用结果对象

设计引用：§4.6、§4.11、§7。

文件范围：

- `src/claw_trade/runtime/evidence_reader.py`
- `src/claw_trade/guards/common.py`
- `src/claw_trade/artifacts/openviking_client.py`

新增/修改对象：

```python
@dataclass(frozen=True)
class OpenClawResult: ...
@dataclass(frozen=True)
class ProviderEvidence: ...
@dataclass(frozen=True)
class GuardResult: ...
@dataclass(frozen=True)
class ApprovalResult: ...
@dataclass(frozen=True)
class BootResult: ...
@dataclass(frozen=True)
class OpenVikingStat: ...
@dataclass(frozen=True)
class OpenVikingReadResult: ...
@dataclass(frozen=True)
class OpenVikingAccessRecord: ...
```

函数级动作：

```python
def guard_passed(category: str = "ok") -> GuardResult: ...
def guard_failed(category: str, reason: str, paths: tuple[Path, ...], early_stop: bool = False) -> GuardResult: ...
def combine_guard_results(results: tuple[GuardResult, ...]) -> GuardResult: ...
```

验收证据：

- OpenViking stat/read error category 只允许设计列出的 8 类。
- `ProviderEvidence` 不携带 worker 业务正文。

## 4. Phase C：纯 controller 和固定流程

### T10  实现 controller 输入和主决策函数

设计引用：§5.3、§5.4。

文件范围：

- `src/claw_trade/workflow/controller.py`

新增/修改对象：

```python
@dataclass(frozen=True)
class ControllerInput: ...
```

函数级动作：

```python
def decide_next(input: ControllerInput) -> Decision:
    if terminal: return WAIT
    if CREATED: return wake single or frontline
    if running: return decide_running_stage(...)
    if ready: return decide_ready_stage(...)
    if REPORT_EXPORTING: return decide_report_exporting(...)
    return FAIL workflow_state
```

验收证据：

- `controller.py` 不导入 `runtime`、OpenClaw client、OpenViking client、report exporter。
- `Decision.batch` 只含 worker id，不含 `WorkerCall`。

### T11  实现阶段叫醒决策

设计引用：§5.5。

文件范围：

- `src/claw_trade/workflow/controller.py`

函数级动作：

```python
def require_upstream_ready(plan: StagePlan, manifest: ApprovedManifest) -> GuardResult: ...
def decide_wake_stage(state: WorkflowState, stage: Stage, manifest: ApprovedManifest) -> Decision: ...
```

验收证据：

- 上游材料缺失返回 `BLOCKED`。
- `FULL_STAGE.worker_ids == StagePlan.workers`。
- 不读取 workspace/stage policy/tool registry。

### T12  实现单 worker 目标解析和派发

设计引用：§4.1、§5.5、§14.2、§14.3。

文件范围：

- `src/claw_trade/workflow/controller.py`

新增/修改对象：

```python
@dataclass(frozen=True)
class TargetWorkerResult: ...
```

函数级动作：

```python
def resolve_single_worker_target(state: WorkflowState) -> TargetWorkerResult: ...
def decide_wake_single_worker(state: WorkflowState, manifest: ApprovedManifest) -> Decision: ...
```

验收证据：

- `FIRST_RESPONSE` 和 `SINGLE_WORKER_COMPLETE` 缺 `target_worker_id` 时 `BLOCKED`。
- `target_stage` 与 worker 所属阶段不一致时 `BLOCKED`。
- 单 worker 只叫醒一个 worker，不启动完整前线阶段。

### T13  实现 running 阶段推进

设计引用：§5.6。

文件范围：

- `src/claw_trade/workflow/controller.py`

函数级动作：

```python
def decide_running_stage(state, stage, results, manifest) -> Decision: ...
def terminal_failures(results) -> tuple[FailureRecord, ...]: ...
def all_workers_have_result(results, workers) -> bool: ...
def all_workers_approved(workers, stage, manifest) -> bool: ...
def first_response_ready(results, workers) -> bool: ...
def expected_workers_for_state(state, plan) -> tuple[str, ...]: ...
```

验收证据：

- first response stop point 只等目标 worker first response。
- single worker complete 只等目标 worker result 和 approved material。
- 完整阶段必须等所有 required worker approved。

### T14  实现 ready 阶段和 REPORT_EXPORTING 决策

设计引用：§5.7。

文件范围：

- `src/claw_trade/workflow/controller.py`

函数级动作：

```python
def decide_ready_stage(state: WorkflowState, stage: Stage, manifest: ApprovedManifest) -> Decision: ...
def decide_report_exporting(state: WorkflowState, export_result: ExportResult | None) -> Decision: ...
```

验收证据：

- `PORTFOLIO_DECISION_READY` 只返回 `EXPORT_REPORT`。
- `REPORT_EXPORTING` 没有 `ExportResult` 时 `WAIT`。
- 只有 `ExportResult.status == "passed"` 才能 `COMPLETE`。

### T15  实现阶段失败归因

设计引用：§11.3。

文件范围：

- `src/claw_trade/workflow/runner.py`
- `src/claw_trade/workflow/controller.py`

函数级动作：

```python
def group_failures_by_category(failures: tuple[FailureRecord, ...]) -> dict[str, tuple[FailureRecord, ...]]: ...
def merge_stage_failures(run_id: str, stage: Stage, failures: tuple[FailureRecord, ...]) -> FailureRecord: ...
def first_human_action(failures: tuple[FailureRecord, ...]) -> str | None: ...
```

验收证据：

- collect-first 可一次归因多个同阶段失败。
- 早停失败必须写 `early_stop=True`。

### T16  controller 边界扫描

设计引用：§3.3、§5.2、§5.5。

文件范围：只读。

命令：

```bash
rg -n "OpenClaw|openclaw|openviking|evidence_reader|request_builder|exporter|WorkerCall" src/claw_trade/workflow/controller.py
```

验收证据：

- 只允许设计中描述对象名出现；不能出现 runtime/OpenViking/report 调用。

## 5. Phase D：config、workspace 和 stage policy

### T17  实现 profile 批准策略

设计引用：§4.1、§6、§11.2、§16。

文件范围：

- `src/claw_trade/config/profiles.py`

函数级动作：

```python
def require_profile(profile: str) -> ProfileResult: ...
def is_profile_approved(profile: str) -> bool: ...
```

验收证据：

- US/CN_A 按批准策略返回。
- HK/CRYPTO 未批准时失败，不 fallback。

### T18  实现 worker workspace 校验

设计引用：§6、§7。

文件范围：

- `src/claw_trade/config/workspace.py`

函数级动作：

```python
def validate_worker_workspace_for_control(agents_root: Path, worker_id: str) -> WorkspaceResult: ...
def worker_workspace_sources(agents_root: Path, worker_id: str) -> WorkspaceSources: ...
```

验收证据：

- 每个 worker 必须有 `AGENTS.md`、`IDENTITY.md`、`STAGES.yaml`、`SKILLS.md`、`skills/manifest.yaml`。
- Python 不读取和改写 worker 文案内容，只校验人工资产存在和可被 OpenClaw 加载。

### T19  实现 stage policy 读取

设计引用：§6、§9。

文件范围：

- `src/claw_trade/config/stage_policy.py`

函数级动作：

```python
def load_stage_policy(agents_root: Path, worker_id: str, profile: str) -> StagePolicyResult: ...
def validate_stage_policy_matches_worker(policy: StagePolicy, worker: WorkerSpec) -> GuardResult: ...
```

验收证据：

- stage policy 的 stage 必须匹配固定 worker 表。
- OpenViking read/write 工具按 stage/profile 收窄。

### T20  实现工具注册和工具名规范

设计引用：§6、§7、§10。

文件范围：

- `src/claw_trade/config/tool_names.py`

函数级动作：

```python
def load_tool_registry() -> ToolRegistryResult: ...
def resolve_tools(policy: StagePolicy, registry: ToolRegistry) -> tuple[str, ...]: ...
def require_global_news_capability_for_news(registry: ToolRegistry) -> GuardResult: ...
```

验收证据：

- `news_analyst` 必须明确有公司新闻和全球/宏观新闻能力，否则 `BLOCKED`。
- 工具集合为空时不能调用 OpenClaw。

### T21  实现 OpenViking 配置探测

设计引用：§8、§11.2、§16。

文件范围：

- `src/claw_trade/config/openviking_config.py`

函数级动作：

```python
def load_openviking_config() -> OpenVikingConfigResult: ...
def probe_openviking_contract(client: OpenVikingClient) -> BootResult: ...
```

验收证据：

- read/stat/receipt 任一不可用时 `BLOCKED`。
- 不能退回本地文件成功路径。

## 6. Phase E：OpenViking 材料合同和批准

### T22  实现 URI 和 material target 构造

设计引用：§4.8、§8。

文件范围：

- `src/claw_trade/artifacts/refs.py`

函数级动作：

```python
def make_material_target(run_id, stage, worker_id, call_id, target_name) -> MaterialTarget: ...
def validate_viking_uri_shape(uri: VikingUri, run_id: str, stage: Stage, worker_id: str, call_id: str) -> GuardResult: ...
```

验收证据：

- L1 和 L2 prefix 都带 run/stage/worker/call。
- 不允许 latest/list/目录扫描生成正式路径。

### T23  实现 OpenViking client 合同

设计引用：§4.9、§8、§11.6。

文件范围：

- `src/claw_trade/artifacts/openviking_client.py`

函数级动作：

```python
class OpenVikingClient:
    def ensure_namespace(namespace: str) -> None: ...
    def probe_read_stat_receipt() -> ProbeResult: ...
    def read_receipt(path: Path) -> MaterialReceipt: ...
    def stat_for_receipt_verification(receipt: MaterialReceipt, target: MaterialTarget) -> OpenVikingStat: ...
    def read_for_receipt_verification(receipt: MaterialReceipt, target: MaterialTarget) -> OpenVikingReadResult: ...
    def read_l2_index_for_receipt_verification(receipt: MaterialReceipt, target: MaterialTarget) -> L2Index: ...
    def stat_with_capability(capability: OpenVikingReadCapability, uri: VikingUri) -> OpenVikingStat: ...
    def read_with_capability(capability: OpenVikingReadCapability, uri: VikingUri, expected_sha256: str) -> OpenVikingReadResult: ...
    def read_l2_index_with_capability(capability: OpenVikingReadCapability) -> L2Index: ...
```

验收证据：

- 批准复核只能用 `read_for_receipt_verification(receipt, target)` 和 `stat_for_receipt_verification(receipt, target)`。
- 下游读取只能用 `read_with_capability(capability, uri)` 和 `stat_with_capability(capability, uri)`。
- worker 侧输入应优先是 material/layer 选择；`capability_id/uri` 由 runtime manifest 映射后再进入 client 校验。
- `stat/read` 结果校验 SHA 和 size，正式口径固定为 OpenViking `content/download` 原始字节。
- `content/read` 仅用于 worker 文本读取/展示，不作为 receipt、manifest、capability、PM/export truth gate 的正式 hash 依据。
- 错误分类符合 `not_found/permission_denied/capability_mismatch/hash_mismatch/size_mismatch/backend_unavailable/invalid_uri/unknown`。
- 不提供 compact/latest/list/目录扫描/裸 URI read 成功路径。
- 禁止新增 `read(uri)`、`read_text(uri)`、`read_latest()`、`latest_material()`、`compact_read()` 这类泛化成功接口。

### T24  实现 ApprovedManifest 存储和 capability

设计引用：§4.10、§8、§11.1。

文件范围：

- `src/claw_trade/artifacts/manifest.py`

函数级动作：

```python
class ApprovedManifest:
    def add(material: ApprovedMaterial) -> None: ...
    def required_workers_for(stage: Stage) -> tuple[str, ...]: ...
    def has_worker(worker_id: str, stage: Stage) -> bool: ...
    def for_downstream_stage(stage: Stage) -> tuple[MaterialReadRef, ...]: ...
    def capabilities_for_downstream_stage(stage: Stage) -> tuple[OpenVikingReadCapability, ...]: ...
    def all_for_run(run_id: str) -> tuple[ApprovedMaterial, ...]: ...

class ManifestStore:
    def load(run_id: str) -> ApprovedManifest: ...
    def add(run_id: str, material: ApprovedMaterial) -> None: ...
```

验收证据：

- hard gate 未通过材料不能进入 manifest。
- 下游只能拿 manifest-scoped capability。

### T25  实现 L1/L2 index 和材料完整性校验

设计引用：§4.8、§8、§10、§11.6。

文件范围：

- `src/claw_trade/artifacts/claims.py`
- `src/claw_trade/guards/l1_l2.py`

函数级动作：

```python
def validate_l2_entries(client: OpenVikingClient, l2_index: L2Index, allowed_prefix: VikingUri) -> GuardResult: ...
def validate_l1_l2_contract(call: WorkerCall, l1_text: str, raw_output: str, l2_index: L2Index) -> tuple[tuple[L1Claim, ...], GuardResult]: ...
```

验收证据：

- L2 为空必须有 empty reason。
- L2 entry 必须在当前 call 的 L2 prefix 内。
- L1 内容指纹和 receipt/stat/read 结果一致。
- 不能把缺失图表、新闻、来源写成已有。

### T25A  实现结构化 claim 合同

设计引用：§4.8、§10、§12。

目标：定义并实现 machine-generated claim ledger 合同。L1 只保留读者分析正文；控制层从写工具审计记录和 approval evidence 生成并校验结构化 claim ledger，不从自然语言正文里猜业务声明。

文件范围：

- `src/claw_trade/artifacts/claims.py`
- `src/claw_trade/guards/l1_l2.py`

固定格式（evidence sidecar）：

```json
{
  "schema_version": "control.claims.v1",
  "run_id": "run-...",
  "call_id": "call-...",
  "worker_id": "market_analyst",
  "stage": "frontline",
  "material_id": "mat-...",
  "claims": [
    {
      "claim_id": "claim-...",
      "kind": "news|valuation|rating|trade_action|risk_condition|sentiment|chart|tool_success|source|other",
      "text": "结构化声明文本",
      "value": null,
      "evidence_ids": ["l2-..."],
      "source_worker_id": "market_analyst"
    }
  ]
}
```

函数级动作：

```python
def build_claim_ledger_from_evidence(call: WorkerCall, evidence: ProviderEvidence, l1_text: str) -> ClaimLedgerResult: ...
def require_claim_ledger(ledger: ClaimLedger, call: WorkerCall) -> tuple[tuple[L1Claim, ...], GuardResult]: ...
def validate_claim_ledger_identity(ledger: ClaimLedger, call: WorkerCall) -> GuardResult: ...
def validate_claims(claims: tuple[L1Claim, ...], l2_index: L2Index) -> GuardResult: ...
```

验收证据：

- claim ledger 缺失、JSON 非法、身份字段不匹配时 hard fail。
- 高风险声明必须映射到 L2 evidence。
- Python 只解析工具/控制层生成的固定结构，不从正文推断投资结论、新闻、估值、情绪或图表声明。
- export 和 PM owner 后续任务只能使用 `L1Claim`，不能另写自然语言启发式 claim 抽取。

### T26  实现材料批准数据流骨架

设计引用：§4.9、§4.10、§10、§11.6。

文件范围：

- `src/claw_trade/artifacts/approval.py`

函数级动作：

```python
@dataclass(frozen=True)
class ApprovalCandidate:
    call: WorkerCall
    receipt: MaterialReceipt
    l1_text: str
    l2_index: L2Index
    raw_output_path: Path

def prepare_approval_candidate(call: WorkerCall, evidence: ProviderEvidence, openviking: OpenVikingClient) -> ApprovalCandidateResult:
    receipt = openviking.read_receipt(evidence.openviking_receipt_path)
    l1_read = openviking.read_for_receipt_verification(receipt, call.material_target)
    l2_index = openviking.read_l2_index_for_receipt_verification(receipt, call.material_target)
    return ApprovalCandidateResult(candidate=ApprovalCandidate(...))

def build_approved_material_from_passed_gates(call: WorkerCall, candidate: ApprovalCandidate, claims: tuple[L1Claim, ...]) -> ApprovedMaterial: ...
```

验收证据：

- receipt 不是批准。
- raw output 不是正式材料权威。
- 本任务只准备 approval 输入和 `ApprovedMaterial` 构造函数，不把材料写入 manifest。
- `ApprovalResult.ok_result(...)` 的最终成功接线必须等 T44-T48 和 T49A 完成后再做。

## 7. Phase F：OpenClaw 单 worker runtime 接缝

### T27  定位 OpenClaw 单 worker 入口

设计引用：§3.8、§7、§14.1。

文件范围：只读，优先候选：

```text
third_party/openclaw/openclaw.mjs
third_party/openclaw/packages/memory-host-sdk/src/host/openclaw-runtime-cli.ts
third_party/openclaw/packages/memory-host-sdk/src/host/openclaw-runtime-agent.ts
third_party/openclaw/packages/memory-host-sdk/src/host/openclaw-runtime.ts
third_party/openclaw/packages/memory-host-sdk/src/host/openclaw-runtime-session.ts
third_party/openclaw/packages/memory-host-sdk/src/host/openclaw-runtime-io.ts
third_party/openclaw/src/agents/pi-embedded-runner.ts
third_party/openclaw/src/agents/provider-request-config.ts
third_party/openclaw/src/agents/provider-transport-stream.ts
third_party/openclaw/src/agents/tool-allowlist-guard.ts
```

命令：

```bash
rg -n "provider request|tools|toolCall|runSingle|agent|openclaw-runtime" third_party/openclaw
```

验收证据：

- 记录真实入口文件和函数，并写入本任务执行记录，作为 T28-T35 和 T34A 的强输入。
- T28-T35/T34A 只能修改 T27 记录确认过的入口附近文件；如果入口仍无法确认，后续 OpenClaw 写入任务全部 `BLOCKED`。
- 如果入口不存在，状态写 `BLOCKED`，不能把 12 worker 逻辑写入 OpenClaw。

### T28  增加 SingleWorkerCommand 协议

设计引用：§7.1。

文件范围：

- OpenClaw 单 worker 入口附近的类型文件或 runtime CLI 文件。

函数级动作：

```ts
type SingleWorkerCommand = {
  agent: string
  run_id: string
  call_id: string
  worker_id: string
  stage: string
  profile: string
  runtime_vars: Record<string, string>
  allowed_tools: string[]
  evidence_dir: string
  upstream_materials: MaterialReadRef[]
  openviking_read_capabilities: OpenVikingReadCapability[]
  material_target: MaterialTarget
  read_policy: ReadPolicy
  stop_after_first_response: boolean
}
```

验收证据：

- JSON 字段和 Python `OpenClawCommand` 一一对应。
- `agent` 和 `worker_id` 在 control 路径必须相等。

### T29  增加 runtime context 和 evidence dir

设计引用：§7。

文件范围：

- OpenClaw runtime context / session / IO 文件。

函数级动作：

```ts
function createRuntimeContext(command: SingleWorkerCommand): RuntimeContext
function ensureEvidenceDir(command: SingleWorkerCommand): void
function withRuntimeMarkers(payload, context): ProviderRequest
```

验收证据：

- 所有证据文件都有 run/call/worker/stage/profile/openclaw_run_id。
- 证据写入 `command.evidence_dir`。

### T29A  OpenClaw workspace evidence capture

设计引用：§6、§7、§10、§14.2。

目标：OpenClaw 在真实加载 worker workspace 后写出 `workspace-evidence.json`。这个文件证明本次模型回合实际加载了哪个 worker 的身份、技能、阶段策略和人工维护文本资产指纹。

文件范围：

- OpenClaw workspace loader / runtime context / session 入口。
- T27 定位出的真实入口文件；不能凭候选路径硬写。

函数级动作：

```ts
type WorkspaceEvidence = {
  source: "openclaw_workspace_loader"
  run_id: string
  call_id: string
  worker_id: string
  stage: string
  profile: string
  openclaw_run_id: string
  workspace_root: string
  identity: { path: string; sha256: string }
  skills_manifest: { path: string; sha256: string }
  stage_policy: { path: string; sha256: string; selected_stage: string }
  text_assets: { path: string; sha256: string }[]
  loaded_at: string
}

async function writeWorkspaceEvidence(context: RuntimeContext, loadedWorkspace: LoadedWorkspace): Promise<string>
```

中文注释：

```text
workspace evidence 只能由 OpenClaw 加载 workspace 后写出，防止 Python 用配置扫描结果冒充真实 worker 加载证据。
```

验收证据：

- `workspace-evidence.json.source == "openclaw_workspace_loader"`。
- 文件含 run/call/worker/stage/profile/openclaw_run_id。
- 文件含 identity、skills manifest、stage policy、text asset SHA。
- first response 里程碑不需要 receipt，但必须有 workspace evidence。
- 不能保存 worker 文案正文；只保存路径、SHA 和加载事实。

### T30  实现 per-turn tool narrowing

设计引用：§7、§10。

文件范围：

- OpenClaw tool registry / allowlist / provider request 构造入口。

函数级动作：

```ts
function buildVisibleTools(command: SingleWorkerCommand, recorder: ToolCallRecorder): ToolDefinition[] {
  const tools = loadWorkerTools(command.allowed_tools)
  return tools.map((tool) => wrapToolWithAudit(tool, command, recorder))
}
```

中文注释：

```text
模型实际可见工具必须来自本回合策略，防止 worker 越权调用工具。
```

验收证据：

- provider request 里的 tools 等于 `allowed_tools` 收窄后的真实工具。
- 不能从 Python allowlist 反推 visible tools 文件。

### T31  捕获真实 provider request

设计引用：§7、§10、§17。

文件范围：

- OpenClaw provider request 构造/发送前入口。

函数级动作：

```ts
function buildProviderRequest(command: SingleWorkerCommand, tools: ToolDefinition[]): ProviderRequest
async function writeProviderRequest(evidenceDir: string, providerRequest: ProviderRequest): Promise<void>
```

中文注释：

```text
这是发给 provider 前的真实请求，不能由上层重构。
```

验收证据：

- `provider-request.json` 包含 `source=provider_request_capture`、runtime marker、payload.messages、payload.tools。
- 不能用日志或 renderer output 替代。

### T32  捕获 visible tools

设计引用：§7、§10。

文件范围：

- OpenClaw provider request capture 相邻位置。

函数级动作：

```ts
async function writeVisibleToolsFromProviderRequest(evidenceDir: string, providerRequestPath: string): Promise<void>
```

中文注释：

```text
工具清单必须和 provider request 同源，不能复制输入 allowlist。
```

验收证据：

- `visible-tools.json.source == "provider_request"`。
- tools 来自同一份 provider request。

### T33  捕获 first response

设计引用：§7、§14.2。

文件范围：

- OpenClaw streaming/response handling 入口。

函数级动作：

```ts
function captureFirstModelEvent(event: ModelEvent, context: RuntimeContext): FirstResponseEvidence
```

验收证据：

- first response 可以是第一条 assistant text 或第一条 tool call。
- `stop_after_first_response=true` 时仍返回机器可读 `OpenClawResult`。

### T34  实现 tool call recorder

设计引用：§7.1、§10。

文件范围：

- OpenClaw 统一工具执行入口。

函数级动作：

```ts
function createToolCallRecorder(command: SingleWorkerCommand): ToolCallRecorder
function wrapToolWithAudit(tool: ToolDefinition, command: SingleWorkerCommand, recorder: ToolCallRecorder): ToolDefinition
function successRecord(command, tool, toolCallId, args, result, startedAt): ToolCallRecord
function failureRecord(command, tool, toolCallId, args, error, startedAt): ToolCallRecord
```

验收证据：

- `tool-calls.json.source == "model_tool_events"`。
- 没有工具调用时写 `status="none"`。
- OpenViking write 记录带 uri/result_sha256/status；read 成功记录带 capability/material/uri/result_sha256/status，read 失败记录至少带 status/error 和原始输入审计（不得伪造 capability）。

### T34A  OpenClaw OpenViking tool adapter

设计引用：§4.4、§4.8、§7、§8、§10、§14.3。

目标：把 OpenViking 写材料和按 capability 读取材料作为真实 OpenClaw worker 工具接入。Python 只能把 `material_target`、`upstream_materials` 和 `openviking_read_capabilities` 放进 command，不能替 worker 调工具。

文件范围：

- OpenClaw tool registry / tool adapter / tool execution 入口。
- T27 定位出的真实入口文件；不能把 claw-trade 的 12 worker DAG 写进 OpenClaw。

函数级动作：

```ts
function registerOpenVikingTools(context: RuntimeContext): ToolDefinition[]
function renderOpenVikingMaterialBrief(context: RuntimeContext): string

async function writeMaterial(args: WriteMaterialArgs, context: RuntimeContext): Promise<WriteMaterialResult> {
  assertTargetMatchesCommand(args.target, context.command.material_target)
  const receipt = await openviking.writeMaterial(args)
  await recorder.recordOpenVikingWrite(args, receipt)
  return { receipt_path: receipt.local_path, uri: receipt.uri, sha256: receipt.sha256 }
}

async function readWithCapability(args: ReadWithCapabilityArgs, context: RuntimeContext): Promise<ReadMaterialResult> {
  const capability = requireCapability(
    context.command.openviking_read_capabilities,
    args.capability_id,
    args.material_id,
    args.uri,
  )
  const result = await openviking.readWithCapability(capability, args.uri)
  await recorder.recordOpenVikingRead(args, result)
  return result
}
```

中文注释：

```text
OpenViking 工具只能使用本次 command 的写入目标和 manifest capability，防止 worker 裸 URI 读取或写到未批准路径。
可读材料清单只暴露 material_id/capability_id/URI/SHA/来源，不把上游正文塞进 prompt，防止 Python 变成材料搬运工。
```

验收证据：

- 模型可见工具包含 `openviking.write_material` 和按策略允许的 `openviking.read_with_capability`。
- provider request 里能看到本轮可读材料清单；清单来自 `command.upstream_materials`，包含来源 worker、stage、`material_id`、`capability_id`、`l1_uri`、`l1_sha256` 和 L2 前缀，不包含上游材料正文。
- `writeMaterial()` 返回 `adapter verified receipt`（非 OpenViking 原生 receipt），且证据里必须包含真实 OpenViking write/stat/read-back 校验，不允许 fake receipt 或本地替身。
- `readWithCapability()` 必须校验 capability id、material id、URI 范围；`result_sha256`/`l2_index_sha256` 继续记录审计，但不再因为与 capability 绑定 SHA 不一致而失败。
- 同一个 worker 在不同 stage 或重跑时，`run_id/stage/worker_id/call_id` 必须不同，写入目标和读取 capability 不能串用。
- 不提供 `openviking.read(uri)`、`read_latest`、`latest_material`、`compact_read`、目录扫描或裸 URI read 成功工具。
- `tool-calls.json` 或等价 access evidence 记录 OpenViking write/read 的 capability、material、URI、result_sha256、status。

### T35  捕获 raw output、receipt 和机器可读结果

设计引用：§4.6、§7。

文件范围：

- OpenClaw runtime result builder。

函数级动作：

```ts
function buildOpenClawResult(command: SingleWorkerCommand, recorder: ToolCallRecorder): OpenClawResult
async function writeRawOutput(evidenceDir: string, output: string): Promise<string>
async function writeResultFile(evidenceDir: string, result: OpenClawResult): Promise<void>
```

验收证据：

- 完整运行返回 `raw_output_path` 和 `openviking_receipt_path`。
- `openviking_receipt_path` 指向 `adapter verified receipt` 证据文件（非 OpenViking 原生 receipt）。
- 失败时 `failure_reason` 非空，不能包装成成功。

### T36  OpenClaw 越权扫描

设计引用：§7、§15。

文件范围：只读。

命令：

```bash
rg -n "StagePlan|ApprovedManifest|hard gate|portfolio_manager|final-report|export|12 worker|workflow" third_party/openclaw
```

验收证据：

- OpenClaw 没有 `claw-trade` 的 DAG、批准、hard gate、报告导出或投资逻辑。

## 8. Phase G：claw-trade 调 OpenClaw

### T37  实现 request_builder 输入校验

设计引用：§5.8、§9。

文件范围：

- `src/claw_trade/runtime/request_builder.py`

新增/修改对象：

```python
@dataclass(frozen=True)
class RequestBuildResult: ...
@dataclass(frozen=True)
class RequestBuildContext: ...
```

函数级动作：

```python
def build_request_context(state: WorkflowState, worker_id: str, stage: Stage, manifest: ApprovedManifest) -> RequestBuildResult:
    profile = require_profile(state.request.profile)
    worker = worker_by_id(worker_id)
    validate workspace
    load stage policy
    resolve allowed tools
    upstream_refs = manifest.for_downstream_stage(stage)
    upstream_caps = manifest.capabilities_for_downstream_stage(stage)
    return RequestBuildResult(context=RequestBuildContext(...))
```

中文注释：

```text
这里只校验运行上下文和权限边界，禁止 Python 生成业务正文。
```

验收证据：

- profile、workspace、stage policy、tool registry、manifest capability 都被校验。
- `allowed_tools` 为空时返回 `config_blocked`。
- 不生成 `WorkerCall`，不启动 OpenClaw，不写材料。

### T37A  实现 WorkerCall 构造

设计引用：§4.4、§5.8、§9。

文件范围：

- `src/claw_trade/runtime/request_builder.py`

函数级动作：

```python
def build_worker_call_from_context(context: RequestBuildContext) -> RequestBuildResult:
    call_id = make_call_id(...)
    material_target = make_material_target(...)
    upstream_materials = manifest.for_downstream_stage(...)
    read_capabilities = manifest.capabilities_for_downstream_stage(...)
    return WorkerCall(...)
```

中文注释：

```text
这里只传运行变量和 approved capability，禁止 Python 生成业务正文。
```

验收证据：

- `WorkerCall` 不含业务分析正文。
- `upstream_materials` 和 `openviking_read_capabilities` 只来自 approved manifest；frontline 首批没有上游材料时必须为空。
- 下游 worker 的 `upstream_materials` 必须带来源 worker、stage、`material_id`、`capability_id`、`l1_uri`、`l1_sha256`、L2 前缀和来源 `call_id`。
- `material_target` 只来自本次 run/stage/worker/call。
- 同一个 worker 不同 stage 或重跑时必须生成新的 `call_id` 和新的 `material_target`，不能覆盖旧 URI。
- 本任务不读取 evidence，不调用 OpenClaw，不批准材料。

### T38  实现 openclaw_client

设计引用：§4.5、§7、§9。

文件范围：

- `src/claw_trade/runtime/openclaw_client.py`

函数级动作：

```python
class OpenClawClient:
    def probe(self) -> ProbeResult: ...
    def run_worker(self, command: OpenClawCommand) -> OpenClawResult: ...

def build_openclaw_command(call: WorkerCall) -> OpenClawCommand: ...
def parse_openclaw_result(payload: dict[str, object]) -> OpenClawResult: ...
def validate_openclaw_result_shape(result: OpenClawResult, call: WorkerCall) -> GuardResult: ...
```

验收证据：

- `build_openclaw_command()` 必须原样序列化 `material_target`、`upstream_materials` 和 `openviking_read_capabilities`，不能丢失 `capability_id/material_id/uri/sha`。
- client 不直接调用 provider。
- OpenClaw 退出失败、结果缺字段、证据路径缺失时返回失败。

### T39  实现 evidence_reader

设计引用：§4.6、§4.11、§9、§11.4。

文件范围：

- `src/claw_trade/runtime/evidence_reader.py`

函数级动作：

```python
class EvidenceReader:
    def require_provider_evidence(self, call: WorkerCall, result: OpenClawResult, full_run: bool) -> EvidenceReadResult: ...
    def read_workspace_evidence(path: Path) -> dict[str, object]: ...
    def read_provider_request(path: Path) -> dict[str, object]: ...
    def read_visible_tools(path: Path) -> dict[str, object]: ...
    def read_tool_calls(path: Path) -> dict[str, object]: ...
```

验收证据：

- 路径都必须在本次 run/call evidence dir 下。
- full run 缺 raw output 或 receipt 时失败。
- first response 模式不要求 receipt。

## 9. Phase H：hard gate

### T40  workspace evidence guard

设计引用：§7、§10、§17。

文件范围：

- `src/claw_trade/guards/workspace_evidence.py`

函数级动作：

```python
def validate_workspace_evidence(call: WorkerCall, evidence: ProviderEvidence, agents_root: Path) -> GuardResult: ...
```

验收证据：

- source、run/call/worker/stage/profile/openclaw_run_id 匹配。
- identity、skills、stage policy、worker text asset SHA 证据存在。

### T41  provider request guard

设计引用：§7、§10、§17。

文件范围：

- `src/claw_trade/guards/provider_request.py`

函数级动作：

```python
def validate_provider_request(call: WorkerCall, evidence: ProviderEvidence) -> GuardResult: ...
```

验收证据：

- source 是真实 provider request capture。
- payload 有 messages/tools。
- 带 runtime marker。
- 不接受日志、renderer output、export report 或重构文本。

### T42  visible tools guard

设计引用：§7、§10。

文件范围：

- `src/claw_trade/guards/visible_tools.py`

函数级动作：

```python
def validate_visible_tools(call: WorkerCall, evidence: ProviderEvidence) -> GuardResult: ...
```

验收证据：

- visible tools 与 provider request 同源。
- visible tools 等于本阶段允许工具。
- 工具为空或来源不可信时 hard fail。

### T43  tool calls 和 runtime reads guard

设计引用：§7.1、§10。

文件范围：

- `src/claw_trade/guards/tool_calls.py`
- `src/claw_trade/guards/openviking_access.py`

函数级动作：

```python
def validate_tool_calls(call: WorkerCall, evidence: ProviderEvidence) -> GuardResult: ...
def validate_openviking_runtime_reads(call: WorkerCall, evidence: ProviderEvidence, manifest: ApprovedManifest) -> GuardResult: ...
```

验收证据：

- `tool-calls.json.source == "model_tool_events"`。
- OpenViking read 成功必须带 capability_id/material_id/uri/result_sha256/status；read 失败必须带 status=error 与 error（保留原始输入审计，不要求伪造 capability_id/uri）。
- 实际 read URI 必须来自 manifest capability。

### T44  OpenViking receipt guard

设计引用：§4.9、§8、§10、§11.6。

文件范围：

- `src/claw_trade/guards/openviking_receipt.py`

函数级动作：

```python
def validate_openviking_receipt(call: WorkerCall, receipt: MaterialReceipt, client: OpenVikingClient) -> GuardResult: ...
```

验收证据：

- receipt URI、run/call/worker/stage/target_name 与 `WorkerCall.material_target` 一致。
- stat/read SHA 和 size 一致，且按 OpenViking `content/download` 原始字节口径校验。
- fake receipt 或本地替身 hard fail。

### T45  L1/L2 guard

设计引用：§8、§10、§11.6。

文件范围：

- `src/claw_trade/guards/l1_l2.py`

函数级动作：

```python
def validate_l2_entries(client: OpenVikingClient, l2_index: L2Index, allowed_prefix: VikingUri) -> GuardResult: ...
def validate_l1_l2_contract(call: WorkerCall, l1_text: str, raw_output: str, l2_index: L2Index) -> tuple[tuple[L1Claim, ...], GuardResult]: ...
```

验收证据：

- L1 不能是 compact 摘要。
- L2 entry 必须在 allowed prefix 内且 SHA/size 可校验。
- L2 为空时必须有 empty reason。

### T46  claim guard

设计引用：§4.8、§10。

文件范围：

- `src/claw_trade/artifacts/claims.py`
- `src/claw_trade/guards/l1_l2.py`

函数级动作：

```python
def high_risk_claim_kinds() -> tuple[str, ...]: ...
def validate_claims(claims: tuple[L1Claim, ...], l2_index: L2Index) -> GuardResult: ...
```

验收证据：

- valuation、target price、rating、trade action、risk condition、news、sentiment、chart、tool success、source claim 都要能回到 L2 evidence。

### T47  PM owner guard

设计引用：§10、§12。

文件范围：

- `src/claw_trade/guards/pm_owner.py`

新增对象：

```python
@dataclass(frozen=True)
class PMDecision: ...
```

函数级动作：

```python
def validate_pm_decision_identity(decision: PMDecision, material: ApprovedMaterial) -> GuardResult: ...
def validate_pm_owner(pm_l1_text: str, evidence: ProviderEvidence) -> GuardResult: ...
def validate_export_does_not_rewrite_pm(decision: PMDecision, export_mapping: ExportClaimMapping) -> GuardResult: ...
```

验收证据：

- PM 评级、最终结论、执行条件、风险条件只能来自 PM。
- exporter 改写 PM 决策时导出失败。
- 本任务依赖 T47A 的结构化 PM decision record，不允许自然语言启发式抽取 PM 决策。

### T47A  结构化 PM decision 合同

设计引用：§10、§12。

目标：portfolio manager 必须通过结构化工具字段提交 PM 决策。exporter 和 PM owner guard 只逐项比对结构化 PM decision record，不从 PM 正文或 final report 正文里猜评级和结论。

文件范围：

- `src/claw_trade/guards/pm_owner.py`
- `src/claw_trade/artifacts/claims.py`

固定格式（tool/control evidence record）：

```json
{
  "schema_version": "control.pm_decision.v1",
  "run_id": "run-...",
  "call_id": "call-...",
  "worker_id": "portfolio_manager",
  "stage": "portfolio_decision",
  "material_id": "mat-...",
  "rating": "buy|hold|sell|neutral|not_rated",
  "final_conclusion": "PM 原始最终结论",
  "execution_conditions": ["..."],
  "risk_conditions": ["..."],
  "source_claim_ids": ["claim-..."],
  "source_l1_sha256": "..."
}
```

函数级动作：

```python
def parse_pm_decision_record(record_text: str, material: ApprovedMaterial) -> PMDecisionResult: ...
def require_pm_decision_record(evidence: ProviderEvidence, material: ApprovedMaterial) -> tuple[PMDecision, GuardResult]: ...
def compare_pm_decision_fields(left: PMDecision, right: PMDecision) -> GuardResult: ...
```

验收证据：

- PM decision record 缺失、JSON 非法、worker/stage/material 身份不匹配时 hard fail。
- `rating`、`final_conclusion`、`execution_conditions`、`risk_conditions` 可逐项比对。
- Python 不从自然语言正文推断 PM 评级、结论、执行条件或风险条件。

### T48  export claims guard

设计引用：§10、§12。

文件范围：

- `src/claw_trade/guards/export_claims.py`

新增对象：

```python
@dataclass(frozen=True)
class ExportClaim: ...
@dataclass(frozen=True)
class ExportClaimMapping: ...
```

函数级动作：

```python
def parse_export_claim_mapping(path: Path) -> ExportClaimMappingResult: ...
def validate_export_mapping_identity(mapping: ExportClaimMapping, state: WorkflowState) -> GuardResult: ...
def validate_export_claims_are_supported(mapping: ExportClaimMapping, materials: tuple[ApprovedMaterial, ...]) -> GuardResult: ...
def validate_export_pm_fields(mapping: ExportClaimMapping, pm_decision: PMDecision) -> GuardResult: ...
```

固定格式：

```json
{
  "schema_version": "control.export_claims.v1",
  "run_id": "run-...",
  "final_report_path": "runs/.../reports/final-report.md",
  "claims": [
    {
      "export_claim_id": "export-claim-...",
      "text": "final report 中的声明文本",
      "kind": "rating|trade_action|risk_condition|valuation|news|sentiment|chart|tool_success|source|other",
      "source_material_ids": ["mat-..."],
      "source_claim_ids": ["claim-..."],
      "source_l1_sha256": ["..."]
    }
  ],
  "pm_decision": {
    "source_material_id": "mat-...",
    "rating": "...",
    "final_conclusion": "...",
    "execution_conditions": ["..."],
    "risk_conditions": ["..."]
  }
}
```

验收证据：

- final report 新增 unsupported 投资结论、评级、交易动作、风险条件、估值、新闻、情绪、图表、来源或工具成功声明时失败。
- export claim mapping 缺失、JSON 非法、source claim 不存在或 SHA 不匹配时 hard fail。
- exporter 必须在导出时写 `reports/export-claims.json`，guard 只读这个映射和 approved materials，不从报告正文猜业务 claim。

### T49  guard 组合和早停类别

设计引用：§10、§11.3、§16。

文件范围：

- `src/claw_trade/guards/common.py`
- `src/claw_trade/workflow/runner.py`

函数级动作：

```python
EARLY_STOP_CATEGORIES = {...}
def should_early_stop(failure: FailureRecord) -> bool: ...
def combine_guard_results(checks: tuple[GuardResult, ...]) -> GuardResult: ...
```

验收证据：

- 早停类别有中文注释。
- hard fail 不能降级为 warning。

### T49A  材料批准最终接线

设计引用：§4.9、§4.10、§10、§11.6。

目标：在 T44-T48 的 hard gate 都可用后，把 T26 的 approval candidate 接成真正的批准流程。只有全部 guard 通过，才能生成 `ApprovedMaterial` 并交给 manifest store。

文件范围：

- `src/claw_trade/artifacts/approval.py`
- `src/claw_trade/workflow/runner.py`

函数级动作：

```python
def approve_worker_material(call: WorkerCall, evidence: ProviderEvidence, openviking: OpenVikingClient) -> ApprovalResult:
    candidate = prepare_approval_candidate(call, evidence, openviking)
    receipt_guard = validate_openviking_receipt(call, candidate.receipt, openviking)
    l1_l2_claims, l1_l2_guard = validate_l1_l2_contract(call, candidate.l1_text, read_raw_output(candidate.raw_output_path), candidate.l2_index)
    claim_guard = validate_claims(l1_l2_claims, candidate.l2_index)
    pm_guard = validate_pm_owner_if_needed(call, candidate)
    combined = combine_guard_results((receipt_guard, l1_l2_guard, claim_guard, pm_guard))
    if not combined.ok:
        return ApprovalResult.failed(combined.category, combined.reason, combined.paths)
    return ApprovalResult.ok_result(build_approved_material_from_passed_gates(call, candidate, l1_l2_claims))
```

验收证据：

- receipt 通过不等于批准；必须等 receipt、L1/L2、claim、PM owner 全部通过。
- approval 不写 manifest；runner 在保存 approval 结果和 guard evidence 后再调用 manifest store。
- 任何 guard 失败都返回材料拒绝，不能写 fake approved material。

## 10. Phase I：store、runner、CLI 和报告导出

### T50  实现 WorkflowStore

设计引用：§11.1、§13。

文件范围：

- `src/claw_trade/workflow/store.py`

函数级动作：

```python
class WorkflowStore:
    def run_dir(run_id: str) -> Path: ...
    def call_dir(call: WorkerCall) -> Path: ...
    def evidence_dir(call: WorkerCall) -> Path: ...
    def create_run(request: RunRequest) -> WorkflowState: ...
    def load_state(run_id: str) -> WorkflowState: ...
    def save_state(state: WorkflowState) -> None: ...
    def save_decision(run_id: str, decision: Decision) -> Path: ...
    def save_call(call: WorkerCall) -> Path: ...
    def save_openclaw_result(call: WorkerCall, result: OpenClawResult) -> Path: ...
    def save_worker_result(result: WorkerResult) -> Path: ...
    def list_worker_results(run_id: str) -> tuple[WorkerResult, ...]: ...
    def save_failure(failure: FailureRecord) -> Path: ...
    def save_stage_batch_result(result: StageBatchResult) -> Path: ...
    def save_export_result(result: ExportResult) -> Path: ...
    def load_export_result(run_id: str) -> ExportResult | None: ...
```

中文注释：

```text
runs/<run>/openviking 只是本地审计副本，不是正式材料权威。
```

验收证据：

- run dir 结构符合 §13。
- store 不保存 OpenViking 正式材料正文作为权威。

### T50A  evidence 和 audit 持久化

设计引用：§11.1、§13。

目标：把 guard 结果、OpenViking receipt 审计、runtime read 审计、L1/L2 index 审计保存到 run dir。这里保存的是审计副本和索引，不是正式材料正文权威。

文件范围：

- `src/claw_trade/workflow/store.py`

函数级动作：

```python
class WorkflowStore:
    def save_guard_result(call: WorkerCall, guard: GuardResult) -> Path: ...
    def save_export_guard_result(run_id: str, guard: GuardResult) -> Path: ...
    def append_receipt_audit(run_id: str, receipt: MaterialReceipt, guard: GuardResult) -> Path: ...
    def append_access_audit(call: WorkerCall, records: tuple[OpenVikingAccessRecord, ...]) -> Path: ...
    def save_l1_l2_index(call: WorkerCall, index: L2Index) -> Path: ...
    def save_export_claim_mapping(run_id: str, mapping: ExportClaimMapping) -> Path: ...
```

中文注释：

```text
这里写的是审计索引和校验结果，正式材料权威仍在 OpenViking URI、receipt、stat/read 和内容指纹。
```

验收证据：

- `runs/<run>/calls/<call>/guard-results.json` 存在。
- `runs/<run>/openviking/receipts.json` 记录 receipt 校验摘要。
- `runs/<run>/openviking/access-audit.json` 记录 runtime reads。
- `runs/<run>/openviking/l1-l2-index.json` 记录 L2 index 审计摘要。
- 不把 OpenViking L1 正文保存成本地权威材料。

### T51  实现 ControlRunner.boot

设计引用：§11.2。

文件范围：

- `src/claw_trade/workflow/runner.py`

函数级动作：

```python
class ControlRunner:
    def boot(self, request: RunRequest) -> BootResult: ...
    def fail_before_run(self, request: RunRequest, boot: BootResult) -> WorkflowState: ...
```

验收证据：

- boot 检查 profile/OpenClaw/OpenViking/tool registry。
- 真实依赖缺失返回 `BLOCKED`，不创建 fake provider 或 fake receipt。

### T51A  实现 ControlRunner.run 主循环

设计引用：§11.2、§11.7。

文件范围：

- `src/claw_trade/workflow/runner.py`

函数级动作：

```python
class ControlRunner:
    def run(self, request: RunRequest) -> WorkflowState: ...
    def apply_decision(self, state: WorkflowState, decision: Decision) -> WorkflowState: ...
    def fail_run(self, state: WorkflowState, failure: FailureRecord, decision_path: Path) -> WorkflowState: ...
```

验收证据：

- 每轮先保存 decision，再执行对应 action。
- controller 只给 decision；runner 负责调用 runtime/artifacts/guards/reports。
- `REPORT_EXPORTING` 只有看到已持久化且通过的 `ExportResult` 才能进入 completed。

### T52  实现 run_stage_batch

设计引用：§11.3。

文件范围：

- `src/claw_trade/workflow/runner.py`

函数级动作：

```python
def run_stage_batch(self, state: WorkflowState, batch: StageBatch) -> StageBatchResult: ...
```

验收证据：

- 同阶段 collect-first 尽量收集失败。
- 允许早停类别符合设计。
- 早停写 `exception_evidence`。

### T53  实现 run_single_worker 的 OpenClaw 调用骨架

设计引用：§11.4、§11.5、§11.6。

文件范围：

- `src/claw_trade/workflow/runner.py`

函数级动作：

```python
def run_single_worker(self, call: WorkerCall) -> WorkerResult: ...
```

验收证据：

- 保存顺序先覆盖：call -> OpenClaw -> openclaw result。
- OpenClaw 返回失败时生成 `WorkerResult.failed`，不继续读 evidence 或批准材料。
- 本任务不跑 guards、不批准材料、不写 manifest。

### T53A  接线 evidence_reader

设计引用：§11.4。

文件范围：

- `src/claw_trade/workflow/runner.py`
- `src/claw_trade/runtime/evidence_reader.py`

函数级动作：

```python
def read_worker_evidence(self, call: WorkerCall, result: OpenClawResult) -> EvidenceReadResult: ...
```

验收证据：

- OpenClaw 成功后才读取 evidence。
- first response 模式不要求 raw output 或 receipt。
- 完整 worker 缺 raw output 或 receipt 时返回失败，不补本地替身。

### T53B  接线 runtime guards

设计引用：§10、§11.4。

文件范围：

- `src/claw_trade/workflow/runner.py`

函数级动作：

```python
def run_runtime_guards(self, call: WorkerCall, evidence: ProviderEvidence) -> GuardResult: ...
```

验收证据：

- runtime guards 包括 workspace evidence、provider request、visible tools、tool calls、runtime reads。
- guard 结果通过 T50A 持久化。
- runtime guard 失败时当前 worker 失败，不能进入 approval。

### T53C  接线 approval

设计引用：§11.6。

文件范围：

- `src/claw_trade/workflow/runner.py`
- `src/claw_trade/artifacts/approval.py`

函数级动作：

```python
def run_material_approval(self, call: WorkerCall, evidence: ProviderEvidence) -> ApprovalResult: ...
```

验收证据：

- first response 模式不批准材料。
- 完整模式必须调用 T49A 的最终批准接线。
- approval 失败时不写 manifest。

### T53D  接线 manifest 持久化

设计引用：§4.10、§11.6。

文件范围：

- `src/claw_trade/workflow/runner.py`
- `src/claw_trade/artifacts/manifest.py`

函数级动作：

```python
def persist_approved_material(self, state: WorkflowState, result: ApprovalResult) -> WorkerResult: ...
```

验收证据：

- 只有 `ApprovalResult.ok` 才能写 approved manifest。
- manifest 写入发生在 guard/approval evidence 持久化之后。
- first response 模式不批准材料。
- 完整模式必须批准材料后才返回 `WorkerResult.succeeded`。

### T54  实现 collect-first 报告

设计引用：§11.3。

文件范围：

- `src/claw_trade/workflow/runner.py`

函数级动作：

```python
def write_collect_first_report(batch: StageBatch, results: list[WorkerResult], failures: list[FailureRecord], early_stop_used: bool) -> Path: ...
def build_batch_fix_grouping(failures: list[FailureRecord]) -> list[dict[str, object]]: ...
```

验收证据：

```json
{
  "collect_first_compliance": {
    "batch_scope": {},
    "completed_items": [],
    "failures_collected": [],
    "early_stop_exception_used": false,
    "exception_evidence": [],
    "batch_fix_grouping": []
  }
}
```

### T55  落实状态保存顺序约束

设计引用：§11.7。

文件范围：

- `src/claw_trade/workflow/runner.py`
- `src/claw_trade/workflow/store.py`

函数级动作：

```python
def assert_manifest_not_written_before_guards(call_id: str) -> None: ...
def assert_export_result_before_completed(state: WorkflowState) -> None: ...
```

验收证据：

- guard 前不能写 approved manifest。
- `REPORT_EXPORTING` 后必须保存 `export-result.json`，controller 下一轮才能 completed。

### T56  实现 final report exporter 的材料装载和渲染

设计引用：§12。

文件范围：

- `src/claw_trade/reports/exporter.py`

函数级动作：

```python
def required_report_workers() -> set[str]: ...
def load_report_materials(state: WorkflowState, manifest: ApprovedManifest) -> ReportMaterialsResult: ...
@dataclass(frozen=True)
class RenderedReport:
    text: str
    claim_links: tuple[ExportClaim, ...]

def render_final_report(materials: tuple[ApprovedMaterial, ...], pm_decision: PMDecision) -> RenderedReport: ...
```

验收证据：

- 缺任一 required approved material 时导出失败。
- 只能整理 approved materials。
- 本任务只生成 final report 文本候选，不保存 `ExportResult`。

### T56A  实现 final report export guard 接线

设计引用：§12。

文件范围：

- `src/claw_trade/reports/exporter.py`
- `src/claw_trade/guards/pm_owner.py`
- `src/claw_trade/guards/export_claims.py`

函数级动作：

```python
def build_export_claim_mapping(rendered: RenderedReport, materials: tuple[ApprovedMaterial, ...], pm_decision: PMDecision) -> ExportClaimMapping: ...
def run_export_guards(mapping: ExportClaimMapping, materials: tuple[ApprovedMaterial, ...], pm_decision: PMDecision) -> GuardResult: ...
```

验收证据：

- PM 评级、最终结论、执行条件、风险条件不被改写。
- final report 的高风险声明都能映射回 approved material 的 source claim。
- `reports/export-claims.json` 可被 T48 解析。
- export mapping 来自 renderer 选择的 source claim links，不从 final report 自然语言正文反向抽取。

### T56B  实现 final report 导出结果持久化

设计引用：§11.7、§12。

文件范围：

- `src/claw_trade/reports/exporter.py`
- `src/claw_trade/workflow/store.py`

函数级动作：

```python
def export_final_report(state: WorkflowState, manifest: ApprovedManifest) -> ExportResult: ...
def persist_export_outputs(state: WorkflowState, rendered: RenderedReport, mapping: ExportClaimMapping, guard: GuardResult) -> ExportResult: ...
```

验收证据：

- 导出后写 `reports/final-report.md`、`reports/export-claims.json`、`reports/export-guard-results.json` 和 `reports/export-result.json`。
- `ExportResult.status="passed"` 只有在 export guard 通过后才允许出现。
- export 失败不能把 run 标成 completed。

### T57  实现 CLI

设计引用：§13、§14。

文件范围：

- `src/claw_trade/cli/run_control.py`

函数级动作：

```python
def parse_args(argv: list[str]) -> RunRequest: ...
def main(argv: list[str] | None = None) -> int: ...
```

参数：

```text
ticker
company-name
market
profile
currency
currency-symbol
current-date
start-date
end-date
stop-point
target-worker-id
target-stage
run-dir
```

验收证据：

- CLI 不提供跳过 OpenClaw/OpenViking 的成功模式。
- 单 worker 命令能指定目标 worker。

## 11. Phase J：真实链路里程碑验证

### T58  基础结构验证收口

设计引用：§14.1。

命令：

```bash
test -d src/claw_trade
rg --files src/claw_trade
uv run python -c "import claw_trade; print(claw_trade.__file__)"
```

验收证据：

- `claw_trade.__file__` 位于 `/home/frank/src/claw-trade/src/claw_trade`。
- 基础包和控制面模块可导入。
- 如果 `src/claw_trade` 缺失，先回到 T00，不能转用旧代码目录。

### T59  单 worker first response 里程碑

设计引用：§14.2。

命令：

```bash
uv run python -m claw_trade.cli.run_control --ticker AAPL --company-name Apple --market US --profile US --currency USD --currency-symbol '$' --current-date 2026-05-03 --start-date 2026-05-03 --end-date 2026-05-03 --stop-point first_response --target-worker-id market_analyst --target-stage frontline
```

验收证据：

- `call.json`
- `openclaw-result.json`
- `workspace-evidence.json`
- `provider-request.json`
- `visible-tools.json`
- `first-response.json`
- `tool-calls.json`

偏离检查：

- 不应出现 raw output/receipt 才能算 first response 成功。
- 不应唤醒整个前线阶段。

### T60  单 worker 完整 OpenViking 写入里程碑

设计引用：§14.3。

命令：

```bash
uv run python -m claw_trade.cli.run_control --ticker AAPL --company-name Apple --market US --profile US --currency USD --currency-symbol '$' --current-date 2026-05-03 --start-date 2026-05-03 --end-date 2026-05-03 --stop-point single_worker_complete --target-worker-id market_analyst --target-stage frontline
```

验收证据：

- raw output。
- OpenViking L1/L2 URI。
- receipt。
- stat/read SHA 校验（以 OpenViking `content/download` 原始字节为准）。
- approved manifest 中只新增目标 worker 材料。
- provider visible tools 包含 `market.stock_price`、`market.techlab_analyze`，且不依赖 `openvikingArtifact__*` 的 1944 MCP sidecar。

### T61  前线四 worker 里程碑

设计引用：§14.4。

命令：

```bash
uv run python -m claw_trade.cli.run_control --ticker AAPL --company-name Apple --market US --profile US --currency USD --currency-symbol '$' --current-date 2026-05-03 --start-date 2026-05-03 --end-date 2026-05-03 --stop-point frontline_ready
```

验收证据：

- market/fundamental/news/social 四份 approved material。
- collect-first report。
- `FRONTLINE_READY` 或明确失败归因。

### T62  12 worker 全链路里程碑

设计引用：§14.5。

命令：

```bash
uv run python -m claw_trade.cli.run_control --ticker AAPL --company-name Apple --market US --profile US --currency USD --currency-symbol '$' --current-date 2026-05-03 --start-date 2026-05-03 --end-date 2026-05-03
```

验收证据：

- 12 个 worker 都有 OpenClaw wake 证据。
- 后八个 worker 只通过 approved manifest capability 读取上游材料。
- 没有 direct LLM 或 Python materializer 路径。
- `content/read` 展示差异（例如末尾少一个换行）和 canonical download bytes mismatch 都不能单独作为 T62 阻断理由；SHA 相关字段仅保留审计信息。

### T63  final report truth gate 里程碑

设计引用：§12、§14.6。

命令：

```bash
uv run pytest tests/contracts/test_export_claims_guard.py tests/unit/test_report_exporter.py -q
```

验收证据：

- `reports/final-report.md`
- `reports/export-claims.json`
- `reports/export-guard-results.json`
- `reports/export-result.json`
- PM 决策未被改写。
- final report 无 unsupported claim。

### T63A  live final report truth gate

设计引用：§12、§14.6、§17。

目标：使用 T62 真实 run 的产物检查 final report truth gate，而不是只看 exporter 合同和单元级验证。

输入：

- T62 产生的 `run_id`。
- `runs/<run_id>/reports/final-report.md`
- `runs/<run_id>/reports/export-claims.json`
- `runs/<run_id>/reports/export-guard-results.json`
- `runs/<run_id>/reports/export-result.json`
- portfolio manager 的 approved material、PM decision record 和 L1 SHA。

函数级动作：

```python
def verify_live_final_report_truth_gate(run_id: str, store: WorkflowStore) -> CompletionCheckResult: ...
def load_live_pm_decision(run_id: str, manifest: ApprovedManifest) -> PMDecision: ...
def load_live_export_mapping(run_id: str) -> ExportClaimMapping: ...
```

验收证据：

- `ExportResult.status == "passed"` 时必须能找到对应的 `export-guard-results.json` 和 `export-claims.json`。
- PM 评级、最终结论、执行条件、风险条件与 PM decision record 逐项一致。
- final report 所有高风险 claim 都能映射回 approved material 的 source claim 和 L1 SHA（SHA 口径为 OpenViking `content/download` 原始字节）。
- 若 T62 未产出完整 reports 目录，本任务写 `BLOCKED`，不能用单元级结果替代 live gate。

## 12. Phase K：代码评审、覆盖和偏离检查

### T64  禁止路径扫描

设计引用：§15。

命令：

```bash
rg -n "direct_llm|directLLM|materializer|Materializer|fake|stub|mock|fallback|fallback_prompt|fallbackPrompt|fallback_market|fallbackMarket|latest|list_latest|listLatest|read_latest|readLatest|latest_material|latestMaterial|compact|compact_read|compactRead|bare_uri|bareUri|read\\(uri|readText\\(|read_text\\(|provider_request.*reconstruct|providerRequest.*reconstruct|reconstructedProviderRequest|renderer.*provider|export.*provider|final.*rewrite|portfolio.*rewrite|pm.*rewrite" src/claw_trade agents third_party/openclaw tests
```

验收证据：

- 每个命中都归类：真实违规、设计允许的禁止项文字、无关命中。
- 真实违规必须修或标 `BLOCKED`。
- 旧代码目录不参与实现违规判定；如果要做旧代码污染对照，必须单独标为只读参考扫描。

### T65  架构边界评审

设计引用：§1、§3、§7、§8、§15。

检查项：

- `claw-trade` 是否仍管流程、批准、hard gate、manifest、报告导出。
- OpenClaw 是否只管单 worker 模型回合。
- OpenViking 是否只管正式材料、receipt、stat/read、capability、上下文。
- controller 是否保持纯决策。
- exporter 是否只整理 approved materials。

验收证据：

- 输出 `达到预期 / 未达到预期 / 无法判断 / BLOCKED`。
- 每个结论必须引用文件和证据。

### T66  设计覆盖追踪复核

设计引用：全篇。

函数级动作：

```text
for each design_section:
    list task_ids
    list changed_files
    list evidence_paths
    classify status as 达到预期 / 未达到预期 / 无法判断 / BLOCKED
```

验收证据：

- 第 1 节覆盖矩阵每一行都有真实状态。
- 不使用未经逐项证明的绝对性结论。

### T67  完成判定报告

设计引用：§17。

输出文件建议：

```text
runs/<run_id>/reports/control-completion-review.md
```

必须回答：

1. 是否经过 OpenClaw 真实 worker wake？
2. provider request 在哪里？
3. visible tools 是否来自同一 provider request？
4. workspace evidence 是否证明真实加载？
5. first response 在哪里？
6. tool calls 是 recorded 还是 none？
7. raw output 在哪里？
8. L1 正式材料 URI 在哪里？
9. receipt 是否通过 stat/read 校验？
10. L1 高风险 claim 对应哪些 L2 evidence？
11. 下游 read 是否全部来自 manifest capability？
12. 是否存在 compact/latest/list/目录扫描/裸 URI read 正式路径？
13. Python 是否写业务正文或替 worker 调工具？
14. 后八个 worker 是否绕过 OpenClaw？
15. final report 是否新增 unsupported claim？
16. PM 决策是否被 exporter 改写？
17. 中文注释是否覆盖关键边界？

验收证据：

- 每项只能写 `达到预期`、`未达到预期`、`无法判断` 或 `BLOCKED`。
- 任何关键证据缺失，都不能把该阶段写成 `达到预期`。

## 13. 停止条件索引

任一任务命中下面情况，必须停下来问人类：

- 需要改变 OpenClaw 修改范围，超出通用单 worker runtime 接缝。
- 需要让 OpenClaw 接管 12 worker DAG、批准、hard gate 或报告导出。
- 需要让 OpenViking 接管流程推进、批准、重试或最终结论。
- 需要改变 PM owner。
- 需要 Python 改写 PM 投资结论、评级、执行条件或风险条件。
- 需要保留或新增 direct LLM report path。
- 需要新增未经批准的 worker 文案回退、fallback tool、fallback market profile、fake provider result 或 fake artifact success。
- 需要 mock/stub/fake/fallback 成功路径才能让链路看起来通过。
- 需要放松 unsafe/fabrication/chart hard gate。
- provider payload 需要作为证据但 OpenClaw 不能提供真实 provider request capture。
- visible tools 不能证明来自同一份 provider request。
- OpenViking 不能提供正式 read/stat/receipt 或内容指纹。
- OpenViking 只能提供 compact/latest/list/目录扫描/裸 URI read。
- HK 或 CRYPTO market profile 策略未批准，但实现需要继续跑。
- 需要改变 worker 调度、执行权、重试 owner、重试预算或 Python/OpenClaw 责任边界。
- 同一类 gate 在修复后仍重复失败，且根因无法确认。

## 14. 当前执行边界

这些是执行任务时必须遵守的边界：

- 工程目录和工作区固定为 `/home/frank/src/claw-trade`，已经存在，不能创建、移动、嵌套或重命名。
- 目标代码路径是 `/home/frank/src/claw-trade/src/claw_trade`，任务只从重建已有工程目录下的 `./src/` 子目录开始。
- 已有 `memory/` 目录只用于追加任务记录，不能新建第二套工作区或第二套 `memory/`。
- 旧代码目录只是只读参考，不能作为实现目标、导入路径、整目录复制来源或成功路径。
- `docs/control迁移任务清单.md` 当前工作区不可见。本文档是新建清单，不覆盖旧清单。
- `agents/` 已有 12 个 worker workspace 和人工维护资产。本文档不要求自动修改这些资产内容。
- `third_party/openclaw` 文件很多，OpenClaw 接缝任务必须先定位真实单 worker 入口，不能凭设计候选路径硬写。
