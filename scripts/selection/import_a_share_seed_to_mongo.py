#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from urllib.parse import urlparse

from claw_trade.data_gateway.selection_seed_importer import import_a_share_seed_to_mongo
from claw_trade.data_gateway.store import (
    MongoAttemptStore,
    MongoNormalizedStore,
    MongoRawPayloadStore,
)
from claw_trade.data_gateway.store.mongo import (
    OPENBB_NORMALIZED,
    OPENBB_PROVIDER_ATTEMPTS,
    OPENBB_RAW_PAYLOADS,
    ensure_openbb_store_indexes,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Import local A-share Baostock/AkShare seed files into Mongo warehouse.")
    parser.add_argument("--baostock-root", required=True, type=Path)
    parser.add_argument("--private-placement-root", required=True, type=Path)
    parser.add_argument("--trade-date", required=True)
    parser.add_argument("--start-date", default="2015-01-01")
    parser.add_argument("--import-run-id", required=True)
    parser.add_argument(
        "--mongo-uri",
        default=os.environ.get("DATA_GATEWAY_MONGODB_URI") or os.environ.get("CN_A_MONGODB_URI") or "",
    )
    parser.add_argument(
        "--mongo-database",
        default=os.environ.get("DATA_GATEWAY_MONGODB_DATABASE") or os.environ.get("CN_A_MONGODB_DATABASE") or "",
    )
    args = parser.parse_args()
    if not args.mongo_uri:
        raise SystemExit("missing --mongo-uri or DATA_GATEWAY_MONGODB_URI/CN_A_MONGODB_URI")

    from pymongo import MongoClient

    client = MongoClient(args.mongo_uri, serverSelectionTimeoutMS=5000)
    database_name = args.mongo_database or _database_name_from_uri(args.mongo_uri)
    database = client[database_name]
    ensure_openbb_store_indexes(database)

    result = import_a_share_seed_to_mongo(
        baostock_root=args.baostock_root,
        private_placement_root=args.private_placement_root,
        trade_date=args.trade_date,
        import_run_id=args.import_run_id,
        raw_store=MongoRawPayloadStore(database[OPENBB_RAW_PAYLOADS]),
        normalized_store=MongoNormalizedStore(database[OPENBB_NORMALIZED]),
        attempt_store=MongoAttemptStore(database[OPENBB_PROVIDER_ATTEMPTS]),
        start_date=args.start_date,
    )
    print(
        json.dumps(
            {
                "import_run_id": result.import_run_id,
                "trade_date": result.trade_date,
                "manifest_hash": result.manifest_hash,
                "raw_refs": result.raw_refs,
                "normalized_refs": result.normalized_refs,
                "attempt_refs": result.attempt_refs,
                "data_gaps": [gap.__dict__ for gap in result.data_gaps],
                "readback_counts": dict(result.readback_counts),
                "warehouse_check_ref": result.warehouse_check_ref,
                "execution_scope": "operator_supplied_paths",
                "full_seed_completion_claimed": False,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 1 if result.blocker_gaps else 0


def _database_name_from_uri(uri: str) -> str:
    parsed = urlparse(uri)
    path_name = parsed.path.strip("/")
    if path_name:
        return path_name.split("/", 1)[0]
    return "claw_trade_openbb"


if __name__ == "__main__":
    raise SystemExit(main())
