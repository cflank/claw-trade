from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Sequence


@dataclass(frozen=True)
class DataRunScheduleContext:
    run_id: str
    run_started_at: datetime

    @classmethod
    def for_plan(cls, *, run_id: str, run_started_at: datetime) -> "DataRunScheduleContext":
        started_at = run_started_at if run_started_at.tzinfo else run_started_at.replace(tzinfo=UTC)
        if started_at.tzinfo != UTC:
            started_at = started_at.astimezone(UTC)
        return cls(run_id=run_id, run_started_at=started_at)


class DataRunScheduler:
    def schedule(self, batches: Sequence[Any], context: DataRunScheduleContext) -> tuple[Any, ...]:
        return tuple(batches)
