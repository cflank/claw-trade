from __future__ import annotations

import json
from pathlib import Path

import pytest
from claw_trade.selection.dispatch import build_fixed_selection_dispatches
from claw_trade.selection.evidence import (
    evidence_from_dispatch,
    validate_selection_dispatch_evidence,
)
from claw_trade.selection.models import (
    SelectionMarket,
    SelectionProfile,
    SelectionSystemContextPolicy,
    SelectionWorkerDispatch,
    SelectionWorkerId,
    SelectRequest,
)
from claw_trade.workflow.models import WorkflowEntryPoint


# SEL-10 说明：本文件通过写入本地 evidence fixture 做 payload 合约校验，
# 仅用于 contract 证明；不作为 runtime/live/provider payload 最终验收（SEL-11 负责）。
def test_selection_provider_payload_contract_passes_for_four_workers(tmp_path: Path) -> None:
    dispatches = _dispatches(tmp_path)
    by_worker = {item.worker_id: item for item in dispatches}

    _write_dispatch_evidence(
        dispatch=by_worker[SelectionWorkerId.STRATEGIST],
        tools=("claw_get_selection_candidate_pack",),
        messages=("请基于候选池工具结果完成正向评审。",),
    )
    _write_dispatch_evidence(
        dispatch=by_worker[SelectionWorkerId.SKEPTIC],
        tools=("claw_get_selection_candidate_pack",),
        messages=_skeptic_message(by_worker[SelectionWorkerId.SKEPTIC]),
    )
    _write_dispatch_evidence(
        dispatch=by_worker[SelectionWorkerId.MANAGER],
        tools=(),
        messages=_manager_pm_messages(by_worker[SelectionWorkerId.MANAGER]),
    )
    _write_dispatch_evidence(
        dispatch=by_worker[SelectionWorkerId.PORTFOLIO_MANAGER],
        tools=(),
        messages=_manager_pm_messages(by_worker[SelectionWorkerId.PORTFOLIO_MANAGER]),
    )

    for dispatch in dispatches:
        guard = validate_selection_dispatch_evidence(evidence_from_dispatch(dispatch))
        assert guard.ok, guard.reason


def test_selection_provider_payload_rejects_preloaded_candidate_pack_for_strategist(tmp_path: Path) -> None:
    dispatch = _dispatch_by_worker(tmp_path, SelectionWorkerId.STRATEGIST)
    _write_dispatch_evidence(
        dispatch=dispatch,
        tools=("claw_get_selection_candidate_pack",),
        messages=("首轮不应出现完整候选池正文。\n# 候选池事实包\n## 候选事实表",),
    )

    guard = validate_selection_dispatch_evidence(evidence_from_dispatch(dispatch))

    assert guard.ok is False
    assert guard.reason is not None and "preloaded full candidate pack body" in guard.reason


def test_selection_provider_payload_rejects_compact_candidate_pack_tool_result(tmp_path: Path) -> None:
    dispatch = _dispatch_by_worker(tmp_path, SelectionWorkerId.STRATEGIST)
    _write_dispatch_evidence(
        dispatch=dispatch,
        tools=("claw_get_selection_candidate_pack",),
        messages=("selection payload contract test",),
    )
    _write_provider_requests_jsonl(
        dispatch=dispatch,
        tools=("claw_get_selection_candidate_pack",),
        tool_result_text=(
            "# 候选池事实包\n"
            "## 候选事实表\n"
            "| 排名 | 代码 | 公司 | 得分 |\n"
            "| 1 | 300721.SZ | 怡达股份 | 9.7 |"
        ),
    )

    guard = validate_selection_dispatch_evidence(evidence_from_dispatch(dispatch))

    assert guard.ok is False
    assert guard.reason is not None and "candidate pack tool result missing required model-visible field" in guard.reason


def test_selection_provider_payload_accepts_full_candidate_pack_tool_result(tmp_path: Path) -> None:
    dispatch = _dispatch_by_worker(tmp_path, SelectionWorkerId.STRATEGIST)
    _write_dispatch_evidence(
        dispatch=dispatch,
        tools=("claw_get_selection_candidate_pack",),
        messages=("selection payload contract test",),
    )
    _write_provider_requests_jsonl(
        dispatch=dispatch,
        tools=("claw_get_selection_candidate_pack",),
        tool_result_text=_full_candidate_pack_tool_result(),
    )

    guard = validate_selection_dispatch_evidence(evidence_from_dispatch(dispatch))

    assert guard.ok, guard.reason


