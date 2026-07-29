from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from threading import RLock
from typing import Any, Iterable, Mapping, MutableMapping, Sequence
from uuid import uuid4

from claw_trade.data_gateway.warehouse.normalized_columnar import NormalizedColumnarWarehouse

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
    "company_profile",
    "company_news",
    "macro_news",
    "social_signal",
    "event_calendar",
    "capital_flow",
    "northbound_flow",
    "margin_trading",
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
STRUCTURAL_ROW_FIELDS: tuple[str, ...] = ("open_time", "close_time", "timestamp", "time")
_COLUMNAR_ROW_REPLACEMENT_DATASETS: frozenset[str] = frozenset(
    {"daily_bar", "intraday_bar", "quote_snapshot", "order_book_snapshot"}
)
_COLUMNAR_METRIC_STRUCTURAL_FIELDS: frozenset[str] = frozenset(
    {
        "as_of",
        "base_asset",
        "calendar",
        "currency",
        "dataset",
        "date",
        "exchange",
        "fresh_until",
        "granularity",
        "market",
        "period_end",
        "period_start",
        "provider_lineage",
        "quality_flags",
        "quote_asset",
        "schema_id",
        "source_raw_refs",
        "source_roles",
        "symbol_id",
        "time",
        "timestamp",
        "timestamp_utc",
        "timezone",
        "universe_ref",
    }
)
_SOURCE_ROLE_READ_PRIORITY: dict[str, int] = {
    "local_seed": 0,
    "official": 1,
    "paid_data": 2,
    "built_in_public": 20,
    "sentiment": 30,
    "discovery": 40,
    "event_expectation": 50,
}

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
    "provider_attempts": (("attempt_ref",), ("dataset_ref_prefixes",)),
    "provider_rate_limits": (("rate_limit_ref",), ("rate_key_hash",)),
    "single_flight_calls": (("call_key_hash",),),
    "provider_result_cache": (("cache_key_hash",),),
    "dataset_manifests": (
        ("manifest_ref",),
        ("storage", "status", "dataset", "market"),
        ("storage", "status", "dataset", "market", "period_start_min", "period_end_max"),
    ),
    "maintenance_jobs": (("job_id",),),
}

_SEED_READ_COLLECTIONS: tuple[str, ...] = (
    "raw_payloads",
    "provider_attempts",
    "dataset_manifests",
)

_DATASET_CHECKSUM_ALGORITHM = "sha256:canonical-json-v1"
_DATASET_CHECKSUM_SCOPE = "normalized-batch-v1"
_DATASET_CHECKSUM_FIELDS = {
    "dataset_checksum",
    "dataset_checksum_algorithm",
    "dataset_checksum_scope",
    "dataset_row_count",
}
_COMPANY_NAME_ROW_FIELDS = ("company_name", "name", "stock_name", "code_name", "security_name")
_COMPANY_NAME_BATCH_QUERY_MIN_SYMBOLS = 20


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


def _company_name_from_row(row: Mapping[str, Any]) -> str | None:
    for field in _COMPANY_NAME_ROW_FIELDS:
        text = str(row.get(field) or "").strip()
        if text:
            return text
    return None


class _CollectionAdapter:
    def __init__(
        self,
        *,
        name: str,
        key_field: str,
        backend: Any,
        read_backends: Sequence[Any] = (),
    ) -> None:
        self.name = name
        self.key_field = key_field
        self.backend = backend
        self.read_backends = tuple(read_backends)

    def get(self, key: str) -> dict[str, Any] | None:
        for backend in self._read_backends():
            row = self._get_from_backend(backend, key)
            if row is not None:
                return row
        return None

    def set(self, key: str, doc: Mapping[str, Any]) -> None:
        payload = dict(doc)
        payload[self.key_field] = key
        if isinstance(self.backend, MutableMapping):
            self.backend[key] = payload
            return
        if self.key_field != "_id":
            payload.pop("_id", None)
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
            if self.key_field != "_id":
                payload.pop("_id", None)
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
        return self._dedupe_rows(self.find_all_backends(criteria, include_row=include_row))

    def find_all_backends(self, criteria: Mapping[str, Any], *, include_row: bool = True) -> tuple[dict[str, Any], ...]:
        criteria_dict = dict(criteria)
        rows: list[dict[str, Any]] = []
        for backend in self._read_backends():
            rows.extend(self._find_from_backend(backend, criteria_dict, include_row=include_row))
        return tuple(rows)

    def _find_from_backend(
        self,
        backend: Any,
        criteria_dict: Mapping[str, Any],
        *,
        include_row: bool,
    ) -> tuple[dict[str, Any], ...]:
        if isinstance(backend, MutableMapping):
            def _copy(item: Mapping[str, Any]) -> dict[str, Any]:
                copied = dict(item)
                if not include_row:
                    copied.pop("row", None)
                return copied

            return tuple(
                _copy(item)
                for item in backend.values()
                if _matches_criteria(item, criteria_dict)
            )
        finder = getattr(backend, "find", None)
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
        if self.read_backends:
            return self.find(criteria_dict, include_row=include_row)[:limit]
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
        yield from self.find(criteria, include_row=include_row)

    def aggregate(self, pipeline: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], ...] | None:
        if self.read_backends:
            return None
        if isinstance(self.backend, MutableMapping):
            return None
        aggregator = getattr(self.backend, "aggregate", None)
        if not callable(aggregator):
            return None
        return tuple(dict(item) for item in aggregator([dict(stage) for stage in pipeline]))

    def count(self, criteria: Mapping[str, Any]) -> int:
        criteria_dict = dict(criteria)
        if self.read_backends:
            return len(self.find(criteria_dict, include_row=False))
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
        rows: list[dict[str, Any]] = []
        for backend in self._read_backends():
            rows.extend(self._values_from_backend(backend))
        return self._dedupe_rows(tuple(rows))

    def _values_from_backend(self, backend: Any) -> tuple[dict[str, Any], ...]:
        if isinstance(backend, MutableMapping):
            return tuple(dict(item) for item in backend.values())
        finder = getattr(backend, "find", None)
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

    def _read_backends(self) -> tuple[Any, ...]:
        return (self.backend, *self.read_backends)

    def _get_from_backend(self, backend: Any, key: str) -> dict[str, Any] | None:
        if isinstance(backend, MutableMapping):
            row = backend.get(key)
            return None if row is None else dict(row)
        finder = getattr(backend, "find_one", None)
        if callable(finder):
            row = finder({self.key_field: key})
            return None if row is None else dict(row)
        return None

    def _dedupe_rows(self, rows: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], ...]:
        deduped: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in rows:
            copied = dict(row)
            key = copied.get(self.key_field)
            if key is not None:
                key_text = str(key)
                if key_text in seen:
                    continue
                seen.add(key_text)
            deduped.append(copied)
        return tuple(deduped)


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


