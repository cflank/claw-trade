from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from threading import Event, Thread

import pytest
from claw_trade.config.report_workflow_settings import ReportWorkflowSettings
from claw_trade.data_gateway.warehouse.selection_columnar import SelectionColumnarWarehouse
from claw_trade.runtime.openclaw_client import OpenClawClient, ProbeResult
from claw_trade.selection.confirmation import (
    SelectionConfirmationController,
    SelectionConfirmRequest,
)
from claw_trade.selection.controller import SelectionController
from claw_trade.selection.models import (
    CandidateCacheManifest,
    CandidateCacheReadbackStatus,
    CandidateCacheRef,
    SelectionDataRun,
    SelectionDataRunStatus,
    SelectionMarket,
    SelectionProfile,
    SelectionRunPlan,
    SelectionTriggerSource,
)
from claw_trade.selection.refresh import SelectionDataRefreshResult
from claw_trade.selection.store import SelectionDataRunRecord, SelectionRunStore
from claw_trade.ui_backend.chat_controller import ChatController
from claw_trade.ui_backend.confirmation_controller import ConfirmationController
from claw_trade.ui_backend.intent_recognizer import IntentRecognizer
from claw_trade.ui_backend.openclaw_client import OpenClawGatewayClient
from claw_trade.ui_backend.report_queue import ReportTaskQueue
from claw_trade.ui_backend.workflow_bridge import ReportWorkflowBridge

_READER_FORBIDDEN_CANDIDATE_FACT_TERMS = (
    "manifest",
    "lineage",
    "OpenViking",
    "approved_",
    "_l1",
    "L1",
    "Selection",
    "Review",
    "review",
    "Top 8",
    "high tight flag",
    "platform_deviation_pct",
    "post_limit_up_window_days",
    "post_limit_up_",
    "(U5)",
    "（U5）",
    "策略配置版本",
    "权重版本",
    "命中字段",
    "分项得分",
    "排序 tie-break 字段",
    "策略命中明细",
    "slope_10d",
    "ma30_slope_10d",
    "ma30",
    "score",
    "watchlist",
    "sequoia 系列",
    "30日均线_",
    "相关指标",
    "low_atr",
    "myhhub_",
    "sequoia_",
    "selection_",
    "cn_a.selection",
    "PE_TTM",
    "PE_ttm",
    "PE TTM",
    "市盈率 TTM",
    "PE(TTM)",
    "PS_TTM",
    "PS_ttm",
    "limit_up_streak",
    "内部指标",
    "Skeptic",
    "Strategist",
    "30日均线_",
    "pe/",
    "pb",
    "peak",
)
_READER_REQUIRED_CANDIDATE_FACT_TERMS = (
    "策略命中与分析过程",
    "命中的策略条件",
    "逐只策略核对",
    "分析过程",
    "数据范围与质量",
    "候选数量",
    "数据质量",
    "来源摘要",
)


@pytest.fixture(autouse=True)
def _isolated_selection_columnar_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAW_TRADE_SELECTION_COLUMNAR_ROOT", str(tmp_path / "columnar"))


def _assert_candidate_fact_body_is_reader_chinese(text: str) -> None:
    for term in _READER_FORBIDDEN_CANDIDATE_FACT_TERMS:
        assert term not in text
    for term in _READER_REQUIRED_CANDIDATE_FACT_TERMS:
        assert term in text


def _add_candidate_cache_summary_fields(payload_path: Path) -> None:
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    for candidate in payload["candidates"]:
        values = dict(candidate["feature_values"])
        candidate["component_scores"] = {
            key: value
            for key, value in values.items()
            if key.endswith("_score") and key not in {"risk_penalty_score", "data_gap_penalty_score"}
        }
        candidate["actual_metric_values"] = dict(values)
        candidate["hit_fields"] = {
            key: value
            for key, value in values.items()
            if key.startswith("hit_") or key.startswith("strategy_hit_") or key.endswith("_hit")
        }
        candidate["tie_break_fields"] = {"amount": values["amount"]}
    payload_path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")


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


