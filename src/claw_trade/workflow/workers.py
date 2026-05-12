from __future__ import annotations

from claw_trade.workflow.models import RunStatus, Stage, StagePlan, WorkerSpec


# 核心 12 个决策 worker 加 1 个读者版终稿 worker 在这里固定声明，LLM 不能临场决定“下一步叫谁”。
WORKER_SPECS: tuple[WorkerSpec, ...] = (
    WorkerSpec("market_analyst", Stage.FRONTLINE),
    WorkerSpec("fundamental_analyst", Stage.FRONTLINE),
    WorkerSpec("news_analyst", Stage.FRONTLINE),
    WorkerSpec("social_analyst", Stage.FRONTLINE),
    WorkerSpec("bull_researcher", Stage.INVESTMENT_DEBATE),
    WorkerSpec("bear_researcher", Stage.INVESTMENT_DEBATE),
    WorkerSpec("research_manager", Stage.INVESTMENT_DECISION),
    WorkerSpec("trader", Stage.TRADE_DECISION),
    WorkerSpec("risk_challenger", Stage.RISK_DEBATE),
    WorkerSpec("risk_guardian", Stage.RISK_DEBATE),
    WorkerSpec("risk_moderator", Stage.RISK_DEBATE),
    WorkerSpec("portfolio_manager", Stage.PORTFOLIO_DECISION),
    WorkerSpec("report_polisher", Stage.FINAL_REPORT),
)

# 每个阶段是否可 collect-first、依赖哪个上游阶段，也集中放在这里，runner 只按表执行。
STAGE_PLANS: tuple[StagePlan, ...] = (
    StagePlan(
        stage=Stage.FRONTLINE,
        workers=("market_analyst", "fundamental_analyst", "news_analyst", "social_analyst"),
        running_status=RunStatus.FRONTLINE_RUNNING,
        ready_status=RunStatus.FRONTLINE_READY,
        next_stage=Stage.INVESTMENT_DEBATE,
        required_upstream_stage=None,
        collect_first=True,
    ),
    StagePlan(
        stage=Stage.INVESTMENT_DEBATE,
        workers=("bull_researcher", "bear_researcher"),
        running_status=RunStatus.INVESTMENT_DEBATE_RUNNING,
        ready_status=RunStatus.INVESTMENT_DEBATE_READY,
        next_stage=Stage.INVESTMENT_DECISION,
        required_upstream_stage=Stage.FRONTLINE,
        collect_first=True,
    ),
    StagePlan(
        stage=Stage.INVESTMENT_DECISION,
        workers=("research_manager",),
        running_status=RunStatus.INVESTMENT_DECISION_RUNNING,
        ready_status=RunStatus.INVESTMENT_DECISION_READY,
        next_stage=Stage.TRADE_DECISION,
        required_upstream_stage=Stage.INVESTMENT_DEBATE,
        collect_first=False,
    ),
    StagePlan(
        stage=Stage.TRADE_DECISION,
        workers=("trader",),
        running_status=RunStatus.TRADE_DECISION_RUNNING,
        ready_status=RunStatus.TRADE_DECISION_READY,
        next_stage=Stage.RISK_DEBATE,
        required_upstream_stage=Stage.INVESTMENT_DECISION,
        collect_first=False,
    ),
    StagePlan(
        stage=Stage.RISK_DEBATE,
        workers=("risk_challenger", "risk_guardian", "risk_moderator"),
        running_status=RunStatus.RISK_DEBATE_RUNNING,
        ready_status=RunStatus.RISK_DEBATE_READY,
        next_stage=Stage.PORTFOLIO_DECISION,
        required_upstream_stage=Stage.TRADE_DECISION,
        collect_first=False,
    ),
    StagePlan(
        stage=Stage.PORTFOLIO_DECISION,
        workers=("portfolio_manager",),
        running_status=RunStatus.PORTFOLIO_DECISION_RUNNING,
        ready_status=RunStatus.PORTFOLIO_DECISION_READY,
        next_stage=Stage.FINAL_REPORT,
        required_upstream_stage=Stage.RISK_DEBATE,
        collect_first=False,
    ),
    StagePlan(
        stage=Stage.FINAL_REPORT,
        workers=("report_polisher",),
        running_status=RunStatus.FINAL_REPORT_RUNNING,
        ready_status=RunStatus.FINAL_REPORT_READY,
        next_stage=None,
        required_upstream_stage=Stage.PORTFOLIO_DECISION,
        collect_first=False,
    ),
)

_WORKERS_BY_ID = {worker.id: worker for worker in WORKER_SPECS}
_STAGE_PLAN_BY_STAGE = {plan.stage: plan for plan in STAGE_PLANS}
_RUNNING_STATUS_TO_STAGE = {plan.running_status: plan.stage for plan in STAGE_PLANS}
_READY_STATUS_TO_STAGE = {plan.ready_status: plan.stage for plan in STAGE_PLANS}

if len(_WORKERS_BY_ID) != len(WORKER_SPECS):
    raise ValueError("Duplicate worker id found in WORKER_SPECS")

if len(_STAGE_PLAN_BY_STAGE) != len(STAGE_PLANS):
    raise ValueError("Duplicate stage found in STAGE_PLANS")


def all_worker_ids() -> tuple[str, ...]:
    return tuple(worker.id for worker in WORKER_SPECS)


def worker_ids() -> list[str]:
    # 固定报告链路 worker 顺序的公开只读接口；保留旧调用名以兼容历史测试。
    return list(all_worker_ids())


def worker_by_id_or_none(worker_id: str) -> WorkerSpec | None:
    return _WORKERS_BY_ID.get(worker_id)


def worker_by_id(worker_id: str) -> WorkerSpec:
    worker = worker_by_id_or_none(worker_id)
    if worker is None:
        raise KeyError(f"Unknown worker id: {worker_id}")
    return worker


def stage_plan(stage: Stage) -> StagePlan:
    try:
        return _STAGE_PLAN_BY_STAGE[stage]
    except KeyError as exc:
        raise KeyError(f"Unknown stage: {stage}") from exc


def is_running_status(status: RunStatus) -> bool:
    return status in _RUNNING_STATUS_TO_STAGE


def is_ready_status(status: RunStatus) -> bool:
    return status in _READY_STATUS_TO_STAGE


def stage_for_running_status(status: RunStatus) -> Stage:
    try:
        return _RUNNING_STATUS_TO_STAGE[status]
    except KeyError as exc:
        raise KeyError(f"Status is not a running status: {status}") from exc


def stage_for_ready_status(status: RunStatus) -> Stage:
    try:
        return _READY_STATUS_TO_STAGE[status]
    except KeyError as exc:
        raise KeyError(f"Status is not a ready status: {status}") from exc
