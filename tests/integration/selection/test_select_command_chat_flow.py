from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
from claw_trade.config.report_workflow_settings import ReportWorkflowSettings
from claw_trade.runtime.openclaw_client import OpenClawClient, ProbeResult
from claw_trade.selection.confirmation import (
    SelectionConfirmationController,
    SelectionConfirmRequest,
)
from claw_trade.selection.controller import SelectionController
from claw_trade.selection.models import (
    CandidatePackManifest,
    CandidatePackReadbackStatus,
    CandidatePackRef,
    SelectionDataRun,
    SelectionDataRunStatus,
    SelectionMarket,
    SelectionProfile,
    SelectionRunPlan,
    SelectionTriggerSource,
)
from claw_trade.selection.store import SelectionDataRunRecord, SelectionRunStore
from claw_trade.ui_backend.chat_controller import ChatController
from claw_trade.ui_backend.confirmation_controller import ConfirmationController
from claw_trade.ui_backend.intent_recognizer import IntentRecognizer
from claw_trade.ui_backend.openclaw_client import OpenClawGatewayClient
from claw_trade.ui_backend.report_queue import ReportTaskQueue
from claw_trade.ui_backend.workflow_bridge import ReportWorkflowBridge

_READER_FORBIDDEN_CANDIDATE_FACT_TERMS = (
    "liquidity_tradability_score",
    "amount=",
    '"amount":',
    "close=",
    '"close":',
    "strategy_hit_count",
    "data_gap_penalty_score",
    "risk_penalty_score",
)
_READER_REQUIRED_CANDIDATE_FACT_TERMS = (
    "成交额",
    "收盘价",
    "数据缺口扣分",
    "流动性/可交易性",
)


def _assert_candidate_fact_body_is_reader_chinese(text: str) -> None:
    for term in _READER_FORBIDDEN_CANDIDATE_FACT_TERMS:
        assert term not in text
    for term in _READER_REQUIRED_CANDIDATE_FACT_TERMS:
        assert term in text


class _FakeSelectionOpenClawRunner:
    # SEL-10 说明：这里是 test double，只证明 /select 状态机与控制流；
    # 不可作为 runtime/live/provider payload 最终验收依据（SEL-11 负责 live focused run）。
    def __init__(
        self,
        *,
        pm_output: str | None = None,
        fail_worker_id: str | None = None,
        empty_output_worker_id: str | None = None,
    ) -> None:
        self.payloads: list[dict[str, object]] = []
        self._pm_output = pm_output
        self._fail_worker_id = fail_worker_id
        self._empty_output_worker_id = empty_output_worker_id

    def probe(self) -> ProbeResult:
        return ProbeResult.passed()

    def run_worker(self, payload: dict[str, object]) -> dict[str, object]:
        self.payloads.append(dict(payload))
        evidence_dir = Path(str(payload["evidence_dir"]))
        evidence_dir.mkdir(parents=True, exist_ok=True)
        worker_id = str(payload["worker_id"])
        should_fail = self._fail_worker_id is not None and worker_id == self._fail_worker_id
        runtime_markers = {
            "run_id": payload["run_id"],
            "call_id": payload["call_id"],
            "worker_id": payload["worker_id"],
            "stage": payload["stage"],
            "profile": payload["profile"],
            "openclaw_run_id": f"oc-{payload['call_id']}",
        }
        provider_request_path = evidence_dir / "provider-request.json"
        visible_tools_path = evidence_dir / "visible-tools.json"
        workspace_evidence_path = evidence_dir / "workspace-evidence.json"
        first_response_path = evidence_dir / "first-response.json"
        tool_calls_path = evidence_dir / "tool-calls.json"
        raw_output_path = evidence_dir / "raw-output.md"
        receipt_path = evidence_dir / "openviking-receipt.json"

        tools = [
            {"type": "function", "function": {"name": tool_name}}
            for tool_name in payload.get("allowed_tools", ())
        ]
        runtime_vars = payload.get("runtime_vars")
        prompt_text = "selection test prompt"
        if isinstance(runtime_vars, dict):
            select_context = runtime_vars.get("select_workflow_run_id")
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
        first_response_text = (
            ""
            if self._empty_output_worker_id is not None and worker_id == self._empty_output_worker_id
            else "ok"
        )
        first_response_path.write_text(
            json.dumps({"source": "openclaw_first_model_event", "runtime_markers": runtime_markers, "text": first_response_text}),
            encoding="utf-8",
        )
        tool_calls_path.write_text(
            json.dumps({"source": "model_tool_events", "status": "recorded", "calls": []}),
            encoding="utf-8",
        )
        if self._empty_output_worker_id is not None and worker_id == self._empty_output_worker_id:
            raw_output = ""
        elif worker_id == "selection_portfolio_manager" and self._pm_output is not None:
            raw_output = self._pm_output
        else:
            raw_output = _worker_output(worker_id)
        raw_output_path.write_text(raw_output, encoding="utf-8")
        receipt_path.write_text(json.dumps({"receipt_id": f"receipt-{payload['call_id']}"}), encoding="utf-8")
        return {
            "status": "failed" if should_fail else "succeeded",
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
            "failure_reason": f"worker_runtime_failed:{worker_id}" if should_fail else None,
        }


