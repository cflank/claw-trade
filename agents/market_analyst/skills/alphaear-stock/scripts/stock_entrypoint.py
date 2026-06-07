#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from typing import Optional


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError(message)


def emit_json(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, default=str))


def emit_error(error_type: str, message: str) -> int:
    emit_json({"ok": False, "error": {"type": error_type, "message": message}})
    return 1


def get_stock_tools(*, auto_update: bool = True):
    from .stock_tools import StockTools

    return StockTools(auto_update=auto_update)


def build_parser() -> argparse.ArgumentParser:
    parser = JsonArgumentParser(description="AlphaEar stock entrypoint")
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

    news_pack_parser = subparsers.add_parser("cn-a-news-pack", help="get CN A-share news package")
    news_pack_parser.add_argument("--ticker", required=True)
    news_pack_parser.add_argument("--start-date")
    news_pack_parser.add_argument("--end-date")

    social_pack_parser = subparsers.add_parser("cn-a-social-pack", help="get CN A-share heat package")
    social_pack_parser.add_argument("--ticker", required=True)
    return parser


def _clean_cn_ticker(ticker: str) -> str:
    clean = "".join(filter(str.isdigit, ticker))
    if not re.fullmatch(r"\d{6}", clean):
        raise ValueError(f"unsupported CN_A ticker: {ticker}")
    return clean


def _eastmoney_symbol(ticker: str) -> str:
    clean = _clean_cn_ticker(ticker)
    prefix = "SH" if clean.startswith(("5", "6", "9")) else "SZ"
    return f"{prefix}{clean}"


def _date_to_yyyymmdd(value: str | None) -> str:
    if not value:
        from datetime import datetime

        return datetime.now().strftime("%Y%m%d")
    digits = "".join(filter(str.isdigit, value))
    if len(digits) >= 8:
        return digits[:8]
    raise ValueError(f"unsupported date: {value}")


def _records(df, limit: int) -> list[dict]:
    if df is None or getattr(df, "empty", True):
        return []
    limited = df.head(limit).copy()
    # 中文注释：第三方 DataFrame 可能带 NaN/Timestamp，统一转 JSON 可写的真实原始字段。
    return json.loads(limited.to_json(orient="records", force_ascii=False, date_format="iso"))


def build_cn_a_news_pack(ticker: str, start_date: str | None, end_date: str | None) -> dict:
    from claw_trade.data_gateway.agent_tools import load_cn_a_news_pack

    _clean_cn_ticker(ticker)
    return load_cn_a_news_pack(ticker=ticker, start_date=start_date, end_date=end_date)


def build_cn_a_social_pack(ticker: str) -> dict:
    from claw_trade.data_gateway.agent_tools import load_cn_a_social_pack

    _clean_cn_ticker(ticker)
    return load_cn_a_social_pack(ticker=ticker)


def main(argv: Optional[list[str]] = None) -> int:
    try:
        args = build_parser().parse_args(argv)
    except ValueError as exc:
        return emit_error("argparse_error", str(exc))

    try:
        if args.command == "cn-a-news-pack":
            payload = build_cn_a_news_pack(args.ticker, args.start_date, args.end_date)
            emit_json(payload)
            return 0 if payload.get("ok") else 1
        if args.command == "cn-a-social-pack":
            payload = build_cn_a_social_pack(args.ticker)
            emit_json(payload)
            return 0 if payload.get("ok") else 1

        auto_update = not args.skip_auto_update and args.command not in {"search", "price", "fundamentals"}
        # 中文注释：formal session 首条命令不能先卡在全市场列表刷新，按需取证命令不做全量列表刷新。
        tools = get_stock_tools(auto_update=auto_update)
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
