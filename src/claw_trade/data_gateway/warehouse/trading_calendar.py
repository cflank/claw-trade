from __future__ import annotations

from datetime import date


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


def is_expected_daily_date(day: date, calendar: str) -> bool:
    if calendar == "CRYPTO_24_7":
        return True
    if day.weekday() >= 5:
        return False
    if calendar == "CN_A_SSE_SZSE":
        return not any(start <= day <= end for start, end in _CN_A_NON_TRADING_RANGES)
    return True