def _worker_output(worker_id: str) -> str:
    if worker_id == "selection_strategist":
        return "策略评审：优先关注 600519.SH 与 000858.SZ。"
    if worker_id == "selection_skeptic":
        return "反方评审：300750.SZ 风险暴露偏高。"
    if worker_id == "selection_manager":
        return "综合判断：优先进入组合评审、继续观察、暂不继续。"
    return "\n".join(
        [
            "进入 /report:",
            "- 600519.SH | 贵州茅台 | 经营质量与现金流稳定，值得进入深度报告验证。",
            "观察:",
            "- 000858.SZ | 五粮液 | 还需后续财报与景气数据确认。",
            "放弃:",
            "- 300750.SZ | 宁德时代 | 当前证据链分歧较大且不够完整。",
        ]
    )


@dataclass
class _FakeWorkflowState:
    status: str = "frontline_running"


class _FakeWorkflowRunner:
    def __init__(self) -> None:
        self.calls = 0

    def create_run(self, request):  # type: ignore[no-untyped-def]
        self.calls += 1
        return f"run-{self.calls}"

    def load_state(self, run_id: str) -> _FakeWorkflowState:
        return _FakeWorkflowState()


class _FakeChatTransport:
    def __init__(self) -> None:
        self.calls = 0

    def chat_send(self, *, context_id: str, text: str, request_id: str) -> dict[str, str]:
        self.calls += 1
        return {"text": f"echo:{text}"}


def _build_controller(
    *,
    selection_controller: SelectionController | None = None,
) -> tuple[ChatController, _FakeChatTransport, _FakeWorkflowRunner]:
    transport = _FakeChatTransport()
    workflow_runner = _FakeWorkflowRunner()
    queue = ReportTaskQueue(ReportWorkflowBridge(workflow_runner))
    resolved_selection_controller = selection_controller or SelectionController(store=SelectionRunStore())
    controller = ChatController(
        openclaw_client=OpenClawGatewayClient(transport),
        recognizer=IntentRecognizer(),
        confirmation=ConfirmationController(queue),
        queue=queue,
        settings=ReportWorkflowSettings(),
        selection_controller=resolved_selection_controller,
    )
    return controller, transport, workflow_runner


