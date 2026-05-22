from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

_FAILED_STATUSES = frozenset({"auth_invalid", "unreachable", "rate_limited", "schema_invalid", "empty"})


class DataSourceHealthService:
    def __init__(
        self,
        *,
        attempt_reader: Any,
        instance_reader: Any,
    ) -> None:
        self._attempt_reader = attempt_reader
        self._instance_reader = instance_reader

    def collect_configured_failed_data_sources(
        self,
        *,
        report_id: str | None = None,
        task_id: str | None = None,
    ) -> tuple[dict[str, Any], ...]:
        attempts = self._attempt_reader.list_run_attempts(report_id=report_id, task_id=task_id)
        instances = {str(item.get("id", "")): item for item in self._instance_reader.list_instances()}
        events: list[dict[str, Any]] = []
        for attempt in attempts:
            instance_id = str(attempt.get("dataSourceInstanceId") or attempt.get("data_source_instance_id") or "")
            if not instance_id:
                continue
            instance = instances.get(instance_id)
            if instance is None:
                continue
            if not _has_configuration(instance):
                continue
            if not bool(instance.get("enabled", False)):
                continue
            if bool(instance.get("requiresKey", instance.get("requires_key", True))) is False:
                continue
            if not bool(attempt.get("usedInRun", attempt.get("used_in_run", False))):
                continue
            status = str(attempt.get("status", "unknown"))
            if status not in _FAILED_STATUSES:
                continue
            events.append(
                to_data_source_health_event_for_user(
                    {
                        "displayName": str(instance.get("display_name", instance.get("displayName", instance_id))),
                        "status": status,
                        "userMessage": _translate_data_source_status(status),
                        "impact": _resolve_impact(instance, attempt),
                        "occurredAt": str(attempt.get("occurredAt", attempt.get("occurred_at", ""))),
                    }
                )
            )
        return tuple(events)


def to_data_source_health_event_for_user(event: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "displayName": str(event.get("displayName", "")),
        "status": str(event.get("status", "unknown")),
        "userMessage": str(event.get("userMessage", "该数据源本次不可用。")),
        "impact": str(event.get("impact", "medium")),
        "occurredAt": str(event.get("occurredAt", "")),
    }


def _has_configuration(instance: Mapping[str, Any]) -> bool:
    credential_ref = str(instance.get("credential_ref", instance.get("credentialRef", ""))).strip()
    endpoint_url = str(instance.get("endpoint_url", instance.get("endpointUrl", ""))).strip()
    return bool(credential_ref or endpoint_url)


def _resolve_impact(instance: Mapping[str, Any], attempt: Mapping[str, Any]) -> str:
    domain = str(attempt.get("domain", "")).strip().lower()
    supported_type = str(instance.get("supported_type", instance.get("supportedType", ""))).lower()
    if "news" in domain or "search" in supported_type:
        return "medium"
    if "market" in domain or "price" in domain or "kline" in domain:
        return "high"
    return "medium"


def _translate_data_source_status(status: str) -> str:
    text = status.strip().lower()
    if text == "auth_invalid":
        return "密钥无效，请到设置中更新。"
    if text == "unreachable":
        return "接口地址不可访问，请检查地址或代理。"
    if text == "rate_limited":
        return "当前访问频率过高，请稍后重试。"
    if text == "schema_invalid":
        return "返回格式不符合要求，该数据源本次不可用。"
    if text == "empty":
        return "本次没有获取到有效数据。"
    return "该数据源本次不可用。"

