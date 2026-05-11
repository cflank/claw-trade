from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from claw_trade.artifacts.manifest import ApprovedManifest, ArtifactFlowError
from claw_trade.artifacts.refs import MaterialReadRef, OpenVikingReadCapability, make_material_target
from claw_trade.config.profiles import ConfigError, require_profile
from claw_trade.config.stage_policy import load_stage_policy, validate_stage_policy_matches_worker
from claw_trade.config.tool_names import load_tool_registry, resolve_tools
from claw_trade.config.workspace import validate_worker_workspace_for_control
from claw_trade.workflow.models import (
    FailureRecord,
    ReadPolicy,
    Stage,
    StopPoint,
    WorkerCall,
    WorkflowEntryPoint,
    WorkflowState,
)
from claw_trade.workflow.workers import worker_by_id

_REPO_ROOT = Path(__file__).resolve().parents[3]
_AGENTS_ROOT = _REPO_ROOT / "agents"


@dataclass(frozen=True)
class RequestBuildContext:
    state: WorkflowState
    worker_id: str
    stage: Stage
    profile: str
    allowed_tools: tuple[str, ...]
    upstream_materials: tuple[MaterialReadRef, ...]
    openviking_read_capabilities: tuple[OpenVikingReadCapability, ...]


@dataclass(frozen=True)
class RequestBuildResult:
    ok: bool
    context: RequestBuildContext | None
    call: WorkerCall | None
    failure: FailureRecord | None

    @classmethod
    def failed(
        cls,
        state: WorkflowState,
        worker_id: str,
        stage: Stage,
        category: str,
        reason: str,
        paths: tuple[Path, ...] = (),
    ) -> RequestBuildResult:
        return cls(
            ok=False,
            context=None,
            call=None,
            failure=FailureRecord(
                run_id=state.run_id,
                call_id=None,
                worker_id=worker_id,
                stage=stage,
                category=category,
                reason=reason,
                evidence_paths=paths or (state.run_dir / "request.json",),
                early_stop=True,
                human_action_required=None,
            ),
        )

    @classmethod
    def context_result(cls, context: RequestBuildContext) -> RequestBuildResult:
        return cls(ok=True, context=context, call=None, failure=None)

    @classmethod
    def call_result(cls, context: RequestBuildContext, call: WorkerCall) -> RequestBuildResult:
        return cls(ok=True, context=context, call=call, failure=None)


def build_request_context(
    state: WorkflowState,
    worker_id: str,
    stage: Stage,
    manifest: ApprovedManifest,
) -> RequestBuildResult:
    # Python 只做运行前校验与参数拼装，不负责 worker 正文，也不能替 OpenClaw 改写 provider request。
    profile = require_profile(state.request.profile)
    if not profile.ok:
        return RequestBuildResult.failed(
            state=state,
            worker_id=worker_id,
            stage=stage,
            category="config_blocked",
            reason=profile.reason or f"profile 校验失败: {state.request.profile}",
        )

    try:
        worker = worker_by_id(worker_id)
    except KeyError:
        return RequestBuildResult.failed(
            state=state,
            worker_id=worker_id,
            stage=stage,
            category="config_blocked",
            reason=f"unknown worker id: {worker_id}",
        )

    if worker.stage != stage:
        return RequestBuildResult.failed(
            state=state,
            worker_id=worker_id,
            stage=stage,
            category="config_blocked",
            reason=f"worker 阶段不匹配: {worker_id}/{stage.value}",
        )

    workspace = validate_worker_workspace_for_control(_AGENTS_ROOT, worker_id)
    if not workspace.ok:
        return RequestBuildResult.failed(
            state=state,
            worker_id=worker_id,
            stage=stage,
            category="config_blocked",
            reason=workspace.reason or f"worker workspace 无效: {worker_id}",
            paths=workspace.missing_paths,
        )

    policy_result = load_stage_policy(_AGENTS_ROOT, worker_id, profile.name)
    if not policy_result.ok or policy_result.policy is None:
        return RequestBuildResult.failed(
            state=state,
            worker_id=worker_id,
            stage=stage,
            category="config_blocked",
            reason=policy_result.reason or f"stage policy 加载失败: {worker_id}/{profile.name}",
            paths=(policy_result.source_path,),
        )
    policy = policy_result.policy

    match_guard = validate_stage_policy_matches_worker(policy, worker)
    if not match_guard.ok:
        return RequestBuildResult.failed(
            state=state,
            worker_id=worker_id,
            stage=stage,
            category=match_guard.category,
            reason=match_guard.reason or "stage policy 与 worker 不匹配",
            paths=match_guard.paths or (policy.source_path,),
        )

    tool_registry_result = load_tool_registry()
    if not tool_registry_result.ok or tool_registry_result.registry is None:
        return RequestBuildResult.failed(
            state=state,
            worker_id=worker_id,
            stage=stage,
            category="config_blocked",
            reason=tool_registry_result.reason or "tool registry 加载失败",
        )

    try:
        allowed_tools = resolve_tools(policy, tool_registry_result.registry)
    except ConfigError as exc:
        return RequestBuildResult.failed(
            state=state,
            worker_id=worker_id,
            stage=stage,
            category="config_blocked",
            reason=str(exc),
            paths=(policy.source_path,),
        )
    if not allowed_tools:
        return RequestBuildResult.failed(
            state=state,
            worker_id=worker_id,
            stage=stage,
            category="config_blocked",
            reason=f"阶段工具为空: {worker_id}",
            paths=(policy.source_path,),
        )

    try:
        upstream_refs = manifest.for_downstream_stage(stage=stage, run_id=state.run_id)
        upstream_caps = manifest.capabilities_for_downstream_stage(stage=stage, run_id=state.run_id)
    except (ArtifactFlowError, ValueError) as exc:
        return RequestBuildResult.failed(
            state=state,
            worker_id=worker_id,
            stage=stage,
            category="config_blocked",
            reason=f"approved manifest 无法满足阶段输入: {exc}",
        )

    manifest_reason = _validate_manifest_contract(stage, upstream_refs, upstream_caps)
    if manifest_reason is not None:
        return RequestBuildResult.failed(
            state=state,
            worker_id=worker_id,
            stage=stage,
            category="config_blocked",
            reason=manifest_reason,
        )

    context = RequestBuildContext(
        state=state,
        worker_id=worker_id,
        stage=stage,
        profile=profile.name,
        allowed_tools=allowed_tools,
        upstream_materials=upstream_refs,
        openviking_read_capabilities=upstream_caps,
    )
    return RequestBuildResult.context_result(context)


