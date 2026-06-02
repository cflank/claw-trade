from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Sequence

from claw_trade.data_gateway.execution.rate_limit_policy import with_rate_limit_anchor


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
        return tuple(_batch_with_anchor(batch, context.run_started_at) for batch in batches)


def _batch_with_anchor(batch: Any, run_started_at: datetime) -> Any:
    policy = with_rate_limit_anchor(getattr(batch, "rate_limit_policy", None), run_started_at)
    if callable(getattr(batch, "model_copy", None)):
        return batch.model_copy(update={"rate_limit_policy": policy})
    data = dict(getattr(batch, "__dict__", {}))
    data["rate_limit_policy"] = policy
    return type(batch)(**data)
