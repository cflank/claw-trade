from __future__ import annotations

from enum import Enum


class MarketProfile(str, Enum):
    CN_A = "CN_A"
    US = "US"
    HK = "HK"
    CRYPTO = "CRYPTO"


class ChatContextKind(str, Enum):
    NORMAL_CHAT = "normal_chat"
    INTENT_CONFIRMING = "intent_confirming"
    TASK_FOLLOWING = "task_following"
    REPORT_READING = "report_reading"


class IntentKind(str, Enum):
    REPORT = "report"
    SCHEDULED_REPORT = "scheduled_report"
    SCHEDULED_SELECTION = "scheduled_selection"
    PRICE_ALERT = "price_alert"


class ReportTaskStatus(str, Enum):
    DRAFT = "draft"
    CONFIRMED = "confirmed"
    QUEUED = "queued"
    RUNNING = "running"
    SAVING_REPORT = "saving_report"
    PDF_EXPORTING = "pdf_exporting"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class UserVisibleSeverity(str, Enum):
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"