def _columnar_range_start_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat() if _is_midnight_datetime(value) else value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if not text:
        return None
    return text[:10] if _is_date_only_text(text) or _is_midnight_text(text) else text


def _columnar_range_end_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if _is_midnight_datetime(value):
            return _end_of_day_text(value.date().isoformat())
        return value.isoformat()
    if isinstance(value, date):
        return _end_of_day_text(value.isoformat())
    text = str(value).strip()
    if not text:
        return None
    if _is_date_only_text(text) or _is_midnight_text(text):
        return _end_of_day_text(text[:10])
    return text


def _is_midnight_datetime(value: datetime) -> bool:
    return value.hour == 0 and value.minute == 0 and value.second == 0 and value.microsecond == 0


def _is_date_only_text(text: str) -> bool:
    return len(text) == 10 and text[4:5] == "-" and text[7:8] == "-"


def _is_midnight_text(text: str) -> bool:
    if "T" not in text and " " not in text:
        return False
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return False
    return _is_midnight_datetime(parsed)


def _end_of_day_text(day: str) -> str:
    return f"{day}T23:59:59.999999"


def _date_query_datetime_start(value: Any) -> datetime | None:
    parsed = _coerce_query_datetime(value)
    if parsed is not None:
        return parsed
    parsed_date = _coerce_query_date(value)
    if parsed_date is None:
        return None
    return datetime(parsed_date.year, parsed_date.month, parsed_date.day)


def _date_query_datetime_end(value: Any) -> datetime | None:
    parsed = _coerce_query_datetime(value)
    if parsed is not None:
        return parsed
    parsed_date = _coerce_query_date(value)
    if parsed_date is None:
        return None
    return datetime(parsed_date.year, parsed_date.month, parsed_date.day, 23, 59, 59, 999999)


def _coerce_query_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _naive_utc_datetime(value)
    if isinstance(value, date):
        return None
    text = str(value or "").strip()
    if not text or "T" not in text:
        return None
    try:
        return _naive_utc_datetime(datetime.fromisoformat(text.replace("Z", "+00:00")))
    except ValueError:
        return None


def _coerce_query_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.astimezone(UTC).date() if value.tzinfo is not None else value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _naive_utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _matches_criteria(item: Mapping[str, Any], criteria: Mapping[str, Any]) -> bool:
    for key, value in criteria.items():
        if key == "$or":
            branches = value if isinstance(value, (tuple, list)) else ()
            if not any(isinstance(branch, Mapping) and _matches_criteria(item, branch) for branch in branches):
                return False
            continue
        if not _matches_value(_lookup_dotted(item, str(key)), value):
            return False
    return True


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


def _dataset_ref_prefix_map(dataset_refs: Sequence[str]) -> dict[str, tuple[str, ...]]:
    grouped: dict[str, list[str]] = {}
    for dataset_ref in dataset_refs:
        prefix = _dataset_ref_lookup_prefix(dataset_ref)
        if prefix is None:
            continue
        grouped.setdefault(prefix, []).append(dataset_ref)
    return {prefix: tuple(refs) for prefix, refs in grouped.items()}


def _dataset_ref_lookup_prefix(dataset_ref: str) -> str | None:
    parts = str(dataset_ref).split(":")
    if len(parts) < 6 or parts[0] != "dataset":
        return None
    if len(parts) >= 8 and parts[2] == "CRYPTO":
        return ":".join(parts[:6]) + ":"
    return ":".join(parts[:5]) + ":"


