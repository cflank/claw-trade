from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

from claw_trade.runtime.openclaw_client import OpenClawClient, ProbeResult
from claw_trade.selection.dispatch import (
    build_fixed_selection_dispatches,
    execute_selection_dispatches,
    selection_dispatch_worker_order,
)
from claw_trade.selection.models import (
    CandidateCacheRef,
    SelectionMarket,
    SelectionProfile,
    SelectionSystemContextPolicy,
    SelectionWorkerId,
    SelectRequest,
)
from claw_trade.workflow.models import WorkflowEntryPoint


class _FakeOpenClawRunner:
    # SEL-10 说明：这里是 test double，只用于状态机/控制流与 payload 绑定证明，
    # 不可作为 runtime/live/provider payload 最终验收依据（SEL-11 负责 live focused run）。
    def __init__(self) -> None:
        self.payloads: list[dict[str, object]] = []

    def probe(self) -> ProbeResult:
        return ProbeResult.passed()

    def run_worker(self, payload: dict[str, object]) -> dict[str, object]:
        self.payloads.append(dict(payload))
        evidence_dir = Path(str(payload["evidence_dir"]))
        evidence_dir.mkdir(parents=True, exist_ok=True)

        provider_request_path = evidence_dir / "provider-request.json"
        visible_tools_path = evidence_dir / "visible-tools.json"
        workspace_evidence_path = evidence_dir / "workspace-evidence.json"
        first_response_path = evidence_dir / "first-response.json"
        tool_calls_path = evidence_dir / "tool-calls.json"
        raw_output_path = evidence_dir / "raw-output.md"
        receipt_path = evidence_dir / "openviking-receipt.json"

        runtime_markers = {
            "run_id": payload["run_id"],
            "call_id": payload["call_id"],
            "worker_id": payload["worker_id"],
            "stage": payload["stage"],
            "profile": payload["profile"],
            "openclaw_run_id": f"oc-{payload['call_id']}",
        }
        tools = [
            {"type": "function", "function": {"name": tool_name}}
            for tool_name in payload.get("allowed_tools", ())
        ]
        runtime_vars = payload.get("runtime_vars")
        prompt_text = "selection test prompt"
        if isinstance(runtime_vars, dict):
            select_context = runtime_vars.get("selection_prompt_context")
            if isinstance(select_context, str) and select_context.strip():
                prompt_text = select_context
        provider_request = {
            "source": "provider_request_capture",
            "payload": {
                "messages": [{"role": "user", "content": prompt_text}],
                "tools": tools,
            },
            "runtime_markers": runtime_markers,
        }
        visible_tools = {
            "source": "provider_request",
            "provider_request_path": str(provider_request_path),
            "tools": tools,
            "runtime_markers": runtime_markers,
        }
        workspace_evidence = {
            "source": "openclaw_workspace_loader",
            "run_id": payload["run_id"],
            "call_id": payload["call_id"],
            "worker_id": payload["worker_id"],
            "stage": payload["stage"],
            "profile": payload["profile"],
            "openclaw_run_id": runtime_markers["openclaw_run_id"],
            "runtime_markers": runtime_markers,
        }
        provider_request_path.write_text(json.dumps(provider_request), encoding="utf-8")
        visible_tools_path.write_text(json.dumps(visible_tools), encoding="utf-8")
        workspace_evidence_path.write_text(json.dumps(workspace_evidence), encoding="utf-8")
        first_response_path.write_text(
            json.dumps({"source": "openclaw_first_model_event", "runtime_markers": runtime_markers}),
            encoding="utf-8",
        )
        tool_calls_path.write_text(
            json.dumps({"source": "model_tool_events", "status": "recorded", "calls": []}),
            encoding="utf-8",
        )
        raw_output_path.write_text("# fake raw output", encoding="utf-8")
        receipt_path.write_text(json.dumps({"receipt_id": f"receipt-{payload['call_id']}"}), encoding="utf-8")

        return {
            "status": "succeeded",
            "openclaw_run_id": runtime_markers["openclaw_run_id"],
            "provider_request_id": None,
            "provider_request_id_status": "not_available",
            "workspace_evidence_path": str(workspace_evidence_path),
            "provider_request_path": str(provider_request_path),
            "visible_tools_path": str(visible_tools_path),
            "first_response_path": str(first_response_path),
            "tool_calls_status": "recorded",
            "tool_calls_path": str(tool_calls_path),
            "raw_output_path": str(raw_output_path),
            "openviking_receipt_path": str(receipt_path),
            "failure_reason": None,
        }


