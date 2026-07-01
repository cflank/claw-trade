from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
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


class CryptoBypassPlanner:
    def find_gaps(self, *, market: str, dataset_scope: str, as_of: object) -> tuple[DailyBarMaintenanceGap, ...]:
        _ = market, dataset_scope, as_of
        return (DailyBarMaintenanceGap(market="CRYPTO", instrument="BTCUSDT", missing_day=date(2026, 6, 17)),)


class ManifestOnlyDatasetRepository:
    def __init__(self, manifests: tuple[dict[str, object], ...]) -> None:
        self._manifests = manifests

    def list_dataset_manifests(self) -> tuple[dict[str, object], ...]:
        return self._manifests

    def iter_normalized(self, **_kwargs: object) -> tuple[object, ...]:
        raise AssertionError("daily maintenance should use manifest metadata")


class PlanningStatusProbeDatasetRepository:
    def __init__(self, *, jobs: InMemoryMaintenanceJobRepository, job_id: str) -> None:
        self._jobs = jobs
        self._job_id = job_id
        self.observed_status: str | None = None
        self.observed_started_at: datetime | None = None
        self.observed_lock_expires_at: datetime | None = None

    def list_dataset_manifests(self) -> tuple[dict[str, object], ...]:
        job = self._jobs.get(self._job_id)
        self.observed_status = None if job is None else job.status
        self.observed_started_at = None if job is None else job.started_at
        self.observed_lock_expires_at = None if job is None else job.lock_expires_at
        return (
            {
                "dataset": "daily_bar",
                "market": "CN_A",
                "status": "active",
                "storage": "parquet",
                "universe_refs": ("all_a_shares",),
                "period_end_max": "2026-06-26",
            },
        )

    def iter_normalized(self, **_kwargs: object) -> tuple[object, ...]:
        raise AssertionError("daily maintenance should use manifest metadata")


def _missing_crypto_columnar_root(tmp_path: Path) -> Path:
    return tmp_path / "missing-crypto-history"


def _write_crypto_history_daily_rows(root: Path, rows: tuple[tuple[str, date], ...]) -> None:
    daily_dir = root / "market=CRYPTO" / "dataset=daily_bar" / "granularity=daily"
    daily_dir.mkdir(parents=True, exist_ok=True)
    import duckdb

    parquet_path = daily_dir / "part-0.parquet"
    with duckdb.connect(":memory:") as conn:
        conn.execute(
            """
            create table crypto_rows(
                dataset_ref varchar,
                dataset varchar,
                market varchar,
                symbol_id varchar,
                universe_ref varchar,
                granularity varchar,
                period_start date,
                period_end date,
                row_json varchar
            )
            """
        )
        conn.executemany(
            "insert into crypto_rows values (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    f"dataset://crypto/{symbol}/{day.isoformat()}",
                    "daily_bar",
                    "CRYPTO",
                    symbol,
                    "binance_spot_all_symbols",
                    "daily",
                    day,
                    day,
                    json.dumps(
                        {
                            "quote_asset": "USDT",
                            "market_segment": "spot",
                            "close": 1,
                        },
                        sort_keys=True,
                    ),
                )
                for symbol, day in rows
            ],
        )
        escaped_path = str(parquet_path).replace("'", "''")
        conn.execute(f"COPY crypto_rows TO '{escaped_path}' (FORMAT PARQUET)")


def _complete_crypto_history_daily_rows(
    rows: tuple[tuple[str, date], ...],
    *,
    filled_day: date,
    minimum_symbols: int = 305,
) -> tuple[tuple[str, date], ...]:
    completed: list[tuple[str, date]] = list(rows)
    seen = {symbol for symbol, _day in completed}
    index = 0
    while len(seen) < minimum_symbols:
        symbol = f"T{index:04d}USDT"
        index += 1
        if symbol in seen:
            continue
        seen.add(symbol)
        completed.append((symbol, filled_day))
    return tuple(completed)


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


def test_daily_maintenance_marks_job_running_before_gap_planning() -> None:
    api = FakeDataAPI()
    jobs = InMemoryMaintenanceJobRepository()
    job_id = "data-maintenance:CN_A:eod:cron-run-cn-a"
    dataset_repository = PlanningStatusProbeDatasetRepository(jobs=jobs, job_id=job_id)
    runner = ScheduledDataMaintenanceRunner(
        data_api=api,
        job_repository=jobs,
        dataset_repository=dataset_repository,
        now_provider=lambda: datetime(2026, 6, 30, 9, 0, tzinfo=UTC),
    )

    result = runner.run(market="CN_A", job_kind="eod", cron_run_id="cron-run-cn-a")

    assert dataset_repository.observed_status == "running"
    assert dataset_repository.observed_started_at == datetime(2026, 6, 30, 9, 0, tzinfo=UTC)
    assert dataset_repository.observed_lock_expires_at == datetime(2026, 6, 30, 13, 0, tzinfo=UTC)
    assert result.status == "succeeded"


