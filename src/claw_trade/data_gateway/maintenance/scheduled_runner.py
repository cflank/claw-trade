from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence
from zoneinfo import ZoneInfo

from claw_trade.data_gateway.maintenance.incremental import IncrementalGapPlanner, run_daily_incremental
from claw_trade.data_gateway.maintenance.jobs import (
    MaintenanceJob,
    MaintenanceJobRepository,
    begin_job,
    mark_job_failed,
)
from claw_trade.data_gateway.models import DataResultStatus, Market
from claw_trade.data_gateway.public_api import PublicDataRequest
from claw_trade.data_gateway.warehouse.repository import DatasetRecord, DatasetRepository
from claw_trade.data_gateway.warehouse.trading_calendar import is_expected_daily_date

_SUPPORTED_JOBS: dict[tuple[str, str], tuple[str, str]] = {
    ("CN_A", "eod"): ("daily_incremental", "daily_bar"),
    ("HK", "eod"): ("daily_incremental", "daily_bar"),
    ("US", "eod"): ("daily_incremental", "daily_bar"),
    ("CRYPTO", "kline-refresh"): ("daily_incremental", "daily_bar"),
}
_DEFAULT_DAILY_BAR_MAINTENANCE_INSTRUMENTS: dict[str, str] = {
    "CN_A": "all_a_shares",
}
_CRYPTO_HISTORY_COLUMNAR_ROOT = Path(
    os.environ.get("CLAW_TRADE_CRYPTO_HISTORY_COLUMNAR_ROOT") or "data/crypto-history-full/normalized-columnar-usdt-only"
)
_CRYPTO_HISTORY_COLUMNAR_DAILY_GLOB = "market=CRYPTO/dataset=daily_bar/granularity=daily/*.parquet"
_CRYPTO_HISTORY_MIN_SPOT_USDT_SYMBOLS = 300
_CRYPTO_HISTORY_MIN_TRADING_SYMBOL_COVERAGE_RATIO = 0.90
_BINANCE_SPOT_USDT_SYMBOL_RE = re.compile(r"^[A-Z0-9]+USDT$")
_DAILY_BAR_HOLE_SCAN_LOOKBACK_DAYS = 31
_MAINTENANCE_JOB_LOCK_TTL = timedelta(hours=4)
_MAINTENANCE_REQUEST_DEADLINE = timedelta(hours=4)
_REQUESTED_BY = "openclaw_cron"


class DataMaintenanceApi(Protocol):
    def request_data(self, requests: Any) -> Any: ...


@dataclass(frozen=True)
class DailyBarMaintenanceGap:
    market: str
    instrument: str
    missing_day: date
    missing_end_day: date | None = None

    @property
    def gap_id(self) -> str:
        end_day = self.missing_end_day or self.missing_day
        if end_day == self.missing_day:
            return f"{self.market}:daily_bar:{self.instrument}:{self.missing_day.isoformat()}"
        return f"{self.market}:daily_bar:{self.instrument}:{self.missing_day.isoformat()}:{end_day.isoformat()}"

    def to_public_data_request(self, *, consumer: str, consumer_id: str) -> PublicDataRequest:
        return PublicDataRequest(
            request_id=f"maintenance:{self.gap_id}",
            item="daily_bar",
            market=Market(self.market),
            instrument=self.instrument,
            time_range_start=self.missing_day,
            time_range_end=self.missing_end_day or self.missing_day,
            granularity="daily",
            requested_by_worker=_REQUESTED_BY,
            purpose="scheduled_data_maintenance",
            deadline_at=datetime.now(tz=UTC) + _MAINTENANCE_REQUEST_DEADLINE,
            consumer=consumer,
        )


