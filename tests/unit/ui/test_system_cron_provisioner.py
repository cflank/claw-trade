from __future__ import annotations

from typing import Any, Mapping

import pytest

from claw_trade.ui_backend.openclaw_cron_adapter import OpenClawCronAdapter
from claw_trade.ui_backend.system_cron_provisioner import SystemCronProvisioner


class FakeCronGateway:
    def __init__(self, list_payload: Any | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.update_calls: list[dict[str, Any]] = []
        self.list_payload = [] if list_payload is None else list_payload

    def cron_add(self, params: Mapping[str, Any]) -> dict[str, Any]:
        payload = dict(params)
        self.calls.append(payload)
        return {"jobId": payload["name"]}

    def cron_update(self, params: Mapping[str, Any]) -> dict[str, Any]:
        payload = dict(params)
        self.update_calls.append(payload)
        return {"updated": True}

    def cron_list(self, params: Mapping[str, Any] | None = None) -> Any:
        return self.list_payload


def test_selection_data_refresh_cron_calls_scheduled_work_wake_only() -> None:
    gateway = FakeCronGateway()
    provisioner = SystemCronProvisioner(OpenClawCronAdapter(gateway))

    result = provisioner.ensure_selection_data_refresh()

    assert result.key == "selection-data-refresh:CN_A:daily"
    assert result.openclaw_cron_job_id == "selection-data-refresh:CN_A:daily"
    params = gateway.calls[0]
    assert params["name"] == "selection-data-refresh:CN_A:daily"
    assert params["agentId"] == "market_data_maintenance_worker"
    assert params["payload"]["kind"] == "toolCall"
    assert params["payload"]["toolName"] == "claw-trade-scheduled-work-wake"
    assert params["payload"]["input"]["kind"] == "selection_data_refresh"


@pytest.mark.parametrize(
    ("list_payload", "expected_job_id"),
    (
        ([{"jobId": "existing-selection", "name": "selection-data-refresh:CN_A:daily"}], "existing-selection"),
        ({"jobs": [{"id": "existing-selection", "name": "selection-data-refresh:CN_A:daily"}]}, "existing-selection"),
        ({"data": [{"openclawCronJobId": "existing-selection", "key": "selection-data-refresh:CN_A:daily"}]}, "existing-selection"),
        ({"items": [{"name": "selection-data-refresh:CN_A:daily"}]}, "selection-data-refresh:CN_A:daily"),
    ),
)
def test_selection_data_refresh_ensure_updates_existing_job_instead_of_adding(
    list_payload: Any,
    expected_job_id: str,
) -> None:
    gateway = FakeCronGateway(list_payload)
    provisioner = SystemCronProvisioner(OpenClawCronAdapter(gateway))

    result = provisioner.ensure_selection_data_refresh()

    assert result.openclaw_cron_job_id == expected_job_id
    assert gateway.calls == []
    assert len(gateway.update_calls) == 1
    update = gateway.update_calls[0]
    assert update["jobId"] == expected_job_id
    assert update["name"] == "selection-data-refresh:CN_A:daily"
    assert update["schedule"] == {"kind": "cron", "expr": "0 21 * * *", "tz": "UTC", "staggerMs": 0}
    assert update["enabled"] is True
    assert update["agentId"] == "market_data_maintenance_worker"
    assert update["payload"]["kind"] == "toolCall"
    assert update["payload"]["toolName"] == "claw-trade-scheduled-work-wake"
    assert update["payload"]["input"]["kind"] == "selection_data_refresh"


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
        assert call["payload"]["kind"] == "toolCall"
        assert call["payload"]["toolName"] == "claw-trade-scheduled-work-wake"
        assert call["payload"]["input"]["kind"] == "data_maintenance"


def test_data_maintenance_ensure_updates_existing_job_instead_of_adding() -> None:
    gateway = FakeCronGateway({"items": [{"jobId": "existing-maintenance", "name": "data-maintenance:CN_A:eod"}]})
    provisioner = SystemCronProvisioner(OpenClawCronAdapter(gateway))
    schedule = {"kind": "cron", "expr": "0 2 * * *", "tz": "UTC", "staggerMs": 0}

    result = provisioner.ensure_data_maintenance(market="CN_A", job_kind="eod", schedule=schedule)

    assert result.key == "data-maintenance:CN_A:eod"
    assert result.openclaw_cron_job_id == "existing-maintenance"
    assert gateway.calls == []
    assert len(gateway.update_calls) == 1
    update = gateway.update_calls[0]
    assert update["jobId"] == "existing-maintenance"
    assert update["name"] == "data-maintenance:CN_A:eod"
    assert update["schedule"] == schedule
    assert update["enabled"] is True
    assert update["payload"]["input"] == {
        "kind": "data_maintenance",
        "market": "CN_A",
        "jobKind": "eod",
        "cronRunId": "auto",
    }
