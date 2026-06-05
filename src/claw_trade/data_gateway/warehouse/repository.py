from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from hashlib import sha256
from threading import RLock
from typing import Any, Iterable, Mapping, MutableMapping, Sequence
from uuid import uuid4


ALLOWED_MONGO_COLLECTIONS: tuple[str, ...] = (
    "normalized_datasets",
    "raw_payloads",
    "provider_attempts",
    "provider_rate_limits",
    "single_flight_calls",
    "provider_result_cache",
    "dataset_manifests",
    "maintenance_jobs",
)

SUPPORTED_UNIFIED_DATASETS: tuple[str, ...] = (
    "daily_bar",
    "intraday_bar",
    "quote_snapshot",
    "order_book_snapshot",
    "corporate_action",
    "financial_statement",
    "financial_metric",
    "valuation_metric",
    "official_filing",
    "company_news",
    "macro_news",
    "social_signal",
    "event_calendar",
    "capital_flow",
    "sector_snapshot",
    "hot_money_event",
    "lockup_event",
    "macro_series",
    "crypto_derivative_metric",
    "crypto_onchain_metric",
    "defi_metric",
)

REQUIRED_MULTI_MARKET_FIELDS: tuple[str, ...] = (
    "market",
    "exchange",
    "currency",
    "timezone",
    "calendar",
    "base_asset",
    "quote_asset",
    "provider_lineage",
    "schema_id",
    "quality_flags",
)

_PROVIDER_NAME_PREFIXES: tuple[str, ...] = (
    "tushare",
    "baostock",
    "akshare",
    "eastmoney",
    "yfinance",
    "coinglass",
    "coingecko",
    "sec",
    "fred",
    "openbb",
)

_COLLECTION_KEY_FIELDS: dict[str, str] = {
    "normalized_datasets": "dataset_ref",
    "raw_payloads": "raw_ref",
    "provider_attempts": "attempt_ref",
    "provider_rate_limits": "rate_limit_ref",
    "single_flight_calls": "call_key_hash",
    "provider_result_cache": "cache_key_hash",
    "dataset_manifests": "manifest_ref",
    "maintenance_jobs": "job_id",
}

_COLLECTION_INDEXES: dict[str, tuple[tuple[str, ...], ...]] = {
    "normalized_datasets": (
        ("dataset_ref",),
        ("dataset", "market", "symbol_id", "universe_ref"),
        ("dataset", "market", "symbol_id", "period_start", "period_end"),
        ("dataset", "market", "universe_ref", "period_start", "period_end"),
    ),
    "raw_payloads": (("raw_ref",),),
    "provider_attempts": (("attempt_ref",), ("dataset_refs",)),
    "provider_rate_limits": (("rate_limit_ref",), ("rate_key_hash",)),
    "single_flight_calls": (("call_key_hash",),),
    "provider_result_cache": (("cache_key_hash",),),
    "dataset_manifests": (("manifest_ref",),),
    "maintenance_jobs": (("job_id",),),
}

_DATASET_CHECKSUM_ALGORITHM = "sha256:canonical-json-v1"
_DATASET_CHECKSUM_SCOPE = "normalized-batch-v1"
_DATASET_CHECKSUM_FIELDS = {
    "dataset_checksum",
    "dataset_checksum_algorithm",
    "dataset_checksum_scope",
    "dataset_row_count",
}


@dataclass(frozen=True)
class DatasetRecord:
    dataset_ref: str
    dataset: str
    market: str
    symbol_id: str | None
    universe_ref: str | None
    granularity: str
    period_start: date | datetime | None
    period_end: date | datetime | None
    field_set: tuple[str, ...]
    as_of: datetime | None
    fresh_until: datetime | None
    source_roles: tuple[str, ...]
    dataset_checksum: str | None
    dataset_checksum_algorithm: str | None
    dataset_checksum_scope: str | None
    row: Mapping[str, Any]
    dataset_row_count: int | None = None


@dataclass(frozen=True)
class DatasetChecksumCoverage:
    checksum: str
    actual_count: int
    expected_min: int | None
    expected_max: int | None
    min_start: Any | None
    max_end: Any | None


@dataclass(frozen=True)
class DatasetCoverageSummary:
    record_count: int
    dataset_refs: tuple[str, ...]
    ranges: tuple[tuple[Any, Any], ...]
    starts: tuple[Any, ...]
    ends: tuple[Any, ...]
    field_sets: tuple[tuple[str, ...], ...]
    source_role_sets: tuple[tuple[str, ...], ...]
    freshest_as_of: datetime | None
    freshest_until: datetime | None
    checksum_counts: tuple[DatasetChecksumCoverage, ...]


def is_provider_style_dataset_name(name: str) -> bool:
    lowered = name.strip().lower()
    if "." in lowered:
        return True
    return any(lowered.startswith(f"{prefix}_") for prefix in _PROVIDER_NAME_PREFIXES)


