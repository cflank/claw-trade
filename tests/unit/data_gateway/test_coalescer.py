from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from claw_trade.data_gateway.coordination.coalescer import RequestCoalescer


def _candidate(
    *,
    request_id: str,
    provider_id: str = "official_feed",
    endpoint_id: str = "daily",
    market: str = "US",
    data_type: str = "daily_bar",
    granularity: str = "daily",
    source_role: str = "official",
    priority_rank: int = 10,
    symbol_id: str | None = "AAPL",
    universe_ref: str | None = None,
    fields: tuple[str, ...] = ("close",),
    start: date | None = None,
    end: date | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        request_id=request_id,
        provider_id=provider_id,
        endpoint_id=endpoint_id,
        market=market,
        data_type=data_type,
        granularity=granularity,
        source_role=source_role,
        priority_rank=priority_rank,
        symbol_id=symbol_id,
        universe_ref=universe_ref,
        fields=fields,
        date_range_start=start,
        date_range_end=end,
        exchange="NASDAQ",
        currency="USD",
        timezone="America/New_York",
        calendar="US_NYSE_NASDAQ",
        required_level="required",
    )


def test_coalescer_deduplicates_same_request_and_merges_fields_ranges() -> None:
    coalescer = RequestCoalescer()
    candidates = (
        _candidate(
            request_id="req-1",
            fields=("close",),
            start=date(2026, 5, 1),
            end=date(2026, 5, 10),
        ),
        _candidate(
            request_id="req-1",
            fields=("volume",),
            start=date(2026, 5, 5),
            end=date(2026, 5, 15),
        ),
    )

    groups = coalescer.coalesce((), candidates, capabilities=None)
    assert len(groups) == 1
    group = groups[0]
    assert group.request_ids == ("req-1",)
    assert group.fields_union == ("close", "volume")
    assert group.date_range_start == date(2026, 5, 1)
    assert group.date_range_end == date(2026, 5, 15)
    assert group.exchange == "NASDAQ"
    assert group.currency == "USD"
    assert group.timezone == "America/New_York"
    assert group.calendar == "US_NYSE_NASDAQ"


def test_coalescer_preserves_universe_ref_for_universe_request() -> None:
    coalescer = RequestCoalescer()

    groups = coalescer.coalesce(
        (),
        (
            _candidate(
                request_id="req-universe",
                symbol_id=None,
                universe_ref="all_a_shares",
                start=date(2026, 6, 4),
                end=date(2026, 6, 4),
            ),
        ),
        capabilities=None,
    )

    assert len(groups) == 1
    group = groups[0]
    assert group.symbol_ids == ()
    assert group.universe_ref == "all_a_shares"
    assert group.items[0].universe_ref == "all_a_shares"


def test_coalescer_splits_different_source_roles() -> None:
    coalescer = RequestCoalescer()
    groups = coalescer.coalesce(
        (),
        (
            _candidate(request_id="req-1", source_role="official"),
            _candidate(request_id="req-2", source_role="discovery"),
        ),
        capabilities=None,
    )
    assert len(groups) == 2
    assert {group.source_role for group in groups} == {"official", "discovery"}
