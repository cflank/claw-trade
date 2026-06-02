from __future__ import annotations

from hashlib import sha256
from typing import Any, Sequence

from claw_trade.data_gateway.warehouse.repository import DatasetRepository


class NormalizedStore:
    def __init__(self, repository: DatasetRepository | None = None) -> None:
        self._repository = repository or DatasetRepository()

    def upsert(self, rows: Sequence[dict[str, Any]]) -> tuple[str, ...]:
        if not rows:
            return ()
        model_rows = tuple(self._document_from_row(row) for row in rows)
        return self._repository.upsert_normalized_documents(model_rows)

    @staticmethod
    def _document_from_row(row: dict[str, Any]) -> dict[str, Any]:
        model_row = dict(row)
        dataset = model_row.get("dataset", "unknown_dataset")
        market = model_row.get("market")
        model_row.setdefault("dataset", dataset)
        model_row.setdefault("market", market)
        model_row.setdefault("symbol_id", model_row.get("symbol_id"))
        model_row.setdefault("universe_ref", model_row.get("universe_ref"))
        model_row.setdefault("granularity", model_row.get("granularity", "unknown"))
        model_row.setdefault("period_start", model_row.get("period_start"))
        model_row.setdefault("period_end", model_row.get("period_end"))
        model_row["field_set"] = _available_field_set(model_row)
        model_row.setdefault("as_of", model_row.get("as_of"))
        model_row.setdefault("fresh_until", model_row.get("fresh_until"))
        model_row.setdefault("source_roles", model_row.get("source_roles", ("official",)))
        model_row.setdefault("exchange", model_row.get("exchange"))
        model_row.setdefault("currency", model_row.get("currency"))
        model_row.setdefault("timezone", model_row.get("timezone"))
        model_row.setdefault("calendar", model_row.get("calendar"))
        model_row.setdefault("base_asset", model_row.get("base_asset"))
        model_row.setdefault("quote_asset", model_row.get("quote_asset"))
        model_row.setdefault("provider_lineage", model_row.get("provider_lineage", {}))
        model_row.setdefault("schema_id", model_row.get("schema_id", f"{dataset}.v1"))
        model_row.setdefault("quality_flags", model_row.get("quality_flags", ()))
        model_row["dataset_ref"] = _dataset_ref(model_row)
        model_row["row"] = dict(model_row.get("row") or row)
        return model_row


def _dataset_ref(row: dict[str, Any]) -> str:
    dataset = row.get("dataset", "unknown_dataset")
    market = row.get("market")
    key_material = repr(
        (
            dataset,
            market,
            row.get("symbol_id"),
            row.get("universe_ref"),
            row.get("granularity"),
            row.get("period_start"),
            row.get("period_end"),
            row.get("schema_id"),
            row.get("schema_version"),
            tuple(row.get("field_set", ())),
        )
    )
    digest = sha256(key_material.encode("utf-8")).hexdigest()[:16]
    return f"dataset:{dataset}:{market}:{digest}"


def _available_field_set(row: dict[str, Any]) -> tuple[str, ...]:
    return tuple(sorted(key for key, value in row.items() if key != "row" and value is not None))
