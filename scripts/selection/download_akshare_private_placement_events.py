#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import socket
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

EMPTY_EVENT_COLUMNS = ("公告日期", "发行方式")


def main() -> int:
    parser = argparse.ArgumentParser(description="Download AkShare add-stock/private-placement cache.")
    parser.add_argument("--baostock-root", type=Path, default=Path(".runtime/selection-data/raw/baostock"))
    parser.add_argument("--root", type=Path, default=Path(".runtime/selection-data/raw/akshare_stock_add_stock"))
    parser.add_argument("--trade-date", required=True)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--offset-stocks", type=int, default=0)
    parser.add_argument("--limit-stocks", type=int, default=0)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--retry-sleep-seconds", type=float, default=2.0)
    parser.add_argument("--socket-timeout-seconds", type=float, default=15.0)
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument("--progress-interval", type=int, default=500)
    parser.add_argument("--run-label", default="")
    args = parser.parse_args()

    socket.setdefaulttimeout(args.socket_timeout_seconds)
    tickers = _load_tickers(args.baostock_root.expanduser(), trade_date=args.trade_date)
    tickers = tickers[args.offset_stocks :]
    if args.limit_stocks > 0:
        tickers = tickers[: args.limit_stocks]
    if not tickers:
        print("no A-share tickers found in baostock universe", file=sys.stderr)
        return 2

    root = args.root.expanduser()
    root.mkdir(parents=True, exist_ok=True)
    failures: list[dict[str, str]] = []
    completed = 0
    started_at = datetime.now(timezone.utc).isoformat()
    with ThreadPoolExecutor(max_workers=max(args.workers, 1)) as executor:
        futures = {
            executor.submit(
                _download_one,
                root=root,
                ticker=ticker,
                retries=args.retries,
                retry_sleep_seconds=args.retry_sleep_seconds,
                skip_existing=args.skip_existing,
            ): ticker
            for ticker in tickers
        }
        for future in as_completed(futures):
            completed += 1
            result = future.result()
            if result["status"] != "success":
                failures.append(result)
            if completed % max(args.progress_interval, 1) == 0:
                print(f"events {completed}/{len(tickers)} failures={len(failures)}", flush=True)

    summary = {
        "status": "completed" if not failures else "failed",
        "run_label": args.run_label,
        "started_at": started_at,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "trade_date": args.trade_date,
        "offset_stocks": args.offset_stocks,
        "limit_stocks": args.limit_stocks,
        "stock_count": len(tickers),
        "success_count": len(tickers) - len(failures),
        "failure_count": len(failures),
        "failures": failures[:100],
        "root": str(root),
        "source": "akshare.stock_add_stock:sina",
    }
    manifest_dir = root / "manifest"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    label = args.run_label or args.trade_date
    manifest_path = manifest_dir / f"akshare-stock-add-stock-{label}.json"
    manifest_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not failures else 3


def _download_one(
    *,
    root: Path,
    ticker: str,
    retries: int,
    retry_sleep_seconds: float,
    skip_existing: bool,
) -> dict[str, str]:
    code = ticker.split(".", 1)[0]
    path = root / f"{code}.csv"
    if skip_existing and path.exists():
        return {"ticker": ticker, "status": "success", "path": str(path), "mode": "skipped_existing"}

    import akshare as ak

    last_error = ""
    for attempt in range(retries + 1):
        try:
            df = ak.stock_add_stock(symbol=code)
            if df is None or df.empty:
                _write_empty(path)
            else:
                df.to_csv(path, index=False)
            return {"ticker": ticker, "status": "success", "path": str(path), "mode": "downloaded"}
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc)
            if _is_empty_event_error(last_error):
                _write_empty(path)
                return {"ticker": ticker, "status": "success", "path": str(path), "mode": "empty_event"}
            if attempt < retries:
                time.sleep(retry_sleep_seconds)
    return {"ticker": ticker, "status": "failed", "error": last_error}


def _write_empty(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=EMPTY_EVENT_COLUMNS)
        writer.writeheader()


def _is_empty_event_error(message: str) -> bool:
    return (
        "No tables found" in message
        or "No table" in message
        or "exceptions must derive from BaseException" in message
        or "not have 股票增发" in message
    )


def _load_tickers(baostock_root: Path, *, trade_date: str) -> tuple[str, ...]:
    universe_file = _select_universe_file(baostock_root, trade_date=trade_date)
    if universe_file is None:
        return ()
    tickers: list[str] = []
    with universe_file.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            raw_code = str(row.get("code") or "").strip().lower()
            if _is_a_share_code(raw_code):
                tickers.append(_ticker_from_baostock_code(raw_code))
    return tuple(tickers)


def _select_universe_file(root: Path, *, trade_date: str) -> Path | None:
    universe_dir = root / "universe"
    files = sorted(universe_dir.glob("query_all_stock_*.csv"))
    eligible = [path for path in files if path.stem.removeprefix("query_all_stock_") <= trade_date]
    if eligible:
        return eligible[-1]
    fallback = universe_dir / "a_share_stock_universe.csv"
    return fallback if fallback.exists() else None


def _is_a_share_code(raw_code: str) -> bool:
    if "." not in raw_code:
        return False
    market, code = raw_code.split(".", 1)
    if len(code) != 6 or not code.isdigit():
        return False
    if market == "sh":
        return code.startswith(("600", "601", "603", "605", "688", "689"))
    if market == "sz":
        return code.startswith(("000", "001", "002", "003", "300", "301"))
    if market == "bj":
        return code.startswith(("43", "83", "87", "88", "92"))
    return False


def _ticker_from_baostock_code(raw_code: str) -> str:
    market, code = raw_code.split(".", 1)
    return f"{code}.{market.upper()}"


if __name__ == "__main__":
    raise SystemExit(main())