class _CollectionAdapter:
    def __init__(self, *, name: str, key_field: str, backend: Any) -> None:
        self.name = name
        self.key_field = key_field
        self.backend = backend

    def get(self, key: str) -> dict[str, Any] | None:
        if isinstance(self.backend, MutableMapping):
            row = self.backend.get(key)
            return None if row is None else dict(row)
        finder = getattr(self.backend, "find_one", None)
        if callable(finder):
            row = finder({self.key_field: key})
            return None if row is None else dict(row)
        return None

    def set(self, key: str, doc: Mapping[str, Any]) -> None:
        payload = dict(doc)
        payload[self.key_field] = key
        if isinstance(self.backend, MutableMapping):
            self.backend[key] = payload
            return
        payload = _mongo_safe_document(payload)
        replacer = getattr(self.backend, "replace_one", None)
        if callable(replacer):
            replacer({self.key_field: key}, payload, upsert=True)
            return
        inserter = getattr(self.backend, "insert_one", None)
        if callable(inserter):
            inserter(payload)
            return
        raise TypeError(f"unsupported collection backend for {self.name}")

    def set_many(self, items: Sequence[tuple[str, Mapping[str, Any]]]) -> None:
        if not items:
            return
        if isinstance(self.backend, MutableMapping):
            for key, doc in items:
                payload = dict(doc)
                payload[self.key_field] = key
                self.backend[key] = payload
            return

        payloads: list[tuple[str, dict[str, Any]]] = []
        for key, doc in items:
            payload = dict(doc)
            payload[self.key_field] = key
            payloads.append((key, _mongo_safe_document(payload)))

        bulk_writer = getattr(self.backend, "bulk_write", None)
        if callable(bulk_writer):
            try:
                from pymongo import ReplaceOne
            except Exception:
                ReplaceOne = None  # type: ignore[assignment]
            if ReplaceOne is not None:
                operations = [
                    ReplaceOne({self.key_field: key}, payload, upsert=True)
                    for key, payload in payloads
                ]
                bulk_writer(operations, ordered=False)
                return

        for key, payload in payloads:
            self.set(key, payload)

    def find(self, criteria: Mapping[str, Any], *, include_row: bool = True) -> tuple[dict[str, Any], ...]:
        criteria_dict = dict(criteria)
        if isinstance(self.backend, MutableMapping):
            def _copy(item: Mapping[str, Any]) -> dict[str, Any]:
                copied = dict(item)
                if not include_row:
                    copied.pop("row", None)
                return copied

            return tuple(
                _copy(item)
                for item in self.backend.values()
                if _matches_criteria(item, criteria_dict)
            )
        finder = getattr(self.backend, "find", None)
        if callable(finder):
            if include_row:
                rows = finder(criteria_dict)
            else:
                try:
                    rows = finder(criteria_dict, {"row": 0})
                except TypeError:
                    rows = finder(criteria_dict)
            return tuple(dict(item) for item in rows)
        return ()

    def find_limited(
        self,
        criteria: Mapping[str, Any],
        *,
        include_row: bool = True,
        limit: int,
    ) -> tuple[dict[str, Any], ...]:
        criteria_dict = dict(criteria)
        if limit <= 0:
            return ()
        if isinstance(self.backend, MutableMapping):
            matches: list[dict[str, Any]] = []
            for item in self.backend.values():
                if not _matches_criteria(item, criteria_dict):
                    continue
                copied = dict(item)
                if not include_row:
                    copied.pop("row", None)
                matches.append(copied)
                if len(matches) >= limit:
                    break
            return tuple(matches)
        finder = getattr(self.backend, "find", None)
        if callable(finder):
            if include_row:
                rows = finder(criteria_dict)
            else:
                try:
                    rows = finder(criteria_dict, {"row": 0})
                except TypeError:
                    rows = finder(criteria_dict)
            limiter = getattr(rows, "limit", None)
            if callable(limiter):
                rows = limiter(limit)
                return tuple(dict(item) for item in rows)
            limited: list[dict[str, Any]] = []
            for item in rows:
                limited.append(dict(item))
                if len(limited) >= limit:
                    break
            return tuple(limited)
        return ()

    def iter_find(self, criteria: Mapping[str, Any], *, include_row: bool = True) -> Iterable[dict[str, Any]]:
        criteria_dict = dict(criteria)
        if isinstance(self.backend, MutableMapping):
            for item in self.backend.values():
                if not _matches_criteria(item, criteria_dict):
                    continue
                copied = dict(item)
                if not include_row:
                    copied.pop("row", None)
                yield copied
            return
        finder = getattr(self.backend, "find", None)
        if callable(finder):
            if include_row:
                rows = finder(criteria_dict)
            else:
                try:
                    rows = finder(criteria_dict, {"row": 0})
                except TypeError:
                    rows = finder(criteria_dict)
            for item in rows:
                yield dict(item)

    def aggregate(self, pipeline: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], ...] | None:
        if isinstance(self.backend, MutableMapping):
            return None
        aggregator = getattr(self.backend, "aggregate", None)
        if not callable(aggregator):
            return None
        return tuple(dict(item) for item in aggregator([dict(stage) for stage in pipeline]))

    def count(self, criteria: Mapping[str, Any]) -> int:
        criteria_dict = dict(criteria)
        if isinstance(self.backend, MutableMapping):
            return sum(1 for item in self.backend.values() if _matches_criteria(item, criteria_dict))
        counter = getattr(self.backend, "count_documents", None)
        if callable(counter):
            return int(counter(criteria_dict))
        return sum(1 for _row in self.iter_find(criteria_dict, include_row=False))

    def ensure_indexes(self, index_specs: Sequence[tuple[str, ...]]) -> None:
        if isinstance(self.backend, MutableMapping):
            return
        creator = getattr(self.backend, "create_index", None)
        if not callable(creator):
            return
        for fields in index_specs:
            mongo_fields = [(field, 1) for field in fields]
            creator(
                mongo_fields,
                name=f"{self.name}__{'__'.join(fields)}",
                unique=len(fields) == 1 and fields[0] == self.key_field,
                background=True,
            )

    def pop(self, key: str) -> None:
        if isinstance(self.backend, MutableMapping):
            self.backend.pop(key, None)
            return
        deleter = getattr(self.backend, "delete_one", None)
        if callable(deleter):
            deleter({self.key_field: key})

    def delete_many(self, criteria: Mapping[str, Any]) -> int:
        criteria_dict = dict(criteria)
        if isinstance(self.backend, MutableMapping):
            keys = [
                key
                for key, item in self.backend.items()
                if _matches_criteria(item, criteria_dict)
            ]
            for key in keys:
                self.backend.pop(key, None)
            return len(keys)
        deleter = getattr(self.backend, "delete_many", None)
        if callable(deleter):
            result = deleter(criteria_dict)
            return int(getattr(result, "deleted_count", 0))
        deleted = 0
        for row in self.find(criteria_dict):
            key = row.get(self.key_field)
            if key is None:
                continue
            self.pop(str(key))
            deleted += 1
        return deleted

    def values(self) -> tuple[dict[str, Any], ...]:
        if isinstance(self.backend, MutableMapping):
            return tuple(dict(item) for item in self.backend.values())
        finder = getattr(self.backend, "find", None)
        if callable(finder):
            rows = finder({})
            return tuple(dict(item) for item in rows)
        return ()

    def insert_if_absent(self, key: str, doc: Mapping[str, Any]) -> bool:
        if self.get(key) is not None:
            return False
        payload = dict(doc)
        payload[self.key_field] = key
        if isinstance(self.backend, MutableMapping):
            self.backend[key] = payload
            return True
        payload = _mongo_safe_document(payload)
        inserter = getattr(self.backend, "insert_one", None)
        if callable(inserter):
            try:
                inserter(payload)
            except Exception:
                if self.get(key) is not None:
                    return False
                raise
            return True
        self.set(key, payload)
        return True


