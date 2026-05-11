#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
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
    import akshare as ak

    clean = _clean_cn_ticker(ticker)
    cctv_date = _date_to_yyyymmdd(end_date)
    limitations: list[dict] = []
    company_news: list[dict] = []
    macro_news: list[dict] = []

    try:
        company_news = _records(ak.stock_news_em(symbol=clean), 15)
        if not company_news:
            limitations.append(
                {
                    "type": "company_news_empty",
                    "provider": "akshare.stock_news_em",
                    "message": f"stock_news_em returned no rows for {clean}",
                }
            )
    except Exception as exc:
        limitations.append(
            {
                "type": "company_news_unavailable",
                "provider": "akshare.stock_news_em",
                "message": str(exc),
            }
        )

    try:
        macro_news = _records(ak.news_cctv(date=cctv_date), 10)
        if not macro_news:
            limitations.append(
                {
                    "type": "macro_news_empty",
                    "provider": "akshare.news_cctv",
                    "message": f"news_cctv returned no rows for {cctv_date}",
                }
            )
    except Exception as exc:
        limitations.append(
            {
                "type": "macro_news_unavailable",
                "provider": "akshare.news_cctv",
                "message": str(exc),
            }
        )

    has_company = bool(company_news)
    has_macro = bool(macro_news)
    status = "ok" if has_company and has_macro else "partial" if has_company or has_macro else "failed"
    return {
        "ok": has_company or has_macro,
        "ticker": clean,
        "start_date": start_date,
        "end_date": end_date,
        "data": {
            "company_news": company_news,
            "macro_news": macro_news,
            "limitations": limitations,
        },
        "quality": {
            "status": status,
            "is_partial": status == "partial",
            "warnings": limitations,
            "source_used": "akshare.stock_news_em+akshare.news_cctv",
        },
    }


def build_cn_a_social_pack(ticker: str) -> dict:
    import akshare as ak

    clean = _clean_cn_ticker(ticker)
    symbol = _eastmoney_symbol(clean)
    limitations: list[dict] = [
        {
            "type": "heat_not_text_sentiment",
            "provider": "akshare.eastmoney_hot_rank",
            "message": "CN_A route returns market heat/keyword evidence, not post-level bullish/bearish text sentiment.",
        }
    ]
    latest: dict = {}
    keywords: list[dict] = []
    related: list[dict] = []
    top_rank_match: list[dict] = []

    try:
        latest_rows = _records(ak.stock_hot_rank_latest_em(symbol=symbol), 20)
        latest = {str(row.get("item")): row.get("value") for row in latest_rows if row.get("item")}
    except Exception as exc:
        limitations.append(
            {
                "type": "hot_rank_latest_unavailable",
                "provider": "akshare.stock_hot_rank_latest_em",
                "message": str(exc),
            }
        )

    try:
        keywords = _records(ak.stock_hot_keyword_em(symbol=symbol), 20)
    except Exception as exc:
        limitations.append(
            {
                "type": "hot_keyword_unavailable",
                "provider": "akshare.stock_hot_keyword_em",
                "message": str(exc),
            }
        )

    try:
        related = _records(ak.stock_hot_rank_relate_em(symbol=symbol), 20)
    except Exception as exc:
        limitations.append(
            {
                "type": "hot_related_unavailable",
                "provider": "akshare.stock_hot_rank_relate_em",
                "message": str(exc),
            }
        )

    try:
        rank_rows = _records(ak.stock_hot_rank_em(), 100)
        top_rank_match = [row for row in rank_rows if str(row.get("代码", "")).endswith(clean)]
    except Exception as exc:
        limitations.append(
            {
                "type": "hot_rank_unavailable",
                "provider": "akshare.stock_hot_rank_em",
                "message": str(exc),
            }
        )

    has_heat = bool(latest or keywords or related or top_rank_match)
    return {
        "ok": has_heat,
        "ticker": clean,
        "data": {
            "heat_snapshot": latest,
            "hot_keywords": keywords,
            "related_hot_stocks": related,
            "top_rank_match": top_rank_match,
            "limitations": limitations,
        },
        "quality": {
            "status": "partial" if has_heat else "failed",
            "is_partial": True,
            "warnings": limitations,
            "source_used": "akshare.eastmoney_hot_rank",
        },
    }


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