def test_daily_maintenance_request_deadline_allows_rate_limited_batches() -> None:
    request = DailyBarMaintenanceGap(
        market="CN_A",
        instrument="all_a_shares",
        missing_day=date(2026, 6, 30),
    ).to_public_data_request(consumer="maintenance", consumer_id="job-cn-a")

    assert request.deadline_at is not None
    assert request.deadline_at - datetime.now(tz=UTC) > timedelta(hours=3, minutes=50)


def test_crypto_kline_refresh_fails_closed_when_full_history_is_missing_despite_old_btc_seed(tmp_path: Path) -> None:
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
        crypto_history_columnar_root=_missing_crypto_columnar_root(tmp_path),
    )

    with pytest.raises(ValueError, match="full spot USDT columnar history is missing"):
        runner.run(
            market="CRYPTO",
            job_kind="kline-refresh",
            cron_run_id="cron-run-crypto",
            maintenance_job_id="job-crypto-missing-history",
        )

    assert api.requests == []
    saved = jobs.get("job-crypto-missing-history")
    assert saved is not None
    assert saved.status == "failed"
    assert "full spot USDT columnar history is missing" in str(saved.error)


def test_crypto_kline_refresh_bootstraps_missing_full_history_before_planning(tmp_path: Path) -> None:
    root = _missing_crypto_columnar_root(tmp_path)
    calls: list[tuple[Path, datetime | date]] = []

    def initializer(target_root: Path, as_of: datetime | date) -> None:
        calls.append((target_root, as_of))
        _write_crypto_history_daily_rows(
            target_root,
            _complete_crypto_history_daily_rows((("BTCUSDT", date(2026, 6, 6)),), filled_day=date(2026, 6, 8)),
        )

    api = FakeDataAPI()
    runner = ScheduledDataMaintenanceRunner(
        data_api=api,
        job_repository=InMemoryMaintenanceJobRepository(),
        dataset_repository=DatasetRepository(),
        now_provider=lambda: datetime(2026, 6, 8, 9, 0, tzinfo=UTC),
        crypto_history_columnar_root=root,
        crypto_history_initializer=initializer,
    )

    result = runner.run(market="CRYPTO", job_kind="kline-refresh", cron_run_id="cron-run-crypto")

    assert result.status == "succeeded"
    assert calls == [(root, datetime(2026, 6, 8, 9, 0, tzinfo=UTC))]
    assert [(request.instrument, request.time_range_start, request.time_range_end) for request in api.requests] == [
        ("BTCUSDT", date(2026, 6, 7), date(2026, 6, 8)),
    ]


def test_crypto_kline_refresh_fails_closed_when_full_history_has_no_valid_binance_symbols(tmp_path: Path) -> None:
    root = tmp_path / "crypto-history"
    _write_crypto_history_daily_rows(root, (("币安人生USDT", date(2026, 6, 6)),))
    api = FakeDataAPI()
    jobs = InMemoryMaintenanceJobRepository()
    runner = ScheduledDataMaintenanceRunner(
        data_api=api,
        job_repository=jobs,
        dataset_repository=DatasetRepository(),
        now_provider=lambda: datetime(2026, 6, 8, 9, 0, tzinfo=UTC),
        crypto_history_columnar_root=root,
    )

    with pytest.raises(ValueError, match="no valid Binance spot USDT symbols"):
        runner.run(market="CRYPTO", job_kind="kline-refresh", cron_run_id="cron-run-crypto")

    assert api.requests == []


def test_crypto_kline_refresh_fails_closed_when_full_history_is_incomplete(tmp_path: Path) -> None:
    root = tmp_path / "crypto-history"
    _write_crypto_history_daily_rows(root, (("BTCUSDT", date(2026, 6, 6)),))
    api = FakeDataAPI()
    runner = ScheduledDataMaintenanceRunner(
        data_api=api,
        job_repository=InMemoryMaintenanceJobRepository(),
        dataset_repository=DatasetRepository(),
        now_provider=lambda: datetime(2026, 6, 8, 9, 0, tzinfo=UTC),
        crypto_history_columnar_root=root,
    )

    with pytest.raises(ValueError, match="full spot USDT columnar history is incomplete"):
        runner.run(market="CRYPTO", job_kind="kline-refresh", cron_run_id="cron-run-crypto")

    assert api.requests == []


