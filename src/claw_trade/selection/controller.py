from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable, Mapping

from claw_trade.runtime.openclaw_client import OpenClawClient
from claw_trade.selection.candidate_pack import (
    CandidatePackError,
    validate_candidate_pack_payload_strategy_field_completeness,
)
from claw_trade.selection.dispatch import (
    build_fixed_selection_dispatches,
    execute_selection_dispatches,
    selection_dispatch_worker_order,
)
from claw_trade.selection.models import (
    DEFAULT_SELECTION_STRATEGY_CONFIG_VERSION,
    DEFAULT_SELECTION_WEIGHT_VERSION,
    CandidatePackRef,
    DecisionTicker,
    SelectionDecision,
    SelectionMarket,
    SelectionProfile,
    SelectionSystemContextPolicy,
    SelectionWorkerDispatch,
    SelectionWorkerId,
    SelectRequest,
)
from claw_trade.selection.refresh import SelectionDataRefreshResult
from claw_trade.selection.store import (
    LatestCompletedSelectionRun,
    SelectionRunStore,
    SelectUnavailableCode,
    resolve_latest_terminal_selection_run,
)
from claw_trade.workflow.models import WorkflowEntryPoint


class SelectCommandCode(StrEnum):
    COMPLETED = "completed"
    DATA_REFRESH_REQUESTED = "data_refresh_requested"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"
    BLOCKED_ASK_HUMAN = "blocked_ask_human"


@dataclass(frozen=True)
class SelectCommandResult:
    code: SelectCommandCode
    chat_text: str
    select_workflow_run_id: str
    evidence_path: Path
    unavailable_code: SelectUnavailableCode | None = None
    failure_reason: str | None = None
    decision: SelectionDecision | None = None
    data_refresh: SelectionDataRefreshResult | None = None

    @property
    def ok(self) -> bool:
        return self.code == SelectCommandCode.COMPLETED


@dataclass(frozen=True)
class SelectReadGateResult:
    is_available: bool
    unavailable_code: SelectUnavailableCode | None
    latest_completed_run: LatestCompletedSelectionRun | None

    @classmethod
    def unavailable(cls, code: SelectUnavailableCode) -> SelectReadGateResult:
        return cls(
            is_available=False,
            unavailable_code=code,
            latest_completed_run=None,
        )

    @classmethod
    def available(cls, run: LatestCompletedSelectionRun) -> SelectReadGateResult:
        return cls(
            is_available=True,
            unavailable_code=None,
            latest_completed_run=run,
        )


@dataclass(frozen=True)
class _DecisionParseResult:
    decision: SelectionDecision | None
    invalid_reason: str | None = None
    blocked_reason: str | None = None


_PM_DECISION_MATERIAL_TARGET = "selection_portfolio_decision"
_PM_DECISION_MATERIAL_TYPE = "pm_decision"
_CANDIDATE_PACK_REQUIRED_SUMMARY_LABELS = (
    "总分",
    "分项得分",
    "策略来源",
    "策略变体",
    "命中字段",
    "实际指标值",
    "风险扣分",
    "数据缺口扣分",
    "tie-break",
    "权重版本",
    "策略配置版本",
)

_READER_VISIBLE_FIELD_LABELS = {
    "amount": "成交额",
    "vol_ratio": "量比",
    "strategy_hit_count": "命中策略数",
    "data_gap_penalty_score": "数据缺口扣分",
    "risk_penalty_score": "风险扣分",
    "evidence_completeness_score": "证据完整度",
    "strategy_hit_coverage_score": "策略命中覆盖",
    "strategy_coverage_score": "策略命中覆盖",
    "strategy_inner_strength_score": "策略内强度",
    "strategy_strength_score": "策略内强度",
    "rps_trend_score": "RPS/趋势强度",
    "industry_theme_score": "行业/主题强度",
    "industry_theme_strength_score": "行业/主题强度",
    "intraday_return_pct": "日内涨跌幅",
    "close_open_ratio": "收盘/开盘比",
    "strategy_missing_field_count": "缺失策略字段数",
    "strategy_required_field_count": "策略必需字段数",
    "strategy_variant_count": "策略变体数",
    "score": "总分",
    "open": "开盘价",
    "close": "收盘价",
    "high": "最高价",
    "low": "最低价",
    "volume": "成交量",
    "range_pct": "振幅",
    "risk_penalty": "风险扣分",
    "data_gap_penalty": "数据缺口扣分",
    "liquidity_score": "流动性/可交易性",
    "liquidity_tradability_score": "流动性/可交易性",
    "tradability_score": "流动性/可交易性",
    "hit_volume_ratio": "命中量比",
    "hit_volume_breakout": "命中放量突破",
}

_READER_VISIBLE_RAW_FIELD_NAMES = tuple(_READER_VISIBLE_FIELD_LABELS)

_REFRESHABLE_UNAVAILABLE_CODES = frozenset(
    {
        SelectUnavailableCode.NO_COMPLETED_SELECTION_RUN,
        SelectUnavailableCode.NO_CANDIDATE_SELECTION_RUN,
        SelectUnavailableCode.STALE_SELECTION_RUN,
        SelectUnavailableCode.CANDIDATE_PACK_NOT_APPROVED,
        SelectUnavailableCode.SELECTION_WAREHOUSE_CHECK_MISSING,
    }
)


