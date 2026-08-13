from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from claw_trade.data_gateway.runtime import build_data_gateway_runtime_from_env
from claw_trade.data_gateway.warehouse.selection_columnar import (
    SelectionColumnarManifest,
    SelectionColumnarWarehouse,
)
from claw_trade.selection.models import SelectionMarket

_CRYPTO_HISTORY_DAILY_GLOB = "market=CRYPTO/dataset=daily_bar/granularity=daily/*.parquet"


def validate_selection_columnar_manifest_ref(manifest_ref: str, *, expected_sha256: str | None = None) -> bool:
    manifest = SelectionColumnarWarehouse.default().load_valid_manifest_ref(
        manifest_ref,
        expected_sha256=expected_sha256,
    )
    return manifest is not None and selection_columnar_manifest_sources_are_current(manifest)


# Guard source: explicit 2026-08-12 user approval to reject CRYPTO selection caches
# older than refreshed source data.
def selection_columnar_manifest_sources_are_current(manifest: SelectionColumnarManifest) -> bool:
    if manifest.market != SelectionMarket.CRYPTO or manifest.universe_scope != "spot_usdt":
        return True
    try:
        created_at = _parse_timestamp(manifest.created_at)
        latest_source_at = _latest_crypto_source_update_at(trade_date=manifest.trade_date)
    except Exception:  # noqa: BLE001
        return False
    return latest_source_at is None or created_at >= latest_source_at


def _latest_crypto_source_update_at(*, trade_date: str) -> datetime | None:
    updated_at = [
        datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        for path in _crypto_history_root().glob(_CRYPTO_HISTORY_DAILY_GLOB)
        if path.is_file()
    ]
    if os.environ.get("DATA_GATEWAY_MONGODB_URI", "").strip() or os.environ.get("CN_A_MONGODB_URI", "").strip():
        repository = build_data_gateway_runtime_from_env().repository
        updated_at.extend(
            _parse_timestamp(str(item.get("created_at") or ""))
            for item in repository.list_dataset_manifests()
            if _crypto_runtime_manifest_covers(item, trade_date=trade_date)
            and str(item.get("created_at") or "").strip()
        )
    return max(updated_at, default=None)


def _crypto_runtime_manifest_covers(manifest: Mapping[str, Any], *, trade_date: str) -> bool:
    return (
        str(manifest.get("storage") or "") == "parquet"
        and str(manifest.get("status") or "active") == "active"
        and str(manifest.get("market") or "") == "CRYPTO"
        and str(manifest.get("dataset") or "") == "daily_bar"
        and str(manifest.get("granularity") or "") == "daily"
        and str(manifest.get("period_start_min") or "")[:10] <= trade_date
        and str(manifest.get("period_end_max") or "")[:10] >= trade_date
        and "binance_spot_all_symbols" in tuple(manifest.get("universe_refs", ()) or ())
    )


def _crypto_history_root() -> Path:
    return Path(
        os.environ.get("CLAW_TRADE_CRYPTO_HISTORY_COLUMNAR_ROOT")
        or "data/crypto-history-full/normalized-columnar-usdt-only"
    )


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
