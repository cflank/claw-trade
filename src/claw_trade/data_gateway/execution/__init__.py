from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from claw_trade.data_gateway.models import FetchResult, GateDecision, ResultRefs
from claw_trade.data_gateway.warehouse.repository import DatasetRepository


NON_REMOTE_GATE_KINDS = frozenset({"cache_hit", "shared_result", "rate_limited", "cached_empty", "cooldown_skipped"})


@dataclass(frozen=True)
class CacheEntry:
    cache_key: str
    status: Literal["remote_success", "cached_empty"]
    refs: ResultRefs
    fresh_until: datetime
    stale_until: datetime
    empty_reason: str | None = None

    def is_fresh(self, *, now: datetime) -> bool:
        return now <= self.fresh_until

    def is_stale(self, *, now: datetime) -> bool:
        return self.fresh_until < now <= self.stale_until


@dataclass(frozen=True)
class CacheLookup:
    state: Literal["miss", "fresh_success", "fresh_empty", "stale"]
    entry: CacheEntry | None = None


class ProviderResultCache:
    def __init__(self, repository: DatasetRepository | None = None) -> None:
        self._repository = repository or DatasetRepository()

    def get(self, cache_key: str, *, now: datetime | None = None) -> CacheLookup:
        row = self._repository.read_provider_result_cache(cache_key)
        if row is None:
            return CacheLookup(state="miss")
        fresh_until = row.get("fresh_until")
        stale_until = row.get("stale_until")
        if not isinstance(fresh_until, datetime) or not isinstance(stale_until, datetime):
            self._repository.delete_provider_result_cache(cache_key)
            return CacheLookup(state="miss")
        when = now or datetime.now(tz=fresh_until.tzinfo or UTC)
        status = str(row.get("status", ""))
        if status not in {"remote_success", "cached_empty"}:
            self._repository.delete_provider_result_cache(cache_key)
            return CacheLookup(state="miss")
        entry = CacheEntry(
            cache_key=cache_key,
            status=status,  # type: ignore[arg-type]
            refs=ResultRefs(
                dataset_refs=tuple(row.get("dataset_refs", ())),
                raw_refs=tuple(row.get("raw_refs", ())),
                attempt_refs=tuple(row.get("attempt_refs", ())),
            ),
            fresh_until=fresh_until,
            stale_until=stale_until,
            empty_reason=row.get("empty_reason"),
        )
        if entry.is_fresh(now=when):
            return CacheLookup(
                state="fresh_success" if entry.status == "remote_success" else "fresh_empty",
                entry=entry,
            )
        if entry.is_stale(now=when):
            return CacheLookup(state="stale", entry=entry)
        self._repository.delete_provider_result_cache(cache_key)
        return CacheLookup(state="miss")

    def put_remote_success(
        self,
        *,
        cache_key: str,
        refs: ResultRefs,
        fresh_until: datetime,
        stale_until: datetime,
    ) -> None:
        self._repository.write_provider_result_cache(
            cache_key=cache_key,
            status="remote_success",
            dataset_refs=refs.dataset_refs,
            raw_refs=refs.raw_refs,
            attempt_refs=refs.attempt_refs,
            fresh_until=fresh_until,
            stale_until=stale_until,
            empty_reason=None,
        )

    def put_cached_empty(
        self,
        *,
        cache_key: str,
        refs: ResultRefs,
        fresh_until: datetime,
        stale_until: datetime,
        empty_reason: str = "empty_result",
    ) -> None:
        self._repository.write_provider_result_cache(
            cache_key=cache_key,
            status="cached_empty",
            dataset_refs=refs.dataset_refs,
            raw_refs=refs.raw_refs,
            attempt_refs=refs.attempt_refs,
            fresh_until=fresh_until,
            stale_until=stale_until,
            empty_reason=empty_reason,
        )
