from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .repository import DatasetRecord
from .trading_calendar import is_expected_daily_date

Gap = dict[str, Any]


@dataclass(frozen=True)
class FreshnessVerdict:
    satisfied: bool
    gaps: tuple[Gap, ...]


class FreshnessChecker:
    def evaluate(self, *, request: Any, records: Sequence[DatasetRecord]) -> FreshnessVerdict:
        if not records:
            return FreshnessVerdict(
                satisfied=False,
                gaps=(self._gap("warehouse_missing", dimension="dataset", message="dataset not found"),),
            )

        gaps: list[Gap] = []
        granularity = self._value(request, "granularity")
        if any(record.granularity != granularity for record in records):
            gaps.append(
                self._gap(
                    "granularity_mismatch",
                    expected=granularity,
                    actual=tuple(sorted({record.granularity for record in records})),
                )
            )

        request_fields = tuple(self._value(request, "fields", ()))
        present_fields = set().union(*(set(record.field_set) for record in records))
        missing_fields = tuple(field for field in request_fields if field not in present_fields)
        if missing_fields:
            gaps.append(self._gap("field_missing", missing_fields=missing_fields))

        range_gap = self._check_date_coverage(request=request, records=records)
        if range_gap is not None:
            gaps.append(range_gap)

        source_gap = self._check_source_role(request=request, records=records)
        if source_gap is not None:
            gaps.append(source_gap)

        freshness_gap = self._check_freshness(request=request, records=records)
        if freshness_gap is not None:
            gaps.append(freshness_gap)

        return FreshnessVerdict(satisfied=not gaps, gaps=tuple(gaps))

    def _check_date_coverage(self, *, request: Any, records: Sequence[DatasetRecord]) -> Gap | None:
        request_start = self._to_datetime(self._value(request, "date_range_start"))
        request_end = self._effective_request_end(request, self._to_datetime(self._value(request, "date_range_end")))
        if request_start is None and request_end is None:
            return None

        starts = [self._to_datetime(record.period_start) for record in records if record.period_start]
        ends = [self._to_datetime(record.period_end) for record in records if record.period_end]
        if not starts or not ends:
            return self._gap(
                "date_range_missing",
                expected_start=request_start.isoformat() if request_start else None,
                expected_end=request_end.isoformat() if request_end else None,
                actual_start=None,
                actual_end=None,
            )

        actual_start = min(starts)
        actual_end = max(ends)
        expected_start = self._first_expected_daily_datetime(request, request_start, request_end)
        expected_end = self._last_expected_daily_datetime(request, request_start, request_end)
        start_missing = expected_start is not None and actual_start > expected_start
        end_missing = expected_end is not None and actual_end < expected_end
        continuity_gap = self._check_continuity(
            request=request,
            records=records,
            granularity=str(self._value(request, "granularity")),
            coverage_start=expected_start.date() if expected_start else None,
            coverage_end=expected_end.date() if expected_end else None,
        )
        if start_missing or end_missing or continuity_gap:
            return self._gap(
                "date_range_missing",
                expected_start=expected_start.isoformat() if expected_start else None,
                expected_end=expected_end.isoformat() if expected_end else None,
                actual_start=actual_start.isoformat(),
                actual_end=actual_end.isoformat(),
                missing_ranges=continuity_gap,
            )
        return None

    def _check_continuity(
        self,
        *,
        request: Any,
        records: Sequence[DatasetRecord],
        granularity: str,
        coverage_start: date | None = None,
        coverage_end: date | None = None,
    ) -> tuple[dict[str, str], ...]:
        if granularity != "daily":
            return ()
        ranges = sorted(
            (
                (start.date(), end.date())
                for start, end in (
                    (self._to_datetime(record.period_start), self._to_datetime(record.period_end))
                    for record in records
                )
                if start is not None and end is not None
            ),
            key=lambda item: item[0],
        )
        if not ranges:
            return ()
        calendar = str(self._value(request, "calendar", ""))
        expected_start = coverage_start or ranges[0][0]
        expected_end = coverage_end or ranges[-1][1]
        expected_dates = self._expected_daily_dates(expected_start, expected_end, calendar)
        merged_ranges = self._merge_daily_ranges(ranges, expected_start, expected_end)
        missing_dates = self._missing_daily_dates(expected_dates, merged_ranges)
        return self._compress_missing_dates(missing_dates, calendar)

    @staticmethod
    def _merge_daily_ranges(ranges: Sequence[tuple[date, date]], start: date, end: date) -> tuple[tuple[date, date], ...]:
        merged: list[tuple[date, date]] = []
        for range_start, range_end in ranges:
            if range_end < start or range_start > end:
                continue
            clipped_start = max(range_start, start)
            clipped_end = min(range_end, end)
            if not merged or clipped_start > merged[-1][1] + timedelta(days=1):
                merged.append((clipped_start, clipped_end))
                continue
            merged[-1] = (merged[-1][0], max(merged[-1][1], clipped_end))
        return tuple(merged)

    @staticmethod
    def _missing_daily_dates(
        expected_dates: Sequence[date],
        merged_ranges: Sequence[tuple[date, date]],
    ) -> tuple[date, ...]:
        missing: list[date] = []
        range_index = 0
        for day in expected_dates:
            while range_index < len(merged_ranges) and merged_ranges[range_index][1] < day:
                range_index += 1
            if range_index >= len(merged_ranges) or merged_ranges[range_index][0] > day:
                missing.append(day)
        return tuple(missing)

    def _check_source_role(self, *, request: Any, records: Sequence[DatasetRecord]) -> Gap | None:
        required_source_role = self._value(request, "source_role_required")
        if not required_source_role:
            return None
        for record in records:
            if required_source_role in record.source_roles:
                return None
        return self._gap(
            "warehouse_missing",
            dimension="source_role",
            required_source_role=required_source_role,
        )

    def _check_freshness(self, *, request: Any, records: Sequence[DatasetRecord]) -> Gap | None:
        policy = str(self._value(request, "freshness_policy", "")).strip()
        if not policy:
            return None
        as_of = self._to_datetime(self._value(request, "as_of")) or datetime.now(UTC)
        freshest_record = max(
            (record for record in records if record.as_of is not None),
            key=lambda record: self._to_datetime(record.as_of) or datetime.min.replace(tzinfo=UTC),
            default=None,
        )
        if freshest_record is None or freshest_record.as_of is None:
            return self._gap("warehouse_stale", policy=policy, stale_reason="missing_as_of")

        record_as_of = self._to_datetime(freshest_record.as_of)
        if record_as_of is None:
            return self._gap("warehouse_stale", policy=policy, stale_reason="invalid_as_of")

        if policy == "immutable_seed":
            return None
        if policy == "warehouse_only":
            return None
        if policy == "trading_day":
            if record_as_of.date() >= as_of.date():
                return None
            return self._gap("warehouse_stale", policy=policy, record_as_of=record_as_of.isoformat(), as_of=as_of.isoformat())
        ttl_match = re.fullmatch(r"ttl_(\d+)([mhd])", policy)
        if ttl_match:
            amount = int(ttl_match.group(1))
            unit = ttl_match.group(2)
            delta = {"m": timedelta(minutes=amount), "h": timedelta(hours=amount), "d": timedelta(days=amount)}[unit]
            if as_of - record_as_of <= delta:
                return None
            return self._gap("warehouse_stale", policy=policy, record_as_of=record_as_of.isoformat(), as_of=as_of.isoformat())

        fresh_until = self._to_datetime(freshest_record.fresh_until)
        if fresh_until and as_of <= fresh_until:
            return None
        return self._gap("warehouse_stale", policy=policy, record_as_of=record_as_of.isoformat(), as_of=as_of.isoformat())

    def _effective_request_end(self, request: Any, request_end: datetime | None) -> datetime | None:
        if request_end is None:
            return None
        if str(self._value(request, "granularity", "")) != "daily":
            return request_end
        if str(self._value(request, "freshness_policy", "")) != "trading_day":
            return request_end

        calendar = str(self._value(request, "calendar", ""))
        if calendar == "CRYPTO_24_7":
            return request_end

        as_of = self._to_datetime(self._value(request, "as_of")) or datetime.now(UTC)
        local_as_of = self._local_as_of(as_of, self._value(request, "timezone"))
        local_day = local_as_of.date()
        requested_end_day = request_end.date()
        if requested_end_day < local_day:
            return request_end

        close_time = self._market_close_time(calendar)
        if self._is_expected_daily_date(local_day, calendar) and local_as_of.time() >= close_time:
            completed_day = local_day
        else:
            completed_day = self._previous_expected_daily_date(local_day - timedelta(days=1), calendar)
        if completed_day is None:
            return request_end
        effective_day = min(requested_end_day, completed_day)
        return datetime(effective_day.year, effective_day.month, effective_day.day, tzinfo=UTC)

    def _first_expected_daily_datetime(self, request: Any, request_start: datetime | None, request_end: datetime | None) -> datetime | None:
        if request_start is None:
            return None
        if str(self._value(request, "granularity", "")) != "daily":
            return request_start
        end_date = request_end.date() if request_end else request_start.date()
        day = self._first_expected_daily_date(request_start.date(), end_date, str(self._value(request, "calendar", "")))
        if day is None:
            return None
        return datetime(day.year, day.month, day.day, tzinfo=UTC)

    def _last_expected_daily_datetime(self, request: Any, request_start: datetime | None, request_end: datetime | None) -> datetime | None:
        if request_end is None:
            return None
        if str(self._value(request, "granularity", "")) != "daily":
            return request_end
        start_date = request_start.date() if request_start else request_end.date()
        day = self._last_expected_daily_date(start_date, request_end.date(), str(self._value(request, "calendar", "")))
        if day is None:
            return None
        return datetime(day.year, day.month, day.day, tzinfo=UTC)

    def _expected_daily_dates(self, start: date, end: date, calendar: str) -> tuple[date, ...]:
        if start > end:
            return ()
        output: list[date] = []
        cursor = start
        while cursor <= end:
            if self._is_expected_daily_date(cursor, calendar):
                output.append(cursor)
            cursor += timedelta(days=1)
        return tuple(output)

    def _first_expected_daily_date(self, start: date, end: date, calendar: str) -> date | None:
        for day in self._expected_daily_dates(start, end, calendar):
            return day
        return None

    def _last_expected_daily_date(self, start: date, end: date, calendar: str) -> date | None:
        dates = self._expected_daily_dates(start, end, calendar)
        return dates[-1] if dates else None

    def _previous_expected_daily_date(self, start: date, calendar: str) -> date | None:
        cursor = start
        for _ in range(14):
            if self._is_expected_daily_date(cursor, calendar):
                return cursor
            cursor -= timedelta(days=1)
        return None

    @staticmethod
    def _is_expected_daily_date(day: date, calendar: str) -> bool:
        return is_expected_daily_date(day, calendar)

    def _compress_missing_dates(self, missing_dates: Sequence[date], calendar: str) -> tuple[dict[str, str], ...]:
        if not missing_dates:
            return ()
        ranges: list[dict[str, str]] = []
        start = missing_dates[0]
        end = missing_dates[0]
        for day in missing_dates[1:]:
            previous_expected = self._previous_expected_daily_date(day - timedelta(days=1), calendar)
            if previous_expected == end:
                end = day
                continue
            ranges.append({"start": start.isoformat(), "end": end.isoformat()})
            start = day
            end = day
        ranges.append({"start": start.isoformat(), "end": end.isoformat()})
        return tuple(ranges)

    @staticmethod
    def _market_close_time(calendar: str) -> time:
        if calendar == "US_NYSE_NASDAQ":
            return time(16, 0)
        if calendar == "HK_HKEX":
            return time(16, 0)
        if calendar == "CN_A_SSE_SZSE":
            return time(15, 0)
        return time(23, 59)

    @staticmethod
    def _local_as_of(as_of: datetime, timezone_name: Any) -> datetime:
        if as_of.tzinfo is None:
            as_of = as_of.replace(tzinfo=UTC)
        if not timezone_name:
            return as_of.astimezone(UTC)
        try:
            return as_of.astimezone(ZoneInfo(str(timezone_name)))
        except ZoneInfoNotFoundError:
            return as_of.astimezone(UTC)

    @staticmethod
    def _value(obj: Any, key: str, default: Any = None) -> Any:
        if isinstance(obj, Mapping):
            return obj.get(key, default)
        return getattr(obj, key, default)

    @staticmethod
    def _to_datetime(value: Any) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=UTC)
            return value
        if isinstance(value, date):
            return datetime(value.year, value.month, value.day, tzinfo=UTC)
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            try:
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError:
                try:
                    parsed_date = date.fromisoformat(text)
                except ValueError:
                    return None
                return datetime(parsed_date.year, parsed_date.month, parsed_date.day, tzinfo=UTC)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=UTC)
            return parsed
        return None

    @staticmethod
    def _gap(reason: str, **details: Any) -> Gap:
        return {"reason": reason, "details": details}
