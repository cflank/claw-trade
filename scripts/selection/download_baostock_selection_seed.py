#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import signal
import socket
import sys
import time
from datetime import date
from pathlib import Path
from typing import Iterable, Mapping


STOCK_FIELDS = (
    "date,code,open,high,low,close,preclose,volume,amount,"
    "adjustflag,turn,tradestatus,pctChg,isST"
)
INDEX_FIELDS = "date,code,open,high,low,close,preclose,volume,amount,pctChg"
QFQ_ADJUSTFLAG = "2"
INDEX_ADJUSTFLAG = "3"


def main() -> int:
    parser = argparse.ArgumentParser(description="Download local Baostock seed data for A-share /select.")
    parser.add_argument("--root", type=Path, default=Path(".runtime/selection-data/raw/baostock"))
    parser.add_argument("--start-date", default="2015-01-01")
    parser.add_argument("--factor-start-date", default="1990-01-01")
    parser.add_argument("--trade-date", default="")
    parser.add_argument(
        "--adjustflag",
        default=QFQ_ADJUSTFLAG,
        choices=("1", "2", "3"),
        help="Stock daily adjustment flag. Baostock: 1=hfq, 2=qfq, 3=raw.",
    )
    parser.add_argument("--download-kinds", default="qfq,factor,index,basic,industry")
    parser.add_argument("--offset-stocks", type=int, default=0)
    parser.add_argument("--limit-stocks", type=int, default=0)
    parser.add_argument("--offset-indices", type=int, default=0)
    parser.add_argument("--limit-indices", type=int, default=0)
    parser.add_argument("--socket-timeout-seconds", type=float, default=20.0)
    parser.add_argument("--query-timeout-seconds", type=int, default=45)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--retry-sleep-seconds", type=float, default=2.0)
    parser.add_argument("--no-manifest", action="store_true")
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument("--use-existing-universe", action="store_true")
    parser.add_argument(
        "--skip-complete-existing",
        action="store_true",
        help="Skip only when the existing CSV already covers start/trade date and expected adjustment flag.",
    )
    parser.add_argument("--progress-interval", type=int, default=200)
    parser.add_argument("--run-label", default="")
    args = parser.parse_args()

    import baostock as bs

    socket.setdefaulttimeout(args.socket_timeout_seconds)
    root = args.root.expanduser()
    _ensure_dirs(root)
    download_kinds = {kind.strip() for kind in args.download_kinds.split(",") if kind.strip()}
    if "qfq" in download_kinds and args.adjustflag != QFQ_ADJUSTFLAG:
        print("download-kind qfq requires --adjustflag 2 (Baostock 前复权)", file=sys.stderr)
        return 2
    _login_or_raise(bs)

    failures: list[dict[str, str]] = []
    try:
        trade_date = args.trade_date.strip() or _latest_trading_date(bs)
        if not trade_date:
            print("could not resolve latest trading date", file=sys.stderr)
            return 2

        if args.use_existing_universe:
            universe_rows = _read_rows(root / "universe" / f"query_all_stock_{trade_date}.csv")
            if not universe_rows:
                universe_rows = _read_rows(root / "universe" / "a_share_stock_universe.csv")
            if not universe_rows:
                print(f"existing universe is missing for {trade_date}", file=sys.stderr)
                return 2
        else:
            universe_rows = _query_rows_with_retries(
                bs,
                lambda: bs.query_all_stock(day=trade_date),
                action="query_all_stock",
                retries=args.retries,
                retry_sleep_seconds=args.retry_sleep_seconds,
                query_timeout_seconds=args.query_timeout_seconds,
            )
        if not universe_rows:
            print(f"query_all_stock returned no rows for {trade_date}", file=sys.stderr)
            return 2
        if not args.use_existing_universe:
            _write_rows(root / "universe" / f"query_all_stock_{trade_date}.csv", universe_rows)

        stock_rows = [row for row in universe_rows if _is_a_share_code(str(row.get("code") or ""))]
        index_rows = [row for row in universe_rows if not _is_a_share_code(str(row.get("code") or ""))]
        if not args.use_existing_universe:
            _write_rows(root / "universe" / "a_share_stock_universe.csv", stock_rows)
            _write_rows(root / "universe" / "index_universe.csv", index_rows)

        basic_count = 0
        industry_count = 0
        if "basic" in download_kinds:
            try:
                basic_rows = _query_rows_with_retries(
                    bs,
                    lambda: bs.query_stock_basic(),
                    action="stock_basic",
                    retries=args.retries,
                    retry_sleep_seconds=args.retry_sleep_seconds,
                    query_timeout_seconds=args.query_timeout_seconds,
                )
                basic_count = len(basic_rows)
                _write_rows(root / "basic" / f"stock_basic_{trade_date}.csv", basic_rows)
                _write_rows(root / "basic" / "stock_basic.csv", basic_rows)
                if not basic_rows:
                    failures.append({"code": "ALL", "stage": "stock_basic", "error": "empty result"})
            except Exception as exc:  # noqa: BLE001
                failures.append({"code": "ALL", "stage": "stock_basic", "error": str(exc)})
        if "industry" in download_kinds:
            try:
                industry_rows = _query_rows_with_retries(
                    bs,
                    lambda: bs.query_stock_industry(date=trade_date),
                    action="stock_industry",
                    retries=args.retries,
                    retry_sleep_seconds=args.retry_sleep_seconds,
                    query_timeout_seconds=args.query_timeout_seconds,
                )
                industry_count = len(industry_rows)
                _write_rows(root / "basic" / f"stock_industry_{trade_date}.csv", industry_rows)
                _write_rows(root / "basic" / "stock_industry.csv", industry_rows)
                if not industry_rows:
                    failures.append({"code": "ALL", "stage": "stock_industry", "error": "empty result"})
            except Exception as exc:  # noqa: BLE001
                failures.append({"code": "ALL", "stage": "stock_industry", "error": str(exc)})

        stock_slice = stock_rows[args.offset_stocks :]
        if args.limit_stocks > 0:
            stock_slice = stock_slice[: args.limit_stocks]

        for index, row in enumerate(stock_slice, start=1):
            code = str(row["code"]).strip().lower()
            path = root / "daily" / "qfq" / f"{code}.csv"
            skip_daily = args.skip_existing and _has_trade_date(path, trade_date)
            skip_daily = skip_daily or (
                args.skip_complete_existing
                and _has_complete_daily(path, start_date=args.start_date, trade_date=trade_date, adjustflag=args.adjustflag)
            )
            if "qfq" in download_kinds and not skip_daily:
                try:
                    data = _query_rows_with_retries(
                        bs,
                        lambda code=code: bs.query_history_k_data_plus(
                            code,
                            STOCK_FIELDS,
                            start_date=args.start_date,
                            end_date=trade_date,
                            frequency="d",
                            adjustflag=args.adjustflag,
                        ),
                        action=f"daily:{code}",
                        retries=args.retries,
                        retry_sleep_seconds=args.retry_sleep_seconds,
                        query_timeout_seconds=args.query_timeout_seconds,
                    )
                    _write_rows(path, data)
                except Exception as exc:  # noqa: BLE001
                    failures.append({"code": code, "stage": "daily", "error": str(exc)})
            factor_path = root / "adjust_factor" / f"{code}_factor.csv"
            if "factor" in download_kinds and (not factor_path.exists() or not args.skip_existing):
                try:
                    factors = _query_rows_with_retries(
                        bs,
                        lambda code=code: bs.query_adjust_factor(code, args.factor_start_date, trade_date),
                        action=f"factor:{code}",
                        retries=args.retries,
                        retry_sleep_seconds=args.retry_sleep_seconds,
                        query_timeout_seconds=args.query_timeout_seconds,
                    )
                    _write_rows(factor_path, factors)
                except Exception as exc:  # noqa: BLE001
                    failures.append({"code": code, "stage": "factor", "error": str(exc)})
            if index % max(args.progress_interval, 1) == 0:
                print(
                    f"stocks {index}/{len(stock_slice)} "
                    f"offset={args.offset_stocks} failures={len(failures)}",
                    flush=True,
                )

        if "index" in download_kinds:
            index_slice = index_rows[args.offset_indices :]
            if args.limit_indices > 0:
                index_slice = index_slice[: args.limit_indices]
            for index, row in enumerate(index_slice, start=1):
                code = str(row["code"]).strip().lower()
                path = root / "index_daily" / f"{code}.csv"
                if args.skip_existing and _has_trade_date(path, trade_date):
                    continue
                if args.skip_complete_existing and _has_trade_date(path, trade_date):
                    continue
                try:
                    data = _query_rows_with_retries(
                        bs,
                        lambda code=code: bs.query_history_k_data_plus(
                            code,
                            INDEX_FIELDS,
                            start_date=args.start_date,
                            end_date=trade_date,
                            frequency="d",
                            adjustflag=INDEX_ADJUSTFLAG,
                        ),
                        action=f"index:{code}",
                        retries=args.retries,
                        retry_sleep_seconds=args.retry_sleep_seconds,
                        query_timeout_seconds=args.query_timeout_seconds,
                    )
                    _write_rows(path, data)
                except Exception as exc:  # noqa: BLE001
                    failures.append({"code": code, "stage": "index_daily", "error": str(exc)})
                if index % max(args.progress_interval, 1) == 0:
                    print(f"indices {index}/{len(index_slice)} failures={len(failures)}", flush=True)

        manifest_path = root / "manifest" / "baostock_files.sha256"
        if not args.no_manifest:
            _write_sha256_manifest(root, manifest_path)
        summary = {
            "status": "completed" if not failures else "failed",
            "run_label": args.run_label,
            "trade_date": trade_date,
            "start_date": args.start_date,
            "factor_start_date": args.factor_start_date,
            "adjustflag": args.adjustflag,
            "query_timeout_seconds": args.query_timeout_seconds,
            "download_kinds": sorted(download_kinds),
            "offset_stocks": args.offset_stocks,
            "limit_stocks": args.limit_stocks,
            "offset_indices": args.offset_indices,
            "limit_indices": args.limit_indices,
            "stock_count": len(stock_rows),
            "stock_slice_count": len(stock_slice),
            "index_count": len(index_rows),
            "basic_count": basic_count,
            "industry_count": industry_count,
            "failure_count": len(failures),
            "failures": failures[:50],
            "manifest_path": str(manifest_path) if not args.no_manifest else None,
        }
        label = args.run_label or trade_date
        summary_path = root / "manifest" / f"baostock-selection-seed-{label}.json"
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0 if not failures else 3
    finally:
        bs.logout()


