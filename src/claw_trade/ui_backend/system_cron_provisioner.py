from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from claw_trade.ui_backend.openclaw_cron_adapter import OpenClawCronAdapter

_AGENT_ID = "market_data_maintenance_worker"
_SCHEDULED_WORK_TOOL = "claw-trade-scheduled-work-wake"
_SELECTION_REFRESH_KEY = "selection-data-refresh:CN_A:daily"
_DEFAULT_SELECTION_REFRESH_SCHEDULE = {"kind": "cron", "expr": "0 21 * * *", "tz": "UTC", "staggerMs": 0}


@dataclass(frozen=True)
class SystemCronJobRef:
    key: str
    openclaw_cron_job_id: str


class SystemCronProvisioner:
    def __init__(self, cron_adapter: OpenClawCronAdapter) -> None:
        self._cron_adapter = cron_adapter

    def ensure_selection_data_refresh(self) -> SystemCronJobRef:
        return self._ensure_job(
            key=_SELECTION_REFRESH_KEY,
            schedule=_DEFAULT_SELECTION_REFRESH_SCHEDULE,
            wake_payload={
                "kind": "selection_data_refresh",
                "reason": "scheduled_data_refresh",
                "cronRunId": "auto",
            },
        )

    def ensure_data_maintenance(
        self,
        *,
        market: str,
        job_kind: str,
        schedule: Mapping[str, Any],
    ) -> SystemCronJobRef:
        key = f"data-maintenance:{market}:{job_kind}"
        return self._ensure_job(
            key=key,
            schedule=schedule,
            wake_payload={
                "kind": "data_maintenance",
                "market": market,
                "jobKind": job_kind,
                "cronRunId": "auto",
            },
        )

    def _ensure_job(
        self,
        *,
        key: str,
        schedule: Mapping[str, Any],
        wake_payload: Mapping[str, Any],
    ) -> SystemCronJobRef:
        payload = _tool_call_payload(wake_payload)
        existing = _find_existing_job(self._cron_adapter.list_jobs({"name": key, "namePrefix": key}), key)
        if existing is not None:
            job_id = _job_id(existing)
            self._cron_adapter.update_job(
                job_id=job_id,
                patch={
                    "name": key,
                    "schedule": dict(schedule),
                    "enabled": True,
                    "agentId": _AGENT_ID,
                    "sessionTarget": "isolated",
                    "wakeMode": "now",
                    "payload": payload,
                    "delivery": {"mode": "none"},
                },
            )
            return SystemCronJobRef(key=key, openclaw_cron_job_id=job_id)

        job = self._cron_adapter.add_job(
            name=key,
            schedule=schedule,
            agent_id=_AGENT_ID,
            payload=payload,
            session_target="isolated",
            wake_mode="now",
            delivery={"mode": "none"},
            enabled=True,
        )
        return SystemCronJobRef(key=key, openclaw_cron_job_id=job.openclaw_cron_job_id)


def _tool_call_payload(wake_payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "kind": "toolCall",
        "toolName": _SCHEDULED_WORK_TOOL,
        "input": dict(wake_payload),
    }


def _find_existing_job(payload: Any, key: str) -> Mapping[str, Any] | str | None:
    for job in _jobs_from_list_payload(payload):
        if isinstance(job, str):
            if job == key:
                return job
            continue
        name = _job_text(job, "name", "key")
        if name == key:
            return job
    return None


def _jobs_from_list_payload(payload: Any) -> list[Mapping[str, Any] | str]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, Mapping) or isinstance(item, str)]
    if isinstance(payload, Mapping):
        for key in ("items", "jobs", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, Mapping) or isinstance(item, str)]
        nested = payload.get("result")
        if isinstance(nested, Mapping) or isinstance(nested, list):
            return _jobs_from_list_payload(nested)
    return []


def _job_id(job: Mapping[str, Any] | str) -> str:
    if isinstance(job, str):
        return job
    for key in ("jobId", "id", "openclawCronJobId", "name"):
        value = _job_text(job, key)
        if value:
            return value
    raise RuntimeError("openclaw_cron_job_id_unavailable")


def _job_text(job: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = job.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None
