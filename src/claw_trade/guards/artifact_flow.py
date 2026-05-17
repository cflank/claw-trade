from __future__ import annotations

from claw_trade.artifacts.manifest import ApprovedManifest
from claw_trade.guards.common import GuardResult, guard_failed, guard_passed
from claw_trade.workflow.models import Stage, WorkerCall


def validate_artifact_flow(call: WorkerCall, manifest: ApprovedManifest) -> GuardResult:
    # Guard source: human approval in 2026-05-16 chat; tests/contracts/test_artifact_flow_guard.py.
    if call.stage == Stage.FRONTLINE:
        if call.upstream_materials or call.openviking_read_capabilities:
            return guard_failed(
                category="artifact_flow_overreach",
                reason="frontline 不允许携带 upstream_materials/openviking_read_capabilities",
                paths=(call.evidence_dir / "call.json",),
                early_stop=True,
            )
        return guard_passed(category="artifact_flow")
    try:
        expected_refs = manifest.for_worker_call(
            call.stage,
            worker_id=call.worker_id,
            run_id=call.run_id,
            turn_index=call.turn_index,
        )
        expected_caps = manifest.capabilities_for_worker_call(
            call.stage,
            worker_id=call.worker_id,
            run_id=call.run_id,
            turn_index=call.turn_index,
        )
    except Exception as exc:
        return guard_failed(
            category="artifact_flow_overreach",
            reason=f"manifest 下游引用加载失败: {exc}",
            paths=(call.evidence_dir / "call.json",),
            early_stop=True,
        )
    if tuple(call.upstream_materials) != tuple(expected_refs):
        return guard_failed(
            category="artifact_flow_overreach",
            reason="upstream_materials 与 approved manifest 不一致",
            paths=(call.evidence_dir / "call.json",),
            early_stop=True,
        )
    if tuple(call.openviking_read_capabilities) != tuple(expected_caps):
        return guard_failed(
            category="artifact_flow_overreach",
            reason="openviking_read_capabilities 与 approved manifest 不一致",
            paths=(call.evidence_dir / "call.json",),
            early_stop=True,
        )
    return guard_passed(category="artifact_flow")


__all__ = ["validate_artifact_flow"]
