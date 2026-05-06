#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Optional


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError(message)


def emit_json(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, default=str))


def emit_error(error_type: str, message: str) -> int:
    emit_json({"ok": False, "error": {"type": error_type, "message": message}})
    return 1


def _default_db_path() -> str:
    return str((Path(__file__).resolve().parent.parent / "data" / "signal_flux.db").resolve())


def load_stock_deps():
    from .database_manager import DatabaseManager
    from .stock_tools import StockTools

    return DatabaseManager, StockTools


def get_stock_tools(db_path: str, *, auto_update: bool = True):
    DatabaseManager, StockTools = load_stock_deps()
    db = DatabaseManager(db_path=db_path)
    return StockTools(db=db, auto_update=auto_update)


def build_parser() -> argparse.ArgumentParser:
    parser = JsonArgumentParser(description="AlphaEar stock entrypoint")
    parser.add_argument("--db-path", default=_default_db_path())
    parser.add_argument("--skip-auto-update", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)

    search_parser = subparsers.add_parser("search", help="search ticker by name/code")
    search_parser.add_argument("--query", required=True)
    search_parser.add_argument("--limit", type=int, default=5)

    price_parser = subparsers.add_parser("price", help="get historical OHLCV price rows")
    price_parser.add_argument("--ticker", required=True)
    price_parser.add_argument("--start-date")
    price_parser.add_argument("--end-date")

    fundamentals_parser = subparsers.add_parser("fundamentals", help="get fundamentals snapshot")
    fundamentals_parser.add_argument("--ticker", required=True)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    try:
        args = build_parser().parse_args(argv)
    except ValueError as exc:
        return emit_error("argparse_error", str(exc))

    try:
        auto_update = not args.skip_auto_update and args.command not in {"search", "price", "fundamentals"}
        # 中文注释：formal session 首条命令不能先卡在全市场列表刷新，search/fundamentals/price 走按需取证。
        tools = get_stock_tools(args.db_path, auto_update=auto_update)
        if args.command == "search":
            emit_json({"ok": True, "results": tools.search_ticker(args.query, limit=args.limit)})
            return 0
        if args.command == "price":
            df = tools.get_stock_price(
                args.ticker,
                start_date=args.start_date,
                end_date=args.end_date,
            )
            emit_json({"ok": True, "rows": df.to_dict(orient="records")})
            return 0
        emit_json({"ok": True, "fundamentals": tools.get_stock_fundamentals(args.ticker)})
        return 0
    except Exception as exc:
        return emit_error("runtime_error", str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