def _mongo_safe_document(value: Any) -> Any:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _mongo_safe_document(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(_mongo_safe_document(item) for item in value)
    if isinstance(value, list):
        return [_mongo_safe_document(item) for item in value]
    return value


def _date_query_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if not text:
        return None
    return text[:10]


def _matches_criteria(item: Mapping[str, Any], criteria: Mapping[str, Any]) -> bool:
    return all(_matches_value(_lookup_dotted(item, str(key)), value) for key, value in criteria.items())


def _matches_value(actual: Any, expected: Any) -> bool:
    if isinstance(expected, Mapping) and any(str(key).startswith("$") for key in expected):
        actual_value = _comparable_value(actual)
        for operator, raw_value in expected.items():
            expected_value = _comparable_value(raw_value)
            if operator == "$lte":
                if actual_value is None or actual_value > expected_value:
                    return False
                continue
            if operator == "$gte":
                if actual_value is None or actual_value < expected_value:
                    return False
                continue
            if operator == "$gt":
                if actual_value is None or actual_value <= expected_value:
                    return False
                continue
            if operator == "$ne":
                if actual_value == expected_value:
                    return False
                continue
            if operator == "$exists":
                exists = actual is not None
                if bool(raw_value) != exists:
                    return False
                continue
            if operator == "$in":
                raw_values = raw_value if isinstance(raw_value, (tuple, list, set)) else (raw_value,)
                expected_values = tuple(_comparable_value(item) for item in raw_values if item is not None)
                if isinstance(actual_value, (tuple, list, set)):
                    actual_values = tuple(_comparable_value(item) for item in actual_value)
                    if not any(item in expected_values for item in actual_values):
                        return False
                    continue
                if actual_value not in expected_values:
                    return False
                continue
            return False
        return True
    return actual == expected


def _comparable_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def _lookup_dotted(item: Mapping[str, Any], key: str) -> Any:
    current: Any = item
    for part in key.split("."):
        if not isinstance(current, Mapping):
            return None
        current = current.get(part)
    return current


class DatasetRepository:
    def __init__(
        self,
        records: Iterable[Mapping[str, Any]] | None = None,
        *,
        database: Any | None = None,
        collections: Mapping[str, Any] | None = None,
    ) -> None:
        self._lock = RLock()
        self._collections: dict[str, _CollectionAdapter] = self._build_collection_adapters(
            database=database,
            collections=collections,
        )
        self._ensure_collection_indexes()
        for record in records or ():
            self.insert_normalized(record)

    @classmethod
    def from_database(cls, database: Any) -> "DatasetRepository":
        return cls(database=database)

    @staticmethod
    def collection_names() -> tuple[str, ...]:
        return ALLOWED_MONGO_COLLECTIONS

    def insert_normalized(self, record: Mapping[str, Any]) -> None:
        record_dict = self._normalize_record(record)
        self._validate_dataset_name(str(record_dict.get("dataset", "")))
        self._validate_multi_market_fields(record_dict)
        dataset_ref = str(record_dict.get("dataset_ref") or self._build_dataset_ref(record_dict))
        record_dict["dataset_ref"] = dataset_ref
        record_dict = self._ensure_dataset_checksum((record_dict,))[0]
        with self._lock:
            self._collection("normalized_datasets").set(dataset_ref, record_dict)

    def upsert_normalized_document(self, record: Mapping[str, Any]) -> str:
        record_dict = self._normalize_record(record)
        self._validate_dataset_name(str(record_dict.get("dataset", "")))
        self._validate_multi_market_fields(record_dict)
        dataset_ref = str(record_dict.get("dataset_ref") or self._build_dataset_ref(record_dict))
        record_dict["dataset_ref"] = dataset_ref
        record_dict = self._ensure_dataset_checksum((record_dict,))[0]
        with self._lock:
            self._collection("normalized_datasets").set(dataset_ref, record_dict)
        return dataset_ref

    def upsert_normalized_documents(self, records: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
        items: list[tuple[str, dict[str, Any]]] = []
        refs: list[str] = []
        for record in records:
            record_dict = self._normalize_record(record)
            self._validate_dataset_name(str(record_dict.get("dataset", "")))
            self._validate_multi_market_fields(record_dict)
            dataset_ref = str(record_dict.get("dataset_ref") or self._build_dataset_ref(record_dict))
            record_dict["dataset_ref"] = dataset_ref
            refs.append(dataset_ref)
            items.append((dataset_ref, record_dict))
        checked_records = self._ensure_dataset_checksum(tuple(record for _ref, record in items))
        if items:
            with self._lock:
                self._collection("normalized_datasets").set_many(
                    tuple((ref, record) for (ref, _old), record in zip(items, checked_records, strict=True))
                )
        return tuple(dict.fromkeys(refs))

    def query_normalized(
        self,
        *,
        dataset: str,
        market: str,
        symbol_id: str | None,
        universe_ref: str | None,
        date_range_start: Any | None = None,
        date_range_end: Any | None = None,
        require_integrity_metadata: bool = False,
        include_row: bool = True,
    ) -> tuple[DatasetRecord, ...]:
        matched: list[DatasetRecord] = []
        criteria = self._normalized_query_criteria(
            dataset=dataset,
            market=market,
            symbol_id=symbol_id,
            universe_ref=universe_ref,
            date_range_start=date_range_start,
            date_range_end=date_range_end,
            require_integrity_metadata=require_integrity_metadata,
        )
        with self._lock:
            records = self._collection("normalized_datasets").find(criteria, include_row=include_row)
        for record in records:
            row = record.get("row")
            matched.append(
                DatasetRecord(
                    dataset_ref=str(record.get("dataset_ref")),
                    dataset=dataset,
                    market=market,
                    symbol_id=record.get("symbol_id"),
                    universe_ref=record.get("universe_ref"),
                    granularity=str(record.get("granularity", "")),
                    period_start=record.get("period_start"),
                    period_end=record.get("period_end"),
                    field_set=tuple(record.get("field_set", ())),
                    as_of=record.get("as_of"),
                    fresh_until=record.get("fresh_until"),
                    source_roles=tuple(record.get("source_roles", ())),
                    dataset_checksum=record.get("dataset_checksum"),
                    dataset_checksum_algorithm=record.get("dataset_checksum_algorithm"),
                    dataset_checksum_scope=record.get("dataset_checksum_scope"),
                    row=row if isinstance(row, Mapping) else {},
                    dataset_row_count=self._optional_int(record.get("dataset_row_count")),
                )
            )
        return tuple(matched)

    @staticmethod
    def _normalized_query_criteria(
        *,
        dataset: str,
        market: str,
        symbol_id: str | None,
        universe_ref: str | None,
        date_range_start: Any | None = None,
        date_range_end: Any | None = None,
        require_integrity_metadata: bool = False,
    ) -> dict[str, Any]:
        criteria: dict[str, Any] = {"dataset": dataset, "market": market}
        if symbol_id:
            criteria["symbol_id"] = symbol_id
        if universe_ref:
            criteria["universe_ref"] = universe_ref
        start = _date_query_text(date_range_start)
        end = _date_query_text(date_range_end)
        if end:
            criteria["period_start"] = {"$lte": end}
        if start:
            criteria["period_end"] = {"$gte": start}
        if require_integrity_metadata:
            criteria["dataset_checksum"] = {"$exists": True, "$ne": None}
            criteria["dataset_checksum_algorithm"] = _DATASET_CHECKSUM_ALGORITHM
            criteria["dataset_checksum_scope"] = _DATASET_CHECKSUM_SCOPE
            criteria["dataset_row_count"] = {"$gt": 0}
        return criteria

    def iter_normalized(
        self,
        *,
        dataset: str,
        market: str,
        symbol_id: str | None,
        universe_ref: str | None,
        date_range_start: Any | None = None,
        date_range_end: Any | None = None,
        require_integrity_metadata: bool = False,
        include_row: bool = True,
    ) -> Iterable[DatasetRecord]:
        criteria = self._normalized_query_criteria(
            dataset=dataset,
            market=market,
            symbol_id=symbol_id,
            universe_ref=universe_ref,
            date_range_start=date_range_start,
            date_range_end=date_range_end,
            require_integrity_metadata=require_integrity_metadata,
        )
        with self._lock:
            records = self._collection("normalized_datasets").iter_find(criteria, include_row=include_row)
            for record in records:
                row = record.get("row")
                yield DatasetRecord(
                    dataset_ref=str(record.get("dataset_ref")),
                    dataset=dataset,
                    market=market,
                    symbol_id=record.get("symbol_id"),
                    universe_ref=record.get("universe_ref"),
                    granularity=str(record.get("granularity", "")),
                    period_start=record.get("period_start"),
                    period_end=record.get("period_end"),
                    field_set=tuple(record.get("field_set", ())),
                    as_of=record.get("as_of"),
                    fresh_until=record.get("fresh_until"),
                    source_roles=tuple(record.get("source_roles", ())),
                    dataset_checksum=record.get("dataset_checksum"),
                    dataset_checksum_algorithm=record.get("dataset_checksum_algorithm"),
                    dataset_checksum_scope=record.get("dataset_checksum_scope"),
                    row=row if isinstance(row, Mapping) else {},
                    dataset_row_count=self._optional_int(record.get("dataset_row_count")),
                )

    def aggregate_normalized_coverage(
        self,
        *,
        dataset: str,
        market: str,
        symbol_id: str | None,
        universe_ref: str | None,
        date_range_start: Any | None = None,
        date_range_end: Any | None = None,
        require_integrity_metadata: bool = False,
        sample_limit: int = 50,
    ) -> DatasetCoverageSummary | None:
        criteria = self._normalized_query_criteria(
            dataset=dataset,
            market=market,
            symbol_id=symbol_id,
            universe_ref=universe_ref,
            date_range_start=date_range_start,
            date_range_end=date_range_end,
            require_integrity_metadata=require_integrity_metadata,
        )
        summary_pipeline: tuple[Mapping[str, Any], ...] = (
            {"$match": criteria},
            {
                "$group": {
                    "_id": None,
                    "record_count": {"$sum": 1},
                    "ranges": {"$addToSet": {"start": "$period_start", "end": "$period_end"}},
                    "starts": {"$addToSet": "$period_start"},
                    "ends": {"$addToSet": "$period_end"},
                    "field_sets": {"$addToSet": "$field_set"},
                    "source_role_sets": {"$addToSet": "$source_roles"},
                    "freshest_as_of": {"$max": "$as_of"},
                    "freshest_until": {"$max": "$fresh_until"},
                }
            },
        )
        checksum_pipeline: tuple[Mapping[str, Any], ...] = (
            {"$match": criteria},
            {
                "$group": {
                    "_id": "$dataset_checksum",
                    "actual_count": {"$sum": 1},
                    "expected_min": {"$min": "$dataset_row_count"},
                    "expected_max": {"$max": "$dataset_row_count"},
                    "min_start": {"$min": "$period_start"},
                    "max_end": {"$max": "$period_end"},
                }
            },
        )
        with self._lock:
            summary_rows = self._collection("normalized_datasets").aggregate(summary_pipeline)
            checksum_rows = self._collection("normalized_datasets").aggregate(checksum_pipeline)
            if summary_rows is None or checksum_rows is None:
                return None
            sample_rows = self._collection("normalized_datasets").find_limited(
                criteria,
                include_row=False,
                limit=sample_limit,
            )
        summary = summary_rows[0] if summary_rows else {}
        return DatasetCoverageSummary(
            record_count=self._optional_int(summary.get("record_count")) or 0,
            dataset_refs=tuple(
                str(row.get("dataset_ref"))
                for row in sample_rows
                if row.get("dataset_ref") is not None
            ),
            ranges=self._range_values(summary.get("ranges")),
            starts=self._tuple_values(summary.get("starts")),
            ends=self._tuple_values(summary.get("ends")),
            field_sets=tuple(self._tuple_values(item) for item in self._tuple_values(summary.get("field_sets"))),
            source_role_sets=tuple(self._tuple_values(item) for item in self._tuple_values(summary.get("source_role_sets"))),
            freshest_as_of=summary.get("freshest_as_of") if isinstance(summary.get("freshest_as_of"), datetime) else None,
            freshest_until=summary.get("freshest_until") if isinstance(summary.get("freshest_until"), datetime) else None,
            checksum_counts=tuple(
                DatasetChecksumCoverage(
                    checksum=str(row.get("_id") or ""),
                    actual_count=self._optional_int(row.get("actual_count")) or 0,
                    expected_min=self._optional_int(row.get("expected_min")),
                    expected_max=self._optional_int(row.get("expected_max")),
                    min_start=row.get("min_start"),
                    max_end=row.get("max_end"),
                )
                for row in checksum_rows
                if row.get("_id")
            ),
        )

    def count_normalized(
        self,
        *,
        dataset: str,
        market: str,
        symbol_id: str | None,
        universe_ref: str | None,
        date_range_start: Any | None = None,
        date_range_end: Any | None = None,
        require_integrity_metadata: bool = False,
    ) -> int:
        criteria = self._normalized_query_criteria(
            dataset=dataset,
            market=market,
            symbol_id=symbol_id,
            universe_ref=universe_ref,
            date_range_start=date_range_start,
            date_range_end=date_range_end,
            require_integrity_metadata=require_integrity_metadata,
        )
        with self._lock:
            return self._collection("normalized_datasets").count(criteria)

    def find_company_names_by_symbol_ids(
        self,
        *,
        dataset: str,
        market: str,
        symbol_ids: Sequence[str],
    ) -> Mapping[str, str]:
        symbols = tuple(dict.fromkeys(str(symbol).strip() for symbol in symbol_ids if str(symbol).strip()))
        if not symbols:
            return {}
        criteria: dict[str, Any] = {
            "dataset": dataset,
            "market": market,
            "symbol_id": {"$in": symbols},
            "row.company_name": {"$exists": True, "$ne": None},
        }
        pipeline: tuple[Mapping[str, Any], ...] = (
            {"$match": criteria},
            {"$sort": {"period_end": -1, "period_start": -1}},
            {"$group": {"_id": "$symbol_id", "company_name": {"$first": "$row.company_name"}}},
        )
        with self._lock:
            rows = self._collection("normalized_datasets").aggregate(pipeline)
            if rows is None:
                records = self._collection("normalized_datasets").find(criteria, include_row=True)
            else:
                return {
                    str(row.get("_id")): text
                    for row in rows
                    if (text := str(row.get("company_name") or "").strip())
                }

        sorted_records = sorted(
            records,
            key=lambda record: (
                _date_query_text(record.get("period_end")) or "",
                _date_query_text(record.get("period_start")) or "",
            ),
            reverse=True,
        )
        names: dict[str, str] = {}
        for record in sorted_records:
            symbol = str(record.get("symbol_id") or "").strip()
            if not symbol or symbol in names:
                continue
            row = record.get("row")
            if not isinstance(row, Mapping):
                continue
            company_name = str(row.get("company_name") or "").strip()
            if company_name:
                names[symbol] = company_name
        return names

    def insert_raw_payload(self, record: Mapping[str, Any]) -> str:
        record_dict = dict(record)
        raw_ref = str(record_dict.get("raw_ref") or f"raw:unknown:{uuid4().hex[:12]}")
        record_dict["raw_ref"] = raw_ref
        with self._lock:
            self._collection("raw_payloads").set(raw_ref, record_dict)
        return raw_ref

    def get_raw_payload(self, raw_ref: str) -> dict[str, Any] | None:
        with self._lock:
            return self._collection("raw_payloads").get(raw_ref)

    def get_normalized_document(self, dataset_ref: str) -> dict[str, Any] | None:
        with self._lock:
            return self._collection("normalized_datasets").get(dataset_ref)

    def insert_provider_attempt(self, record: Mapping[str, Any]) -> str:
        record_dict = dict(record)
        attempt_ref = str(record_dict.get("attempt_ref") or f"attempt:unknown:{uuid4().hex[:12]}")
        record_dict["attempt_ref"] = attempt_ref
        with self._lock:
            self._collection("provider_attempts").set(attempt_ref, record_dict)
        return attempt_ref

    def get_provider_attempt(self, attempt_ref: str) -> dict[str, Any] | None:
        with self._lock:
            return self._collection("provider_attempts").get(attempt_ref)

    def find_provider_attempt_refs_by_dataset_ref(
        self,
        dataset_refs: Sequence[str],
    ) -> dict[str, tuple[str, ...]]:
        wanted_refs = tuple(dict.fromkeys(str(ref) for ref in dataset_refs if str(ref).strip()))
        wanted = set(wanted_refs)
        if not wanted:
            return {}
        found: dict[str, list[str]] = {ref: [] for ref in wanted}
        with self._lock:
            attempts = self._collection("provider_attempts").find({"dataset_refs": {"$in": wanted_refs}})
        for attempt in attempts:
            attempt_ref = str(attempt.get("attempt_ref") or "").strip()
            if not attempt_ref:
                continue
            for dataset_ref in tuple(attempt.get("dataset_refs", ()) or ()):
                normalized_ref = str(dataset_ref).strip()
                if normalized_ref in found and attempt_ref not in found[normalized_ref]:
                    found[normalized_ref].append(attempt_ref)
        return {ref: tuple(refs) for ref, refs in found.items() if refs}

    def write_dataset_manifest(self, manifest: Mapping[str, Any]) -> str:
        doc = dict(manifest)
        manifest_ref = str(doc.get("manifest_ref") or f"manifest:{uuid4().hex[:12]}")
        doc["manifest_ref"] = manifest_ref
        with self._lock:
            self._collection("dataset_manifests").set(manifest_ref, doc)
        return manifest_ref

    def get_dataset_manifest(self, manifest_ref: str) -> dict[str, Any] | None:
        with self._lock:
            return self._collection("dataset_manifests").get(manifest_ref)

    def list_dataset_manifests(self) -> tuple[dict[str, Any], ...]:
        with self._lock:
            return self._collection("dataset_manifests").values()

    def save_maintenance_job(self, job_id: str, job_doc: Mapping[str, Any]) -> None:
        doc = dict(job_doc)
        doc["job_id"] = job_id
        with self._lock:
            self._collection("maintenance_jobs").set(job_id, doc)

    def get_maintenance_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._collection("maintenance_jobs").get(job_id)

    def list_maintenance_jobs(self) -> tuple[dict[str, Any], ...]:
        with self._lock:
            return self._collection("maintenance_jobs").values()

    # provider_result_cache collection
    def read_provider_result_cache(self, cache_key: str) -> dict[str, Any] | None:
        with self._lock:
            return self._collection("provider_result_cache").get(cache_key)

    def write_provider_result_cache(
        self,
        *,
        cache_key: str,
        status: str,
        dataset_refs: tuple[str, ...],
        raw_refs: tuple[str, ...],
        attempt_refs: tuple[str, ...],
        fresh_until: datetime,
        stale_until: datetime,
        empty_reason: str | None = None,
    ) -> None:
        with self._lock:
            self._collection("provider_result_cache").set(
                cache_key,
                {
                    "cache_key_hash": cache_key,
                    "status": status,
                    "dataset_refs": tuple(dataset_refs),
                    "raw_refs": tuple(raw_refs),
                    "attempt_refs": tuple(attempt_refs),
                    "empty_reason": empty_reason,
                    "fresh_until": fresh_until,
                    "stale_until": stale_until,
                    "expires_at": stale_until,
                    "updated_at": datetime.now(tz=stale_until.tzinfo or UTC),
                },
            )

    def delete_provider_result_cache(self, cache_key: str) -> None:
        with self._lock:
            self._collection("provider_result_cache").pop(cache_key)

    def delete_normalized_documents(self, criteria: Mapping[str, Any]) -> int:
        with self._lock:
            return self._collection("normalized_datasets").delete_many(criteria)

    # provider_rate_limits collection
    def get_or_init_rate_limit_record(
        self,
        *,
        rate_limit_key: str,
        window_start: datetime,
        window_seconds: int,
        max_requests: int | None,
        safety_margin: int,
        overflow_policy: str,
    ) -> dict[str, Any]:
        doc_id = self._rate_limit_doc_id(rate_limit_key, window_start)
        with self._lock:
            collection = self._collection("provider_rate_limits")
            row = collection.get(doc_id)
            if row is None:
                row = {
                    "rate_limit_ref": doc_id,
                    "rate_key_hash": rate_limit_key,
                    "window_start": window_start,
                    "window_seconds": window_seconds,
                    "max_requests": max_requests,
                    "used_requests": 0,
                    "safety_margin": safety_margin,
                    "overflow_policy": overflow_policy,
                    "cooldown_until": None,
                    "version": 1,
                }
                collection.set(doc_id, row)
            return self._normalize_rate_limit_row(row)

    def cas_increment_rate_limit_used(
        self,
        *,
        rate_limit_key: str,
        window_start: datetime,
        expected_version: int,
        cost: int,
    ) -> bool:
        doc_id = self._rate_limit_doc_id(rate_limit_key, window_start)
        with self._lock:
            collection = self._collection("provider_rate_limits")
            row = collection.get(doc_id)
            if row is None:
                return False
            if int(row.get("version", 0)) != expected_version:
                return False
            row["used_requests"] = int(row.get("used_requests", 0)) + cost
            row["version"] = expected_version + 1
            row["updated_at"] = datetime.now(tz=window_start.tzinfo or UTC)
            collection.set(doc_id, row)
            return True

    def mark_rate_limit_cooldown(
        self,
        *,
        rate_limit_key: str,
        now: datetime,
        until: datetime,
        reason: str,
    ) -> None:
        latest = self.get_latest_rate_limit_record(rate_limit_key=rate_limit_key)
        if latest is None:
            latest = self.get_or_init_rate_limit_record(
                rate_limit_key=rate_limit_key,
                window_start=self._floor_to_window(now, 60),
                window_seconds=60,
                max_requests=None,
                safety_margin=0,
                overflow_policy="fail_fast",
            )
        doc_id = str(latest["rate_limit_ref"])
        with self._lock:
            collection = self._collection("provider_rate_limits")
            row = collection.get(doc_id)
            if row is None:
                row = dict(latest)
            row["cooldown_until"] = self._aware_datetime(until, reference=now)
            row["cooldown_reason"] = reason
            row["updated_at"] = datetime.now(tz=self._datetime_tz(row["cooldown_until"]))
            row["version"] = int(row.get("version", 0)) + 1
            collection.set(doc_id, row)

    def get_active_rate_limit_cooldown(self, *, rate_limit_key: str, now: datetime) -> datetime | None:
        latest_until: datetime | None = None
        now = self._aware_datetime(now)
        with self._lock:
            rows = self._collection("provider_rate_limits").values()
        for row in rows:
            if row.get("rate_key_hash") != rate_limit_key:
                continue
            cooldown_until = row.get("cooldown_until")
            if not isinstance(cooldown_until, datetime):
                continue
            cooldown_until = self._aware_datetime(cooldown_until, reference=now)
            if cooldown_until <= now:
                continue
            if latest_until is None or cooldown_until > latest_until:
                latest_until = cooldown_until
        return latest_until

    def get_latest_rate_limit_record(self, *, rate_limit_key: str) -> dict[str, Any] | None:
        latest: dict[str, Any] | None = None
        with self._lock:
            rows = self._collection("provider_rate_limits").values()
        for row in rows:
            if row.get("rate_key_hash") != rate_limit_key:
                continue
            candidate = self._normalize_rate_limit_row(row)
            if latest is None or candidate.get("window_start") > latest.get("window_start"):
                latest = candidate
        return None if latest is None else dict(latest)

    # single_flight_calls collection
    def try_insert_single_flight_call(
        self,
        *,
        key: str,
        owner_token: str,
        lease_expires_at: datetime,
    ) -> bool:
        with self._lock:
            return self._collection("single_flight_calls").insert_if_absent(
                key,
                {
                    "call_key_hash": key,
                    "status": "pending",
                    "owner_id": owner_token,
                    "lease_expires_at": lease_expires_at,
                    "version": 1,
                },
            )

    def read_single_flight_call(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            return self._collection("single_flight_calls").get(key)

    def cas_takeover_expired_single_flight(
        self,
        *,
        key: str,
        expected_owner_token: str,
        expected_version: int,
        owner_token: str,
        lease_expires_at: datetime,
    ) -> bool:
        with self._lock:
            collection = self._collection("single_flight_calls")
            row = collection.get(key)
            if row is None:
                return False
            if row.get("owner_id") != expected_owner_token:
                return False
            if int(row.get("version", 0)) != expected_version:
                return False
            row["owner_id"] = owner_token
            row["lease_expires_at"] = lease_expires_at
            row["status"] = "pending"
            row["result_dataset_refs"] = ()
            row["result_raw_refs"] = ()
            row["result_attempt_refs"] = ()
            row["gap_summary"] = ()
            row["error_summary"] = None
            row["version"] = expected_version + 1
            row["updated_at"] = datetime.now(tz=lease_expires_at.tzinfo or UTC)
            collection.set(key, row)
            return True

    def cas_publish_single_flight_if_owner(
        self,
        *,
        key: str,
        owner_token: str,
        expected_version: int,
        status: str,
        dataset_refs: tuple[str, ...],
        raw_refs: tuple[str, ...],
        attempt_refs: tuple[str, ...],
        gap_summary: tuple[dict[str, Any], ...],
        error_summary: str | None,
    ) -> bool:
        with self._lock:
            collection = self._collection("single_flight_calls")
            row = collection.get(key)
            if row is None:
                return False
            if row.get("owner_id") != owner_token:
                return False
            if int(row.get("version", 0)) != expected_version:
                return False
            if row.get("status") != "pending":
                return False
            row["status"] = status
            row["result_dataset_refs"] = tuple(dataset_refs)
            row["result_raw_refs"] = tuple(raw_refs)
            row["result_attempt_refs"] = tuple(attempt_refs)
            row["gap_summary"] = tuple(gap_summary)
            row["error_summary"] = error_summary
            row["version"] = expected_version + 1
            row["updated_at"] = datetime.now(tz=UTC)
            collection.set(key, row)
            return True

    def _collection(self, name: str) -> _CollectionAdapter:
        return self._collections[name]

    def _ensure_collection_indexes(self) -> None:
        for name, index_specs in _COLLECTION_INDEXES.items():
            self._collection(name).ensure_indexes(index_specs)

    def _build_collection_adapters(
        self,
        *,
        database: Any | None,
        collections: Mapping[str, Any] | None,
    ) -> dict[str, _CollectionAdapter]:
        if collections is not None:
            backends = {name: collections[name] for name in ALLOWED_MONGO_COLLECTIONS}
        elif database is not None:
            backends = {name: self._resolve_database_collection(database, name) for name in ALLOWED_MONGO_COLLECTIONS}
        else:
            backends = {name: {} for name in ALLOWED_MONGO_COLLECTIONS}

        return {
            name: _CollectionAdapter(name=name, key_field=_COLLECTION_KEY_FIELDS[name], backend=backends[name])
            for name in ALLOWED_MONGO_COLLECTIONS
        }

    @staticmethod
    def _resolve_database_collection(database: Any, name: str) -> Any:
        if isinstance(database, Mapping) and name in database:
            return database[name]
        if hasattr(database, "__getitem__"):
            return database[name]
        collection = getattr(database, name, None)
        if collection is not None:
            return collection
        raise KeyError(f"database does not expose collection: {name}")

    @staticmethod
    def _rate_limit_doc_id(rate_limit_key: str, window_start: datetime) -> str:
        return f"{rate_limit_key}:{window_start.isoformat()}"

    @staticmethod
    def _datetime_tz(value: datetime) -> Any:
        return value.tzinfo if value.tzinfo is not None and value.utcoffset() is not None else UTC

    @classmethod
    def _aware_datetime(cls, value: datetime, *, reference: datetime | None = None) -> datetime:
        if value.tzinfo is not None and value.utcoffset() is not None:
            return value
        tz = cls._datetime_tz(reference) if reference is not None else UTC
        return value.replace(tzinfo=tz)

    @classmethod
    def _normalize_rate_limit_row(cls, row: Mapping[str, Any]) -> dict[str, Any]:
        normalized = dict(row)
        window_start = normalized.get("window_start")
        if isinstance(window_start, datetime):
            normalized["window_start"] = cls._aware_datetime(window_start)
        cooldown_until = normalized.get("cooldown_until")
        if isinstance(cooldown_until, datetime):
            reference = normalized.get("window_start")
            normalized["cooldown_until"] = cls._aware_datetime(
                cooldown_until,
                reference=reference if isinstance(reference, datetime) else None,
            )
        return normalized

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _tuple_values(value: Any) -> tuple[Any, ...]:
        if value is None:
            return ()
        if isinstance(value, tuple):
            return value
        if isinstance(value, list):
            return tuple(value)
        return (value,)

    @classmethod
    def _range_values(cls, value: Any) -> tuple[tuple[Any, Any], ...]:
        ranges: list[tuple[Any, Any]] = []
        for item in cls._tuple_values(value):
            if isinstance(item, Mapping):
                start = item.get("start")
                end = item.get("end")
            elif isinstance(item, (tuple, list)) and len(item) >= 2:
                start = item[0]
                end = item[1]
            else:
                continue
            ranges.append((start, end))
        return tuple(ranges)

    @staticmethod
    def _floor_to_window(now: datetime, window_seconds: int) -> datetime:
        epoch = int(now.timestamp())
        floored = epoch - (epoch % window_seconds)
        return datetime.fromtimestamp(floored, tz=now.tzinfo)

    @staticmethod
    def _build_dataset_ref(record: Mapping[str, Any]) -> str:
        dataset = DatasetRepository._required_text(record, "dataset")
        market = DatasetRepository._required_text(record, "market")
        subject_raw = record.get("symbol_id") or record.get("universe_ref")
        if subject_raw is None or not str(subject_raw).strip():
            raise ValueError("missing required dataset subject: symbol_id or universe_ref")
        subject = str(subject_raw).strip()
        granularity = DatasetRepository._required_text(record, "granularity").lower()
        period_start = str(record.get("period_start") or "")
        period_end = str(record.get("period_end") or "")
        return f"dataset:{dataset}:{market}:{subject}:{granularity}:{period_start}:{period_end}"

    @staticmethod
    def _validate_dataset_name(dataset: str) -> None:
        if dataset not in SUPPORTED_UNIFIED_DATASETS:
            raise ValueError(f"unsupported dataset: {dataset}")
        if is_provider_style_dataset_name(dataset):
            raise ValueError(f"dataset must be semantic, got provider-style name: {dataset}")

    @staticmethod
    def _validate_multi_market_fields(record: Mapping[str, Any]) -> None:
        for key in REQUIRED_MULTI_MARKET_FIELDS:
            if key not in record:
                raise ValueError(f"missing required multi-market field: {key}")
        for key in ("market", "exchange", "currency", "timezone", "calendar", "schema_id"):
            if not DatasetRepository._has_value(record.get(key)):
                raise ValueError(f"missing required multi-market field: {key}")
        if not record.get("provider_lineage"):
            raise ValueError("missing required multi-market field: provider_lineage")
        market = str(record.get("market"))
        base_asset = record.get("base_asset")
        quote_asset = record.get("quote_asset")
        if market == "CRYPTO" and (not DatasetRepository._has_value(base_asset) or not DatasetRepository._has_value(quote_asset)):
            raise ValueError("CRYPTO record requires both base_asset and quote_asset")
        if market != "CRYPTO" and (DatasetRepository._has_value(base_asset) or DatasetRepository._has_value(quote_asset)):
            raise ValueError("non-CRYPTO record must not include base_asset or quote_asset")

    @staticmethod
    def _normalize_record(record: Mapping[str, Any]) -> dict[str, Any]:
        record_dict = dict(record)
        if "market" in record_dict:
            record_dict["market"] = DatasetRepository._enum_value(record_dict["market"])
        if "granularity" in record_dict and record_dict["granularity"] is not None:
            record_dict["granularity"] = str(record_dict["granularity"]).strip().lower()
        return record_dict

    @classmethod
    def _ensure_dataset_checksum(cls, records: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], ...]:
        normalized = tuple(dict(record) for record in records)
        if not normalized:
            return ()
        if all(cls._has_dataset_checksum(record) for record in normalized):
            return normalized
        checksum = cls.dataset_checksum(normalized)
        row_count = len(normalized)
        checked: list[dict[str, Any]] = []
        for record in normalized:
            item = dict(record)
            item["dataset_checksum"] = checksum
            item["dataset_checksum_algorithm"] = _DATASET_CHECKSUM_ALGORITHM
            item["dataset_checksum_scope"] = _DATASET_CHECKSUM_SCOPE
            item["dataset_row_count"] = row_count
            checked.append(item)
        return tuple(checked)

    @staticmethod
    def _has_dataset_checksum(record: Mapping[str, Any]) -> bool:
        return (
            bool(record.get("dataset_checksum"))
            and record.get("dataset_checksum_algorithm") == _DATASET_CHECKSUM_ALGORITHM
            and record.get("dataset_checksum_scope") == _DATASET_CHECKSUM_SCOPE
            and DatasetRepository._optional_int(record.get("dataset_row_count")) is not None
            and int(record.get("dataset_row_count") or 0) > 0
        )

    @staticmethod
    def dataset_checksum(records: Sequence[Mapping[str, Any]]) -> str:
        payload = json.dumps(
            tuple(
                {key: value for key, value in record.items() if key not in _DATASET_CHECKSUM_FIELDS}
                for record in records
            ),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return f"sha256:{sha256(payload.encode('utf-8')).hexdigest()}"

    @staticmethod
    def _required_text(record: Mapping[str, Any], key: str) -> str:
        value = record.get(key)
        if value is None or not str(value).strip():
            raise ValueError(f"missing required dataset field: {key}")
        return str(value).strip()

    @staticmethod
    def _has_value(value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, str):
            return bool(value.strip())
        return True

    @staticmethod
    def _enum_value(value: Any) -> str:
        enum_value = getattr(value, "value", None)
        if isinstance(enum_value, str):
            return enum_value
        return str(value)
