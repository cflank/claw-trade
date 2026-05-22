from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from claw_trade.artifacts.manifest import ApprovedManifest
from claw_trade.artifacts.refs import MaterialReadRef, OpenVikingReadCapability
from claw_trade.runtime import request_builder
from claw_trade.runtime.request_builder import build_request_context, build_worker_call_from_context
from claw_trade.workflow.models import (
    RunRequest,
    RunStatus,
    Stage,
    WorkflowEntryPoint,
    WorkflowState,
)


def test_build_request_context_rejects_unapproved_profile(tmp_path: Path) -> None:
    state = _state(tmp_path=tmp_path, profile="MARS")
    result = build_request_context(
        state=state,
        worker_id="market_analyst",
        stage=Stage.FRONTLINE,
        manifest=ApprovedManifest.empty(),
    )
    assert result.ok is False
    assert result.failure is not None
    assert result.failure.category == "config_blocked"
    assert "unknown profile" in result.failure.reason


def test_build_request_context_rejects_worker_stage_mismatch(tmp_path: Path) -> None:
    state = _state(tmp_path=tmp_path, profile="US")
    result = build_request_context(
        state=state,
        worker_id="market_analyst",
        stage=Stage.RISK_DEBATE,
        manifest=ApprovedManifest.empty(),
    )
    assert result.ok is False
    assert result.failure is not None
    assert result.failure.category == "config_blocked"
    assert "阶段不匹配" in result.failure.reason


def test_build_request_context_allows_empty_allowed_tools_for_pure_prompt_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _state(tmp_path=tmp_path, profile="US")
    monkeypatch.setattr(request_builder, "resolve_tools", lambda policy, registry: ())
    result = build_request_context(
        state=state,
        worker_id="research_manager",
        stage=Stage.INVESTMENT_DECISION,
        manifest=ApprovedManifest.empty(),
    )
    assert result.ok is False
    assert result.failure is not None
    assert result.failure.category == "config_blocked"
    assert "worker 输入材料缺失" in result.failure.reason


def test_build_request_context_rejects_manifest_capability_mismatch(tmp_path: Path) -> None:
    state = _state(tmp_path=tmp_path, profile="US")
    bad_manifest = _BadManifest(
        refs=(
            MaterialReadRef(
                material_id="mat-1",
                capability_id="cap-1",
                worker_id="market_analyst",
                stage=Stage.FRONTLINE,
                l1_uri="viking://resources/workflow/run-1/frontline/market_analyst/call-1/report.md",
                l1_sha256="sha-l1",
                l2_index_uri=None,
                l2_allowed_prefix=None,
                call_id="call-1",
            ),
        ),
        capabilities=(
            OpenVikingReadCapability(
                capability_id="cap-mismatch",
                material_id="mat-1",
                allowed_l1_uri="viking://resources/workflow/run-1/frontline/market_analyst/call-1/report.md",
                allowed_l1_sha256="sha-l1",
                allowed_l2_prefix=None,
                manifest_entry_sha256="sha-manifest",
            ),
        ),
    )

    result = build_request_context(
        state=state,
        worker_id="bull_researcher",
        stage=Stage.INVESTMENT_DEBATE,
        manifest=bad_manifest,  # type: ignore[arg-type]
    )
    assert result.ok is False
    assert result.failure is not None
    assert result.failure.category == "config_blocked"
    assert "manifest capability 缺失" in result.failure.reason


def test_build_worker_call_from_context_uses_call_root_evidence_dir(tmp_path: Path) -> None:
    state = _state(tmp_path=tmp_path, profile="US")
    context_result = build_request_context(
        state=state,
        worker_id="market_analyst",
        stage=Stage.FRONTLINE,
        manifest=ApprovedManifest.empty(),
    )
    assert context_result.ok is True
    assert context_result.context is not None

    call_result = build_worker_call_from_context(context_result.context)
    assert call_result.ok is True
    assert call_result.call is not None
    call = call_result.call
    assert call.evidence_dir == state.run_dir / "calls" / call.call_id
    assert call.evidence_dir.name == call.call_id
    assert "/evidence" not in str(call.evidence_dir).replace("\\", "/")
    assert call.system_context_policy == "openclaw_default"


def test_build_worker_call_uses_minimal_system_context_only_for_report_command(
    tmp_path: Path,
) -> None:
    state = _state(tmp_path=tmp_path, profile="US", entry_point=WorkflowEntryPoint.REPORT_COMMAND)
    context_result = build_request_context(
        state=state,
        worker_id="market_analyst",
        stage=Stage.FRONTLINE,
        manifest=ApprovedManifest.empty(),
    )
    assert context_result.ok is True
    assert context_result.context is not None

    call_result = build_worker_call_from_context(context_result.context)
    assert call_result.ok is True
    assert call_result.call is not None
    call = call_result.call
    assert call.system_context_policy == "single_worker_minimal"


def test_build_request_context_uses_repo_agents_root_when_cwd_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _state(tmp_path=tmp_path, profile="US")
    monkeypatch.chdir(tmp_path)
    result = build_request_context(
        state=state,
        worker_id="market_analyst",
        stage=Stage.FRONTLINE,
        manifest=ApprovedManifest.empty(),
    )
    assert result.ok is True
    assert result.context is not None


def _state(
    *,
    tmp_path: Path,
    profile: str,
    entry_point: WorkflowEntryPoint = WorkflowEntryPoint.GENERIC,
) -> WorkflowState:
    run_id = "run-1"
    request = RunRequest(
        ticker="AAPL",
        company_name="Apple",
        market="US",
        profile=profile,
        currency="USD",
        currency_symbol="$",
        current_date="2026-05-03",
        start_date="2026-01-01",
        end_date="2026-05-03",
        entry_point=entry_point,
    )
    return WorkflowState(
        run_id=run_id,
        request=request,
        status=RunStatus.CREATED,
        run_dir=tmp_path / run_id,
        openviking_namespace=f"workflow/{run_id}",
        created_at="2026-05-03T12:00:00Z",
        updated_at="2026-05-03T12:00:00Z",
    )


@dataclass(frozen=True)
class _BadManifest:
    refs: tuple[MaterialReadRef, ...]
    capabilities: tuple[OpenVikingReadCapability, ...]

    def for_downstream_stage(self, stage: Stage, run_id: str | None = None) -> tuple[MaterialReadRef, ...]:
        return self.refs

    def for_worker_call(
        self,
        stage: Stage,
        worker_id: str,
        run_id: str | None = None,
        turn_index: int = 0,
        market: str | None = None,
    ) -> tuple[MaterialReadRef, ...]:
        return self.refs

    def capabilities_for_downstream_stage(
        self,
        stage: Stage,
        run_id: str | None = None,
    ) -> tuple[OpenVikingReadCapability, ...]:
        return self.capabilities

    def capabilities_for_worker_call(
        self,
        stage: Stage,
        worker_id: str,
        run_id: str | None = None,
        turn_index: int = 0,
        market: str | None = None,
    ) -> tuple[OpenVikingReadCapability, ...]:
        return self.capabilities