def _ensure_dirs(root: Path) -> None:
    for relative in ("universe", "basic", "daily/qfq", "daily/raw", "adjust_factor", "index_daily", "manifest"):
        (root / relative).mkdir(parents=True, exist_ok=True)


def _latest_trading_date(bs_module: object) -> str:
    end = date.today().isoformat()
    start = f"{date.today().year}-01-01"
    rows = _rows_from_result(bs_module.query_trade_dates(start_date=start, end_date=end))
    trading_days = [str(row.get("calendar_date") or "") for row in rows if str(row.get("is_trading_day")) == "1"]
    return trading_days[-1] if trading_days else ""


def _rows_from_result(result: object) -> list[dict[str, str]]:
    error_code = str(getattr(result, "error_code", "0"))
    if error_code != "0":
        error_msg = str(getattr(result, "error_msg", ""))
        raise RuntimeError(error_msg or f"baostock error_code={error_code}")
    fields = list(getattr(result, "fields", ()))
    rows: list[dict[str, str]] = []
    while result.next():
        rows.append(dict(zip(fields, result.get_row_data(), strict=False)))
    return rows


def _query_rows_with_retries(
    bs_module: object,
    query: object,
    *,
    action: str,
    retries: int,
    retry_sleep_seconds: float,
    query_timeout_seconds: int,
) -> list[dict[str, str]]:
    last_error: Exception | None = None
    for attempt in range(max(retries, 0) + 1):
        try:
            return _call_with_timeout(query, timeout_seconds=query_timeout_seconds)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt >= retries:
                break
            _relogin(bs_module, action=action)
            time.sleep(retry_sleep_seconds)
    raise RuntimeError(str(last_error) if last_error is not None else f"{action} failed")