def test_crypto_kline_refresh_fails_closed_when_full_history_dir_has_no_parquet(tmp_path: Path) -> None:
    daily_dir = tmp_path / "crypto-history" / "market=CRYPTO" / "dataset=daily_bar" / "granularity=daily"
    daily_dir.mkdir(parents=True)
    api = FakeDataAPI()
    runner = ScheduledDataMaintenanceRunner(
        data_api=api,
        job_repository=InMemoryMaintenanceJobRepository(),
        dataset_repository=DatasetRepository(),
        now_provider=lambda: datetime(2026, 6, 8, 9, 0, tzinfo=UTC),
        crypto_history_columnar_root=tmp_path / "crypto-history",
    )

    with pytest.raises(ValueError, match="has no parquet files"):
        runner.run(market="CRYPTO", job_kind="kline-refresh", cron_run_id="cron-run-crypto")

    assert api.requests == []


def test_crypto_kline_refresh_rejects_incremental_planner_bypass() -> None:
    jobs = InMemoryMaintenanceJobRepository()
    runner = ScheduledDataMaintenanceRunner(
        data_api=FakeDataAPI(),
        job_repository=jobs,
        dataset_repository=DatasetRepository(),
        incremental_planner=CryptoBypassPlanner(),
        now_provider=lambda: datetime(2026, 6, 18, 9, 0, tzinfo=UTC),
    )

    with pytest.raises(ValueError, match="incremental planner bypass is not allowed"):
        runner.run(
            market="CRYPTO",
            job_kind="kline-refresh",
            cron_run_id="cron-run-crypto",
            maintenance_job_id="job-crypto-planner-bypass",
        )

    assert jobs.get("job-crypto-planner-bypass") is not None
    assert jobs.get("job-crypto-planner-bypass").status == "failed"  # type: ignore[union-attr]


def test_crypto_kline_refresh_plans_all_spot_usdt_symbols_from_columnar_history(tmp_path: Path) -> None:
    root = tmp_path / "crypto-history"
    _write_crypto_history_daily_rows(
        root,
        _complete_crypto_history_daily_rows(
            (
                ("BTCUSDT", date(2026, 6, 6)),
                ("ETHUSDT", date(2026, 6, 5)),
                ("SOLUSDT", date(2026, 6, 8)),
                ("币安人生USDT", date(2026, 6, 5)),
            ),
            filled_day=date(2026, 6, 8),
        ),
    )
    api = FakeDataAPI()
    runner = ScheduledDataMaintenanceRunner(
        data_api=api,
        job_repository=InMemoryMaintenanceJobRepository(),
        dataset_repository=DatasetRepository(),
        now_provider=lambda: datetime(2026, 6, 8, 9, 0, tzinfo=UTC),
        crypto_history_columnar_root=root,
    )

    result = runner.run(market="CRYPTO", job_kind="kline-refresh", cron_run_id="cron-run-crypto")

    assert result.status == "succeeded"
    assert [(request.instrument, request.time_range_start, request.time_range_end) for request in api.requests] == [
        ("BTCUSDT", date(2026, 6, 7), date(2026, 6, 8)),
        ("ETHUSDT", date(2026, 6, 6), date(2026, 6, 8)),
    ]
    assert all(request.instrument != "binance_spot_all_symbols" for request in api.requests)
    assert all(request.instrument != "币安人生USDT" for request in api.requests)


def test_crypto_kline_refresh_filters_non_trading_spot_symbols(tmp_path: Path) -> None:
    root = tmp_path / "crypto-history"
    rows = _complete_crypto_history_daily_rows(
        (
            ("BTCUSDT", date(2026, 6, 30)),
            ("COSUSDT", date(2026, 6, 19)),
        ),
        filled_day=date(2026, 7, 1),
    )
    _write_crypto_history_daily_rows(
        root,
        rows,
    )
    api = FakeDataAPI()
    runner = ScheduledDataMaintenanceRunner(
        data_api=api,
        job_repository=InMemoryMaintenanceJobRepository(),
        dataset_repository=DatasetRepository(),
        now_provider=lambda: datetime(2026, 7, 1, 9, 0, tzinfo=UTC),
        crypto_history_columnar_root=root,
        crypto_trading_symbol_loader=lambda: tuple(symbol for symbol, _day in rows if symbol != "COSUSDT"),
    )

    result = runner.run(market="CRYPTO", job_kind="kline-refresh", cron_run_id="cron-run-crypto")

    assert result.status == "succeeded"
    assert [(request.instrument, request.time_range_start, request.time_range_end) for request in api.requests] == [
        ("BTCUSDT", date(2026, 7, 1), date(2026, 7, 1)),
    ]


