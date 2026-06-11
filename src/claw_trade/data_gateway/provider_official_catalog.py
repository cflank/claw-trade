from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OfficialProviderCatalogEntry:
    source_type: str
    docs_url: str
    interface_mode: str
    documented_groups: tuple[str, ...]


OFFICIAL_PROVIDER_CATALOG: tuple[OfficialProviderCatalogEntry, ...] = (
    OfficialProviderCatalogEntry(
        source_type="tushare",
        docs_url="https://tushare.pro/document/2",
        interface_mode="post_api_name",
        documented_groups=(
            "stock_market",
            "financials",
            "fundamentals",
            "moneyflow",
            "announcement",
            "realtime_quote",
            "index",
            "fund",
            "futures",
            "options",
            "hong_kong",
        ),
    ),
    OfficialProviderCatalogEntry(
        source_type="finnhub",
        docs_url="https://finnhub.io/docs/api",
        interface_mode="rest_path",
        documented_groups=(
            "quote",
            "stock_candles",
            "company_profile",
            "company_news",
            "financials",
            "metrics",
            "filings",
            "earnings",
            "economic",
            "forex",
            "crypto",
        ),
    ),
    OfficialProviderCatalogEntry(
        source_type="fred",
        docs_url="https://fred.stlouisfed.org/docs/api/fred/",
        interface_mode="rest_path",
        documented_groups=("categories", "releases", "series", "sources", "tags", "maps"),
    ),
    OfficialProviderCatalogEntry(
        source_type="coingecko_pro",
        docs_url="https://docs.coingecko.com/reference/endpoint-overview",
        interface_mode="rest_path",
        documented_groups=(
            "simple",
            "search",
            "coins",
            "contract",
            "asset_platforms",
            "categories",
            "exchanges",
            "derivatives",
            "public_treasury",
            "nfts",
            "exchange_rates",
            "trending",
            "news",
            "global",
            "onchain_dex",
        ),
    ),
    OfficialProviderCatalogEntry(
        source_type="coinglass",
        docs_url="https://docs.coinglass.com/reference",
        interface_mode="rest_path",
        documented_groups=("v4_api_reference", "futures", "spot", "options", "etf", "onchain", "indicators"),
    ),
    OfficialProviderCatalogEntry(
        source_type="glassnode",
        docs_url="https://docs.glassnode.com/basic-api/endpoints",
        interface_mode="rest_path",
        documented_groups=(
            "addresses",
            "bridges",
            "blockchain",
            "breakdowns",
            "defi",
            "derivatives",
            "distribution",
            "entities",
            "eth2",
            "fees",
            "indicators",
            "institutions",
            "lightning",
            "macro",
            "market",
            "mempool",
            "mining",
            "options",
            "point_in_time",
            "protocols",
            "signals",
            "supply",
            "transactions",
            "treasuries",
        ),
    ),
)


def iter_official_provider_catalog() -> tuple[OfficialProviderCatalogEntry, ...]:
    return OFFICIAL_PROVIDER_CATALOG
