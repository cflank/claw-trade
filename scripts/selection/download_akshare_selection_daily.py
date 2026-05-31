#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path
from typing import Mapping


QFQ_ADJUSTFLAG = "2"
STOCK_FIELDS = (
    "date",
    "code",
    "open",
    "high",
    "low",
    "close",
    "preclose",
    "volume",
    "amount",
    "adjustflag",
    "turn",
    "tradestatus",
    "pctChg",
    "isST",
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Download AkShare qfq daily history into local /select seed CSVs.")
    parser.add_argument("--root", type=Path, default=Path(".runtime/selection-data/raw/baostock"))
    parser.add_argument("--start-date", default="2015-01-01")
    parser.add_argument("--trade-date", required=True)
    parser.add_argument("--offset-stocks", type=int, default=0)
    parser.add_argument("--limit-stocks", type=int, default=0)
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument("--timeout-seconds", type=float, default=20.0)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--retry-sleep-seconds", type=float, default=1.0)
    parser.add_argument("--progress-interval", type=int, default=100)
    parser.add_argument("--skip-complete-existing", action="store_true")
    parser.add_argument("--run-label", default="")
    args = parser.parse_args()

    root = args.root.expanduser()
    universe = _read_rows(root / "universe" / "a_share_stock_universe.csv")
    if not universe:
        raise SystemExit("missing universe/a_share_stock_universe.csv")
    stock_slice = universe[args.offset_stocks :]
    if args.limit_stocks > 0:
        stock_slice = stock_slice[: args.limit_stocks]

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
                timeout_seconds=args.timeout_seconds,
                retries=args.retries,
                retry_sleep_seconds=args.retry_sleep_seconds,
                skip_complete_existing=args.skip_complete_existing,
            ): row
            for row in stock_slice
        }
        for completed, future in enumerate(as_completed(futures), start=1):
            result = future.result()
            if result["status"] == "success" or result["status"] == "skipped":
                successes.append(result)
            else:
                failures.append(result)
            if completed % max(args.progress_interval, 1) == 0:
                print(
                    f"stocks {completed}/{len(stock_slice)} "
                    f"success={len(successes)} failures={len(failures)} elapsed={time.time() - started:.1f}s",
                    flush=True,
                )

    summary = {
        "status": "completed" if not failures else "failed",
        "provider": "akshare.stock_zh_a_hist",
        "adjust": "qfq",
        "adjustflag": QFQ_ADJUSTFLAG,
        "root": str(root),
        "run_label": args.run_label,
        "start_date": args.start_date,
        "trade_date": args.trade_date,
        "offset_stocks": args.offset_stocks,
        "limit_stocks": args.limit_stocks,
        "stock_slice_count": len(stock_slice),
        "success_count": len(successes),
        "failure_count": len(failures),
        "failures": failures[:100],
        "success_samples": successes[:20],
        "elapsed_seconds": round(time.time() - started, 3),
        "volume_unit": "shares",
        "amount_unit": "CNY",
    }
    manifest_dir = root / "manifest"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    label = args.run_label or args.trade_date
    summary_path = manifest_dir / f"akshare-selection-daily-{label}.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    source_manifest = {
        "daily_qfq_source": "akshare.stock_zh_a_hist",
        "adjust": "qfq",
        "adjustflag": QFQ_ADJUSTFLAG,
        "start_date": args.start_date,
        "trade_date": args.trade_date,
        "latest_run_manifest": str(summary_path),
    }
    (manifest_dir / "daily_source.json").write_text(
        json.dumps(source_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0 if not failures else 3


def _download_one(
    *,
    root: Path,
    row: Mapping[str, str],
    start_date: str,
    trade_date: str,
    timeout_seconds: float,
    retries: int,
    retry_sleep_seconds: float,
    skip_complete_existing: bool,
) -> Mapping[str, object]:
    import akshare as ak

    raw_code = str(row.get("code") or "").strip().lower()
    code = raw_code.split(".", 1)[1] if "." in raw_code else raw_code
    path = root / "daily" / "qfq" / f"{raw_code}.csv"
    if skip_complete_existing and _has_complete_daily(path=path, start_date=start_date, trade_date=trade_date):
        audit = _audit_daily(path)
        return {"status": "skipped", "code": raw_code, **audit}

    last_error: Exception | None = None
    for attempt in range(max(retries, 0) + 1):
        try:
            frame = ak.stock_zh_a_hist(
                symbol=code,
                period="daily",
                start_date=start_date.replace("-", ""),
                end_date=trade_date.replace("-", ""),
                adjust="qfq",
                timeout=timeout_seconds,
            )
            rows = _map_akshare_rows(frame=frame, raw_code=raw_code, company_name=str(row.get("code_name") or ""))
            if not rows:
                return {"status": "failed", "code": raw_code, "error": "empty result"}
            _write_rows(path, rows)
            audit = _audit_daily(path)
            return {"status": "success", "code": raw_code, **audit}
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt < retries:
                time.sleep(retry_sleep_seconds)
    return {"status": "failed", "code": raw_code, "error": str(last_error)}


def _map_akshare_rows(*, frame: object, raw_code: str, company_name: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    is_st = 1 if "ST" in company_name.upper() else 0
    records = frame.to_dict("records")
    for record in records:
        close = _float(record.get("收盘"))
        pct_chg = _float(record.get("涨跌幅"))
        preclose = ""
        if close is not None and pct_chg is not None and abs(1 + pct_chg / 100) > 1e-9:
            preclose = round(close / (1 + pct_chg / 100), 4)
        volume_lot = _float(record.get("成交量"))
        rows.append(
            {
                "date": str(record.get("日期"))[:10],
                "code": raw_code,
                "open": _float(record.get("开盘")),
                "high": _float(record.get("最高")),
                "low": _float(record.get("最低")),
                "close": close,
                "preclose": preclose,
                "volume": int(volume_lot * 100) if volume_lot is not None else "",
                "amount": _float(record.get("成交额")),
                "adjustflag": QFQ_ADJUSTFLAG,
                "turn": _float(record.get("换手率")),
                "tradestatus": 1,
                "pctChg": pct_chg,
                "isST": is_st,
            }
        )
    return rows


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_rows(path: Path, rows: list[Mapping[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=STOCK_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def _has_complete_daily(*, path: Path, start_date: str, trade_date: str) -> bool:
    audit = _audit_daily(path)
    return bool(
        audit["row_count"] > 0
        and audit["first_date"] <= start_date
        and audit["last_date"] >= trade_date
        and audit["adjustflag_mismatch_count"] == 0
    )


def _audit_daily(path: Path) -> Mapping[str, object]:
    if not path.exists() or path.stat().st_size == 0:
        return {"row_count": 0, "first_date": "", "last_date": "", "adjustflag_mismatch_count": 0}
    row_count = 0
    first_date = ""
    last_date = ""
    mismatch_count = 0
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            row_count += 1
            row_date = str(row.get("date") or "")
            first_date = first_date or row_date
            last_date = row_date or last_date
            if str(row.get("adjustflag") or "") != QFQ_ADJUSTFLAG:
                mismatch_count += 1
    return {
        "row_count": row_count,
        "first_date": first_date,
        "last_date": last_date,
        "adjustflag_mismatch_count": mismatch_count,
    }


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
