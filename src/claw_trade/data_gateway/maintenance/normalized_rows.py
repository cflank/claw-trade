from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from claw_trade.data_gateway.warehouse.repository import DatasetRepository


@dataclass(frozen=True)
class NormalizedMongoDiscardResult:
    criteria: dict[str, Any]
    matched_count: int
    deleted_count: int
    dry_run: bool


def discard_normalized_mongo_rows(
    repository: DatasetRepository,
    criteria: Mapping[str, Any],
    *,
    confirmed: bool = False,
    dry_run: bool = False,
) -> NormalizedMongoDiscardResult:
    criteria_dict = dict(criteria)
    matched_count = repository.count_normalized_documents_for_maintenance(criteria_dict)
    if dry_run:
        return NormalizedMongoDiscardResult(
            criteria=criteria_dict,
            matched_count=matched_count,
            deleted_count=0,
            dry_run=True,
        )
    if not confirmed:
        raise RuntimeError("normalized_mongo_discard_requires_explicit_confirmation")
    deleted_count = repository.delete_normalized_documents_for_maintenance(criteria_dict)
    return NormalizedMongoDiscardResult(
        criteria=criteria_dict,
        matched_count=matched_count,
        deleted_count=deleted_count,
        dry_run=False,
    )


def normalized_mongo_row_exists(repository: DatasetRepository, dataset_refs: Sequence[str]) -> bool:
    return any(
        repository.get_normalized_document_for_maintenance(dataset_ref) is not None
        for dataset_ref in dataset_refs
    )
