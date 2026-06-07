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
from .repository import DatasetCoverageSummary, DatasetRecord, DatasetRepository

_DEFAULT_MARKET = Market.CN_A
_METADATA_REF_SAMPLE_LIMIT = 50


class Warehouse:
    def __init__(self, repository: DatasetRepository, freshness_checker: FreshnessChecker | None = None) -> None:
        self._repository = repository
        self._freshness_checker = freshness_checker or FreshnessChecker()

    def check(self, checks: Sequence[WarehouseCheck], coverage: CoverageRequirement | None = None) -> WarehouseResult:
        return self._check(checks, coverage, include_rows=True)

    def check_coverage(self, checks: Sequence[WarehouseCheck], coverage: CoverageRequirement | None = None) -> WarehouseResult:
        return self._check(checks, coverage, include_rows=False)

    def resolve_company_names(
        self,
        *,
        market: Market | str,
        symbol_ids: Sequence[str],
        dataset: str = "daily_bar",
    ) -> Mapping[str, str]:
        return self._repository.find_company_names_by_symbol_ids(
            dataset=dataset,
            market=self._enum_value(market),
            symbol_ids=symbol_ids,
        )

    def _check(
        self,
        checks: Sequence[WarehouseCheck],
        coverage: CoverageRequirement | None = None,
        *,
        include_rows: bool,
    ) -> WarehouseResult:
        del coverage
        rows: list[dict[str, Any]] = []
        dataset_refs: list[str] = []
        gaps: list[DataGap] = []
        freshness: dict[str, Any] = {}
        coverage_by_request: list[dict[str, Any]] = []

        for check in checks:
            if not include_rows and self._is_universe_batch_check(check):
                streamed = self._stream_universe_coverage_check(check)
                rows.extend(streamed.rows)
                dataset_refs.extend(streamed.dataset_refs)
                gaps.extend(streamed.gaps)
                if streamed.freshness:
                    if "policy" not in freshness and streamed.freshness.get("policy"):
                        freshness["policy"] = streamed.freshness.get("policy")
                    freshness.setdefault("checked_requests", []).extend(
                        tuple(streamed.freshness.get("checked_requests", ()) or ())
                    )
                    coverage_by_request.extend(tuple(streamed.freshness.get("coverage_by_request", ()) or ()))
                continue
            records = self._repository.query_normalized(
                dataset=check.data_type,
                market=self._enum_value(check.market),
                symbol_id=check.symbol_id,
                universe_ref=check.universe_ref,
                date_range_start=check.date_range_start,
                date_range_end=check.date_range_end,
                require_integrity_metadata=True,
                include_row=include_rows,
                fields=check.fields if include_rows else (),
            )
            if not records:
                records = self._repository.query_normalized(
                    dataset=check.data_type,
                    market=self._enum_value(check.market),
                    symbol_id=check.symbol_id,
                    universe_ref=check.universe_ref,
                    date_range_start=check.date_range_start,
                    date_range_end=check.date_range_end,
                    include_row=include_rows,
                    fields=check.fields if include_rows else (),
                )
            if not records:
                gaps.append(self._warehouse_missing_gap(check))
                coverage_by_request.append(self._coverage_summary(check, (), ()))
                if "policy" not in freshness:
                    freshness["policy"] = check.freshness_policy
                freshness.setdefault("checked_requests", []).append(check.request_id)
                continue
            records_for_check = self._records_for_check(records, check)
            valid_records, integrity_gaps = self._records_passing_integrity_check(
                check,
                records_for_check,
                include_rows=include_rows,
            )
            gaps.extend(integrity_gaps)
            records_for_check = valid_records
            if not records_for_check:
                coverage_by_request.append(self._coverage_summary(check, (), ()))
                if "policy" not in freshness:
                    freshness["policy"] = check.freshness_policy
                freshness.setdefault("checked_requests", []).append(check.request_id)
                continue
            batch_count_gaps = self._batch_count_integrity_gaps(check, records_for_check)
            if batch_count_gaps:
                gaps.extend(batch_count_gaps)
                coverage_by_request.append(
                    self._coverage_summary(
                        check,
                        records_for_check,
                        (),
                        dataset_ref_limit=None if include_rows else _METADATA_REF_SAMPLE_LIMIT,
                    )
                )
                if "policy" not in freshness:
                    freshness["policy"] = check.freshness_policy
                freshness.setdefault("checked_requests", []).append(check.request_id)
                continue

            verdict = self._freshness_checker.evaluate(request=check, records=records_for_check)
            if include_rows:
                rows.extend(self._materialize_rows(records_for_check))
            record_refs = tuple(self._dataset_ref(record) for record in records_for_check)
            if include_rows:
                dataset_refs.extend(record_refs)
            else:
                dataset_refs.extend(record_refs[:_METADATA_REF_SAMPLE_LIMIT])
            gaps.extend(self._normalize_gaps(check, verdict.gaps))
            coverage_by_request.append(
                self._coverage_summary(
                    check,
                    records_for_check,
                    verdict.gaps,
                    dataset_ref_limit=None if include_rows else _METADATA_REF_SAMPLE_LIMIT,
                )
            )

            if "policy" not in freshness:
                freshness["policy"] = check.freshness_policy
            freshness.setdefault("checked_requests", []).append(check.request_id)
        if coverage_by_request:
            freshness["coverage_by_request"] = tuple(coverage_by_request)

        unique_rows = tuple(rows)
        dataset_refs_for_rows = tuple(dataset_refs)
        attempt_refs_by_dataset_ref = (
            self._repository.find_provider_attempt_refs_by_dataset_ref(dataset_refs_for_rows)
            if dataset_refs_for_rows
            else {}
        )
        attempt_refs = tuple(
            dict.fromkeys(
                attempt_ref
                for dataset_ref in dataset_refs_for_rows
                for attempt_ref in attempt_refs_by_dataset_ref.get(dataset_ref, ())
            )
        )
        satisfied = not gaps and bool(dataset_refs_for_rows)
        return WarehouseResult(
            satisfied=satisfied,
            rows=unique_rows,
            dataset_refs=dataset_refs_for_rows,
            attempt_refs=attempt_refs,
            attempt_refs_by_dataset_ref=attempt_refs_by_dataset_ref,
            gaps=tuple(gaps),
            freshness=freshness,
        )

    def _stream_universe_coverage_check(self, check: WarehouseCheck) -> WarehouseResult:
        aggregated = self._repository.aggregate_normalized_coverage(
            dataset=check.data_type,
            market=self._enum_value(check.market),
            symbol_id=check.symbol_id,
            universe_ref=check.universe_ref,
            date_range_start=check.date_range_start,
            date_range_end=check.date_range_end,
            require_integrity_metadata=True,
            sample_limit=_METADATA_REF_SAMPLE_LIMIT,
        )
        if aggregated is not None:
            return self._aggregated_universe_coverage_check(check, aggregated)

        records_seen = 0
        valid_records_seen = 0
        dataset_refs: list[str] = []
        checksum_counts: dict[str, int] = {}
        checksum_expected: dict[str, int | None] = {}
        checksum_ranges: dict[str, set[tuple[date, date]]] = {}
        ranges: set[tuple[date, date]] = set()
        starts: set[date] = set()
        ends: set[date] = set()
        field_set: set[str] = set()
        source_roles: set[str] = set()
        freshest_as_of: datetime | None = None
        freshest_until: datetime | None = None

        for record in self._repository.iter_normalized(
            dataset=check.data_type,
            market=self._enum_value(check.market),
            symbol_id=check.symbol_id,
            universe_ref=check.universe_ref,
            date_range_start=check.date_range_start,
            date_range_end=check.date_range_end,
            require_integrity_metadata=True,
            include_row=False,
        ):
            records_seen += 1
            if record.granularity != check.granularity:
                continue
            if not self._record_overlaps_request(record, check):
                continue
            valid_records_seen += 1
            if len(dataset_refs) < _METADATA_REF_SAMPLE_LIMIT:
                dataset_refs.append(self._dataset_ref(record))
            start = self._to_date(record.period_start)
            end = self._to_date(record.period_end)
            if start is not None:
                starts.add(start)
            if end is not None:
                ends.add(end)
            if start is not None and end is not None:
                ranges.add((start, end))
            field_set.update(record.field_set)
            source_roles.update(record.source_roles)
            if record.as_of is not None:
                as_of = self._aware_datetime(record.as_of)
                if freshest_as_of is None or as_of > freshest_as_of:
                    freshest_as_of = as_of
            if record.fresh_until is not None:
                fresh_until = self._aware_datetime(record.fresh_until)
                if freshest_until is None or fresh_until > freshest_until:
                    freshest_until = fresh_until
            checksum = str(record.dataset_checksum or "").strip()
            if checksum:
                checksum_counts[checksum] = checksum_counts.get(checksum, 0) + 1
                checksum_expected.setdefault(checksum, record.dataset_row_count)
                if start is not None and end is not None:
                    checksum_ranges.setdefault(checksum, set()).add((start, end))

        if records_seen == 0:
            legacy_count = self._stream_legacy_universe_record_count(check)
            gap = (
                self._data_integrity_gap(check, self._empty_stream_record(check), invalid_count=legacy_count)
                if legacy_count
                else self._warehouse_missing_gap(check)
            )
            return WarehouseResult(
                satisfied=False,
                rows=(),
                dataset_refs=(),
                gaps=(gap,),
                freshness={
                    "policy": check.freshness_policy,
                    "checked_requests": [check.request_id],
                    "coverage_by_request": (self._stream_coverage_summary(check, record_count=0, dataset_refs=(), starts=(), ends=(), raw_gaps=()),),
                },
            )

        synthetic_records = tuple(
            DatasetRecord(
                dataset_ref=f"streamed:{check.request_id}:{range_start.isoformat()}:{range_end.isoformat()}",
                dataset=check.data_type,
                market=self._enum_value(check.market),
                symbol_id=None,
                universe_ref=check.universe_ref,
                granularity=check.granularity,
                period_start=range_start,
                period_end=range_end,
                field_set=tuple(sorted(field_set)),
                as_of=freshest_as_of,
                fresh_until=freshest_until,
                source_roles=tuple(sorted(source_roles)),
                dataset_checksum="streamed",
                dataset_checksum_algorithm="sha256:canonical-json-v1",
                dataset_checksum_scope="normalized-batch-v1",
                row={},
                dataset_row_count=valid_records_seen,
            )
            for range_start, range_end in sorted(ranges)
        )
        gaps: list[DataGap] = []
        integrity_mismatch_ranges: list[tuple[date, date]] = []
        for checksum, actual_count in checksum_counts.items():
            expected_count = checksum_expected.get(checksum)
            if expected_count is None or expected_count <= 0 or actual_count != expected_count:
                integrity_mismatch_ranges.extend(sorted(checksum_ranges.get(checksum, ())))
                gaps.append(
                    self._dataset_batch_integrity_gap(
                        check,
                        self._stream_checksum_record(check, checksum=checksum, expected_count=expected_count),
                        actual_count=actual_count,
                        expected_count=expected_count,
                    )
                )
        verdict = self._freshness_checker.evaluate(request=check, records=synthetic_records)
        gaps.extend(self._normalize_gaps(check, verdict.gaps))
        result_dataset_refs = () if any(gap.reason == GapReason.DATA_INTEGRITY_FAILED for gap in gaps) else tuple(dataset_refs)
        attempt_refs_by_dataset_ref = self._metadata_attempt_refs_by_dataset_ref(result_dataset_refs)
        attempt_refs = self._flatten_attempt_refs(attempt_refs_by_dataset_ref)
        return WarehouseResult(
            satisfied=not gaps and bool(result_dataset_refs),
            rows=(),
            dataset_refs=result_dataset_refs,
            attempt_refs=attempt_refs,
            attempt_refs_by_dataset_ref=attempt_refs_by_dataset_ref,
            gaps=tuple(gaps),
            freshness={
                "policy": check.freshness_policy,
                "checked_requests": [check.request_id],
                "coverage_by_request": (
                    self._stream_coverage_summary(
                        check,
                        record_count=valid_records_seen,
                        dataset_refs=tuple(dataset_refs),
                        starts=tuple(starts),
                        ends=tuple(ends),
                        raw_gaps=verdict.gaps,
                        integrity_mismatch_ranges=tuple(integrity_mismatch_ranges),
                    ),
                ),
            },
        )

    def _aggregated_universe_coverage_check(
        self,
        check: WarehouseCheck,
        summary: DatasetCoverageSummary,
    ) -> WarehouseResult:
        starts = tuple(
            parsed
            for parsed in (self._to_date(value) for value in summary.starts)
            if parsed is not None
        )
        ends = tuple(
            parsed
            for parsed in (self._to_date(value) for value in summary.ends)
            if parsed is not None
        )
        ranges = tuple(
            (start, end)
            for start, end in (
                (self._to_date(raw_start), self._to_date(raw_end))
                for raw_start, raw_end in summary.ranges
            )
            if start is not None and end is not None
        )
        field_set = tuple(
            sorted(
                {
                    str(field)
                    for fields in summary.field_sets
                    for field in fields
                    if str(field).strip()
                }
            )
        )
        source_roles = tuple(
            sorted(
                {
                    str(role)
                    for roles in summary.source_role_sets
                    for role in roles
                    if str(role).strip()
                }
            )
        )
        if summary.record_count == 0:
            legacy_count = self._metadata_record_count(check, require_integrity_metadata=False)
            gap = (
                self._data_integrity_gap(check, self._empty_stream_record(check), invalid_count=legacy_count)
                if legacy_count
                else self._warehouse_missing_gap(check)
            )
            return WarehouseResult(
                satisfied=False,
                rows=(),
                dataset_refs=(),
                gaps=(gap,),
                freshness={
                    "policy": check.freshness_policy,
                    "checked_requests": [check.request_id],
                    "coverage_by_request": (
                        self._stream_coverage_summary(
                            check,
                            record_count=0,
                            dataset_refs=(),
                            starts=(),
                            ends=(),
                            raw_gaps=(),
                            read_mode="aggregate_metadata",
                        ),
                    ),
                },
            )

        synthetic_records = tuple(
            DatasetRecord(
                dataset_ref=f"aggregated:{check.request_id}:{range_start.isoformat()}:{range_end.isoformat()}",
                dataset=check.data_type,
                market=self._enum_value(check.market),
                symbol_id=None,
                universe_ref=check.universe_ref,
                granularity=check.granularity,
                period_start=range_start,
                period_end=range_end,
                field_set=field_set,
                as_of=summary.freshest_as_of,
                fresh_until=summary.freshest_until,
                source_roles=source_roles,
                dataset_checksum="aggregated",
                dataset_checksum_algorithm="sha256:canonical-json-v1",
                dataset_checksum_scope="normalized-batch-v1",
                row={},
                dataset_row_count=summary.record_count,
            )
            for range_start, range_end in sorted(ranges)
        )
        gaps: list[DataGap] = []
        integrity_mismatch_ranges: list[tuple[date, date]] = []
        for checksum_summary in summary.checksum_counts:
            expected_count = checksum_summary.expected_min
            if (
                expected_count is None
                or expected_count <= 0
                or checksum_summary.expected_max != expected_count
                or checksum_summary.actual_count != expected_count
            ):
                mismatch_start = self._to_date(checksum_summary.min_start)
                mismatch_end = self._to_date(checksum_summary.max_end)
                if mismatch_start is not None and mismatch_end is not None:
                    integrity_mismatch_ranges.append((mismatch_start, mismatch_end))
                gaps.append(
                    self._dataset_batch_integrity_gap(
                        check,
                        self._stream_checksum_record(
                            check,
                            checksum=checksum_summary.checksum,
                            expected_count=expected_count,
                        ),
                        actual_count=checksum_summary.actual_count,
                        expected_count=expected_count,
                    )
                )
        verdict = self._freshness_checker.evaluate(request=check, records=synthetic_records)
        gaps.extend(self._normalize_gaps(check, verdict.gaps))
        result_dataset_refs = (
            ()
            if any(gap.reason == GapReason.DATA_INTEGRITY_FAILED for gap in gaps)
            else tuple(summary.dataset_refs)
        )
        attempt_refs_by_dataset_ref = self._metadata_attempt_refs_by_dataset_ref(result_dataset_refs)
        attempt_refs = self._flatten_attempt_refs(attempt_refs_by_dataset_ref)
        return WarehouseResult(
            satisfied=not gaps and bool(result_dataset_refs),
            rows=(),
            dataset_refs=result_dataset_refs,
            attempt_refs=attempt_refs,
            attempt_refs_by_dataset_ref=attempt_refs_by_dataset_ref,
            gaps=tuple(gaps),
            freshness={
                "policy": check.freshness_policy,
                "checked_requests": [check.request_id],
                "coverage_by_request": (
                    self._stream_coverage_summary(
                        check,
                        record_count=summary.record_count,
                        dataset_refs=tuple(summary.dataset_refs),
                        starts=starts,
                        ends=ends,
                        raw_gaps=verdict.gaps,
                        read_mode="aggregate_metadata",
                        integrity_mismatch_ranges=tuple(integrity_mismatch_ranges),
                    ),
                ),
            },
        )

    def _stream_legacy_universe_record_count(self, check: WarehouseCheck) -> int:
        return self._metadata_record_count(check, require_integrity_metadata=False)

    def _metadata_record_count(self, check: WarehouseCheck, *, require_integrity_metadata: bool) -> int:
        return self._repository.count_normalized(
            dataset=check.data_type,
            market=self._enum_value(check.market),
            symbol_id=check.symbol_id,
            universe_ref=check.universe_ref,
            date_range_start=check.date_range_start,
            date_range_end=check.date_range_end,
            require_integrity_metadata=require_integrity_metadata,
        )

    def _metadata_attempt_refs_by_dataset_ref(self, dataset_refs: Sequence[str]) -> dict[str, tuple[str, ...]]:
        refs = tuple(dict.fromkeys(str(ref) for ref in dataset_refs if str(ref).strip()))
        if not refs:
            return {}
        return self._repository.find_provider_attempt_refs_by_dataset_ref(refs)

    @staticmethod
    def _flatten_attempt_refs(refs_by_dataset_ref: Mapping[str, Sequence[str]]) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                str(attempt_ref)
                for refs in refs_by_dataset_ref.values()
                for attempt_ref in refs
                if str(attempt_ref).strip()
            )
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
        rows: list[dict[str, Any]] = []
        for record in records:
            row = dict(record.row)
            row.setdefault("dataset", record.dataset)
            row.setdefault("market", record.market)
            if record.symbol_id:
                row.setdefault("symbol_id", record.symbol_id)
            if record.universe_ref:
                row.setdefault("universe_ref", record.universe_ref)
            row.setdefault("granularity", record.granularity)
            if record.period_start is not None:
                row.setdefault("period_start", record.period_start)
                if record.dataset == "daily_bar":
                    row.setdefault("date", record.period_start)
            if record.period_end is not None:
                row.setdefault("period_end", record.period_end)
            rows.append(row)
        return tuple(rows)

    @staticmethod
    def _dataset_ref(record: DatasetRecord) -> str:
        return record.dataset_ref

    def _coverage_summary(
        self,
        check: WarehouseCheck,
        records: Sequence[DatasetRecord],
        raw_gaps: Sequence[dict[str, Any]],
        *,
        dataset_ref_limit: int | None = _METADATA_REF_SAMPLE_LIMIT,
    ) -> dict[str, Any]:
        starts = tuple(self._to_date(record.period_start) for record in records if record.period_start)
        ends = tuple(self._to_date(record.period_end) for record in records if record.period_end)
        ref_records = records if dataset_ref_limit is None else records[:dataset_ref_limit]
        date_gap_details: Mapping[str, Any] = {}
        for gap in raw_gaps:
            if self._value(gap, "reason") != GapReason.DATE_RANGE_MISSING.value:
                continue
            details = self._value(gap, "details", {}) or {}
            if isinstance(details, Mapping):
                date_gap_details = details
            break
        return {
            "request_id": check.request_id,
            "data_type": check.data_type,
            "granularity": check.granularity,
            "market": self._enum_value(check.market),
            "symbol_id": check.symbol_id,
            "universe_ref": check.universe_ref,
            "record_count": len(records),
            "dataset_refs": tuple(self._dataset_ref(record) for record in ref_records),
            "dataset_ref_count": len(records),
            "actual_start": date_gap_details.get("actual_start")
            or (min(starts).isoformat() if starts else None),
            "actual_end": date_gap_details.get("actual_end") or (max(ends).isoformat() if ends else None),
            "expected_start": date_gap_details.get("expected_start") or self._date_text(check.date_range_start),
            "expected_end": date_gap_details.get("expected_end") or self._date_text(check.date_range_end),
            "missing_ranges": tuple(date_gap_details.get("missing_ranges") or ()),
        }

    def _records_for_check(self, records: Sequence[DatasetRecord], check: WarehouseCheck) -> tuple[DatasetRecord, ...]:
        granularity_matches = tuple(record for record in records if record.granularity == check.granularity)
        scoped = granularity_matches if granularity_matches else tuple(records)
        if not granularity_matches:
            return scoped
        field_matches = self._records_with_requested_fields(scoped, check)
        if field_matches:
            scoped = field_matches
        overlap_matches = tuple(record for record in scoped if self._record_overlaps_request(record, check))
        return overlap_matches or scoped

    @staticmethod
    def _records_with_requested_fields(records: Sequence[DatasetRecord], check: WarehouseCheck) -> tuple[DatasetRecord, ...]:
        requested = tuple(str(field).strip() for field in check.fields if str(field).strip())
        if not requested:
            return tuple(records)
        return tuple(
            record
            for record in records
            if all(field in record.field_set or field in record.row for field in requested)
        )

    def _records_passing_integrity_check(
        self,
        check: WarehouseCheck,
        records: Sequence[DatasetRecord],
        *,
        include_rows: bool,
    ) -> tuple[tuple[DatasetRecord, ...], tuple[DataGap, ...]]:
        valid: list[DatasetRecord] = []
        invalid: list[DatasetRecord] = []
        for record in records:
            if self._record_integrity_ok(record, include_rows=include_rows):
                valid.append(record)
                continue
            invalid.append(record)
        if not invalid:
            return tuple(valid), ()
        if self._is_universe_batch_check(check) and valid:
            return tuple(valid), ()
        if self._is_universe_batch_check(check):
            return tuple(valid), (self._data_integrity_gap(check, invalid[0], invalid_count=len(invalid)),)
        return tuple(valid), tuple(self._data_integrity_gap(check, record) for record in invalid)

    @staticmethod
    def _is_universe_batch_check(check: WarehouseCheck) -> bool:
        return bool(check.universe_ref) and not check.symbol_id

    def _record_integrity_ok(self, record: DatasetRecord, *, include_rows: bool) -> bool:
        if not self._record_has_dataset_checksum(record):
            return False
        if include_rows and not record.row:
            return False
        return True

    @staticmethod
    def _record_has_dataset_checksum(record: DatasetRecord) -> bool:
        if not record.dataset_checksum:
            return False
        if record.dataset_checksum_algorithm not in {"sha256:canonical-json-v1"}:
            return False
        if record.dataset_checksum_scope not in {"normalized-batch-v1"}:
            return False
        if record.dataset_row_count is None or record.dataset_row_count <= 0:
            return False
        return True

    def _batch_count_integrity_gaps(
        self,
        check: WarehouseCheck,
        records: Sequence[DatasetRecord],
    ) -> tuple[DataGap, ...]:
        if check.symbol_id:
            return ()
        if not check.universe_ref:
            return ()
        grouped: dict[str, list[DatasetRecord]] = {}
        for record in records:
            checksum = str(record.dataset_checksum or "").strip()
            if not checksum:
                continue
            grouped.setdefault(checksum, []).append(record)
        gaps: list[DataGap] = []
        for checksum, group in grouped.items():
            expected_count = group[0].dataset_row_count
            if expected_count is None or expected_count <= 0:
                gaps.append(
                    self._dataset_batch_integrity_gap(
                        check,
                        group[0],
                        actual_count=len(group),
                        expected_count=expected_count,
                    )
                )
                continue
            if len(group) != expected_count:
                gaps.append(
                    self._dataset_batch_integrity_gap(
                        check,
                        group[0],
                        actual_count=len(group),
                        expected_count=expected_count,
                    )
                )
        return tuple(gaps)

    def _dataset_batch_integrity_gap(
        self,
        check: WarehouseCheck,
        record: DatasetRecord,
        *,
        actual_count: int,
        expected_count: int | None,
    ) -> DataGap:
        checksum = str(record.dataset_checksum or record.dataset_ref)
        return DataGap(
            gap_id=f"gap:{check.request_id}:data_integrity_failed:{checksum}",
            request_id=check.request_id,
            severity=GapSeverity.BLOCKER,
            reason=GapReason.DATA_INTEGRITY_FAILED,
            market=check.market,
            symbol_id=check.symbol_id,
            data_type=check.data_type,
            granularity=check.granularity,
            required_fields=(),
            evidence_refs=(checksum,),
            human_readable=(
                "local dataset batch count mismatch: "
                f"expected={expected_count}, actual={actual_count}"
            ),
            as_of=datetime.now(tz=UTC),
        )

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

    @classmethod
    def _date_text(cls, value: Any) -> str | None:
        parsed = cls._to_date(value)
        return parsed.isoformat() if parsed is not None else None

    @staticmethod
    def _aware_datetime(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def _stream_coverage_summary(
        self,
        check: WarehouseCheck,
        *,
        record_count: int,
        dataset_refs: tuple[str, ...],
        starts: Sequence[date],
        ends: Sequence[date],
        raw_gaps: Sequence[dict[str, Any]],
        read_mode: str = "stream_metadata",
        integrity_mismatch_ranges: Sequence[tuple[date, date]] = (),
    ) -> dict[str, Any]:
        date_gap_details: Mapping[str, Any] = {}
        for gap in raw_gaps:
            if self._value(gap, "reason") != GapReason.DATE_RANGE_MISSING.value:
                continue
            details = self._value(gap, "details", {}) or {}
            if isinstance(details, Mapping):
                date_gap_details = details
            break
        return {
            "request_id": check.request_id,
            "data_type": check.data_type,
            "granularity": check.granularity,
            "market": self._enum_value(check.market),
            "symbol_id": check.symbol_id,
            "universe_ref": check.universe_ref,
            "record_count": record_count,
            "dataset_refs": dataset_refs[:_METADATA_REF_SAMPLE_LIMIT],
            "dataset_ref_count": record_count,
            "actual_start": date_gap_details.get("actual_start")
            or (min(starts).isoformat() if starts else None),
            "actual_end": date_gap_details.get("actual_end") or (max(ends).isoformat() if ends else None),
            "expected_start": date_gap_details.get("expected_start") or self._date_text(check.date_range_start),
            "expected_end": date_gap_details.get("expected_end") or self._date_text(check.date_range_end),
            "missing_ranges": tuple(date_gap_details.get("missing_ranges") or ()),
            "integrity_mismatch_ranges": tuple(
                {"start": start.isoformat(), "end": end.isoformat()}
                for start, end in integrity_mismatch_ranges
            ),
            "read_mode": read_mode,
        }

    def _empty_stream_record(self, check: WarehouseCheck) -> DatasetRecord:
        return DatasetRecord(
            dataset_ref=f"streamed:{check.request_id}:metadata-missing",
            dataset=check.data_type,
            market=self._enum_value(check.market),
            symbol_id=check.symbol_id,
            universe_ref=check.universe_ref,
            granularity=check.granularity,
            period_start=None,
            period_end=None,
            field_set=(),
            as_of=None,
            fresh_until=None,
            source_roles=(),
            dataset_checksum=None,
            dataset_checksum_algorithm=None,
            dataset_checksum_scope=None,
            row={},
            dataset_row_count=None,
        )

    def _stream_checksum_record(
        self,
        check: WarehouseCheck,
        *,
        checksum: str,
        expected_count: int | None,
    ) -> DatasetRecord:
        return DatasetRecord(
            dataset_ref=checksum,
            dataset=check.data_type,
            market=self._enum_value(check.market),
            symbol_id=check.symbol_id,
            universe_ref=check.universe_ref,
            granularity=check.granularity,
            period_start=None,
            period_end=None,
            field_set=(),
            as_of=None,
            fresh_until=None,
            source_roles=(),
            dataset_checksum=checksum,
            dataset_checksum_algorithm="sha256:canonical-json-v1",
            dataset_checksum_scope="normalized-batch-v1",
            row={},
            dataset_row_count=expected_count,
        )

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

    def _data_integrity_gap(self, check: WarehouseCheck, record: DatasetRecord, *, invalid_count: int | None = None) -> DataGap:
        message = "local dataset checksum metadata missing or row payload missing"
        if invalid_count is not None and invalid_count > 1:
            message = f"{message}: invalid_records={invalid_count}"
        return DataGap(
            gap_id=f"gap:{check.request_id}:data_integrity_failed:{record.dataset_ref}",
            request_id=check.request_id,
            severity=GapSeverity.BLOCKER,
            reason=GapReason.DATA_INTEGRITY_FAILED,
            market=check.market,
            symbol_id=check.symbol_id,
            data_type=check.data_type,
            granularity=check.granularity,
            required_fields=(),
            evidence_refs=(record.dataset_ref,),
            human_readable=message,
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
