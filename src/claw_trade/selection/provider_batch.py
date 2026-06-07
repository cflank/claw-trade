"""Selection provider-batch CLI helpers.

Runtime Web wiring imports provider fetch/build functions from
`claw_trade.data_gateway.selection_api`. This module is kept for explicit CLI
smoke runs and strategy/date helpers; `/select` reads only approved warehouse
runs from `SelectionRunStore`.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable

from claw_trade.data_gateway.selection_api import (
    build_selection_provider_batch_plan as build_selection_provider_batch_plan,
)
from claw_trade.data_gateway.selection_api import (
    fetch_selection_batch_from_data_gateway as _fetch_selection_batch_from_data_gateway,
)
from claw_trade.data_gateway.selection_api import (
    resolve_cn_a_selection_closed_trade_date,
)
from claw_trade.data_gateway.selection_api import (
    resolve_cn_a_selection_trade_date_for_scheduler as _resolve_cn_a_selection_trade_date_for_scheduler,
)
from claw_trade.selection.data_job import SelectionDataFetchProgress
from claw_trade.selection.models import (
    DataGapRef,
    SelectionMarket,
    SelectionProfile,
    SelectionRunPlan,
    SelectionTriggerSource,
)
from claw_trade.selection.strategy_config import (
    CN_A_SELECTION_STRATEGY_CONFIG_REF as _CN_A_SELECTION_STRATEGY_CONFIG_REF,
)
from claw_trade.selection.strategy_config import (
    load_cn_a_selection_v1_strategy as load_cn_a_selection_v1_strategy,
)
from claw_trade.selection.strategy_config import (
    load_cn_a_selection_v1_strategy_config_ref as _load_cn_a_selection_v1_strategy_config_ref,
)


def resolve_cn_a_closed_trade_date(now: datetime) -> str:
    return resolve_cn_a_selection_closed_trade_date(now)


def load_cn_a_selection_v1_strategy_config_ref(
    market: SelectionMarket,
    profile: SelectionProfile,
) -> str | None:
    return _load_cn_a_selection_v1_strategy_config_ref(market, profile)


def resolve_cn_a_closed_trade_date_for_scheduler(trade_date: str | None, *, now: datetime | None = None) -> str:
    return _resolve_cn_a_selection_trade_date_for_scheduler(trade_date, now=now)


def fetch_selection_batch_from_data_gateway(
    plan: SelectionRunPlan,
    *,
    evidence_root: Path | None = None,
    progress_callback: Callable[[SelectionDataFetchProgress], None] | None = None,
):
    return _fetch_selection_batch_from_data_gateway(
        plan,
        evidence_root=evidence_root,
        progress_callback=progress_callback,
    )


def _serialize_data_gap(gap: DataGapRef) -> dict[str, object]:
    return {
        "gap_id": gap.gap_id,
        "domain": gap.domain,
        "gap_code": gap.gap_code,
        "severity": gap.severity.value,
        "attempt_refs": list(gap.attempt_refs),
        "reader_message": gap.reader_message,
        "source_metadata": dict(gap.source_metadata or {}),
    }


def _smoke_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SEL-13 selection batch smoke (debug evidence only).")
    parser.add_argument("--selection-run-id", required=True)
    parser.add_argument("--trade-date", required=True)
    parser.add_argument("--output-json", default="")
    return parser


def _smoke_cli_run(argv: list[str]) -> int:
    args = _smoke_cli_parser().parse_args(argv)
    provider_plan = build_selection_provider_batch_plan(
        market=SelectionMarket.CN_A,
        profile=SelectionProfile.CN_A,
        trade_date=args.trade_date,
    )
    plan = SelectionRunPlan(
        selection_run_id=args.selection_run_id,
        market=SelectionMarket.CN_A,
        profile=SelectionProfile.CN_A,
        trade_date=args.trade_date,
        lookback_trading_days=provider_plan.lookback_trading_days,
        universe_scope=provider_plan.universe_scope,
        provider_batch_plan_ref=provider_plan.plan_id,
        approved_strategy_config_ref=_CN_A_SELECTION_STRATEGY_CONFIG_REF,
        trigger_source=SelectionTriggerSource.SCHEDULED,
    )
    result = fetch_selection_batch_from_data_gateway(plan)
    has_blocker_gap = any(item.severity.value == "blocker" for item in result.data_gaps)
    payload = {
        "selection_run_id": plan.selection_run_id,
        "trade_date": plan.trade_date,
        "attempt_refs": list(result.attempt_refs),
        "normalized_refs": list(result.normalized_refs),
        "rows": len(result.rows),
        "data_gaps": [_serialize_data_gap(item) for item in result.data_gaps],
        "data_gap_codes": [item.gap_code for item in result.data_gaps],
        "data_gap_severities": [item.severity.value for item in result.data_gaps],
    }
    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output_json.strip():
        target = Path(args.output_json.strip())
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered, encoding="utf-8")
    print(rendered)
    return 0 if result.rows and not has_blocker_gap else 2


def main(argv: list[str] | None = None) -> int:
    return _smoke_cli_run(argv if argv is not None else sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