def _selection_controller_with_completed_run(
    tmp_path: Path,
    *,
    pm_output: str | None = None,
    fail_worker_id: str | None = None,
    empty_output_worker_id: str | None = None,
    legacy_summary: bool = False,
    raw_complete_summary: bool = False,
) -> tuple[SelectionController, _FakeSelectionOpenClawRunner]:
    store = SelectionRunStore()
    summary_path = tmp_path / "candidate-pack-summary.md"
    if legacy_summary or raw_complete_summary:
        summary_lines = (
            [
                "# A股候选事实包",
                "",
                "## 本轮范围",
                "- 交易日：2026-05-26",
                "- 市场：CN_A",
                "- 候选数量：3",
                "- 策略配置版本：cn_a.selection_strategy.v1",
                "- 权重版本：cn_a.selection_weights.v1",
                "",
                "## 候选事实表",
                "| 排名 | 股票代码 | 股票名称 | 行业 | 总分 | 分项得分 | 策略来源 | 策略变体 | 命中字段 | 实际指标值 | 风险扣分 | 数据缺口扣分 | 排序 tie-break 字段 |",
                "| --- | --- | --- | --- | ---: | --- | --- | --- | --- | --- | ---: | ---: | --- |",
                '| 1 | 600519.SH | 贵州茅台 | 酿酒行业 | 91.00 | {"liquidity_tradability_score":15} | myhhub/stock | myhhub_volume_rise | {"hit_volume_breakout":1} | {"amount":3000000000,"close":1612} | 0.00 | 0.00 | {"amount":3000000000} |',
            ]
            if raw_complete_summary
            else [
                "# A股候选事实包",
                "",
                "| 排名 | 代码 | 公司 | 行业 | 得分 | 策略命中 | 数据质量 |",
                "| --- | --- | --- | --- | ---: | --- | --- |",
                "| 1 | 600519.SH | 贵州茅台 | 酿酒行业 | 91.00 | volume_breakout | 完整 |",
                "| 2 | 000858.SZ | 五粮液 | 酿酒行业 | 83.00 | volume_breakout | 完整 |",
                "| 3 | 300750.SZ | 宁德时代 | 电力设备 | 67.00 | volume_breakout | 完整 |",
            ]
        )
        summary_path.write_text(
            "\n".join(summary_lines),
            encoding="utf-8",
        )
        (tmp_path / "candidate-pack.json").write_text(
            json.dumps(
                {
                    "trade_date": "2026-05-26",
                    "market": "CN_A",
                    "candidate_count": 3,
                    "candidates": [
                        {
                            "rank": 1,
                            "ticker": "600519.SH",
                            "company_name": "贵州茅台",
                            "industry": "酿酒行业",
                            "feature_values": {
                                "score": 91,
                                "liquidity_tradability_score": 15,
                                "amount": 3000000000,
                                "close": 1612,
                                "hit_volume_breakout": 1,
                                "risk_penalty_score": 0,
                                "data_gap_penalty_score": 0,
                                "strategy_missing_field_count": 0,
                                "strategy_required_field_count": 64,
                            },
                            "strategy_hits": ["myhhub/stock:myhhub_volume_rise"],
                            "data_quality": "完整",
                            "source_summary": "标准化行情与财务快照",
                        },
                        {
                            "rank": 2,
                            "ticker": "000858.SZ",
                            "company_name": "五粮液",
                            "industry": "酿酒行业",
                            "feature_values": {
                                "score": 83,
                                "liquidity_tradability_score": 12,
                                "amount": 900000000,
                                "close": 132,
                                "hit_volume_breakout": 1,
                                "risk_penalty_score": 0,
                                "data_gap_penalty_score": 1,
                                "strategy_missing_field_count": 0,
                                "strategy_required_field_count": 64,
                            },
                            "strategy_hits": ["Sequoia-X:sequoia_ma_volume"],
                            "data_quality": "完整",
                            "source_summary": "标准化行情与财务快照",
                        },
                        {
                            "rank": 3,
                            "ticker": "300750.SZ",
                            "company_name": "宁德时代",
                            "industry": "电力设备",
                            "feature_values": {
                                "score": 67,
                                "rps_trend_score": 8,
                                "amount": 500000000,
                                "close": 240,
                                "risk_penalty_score": 2,
                                "data_gap_penalty_score": 3,
                                "strategy_missing_field_count": 0,
                                "strategy_required_field_count": 64,
                            },
                            "strategy_hits": [],
                            "data_quality": "完整",
                            "source_summary": "标准化行情与财务快照",
                        },
                    ],
                    "data_quality_summary": "数据质量：本批次未发现阻断级或警告级缺口。",
                    "source_summary": "来源摘要：交易日全市场标准化快照、特征快照与确定性评分结果。",
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        (tmp_path / "candidate-pack-manifest.json").write_text(
            json.dumps(
                {
                    "trade_date": "2026-05-26",
                    "market": "CN_A",
                    "candidate_count": 3,
                    "strategy_config_version": "cn_a.selection_strategy.v1",
                    "weight_version": "cn_a.selection_weights.v1",
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
    else:
        summary_path.write_text(
            "\n".join(
                [
                    "# A股候选事实包",
                    "",
                    "## 本轮范围",
                    "- 交易日：2026-05-26",
                    "- 市场：CN_A",
                    "- 候选数量：3",
                    "- 策略配置版本：cn_a.selection_strategy.v1",
                    "- 权重版本：cn_a.selection_weights.v1",
                    "",
                    "## 候选事实表",
                    "| 排名 | 股票代码 | 股票名称 | 行业 | 总分 | 分项得分 | 策略来源 | 策略变体 | 命中字段 | 实际指标值 | 风险扣分 | 数据缺口扣分 | 排序 tie-break 字段 |",
                    "| --- | --- | --- | --- | ---: | --- | --- | --- | --- | --- | ---: | ---: | --- |",
                    "| 1 | 600519.SH | 贵州茅台 | 酿酒行业 | 91.00 | 流动性/可交易性=15.00 | myhhub/stock | myhhub_volume_rise | 成交额=3000000000 | 成交额=3000000000, 收盘价=1612 | 0.00 | 0.00 | 成交额=3000000000 |",
                    "| 2 | 000858.SZ | 五粮液 | 酿酒行业 | 83.00 | 流动性/可交易性=12.00 | Sequoia-X | sequoia_ma_volume | 成交额=900000000 | 成交额=900000000, 收盘价=132 | 0.00 | 1.00 | 成交额=900000000 |",
                    "| 3 | 300750.SZ | 宁德时代 | 电力设备 | 67.00 | RPS/趋势强度=8.00 | - | - | - | 收盘价=240 | 2.00 | 3.00 | 成交额=500000000 |",
                ]
            ),
            encoding="utf-8",
        )
    if not (tmp_path / "candidate-pack.json").is_file():
        (tmp_path / "candidate-pack.json").write_text(
            json.dumps(
                {
                    "trade_date": "2026-05-26",
                    "market": "CN_A",
                    "candidate_count": 3,
                    "candidates": [
                        {
                            "rank": 1,
                            "ticker": "600519.SH",
                            "company_name": "贵州茅台",
                            "industry": "酿酒行业",
                            "feature_values": {
                                "score": 91,
                                "liquidity_tradability_score": 15,
                                "amount": 3000000000,
                                "close": 1612,
                                "hit_volume_breakout": 1,
                                "risk_penalty_score": 0,
                                "data_gap_penalty_score": 0,
                                "strategy_missing_field_count": 0,
                                "strategy_required_field_count": 64,
                            },
                            "strategy_hits": ["myhhub/stock:myhhub_volume_rise"],
                            "data_quality": "完整",
                            "source_summary": "标准化行情与财务快照",
                        },
                        {
                            "rank": 2,
                            "ticker": "000858.SZ",
                            "company_name": "五粮液",
                            "industry": "酿酒行业",
                            "feature_values": {
                                "score": 83,
                                "liquidity_tradability_score": 12,
                                "amount": 900000000,
                                "close": 132,
                                "hit_volume_breakout": 1,
                                "risk_penalty_score": 0,
                                "data_gap_penalty_score": 1,
                                "strategy_missing_field_count": 0,
                                "strategy_required_field_count": 64,
                            },
                            "strategy_hits": ["Sequoia-X:sequoia_ma_volume"],
                            "data_quality": "完整",
                            "source_summary": "标准化行情与财务快照",
                        },
                        {
                            "rank": 3,
                            "ticker": "300750.SZ",
                            "company_name": "宁德时代",
                            "industry": "电力设备",
                            "feature_values": {
                                "score": 67,
                                "rps_trend_score": 8,
                                "amount": 500000000,
                                "close": 240,
                                "risk_penalty_score": 2,
                                "data_gap_penalty_score": 3,
                                "strategy_missing_field_count": 0,
                                "strategy_required_field_count": 64,
                            },
                            "strategy_hits": [],
                            "data_quality": "完整",
                            "source_summary": "标准化行情与财务快照",
                        },
                    ],
                    "data_quality_summary": "数据质量：本批次未发现阻断级或警告级缺口。",
                    "source_summary": "来源摘要：交易日全市场标准化快照、特征快照与确定性评分结果。",
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
    if not (tmp_path / "candidate-pack-manifest.json").is_file():
        (tmp_path / "candidate-pack-manifest.json").write_text(
            json.dumps(
                {
                    "trade_date": "2026-05-26",
                    "market": "CN_A",
                    "candidate_count": 3,
                    "strategy_config_version": "cn_a.selection_strategy.v1",
                    "weight_version": "cn_a.selection_weights.v1",
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
    run_id = "sel-run-08"
    candidate_pack_ref = CandidatePackRef(
        selection_run_id=run_id,
        material_id="mat-sel-run-08",
        l1_uri=str(tmp_path / "candidate-pack.md"),
        content_sha256="a" * 64,
        manifest_ref=str(tmp_path / "candidate-pack-manifest.json"),
        approved_at="2026-05-26T09:00:00+00:00",
        expires_at="2026-05-27T09:00:00+00:00",
        pack_summary_ref=str(summary_path),
    )
    store.save_data_run_record(
        SelectionDataRunRecord(
            run_plan=SelectionRunPlan(
                selection_run_id=run_id,
                market=SelectionMarket.CN_A,
                profile=SelectionProfile.CN_A,
                trade_date="2026-05-26",
                lookback_trading_days=260,
                universe_scope="all_a_shares",
                provider_batch_plan_ref="plan://sel-run-08",
                approved_strategy_config_ref="config://approved",
                trigger_source=SelectionTriggerSource.SCHEDULED,
            ),
            data_run=SelectionDataRun(
                selection_run_id=run_id,
                status=SelectionDataRunStatus.COMPLETED,
                normalized_refs=(f"normalized://mongo/openbb_normalized/{run_id}",),
                provider_attempt_refs=(f"attempt://{run_id}",),
                select_data_plan_ref=f"select-data-plan://selection/{run_id}/2026-05-26",
                warehouse_check_ref=f"warehouse-check://selection/{run_id}/2026-05-26/ok",
                candidate_pack_ref=candidate_pack_ref,
                completed_at="2026-05-26T09:01:00+00:00",
            ),
            manifest=CandidatePackManifest(
                schema_version="sel-04-candidate-pack-v1",
                selection_run_id=run_id,
                market=SelectionMarket.CN_A,
                profile=SelectionProfile.CN_A,
                trade_date="2026-05-26",
                candidate_count=20,
                source_lineage_refs=("lineage://a",),
                pack_body_sha256="a" * 64,
                strategy_config_ref="config://approved",
                readback_status=CandidatePackReadbackStatus.VERIFIED,
                stage="approving_candidate_pack",
                target="candidate_pack",
            ),
        )
    )
    selection_runner = _FakeSelectionOpenClawRunner(
        pm_output=pm_output,
        fail_worker_id=fail_worker_id,
        empty_output_worker_id=empty_output_worker_id,
    )
    selection_controller = SelectionController(
        store=store,
        now_fn=lambda: datetime.fromisoformat("2026-05-26T10:00:00+00:00").astimezone(UTC),
        openclaw=OpenClawClient(selection_runner),
        workflow_evidence_root=tmp_path / "selection-workflows",
    )
    return selection_controller, selection_runner


@pytest.mark.integration
def test_select_command_no_completed_run_returns_unavailable_and_does_not_touch_report_queue() -> None:
    controller, chat_transport, workflow_runner = _build_controller()

    result = controller.send_chat_message(request_id="sel-08-no-run", context_id="ctx-no-run", text="/select")

    assert "error" not in result
    assert result["selection"]["code"] == "unavailable"
    assert result["selection"]["unavailableCode"] == "no_completed_selection_run"
    assert Path(result["selection"]["evidencePath"]).is_file()
    assert chat_transport.calls == 0
    assert workflow_runner.calls == 0
    assert result["context"]["kind"] == "normal_chat"


@pytest.mark.integration
def test_select_command_happy_path_runs_fixed_workers_and_renders_three_categories(tmp_path: Path) -> None:
    selection_controller, selection_runner = _selection_controller_with_completed_run(tmp_path)
    controller, chat_transport, workflow_runner = _build_controller(selection_controller=selection_controller)

    result = controller.send_chat_message(request_id="sel-08-ok", context_id="ctx-ok", text="/select")

    assert "error" not in result
    assert result["selection"]["code"] == "completed"
    assert result["selection"]["workflowRunId"].startswith("select-")
    evidence_path = Path(result["selection"]["evidencePath"])
    assert evidence_path.is_file()
    evidence_payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert evidence_payload["status"] == "completed"
    assert evidence_payload["reason"] == "waiting_report_confirmation"
    decision_payload = evidence_payload["decision"]
    assert decision_payload["approved_material_id"] == f"selection-pm-decision-{result['selection']['workflowRunId']}"
    assert decision_payload["approval_status"] == "approved"
    assert decision_payload["select_workflow_run_id"] == result["selection"]["workflowRunId"]
    assert decision_payload["material_target"] == "selection_portfolio_decision"
    assert decision_payload["material_type"] == "pm_decision"

    assert [payload["worker_id"] for payload in selection_runner.payloads] == [
        "selection_strategist",
        "selection_skeptic",
        "selection_manager",
        "selection_portfolio_manager",
    ]
    message = result["messages"][-1]["text"]
    assert "进入 `/report`" in message
    assert "观察：" in message
    assert "放弃：" in message
    assert "候选事实包：" in message
    assert "总分" in message
    assert "分项得分" in message
    assert "策略来源" in message
    assert "策略变体" in message
    assert "命中字段" in message
    assert "实际指标值" in message
    assert "风险扣分" in message
    assert "数据缺口扣分" in message
    assert "排序 tie-break 字段" in message
    assert "策略配置版本：cn_a.selection_strategy.v1" in message
    assert "权重版本：cn_a.selection_weights.v1" in message
    _assert_candidate_fact_body_is_reader_chinese(message)
    assert "买入" not in message
    assert "卖出" not in message
    assert "持有" not in message
    assert "目标价" not in message
    assert "止损" not in message

    assert chat_transport.calls == 0
    assert workflow_runner.calls == 0
    assert result["context"]["kind"] == "normal_chat"


@pytest.mark.integration
def test_select_command_rejects_approved_pack_with_missing_strategy_fields(tmp_path: Path) -> None:
    selection_controller, selection_runner = _selection_controller_with_completed_run(tmp_path)
    payload_path = tmp_path / "candidate-pack.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    payload["candidates"][0]["feature_values"]["strategy_missing_field_count"] = 56
    payload_path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    controller, chat_transport, workflow_runner = _build_controller(selection_controller=selection_controller)

    result = controller.send_chat_message(request_id="sel-08-missing-strategy-fields", context_id="ctx-missing", text="/select")

    assert "error" not in result
    assert result["selection"]["code"] == "unavailable"
    assert result["selection"]["unavailableCode"] == "candidate_pack_integrity_failed"
    evidence_payload = json.loads(Path(result["selection"]["evidencePath"]).read_text(encoding="utf-8"))
    assert evidence_payload["reason"] == "candidate_pack_strategy_fields_missing: ticker=600519.SH missing 56/64 approved strategy fields"
    assert selection_runner.payloads == []
    assert chat_transport.calls == 0
    assert workflow_runner.calls == 0


@pytest.mark.integration
def test_select_command_rebuilds_legacy_candidate_pack_summary_fields(tmp_path: Path) -> None:
    selection_controller, _ = _selection_controller_with_completed_run(tmp_path, legacy_summary=True)
    controller, chat_transport, workflow_runner = _build_controller(selection_controller=selection_controller)

    result = controller.send_chat_message(request_id="sel-08-legacy-summary", context_id="ctx-legacy", text="/select")

    assert "error" not in result
    assert result["selection"]["code"] == "completed"
    message = result["messages"][-1]["text"]
    assert "候选事实包：" in message
    assert "总分" in message
    assert "分项得分" in message
    assert "策略来源" in message
    assert "策略变体" in message
    assert "命中字段" in message
    assert "实际指标值" in message
    assert "风险扣分" in message
    assert "数据缺口扣分" in message
    assert "排序 tie-break 字段" in message
    assert "策略配置版本：cn_a.selection_strategy.v1" in message
    assert "权重版本：cn_a.selection_weights.v1" in message
    _assert_candidate_fact_body_is_reader_chinese(message)
    assert "manifest" not in message.lower()
    assert "lineage" not in message.lower()
    assert "OpenViking" not in message
    assert chat_transport.calls == 0
    assert workflow_runner.calls == 0


@pytest.mark.integration
def test_select_command_rebuilds_candidate_pack_summary_with_raw_reader_field_names(tmp_path: Path) -> None:
    selection_controller, _ = _selection_controller_with_completed_run(tmp_path, raw_complete_summary=True)
    controller, chat_transport, workflow_runner = _build_controller(selection_controller=selection_controller)

    result = controller.send_chat_message(request_id="sel-08-raw-summary", context_id="ctx-raw", text="/select")

    assert "error" not in result
    assert result["selection"]["code"] == "completed"
    message = result["messages"][-1]["text"]
    assert "候选事实包：" in message
    _assert_candidate_fact_body_is_reader_chinese(message)
    assert "hit_volume_breakout" not in message
    assert chat_transport.calls == 0
    assert workflow_runner.calls == 0


@pytest.mark.integration
def test_real_select_evidence_passes_confirmation_pm_decision_material_gate(tmp_path: Path) -> None:
    selection_controller, _ = _selection_controller_with_completed_run(tmp_path)
    controller, _, _ = _build_controller(selection_controller=selection_controller)
    result = controller.send_chat_message(request_id="sel-08-confirm", context_id="ctx-confirm", text="/select")

    workflow_run_id = result["selection"]["workflowRunId"]
    evidence_path = Path(result["selection"]["evidencePath"])
    evidence_payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    decision_payload = evidence_payload["decision"]

    confirm_runner = _FakeWorkflowRunner()
    confirm_queue = ReportTaskQueue(ReportWorkflowBridge(confirm_runner))
    confirm_controller = SelectionConfirmationController(
        store=selection_controller._store,  # noqa: SLF001
        queue=confirm_queue,
        settings=ReportWorkflowSettings(),
        workflow_evidence_root=tmp_path / "selection-workflows",
        now_fn=lambda: datetime(2026, 5, 26, 10, 1, tzinfo=UTC),
        today_fn=lambda: "2026-05-26",
    )
    confirm_result = confirm_controller.confirm(
        SelectionConfirmRequest(
            confirmation_id="cfm-real-select",
            idempotency_key=f"{workflow_run_id}:600519.SH:cfm-real-select",
            select_workflow_run_id=workflow_run_id,
            ticker="600519.SH",
        )
    )

    assert confirm_result.code == "report_handoff_started"
    assert confirm_result.handoff_request["selectionContextRef"] == decision_payload["approved_material_id"]
    assert confirm_result.handoff_request["selectionContextRef"] != "mat-sel-run-08"
    assert confirm_runner.calls == 1


@pytest.mark.integration
def test_select_command_missing_watch_section_fails_with_selection_result_invalid(tmp_path: Path) -> None:
    pm_output = "\n".join(
        [
            "进入 /report:",
            "- 600519.SH | 贵州茅台 | 经营质量与现金流稳定。",
            "放弃:",
            "- 300750.SZ | 宁德时代 | 当前证据链分歧较大。",
        ]
    )
    selection_controller, _ = _selection_controller_with_completed_run(tmp_path, pm_output=pm_output)
    controller, chat_transport, workflow_runner = _build_controller(selection_controller=selection_controller)

    result = controller.send_chat_message(request_id="sel-08-missing-watch", context_id="ctx-missing-watch", text="/select")

    assert "error" not in result
    assert result["selection"]["code"] == "failed"
    assert result["selection"]["failureReason"] == "selection_result_invalid:missing_required_sections"
    evidence_path = Path(result["selection"]["evidencePath"])
    assert evidence_path.is_file()
    evidence_payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert evidence_payload["status"] == "failed"
    assert evidence_payload["reason"] == "selection_result_invalid:missing_required_sections"
    assert chat_transport.calls == 0
    assert workflow_runner.calls == 0


@pytest.mark.integration
def test_select_command_missing_candidate_classification_fails_closed(tmp_path: Path) -> None:
    pm_output = "\n".join(
        [
            "进入 /report:",
            "- 600519.SH | 贵州茅台 | 经营质量与现金流稳定。",
            "观察:",
            "- 000858.SZ | 五粮液 | 还需后续财报与景气数据确认。",
            "放弃:",
            "- 无",
        ]
    )
    selection_controller, _ = _selection_controller_with_completed_run(tmp_path, pm_output=pm_output)
    controller, chat_transport, workflow_runner = _build_controller(selection_controller=selection_controller)

    result = controller.send_chat_message(request_id="sel-08-missing-candidate", context_id="ctx-missing-candidate", text="/select")

    assert "error" not in result
    assert result["selection"]["code"] == "failed"
    assert (
        result["selection"]["failureReason"]
        == "selection_result_invalid:candidate_classification_incomplete:missing=300750.SZ"
    )
    evidence_path = Path(result["selection"]["evidencePath"])
    assert evidence_path.is_file()
    evidence_payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert evidence_payload["status"] == "failed"
    assert evidence_payload["reason"] == "selection_result_invalid:candidate_classification_incomplete:missing=300750.SZ"
    assert chat_transport.calls == 0
    assert workflow_runner.calls == 0


@pytest.mark.integration
def test_select_command_ticker_company_mismatch_fails_closed(tmp_path: Path) -> None:
    pm_output = "\n".join(
        [
            "进入 /report:",
            "- 600519.SH | 五粮液 | 经营质量与现金流稳定。",
            "观察:",
            "- 000858.SZ | 贵州茅台 | 还需后续财报与景气数据确认。",
            "放弃:",
            "- 300750.SZ | 宁德时代 | 当前证据链分歧较大且不够完整。",
        ]
    )
    selection_controller, _ = _selection_controller_with_completed_run(tmp_path, pm_output=pm_output)
    controller, chat_transport, workflow_runner = _build_controller(selection_controller=selection_controller)

    result = controller.send_chat_message(
        request_id="sel-08-company-mismatch",
        context_id="ctx-company-mismatch",
        text="/select",
    )

    assert "error" not in result
    assert result["selection"]["code"] == "failed"
    assert (
        result["selection"]["failureReason"]
        == "selection_result_invalid:ticker_company_mismatch:600519.SH:expected=贵州茅台:actual=五粮液"
    )
    evidence_path = Path(result["selection"]["evidencePath"])
    assert evidence_path.is_file()
    evidence_payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert evidence_payload["status"] == "failed"
    assert (
        evidence_payload["reason"]
        == "selection_result_invalid:ticker_company_mismatch:600519.SH:expected=贵州茅台:actual=五粮液"
    )
    assert chat_transport.calls == 0
    assert workflow_runner.calls == 0


@pytest.mark.integration
def test_select_command_worker_runtime_failure_stops_downstream_dispatches(tmp_path: Path) -> None:
    selection_controller, selection_runner = _selection_controller_with_completed_run(
        tmp_path,
        fail_worker_id="selection_strategist",
    )
    controller, _, _ = _build_controller(selection_controller=selection_controller)

    result = controller.send_chat_message(request_id="sel-10-worker-failed", context_id="ctx-worker-failed", text="/select")

    assert result["selection"]["code"] == "failed"
    assert result["selection"]["failureReason"] == "worker_runtime_failed:selection_strategist"
    assert [payload["worker_id"] for payload in selection_runner.payloads] == ["selection_strategist"]


@pytest.mark.integration
def test_select_command_empty_worker_output_stops_before_manager(tmp_path: Path) -> None:
    selection_controller, selection_runner = _selection_controller_with_completed_run(
        tmp_path,
        empty_output_worker_id="selection_skeptic",
    )
    controller, _, _ = _build_controller(selection_controller=selection_controller)

    result = controller.send_chat_message(request_id="sel-10-empty-output", context_id="ctx-empty-output", text="/select")

    assert result["selection"]["code"] == "failed"
    assert result["selection"]["failureReason"] == "artifact_approval_failed:selection_skeptic:empty_output"
    assert [payload["worker_id"] for payload in selection_runner.payloads] == [
        "selection_strategist",
        "selection_skeptic",
    ]
