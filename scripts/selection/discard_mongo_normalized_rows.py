#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from claw_trade.data_gateway.maintenance.normalized_rows import discard_normalized_mongo_rows
from claw_trade.data_gateway.runtime import open_data_gateway_database_from_env
from claw_trade.data_gateway.warehouse import DatasetRepository


def main() -> int:
    parser = argparse.ArgumentParser(description="Discard legacy Mongo normalized_datasets rows after deletion-gate approval.")
    parser.add_argument("--criteria-json", default="{}", help="Mongo-style criteria JSON object. Default deletes all normalized rows.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--confirm-discard-normalized-rows", action="store_true")
    parser.add_argument("--output-json", default="")
    args = parser.parse_args()

    criteria = _criteria(args.criteria_json)
    database = open_data_gateway_database_from_env()
    repository = DatasetRepository.from_database(database)
    result = discard_normalized_mongo_rows(
        repository,
        criteria,
        dry_run=args.dry_run,
        confirmed=args.confirm_discard_normalized_rows,
    )
    payload = {
        "ok": True,
        "mode": "dry_run" if result.dry_run else "discard",
        "criteria": result.criteria,
        "matched_count": result.matched_count,
        "deleted_count": result.deleted_count,
    }
    _emit(payload, target=args.output_json)
    return 0


def _criteria(value: str) -> dict[str, Any]:
    try:
        payload = json.loads(value)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"--criteria-json must be valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit("--criteria-json must decode to an object")
    return dict(payload)


def _emit(payload: Mapping[str, Any] | dict[str, Any], *, target: str) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if target:
        Path(target).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)


if __name__ == "__main__":
    raise SystemExit(main())
