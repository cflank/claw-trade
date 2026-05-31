from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Mapping

from claw_trade.runtime.evidence_reader import OpenClawResult
from claw_trade.runtime.openclaw_client import (
    OpenClawClient,
    OpenClawCommand,
    serialize_openclaw_command_payload,
)
from claw_trade.selection.evidence import (
    evidence_from_dispatch,
    validate_selection_dispatch_evidence,
)
from claw_trade.selection.models import (
    CandidatePackRef,
    SelectionStage,
    SelectionSystemContextPolicy,
    SelectionWorkerDispatch,
    SelectionWorkerId,
    SelectRequest,
)

_SELECTION_CANDIDATE_PACK_TOOL = "claw_get_selection_candidate_pack"
_SELECTION_DISPATCH_WORKER_ORDER = (
    SelectionWorkerId.STRATEGIST,
    SelectionWorkerId.SKEPTIC,
    SelectionWorkerId.MANAGER,
    SelectionWorkerId.PORTFOLIO_MANAGER,
)
_SELECTION_DISPATCH_STAGE_BY_WORKER: dict[SelectionWorkerId, SelectionStage] = {
    SelectionWorkerId.STRATEGIST: SelectionStage.SELECTION_REVIEW,
    SelectionWorkerId.SKEPTIC: SelectionStage.SELECTION_REVIEW,
    SelectionWorkerId.MANAGER: SelectionStage.SELECTION_DECISION,
    SelectionWorkerId.PORTFOLIO_MANAGER: SelectionStage.SELECTION_PORTFOLIO_DECISION,
}
_SELECTION_DISPATCH_TOOLS_BY_WORKER: dict[SelectionWorkerId, tuple[str, ...]] = {
    SelectionWorkerId.STRATEGIST: (_SELECTION_CANDIDATE_PACK_TOOL,),
    SelectionWorkerId.SKEPTIC: (_SELECTION_CANDIDATE_PACK_TOOL,),
    SelectionWorkerId.MANAGER: (),
    SelectionWorkerId.PORTFOLIO_MANAGER: (),
}
_RUNTIME_CONTEXT_PLACEHOLDER = "selection_runtime_context"
_SKEPTIC_PROMPT_MATERIAL_MARKERS = ("approved_strategist_l1",)
_MANAGER_PROMPT_MATERIAL_MARKERS = (
    "approved_strategist_l1",
    "approved_skeptic_l1",
    "candidate_pack_summary",
)
_PORTFOLIO_MANAGER_PROMPT_MATERIAL_MARKERS = (
    "approved_manager_l1",
    "approved_strategist_l1",
    "approved_skeptic_l1",
    "candidate_pack_summary",
)


@dataclass(frozen=True)
class SelectionDispatchExecution:
    dispatch: SelectionWorkerDispatch
    openclaw_result: OpenClawResult
    command_snapshot_path: Path


def selection_dispatch_worker_order() -> tuple[SelectionWorkerId, ...]:
    return _SELECTION_DISPATCH_WORKER_ORDER


def build_fixed_selection_dispatches(
    *,
    request: SelectRequest,
    select_workflow_run_id: str,
    selection_run_id: str,
    evidence_root: Path,
    candidate_pack_summary_md: str,
    approved_l1_materials: Mapping[SelectionWorkerId, str],
) -> tuple[SelectionWorkerDispatch, ...]:
    dispatches: list[SelectionWorkerDispatch] = []
    for index, worker_id in enumerate(_SELECTION_DISPATCH_WORKER_ORDER, start=1):
        dispatches.append(
            _build_single_dispatch(
                request=request,
                select_workflow_run_id=select_workflow_run_id,
                selection_run_id=selection_run_id,
                evidence_root=evidence_root,
                dispatch_index=index,
                worker_id=worker_id,
                candidate_pack_summary_md=candidate_pack_summary_md,
                approved_l1_materials=approved_l1_materials,
            )
        )
    return tuple(dispatches)