def build_worker_call_from_context(context: RequestBuildContext) -> RequestBuildResult:
    state = context.state
    if not context.allowed_tools:
        return RequestBuildResult.failed(
            state=state,
            worker_id=context.worker_id,
            stage=context.stage,
            category="config_blocked",
            reason=f"阶段工具为空: {context.worker_id}",
        )

    manifest_reason = _validate_manifest_contract(
        context.stage,
        context.upstream_materials,
        context.openviking_read_capabilities,
    )
    if manifest_reason is not None:
        return RequestBuildResult.failed(
            state=state,
            worker_id=context.worker_id,
            stage=context.stage,
            category="config_blocked",
            reason=manifest_reason,
        )

    call_id = make_call_id(state.run_id, context.stage, context.worker_id)
    # material_target 绑定 run/stage/worker/call，确保同名“report”不会跨 worker 或跨阶段串证据。
    material_target = make_material_target(
        run_id=state.run_id,
        stage=context.stage,
        worker_id=context.worker_id,
        call_id=call_id,
        target_name="report",
    )
    evidence_dir = state.run_dir / "calls" / call_id

    # 这里只传运行变量和 approved capability，禁止 Python 生成业务正文。
    call = WorkerCall(
        call_id=call_id,
        run_id=state.run_id,
        worker_id=context.worker_id,
        stage=context.stage,
        profile=context.profile,
        ticker=state.request.ticker,
        company_name=state.request.company_name,
        market=state.request.market,
        currency=state.request.currency,
        currency_symbol=state.request.currency_symbol,
        current_date=state.request.current_date,
        start_date=state.request.start_date,
        end_date=state.request.end_date,
        allowed_tools=context.allowed_tools,
        upstream_materials=context.upstream_materials,
        openviking_read_capabilities=context.openviking_read_capabilities,
        material_target=material_target,
        read_policy=ReadPolicy(),
        evidence_dir=evidence_dir,
        stop_after_first_response=state.request.stop_point == StopPoint.FIRST_RESPONSE,
        system_context_policy=_system_context_policy_for_request(state.request.entry_point),
    )
    return RequestBuildResult.call_result(context, call)


def build_worker_call(
    state: WorkflowState,
    worker_id: str,
    stage: Stage,
    manifest: ApprovedManifest,
) -> RequestBuildResult:
    # 这里只校验运行上下文和权限边界，禁止 Python 生成业务正文。
    context_result = build_request_context(state=state, worker_id=worker_id, stage=stage, manifest=manifest)
    if not context_result.ok or context_result.context is None:
        return context_result
    return build_worker_call_from_context(context_result.context)


def make_call_id(run_id: str, stage: Stage, worker_id: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{run_id}-{stage.value}-{worker_id}-{timestamp}-{uuid4().hex[:8]}"


def default_read_policy() -> ReadPolicy:
    return ReadPolicy()


def _system_context_policy_for_request(entry_point: WorkflowEntryPoint) -> str:
    if entry_point == WorkflowEntryPoint.REPORT_COMMAND:
        return "single_worker_minimal"
    return "openclaw_default"


def _validate_manifest_contract(
    stage: Stage,
    refs: tuple[MaterialReadRef, ...],
    caps: tuple[OpenVikingReadCapability, ...],
) -> str | None:
    # frontline 是首阶段，必须没有上游材料；否则等于 Python 人工注入了不存在的“历史输入”。
    if stage == Stage.FRONTLINE:
        if refs or caps:
            return "frontline 首批 upstream_materials/openviking_read_capabilities 必须为空"
        return None

    if not refs:
        return f"阶段缺少 approved upstream_materials: {stage.value}"
    if not caps:
        return f"阶段缺少 approved openviking_read_capabilities: {stage.value}"
    if len(refs) != len(caps):
        return f"manifest 引用数量不一致: refs={len(refs)} caps={len(caps)}"

    caps_by_id = {cap.capability_id: cap for cap in caps}
    if len(caps_by_id) != len(caps):
        return "manifest capability_id 存在重复"
    for ref in refs:
        capability = caps_by_id.get(ref.capability_id)
        if capability is None:
            return f"manifest capability 缺失: {ref.capability_id}"
        # stage/worker/path/hash 的一致性检查是跨阶段防串线的核心：只允许读取本次批准链路上的正式材料。
        if capability.material_id != ref.material_id:
            return f"manifest capability material_id 不匹配: {ref.capability_id}"
        if capability.allowed_l1_uri != ref.l1_uri:
            return f"manifest capability l1_uri 不匹配: {ref.capability_id}"
        if capability.allowed_l1_sha256 != ref.l1_sha256:
            return f"manifest capability l1_sha256 不匹配: {ref.capability_id}"
        if capability.allowed_l2_prefix != ref.l2_allowed_prefix:
            return f"manifest capability l2_prefix 不匹配: {ref.capability_id}"
    return None