class _CorruptingOpenClawRunner(_FakeOpenClawRunner):
    def __init__(self, *, corrupt_after_call_count: int) -> None:
        super().__init__()
        self._corrupt_after_call_count = corrupt_after_call_count

    def run_worker(self, payload: dict[str, object]) -> dict[str, object]:
        result = super().run_worker(payload)
        if len(self.payloads) == self._corrupt_after_call_count:
            evidence_dir = Path(str(payload["evidence_dir"]))
            visible_tools_path = evidence_dir / "visible-tools.json"
            visible_tools = json.loads(visible_tools_path.read_text(encoding="utf-8"))
            visible_tools["source"] = "invalid_source"
            visible_tools_path.write_text(json.dumps(visible_tools), encoding="utf-8")
        return result


def test_selection_dispatch_order_is_fixed_and_not_model_decided(tmp_path: Path) -> None:
    dispatches = _dispatches(tmp_path)

    assert tuple(item.worker_id for item in dispatches) == selection_dispatch_worker_order()
    assert [item.stage.value for item in dispatches] == [
        "selection_review",
        "selection_review",
        "selection_decision",
        "selection_portfolio_decision",
    ]
    assert [item.allowed_tools for item in dispatches] == [
        ("claw_get_selection_candidate_cache",),
        ("claw_get_selection_candidate_cache",),
        (),
        (),
    ]


def test_selection_dispatch_executes_four_openclaw_single_worker_turns(tmp_path: Path) -> None:
    runner = _FakeOpenClawRunner()
    openclaw = OpenClawClient(runner=runner)
    dispatches = _dispatches(tmp_path)
    executions = execute_selection_dispatches(
        openclaw=openclaw,
        dispatches=dispatches,
        candidate_cache_ref=_candidate_cache_ref(),
        profile="CN_A",
        selection_artifact_root=tmp_path / "selection-artifacts",
    )

    assert len(executions) == 4
    assert len(runner.payloads) == 4
    for dispatch, execution, payload in zip(dispatches, executions, runner.payloads, strict=True):
        assert execution.dispatch.dispatch_id == dispatch.dispatch_id
        assert execution.openclaw_result.status == "succeeded"
        assert execution.command_snapshot_path.is_file()
        assert payload["run_id"] == dispatch.select_workflow_run_id
        assert payload["call_id"] == dispatch.dispatch_id
        assert payload["worker_id"] == dispatch.worker_id.value
        assert payload["stage"] == dispatch.stage.value
        assert payload["allowed_tools"] == list(dispatch.allowed_tools)
        assert payload["system_context_policy"] == SelectionSystemContextPolicy.SINGLE_WORKER_MINIMAL.value
        assert payload["stop_after_first_response"] is False
        runtime_vars = payload["runtime_vars"]
        assert isinstance(runtime_vars, dict)
        candidate_cache_ref = runtime_vars.get("candidate_cache_ref")
        assert isinstance(candidate_cache_ref, str) and candidate_cache_ref
        candidate_cache_payload = json.loads(candidate_cache_ref)
        assert candidate_cache_payload["selection_run_id"] == "sel-run-07"
        material_target = payload["material_target"]
        assert isinstance(material_target, dict)
        for unexpected_field in ("turn_index", "round_index", "role_turn_index"):
            assert unexpected_field not in material_target