def test_selection_provider_payload_rejects_skeptic_missing_required_material_body(tmp_path: Path) -> None:
    dispatch = _dispatch_by_worker(tmp_path, SelectionWorkerId.SKEPTIC)
    _write_dispatch_evidence(
        dispatch=dispatch,
        tools=("claw_get_selection_candidate_pack",),
        messages=("【approved_strategist_l1】",),
    )

    guard = validate_selection_dispatch_evidence(evidence_from_dispatch(dispatch))

    assert guard.ok is False
    assert guard.reason is not None and "skeptic payload missing required approved material body" in guard.reason


def test_selection_provider_payload_accepts_skeptic_with_approved_strategist_l1_containing_candidate_table_snippet(
    tmp_path: Path,
) -> None:
    dispatch = _dispatch_by_worker(
        tmp_path,
        SelectionWorkerId.SKEPTIC,
        strategist_l1_override=(
            "好，已获取候选池事实材料。\n"
            "# 候选池事实包\n"
            "## 候选事实表\n"
            "| 排名 | 代码 | 公司 |\n"
            "| 1 | 300721.SZ | 怡达股份 |"
        ),
    )
    _write_dispatch_evidence(
        dispatch=dispatch,
        tools=("claw_get_selection_candidate_pack",),
        messages=_skeptic_message(dispatch),
    )

    guard = validate_selection_dispatch_evidence(evidence_from_dispatch(dispatch))

    assert guard.ok, guard.reason


def test_selection_provider_payload_rejects_manager_protocol_materials(tmp_path: Path) -> None:
    dispatch = _dispatch_by_worker(tmp_path, SelectionWorkerId.MANAGER)
    _write_dispatch_evidence(
        dispatch=dispatch,
        tools=(),
        messages=("禁止内容：manifest hash viking://resources/workflow。",),
    )

    guard = validate_selection_dispatch_evidence(evidence_from_dispatch(dispatch))

    assert guard.ok is False
    assert guard.reason is not None and "forbidden protocol text" in guard.reason


def test_selection_provider_payload_rejects_manager_missing_required_material_body(tmp_path: Path) -> None:
    dispatch = _dispatch_by_worker(tmp_path, SelectionWorkerId.MANAGER)
    _write_dispatch_evidence(
        dispatch=dispatch,
        tools=(),
        messages=("【approved_strategist_l1】\n【approved_skeptic_l1】\n【candidate_pack_summary】",),
    )

    guard = validate_selection_dispatch_evidence(evidence_from_dispatch(dispatch))

    assert guard.ok is False
    assert guard.reason is not None and "missing required approved material body" in guard.reason


def test_selection_provider_payload_rejects_manager_refs_only_materials(tmp_path: Path) -> None:
    dispatch = _dispatch_by_worker(tmp_path, SelectionWorkerId.MANAGER)
    _write_dispatch_evidence(
        dispatch=dispatch,
        tools=(),
        messages=(
            "【approved_strategist_l1】 viking://resources/workflow/x/report.md\n"
            "【approved_skeptic_l1】 l1_sha256=abc123\n"
            "【candidate_pack_summary】 manifest=selection-manifest",
        ),
    )

    guard = validate_selection_dispatch_evidence(evidence_from_dispatch(dispatch))

    assert guard.ok is False
    assert guard.reason is not None and "forbidden protocol text" in guard.reason


def test_selection_provider_payload_requires_single_worker_minimal_policy(tmp_path: Path) -> None:
    dispatch = _dispatch_by_worker(tmp_path, SelectionWorkerId.PORTFOLIO_MANAGER)
    _write_dispatch_evidence(
        dispatch=dispatch,
        tools=(),
        messages=_manager_pm_messages(dispatch),
        system_context_policy="openclaw_default",
    )

    guard = validate_selection_dispatch_evidence(evidence_from_dispatch(dispatch))

    assert guard.ok is False
    assert guard.reason is not None and "system_context_policy mismatch" in guard.reason


@pytest.mark.parametrize(
    ("worker_id", "expected_tools"),
    (
        (SelectionWorkerId.STRATEGIST, ("claw_get_selection_candidate_pack",)),
        (SelectionWorkerId.SKEPTIC, ("claw_get_selection_candidate_pack",)),
        (SelectionWorkerId.MANAGER, ()),
        (SelectionWorkerId.PORTFOLIO_MANAGER, ()),
    ),
)
def test_selection_visible_tools_matrix_matches_worker_contract(
    tmp_path: Path,
    worker_id: SelectionWorkerId,
    expected_tools: tuple[str, ...],
) -> None:
    dispatch = _dispatch_by_worker(tmp_path, worker_id)
    _write_dispatch_evidence(
        dispatch=dispatch,
        tools=expected_tools,
        messages=_default_messages_for_dispatch(dispatch),
    )

    guard = validate_selection_dispatch_evidence(evidence_from_dispatch(dispatch))

    assert guard.ok, guard.reason


