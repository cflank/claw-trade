from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OfficialProviderCatalogEntry:
    source_type: str
    docs_url: str
    interface_mode: str


OFFICIAL_PROVIDER_CATALOG: tuple[OfficialProviderCatalogEntry, ...] = (
    OfficialProviderCatalogEntry(
        source_type="tushare",
        docs_url="https://tushare.pro/document/2",
        interface_mode="post_api_name_and_sdk",
    ),
    OfficialProviderCatalogEntry(
        source_type="finnhub",
        docs_url="https://finnhub.io/docs/api",
        interface_mode="rest_path",
    ),
    OfficialProviderCatalogEntry(
        source_type="fred",
        docs_url="https://fred.stlouisfed.org/docs/api/fred/",
        interface_mode="rest_path",
    ),
    OfficialProviderCatalogEntry(
        source_type="coingecko",
        docs_url="https://docs.coingecko.com/reference/endpoint-overview",
        interface_mode="rest_path",
    ),
    OfficialProviderCatalogEntry(
        source_type="coingecko_pro",
        docs_url="https://docs.coingecko.com/reference/endpoint-overview",
        interface_mode="rest_path",
    ),
    OfficialProviderCatalogEntry(
        source_type="coinglass",
        docs_url="https://docs.coinglass.com/reference",
        interface_mode="rest_path",
    ),
    OfficialProviderCatalogEntry(
        source_type="glassnode",
        docs_url="https://docs.glassnode.com/basic-api/endpoints",
        interface_mode="rest_path",
    ),
)


def iter_official_provider_catalog() -> tuple[OfficialProviderCatalogEntry, ...]:
    return OFFICIAL_PROVIDER_CATALOG
