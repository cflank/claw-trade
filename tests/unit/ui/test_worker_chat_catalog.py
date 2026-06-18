from __future__ import annotations

import pytest
from claw_trade.ui_backend import worker_chat_catalog
from claw_trade.ui_backend.report_repository import UiProductError
from claw_trade.ui_backend.worker_chat_catalog import (
    default_worker_id,
    list_worker_chat_menu,
    require_allowed_worker,
)


def test_worker_chat_catalog_only_exposes_approved_workers() -> None:
    menu = list_worker_chat_menu()
    assert [item["workerId"] for item in menu] == [
        "portfolio_manager",
        "research_manager",
        "market_analyst",
        "fundamental_analyst",
        "news_analyst",
        "social_analyst",
        "risk_moderator",
    ]
    assert [item["displayName"] for item in menu] == [
        "组合经理",
        "研究经理",
        "市场分析师",
        "基本面分析师",
        "新闻分析师",
        "情绪分析师",
        "风险经理",
    ]


def test_default_worker_is_portfolio_manager() -> None:
    assert default_worker_id() == "portfolio_manager"
    menu = list_worker_chat_menu()
    assert [item["workerId"] for item in menu if item["default"]] == ["portfolio_manager"]


def test_catalog_does_not_parse_message_mentions() -> None:
    assert not hasattr(worker_chat_catalog, "parse_text_worker_mention")
    assert not hasattr(worker_chat_catalog, "detect_mentioned_worker_id")


def test_require_allowed_worker_accepts_only_canonical_worker_id() -> None:
    assert require_allowed_worker("market_analyst").worker_id == "market_analyst"

    for worker_id in ("@市场分析师", "市场分析师", "市场", "@market_analyst", "unknown_worker"):
        with pytest.raises(UiProductError) as exc:
            require_allowed_worker(worker_id)
        assert exc.value.code == "WORKER_CHAT_WORKER_UNAVAILABLE"


def test_menu_aliases_are_display_search_terms_without_at_prefixes() -> None:
    menu = list_worker_chat_menu()
    assert all("aliases" in item for item in menu)
    assert all(not alias.startswith("@") for item in menu for alias in item["aliases"])