class ScheduledDataMaintenanceRunner:
    def __init__(
        self,
        *,
        data_api: DataMaintenanceApi,
        job_repository: MaintenanceJobRepository,
        dataset_repository: DatasetRepository,
        incremental_planner: IncrementalGapPlanner | None = None,
        now_provider: Any | None = None,
        crypto_history_columnar_root: Path | None = None,
        crypto_history_initializer: Callable[[Path, datetime | date], None] | None = None,
        crypto_trading_symbol_loader: Callable[[], Sequence[str]] | None = None,
    ) -> None:
        self._data_api = data_api
        self._job_repository = job_repository
        self._dataset_repository = dataset_repository
        self._incremental_planner = incremental_planner
        self._now_provider = now_provider or (lambda: datetime.now(tz=UTC))
        self._crypto_history_columnar_root = crypto_history_columnar_root or _CRYPTO_HISTORY_COLUMNAR_ROOT
        self._crypto_history_initializer = crypto_history_initializer
        self._crypto_trading_symbol_loader = crypto_trading_symbol_loader

    def run(
        self,
        *,
        market: str,
        job_kind: str,
        cron_run_id: str | None = None,
        maintenance_job_id: str | None = None,
    ) -> MaintenanceJob:
        normalized_market = market.strip().upper()
        normalized_job_kind = job_kind.strip()
        job_type, dataset_scope = self._job_config(market=normalized_market, job_kind=normalized_job_kind)
        as_of = self._now_provider()
        job = MaintenanceJob(
            job_id=self._job_id(
                market=normalized_market,
                job_kind=normalized_job_kind,
                cron_run_id=cron_run_id,
                maintenance_job_id=maintenance_job_id,
                as_of=as_of,
            ),
            job_type=job_type,
            market=normalized_market,
            dataset_scope=dataset_scope,
            requested_by=_REQUESTED_BY,
            scheduled_at=as_of,
        )
        if _is_startup_cron_run_id(cron_run_id):
            self._mark_interrupted_startup_jobs(job=job, job_kind=normalized_job_kind, as_of=as_of)
        if self._incremental_planner is not None:
            if normalized_market == "CRYPTO" and dataset_scope == "daily_bar":
                error = "CRYPTO daily_bar maintenance must use full spot USDT columnar history; incremental planner bypass is not allowed"
                job.status = "failed"
                job.error = error
                job.finished_at = as_of if isinstance(as_of, datetime) else datetime.now(tz=UTC)
                self._job_repository.save(job)
                raise ValueError(error)
            return run_daily_incremental(
                job,
                data_api=_FailClosedDataApi(self._data_api),
                repo=self._job_repository,
                incremental_planner=self._incremental_planner,
                as_of=as_of,
                lock_ttl=_MAINTENANCE_JOB_LOCK_TTL,
            )
        current, started = begin_job(
            self._job_repository,
            job,
            lock_owner="maintenance.scheduled_runner",
            lock_ttl=_MAINTENANCE_JOB_LOCK_TTL,
            now=as_of if isinstance(as_of, datetime) else None,
        )
        if not started:
            return current
        try:
            gaps = self._plan_daily_bar_gaps(market=normalized_market, dataset_scope=dataset_scope, as_of=as_of)
        except Exception as exc:
            mark_job_failed(self._job_repository, current, error=str(exc))
            raise
        return run_daily_incremental(
            current,
            data_api=_FailClosedDataApi(self._data_api),
            repo=self._job_repository,
            gaps=gaps,
            as_of=as_of,
            begin_if_needed=False,
        )

    @staticmethod
    def _job_config(*, market: str, job_kind: str) -> tuple[str, str]:
        try:
            return _SUPPORTED_JOBS[(market, job_kind)]
        except KeyError as exc:
            raise ValueError(f"unsupported data maintenance job: {market}:{job_kind}") from exc

    @staticmethod
    def _job_id(
        *,
        market: str,
        job_kind: str,
        cron_run_id: str | None,
        maintenance_job_id: str | None,
        as_of: datetime | date,
    ) -> str:
        explicit = str(maintenance_job_id or "").strip()
        if explicit:
            return explicit
        run_id = str(cron_run_id or "").strip()
        if not run_id or run_id == "auto":
            day = as_of.date() if isinstance(as_of, datetime) else as_of
            run_id = day.isoformat()
        return f"data-maintenance:{market}:{job_kind}:{run_id}"

    def _mark_interrupted_startup_jobs(self, *, job: MaintenanceJob, job_kind: str, as_of: datetime | date) -> None:
        ts = as_of if isinstance(as_of, datetime) else datetime.now(tz=UTC)
        prefix = f"data-maintenance:{job.market}:{job_kind}:startup-"
        for existing in self._job_repository.list():
            if existing.job_id == job.job_id:
                continue
            if existing.status != "running":
                continue
            if existing.market != job.market or existing.dataset_scope != job.dataset_scope:
                continue
            if not existing.job_id.startswith(prefix):
                continue
            mark_job_failed(
                self._job_repository,
                existing,
                error="startup_data_maintenance_interrupted_by_new_startup",
                now=ts,
            )

    def _plan_daily_bar_gaps(
        self,
        *,
        market: str,
        dataset_scope: str,
        as_of: datetime | date,
    ) -> tuple[DailyBarMaintenanceGap, ...]:
        if market == "CRYPTO" and dataset_scope == "daily_bar":
            return self._plan_crypto_daily_bar_gaps_from_columnar_history(as_of=as_of)
        manifest_gaps = self._plan_daily_bar_gaps_from_manifests(market=market, dataset_scope=dataset_scope, as_of=as_of)
        if manifest_gaps is not None:
            return manifest_gaps

        latest_by_scope: dict[tuple[str | None, str | None], DatasetRecord] = {}
        # ponytail: O(n) metadata scan is fine for the first cron bridge; replace with an aggregate query if maintenance scope grows.
        for record in self._dataset_repository.iter_normalized(
            dataset=dataset_scope,
            market=market,
            symbol_id=None,
            universe_ref=None,
            include_row=False,
        ):
            key = (record.symbol_id, record.universe_ref)
            latest = latest_by_scope.get(key)
            if latest is None or _is_later(record.period_end, latest.period_end):
                latest_by_scope[key] = record
        if not latest_by_scope:
            raise ValueError(f"{market} daily_bar maintenance scope is empty in data layer")
        as_of_day = _maintenance_as_of_day(market=market, as_of=as_of)
        gaps: list[DailyBarMaintenanceGap] = []
        for record in latest_by_scope.values():
            latest_day = _date_or_none(record.period_end)
            if latest_day is None:
                continue
            instrument = str(record.symbol_id or "").strip()
            if not instrument and market != "CRYPTO":
                instrument = str(record.universe_ref or "").strip()
            if not instrument:
                continue
            missing_day = _next_missing_day(market=market, latest_day=latest_day)
            if missing_day <= as_of_day:
                gaps.extend(
                    _daily_bar_maintenance_gaps(
                        market=market,
                        instrument=instrument,
                        missing_day=missing_day,
                        as_of_day=as_of_day,
                    )
                )
        return tuple(gaps)

    def _plan_crypto_daily_bar_gaps_from_columnar_history(
        self,
        *,
        as_of: datetime | date,
    ) -> tuple[DailyBarMaintenanceGap, ...]:
        root = self._crypto_history_columnar_root
        daily_dir = root / "market=CRYPTO" / "dataset=daily_bar" / "granularity=daily"
        self._ensure_crypto_history_columnar_root(root=root, daily_dir=daily_dir, as_of=as_of)
        rows = self._read_valid_crypto_history_rows(root=root, as_of=as_of)
        latest_by_symbol = {
            str(instrument or "").strip(): latest_day
            for instrument, latest_value in rows
            if (latest_day := _date_or_none(latest_value)) is not None
            and _BINANCE_SPOT_USDT_SYMBOL_RE.fullmatch(str(instrument or "").strip())
        }
        covered_days_by_symbol = self._overlay_crypto_runtime_manifest_latest_days(latest_by_symbol)
        latest_by_symbol = self._filter_current_crypto_trading_symbols(latest_by_symbol)
        covered_days_by_symbol = {
            symbol: covered_days
            for symbol, covered_days in covered_days_by_symbol.items()
            if symbol in latest_by_symbol
        }
        as_of_day = _maintenance_as_of_day(market="CRYPTO", as_of=as_of)
        gaps: list[DailyBarMaintenanceGap] = []
        for symbol, latest_day in sorted(latest_by_symbol.items()):
            gaps.extend(
                _missing_default_instrument_hole_gaps(
                    market="CRYPTO",
                    instrument=symbol,
                    covered_days=covered_days_by_symbol.get(symbol, set()),
                    latest_day=latest_day,
                    as_of_day=as_of_day,
                )
            )
            missing_day = _next_missing_day(market="CRYPTO", latest_day=latest_day)
            if missing_day <= as_of_day:
                gaps.extend(
                    _daily_bar_maintenance_gaps(
                        market="CRYPTO",
                        instrument=symbol,
                        missing_day=missing_day,
                        as_of_day=as_of_day,
                    )
                )
        return _dedupe_daily_bar_gaps(gaps)

    def _filter_current_crypto_trading_symbols(self, latest_by_symbol: dict[str, date]) -> dict[str, date]:
        if self._crypto_trading_symbol_loader is None:
            return latest_by_symbol
        try:
            symbols = self._crypto_trading_symbol_loader()
        except Exception as exc:
            raise RuntimeError(f"CRYPTO spot USDT trading symbol discovery failed: {exc}") from exc
        trading_symbols = {
            str(symbol or "").strip().upper()
            for symbol in symbols
            if _BINANCE_SPOT_USDT_SYMBOL_RE.fullmatch(str(symbol or "").strip().upper())
        }
        if len(trading_symbols) < _CRYPTO_HISTORY_MIN_SPOT_USDT_SYMBOLS:
            raise RuntimeError(
                "CRYPTO spot USDT trading symbol discovery is incomplete: "
                f"valid_spot_usdt_symbols={len(trading_symbols)} minimum={_CRYPTO_HISTORY_MIN_SPOT_USDT_SYMBOLS}"
            )
        filtered = {
            symbol: latest_day
            for symbol, latest_day in latest_by_symbol.items()
            if symbol in trading_symbols
        }
        coverage_ratio = len(filtered) / len(trading_symbols)
        if len(filtered) < _CRYPTO_HISTORY_MIN_SPOT_USDT_SYMBOLS or coverage_ratio < _CRYPTO_HISTORY_MIN_TRADING_SYMBOL_COVERAGE_RATIO:
            missing_from_history = tuple(sorted(symbol for symbol in trading_symbols if symbol not in latest_by_symbol))
            sample = ",".join(missing_from_history[:10])
            raise RuntimeError(
                "CRYPTO full spot USDT columnar history does not cover enough current Binance trading symbols: "
                f"covered={len(filtered)} trading={len(trading_symbols)} "
                f"minimum={_CRYPTO_HISTORY_MIN_SPOT_USDT_SYMBOLS} "
                f"min_coverage={_CRYPTO_HISTORY_MIN_TRADING_SYMBOL_COVERAGE_RATIO:.0%} "
                f"missing_sample={sample}"
            )
        return filtered

    def _read_valid_crypto_history_rows(
        self,
        *,
        root: Path,
        as_of: datetime | date,
    ) -> tuple[tuple[Any, Any], ...]:
        rows = _read_crypto_history_latest_rows(root)
        if _crypto_history_rows_are_complete(rows):
            return rows
        if self._crypto_history_initializer is not None:
            self._crypto_history_initializer(root, as_of)
            daily_dir = root / "market=CRYPTO" / "dataset=daily_bar" / "granularity=daily"
            self._ensure_crypto_history_columnar_root(root=root, daily_dir=daily_dir, as_of=as_of)
            rows = _read_crypto_history_latest_rows(root)
            if _crypto_history_rows_are_complete(rows):
                return rows
        if not rows:
            raise ValueError("CRYPTO daily_bar full spot USDT columnar history has no valid Binance spot USDT symbols")
        raise ValueError(
            "CRYPTO daily_bar full spot USDT columnar history is incomplete: "
            f"valid_spot_usdt_symbols={len(rows)} minimum={_CRYPTO_HISTORY_MIN_SPOT_USDT_SYMBOLS}"
        )

    def _ensure_crypto_history_columnar_root(
        self,
        *,
        root: Path,
        daily_dir: Path,
        as_of: datetime | date,
    ) -> None:
        if _has_parquet_files(daily_dir):
            return
        if self._crypto_history_initializer is None:
            if not daily_dir.exists():
                raise ValueError(f"CRYPTO daily_bar full spot USDT columnar history is missing: {daily_dir}")
            raise ValueError(f"CRYPTO daily_bar full spot USDT columnar history has no parquet files: {daily_dir}")
        try:
            self._crypto_history_initializer(root, as_of)
        except Exception as exc:
            raise RuntimeError(f"CRYPTO daily_bar full spot USDT columnar history bootstrap failed: {exc}") from exc
        if not daily_dir.exists():
            raise ValueError(f"CRYPTO daily_bar full spot USDT columnar history is missing after bootstrap: {daily_dir}")
        if not _has_parquet_files(daily_dir):
            raise ValueError(f"CRYPTO daily_bar full spot USDT columnar history has no parquet files after bootstrap: {daily_dir}")

    def _overlay_crypto_runtime_manifest_latest_days(self, latest_by_symbol: dict[str, date]) -> dict[str, set[date]]:
        list_manifests = getattr(self._dataset_repository, "list_dataset_manifests", None)
        if not callable(list_manifests):
            return {}
        covered_days_by_symbol: dict[str, set[date]] = {}
        for manifest in list_manifests():
            if not _is_active_daily_bar_manifest(manifest, market="CRYPTO", dataset_scope="daily_bar"):
                continue
            if not _is_runtime_crypto_increment_manifest(manifest):
                continue
            latest_day = _date_or_none(manifest.get("period_end_max"))
            if latest_day is None:
                continue
            covered_days = _manifest_covered_daily_dates(market="CRYPTO", manifest=manifest)
            for symbol in _clean_text_tuple(manifest.get("symbol_ids")):
                if symbol not in latest_by_symbol or not _BINANCE_SPOT_USDT_SYMBOL_RE.fullmatch(symbol):
                    continue
                if covered_days:
                    covered_days_by_symbol.setdefault(symbol, set()).update(covered_days)
                current = latest_by_symbol.get(symbol)
                if current is None or latest_day > current:
                    latest_by_symbol[symbol] = latest_day
        return covered_days_by_symbol

    def _plan_daily_bar_gaps_from_manifests(
        self,
        *,
        market: str,
        dataset_scope: str,
        as_of: datetime | date,
    ) -> tuple[DailyBarMaintenanceGap, ...] | None:
        if market == "CRYPTO":
            raise ValueError("CRYPTO daily_bar maintenance must use full spot USDT columnar history")
        list_manifests = getattr(self._dataset_repository, "list_dataset_manifests", None)
        if not callable(list_manifests):
            return None
        default_instrument = _DEFAULT_DAILY_BAR_MAINTENANCE_INSTRUMENTS.get(market)
        latest_by_universe: dict[str, date] = {}
        latest_by_symbol: dict[str, date] = {}
        covered_days_by_default_instrument: dict[str, set[date]] = {}
        covered_days_by_instrument: dict[str, set[date]] = {}
        latest_symbol_only_range_start: date | None = None
        latest_symbol_only_range_end: date | None = None
        saw_scope = False
        for manifest in list_manifests():
            if not _is_active_daily_bar_manifest(manifest, market=market, dataset_scope=dataset_scope):
                continue
            saw_scope = True
            latest_day = _date_or_none(manifest.get("period_end_max"))
            if latest_day is None:
                continue
            universes = _clean_text_tuple(manifest.get("universe_refs"))
            symbol_ids = _clean_text_tuple(manifest.get("symbol_ids"))
            if default_instrument and (default_instrument in universes or default_instrument in symbol_ids):
                covered_days_by_default_instrument.setdefault(default_instrument, set()).update(
                    _manifest_covered_daily_dates(market=market, manifest=manifest)
                )
            targets = latest_by_universe if universes else latest_by_symbol
            instruments = universes or symbol_ids
            if market == "CRYPTO":
                targets = latest_by_symbol
                instruments = symbol_ids
            if market == "CN_A" and default_instrument and not universes:
                if symbol_ids and (latest_symbol_only_range_end is None or latest_day > latest_symbol_only_range_end):
                    latest_symbol_only_range_end = latest_day
                    latest_symbol_only_range_start = _date_or_none(manifest.get("period_start_min")) or latest_day
            covered_days = _manifest_covered_daily_dates(market=market, manifest=manifest)
            for instrument in instruments:
                if covered_days:
                    covered_days_by_instrument.setdefault(instrument, set()).update(covered_days)
                current = targets.get(instrument)
                if current is None or latest_day > current:
                    targets[instrument] = latest_day
        if not saw_scope:
            return None
        if not latest_by_universe and not latest_by_symbol:
            return None
        if default_instrument:
            has_default_universe_manifest = default_instrument in latest_by_universe
            latest_day = _default_maintenance_latest_day(
                market=market,
                latest_by_universe=latest_by_universe,
                latest_by_symbol=latest_by_symbol,
                default_instrument=default_instrument,
            )
            if latest_day is None:
                return ()
            as_of_day = _maintenance_as_of_day(market=market, as_of=as_of)
            missing_day = _next_missing_day(market=market, latest_day=latest_day)
            if market == "CN_A" and not has_default_universe_manifest and missing_day > as_of_day:
                missing_day = latest_symbol_only_range_start or as_of_day
                missing_day = _adjust_missing_start_day(market=market, missing_day=missing_day)
                if missing_day > as_of_day:
                    missing_day = as_of_day
            gaps = list(
                _missing_default_instrument_hole_gaps(
                    market=market,
                    instrument=default_instrument,
                    covered_days=covered_days_by_default_instrument.get(default_instrument, set()),
                    latest_day=latest_day,
                    as_of_day=as_of_day,
                )
            )
            if missing_day <= as_of_day:
                gaps.extend(
                    _daily_bar_maintenance_gaps(
                        market=market,
                        instrument=default_instrument,
                        missing_day=missing_day,
                        as_of_day=as_of_day,
                    )
                )
            return _dedupe_daily_bar_gaps(gaps)

        latest_by_instrument = latest_by_universe or latest_by_symbol
        as_of_day = _maintenance_as_of_day(market=market, as_of=as_of)
        gaps: list[DailyBarMaintenanceGap] = []
        for instrument, latest_day in latest_by_instrument.items():
            gaps.extend(
                _missing_default_instrument_hole_gaps(
                    market=market,
                    instrument=instrument,
                    covered_days=covered_days_by_instrument.get(instrument, set()),
                    latest_day=latest_day,
                    as_of_day=as_of_day,
                )
            )
            missing_day = _next_missing_day(market=market, latest_day=latest_day)
            if missing_day <= as_of_day:
                gaps.extend(
                    _daily_bar_maintenance_gaps(
                        market=market,
                        instrument=instrument,
                        missing_day=missing_day,
                        as_of_day=as_of_day,
                    )
                )
        return _dedupe_daily_bar_gaps(gaps)


