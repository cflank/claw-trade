from __future__ import annotations

import pytest
from claw_trade.ui_backend import worker_chat_catalog
from claw_trade.ui_backend.report_repository import UiProductError
from claw_trade.ui_backend.worker_chat_catalog import (
    default_worker_id,
    list_worker_chat_menu,
    require_allowed_worker,
)


def test_worker_chat_user_surface_is_bounded_to_picker_workers() -> None:
    assert list_worker_chat_menu() == (
        {
            "workerId": "portfolio_manager",
            "displayName": "组合经理",
            "default": True,
            "aliases": ("组合经理", "PM"),
        },
        {
            "workerId": "research_manager",
            "displayName": "研究经理",
            "default": False,
            "aliases": ("研究经理",),
        },
        {
            "workerId": "market_analyst",
            "displayName": "市场分析师",
            "default": False,
            "aliases": ("市场分析师", "市场"),
        },
        {
            "workerId": "fundamental_analyst",
            "displayName": "基本面分析师",
            "default": False,
            "aliases": ("基本面分析师", "基本面"),
        },
        {
            "workerId": "news_analyst",
            "displayName": "新闻分析师",
            "default": False,
            "aliases": ("新闻分析师", "新闻"),
        },
        {
            "workerId": "social_analyst",
            "displayName": "情绪分析师",
            "default": False,
            "aliases": ("情绪分析师", "情绪"),
        },
        {
            "workerId": "risk_moderator",
            "displayName": "风险经理",
            "default": False,
            "aliases": ("风险经理", "风险"),
        },
    )


def test_structured_worker_id_is_the_only_worker_identity_input() -> None:
    assert default_worker_id() == "portfolio_manager"
    assert require_allowed_worker("portfolio_manager").display_name == "组合经理"

    for worker_id in ("组合经理", "@组合经理", "PM", "@portfolio_manager", "risk_manager"):
        with pytest.raises(UiProductError) as exc:
            require_allowed_worker(worker_id)
        assert exc.value.code == "WORKER_CHAT_WORKER_UNAVAILABLE"


def test_user_message_mentions_are_not_a_catalog_api() -> None:
    assert not hasattr(worker_chat_catalog, "parse_text_worker_mention")
    assert not hasattr(worker_chat_catalog, "detect_mentioned_worker_id")