def execute_selection_dispatches(
    *,
    openclaw: OpenClawClient,
    dispatches: tuple[SelectionWorkerDispatch, ...],
    candidate_pack_ref: CandidatePackRef,
    profile: str = "CN_A",
    selection_artifact_root: Path | None = None,
) -> tuple[SelectionDispatchExecution, ...]:
    runs: list[SelectionDispatchExecution] = []
    for dispatch in dispatches:
        command = build_openclaw_command_for_selection_dispatch(
            dispatch=dispatch,
            candidate_pack_ref=candidate_pack_ref,
            profile=profile,
            selection_artifact_root=selection_artifact_root,
        )
        command_snapshot = _write_selection_dispatch_command_snapshot(dispatch, command)
        openclaw_result = openclaw.run_worker(command)
        evidence_guard = validate_selection_dispatch_evidence(evidence_from_dispatch(dispatch))
        if not evidence_guard.ok:
            reason = evidence_guard.reason or "selection dispatch evidence validation failed"
            if evidence_guard.paths:
                path_text = ", ".join(str(path) for path in evidence_guard.paths)
                reason = f"{reason}; paths={path_text}"
            runs.append(
                SelectionDispatchExecution(
                    dispatch=dispatch,
                    openclaw_result=OpenClawResult(
                        status="failed",
                        openclaw_run_id=openclaw_result.openclaw_run_id,
                        provider_request_id=openclaw_result.provider_request_id,
                        provider_request_id_status=openclaw_result.provider_request_id_status,
                        workspace_evidence_path=openclaw_result.workspace_evidence_path,
                        provider_request_path=openclaw_result.provider_request_path,
                        visible_tools_path=openclaw_result.visible_tools_path,
                        first_response_path=openclaw_result.first_response_path,
                        tool_calls_status=openclaw_result.tool_calls_status,
                        tool_calls_path=openclaw_result.tool_calls_path,
                        raw_output_path=openclaw_result.raw_output_path,
                        openviking_receipt_path=openclaw_result.openviking_receipt_path,
                        failure_reason=f"selection dispatch evidence validation failed: {reason}",
                    ),
                    command_snapshot_path=command_snapshot,
                )
            )
            return tuple(runs)

        runs.append(
            SelectionDispatchExecution(
                dispatch=dispatch,
                openclaw_result=openclaw_result,
                command_snapshot_path=command_snapshot,
            )
        )
    return tuple(runs)


def build_openclaw_command_for_selection_dispatch(
    *,
    dispatch: SelectionWorkerDispatch,
    candidate_pack_ref: CandidatePackRef,
    profile: str = "CN_A",
    selection_artifact_root: Path | None = None,
) -> OpenClawCommand:
    runtime_vars: dict[str, object] = dict(dispatch.prompt_runtime_vars)
    runtime_vars["select_workflow_run_id"] = _prompt_context_with_model_visible_materials(dispatch)
    runtime_vars["candidate_pack_ref"] = _serialize_candidate_pack_ref_runtime_var(candidate_pack_ref)
    if selection_artifact_root is not None:
        runtime_vars["selection_artifact_root"] = str(selection_artifact_root)
    upstream_materials = _build_upstream_material_refs(
        dispatch=dispatch,
        candidate_pack_ref=candidate_pack_ref,
    )

    return OpenClawCommand(
        agent=dispatch.worker_id.value,
        worker_id=dispatch.worker_id.value,
        profile=profile,
        stage=dispatch.stage.value,
        run_id=dispatch.select_workflow_run_id,
        call_id=dispatch.dispatch_id,
        runtime_vars=runtime_vars,  # type: ignore[arg-type]
        allowed_tools=dispatch.allowed_tools,
        upstream_materials=upstream_materials,
        openviking_read_capabilities=(),
        material_target={
            "run_id": dispatch.select_workflow_run_id,
            "call_id": dispatch.dispatch_id,
            "worker_id": dispatch.worker_id.value,
            "stage": dispatch.stage.value,
            "target_name": "report",
            "l1_uri": _selection_l1_uri(dispatch),
            "l2_prefix": _selection_l2_prefix(dispatch),
        },
        read_policy={
            "default_layer": "L1",
            "allow_l2_when": [
                "specific_number_required",
                "chart_required",
                "source_text_required",
                "conflict_resolution_required",
                "hard_gate_field_required",
            ],
            "forbid_compact_as_writing_source": True,
        },
        evidence_dir=dispatch.evidence_dir,
        stop_after_first_response=False,
        system_context_policy=SelectionSystemContextPolicy.SINGLE_WORKER_MINIMAL.value,
        initial_tool_choice=None,
    )