def _missing_default_instrument_hole_gaps(
    *,
    market: str,
    instrument: str,
    covered_days: set[date],
    latest_day: date,
    as_of_day: date,
) -> tuple[DailyBarMaintenanceGap, ...]:
    if not covered_days:
        return ()
    end_day = min(latest_day, as_of_day)
    start_day = max(min(covered_days), end_day - timedelta(days=_DAILY_BAR_HOLE_SCAN_LOOKBACK_DAYS - 1))
    missing_days = tuple(day for day in _expected_maintenance_dates_between(market=market, start=start_day, end=end_day) if day not in covered_days)
    gaps: list[DailyBarMaintenanceGap] = []
    for day in missing_days:
        gaps.extend(
            _daily_bar_maintenance_gaps(
                market=market,
                instrument=instrument,
                missing_day=day,
                as_of_day=day,
            )
        )
    return tuple(gaps)


def _manifest_covered_daily_dates(*, market: str, manifest: Mapping[str, Any]) -> tuple[date, ...]:
    start = _date_or_none(manifest.get("period_start_min")) or _date_or_none(manifest.get("period_end_max"))
    end = _date_or_none(manifest.get("period_end_max")) or start
    if start is None or end is None:
        return ()
    return _expected_maintenance_dates_between(market=market, start=start, end=end)


