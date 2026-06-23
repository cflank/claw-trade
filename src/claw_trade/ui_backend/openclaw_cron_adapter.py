from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol


class OpenClawCronGateway(Protocol):
    def cron_add(self, params: Mapping[str, Any]) -> Any: ...

    def cron_update(self, params: Mapping[str, Any]) -> Any: ...

    def cron_remove(self, *, job_id: str) -> Any: ...

    def cron_run(self, *, job_id: str, idempotency_key: str | None = None) -> Any: ...

    def cron_list(self, params: Mapping[str, Any] | None = None) -> Any: ...

    def cron_status(self, *, job_id: str) -> Any: ...

    def cron_runs(self, *, job_id: str, limit: int | None = None) -> Any: ...


@dataclass(frozen=True)
class OpenClawCronJobRef:
    openclaw_cron_job_id: str
    raw_payload: Any


class OpenClawCronAdapter:
    def __init__(self, gateway: OpenClawCronGateway) -> None:
        self._gateway = gateway

    def add_job(
        self,
        *,
        name: str,
        schedule: Mapping[str, Any],
        agent_id: str,
        payload: Mapping[str, Any],
        session_target: str = "isolated",
        wake_mode: str = "now",
        delivery: Mapping[str, Any] | None = None,
        enabled: bool = True,
    ) -> OpenClawCronJobRef:
        result = self._gateway.cron_add(
            {
                "name": name,
                "schedule": _normalize_schedule(schedule),
                "enabled": enabled,
                "agentId": agent_id,
                "sessionTarget": session_target,
                "wakeMode": wake_mode,
                "payload": dict(payload),
                "delivery": dict(delivery or {"mode": "none"}),
            }
        )
        return OpenClawCronJobRef(openclaw_cron_job_id=_extract_job_id(result), raw_payload=result)

    def update_job(self, *, job_id: str, patch: Mapping[str, Any]) -> Any:
        params = {"jobId": job_id, **dict(patch)}
        return self._gateway.cron_update(params)

    def remove_job(self, *, job_id: str) -> Any:
        return self._gateway.cron_remove(job_id=job_id)

    def run_job(self, *, job_id: str, idempotency_key: str | None = None) -> Any:
        return self._gateway.cron_run(job_id=job_id, idempotency_key=idempotency_key)

    def list_jobs(self, params: Mapping[str, Any] | None = None) -> Any:
        return self._gateway.cron_list(_normalize_list_params(params))

    def status(self, *, job_id: str) -> Any:
        return self._gateway.cron_status(job_id=job_id)

    def runs(self, *, job_id: str, limit: int | None = None) -> Any:
        return self._gateway.cron_runs(job_id=job_id, limit=limit)


def _extract_job_id(payload: Any) -> str:
    if isinstance(payload, str) and payload.strip():
        return payload.strip()
    if isinstance(payload, Mapping):
        for key in ("jobId", "id", "openclawCronJobId"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        nested = payload.get("job")
        if isinstance(nested, Mapping):
            return _extract_job_id(nested)
        nested = payload.get("data")
        if isinstance(nested, Mapping):
            return _extract_job_id(nested)
    raise RuntimeError("openclaw_cron_job_id_unavailable")


def _normalize_schedule(schedule: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(schedule)
    if payload.get("kind") == "every":
        return payload
    if payload.get("type") == "every" and "intervalMs" in payload:
        return {"kind": "every", "everyMs": payload["intervalMs"]}
    return payload


def _normalize_list_params(params: Mapping[str, Any] | None) -> dict[str, Any]:
    payload = dict(params or {})
    name_prefix = payload.pop("namePrefix", None)
    exact_name = payload.pop("name", None)
    if "query" not in payload:
        query = name_prefix if isinstance(name_prefix, str) and name_prefix.strip() else exact_name
        if isinstance(query, str) and query.strip():
            payload["query"] = query.strip()
    if name_prefix is not None or exact_name is not None:
        payload.setdefault("includeDisabled", True)
    return payload
