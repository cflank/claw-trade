#!/usr/bin/env python3
"""Run claw-trade CN_A report workflow through investment debate and export evidence."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from claw_trade.cli.run_control import _build_runner
from claw_trade.workflow.models import RunRequest, RunStatus, StopPoint, WorkflowEntryPoint


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUNTIME_ENV = REPO_ROOT / ".runtime/dev-services/runtime.env"
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "docs/evidence/invest_debate_prompt_feedback"


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def safe_token(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip() or "unknown")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def extract_final_prompt_text(provider_request_payload: dict[str, Any]) -> str:
    payload = provider_request_payload.get("payload")
    if not isinstance(payload, dict):
        return ""
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return ""
    parts: list[str] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, str):
            parts.append(content)
            continue
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
    return "\n\n".join(parts).strip()


def resolve_evidence_path(raw_value: object, call_dir: Path) -> Path | None:
    if not isinstance(raw_value, str) or not raw_value.strip():
        return None
    raw_path = Path(raw_value)
    return raw_path if raw_path.is_absolute() else call_dir / raw_path


def copy_if_exists(source: Path | None, target: Path) -> str | None:
    if source is None:
        return None
    if not source.exists() or not source.is_file():
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return str(target)


def call_dirs_for_workers(run_dir: Path, workers: tuple[str, ...]) -> dict[str, Path]:
    out: dict[str, Path] = {}
    for call_path in sorted((run_dir / "calls").glob("*/call.json")):
        try:
            payload = read_json(call_path)
        except Exception:
            continue
        worker_id = payload.get("worker_id")
        if isinstance(worker_id, str) and worker_id in workers:
            out[worker_id] = call_path.parent
    return out


def export_worker_evidence(run_dir: Path, output_dir: Path, worker_id: str, call_dir: Path) -> dict[str, Any]:
    call_path = call_dir / "call.json"
    openclaw_result_path = call_dir / "openclaw-result.json"
    call_payload = read_json(call_path)
    openclaw_result = read_json(openclaw_result_path)

    copied: dict[str, str | None] = {
        "call_raw": copy_if_exists(call_path, output_dir / f"{worker_id}_call_raw.json"),
        "openclaw_result_raw": copy_if_exists(openclaw_result_path, output_dir / f"{worker_id}_openclaw_result_raw.json"),
    }
    evidence_names = {
        "provider_request_path": "final_prompt_raw.json",
        "visible_tools_path": "visible_tools_raw.json",
        "first_response_path": "first_response_raw.json",
        "tool_calls_path": "tool_calls_raw.json",
        "raw_output_path": "raw_output.md",
        "openviking_receipt_path": "openviking_receipt_raw.json",
    }
    resolved_paths: dict[str, str | None] = {}
    for field_name, output_name in evidence_names.items():
        resolved = resolve_evidence_path(openclaw_result.get(field_name), call_dir)
        resolved_paths[field_name] = str(resolved) if resolved is not None else None
        copied[field_name] = copy_if_exists(resolved, output_dir / f"{worker_id}_{output_name}")

    provider_request_path = resolve_evidence_path(openclaw_result.get("provider_request_path"), call_dir)
    final_prompt_text = ""
    if provider_request_path is not None and provider_request_path.exists():
        final_prompt_text = extract_final_prompt_text(read_json(provider_request_path))
    final_prompt_md = output_dir / f"{worker_id}_final_prompt.md"
    final_prompt_md.write_text(final_prompt_text + "\n", encoding="utf-8")

    raw_output_path = resolve_evidence_path(openclaw_result.get("raw_output_path"), call_dir)
    raw_output_text = raw_output_path.read_text(encoding="utf-8") if raw_output_path and raw_output_path.exists() else ""
    llm_back_md = output_dir / f"{worker_id}_llm_back.md"
    report_md = output_dir / f"{worker_id}_report.md"
    llm_back_md.write_text(raw_output_text, encoding="utf-8")
    report_md.write_text(raw_output_text, encoding="utf-8")

    return {
        "worker_id": worker_id,
        "call_id": call_payload.get("call_id"),
        "stage": call_payload.get("stage"),
        "call_dir": str(call_dir),
        "openclaw_status": openclaw_result.get("status"),
        "provider_request_id": openclaw_result.get("provider_request_id"),
        "resolved_paths": resolved_paths,
        "outputs": {
            **copied,
            "final_prompt": str(final_prompt_md),
            "llm_back": str(llm_back_md),
            "report": str(report_md),
        },
        "final_prompt_chars": len(final_prompt_text),
        "report_chars": len(raw_output_text),
    }


def export_debate_evidence(run_dir: Path, output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=False)
    workers = ("bull_researcher", "bear_researcher")
    call_dirs = call_dirs_for_workers(run_dir, workers)
    missing = [worker for worker in workers if worker not in call_dirs]
    if missing:
        raise RuntimeError(f"缺少 debate worker call: {','.join(missing)}")

    worker_summaries = [
        export_worker_evidence(run_dir=run_dir, output_dir=output_dir, worker_id=worker, call_dir=call_dirs[worker])
        for worker in workers
    ]
    for relative in ("state.json", "request.json"):
        copy_if_exists(run_dir / relative, output_dir / relative)
    copy_if_exists(run_dir / "openviking" / "approved-manifest.json", output_dir / "approved-manifest.json")

    summary = {
        "run_dir": str(run_dir),
        "output_dir": str(output_dir),
        "workers": worker_summaries,
        "notes": [
            "This is a claw-trade run, not a TradingAgents-CN direct capture.",
            "The workflow stops at StopPoint.INVESTMENT_DEBATE_READY after bull and bear approved materials exist.",
            "llm_back.md and report.md are copied from this project's raw_output.md for each worker.",
        ],
    }
    write_json(output_dir / "capture_summary.json", summary)
    lines = [
        "# claw-trade CN_A investment debate capture",
        "",
        f"- run_dir: `{run_dir}`",
        f"- output_dir: `{output_dir}`",
        "- stop_point: `investment_debate_ready`",
        "",
        "| worker | status | final prompt | LLM back | report | call |",
        "|---|---|---|---|---|---|",
    ]
    for item in worker_summaries:
        worker = item["worker_id"]
        lines.append(
            f"| `{worker}` | `{item['openclaw_status']}` | "
            f"`{worker}_final_prompt.md` | `{worker}_llm_back.md` | "
            f"`{worker}_report.md` | `{worker}_call_raw.json` |"
        )
    (output_dir / "capture_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary


def parse_args() -> argparse.Namespace:
    today = date.today()
    parser = argparse.ArgumentParser(description="Run claw-trade CN_A workflow until investment debate is ready.")
    parser.add_argument("--ticker", default="600519")
    parser.add_argument("--company-name", default="贵州茅台")
    parser.add_argument("--market", default="CN_A")
    parser.add_argument("--profile", default="CN_A")
    parser.add_argument("--currency", default="CNY")
    parser.add_argument("--currency-symbol", default="¥")
    parser.add_argument("--current-date", default=today.isoformat())
    parser.add_argument("--start-date", default=(today - timedelta(days=30)).isoformat())
    parser.add_argument("--end-date", default=today.isoformat())
    parser.add_argument("--run-dir", default="runs")
    parser.add_argument("--runtime-env", default=str(DEFAULT_RUNTIME_ENV))
    parser.add_argument("--output-dir", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    load_env_file(Path(args.runtime_env))
    request = RunRequest(
        ticker=args.ticker,
        company_name=args.company_name,
        market=args.market,
        profile=args.profile,
        currency=args.currency,
        currency_symbol=args.currency_symbol,
        current_date=args.current_date,
        start_date=args.start_date,
        end_date=args.end_date,
        stop_point=StopPoint.INVESTMENT_DEBATE_READY,
        entry_point=WorkflowEntryPoint.REPORT_COMMAND,
    )
    runner = _build_runner(Path(args.run_dir))
    state = runner.run(request)
    if state.status != RunStatus.COMPLETED:
        print(
            f"FAILED run_id={state.run_id} status={state.status.value} reason={state.failure_reason}",
            file=sys.stderr,
        )
        return 1

    output_dir = Path(args.output_dir) if args.output_dir else (
        DEFAULT_OUTPUT_ROOT / f"claw_trade_cn_a_{safe_token(args.ticker)}_debate_live_{state.run_id}"
    )
    summary = export_debate_evidence(state.run_dir, output_dir)
    print(f"COMPLETED run_id={state.run_id} output_dir={summary['output_dir']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