def test_crypto_kline_refresh_fails_closed_when_trading_symbols_are_incomplete(tmp_path: Path) -> None:
    root = tmp_path / "crypto-history"
    _write_crypto_history_daily_rows(
        root,
        _complete_crypto_history_daily_rows((("BTCUSDT", date(2026, 6, 30)),), filled_day=date(2026, 7, 1)),
    )
    runner = ScheduledDataMaintenanceRunner(
        data_api=FakeDataAPI(),
        job_repository=InMemoryMaintenanceJobRepository(),
        dataset_repository=DatasetRepository(),
        now_provider=lambda: datetime(2026, 7, 1, 9, 0, tzinfo=UTC),
        crypto_history_columnar_root=root,
        crypto_trading_symbol_loader=lambda: ("BTCUSDT",),
    )

    with pytest.raises(RuntimeError, match="trading symbol discovery is incomplete"):
        runner.run(market="CRYPTO", job_kind="kline-refresh", cron_run_id="cron-run-crypto")


def test_crypto_kline_refresh_fails_closed_when_current_trading_symbol_coverage_is_too_low(tmp_path: Path) -> None:
    root = tmp_path / "crypto-history"
    rows = _complete_crypto_history_daily_rows((("BTCUSDT", date(2026, 6, 30)),), filled_day=date(2026, 7, 1))
    _write_crypto_history_daily_rows(root, rows)
    missing_current_symbols = tuple(f"NEW{index:03d}USDT" for index in range(40))
    trading_symbols = tuple(symbol for symbol, _day in rows) + missing_current_symbols
    runner = ScheduledDataMaintenanceRunner(
        data_api=FakeDataAPI(),
        job_repository=InMemoryMaintenanceJobRepository(),
        dataset_repository=DatasetRepository(),
        now_provider=lambda: datetime(2026, 7, 1, 9, 0, tzinfo=UTC),
        crypto_history_columnar_root=root,
        crypto_trading_symbol_loader=lambda: trading_symbols,
    )

    with pytest.raises(RuntimeError, match="does not cover enough current Binance trading symbols"):
        runner.run(market="CRYPTO", job_kind="kline-refresh", cron_run_id="cron-run-crypto")


def test_crypto_kline_refresh_fails_closed_when_trading_symbol_discovery_fails(tmp_path: Path) -> None:
    root = tmp_path / "crypto-history"
    _write_crypto_history_daily_rows(
        root,
        _complete_crypto_history_daily_rows((("BTCUSDT", date(2026, 6, 30)),), filled_day=date(2026, 7, 1)),
    )
    jobs = InMemoryMaintenanceJobRepository()
    runner = ScheduledDataMaintenanceRunner(
        data_api=FakeDataAPI(),
        job_repository=jobs,
        dataset_repository=DatasetRepository(),
        now_provider=lambda: datetime(2026, 7, 1, 9, 0, tzinfo=UTC),
        crypto_history_columnar_root=root,
        crypto_trading_symbol_loader=lambda: (_ for _ in ()).throw(RuntimeError("exchangeInfo down")),
    )

    with pytest.raises(RuntimeError, match="trading symbol discovery failed"):
        runner.run(market="CRYPTO", job_kind="kline-refresh", cron_run_id="cron-run-crypto")

    saved = jobs.get("data-maintenance:CRYPTO:kline-refresh:cron-run-crypto")
    assert saved is not None
    assert saved.status == "failed"


def test_crypto_kline_refresh_uses_runtime_manifests_to_avoid_redownloading_seed_ranges(tmp_path: Path) -> None:
    root = tmp_path / "crypto-history"
    _write_crypto_history_daily_rows(
        root,
        _complete_crypto_history_daily_rows(
            (
                ("BTCUSDT", date(2026, 6, 6)),
                ("ETHUSDT", date(2026, 6, 6)),
            ),
            filled_day=date(2026, 6, 8),
        ),
    )
    api = FakeDataAPI()
    runner = ScheduledDataMaintenanceRunner(
        data_api=api,
        job_repository=InMemoryMaintenanceJobRepository(),
        dataset_repository=ManifestOnlyDatasetRepository(
            (
                {
                    "dataset": "daily_bar",
                    "market": "CRYPTO",
                    "status": "active",
                    "storage": "parquet",
                    "symbol_ids": ("BTCUSDT",),
                    "period_start_min": "2026-06-07",
                    "period_end_max": "2026-06-08",
                },
            )
        ),
        now_provider=lambda: datetime(2026, 6, 8, 9, 0, tzinfo=UTC),
        crypto_history_columnar_root=root,
    )

    result = runner.run(market="CRYPTO", job_kind="kline-refresh", cron_run_id="cron-run-crypto")

    assert result.status == "succeeded"
    assert [(request.instrument, request.time_range_start, request.time_range_end) for request in api.requests] == [
        ("ETHUSDT", date(2026, 6, 7), date(2026, 6, 8)),
    ]


