#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

SKILL_SCRIPTS_DIR = Path(__file__).resolve().parent
WORKSPACE_ROOT = SKILL_SCRIPTS_DIR.parents[2]

from .chart_engine import ChartInputInsufficientHistoryError, ChartRuntimeUnavailableError, render_market_charts
from .indicator_engine import IndicatorRuntimeUnavailableError, analyze_market_frame
from .market_data_provider import NoMarketDataError, load_price_frame


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ValueError(message)


def emit_json(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, default=str))


def emit_error(error_code: str, message: str, *, code_key: str = "type") -> int:
    emit_json({"ok": False, "error": {code_key: error_code, "message": message}})
    return 1


def _validate_relative_output_dir(output_dir: str) -> Path:
    if not output_dir or not output_dir.strip():
        raise ValueError("output-dir is required")
    candidate = Path(output_dir)
    if candidate.is_absolute():
        raise ValueError("output-dir must be relative")
    if any(part == ".." for part in candidate.parts):
        raise ValueError("output-dir must not escape the workspace root")
    return WORKSPACE_ROOT / candidate


def build_parser() -> argparse.ArgumentParser:
    parser = JsonArgumentParser(description="AlphaEar techlab entrypoint")
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze_parser = subparsers.add_parser("analyze", help="technical analysis and chart rendering")
    analyze_parser.add_argument("--ticker", required=True)
    analyze_parser.add_argument("--start-date", required=True)
    analyze_parser.add_argument("--end-date", required=True)
    analyze_parser.add_argument("--output-dir", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
    except ValueError as exc:
        return emit_error("invalid_arguments", str(exc), code_key="code")

    try:
        output_dir = _validate_relative_output_dir(args.output_dir)
    except ValueError as exc:
        return emit_error("invalid_output_dir", str(exc), code_key="code")

    try:
        frame = load_price_frame(ticker=args.ticker, start_date=args.start_date, end_date=args.end_date)
        if frame.empty:
            return emit_error("no_market_data", f"no price rows available for {args.ticker}")
        bundle = analyze_market_frame(frame, ticker=args.ticker)
        payload = {
            "ok": True,
            "ticker": args.ticker,
            "summary": bundle.summary,
            "indicators": bundle.indicators,
            "warnings": list(bundle.warnings),
            "chart_files": [],
        }
        try:
            chart_paths = render_market_charts(bundle.chart_frame, ticker=args.ticker, output_dir=output_dir)
        except ChartRuntimeUnavailableError as exc:
            return emit_error("chart_runtime_unavailable", str(exc))
        except ChartInputInsufficientHistoryError as exc:
            return emit_error("chart_input_insufficient_history", str(exc))
        except Exception as exc:
            return emit_error("chart_render_failed", str(exc))
        else:
            payload["chart_files"] = [str(chart_path.relative_to(WORKSPACE_ROOT)) for chart_path in chart_paths]
        emit_json(
            payload
        )
        return 0
    except NoMarketDataError as exc:
        return emit_error("no_market_data", str(exc))
    except IndicatorRuntimeUnavailableError as exc:
        return emit_error("indicator_backend_unavailable", str(exc))
    except Exception as exc:
        return emit_error("runtime_error", str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
