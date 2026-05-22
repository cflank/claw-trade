from __future__ import annotations

import pytest

from claw_trade.ui_contracts.constants import FIRST_VERSION_NOT_IN_SCOPE
from claw_trade.ui_contracts.scope_guard import (
    FirstVersionScopeError,
    assert_capability_allowed,
    assert_data_source_type_supported,
    assert_schedule_frequency_supported,
    assert_wechat_protocol_not_in_claw_trade,
    evaluate_first_version_capability,
)


def test_first_version_blocked_capabilities_are_frozen() -> None:
    assert FIRST_VERSION_NOT_IN_SCOPE == (
        "mobile_layout",
        "parallel_full_report_generation",
        "hourly_full_report_schedule",
        "brief_summary_worker",
        "failed_task_management_page",
        "unknown_http_json_data_source",
        "wechat_protocol_in_claw_trade",
    )


@pytest.mark.parametrize(
    "capability, expected_code",
    [
        ("mobile_layout", "mobile_layout"),
        ("parallel_full_report_generation", "parallel_full_report_generation"),
        ("brief_summary_worker", "brief_summary_worker"),
        ("failed_task_management_page", "failed_task_management_page"),
    ],
)
def test_out_of_scope_capability_is_rejected(capability: str, expected_code: str) -> None:
    with pytest.raises(FirstVersionScopeError) as exc:
        assert_capability_allowed(capability)
    assert exc.value.item_code == expected_code


def test_hourly_schedule_is_rejected() -> None:
    with pytest.raises(FirstVersionScopeError) as exc:
        assert_schedule_frequency_supported("hourly")
    assert exc.value.item_code == "hourly_full_report_schedule"


def test_daily_and_weekly_schedule_are_allowed() -> None:
    assert_schedule_frequency_supported("daily")
    assert_schedule_frequency_supported("weekly")


def test_unknown_http_json_data_source_is_rejected() -> None:
    with pytest.raises(FirstVersionScopeError) as exc:
        assert_data_source_type_supported("custom_http")
    assert exc.value.item_code == "unknown_http_json_data_source"


def test_wechat_protocol_implementation_is_rejected() -> None:
    with pytest.raises(FirstVersionScopeError) as exc:
        assert_wechat_protocol_not_in_claw_trade(True)
    assert exc.value.item_code == "wechat_protocol_in_claw_trade"


def test_normal_capability_is_allowed() -> None:
    decision = evaluate_first_version_capability("three_column_shell")
    assert decision.allowed is True
    assert decision.code == "allowed"
