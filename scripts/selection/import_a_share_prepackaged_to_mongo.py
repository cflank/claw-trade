#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from urllib.parse import urlparse

from claw_trade.data_gateway.a_share_prepackaged_importer import (
    audit_a_share_prepackaged_packages,
    import_a_share_prepackaged_to_repository,
)
from claw_trade.data_gateway.warehouse import DatasetRepository


def main() -> int:
    parser = argparse.ArgumentParser(description="Import local A-share daily/factor packages into unified data warehouse.")
    parser.add_argument("--daily-root", required=True, type=Path)
    parser.add_argument("--factor-root", required=True, type=Path)
    parser.add_argument("--trade-date", required=True)
    parser.add_argument("--import-run-id", required=True)
    parser.add_argument("--local-root", action="append", default=[], type=Path)
    parser.add_argument(
        "--identity-source-order",
        default=os.environ.get("CLAW_TRADE_A_SHARE_IDENTITY_SOURCE_ORDER", ""),
        help="Comma-separated online identity source order, for example: baostock,akshare,tushare.",
    )
    parser.add_argument(
        "--identity-fetch-timeout-seconds",
        default=float(os.environ.get("CLAW_TRADE_A_SHARE_IDENTITY_FETCH_TIMEOUT_SECONDS", "12")),
        type=float,
    )
    parser.add_argument("--tushare-token", default=os.environ.get("TUSHARE_TOKEN") or os.environ.get("CN_A_TUSHARE_TOKEN") or "")
    parser.add_argument("--tushare-endpoint-url", default=os.environ.get("TUSHARE_HTTP_URL") or "")
    parser.add_argument("--history-days", default=390, type=int)
    parser.add_argument("--max-symbols", default=None, type=int)
    parser.add_argument(
        "--replace-existing",
        action="store_true",
        help="Delete existing normalized rows from this local prepackaged provider scope before importing.",
    )
    parser.add_argument("--audit-only", action="store_true")
    parser.add_argument("--output-json", default="")
    parser.add_argument(
        "--mongo-uri",
        default=os.environ.get("DATA_GATEWAY_MONGODB_URI") or os.environ.get("CN_A_MONGODB_URI") or "",
    )
    parser.add_argument(
        "--mongo-database",
        default=os.environ.get("DATA_GATEWAY_MONGODB_DATABASE") or os.environ.get("CN_A_MONGODB_DATABASE") or "",
    )
    args = parser.parse_args()

    if args.audit_only:
        payload = {
            "mode": "audit_only",
            "audit": audit_a_share_prepackaged_packages(
                daily_root=args.daily_root,
                factor_root=args.factor_root,
                local_roots=tuple(args.local_root),
            ).as_dict(),
        }
        _emit(payload, target=args.output_json)
        return 0

    if not args.mongo_uri:
        raise SystemExit("missing --mongo-uri or DATA_GATEWAY_MONGODB_URI/CN_A_MONGODB_URI")

    from pymongo import MongoClient

    client = MongoClient(args.mongo_uri, serverSelectionTimeoutMS=5000)
    database_name = args.mongo_database or _database_name_from_uri(args.mongo_uri)
    repository = DatasetRepository.from_database(client[database_name])
    result = import_a_share_prepackaged_to_repository(
        daily_root=args.daily_root,
        factor_root=args.factor_root,
        repository=repository,
        trade_date=args.trade_date,
        import_run_id=args.import_run_id,
        local_roots=tuple(args.local_root),
        identity_source_order=_csv_items(args.identity_source_order),
        identity_fetch_timeout_seconds=args.identity_fetch_timeout_seconds,
        tushare_token=args.tushare_token or None,
        tushare_endpoint_url=args.tushare_endpoint_url or None,
        history_days=args.history_days,
        max_symbols=args.max_symbols,
        replace_existing=args.replace_existing,
    )
    _emit(
        {
            "mode": "import",
            "mongo_database": database_name,
            **result.as_dict(),
        },
        target=args.output_json,
    )
    return 0


def _emit(payload: dict[str, object], *, target: str) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str)
    if target.strip():
        output = Path(target)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
    print(text)


def _database_name_from_uri(uri: str) -> str:
    parsed = urlparse(uri)
    path_name = parsed.path.strip("/")
    if path_name:
        return path_name.split("/", 1)[0]
    return "claw_trade"


def _csv_items(value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in value.split(",") if item.strip())


if __name__ == "__main__":
    raise SystemExit(main())