def _expected_maintenance_dates_between(*, market: str, start: date, end: date) -> tuple[date, ...]:
    if market == "CN_A":
        return _expected_daily_dates_between(start, end, calendar="CN_A_SSE_SZSE")
    days: list[date] = []
    cursor = start
    while cursor <= end:
        days.append(cursor)
        cursor += timedelta(days=1)
    return tuple(days)


def _dedupe_daily_bar_gaps(gaps: Sequence[DailyBarMaintenanceGap]) -> tuple[DailyBarMaintenanceGap, ...]:
    deduped: dict[str, DailyBarMaintenanceGap] = {}
    for gap in gaps:
        deduped.setdefault(gap.gap_id, gap)
    return tuple(deduped.values())


def _has_parquet_files(path: Path) -> bool:
    return path.exists() and any(path.glob("*.parquet"))


def _read_crypto_history_latest_rows(root: Path) -> tuple[tuple[Any, Any], ...]:
    import duckdb

    glob_path = str(root / _CRYPTO_HISTORY_COLUMNAR_DAILY_GLOB)
    with duckdb.connect(":memory:") as conn:
        return tuple(
            conn.execute(
                """
                select symbol_id, max(period_end) as latest_day
                from read_parquet(?)
                where dataset = 'daily_bar'
                  and market = 'CRYPTO'
                  and granularity = 'daily'
                  and universe_ref = 'binance_spot_all_symbols'
                  and regexp_matches(symbol_id, '^[A-Z0-9]+USDT$')
                  and json_extract_string(row_json, '$.quote_asset') = 'USDT'
                  and json_extract_string(row_json, '$.market_segment') = 'spot'
                group by symbol_id
                order by symbol_id
                """,
                [glob_path],
            ).fetchall()
        )


