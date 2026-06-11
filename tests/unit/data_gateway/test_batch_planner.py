from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pytest
from claw_trade.data_gateway.coordination.batch_planner import ProviderBatchPlanner
from claw_trade.data_gateway.coordination.coalescer import MergeGroup, MergeItem
from claw_trade.data_gateway.providers.base import CapabilityError
from claw_trade.data_gateway.providers.registry import CapabilitySnapshot


@dataclass(frozen=True)
class BatchPolicy:
    supports_batch: bool
    batch_by: str
    max_symbols_per_call: int | None = None
    max_days_per_call: int | None = None
    mergeable_fields: tuple[str, ...] = ()
    pagination_policy: object = "none"
    split_policy: object = "strict"


@dataclass(frozen=True)
class Capability:
    provider_id: str
    plugin_version: str
    endpoint_id: str
    market: str
    data_type: str
    source_role: str
    supported_granularities: tuple[str, ...]
    coverage_fields: tuple[str, ...]
    priority_rank: int
    credential_required: bool
    credential_scope: str | None
    http_visibility: str
    can_be_formal_fact_source: bool
    license_policy: object
    rate_limit_policy: object
    batch_policy: BatchPolicy


def _capability(
    batch_policy: BatchPolicy,
    *,
    provider_id: str = "official_feed",
    endpoint_id: str = "daily",
) -> Capability:
    return Capability(
        provider_id=provider_id,
        plugin_version="1.0.0",
        endpoint_id=endpoint_id,
        market="US",
        data_type="daily_bar",
        source_role="official",
        supported_granularities=("daily",),
        coverage_fields=("close", "volume"),
        priority_rank=10,
        credential_required=False,
        credential_scope=None,
        http_visibility="managed_http",
        can_be_formal_fact_source=True,
        license_policy={"raw_storage_mode": "metadata_only"},
        rate_limit_policy={"window_seconds": 60, "max_calls": 20},
        batch_policy=batch_policy,
    )


def _group(
    symbol_ids: tuple[str, ...],
    items: tuple[MergeItem, ...],
    *,
    provider_id: str = "official_feed",
    endpoint_id: str = "daily",
    universe_ref: str | None = None,
    start: date = date(2026, 5, 1),
    end: date = date(2026, 5, 31),
) -> MergeGroup:
    return MergeGroup(
        provider_id=provider_id,
        endpoint_id=endpoint_id,
        market="US",
        data_type="daily_bar",
        granularity="daily",
        source_role="official",
        priority_rank=10,
        request_ids=tuple(item.request_id for item in items),
        symbol_ids=symbol_ids,
        universe_ref=universe_ref,
        date_range_start=start,
        date_range_end=end,
        exchange="NYSE",
        currency="USD",
        timezone="America/New_York",
        calendar="US_NYSE_NASDAQ",
        fields_union=("close", "volume"),
        items=items,
    )


def test_batch_planner_merges_symbols_when_batch_supported() -> None:
    planner = ProviderBatchPlanner()
    snapshot = CapabilitySnapshot.from_capabilities(
        (
            _capability(
                BatchPolicy(
                    supports_batch=True,
                    batch_by="symbol",
                    max_symbols_per_call=2,
                    mergeable_fields=("close", "volume"),
                )
            ),
        )
    )
    items = (
        MergeItem(
            request_id="req-1",
            symbol_ids=("AAPL",),
            date_range_start=date(2026, 5, 1),
            date_range_end=date(2026, 5, 31),
            fields=("close",),
            required_level="required",
        ),
        MergeItem(
            request_id="req-2",
            symbol_ids=("MSFT",),
            date_range_start=date(2026, 5, 1),
            date_range_end=date(2026, 5, 31),
            fields=("volume",),
            required_level="required",
        ),
        MergeItem(
            request_id="req-3",
            symbol_ids=("GOOG",),
            date_range_start=date(2026, 5, 1),
            date_range_end=date(2026, 5, 31),
            fields=("close",),
            required_level="required",
        ),
    )
    groups = (_group(("AAPL", "MSFT", "GOOG"), items),)

    batches = planner.build_batches(groups, snapshot)
    assert len(batches) == 2
    assert sum(len(getattr(batch, "symbol_ids")) for batch in batches) == 3
    assert getattr(batches[0], "rate_limit_policy").max_requests is None
    assert getattr(batches[0], "lease_ttl_seconds") == 30
    assert getattr(batches[0], "wait_timeout_seconds") == 1
    assert getattr(batches[0], "license_policy") == {"raw_storage_mode": "metadata_only"}
    assert getattr(batches[0], "exchange") == "NYSE"
    assert getattr(batches[0], "currency") == "USD"
    assert getattr(batches[0], "timezone") == "America/New_York"
    assert getattr(batches[0], "calendar") == "US_NYSE_NASDAQ"
    assert getattr(batches[0], "http_visibility") == "managed_http"


