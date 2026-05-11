from __future__ import annotations

import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_PYTHON_ROOT = REPO_ROOT / "openclaw_plugins" / "claw-trade-frontline-tools" / "python"
if str(SHARED_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(SHARED_PYTHON_ROOT))

from frontline_data_pack.config import load_frontline_provider_config  # noqa: E402
from frontline_data_pack.errors import PROVIDER_NOT_APPROVED, FrontlineValidationError  # noqa: E402
from frontline_data_pack.provider_specs import (  # noqa: E402
    assert_provider_approved,
    load_fundamental_provider_specs,
    load_market_provider_specs,
    load_news_provider_specs,
    load_social_provider_specs,
)


def _base_env() -> dict[str, str]:
    return {
        "CN_A_MONGODB_URI": "mongodb://localhost:27017/claw_trade",
        "CLAW_TRADE_OPENVIKING_BASE_URI": "https://openviking.internal",
        "CLAW_TRADE_OPENVIKING_AUTH_MODE": "none",
    }


def test_t_pvd_001_market_default_enabled_matrix() -> None:
    config = load_frontline_provider_config(_base_env())
    specs = load_market_provider_specs(config)
    by_provider_endpoint = {(spec.provider, spec.endpoint): spec.enabled for spec in specs}

    assert by_provider_endpoint[("akshare", "stock_zh_a_hist")] is True
    assert by_provider_endpoint[("eastmoney_direct", "push2his_kline")] is True
    assert by_provider_endpoint[("sina", "stock_zh_a_daily")] is True
    assert by_provider_endpoint[("tencent", "stock_zh_a_hist_tx")] is True
    assert by_provider_endpoint[("baostock", "daily_history_quotes")] is True
    assert by_provider_endpoint[("efinance", "history_quotes")] is True
    assert by_provider_endpoint[("tushare", "pro_bar")] is True


def test_t_pvd_001_news_default_enabled_matrix() -> None:
    config = load_frontline_provider_config(_base_env())
    specs = load_news_provider_specs(config)
    by_provider_endpoint = {(spec.provider, spec.endpoint): spec.enabled for spec in specs}

    assert by_provider_endpoint[("akshare", "stock_news_em")] is True
    assert by_provider_endpoint[("akshare", "stock_info_global_cls")] is True
    assert by_provider_endpoint[("bocha", "cn_web_search")] is True
    assert by_provider_endpoint[("tavily", "news_web_search")] is True
    assert by_provider_endpoint[("jina", "search_and_reader")] is True
    assert by_provider_endpoint[("newsnow", "hot_topics_aggregator")] is True
    assert by_provider_endpoint[("minimax", "structured_search")] is True
    assert by_provider_endpoint[("tushare", "anns_d")] is True


def test_t_pvd_001_fundamental_default_enabled_matrix() -> None:
    config = load_frontline_provider_config(_base_env())
    specs = load_fundamental_provider_specs(config)
    by_provider_endpoint = {(spec.provider, spec.endpoint): spec.enabled for spec in specs}

    assert by_provider_endpoint[("tushare", "stock_basic")] is True
    assert by_provider_endpoint[("tushare", "daily_basic")] is True
    assert by_provider_endpoint[("tushare", "fina_indicator")] is True
    assert by_provider_endpoint[("tushare", "income")] is True
    assert by_provider_endpoint[("tushare", "cashflow")] is True
    assert by_provider_endpoint[("akshare", "company_info")] is True
    assert by_provider_endpoint[("akshare", "financial_abstract")] is True
    assert by_provider_endpoint[("akshare", "stock_zh_a_spot_em")] is True
    assert by_provider_endpoint[("eastmoney_direct", "quote_valuation_snapshot")] is True
    assert by_provider_endpoint[("baostock", "candidate_financials")] is True
    assert by_provider_endpoint[("efinance", "candidate_enrichment")] is True


def test_t_pvd_001_social_default_enabled_matrix() -> None:
    config = load_frontline_provider_config(_base_env())
    specs = load_social_provider_specs(config)
    by_provider_endpoint = {(spec.provider, spec.endpoint): spec.enabled for spec in specs}

    assert by_provider_endpoint[("eastmoney_akshare", "hot_rank_latest")] is True
    assert by_provider_endpoint[("eastmoney_akshare", "hot_keyword")] is True
    assert by_provider_endpoint[("eastmoney_akshare", "related_hot_rank")] is True
    assert by_provider_endpoint[("eastmoney_direct", "full_hot_rank_board")] is True
    assert by_provider_endpoint[("bocha", "public_page_search")] is True
    assert by_provider_endpoint[("jina", "public_page_search")] is True
    assert by_provider_endpoint[("tavily", "public_page_search")] is True
    assert by_provider_endpoint[("alphaear_news_source_list", "market_background")] is True
    assert by_provider_endpoint[("xueqiu_guba", "post_level_sentiment")] is True


def test_t_pvd_001_unknown_provider_is_not_approved() -> None:
    with pytest.raises(FrontlineValidationError) as exc_info:
        assert_provider_approved(domain="market", provider="not_in_matrix")
    assert exc_info.value.code == PROVIDER_NOT_APPROVED
