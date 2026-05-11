from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Sequence

from models import ProviderQuery
from providers import (
    AkshareNewsCctvProvider,
    AkshareStockInfoGlobalClsProvider,
    AkshareStockInfoGlobalEmProvider,
    AkshareStockNewsEmProvider,
    NewsProvider,
    TushareAnnouncementsProvider,
)
from security import sanitize_error

SCHEMA_VERSION = "cn_a_news_health_check.v1"
DEFAULT_EVIDENCE_ROOT = Path("docs/evidence/cn_a_news/provider-health-check")
PROVIDER_FACTORIES = {
    "akshare.stock_news_em": AkshareStockNewsEmProvider,
    "akshare.stock_info_global_cls": AkshareStockInfoGlobalClsProvider,
    "akshare.stock_info_global_em": AkshareStockInfoGlobalEmProvider,
    "akshare.news_cctv": AkshareNewsCctvProvider,
    "tushare.anns_d": TushareAnnouncementsProvider,
}


class _SanitizedArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError(message)


def _build_parser() -> _SanitizedArgumentParser:
    parser = _SanitizedArgumentParser(description="CN_A news provider health check")
    parser.add_argument("--provider", required=True, choices=sorted(PROVIDER_FACTORIES.keys()))
    parser.add_argument("--ticker", help="A-share ticker, e.g. 600519")
    parser.add_argument("--ts-code", help="Tushare ts_code, e.g. 600519.SH")
    parser.add_argument("--date", help="Date in YYYY-MM-DD format")
    parser.add_argument("--start-date", help="Start date in YYYY-MM-DD format")
    parser.add_argument("--end-date", help="End date in YYYY-MM-DD format")
    parser.add_argument(
        "--evidence-dir",
        help="Optional output root. JSON is saved under {root}/provider-health-check/{yyyy-mm-dd}/",
    )
    parser.add_argument(
        "--runtime-evidence-root",
        help="Runtime evidence root. Must be used together with --run-id/--stage/--call-id.",
    )
    parser.add_argument("--run-id", help="Runtime run id for runtime evidence mode.")
    parser.add_argument("--stage", help="Runtime stage for runtime evidence mode.")
    parser.add_argument("--call-id", help="Runtime call id for runtime evidence mode.")
    return parser