def test_selection_manager_pm_command_include_model_visible_upstream_material_refs(tmp_path: Path) -> None:
    runner = _FakeOpenClawRunner()
    openclaw = OpenClawClient(runner=runner)
    dispatches = _dispatches(tmp_path)
    executions = execute_selection_dispatches(
        openclaw=openclaw,
        dispatches=dispatches,
        candidate_cache_ref=_candidate_cache_ref(),
        profile="CN_A",
        selection_artifact_root=tmp_path / "selection-artifacts",
    )
    by_worker_execution = {item.dispatch.worker_id: item for item in executions}

    manager_snapshot = json.loads(
        by_worker_execution[SelectionWorkerId.MANAGER].command_snapshot_path.read_text(encoding="utf-8")
    )
    pm_snapshot = json.loads(
        by_worker_execution[SelectionWorkerId.PORTFOLIO_MANAGER].command_snapshot_path.read_text(encoding="utf-8")
    )

    manager_upstream = manager_snapshot["upstream_materials"]
    pm_upstream = pm_snapshot["upstream_materials"]
    assert [item["worker_id"] for item in manager_upstream] == [
        SelectionWorkerId.STRATEGIST.value,
        SelectionWorkerId.SKEPTIC.value,
        "selection_candidate_cache_summary",
    ]
    assert [item["worker_id"] for item in pm_upstream] == [
        SelectionWorkerId.MANAGER.value,
        SelectionWorkerId.STRATEGIST.value,
        SelectionWorkerId.SKEPTIC.value,
        "selection_candidate_cache_summary",
    ]

    manager_dispatch = {item.worker_id: item for item in dispatches}[SelectionWorkerId.MANAGER]
    pm_dispatch = {item.worker_id: item for item in dispatches}[SelectionWorkerId.PORTFOLIO_MANAGER]
    assert manager_upstream[0]["l1_sha256"] == _sha256(manager_dispatch.model_visible_materials[0])
    assert manager_upstream[1]["l1_sha256"] == _sha256(manager_dispatch.model_visible_materials[1])
    assert manager_upstream[2]["l1_sha256"] == _sha256(manager_dispatch.model_visible_materials[2])
    assert manager_upstream[2]["l1_uri"] == _candidate_cache_ref().cache_summary_ref

    assert pm_upstream[0]["l1_sha256"] == _sha256(pm_dispatch.model_visible_materials[0])
    assert pm_upstream[1]["l1_sha256"] == _sha256(pm_dispatch.model_visible_materials[1])
    assert pm_upstream[2]["l1_sha256"] == _sha256(pm_dispatch.model_visible_materials[2])
    assert pm_upstream[3]["l1_sha256"] == _sha256(pm_dispatch.model_visible_materials[3])
    assert pm_upstream[3]["l1_uri"] == _candidate_cache_ref().cache_summary_ref

    assert manager_snapshot["runtime_vars"]["select_workflow_run_id"] == manager_dispatch.select_workflow_run_id
    assert pm_snapshot["runtime_vars"]["select_workflow_run_id"] == pm_dispatch.select_workflow_run_id
    manager_prompt_context = manager_snapshot["runtime_vars"]["selection_prompt_context"]
    pm_prompt_context = pm_snapshot["runtime_vars"]["selection_prompt_context"]
    assert "[模型可见已批准材料]" in manager_prompt_context
    assert "【approved_strategist_l1】" in manager_prompt_context
    assert "【approved_skeptic_l1】" in manager_prompt_context
    assert "【candidate_cache_summary】" in manager_prompt_context
    assert manager_dispatch.model_visible_materials[0] in manager_prompt_context
    assert manager_dispatch.model_visible_materials[1] in manager_prompt_context
    assert manager_dispatch.model_visible_materials[2] in manager_prompt_context

    assert "[模型可见已批准材料]" in pm_prompt_context
    assert "【approved_manager_l1】" in pm_prompt_context
    assert "【approved_strategist_l1】" in pm_prompt_context
    assert "【approved_skeptic_l1】" in pm_prompt_context
    assert "【candidate_cache_summary】" in pm_prompt_context
    assert pm_dispatch.model_visible_materials[0] in pm_prompt_context
    assert pm_dispatch.model_visible_materials[1] in pm_prompt_context
    assert pm_dispatch.model_visible_materials[2] in pm_prompt_context
    assert pm_dispatch.model_visible_materials[3] in pm_prompt_context