def _dispatches(
    tmp_path: Path,
    *,
    strategist_l1_override: str | None = None,
) -> tuple[SelectionWorkerDispatch, ...]:
    approved_l1_materials = {
        SelectionWorkerId.STRATEGIST: "approved strategist l1",
        SelectionWorkerId.SKEPTIC: "approved skeptic l1",
        SelectionWorkerId.MANAGER: "approved manager l1",
    }
    if strategist_l1_override is not None:
        approved_l1_materials[SelectionWorkerId.STRATEGIST] = strategist_l1_override

    return build_fixed_selection_dispatches(
        request=SelectRequest(
            request_id="sel-07-provider-payload",
            market=SelectionMarket.CN_A,
            profile=SelectionProfile.CN_A,
            trade_date="2026-05-26",
            user_id="u-1",
            created_at="2026-05-26T10:00:00+00:00",
            entry_point=WorkflowEntryPoint.SELECT_COMMAND,
            system_context_policy=SelectionSystemContextPolicy.SINGLE_WORKER_MINIMAL,
        ),
        select_workflow_run_id="sel-wf-provider",
        selection_run_id="sel-run-provider",
        evidence_root=tmp_path / "evidence",
        candidate_pack_summary_md="candidate_pack_summary：只含事实与数据质量摘要。",
        approved_l1_materials=approved_l1_materials,
    )


def _dispatch_by_worker(
    tmp_path: Path,
    worker_id: SelectionWorkerId,
    *,
    strategist_l1_override: str | None = None,
) -> SelectionWorkerDispatch:
    mapping = {
        dispatch.worker_id: dispatch
        for dispatch in _dispatches(tmp_path, strategist_l1_override=strategist_l1_override)
    }
    return mapping[worker_id]


