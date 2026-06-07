from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from claw_trade.data_gateway.execution.single_flight import SingleFlight
from claw_trade.data_gateway.warehouse.repository import DatasetRepository


@dataclass(frozen=True)
class _Ingest:
    status: str
    dataset_refs: tuple[str, ...]
    raw_refs: tuple[str, ...]
    attempt_refs: tuple[str, ...]
    gaps: tuple[object, ...]


class _Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def tick(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def test_single_flight_owner_waiter_and_shared_publish() -> None:
    sf = SingleFlight()
    owner = sf.acquire("key", lease_ttl_seconds=30)
    assert owner.kind == "owner"
    waiter = sf.acquire("key", lease_ttl_seconds=30)
    assert waiter.kind == "waiter"

    published = sf.publish(
        "key",
        owner.owner_token or "",
        _Ingest(
            status="ingested",
            dataset_refs=("dataset:1",),
            raw_refs=("raw:1",),
            attempt_refs=("attempt:1",),
            gaps=(),
        ),
    )
    assert published is True

    shared = sf.acquire("key", lease_ttl_seconds=30)
    assert shared.kind == "shared"
    assert shared.published is not None
    assert shared.published.refs.dataset_refs == ("dataset:1",)


def test_single_flight_wait_timeout() -> None:
    sf = SingleFlight()
    sf.acquire("key", lease_ttl_seconds=30)
    with pytest.raises(TimeoutError):
        sf.wait("key", timeout_seconds=0)


def test_single_flight_expired_owner_can_be_taken_over() -> None:
    clock = _Clock(datetime(2026, 5, 31, 12, 0, tzinfo=UTC))
    sf = SingleFlight(now_fn=clock)
    owner1 = sf.acquire("key", lease_ttl_seconds=1)
    assert owner1.kind == "owner"
    clock.tick(2)
    owner2 = sf.acquire("key", lease_ttl_seconds=5)
    assert owner2.kind == "owner"
    assert owner2.owner_token != owner1.owner_token


def test_single_flight_published_error_can_be_retried_after_lease_expiry() -> None:
    clock = _Clock(datetime(2026, 5, 31, 12, 0, tzinfo=UTC))
    sf = SingleFlight(now_fn=clock)
    owner1 = sf.acquire("key", lease_ttl_seconds=1)
    assert owner1.kind == "owner"
    assert sf.publish(
        "key",
        owner1.owner_token or "",
        _Ingest(
            status="failed",
            dataset_refs=(),
            raw_refs=("raw:failed",),
            attempt_refs=("attempt:failed",),
            gaps=(),
        ),
    )

    immediate = sf.acquire("key", lease_ttl_seconds=1)
    assert immediate.kind == "shared"

    clock.tick(2)
    owner2 = sf.acquire("key", lease_ttl_seconds=5)
    assert owner2.kind == "owner"
    assert owner2.owner_token != owner1.owner_token


def test_single_flight_persists_publish_across_instances_with_same_repository() -> None:
    repository = DatasetRepository()
    owner_sf = SingleFlight(repository=repository)
    owner = owner_sf.acquire("key", lease_ttl_seconds=30)
    assert owner.kind == "owner"
    assert owner.owner_token is not None
    assert owner_sf.publish(
        "key",
        owner.owner_token,
        _Ingest(
            status="ingested",
            dataset_refs=("dataset:shared",),
            raw_refs=("raw:shared",),
            attempt_refs=("attempt:shared",),
            gaps=(),
        ),
    )

    consumer_sf = SingleFlight(repository=repository)
    shared = consumer_sf.acquire("key", lease_ttl_seconds=30)
    assert shared.kind == "shared"
    assert shared.published is not None
    assert shared.published.refs.dataset_refs == ("dataset:shared",)