def _build_single_dispatch(
    *,
    request: SelectRequest,
    select_workflow_run_id: str,
    selection_run_id: str,
    evidence_root: Path,
    dispatch_index: int,
    worker_id: SelectionWorkerId,
    candidate_pack_summary_md: str,
    approved_l1_materials: Mapping[SelectionWorkerId, str],
) -> SelectionWorkerDispatch:
    stage = _SELECTION_DISPATCH_STAGE_BY_WORKER[worker_id]
    dispatch_id = f"{select_workflow_run_id}-dispatch-{dispatch_index:02d}-{worker_id.value}"
    trade_date = request.trade_date
    if trade_date is None or not trade_date.strip():
        raise ValueError("request.trade_date is required for selection dispatch runtime vars")
    model_materials = _model_visible_materials_for_worker(
        worker_id=worker_id,
        candidate_pack_summary_md=candidate_pack_summary_md,
        approved_l1_materials=approved_l1_materials,
    )
    return SelectionWorkerDispatch(
        dispatch_id=dispatch_id,
        select_workflow_run_id=select_workflow_run_id,
        worker_id=worker_id,
        stage=stage,
        allowed_tools=_SELECTION_DISPATCH_TOOLS_BY_WORKER[worker_id],
        prompt_runtime_vars={
            "market": request.market.value,
            "profile": request.profile.value,
            "trade_date": trade_date,
            "selection_run_id": selection_run_id,
            "select_workflow_run_id": select_workflow_run_id,
        },
        model_visible_materials=model_materials,
        evidence_dir=evidence_root / dispatch_id,
        provider_payload_ref=None,
    )


def _model_visible_materials_for_worker(
    *,
    worker_id: SelectionWorkerId,
    candidate_pack_summary_md: str,
    approved_l1_materials: Mapping[SelectionWorkerId, str],
) -> tuple[str, ...]:
    if not candidate_pack_summary_md.strip():
        raise ValueError("candidate_pack_summary_md must be non-empty")

    if worker_id == SelectionWorkerId.STRATEGIST:
        return (_RUNTIME_CONTEXT_PLACEHOLDER,)
    if worker_id == SelectionWorkerId.SKEPTIC:
        strategist_l1 = _required_upstream_l1(approved_l1_materials, SelectionWorkerId.STRATEGIST)
        return (strategist_l1,)
    if worker_id == SelectionWorkerId.MANAGER:
        strategist_l1 = _required_upstream_l1(approved_l1_materials, SelectionWorkerId.STRATEGIST)
        skeptic_l1 = _required_upstream_l1(approved_l1_materials, SelectionWorkerId.SKEPTIC)
        return (strategist_l1, skeptic_l1, candidate_pack_summary_md)
    if worker_id == SelectionWorkerId.PORTFOLIO_MANAGER:
        strategist_l1 = _required_upstream_l1(approved_l1_materials, SelectionWorkerId.STRATEGIST)
        skeptic_l1 = _required_upstream_l1(approved_l1_materials, SelectionWorkerId.SKEPTIC)
        manager_l1 = _required_upstream_l1(approved_l1_materials, SelectionWorkerId.MANAGER)
        return (manager_l1, strategist_l1, skeptic_l1, candidate_pack_summary_md)
    raise ValueError(f"unsupported selection worker: {worker_id}")


def _required_upstream_l1(materials: Mapping[SelectionWorkerId, str], worker_id: SelectionWorkerId) -> str:
    value = materials.get(worker_id)
    if value is None or not value.strip():
        raise ValueError(f"approved_l1_materials missing required worker output: {worker_id.value}")
    return value


def _selection_l1_uri(dispatch: SelectionWorkerDispatch) -> str:
    return (
        "viking://resources/workflow/"
        f"{dispatch.select_workflow_run_id}/"
        f"{dispatch.stage.value}/"
        f"{dispatch.worker_id.value}/"
        f"{dispatch.dispatch_id}/report.md"
    )


def _selection_l2_prefix(dispatch: SelectionWorkerDispatch) -> str:
    return (
        "viking://resources/workflow/"
        f"{dispatch.select_workflow_run_id}/"
        f"{dispatch.stage.value}/"
        f"{dispatch.worker_id.value}/"
        f"{dispatch.dispatch_id}/evidence/"
    )


def _write_selection_dispatch_command_snapshot(
    dispatch: SelectionWorkerDispatch,
    command: OpenClawCommand,
) -> Path:
    payload = serialize_openclaw_command_payload(command)
    command_snapshot = dispatch.evidence_dir / "selection-dispatch-command.json"
    command_snapshot.parent.mkdir(parents=True, exist_ok=True)
    command_snapshot.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return command_snapshot