class SelectionController:
    """
    SEL-08 scope: /select 命令只读 latest terminal run；只有 completed + approved pack 才走 SEL-07 固定 dispatch。
    """

    def __init__(
        self,
        *,
        store: SelectionRunStore,
        now_fn: Callable[[], datetime] | None = None,
        openclaw: OpenClawClient | None = None,
        workflow_evidence_root: Path | None = None,
        provider_fetch: Callable[..., object] | None = None,
        scheduler_enqueue: Callable[..., object] | None = None,
        data_job_runner: Callable[..., object] | None = None,
    ) -> None:
        self._store = store
        self._now_fn = now_fn or _utc_now
        self._openclaw = openclaw
        self._workflow_evidence_root = workflow_evidence_root or Path("runs/selection/workflows")
        # 这些依赖保留给后续 SEL-03/SEL-08 注入；/select 不应调用。
        self._provider_fetch = provider_fetch
        self._scheduler_enqueue = scheduler_enqueue
        self._data_job_runner = data_job_runner

    def load_latest_completed_for_select(self, request: SelectRequest) -> SelectReadGateResult:
        resolved = resolve_latest_terminal_selection_run(
            store=self._store,
            market=request.market,
            profile=request.profile,
            trade_date=request.trade_date,
            now_fn=self._now_fn,
        )
        if resolved.is_available:
            assert resolved.run is not None
            return SelectReadGateResult.available(resolved.run)
        assert resolved.unavailable_code is not None
        return SelectReadGateResult.unavailable(resolved.unavailable_code)

    def build_fixed_selection_dispatches(
        self,
        *,
        request: SelectRequest,
        select_workflow_run_id: str,
        selection_run_id: str,
        evidence_root: str,
        candidate_pack_summary_md: str,
        approved_l1_materials: Mapping[SelectionWorkerId, str],
    ) -> tuple[SelectionWorkerDispatch, ...]:
        return build_fixed_selection_dispatches(
            request=request,
            select_workflow_run_id=select_workflow_run_id,
            selection_run_id=selection_run_id,
            evidence_root=Path(evidence_root),
            candidate_pack_summary_md=candidate_pack_summary_md,
            approved_l1_materials=approved_l1_materials,
        )

    def handle_select_command(
        self,
        *,
        raw_text: str,
        request_id: str,
        user_id: str | None = None,
    ) -> SelectCommandResult:
        request = _parse_select_request(raw_text=raw_text, request_id=request_id, user_id=user_id, now_fn=self._now_fn)
        workflow_run_id = _build_select_workflow_run_id(request_id=request.request_id, now_fn=self._now_fn)
        evidence_dir = self._workflow_evidence_root / workflow_run_id
        evidence_dir.mkdir(parents=True, exist_ok=True)

        gate = self.load_latest_completed_for_select(request)
        if not gate.is_available:
            assert gate.unavailable_code is not None
            payload = _base_workflow_evidence_payload(
                request=request,
                workflow_run_id=workflow_run_id,
                status=gate.unavailable_code.value,
                selection_run_id=None,
                reason=gate.unavailable_code.value,
            )
            refresh_result = self._request_data_refresh_if_needed(
                request=request,
                unavailable_code=gate.unavailable_code,
                workflow_run_id=workflow_run_id,
            )
            if refresh_result is not None:
                payload["data_refresh"] = _data_refresh_payload(refresh_result)
                evidence_path = _write_selection_workflow_evidence(evidence_dir=evidence_dir, payload=payload)
                if refresh_result.status in {"started", "already_running"}:
                    return SelectCommandResult(
                        code=SelectCommandCode.DATA_REFRESH_REQUESTED,
                        chat_text=_data_refresh_chat_text(refresh_result),
                        select_workflow_run_id=workflow_run_id,
                        evidence_path=evidence_path,
                        unavailable_code=gate.unavailable_code,
                        data_refresh=refresh_result,
                    )
                return SelectCommandResult(
                    code=SelectCommandCode.UNAVAILABLE,
                    chat_text=_data_refresh_unavailable_chat_text(gate.unavailable_code, refresh_result),
                    select_workflow_run_id=workflow_run_id,
                    evidence_path=evidence_path,
                    unavailable_code=gate.unavailable_code,
                    failure_reason=refresh_result.error_code or refresh_result.reason,
                    data_refresh=refresh_result,
                )
            evidence_path = _write_selection_workflow_evidence(evidence_dir=evidence_dir, payload=payload)
            return SelectCommandResult(
                code=SelectCommandCode.UNAVAILABLE,
                chat_text=_unavailable_chat_text(gate.unavailable_code),
                select_workflow_run_id=workflow_run_id,
                evidence_path=evidence_path,
                unavailable_code=gate.unavailable_code,
            )

        latest = gate.latest_completed_run
        assert latest is not None
        if request.trade_date is None:
            request = replace(request, trade_date=latest.run_plan.trade_date)
        candidate_pack_ref = latest.data_run.candidate_pack_ref
        if candidate_pack_ref is None:
            payload = _base_workflow_evidence_payload(
                request=request,
                workflow_run_id=workflow_run_id,
                status=SelectUnavailableCode.CANDIDATE_PACK_NOT_APPROVED.value,
                selection_run_id=latest.run_plan.selection_run_id,
                reason=SelectUnavailableCode.CANDIDATE_PACK_NOT_APPROVED.value,
            )
            evidence_path = _write_selection_workflow_evidence(evidence_dir=evidence_dir, payload=payload)
            return SelectCommandResult(
                code=SelectCommandCode.UNAVAILABLE,
                chat_text=_unavailable_chat_text(SelectUnavailableCode.CANDIDATE_PACK_NOT_APPROVED),
                select_workflow_run_id=workflow_run_id,
                evidence_path=evidence_path,
                unavailable_code=SelectUnavailableCode.CANDIDATE_PACK_NOT_APPROVED,
            )

        candidate_pack_strategy_error = _candidate_pack_strategy_completeness_error(candidate_pack_ref)
        if candidate_pack_strategy_error is not None:
            payload = _base_workflow_evidence_payload(
                request=request,
                workflow_run_id=workflow_run_id,
                status=SelectUnavailableCode.CANDIDATE_PACK_INTEGRITY_FAILED.value,
                selection_run_id=latest.run_plan.selection_run_id,
                reason=candidate_pack_strategy_error,
            )
            evidence_path = _write_selection_workflow_evidence(evidence_dir=evidence_dir, payload=payload)
            return SelectCommandResult(
                code=SelectCommandCode.UNAVAILABLE,
                chat_text=_unavailable_chat_text(SelectUnavailableCode.CANDIDATE_PACK_INTEGRITY_FAILED),
                select_workflow_run_id=workflow_run_id,
                evidence_path=evidence_path,
                unavailable_code=SelectUnavailableCode.CANDIDATE_PACK_INTEGRITY_FAILED,
            )

        if self._openclaw is None:
            payload = _base_workflow_evidence_payload(
                request=request,
                workflow_run_id=workflow_run_id,
                status=SelectCommandCode.BLOCKED_ASK_HUMAN.value,
                selection_run_id=latest.run_plan.selection_run_id,
                reason="selection_openclaw_not_configured",
            )
            evidence_path = _write_selection_workflow_evidence(evidence_dir=evidence_dir, payload=payload)
            return SelectCommandResult(
                code=SelectCommandCode.BLOCKED_ASK_HUMAN,
                chat_text="`/select` 当前不可用：选股执行通道未配置，请联系维护者确认。",
                select_workflow_run_id=workflow_run_id,
                evidence_path=evidence_path,
                failure_reason="selection_openclaw_not_configured",
            )

        summary_md = _load_candidate_pack_summary(candidate_pack_ref)
        allowed_ticker_companies = _extract_allowed_ticker_companies_from_summary(summary_md)
        allowed_tickers = frozenset(allowed_ticker_companies)
        if not allowed_tickers:
            payload = _base_workflow_evidence_payload(
                request=request,
                workflow_run_id=workflow_run_id,
                status=SelectUnavailableCode.CANDIDATE_PACK_INTEGRITY_FAILED.value,
                selection_run_id=latest.run_plan.selection_run_id,
                reason="candidate_pack_summary_missing_allowed_tickers",
            )
            evidence_path = _write_selection_workflow_evidence(evidence_dir=evidence_dir, payload=payload)
            return SelectCommandResult(
                code=SelectCommandCode.UNAVAILABLE,
                chat_text=_unavailable_chat_text(SelectUnavailableCode.CANDIDATE_PACK_INTEGRITY_FAILED),
                select_workflow_run_id=workflow_run_id,
                evidence_path=evidence_path,
                unavailable_code=SelectUnavailableCode.CANDIDATE_PACK_INTEGRITY_FAILED,
            )

        approved_l1: dict[SelectionWorkerId, str] = {
            SelectionWorkerId.STRATEGIST: "[pending strategist material]",
            SelectionWorkerId.SKEPTIC: "[pending skeptic material]",
            SelectionWorkerId.MANAGER: "[pending manager material]",
        }
        dispatch_results: list[dict[str, str]] = []
        pm_raw_text: str | None = None

        for worker_id in selection_dispatch_worker_order():
            dispatches = self.build_fixed_selection_dispatches(
                request=request,
                select_workflow_run_id=workflow_run_id,
                selection_run_id=latest.run_plan.selection_run_id,
                evidence_root=str(evidence_dir / "dispatches"),
                candidate_pack_summary_md=summary_md,
                approved_l1_materials=approved_l1,
            )
            dispatch = _select_dispatch_for_worker(dispatches=dispatches, worker_id=worker_id)
            executions = execute_selection_dispatches(
                openclaw=self._openclaw,
                dispatches=(dispatch,),
                candidate_pack_ref=candidate_pack_ref,
                profile=request.profile.value,
            )
            if not executions:
                return _failed_result(
                    request=request,
                    workflow_run_id=workflow_run_id,
                    evidence_dir=evidence_dir,
                    selection_run_id=latest.run_plan.selection_run_id,
                    reason=f"worker_runtime_failed:no_execution:{worker_id.value}",
                )
            execution = executions[0]
            if execution.openclaw_result.status != "succeeded":
                return _failed_result(
                    request=request,
                    workflow_run_id=workflow_run_id,
                    evidence_dir=evidence_dir,
                    selection_run_id=latest.run_plan.selection_run_id,
                    reason=execution.openclaw_result.failure_reason or f"worker_runtime_failed:{worker_id.value}",
                )
            worker_output = _read_worker_output_text(execution.openclaw_result)
            if not worker_output:
                return _failed_result(
                    request=request,
                    workflow_run_id=workflow_run_id,
                    evidence_dir=evidence_dir,
                    selection_run_id=latest.run_plan.selection_run_id,
                    reason=f"artifact_approval_failed:{worker_id.value}:empty_output",
                )
            dispatch_results.append(
                {
                    "worker_id": worker_id.value,
                    "dispatch_id": dispatch.dispatch_id,
                    "evidence_dir": str(dispatch.evidence_dir),
                    "command_snapshot_path": str(execution.command_snapshot_path),
                }
            )
            if worker_id == SelectionWorkerId.PORTFOLIO_MANAGER:
                pm_raw_text = worker_output
            else:
                approved_l1[worker_id] = worker_output

        if pm_raw_text is None:
            return _failed_result(
                request=request,
                workflow_run_id=workflow_run_id,
                evidence_dir=evidence_dir,
                selection_run_id=latest.run_plan.selection_run_id,
                reason="selection_result_invalid:pm_output_missing",
            )

        pm_decision_material_id = _build_pm_decision_material_id(workflow_run_id=workflow_run_id)
        decision_parse = _parse_and_validate_selection_decision(
            pm_raw_text=pm_raw_text,
            workflow_run_id=workflow_run_id,
            allowed_tickers=allowed_tickers,
            allowed_ticker_companies=allowed_ticker_companies,
            approved_material_id=pm_decision_material_id,
        )
        if decision_parse.blocked_reason is not None:
            payload = _base_workflow_evidence_payload(
                request=request,
                workflow_run_id=workflow_run_id,
                status=SelectCommandCode.BLOCKED_ASK_HUMAN.value,
                selection_run_id=latest.run_plan.selection_run_id,
                reason=decision_parse.blocked_reason,
            )
            payload["dispatches"] = dispatch_results
            payload["pm_output_path"] = str(evidence_dir / "pm-selection-decision.md")
            (evidence_dir / "pm-selection-decision.md").write_text(pm_raw_text, encoding="utf-8")
            evidence_path = _write_selection_workflow_evidence(evidence_dir=evidence_dir, payload=payload)
            return SelectCommandResult(
                code=SelectCommandCode.BLOCKED_ASK_HUMAN,
                chat_text="`/select` 结果无法机械解析，已阻断自动处理，请人工复核 PM 决策文本。",
                select_workflow_run_id=workflow_run_id,
                evidence_path=evidence_path,
                failure_reason=decision_parse.blocked_reason,
            )

        if decision_parse.decision is None:
            assert decision_parse.invalid_reason is not None
            return _failed_result(
                request=request,
                workflow_run_id=workflow_run_id,
                evidence_dir=evidence_dir,
                selection_run_id=latest.run_plan.selection_run_id,
                reason=f"selection_result_invalid:{decision_parse.invalid_reason}",
            )

        decision = decision_parse.decision
        reader_text = _render_selection_reader_chat_message(
            decision,
            candidate_pack_summary_md=summary_md,
        )
        payload = _base_workflow_evidence_payload(
            request=request,
            workflow_run_id=workflow_run_id,
            status=SelectCommandCode.COMPLETED.value,
            selection_run_id=latest.run_plan.selection_run_id,
            reason="waiting_report_confirmation",
        )
        payload["dispatches"] = dispatch_results
        payload["decision"] = {
            "enter_report": [item.ticker for item in decision.enter_report],
            "watch": [item.ticker for item in decision.watch],
            "reject": [item.ticker for item in decision.reject],
            "enter_report_items": _decision_tickers_payload(decision.enter_report),
            "watch_items": _decision_tickers_payload(decision.watch),
            "reject_items": _decision_tickers_payload(decision.reject),
            "approved_material_id": decision.approved_material_id,
            "approval_status": "approved",
            "select_workflow_run_id": decision.select_workflow_run_id,
            "material_target": _PM_DECISION_MATERIAL_TARGET,
            "material_type": _PM_DECISION_MATERIAL_TYPE,
        }
        payload["pm_output_path"] = str(evidence_dir / "pm-selection-decision.md")
        (evidence_dir / "pm-selection-decision.md").write_text(pm_raw_text, encoding="utf-8")
        evidence_path = _write_selection_workflow_evidence(evidence_dir=evidence_dir, payload=payload)
        return SelectCommandResult(
            code=SelectCommandCode.COMPLETED,
            chat_text=reader_text,
            select_workflow_run_id=workflow_run_id,
            evidence_path=evidence_path,
            decision=decision,
        )

    def _request_data_refresh_if_needed(
        self,
        *,
        request: SelectRequest,
        unavailable_code: SelectUnavailableCode,
        workflow_run_id: str,
    ) -> SelectionDataRefreshResult | None:
        if unavailable_code not in _REFRESHABLE_UNAVAILABLE_CODES:
            return None
        if self._scheduler_enqueue is None:
            return SelectionDataRefreshResult(
                status="not_configured",
                selection_run_id=None,
                trade_date=request.trade_date,
                reason=unavailable_code.value,
                error_code="selection_data_refresh_not_configured",
            )
        try:
            raw_result = self._scheduler_enqueue(
                request=request,
                unavailable_code=unavailable_code,
                select_workflow_run_id=workflow_run_id,
            )
        except Exception as exc:  # noqa: BLE001
            return SelectionDataRefreshResult(
                status="failed",
                selection_run_id=None,
                trade_date=request.trade_date,
                reason=unavailable_code.value,
                error_code=f"{type(exc).__name__}: {exc}",
            )
        return _coerce_data_refresh_result(raw_result, default_reason=unavailable_code.value)


