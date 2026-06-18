from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, Mapping

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from claw_trade.ui_backend.openclaw_cron_adapter import OpenClawCronAdapter
from claw_trade.ui_backend.price_alert_scan_service import PriceAlertScanSummary
from claw_trade.ui_backend.price_alert_service import PriceAlertService, UiServiceError
from claw_trade.ui_backend.scheduled_work_runner import ScheduledWorkRunner
from claw_trade.ui_backend.scheduled_work_store import InMemoryScheduledWorkStore
from claw_trade.ui_contracts.enums import MarketProfile
from claw_trade.web.routes_ui import router


def _fixed_now() -> datetime:
    return datetime(2026, 5, 19, 12, 0, tzinfo=UTC)


class _FakeCronGateway:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def cron_add(self, params: Mapping[str, Any]) -> dict[str, Any]:
        payload = dict(params)
        self.calls.append({"method": "cron.add", "params": payload})
        return {"jobId": payload["name"]}

    def cron_update(self, params: Mapping[str, Any]) -> dict[str, Any]:
        self.calls.append({"method": "cron.update", "params": dict(params)})
        return {"updated": True}

    def cron_remove(self, *, job_id: str) -> dict[str, Any]:
        self.calls.append({"method": "cron.remove", "params": {"jobId": job_id}})
        return {"removed": True}

    def cron_run(self, *, job_id: str, idempotency_key: str | None = None) -> dict[str, Any]:
        params = {"jobId": job_id}
        if idempotency_key:
            params["idempotencyKey"] = idempotency_key
        self.calls.append({"method": "cron.run", "params": params})
        return {"runId": "cron-run-1"}

    def cron_list(self, params: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
        self.calls.append({"method": "cron.list", "params": dict(params or {})})
        return []

    def cron_status(self, *, job_id: str) -> dict[str, Any]:
        self.calls.append({"method": "cron.status", "params": {"jobId": job_id}})
        return {"jobId": job_id, "status": "active"}

    def cron_runs(self, *, job_id: str, limit: int | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"jobId": job_id}
        if limit is not None:
            params["limit"] = limit
        self.calls.append({"method": "cron.runs", "params": params})
        return []


def _service(store: InMemoryScheduledWorkStore, gateway: _FakeCronGateway) -> PriceAlertService:
    return PriceAlertService(
        quote_provider=lambda _instrument, _market: {"current_price": 1, "percent_change": 0},
        store=store,
        cron_adapter=OpenClawCronAdapter(gateway),
        now_provider=_fixed_now,
    )


def test_crypto_alert_creates_scan_bucket_and_one_cron_job_for_bucket() -> None:
    store = InMemoryScheduledWorkStore()
    gateway = _FakeCronGateway()
    service = _service(store, gateway)

    first = service.create_price_alert(
        request_id="req-1",
        instrument_code="BTC",
        market=MarketProfile.CRYPTO,
        condition={"type": "price_threshold", "operator": "above", "value": 1},
    )
    second = service.create_price_alert(
        request_id="req-2",
        instrument_code="ETH",
        market=MarketProfile.CRYPTO,
        condition={"type": "price_threshold", "operator": "above", "value": 1},
    )

    bucket = store.get_scan_bucket("CRYPTO:3m")
    assert bucket is not None
    assert bucket.enabled is True
    assert bucket.openclaw_cron_job_id == "price-alert-scan:CRYPTO:3m"
    assert first.priceAlertId == "alert-1"
    assert second.priceAlertId == "alert-2"
    assert [call["method"] for call in gateway.calls] == ["cron.add"]
    assert gateway.calls[0]["params"] == {
        "name": "price-alert-scan:CRYPTO:3m",
        "schedule": {"kind": "every", "everyMs": 180_000},
        "enabled": True,
        "agentId": "price_alert_scan_worker",
        "sessionTarget": "isolated",
        "wakeMode": "now",
        "payload": {
            "kind": "agentTurn",
            "message": (
                "Call `claw-trade-scheduled-work-wake` exactly once with this JSON payload and no other tool calls:\n"
                '{"kind":"price_alert_scan","bucketKey":"CRYPTO:3m","cronRunId":"auto"}\n'
                "Do not compare prices, write investment commentary, or fabricate quote results."
            ),
            "toolsAllow": ["claw-trade-scheduled-work-wake"],
            "timeoutSeconds": 60,
        },
        "delivery": {"mode": "none"},
    }


def test_cn_a_alert_creates_cn_a_scan_bucket() -> None:
    store = InMemoryScheduledWorkStore()
    gateway = _FakeCronGateway()
    service = _service(store, gateway)

    service.create_price_alert(
        request_id="req-cn",
        instrument_code="SH600519",
        market=MarketProfile.CN_A,
        condition={"type": "price_threshold", "operator": "above", "value": 1},
    )

    bucket = store.get_scan_bucket("CN_A:3m")
    assert bucket is not None
    assert bucket.enabled is True
    assert bucket.openclaw_cron_job_id == "price-alert-scan:CN_A:3m"
    assert gateway.calls[0]["params"]["payload"]["kind"] == "agentTurn"
    assert '"bucketKey":"CN_A:3m"' in gateway.calls[0]["params"]["payload"]["message"]


def test_us_alert_creates_disabled_skipped_bucket_without_cron_job() -> None:
    store = InMemoryScheduledWorkStore()
    gateway = _FakeCronGateway()
    service = _service(store, gateway)

    created = service.create_price_alert(
        request_id="req-us",
        instrument_code="AAPL.US",
        market=MarketProfile.US,
        condition={"type": "price_threshold", "operator": "above", "value": 1},
    )

    bucket = store.get_scan_bucket("US:3m")
    assert created.market == MarketProfile.US.value
    assert bucket is not None
    assert bucket.enabled is False
    assert bucket.openclaw_cron_job_id is None
    assert bucket.skipped_reason == "quote_calendar_live_evidence_required"
    assert gateway.calls == []


def test_hk_alert_returns_invalid_input_and_creates_no_alert_or_cron_job() -> None:
    store = InMemoryScheduledWorkStore()
    gateway = _FakeCronGateway()
    service = _service(store, gateway)

    with pytest.raises(UiServiceError) as exc:
        service.create_price_alert(
            request_id="req-hk",
            instrument_code="HK00700",
            market=MarketProfile.HK,
            condition={"type": "price_threshold", "operator": "above", "value": 1},
        )

    assert exc.value.code == "INVALID_INPUT"
    assert store.list_price_alerts() == []
    assert store.get_scan_bucket("HK:3m") is None
    assert gateway.calls == []


def test_cron_adapter_maps_basic_job_operations() -> None:
    gateway = _FakeCronGateway()
    adapter = OpenClawCronAdapter(gateway)

    adapter.update_job(job_id="job-1", patch={"enabled": False})
    adapter.remove_job(job_id="job-1")
    adapter.run_job(job_id="job-1", idempotency_key="req-run")
    adapter.list_jobs({"namePrefix": "price-alert-scan"})
    adapter.status(job_id="job-1")
    adapter.runs(job_id="job-1", limit=3)

    assert gateway.calls == [
        {"method": "cron.update", "params": {"jobId": "job-1", "enabled": False}},
        {"method": "cron.remove", "params": {"jobId": "job-1"}},
        {"method": "cron.run", "params": {"jobId": "job-1", "idempotencyKey": "req-run"}},
        {"method": "cron.list", "params": {"namePrefix": "price-alert-scan"}},
        {"method": "cron.status", "params": {"jobId": "job-1"}},
        {"method": "cron.runs", "params": {"jobId": "job-1", "limit": 3}},
    ]


def test_cron_adapter_extracts_openclaw_job_id_from_job_payload() -> None:
    class Gateway(_FakeCronGateway):
        def cron_add(self, params: Mapping[str, Any]) -> dict[str, Any]:
            self.calls.append({"method": "cron.add", "params": dict(params)})
            return {"job": {"id": "job-openclaw-1"}}

    adapter = OpenClawCronAdapter(Gateway())

    created = adapter.add_job(
        name="price-alert-scan:CRYPTO:3m",
        schedule={"kind": "every", "everyMs": 180_000},
        agent_id="price_alert_scan_worker",
        payload={"kind": "agentTurn", "message": "wake"},
    )

    assert created.openclaw_cron_job_id == "job-openclaw-1"


class _FakeScanService:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[dict[str, str]] = []

    def scan_bucket(self, bucket_key: str, *, cron_run_id: str) -> PriceAlertScanSummary:
        self.calls.append({"bucketKey": bucket_key, "cronRunId": cron_run_id})
        if self.fail:
            raise RuntimeError("scan failed")
        return PriceAlertScanSummary(
            scan_run_id=f"price-alert-scan:{bucket_key}:{cron_run_id}",
            bucket_key=bucket_key,
            cron_run_id=cron_run_id,
            active_alert_count=1,
            grouped_quote_count=1,
            triggered_alert_count=0,
            skipped_alert_count=0,
            failed_alert_count=0,
            quote_evidence_refs=("quote://btc",),
            started_at="2026-06-17T12:00:00Z",
            finished_at="2026-06-17T12:00:01Z",
        )


def _client_for_runner(runner: ScheduledWorkRunner, *, token: str = "secret") -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.state.ui_services = SimpleNamespace(scheduled_work_runner=runner)
    app.state.scheduled_work_internal_token = token
    return TestClient(app)


def _prefixed_client_for_runner(runner: ScheduledWorkRunner, *, token: str = "secret") -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/api/ui")
    app.state.ui_services = SimpleNamespace(scheduled_work_runner=runner)
    app.state.scheduled_work_internal_token = token
    return TestClient(app)


def test_internal_cron_wake_route_requires_internal_token() -> None:
    client = _client_for_runner(ScheduledWorkRunner(price_alert_scan_service=_FakeScanService()))

    response = client.post(
        "/internal/scheduled-work/cron-wake",
        json={"kind": "price_alert_scan", "bucketKey": "CRYPTO:3m", "cronRunId": "cron-run-1"},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"


def test_internal_cron_wake_route_rejects_unknown_kind() -> None:
    client = _client_for_runner(ScheduledWorkRunner(price_alert_scan_service=_FakeScanService()))

    response = client.post(
        "/internal/scheduled-work/cron-wake",
        json={"kind": "unknown", "bucketKey": "CRYPTO:3m", "cronRunId": "cron-run-1"},
        headers={"x-claw-trade-internal-token": "secret"},
    )

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_INPUT"


def test_internal_cron_wake_route_calls_price_alert_scan_service() -> None:
    scan_service = _FakeScanService()
    client = _client_for_runner(ScheduledWorkRunner(price_alert_scan_service=scan_service))

    response = client.post(
        "/internal/scheduled-work/cron-wake",
        json={"kind": "price_alert_scan", "bucketKey": "CRYPTO:3m", "cronRunId": "cron-run-1"},
        headers={"x-claw-trade-internal-token": "secret"},
    )

    assert response.status_code == 200
    assert scan_service.calls == [{"bucketKey": "CRYPTO:3m", "cronRunId": "cron-run-1"}]
    assert response.json() == {
        "kind": "price_alert_scan",
        "bucketKey": "CRYPTO:3m",
        "cronRunId": "cron-run-1",
        "status": "ok",
        "summary": {
            "scan_run_id": "price-alert-scan:CRYPTO:3m:cron-run-1",
            "bucket_key": "CRYPTO:3m",
            "cron_run_id": "cron-run-1",
            "active_alert_count": 1,
            "grouped_quote_count": 1,
            "triggered_alert_count": 0,
            "skipped_alert_count": 0,
            "failed_alert_count": 0,
            "quote_evidence_refs": ["quote://btc"],
            "started_at": "2026-06-17T12:00:00Z",
            "finished_at": "2026-06-17T12:00:01Z",
            "last_error_message": None,
        },
    }


def test_internal_cron_wake_route_works_with_research_ui_api_prefix() -> None:
    scan_service = _FakeScanService()
    client = _prefixed_client_for_runner(ScheduledWorkRunner(price_alert_scan_service=scan_service))

    response = client.post(
        "/api/ui/internal/scheduled-work/cron-wake",
        json={"kind": "price_alert_scan", "bucketKey": "CRYPTO:3m", "cronRunId": "cron-run-1"},
        headers={"x-claw-trade-internal-token": "secret"},
    )

    assert response.status_code == 200
    assert scan_service.calls == [{"bucketKey": "CRYPTO:3m", "cronRunId": "cron-run-1"}]


def test_internal_cron_wake_route_returns_error_status_with_business_id() -> None:
    client = _client_for_runner(ScheduledWorkRunner(price_alert_scan_service=_FakeScanService(fail=True)))

    response = client.post(
        "/internal/scheduled-work/cron-wake",
        json={"kind": "price_alert_scan", "bucketKey": "CRYPTO:3m", "cronRunId": "cron-run-1"},
        headers={"x-claw-trade-internal-token": "secret"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "kind": "price_alert_scan",
        "bucketKey": "CRYPTO:3m",
        "cronRunId": "cron-run-1",
        "status": "error",
        "error": {"message": "scan failed"},
    }