def _serialize_candidate_pack_ref_runtime_var(candidate_pack_ref: CandidatePackRef) -> str:
    payload = {
        "selection_run_id": candidate_pack_ref.selection_run_id,
        "material_id": candidate_pack_ref.material_id,
        "l1_uri": candidate_pack_ref.l1_uri,
        "content_sha256": candidate_pack_ref.content_sha256,
        "manifest_ref": candidate_pack_ref.manifest_ref,
        "approved_at": candidate_pack_ref.approved_at,
        "expires_at": candidate_pack_ref.expires_at,
        "pack_summary_ref": candidate_pack_ref.pack_summary_ref,
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _prompt_context_with_model_visible_materials(dispatch: SelectionWorkerDispatch) -> str:
    run_id = dispatch.select_workflow_run_id
    if dispatch.worker_id not in {
        SelectionWorkerId.SKEPTIC,
        SelectionWorkerId.MANAGER,
        SelectionWorkerId.PORTFOLIO_MANAGER,
    }:
        return run_id

    sections = _prompt_material_sections(dispatch)
    lines = [run_id, "", "[模型可见已批准材料]"]
    for marker, material in sections:
        lines.append(f"【{marker}】")
        lines.append(material.strip())
    return "\n".join(lines).strip()


def _prompt_material_sections(dispatch: SelectionWorkerDispatch) -> tuple[tuple[str, str], ...]:
    materials = dispatch.model_visible_materials
    if dispatch.worker_id == SelectionWorkerId.SKEPTIC:
        return tuple(zip(_SKEPTIC_PROMPT_MATERIAL_MARKERS, materials, strict=True))
    if dispatch.worker_id == SelectionWorkerId.MANAGER:
        return tuple(zip(_MANAGER_PROMPT_MATERIAL_MARKERS, materials, strict=True))
    if dispatch.worker_id == SelectionWorkerId.PORTFOLIO_MANAGER:
        return tuple(zip(_PORTFOLIO_MANAGER_PROMPT_MATERIAL_MARKERS, materials, strict=True))
    return ()


def _build_upstream_material_refs(
    *,
    dispatch: SelectionWorkerDispatch,
    candidate_pack_ref: CandidatePackRef,
) -> tuple[dict[str, str | None], ...]:
    upstream_sources = _upstream_material_sources(dispatch=dispatch, candidate_pack_ref=candidate_pack_ref)
    if not upstream_sources:
        return ()

    refs: list[dict[str, str | None]] = []
    for index, (material_body, source_worker_id, source_stage, source_call_id, source_l1_uri) in enumerate(
        upstream_sources,
        start=1,
    ):
        refs.append(
            {
                "material_id": f"{dispatch.select_workflow_run_id}:{dispatch.dispatch_id}:model-visible:{index:02d}",
                "capability_id": f"selection-model-visible-{dispatch.dispatch_id}-{index:02d}",
                "worker_id": source_worker_id,
                "stage": source_stage,
                "l1_uri": source_l1_uri,
                "l1_sha256": sha256(material_body.encode("utf-8")).hexdigest(),
                "l2_index_uri": None,
                "l2_allowed_prefix": None,
                "call_id": source_call_id,
            }
        )
    return tuple(refs)


def _upstream_material_sources(
    *,
    dispatch: SelectionWorkerDispatch,
    candidate_pack_ref: CandidatePackRef,
) -> tuple[tuple[str, str, str, str, str], ...]:
    materials = dispatch.model_visible_materials
    if dispatch.worker_id == SelectionWorkerId.STRATEGIST:
        return ()
    if dispatch.worker_id == SelectionWorkerId.SKEPTIC:
        strategist_dispatch_id = _dispatch_id_for_worker(dispatch.select_workflow_run_id, SelectionWorkerId.STRATEGIST)
        return (
            (
                materials[0],
                SelectionWorkerId.STRATEGIST.value,
                _SELECTION_DISPATCH_STAGE_BY_WORKER[SelectionWorkerId.STRATEGIST].value,
                strategist_dispatch_id,
                _selection_l1_uri_for_source(
                    select_workflow_run_id=dispatch.select_workflow_run_id,
                    worker_id=SelectionWorkerId.STRATEGIST,
                    dispatch_id=strategist_dispatch_id,
                ),
            ),
        )
    if dispatch.worker_id == SelectionWorkerId.MANAGER:
        strategist_dispatch_id = _dispatch_id_for_worker(dispatch.select_workflow_run_id, SelectionWorkerId.STRATEGIST)
        skeptic_dispatch_id = _dispatch_id_for_worker(dispatch.select_workflow_run_id, SelectionWorkerId.SKEPTIC)
        return (
            (
                materials[0],
                SelectionWorkerId.STRATEGIST.value,
                _SELECTION_DISPATCH_STAGE_BY_WORKER[SelectionWorkerId.STRATEGIST].value,
                strategist_dispatch_id,
                _selection_l1_uri_for_source(
                    select_workflow_run_id=dispatch.select_workflow_run_id,
                    worker_id=SelectionWorkerId.STRATEGIST,
                    dispatch_id=strategist_dispatch_id,
                ),
            ),
            (
                materials[1],
                SelectionWorkerId.SKEPTIC.value,
                _SELECTION_DISPATCH_STAGE_BY_WORKER[SelectionWorkerId.SKEPTIC].value,
                skeptic_dispatch_id,
                _selection_l1_uri_for_source(
                    select_workflow_run_id=dispatch.select_workflow_run_id,
                    worker_id=SelectionWorkerId.SKEPTIC,
                    dispatch_id=skeptic_dispatch_id,
                ),
            ),
            (
                materials[2],
                "selection_candidate_pack_summary",
                dispatch.stage.value,
                f"{dispatch.select_workflow_run_id}-candidate-pack-summary",
                candidate_pack_ref.pack_summary_ref,
            ),
        )
    if dispatch.worker_id == SelectionWorkerId.PORTFOLIO_MANAGER:
        manager_dispatch_id = _dispatch_id_for_worker(dispatch.select_workflow_run_id, SelectionWorkerId.MANAGER)
        strategist_dispatch_id = _dispatch_id_for_worker(dispatch.select_workflow_run_id, SelectionWorkerId.STRATEGIST)
        skeptic_dispatch_id = _dispatch_id_for_worker(dispatch.select_workflow_run_id, SelectionWorkerId.SKEPTIC)
        return (
            (
                materials[0],
                SelectionWorkerId.MANAGER.value,
                _SELECTION_DISPATCH_STAGE_BY_WORKER[SelectionWorkerId.MANAGER].value,
                manager_dispatch_id,
                _selection_l1_uri_for_source(
                    select_workflow_run_id=dispatch.select_workflow_run_id,
                    worker_id=SelectionWorkerId.MANAGER,
                    dispatch_id=manager_dispatch_id,
                ),
            ),
            (
                materials[1],
                SelectionWorkerId.STRATEGIST.value,
                _SELECTION_DISPATCH_STAGE_BY_WORKER[SelectionWorkerId.STRATEGIST].value,
                strategist_dispatch_id,
                _selection_l1_uri_for_source(
                    select_workflow_run_id=dispatch.select_workflow_run_id,
                    worker_id=SelectionWorkerId.STRATEGIST,
                    dispatch_id=strategist_dispatch_id,
                ),
            ),
            (
                materials[2],
                SelectionWorkerId.SKEPTIC.value,
                _SELECTION_DISPATCH_STAGE_BY_WORKER[SelectionWorkerId.SKEPTIC].value,
                skeptic_dispatch_id,
                _selection_l1_uri_for_source(
                    select_workflow_run_id=dispatch.select_workflow_run_id,
                    worker_id=SelectionWorkerId.SKEPTIC,
                    dispatch_id=skeptic_dispatch_id,
                ),
            ),
            (
                materials[3],
                "selection_candidate_pack_summary",
                dispatch.stage.value,
                f"{dispatch.select_workflow_run_id}-candidate-pack-summary",
                candidate_pack_ref.pack_summary_ref,
            ),
        )
    raise ValueError(f"unsupported selection worker: {dispatch.worker_id.value}")


def _dispatch_id_for_worker(select_workflow_run_id: str, worker_id: SelectionWorkerId) -> str:
    for index, current_worker in enumerate(_SELECTION_DISPATCH_WORKER_ORDER, start=1):
        if current_worker == worker_id:
            return f"{select_workflow_run_id}-dispatch-{index:02d}-{worker_id.value}"
    raise ValueError(f"unsupported selection worker: {worker_id.value}")


def _selection_l1_uri_for_source(
    *,
    select_workflow_run_id: str,
    worker_id: SelectionWorkerId,
    dispatch_id: str,
) -> str:
    stage = _SELECTION_DISPATCH_STAGE_BY_WORKER[worker_id]
    return (
        "viking://resources/workflow/"
        f"{select_workflow_run_id}/"
        f"{stage.value}/"
        f"{worker_id.value}/"
        f"{dispatch_id}/report.md"
    )
