from claw_trade.workflow.models import Stage
from claw_trade.workflow.workers import (
    CN_A_FRONTLINE_WORKERS,
    DEFAULT_FRONTLINE_WORKERS,
    frontline_workers_for_market,
    stage_plan_for_market,
    stage_plans_for_market,
)


def test_cn_a_frontline_stage_plan_snapshot() -> None:
    frontline_plan = stage_plan_for_market(Stage.FRONTLINE, "CN_A")
    assert frontline_plan.workers == CN_A_FRONTLINE_WORKERS
    assert frontline_plan.collect_first is True

    plans = stage_plans_for_market("CN_A")
    full_order = tuple(worker_id for plan in plans for worker_id in plan.workers)
    assert full_order == (
        "market_analyst",
        "fundamental_analyst",
        "news_analyst",
        "social_analyst",
        "policy_analyst",
        "hot_money_tracker",
        "lockup_watcher",
        "bull_researcher",
        "bear_researcher",
        "research_manager",
        "trader",
        "risk_challenger",
        "risk_guardian",
        "risk_moderator",
        "portfolio_manager",
        "report_polisher",
    )
    downstream_order = tuple(
        worker_id
        for plan in plans
        if plan.stage != Stage.FRONTLINE
        for worker_id in plan.workers
    )
    assert downstream_order == (
        "bull_researcher",
        "bear_researcher",
        "research_manager",
        "trader",
        "risk_challenger",
        "risk_guardian",
        "risk_moderator",
        "portfolio_manager",
        "report_polisher",
    )


def test_non_cn_a_frontline_stage_plan_negative_snapshot() -> None:
    cn_a_only_workers = {"policy_analyst", "hot_money_tracker", "lockup_watcher"}
    for market in ("US", "HK", "CRYPTO"):
        assert frontline_workers_for_market(market) == DEFAULT_FRONTLINE_WORKERS
        assert stage_plan_for_market(Stage.FRONTLINE, market).workers == DEFAULT_FRONTLINE_WORKERS
        assert cn_a_only_workers.isdisjoint(stage_plan_for_market(Stage.FRONTLINE, market).workers)