def _parse_select_request(
    *,
    raw_text: str,
    request_id: str,
    user_id: str | None,
    now_fn: Callable[[], datetime],
) -> SelectRequest:
    text = raw_text.strip()
    trade_date: str | None = None
    matched = re.match(r"^\s*/select(?:\s+(?P<trade_date>\d{4}-\d{2}-\d{2}))?\s*$", text, re.IGNORECASE)
    if matched and matched.group("trade_date"):
        trade_date = matched.group("trade_date")
        date.fromisoformat(trade_date)
    created_at = now_fn().isoformat()
    return SelectRequest(
        request_id=request_id,
        market=SelectionMarket.CN_A,
        profile=SelectionProfile.CN_A,
        trade_date=trade_date,
        user_id=user_id,
        created_at=created_at,
        entry_point=WorkflowEntryPoint.SELECT_COMMAND,
        system_context_policy=SelectionSystemContextPolicy.SINGLE_WORKER_MINIMAL,
    )


def _build_select_workflow_run_id(*, request_id: str, now_fn: Callable[[], datetime]) -> str:
    stamp = now_fn().strftime("%Y%m%dT%H%M%S")
    safe_request_id = re.sub(r"[^a-zA-Z0-9._-]+", "-", request_id).strip("-") or "request"
    return f"select-{stamp}-{safe_request_id}"


