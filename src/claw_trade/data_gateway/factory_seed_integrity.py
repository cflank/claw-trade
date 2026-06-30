from __future__ import annotations

from pathlib import Path
from typing import Any


def validate_cn_a_daily_bar_company_names(columnar_root: Path, *, label: str) -> dict[str, Any]:
    daily_root = columnar_root / "market=CN_A" / "dataset=daily_bar" / "granularity=daily"
    parquet_files = tuple(sorted(path for path in daily_root.glob("*.parquet") if path.is_file()))
    if not parquet_files:
        raise ValueError(f"{label} missing CN_A daily_bar Parquet files under {daily_root}")

    import duckdb

    row_count = 0
    missing_count = 0
    sample_symbols: list[str] = []
    with duckdb.connect(":memory:") as conn:
        for parquet_file in parquet_files:
            file_row_count, file_missing_count = conn.execute(
                """
                select
                  count(*)::BIGINT,
                  sum(
                    case
                      when coalesce(trim(json_extract_string(row_json, ?)), '') = '' then 1
                      else 0
                    end
                  )::BIGINT
                from read_parquet(?)
                """,
                ["$.company_name", str(parquet_file)],
            ).fetchone()
            row_count += int(file_row_count or 0)
            file_missing = int(file_missing_count or 0)
            missing_count += file_missing
            if file_missing and len(sample_symbols) < 5:
                remaining = 5 - len(sample_symbols)
                sample_symbols.extend(
                    str(row[0])
                    for row in conn.execute(
                        """
                        select symbol_id
                        from read_parquet(?)
                        where coalesce(trim(json_extract_string(row_json, ?)), '') = ''
                        limit ?
                        """,
                        [str(parquet_file), "$.company_name", remaining],
                    ).fetchall()
                )

    if row_count == 0:
        raise ValueError(f"{label} CN_A daily_bar contains zero rows under {daily_root}")
    if missing_count:
        raise ValueError(
            f"{label} CN_A daily_bar company_name missing: "
            f"missing={missing_count}, rows={row_count}, sample_symbols={sample_symbols}, root={columnar_root}"
        )

    return {
        "market": "CN_A",
        "dataset": "daily_bar",
        "rows": row_count,
        "missing_company_name": missing_count,
        "parquet_files": len(parquet_files),
    }