def _write_dispatch_evidence(
    *,
    dispatch: SelectionWorkerDispatch,
    tools: tuple[str, ...],
    messages: tuple[str, ...],
    system_context_policy: str = "single_worker_minimal",
) -> None:
    dispatch.evidence_dir.mkdir(parents=True, exist_ok=True)
    runtime_markers = {
        "run_id": dispatch.select_workflow_run_id,
        "call_id": dispatch.dispatch_id,
        "worker_id": dispatch.worker_id.value,
        "stage": dispatch.stage.value,
        "profile": "CN_A",
        "openclaw_run_id": f"oc-{dispatch.dispatch_id}",
    }
    tools_payload = [
        {"type": "function", "function": {"name": tool_name}}
        for tool_name in tools
    ]

    provider_request_path = dispatch.evidence_dir / "provider-request.json"
    visible_tools_path = dispatch.evidence_dir / "visible-tools.json"
    command_snapshot_path = dispatch.evidence_dir / "selection-dispatch-command.json"

    provider_request_path.write_text(
        json.dumps(
            {
                "source": "provider_request_capture",
                "payload": {
                    "messages": [{"role": "user", "content": text} for text in messages],
                    "tools": tools_payload,
                },
                "runtime_markers": runtime_markers,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    visible_tools_path.write_text(
        json.dumps(
            {
                "source": "provider_request",
                "provider_request_path": str(provider_request_path),
                "tools": tools_payload,
                "runtime_markers": runtime_markers,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    command_snapshot_path.write_text(
        json.dumps(
            {
                "run_id": dispatch.select_workflow_run_id,
                "call_id": dispatch.dispatch_id,
                "worker_id": dispatch.worker_id.value,
                "stage": dispatch.stage.value,
                "system_context_policy": system_context_policy,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _write_provider_requests_jsonl(
    *,
    dispatch: SelectionWorkerDispatch,
    tools: tuple[str, ...],
    tool_result_text: str,
) -> None:
    runtime_markers = {
        "run_id": dispatch.select_workflow_run_id,
        "call_id": dispatch.dispatch_id,
        "worker_id": dispatch.worker_id.value,
        "stage": dispatch.stage.value,
        "profile": "CN_A",
        "openclaw_run_id": f"oc-{dispatch.dispatch_id}",
    }
    tools_payload = [
        {"type": "function", "function": {"name": tool_name}}
        for tool_name in tools
    ]
    first = {
        "source": "provider_request_capture",
        "sequence": 1,
        "payload": {
            "messages": [{"role": "user", "content": "selection payload contract test"}],
            "tools": tools_payload,
        },
        "runtime_markers": runtime_markers,
    }
    second = {
        "source": "provider_request_capture",
        "sequence": 2,
        "payload": {
            "messages": [
                {"role": "user", "content": "selection payload contract test"},
                {
                    "role": "assistant",
                    "content": "读取候选池。",
                    "tool_calls": [
                        {
                            "id": "tool-call-1",
                            "type": "function",
                            "function": {"name": "claw_get_selection_candidate_pack", "arguments": "{}"},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "tool-call-1", "content": tool_result_text},
            ],
            "tools": tools_payload,
        },
        "runtime_markers": runtime_markers,
    }
    (dispatch.evidence_dir / "provider-requests.jsonl").write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in (first, second)),
        encoding="utf-8",
    )


def _full_candidate_pack_tool_result() -> str:
    return "\n".join(
        (
            "# A股候选事实包",
            "",
            "## 本轮范围",
            "- 交易日：2026-05-27",
            "- 市场：CN_A",
            "- 候选数量：1",
            "- 策略配置版本：cn_a.selection_strategy.v1",
            "- 权重版本：cn_a.selection_weights.v1",
            "",
            "## 候选事实表",
            "| 排名 | 股票代码 | 股票名称 | 行业 | 总分 | 分项得分 | 策略来源 | 策略变体 | 命中字段 | 实际指标值 | 风险扣分 | 数据缺口扣分 | 排序 tie-break 字段 | 数据质量 | 读者化来源摘要 |",
            "| --- | --- | --- | --- | ---: | --- | --- | --- | --- | --- | ---: | ---: | --- | --- | --- |",
            "| 1 | 300721.SZ | 怡达股份 | 化学制品 | 9.7 | {\"liquidity_tradability_score\":3.0} | myhhub/stock | myhhub_volume_rise | {\"vol_ratio\":8.7} | {\"amount\":200427475.5} | 0 | 3 | {\"amount\":200427475.5} | 完整 | 标准化行情与财务快照 |",
            "",
            "## 策略命中明细",
            "- 来源：myhhub/stock；策略变体：myhhub_volume_rise。",
            "",
            "## 排序与扣分说明",
            "- 总分：按已批准权重对同一批次特征值进行确定性计算。",
            "- 分项得分：展示可复算的策略覆盖、趋势、流动性、主题、证据完整度等子项。",
            "- 风险扣分与数据缺口扣分：仅展示确定性扣分值；缺字段时显示为空。",
            "- 排序 tie-break 字段：同分时使用的确定性排序字段值。",
        )
    )


def _default_messages_for_dispatch(dispatch: SelectionWorkerDispatch) -> tuple[str, ...]:
    if dispatch.worker_id == SelectionWorkerId.STRATEGIST:
        return ("selection payload contract test",)
    if dispatch.worker_id == SelectionWorkerId.SKEPTIC:
        return _skeptic_message(dispatch)
    return _manager_pm_messages(dispatch)


def _skeptic_message(dispatch: SelectionWorkerDispatch) -> tuple[str, ...]:
    strategist_l1 = dispatch.model_visible_materials[0]
    lines = [dispatch.select_workflow_run_id, "", "[模型可见已批准材料]", "【approved_strategist_l1】", strategist_l1]
    return ("\n".join(lines),)


def _manager_pm_messages(dispatch: SelectionWorkerDispatch) -> tuple[str, ...]:
    if dispatch.worker_id == SelectionWorkerId.MANAGER:
        labels = (
            "approved_strategist_l1",
            "approved_skeptic_l1",
            "candidate_pack_summary",
        )
    elif dispatch.worker_id == SelectionWorkerId.PORTFOLIO_MANAGER:
        labels = (
            "approved_manager_l1",
            "approved_strategist_l1",
            "approved_skeptic_l1",
            "candidate_pack_summary",
        )
    else:
        raise AssertionError(f"unexpected worker for manager/pm message helper: {dispatch.worker_id.value}")
    lines = [dispatch.select_workflow_run_id, "", "[模型可见已批准材料]"]
    for label, material in zip(labels, dispatch.model_visible_materials, strict=True):
        lines.append(f"【{label}】")
        lines.append(material)
    return ("\n".join(lines),)
