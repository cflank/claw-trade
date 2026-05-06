import pytest

from claw_trade.workflow.models import Stage
from claw_trade.workflow.workers import worker_by_id, worker_ids


def test_worker_order_is_fixed():
    assert worker_ids() == [
        "market_analyst",
        "fundamental_analyst",
        "news_analyst",
        "social_analyst",
        "bull_researcher",
        "bear_researcher",
        "research_manager",
        "trader",
        "risk_challenger",
        "risk_guardian",
        "risk_moderator",
        "portfolio_manager",
    ]


def test_worker_stage_mapping_is_fixed():
    assert worker_by_id("market_analyst").stage == Stage.FRONTLINE
    assert worker_by_id("portfolio_manager").stage == Stage.PORTFOLIO_DECISION


def test_worker_stage_mapping_counterexample_rejected():
    assert worker_by_id("trader").stage != Stage.FRONTLINE
    with pytest.raises(KeyError):
        worker_by_id("unknown_worker")
