#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Mapping

INDEX_FIELDS = ("date", "code", "open", "high", "low", "close", "preclose", "volume", "amount", "pctChg")


def main() -> int:
    parser = argparse.ArgumentParser(description="Download AkShare index daily history into local /select seed CSVs.")
    parser.add_argument("--root", type=Path, default=Path(".runtime/selection-data/raw/baostock"))
    parser.add_argument("--start-date", default="2015-01-01")
    parser.add_argument("--trade-date", required=True)
    parser.add_argument("--offset-indices", type=int, default=0)
    parser.add_argument("--limit-indices", type=int, default=0)
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--retry-sleep-seconds", type=float, default=1.0)
    parser.add_argument("--progress-interval", type=int, default=100)
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument("--run-label", default="")
    args = parser.parse_args()

    root = args.root.expanduser()
    index_rows = _read_rows(root / "universe" / "index_universe.csv")
    if not index_rows:
        raise SystemExit("missing universe/index_universe.csv")
    index_slice = index_rows[args.offset_indices :]
    if args.limit_indices > 0:
        index_slice = index_slice[: args.limit_indices]

    successes: list[Mapping[str, object]] = []
    failures: list[Mapping[str, object]] = []
    started = time.time()
    with ThreadPoolExecutor(max_workers=max(args.threads, 1)) as executor:
        futures = {
            executor.submit(
                _download_one,
                root=root,
                row=row,
                start_date=args.start_date,
                trade_date=args.trade_date,
                retries=args.retries,
                retry_sleep_seconds=args.retry_sleep_seconds,
                skip_existing=args.skip_existing,
            ): row
            for row in index_slice
        }
        for completed, future in enumerate(as_completed(futures), start=1):
            result = future.result()
            if result["status"] in {"success", "skipped"}:
                successes.append(result)
            else:
                failures.append(result)
            if completed % max(args.progress_interval, 1) == 0:
                print(
                    f"indices {completed}/{len(index_slice)} "
                    f"success={len(successes)} failures={len(failures)} elapsed={time.time() - started:.1f}s",
                    flush=True,
                )

    summary = {
        "status": "completed" if not failures else "failed",
        "provider": "akshare.stock_zh_index_daily_em",
        "root": str(root),
        "run_label": args.run_label,
        "start_date": args.start_date,
        "trade_date": args.trade_date,
        "offset_indices": args.offset_indices,
        "limit_indices": args.limit_indices,
        "index_slice_count": len(index_slice),
        "success_count": len(successes),
        "failure_count": len(failures),
        "failures": failures[:100],
        "success_samples": successes[:20],
        "elapsed_seconds": round(time.time() - started, 3),
        "amount_unit": "CNY",
    }
    manifest_dir = root / "manifest"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    label = args.run_label or args.trade_date
    summary_path = manifest_dir / f"akshare-selection-index-daily-{label}.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0 if not failures else 3


def _download_one(
    *,
    root: Path,
    row: Mapping[str, str],
    start_date: str,
    trade_date: str,
    retries: int,
    retry_sleep_seconds: float,
    skip_existing: bool,
) -> Mapping[str, object]:
    import akshare as ak

    raw_code = str(row.get("code") or "").strip().lower()
    symbol = raw_code.replace(".", "")
    path = root / "index_daily" / f"{raw_code}.csv"
    if skip_existing and _has_trade_date(path, trade_date):
        return {"status": "skipped", "code": raw_code, **_audit(path)}

    last_error: Exception | None = None
    for attempt in range(max(retries, 0) + 1):
        try:
            frame = ak.stock_zh_index_daily_em(
                symbol=symbol,
                start_date=start_date.replace("-", ""),
                end_date=trade_date.replace("-", ""),
            )
            rows = _map_rows(frame=frame, raw_code=raw_code)
            if not rows:
                return {"status": "failed", "code": raw_code, "error": "empty result"}
            _write_rows(path, rows)
            return {"status": "success", "code": raw_code, **_audit(path)}
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt < retries:
                time.sleep(retry_sleep_seconds)
    return {"status": "failed", "code": raw_code, "error": str(last_error)}


def _map_rows(*, frame: object, raw_code: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    previous_close: float | None = None
    for record in frame.to_dict("records"):
        close = _float(record.get("close"))
        pct_chg = ""
        if close is not None and previous_close is not None and abs(previous_close) > 1e-9:
            pct_chg = round((close / previous_close - 1) * 100, 6)
        rows.append(
            {
                "date": str(record.get("date") or "")[:10],
                "code": raw_code,
                "open": _float(record.get("open")),
                "high": _float(record.get("high")),
                "low": _float(record.get("low")),
                "close": close,
                "preclose": previous_close if previous_close is not None else "",
                "volume": _float(record.get("volume")),
                "amount": _float(record.get("amount")),
                "pctChg": pct_chg,
            }
        )
        previous_close = close
    return rows


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_rows(path: Path, rows: list[Mapping[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=INDEX_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def _has_trade_date(path: Path, trade_date: str) -> bool:
    return bool(_audit(path)["last_date"] >= trade_date)


def _audit(path: Path) -> Mapping[str, object]:
    if not path.exists() or path.stat().st_size == 0:
        return {"row_count": 0, "first_date": "", "last_date": ""}
    row_count = 0
    first_date = ""
    last_date = ""
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            row_count += 1
            row_date = str(row.get("date") or "")
            first_date = first_date or row_date
            last_date = row_date or last_date
    return {"row_count": row_count, "first_date": first_date, "last_date": last_date}


def _float(value: object) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text or text.lower() == "nan":
        return None
    try:
        return float(text)
    except ValueError:
        return None


if __name__ == "__main__":
    raise SystemExit(main())
