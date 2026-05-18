#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from typing import Any

from pymongo import MongoClient

from claw_trade.data_gateway.store.evidence_chain import audit_openbb_evidence_chain


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit OpenBB provider evidence links for one run.")
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--mongo-uri",
        default=os.environ.get("DATA_GATEWAY_MONGODB_URI") or os.environ.get("CN_A_MONGODB_URI") or "",
    )
    parser.add_argument("--database", default="")
    args = parser.parse_args()

    if not args.mongo_uri.strip():
        raise SystemExit("missing --mongo-uri or DATA_GATEWAY_MONGODB_URI/CN_A_MONGODB_URI")

    client: MongoClient[Any] = MongoClient(args.mongo_uri, serverSelectionTimeoutMS=5000)
    client.admin.command("ping")
    database = client[args.database] if args.database else client.get_default_database()
    audit = audit_openbb_evidence_chain(database, run_id=args.run_id)
    print(json.dumps(audit.as_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if audit.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
