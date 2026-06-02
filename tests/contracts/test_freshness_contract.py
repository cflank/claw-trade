from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from claw_trade.data_gateway.warehouse import DatasetRecord, FreshnessChecker


def _record(**overrides: object) -> DatasetRecord:
    payload: dict[str, object] = {
        "dataset_ref": "dataset:daily_bar:US:AAPL:daily:2026-05-01:2026-05-31",
        "dataset": "daily_bar",
        "market": "US",
        "symbol_id": "AAPL",
        "universe_ref": None,
        "granularity": "daily",
        "period_start": date(2026, 5, 1),
        "period_end": date(2026, 5, 31),
        "field_set": ("date", "close", "volume"),
        "as_of": datetime(2026, 5, 31, 20, 0, tzinfo=UTC),
        "fresh_until": datetime(2026, 6, 1, 0, 0, tzinfo=UTC),
        "source_roles": ("official",),
        "row": {"date": "2026-05-31", "close": 200.0},
    }
    payload.update(overrides)
    return DatasetRecord(**payload)


def _request(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "granularity": "daily",
        "fields": ("date", "close"),
        "date_range_start": date(2026, 5, 1),
        "date_range_end": date(2026, 5, 31),
        "freshness_policy": "trading_day",
        "source_role_required": "official",
        "as_of": datetime(2026, 5, 31, 23, 0, tzinfo=UTC),
    }
    payload.update(overrides)
    return payload


def _weekday_records(start: date, end: date, *, as_of: datetime) -> list[DatasetRecord]:
    rows: list[DatasetRecord] = []
    cursor = start
    while cursor <= end:
        if cursor.weekday() < 5:
            rows.append(
                _record(
                    dataset_ref=f"dataset:daily_bar:US:AAPL:daily:{cursor.isoformat()}",
                    period_start=cursor,
                    period_end=cursor,
                    as_of=as_of,
                    fresh_until=as_of,
                    row={"date": cursor.isoformat(), "close": 200.0},
                )
            )
        cursor += timedelta(days=1)
    return rows


def test_freshness_checker_reports_all_contract_gap_types() -> None:
    checker = FreshnessChecker()
    verdict = checker.evaluate(
        request=_request(granularity="intraday", fields=("date", "close", "amount")),
        records=[_record()],
    )
    reasons = {gap["reason"] for gap in verdict.gaps}

    assert verdict.satisfied is False
    assert "granularity_mismatch" in reasons
    assert "field_missing" in reasons


def test_freshness_checker_reports_date_range_gap() -> None:
    checker = FreshnessChecker()
    verdict = checker.evaluate(request=_request(date_range_start=date(2026, 4, 1)), records=[_record()])
    assert verdict.satisfied is False
    assert any(gap["reason"] == "date_range_missing" for gap in verdict.gaps)


def test_freshness_checker_accepts_iso_date_strings_from_mongo_documents() -> None:
    checker = FreshnessChecker()
    verdict = checker.evaluate(
        request=_request(),
        records=[
            _record(
                period_start="2026-05-01",
                period_end="2026-05-31",
                as_of="2026-05-31T20:00:00+00:00",
                fresh_until="2026-06-01T00:00:00+00:00",
            )
        ],
    )

    assert verdict.satisfied is True
    assert verdict.gaps == ()


def test_freshness_checker_uses_market_calendar_for_weekend_boundaries_and_open_session_end() -> None:
    checker = FreshnessChecker()
    as_of = datetime(2026, 6, 1, 15, 0, tzinfo=UTC)
    verdict = checker.evaluate(
        request=_request(
            date_range_start=date(2025, 6, 1),
            date_range_end=date(2026, 6, 1),
            timezone="America/New_York",
            calendar="US_NYSE_NASDAQ",
            as_of=as_of,
        ),
        records=_weekday_records(date(2025, 6, 2), date(2026, 5, 29), as_of=as_of),
    )

    assert verdict.satisfied is True
    assert not any(gap["reason"] == "date_range_missing" for gap in verdict.gaps)


def test_freshness_checker_keeps_crypto_calendar_24_7() -> None:
    checker = FreshnessChecker()
    verdict = checker.evaluate(
        request=_request(
            date_range_start=date(2026, 5, 1),
            date_range_end=date(2026, 5, 4),
            timezone="UTC",
            calendar="CRYPTO_24_7",
            as_of=datetime(2026, 5, 4, 12, 0, tzinfo=UTC),
        ),
        records=[
            _record(period_start=date(2026, 5, 1), period_end=date(2026, 5, 1)),
            _record(period_start=date(2026, 5, 4), period_end=date(2026, 5, 4)),
        ],
    )

    assert verdict.satisfied is False
    gap = next(gap for gap in verdict.gaps if gap["reason"] == "date_range_missing")
    assert gap["details"]["missing_ranges"] == ({"start": "2026-05-02", "end": "2026-05-03"},)


def test_freshness_checker_reports_middle_date_gap_not_just_min_max_coverage() -> None:
    checker = FreshnessChecker()
    verdict = checker.evaluate(
        request=_request(date_range_start=date(2026, 5, 1), date_range_end=date(2026, 5, 31)),
        records=[
            _record(period_start=date(2026, 5, 1), period_end=date(2026, 5, 10)),
            _record(period_start=date(2026, 5, 20), period_end=date(2026, 5, 31)),
        ],
    )

    assert verdict.satisfied is False
    gap = next(gap for gap in verdict.gaps if gap["reason"] == "date_range_missing")
    assert gap["details"]["missing_ranges"] == ({"start": "2026-05-11", "end": "2026-05-19"},)


def test_freshness_checker_reports_stale_gap() -> None:
    checker = FreshnessChecker()
    verdict = checker.evaluate(
        request=_request(freshness_policy="ttl_5m", as_of=datetime(2026, 5, 31, 23, 0, tzinfo=UTC)),
        records=[_record(as_of=datetime(2026, 5, 31, 20, 0, tzinfo=UTC))],
    )
    assert verdict.satisfied is False
    assert any(gap["reason"] == "warehouse_stale" for gap in verdict.gaps)


def test_freshness_checker_reports_stale_gap_when_as_of_is_missing() -> None:
    checker = FreshnessChecker()
    verdict = checker.evaluate(
        request=_request(freshness_policy="trading_day"),
        records=[_record(as_of=None, fresh_until=None)],
    )

    assert verdict.satisfied is False
    assert any(
        gap["reason"] == "warehouse_stale" and gap["details"]["stale_reason"] == "missing_as_of"
        for gap in verdict.gaps
    )


def test_freshness_checker_reports_official_source_gap() -> None:
    checker = FreshnessChecker()
    verdict = checker.evaluate(
        request=_request(source_role_required="official"),
        records=[_record(source_roles=("discovery",))],
    )
    assert verdict.satisfied is False
    assert any(gap["reason"] == "warehouse_missing" for gap in verdict.gaps)