def _build_pm_decision_material_id(*, workflow_run_id: str) -> str:
    return f"selection-pm-decision-{workflow_run_id}"


def _select_dispatch_for_worker(
    *,
    dispatches: tuple[SelectionWorkerDispatch, ...],
    worker_id: SelectionWorkerId,
) -> SelectionWorkerDispatch:
    for dispatch in dispatches:
        if dispatch.worker_id == worker_id:
            return dispatch
    raise ValueError(f"dispatch missing worker {worker_id.value}")


def _load_candidate_pack_summary(candidate_pack_ref: CandidatePackRef) -> str:
    path = _resolve_selection_uri(candidate_pack_ref.pack_summary_ref)
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return ""
    if _candidate_pack_summary_has_required_labels(text):
        return text
    rebuilt = _rebuild_candidate_pack_summary_from_json(candidate_pack_ref)
    if rebuilt is not None:
        return rebuilt
    raw_field = _reader_visible_raw_field_name(text)
    if raw_field is not None:
        raise ValueError(f"candidate pack summary contains reader-visible raw field name: {raw_field}")
    return text


def _candidate_pack_summary_has_required_labels(summary_md: str) -> bool:
    return (
        all(label in summary_md for label in _CANDIDATE_PACK_REQUIRED_SUMMARY_LABELS)
        and _reader_visible_raw_field_name(summary_md) is None
    )


def _reader_visible_raw_field_name(summary_md: str) -> str | None:
    for key in _READER_VISIBLE_RAW_FIELD_NAMES:
        for marker in (f'"{key}"', f"'{key}'", f"{key}=", f"{key}:"):
            if marker in summary_md:
                return key
    return None


