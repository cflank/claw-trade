from __future__ import annotations

from dataclasses import dataclass

from claw_trade.ui_contracts.constants import FIRST_VERSION_NOT_IN_SCOPE


class FirstVersionScopeError(ValueError):
    def __init__(self, item_code: str, message: str) -> None:
        super().__init__(message)
        self.item_code = item_code


@dataclass(frozen=True)
class ScopeDecision:
    allowed: bool
    code: str
    reason: str


_BLOCKED_CAPABILITY_REASON = {
    "mobile_layout": "NT-01: 首版不做移动端布局。",
    "parallel_full_report_generation": "NT-02: 首版完整报告仅支持串行。",
    "hourly_full_report_schedule": "NT-03: 首版仅支持每天或每周。",
    "brief_summary_worker": "NT-04: 禁止新增简报 worker。",
    "failed_task_management_page": "NT-06: 首版不做失败任务管理页。",
    "unknown_http_json_data_source": "NT-07: 禁止任意未知 HTTP/JSON 数据源。",
    "wechat_protocol_in_claw_trade": "NT-08: claw-trade 不实现微信协议。",
}


def list_blocked_capabilities() -> tuple[str, ...]:
    return FIRST_VERSION_NOT_IN_SCOPE


def evaluate_first_version_capability(capability: str) -> ScopeDecision:
    normalized = capability.strip().lower()
    for blocked in FIRST_VERSION_NOT_IN_SCOPE:
        if normalized == blocked.lower():
            return ScopeDecision(allowed=False, code=blocked, reason=_BLOCKED_CAPABILITY_REASON[blocked])
    return ScopeDecision(allowed=True, code="allowed", reason="首版范围允许。")


def assert_capability_allowed(capability: str) -> None:
    decision = evaluate_first_version_capability(capability)
    if not decision.allowed:
        raise FirstVersionScopeError(decision.code, decision.reason)


def assert_schedule_frequency_supported(frequency: str) -> None:
    normalized = frequency.strip().lower()
    if normalized in {"hourly", "every_hour", "per_hour"}:
        raise FirstVersionScopeError(
            "hourly_full_report_schedule",
            _BLOCKED_CAPABILITY_REASON["hourly_full_report_schedule"],
        )
    if normalized not in {"daily", "weekly"}:
        raise ValueError(f"不支持的周期: {frequency}")


def assert_data_source_type_supported(source_type: str) -> None:
    normalized = source_type.strip().lower()
    blocked = {
        "custom_http",
        "customjsonmapping",
        "custom_json",
        "custom_json_mapping",
        "unknown_http_json",
    }
    if normalized in blocked:
        raise FirstVersionScopeError(
            "unknown_http_json_data_source",
            _BLOCKED_CAPABILITY_REASON["unknown_http_json_data_source"],
        )


def assert_wechat_protocol_not_in_claw_trade(requested: bool) -> None:
    if requested:
        raise FirstVersionScopeError(
            "wechat_protocol_in_claw_trade",
            _BLOCKED_CAPABILITY_REASON["wechat_protocol_in_claw_trade"],
        )
