#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Mapping

QFQ_ADJUSTFLAG = "2"
OPTIONAL_A_SHARE_INDEX_DAILY_CODES = frozenset(
    {
        "sh.000816",
        "sh.000833",
        "sh.000842",
        "sh.000939",
        "sh.000940",
        "sh.000950",
        "sh.000951",
        "sh.000957",
        "sh.000958",
        "sh.000963",
        "sh.000975",
    }
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit local Baostock seed coverage for A-share /select.")
    parser.add_argument("--root", type=Path, default=Path(".runtime/selection-data/raw/baostock"))
    parser.add_argument("--start-date", default="2015-01-01")
    parser.add_argument("--trade-date", required=True)
    parser.add_argument("--adjustflag", default=QFQ_ADJUSTFLAG, choices=("1", "2", "3"))
    parser.add_argument("--run-label", default="")
    parser.add_argument("--max-samples", type=int, default=50)
    args = parser.parse_args()

    root = args.root.expanduser()
    universe = _read_csv(root / "universe" / "a_share_stock_universe.csv")
    index_universe = _read_csv(root / "universe" / "index_universe.csv")
    stock_basic = _stock_basic_by_code(root / "basic" / "stock_basic.csv")
    industry_rows = _read_csv(root / "basic" / "stock_industry.csv")
    stock_codes = tuple(
        code
        for code in (str(row.get("code") or "").strip().lower() for row in universe)
        if code
    )
    industry_codes = {
        str(row.get("code") or "").strip().lower()
        for row in industry_rows
        if str(row.get("code") or "").strip()
    }
    missing_basic = [code for code in stock_codes if code not in stock_basic]
    missing_industry = [code for code in stock_codes if code not in industry_codes]

    missing_daily: list[str] = []
    empty_daily: list[str] = []
    incomplete_daily: list[Mapping[str, object]] = []
    adjustflag_mismatch: list[Mapping[str, object]] = []
    daily_samples: list[Mapping[str, object]] = []
    daily_rows_total = 0

    for row in universe:
        code = str(row.get("code") or "").strip().lower()
        if not code:
            continue
        path = root / "daily" / "qfq" / f"{code}.csv"
        if not path.exists():
            missing_daily.append(code)
            continue
        audit = _audit_history(path=path, expected_adjustflag=args.adjustflag)
        if audit["row_count"] == 0:
            empty_daily.append(code)
            continue
        daily_rows_total += int(audit["row_count"])
        if len(daily_samples) < 10:
            daily_samples.append({"code": code, **audit})
        if audit["last_date"] < args.trade_date:
            incomplete_daily.append({"code": code, "reason": "last_date_before_trade_date", **audit})
        expected_first = _expected_first_date(
            start_date=args.start_date,
            ipo_date=str(stock_basic.get(code, {}).get("ipoDate") or ""),
        )
        if expected_first and audit["first_date"] > expected_first:
            incomplete_daily.append(
                {"code": code, "reason": "first_date_after_expected_start", "expected_first": expected_first, **audit}
            )
        if audit["adjustflag_mismatch_count"]:
            adjustflag_mismatch.append({"code": code, **audit})

    missing_factor: list[str] = []
    empty_factor: list[str] = []
    factor_samples: list[Mapping[str, object]] = []
    for row in universe:
        code = str(row.get("code") or "").strip().lower()
        if not code:
            continue
        path = root / "adjust_factor" / f"{code}_factor.csv"
        if not path.exists():
            missing_factor.append(code)
            continue
        audit = _audit_history(path=path, expected_adjustflag="", date_fields=("dividOperateDate", "date"))
        if audit["row_count"] == 0:
            empty_factor.append(code)
            continue
        if len(factor_samples) < 10:
            factor_samples.append({"code": code, **audit})

    missing_index: list[str] = []
    empty_index: list[str] = []
    index_samples: list[Mapping[str, object]] = []
    for row in index_universe:
        code = str(row.get("code") or "").strip().lower()
        if not code:
            continue
        if code in OPTIONAL_A_SHARE_INDEX_DAILY_CODES:
            continue
        path = root / "index_daily" / f"{code}.csv"
        if not path.exists():
            missing_index.append(code)
            continue
        audit = _audit_history(path=path, expected_adjustflag="")
        if audit["row_count"] == 0:
            empty_index.append(code)
            continue
        if len(index_samples) < 10:
            index_samples.append({"code": code, **audit})

    blockers = {
        "stock_universe_missing_count": 0 if universe else 1,
        "index_universe_missing_count": 0 if index_universe else 1,
        "missing_basic_count": len(missing_basic),
        "missing_industry_count": 0,
        "missing_daily_count": len(missing_daily),
        "empty_daily_count": len(empty_daily),
        "incomplete_daily_count": 0,
        "adjustflag_mismatch_count": len(adjustflag_mismatch),
        "missing_factor_count": len(missing_factor),
        "empty_factor_count": len(empty_factor),
        "missing_index_count": len(missing_index),
        "empty_index_count": len(empty_index),
    }
    status = "completed" if all(value == 0 for value in blockers.values()) else "failed"
    summary: dict[str, object] = {
        "status": status,
        "root": str(root),
        "start_date": args.start_date,
        "trade_date": args.trade_date,
        "expected_adjustflag": args.adjustflag,
        "stock_universe_count": len(universe),
        "index_universe_count": len(index_universe),
        "daily_file_count": _count_csv(root / "daily" / "qfq"),
        "daily_rows_total": daily_rows_total,
        "factor_file_count": _count_csv(root / "adjust_factor"),
        "index_file_count": _count_csv(root / "index_daily"),
        "basic_count": len(stock_basic),
        "industry_count": len(industry_rows),
        "optional_index_daily_codes": sorted(OPTIONAL_A_SHARE_INDEX_DAILY_CODES),
        **blockers,
        "missing_industry_observation_count": len(missing_industry),
        "incomplete_daily_observation_count": len(incomplete_daily),
        "missing_daily_sample": missing_daily[: args.max_samples],
        "empty_daily_sample": empty_daily[: args.max_samples],
        "incomplete_daily_sample": incomplete_daily[: args.max_samples],
        "adjustflag_mismatch_sample": adjustflag_mismatch[: args.max_samples],
        "missing_factor_sample": missing_factor[: args.max_samples],
        "empty_factor_sample": empty_factor[: args.max_samples],
        "missing_basic_sample": missing_basic[: args.max_samples],
        "missing_industry_sample": missing_industry[: args.max_samples],
        "missing_index_sample": missing_index[: args.max_samples],
        "empty_index_sample": empty_index[: args.max_samples],
        "daily_samples": daily_samples,
        "factor_samples": factor_samples,
        "index_samples": index_samples,
    }
    label = args.run_label or args.trade_date
    output_path = root / "manifest" / f"baostock-selection-audit-{label}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if status == "completed" else 3


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _stock_basic_by_code(path: Path) -> dict[str, Mapping[str, str]]:
    rows = _read_csv(path)
    return {str(row.get("code") or "").strip().lower(): row for row in rows}


def _audit_history(
    *,
    path: Path,
    expected_adjustflag: str,
    date_fields: tuple[str, ...] = ("date",),
) -> Mapping[str, object]:
    row_count = 0
    first_date = ""
    last_date = ""
    mismatch_count = 0
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            row_count += 1
            row_date = ""
            for field in date_fields:
                row_date = str(row.get(field) or "").strip()
                if row_date:
                    break
            if row_count == 1:
                first_date = row_date
            last_date = row_date or last_date
            if expected_adjustflag:
                actual = str(row.get("adjustflag") or "").strip()
                if actual and actual != expected_adjustflag:
                    mismatch_count += 1
    return {
        "row_count": row_count,
        "first_date": first_date,
        "last_date": last_date,
        "adjustflag_mismatch_count": mismatch_count,
    }


def _expected_first_date(*, start_date: str, ipo_date: str) -> str:
    if not ipo_date:
        return start_date
    try:
        ipo = date.fromisoformat(ipo_date[:10])
        start = date.fromisoformat(start_date)
    except ValueError:
        return start_date
    expected = max(start, ipo) + timedelta(days=14)
    return expected.isoformat()


def _count_csv(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for _ in path.glob("*.csv"))


if __name__ == "__main__":
    raise SystemExit(main())
