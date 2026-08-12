from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from enum import StrEnum
from pathlib import Path
from threading import Lock
from typing import Any, Callable, Mapping

from claw_trade.data_gateway.selection_api import resolve_crypto_selection_trade_date_for_scheduler
from claw_trade.runtime.openclaw_client import OpenClawClient
from claw_trade.selection.candidate_cache import (
    CandidateCacheError,
    validate_candidate_cache_payload_strategy_field_completeness,
)
from claw_trade.selection.dispatch import (
    build_fixed_selection_dispatches,
    execute_selection_dispatches,
    selection_dispatch_worker_order,
)
from claw_trade.selection.models import (
    DEFAULT_SELECTION_STRATEGY_CONFIG_VERSION,
    DEFAULT_SELECTION_WEIGHT_VERSION,
    CandidateCacheRef,
    DecisionTicker,
    SelectionDecision,
    SelectionMarket,
    SelectionProfile,
    SelectionStage,
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
    reader_report_markdown: str | None = None
    reader_report_path: Path | None = None

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
class RawDataMaintenanceStatus:
    status: str
    job_id: str | None = None
    reason: str | None = None
    payload: Mapping[str, object] | None = None


@dataclass(frozen=True)
class _DecisionParseResult:
    decision: SelectionDecision | None
    invalid_reason: str | None = None
    blocked_reason: str | None = None


_PM_DECISION_MATERIAL_TARGET = "selection_portfolio_decision"
_PM_DECISION_MATERIAL_TYPE = "pm_decision"
_PM_RETRYABLE_INVALID_REASON_PREFIXES = (
    "ticker_duplicated_across_sections",
    "candidate_classification_incomplete",
    "ticker_not_in_allowed_set:",
    "ticker_company_mismatch:",
)
_EXPLICIT_TICKER_CORRECTION_RE = re.compile(
    r"(?:股票代码|代码|ticker)?\s*(?:应为|正确为|正确代码为|更正为)\s*[:：]?\s*"
    r"(?P<ticker>\d{6}\.(?:SH|SZ|BJ)|[A-Z0-9]{1,30}USDT)",
    re.IGNORECASE,
)
_CANDIDATE_CACHE_REQUIRED_SUMMARY_LABELS = (
    "总分",
    "分项得分",
    "策略来源",
    "策略变体",
    "命中字段",
    "实际指标值",
    "策略命中明细",
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
_READER_TEXT_REPLACEMENTS = (
    ("MA30_10日斜率", "30日均线10日斜率"),
    ("30日均线_10日斜率", "30日均线10日斜率"),
    ("cn_a.selection_strategy.v1 / 权重版本 cn_a.selection_weights.v1", "当前已批准配置"),
    ("cn_a.selection_strategy.v1 / 评分权重 cn_a.selection_weights.v1", "当前已批准配置"),
    ("cn_a.selection_strategy.v1", "当前已批准策略配置"),
    ("cn_a.selection_weights.v1", "当前已批准评分权重"),
    ("selection_ranked_watchlist", "综合观察清单"),
    ("selection_strategy_review", "选股策略评审"),
    ("selection_skeptic_review", "反方审查意见"),
    ("candidate_cache_summary", "候选池摘要"),
    ("watchlist", "观察清单"),
    ("Selection 反方审查员 Review", "反方审查"),
    ("Selection", "选股"),
    ("Review", "审查"),
    ("review", "审查"),
    ("Top 8", "前8名"),
    ("L1 评审报告", "评审报告"),
    (" L1 ", " "),
    (" L1", ""),
    ("Strategist", "策略评审员"),
    ("strategist", "策略评审员"),
    ("Sceptic", "反方审查员"),
    ("Skeptic", "反方审查员"),
    ("skeptic", "反方审查员"),
    ("ma30_slope_10d", "30日均线10日斜率"),
    ("industry_theme_strength_score", "行业/主题强度评分"),
    ("industry_theme_score", "行业/主题强度评分"),
    ("limit_up_count_20d", "20日涨停次数"),
    ("ma250", "250日均线"),
    ("ma60", "60日均线"),
    ("ma30", "30日均线"),
    ("ma20", "20日均线"),
    ("ma5", "5日均线"),
    ("rps120", "120日相对强度"),
    ("rps60", "60日相对强度"),
    ("rps", "相对强度"),
    ("limit_up_streak_2d", "两日连板信号"),
    ("platform_deviation_pct", "平台偏离度"),
    ("post_limit_up_window_days", "涨停后观察天数"),
    ("post_limit_up_window", "涨停后观察窗口"),
    ("post_limit_up_return_abs_pct", "涨停后绝对收益"),
    ("post_limit_up_range_pct", "涨停后区间振幅"),
    ("post_limit_up", "涨停后"),
    ("single_day_min_return_60d", "60日单日最大回撤"),
    ("ma30_growth_30d", "30日均线30日涨幅"),
    ("return_120d", "120日涨幅"),
    ("return_60d", "60日涨幅"),
    ("return_40d", "40日涨幅"),
    ("return_10d", "10日涨幅"),
    ("p_change_pct", "涨跌幅"),
    ("slope_10d", "10日斜率"),
    ("strategy_signal_myhhub_volume_rise", "放量上涨"),
    ("strategy_signal_myhhub_ma30_keep_increasing", "30日均线持续上行"),
    ("strategy_signal_myhhub_parking_apron", "平台整理信号"),
    ("strategy_signal_myhhub_backtrace_ma250", "年线回踩"),
    ("strategy_signal_myhhub_breakthrough_platform", "平台突破"),
    ("strategy_signal_myhhub_low_backtrace_increase", "低回撤上涨"),
    ("strategy_signal_myhhub_turtle_60_close", "60日突破收盘"),
    ("strategy_signal_myhhub_high_tight_flag", "高紧旗形态"),
    ("strategy_signal_myhhub_climax_limitdown", "极端跌停修复"),
    ("strategy_signal_myhhub_low_atr", "低波动条件"),
    ("strategy_signal_sequoia_ma_volume", "量价放量"),
    ("strategy_signal_sequoia_turtle_20_high", "20日突破高点"),
    ("strategy_signal_sequoia_high_tight_flag", "高紧旗形态"),
    ("strategy_signal_sequoia_limit_up_shakeout", "涨停后洗盘"),
    ("strategy_signal_sequoia_uptrend_limit_down", "上升趋势跌停修复"),
    ("strategy_signal_sequoia_rps_breakout", "相对强度突破"),
    ("strategy_signal_sequoia_private_placement", "定增事件信号"),
    ("myhhub_volume_rise", "放量上涨"),
    ("myhhub_ma30_keep_increasing", "30日均线持续上行"),
    ("myhhub_parking_apron", "平台整理信号"),
    ("myhhub_backtrace_ma250", "年线回踩"),
    ("myhhub_breakthrough_platform", "平台突破"),
    ("myhhub_turtle_60_close", "60日突破收盘"),
    ("myhhub_high_tight_flag", "高紧旗形态"),
    ("myhhub_climax_limitdown", "极端跌停修复"),
    ("myhhub_low_atr", "低波动条件"),
    ("myhhub_low_backtrace_increase", "低回撤上涨"),
    ("sequoia_ma_volume", "量价放量"),
    ("sequoia_turtle_20_high", "20日突破高点"),
    ("sequoia_high_tight_flag", "高紧旗形态"),
    ("sequoia_limit_up_shakeout", "涨停后洗盘"),
    ("sequoia_uptrend_limit_down", "上升趋势跌停修复"),
    ("sequoia_rps_breakout", "相对强度突破"),
    ("sequoia_private_placement", "定增事件信号"),
    ("volume_breakout", "放量突破"),
    ("volume_rise", "放量上涨"),
    ("ma30_keep_increasing", "30日均线持续上行"),
    ("backtrace_ma250", "年线回踩"),
    ("breakthrough_platform", "平台突破"),
    ("turtle_60_close", "60日突破收盘"),
    ("high_tight_flag", "高紧旗形态"),
    ("climax_limitdown", "极端跌停修复"),
    ("low_atr", "低波动条件"),
    ("low_backtrace_increase", "低回撤上涨"),
    ("parking_apron", "平台整理信号"),
    ("ma_volume", "量价放量"),
    ("turtle_20_high", "20日突破高点"),
    ("limit_up_shakeout", "涨停后洗盘"),
    ("uptrend_limit_down", "上升趋势跌停修复"),
    ("rps_breakout", "相对强度突破"),
    ("private_placement", "定增事件信号"),
    ("hit_volume_breakout", "放量突破"),
    ("PE TTM", "滚动市盈率"),
    ("PE ttm", "滚动市盈率"),
    ("PE_TTM", "滚动市盈率"),
    ("PE_ttm", "滚动市盈率"),
    ("pe_ttm", "滚动市盈率"),
    ("市盈率 TTM", "滚动市盈率"),
    ("市盈率/TTM", "滚动市盈率"),
    ("市盈率_ttm", "滚动市盈率"),
    ("PB TTM", "滚动市净率"),
    ("PB ttm", "滚动市净率"),
    ("PB_TTM", "滚动市净率"),
    ("PB_ttm", "滚动市净率"),
    ("pb_ttm", "滚动市净率"),
    ("PS TTM", "滚动市销率"),
    ("PS ttm", "滚动市销率"),
    ("PS_TTM", "滚动市销率"),
    ("PS_ttm", "滚动市销率"),
    ("ps_ttm", "滚动市销率"),
    ("pe/roe", "市盈率/净资产收益率"),
    ("pe/", "市盈率/"),
    ("peak", "高点"),
    ("低ATR", "低波动条件"),
    ("PB", "市净率"),
    ("PE", "市盈率"),
    ("atr_14", "14日波动指标"),
    ("ATR", "波动指标"),
    ("RPS120/60", "120日/60日相对强度"),
    ("RPS120", "120日相对强度"),
    ("RPS60", "60日相对强度"),
    ("RPS", "相对强度"),
    ("MA30", "30日均线"),
    ("MA20", "20日均线"),
    ("CN_A", "A股"),
    ("PE(TTM)", "滚动市盈率"),
    ("市盈率(TTM)", "滚动市盈率"),
    ("PS", "市销率"),
    ("myhhub/stock", "动量策略来源"),
    ("Sequoia-X", "突破策略来源"),
    ("sequoia 系列", "突破策略系列"),
    ("sequoia", "突破策略"),
    ("high tight flag", "高紧旗形态"),
    ("tight flag", "紧旗形态"),
    ("策略变体", "策略条件"),
    ("候选事实表", "候选池数据"),
    ("候选缓存", "候选池数据"),
    ("命中字段", "触发指标"),
    ("排序 tie-break 字段", "同分排序字段"),
    ("tie-break", "同分排序"),
    ("分项得分", "维度得分"),
    ("策略配置版本", "策略配置"),
    ("权重版本", "评分权重"),
)
_READER_STRATEGY_EXPLANATIONS = {
    "放量上涨": "成交额和量价配合达标，用来确认上涨不是缺少成交支撑的孤立波动。",
    "放量突破": "成交活跃度明显放大，用来确认价格信号有资金参与。",
    "30日均线持续上行": "中期均线保持上行，用来确认趋势仍在延续。",
    "平台整理信号": "前期强势后进入整理区间，用来观察蓄势后的再启动可能。",
    "年线回踩": "价格回到长期均线附近并出现支撑迹象，用来观察中长期趋势承接。",
    "平台突破": "价格脱离整理平台，用来确认横盘后的方向选择。",
    "低回撤上涨": "上涨过程中回撤较浅，用来衡量趋势质量和持仓稳定性。",
    "60日突破收盘": "收盘价突破近60日区间，用来确认中期新高信号。",
    "高紧旗形态": "强势上涨后保持紧凑整理，用来观察强趋势延续。",
    "极端跌停修复": "极端下跌后出现修复条件，用来提示后续必须重点验证风险释放是否真实。",
    "低波动条件": "波动水平相对可控，用来降低追高后剧烈波动的风险。",
    "量价放量": "均线关系和成交量同时改善，用来确认价格和资金同步。",
    "20日突破高点": "价格突破近20日高点，用来确认短期突破信号。",
    "涨停后洗盘": "涨停后出现承接和整理，用来观察强势股回踩后的延续性。",
    "上升趋势跌停修复": "上升趋势内经历剧烈下跌后出现修复条件，用来识别需要复核的高风险反转样本。",
    "相对强度突破": "相对市场的强弱指标进入优势区间，用来确认不是只跟随大盘上涨。",
    "定增事件信号": "公司事件进入策略观察窗口，用来提示后续需要核验事件进展和兑现风险。",
}
_READER_STRATEGY_FALLBACK_EXPLANATION = "用于确认候选股在某一类趋势、量价、事件或风险条件上达标。"
_READER_SUMMARY_GAP_REPLACEMENTS = (
    ("selection_batch_rows_dropped", "部分原始行因数据不足被剔除"),
    ("selection_data_api_date_range_missing", "数据源未返回请求区间"),
    ("selection_data_api_empty_result", "部分数据源返回空结果"),
    ("selection_data_api_provider_error", "部分数据源调用异常"),
    ("selection_strategy_variant_disabled", "部分策略条件被禁用"),
    ("selection_data_api", "数据源"),
)

_REFRESHABLE_UNAVAILABLE_CODES = frozenset(
    {
        SelectUnavailableCode.NO_COMPLETED_SELECTION_RUN,
        SelectUnavailableCode.NO_CANDIDATE_SELECTION_RUN,
        SelectUnavailableCode.STALE_SELECTION_RUN,
        SelectUnavailableCode.CANDIDATE_CACHE_NOT_APPROVED,
        SelectUnavailableCode.SELECTION_WAREHOUSE_CHECK_MISSING,
    }
)
_RAW_DATA_MAINTENANCE_ACTIVE_STATUSES = frozenset(
    {"running", "active", "in_progress", "started", "fetching_data", "normalizing_inputs"}
)
_RAW_DATA_MAINTENANCE_FAILED_STATUSES = frozenset({"failed", "error"})

_SELECTION_WORKER_LABELS: dict[SelectionWorkerId, str] = {
    SelectionWorkerId.STRATEGIST: "策略评审",
    SelectionWorkerId.SKEPTIC: "反方评审",
    SelectionWorkerId.MANAGER: "整合排序",
    SelectionWorkerId.PORTFOLIO_MANAGER: "组合经理",
}

_SELECTION_WORKER_ORDER = tuple(_SELECTION_WORKER_LABELS)
_SELECTION_CHAT_BOUNDARY_NOTICE = "`/select` 是候选研究池，不是买入建议；最终买入、持有或卖出，以完整 `/report` 的组合经理结论为准。"
_SELECTION_REPORT_BOUNDARY_NOTICE = (
    "本次 select 结果是候选研究池，不是买入建议。select 主要根据当前可用数据筛出值得进一步研究的股票，"
    "代表这些股票存在量价、资金、事件或策略特征上的研究价值，不等同于最终投资结论。最终是否买入、"
    "持有或卖出，以完整 report 的组合经理结论为准。若后续 report 给出卖出或观望，表示该股票虽然触发了"
    "候选筛选条件，但在基本面、估值、现金流、风险或交易条件上未通过最终投资判断。"
)
_CRYPTO_SELECTION_REPORT_BOUNDARY_NOTICE = (
    "本次 select 结果是候选研究池，不是买入建议。select 主要根据当前可用数据筛出值得进一步研究的标的，"
    "代表这些标的存在量价、资金、事件或策略特征上的研究价值，不等同于最终投资结论。最终是否买入、"
    "持有或卖出，以完整 report 的组合经理结论为准。若后续 report 给出卖出或观望，表示该标的虽然触发了"
    "候选筛选条件，但在基本面、估值、现金流、风险或交易条件上未通过最终投资判断。"
)


class SelectionController:
    """
    SEL-08 scope: /select 命令只读 latest terminal run；只有 completed + approved candidate cache 才走 SEL-07 固定 dispatch。
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
        refresh_completion_waiter: Callable[[str], object] | None = None,
        data_job_runner: Callable[..., object] | None = None,
        default_trade_date_resolver: Callable[[str | None], str] | None = None,
        raw_maintenance_status_provider: Callable[[SelectionMarket], object | None] | None = None,
        report_model_ready_checker: Callable[[], None] | None = None,
    ) -> None:
        self._store = store
        self._now_fn = now_fn or _utc_now
        self._openclaw = openclaw
        self._workflow_evidence_root = (
            workflow_evidence_root or Path("runs/selection/workflows")
        ).resolve()
        # 这些依赖保留给后续 SEL-03/SEL-08 注入；/select 不应调用。
        self._provider_fetch = provider_fetch
        self._scheduler_enqueue = scheduler_enqueue
        self._refresh_completion_waiter = refresh_completion_waiter
        self._data_job_runner = data_job_runner
        self._default_trade_date_resolver = default_trade_date_resolver
        self._raw_maintenance_status_provider = raw_maintenance_status_provider
        self._report_model_ready_checker = report_model_ready_checker
        self._progress_lock = Lock()
        self._active_progress: dict[str, object] | None = None
        self._cancelled_progress_ids: set[str] = set()

    def _selection_artifact_root(self) -> Path:
        return self._store.candidate_cache_artifact_root().resolve()

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
        candidate_cache_summary_md: str,
        approved_l1_materials: Mapping[SelectionWorkerId, str],
    ) -> tuple[SelectionWorkerDispatch, ...]:
        return build_fixed_selection_dispatches(
            request=request,
            select_workflow_run_id=select_workflow_run_id,
            selection_run_id=selection_run_id,
            evidence_root=Path(evidence_root),
            candidate_cache_summary_md=candidate_cache_summary_md,
            approved_l1_materials=approved_l1_materials,
        )

    def handle_select_command(
        self,
        *,
        raw_text: str,
        request_id: str,
        user_id: str | None = None,
        wait_for_data_refresh: bool = False,
    ) -> SelectCommandResult:
        request = _parse_select_request(
            raw_text=raw_text, request_id=request_id, user_id=user_id, now_fn=self._now_fn
        )
        request = self._resolve_default_trade_date(request)
        workflow_run_id = _build_select_workflow_run_id(
            request_id=request.request_id, now_fn=self._now_fn
        )
        evidence_dir = self._workflow_evidence_root / workflow_run_id
        evidence_dir.mkdir(parents=True, exist_ok=True)
        self._publish_workflow_progress(
            command=raw_text.strip(),
            workflow_run_id=workflow_run_id,
            running_worker=None,
            completed_workers=frozenset(),
            started_at=request.created_at,
        )

        try:
            if request.market not in {SelectionMarket.CN_A, SelectionMarket.CRYPTO}:
                payload = _base_workflow_evidence_payload(
                    request=request,
                    workflow_run_id=workflow_run_id,
                    status=SelectUnavailableCode.SELECT_MARKET_UNSUPPORTED.value,
                    selection_run_id=None,
                    reason=SelectUnavailableCode.SELECT_MARKET_UNSUPPORTED.value,
                )
                evidence_path = _write_selection_workflow_evidence(
                    evidence_dir=evidence_dir, payload=payload
                )
                return SelectCommandResult(
                    code=SelectCommandCode.UNAVAILABLE,
                    chat_text=_unavailable_chat_text(SelectUnavailableCode.SELECT_MARKET_UNSUPPORTED),
                    select_workflow_run_id=workflow_run_id,
                    evidence_path=evidence_path,
                    unavailable_code=SelectUnavailableCode.SELECT_MARKET_UNSUPPORTED,
                )
            raw_maintenance_block = self._raw_data_maintenance_block(
                request=request,
                workflow_run_id=workflow_run_id,
                evidence_dir=evidence_dir,
            )
            if raw_maintenance_block is not None:
                return raw_maintenance_block
            if request.force_refresh:
                payload = _base_workflow_evidence_payload(
                    request=request,
                    workflow_run_id=workflow_run_id,
                    status="force_refresh_requested",
                    selection_run_id=None,
                    reason="force_refresh_requested",
                )
                refresh_result = self._request_data_refresh_if_needed(
                    request=request,
                    unavailable_code=SelectUnavailableCode.NO_COMPLETED_SELECTION_RUN,
                    workflow_run_id=workflow_run_id,
                    force_refresh=True,
                )
                if refresh_result is not None:
                    payload["data_refresh"] = _data_refresh_payload(refresh_result)
                    evidence_path = _write_selection_workflow_evidence(
                        evidence_dir=evidence_dir, payload=payload
                    )
                    if refresh_result.status in {"started", "already_running"}:
                        return SelectCommandResult(
                            code=SelectCommandCode.DATA_REFRESH_REQUESTED,
                            chat_text=_data_refresh_chat_text(refresh_result),
                            select_workflow_run_id=workflow_run_id,
                            evidence_path=evidence_path,
                            unavailable_code=SelectUnavailableCode.NO_COMPLETED_SELECTION_RUN,
                            data_refresh=refresh_result,
                        )
                    return SelectCommandResult(
                        code=SelectCommandCode.UNAVAILABLE,
                        chat_text=_data_refresh_unavailable_chat_text(
                            SelectUnavailableCode.NO_COMPLETED_SELECTION_RUN,
                            refresh_result,
                        ),
                        select_workflow_run_id=workflow_run_id,
                        evidence_path=evidence_path,
                        unavailable_code=SelectUnavailableCode.NO_COMPLETED_SELECTION_RUN,
                        failure_reason=refresh_result.error_code or refresh_result.reason,
                        data_refresh=refresh_result,
                    )

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
                    if (
                        wait_for_data_refresh
                        and self._refresh_completion_waiter is not None
                        and refresh_result.status in {"started", "already_running"}
                        and refresh_result.selection_run_id
                    ):
                        refresh_result = _coerce_data_refresh_result(
                            self._refresh_completion_waiter(refresh_result.selection_run_id),
                            default_reason=gate.unavailable_code.value,
                        )
                    if wait_for_data_refresh and refresh_result.status == "completed":
                        refreshed_gate = self.load_latest_completed_for_select(request)
                        if refreshed_gate.is_available:
                            return self._run_available_select_workflow(
                                request=request,
                                raw_text=raw_text,
                                workflow_run_id=workflow_run_id,
                                evidence_dir=evidence_dir,
                                gate=refreshed_gate,
                            )
                    payload["data_refresh"] = _data_refresh_payload(refresh_result)
                    evidence_path = _write_selection_workflow_evidence(
                        evidence_dir=evidence_dir, payload=payload
                    )
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
                        chat_text=_data_refresh_unavailable_chat_text(
                            gate.unavailable_code, refresh_result
                        ),
                        select_workflow_run_id=workflow_run_id,
                        evidence_path=evidence_path,
                        unavailable_code=gate.unavailable_code,
                        failure_reason=refresh_result.error_code or refresh_result.reason,
                        data_refresh=refresh_result,
                    )
                evidence_path = _write_selection_workflow_evidence(
                    evidence_dir=evidence_dir, payload=payload
                )
                return SelectCommandResult(
                    code=SelectCommandCode.UNAVAILABLE,
                    chat_text=_unavailable_chat_text(gate.unavailable_code),
                    select_workflow_run_id=workflow_run_id,
                    evidence_path=evidence_path,
                    unavailable_code=gate.unavailable_code,
                )
            return self._run_available_select_workflow(
                request=request,
                raw_text=raw_text,
                workflow_run_id=workflow_run_id,
                evidence_dir=evidence_dir,
                gate=gate,
            )
        finally:
            self._clear_workflow_progress(workflow_run_id)

    def _raw_data_maintenance_block(
        self,
        *,
        request: SelectRequest,
        workflow_run_id: str,
        evidence_dir: Path,
    ) -> SelectCommandResult | None:
        status = self._raw_data_maintenance_status(request.market)
        if status is None:
            return None
        if status.status in _RAW_DATA_MAINTENANCE_ACTIVE_STATUSES:
            code = SelectUnavailableCode.RAW_DATA_MAINTENANCE_RUNNING
        elif status.status in _RAW_DATA_MAINTENANCE_FAILED_STATUSES and not request.force_refresh:
            code = SelectUnavailableCode.RAW_DATA_MAINTENANCE_FAILED
        else:
            return None
        reason = status.reason or code.value
        payload = _base_workflow_evidence_payload(
            request=request,
            workflow_run_id=workflow_run_id,
            status=code.value,
            selection_run_id=None,
            reason=reason,
        )
        payload["raw_data_maintenance"] = dict(status.payload or {})
        evidence_path = _write_selection_workflow_evidence(evidence_dir=evidence_dir, payload=payload)
        return SelectCommandResult(
            code=SelectCommandCode.UNAVAILABLE,
            chat_text=_raw_data_maintenance_chat_text(code, reason=reason),
            select_workflow_run_id=workflow_run_id,
            evidence_path=evidence_path,
            unavailable_code=code,
            failure_reason=reason,
        )

    def _resolve_default_trade_date(self, request: SelectRequest) -> SelectRequest:
        if request.trade_date is not None:
            return request
        resolver: Callable[[str | None], str] | None
        if request.market == SelectionMarket.CRYPTO:
            resolver = resolve_crypto_selection_trade_date_for_scheduler
        elif request.market == SelectionMarket.CN_A:
            resolver = self._default_trade_date_resolver
        else:
            resolver = None
        if resolver is None:
            return request
        resolved_trade_date = resolver(None).strip()
        date.fromisoformat(resolved_trade_date)
        return replace(request, trade_date=resolved_trade_date)

    def _raw_data_maintenance_status(self, market: SelectionMarket) -> RawDataMaintenanceStatus | None:
        if self._raw_maintenance_status_provider is None:
            return None
        try:
            raw_status = self._raw_maintenance_status_provider(market)
        except Exception as exc:  # pragma: no cover - defensive path for live status storage errors
            return RawDataMaintenanceStatus(
                status="failed",
                reason=f"raw_data_maintenance_status_unreadable:{exc}",
                payload={"status": "failed", "error": str(exc)},
            )
        return _coerce_raw_data_maintenance_status(raw_status)

    def _report_model_ready_block(
        self,
        *,
        request: SelectRequest,
        workflow_run_id: str,
        evidence_dir: Path,
        selection_run_id: str,
    ) -> SelectCommandResult | None:
        if self._report_model_ready_checker is None:
            return None
        try:
            self._report_model_ready_checker()
        except Exception as exc:
            reason = str(exc).strip() or "report_model_not_ready"
            payload = _base_workflow_evidence_payload(
                request=request,
                workflow_run_id=workflow_run_id,
                status="report_model_not_ready",
                selection_run_id=selection_run_id,
                reason=reason,
            )
            evidence_path = _write_selection_workflow_evidence(
                evidence_dir=evidence_dir, payload=payload
            )
            return SelectCommandResult(
                code=SelectCommandCode.BLOCKED_ASK_HUMAN,
                chat_text=f"`/select` 当前不可用：{reason}",
                select_workflow_run_id=workflow_run_id,
                evidence_path=evidence_path,
                failure_reason=reason,
            )
        return None

    def _run_available_select_workflow(
        self,
        *,
        request: SelectRequest,
        raw_text: str,
        workflow_run_id: str,
        evidence_dir: Path,
        gate: SelectReadGateResult,
    ) -> SelectCommandResult:
        latest = gate.latest_completed_run
        assert latest is not None
        if request.trade_date is None:
            request = replace(request, trade_date=latest.run_plan.trade_date)
        candidate_cache_ref = latest.data_run.candidate_cache_ref
        if candidate_cache_ref is None:
            payload = _base_workflow_evidence_payload(
                request=request,
                workflow_run_id=workflow_run_id,
                status=SelectUnavailableCode.CANDIDATE_CACHE_NOT_APPROVED.value,
                selection_run_id=latest.run_plan.selection_run_id,
                reason=SelectUnavailableCode.CANDIDATE_CACHE_NOT_APPROVED.value,
            )
            evidence_path = _write_selection_workflow_evidence(
                evidence_dir=evidence_dir, payload=payload
            )
            return SelectCommandResult(
                code=SelectCommandCode.UNAVAILABLE,
                chat_text=_unavailable_chat_text(
                    SelectUnavailableCode.CANDIDATE_CACHE_NOT_APPROVED
                ),
                select_workflow_run_id=workflow_run_id,
                evidence_path=evidence_path,
                unavailable_code=SelectUnavailableCode.CANDIDATE_CACHE_NOT_APPROVED,
            )

        selection_artifact_root = self._selection_artifact_root()
        candidate_cache_strategy_error = _candidate_cache_strategy_completeness_error(
            candidate_cache_ref,
            artifact_root=selection_artifact_root,
        )
        if candidate_cache_strategy_error is not None:
            payload = _base_workflow_evidence_payload(
                request=request,
                workflow_run_id=workflow_run_id,
                status=SelectUnavailableCode.CANDIDATE_CACHE_INTEGRITY_FAILED.value,
                selection_run_id=latest.run_plan.selection_run_id,
                reason=candidate_cache_strategy_error,
            )
            evidence_path = _write_selection_workflow_evidence(
                evidence_dir=evidence_dir, payload=payload
            )
            return SelectCommandResult(
                code=SelectCommandCode.UNAVAILABLE,
                chat_text=_unavailable_chat_text(
                    SelectUnavailableCode.CANDIDATE_CACHE_INTEGRITY_FAILED
                ),
                select_workflow_run_id=workflow_run_id,
                evidence_path=evidence_path,
                unavailable_code=SelectUnavailableCode.CANDIDATE_CACHE_INTEGRITY_FAILED,
            )

        report_model_block = self._report_model_ready_block(
            request=request,
            workflow_run_id=workflow_run_id,
            evidence_dir=evidence_dir,
            selection_run_id=latest.run_plan.selection_run_id,
        )
        if report_model_block is not None:
            return report_model_block

        if self._openclaw is None:
            payload = _base_workflow_evidence_payload(
                request=request,
                workflow_run_id=workflow_run_id,
                status=SelectCommandCode.BLOCKED_ASK_HUMAN.value,
                selection_run_id=latest.run_plan.selection_run_id,
                reason="selection_openclaw_not_configured",
            )
            evidence_path = _write_selection_workflow_evidence(
                evidence_dir=evidence_dir, payload=payload
            )
            return SelectCommandResult(
                code=SelectCommandCode.BLOCKED_ASK_HUMAN,
                chat_text="`/select` 当前不可用：选股执行通道未配置，请联系维护者确认。",
                select_workflow_run_id=workflow_run_id,
                evidence_path=evidence_path,
                failure_reason="selection_openclaw_not_configured",
            )

        summary_md = _load_candidate_cache_summary(
            candidate_cache_ref,
            artifact_root=selection_artifact_root,
        )
        allowed_ticker_companies = _extract_allowed_ticker_companies_from_summary(summary_md)
        allowed_tickers = frozenset(allowed_ticker_companies)
        if not allowed_tickers:
            payload = _base_workflow_evidence_payload(
                request=request,
                workflow_run_id=workflow_run_id,
                status=SelectUnavailableCode.CANDIDATE_CACHE_INTEGRITY_FAILED.value,
                selection_run_id=latest.run_plan.selection_run_id,
                reason="candidate_cache_summary_missing_allowed_tickers",
            )
            evidence_path = _write_selection_workflow_evidence(
                evidence_dir=evidence_dir, payload=payload
            )
            return SelectCommandResult(
                code=SelectCommandCode.UNAVAILABLE,
                chat_text=_unavailable_chat_text(
                    SelectUnavailableCode.CANDIDATE_CACHE_INTEGRITY_FAILED
                ),
                select_workflow_run_id=workflow_run_id,
                evidence_path=evidence_path,
                unavailable_code=SelectUnavailableCode.CANDIDATE_CACHE_INTEGRITY_FAILED,
            )

        approved_l1: dict[SelectionWorkerId, str] = {
            SelectionWorkerId.STRATEGIST: "[pending strategist material]",
            SelectionWorkerId.SKEPTIC: "[pending skeptic material]",
            SelectionWorkerId.MANAGER: "[pending manager material]",
        }
        dispatch_results: list[dict[str, str]] = []
        pm_raw_text: str | None = None
        completed_workers: set[SelectionWorkerId] = set()

        for worker_id in selection_dispatch_worker_order():
            if self._is_workflow_cancelled(workflow_run_id):
                return _failed_result(
                    request=request,
                    workflow_run_id=workflow_run_id,
                    evidence_dir=evidence_dir,
                    selection_run_id=latest.run_plan.selection_run_id,
                    reason="selection_workflow_cancelled:user_cancelled",
                )
            self._publish_workflow_progress(
                command=raw_text.strip(),
                workflow_run_id=workflow_run_id,
                running_worker=worker_id,
                completed_workers=frozenset(completed_workers),
                started_at=request.created_at,
            )
            dispatches = self.build_fixed_selection_dispatches(
                request=request,
                select_workflow_run_id=workflow_run_id,
                selection_run_id=latest.run_plan.selection_run_id,
                evidence_root=str(evidence_dir / "dispatches"),
                candidate_cache_summary_md=summary_md,
                approved_l1_materials=approved_l1,
            )
            dispatch = _select_dispatch_for_worker(dispatches=dispatches, worker_id=worker_id)
            executions = execute_selection_dispatches(
                openclaw=self._openclaw,
                dispatches=(dispatch,),
                candidate_cache_ref=candidate_cache_ref,
                profile=request.profile.value,
                selection_artifact_root=selection_artifact_root,
            )
            if self._is_workflow_cancelled(workflow_run_id):
                return _failed_result(
                    request=request,
                    workflow_run_id=workflow_run_id,
                    evidence_dir=evidence_dir,
                    selection_run_id=latest.run_plan.selection_run_id,
                    reason="selection_workflow_cancelled:user_cancelled",
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
                    reason=execution.openclaw_result.failure_reason
                    or f"worker_runtime_failed:{worker_id.value}",
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
            completed_workers.add(worker_id)
            self._publish_workflow_progress(
                command=raw_text.strip(),
                workflow_run_id=workflow_run_id,
                running_worker=None,
                completed_workers=frozenset(completed_workers),
                started_at=request.created_at,
            )

        if self._is_workflow_cancelled(workflow_run_id):
            return _failed_result(
                request=request,
                workflow_run_id=workflow_run_id,
                evidence_dir=evidence_dir,
                selection_run_id=latest.run_plan.selection_run_id,
                reason="selection_workflow_cancelled:user_cancelled",
            )

        if pm_raw_text is None:
            return _failed_result(
                request=request,
                workflow_run_id=workflow_run_id,
                evidence_dir=evidence_dir,
                selection_run_id=latest.run_plan.selection_run_id,
                reason="selection_result_invalid:pm_output_missing",
            )

        pm_decision_material_id = _build_pm_decision_material_id(workflow_run_id=workflow_run_id)
        pm_retry_payload: dict[str, object] | None = None
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
            evidence_path = _write_selection_workflow_evidence(
                evidence_dir=evidence_dir, payload=payload
            )
            return SelectCommandResult(
                code=SelectCommandCode.BLOCKED_ASK_HUMAN,
                chat_text="`/select` 结果无法机械解析，已阻断自动处理，请人工复核 PM 决策文本。",
                select_workflow_run_id=workflow_run_id,
                evidence_path=evidence_path,
                failure_reason=decision_parse.blocked_reason,
            )

        if decision_parse.decision is None:
            assert decision_parse.invalid_reason is not None
            first_invalid_reason = decision_parse.invalid_reason
            if _is_retryable_pm_decision_invalid_reason(first_invalid_reason):
                first_pm_output_path = evidence_dir / "pm-selection-decision-first-invalid.md"
                first_pm_output_path.write_text(pm_raw_text, encoding="utf-8")
                retry_instruction = _build_pm_retry_instruction(
                    invalid_reason=first_invalid_reason,
                    previous_pm_output=pm_raw_text,
                    allowed_ticker_companies=allowed_ticker_companies,
                )
                retry_dispatch = _build_pm_retry_dispatch(
                    request=request,
                    workflow_run_id=workflow_run_id,
                    selection_run_id=latest.run_plan.selection_run_id,
                    evidence_dir=evidence_dir,
                    summary_md=summary_md,
                    approved_l1=approved_l1,
                    retry_instruction=retry_instruction,
                )
                retry_executions = execute_selection_dispatches(
                    openclaw=self._openclaw,
                    dispatches=(retry_dispatch,),
                    candidate_cache_ref=candidate_cache_ref,
                    profile=request.profile.value,
                    selection_artifact_root=selection_artifact_root,
                )
                dispatch_results.append(
                    {
                        "worker_id": SelectionWorkerId.PORTFOLIO_MANAGER.value,
                        "dispatch_id": retry_dispatch.dispatch_id,
                        "evidence_dir": str(retry_dispatch.evidence_dir),
                        "command_snapshot_path": str(
                            retry_executions[0].command_snapshot_path
                            if retry_executions
                            else retry_dispatch.evidence_dir / "selection-dispatch-command.json"
                        ),
                    }
                )
                pm_retry_payload = {
                    "attempted": True,
                    "first_invalid_reason": first_invalid_reason,
                    "first_pm_output_path": str(first_pm_output_path),
                    "retry_dispatch_id": retry_dispatch.dispatch_id,
                    "retry_evidence_dir": str(retry_dispatch.evidence_dir),
                }
                if not retry_executions:
                    return _failed_result(
                        request=request,
                        workflow_run_id=workflow_run_id,
                        evidence_dir=evidence_dir,
                        selection_run_id=latest.run_plan.selection_run_id,
                        reason="worker_runtime_failed:no_execution:selection_portfolio_manager_retry",
                    )
                retry_execution = retry_executions[0]
                if retry_execution.openclaw_result.status != "succeeded":
                    return _failed_result(
                        request=request,
                        workflow_run_id=workflow_run_id,
                        evidence_dir=evidence_dir,
                        selection_run_id=latest.run_plan.selection_run_id,
                        reason=retry_execution.openclaw_result.failure_reason
                        or "worker_runtime_failed:selection_portfolio_manager_retry",
                    )
                retry_pm_raw_text = _read_worker_output_text(retry_execution.openclaw_result)
                if not retry_pm_raw_text:
                    return _failed_result(
                        request=request,
                        workflow_run_id=workflow_run_id,
                        evidence_dir=evidence_dir,
                        selection_run_id=latest.run_plan.selection_run_id,
                        reason="artifact_approval_failed:selection_portfolio_manager_retry:empty_output",
                    )
                pm_raw_text = retry_pm_raw_text
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
                    payload["pm_retry"] = pm_retry_payload
                    payload["pm_output_path"] = str(evidence_dir / "pm-selection-decision.md")
                    (evidence_dir / "pm-selection-decision.md").write_text(
                        pm_raw_text, encoding="utf-8"
                    )
                    evidence_path = _write_selection_workflow_evidence(
                        evidence_dir=evidence_dir, payload=payload
                    )
                    return SelectCommandResult(
                        code=SelectCommandCode.BLOCKED_ASK_HUMAN,
                        chat_text="`/select` 结果无法机械解析，已阻断自动处理，请人工复核 PM 决策文本。",
                        select_workflow_run_id=workflow_run_id,
                        evidence_path=evidence_path,
                        failure_reason=decision_parse.blocked_reason,
                    )
                if decision_parse.decision is not None:
                    pm_retry_payload["resolved"] = True

        if decision_parse.decision is None:
            assert decision_parse.invalid_reason is not None
            if pm_retry_payload is not None:
                reason = f"selection_result_invalid:{decision_parse.invalid_reason}"
                payload = _base_workflow_evidence_payload(
                    request=request,
                    workflow_run_id=workflow_run_id,
                    status=SelectCommandCode.FAILED.value,
                    selection_run_id=latest.run_plan.selection_run_id,
                    reason=reason,
                )
                payload["dispatches"] = dispatch_results
                payload["pm_retry"] = pm_retry_payload
                payload["pm_output_path"] = str(evidence_dir / "pm-selection-decision.md")
                (evidence_dir / "pm-selection-decision.md").write_text(
                    pm_raw_text, encoding="utf-8"
                )
                evidence_path = _write_selection_workflow_evidence(
                    evidence_dir=evidence_dir, payload=payload
                )
                return SelectCommandResult(
                    code=SelectCommandCode.FAILED,
                    chat_text=_failed_chat_text(reason),
                    select_workflow_run_id=workflow_run_id,
                    evidence_path=evidence_path,
                    failure_reason=reason,
                )
            return _failed_result(
                request=request,
                workflow_run_id=workflow_run_id,
                evidence_dir=evidence_dir,
                selection_run_id=latest.run_plan.selection_run_id,
                reason=f"selection_result_invalid:{decision_parse.invalid_reason}",
            )

        decision = decision_parse.decision
        reader_text = _render_selection_reader_chat_message(decision)
        reader_report_markdown = _render_selection_reader_report(
            decision,
            market=latest.run_plan.market,
            selection_worker_reports=approved_l1,
            portfolio_manager_report=pm_raw_text,
            candidate_cache_summary_md=summary_md,
        )
        reader_report_path = evidence_dir / "select-reader-report.md"
        reader_report_path.write_text(f"{reader_report_markdown.strip()}\n", encoding="utf-8")
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
        if pm_retry_payload is not None:
            payload["pm_retry"] = pm_retry_payload
        payload["pm_output_path"] = str(evidence_dir / "pm-selection-decision.md")
        payload["reader_report_path"] = str(reader_report_path)
        (evidence_dir / "pm-selection-decision.md").write_text(pm_raw_text, encoding="utf-8")
        evidence_path = _write_selection_workflow_evidence(
            evidence_dir=evidence_dir, payload=payload
        )
        return SelectCommandResult(
            code=SelectCommandCode.COMPLETED,
            chat_text=reader_text,
            select_workflow_run_id=workflow_run_id,
            evidence_path=evidence_path,
            decision=decision,
            reader_report_markdown=reader_report_markdown,
            reader_report_path=reader_report_path,
        )

    def latest_progress_for_user(self) -> dict[str, object]:
        with self._progress_lock:
            progress = dict(self._active_progress) if self._active_progress is not None else None
        return {"selectionProgress": progress}

    def cancel_progress(self, *, workflow_run_id: str) -> bool:
        with self._progress_lock:
            if (
                self._active_progress
                and self._active_progress.get("workflowRunId") == workflow_run_id
            ):
                self._cancelled_progress_ids.add(workflow_run_id)
                self._active_progress = None
                return True
        return False

    def _is_workflow_cancelled(self, workflow_run_id: str) -> bool:
        with self._progress_lock:
            return workflow_run_id in self._cancelled_progress_ids

    def _publish_workflow_progress(
        self,
        *,
        command: str,
        workflow_run_id: str,
        running_worker: SelectionWorkerId | None,
        completed_workers: frozenset[SelectionWorkerId],
        started_at: str,
    ) -> None:
        completed_count = len(completed_workers)
        running_offset = 1 if running_worker is not None else 0
        percent = min(95, 15 + completed_count * 20 + running_offset * 10)
        if running_worker is None:
            current_action = (
                "正在准备选股评审。" if completed_count == 0 else "正在整理上一位选股评审的结果。"
            )
        else:
            current_action = f"正在运行{_SELECTION_WORKER_LABELS[running_worker]}。"
        worker_status_labels = []
        for worker_id in _SELECTION_WORKER_ORDER:
            label = _SELECTION_WORKER_LABELS[worker_id]
            if worker_id in completed_workers:
                status = "已完成"
            elif worker_id == running_worker:
                status = "执行中"
            else:
                status = "等待启动"
            worker_status_labels.append(f"{label}：{status}")
        completed_labels = [
            _SELECTION_WORKER_LABELS[worker_id]
            for worker_id in _SELECTION_WORKER_ORDER
            if worker_id in completed_workers
        ]
        waiting_labels = [
            _SELECTION_WORKER_LABELS[worker_id]
            for worker_id in _SELECTION_WORKER_ORDER
            if worker_id not in completed_workers and worker_id != running_worker
        ]
        progress = {
            "kind": "selection_workflow",
            "status": "running",
            "statusLabel": "选股中",
            "command": command or "/select",
            "stageLabel": "选股工作流执行中",
            "currentAction": current_action,
            "percent": percent,
            "workerStatusLabels": worker_status_labels,
            "completedRoleLabels": completed_labels,
            "waitingRoleLabels": waiting_labels,
            "startedAt": started_at,
            "finishedAt": None,
            "workflowRunId": workflow_run_id,
        }
        with self._progress_lock:
            if workflow_run_id in self._cancelled_progress_ids:
                return
            self._active_progress = progress

    def _clear_workflow_progress(self, workflow_run_id: str) -> None:
        with self._progress_lock:
            if (
                self._active_progress
                and self._active_progress.get("workflowRunId") == workflow_run_id
            ):
                self._active_progress = None
            self._cancelled_progress_ids.discard(workflow_run_id)

    def _request_data_refresh_if_needed(
        self,
        *,
        request: SelectRequest,
        unavailable_code: SelectUnavailableCode,
        workflow_run_id: str,
        force_refresh: bool = False,
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
                force_refresh=force_refresh,
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
    tokens = text.split()
    if not tokens or tokens[0].lower() != "/select":
        raise ValueError("invalid_select_command")
    market = SelectionMarket.CN_A
    profile = SelectionProfile.CN_A
    force_refresh = False
    trade_date: str | None = None
    rest = tokens[1:]
    if rest:
        first = rest[0]
        parsed_market = _select_market_from_token(first)
        if parsed_market is not None:
            market = parsed_market
            profile = SelectionProfile(parsed_market.value)
            rest = rest[1:]
            if rest:
                if rest[0].lower() not in {"refresh", "刷新"}:
                    raise ValueError("invalid_select_command")
                force_refresh = True
                rest = rest[1:]
        elif first.lower() in {"refresh", "刷新"}:
            force_refresh = True
            rest = rest[1:]
        elif re.fullmatch(r"\d{4}-\d{2}-\d{2}", first):
            trade_date = first
            date.fromisoformat(trade_date)
            rest = rest[1:]
        else:
            raise ValueError("invalid_select_command")
        if rest:
            if len(rest) != 1 or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", rest[0]):
                raise ValueError("invalid_select_command")
            trade_date = rest[0]
            date.fromisoformat(trade_date)
    created_at = now_fn().isoformat()
    return SelectRequest(
        request_id=request_id,
        market=market,
        profile=profile,
        trade_date=trade_date,
        user_id=user_id,
        created_at=created_at,
        force_refresh=force_refresh,
        entry_point=WorkflowEntryPoint.SELECT_COMMAND,
        system_context_policy=SelectionSystemContextPolicy.SINGLE_WORKER_MINIMAL,
    )


def _select_market_from_token(token: str) -> SelectionMarket | None:
    normalized = token.lower()
    if normalized in {"1", "cn_a", "a"} or token == "A股":
        return SelectionMarket.CN_A
    if normalized in {"2", "crypto"} or token == "加密":
        return SelectionMarket.CRYPTO
    if normalized in {"3", "us"}:
        return SelectionMarket.US
    if normalized == "hk" or token == "港股":
        return SelectionMarket.HK
    return None


def _selection_market_label(market: object) -> str:
    value = getattr(market, "value", market)
    return "加密市场" if str(value).strip().upper() == "CRYPTO" else "A股"


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

def _load_candidate_cache_summary(
    candidate_cache_ref: CandidateCacheRef,
    *,
    artifact_root: Path | None = None,
) -> str:
    path = _resolve_selection_uri(candidate_cache_ref.cache_summary_ref, artifact_root=artifact_root)
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        return ""
    if _candidate_cache_summary_has_required_labels(text):
        return text
    rebuilt = _rebuild_candidate_cache_summary_from_json(
        candidate_cache_ref,
        artifact_root=artifact_root,
    )
    if rebuilt is not None:
        return rebuilt
    raw_field = _reader_visible_raw_field_name(text)
    if raw_field is not None:
        raise ValueError(
            f"candidate cache summary contains reader-visible raw field name: {raw_field}"
        )
    return text


def _candidate_cache_summary_has_required_labels(summary_md: str) -> bool:
    return (
        all(label in summary_md for label in _CANDIDATE_CACHE_REQUIRED_SUMMARY_LABELS)
        and _reader_visible_raw_field_name(summary_md) is None
    )


def _reader_visible_raw_field_name(summary_md: str) -> str | None:
    for key in _READER_VISIBLE_RAW_FIELD_NAMES:
        for marker in (f'"{key}"', f"'{key}'", f"{key}=", f"{key}:"):
            if marker in summary_md:
                return key
    return None


def _rebuild_candidate_cache_summary_from_json(
    candidate_cache_ref: CandidateCacheRef,
    *,
    artifact_root: Path | None = None,
) -> str | None:
    json_path = _candidate_cache_json_path(candidate_cache_ref, artifact_root=artifact_root)
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

    sidecar = _candidate_cache_sidecar_payload(candidate_cache_ref, artifact_root=artifact_root)
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
    candidate_count = _first_text(
        payload.get("candidate_count"), sidecar.get("candidate_count"), str(len(candidates))
    )

    lines = [
        f"# {_selection_market_label(market)}候选缓存",
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
        _first_text(payload.get("data_quality_summary"), "数据质量：候选缓存未提供汇总文本。")
    )
    source_summary = _reader_friendly_summary_text(
        _first_text(payload.get("source_summary"), "来源摘要：候选缓存未提供汇总文本。")
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


def _candidate_cache_json_path(
    candidate_cache_ref: CandidateCacheRef,
    *,
    artifact_root: Path | None = None,
) -> Path | None:
    summary_path = _resolve_selection_uri(candidate_cache_ref.cache_summary_ref, artifact_root=artifact_root)
    candidates = (summary_path.with_name("candidate-cache.json"),)
    for path in candidates:
        if path.is_file():
            return path
    body_path = _resolve_selection_uri(candidate_cache_ref.l1_uri, artifact_root=artifact_root)
    alt_path = body_path.with_name("candidate-cache.json")
    return alt_path if alt_path.is_file() else None


def _candidate_cache_strategy_completeness_error(
    candidate_cache_ref: CandidateCacheRef,
    *,
    artifact_root: Path | None = None,
) -> str | None:
    json_path = _candidate_cache_json_path(candidate_cache_ref, artifact_root=artifact_root)
    if json_path is None:
        return "candidate_cache_strategy_fields_missing: candidate-cache.json missing"
    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return f"candidate_cache_strategy_fields_missing: candidate-cache.json unreadable: {exc}"
    if not isinstance(payload, Mapping):
        return "candidate_cache_strategy_fields_missing: candidate-cache.json must be an object"
    try:
        validate_candidate_cache_payload_strategy_field_completeness(payload)
    except CandidateCacheError as exc:
        return f"{exc.code}: {exc.reason}"
    return None


def _candidate_cache_sidecar_payload(
    candidate_cache_ref: CandidateCacheRef,
    *,
    artifact_root: Path | None = None,
) -> dict[str, object]:
    try:
        path = _resolve_selection_uri(candidate_cache_ref.manifest_ref, artifact_root=artifact_root)
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _candidate_row_summary(candidate: Mapping[str, object]) -> dict[str, str]:
    features = _mapping(candidate.get("feature_values"))
    strategy_hits = _string_tuple(candidate.get("strategy_hits"))
    component_scores = _mapping(candidate.get("component_scores"))
    actual_metric_values = _mapping(candidate.get("actual_metric_values"))
    hit_fields = _mapping(candidate.get("hit_fields"))
    tie_break_fields = _mapping(candidate.get("tie_break_fields"))
    return {
        "rank": _first_text(candidate.get("rank"), "-"),
        "ticker": _first_text(candidate.get("ticker"), "-"),
        "company_name": _first_text(candidate.get("company_name"), "-"),
        "industry": _first_text(candidate.get("industry"), "-"),
        "total_score": _format_candidate_number(
            _first_value(candidate.get("total_score"), features.get("score"))
        ),
        "component_scores": _format_visible_mapping(component_scores),
        "strategy_sources": _join_or_dash(_strategy_sources_from_hits(strategy_hits)),
        "strategy_variants": _join_or_dash(_strategy_variants_from_hits(strategy_hits)),
        "hit_fields": _format_visible_mapping(hit_fields),
        "actual_metric_values": _format_visible_mapping(actual_metric_values),
        "risk_penalty": _format_candidate_number(
            _first_value(
                candidate.get("risk_penalty"),
                features.get("risk_penalty_score"),
                features.get("risk_penalty"),
            )
        ),
        "data_gap_penalty": _format_candidate_number(
            _first_value(
                candidate.get("data_gap_penalty"),
                features.get("data_gap_penalty_score"),
                features.get("data_gap_penalty"),
            )
        ),
        "tie_break_fields": _format_visible_mapping(tie_break_fields),
        "data_quality": _first_text(candidate.get("data_quality"), "-"),
        "source_summary": _first_text(candidate.get("source_summary"), "-"),
    }


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
    return _dedupe(
        tuple(
            source for source, _variant in (_split_strategy_hit_text(hit) for hit in hits) if source
        )
    )


def _strategy_variants_from_hits(hits: tuple[str, ...]) -> tuple[str, ...]:
    return _dedupe(
        tuple(
            variant
            for _source, variant in (_split_strategy_hit_text(hit) for hit in hits)
            if variant
        )
    )


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
    payload = {
        _reader_visible_label(str(key)): _model_visible_value(value)
        for key, value in sorted(values.items())
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _reader_visible_label(key: str) -> str:
    return _READER_VISIBLE_FIELD_LABELS.get(key, key)


def _reader_friendly_summary_text(text: str) -> str:
    out = text.replace("BLOCKER", "阻断").replace("WARN", "提示")
    out = out.replace(" provider ", " 数据源 ").replace("provider 调用", "数据源调用")
    for source, target in _READER_SUMMARY_GAP_REPLACEMENTS:
        out = out.replace(source, target)
    return _reader_friendly_selection_text(out)


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


def _resolve_selection_uri(ref: str, *, artifact_root: Path | None = None) -> Path:
    prefix = "local://selection/"
    if not ref.startswith(prefix):
        return Path(ref)
    relative = ref[len(prefix) :].strip("/")
    segments = [part for part in relative.split("/") if part]
    if not segments or ".." in segments:
        raise ValueError(f"unsafe local selection uri: {ref}")
    return (artifact_root or Path("runs/selection/artifacts")) / Path(*segments)


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
        if not _is_supported_selection_ticker(ticker):
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


def _is_retryable_pm_decision_invalid_reason(reason: str) -> bool:
    return any(reason.startswith(prefix) for prefix in _PM_RETRYABLE_INVALID_REASON_PREFIXES)


def _build_pm_retry_instruction(
    *,
    invalid_reason: str,
    previous_pm_output: str,
    allowed_ticker_companies: Mapping[str, str],
) -> str:
    candidate_lines = [
        f"- {index} | {ticker} | {company_name}"
        for index, (ticker, company_name) in enumerate(allowed_ticker_companies.items(), start=1)
    ]
    return "\n".join(
        [
            "上一次组合经理三分类输出未通过机械校验，需要你重新输出最终三分类。",
            "",
            f"错误原因：selection_result_invalid:{invalid_reason}",
            "",
            "候选池完整清单如下；每只只能进入一个分组，必须全部覆盖，不能新增池外标的：",
            *candidate_lines,
            "",
            "上一次 PM 原文如下，仅用于纠错，不代表有效最终结论：",
            previous_pm_output.strip(),
            "",
            "请重新输出三段，且只使用以下标题：",
            "进入 /report:",
            "观察:",
            "放弃:",
            "每行格式：- 股票代码 | 股票名称 | 一句话理由",
        ]
    ).strip()


def _build_pm_retry_dispatch(
    *,
    request: SelectRequest,
    workflow_run_id: str,
    selection_run_id: str,
    evidence_dir: Path,
    summary_md: str,
    approved_l1: Mapping[SelectionWorkerId, str],
    retry_instruction: str,
) -> SelectionWorkerDispatch:
    trade_date = request.trade_date
    if trade_date is None or not trade_date.strip():
        raise ValueError("request.trade_date is required for selection PM retry dispatch")
    dispatch_id = f"{workflow_run_id}-dispatch-05-{SelectionWorkerId.PORTFOLIO_MANAGER.value}-retry"
    return SelectionWorkerDispatch(
        dispatch_id=dispatch_id,
        select_workflow_run_id=workflow_run_id,
        worker_id=SelectionWorkerId.PORTFOLIO_MANAGER,
        stage=SelectionStage.SELECTION_PORTFOLIO_DECISION,
        allowed_tools=(),
        prompt_runtime_vars={
            "market": request.market.value,
            "profile": request.profile.value,
            "trade_date": trade_date,
            "selection_run_id": selection_run_id,
            "select_workflow_run_id": workflow_run_id,
        },
        model_visible_materials=(
            approved_l1[SelectionWorkerId.MANAGER],
            approved_l1[SelectionWorkerId.STRATEGIST],
            approved_l1[SelectionWorkerId.SKEPTIC],
            summary_md,
            retry_instruction,
        ),
        evidence_dir=evidence_dir / "dispatches" / dispatch_id,
        provider_payload_ref=None,
    )


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
    if allowed_ticker_companies:
        parsed = _canonicalize_explicit_ticker_corrections(
            parsed=parsed,
            allowed_tickers=allowed_tickers,
            allowed_ticker_companies=allowed_ticker_companies,
        )

    all_tickers = [
        item.ticker for item in (*parsed["enter_report"], *parsed["watch"], *parsed["reject"])
    ]
    if len(all_tickers) != len(set(all_tickers)):
        return _DecisionParseResult(
            decision=None, invalid_reason="ticker_duplicated_across_sections"
        )
    for ticker in all_tickers:
        if ticker.upper() not in allowed_tickers:
            return _DecisionParseResult(
                decision=None, invalid_reason=f"ticker_not_in_allowed_set:{ticker}"
            )
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


def _is_supported_selection_ticker(ticker: str) -> bool:
    return (
        re.fullmatch(r"\d{6}\.(?:SH|SZ|BJ)|[A-Z0-9]{1,30}USDT", ticker, re.IGNORECASE) is not None
    )


def _strip_markdown_inline(value: str) -> str:
    out = value.replace("`", "")
    out = re.sub(r"<br\s*/?>", " ", out, flags=re.IGNORECASE)
    out = re.sub(r"\*\*(.*?)\*\*", r"\1", out)
    return out.strip()


def _canonicalize_explicit_ticker_corrections(
    *,
    parsed: Mapping[str, tuple[DecisionTicker, ...]],
    allowed_tickers: frozenset[str],
    allowed_ticker_companies: Mapping[str, str],
) -> dict[str, tuple[DecisionTicker, ...]]:
    canonicalized: dict[str, tuple[DecisionTicker, ...]] = {}
    for section, rows in parsed.items():
        canonicalized[section] = tuple(
            _canonicalize_explicit_ticker_correction(
                row=row,
                allowed_tickers=allowed_tickers,
                allowed_ticker_companies=allowed_ticker_companies,
            )
            for row in rows
        )
    return canonicalized


def _canonicalize_explicit_ticker_correction(
    *,
    row: DecisionTicker,
    allowed_tickers: frozenset[str],
    allowed_ticker_companies: Mapping[str, str],
) -> DecisionTicker:
    if row.ticker.upper() in allowed_tickers:
        return row
    corrected_ticker = _extract_explicit_corrected_ticker(
        row.rationale_excerpt, allowed_tickers=allowed_tickers
    )
    if corrected_ticker is None:
        return row
    expected_company = allowed_ticker_companies.get(corrected_ticker)
    if expected_company != row.company_name:
        return row
    return replace(row, ticker=corrected_ticker)


def _extract_explicit_corrected_ticker(text: str, *, allowed_tickers: frozenset[str]) -> str | None:
    matches = tuple(
        match.group("ticker").upper()
        for match in _EXPLICIT_TICKER_CORRECTION_RE.finditer(text)
        if match.group("ticker").upper() in allowed_tickers
    )
    unique_matches = frozenset(matches)
    if len(unique_matches) != 1:
        return None
    return next(iter(unique_matches))


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
        pipe_match = re.match(
            r"^(?P<ticker>\S+)\s*[|｜]\s*(?P<company>[^|｜:：]+)\s*[|｜]\s*(?P<reason>.+)$",
            normalized,
        )
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


def _render_selection_reader_chat_message(decision: SelectionDecision) -> str:
    lines: list[str] = []
    lines.append("`/select` 已完成，本轮仅进入等待确认，不会自动启动 `/report`。")
    lines.append(_SELECTION_CHAT_BOUNDARY_NOTICE)
    lines.append("")
    lines.append("简报：")
    lines.append(f"- 进入 `/report`：{_category_brief(decision.enter_report)}")
    lines.append(f"- 观察：{_category_brief(decision.watch)}")
    lines.append(f"- 放弃：{_category_brief(decision.reject)}")
    lines.append("")
    lines.append("进入 `/report`：")
    lines.extend(_render_rows(decision.enter_report))
    lines.append("")
    lines.append("观察：")
    lines.append(f"- {_category_brief(decision.watch)}")
    lines.append("")
    lines.append("放弃：")
    lines.append(f"- {_category_brief(decision.reject)}")
    lines.append("")
    lines.append("完整观察与放弃名单请查看左侧选股报告。")
    return "\n".join(lines).strip()


def _render_selection_reader_report(
    decision: SelectionDecision,
    *,
    market: SelectionMarket = SelectionMarket.CN_A,
    selection_worker_reports: Mapping[SelectionWorkerId, str] | None = None,
    portfolio_manager_report: str | None = None,
    candidate_cache_summary_md: str | None = None,
) -> str:
    worker_reports = selection_worker_reports or {}
    lines: list[str] = [
        f"# {_selection_market_label(market)}选股报告",
        "",
        _selection_report_boundary_notice(market),
        "",
        "## 一、候选分组结论",
        "",
        "### 进入 `/report`",
        *_render_rows(decision.enter_report),
        "",
        "### 观察",
        *_render_rows(decision.watch),
        "",
        "### 放弃",
        *_render_rows(decision.reject),
        "",
        "## 二、正方策略观点",
        _selection_worker_report_text(worker_reports, SelectionWorkerId.STRATEGIST),
        "",
        "## 三、反方审查意见",
        _selection_worker_report_text(worker_reports, SelectionWorkerId.SKEPTIC),
        "",
        "## 四、综合取舍",
        _selection_worker_report_text(worker_reports, SelectionWorkerId.MANAGER),
        "",
        "## 五、最终分流决策",
        _reader_friendly_selection_text(portfolio_manager_report or ""),
    ]
    strategy_analysis = _reader_strategy_analysis(candidate_cache_summary_md, market=market)
    if strategy_analysis:
        lines.extend(("", "## 六、策略命中与分析过程", strategy_analysis))
    summary = _reader_selection_summary(candidate_cache_summary_md)
    if summary:
        lines.extend(("", "## 七、数据范围与质量", summary))
    lines.extend(
        (
            "",
            "## 八、进入 `/report` 的验证重点",
            *_render_validation_focus_rows(decision.enter_report),
        )
    )
    return "\n".join(lines).strip()


def _selection_report_boundary_notice(market: SelectionMarket) -> str:
    if market == SelectionMarket.CRYPTO:
        return _CRYPTO_SELECTION_REPORT_BOUNDARY_NOTICE
    return _SELECTION_REPORT_BOUNDARY_NOTICE


def _category_brief(rows: tuple[DecisionTicker, ...]) -> str:
    if not rows:
        return "无"
    shown = "、".join(f"{row.ticker} {row.company_name}".strip() for row in rows[:3])
    if len(rows) <= 3:
        return shown
    return f"{shown} 等 {len(rows)} 只"


def _render_rows(rows: tuple[DecisionTicker, ...]) -> list[str]:
    if not rows:
        return ["- 无"]
    rendered: list[str] = []
    for row in rows:
        rendered.append(
            f"- {row.ticker} {row.company_name}：{_reader_friendly_selection_text(row.rationale_excerpt)}"
        )
    return rendered


def _selection_worker_report_text(
    worker_reports: Mapping[SelectionWorkerId, str],
    worker_id: SelectionWorkerId,
) -> str:
    return _reader_friendly_selection_text(worker_reports.get(worker_id, "")) or "无"


def _render_validation_focus_rows(rows: tuple[DecisionTicker, ...]) -> list[str]:
    if not rows:
        return ["- 无"]
    return [
        f"- {row.ticker} {row.company_name}：{_reader_friendly_selection_text(row.rationale_excerpt)}"
        for row in rows
    ]


def _reader_strategy_analysis(
    candidate_cache_summary_md: str | None, *, market: SelectionMarket = SelectionMarket.CN_A
) -> str:
    summary = (candidate_cache_summary_md or "").strip()
    if not summary:
        return ""
    candidate_rows = _reader_candidate_strategy_rows(summary)
    strategy_names = _reader_strategy_names(summary, candidate_rows)
    lines: list[str] = ["### 命中的策略条件"]
    if strategy_names:
        for name in strategy_names:
            explanation = _READER_STRATEGY_EXPLANATIONS.get(
                name, _reader_strategy_fallback_explanation(market)
            )
            lines.append(f"- {name}：{explanation}")
    else:
        lines.append("- 候选缓存没有提供可读的策略条件明细；本报告不补造策略名称。")

    if candidate_rows:
        lines.extend(
            (
                "",
                "### 逐只策略核对",
                "| 候选 | 命中的策略条件 |",
                "| --- | --- |",
            )
        )
        for candidate, strategy_text in candidate_rows:
            lines.append(
                f"| {_markdown_table_cell(candidate)} | {_markdown_table_cell(strategy_text)} |"
            )

    lines.extend(
        (
            "",
            "### 分析过程",
            f"- 第一步：先看每只{_reader_subject_label(market)}命中的策略条件数量，判断是否是多类信号共振，而不是单一指标触发。",
            "- 第二步：再看趋势强度、相对强度和均线状态，确认上涨是否仍有延续性。",
            "- 第三步：检查成交额、量比等可交易性，排除流动性不足或异常波动过大的候选。",
            "- 第四步：扣除风险项和数据缺口，把候选分为进入 `/report`、观察、放弃三类。",
            "- 说明：报告里类似“命中6/8”的说法，指该标的命中了已批准策略集合中的多个条件；具体条件以本节逐只核对为准。",
        )
    )
    return "\n".join(lines).strip()


def _reader_strategy_fallback_explanation(market: SelectionMarket) -> str:
    if market == SelectionMarket.CRYPTO:
        return "用于确认候选标的在某一类趋势、量价、事件或风险条件上达标。"
    return _READER_STRATEGY_FALLBACK_EXPLANATION


def _reader_subject_label(market: SelectionMarket) -> str:
    if market == SelectionMarket.CRYPTO:
        return "标的"
    return "股票"


def _reader_candidate_strategy_rows(summary_md: str) -> tuple[tuple[str, str], ...]:
    header: tuple[str, ...] | None = None
    ticker_idx = company_idx = strategy_idx = -1
    rows: list[tuple[str, str]] = []
    for line in summary_md.splitlines():
        cells = _markdown_table_cells(line)
        if not cells:
            if header is not None and rows:
                break
            continue
        if header is None:
            strategy_header = _first_index(cells, ("策略变体", "策略命中"))
            ticker_header = _first_index(cells, ("股票代码", "代码"))
            company_header = _first_index(cells, ("股票名称", "公司"))
            if strategy_header >= 0 and ticker_header >= 0 and company_header >= 0:
                header = cells
                strategy_idx = strategy_header
                ticker_idx = ticker_header
                company_idx = company_header
            continue
        if _is_markdown_separator_row(cells):
            continue
        if len(cells) <= max(ticker_idx, company_idx, strategy_idx):
            continue
        ticker = _reader_friendly_selection_text(cells[ticker_idx])
        company = _reader_friendly_selection_text(cells[company_idx])
        names = _reader_strategy_names_from_text(cells[strategy_idx])
        strategy_text = "、".join(names) if names else "未记录"
        rows.append((f"{ticker} {company}".strip(), strategy_text))
    return tuple(rows)


def _reader_strategy_names(
    summary_md: str, candidate_rows: tuple[tuple[str, str], ...]
) -> tuple[str, ...]:
    names: list[str] = []
    for _candidate, strategy_text in candidate_rows:
        names.extend(strategy_text.split("、"))
    for line in _reader_section_lines(summary_md, "策略命中明细"):
        matched = re.search(r"策略变体\s*[：:]\s*(?P<value>[^。；]+)", line)
        if matched:
            names.extend(_reader_strategy_names_from_text(matched.group("value")))
    return _dedupe_strategy_names(names)


def _reader_strategy_names_from_text(text: str) -> tuple[str, ...]:
    raw = text.strip().strip("-")
    if not raw or raw == "未记录":
        return ()
    names: list[str] = []
    for part in re.split(r"[、,，]\s*", raw):
        name = _reader_friendly_selection_text(part).strip(" -。；,，")
        if not name or name in {"-", "相关指标", "未记录"}:
            continue
        names.append(name)
    return _dedupe_strategy_names(names)


def _reader_section_lines(summary_md: str, heading: str) -> tuple[str, ...]:
    lines = summary_md.splitlines()
    collected: list[str] = []
    inside = False
    for line in lines:
        if line.startswith("## "):
            title = line[3:].strip()
            if inside and title != heading:
                break
            inside = title == heading
            continue
        if inside and line.strip():
            collected.append(line.strip())
    return tuple(collected)


def _dedupe_strategy_names(values: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        name = value.strip()
        if not name or name in seen or name == "未记录":
            continue
        seen.add(name)
        out.append(name)
    return tuple(out)


def _markdown_table_cells(line: str) -> tuple[str, ...]:
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return ()
    return tuple(cell.strip() for cell in stripped.strip("|").split("|"))


def _is_markdown_separator_row(cells: tuple[str, ...]) -> bool:
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")) for cell in cells)


def _first_index(values: tuple[str, ...], candidates: tuple[str, ...]) -> int:
    for candidate in candidates:
        if candidate in values:
            return values.index(candidate)
    return -1


def _markdown_table_cell(value: str) -> str:
    return value.replace("|", "｜").replace("\n", " ").strip()


def _reader_selection_summary(candidate_cache_summary_md: str | None) -> str:
    summary = (candidate_cache_summary_md or "").strip()
    if not summary:
        return ""
    scope = _reader_scope_values(summary)
    data_quality = _reader_section_text(summary, "数据质量摘要")
    source_summary = _reader_section_text(summary, "来源摘要")
    lines: list[str] = ["### 数据范围与质量"]
    if scope:
        for label in ("交易日", "市场", "候选数量"):
            value = scope.get(label)
            if value:
                lines.append(f"- {label}：{_reader_friendly_selection_text(value)}")
    if data_quality:
        lines.append(f"- 数据质量：{_reader_friendly_summary_text(data_quality)}")
    if source_summary:
        lines.append(f"- 来源摘要：{_reader_friendly_summary_text(source_summary)}")
    if not lines[1:]:
        return ""
    lines.append("- 指标口径：总分和实际指标值用于候选核对；默认报告不展开内部字段清单。")
    return "\n".join(lines).strip()


def _reader_scope_values(summary_md: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in summary_md.splitlines():
        matched = re.match(r"^\s*-\s*(交易日|市场|候选数量)\s*[：:]\s*(?P<value>.+?)\s*$", line)
        if matched:
            values[matched.group(1)] = matched.group("value")
    return values


def _reader_section_text(summary_md: str, heading: str) -> str:
    lines = summary_md.splitlines()
    collected: list[str] = []
    inside = False
    for line in lines:
        if line.startswith("## "):
            title = line[3:].strip()
            if inside and title != heading:
                break
            inside = title == heading
            continue
        if inside and line.strip():
            collected.append(line.strip())
    return " ".join(collected).strip()


def _reader_friendly_selection_text(text: str) -> str:
    out = text.strip()
    if not out:
        return ""
    for source, target in sorted(
        _READER_TEXT_REPLACEMENTS, key=lambda item: len(item[0]), reverse=True
    ):
        out = out.replace(source, target)
    for source, target in sorted(
        _READER_VISIBLE_FIELD_LABELS.items(), key=lambda item: len(item[0]), reverse=True
    ):
        out = re.sub(rf"(?<![A-Za-z0-9_]){re.escape(source)}(?![A-Za-z0-9_])", target, out)
    out = re.sub(r"approved_[^\s，。；,;)）]+_l1", "已批准的正向策略评审", out)
    out = re.sub(r"[（(]\s*U\d+\s*[）)]", "", out)
    out = re.sub(r"`?([a-z][a-z0-9]*(?:_[a-z0-9]+)+)`?", "指标", out)
    out = out.replace("指标_评分", "评分").replace("指标评分", "评分")
    out = re.sub(r"指标\s*=", "指标值=", out)
    out = re.sub(r"([0-9一-龥]+(?:日)?均线)\\?_指标值", r"\1指标值", out)
    out = re.sub(r"([0-9一-龥]+(?:日)?均线)\\?_指标", r"\1指标", out)
    out = re.sub(r"涨停后[_\s-]*(\d+)日", r"涨停后\1日", out)
    out = re.sub(r"30日均线[_\s-]*(\d+)d增长", r"30日均线\1日涨幅", out)
    out = re.sub(r"30日均线[_\s-]*(\d+)日增长", r"30日均线\1日涨幅", out)
    out = re.sub(r"\bscore\b", "评分", out)
    out = re.sub(r"\bpe\b", "市盈率", out)
    out = re.sub(r"\bpb\b", "市净率", out)
    out = re.sub(r"\broe\b", "净资产收益率", out)
    out = out.replace("市净率（市净率）", "市净率")
    out = out.replace("平台整理信号信号", "平台整理信号")
    out = out.replace("低 波动指标", "低波动条件")
    return out


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


def _coerce_data_refresh_result(
    raw_result: object, *, default_reason: str
) -> SelectionDataRefreshResult:
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
            "`/select` 发现当前没有可用候选缓存；已有后台补数任务在运行。"
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
    reason = _selection_failure_reason_for_user(refresh.error_code or refresh.reason or "")
    return f"{_unavailable_chat_text(code)} 已尝试启动后台补数，但失败了：{reason}"


def _coerce_raw_data_maintenance_status(raw_status: object | None) -> RawDataMaintenanceStatus | None:
    if raw_status is None:
        return None
    if isinstance(raw_status, RawDataMaintenanceStatus):
        return raw_status
    if isinstance(raw_status, Mapping):
        payload = dict(raw_status)
    else:
        payload = {
            "status": getattr(raw_status, "status", None),
            "job_id": getattr(raw_status, "job_id", None),
            "jobId": getattr(raw_status, "jobId", None),
            "reason": getattr(raw_status, "reason", None),
            "message": getattr(raw_status, "message", None),
            "error": getattr(raw_status, "error", None),
        }
    status = str(payload.get("status") or "").strip().lower()
    if not status:
        return None
    job_id = _optional_result_text(payload.get("job_id") or payload.get("jobId"))
    reason = _optional_result_text(
        payload.get("reason") or payload.get("message") or payload.get("error")
    )
    normalized_payload = dict(payload)
    normalized_payload["status"] = status
    if job_id is not None:
        normalized_payload["job_id"] = job_id
    if reason is not None:
        normalized_payload["reason"] = reason
    return RawDataMaintenanceStatus(
        status=status,
        job_id=job_id,
        reason=reason,
        payload=normalized_payload,
    )


def _raw_data_maintenance_chat_text(code: SelectUnavailableCode, *, reason: str) -> str:
    if code == SelectUnavailableCode.RAW_DATA_MAINTENANCE_RUNNING:
        return "`/select` 当前不可用：原始行情正在补数据，补完后会再计算候选池。"
    if reason and reason != code.value:
        return f"`/select` 当前不可用：最近一次原始行情补数据失败。{_selection_failure_reason_for_user(reason)}"
    return "`/select` 当前不可用：最近一次原始行情补数据失败。"


def _unavailable_chat_text(code: SelectUnavailableCode) -> str:
    messages = {
        SelectUnavailableCode.NO_COMPLETED_SELECTION_RUN: "`/select` 当前不可用：没有可用的已完成选股批次。",
        SelectUnavailableCode.NO_CANDIDATE_SELECTION_RUN: "`/select` 今日没有符合已批准策略条件的候选标的。",
        SelectUnavailableCode.STALE_SELECTION_RUN: "`/select` 当前不可用：最新选股批次已过期。",
        SelectUnavailableCode.CANDIDATE_CACHE_NOT_APPROVED: "`/select` 当前不可用：候选缓存尚未批准。",
        SelectUnavailableCode.CANDIDATE_CACHE_HASH_MISMATCH: "`/select` 当前不可用：候选池完整性校验失败（hash 不一致）。",
        SelectUnavailableCode.CANDIDATE_CACHE_INTEGRITY_FAILED: "`/select` 当前不可用：候选池完整性校验失败。",
        SelectUnavailableCode.CANDIDATE_CACHE_LINEAGE_INCOMPLETE: "`/select` 当前不可用：候选池 lineage 不完整。",
        SelectUnavailableCode.SELECTION_WAREHOUSE_CHECK_MISSING: "`/select` 当前不可用：最新选股批次缺少列式仓库 manifest、hash 或 provider 证据。",
        SelectUnavailableCode.SELECT_MARKET_UNSUPPORTED: "`/select` 当前暂不支持该市场。",
        SelectUnavailableCode.CRYPTO_SELECT_HISTORY_MISSING: (
            "`/select` 当前不可用：Crypto 列式历史仓库未读到可用 spot USDT 日线。"
        ),
        SelectUnavailableCode.RAW_DATA_MAINTENANCE_RUNNING: (
            "`/select` 当前不可用：原始行情正在补数据，补完后会再计算候选池。"
        ),
        SelectUnavailableCode.RAW_DATA_MAINTENANCE_FAILED: "`/select` 当前不可用：最近一次原始行情补数据失败。",
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
        chat_text=_failed_chat_text(reason),
        select_workflow_run_id=workflow_run_id,
        evidence_path=evidence_path,
        failure_reason=reason,
    )


def _failed_chat_text(reason: str) -> str:
    return f"`/select` 失败：{_selection_failure_reason_for_user(reason)}本轮结果未生效。"


def _selection_failure_reason_for_user(reason: str) -> str:
    text = reason.strip()
    lower = text.lower()
    if not text:
        return "系统没有拿到可用的选股结果。"
    if "credential_missing" in lower or "api key" in lower or "api_key" in lower or "auth" in lower:
        return "数据源凭证没配好，拿不到选股需要的数据。"
    if "timeout" in lower or "timed out" in lower:
        return "服务响应超时，请稍后重试。"
    if "selection_workflow_cancelled" in lower or "user_cancelled" in lower:
        return "选股任务已停止。"
    if "worker_runtime_failed" in lower:
        return "选股评审服务没有正常返回。"
    if "empty_output" in lower:
        return "选股评审没有生成可用结论。"
    if "selection_result_invalid" in lower:
        return "选股结果格式不完整，系统没有采用这轮结果。"
    if "candidate_cache" in lower or "hash" in lower or "lineage" in lower:
        return "候选池数据不完整或校验失败。"
    if "selection_data_refresh" in lower or "data_run" in lower:
        return "选股数据刷新失败。"
    if "provider" in lower:
        return "数据源没有返回可核验的调用记录。"
    return "系统没有拿到可用的选股结果。"


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)
