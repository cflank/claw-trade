from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any, Mapping, Sequence

from claw_trade.data_gateway.models import (
    CoverageRequirement,
    DataGap,
    GapReason,
    GapSeverity,
    Market,
    WarehouseCheck,
    WarehouseResult,
)

from .freshness import FreshnessChecker
from .repository import DatasetRecord, DatasetRepository

_DEFAULT_MARKET = Market.CN_A


class Warehouse:
    def __init__(self, repository: DatasetRepository, freshness_checker: FreshnessChecker | None = None) -> None:
        self._repository = repository
        self._freshness_checker = freshness_checker or FreshnessChecker()

    def check(self, checks: Sequence[WarehouseCheck], coverage: CoverageRequirement | None = None) -> WarehouseResult:
        del coverage
        rows: list[dict[str, Any]] = []
        dataset_refs: list[str] = []
        gaps: list[DataGap] = []
        freshness: dict[str, Any] = {}

        for check in checks:
            records = self._repository.query_normalized(
                dataset=check.data_type,
                market=self._enum_value(check.market),
                symbol_id=check.symbol_id,
                universe_ref=check.universe_ref,
            )
            if not records:
                gaps.append(self._warehouse_missing_gap(check))
                continue
            records_for_check = self._records_for_check(records, check)

            verdict = self._freshness_checker.evaluate(request=check, records=records_for_check)
            rows.extend(self._materialize_rows(records_for_check))
            dataset_refs.extend(self._dataset_ref(record) for record in records_for_check)
            gaps.extend(self._normalize_gaps(check, verdict.gaps))

            if "policy" not in freshness:
                freshness["policy"] = check.freshness_policy
            freshness.setdefault("checked_requests", []).append(check.request_id)

        unique_rows = tuple(rows)
        dataset_refs_for_rows = tuple(dataset_refs)
        satisfied = not gaps and bool(dataset_refs_for_rows)
        return WarehouseResult(
            satisfied=satisfied,
            rows=unique_rows,
            dataset_refs=dataset_refs_for_rows,
            gaps=tuple(gaps),
            freshness=freshness,
        )

    def recheck(self, checks: Sequence[WarehouseCheck], coverage: CoverageRequirement | None = None) -> WarehouseResult:
        return self.check(checks, coverage)

    def query(self, request: Any) -> WarehouseResult:
        timezone = self._value(request, "timezone")
        calendar = self._value(request, "calendar")
        if not self._non_empty_text(timezone) or not self._non_empty_text(calendar):
            gap = DataGap.invalid_request(
                request_id=str(self._value(request, "request_id", "warehouse-query")),
                message="missing required market timezone/calendar",
                market=self._to_market(self._value(request, "market", _DEFAULT_MARKET.value)),
            )
            return WarehouseResult(satisfied=False, rows=(), dataset_refs=(), gaps=(gap,), freshness={})
        check = WarehouseCheck(
            request_id=self._value(request, "request_id", "warehouse-query"),
            market=self._to_market(self._value(request, "market", _DEFAULT_MARKET.value)),
            symbol_id=self._value(request, "symbol_id"),
            universe_ref=self._value(request, "universe_ref"),
            data_type=self._value(request, "dataset", self._value(request, "data_type", "unknown")),
            granularity=self._value(request, "granularity", "unknown"),
            fields=tuple(self._value(request, "fields", ()) or ()),
            date_range_start=self._value(request, "date_range_start"),
            date_range_end=self._value(request, "date_range_end"),
            freshness_policy=self._value(request, "freshness_policy", "trading_day"),
            timezone=timezone,
            calendar=calendar,
            source_role_required=self._value(request, "source_role_required"),
            as_of=self._value(request, "as_of"),
        )
        return self.check((check,), None)

    @staticmethod
    def _materialize_rows(records: Sequence[DatasetRecord]) -> tuple[dict[str, Any], ...]:
        return tuple(dict(record.row) for record in records)

    @staticmethod
    def _dataset_ref(record: DatasetRecord) -> str:
        return record.dataset_ref

    def _records_for_check(self, records: Sequence[DatasetRecord], check: WarehouseCheck) -> tuple[DatasetRecord, ...]:
        granularity_matches = tuple(record for record in records if record.granularity == check.granularity)
        scoped = granularity_matches if granularity_matches else tuple(records)
        if not granularity_matches:
            return scoped
        overlap_matches = tuple(record for record in scoped if self._record_overlaps_request(record, check))
        return overlap_matches or scoped

    def _record_overlaps_request(self, record: DatasetRecord, check: WarehouseCheck) -> bool:
        request_start = self._to_date(check.date_range_start)
        request_end = self._to_date(check.date_range_end)
        if request_start is None and request_end is None:
            return True
        record_start = self._to_date(record.period_start)
        record_end = self._to_date(record.period_end)
        if record_start is None or record_end is None:
            return True
        if request_start is not None and record_end < request_start:
            return False
        if request_end is not None and record_start > request_end:
            return False
        return True

    @staticmethod
    def _value(obj: Any, key: str, default: Any = None) -> Any:
        if isinstance(obj, Mapping):
            return obj.get(key, default)
        return getattr(obj, key, default)

    @staticmethod
    def _enum_value(value: Any) -> str:
        enum_value = getattr(value, "value", None)
        if isinstance(enum_value, str):
            return enum_value
        return str(value)

    @staticmethod
    def _non_empty_text(value: Any) -> bool:
        return value is not None and bool(str(value).strip())

    @staticmethod
    def _to_date(value: Any) -> date | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            try:
                return date.fromisoformat(text[:10])
            except ValueError:
                return None
        return None

    def _to_market(self, value: Any) -> Market:
        if isinstance(value, Market):
            return value
        try:
            return Market(str(value))
        except ValueError:
            return _DEFAULT_MARKET

    def _warehouse_missing_gap(self, check: WarehouseCheck) -> DataGap:
        return DataGap(
            gap_id=f"gap:{check.request_id}:warehouse_missing",
            request_id=check.request_id,
            severity=GapSeverity.BLOCKER,
            reason=GapReason.WAREHOUSE_MISSING,
            market=check.market,
            symbol_id=check.symbol_id,
            data_type=check.data_type,
            granularity=check.granularity,
            required_fields=check.fields,
            human_readable="warehouse_missing",
            as_of=datetime.now(tz=UTC),
        )

    def _normalize_gaps(self, check: WarehouseCheck, raw_gaps: Sequence[dict[str, Any]]) -> tuple[DataGap, ...]:
        normalized: list[DataGap] = []
        for idx, gap in enumerate(raw_gaps):
            reason_raw = self._value(gap, "reason", GapReason.WAREHOUSE_MISSING.value)
            reason = self._to_gap_reason(reason_raw)
            details = self._value(gap, "details", {}) or {}
            if not isinstance(details, Mapping):
                details = {}
            missing_fields = self._to_required_fields(reason, details, check.fields)
            normalized.append(
                DataGap(
                    gap_id=f"gap:{check.request_id}:{reason.value}:{idx}",
                    request_id=check.request_id,
                    severity=GapSeverity.BLOCKER,
                    reason=reason,
                    market=check.market,
                    symbol_id=check.symbol_id,
                    data_type=check.data_type,
                    granularity=check.granularity,
                    required_fields=missing_fields,
                    human_readable=reason.value,
                    as_of=datetime.now(tz=UTC),
                )
            )
        return tuple(normalized)

    @staticmethod
    def _to_required_fields(reason: GapReason, details: Mapping[str, Any], default_fields: tuple[str, ...]) -> tuple[str, ...]:
        if reason != GapReason.FIELD_MISSING:
            return ()
        missing = details.get("missing_fields")
        if isinstance(missing, tuple):
            return tuple(str(field) for field in missing)
        if isinstance(missing, list):
            return tuple(str(field) for field in missing)
        return tuple(default_fields)

    @staticmethod
    def _to_gap_reason(reason: Any) -> GapReason:
        try:
            return GapReason(str(reason))
        except ValueError:
            return GapReason.WAREHOUSE_MISSING