def _rebuild_candidate_pack_summary_from_json(candidate_pack_ref: CandidatePackRef) -> str | None:
    json_path = _candidate_pack_json_path(candidate_pack_ref)
    if json_path is None:
        return None
    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        return None

    sidecar = _candidate_pack_sidecar_payload(candidate_pack_ref)
    strategy_config_version = _first_text(
        payload.get("strategy_config_version"),
        sidecar.get("strategy_config_version"),
        _first_candidate_value(candidates, "strategy_config_version"),
        DEFAULT_SELECTION_STRATEGY_CONFIG_VERSION,
    )
    weight_version = _first_text(
        payload.get("weight_version"),
        sidecar.get("weight_version"),
        _first_candidate_value(candidates, "weight_version"),
        DEFAULT_SELECTION_WEIGHT_VERSION,
    )
    trade_date = _first_text(payload.get("trade_date"), sidecar.get("trade_date"), "-")
    market = _first_text(payload.get("market"), sidecar.get("market"), "-")
    candidate_count = _first_text(payload.get("candidate_count"), sidecar.get("candidate_count"), str(len(candidates)))

    lines = [
        "# A股候选事实包",
        "",
        "## 本轮范围",
        f"- 交易日：{trade_date}",
        f"- 市场：{market}",
        f"- 候选数量：{candidate_count}",
        f"- 策略配置版本：{strategy_config_version}",
        f"- 权重版本：{weight_version}",
        "",
        "## 候选事实表",
        "| 排名 | 股票代码 | 股票名称 | 行业 | 总分 | 分项得分 | 策略来源 | 策略变体 | 命中字段 | 实际指标值 | 风险扣分 | 数据缺口扣分 | 排序 tie-break 字段 | 数据质量 | 读者化来源摘要 |",
        "| --- | --- | --- | --- | ---: | --- | --- | --- | --- | --- | ---: | ---: | --- | --- | --- |",
    ]
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        row = _candidate_row_summary(candidate)
        lines.append(
            f"| {row['rank']} | {row['ticker']} | {row['company_name']} | {row['industry']} | {row['total_score']} | "
            f"{row['component_scores']} | {row['strategy_sources']} | {row['strategy_variants']} | {row['hit_fields']} | "
            f"{row['actual_metric_values']} | {row['risk_penalty']} | {row['data_gap_penalty']} | {row['tie_break_fields']} | "
            f"{row['data_quality']} | {row['source_summary']} |"
        )

    data_quality_summary = _reader_friendly_summary_text(
        _first_text(payload.get("data_quality_summary"), "数据质量：候选包未提供汇总文本。")
    )
    source_summary = _reader_friendly_summary_text(
        _first_text(payload.get("source_summary"), "来源摘要：候选包未提供汇总文本。")
    )
    lines.extend(
        (
            "",
            "## 策略命中明细",
            *_strategy_hit_lines(candidates),
            "",
            "## 排序与扣分说明",
            "- 总分：按已批准权重对同一批次特征值进行确定性计算。",
            "- 分项得分：展示可复算的策略覆盖、趋势、流动性、主题、证据完整度等子项。",
            "- 风险扣分与数据缺口扣分：仅展示确定性扣分值；缺字段时显示为空。",
            "- 排序 tie-break 字段：同分时使用的确定性排序字段值。",
            "",
            "## 字段说明",
            "- 策略来源与策略变体：来自已批准策略配置中的命中 id 解析结果。",
            "- 命中字段与实际指标值：展示本轮候选行已有的可复算字段值。",
            "- 数据质量：仅描述样本完整性，不包含研究结论。",
            "",
            "## 数据质量摘要",
            data_quality_summary,
            "",
            "## 来源摘要",
            source_summary,
        )
    )
    return "\n".join(lines).strip()


def _candidate_pack_json_path(candidate_pack_ref: CandidatePackRef) -> Path | None:
    summary_path = _resolve_selection_uri(candidate_pack_ref.pack_summary_ref)
    candidates = (summary_path.with_name("candidate-pack.json"),)
    for path in candidates:
        if path.is_file():
            return path
    body_path = _resolve_selection_uri(candidate_pack_ref.l1_uri)
    alt_path = body_path.with_name("candidate-pack.json")
    return alt_path if alt_path.is_file() else None


def _candidate_pack_strategy_completeness_error(candidate_pack_ref: CandidatePackRef) -> str | None:
    json_path = _candidate_pack_json_path(candidate_pack_ref)
    if json_path is None:
        return "candidate_pack_strategy_fields_missing: candidate-pack.json missing"
    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return f"candidate_pack_strategy_fields_missing: candidate-pack.json unreadable: {exc}"
    if not isinstance(payload, Mapping):
        return "candidate_pack_strategy_fields_missing: candidate-pack.json must be an object"
    try:
        validate_candidate_pack_payload_strategy_field_completeness(payload)
    except CandidatePackError as exc:
        return f"{exc.code}: {exc.reason}"
    return None


def _candidate_pack_sidecar_payload(candidate_pack_ref: CandidatePackRef) -> dict[str, object]:
    try:
        path = _resolve_selection_uri(candidate_pack_ref.manifest_ref)
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _candidate_row_summary(candidate: Mapping[str, object]) -> dict[str, str]:
    features = _mapping(candidate.get("feature_values"))
    strategy_hits = _string_tuple(candidate.get("strategy_hits"))
    component_scores = _mapping(candidate.get("component_scores")) or _legacy_component_scores(features)
    actual_metric_values = _mapping(candidate.get("actual_metric_values")) or features
    hit_fields = _mapping(candidate.get("hit_fields")) or _legacy_hit_fields(features)
    tie_break_fields = _mapping(candidate.get("tie_break_fields")) or _legacy_tie_break_fields(features)
    return {
        "rank": _first_text(candidate.get("rank"), "-"),
        "ticker": _first_text(candidate.get("ticker"), "-"),
        "company_name": _first_text(candidate.get("company_name"), "-"),
        "industry": _first_text(candidate.get("industry"), "-"),
        "total_score": _format_candidate_number(_first_value(candidate.get("total_score"), features.get("score"))),
        "component_scores": _format_visible_mapping(component_scores),
        "strategy_sources": _join_or_dash(_strategy_sources_from_hits(strategy_hits)),
        "strategy_variants": _join_or_dash(_strategy_variants_from_hits(strategy_hits)),
        "hit_fields": _format_visible_mapping(hit_fields),
        "actual_metric_values": _format_visible_mapping(actual_metric_values),
        "risk_penalty": _format_candidate_number(_first_value(candidate.get("risk_penalty"), features.get("risk_penalty_score"), features.get("risk_penalty"))),
        "data_gap_penalty": _format_candidate_number(
            _first_value(candidate.get("data_gap_penalty"), features.get("data_gap_penalty_score"), features.get("data_gap_penalty"))
        ),
        "tie_break_fields": _format_visible_mapping(tie_break_fields),
        "data_quality": _first_text(candidate.get("data_quality"), "-"),
        "source_summary": _first_text(candidate.get("source_summary"), "-"),
    }


