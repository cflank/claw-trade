from __future__ import annotations

from collections.abc import Iterable

from . import coingecko_pro, coinglass, finnhub, fred, glassnode, public_sources, tushare
from .models import OfficialEndpoint


OFFICIAL_ENDPOINTS: tuple[OfficialEndpoint, ...] = (
    *tushare.ENDPOINTS,
    *coinglass.ENDPOINTS,
    *finnhub.ENDPOINTS,
    *fred.ENDPOINTS,
    *glassnode.ENDPOINTS,
    *coingecko_pro.ENDPOINTS,
    *public_sources.ENDPOINTS,
)


def iter_official_catalog_endpoints() -> Iterable[OfficialEndpoint]:
    return iter(OFFICIAL_ENDPOINTS)


def iter_endpoints() -> Iterable[OfficialEndpoint]:
    return iter_official_catalog_endpoints()


def all_endpoints() -> tuple[OfficialEndpoint, ...]:
    return OFFICIAL_ENDPOINTS


__all__ = [
    "OFFICIAL_ENDPOINTS",
    "OfficialEndpoint",
    "all_endpoints",
    "iter_endpoints",
    "iter_official_catalog_endpoints",
]