def _parse_iso_date(name: str, value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    try:
        return datetime.strptime(cleaned, "%Y-%m-%d").date().isoformat()
    except ValueError as exc:
        raise ValueError(f"{name} must be YYYY-MM-DD, got {cleaned}") from exc


def _resolve_exchange_ticker(ticker: str) -> str:
    cleaned = ticker.strip()
    if "." in cleaned:
        return cleaned
    return f"{cleaned}.SH"


def _validate_required_fields(args: argparse.Namespace) -> None:
    provider_id = args.provider
    if provider_id == "akshare.stock_news_em":
        if not args.ticker or args.ticker.strip() == "":
            raise ValueError("--ticker is required for akshare.stock_news_em")
        return

    if provider_id == "tushare.anns_d":
        if not args.ts_code or args.ts_code.strip() == "":
            raise ValueError("--ts-code is required for tushare.anns_d")
        return

    if provider_id == "akshare.news_cctv":
        if (not args.date or args.date.strip() == "") and (not args.end_date or args.end_date.strip() == ""):
            raise ValueError("--date or --end-date is required for akshare.news_cctv")


def _resolve_runtime_evidence_parts(args: argparse.Namespace) -> dict[str, str] | None:
    pairs = (
        ("runtime_evidence_root", args.runtime_evidence_root),
        ("run_id", args.run_id),
        ("stage", args.stage),
        ("call_id", args.call_id),
    )
    provided = [name for name, value in pairs if value is not None and value.strip() != ""]
    if not provided:
        return None
    missing = [name for name, value in pairs if value is None or value.strip() == ""]
    if missing:
        raise ValueError(
            "runtime evidence arguments must be provided together: "
            "--runtime-evidence-root --run-id --stage --call-id"
        )
    return {name: value.strip() for name, value in pairs}


def _build_query(args: argparse.Namespace, provider: NewsProvider) -> ProviderQuery:
    today = datetime.now(tz=UTC).date().isoformat()
    date_value = _parse_iso_date("--date", args.date)
    start_date = _parse_iso_date("--start-date", args.start_date)
    end_date = _parse_iso_date("--end-date", args.end_date)

    if end_date is None:
        end_date = date_value or today
    if start_date is None:
        start_date = end_date

    if args.ts_code:
        exchange_ticker = args.ts_code.strip()
        ticker = exchange_ticker.split(".", 1)[0]
    else:
        ticker = (args.ticker or "000000").strip()
        exchange_ticker = _resolve_exchange_ticker(ticker)

    return ProviderQuery(
        endpoint=provider.endpoint,
        ticker=ticker,
        exchange_ticker=exchange_ticker,
        company_name=None,
        keywords=[],
        start_date=start_date,
        end_date=end_date,
    )


def _build_request_payload(args: argparse.Namespace, query: ProviderQuery) -> dict[str, str | None]:
    return {
        "ticker": args.ticker.strip() if args.ticker else None,
        "ts_code": args.ts_code.strip() if args.ts_code else None,
        "date": args.date.strip() if args.date else None,
        "start_date": query.start_date,
        "end_date": query.end_date,
    }


def _build_output_path(
    *,
    evidence_dir: str | None,
    runtime_evidence_parts: dict[str, str] | None,
    check_date: str,
    provider_name: str,
    endpoint: str,
) -> Path:
    if runtime_evidence_parts is not None:
        root = Path(runtime_evidence_parts["runtime_evidence_root"]).expanduser().resolve()
        return (
            root
            / runtime_evidence_parts["run_id"]
            / runtime_evidence_parts["stage"]
            / "news_analyst"
            / runtime_evidence_parts["call_id"]
            / "provider-health-check.json"
        )
    if evidence_dir is None:
        root = DEFAULT_EVIDENCE_ROOT
    else:
        root = Path(evidence_dir).expanduser().resolve() / "provider-health-check"
    return root / check_date / f"{provider_name}_{endpoint}.json"


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_health_check(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    _validate_required_fields(args)
    runtime_evidence_parts = _resolve_runtime_evidence_parts(args)

    provider_factory = PROVIDER_FACTORIES[args.provider]
    provider = provider_factory()
    query = _build_query(args, provider)
    request_payload = _build_request_payload(args, query)

    result = provider.fetch(query)
    exit_code = 0 if result.attempt.ok else 1
    check_time = datetime.now().astimezone().isoformat()
    payload = {
        "schema_version": SCHEMA_VERSION,
        "check_time": check_time,
        "provider": result.attempt.provider,
        "endpoint": result.attempt.endpoint,
        "request": request_payload,
        "attempt": {
            "ok": result.attempt.ok,
            "elapsed_ms": result.attempt.elapsed_ms,
            "raw_count": result.attempt.raw_count,
            "empty_reason": result.attempt.empty_reason,
            "error": result.attempt.error,
            "cancelled": result.attempt.cancelled,
        },
        "exit_code": exit_code,
        "diagnosis": "provider_ok" if exit_code == 0 else result.attempt.empty_reason,
    }

    check_date = check_time[:10]
    output_path = _build_output_path(
        evidence_dir=args.evidence_dir,
        runtime_evidence_parts=runtime_evidence_parts,
        check_date=check_date,
        provider_name=result.attempt.provider,
        endpoint=result.attempt.endpoint,
    )
    _write_json(output_path, payload)
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    return exit_code


def main(argv: Sequence[str] | None = None) -> int:
    try:
        return run_health_check(argv)
    except Exception as exc:
        sys.stderr.write(f"health_check failed: {sanitize_error(exc)}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
