from __future__ import annotations

from dataclasses import dataclass

from claw_trade.ui_contracts.constants import PRODUCT_ERROR_CODES
from claw_trade.ui_contracts.enums import UserVisibleSeverity


@dataclass(frozen=True)
class UserFacingFailure:
    code: str
    user_message: str
    severity: UserVisibleSeverity = UserVisibleSeverity.ERROR


_CATEGORY_TO_CODE = {
    **{code.lower(): code for code in PRODUCT_ERROR_CODES},
    "invalid_input": "INVALID_INPUT",
    "confirmation_required": "CONFIRMATION_REQUIRED",
    "draft_expired": "DRAFT_EXPIRED",
    "queue_full": "QUEUE_FULL",
    "duplicate_task": "DUPLICATE_TASK",
    "task_not_found": "TASK_NOT_FOUND",
    "task_not_cancellable": "TASK_NOT_CANCELLABLE",
    "report_not_found": "REPORT_NOT_FOUND",
    "report_not_ready": "REPORT_NOT_READY",
    "report_context_too_long": "REPORT_CONTEXT_TOO_LONG",
    "notification_unavailable": "NOTIFICATION_UNAVAILABLE",
    "file_send_unsupported": "FILE_SEND_UNSUPPORTED",
    "openclaw_unavailable": "ASSISTANT_UNAVAILABLE",
    "openclaw_config_unavailable": "ASSISTANT_UNAVAILABLE",
    "openclaw_model_unavailable": "ASSISTANT_UNAVAILABLE",
    "assistant_unavailable": "ASSISTANT_UNAVAILABLE",
    "channel_unavailable": "NOTIFICATION_UNAVAILABLE",
    "workflow_failed": "ASSISTANT_UNAVAILABLE",
    "export_report_assets": "REPORT_EXPORT_FAILED",
    "export_chart_cleanup": "REPORT_EXPORT_FAILED",
    "export_missing_material": "REPORT_EXPORT_FAILED",
    "final_report_structure": "REPORT_EXPORT_FAILED",
    "datasource_test_failed": "DATASOURCE_TEST_FAILED",
    "data_source_test_failed": "DATASOURCE_TEST_FAILED",
    "pdf_export_failed": "PDF_EXPORT_FAILED",
    "profile_strategy_unapproved": "PROFILE_STRATEGY_UNAPPROVED",
    "license_blocked": "LICENSE_BLOCKED",
    "schedule_not_found": "SCHEDULE_NOT_FOUND",
    "alert_not_found": "ALERT_NOT_FOUND",
    "unauthorized": "UNAUTHORIZED",
    "conflict": "CONFLICT",
}

_CODE_TO_MESSAGE = {
    "INVALID_INPUT": "输入内容暂不可用，请检查后重试。",
    "CONFIRMATION_REQUIRED": "请先确认后再继续。",
    "DRAFT_EXPIRED": "确认草稿已过期，请重新提交。",
    "QUEUE_FULL": "报告队列已满，请稍后再试。",
    "DUPLICATE_TASK": "已有同标的报告在队列中，已复用。",
    "TASK_NOT_FOUND": "没有找到对应任务，请刷新后重试。",
    "TASK_NOT_CANCELLABLE": "报告正在生成，当前不能中途取消。",
    "REPORT_NOT_FOUND": "没有找到这份报告。",
    "REPORT_NOT_READY": "报告尚未准备好，请稍后再试。",
    "REPORT_CONTEXT_TOO_LONG": "当前报告过长，暂时无法追问。",
    "NOTIFICATION_UNAVAILABLE": "微信通知暂不可用，请在设备界面查看。",
    "FILE_SEND_UNSUPPORTED": "完整报告文件暂不可发送，请在设备界面查看。",
    "ASSISTANT_UNAVAILABLE": "助手服务暂不可用，请稍后重试。",
    "REPORT_MODEL_NOT_READY": "报告模型尚未就绪，请先在设置中完成测试。",
    "MAINTENANCE_LOCKED": "系统正在维护中，请稍后再试。",
    "DATASOURCE_TEST_FAILED": "数据源连接或鉴权失败，请到设置页更新后重试。",
    "REPORT_EXPORT_FAILED": "报告导出失败：缺少图表或报告资产，请检查数据源后重试。",
    "PDF_EXPORT_FAILED": "PDF 暂不可用，完整报告仍可在设备界面查看。",
    "PROFILE_STRATEGY_UNAPPROVED": "当前市场策略尚未批准。",
    "LICENSE_BLOCKED": "设备授权已失效，请在授权页修复后重试。",
    "SCHEDULE_NOT_FOUND": "没有找到对应定时任务。",
    "ALERT_NOT_FOUND": "没有找到对应价格提醒。",
    "UNAUTHORIZED": "当前会话无权限执行该操作。",
    "CONFLICT": "当前状态已变更，请刷新后重试。",
}

_PUBLIC_ERROR_CODE_SET = frozenset(PRODUCT_ERROR_CODES)


def public_code_for(category: str) -> str:
    normalized = category.strip().lower()
    direct = category.strip().upper()
    if direct in _PUBLIC_ERROR_CODE_SET:
        return direct
    code = _CATEGORY_TO_CODE.get(normalized)
    if code is None:
        return "ASSISTANT_UNAVAILABLE"
    return code


