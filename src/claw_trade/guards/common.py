from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from claw_trade.artifacts.manifest import ApprovedMaterial
    from claw_trade.workflow.models import FailureRecord


@dataclass(frozen=True)
class GuardResult:
    ok: bool
    category: str
    reason: str | None
    paths: tuple[Path, ...]
    early_stop: bool = False

    @classmethod
    def passed(cls, category: str = "ok") -> GuardResult:
        return cls(ok=True, category=category, reason=None, paths=(), early_stop=False)

    @classmethod
    def failed(
        cls,
        category: str,
        reason: str,
        paths: tuple[Path, ...],
        early_stop: bool = False,
    ) -> GuardResult:
        return cls(ok=False, category=category, reason=reason, paths=paths, early_stop=early_stop)


@dataclass(frozen=True)
class ApprovalResult:
    ok: bool
    material: ApprovedMaterial | None
    category: str | None
    reason: str | None
    paths: tuple[Path, ...]

    @classmethod
    def ok_result(cls, material: ApprovedMaterial) -> ApprovalResult:
        return cls(ok=True, material=material, category=None, reason=None, paths=())

    @classmethod
    def failed(cls, category: str, reason: str, paths: tuple[Path, ...]) -> ApprovalResult:
        return cls(ok=False, material=None, category=category, reason=reason, paths=paths)


@dataclass(frozen=True)
class BootResult:
    ok: bool
    category: str | None
    reason: str | None
    failure: FailureRecord | None

    @classmethod
    def ok_result(cls) -> BootResult:
        return cls(ok=True, category=None, reason=None, failure=None)

    @classmethod
    def blocked(cls, category: str, reason: str) -> BootResult:
        return cls(ok=False, category=category, reason=reason, failure=None)


def guard_passed(category: str = "ok") -> GuardResult:
    return GuardResult.passed(category=category)


def guard_failed(
    category: str,
    reason: str,
    paths: tuple[Path, ...],
    early_stop: bool = False,
) -> GuardResult:
    return GuardResult.failed(category=category, reason=reason, paths=paths, early_stop=early_stop)


EARLY_STOP_CATEGORIES = frozenset(
    {
        # provider / workspace 证据链不可信时必须立刻停，继续 collect-first 会污染后续归因。
        "provider_evidence_untrusted",
        "provider_request",
        "workspace_evidence",
        "visible_tools",
        "tool_calls",
        # OpenViking 与材料完整性边界失真时必须停，继续执行会扩大越权写读并污染证据链。
        "openviking_integrity",
        "openviking_runtime_reads",
        "openviking_receipt",
        "l1_l2",
        # 材料流/所有权/运行边界越权时必须停，不能让 Python、OpenClaw、OpenViking 越权推进。
        "artifact_flow_overreach",
        "python_overreach",
        "openclaw_overreach",
        "openviking_overreach",
        "architecture_boundary",
        # 投资结论真实性和导出真实性失败必须停，不能降级成 warning。
        "claim",
        "claims",
        "export_truthfulness",
        # 假成功路径/安全与数据损坏/人类决策缺失都必须停，继续跑会产生不可接受风险。
        "human_decision_required",
        "fake_success_path",
        "security_or_data_loss",
    }
)


def should_early_stop(failure: FailureRecord) -> bool:
    if failure.early_stop:
        return True
    if failure.category in EARLY_STOP_CATEGORIES:
        return True
    if failure.human_action_required and failure.human_action_required.strip():
        return True
    return False


def combine_guard_results(checks: tuple[GuardResult, ...]) -> GuardResult:
    failed = tuple(result for result in checks if not result.ok)
    if not failed:
        return guard_passed()
    first = failed[0]
    merged_paths: list[Path] = []
    for result in failed:
        for path in result.paths:
            if path not in merged_paths:
                merged_paths.append(path)
    return guard_failed(
        category=first.category,
        reason=first.reason or "guard failed",
        paths=tuple(merged_paths),
        early_stop=any(result.early_stop for result in failed),
    )
