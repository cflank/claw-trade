from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from claw_trade.data_gateway.models import GapReason, Market
from claw_trade.data_gateway.needs import DataNeed, DataNeedGap
from claw_trade.data_gateway.official_catalog import iter_official_catalog_endpoints
from claw_trade.data_gateway.official_catalog.models import OfficialEndpoint


_NEED_ENDPOINTS: dict[tuple[Market, str], tuple[str, ...]] = {
    (Market.CN_A, "market_price"): ("tushare.daily", "akshare.stock_zh_a_hist"),
    (Market.CN_A, "capital_flow"): (
        "tushare.moneyflow",
        "akshare.stock_individual_fund_flow",
        "eastmoney.datacenter.stock_fund_flow_daily",
    ),
    (Market.CN_A, "financial_metric"): ("tushare.fina_indicator", "tushare.daily_basic"),
    (Market.CN_A, "financial_statement"): ("tushare.income", "tushare.balancesheet", "tushare.cashflow"),
    (Market.CN_A, "official_filing"): ("tushare.anns_d", "cninfo.official_filing"),
    (Market.CN_A, "company_news"): ("akshare.stock_news_em", "google_news.cn_a_company_news"),
    (Market.US, "market_price"): ("finnhub.stock_candle", "yahoo_finance.chart_daily"),
    (Market.US, "quote_snapshot"): ("finnhub.quote", "yahoo_finance.quote_snapshot"),
    (Market.US, "company_news"): ("finnhub.company_news", "google_news.us_company_news"),
    (Market.US, "financial_metric"): ("finnhub.stock_metric", "yahoo_finance.fundamentals_timeseries"),
    (Market.US, "official_filing"): ("finnhub.stock_filings", "sec.submissions"),
    (Market.HK, "market_price"): ("tushare.hk_daily", "yahoo_finance.hk_chart_daily"),
    (Market.HK, "financial_metric"): ("tushare.hk_fina_indicator", "yahoo_finance.hk_fundamentals_timeseries"),
    (Market.HK, "official_filing"): ("hkexnews.regulatory_announcements",),
    (Market.HK, "company_news"): ("google_news.hk_company_news",),
    (Market.CRYPTO, "market_price"): (
        "coinglass.coin_market_data_history",
        "coingecko_pro.coins_id_market_chart",
        "binance.spot_daily_bar",
    ),
    (Market.CRYPTO, "funding_rate"): ("coinglass.futures_funding_rate",),
    (Market.CRYPTO, "open_interest"): (
        "coinglass.futures_open_interest_aggregated_history",
        "coinglass.futures_open_interest",
    ),
    (Market.CRYPTO, "long_short_ratio"): ("coinglass.futures_long_short_ratio",),
    (Market.CRYPTO, "liquidation"): ("coinglass.futures_liquidation",),
    (Market.CRYPTO, "options_open_interest"): ("coinglass.options_open_interest",),
    (Market.CRYPTO, "options_volume"): ("coinglass.options_volume",),
    (Market.CRYPTO, "exchange_netflow"): ("coinglass.spot_coin_netflow", "coinglass.futures_coin_netflow"),
    (Market.CRYPTO, "onchain_exchange_balance"): ("coinglass.onchain_exchange_balance",),
    (Market.CRYPTO, "onchain_whale_transfer"): ("coinglass.onchain_whale_transfer",),
    (Market.CRYPTO, "onchain_metric"): (
        "glassnode.addresses_active_count",
        "glassnode.indicators_mvrv_account_based",
        "glassnode.indicators_nupl",
    ),
    (Market.CRYPTO, "company_news"): ("google_news.crypto_company_news", "github.releases"),
    (Market.CRYPTO, "sentiment"): ("alternative_me.fear_greed",),
}


@dataclass(frozen=True)
class ResolvedNeed:
    need: DataNeed
    candidate_endpoints: tuple[OfficialEndpoint, ...] = ()
    gap: DataNeedGap | None = None


def resolve_need(
    need: DataNeed,
    *,
    catalog_endpoints: Iterable[OfficialEndpoint] | None = None,
) -> ResolvedNeed:
    endpoint_ids = _NEED_ENDPOINTS.get((need.market, need.need_kind), ())
    if not endpoint_ids:
        return ResolvedNeed(need=need, gap=_mapping_gap(need))

    source_endpoints = iter_official_catalog_endpoints() if catalog_endpoints is None else catalog_endpoints
    endpoint_by_id = {endpoint.endpoint_id: endpoint for endpoint in source_endpoints}
    candidates = tuple(endpoint_by_id[endpoint_id] for endpoint_id in endpoint_ids if endpoint_id in endpoint_by_id)
    if not candidates:
        return ResolvedNeed(need=need, gap=_mapping_gap(need))
    return ResolvedNeed(need=need, candidate_endpoints=candidates)


def _mapping_gap(need: DataNeed) -> DataNeedGap:
    return DataNeedGap(
        need_id=need.need_id,
        reason=GapReason.RESOLVER_MAPPING_MISSING,
        human_readable=f"resolver has no catalog endpoint mapping for {need.market.value}:{need.need_kind}",
    )


__all__ = ["ResolvedNeed", "resolve_need"]