def test_crypto_kline_refresh_ignores_factory_seed_manifests_when_overlaying_runtime_latest(tmp_path: Path) -> None:
    root = tmp_path / "crypto-history"
    _write_crypto_history_daily_rows(
        root,
        _complete_crypto_history_daily_rows((("BTCUSDT", date(2026, 6, 6)),), filled_day=date(2026, 6, 8)),
    )
    api = FakeDataAPI()
    runner = ScheduledDataMaintenanceRunner(
        data_api=api,
        job_repository=InMemoryMaintenanceJobRepository(),
        dataset_repository=ManifestOnlyDatasetRepository(
            (
                {
                    "dataset": "daily_bar",
                    "market": "CRYPTO",
                    "status": "active",
                    "storage": "parquet",
                    "symbol_ids": ("BTCUSDT",),
                    "universe_refs": ("binance_spot_all_symbols",),
                    "period_start_min": "2025-03-30",
                    "period_end_max": "2026-06-08",
                    "path": "/repo/.runtime/factory-seeds/current-seed-20260626/normalized/market=CRYPTO/dataset=daily_bar/granularity=daily/partition.parquet",
                },
                {
                    "dataset": "daily_bar",
                    "market": "CRYPTO",
                    "status": "active",
                    "storage": "parquet",
                    "symbol_ids": ("BTCUSDT",),
                    "universe_refs": ("binance_spot_all_symbols",),
                    "period_start_min": "2025-03-30",
                    "period_end_max": "2026-06-08",
                    "path": "data/crypto-history-full/normalized-columnar-usdt-only/market=CRYPTO/dataset=daily_bar/granularity=daily/partition.parquet",
                },
            )
        ),
        now_provider=lambda: datetime(2026, 6, 8, 9, 0, tzinfo=UTC),
        crypto_history_columnar_root=root,
    )

    result = runner.run(market="CRYPTO", job_kind="kline-refresh", cron_run_id="cron-run-crypto")

    assert result.status == "succeeded"
    assert [(request.instrument, request.time_range_start, request.time_range_end) for request in api.requests] == [
        ("BTCUSDT", date(2026, 6, 7), date(2026, 6, 8)),
    ]


def test_crypto_kline_refresh_skips_when_runtime_manifests_are_current(tmp_path: Path) -> None:
    root = tmp_path / "crypto-history"
    _write_crypto_history_daily_rows(
        root,
        _complete_crypto_history_daily_rows(
            (
                ("BTCUSDT", date(2026, 6, 6)),
                ("ETHUSDT", date(2026, 6, 6)),
            ),
            filled_day=date(2026, 6, 8),
        ),
    )
    api = FakeDataAPI()
    runner = ScheduledDataMaintenanceRunner(
        data_api=api,
        job_repository=InMemoryMaintenanceJobRepository(),
        dataset_repository=ManifestOnlyDatasetRepository(
            (
                {
                    "dataset": "daily_bar",
                    "market": "CRYPTO",
                    "status": "active",
                    "storage": "parquet",
                    "symbol_ids": ("BTCUSDT", "ETHUSDT"),
                    "period_start_min": "2026-06-07",
                    "period_end_max": "2026-06-08",
                },
            )
        ),
        now_provider=lambda: datetime(2026, 6, 8, 9, 0, tzinfo=UTC),
        crypto_history_columnar_root=root,
    )

    result = runner.run(market="CRYPTO", job_kind="kline-refresh", cron_run_id="cron-run-crypto")

    assert result.status == "succeeded"
    assert api.requests == []


