from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


REQUIRED_CONTEXT_FIELDS: tuple[str, ...] = (
    "run_id",
    "stage",
    "worker_id",
    "call_id",
    "dispatch_id",
    "tool_name",
    "evidence_root",
    "current_time",
)


@dataclass(frozen=True)
class ToolRuntimeContext:
    run_id: str
    stage: str
    worker_id: str
    call_id: str
    dispatch_id: str
    tool_name: str
    evidence_root: str
    current_time: str
    current_date: str | None = None

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "ToolRuntimeContext":
        values: dict[str, str] = {}
        for field_name in REQUIRED_CONTEXT_FIELDS:
            raw_value = payload.get(field_name)
            if not isinstance(raw_value, str) or not raw_value.strip():
                raise ValueError(f"runtime_context.{field_name} is required")
            values[field_name] = raw_value
        raw_current_date = payload.get("current_date")
        current_date: str | None
        if raw_current_date is None:
            current_date = None
        elif isinstance(raw_current_date, str) and raw_current_date.strip():
            current_date = raw_current_date
        else:
            raise ValueError("runtime_context.current_date must be a non-empty string when provided")
        return cls(current_date=current_date, **values)
