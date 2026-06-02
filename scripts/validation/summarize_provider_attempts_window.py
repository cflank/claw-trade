from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pymongo import MongoClient


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-count", type=int, required=True)
    parser.add_argument("--market", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--report-output-dir", default="")
    args = parser.parse_args()

    db = _mongo_db()
    attempts = list(db.provider_attempts.find({}).skip(args.skip_count))
    status_counts = Counter(str(item.get("status") or "unknown") for item in attempts)
    provider_counts = Counter(str(item.get("provider") or "unknown") for item in attempts)
    remote_success_counts = Counter(str(bool(item.get("remote_success"))) for item in attempts)
    endpoint_counts = Counter(
        f"{item.get('provider') or 'unknown'}::{item.get('endpoint') or 'unknown'}::{item.get('status') or 'unknown'}"
        for item in attempts
    )
    payload: dict[str, Any] = {
        "schema": "provider-attempt-window-summary-v1",
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "market": args.market,
        "run_id": args.run_id,
        "report_output_dir": args.report_output_dir,
        "database": db.name,
        "skip_count": args.skip_count,
        "attempt_count": len(attempts),
        "status_counts": dict(sorted(status_counts.items())),
        "remote_success_counts": dict(sorted(remote_success_counts.items())),
        "provider_counts": dict(sorted(provider_counts.items())),
        "endpoint_status_counts": dict(sorted(endpoint_counts.items())),
        "attempts": [_attempt_summary(item) for item in attempts],
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({key: payload[key] for key in ("market", "run_id", "attempt_count", "status_counts")}, ensure_ascii=False))
    return 0


def _attempt_summary(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "provider": item.get("provider"),
        "endpoint": item.get("endpoint"),
        "status": item.get("status"),
        "remote_attempted": item.get("remote_attempted"),
        "remote_success": item.get("remote_success"),
        "dataset_ref_count": len(item.get("dataset_refs") or ()),
        "raw_ref_count": len(item.get("raw_refs") or ()),
        "gap_codes": list(item.get("gap_codes") or ()),
        "created_at": item.get("created_at"),
        "attempt_ref": item.get("attempt_ref"),
    }


def _mongo_db() -> Any:
    uri = os.environ.get("DATA_GATEWAY_MONGODB_URI", "mongodb://127.0.0.1:27017/claw_trade")
    db_name = os.environ.get("DATA_GATEWAY_MONGODB_DATABASE", "claw_trade")
    if "/" in uri.rsplit("/", 1)[-1] and not db_name:
        db_name = uri.rsplit("/", 1)[-1]
    return MongoClient(uri, serverSelectionTimeoutMS=5000)[db_name]


if __name__ == "__main__":
    raise SystemExit(main())
