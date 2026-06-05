from __future__ import annotations

from pathlib import Path

import pytest

from claw_trade.data_gateway.models import Market
from claw_trade.ui_backend.data_source_settings import (
    DataSourceSettingsService,
    InMemoryDataSourceStore,
    InMemorySecretStore,
    ProviderDisplayDecision,
    ProviderDisplayStatus,
    SUPPORTED_DATA_SOURCE_TYPES,
    allowed_data_source_env_keys,
)
from claw_trade.ui_backend.settings_service import EnvLocalAllowlistWriter, UiBoundaryError


def _show_decision(provider_id: str = "tushare", *, market: Market = Market.CN_A) -> ProviderDisplayDecision:
    return ProviderDisplayDecision(
        provider_id=provider_id,
        market=market,
        display_status=ProviderDisplayStatus.SHOW,
        reason="test main-chain evidence",
        requires_user_credential=True,
        changes_report_or_select_result=True,
        writes_mongo_and_evidence=True,
        enters_domain_pack=True,
        consumed_by_worker_or_strategy=True,
        live_fresh_evidence_ref=f"evidence://main-chain/{provider_id}",
        probe_only=False,
    )


def _service_with_displayed_tushare(**kwargs) -> DataSourceSettingsService:
    return DataSourceSettingsService(display_decision_source=lambda: (_show_decision("tushare"),), **kwargs)


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
    service = _service_with_displayed_tushare()
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
    writer = EnvLocalAllowlistWriter(tmp_path / ".env.local", allowed_keys=allowed_data_source_env_keys())
    service = _service_with_displayed_tushare(
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


def test_save_data_source_persists_rate_limit_settings_to_env(tmp_path: Path) -> None:
    env_path = tmp_path / ".env.local"
    writer = EnvLocalAllowlistWriter(env_path, allowed_keys=allowed_data_source_env_keys())
    service = _service_with_displayed_tushare(
        data_source_store=InMemoryDataSourceStore(),
        secret_store=InMemorySecretStore(),
        env_writer=writer,
        health_tester=lambda _item: {"status": "validated", "message": "连接测试通过。"},
    )

    payload = service.save_data_source_instance(
        {
            "supportedType": "tushare",
            "enabled": True,
            "state": "validated",
            "apiKeyReplacement": "secret-token-abc",
            "rateLimitMaxCalls": "10",
            "rateLimitWindowSeconds": "60",
            "rateLimitSafetyMargin": "1",
            "rateLimitOverflow": "wait",
            "rateLimitWaitTimeoutSeconds": "75",
        },
        request_id="req-rate-limit-env",
    )

    assert payload["rateLimitMaxCalls"] == 10
    assert payload["rateLimitWindowSeconds"] == 60
    assert payload["rateLimitSafetyMargin"] == 1
    assert payload["rateLimitOverflow"] == "wait"
    assert payload["rateLimitWaitTimeoutSeconds"] == 75
    env_text = env_path.read_text(encoding="utf-8")
    assert "TUSHARE_RATE_LIMIT_MAX_CALLS=10" in env_text
    assert "TUSHARE_RATE_LIMIT_WINDOW_SECONDS=60" in env_text
    assert "TUSHARE_RATE_LIMIT_SAFETY_MARGIN=1" in env_text
    assert "TUSHARE_RATE_LIMIT_OVERFLOW=wait" in env_text
    assert "TUSHARE_RATE_LIMIT_WAIT_TIMEOUT_SECONDS=75" in env_text


def test_save_data_source_clears_stale_rate_limit_env_when_fields_are_blank(tmp_path: Path) -> None:
    env_path = tmp_path / ".env.local"
    env_path.write_text(
        "\n".join(
            (
                "TUSHARE_TOKEN=old-secret",
                "TUSHARE_RATE_LIMIT_MAX_CALLS=10",
                "TUSHARE_RATE_LIMIT_WINDOW_SECONDS=60",
                "KEEP_ME=1",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    writer = EnvLocalAllowlistWriter(env_path, allowed_keys=allowed_data_source_env_keys())
    service = _service_with_displayed_tushare(
        data_source_store=InMemoryDataSourceStore(),
        secret_store=InMemorySecretStore(),
        env_writer=writer,
        health_tester=lambda _item: {"status": "validated", "message": "连接测试通过。"},
    )

    payload = service.save_data_source_instance(
        {
            "supportedType": "tushare",
            "enabled": False,
            "state": "draft",
        },
        request_id="req-clear-rate-limit-env",
    )

    assert payload["rateLimitMaxCalls"] is None
    env_text = env_path.read_text(encoding="utf-8")
    assert "TUSHARE_RATE_LIMIT_MAX_CALLS" not in env_text
    assert "TUSHARE_RATE_LIMIT_WINDOW_SECONDS" not in env_text
    assert "TUSHARE_TOKEN=old-secret" in env_text
    assert "KEEP_ME=1" in env_text


def test_reset_data_sources_clears_saved_sources_and_allowed_env_keys(tmp_path: Path) -> None:
    env_path = tmp_path / ".env.local"
    env_path.write_text("TUSHARE_TOKEN=secret\nTUSHARE_HTTP_URL=https://api.tushare.pro\nKEEP_ME=1\n", encoding="utf-8")
    writer = EnvLocalAllowlistWriter(env_path, allowed_keys=allowed_data_source_env_keys())
    store = InMemoryDataSourceStore()
    service = _service_with_displayed_tushare(
        data_source_store=store,
        secret_store=InMemorySecretStore(),
        env_writer=writer,
        health_tester=lambda _item: {"status": "validated", "message": "连接测试通过。"},
    )
    service.save_data_source_instance(
        {
            "supportedType": "tushare",
            "enabled": True,
            "state": "validated",
            "apiKeyReplacement": "secret-token-abc",
        },
        request_id="req-save-before-reset",
    )

    out = service.reset_to_defaults("req-reset")

    assert out["status"] == "reset"
    assert store.list_instances() == ()
    assert out["instances"][0]["state"] == "draft"
    assert out["instances"][0]["apiKeyMasked"] is None
    env_text = env_path.read_text(encoding="utf-8")
    assert "TUSHARE_TOKEN" not in env_text
    assert "TUSHARE_HTTP_URL" not in env_text
    assert "KEEP_ME=1" in env_text


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
    service = _service_with_displayed_tushare(
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

    service = _service_with_displayed_tushare(
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
    service = _service_with_displayed_tushare(
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


def test_save_data_source_enabled_unsupported_probe_is_not_saved_as_success() -> None:
    def _unsupported_probe(_item):
        raise RuntimeError("datasource_test_failed: probe_not_supported")

    store = InMemoryDataSourceStore()
    service = DataSourceSettingsService(
        data_source_store=store,
        secret_store=InMemorySecretStore(),
        health_tester=_unsupported_probe,
        display_decision_source=lambda: (_show_decision("coinglass", market=Market.CRYPTO),),
    )

    with pytest.raises(UiBoundaryError) as exc:
        service.save_data_source_instance(
            {
                "supportedType": "coinglass",
                "displayName": "Coinglass",
                "enabled": True,
                "state": "draft",
                "apiKeyReplacement": "cg-token",
                "endpointUrl": "https://open-api-v4.coinglass.com",
            },
            request_id="req-unsupported-probe-enable",
        )

    assert exc.value.code == "DATASOURCE_TEST_FAILED"
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
    service = _service_with_displayed_tushare(
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
    service = _service_with_displayed_tushare()
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
    service = _service_with_displayed_tushare()
    with pytest.raises(UiBoundaryError) as exc:
        service.test_data_source_instance(
            {
                "supportedType": "tushare",
                "displayName": "Tushare",
            },
            request_id="req-6",
        )
    assert exc.value.code in {"DATASOURCE_TEST_FAILED", "INVALID_INPUT"}


def test_default_display_policy_exposes_probe_backed_api_source_rows() -> None:
    service = DataSourceSettingsService()

    listed = service.list_data_sources()

    assert listed["supportedTypes"] == SUPPORTED_DATA_SOURCE_TYPES
    assert len(listed["instances"]) == len(SUPPORTED_DATA_SOURCE_TYPES)
    assert listed["instances"][0]["supportedType"] == "tushare"
    assert listed["instances"][0]["enabled"] is False
    assert listed["instances"][0]["state"] == "draft"


def test_save_rejects_provider_without_settings_probe() -> None:
    service = DataSourceSettingsService()

    with pytest.raises(UiBoundaryError) as exc:
        service.save_data_source_instance(
            {
                "supportedType": "not_registered_vendor",
                "displayName": "Not Registered Vendor",
                "enabled": False,
                "apiKeyReplacement": "secret-token-abc",
            },
            request_id="req-hidden-provider-save",
        )

    assert exc.value.code == "INVALID_INPUT"
    assert "设置页连接测试" in exc.value.user_message


def test_save_rejects_registered_source_without_settings_probe() -> None:
    service = DataSourceSettingsService()

    with pytest.raises(UiBoundaryError) as exc:
        service.save_data_source_instance(
            {
                "supportedType": "wind",
                "displayName": "Wind",
                "enabled": False,
                "apiKeyReplacement": "secret-token-abc",
            },
            request_id="req-wind-without-probe-save",
        )

    assert exc.value.code == "INVALID_INPUT"
    assert "设置页连接测试" in exc.value.user_message