def test_daily_maintenance_plans_from_columnar_manifests_without_scanning_rows() -> None:
    api = FakeDataAPI()
    runner = ScheduledDataMaintenanceRunner(
        data_api=api,
        job_repository=InMemoryMaintenanceJobRepository(),
        dataset_repository=ManifestOnlyDatasetRepository(
            (
                {
                    "dataset": "daily_bar",
                    "market": "US",
                    "status": "active",
                    "storage": "parquet",
                    "symbol_ids": ("AAPL", "MSFT"),
                    "period_end_max": "2026-06-28",
                },
            )
        ),
        now_provider=lambda: datetime(2026, 6, 30, 9, 0, tzinfo=UTC),
    )

    result = runner.run(market="US", job_kind="eod", cron_run_id="cron-run-us")

    assert result.status == "succeeded"
    assert [(request.instrument, request.time_range_start, request.time_range_end) for request in api.requests] == [
        ("AAPL", date(2026, 6, 29), date(2026, 6, 30)),
        ("MSFT", date(2026, 6, 29), date(2026, 6, 30)),
    ]


def test_cn_a_daily_maintenance_uses_default_universe_when_latest_universe_manifest_is_missing() -> None:
    api = FakeDataAPI()
    runner = ScheduledDataMaintenanceRunner(
        data_api=api,
        job_repository=InMemoryMaintenanceJobRepository(),
        dataset_repository=ManifestOnlyDatasetRepository(
            (
                {
                    "dataset": "daily_bar",
                    "market": "CN_A",
                    "status": "active",
                    "storage": "parquet",
                    "symbol_ids": ("000001.SZ", "600000.SH"),
                    "period_end_max": "2026-06-28",
                },
            )
        ),
        now_provider=lambda: datetime(2026, 6, 30, 9, 0, tzinfo=UTC),
    )

    result = runner.run(market="CN_A", job_kind="eod", cron_run_id="cron-run-cn-a")

    assert result.status == "succeeded"
    assert [(request.instrument, request.time_range_start, request.time_range_end) for request in api.requests] == [
        ("all_a_shares", date(2026, 6, 29), date(2026, 6, 29)),
        ("all_a_shares", date(2026, 6, 30), date(2026, 6, 30)),
    ]


def test_cn_a_daily_maintenance_caps_end_at_latest_completed_trading_day_before_close() -> None:
    api = FakeDataAPI()
    runner = ScheduledDataMaintenanceRunner(
        data_api=api,
        job_repository=InMemoryMaintenanceJobRepository(),
        dataset_repository=ManifestOnlyDatasetRepository(
            (
                {
                    "dataset": "daily_bar",
                    "market": "CN_A",
                    "status": "active",
                    "storage": "parquet",
                    "universe_refs": ("all_a_shares",),
                    "period_end_max": "2026-06-23",
                },
            )
        ),
        now_provider=lambda: datetime(2026, 7, 1, 0, 21, tzinfo=UTC),
    )

    result = runner.run(market="CN_A", job_kind="eod", cron_run_id="cron-run-cn-a")

    assert result.status == "succeeded"
    assert [(request.instrument, request.time_range_start, request.time_range_end) for request in api.requests] == [
        ("all_a_shares", date(2026, 6, 24), date(2026, 6, 24)),
        ("all_a_shares", date(2026, 6, 25), date(2026, 6, 25)),
        ("all_a_shares", date(2026, 6, 26), date(2026, 6, 26)),
        ("all_a_shares", date(2026, 6, 29), date(2026, 6, 29)),
        ("all_a_shares", date(2026, 6, 30), date(2026, 6, 30)),
    ]


def test_cn_a_daily_maintenance_retries_recent_hole_behind_latest_manifest() -> None:
    api = FakeDataAPI()
    runner = ScheduledDataMaintenanceRunner(
        data_api=api,
        job_repository=InMemoryMaintenanceJobRepository(),
        dataset_repository=ManifestOnlyDatasetRepository(
            tuple(
                {
                    "dataset": "daily_bar",
                    "market": "CN_A",
                    "status": "active",
                    "storage": "parquet",
                    "universe_refs": ("all_a_shares",),
                    "period_start_min": day.isoformat(),
                    "period_end_max": day.isoformat(),
                }
                for day in (
                    date(2026, 6, 24),
                    date(2026, 6, 26),
                    date(2026, 6, 29),
                    date(2026, 6, 30),
                )
            )
        ),
        now_provider=lambda: datetime(2026, 7, 1, 0, 21, tzinfo=UTC),
    )

    result = runner.run(market="CN_A", job_kind="eod", cron_run_id="cron-run-cn-a")

    assert result.status == "succeeded"
    assert [(request.instrument, request.time_range_start, request.time_range_end) for request in api.requests] == [
        ("all_a_shares", date(2026, 6, 25), date(2026, 6, 25)),
    ]