class _BlockingSelectionOpenClawRunner(_FakeSelectionOpenClawRunner):
    def __init__(self) -> None:
        super().__init__()
        self.worker_started = Event()
        self.release_worker = Event()

    def run_worker(self, payload: dict[str, object]) -> dict[str, object]:
        if str(payload.get("worker_id") or "") == "selection_strategist":
            self.worker_started.set()
            if not self.release_worker.wait(timeout=5):
                raise TimeoutError("test did not release blocked selection worker")
        return super().run_worker(payload)


def _worker_output(worker_id: str) -> str:
    if worker_id == "selection_strategist":
        return "策略评审：优先关注 600519.SH 与 000858.SZ。Strategist 依据 slope_10d、low_atr、ma30_growth_30d、return_120d、limit_up_count_20d、industry_theme_score 和 cn_a.selection_strategy.v1。"
    if worker_id == "selection_skeptic":
        return "Selection 反方审查员 Review：300750.SZ 风险暴露偏高。Skeptic review 认为 PE_TTM、PE_ttm、PE TTM、市盈率 TTM、PE(TTM)、PS_TTM、PS_ttm、MA30_10日斜率、pe/roe、pb、Top 8、high tight flag、platform_deviation_pct、post_limit_up_window_days、single_day_min_return_60d、peak 与 limit_up_streak_2d 需翻译后展示。"
    if worker_id == "selection_manager":
        return "综合判断：优先进入组合评审、继续观察、暂不继续。输出 `selection_ranked_watchlist`，审查基础为 approved_selection_strategy_review_l1 和 L1 评审报告，缺失项标为（U5）。"
    return "\n".join(
        [
            "进入 /report:",
            "- 600519.SH | 贵州茅台 | 策略信号一致性最强（命中6/8）；经营质量与现金流稳定，值得进入深度报告验证。",
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


def _candidate_cache_manifest_payload(*, run_id: str, body_sha: str) -> dict[str, object]:
    return {
        "schema_version": "sel-04-candidate-cache-v1",
        "selection_run_id": run_id,
        "market": "CN_A",
        "profile": "CN_A",
        "trade_date": "2026-05-26",
        "candidate_count": 3,
        "source_lineage_refs": ["lineage://a"],
        "cache_body_sha256": body_sha,
        "strategy_config_ref": "config://approved",
        "strategy_config_version": "cn_a.selection_strategy.v1",
        "weight_version": "cn_a.selection_weights.v1",
        "candidate_scores_ref": "scores://sel-run-08",
        "stable_top20_rule": {"score_field": "score", "tie_break_fields": ["amount"], "missing_policy": "fail"},
        "readback_status": "verified",
        "stage": "approving_candidate_cache",
        "target": "candidate_cache",
    }


def _write_columnar_manifest(plan: SelectionRunPlan):
    writer = SelectionColumnarWarehouse.default().begin_write(plan=plan)
    writer.add_daily_rows(
        (
            {
                "market": "CN_A",
                "profile": "CN_A",
                "selection_trade_date": plan.trade_date,
                "ticker": "600519.SH",
                "date": plan.trade_date,
                "close": 1612.0,
                "amount": 3000000000.0,
                "source_ref": f"dataset://normalized/CN_A/daily/{plan.selection_run_id}",
            },
            {
                "market": "CN_A",
                "profile": "CN_A",
                "selection_trade_date": plan.trade_date,
                "ticker": "000858.SZ",
                "date": plan.trade_date,
                "close": 132.0,
                "amount": 900000000.0,
                "source_ref": f"dataset://normalized/CN_A/daily/{plan.selection_run_id}",
            },
            {
                "market": "CN_A",
                "profile": "CN_A",
                "selection_trade_date": plan.trade_date,
                "ticker": "300750.SZ",
                "date": plan.trade_date,
                "close": 240.0,
                "amount": 500000000.0,
                "source_ref": f"dataset://normalized/CN_A/daily/{plan.selection_run_id}",
            },
        )
    )
    writer.add_feature_rows(
        (
            {
                "ticker": "600519.SH",
                "company_name": "贵州茅台",
                "trade_date": plan.trade_date,
                "selection_features_materialized": True,
                "close": 1612.0,
                "amount": 3000000000.0,
                "source_ref": f"dataset://normalized/CN_A/daily/{plan.selection_run_id}",
            },
            {
                "ticker": "000858.SZ",
                "company_name": "五粮液",
                "trade_date": plan.trade_date,
                "selection_features_materialized": True,
                "close": 132.0,
                "amount": 900000000.0,
                "source_ref": f"dataset://normalized/CN_A/daily/{plan.selection_run_id}",
            },
            {
                "ticker": "300750.SZ",
                "company_name": "宁德时代",
                "trade_date": plan.trade_date,
                "selection_features_materialized": True,
                "close": 240.0,
                "amount": 500000000.0,
                "source_ref": f"dataset://normalized/CN_A/daily/{plan.selection_run_id}",
            },
        )
    )
    return writer.commit(
        provider_attempt_refs=(f"attempt://{plan.selection_run_id}",),
        normalized_refs=(f"dataset://normalized/CN_A/daily/{plan.selection_run_id}",),
    )


def _write_readback_log(path: Path, *, expected_sha256: str) -> None:
    suffix = path.suffix
    verify_path = path.with_suffix(f"{suffix}.readback-verify.json") if suffix else path.with_name(f"{path.name}.readback-verify.json")
    verify_path.write_text(
        json.dumps(
            {
                "status": "verified",
                "expected_sha256": expected_sha256,
                "readback_sha256": expected_sha256,
            },
            ensure_ascii=False,
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def _selection_controller_with_completed_run(
    tmp_path: Path,
    *,
    pm_output: str | None = None,
    fail_worker_id: str | None = None,
    empty_output_worker_id: str | None = None,
    legacy_summary: bool = False,
    raw_complete_summary: bool = False,
    selection_runner: _FakeSelectionOpenClawRunner | None = None,
) -> tuple[SelectionController, _FakeSelectionOpenClawRunner]:
    store = SelectionRunStore()
    summary_path = tmp_path / "candidate-cache-summary.md"
    if legacy_summary or raw_complete_summary:
        summary_lines = (
            [
                "# A股候选缓存",
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
                "# A股候选缓存",
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
        (tmp_path / "candidate-cache.json").write_text(
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
        (tmp_path / "candidate-cache-manifest.json").write_text(
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
                    "# A股候选缓存",
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
    if not (tmp_path / "candidate-cache.json").is_file():
        (tmp_path / "candidate-cache.json").write_text(
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
    if not legacy_summary:
        _add_candidate_cache_summary_fields(tmp_path / "candidate-cache.json")
    if not (tmp_path / "candidate-cache-manifest.json").is_file():
        (tmp_path / "candidate-cache-manifest.json").write_text(
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
    plan = SelectionRunPlan(
        selection_run_id=run_id,
        market=SelectionMarket.CN_A,
        profile=SelectionProfile.CN_A,
        trade_date="2026-05-26",
        lookback_trading_days=260,
        universe_scope="all_a_shares",
        data_need_audit_ref="plan://sel-run-08",
        approved_strategy_config_ref="config://approved",
        trigger_source=SelectionTriggerSource.SCHEDULED,
    )
    body_path = tmp_path / "candidate-cache.md"
    body_text = "\n".join(
        [
            "# A股候选缓存",
            "",
            summary_path.read_text(encoding="utf-8"),
        ]
    )
    body_path.write_text(body_text, encoding="utf-8")
    body_sha = sha256(body_text.encode("utf-8")).hexdigest()
    manifest_path = tmp_path / "candidate-cache-manifest.json"
    manifest_payload = _candidate_cache_manifest_payload(run_id=run_id, body_sha=body_sha)
    manifest_path.write_text(json.dumps(manifest_payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    _write_readback_log(body_path, expected_sha256=body_sha)
    _write_readback_log(manifest_path, expected_sha256=sha256(manifest_path.read_bytes()).hexdigest())
    columnar_manifest = _write_columnar_manifest(plan)
    candidate_cache_ref = CandidateCacheRef(
        selection_run_id=run_id,
        material_id="mat-sel-run-08",
        l1_uri=str(body_path),
        content_sha256=body_sha,
        manifest_ref=str(manifest_path),
        approved_at="2026-05-26T09:00:00+00:00",
        expires_at="2026-05-27T09:00:00+00:00",
        cache_summary_ref=str(summary_path),
    )
    store.save_data_run_record(
        SelectionDataRunRecord(
            run_plan=plan,
            data_run=SelectionDataRun(
                selection_run_id=run_id,
                status=SelectionDataRunStatus.COMPLETED,
                normalized_refs=(f"dataset://normalized/CN_A/daily/{run_id}",),
                provider_attempt_refs=(f"attempt://{run_id}",),
                select_data_plan_ref=f"select-data-plan://selection/{run_id}/2026-05-26",
                warehouse_check_ref=columnar_manifest.warehouse_check_ref,
                columnar_manifest_ref=columnar_manifest.manifest_ref,
                columnar_manifest_sha256=SelectionColumnarWarehouse.default().manifest_sha256(
                    columnar_manifest.manifest_ref
                ),
                candidate_cache_ref=candidate_cache_ref,
                completed_at="2026-05-26T09:01:00+00:00",
            ),
            manifest=CandidateCacheManifest(
                schema_version="sel-04-candidate-cache-v1",
                selection_run_id=run_id,
                market=SelectionMarket.CN_A,
                profile=SelectionProfile.CN_A,
                trade_date="2026-05-26",
                candidate_count=3,
                source_lineage_refs=("lineage://a",),
                cache_body_sha256=body_sha,
                strategy_config_ref="config://approved",
                readback_status=CandidateCacheReadbackStatus.VERIFIED,
                stage="approving_candidate_cache",
                target="candidate_cache",
            ),
        )
    )
    resolved_selection_runner = selection_runner or _FakeSelectionOpenClawRunner(
        pm_output=pm_output,
        fail_worker_id=fail_worker_id,
        empty_output_worker_id=empty_output_worker_id,
    )
    selection_controller = SelectionController(
        store=store,
        now_fn=lambda: datetime.fromisoformat("2026-05-26T10:00:00+00:00").astimezone(UTC),
        openclaw=OpenClawClient(resolved_selection_runner),
        workflow_evidence_root=tmp_path / "selection-workflows",
    )
    return selection_controller, resolved_selection_runner


@pytest.mark.integration
def test_select_command_no_completed_run_returns_unavailable_and_does_not_touch_report_queue() -> None:
    controller, chat_transport, workflow_runner = _build_controller()

    result = controller.send_chat_message(request_id="sel-08-no-run", context_id="ctx-no-run", text="/select")

    assert "error" not in result
    assert result["selection"]["code"] == "unavailable"
    assert result["selection"]["unavailableCode"] == "no_completed_selection_run"
    assert result["selection"]["dataRefresh"]["status"] == "not_configured"
    assert Path(result["selection"]["evidencePath"]).is_file()
    assert chat_transport.calls == 0
    assert workflow_runner.calls == 0
    assert result["context"]["kind"] == "normal_chat"


@pytest.mark.integration
def test_select_command_no_completed_run_requests_background_data_refresh(tmp_path: Path) -> None:
    refresh_calls: list[dict[str, object]] = []

    def _refresh(**kwargs: object) -> SelectionDataRefreshResult:
        refresh_calls.append(dict(kwargs))
        return SelectionDataRefreshResult(
            status="started",
            selection_run_id="sel-refresh-1",
            trade_date="2026-05-26",
            reason="no_completed_selection_run",
        )

    selection_controller = SelectionController(
        store=SelectionRunStore(),
        now_fn=lambda: datetime.fromisoformat("2026-05-26T10:00:00+00:00").astimezone(UTC),
        scheduler_enqueue=_refresh,
        workflow_evidence_root=tmp_path / "selection-workflows",
    )
    controller, chat_transport, workflow_runner = _build_controller(selection_controller=selection_controller)

    result = controller.send_chat_message(request_id="sel-08-refresh", context_id="ctx-refresh", text="/select")

    assert "error" not in result
    assert result["selection"]["code"] == "data_refresh_requested"
    assert result["selection"]["unavailableCode"] == "no_completed_selection_run"
    assert result["selection"]["dataRefresh"] == {
        "status": "started",
        "selectionRunId": "sel-refresh-1",
        "tradeDate": "2026-05-26",
        "reason": "no_completed_selection_run",
        "errorCode": None,
    }
    assert len(refresh_calls) == 1
    assert refresh_calls[0]["select_workflow_run_id"] == result["selection"]["workflowRunId"]
    evidence_payload = json.loads(Path(result["selection"]["evidencePath"]).read_text(encoding="utf-8"))
    assert evidence_payload["data_refresh"]["status"] == "started"
    assert evidence_payload["data_refresh"]["selection_run_id"] == "sel-refresh-1"
    assert chat_transport.calls == 0
    assert workflow_runner.calls == 0


@pytest.mark.integration
def test_select_command_refreshes_current_trade_date_instead_of_reusing_older_completed_run(tmp_path: Path) -> None:
    refresh_calls: list[dict[str, object]] = []

    def _refresh(**kwargs: object) -> SelectionDataRefreshResult:
        refresh_calls.append(dict(kwargs))
        return SelectionDataRefreshResult(
            status="started",
            selection_run_id="sel-refresh-20260604",
            trade_date="2026-06-04",
            reason="no_completed_selection_run",
        )

    selection_store = SelectionRunStore()
    old_selection_controller, _ = _selection_controller_with_completed_run(tmp_path)
    old_store = old_selection_controller._store  # noqa: SLF001
    for record in old_store._runs.values():  # noqa: SLF001
        selection_store.save_data_run_record(record)
    selection_controller = SelectionController(
        store=selection_store,
        now_fn=lambda: datetime.fromisoformat("2026-06-04T17:30:00+00:00").astimezone(UTC),
        scheduler_enqueue=_refresh,
        default_trade_date_resolver=lambda value: value or "2026-06-04",
        workflow_evidence_root=tmp_path / "selection-workflows-current-date",
    )
    controller, chat_transport, workflow_runner = _build_controller(selection_controller=selection_controller)

    result = controller.send_chat_message(request_id="sel-08-current-date-refresh", context_id="ctx-refresh-current", text="/select")

    assert "error" not in result
    assert result["selection"]["code"] == "data_refresh_requested"
    assert result["selection"]["unavailableCode"] == "no_completed_selection_run"
    assert result["selection"]["dataRefresh"]["tradeDate"] == "2026-06-04"
    assert len(refresh_calls) == 1
    request = refresh_calls[0]["request"]
    assert isinstance(request, object)
    assert getattr(request, "trade_date") == "2026-06-04"
    evidence_payload = json.loads(Path(result["selection"]["evidencePath"]).read_text(encoding="utf-8"))
    assert evidence_payload["trade_date"] == "2026-06-04"
    assert chat_transport.calls == 0
    assert workflow_runner.calls == 0


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
    reader_report_path = Path(evidence_payload["reader_report_path"])
    assert reader_report_path.is_file()
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
    report_markdown = result["selection"]["readerReportMarkdown"]
    assert result["selection"]["readerReportPath"] == str(reader_report_path)
    assert reader_report_path.read_text(encoding="utf-8").strip() == report_markdown.strip()
    assert "进入 `/report`" in message
    assert "观察：" in message
    assert "放弃：" in message
    assert "命中6/8" in message
    assert "候选缓存：" not in message
    assert "# A股选股报告" in report_markdown
    assert "## 一、候选分组结论" in report_markdown
    assert "## 二、正方策略观点" in report_markdown
    assert "策略评审：优先关注 600519.SH 与 000858.SZ。" in report_markdown
    assert "## 三、反方审查意见" in report_markdown
    assert "反方审查：300750.SZ 风险暴露偏高。" in report_markdown
    assert "## 四、综合取舍" in report_markdown
    assert "综合判断：优先进入组合评审、继续观察、暂不继续。" in report_markdown
    assert "## 五、最终分流决策" in report_markdown
    assert "进入 /report:" in report_markdown
    assert "## 六、策略命中与分析过程" in report_markdown
    assert "放量上涨" in report_markdown
    assert "量价放量" in report_markdown
    assert "| 600519.SH 贵州茅台 | 放量上涨 |" in report_markdown
    assert "| 000858.SZ 五粮液 | 量价放量 |" in report_markdown
    assert "先看每只股票命中的策略条件数量" in report_markdown
    assert "具体条件以本节逐只核对为准" in report_markdown
    assert "myhhub_volume_rise" not in report_markdown
    assert "sequoia_ma_volume" not in report_markdown
    assert "## 七、数据范围与质量" in report_markdown
    assert "总分" in report_markdown
    assert "实际指标值" in report_markdown
    assert "策略配置版本" not in report_markdown
    assert "权重版本" not in report_markdown
    assert "命中字段" not in report_markdown
    assert "tie-break" not in report_markdown
    assert "slope_10d" not in report_markdown
    assert "low_atr" not in report_markdown
    assert "滚动市盈率" in report_markdown
    assert "滚动市销率" in report_markdown
    assert "已批准的正向策略评审" in report_markdown
    assert "## 八、进入 `/report` 的验证重点" in report_markdown
    _assert_candidate_fact_body_is_reader_chinese(report_markdown)
    assert "买入" not in message
    assert "卖出" not in message
    assert "持有" not in message
    assert "目标价" not in message
    assert "止损" not in message

    assert chat_transport.calls == 0
    assert workflow_runner.calls == 0
    assert result["context"]["kind"] == "normal_chat"


@pytest.mark.integration
def test_select_command_exposes_current_worker_progress_while_running(tmp_path: Path) -> None:
    blocking_runner = _BlockingSelectionOpenClawRunner()
    selection_controller, _ = _selection_controller_with_completed_run(tmp_path, selection_runner=blocking_runner)
    result_holder: dict[str, object] = {}
    errors: list[BaseException] = []

    def _run_select() -> None:
        try:
            result_holder["result"] = selection_controller.handle_select_command(
                raw_text="/select",
                request_id="sel-08-progress",
                user_id="ctx-progress",
            )
        except BaseException as exc:  # pragma: no cover - surfaced by assertions below
            errors.append(exc)

    thread = Thread(target=_run_select, daemon=True)
    thread.start()
    assert blocking_runner.worker_started.wait(timeout=2)

    snapshot = selection_controller.latest_progress_for_user()
    progress = snapshot["selectionProgress"]
    assert isinstance(progress, dict)
    assert progress["kind"] == "selection_workflow"
    assert progress["statusLabel"] == "选股中"
    assert progress["workerStatusLabels"] == [
        "策略评审：执行中",
        "反方评审：等待启动",
        "整合排序：等待启动",
        "组合经理：等待启动",
    ]

    blocking_runner.release_worker.set()
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert errors == []
    result = result_holder["result"]
    assert getattr(result, "code").value == "completed"
    assert selection_controller.latest_progress_for_user() == {"selectionProgress": None}


@pytest.mark.integration
def test_select_command_rejects_approved_candidate_cache_with_missing_strategy_fields(tmp_path: Path) -> None:
    selection_controller, selection_runner = _selection_controller_with_completed_run(tmp_path)
    payload_path = tmp_path / "candidate-cache.json"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    payload["candidates"][0]["feature_values"]["strategy_missing_field_count"] = 56
    payload_path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    controller, chat_transport, workflow_runner = _build_controller(selection_controller=selection_controller)

    result = controller.send_chat_message(request_id="sel-08-missing-strategy-fields", context_id="ctx-missing", text="/select")

    assert "error" not in result
    assert result["selection"]["code"] == "unavailable"
    assert result["selection"]["unavailableCode"] == "candidate_cache_integrity_failed"
    evidence_payload = json.loads(Path(result["selection"]["evidencePath"]).read_text(encoding="utf-8"))
    assert evidence_payload["reason"] == "candidate_cache_strategy_fields_missing: ticker=600519.SH missing 56/64 approved strategy fields"
    assert selection_runner.payloads == []
    assert chat_transport.calls == 0
    assert workflow_runner.calls == 0


@pytest.mark.integration
def test_select_command_rejects_legacy_candidate_cache_summary_fields(tmp_path: Path) -> None:
    selection_controller, _ = _selection_controller_with_completed_run(tmp_path, legacy_summary=True)
    controller, chat_transport, workflow_runner = _build_controller(selection_controller=selection_controller)

    result = controller.send_chat_message(request_id="sel-08-legacy-summary", context_id="ctx-legacy", text="/select")

    assert "error" not in result
    assert result["selection"]["code"] == "unavailable"
    assert result["selection"]["unavailableCode"] == "candidate_cache_integrity_failed"
    evidence_payload = json.loads(Path(result["selection"]["evidencePath"]).read_text(encoding="utf-8"))
    assert evidence_payload["reason"] == "candidate_cache_summary_fields_missing: ticker=600519.SH missing component_scores"
    assert chat_transport.calls == 0
    assert workflow_runner.calls == 0


@pytest.mark.integration
def test_select_command_rebuilds_candidate_cache_summary_with_raw_reader_field_names(tmp_path: Path) -> None:
    selection_controller, _ = _selection_controller_with_completed_run(tmp_path, raw_complete_summary=True)
    controller, chat_transport, workflow_runner = _build_controller(selection_controller=selection_controller)

    result = controller.send_chat_message(request_id="sel-08-raw-summary", context_id="ctx-raw", text="/select")

    assert "error" not in result
    assert result["selection"]["code"] == "completed"
    message = result["messages"][-1]["text"]
    report_markdown = result["selection"]["readerReportMarkdown"]
    assert "候选缓存：" not in message
    _assert_candidate_fact_body_is_reader_chinese(report_markdown)
    assert "策略配置版本" not in report_markdown
    assert "命中字段" not in report_markdown
    assert "策略变体" not in report_markdown
    assert "## 六、策略命中与分析过程" in report_markdown
    assert "放量上涨" in report_markdown
    assert "## 七、数据范围与质量" in report_markdown
    assert "hit_volume_breakout" not in report_markdown
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
    assert confirm_result.handoff_request["companyName"] == "贵州茅台"
    assert confirm_result.queue_payload["task"]["companyName"] == "贵州茅台"
    assert confirm_result.queue_payload["task"]["selectionContextRef"] == decision_payload["approved_material_id"]
    assert confirm_result.queue_payload["task"]["selectionStageMarker"] == "selection_report_handoff"
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