def _crypto_history_rows_are_complete(rows: Sequence[tuple[Any, Any]]) -> bool:
    return len(rows) >= _CRYPTO_HISTORY_MIN_SPOT_USDT_SYMBOLS


def _daily_bar_maintenance_gaps(
    *,
    market: str,
    instrument: str,
    missing_day: date,
    as_of_day: date,
) -> tuple[DailyBarMaintenanceGap, ...]:
    if market == "CN_A" and instrument == _DEFAULT_DAILY_BAR_MAINTENANCE_INSTRUMENTS.get("CN_A"):
        return tuple(
            DailyBarMaintenanceGap(
                market=market,
                instrument=instrument,
                missing_day=day,
            )
            for day in _expected_daily_dates_between(missing_day, as_of_day, calendar="CN_A_SSE_SZSE")
        )
    return (
        DailyBarMaintenanceGap(
            market=market,
            instrument=instrument,
            missing_day=missing_day,
            missing_end_day=as_of_day,
        ),
    )


def _expected_daily_dates_between(start: date, end: date, *, calendar: str) -> tuple[date, ...]:
    days: list[date] = []
    cursor = start
    while cursor <= end:
        if is_expected_daily_date(cursor, calendar):
            days.append(cursor)
        cursor += timedelta(days=1)
    return tuple(days)