def test_batch_planner_splits_to_single_when_batch_not_supported() -> None:
    planner = ProviderBatchPlanner()
    snapshot = CapabilitySnapshot.from_capabilities(
        (
            _capability(
                BatchPolicy(
                    supports_batch=False,
                    batch_by="none",
                )
            ),
        )
    )
    items = (
        MergeItem(
            request_id="req-1",
            symbol_ids=("AAPL",),
            date_range_start=date(2026, 5, 1),
            date_range_end=date(2026, 5, 31),
            fields=("close",),
            required_level="required",
        ),
        MergeItem(
            request_id="req-2",
            symbol_ids=("MSFT",),
            date_range_start=date(2026, 5, 1),
            date_range_end=date(2026, 5, 31),
            fields=("volume",),
            required_level="required",
        ),
    )
    groups = (_group(("AAPL", "MSFT"), items),)

    batches = planner.build_batches(groups, snapshot)
    assert len(batches) == 2
    assert [getattr(batch, "request_ids") for batch in batches] == [("req-1",), ("req-2",)]


def test_batch_planner_uses_source_quota_key_and_shared_managed_http_cooldown_key() -> None:
    planner = ProviderBatchPlanner()
    snapshot = CapabilitySnapshot.from_capabilities(
        (
            _capability(
                BatchPolicy(
                    supports_batch=True,
                    batch_by="symbol",
                    max_symbols_per_call=2,
                    mergeable_fields=("close", "volume"),
                ),
                provider_id="crypto_coinglass_derivatives",
                endpoint_id="futures_open_interest",
            ),
        )
    )
    item = MergeItem(
        request_id="req-cg",
        symbol_ids=("BTCUSDT",),
        date_range_start=date(2026, 5, 1),
        date_range_end=date(2026, 5, 31),
        fields=("close",),
        required_level="required",
    )

    batches = planner.build_batches(
        (
            _group(
                ("BTCUSDT",),
                (item,),
                provider_id="crypto_coinglass_derivatives",
                endpoint_id="futures_open_interest",
            ),
        ),
        snapshot,
    )

    assert getattr(batches[0], "rate_limit_key") == "ratelimit:coinglass"
    assert getattr(batches[0], "cooldown_key") == "ratelimit:coinglass"


def test_batch_planner_preserves_universe_ref_for_date_batch() -> None:
    planner = ProviderBatchPlanner()
    snapshot = CapabilitySnapshot.from_capabilities(
        (
            _capability(
                BatchPolicy(
                    supports_batch=True,
                    batch_by="date",
                    max_days_per_call=1,
                    mergeable_fields=("close", "volume"),
                )
            ),
        )
    )
    item = MergeItem(
        request_id="req-universe",
        symbol_ids=(),
        universe_ref="all_a_shares",
        date_range_start=date(2026, 5, 1),
        date_range_end=date(2026, 5, 1),
        fields=("close",),
        required_level="required",
    )

    batches = planner.build_batches(
        (_group((), (item,), universe_ref="all_a_shares", start=date(2026, 5, 1), end=date(2026, 5, 1)),),
        snapshot,
    )

    assert len(batches) == 1
    assert getattr(batches[0], "symbol_ids") == ()
    assert getattr(batches[0], "universe_ref") == "all_a_shares"
    assert getattr(batches[0], "request_ids") == ("req-universe",)


def test_batch_planner_fails_closed_on_unexecutable_policy() -> None:
    planner = ProviderBatchPlanner()
    snapshot = CapabilitySnapshot.from_capabilities(
        (
            _capability(
                BatchPolicy(
                    supports_batch=True,
                    batch_by="symbol",
                    max_symbols_per_call=None,
                )
            ),
        )
    )
    items = (
        MergeItem(
            request_id="req-1",
            symbol_ids=("AAPL",),
            date_range_start=date(2026, 5, 1),
            date_range_end=date(2026, 5, 31),
            fields=("close",),
            required_level="required",
        ),
    )
    groups = (_group(("AAPL",), items),)

    with pytest.raises(CapabilityError, match="max_symbols_per_call"):
        planner.build_batches(groups, snapshot)
