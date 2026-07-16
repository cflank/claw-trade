from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Sequence


# Guard source:
# - docs/项目文档/工控机第一版部署验证门与最小生产包实施计划.md:104
#   requires CN_A daily_bar and valuation_metric for the latest 300 trading days.
# - src/claw_trade/data_gateway/_selection_batch.py:82,86-87
#   defines usable CN_A selection history/full-market coverage as 250 days,
#   at least 5,000 symbols, and 80% coverage.
_MIN_HISTORY_DAYS = 250
_MIN_FULL_MARKET_SYMBOLS = 5_000
_MIN_HISTORY_COVERAGE_RATIO = 0.8
_REQUIRED_MARKET_DAYS = 300
_REQUIRED_DATASETS = ("daily_bar", "valuation_metric")


def validate_cn_a_factory_seed(
    columnar_root: Path,
    *,
    label: str,
    manifest_records: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    import duckdb

    dataset_results: dict[str, dict[str, Any]] = {}
    for dataset in _REQUIRED_DATASETS:
        dataset_root = columnar_root / "market=CN_A" / f"dataset={dataset}" / "granularity=daily"
        parquet_files = tuple(sorted(path for path in dataset_root.glob("*.parquet") if path.is_file()))
        if not parquet_files:
            raise ValueError(f"{label} missing CN_A {dataset} Parquet files under {dataset_root}")
        manifest_paths = {
            str(record.get("relative_path") or "").strip()
            for record in manifest_records
            if record.get("market") == "CN_A"
            and record.get("dataset") == dataset
            and record.get("granularity") == "daily"
            and record.get("storage") == "parquet"
            and record.get("status") == "active"
        }
        parquet_paths = {path.relative_to(columnar_root).as_posix() for path in parquet_files}
        missing_manifest_paths = sorted(parquet_paths - manifest_paths)
        if missing_manifest_paths:
            raise ValueError(
                f"{label} CN_A {dataset} Parquet files missing active manifest records: "
                f"count={len(missing_manifest_paths)}, sample={missing_manifest_paths[:5]}"
            )
        missing_parquet_paths = sorted(manifest_paths - parquet_paths)
        if missing_parquet_paths:
            raise ValueError(
                f"{label} CN_A {dataset} active manifest records point to missing Parquet files: "
                f"count={len(missing_parquet_paths)}, sample={missing_parquet_paths[:5]}"
            )

        with duckdb.connect(":memory:") as conn:
            row_count, market_days, period_start, period_end, symbol_count, covered_symbol_count = conn.execute(
                """
                with source as (
                  select symbol_id, period_start
                  from read_parquet(?, union_by_name=true)
                  where coalesce(trim(symbol_id), '') != ''
                ),
                symbol_history as (
                  select symbol_id, count(distinct period_start) as history_days
                  from source
                  group by symbol_id
                )
                select
                  (select count(*)::BIGINT from source),
                  (select count(distinct period_start)::BIGINT from source),
                  (select min(period_start) from source),
                  (select max(period_start) from source),
                  count(*)::BIGINT,
                  count(*) filter (where history_days >= ?)::BIGINT
                from symbol_history
                """,
                [[str(path) for path in parquet_files], _MIN_HISTORY_DAYS],
            ).fetchone()

        row_count = int(row_count or 0)
        market_days = int(market_days or 0)
        symbol_count = int(symbol_count or 0)
        covered_symbol_count = int(covered_symbol_count or 0)
        if row_count == 0:
            raise ValueError(f"{label} CN_A {dataset} contains zero rows under {dataset_root}")
        if market_days < _REQUIRED_MARKET_DAYS:
            raise ValueError(
                f"{label} CN_A {dataset} trading-day coverage insufficient: "
                f"days={market_days}, required={_REQUIRED_MARKET_DAYS}, "
                f"period={period_start}..{period_end}, root={columnar_root}"
            )
        if symbol_count < _MIN_FULL_MARKET_SYMBOLS:
            raise ValueError(
                f"{label} CN_A {dataset} full-market coverage insufficient: "
                f"symbols={symbol_count}, required={_MIN_FULL_MARKET_SYMBOLS}, root={columnar_root}"
            )
        history_coverage_ratio = covered_symbol_count / symbol_count
        if history_coverage_ratio < _MIN_HISTORY_COVERAGE_RATIO:
            raise ValueError(
                f"{label} CN_A {dataset} history coverage insufficient: "
                f"covered={covered_symbol_count}, symbols={symbol_count}, min_days={_MIN_HISTORY_DAYS}, "
                f"coverage={history_coverage_ratio:.3f}, required={_MIN_HISTORY_COVERAGE_RATIO:.3f}, "
                f"root={columnar_root}"
            )

        dataset_results[dataset] = {
            "rows": row_count,
            "parquet_files": len(parquet_files),
            "market_days": market_days,
            "period_start": str(period_start),
            "period_end": str(period_end),
            "symbols": symbol_count,
            "symbols_with_min_history": covered_symbol_count,
            "min_history_days": _MIN_HISTORY_DAYS,
            "history_coverage_ratio": history_coverage_ratio,
        }

    daily_root = columnar_root / "market=CN_A" / "dataset=daily_bar" / "granularity=daily"
    daily_files = tuple(sorted(path for path in daily_root.glob("*.parquet") if path.is_file()))
    with duckdb.connect(":memory:") as conn:
        missing_count = int(
            conn.execute(
                """
                select count(*)::BIGINT
                from read_parquet(?, union_by_name=true)
                where coalesce(trim(json_extract_string(row_json, ?)), '') = ''
                """,
                [[str(path) for path in daily_files], "$.company_name"],
            ).fetchone()[0]
            or 0
        )
        sample_symbols = [
            str(row[0])
            for row in conn.execute(
                """
                select distinct symbol_id
                from read_parquet(?, union_by_name=true)
                where coalesce(trim(json_extract_string(row_json, ?)), '') = ''
                limit 5
                """,
                [[str(path) for path in daily_files], "$.company_name"],
            ).fetchall()
        ]
    if missing_count:
        raise ValueError(
            f"{label} CN_A daily_bar company_name missing: "
            f"missing={missing_count}, rows={dataset_results['daily_bar']['rows']}, "
            f"sample_symbols={sample_symbols}, root={columnar_root}"
        )
    dataset_results["daily_bar"]["missing_company_name"] = 0

    return {
        "market": "CN_A",
        "minimum_full_market_symbols": _MIN_FULL_MARKET_SYMBOLS,
        "datasets": dataset_results,
    }