def _compact_refs_for_metadata(prefix: str, refs: Sequence[str], *, full_limit: int = 500, sample_limit: int = 20) -> dict[str, Any]:
    normalized_refs = tuple(dict.fromkeys(str(ref) for ref in refs if str(ref).strip()))
    digest = "sha256:" + sha256(
        json.dumps(normalized_refs, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    if len(normalized_refs) <= full_limit:
        sample = normalized_refs
        truncated = False
    else:
        head_count = sample_limit // 2
        tail_count = sample_limit - head_count
        sample = tuple((*normalized_refs[:head_count], *normalized_refs[-tail_count:]))
        truncated = True
    return {
        f"{prefix}_refs": sample,
        f"{prefix}_ref_count": len(normalized_refs),
        f"{prefix}_refs_truncated": truncated,
        f"{prefix}_refs_sample": sample,
        f"{prefix}_refs_sha256": digest,
    }


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
        seed_database: Any | None = None,
        collections: Mapping[str, Any] | None = None,
        normalized_columnar: NormalizedColumnarWarehouse | None = None,
        allow_normalized_mongo_fallback: bool = False,
        allow_normalized_mongo_read: bool | None = None,
    ) -> None:
        self._lock = RLock()
        self._normalized_columnar = normalized_columnar
        self._allow_normalized_mongo_fallback = allow_normalized_mongo_fallback
        self._allow_normalized_mongo_read = (
            database is None if allow_normalized_mongo_read is None else allow_normalized_mongo_read
        )
        self._collections: dict[str, _CollectionAdapter] = self._build_collection_adapters(
            database=database,
            seed_database=seed_database,
            collections=collections,
        )
        self._ensure_collection_indexes()
        for record in records or ():
            self.insert_normalized(record)

    @classmethod
    def from_database(
        cls,
        database: Any,
        *,
        seed_database: Any | None = None,
        normalized_columnar: NormalizedColumnarWarehouse | None = None,
        allow_normalized_mongo_fallback: bool = False,
        allow_normalized_mongo_read: bool = False,
    ) -> "DatasetRepository":
        return cls(
            database=database,
            seed_database=seed_database,
            normalized_columnar=normalized_columnar,
            allow_normalized_mongo_fallback=allow_normalized_mongo_fallback,
            allow_normalized_mongo_read=allow_normalized_mongo_read,
        )

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
        self._persist_normalized_records(((dataset_ref, record_dict),))

    def upsert_normalized_document(self, record: Mapping[str, Any]) -> str:
        record_dict = self._normalize_record(record)
        self._validate_dataset_name(str(record_dict.get("dataset", "")))
        self._validate_multi_market_fields(record_dict)
        dataset_ref = str(record_dict.get("dataset_ref") or self._build_dataset_ref(record_dict))
        record_dict["dataset_ref"] = dataset_ref
        record_dict = self._ensure_dataset_checksum((record_dict,))[0]
        self._persist_normalized_records(((dataset_ref, record_dict),))
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
        deduped_items = self._dedupe_normalized_upsert_items(items)
        checked_records = self._ensure_dataset_checksum(tuple(record for _ref, record in deduped_items))
        if deduped_items:
            self._persist_normalized_records(
                tuple((ref, record) for (ref, _old), record in zip(deduped_items, checked_records, strict=True))
            )
        return tuple(dict.fromkeys(refs))

    @staticmethod
    def _dedupe_normalized_upsert_items(items: Sequence[tuple[str, dict[str, Any]]]) -> tuple[tuple[str, dict[str, Any]], ...]:
        latest_by_ref: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        for dataset_ref, record in items:
            if dataset_ref not in latest_by_ref:
                order.append(dataset_ref)
            latest_by_ref[dataset_ref] = record
        return tuple((dataset_ref, latest_by_ref[dataset_ref]) for dataset_ref in order)

    def _persist_normalized_records(self, items: Sequence[tuple[str, Mapping[str, Any]]]) -> None:
        if not items:
            return
        if self._normalized_columnar is not None:
            for group in self._columnar_record_groups(tuple(record for _ref, record in items)):
                result = self._normalized_columnar.write_records(group)
                if result.manifest:
                    self._write_columnar_manifest_with_supersession(result.manifest, result.dataset_refs)
            return
        with self._lock:
            self._collection("normalized_datasets").set_many(tuple((ref, dict(record)) for ref, record in items))

    @staticmethod
    def _columnar_record_groups(records: Sequence[Mapping[str, Any]]) -> tuple[tuple[Mapping[str, Any], ...], ...]:
        groups: dict[tuple[str, str, str], list[Mapping[str, Any]]] = {}
        for record in records:
            key = (
                str(record.get("dataset") or "unknown_dataset"),
                str(record.get("market") or "unknown_market"),
                str(record.get("granularity") or "unknown"),
            )
            groups.setdefault(key, []).append(record)
        return tuple(tuple(group) for group in groups.values())

    def _write_columnar_manifest_with_supersession(
        self,
        manifest: Mapping[str, Any],
        dataset_refs: Sequence[str],
    ) -> None:
        incoming_refs = {str(ref) for ref in dataset_refs if str(ref).strip()}
        new_manifest = dict(manifest)
        incoming_prefixes = set(_dataset_ref_prefix_map(tuple(incoming_refs)))
        new_manifest.setdefault("dataset_ref_prefixes", tuple(sorted(incoming_prefixes)))
        manifest_ref = str(new_manifest.get("manifest_ref") or f"manifest:{uuid4().hex[:12]}")
        new_manifest["manifest_ref"] = manifest_ref
        superseded_refs: list[str] = []
        now = datetime.now(tz=UTC)
        criteria = self._columnar_manifest_base_criteria(
            dataset=str(new_manifest.get("dataset") or ""),
            market=str(new_manifest.get("market") or ""),
        )
        with self._lock:
            collection = self._collection("dataset_manifests")
            for existing in collection.find(criteria):
                existing_ref = str(existing.get("manifest_ref") or "").strip()
                if not existing_ref or existing_ref == manifest_ref:
                    continue
                if not self._columnar_manifest_should_supersede_existing(
                    new_manifest,
                    existing,
                    incoming_refs=incoming_refs,
                    incoming_prefixes=incoming_prefixes,
                ):
                    continue
                updated = dict(existing)
                updated["status"] = "superseded"
                updated["superseded_at"] = now
                updated["superseded_by_manifest_ref"] = manifest_ref
                updated.update(_compact_refs_for_metadata("superseded_by_dataset", tuple(sorted(incoming_refs))))
                collection.set(existing_ref, updated)
                superseded_refs.append(existing_ref)
            if superseded_refs:
                new_manifest["supersedes_manifest_refs"] = tuple(superseded_refs)
            collection.set(manifest_ref, new_manifest)

    @staticmethod
    def _columnar_manifest_should_supersede_existing(
        incoming: Mapping[str, Any],
        existing: Mapping[str, Any],
        *,
        incoming_refs: set[str],
        incoming_prefixes: set[str],
    ) -> bool:
        incoming_granularity = str(incoming.get("granularity") or "")
        existing_granularity = str(existing.get("granularity") or "")
        existing_dataset_refs = {str(ref) for ref in tuple(existing.get("dataset_refs", ()) or ())}
        if incoming_refs.intersection(existing_dataset_refs):
            if (
                incoming_granularity != "realtime"
                and existing_dataset_refs.difference(incoming_refs)
                and not DatasetRepository._manifest_date_range_covers(incoming, existing)
            ):
                return False
            return not DatasetRepository._incoming_manifest_has_lower_source_priority(incoming, existing)

        existing_prefixes = {str(ref) for ref in tuple(existing.get("dataset_ref_prefixes", ()) or ())}
        if (
            incoming_prefixes
            and existing_prefixes
            and incoming_prefixes.intersection(existing_prefixes)
            and DatasetRepository._manifest_prefix_supersession_allowed(incoming, existing)
        ):
            return not DatasetRepository._incoming_manifest_has_lower_source_priority(incoming, existing)

        if incoming_granularity != existing_granularity:
            return False
        if not DatasetRepository._manifest_identity_overlaps(incoming, existing):
            return False
        if not DatasetRepository._manifest_replacement_scope_overlaps(incoming, existing):
            return False
        if DatasetRepository._incoming_manifest_has_lower_source_priority(incoming, existing):
            return False
        if incoming_granularity == "realtime":
            return True
        return DatasetRepository._manifest_date_range_covers(incoming, existing)

    @staticmethod
    def _incoming_manifest_has_lower_source_priority(incoming: Mapping[str, Any], existing: Mapping[str, Any]) -> bool:
        return DatasetRepository._manifest_source_role_rank(incoming) > DatasetRepository._manifest_source_role_rank(existing)

    @staticmethod
    def _manifest_source_role_rank(manifest: Mapping[str, Any]) -> int:
        roles = tuple(str(role) for role in tuple(manifest.get("source_roles", ()) or ()) if str(role).strip())
        if not roles:
            return 100
        return min(_SOURCE_ROLE_READ_PRIORITY.get(role, 60) for role in roles)

    @staticmethod
    def _manifest_prefix_supersession_allowed(incoming: Mapping[str, Any], existing: Mapping[str, Any]) -> bool:
        if str(incoming.get("granularity") or "") != str(existing.get("granularity") or ""):
            return False
        return str(incoming.get("granularity") or "") == "realtime"

    @staticmethod
    def _manifest_replacement_scope_overlaps(incoming: Mapping[str, Any], existing: Mapping[str, Any]) -> bool:
        dataset = str(incoming.get("dataset") or "")
        if dataset in _COLUMNAR_ROW_REPLACEMENT_DATASETS:
            return True
        incoming_fields = DatasetRepository._manifest_metric_fields(incoming)
        existing_fields = DatasetRepository._manifest_metric_fields(existing)
        return bool(incoming_fields and existing_fields and incoming_fields.issuperset(existing_fields))

    @staticmethod
    def _manifest_metric_fields(manifest: Mapping[str, Any]) -> set[str]:
        return {
            str(field)
            for field in tuple(manifest.get("field_set", ()) or ())
            if str(field).strip() and str(field) not in _COLUMNAR_METRIC_STRUCTURAL_FIELDS
        }

    @staticmethod
    def _manifest_identity_overlaps(incoming: Mapping[str, Any], existing: Mapping[str, Any]) -> bool:
        incoming_symbols = {str(item) for item in tuple(incoming.get("symbol_ids", ()) or ()) if str(item)}
        existing_symbols = {str(item) for item in tuple(existing.get("symbol_ids", ()) or ()) if str(item)}
        if incoming_symbols or existing_symbols:
            return bool(incoming_symbols and existing_symbols and incoming_symbols.intersection(existing_symbols))

        incoming_universes = {str(item) for item in tuple(incoming.get("universe_refs", ()) or ()) if str(item)}
        existing_universes = {str(item) for item in tuple(existing.get("universe_refs", ()) or ()) if str(item)}
        if incoming_universes or existing_universes:
            return bool(incoming_universes and existing_universes and incoming_universes.intersection(existing_universes))

        return False

    @staticmethod
    def _manifest_date_range_overlaps(incoming: Mapping[str, Any], existing: Mapping[str, Any]) -> bool:
        incoming_start = _columnar_range_start_text(incoming.get("period_start_min"))
        incoming_end = _columnar_range_end_text(incoming.get("period_end_max"))
        existing_start = _columnar_range_start_text(existing.get("period_start_min"))
        existing_end = _columnar_range_end_text(existing.get("period_end_max"))
        if not incoming_start or not incoming_end or not existing_start or not existing_end:
            return False
        return incoming_start <= existing_end and existing_start <= incoming_end

    @staticmethod
    def _manifest_date_range_covers(incoming: Mapping[str, Any], existing: Mapping[str, Any]) -> bool:
        incoming_start = _columnar_range_start_text(incoming.get("period_start_min"))
        incoming_end = _columnar_range_end_text(incoming.get("period_end_max"))
        existing_start = _columnar_range_start_text(existing.get("period_start_min"))
        existing_end = _columnar_range_end_text(existing.get("period_end_max"))
        if not incoming_start or not incoming_end or not existing_start or not existing_end:
            return False
        return incoming_start <= existing_start and incoming_end >= existing_end

    def _normalized_columnar_manifests(
        self,
        *,
        dataset: str,
        market: str,
        symbol_id: str | None,
        universe_ref: str | None,
        date_range_start: Any | None,
        date_range_end: Any | None,
    ) -> tuple[dict[str, Any], ...]:
        start = _columnar_range_start_text(date_range_start)
        end = _columnar_range_end_text(date_range_end)
        criteria = self._columnar_manifest_query_criteria(
            dataset=dataset,
            market=market,
            start=start,
            end=end,
        )
        with self._lock:
            manifests = self._collection("dataset_manifests").find(criteria)
        matched = tuple(
            manifest
            for manifest in manifests
            if self._columnar_manifest_matches(
                manifest,
                dataset=dataset,
                market=market,
                symbol_id=symbol_id,
                universe_ref=universe_ref,
                start=start,
                end=end,
            )
        )
        return tuple(sorted(matched, key=self._columnar_manifest_read_sort_key))

    @staticmethod
    def _columnar_manifest_read_sort_key(manifest: Mapping[str, Any]) -> tuple[Any, ...]:
        return (
            DatasetRepository._manifest_source_role_rank(manifest),
            str(manifest.get("created_at") or ""),
            str(manifest.get("manifest_ref") or ""),
        )

    @staticmethod
    def _columnar_manifest_base_criteria(*, dataset: str, market: str) -> dict[str, Any]:
        criteria: dict[str, Any] = {"storage": "parquet", "status": "active"}
        if dataset:
            criteria["dataset"] = dataset
        if market:
            criteria["market"] = market
        return criteria

    @classmethod
    def _columnar_manifest_query_criteria(
        cls,
        *,
        dataset: str,
        market: str,
        start: str | None,
        end: str | None,
    ) -> dict[str, Any]:
        criteria = cls._columnar_manifest_base_criteria(dataset=dataset, market=market)
        if end:
            criteria["period_start_min"] = {"$lte": end}
        if start:
            criteria["period_end_max"] = {"$gte": start}
        return criteria

    @staticmethod
    def _columnar_manifest_matches(
        manifest: Mapping[str, Any],
        *,
        dataset: str,
        market: str,
        symbol_id: str | None,
        universe_ref: str | None,
        start: str | None,
        end: str | None,
    ) -> bool:
        if manifest.get("storage") != "parquet" or manifest.get("status", "active") != "active":
            return False
        if str(manifest.get("dataset") or "") != dataset or str(manifest.get("market") or "") != market:
            return False
        symbol_ids = tuple(str(item) for item in tuple(manifest.get("symbol_ids", ()) or ()))
        if symbol_id and symbol_ids and symbol_id not in symbol_ids:
            return False
        universe_refs = tuple(str(item) for item in tuple(manifest.get("universe_refs", ()) or ()))
        if universe_ref and universe_refs and universe_ref not in universe_refs:
            return False
        manifest_start = _columnar_range_start_text(manifest.get("period_start_min"))
        manifest_end = _columnar_range_end_text(manifest.get("period_end_max"))
        if end and manifest_start and manifest_start > end:
            return False
        if start and manifest_end and manifest_end < start:
            return False
        return True

    def _coverage_summary_from_columnar(self, summary: Mapping[str, Any]) -> DatasetCoverageSummary:
        return DatasetCoverageSummary(
            record_count=self._optional_int(summary.get("record_count")) or 0,
            dataset_refs=tuple(str(ref) for ref in self._tuple_values(summary.get("dataset_refs")) if str(ref).strip()),
            ranges=self._range_values(summary.get("ranges")),
            starts=self._tuple_values(summary.get("starts")),
            ends=self._tuple_values(summary.get("ends")),
            field_sets=tuple(
                tuple(str(field) for field in self._tuple_values(item))
                for item in self._tuple_values(summary.get("field_sets"))
            ),
            source_role_sets=tuple(
                tuple(str(role) for role in self._tuple_values(item))
                for item in self._tuple_values(summary.get("source_role_sets"))
            ),
            freshest_as_of=self._optional_datetime(summary.get("freshest_as_of")),
            freshest_until=self._optional_datetime(summary.get("freshest_until")),
            checksum_counts=tuple(
                DatasetChecksumCoverage(
                    checksum=str(row.get("checksum") or ""),
                    actual_count=self._optional_int(row.get("actual_count")) or 0,
                    expected_min=self._optional_int(row.get("expected_min")),
                    expected_max=self._optional_int(row.get("expected_max")),
                    min_start=row.get("min_start"),
                    max_end=row.get("max_end"),
                )
                for row in self._tuple_values(summary.get("checksum_counts"))
                if isinstance(row, Mapping) and row.get("checksum")
            ),
        )

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
        fields: Sequence[str] = (),
    ) -> tuple[DatasetRecord, ...]:
        matched: list[DatasetRecord] = []
        if self._normalized_columnar is not None:
            manifests = self._normalized_columnar_manifests(
                dataset=dataset,
                market=market,
                symbol_id=symbol_id,
                universe_ref=universe_ref,
                date_range_start=date_range_start,
                date_range_end=date_range_end,
            )
            columnar_records = self._normalized_columnar.query_documents(
                dataset=dataset,
                market=market,
                symbol_id=symbol_id,
                universe_ref=universe_ref,
                date_range_start=date_range_start,
                date_range_end=date_range_end,
                require_integrity_metadata=require_integrity_metadata,
                include_row=include_row,
                fields=fields,
                manifests=manifests,
            )
            if columnar_records:
                return tuple(
                    self._dataset_record_from_document(record, dataset=dataset, market=market, fields=fields)
                    for record in columnar_records
                )
            if not self._allow_normalized_mongo_fallback:
                return ()
        if not self._allow_normalized_mongo_read:
            return ()
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
            matched.append(self._dataset_record_from_document(record, dataset=dataset, market=market, fields=fields))
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
        range_text: dict[str, Any] = {}
        start = _date_query_text(date_range_start)
        end = _date_query_text(date_range_end)
        if end:
            range_text["period_start"] = {"$lte": end}
        if start:
            range_text["period_end"] = {"$gte": start}
        if range_text:
            branches: list[dict[str, Any]] = [range_text]
            range_datetime: dict[str, Any] = {}
            end_datetime = _date_query_datetime_end(date_range_end)
            start_datetime = _date_query_datetime_start(date_range_start)
            if end_datetime is not None:
                range_datetime["period_start"] = {"$lte": end_datetime}
            if start_datetime is not None:
                range_datetime["period_end"] = {"$gte": start_datetime}
            if range_datetime:
                branches.append(range_datetime)
            criteria["$or"] = tuple(branches)
        if require_integrity_metadata:
            criteria["dataset_checksum"] = {"$exists": True, "$ne": None}
            criteria["dataset_checksum_algorithm"] = _DATASET_CHECKSUM_ALGORITHM
            criteria["dataset_checksum_scope"] = _DATASET_CHECKSUM_SCOPE
            criteria["dataset_row_count"] = {"$gt": 0}
        return criteria

    def _dataset_record_from_document(
        self,
        record: Mapping[str, Any],
        *,
        dataset: str,
        market: str,
        fields: Sequence[str] = (),
    ) -> DatasetRecord:
        row = record.get("row")
        row_payload = row if isinstance(row, Mapping) else {}
        requested_fields = tuple(dict.fromkeys(str(field).strip() for field in fields if str(field).strip()))
        if requested_fields:
            projected_fields = tuple(dict.fromkeys((*requested_fields, *STRUCTURAL_ROW_FIELDS)))
            row_payload = {field: row_payload[field] for field in projected_fields if field in row_payload}
        return DatasetRecord(
            dataset_ref=str(record.get("dataset_ref")),
            dataset=dataset,
            market=market,
            symbol_id=record.get("symbol_id"),
            universe_ref=record.get("universe_ref"),
            granularity=str(record.get("granularity", "")),
            period_start=record.get("period_start"),
            period_end=record.get("period_end"),
            field_set=tuple(record.get("field_set", ())),
            as_of=self._optional_datetime(record.get("as_of")),
            fresh_until=self._optional_datetime(record.get("fresh_until")),
            source_roles=tuple(record.get("source_roles", ())),
            dataset_checksum=record.get("dataset_checksum"),
            dataset_checksum_algorithm=record.get("dataset_checksum_algorithm"),
            dataset_checksum_scope=record.get("dataset_checksum_scope"),
            row=row_payload,
            dataset_row_count=self._optional_int(record.get("dataset_row_count")),
        )

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
        fields: Sequence[str] = (),
        order_by_symbol_id: bool = False,
    ) -> Iterable[DatasetRecord]:
        if self._normalized_columnar is not None:
            manifests = self._normalized_columnar_manifests(
                dataset=dataset,
                market=market,
                symbol_id=symbol_id,
                universe_ref=universe_ref,
                date_range_start=date_range_start,
                date_range_end=date_range_end,
            )
            columnar_records = self._normalized_columnar.iter_documents(
                dataset=dataset,
                market=market,
                symbol_id=symbol_id,
                universe_ref=universe_ref,
                date_range_start=date_range_start,
                date_range_end=date_range_end,
                require_integrity_metadata=require_integrity_metadata,
                include_row=include_row,
                fields=fields,
                order_by_symbol_id=order_by_symbol_id,
                manifests=manifests,
            )
            yielded = False
            for record in columnar_records:
                yielded = True
                yield self._dataset_record_from_document(record, dataset=dataset, market=market, fields=fields)
            if yielded:
                return
            if not self._allow_normalized_mongo_fallback:
                return
        if not self._allow_normalized_mongo_read:
            return
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
            if order_by_symbol_id:
                records = iter(
                    sorted(
                        records,
                        key=lambda record: (
                            str(record.get("symbol_id") or ""),
                            _date_query_text(record.get("period_start")) or "",
                            str(record.get("dataset_ref") or ""),
                        ),
                    )
                )
            for record in records:
                yield self._dataset_record_from_document(record, dataset=dataset, market=market, fields=fields)

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
        if self._normalized_columnar is not None:
            manifests = self._normalized_columnar_manifests(
                dataset=dataset,
                market=market,
                symbol_id=symbol_id,
                universe_ref=universe_ref,
                date_range_start=date_range_start,
                date_range_end=date_range_end,
            )
            columnar_summary = self._normalized_columnar.aggregate_coverage(
                dataset=dataset,
                market=market,
                symbol_id=symbol_id,
                universe_ref=universe_ref,
                date_range_start=date_range_start,
                date_range_end=date_range_end,
                require_integrity_metadata=require_integrity_metadata,
                manifests=manifests,
                sample_limit=sample_limit,
            )
            if columnar_summary is not None:
                return self._coverage_summary_from_columnar(columnar_summary)
            if not self._allow_normalized_mongo_fallback:
                return None
        if not self._allow_normalized_mongo_read:
            return None
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
        if self._normalized_columnar is not None:
            manifests = self._normalized_columnar_manifests(
                dataset=dataset,
                market=market,
                symbol_id=symbol_id,
                universe_ref=universe_ref,
                date_range_start=date_range_start,
                date_range_end=date_range_end,
            )
            count = self._normalized_columnar.count_documents(
                dataset=dataset,
                market=market,
                symbol_id=symbol_id,
                universe_ref=universe_ref,
                date_range_start=date_range_start,
                date_range_end=date_range_end,
                require_integrity_metadata=require_integrity_metadata,
                manifests=manifests,
            )
            if count:
                return count
            if not self._allow_normalized_mongo_fallback:
                return 0
        if not self._allow_normalized_mongo_read:
            return 0
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
        names = self._find_company_names_from_normalized_query(dataset=dataset, market=market, symbols=symbols)
        if len(names) == len(symbols):
            return names
        if not self._allow_normalized_mongo_read:
            return names
        criteria: dict[str, Any] = {
            "dataset": dataset,
            "market": market,
            "symbol_id": {"$in": tuple(symbol for symbol in symbols if symbol not in names)},
            "$or": tuple({f"row.{field}": {"$exists": True, "$ne": None}} for field in _COMPANY_NAME_ROW_FIELDS),
        }
        pipeline: tuple[Mapping[str, Any], ...] = (
            {"$match": criteria},
            {"$sort": {"period_end": -1, "period_start": -1}},
            {
                "$group": {
                    "_id": "$symbol_id",
                    "company_name": {
                        "$first": {
                            "$ifNull": [
                                "$row.company_name",
                                {
                                    "$ifNull": [
                                        "$row.name",
                                        {
                                            "$ifNull": [
                                                "$row.stock_name",
                                                {"$ifNull": ["$row.code_name", "$row.security_name"]},
                                            ]
                                        },
                                    ]
                                },
                            ]
                        }
                    },
                }
            },
        )
        with self._lock:
            rows = self._collection("normalized_datasets").aggregate(pipeline)
            if rows is None:
                records = self._collection("normalized_datasets").find(criteria, include_row=True)
            else:
                return {
                    str(row.get("_id")): text
                    for row in rows
                    if str(row.get("_id")) not in names
                    if (text := str(row.get("company_name") or "").strip())
                } | names

        sorted_records = sorted(
            records,
            key=lambda record: (
                _date_query_text(record.get("period_end")) or "",
                _date_query_text(record.get("period_start")) or "",
            ),
            reverse=True,
        )
        for record in sorted_records:
            symbol = str(record.get("symbol_id") or "").strip()
            if not symbol or symbol in names:
                continue
            row = record.get("row")
            if not isinstance(row, Mapping):
                continue
            company_name = _company_name_from_row(row)
            if company_name:
                names[symbol] = company_name
        return names

    def _find_company_names_from_normalized_query(
        self,
        *,
        dataset: str,
        market: str,
        symbols: Sequence[str],
    ) -> dict[str, str]:
        names = self._find_company_names_from_parquet_manifests(dataset=dataset, market=market, symbols=symbols)
        if len(names) == len(symbols):
            return names
        symbols = tuple(symbol for symbol in symbols if symbol not in names)
        if len(symbols) >= _COMPANY_NAME_BATCH_QUERY_MIN_SYMBOLS:
            return {**self._find_company_names_from_normalized_batch_query(dataset=dataset, market=market, symbols=symbols), **names}
        for symbol in symbols:
            records = self.query_normalized(
                dataset=dataset,
                market=market,
                symbol_id=symbol,
                universe_ref=None,
                require_integrity_metadata=True,
                include_row=True,
            )
            sorted_records = sorted(
                records,
                key=lambda record: (
                    _date_query_text(record.period_end) or "",
                    _date_query_text(record.period_start) or "",
                ),
                reverse=True,
            )
            for record in sorted_records:
                company_name = _company_name_from_row(record.row)
                if company_name:
                    names[symbol] = company_name
                    break
        return names

    def _find_company_names_from_parquet_manifests(
        self,
        *,
        dataset: str,
        market: str,
        symbols: Sequence[str],
    ) -> dict[str, str]:
        wanted_symbols = tuple(dict.fromkeys(str(symbol).strip() for symbol in symbols if str(symbol).strip()))
        wanted = set(wanted_symbols)
        if not wanted:
            return {}
        with self._lock:
            manifests = tuple(
                self._collection("dataset_manifests").find_all_backends(
                    {"storage": "parquet", "status": "active", "dataset": dataset, "market": market}
                )
            )
        paths: list[Path] = []
        for manifest in manifests:
            manifest_symbols = {
                str(symbol).strip()
                for symbol in tuple(manifest.get("symbol_ids", ()) or ())
                if str(symbol).strip()
            }
            if manifest_symbols and wanted.isdisjoint(manifest_symbols):
                continue
            paths.append(Path(str(manifest.get("path") or "")))
        paths = tuple(path for path in paths if path.is_file())
        if not paths:
            return {}

        import duckdb

        names: dict[str, str] = {}
        latest_seen: dict[str, str] = {}
        placeholders = ", ".join("?" for _symbol in wanted_symbols)
        query = (
            "select symbol_id, period_end, period_start, row_json "
            f"from read_parquet(?) where symbol_id in ({placeholders})"
        )
        with duckdb.connect(":memory:") as conn:
            for path in paths:
                for symbol, period_end, period_start, row_json in conn.execute(query, [str(path), *wanted_symbols]).fetchall():
                    symbol_text = str(symbol or "").strip()
                    try:
                        row = json.loads(str(row_json or "{}"))
                    except json.JSONDecodeError:
                        continue
                    company_name = _company_name_from_row(row)
                    if not company_name:
                        continue
                    period = _date_query_text(period_end) or _date_query_text(period_start) or ""
                    if symbol_text in names and latest_seen.get(symbol_text, "") >= period:
                        continue
                    names[symbol_text] = company_name
                    latest_seen[symbol_text] = period
        return names

    def _find_company_names_from_normalized_batch_query(
        self,
        *,
        dataset: str,
        market: str,
        symbols: Sequence[str],
    ) -> dict[str, str]:
        wanted = set(symbols)
        names: dict[str, str] = {}
        latest_seen: dict[str, str] = {}
        records = self.query_normalized(
            dataset=dataset,
            market=market,
            symbol_id=None,
            universe_ref=None,
            require_integrity_metadata=True,
            include_row=True,
        )
        for record in records:
            symbol = str(record.symbol_id or "").strip()
            if symbol not in wanted:
                continue
            company_name = _company_name_from_row(record.row)
            if not company_name:
                continue
            period = _date_query_text(record.period_end) or _date_query_text(record.period_start) or ""
            if symbol in names and latest_seen.get(symbol, "") >= period:
                continue
            names[symbol] = company_name
            latest_seen[symbol] = period
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

    def get_normalized_document_for_maintenance(self, dataset_ref: str) -> dict[str, Any] | None:
        with self._lock:
            return self._collection("normalized_datasets").get(dataset_ref)

    def count_normalized_documents_for_maintenance(self, criteria: Mapping[str, Any]) -> int:
        with self._lock:
            return self._collection("normalized_datasets").count(criteria)

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
        missing_refs = tuple(ref for ref, refs in found.items() if not refs)
        if missing_refs:
            prefix_map = _dataset_ref_prefix_map(missing_refs)
            if prefix_map:
                with self._lock:
                    prefix_attempts = self._collection("provider_attempts").find(
                        {"dataset_ref_prefixes": {"$in": tuple(prefix_map)}}
                    )
                for attempt in prefix_attempts:
                    attempt_ref = str(attempt.get("attempt_ref") or "").strip()
                    if not attempt_ref:
                        continue
                    prefixes = tuple(str(prefix).strip() for prefix in tuple(attempt.get("dataset_ref_prefixes", ()) or ()))
                    for prefix in prefixes:
                        for dataset_ref in prefix_map.get(prefix, ()):
                            if attempt_ref not in found[dataset_ref]:
                                found[dataset_ref].append(attempt_ref)
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

    def normalized_refs_have_readable_storage(self, dataset_refs: Sequence[str]) -> bool:
        refs = {str(ref).strip() for ref in dataset_refs if str(ref).strip()}
        if not refs:
            return True
        with self._lock:
            normalized = self._collection("normalized_datasets")
            rows_by_ref = {ref: normalized.get(ref) for ref in refs}
            manifests = tuple(self._collection("dataset_manifests").find({"storage": "parquet", "status": "active"}))
        for ref in refs:
            row = rows_by_ref.get(ref)
            if isinstance(row, Mapping):
                if self._normalized_row_ref_has_readable_storage(row, manifests):
                    continue
                return False
            if self._active_manifest_has_dataset_ref(ref, manifests):
                continue
            return False
        return True

    def _normalized_row_ref_has_readable_storage(
        self,
        row: Mapping[str, Any],
        manifests: Sequence[Mapping[str, Any]],
    ) -> bool:
        dataset = str(row.get("dataset") or "").strip()
        if dataset not in _COLUMNAR_ROW_REPLACEMENT_DATASETS or self._normalized_columnar is None:
            return True
        market = str(row.get("market") or "").strip()
        if not market:
            return False
        symbol_id = str(row.get("symbol_id") or "").strip() or None
        universe_ref = str(row.get("universe_ref") or "").strip() or None
        start = _columnar_range_start_text(row.get("period_start"))
        end = _columnar_range_end_text(row.get("period_end"))
        for manifest in manifests:
            if not self._columnar_manifest_matches(
                manifest,
                dataset=dataset,
                market=market,
                symbol_id=symbol_id,
                universe_ref=universe_ref,
                start=start,
                end=end,
            ):
                continue
            path = str(manifest.get("path") or "").strip()
            if path and Path(path).exists():
                return True
        return False

    @staticmethod
    def _active_manifest_has_dataset_ref(ref: str, manifests: Sequence[Mapping[str, Any]]) -> bool:
        for manifest in manifests:
            if manifest.get("storage") != "parquet" or manifest.get("status", "active") != "active":
                continue
            path = str(manifest.get("path") or "").strip()
            if not path or not Path(path).exists():
                continue
            if ref in DatasetRepository._manifest_dataset_refs(manifest):
                return True
        return False

    @staticmethod
    def _manifest_dataset_refs(manifest: Mapping[str, Any]) -> set[str]:
        return {
            str(ref).strip()
            for ref in (
                *tuple(manifest.get("dataset_refs", ()) or ()),
                *tuple(manifest.get("dataset_refs_sample", ()) or ()),
            )
            if str(ref).strip()
        }

    def delete_normalized_documents_for_maintenance(self, criteria: Mapping[str, Any]) -> int:
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

    def reserve_sliding_rate_limit(
        self,
        *,
        rate_limit_key: str,
        now: datetime,
        window_seconds: int,
        max_requests: int,
        safety_margin: int,
        cost: int,
    ) -> dict[str, Any]:
        now = self._aware_datetime(now)
        cutoff = now - timedelta(seconds=window_seconds)
        effective_limit = max(max_requests - safety_margin, 0)
        doc_id = f"{rate_limit_key}:sliding"
        with self._lock:
            collection = self._collection("provider_rate_limits")
            row = collection.get(doc_id)
            if row is None:
                row = {
                    "rate_limit_ref": doc_id,
                    "rate_key_hash": rate_limit_key,
                    "window_mode": "sliding",
                    "window_start": cutoff,
                    "window_seconds": window_seconds,
                    "max_requests": max_requests,
                    "used_requests": 0,
                    "safety_margin": safety_margin,
                    "cooldown_until": None,
                    "version": 1,
                    "request_timestamps": (),
                }

            timestamps = tuple(
                timestamp
                for timestamp in (
                    self._optional_datetime(raw)
                    for raw in row.get("request_timestamps", ())
                )
                if timestamp is not None
                and cutoff < self._aware_datetime(timestamp, reference=now) <= now
            )
            timestamps = tuple(self._aware_datetime(timestamp, reference=now) for timestamp in timestamps)
            allowed = len(timestamps) + cost <= effective_limit
            retry_after = None
            if allowed:
                timestamps = (*timestamps, *((now,) * cost))
            elif timestamps:
                retry_after = min(timestamps) + timedelta(seconds=window_seconds)
            else:
                retry_after = now + timedelta(seconds=window_seconds)

            row.update(
                {
                    "rate_limit_ref": doc_id,
                    "rate_key_hash": rate_limit_key,
                    "window_mode": "sliding",
                    "window_start": cutoff,
                    "window_seconds": window_seconds,
                    "max_requests": max_requests,
                    "used_requests": len(timestamps),
                    "safety_margin": safety_margin,
                    "effective_limit": effective_limit,
                    "request_timestamps": timestamps,
                    "retry_after": retry_after,
                    "updated_at": now,
                    "version": int(row.get("version", 0)) + 1,
                }
            )
            collection.set(doc_id, row)
            return {
                **self._normalize_rate_limit_row(row),
                "allowed": allowed,
                "retry_after": retry_after,
            }

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
        seed_database: Any | None,
        collections: Mapping[str, Any] | None,
    ) -> dict[str, _CollectionAdapter]:
        if collections is not None:
            backends = {name: collections[name] for name in ALLOWED_MONGO_COLLECTIONS}
        elif database is not None:
            backends = {name: self._resolve_database_collection(database, name) for name in ALLOWED_MONGO_COLLECTIONS}
        else:
            backends = {name: {} for name in ALLOWED_MONGO_COLLECTIONS}

        seed_backends: dict[str, tuple[Any, ...]] = {}
        if seed_database is not None:
            seed_backends = {
                name: (self._resolve_database_collection(seed_database, name),)
                for name in _SEED_READ_COLLECTIONS
            }

        return {
            name: _CollectionAdapter(
                name=name,
                key_field=_COLLECTION_KEY_FIELDS[name],
                backend=backends[name],
                read_backends=seed_backends.get(name, ()),
            )
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
        retry_after = normalized.get("retry_after")
        if isinstance(retry_after, datetime):
            reference = normalized.get("window_start")
            normalized["retry_after"] = cls._aware_datetime(
                retry_after,
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
    def _optional_datetime(value: Any) -> datetime | None:
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            try:
                return datetime.fromisoformat(text)
            except ValueError:
                return None
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