def _legacy_component_scores(values: Mapping[str, object]) -> dict[str, object]:
    keys = {
        "strategy_hit_coverage_score",
        "strategy_coverage_score",
        "strategy_strength_score",
        "strategy_inner_strength_score",
        "rps_trend_score",
        "liquidity_score",
        "liquidity_tradability_score",
        "tradability_score",
        "industry_theme_strength_score",
        "industry_theme_score",
        "evidence_completeness_score",
    }
    return {key: value for key, value in values.items() if key in keys or key.endswith("_subscore")}


def _legacy_hit_fields(values: Mapping[str, object]) -> dict[str, object]:
    return {
        key: value
        for key, value in values.items()
        if key.startswith("hit_") or key.startswith("strategy_hit_") or key.endswith("_hit")
    }


def _legacy_tie_break_fields(values: Mapping[str, object]) -> dict[str, object]:
    candidates = ("score", "amount", "volume", "vol_ratio", "data_gap_penalty_score", "risk_penalty_score")
    return {key: values[key] for key in candidates if key in values}


def _strategy_hit_lines(candidates: list[object]) -> tuple[str, ...]:
    seen: set[tuple[str, str]] = set()
    lines: list[str] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        for hit in _string_tuple(candidate.get("strategy_hits")):
            source, variant = _split_strategy_hit_text(hit)
            key = (source, variant)
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"- 来源：{source or '-'}；策略变体：{variant or hit}。")
    return tuple(lines) if lines else ("- 本轮候选未记录策略命中。",)


def _strategy_sources_from_hits(hits: tuple[str, ...]) -> tuple[str, ...]:
    return _dedupe(tuple(source for source, _variant in (_split_strategy_hit_text(hit) for hit in hits) if source))


def _strategy_variants_from_hits(hits: tuple[str, ...]) -> tuple[str, ...]:
    return _dedupe(tuple(variant for _source, variant in (_split_strategy_hit_text(hit) for hit in hits) if variant))


def _split_strategy_hit_text(value: str) -> tuple[str, str]:
    text = value.strip()
    for separator in ("::", ":"):
        if separator in text:
            source, variant = text.split(separator, 1)
            return source.strip(), variant.strip()
    return "", text


def _mapping(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, dict) else {}


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def _first_candidate_value(candidates: list[object], key: str) -> object | None:
    for candidate in candidates:
        if isinstance(candidate, dict) and candidate.get(key) is not None:
            return candidate[key]
    return None


def _first_value(*values: object) -> object | None:
    for value in values:
        if value is not None:
            return value
    return None


def _first_text(*values: object) -> str:
    value = _first_value(*values)
    if value is None:
        return "-"
    text = str(value).strip()
    return text if text else "-"


def _format_visible_mapping(values: Mapping[str, object]) -> str:
    if not values:
        return "-"
    payload = {_reader_visible_label(str(key)): _model_visible_value(value) for key, value in sorted(values.items())}
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _reader_visible_label(key: str) -> str:
    return _READER_VISIBLE_FIELD_LABELS.get(key, key)


def _reader_friendly_summary_text(text: str) -> str:
    out = text.replace("BLOCKER", "阻断").replace("WARN", "提示")
    out = out.replace(" provider ", " 数据源 ").replace("provider 调用", "数据源调用")
    return out


def _format_candidate_number(value: object) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "-"
    if isinstance(value, int | float):
        return f"{float(value):.6f}"
    try:
        return f"{float(str(value)):.6f}"
    except ValueError:
        return str(value)


def _model_visible_value(value: object) -> float | int | str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, int | float | str):
        return value
    return str(value)


def _join_or_dash(values: tuple[str, ...]) -> str:
    return "、".join(values) if values else "-"


