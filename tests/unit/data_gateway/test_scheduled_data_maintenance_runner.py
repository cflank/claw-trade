from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from claw_trade.data_gateway.maintenance import (
    DailyBarMaintenanceGap,
    InMemoryMaintenanceJobRepository,
    ScheduledDataMaintenanceRunner,
)
from claw_trade.data_gateway.models import DataResultStatus
from claw_trade.data_gateway.public_api import PublicDataRequest
from claw_trade.data_gateway.warehouse.repository import DatasetRepository


class FakeDataAPI:
    def __init__(self, *, status: object = "ready") -> None:
        self.requests: list[Any] = []
        self.status = status

    def request_data(self, requests: tuple[Any, ...]) -> list[object]:
        self.requests.extend(requests)
        return [
            SimpleNamespace(
                request_id=request.request_id,
                dataset_refs=(f"dataset:{index}",),
                raw_refs=(),
                attempt_refs=(f"attempt:{index}",),
                gaps=(),
                status=self.status,
            )
            for index, request in enumerate(requests, start=1)
        ]


class FakePlanner:
    def find_gaps(self, *, market: str, dataset_scope: str, as_of: object) -> tuple[DailyBarMaintenanceGap, ...]:
        assert market == "CN_A"
        assert dataset_scope == "daily_bar"
        assert as_of == datetime(2026, 6, 18, 9, 0, tzinfo=UTC)
        return (
            DailyBarMaintenanceGap(market="CN_A", instrument="000001.SZ", missing_day=date(2026, 6, 17)),
            DailyBarMaintenanceGap(market="CN_A", instrument="600000.SH", missing_day=date(2026, 6, 17)),
        )


def test_cn_a_eod_runs_existing_daily_incremental_maintenance_job() -> None:
    api = FakeDataAPI()
    jobs = InMemoryMaintenanceJobRepository()
    runner = ScheduledDataMaintenanceRunner(
        data_api=api,
        job_repository=jobs,
        dataset_repository=DatasetRepository(),
        incremental_planner=FakePlanner(),
        now_provider=lambda: datetime(2026, 6, 18, 9, 0, tzinfo=UTC),
    )

    result = runner.run(
        market="CN_A",
        job_kind="eod",
        cron_run_id="cron-run-1",
        maintenance_job_id="job-cn-a-eod",
    )

    assert result.status == "succeeded"
    assert len(api.requests) == 2
    assert all(isinstance(request, PublicDataRequest) for request in api.requests)
    saved = jobs.get("job-cn-a-eod")
    assert saved is not None
    assert saved.requested_by == "openclaw_cron"


def test_crypto_kline_refresh_uses_data_layer_daily_bar_scope_without_bulk_downloader() -> None:
    dataset_repository = DatasetRepository()
    dataset_repository.insert_normalized(
        {
            "dataset": "daily_bar",
            "market": "CRYPTO",
            "symbol_id": "BTCUSDT",
            "universe_ref": None,
            "granularity": "daily",
            "period_start": date(2026, 6, 16),
            "period_end": date(2026, 6, 16),
            "field_set": ("date", "open", "high", "low", "close", "volume"),
            "as_of": datetime(2026, 6, 16, 23, 59, tzinfo=UTC),
            "fresh_until": datetime(2026, 6, 17, 23, 59, tzinfo=UTC),
            "source_roles": ("built_in_public",),
            "exchange": "BINANCE",
            "currency": "USD",
            "timezone": "UTC",
            "calendar": "CRYPTO_24_7",
            "base_asset": "BTC",
            "quote_asset": "USDT",
            "provider_lineage": {"provider_id": "seed"},
            "schema_id": "daily_bar.v1",
            "quality_flags": (),
            "row": {"close": 67000},
        }
    )
    api = FakeDataAPI()
    jobs = InMemoryMaintenanceJobRepository()
    runner = ScheduledDataMaintenanceRunner(
        data_api=api,
        job_repository=jobs,
        dataset_repository=dataset_repository,
        now_provider=lambda: datetime(2026, 6, 18, 9, 0, tzinfo=UTC),
    )

    result = runner.run(market="CRYPTO", job_kind="kline-refresh", cron_run_id="cron-run-crypto")

    assert result.status == "succeeded"
    assert len(api.requests) == 1
    request = api.requests[0]
    assert isinstance(request, PublicDataRequest)
    assert request.item == "daily_bar"
    assert request.market.value == "CRYPTO"
    assert request.granularity == "daily"
    assert request.consumer == "maintenance"
    assert request.time_range_start == date(2026, 6, 17)
    assert result.cursor["last_input_range"] == {"start": "2026-06-17", "end": "2026-06-17"}
    assert result.cursor["coverage_start"] == "2026-06-17"
    assert result.cursor["coverage_end"] == "2026-06-17"
    manifests = jobs.list_dataset_manifests()
    assert manifests[0]["coverage_window"] == {"start": "2026-06-17", "end": "2026-06-17"}
    payload = request.model_dump(mode="python")
    assert "packageRefresh" not in payload
    assert payload.get("packageRefresh") is None


