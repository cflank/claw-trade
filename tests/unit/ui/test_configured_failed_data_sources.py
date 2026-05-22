from __future__ import annotations

from claw_trade.ui_backend.data_source_health import DataSourceHealthService


class _AttemptReader:
    def __init__(self, attempts):
        self._attempts = attempts

    def list_run_attempts(self, *, report_id=None, task_id=None):
        _ = (report_id, task_id)
        return self._attempts


class _InstanceReader:
    def __init__(self, instances):
        self._instances = instances

    def list_instances(self):
        return self._instances


def test_collect_configured_failed_data_sources_filters_to_four_conditions() -> None:
    attempts = [
        {"id": "a1", "dataSourceInstanceId": "s1", "usedInRun": True, "status": "auth_invalid", "occurredAt": "t1"},
        {"id": "a2", "dataSourceInstanceId": "s2", "usedInRun": True, "status": "auth_invalid", "occurredAt": "t2"},
        {"id": "a3", "dataSourceInstanceId": "s3", "usedInRun": False, "status": "unreachable", "occurredAt": "t3"},
        {"id": "a4", "dataSourceInstanceId": "s4", "usedInRun": True, "status": "ok", "occurredAt": "t4"},
        {"id": "a5", "dataSourceInstanceId": "s5", "usedInRun": True, "status": "unreachable", "occurredAt": "t5"},
    ]
    instances = [
        {"id": "s1", "display_name": "News API", "credential_ref": "sec-1", "enabled": True, "requires_key": True},
        {"id": "s2", "display_name": "Disabled API", "credential_ref": "sec-2", "enabled": False, "requires_key": True},
        {"id": "s3", "display_name": "Unused API", "credential_ref": "sec-3", "enabled": True, "requires_key": True},
        {"id": "s4", "display_name": "Healthy API", "credential_ref": "sec-4", "enabled": True, "requires_key": True},
        {"id": "s5", "display_name": "NoKey API", "credential_ref": "sec-5", "enabled": True, "requires_key": False},
    ]
    service = DataSourceHealthService(
        attempt_reader=_AttemptReader(attempts),
        instance_reader=_InstanceReader(instances),
    )
    events = service.collect_configured_failed_data_sources(report_id="r1")
    assert len(events) == 1
    assert events[0]["displayName"] == "News API"
    assert events[0]["status"] == "auth_invalid"
    assert "密钥无效" in events[0]["userMessage"]