def _dedupe(values: tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return tuple(out)


def _resolve_selection_uri(ref: str) -> Path:
    prefix = "local://selection/"
    if not ref.startswith(prefix):
        return Path(ref)
    relative = ref[len(prefix) :].strip("/")
    segments = [part for part in relative.split("/") if part]
    if not segments or ".." in segments:
        raise ValueError(f"unsafe local selection uri: {ref}")
    return Path("runs/selection/artifacts") / Path(*segments)


def _extract_allowed_ticker_companies_from_summary(summary_md: str) -> dict[str, str]:
    allowed: dict[str, str] = {}
    for raw_line in summary_md.splitlines():
        line = raw_line.strip()
        if not line.startswith("|") or "---" in line:
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 3:
            continue
        ticker = cells[1].upper()
        company_name = cells[2]
        if not re.fullmatch(r"\d{6}\.(?:SH|SZ)", ticker, re.IGNORECASE):
            continue
        if not company_name or company_name in {"-", "股票名称", "公司", "名称"}:
            continue
        allowed.setdefault(ticker, company_name)
    return allowed


def _read_worker_output_text(result: Any) -> str:
    raw_output_path = getattr(result, "raw_output_path", None)
    if isinstance(raw_output_path, Path) and raw_output_path.is_file():
        content = raw_output_path.read_text(encoding="utf-8").strip()
        if content:
            return content
    first_response_path = getattr(result, "first_response_path", None)
    if isinstance(first_response_path, Path) and first_response_path.is_file():
        try:
            payload = json.loads(first_response_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return ""
        text = payload.get("text")
        if isinstance(text, str):
            return text.strip()
    return ""


def _parse_and_validate_selection_decision(
    *,
    pm_raw_text: str,
    workflow_run_id: str,
    allowed_tickers: frozenset[str],
    allowed_ticker_companies: Mapping[str, str] | None = None,
    approved_material_id: str,
) -> _DecisionParseResult:
    text = pm_raw_text.strip()
    sections = _extract_three_sections(text)
    if sections is None:
        return _DecisionParseResult(decision=None, invalid_reason="missing_required_sections")

    parsed: dict[str, tuple[DecisionTicker, ...]] = {}
    for key, lines in sections.items():
        rows = _parse_decision_rows(lines)
        if rows is None:
            return _DecisionParseResult(
                decision=None,
                blocked_reason=f"ambiguous_{key}_section_requires_human_review",
            )
        parsed[key] = rows

    all_tickers = [item.ticker for item in (*parsed["enter_report"], *parsed["watch"], *parsed["reject"])]
    if len(all_tickers) != len(set(all_tickers)):
        return _DecisionParseResult(decision=None, invalid_reason="ticker_duplicated_across_sections")
    for ticker in all_tickers:
        if ticker.upper() not in allowed_tickers:
            return _DecisionParseResult(decision=None, invalid_reason=f"ticker_not_in_allowed_set:{ticker}")
    if allowed_ticker_companies:
        for row in (*parsed["enter_report"], *parsed["watch"], *parsed["reject"]):
            expected_company = allowed_ticker_companies.get(row.ticker.upper())
            if expected_company is not None and row.company_name != expected_company:
                return _DecisionParseResult(
                    decision=None,
                    invalid_reason=f"ticker_company_mismatch:{row.ticker}:expected={expected_company}:actual={row.company_name}",
                )
    missing_tickers = sorted(allowed_tickers.difference(ticker.upper() for ticker in all_tickers))
    if missing_tickers:
        return _DecisionParseResult(
            decision=None,
            invalid_reason=f"candidate_classification_incomplete:missing={','.join(missing_tickers)}",
        )
    try:
        decision = SelectionDecision(
            select_workflow_run_id=workflow_run_id,
            enter_report=parsed["enter_report"],
            watch=parsed["watch"],
            reject=parsed["reject"],
            report_questions=None,
            source_summary=None,
            approved_material_id=approved_material_id,
        )
    except ValueError as exc:
        return _DecisionParseResult(decision=None, invalid_reason=str(exc))
    return _DecisionParseResult(decision=decision)


def _extract_three_sections(text: str) -> dict[str, tuple[str, ...]] | None:
    label_by_heading = {
        "进入/report": "enter_report",
        "观察": "watch",
        "放弃": "reject",
    }
    current: str | None = None
    collected: dict[str, list[str]] = {"enter_report": [], "watch": [], "reject": []}
    seen_labels: set[str] = set()
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        heading = _normalize_heading(stripped)
        mapped = label_by_heading.get(heading)
        if mapped is not None:
            current = mapped
            seen_labels.add(mapped)
            continue
        if current is not None:
            collected[current].append(stripped)
    if seen_labels != {"enter_report", "watch", "reject"}:
        return None
    return {key: tuple(value) for key, value in collected.items()}


def _normalize_heading(line: str) -> str:
    stripped = line
    stripped = re.sub(r"^[#>*\-\s]+", "", stripped)
    stripped = stripped.replace("：", ":").replace(" ", "").strip()
    if stripped.endswith(":"):
        stripped = stripped[:-1].strip()
    return stripped.lower()


def _parse_decision_rows(lines: tuple[str, ...]) -> tuple[DecisionTicker, ...] | None:
    rows: list[DecisionTicker] = []
    has_parseable_content = False
    for line in lines:
        normalized = re.sub(r"^\s*(?:[-*•]|\d+[.)]\s+)\s*", "", line).strip()
        if not normalized:
            continue
        if normalized in {"无", "暂无", "空"}:
            continue
        has_parseable_content = True
        pipe_match = re.match(r"^(?P<ticker>\S+)\s*[|｜]\s*(?P<company>[^|｜:：]+)\s*[|｜]\s*(?P<reason>.+)$", normalized)
        if pipe_match:
            rows.append(
                DecisionTicker(
                    ticker=pipe_match.group("ticker").strip().upper(),
                    company_name=pipe_match.group("company").strip(),
                    rationale_excerpt=pipe_match.group("reason").strip(),
                )
            )
            continue
        colon_match = re.match(
            r"^(?P<ticker>[A-Za-z0-9./_-]+)\s+(?P<company>[^:：]+?)\s*[:：]\s*(?P<reason>.+)$",
            normalized,
        )
        if colon_match:
            rows.append(
                DecisionTicker(
                    ticker=colon_match.group("ticker").strip().upper(),
                    company_name=colon_match.group("company").strip(),
                    rationale_excerpt=colon_match.group("reason").strip(),
                )
            )
            continue
        return None
    if not has_parseable_content:
        return ()
    return tuple(rows)


def _render_selection_reader_chat_message(
    decision: SelectionDecision,
    *,
    candidate_pack_summary_md: str | None = None,
) -> str:
    lines: list[str] = []
    lines.append("`/select` 已完成，本轮仅进入等待确认，不会自动启动 `/report`。")
    lines.append("")
    lines.append("进入 `/report`：")
    lines.extend(_render_rows(decision.enter_report))
    lines.append("")
    lines.append("观察：")
    lines.extend(_render_rows(decision.watch))
    lines.append("")
    lines.append("放弃：")
    lines.extend(_render_rows(decision.reject))
    summary = (candidate_pack_summary_md or "").strip()
    if summary:
        lines.append("")
        lines.append("候选事实包：")
        lines.append(summary)
    return "\n".join(lines).strip()


def _render_rows(rows: tuple[DecisionTicker, ...]) -> list[str]:
    if not rows:
        return ["- 无"]
    rendered: list[str] = []
    for row in rows:
        rendered.append(f"- {row.ticker} {row.company_name}：{row.rationale_excerpt}")
    return rendered


def _decision_tickers_payload(rows: tuple[DecisionTicker, ...]) -> list[dict[str, str]]:
    return [
        {
            "ticker": row.ticker,
            "company_name": row.company_name,
            "rationale_excerpt": row.rationale_excerpt,
        }
        for row in rows
    ]


def _base_workflow_evidence_payload(
    *,
    request: SelectRequest,
    workflow_run_id: str,
    status: str,
    selection_run_id: str | None,
    reason: str,
) -> dict[str, object]:
    return {
        "select_workflow_run_id": workflow_run_id,
        "selection_run_id": selection_run_id,
        "request_id": request.request_id,
        "market": request.market.value,
        "profile": request.profile.value,
        "trade_date": request.trade_date,
        "status": status,
        "reason": reason,
        "entry_point": request.entry_point.value,
        "system_context_policy": request.system_context_policy.value,
    }


def _write_selection_workflow_evidence(*, evidence_dir: Path, payload: dict[str, object]) -> Path:
    path = evidence_dir / "selection-workflow-evidence.json"
    path.write_text(f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n", encoding="utf-8")
    return path


def _coerce_data_refresh_result(raw_result: object, *, default_reason: str) -> SelectionDataRefreshResult:
    if isinstance(raw_result, SelectionDataRefreshResult):
        return raw_result
    if isinstance(raw_result, Mapping):
        return SelectionDataRefreshResult(
            status=str(raw_result.get("status") or "started"),
            selection_run_id=_optional_result_text(raw_result.get("selection_run_id")),
            trade_date=_optional_result_text(raw_result.get("trade_date")),
            reason=str(raw_result.get("reason") or default_reason),
            error_code=_optional_result_text(raw_result.get("error_code")),
        )
    selection_run_id = _optional_result_text(getattr(raw_result, "selection_run_id", None))
    trade_date = _optional_result_text(getattr(raw_result, "trade_date", None))
    status = str(getattr(raw_result, "status", "started") or "started")
    reason = str(getattr(raw_result, "reason", default_reason) or default_reason)
    error_code = _optional_result_text(getattr(raw_result, "error_code", None))
    return SelectionDataRefreshResult(
        status=status,
        selection_run_id=selection_run_id,
        trade_date=trade_date,
        reason=reason,
        error_code=error_code,
    )


def _optional_result_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _data_refresh_payload(refresh: SelectionDataRefreshResult) -> dict[str, object | None]:
    return {
        "status": refresh.status,
        "selection_run_id": refresh.selection_run_id,
        "trade_date": refresh.trade_date,
        "reason": refresh.reason,
        "error_code": refresh.error_code,
    }


def _data_refresh_chat_text(refresh: SelectionDataRefreshResult) -> str:
    if refresh.status == "already_running":
        return (
            "`/select` 发现当前没有可用候选包；已有后台补数任务在运行。"
            f" 批次：`{refresh.selection_run_id}`，交易日：`{refresh.trade_date}`。补完后再次发送 `/select`。"
        )
    return (
        "`/select` 发现当前数据不够，已启动后台补数和选股数据任务。"
        f" 批次：`{refresh.selection_run_id}`，交易日：`{refresh.trade_date}`。补完后再次发送 `/select`。"
    )


def _data_refresh_unavailable_chat_text(
    code: SelectUnavailableCode,
    refresh: SelectionDataRefreshResult,
) -> str:
    if refresh.status == "not_configured":
        return (
            f"{_unavailable_chat_text(code)} 已确认需要补数，但当前运行环境没有配置后台补数通道。"
        )
    return f"{_unavailable_chat_text(code)} 已尝试启动后台补数，但调度失败：{refresh.error_code or refresh.reason}。"


def _unavailable_chat_text(code: SelectUnavailableCode) -> str:
    messages = {
        SelectUnavailableCode.NO_COMPLETED_SELECTION_RUN: "`/select` 当前不可用：没有可用的已完成选股批次。",
        SelectUnavailableCode.NO_CANDIDATE_SELECTION_RUN: "`/select` 今日没有符合已批准策略条件的候选股票。",
        SelectUnavailableCode.STALE_SELECTION_RUN: "`/select` 当前不可用：最新选股批次已过期。",
        SelectUnavailableCode.CANDIDATE_PACK_NOT_APPROVED: "`/select` 当前不可用：候选池事实包尚未批准。",
        SelectUnavailableCode.CANDIDATE_PACK_HASH_MISMATCH: "`/select` 当前不可用：候选池完整性校验失败（hash 不一致）。",
        SelectUnavailableCode.CANDIDATE_PACK_INTEGRITY_FAILED: "`/select` 当前不可用：候选池完整性校验失败。",
        SelectUnavailableCode.CANDIDATE_PACK_LINEAGE_INCOMPLETE: "`/select` 当前不可用：候选池 lineage 不完整。",
        SelectUnavailableCode.SELECTION_WAREHOUSE_CHECK_MISSING: "`/select` 当前不可用：最新选股批次缺少 Mongo 仓库检查证据。",
        SelectUnavailableCode.SELECT_MARKET_UNSUPPORTED: "`/select` 当前暂不支持该市场。",
        SelectUnavailableCode.CRYPTO_SELECT_HISTORY_MISSING: "`/select` 当前不可用：Crypto 历史仓库尚未完成下载和入库。",
    }
    return messages.get(code, "`/select` 当前不可用：选股数据暂不可用。")


def _failed_result(
    *,
    request: SelectRequest,
    workflow_run_id: str,
    evidence_dir: Path,
    selection_run_id: str,
    reason: str,
) -> SelectCommandResult:
    payload = _base_workflow_evidence_payload(
        request=request,
        workflow_run_id=workflow_run_id,
        status=SelectCommandCode.FAILED.value,
        selection_run_id=selection_run_id,
        reason=reason,
    )
    evidence_path = _write_selection_workflow_evidence(evidence_dir=evidence_dir, payload=payload)
    return SelectCommandResult(
        code=SelectCommandCode.FAILED,
        chat_text="`/select` 执行失败，本轮结果未生效，请稍后重试。",
        select_workflow_run_id=workflow_run_id,
        evidence_path=evidence_path,
        failure_reason=reason,
    )


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)