def test_cn_a_symbol_only_latest_manifest_does_not_suppress_default_universe_repair() -> None:
    api = FakeDataAPI()
    runner = ScheduledDataMaintenanceRunner(
        data_api=api,
        job_repository=InMemoryMaintenanceJobRepository(),
        dataset_repository=ManifestOnlyDatasetRepository(
            (
                {
                    "dataset": "daily_bar",
                    "market": "CN_A",
                    "status": "active",
                    "storage": "parquet",
                    "symbol_ids": ("000001.SZ", "600000.SH"),
                    "period_start_min": "2026-06-29",
                    "period_end_max": "2026-06-30",
                },
            )
        ),
        now_provider=lambda: datetime(2026, 7, 1, 0, 21, tzinfo=UTC),
    )

    result = runner.run(market="CN_A", job_kind="eod", cron_run_id="cron-run-cn-a")

    assert result.status == "succeeded"
    assert [(request.instrument, request.time_range_start, request.time_range_end) for request in api.requests] == [
        ("all_a_shares", date(2026, 6, 29), date(2026, 6, 29)),
        ("all_a_shares", date(2026, 6, 30), date(2026, 6, 30)),
    ]


def test_cn_a_stale_universe_manifest_is_not_suppressed_by_newer_symbol_only_manifest() -> None:
    api = FakeDataAPI()
    runner = ScheduledDataMaintenanceRunner(
        data_api=api,
        job_repository=InMemoryMaintenanceJobRepository(),
        dataset_repository=ManifestOnlyDatasetRepository(
            (
                {
                    "dataset": "daily_bar",
                    "market": "CN_A",
                    "status": "active",
                    "storage": "parquet",
                    "universe_refs": ("all_a_shares",),
                    "period_end_max": "2026-06-26",
                },
                {
                    "dataset": "daily_bar",
                    "market": "CN_A",
                    "status": "active",
                    "storage": "parquet",
                    "symbol_ids": ("000001.SZ", "600000.SH"),
                    "period_start_min": "2026-06-29",
                    "period_end_max": "2026-06-30",
                },
            )
        ),
        now_provider=lambda: datetime(2026, 7, 1, 0, 21, tzinfo=UTC),
    )

    result = runner.run(market="CN_A", job_kind="eod", cron_run_id="cron-run-cn-a")

    assert result.status == "succeeded"
    assert [(request.instrument, request.time_range_start, request.time_range_end) for request in api.requests] == [
        ("all_a_shares", date(2026, 6, 29), date(2026, 6, 29)),
        ("all_a_shares", date(2026, 6, 30), date(2026, 6, 30)),
    ]


def test_cn_a_manifest_missing_coverage_metadata_falls_back_to_row_scan() -> None:
    dataset_repository = DatasetRepository()
    dataset_repository.write_dataset_manifest(
        {
            "manifest_ref": "manifest:cn-a-missing-coverage",
            "dataset": "daily_bar",
            "market": "CN_A",
            "status": "active",
            "storage": "parquet",
            "symbol_ids": ("000001.SZ",),
        }
    )
    dataset_repository.insert_normalized(
        {
            "dataset": "daily_bar",
            "market": "CN_A",
            "symbol_id": None,
            "universe_ref": "all_a_shares",
            "granularity": "daily",
            "period_start": date(2026, 6, 26),
            "period_end": date(2026, 6, 26),
            "field_set": ("date", "open", "high", "low", "close", "volume"),
            "as_of": datetime(2026, 6, 26, 15, 30, tzinfo=UTC),
            "fresh_until": datetime(2026, 6, 27, 15, 30, tzinfo=UTC),
            "source_roles": ("official",),
            "exchange": "SSE_SZSE",
            "currency": "CNY",
            "timezone": "Asia/Shanghai",
            "calendar": "CN_A_SSE_SZSE",
            "base_asset": None,
            "quote_asset": None,
            "provider_lineage": {"provider_id": "seed"},
            "schema_id": "daily_bar.v1",
            "quality_flags": (),
            "row": {"close": 10.5},
        }
    )
    api = FakeDataAPI()
    runner = ScheduledDataMaintenanceRunner(
        data_api=api,
        job_repository=InMemoryMaintenanceJobRepository(),
        dataset_repository=dataset_repository,
        now_provider=lambda: datetime(2026, 7, 1, 0, 21, tzinfo=UTC),
    )

    result = runner.run(market="CN_A", job_kind="eod", cron_run_id="cron-run-cn-a")

    assert result.status == "succeeded"
    assert [(request.instrument, request.time_range_start, request.time_range_end) for request in api.requests] == [
        ("all_a_shares", date(2026, 6, 29), date(2026, 6, 29)),
        ("all_a_shares", date(2026, 6, 30), date(2026, 6, 30)),
    ]