def _default_maintenance_latest_day(
    *,
    market: str,
    latest_by_universe: Mapping[str, date],
    latest_by_symbol: Mapping[str, date],
    default_instrument: str,
) -> date | None:
    if market == "CN_A":
        return latest_by_universe.get(default_instrument) or _max_day((*latest_by_symbol.values(), *latest_by_universe.values()))
    if market == "CRYPTO":
        return latest_by_symbol.get(default_instrument) or _max_day((*latest_by_symbol.values(), *latest_by_universe.values()))
    return None


def _max_day(values: Sequence[date]) -> date | None:
    return max(values) if values else None


def _next_missing_day(*, market: str, latest_day: date) -> date:
    return _adjust_missing_start_day(market=market, missing_day=latest_day + timedelta(days=1))


def _adjust_missing_start_day(*, market: str, missing_day: date) -> date:
    if market != "CN_A":
        return missing_day
    return _next_expected_daily_date(missing_day, calendar="CN_A_SSE_SZSE")


def _next_expected_daily_date(start: date, *, calendar: str) -> date:
    cursor = start
    for _ in range(14):
        if is_expected_daily_date(cursor, calendar):
            return cursor
        cursor += timedelta(days=1)
    return start


def _maintenance_as_of_day(*, market: str, as_of: datetime | date) -> date:
    if not isinstance(as_of, datetime):
        return as_of
    if market != "CN_A":
        return as_of.date()
    normalized = as_of if as_of.tzinfo is not None else as_of.replace(tzinfo=UTC)
    local_as_of = normalized.astimezone(ZoneInfo("Asia/Shanghai"))
    local_day = local_as_of.date()
    if is_expected_daily_date(local_day, "CN_A_SSE_SZSE") and local_as_of.time() >= time(15, 0):
        return local_day
    return _previous_expected_daily_date(local_day - timedelta(days=1), calendar="CN_A_SSE_SZSE")


