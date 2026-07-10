from __future__ import annotations

from datetime import date, time

CN_A_DAILY_DATA_READY_CUTOFF = time(16, 0)

_CN_A_NON_TRADING_RANGES: tuple[tuple[date, date], ...] = (
    (date(2024, 1, 1), date(2024, 1, 1)),
    (date(2024, 2, 9), date(2024, 2, 17)),
    (date(2024, 4, 4), date(2024, 4, 6)),
    (date(2024, 5, 1), date(2024, 5, 5)),
    (date(2024, 6, 10), date(2024, 6, 10)),
    (date(2024, 9, 15), date(2024, 9, 17)),
    (date(2024, 10, 1), date(2024, 10, 7)),
    (date(2025, 1, 1), date(2025, 1, 1)),
    (date(2025, 1, 28), date(2025, 2, 4)),
    (date(2025, 4, 4), date(2025, 4, 6)),
    (date(2025, 5, 1), date(2025, 5, 5)),
    (date(2025, 5, 31), date(2025, 6, 2)),
    (date(2025, 10, 1), date(2025, 10, 8)),
    (date(2026, 1, 1), date(2026, 1, 3)),
    (date(2026, 2, 15), date(2026, 2, 23)),
    (date(2026, 4, 4), date(2026, 4, 6)),
    (date(2026, 5, 1), date(2026, 5, 5)),
    (date(2026, 6, 19), date(2026, 6, 21)),
    (date(2026, 9, 25), date(2026, 9, 27)),
    (date(2026, 10, 1), date(2026, 10, 7)),
)

_US_NON_TRADING_DATES: frozenset[date] = frozenset(
    {
        date(2025, 1, 1),
        date(2025, 1, 20),
        date(2025, 2, 17),
        date(2025, 4, 18),
        date(2025, 5, 26),
        date(2025, 6, 19),
        date(2025, 7, 4),
        date(2025, 9, 1),
        date(2025, 11, 27),
        date(2025, 12, 25),
        date(2026, 1, 1),
        date(2026, 1, 19),
        date(2026, 2, 16),
        date(2026, 4, 3),
        date(2026, 5, 25),
        date(2026, 6, 19),
        date(2026, 7, 3),
        date(2026, 9, 7),
        date(2026, 11, 26),
        date(2026, 12, 25),
    }
)

_HK_NON_TRADING_DATES: frozenset[date] = frozenset(
    {
        date(2025, 1, 1),
        date(2025, 1, 29),
        date(2025, 1, 30),
        date(2025, 1, 31),
        date(2025, 4, 4),
        date(2025, 4, 18),
        date(2025, 4, 21),
        date(2025, 5, 1),
        date(2025, 5, 5),
        date(2025, 7, 1),
        date(2025, 10, 1),
        date(2025, 10, 7),
        date(2025, 10, 29),
        date(2025, 12, 25),
        date(2025, 12, 26),
        date(2026, 1, 1),
        date(2026, 2, 17),
        date(2026, 2, 18),
        date(2026, 2, 19),
        date(2026, 4, 3),
        date(2026, 4, 6),
        date(2026, 5, 1),
        date(2026, 6, 19),
        date(2026, 7, 1),
        date(2026, 10, 1),
        date(2026, 12, 25),
    }
)


def is_expected_daily_date(day: date, calendar: str) -> bool:
    if calendar == "CRYPTO_24_7":
        return True
    if day.weekday() >= 5:
        return False
    if calendar == "CN_A_SSE_SZSE":
        return not any(start <= day <= end for start, end in _CN_A_NON_TRADING_RANGES)
    if calendar == "US_NYSE_NASDAQ":
        return day not in _US_NON_TRADING_DATES
    if calendar in {"HK_XHKG", "HK_HKEX"}:
        return day not in _HK_NON_TRADING_DATES
    return True