def test_daily_maintenance_uses_universe_manifest_instead_of_symbol_fanout() -> None:
    api = FakeDataAPI()
    runner = ScheduledDataMaintenanceRunner(
        data_api=api,
        job_repository=InMemoryMaintenanceJobRepository(),
        dataset_repository=ManifestOnlyDatasetRepository(
            (
                {
                    "dataset": "daily_bar",
                    "market": "CN_A",
                    "status": "active",
                    "storage": "parquet",
                    "symbol_ids": ("000001.SZ", "600000.SH"),
                    "universe_refs": ("all_a_shares",),
                    "period_end_max": "2026-06-28",
                },
                {
                    "dataset": "daily_bar",
                    "market": "CN_A",
                    "status": "active",
                    "storage": "parquet",
                    "symbol_ids": ("000001.SZ",),
                    "period_end_max": "2026-06-20",
                },
            )
        ),
        now_provider=lambda: datetime(2026, 6, 30, 9, 0, tzinfo=UTC),
    )

    result = runner.run(market="CN_A", job_kind="eod", cron_run_id="cron-run-cn-a")

    assert result.status == "succeeded"
    assert [(request.instrument, request.time_range_start, request.time_range_end) for request in api.requests] == [
        ("all_a_shares", date(2026, 6, 29), date(2026, 6, 29)),
        ("all_a_shares", date(2026, 6, 30), date(2026, 6, 30)),
    ]


def test_crypto_daily_maintenance_does_not_fallback_to_manifest_or_single_symbol(tmp_path: Path) -> None:
    api = FakeDataAPI()
    jobs = InMemoryMaintenanceJobRepository()
    runner = ScheduledDataMaintenanceRunner(
        data_api=api,
        job_repository=jobs,
        dataset_repository=ManifestOnlyDatasetRepository(
            (
                {
                    "dataset": "daily_bar",
                    "market": "CRYPTO",
                    "status": "active",
                    "storage": "parquet",
                    "symbol_ids": ("BTCUSDT",),
                    "period_end_max": "2026-06-28",
                },
            )
        ),
        now_provider=lambda: datetime(2026, 6, 30, 9, 0, tzinfo=UTC),
        crypto_history_columnar_root=_missing_crypto_columnar_root(tmp_path),
    )

    with pytest.raises(ValueError, match="full spot USDT columnar history is missing"):
        runner.run(
            market="CRYPTO",
            job_kind="kline-refresh",
            cron_run_id="cron-run-crypto",
            maintenance_job_id="job-no-crypto-manifest-fallback",
        )

    assert api.requests == []
    saved = jobs.get("job-no-crypto-manifest-fallback")
    assert saved is not None
    assert saved.status == "failed"


def test_scheduled_maintenance_fails_closed_on_non_ready_data_result(tmp_path: Path) -> None:
    root = tmp_path / "crypto-history"
    _write_crypto_history_daily_rows(
        root,
        _complete_crypto_history_daily_rows((("BTCUSDT", date(2026, 6, 16)),), filled_day=date(2026, 6, 17)),
    )
    jobs = InMemoryMaintenanceJobRepository()
    runner = ScheduledDataMaintenanceRunner(
        data_api=FakeDataAPI(status=DataResultStatus.PARTIAL),
        job_repository=jobs,
        dataset_repository=DatasetRepository(),
        now_provider=lambda: datetime(2026, 6, 17, 9, 0, tzinfo=UTC),
        crypto_history_columnar_root=root,
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


def test_crypto_kline_refresh_fails_closed_when_full_history_is_missing(tmp_path: Path) -> None:
    jobs = InMemoryMaintenanceJobRepository()
    runner = ScheduledDataMaintenanceRunner(
        data_api=FakeDataAPI(),
        job_repository=jobs,
        dataset_repository=DatasetRepository(),
        now_provider=lambda: datetime(2026, 6, 18, 9, 0, tzinfo=UTC),
        crypto_history_columnar_root=_missing_crypto_columnar_root(tmp_path),
    )

    with pytest.raises(ValueError, match="full spot USDT columnar history is missing"):
        runner.run(
            market="CRYPTO",
            job_kind="kline-refresh",
            cron_run_id="cron-run-crypto",
            maintenance_job_id="job-empty",
        )

    saved = jobs.get("job-empty")
    assert saved is not None
    assert saved.status == "failed"
    assert "full spot USDT columnar history is missing" in str(saved.error)