def test_scheduled_maintenance_fails_closed_on_non_ready_data_result() -> None:
    dataset_repository = DatasetRepository()
    dataset_repository.insert_normalized(
        {
            "dataset": "daily_bar",
            "market": "CRYPTO",
            "symbol_id": "BTCUSDT",
            "universe_ref": None,
            "granularity": "daily",
            "period_start": date(2026, 6, 16),
            "period_end": date(2026, 6, 16),
            "field_set": ("date", "open", "high", "low", "close", "volume"),
            "as_of": datetime(2026, 6, 16, 23, 59, tzinfo=UTC),
            "fresh_until": datetime(2026, 6, 17, 23, 59, tzinfo=UTC),
            "source_roles": ("built_in_public",),
            "exchange": "BINANCE",
            "currency": "USD",
            "timezone": "UTC",
            "calendar": "CRYPTO_24_7",
            "base_asset": "BTC",
            "quote_asset": "USDT",
            "provider_lineage": {"provider_id": "seed"},
            "schema_id": "daily_bar.v1",
            "quality_flags": (),
            "row": {"close": 67000},
        }
    )
    jobs = InMemoryMaintenanceJobRepository()
    runner = ScheduledDataMaintenanceRunner(
        data_api=FakeDataAPI(status=DataResultStatus.PARTIAL),
        job_repository=jobs,
        dataset_repository=dataset_repository,
        now_provider=lambda: datetime(2026, 6, 18, 9, 0, tzinfo=UTC),
    )

    with pytest.raises(RuntimeError, match="request_id=maintenance:CRYPTO:daily_bar:BTCUSDT:2026-06-17 status=partial"):
        runner.run(
            market="CRYPTO",
            job_kind="kline-refresh",
            cron_run_id="cron-run-crypto",
            maintenance_job_id="job-non-ready",
        )

    saved = jobs.get("job-non-ready")
    assert saved is not None
    assert saved.status == "failed"
    assert saved.error == (
        "scheduled maintenance DataAPI returned non-ready status: "
        "request_id=maintenance:CRYPTO:daily_bar:BTCUSDT:2026-06-17 status=partial"
    )


def test_crypto_kline_refresh_fails_closed_when_scope_is_empty() -> None:
    jobs = InMemoryMaintenanceJobRepository()
    runner = ScheduledDataMaintenanceRunner(
        data_api=FakeDataAPI(),
        job_repository=jobs,
        dataset_repository=DatasetRepository(),
        now_provider=lambda: datetime(2026, 6, 18, 9, 0, tzinfo=UTC),
    )

    with pytest.raises(ValueError, match="CRYPTO daily_bar maintenance scope is empty in data layer"):
        runner.run(
            market="CRYPTO",
            job_kind="kline-refresh",
            cron_run_id="cron-run-crypto",
            maintenance_job_id="job-empty",
        )

    saved = jobs.get("job-empty")
    assert saved is not None
    assert saved.status == "failed"
    assert saved.error == "CRYPTO daily_bar maintenance scope is empty in data layer"
