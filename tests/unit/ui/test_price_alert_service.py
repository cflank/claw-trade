from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from claw_trade.data_gateway import ui_runtime_checks
from claw_trade.data_gateway.models import (
    FreshnessStatus,
    Market,
    PackDomain,
    PrioritySource,
    ProviderAttempt,
    ProviderCallResult,
    ProviderCallSpec,
    ProviderKind,
    ProviderResult,
    ProviderStatus,
    SourceRole,
)
from claw_trade.ui_backend.price_alert_service import PriceAlertService, UiServiceError
from claw_trade.ui_contracts.enums import MarketProfile


def _fixed_now() -> datetime:
    return datetime(2026, 5, 19, 12, 0, tzinfo=UTC)


def _quote_spec() -> ProviderCallSpec:
    return ProviderCallSpec(
        call_key="market:crypto:quote",
        provider="binance",
        adapter_id="project.binance",
        provider_kind=ProviderKind.PROJECT_EXTENSION,
        provider_config_version="ui-runtime",
        endpoint="crypto_price_historical",
        source_role=SourceRole.MARKET_DATA,
        market=Market.CRYPTO,
        domain=PackDomain.MARKET,
        required=True,
        attempt_required=True,
        coverage_group=None,
        coverage_quorum=None,
        params={"symbol": "BTCUSDT"},
        cache_ttl_seconds=300,
        license_policy_id="public_market_data",
        expected_schema_id="market.ohlcv.v1",
        priority=0,
        priority_source=PrioritySource.SYSTEM_DEFAULT,
        user_preferred=False,
        data_type="price_alert_quote",
        requirement_id="price_alert:BTC:quote",
    )


def _remote_success_provider_result(*, request: Any, spec: ProviderCallSpec) -> ProviderResult:
    started = _fixed_now().isoformat()
    attempt = ProviderAttempt(
        attempt_id="attempt-price-alert-success",
        run_id=request.run_id,
        call_id=request.call_id,
        worker_id=request.worker_id,
        pack=request.domain.value,
        provider=spec.provider,
        adapter_id=spec.adapter_id,
        adapter_kind=spec.provider_kind.value,
        provider_kind=spec.provider_kind,
        provider_config_version=spec.provider_config_version,
        endpoint=spec.endpoint,
        source_role=spec.source_role,
        started_at=started,
        finished_at=started,
        status=ProviderStatus.REMOTE_SUCCESS,
        required=spec.required,
        attempt_required=spec.attempt_required,
        coverage_group=spec.coverage_group,
        coverage_quorum=spec.coverage_quorum,
        priority_source=spec.priority_source,
        user_preferred=spec.user_preferred,
        from_cache=False,
        cache_status=None,
        single_flight_role="owner",
        shared_from_attempt_id=None,
        latency_ms=1,
        row_count=1,
        raw_ref="mongo://openbb_raw_payloads/raw-price-alert",
        normalized_ref="mongo://openbb_normalized/norm-price-alert",
        error_code=None,
        error_message=None,
        schema_id=spec.expected_schema_id,
        license_note="approved",
    )
    return ProviderResult(
        spec=spec,
        status=ProviderStatus.REMOTE_SUCCESS,
        request_id=None,
        requested_at=started,
        latency_ms=1,
        source_role=spec.source_role,
        freshness=FreshnessStatus.FRESH_REMOTE,
        license_note="approved",
        raw_ref=attempt.raw_ref,
        normalized_ref=attempt.normalized_ref,
        rows=({"current_price": 100.0, "percent_change_24h": 5.0},),
        row_count=1,
        cache_receipt=None,
        attempt=attempt,
    )


def test_create_price_alert_only_supports_threshold_and_percent_change() -> None:
    service = PriceAlertService(
        quote_provider=lambda _instrument, _market: {"current_price": 70000, "percent_change": 3.0},
        now_provider=_fixed_now,
    )
    threshold = service.create_price_alert(
        request_id="req-threshold",
        instrument_code="BTC",
        market=MarketProfile.CRYPTO,
        condition={"type": "price_threshold", "operator": "above", "value": 69000},
        notification={"channel": "in_app", "enabled": True},
    )
    percent = service.create_price_alert(
        request_id="req-percent",
        instrument_code="ETH",
        market=MarketProfile.CRYPTO,
        condition={"type": "percent_change", "operator": "up_by", "value": 5, "window": "24h"},
        notification={"channel": "wechat_clawbot", "enabled": True},
    )
    assert threshold.priceAlertId == "alert-1"
    assert percent.priceAlertId == "alert-2"
    assert threshold.condition.type == "price_threshold"
    assert percent.condition.type == "percent_change"

    with pytest.raises(UiServiceError, match="价格阈值或涨跌幅"):
        service.create_price_alert(
            request_id="req-invalid-type",
            instrument_code="BTC",
            market=MarketProfile.CRYPTO,
            condition={"type": "macd_cross", "operator": "above", "value": 1},
        )


@pytest.mark.parametrize(
    ("raw_code", "market", "expected_code"),
    (
        ("SH600519", MarketProfile.CN_A, "600519.SH"),
        ("HK00700", MarketProfile.HK, "00700.HK"),
        ("AAPL.US", MarketProfile.US, "AAPL"),
        ("AR", MarketProfile.CRYPTO, "AR/USDT"),
    ),
)
def test_create_price_alert_normalizes_market_specific_codes(
    raw_code: str,
    market: MarketProfile,
    expected_code: str,
) -> None:
    service = PriceAlertService(
        quote_provider=lambda _instrument, _market: {"current_price": 1, "percent_change": 0},
        now_provider=_fixed_now,
    )

    created = service.create_price_alert(
        request_id=f"req-{expected_code}",
        instrument_code=raw_code,
        market=market,
        condition={"type": "price_threshold", "operator": "above", "value": 1},
    )

    assert created.instrumentCode == expected_code
    assert created.market == market.value