def test_selection_manager_pm_prompt_context_prepends_candidate_checklist(tmp_path: Path) -> None:
    runner = _FakeOpenClawRunner()
    openclaw = OpenClawClient(runner=runner)
    dispatches = build_fixed_selection_dispatches(
        request=SelectRequest(
            request_id="sel-07-checklist-request",
            market=SelectionMarket.CN_A,
            profile=SelectionProfile.CN_A,
            trade_date="2026-05-26",
            user_id="u-1",
            created_at="2026-05-26T10:00:00+00:00",
            entry_point=WorkflowEntryPoint.SELECT_COMMAND,
            system_context_policy=SelectionSystemContextPolicy.SINGLE_WORKER_MINIMAL,
        ),
        select_workflow_run_id="sel-wf-checklist",
        selection_run_id="sel-run-checklist",
        evidence_root=tmp_path / "evidence",
        candidate_cache_summary_md="\n".join(
            [
                "## 候选事实表",
                "| 排名 | 股票代码 | 股票名称 | 行业 | 总分 |",
                "| --- | --- | --- | --- | ---: |",
                "| 1 | 003036.SZ | 泰坦股份 | - | 60.55 |",
                "| 2 | NEARUSDT | NEAR/USDT | Crypto | 60.40 |",
                "| 3 | CUSDT | C/USDT | Crypto | 60.30 |",
                "| 6 | 600545.SH | 卓郎智能 | - | 60.20 |",
                "| 20 | 688260.SH | 昀冢科技 | - | 56.83 |",
            ]
        ),
        approved_l1_materials={
            SelectionWorkerId.STRATEGIST: "approved strategist l1",
            SelectionWorkerId.SKEPTIC: "approved skeptic l1",
            SelectionWorkerId.MANAGER: "approved manager l1",
        },
    )
    executions = execute_selection_dispatches(
        openclaw=openclaw,
        dispatches=dispatches,
        candidate_cache_ref=_candidate_cache_ref(),
        profile="CN_A",
        selection_artifact_root=tmp_path / "selection-artifacts",
    )
    by_worker_execution = {item.dispatch.worker_id: item for item in executions}
    manager_snapshot = json.loads(
        by_worker_execution[SelectionWorkerId.MANAGER].command_snapshot_path.read_text(encoding="utf-8")
    )
    pm_snapshot = json.loads(
        by_worker_execution[SelectionWorkerId.PORTFOLIO_MANAGER].command_snapshot_path.read_text(encoding="utf-8")
    )

    for snapshot in (manager_snapshot, pm_snapshot):
        prompt_context = snapshot["runtime_vars"]["selection_prompt_context"]
        assert snapshot["runtime_vars"]["select_workflow_run_id"].startswith("sel-wf-")
        assert "[候选池完整核对清单]" in prompt_context
        assert "- 2 | NEARUSDT | NEAR/USDT" in prompt_context
        assert "- 3 | CUSDT | C/USDT" in prompt_context
        assert "- 6 | 600545.SH | 卓郎智能" in prompt_context
        assert prompt_context.index("[候选池完整核对清单]") < prompt_context.index("[模型可见已批准材料]")


def test_selection_dispatch_validation_failure_blocks_following_workers(tmp_path: Path) -> None:
    runner = _CorruptingOpenClawRunner(corrupt_after_call_count=1)
    openclaw = OpenClawClient(runner=runner)
    dispatches = _dispatches(tmp_path)

    executions = execute_selection_dispatches(
        openclaw=openclaw,
        dispatches=dispatches,
        candidate_cache_ref=_candidate_cache_ref(),
        profile="CN_A",
        selection_artifact_root=tmp_path / "selection-artifacts",
    )

    assert len(runner.payloads) == 1
    assert len(executions) == 1
    assert executions[0].openclaw_result.status == "failed"
    assert executions[0].openclaw_result.failure_reason is not None
    assert "selection dispatch evidence validation failed" in executions[0].openclaw_result.failure_reason


def _dispatches(tmp_path: Path):
    return build_fixed_selection_dispatches(
        request=SelectRequest(
            request_id="sel-07-contract-request",
            market=SelectionMarket.CN_A,
            profile=SelectionProfile.CN_A,
            trade_date="2026-05-26",
            user_id="u-1",
            created_at="2026-05-26T10:00:00+00:00",
            entry_point=WorkflowEntryPoint.SELECT_COMMAND,
            system_context_policy=SelectionSystemContextPolicy.SINGLE_WORKER_MINIMAL,
        ),
        select_workflow_run_id="sel-wf-07",
        selection_run_id="sel-run-07",
        evidence_root=tmp_path / "evidence",
        candidate_cache_summary_md="候选摘要：仅含事实和数据质量说明。",
        approved_l1_materials={
            SelectionWorkerId.STRATEGIST: "approved strategist l1",
            SelectionWorkerId.SKEPTIC: "approved skeptic l1",
            SelectionWorkerId.MANAGER: "approved manager l1",
        },
    )


def _candidate_cache_ref() -> CandidateCacheRef:
    return CandidateCacheRef(
        selection_run_id="sel-run-07",
        material_id="selection-candidate-cache-mat",
        l1_uri="local://selection/sel-run-07/candidate-cache/approved/candidate-cache.md",
        content_sha256="a" * 64,
        manifest_ref="local://selection/sel-run-07/candidate-cache/approved/candidate-cache-manifest.json",
        approved_at="2026-05-26T09:00:00+00:00",
        expires_at="2026-05-27T09:00:00+00:00",
        cache_summary_ref="local://selection/sel-run-07/candidate-cache/approved/candidate-cache-summary.md",
    )


def _sha256(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()
