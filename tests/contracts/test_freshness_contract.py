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
        "dataset_checksum": "unit-checksum",
        "dataset_checksum_algorithm": "sha256:canonical-json-v1",
        "dataset_checksum_scope": "normalized-batch-v1",
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


def test_freshness_checker_skips_cn_a_exchange_holidays() -> None:
    checker = FreshnessChecker()
    as_of = datetime(2025, 2, 5, 9, 0, tzinfo=UTC)
    verdict = checker.evaluate(
        request=_request(
            date_range_start=date(2025, 1, 27),
            date_range_end=date(2025, 2, 5),
            timezone="Asia/Shanghai",
            calendar="CN_A_SSE_SZSE",
            as_of=as_of,
        ),
        records=[
            _record(
                market="CN_A",
                symbol_id="600519.SH",
                period_start=date(2025, 1, 27),
                period_end=date(2025, 1, 27),
                as_of=as_of,
                fresh_until=as_of,
                row={"date": "2025-01-27", "close": 1500.0},
            ),
            _record(
                market="CN_A",
                symbol_id="600519.SH",
                period_start=date(2025, 2, 5),
                period_end=date(2025, 2, 5),
                as_of=as_of,
                fresh_until=as_of,
                row={"date": "2025-02-05", "close": 1501.0},
            ),
        ],
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


def test_freshness_checker_reports_tail_gap_ranges() -> None:
    checker = FreshnessChecker()
    verdict = checker.evaluate(
        request=_request(date_range_start=date(2026, 5, 1), date_range_end=date(2026, 5, 8)),
        records=[_record(period_start=date(2026, 5, 1), period_end=date(2026, 5, 4))],
    )

    assert verdict.satisfied is False
    gap = next(gap for gap in verdict.gaps if gap["reason"] == "date_range_missing")
    assert gap["details"]["missing_ranges"] == ({"start": "2026-05-05", "end": "2026-05-08"},)


def test_freshness_checker_reports_stale_gap() -> None:
    checker = FreshnessChecker()
    verdict = checker.evaluate(
        request=_request(freshness_policy="ttl_5m", as_of=datetime(2026, 5, 31, 23, 0, tzinfo=UTC)),
        records=[_record(as_of=datetime(2026, 5, 31, 20, 0, tzinfo=UTC))],
    )
    assert verdict.satisfied is False
    assert any(gap["reason"] == "warehouse_stale" for gap in verdict.gaps)


def test_trading_day_freshness_accepts_latest_completed_session_on_weekend() -> None:
    checker = FreshnessChecker()
    as_of = datetime(2026, 6, 14, 16, 0, tzinfo=UTC)
    verdict = checker.evaluate(
        request=_request(
            date_range_start=date(2026, 6, 8),
            date_range_end=date(2026, 6, 14),
            timezone="America/New_York",
            calendar="US_NYSE_NASDAQ",
            as_of=as_of,
        ),
        records=_weekday_records(date(2026, 6, 8), date(2026, 6, 12), as_of=datetime(2026, 6, 12, 21, 0, tzinfo=UTC)),
    )

    assert verdict.satisfied is True
    assert not any(gap["reason"] == "warehouse_stale" for gap in verdict.gaps)


def test_us_calendar_skips_2025_exchange_holiday_inside_lookback_window() -> None:
    checker = FreshnessChecker()
    as_of = datetime(2025, 6, 20, 21, 0, tzinfo=UTC)
    verdict = checker.evaluate(
        request=_request(
            date_range_start=date(2025, 6, 16),
            date_range_end=date(2025, 6, 20),
            timezone="America/New_York",
            calendar="US_NYSE_NASDAQ",
            as_of=as_of,
        ),
        records=[
            _record(period_start=date(2025, 6, 16), period_end=date(2025, 6, 16), as_of=as_of),
            _record(period_start=date(2025, 6, 17), period_end=date(2025, 6, 17), as_of=as_of),
            _record(period_start=date(2025, 6, 18), period_end=date(2025, 6, 18), as_of=as_of),
            _record(period_start=date(2025, 6, 20), period_end=date(2025, 6, 20), as_of=as_of),
        ],
    )

    assert verdict.satisfied is True
    assert not any(gap["reason"] == "date_range_missing" for gap in verdict.gaps)


def test_hk_calendar_skips_2025_exchange_holiday_inside_lookback_window() -> None:
    checker = FreshnessChecker()
    as_of = datetime(2025, 10, 3, 9, 0, tzinfo=UTC)
    verdict = checker.evaluate(
        request=_request(
            date_range_start=date(2025, 9, 29),
            date_range_end=date(2025, 10, 3),
            timezone="Asia/Hong_Kong",
            calendar="HK_XHKG",
            as_of=as_of,
        ),
        records=[
            _record(market="HK", period_start=date(2025, 9, 29), period_end=date(2025, 9, 29), as_of=as_of),
            _record(market="HK", period_start=date(2025, 9, 30), period_end=date(2025, 9, 30), as_of=as_of),
            _record(market="HK", period_start=date(2025, 10, 2), period_end=date(2025, 10, 2), as_of=as_of),
            _record(market="HK", period_start=date(2025, 10, 3), period_end=date(2025, 10, 3), as_of=as_of),
        ],
    )

    assert verdict.satisfied is True
    assert not any(gap["reason"] == "date_range_missing" for gap in verdict.gaps)


def test_hk_xhkg_trading_day_freshness_uses_16_00_local_close() -> None:
    checker = FreshnessChecker()
    before_close = checker.evaluate(
        request=_request(
            date_range_start=date(2026, 6, 12),
            date_range_end=date(2026, 6, 15),
            timezone="Asia/Hong_Kong",
            calendar="HK_XHKG",
            as_of=datetime(2026, 6, 15, 7, 30, tzinfo=UTC),
        ),
        records=[_record(market="HK", period_start=date(2026, 6, 12), period_end=date(2026, 6, 12), as_of=datetime(2026, 6, 12, 9, 0, tzinfo=UTC))],
    )
    after_close = checker.evaluate(
        request=_request(
            date_range_start=date(2026, 6, 12),
            date_range_end=date(2026, 6, 15),
            timezone="Asia/Hong_Kong",
            calendar="HK_XHKG",
            as_of=datetime(2026, 6, 15, 8, 30, tzinfo=UTC),
        ),
        records=[_record(market="HK", period_start=date(2026, 6, 12), period_end=date(2026, 6, 12), as_of=datetime(2026, 6, 12, 9, 0, tzinfo=UTC))],
    )

    assert before_close.satisfied is True
    assert after_close.satisfied is False
    assert any(gap["reason"] == "warehouse_stale" for gap in after_close.gaps)


def test_cn_a_trading_day_freshness_uses_16_00_local_close() -> None:
    checker = FreshnessChecker()
    before_close = checker.evaluate(
        request=_request(
            date_range_start=date(2026, 7, 9),
            date_range_end=date(2026, 7, 9),
            timezone="Asia/Shanghai",
            calendar="CN_A_SSE_SZSE",
            as_of=datetime(2026, 7, 10, 7, 40, tzinfo=UTC),
        ),
        records=[
            _record(
                market="CN_A",
                symbol_id="600519.SH",
                period_start=date(2026, 7, 9),
                period_end=date(2026, 7, 9),
                as_of=datetime(2026, 7, 9, 8, 30, tzinfo=UTC),
            )
        ],
    )
    at_close = checker.evaluate(
        request=_request(
            date_range_start=date(2026, 7, 9),
            date_range_end=date(2026, 7, 9),
            timezone="Asia/Shanghai",
            calendar="CN_A_SSE_SZSE",
            as_of=datetime(2026, 7, 10, 8, 0, tzinfo=UTC),
        ),
        records=[
            _record(
                market="CN_A",
                symbol_id="600519.SH",
                period_start=date(2026, 7, 9),
                period_end=date(2026, 7, 9),
                as_of=datetime(2026, 7, 9, 8, 30, tzinfo=UTC),
            )
        ],
    )

    assert before_close.satisfied is True
    assert at_close.satisfied is False
    assert any(gap["reason"] == "warehouse_stale" for gap in at_close.gaps)


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
