from __future__ import annotations

from pathlib import Path

import pytest

from claw_trade.ui_backend.data_source_settings import (
    DataSourceSettingsService,
    InMemoryDataSourceStore,
    InMemorySecretStore,
)
from claw_trade.ui_backend.settings_service import EnvLocalAllowlistWriter, UiBoundaryError


def test_save_data_source_rejects_unknown_custom_http() -> None:
    service = DataSourceSettingsService()
    with pytest.raises(UiBoundaryError) as exc:
        service.save_data_source_instance(
            {
                "supportedType": "custom_http",
                "displayName": "任意 HTTP",
                "enabled": False,
            },
            request_id="req-1",
        )
    assert exc.value.code == "INVALID_INPUT"


def test_save_data_source_requires_test_before_enable() -> None:
    service = DataSourceSettingsService()
    with pytest.raises(UiBoundaryError) as exc:
        service.save_data_source_instance(
            {
                "supportedType": "tushare",
                "displayName": "Tushare",
                "enabled": True,
                "state": "draft",
            },
            request_id="req-2",
        )
    assert exc.value.code in {"DATASOURCE_TEST_FAILED", "INVALID_INPUT"}


def test_save_data_source_masks_secret_and_hides_credential_ref(tmp_path: Path) -> None:
    writer = EnvLocalAllowlistWriter(tmp_path / ".env.local", allowed_keys=("TUSHARE_TOKEN", "TUSHARE_HTTP_URL"))
    service = DataSourceSettingsService(
        data_source_store=InMemoryDataSourceStore(),
        secret_store=InMemorySecretStore(),
        env_writer=writer,
        health_tester=lambda _item: {"status": "validated", "message": "连接测试通过。"},
    )
    payload = service.save_data_source_instance(
        {
            "supportedType": "tushare",
            "group": "cn_a_data",
            "displayName": "Tushare 主源",
            "enabled": True,
            "state": "validated",
            "apiKeyReplacement": "secret-token-abc",
            "endpointUrl": "https://api.tushare.pro",
        },
        request_id="req-3",
    )
    assert payload["apiKeyMasked"] is not None
    assert payload["apiKeyMasked"].startswith("***")
    assert "credentialRef" not in payload
    assert "path" not in payload


def test_save_data_source_replace_only_keeps_old_ref_when_blank() -> None:
    store = InMemoryDataSourceStore()
    existing = store.upsert(
        {
            "id": "source-1",
            "supported_type": "tushare",
            "group": "cn_a_data",
            "display_name": "Tushare",
            "enabled": False,
            "credential_ref": "data_source:old1234",
            "state": "draft",
            "priority": 100,
            "requires_key": True,
        }
    )
    service = DataSourceSettingsService(
        data_source_store=store,
        secret_store=InMemorySecretStore(),
        health_tester=lambda _item: {"status": "validated", "message": "连接测试通过。"},
    )
    saved = service.save_data_source_instance(
        {
            "instanceId": existing["id"],
            "supportedType": "tushare",
            "group": "cn_a_data",
            "displayName": "Tushare",
            "enabled": False,
            "state": "draft",
            "apiKeyReplacement": "   ",
        },
        request_id="req-4",
    )
    assert saved["apiKeyMasked"] == "***1234"


def test_test_data_source_requires_api_key_for_new_key_required_instance() -> None:
    calls = 0

    def _health_tester(_item):
        nonlocal calls
        calls += 1
        return {"status": "validated", "message": "连接测试通过。"}

    service = DataSourceSettingsService(
        data_source_store=InMemoryDataSourceStore(),
        health_tester=_health_tester,
    )
    with pytest.raises(UiBoundaryError) as exc:
        service.test_data_source_instance(
            {
                "supportedType": "tushare",
                "displayName": "Tushare",
            },
            request_id="req-4b",
        )
    assert exc.value.code == "INVALID_INPUT"
    assert calls == 0


def test_save_data_source_enabled_new_instance_requires_api_key_and_does_not_persist() -> None:
    calls = 0

    def _health_tester(_item):
        nonlocal calls
        calls += 1
        return {"status": "validated", "message": "连接测试通过。"}

    store = InMemoryDataSourceStore()
    service = DataSourceSettingsService(
        data_source_store=store,
        health_tester=_health_tester,
    )
    with pytest.raises(UiBoundaryError) as exc:
        service.save_data_source_instance(
            {
                "supportedType": "tushare",
                "displayName": "Tushare",
                "enabled": True,
                "state": "validated",
            },
            request_id="req-4c",
        )
    assert exc.value.code == "INVALID_INPUT"
    assert calls == 0
    assert store.list_instances() == ()


def test_test_data_source_allows_replace_only_with_existing_credential_ref() -> None:
    store = InMemoryDataSourceStore()
    existing = store.upsert(
        {
            "id": "source-2",
            "supported_type": "tushare",
            "group": "cn_a_data",
            "display_name": "Tushare",
            "enabled": False,
            "credential_ref": "data_source:old9876",
            "state": "draft",
            "priority": 100,
            "requires_key": True,
        }
    )
    service = DataSourceSettingsService(
        data_source_store=store,
        health_tester=lambda _item: {"status": "validated", "message": "连接测试通过。"},
    )
    out = service.test_data_source_instance(
        {
            "instanceId": existing["id"],
            "supportedType": "tushare",
            "displayName": "Tushare",
            "apiKeyReplacement": " ",
        },
        request_id="req-4d",
    )
    assert out["state"] == "validated"
    assert out["canEnable"] is True


def test_test_data_source_rejects_custom_mapping_payload() -> None:
    service = DataSourceSettingsService()
    with pytest.raises(UiBoundaryError) as exc:
        service.test_data_source_instance(
            {
                "supportedType": "tushare",
                "displayName": "Tushare",
                "customJsonMapping": {"foo": "bar"},
            },
            request_id="req-5",
        )
    assert exc.value.code == "INVALID_INPUT"


def test_default_health_tester_does_not_fake_validated() -> None:
    service = DataSourceSettingsService()
    with pytest.raises(UiBoundaryError) as exc:
        service.test_data_source_instance(
            {
                "supportedType": "tushare",
                "displayName": "Tushare",
            },
            request_id="req-6",
        )
    assert exc.value.code in {"DATASOURCE_TEST_FAILED", "INVALID_INPUT"}
