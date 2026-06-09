from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Mapping, Sequence

PHASE1_CORE_ASSETS: tuple[str, ...] = ("BTC", "ETH", "SOL")
DEFAULT_QUOTE_ASSET = "USDT"
KNOWN_QUOTE_ASSETS = (
    "FDUSD",
    "USDT",
    "USDC",
    "BUSD",
    "TUSD",
    "USD1",
    "USD",
    "BTC",
    "ETH",
    "BNB",
    "TRY",
    "EUR",
    "BRL",
    "AUD",
    "GBP",
    "IDR",
    "JPY",
)
PHASE1_DEFAULT_SYMBOLS: Mapping[str, str] = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "SOL": "SOLUSDT",
}
PHASE1_UNIVERSE_REF = "crypto_core_phase1"


@dataclass(frozen=True)
class CryptoAssetMapping:
    asset: str
    symbol_id: str
    base_asset: str
    quote_asset: str
    source: str
    status: str = "active"

    def as_dict(self) -> dict[str, str]:
        return {
            "asset": self.asset,
            "symbol_id": self.symbol_id,
            "base_asset": self.base_asset,
            "quote_asset": self.quote_asset,
            "source": self.source,
            "status": self.status,
        }


def map_asset_to_symbol(
    asset: str,
    *,
    candidates: Sequence[Mapping[str, Any]] = (),
    quote_asset: str = DEFAULT_QUOTE_ASSET,
) -> CryptoAssetMapping:
    normalized_asset = _normalize_asset(_strip_known_quote(asset))
    normalized_quote = _normalize_asset(quote_asset)
    for candidate in candidates:
        mapping = _mapping_from_candidate(candidate, asset=normalized_asset, quote_asset=normalized_quote)
        if mapping is not None:
            return mapping
    default_symbol = PHASE1_DEFAULT_SYMBOLS.get(normalized_asset)
    if default_symbol and normalized_quote == "USDT":
        return CryptoAssetMapping(
            asset=normalized_asset,
            symbol_id=default_symbol,
            base_asset=normalized_asset,
            quote_asset=normalized_quote,
            source="phase1_static_core",
        )
    if normalized_quote == DEFAULT_QUOTE_ASSET:
        return CryptoAssetMapping(
            asset=normalized_asset,
            symbol_id=f"{normalized_asset}{DEFAULT_QUOTE_ASSET}",
            base_asset=normalized_asset,
            quote_asset=DEFAULT_QUOTE_ASSET,
            source="default_usdt_pair",
        )
    raise ValueError(f"no active {normalized_quote} symbol mapping for asset: {normalized_asset}")


def build_phase1_core_universe_manifest(
    *,
    candidates: Sequence[Mapping[str, Any]] = (),
    as_of: datetime | None = None,
    assets: Sequence[str] = PHASE1_CORE_ASSETS,
) -> dict[str, Any]:
    mappings = tuple(map_asset_to_symbol(asset, candidates=candidates).as_dict() for asset in assets)
    return {
        "schema_id": "crypto_core_universe_manifest.v1",
        "universe_ref": PHASE1_UNIVERSE_REF,
        "market": "CRYPTO",
        "quote_asset": "USDT",
        "as_of": (as_of or datetime.now(tz=UTC)).astimezone(UTC).isoformat(),
        "assets": tuple(mapping["asset"] for mapping in mappings),
        "symbol_ids": tuple(mapping["symbol_id"] for mapping in mappings),
        "mappings": mappings,
        "generation_method": "phase1_static_core_with_manifest_override",
    }


def symbol_candidates_from_download_manifest(manifest: Mapping[str, Any]) -> tuple[dict[str, str], ...]:
    seen: dict[str, dict[str, str]] = {}
    for item in tuple(manifest.get("items", ()) or ()):
        if not isinstance(item, Mapping):
            continue
        symbol = str(item.get("symbol") or "").strip().upper()
        if not symbol or symbol in seen:
            continue
        base, quote = split_usdt_symbol(symbol)
        if base is None or quote is None:
            continue
        seen[symbol] = {
            "symbol": symbol,
            "base_asset": base,
            "quote_asset": quote,
            "status": "active",
            "source": "download_manifest",
        }
    return tuple(seen.values())


def split_usdt_symbol(symbol: str) -> tuple[str | None, str | None]:
    normalized = symbol.strip().upper()
    if not normalized.endswith(DEFAULT_QUOTE_ASSET) or len(normalized) <= 4:
        return None, None
    return normalized[:-4], DEFAULT_QUOTE_ASSET


def _mapping_from_candidate(
    candidate: Mapping[str, Any],
    *,
    asset: str,
    quote_asset: str,
) -> CryptoAssetMapping | None:
    status = str(candidate.get("status") or "active").strip().lower()
    if status not in {"active", "trading"}:
        return None
    symbol = str(candidate.get("symbol") or candidate.get("symbol_id") or "").strip().upper()
    base = str(candidate.get("base_asset") or "").strip().upper()
    quote = str(candidate.get("quote_asset") or "").strip().upper()
    if not base or not quote:
        inferred_base, inferred_quote = split_usdt_symbol(symbol)
        base = base or (inferred_base or "")
        quote = quote or (inferred_quote or "")
    if base != asset or quote != quote_asset or not symbol:
        return None
    return CryptoAssetMapping(
        asset=asset,
        symbol_id=symbol,
        base_asset=base,
        quote_asset=quote,
        source=str(candidate.get("source") or "symbol_manifest"),
        status=status,
    )


def _normalize_asset(asset: str) -> str:
    normalized = asset.strip().upper()
    if not normalized or not normalized.isalnum():
        raise ValueError(f"invalid crypto asset: {asset}")
    return normalized


def _strip_known_quote(value: str) -> str:
    normalized = value.strip().upper().replace("-", "/").replace(".", "/")
    if "/" in normalized:
        base, _quote = normalized.split("/", 1)
        return base
    if normalized.endswith(DEFAULT_QUOTE_ASSET) and len(normalized) > len(DEFAULT_QUOTE_ASSET):
        return normalized[: -len(DEFAULT_QUOTE_ASSET)]
    for quote in KNOWN_QUOTE_ASSETS:
        if normalized.endswith(quote) and len(normalized) > len(quote):
            return normalized[: -len(quote)]
    return normalized
