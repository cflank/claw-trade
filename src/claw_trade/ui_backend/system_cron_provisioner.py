from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping

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
        job = self._cron_adapter.add_job(
            name=_SELECTION_REFRESH_KEY,
            schedule=_DEFAULT_SELECTION_REFRESH_SCHEDULE,
            agent_id=_AGENT_ID,
            payload=_agent_turn_payload(
                {
                    "kind": "selection_data_refresh",
                    "reason": "scheduled_data_refresh",
                    "cronRunId": "auto",
                }
            ),
            session_target="isolated",
            wake_mode="now",
            delivery={"mode": "none"},
            enabled=True,
        )
        return SystemCronJobRef(key=_SELECTION_REFRESH_KEY, openclaw_cron_job_id=job.openclaw_cron_job_id)

    def ensure_data_maintenance(
        self,
        *,
        market: str,
        job_kind: str,
        schedule: Mapping[str, Any],
    ) -> SystemCronJobRef:
        key = f"data-maintenance:{market}:{job_kind}"
        job = self._cron_adapter.add_job(
            name=key,
            schedule=schedule,
            agent_id=_AGENT_ID,
            payload=_agent_turn_payload(
                {
                    "kind": "data_maintenance",
                    "market": market,
                    "jobKind": job_kind,
                    "cronRunId": "auto",
                }
            ),
            session_target="isolated",
            wake_mode="now",
            delivery={"mode": "none"},
            enabled=True,
        )
        return SystemCronJobRef(key=key, openclaw_cron_job_id=job.openclaw_cron_job_id)


def _agent_turn_payload(wake_payload: Mapping[str, Any]) -> dict[str, Any]:
    wake_json = json.dumps(dict(wake_payload), ensure_ascii=False, separators=(",", ":"))
    return {
        "kind": "agentTurn",
        "message": (
            "Call `claw-trade-scheduled-work-wake` exactly once with this JSON payload and no other tool calls:\n"
            f"{wake_json}\n"
            "Do not generate reports, analyze markets, or rewrite the payload."
        ),
        "toolsAllow": [_SCHEDULED_WORK_TOOL],
        "timeoutSeconds": 60,
    }