def translate_internal_error_for_user(error: Exception | str, *, category: str | None = None) -> UserFacingFailure:
    raw_message = str(error).strip()
    normalized_category = _infer_category(raw_message, category)
    code = public_code_for(normalized_category)
    if code not in _PUBLIC_ERROR_CODE_SET:
        code = "ASSISTANT_UNAVAILABLE"
    message = _data_tool_message(raw_message)
    if message is not None:
        return UserFacingFailure(code=code, user_message=message)
    message = _llm_runtime_message(raw_message, normalized_category)
    if message is not None:
        code = "ASSISTANT_UNAVAILABLE"
    else:
        message = _CODE_TO_MESSAGE.get(code, _CODE_TO_MESSAGE["ASSISTANT_UNAVAILABLE"])
    return UserFacingFailure(code=code, user_message=message)


def translateInternalErrorForUser(error: Exception | str, *, category: str | None = None) -> UserFacingFailure:
    return translate_internal_error_for_user(error, category=category)


def _infer_category(raw_message: str, category: str | None) -> str:
    if category:
        return category
    lowered = raw_message.lower()
    if "confirm" in lowered and "required" in lowered:
        return "confirmation_required"
    if "draft" in lowered and "expire" in lowered:
        return "draft_expired"
    if "task" in lowered and "not found" in lowered:
        return "task_not_found"
    if "not cancellable" in lowered or ("task" in lowered and "cancel" in lowered and "cannot" in lowered):
        return "task_not_cancellable"
    if "report" in lowered and "not found" in lowered:
        return "report_not_found"
    if "report" in lowered and "not ready" in lowered:
        return "report_not_ready"
    if "profile" in lowered and "unapproved" in lowered:
        return "profile_strategy_unapproved"
    if "hk" in lowered and "unapproved" in lowered:
        return "profile_strategy_unapproved"
    if "crypto" in lowered and "unapproved" in lowered:
        return "profile_strategy_unapproved"
    if "schedule" in lowered and "not found" in lowered:
        return "schedule_not_found"
    if "alert" in lowered and "not found" in lowered:
        return "alert_not_found"
    if "queue" in lowered and "full" in lowered:
        return "queue_full"
    if "context" in lowered and "too long" in lowered:
        return "report_context_too_long"
    if "pdf" in lowered:
        return "pdf_export_failed"
    if lowered.startswith("export_report_assets:") or "图表资产" in raw_message:
        return "export_report_assets"
    if lowered.startswith("export_chart_cleanup:"):
        return "export_chart_cleanup"
    if lowered.startswith("export_missing_material:"):
        return "export_missing_material"
    if lowered.startswith("final_report_structure:") or "final_report_structure:" in lowered:
        return "final_report_structure"
    if "datasource" in lowered or "data source" in lowered:
        return "datasource_test_failed"
    if "channel" in lowered or "wechat" in lowered:
        return "channel_unavailable"
    if "duplicate" in lowered:
        return "duplicate_task"
    if "unauthorized" in lowered:
        return "unauthorized"
    if "conflict" in lowered:
        return "conflict"
    return "assistant_unavailable"


def _data_tool_message(raw_message: str) -> str | None:
    lowered = raw_message.lower()
    if not (
        "claw_request_data" in lowered
        or "数据工具" in raw_message
        or "tool_calls" in lowered
        or "data tool" in lowered
    ):
        return None
    if "链上" in raw_message:
        subject = "链上数据"
    elif "行情" in raw_message:
        subject = "行情数据"
    else:
        subject = "报告数据"
    if "超时" in raw_message or "timeout" in lowered or "timed out" in lowered:
        return f"{subject}请求超时，请稍后重试。"
    return f"{subject}请求失败，请检查数据源后重试。"


def _llm_runtime_message(raw_message: str, category: str) -> str | None:
    lowered = raw_message.lower()
    llm_context = category in {
        "assistant_unavailable",
        "workflow_failed",
        "openclaw_unavailable",
        "openclaw_config_unavailable",
        "openclaw_model_unavailable",
        "openclaw_runtime",
    } or any(
        token in lowered
        for token in (
            "openclaw",
            "llm",
            "model",
            "chat completion",
            "completion",
            "api key",
            "insufficient_quota",
            "billing",
            "quota",
            "rate_limit",
            "rate limit",
        )
    )
    if not llm_context:
        return None
    if "missing_credential" in lowered or "api key missing" in lowered or "missing api key" in lowered:
        return "报告模型调用失败，请填写 API Key 后重新测试。"
    if "invalid_expires" in lowered or ("expired" in lowered and ("api key" in lowered or "credential" in lowered)):
        return "报告模型调用失败，API Key 已过期，请到设置更新后重新测试。"
    if any(token in lowered for token in ("insufficient_quota", "billing", "quota", "balance", "credit", "payment_required", "402")):
        return "报告模型调用失败，模型账户额度不足或计费异常，请到服务商后台处理后重新测试。"
    if any(token in lowered for token in ("rate_limit", "rate limit", "too many requests", "429")):
        return "报告模型调用失败，被服务商限流，请稍后重试或降低并发。"
    if any(token in lowered for token in ("invalid_api_key", "invalid api key", "unauthorized", "authentication", "forbidden", "401", "403")):
        return "报告模型调用失败，API Key 无效或无法认证，请到设置更新后重新测试。"
    if "timeout" in lowered or "timed out" in lowered:
        return "报告模型调用超时，请稍后重试。"
    return None
