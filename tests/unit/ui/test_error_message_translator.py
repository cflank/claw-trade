from __future__ import annotations

import pytest
from claw_trade.ui_backend.error_translator import (
    _CODE_TO_MESSAGE,
    public_code_for,
    translate_internal_error_for_user,
)
from claw_trade.ui_contracts.constants import PRODUCT_ERROR_CODES


def test_public_code_for_maps_known_category() -> None:
    assert public_code_for("queue_full") == "QUEUE_FULL"
    assert public_code_for("openclaw_unavailable") == "ASSISTANT_UNAVAILABLE"


def test_public_code_for_uses_safe_default_for_unknown_category() -> None:
    assert public_code_for("unknown_anything") == "ASSISTANT_UNAVAILABLE"


def test_all_product_error_codes_have_public_message_and_category_mapping() -> None:
    for code in PRODUCT_ERROR_CODES:
        assert code in _CODE_TO_MESSAGE
        assert public_code_for(code) == code
        assert public_code_for(code.lower()) == code


@pytest.mark.parametrize(
    "category, expected_code",
    [
        ("openclaw_unavailable", "ASSISTANT_UNAVAILABLE"),
        ("channel_unavailable", "NOTIFICATION_UNAVAILABLE"),
        ("file_send_unsupported", "FILE_SEND_UNSUPPORTED"),
        ("datasource_test_failed", "DATASOURCE_TEST_FAILED"),
        ("export_report_assets", "REPORT_EXPORT_FAILED"),
        ("pdf_export_failed", "PDF_EXPORT_FAILED"),
        ("profile_strategy_unapproved", "PROFILE_STRATEGY_UNAPPROVED"),
        ("task_not_found", "TASK_NOT_FOUND"),
        ("task_not_cancellable", "TASK_NOT_CANCELLABLE"),
        ("report_not_found", "REPORT_NOT_FOUND"),
        ("report_not_ready", "REPORT_NOT_READY"),
        ("schedule_not_found", "SCHEDULE_NOT_FOUND"),
        ("alert_not_found", "ALERT_NOT_FOUND"),
    ],
)
def test_translate_internal_error_for_user_maps_product_codes(category: str, expected_code: str) -> None:
    failure = translate_internal_error_for_user(RuntimeError("internal failed"), category=category)
    assert failure.code == expected_code
    assert failure.code in PRODUCT_ERROR_CODES


def test_export_chart_asset_failure_is_not_reported_as_assistant_unavailable() -> None:
    failure = translate_internal_error_for_user("export_report_assets: 报告导出失败：未找到可复制的图表资产")

    assert failure.code == "REPORT_EXPORT_FAILED"
    assert "报告导出失败" in failure.user_message
    assert "助手服务暂不可用" not in failure.user_message


def test_final_report_structure_failure_in_stage_batch_is_not_reported_as_assistant_unavailable() -> None:
    failure = translate_internal_error_for_user(
        "stage_batch: final_report_structure: workers=['report_polisher']; "
        "reasons=['report_polisher 非首段禁止 H1 标题']"
    )

    assert failure.code == "REPORT_EXPORT_FAILED"
    assert "助手服务暂不可用" not in failure.user_message


def test_translate_internal_error_for_user_can_infer_profile_unapproved() -> None:
    failure = translate_internal_error_for_user("HK strategy unapproved")
    assert failure.code == "PROFILE_STRATEGY_UNAPPROVED"


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        (
            "openclaw_runtime: provider returned insufficient_quota",
            "报告模型调用失败，模型账户额度不足或计费异常，请到服务商后台处理后重新测试。",
        ),
        (
            "openclaw_runtime: 429 rate limit exceeded",
            "报告模型调用失败，被服务商限流，请稍后重试或降低并发。",
        ),
        (
            "openclaw_runtime: API key expired",
            "报告模型调用失败，API Key 已过期，请到设置更新后重新测试。",
        ),
        (
            "openclaw_runtime: 401 unauthorized",
            "报告模型调用失败，API Key 无效或无法认证，请到设置更新后重新测试。",
        ),
    ],
)
def test_llm_runtime_errors_are_translated_to_plain_messages(raw: str, message: str) -> None:
    failure = translate_internal_error_for_user(raw)

    assert failure.code == "ASSISTANT_UNAVAILABLE"
    assert failure.user_message == message