def _call_with_timeout(query: object, *, timeout_seconds: int) -> list[dict[str, str]]:
    if timeout_seconds <= 0:
        return _rows_from_result(query())

    def _raise_timeout(_signum: int, _frame: object) -> None:
        raise TimeoutError(f"baostock query exceeded {timeout_seconds}s")

    previous_handler = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, _raise_timeout)
    signal.alarm(timeout_seconds)
    try:
        return _rows_from_result(query())
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous_handler)


def _login_or_raise(bs_module: object) -> None:
    login_result = bs_module.login()
    if getattr(login_result, "error_code", "0") != "0":
        raise RuntimeError(f"baostock login failed: {getattr(login_result, 'error_msg', '')}")


def _relogin(bs_module: object, *, action: str) -> None:
    try:
        bs_module.logout()
    except Exception:  # noqa: BLE001
        pass
    try:
        _login_or_raise(bs_module)
    except Exception as exc:  # noqa: BLE001
        print(f"baostock relogin failed after {action}: {exc}", file=sys.stderr, flush=True)


def _write_rows(path: Path, rows: Iterable[Mapping[str, object]]) -> None:
    rows = list(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _read_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _has_trade_date(path: Path, trade_date: str) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False
    last = ""
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            last = str(row.get("date") or "")
    return last >= trade_date


def _has_complete_daily(path: Path, *, start_date: str, trade_date: str, adjustflag: str) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False
    first = ""
    last = ""
    mismatch = False
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            row_date = str(row.get("date") or "").strip()
            if row_date and not first:
                first = row_date
            if row_date:
                last = row_date
            actual_adjustflag = str(row.get("adjustflag") or "").strip()
            if actual_adjustflag and actual_adjustflag != adjustflag:
                mismatch = True
                break
    return bool(first and first <= start_date and last >= trade_date and not mismatch)


def _is_a_share_code(raw_code: str) -> bool:
    raw_code = raw_code.strip().lower()
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


def _write_sha256_manifest(root: Path, manifest_path: Path) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with manifest_path.open("w", encoding="utf-8") as handle:
        for path in sorted(root.rglob("*.csv")):
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            handle.write(f"{digest}  {path.relative_to(root)}\n")


if __name__ == "__main__":
    raise SystemExit(main())