def test_runtime_quote_provider_fails_closed_without_evidence_chain() -> None:
    provider = ui_runtime_checks.build_price_alert_quote_provider(env={}, now_provider=_fixed_now)

    with pytest.raises(RuntimeError, match="evidence_chain_unavailable"):
        provider("BTC", MarketProfile.CRYPTO)


def test_runtime_quote_provider_uses_gate_and_evidence_not_direct_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    spec = _quote_spec()
    called: dict[str, Any] = {"direct_fetch": False, "evidence_execute": False, "gate": False}

    class _Adapter:
        def fetch(self, *_args: object, **_kwargs: object) -> object:
            called["direct_fetch"] = True
            raise AssertionError("direct fetch must not be a price alert success path")

    class _EvidenceHelper:
        def execute(self, *, request: Any, spec: ProviderCallSpec, adapter: object, started_at: str) -> ProviderResult:
            assert adapter is adapter_instance
            assert started_at
            called["evidence_execute"] = True
            return _remote_success_provider_result(request=request, spec=spec)

    adapter_instance = _Adapter()
    monkeypatch.setattr(
        ui_runtime_checks,
        "_select_adapter_spec",
        lambda **_: ui_runtime_checks._AdapterSpec(adapter=adapter_instance, spec=spec),
    )

    def _gate(**kwargs: Any) -> ProviderCallResult:
        called["gate"] = True
        owner_result = kwargs["owner_call"]()
        return ProviderCallResult(
            result_id="price-alert-gate-result",
            spec=kwargs["spec"],
            status=ProviderStatus.REMOTE_SUCCESS,
            rows=(),
            raw_payload_ref=owner_result.raw_ref,
            normalized_ref=owner_result.normalized_ref,
            attempt_ref=owner_result.attempt.attempt_id,
            http_evidence_refs=(),
            cache_entry_ref=None,
            shared_owner_attempt_ref=None,
            data_gaps=(),
            remote_success=True,
            created_at=_fixed_now(),
        )

    monkeypatch.setattr(ui_runtime_checks, "run_provider_call_gate", _gate)
    provider = ui_runtime_checks.build_price_alert_quote_provider(
        env={},
        now_provider=_fixed_now,
        evidence_helper=_EvidenceHelper(),  # type: ignore[arg-type]
        cache_store=object(),  # type: ignore[arg-type]
        rate_limit_store=object(),  # type: ignore[arg-type]
        single_flight=object(),  # type: ignore[arg-type]
        attempt_store=object(),  # type: ignore[arg-type]
    )

    quote = provider("BTC", MarketProfile.CRYPTO)

    assert quote == {
        "current_price": 100.0,
        "percent_change": 5.0,
        "percent_change_24h": 5.0,
    }
    assert called == {"direct_fetch": False, "evidence_execute": True, "gate": True}


def test_run_price_alert_now_triggers_and_closes_by_default() -> None:
    sent: list[str] = []
    service = PriceAlertService(
        quote_provider=lambda _instrument, _market: {"current_price": 71000, "percent_change": 6.2, "percent_change_24h": 6.2},
        notifier=lambda text, _notification: sent.append(text),
        now_provider=_fixed_now,
    )
    alert = service.create_price_alert(
        request_id="req-create",
        instrument_code="BTC",
        market=MarketProfile.CRYPTO,
        condition={"type": "price_threshold", "operator": "above", "value": 70000},
    )
    first = service.run_price_alert_now(request_id="req-run", price_alert_id=alert.priceAlertId)
    second = service.run_price_alert_now(request_id="req-run", price_alert_id=alert.priceAlertId)

    assert first["triggered"] is True
    assert first["alert"].state == "closed"
    assert len(sent) == 1
    assert second["triggered"] is True
    assert len(sent) == 1


def test_paused_alert_skip_checking() -> None:
    service = PriceAlertService(
        quote_provider=lambda _instrument, _market: {"current_price": 100, "percent_change": 1},
        now_provider=_fixed_now,
    )
    alert = service.create_price_alert(
        request_id="req-create",
        instrument_code="AAPL",
        market=MarketProfile.US,
        condition={"type": "price_threshold", "operator": "above", "value": 120},
    )
    service.pause_price_alert(request_id="req-pause", price_alert_id=alert.priceAlertId)
    payload = service.run_price_alert_now(request_id="req-run", price_alert_id=alert.priceAlertId)
    assert payload["triggered"] is False
    assert payload["message"] == "提醒已暂停，暂不检查。"
    assert payload["alert"].state == "paused"


def test_quote_provider_failure_sets_error_state() -> None:
    def raise_quote(_instrument: str, _market: MarketProfile) -> dict[str, float]:
        raise RuntimeError("network down")

    service = PriceAlertService(quote_provider=raise_quote, now_provider=_fixed_now)
    alert = service.create_price_alert(
        request_id="req-create",
        instrument_code="TSLA",
        market=MarketProfile.US,
        condition={"type": "price_threshold", "operator": "below", "value": 200},
    )
    with pytest.raises(UiServiceError) as exc:
        service.run_price_alert_now(request_id="req-run", price_alert_id=alert.priceAlertId)
    assert exc.value.code == "DATASOURCE_TEST_FAILED"
    assert service.get_price_alert(alert.priceAlertId).state == "error"
