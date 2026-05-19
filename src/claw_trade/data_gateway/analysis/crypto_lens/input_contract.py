from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from claw_trade.data_gateway.models import (
    CRYPTO_DOMAIN_KEYS,
    Conflict,
    CryptoDomainBundle,
    DataGap,
    DomainReadiness,
    FreshnessStatus,
    Market,
    NormalizedCryptoMarketBundle,
)


_GAP_FIELD_PREFIXES: Mapping[str, tuple[str, ...]] = {
    "market": ("market.",),
    "ohlcv": ("ohlcv.",),
    "derivatives": ("derivatives.",),
    "liquidation_map": ("liquidation_map.", "liquidation."),
    "onchain": ("onchain.",),
    "macro": ("macro.",),
    "events": ("events.",),
    "ahr999": ("ahr999.",),
}


@dataclass(frozen=True)
class CryptoLensInput:
    run_id: str
    call_id: str
    ticker: str
    market: Market
    quote: str
    as_of: str
    start_date: str
    end_date: str
    freshness: FreshnessStatus
    domains: CryptoDomainBundle
    domain_status: Mapping[str, DomainReadiness]
    data_gaps: tuple[DataGap, ...]
    conflicts: tuple[Conflict, ...]
    attempt_refs: tuple[str, ...]
    normalized_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.market != Market.CRYPTO:
            raise ValueError("CryptoLensInput.market must be CRYPTO")
        if set(self.domain_status) != set(CRYPTO_DOMAIN_KEYS):
            raise ValueError("CryptoLensInput.domain_status must include all crypto domains")

    @classmethod
    def from_normalized_bundle(cls, bundle: NormalizedCryptoMarketBundle) -> "CryptoLensInput":
        return cls(
            run_id=bundle.run_id,
            call_id=bundle.call_id,
            ticker=bundle.ticker,
            market=bundle.market,
            quote=bundle.quote,
            as_of=bundle.as_of,
            start_date=bundle.start_date,
            end_date=bundle.end_date,
            freshness=bundle.freshness,
            domains=bundle.domains,
            domain_status=bundle.domain_status,
            data_gaps=bundle.data_gaps,
            conflicts=bundle.conflicts,
            attempt_refs=bundle.attempt_refs,
            normalized_refs=bundle.normalized_refs,
        )

    def gap_ids_for(self, domain_key: str) -> tuple[str, ...]:
        prefixes = _GAP_FIELD_PREFIXES.get(domain_key, ())
        if not prefixes:
            return ()
        return tuple(
            gap.gap_id
            for gap in self.data_gaps
            if any(gap.field_path.startswith(prefix) for prefix in prefixes)
        )

    def as_dict(self) -> Mapping[str, Any]:
        return {
            "run_id": self.run_id,
            "call_id": self.call_id,
            "ticker": self.ticker,
            "market": self.market.value,
            "quote": self.quote,
            "as_of": self.as_of,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "freshness": self.freshness.value,
            "domains": self.domains.to_mapping(),
            "domain_status": {key: status.value for key, status in self.domain_status.items()},
            "data_gaps": self.data_gaps,
            "conflicts": self.conflicts,
            "attempt_refs": self.attempt_refs,
            "normalized_refs": self.normalized_refs,
        }
