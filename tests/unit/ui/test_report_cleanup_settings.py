from __future__ import annotations

from typing import Any

import pytest
from claw_trade.data_gateway.settings_store import MongoReportCleanupSettingsStore
from claw_trade.ui_backend.report_cleanup_settings import (
    ALLOWED_RETENTION_DAYS,
    ReportCleanupSettingsService,
)
from claw_trade.ui_backend.settings_service import UiBoundaryError


def test_load_settings_defaults_to_7_days(tmp_path) -> None:
    service = ReportCleanupSettingsService(json_path=tmp_path / ".ui-report-cleanup-settings.json")

    assert service.load_settings() == {"reportRetentionDays": 7}


def test_json_fallback_persists_and_resets(tmp_path) -> None:
    path = tmp_path / "runs" / ".ui-report-cleanup-settings.json"
    service = ReportCleanupSettingsService(json_path=path)

    assert service.save_settings(14) == {"reportRetentionDays": 14}
    assert ReportCleanupSettingsService(json_path=path).load_settings() == {"reportRetentionDays": 14}

    assert service.reset_to_defaults() == {"reportRetentionDays": 7}
    assert not path.exists()
    assert service.load_settings() == {"reportRetentionDays": 7}


def test_only_allowed_retention_days_are_accepted(tmp_path) -> None:
    service = ReportCleanupSettingsService(json_path=tmp_path / ".ui-report-cleanup-settings.json")

    for days in ALLOWED_RETENTION_DAYS:
        assert service.save_settings(days) == {"reportRetentionDays": days}

    with pytest.raises(UiBoundaryError) as exc:
        service.save_settings(21)

    assert exc.value.code == "INVALID_INPUT"


def test_invalid_saved_payload_is_rejected(tmp_path) -> None:
    path = tmp_path / ".ui-report-cleanup-settings.json"
    path.write_text('{"reportRetentionDays": 21}\n', encoding="utf-8")

    with pytest.raises(UiBoundaryError) as exc:
        ReportCleanupSettingsService(json_path=path).load_settings()

    assert exc.value.code == "INVALID_INPUT"


def test_fractional_saved_retention_days_is_rejected(tmp_path) -> None:
    path = tmp_path / ".ui-report-cleanup-settings.json"
    path.write_text('{"reportRetentionDays": 14.9}\n', encoding="utf-8")

    with pytest.raises(UiBoundaryError) as exc:
        ReportCleanupSettingsService(json_path=path).load_settings()

    assert exc.value.code == "INVALID_INPUT"


def test_non_object_json_settings_payload_is_rejected(tmp_path) -> None:
    path = tmp_path / ".ui-report-cleanup-settings.json"
    path.write_text("[7]\n", encoding="utf-8")

    with pytest.raises(UiBoundaryError) as exc:
        ReportCleanupSettingsService(json_path=path).load_settings()

    assert exc.value.code == "INVALID_INPUT"


def test_mongo_store_uses_single_fixed_record() -> None:
    collection = _MemoryCollection()
    store = MongoReportCleanupSettingsStore(collection)

    store.write({"reportRetentionDays": 30})

    assert store.read() == {"reportRetentionDays": 30}
    assert collection.docs["report_cleanup_settings"]["_id"] == "report_cleanup_settings"
    assert collection.docs["report_cleanup_settings"]["schemaVersion"] == "ui-report-cleanup-settings-v1"

    store.clear()
    assert store.read() == {}


def test_service_uses_mongo_store_when_configured(tmp_path) -> None:
    store = MongoReportCleanupSettingsStore(_MemoryCollection())
    path = tmp_path / ".ui-report-cleanup-settings.json"
    service = ReportCleanupSettingsService(store=store, json_path=path)

    assert service.save_settings(30) == {"reportRetentionDays": 30}

    assert not path.exists()
    assert service.load_settings() == {"reportRetentionDays": 30}


def test_corrupt_mongo_payload_is_rejected(tmp_path) -> None:
    collection = _MemoryCollection()
    collection.docs["report_cleanup_settings"] = {
        "_id": "report_cleanup_settings",
        "payload": [7],
    }
    service = ReportCleanupSettingsService(
        store=MongoReportCleanupSettingsStore(collection),
        json_path=tmp_path / ".ui-report-cleanup-settings.json",
    )

    with pytest.raises(UiBoundaryError) as exc:
        service.load_settings()

    assert exc.value.code == "INVALID_INPUT"


class _MemoryCollection:
    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}

    def find_one(self, query: dict[str, Any]) -> dict[str, Any] | None:
        doc = self.docs.get(str(query["_id"]))
        return dict(doc) if doc is not None else None

    def replace_one(self, query: dict[str, Any], doc: dict[str, Any], *, upsert: bool) -> None:
        assert upsert is True
        self.docs[str(query["_id"])] = dict(doc)

    def delete_one(self, query: dict[str, Any]) -> None:
        self.docs.pop(str(query["_id"]), None)
