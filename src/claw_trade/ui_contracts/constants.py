from __future__ import annotations

PRODUCT_ERROR_CODES = (
    "INVALID_INPUT",
    "CONFIRMATION_REQUIRED",
    "DRAFT_EXPIRED",
    "QUEUE_FULL",
    "DUPLICATE_TASK",
    "TASK_NOT_FOUND",
    "TASK_NOT_CANCELLABLE",
    "REPORT_NOT_FOUND",
    "REPORT_NOT_READY",
    "REPORT_CONTEXT_TOO_LONG",
    "NOTIFICATION_UNAVAILABLE",
    "FILE_SEND_UNSUPPORTED",
    "ASSISTANT_UNAVAILABLE",
    "REPORT_MODEL_NOT_READY",
    "DATASOURCE_TEST_FAILED",
    "REPORT_EXPORT_FAILED",
    "PDF_EXPORT_FAILED",
    "PROFILE_STRATEGY_UNAPPROVED",
    "SCHEDULE_NOT_FOUND",
    "ALERT_NOT_FOUND",
    "UNAUTHORIZED",
    "CONFLICT",
)

FIRST_VERSION_NOT_IN_SCOPE = (
    "mobile_layout",
    "parallel_full_report_generation",
    "hourly_full_report_schedule",
    "brief_summary_worker",
    "failed_task_management_page",
    "unknown_http_json_data_source",
    "wechat_protocol_in_claw_trade",
)