def _previous_expected_daily_date(start: date, *, calendar: str) -> date:
    cursor = start
    for _ in range(14):
        if is_expected_daily_date(cursor, calendar):
            return cursor
        cursor -= timedelta(days=1)
    return start


def _date_or_none(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _is_startup_cron_run_id(value: str | None) -> bool:
    return str(value or "").startswith("startup-")


def _is_later(left: Any, right: Any) -> bool:
    left_day = _date_or_none(left)
    right_day = _date_or_none(right)
    if left_day is None:
        return False
    if right_day is None:
        return True
    return left_day > right_day


def _is_active_daily_bar_manifest(manifest: Mapping[str, Any], *, market: str, dataset_scope: str) -> bool:
    return (
        str(manifest.get("dataset") or "") == dataset_scope
        and str(manifest.get("market") or "") == market
        and str(manifest.get("status", "active")) == "active"
        and str(manifest.get("storage") or "") == "parquet"
    )


def _is_runtime_crypto_increment_manifest(manifest: Mapping[str, Any]) -> bool:
    path_text = str(manifest.get("path") or "")
    normalized = path_text.replace("\\", "/")
    if "/.runtime/factory-seeds/" in normalized:
        return False
    if "/data/crypto-history-full/" in normalized or normalized.startswith("data/crypto-history-full/"):
        return False
    return True


def _clean_text_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(dict.fromkeys(item for item in (str(item).strip() for item in value) if item))


class _FailClosedDataApi:
    def __init__(self, data_api: DataMaintenanceApi) -> None:
        self._data_api = data_api

    def request_data(self, requests: Any) -> Any:
        results = list(self._data_api.request_data(requests))
        for result in results:
            status = _result_status(result)
            if status != DataResultStatus.READY.value:
                detail = _result_failure_detail(result)
                reason = f" reason={detail}" if detail else ""
                raise RuntimeError(
                    f"scheduled maintenance DataAPI returned non-ready status: "
                    f"request_id={_result_request_id(result)} status={status}{reason}"
                )
        return results


def _result_status(result: Any) -> str:
    status = getattr(result, "status", None)
    if isinstance(result, dict):
        status = result.get("status", status)
    value = getattr(status, "value", status)
    return str(value or "").strip().lower()


def _result_request_id(result: Any) -> str:
    if isinstance(result, dict):
        return str(result.get("request_id") or result.get("requestId") or "unknown")
    return str(getattr(result, "request_id", "") or "unknown")


def _result_failure_detail(result: Any) -> str:
    gaps = result.get("gaps", ()) if isinstance(result, dict) else getattr(result, "gaps", ())
    for gap in tuple(gaps or ()):
        if isinstance(gap, dict):
            detail = gap.get("human_readable") or gap.get("reason")
        else:
            detail = getattr(gap, "human_readable", None) or getattr(getattr(gap, "reason", None), "value", None) or getattr(gap, "reason", None)
        text = str(detail or "").strip()
        if text:
            return text
    return ""
