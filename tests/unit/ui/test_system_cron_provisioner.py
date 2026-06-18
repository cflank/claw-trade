from __future__ import annotations

import json
from typing import Any, Mapping

from claw_trade.ui_backend.openclaw_cron_adapter import OpenClawCronAdapter
from claw_trade.ui_backend.system_cron_provisioner import SystemCronProvisioner


class FakeCronGateway:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def cron_add(self, params: Mapping[str, Any]) -> dict[str, Any]:
        payload = dict(params)
        self.calls.append(payload)
        return {"jobId": payload["name"]}


def _embedded_payload(params: Mapping[str, Any]) -> dict[str, Any]:
    message = str(params["payload"]["message"])
    line = message.splitlines()[1]
    return dict(json.loads(line))


def test_selection_data_refresh_cron_calls_scheduled_work_wake_only() -> None:
    gateway = FakeCronGateway()
    provisioner = SystemCronProvisioner(OpenClawCronAdapter(gateway))

    result = provisioner.ensure_selection_data_refresh()

    assert result.key == "selection-data-refresh:CN_A:daily"
    assert result.openclaw_cron_job_id == "selection-data-refresh:CN_A:daily"
    params = gateway.calls[0]
    assert params["name"] == "selection-data-refresh:CN_A:daily"
    assert params["agentId"] == "market_data_maintenance_worker"
    assert params["payload"]["toolsAllow"] == ["claw-trade-scheduled-work-wake"]
    assert _embedded_payload(params)["kind"] == "selection_data_refresh"


def test_data_maintenance_crons_call_scheduled_work_wake_only() -> None:
    gateway = FakeCronGateway()
    provisioner = SystemCronProvisioner(OpenClawCronAdapter(gateway))
    schedule = {"kind": "cron", "expr": "0 2 * * *", "tz": "UTC", "staggerMs": 0}

    results = [
        provisioner.ensure_data_maintenance(market="CN_A", job_kind="eod", schedule=schedule),
        provisioner.ensure_data_maintenance(market="HK", job_kind="eod", schedule=schedule),
        provisioner.ensure_data_maintenance(market="US", job_kind="eod", schedule=schedule),
        provisioner.ensure_data_maintenance(market="CRYPTO", job_kind="kline-refresh", schedule=schedule),
    ]

    assert [result.key for result in results] == [
        "data-maintenance:CN_A:eod",
        "data-maintenance:HK:eod",
        "data-maintenance:US:eod",
        "data-maintenance:CRYPTO:kline-refresh",
    ]
    assert [call["name"] for call in gateway.calls] == [result.key for result in results]
    for call in gateway.calls:
        assert call["agentId"] == "market_data_maintenance_worker"
        assert call["payload"]["toolsAllow"] == ["claw-trade-scheduled-work-wake"]
        assert _embedded_payload(call)["kind"] == "data_maintenance"
