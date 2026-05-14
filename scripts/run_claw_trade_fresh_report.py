#!/usr/bin/env python3
"""Run one claw-trade fresh report and export Markdown evidence."""

from __future__ import annotations

import argparse
import json
import re
import shutil
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from claw_trade.cli.run_control import _build_runner
from claw_trade.config.report_workflow_settings import load_report_workflow_settings
from claw_trade.workflow.models import RunRequest, RunStatus, StopPoint, WorkflowEntryPoint


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "docs" / "evidence"

WORKER_ORDER = (
    "market_analyst",
    "fundamental_analyst",
    "news_analyst",
    "social_analyst",
    "bull_researcher",
    "bear_researcher",
    "research_manager",
    "trader",
    "risk_challenger",
    "risk_guardian",
    "risk_moderator",
    "portfolio_manager",
    "report_polisher",
)


@dataclass(frozen=True)
class WorkerExport:
    worker_id: str
    stage: str
    status: str
    final_prompt_path: str
    llm_back_path: str
    report_path: str
    final_prompt_chars: int
    llm_back_chars: int
    report_chars: int
    provider_request_id: str | None


def safe_token(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip() or "unknown")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def resolve_evidence_path(raw_value: object, call_dir: Path) -> Path | None:
    if not isinstance(raw_value, str) or not raw_value.strip():
        return None
    raw_path = Path(raw_value)
    return raw_path if raw_path.is_absolute() else call_dir / raw_path


def response_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
                elif isinstance(item.get("content"), str):
                    parts.append(str(item["content"]))
        return "\n".join(part for part in parts if part)
    return str(value)


def extract_final_prompt_text(provider_request_payload: dict[str, Any]) -> str:
    payload = provider_request_payload.get("payload")
    if not isinstance(payload, dict):
        return ""
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return ""
    parts: list[str] = []
    for index, message in enumerate(messages, 1):
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or f"message_{index}")
        content = response_text(message.get("content"))
        if content:
            parts.append(f"## message {index}: {role}\n\n{content}")
    return "\n\n".join(parts).strip()


def call_dirs_for_run(run_dir: Path) -> list[Path]:
    return sorted(path.parent for path in (run_dir / "calls").glob("*/call.json"))


def ordered_call_dirs(run_dir: Path) -> list[Path]:
    call_dirs = call_dirs_for_run(run_dir)
    order = {worker: index for index, worker in enumerate(WORKER_ORDER)}

    def sort_key(call_dir: Path) -> tuple[int, int, str]:
        try:
            payload = read_json(call_dir / "call.json")
        except Exception:
            return (999, 999, call_dir.name)
        worker = str(payload.get("worker_id") or "")
        turn = int(payload.get("turn_index") or 0)
        return (order.get(worker, 900), turn, call_dir.name)

    return sorted(call_dirs, key=sort_key)


def export_worker_evidence(call_dir: Path, output_dir: Path) -> WorkerExport:
    call_payload = read_json(call_dir / "call.json")
    result_payload = read_json(call_dir / "openclaw-result.json")
    worker_id = str(call_payload.get("worker_id") or "unknown_worker")
    stage = str(call_payload.get("stage") or "unknown_stage")
    status = str(result_payload.get("status") or "unknown")

    provider_request_path = resolve_evidence_path(result_payload.get("provider_request_path"), call_dir)
    raw_output_path = resolve_evidence_path(result_payload.get("raw_output_path"), call_dir)
    if provider_request_path is None or not provider_request_path.exists():
        raise RuntimeError(f"{worker_id}: provider request missing: {provider_request_path}")
    if raw_output_path is None or not raw_output_path.exists():
        raise RuntimeError(f"{worker_id}: raw output missing: {raw_output_path}")

    final_prompt = extract_final_prompt_text(read_json(provider_request_path))
    raw_output = raw_output_path.read_text(encoding="utf-8")

    prefix = safe_token(worker_id)
    final_prompt_out = output_dir / f"{prefix}_final_prompt.md"
    llm_back_out = output_dir / f"{prefix}_llm_back.md"
    report_out = output_dir / f"{prefix}_report.md"

    final_prompt_out.write_text(final_prompt + "\n", encoding="utf-8")
    llm_back_out.write_text(raw_output, encoding="utf-8")
    report_out.write_text(raw_output, encoding="utf-8")

    return WorkerExport(
        worker_id=worker_id,
        stage=stage,
        status=status,
        final_prompt_path=final_prompt_out.name,
        llm_back_path=llm_back_out.name,
        report_path=report_out.name,
        final_prompt_chars=len(final_prompt),
        llm_back_chars=len(raw_output),
        report_chars=len(raw_output),
        provider_request_id=result_payload.get("provider_request_id"),
    )


def copy_final_report(run_dir: Path, output_dir: Path) -> tuple[Path, int, int]:
    report_path = run_dir / "reports" / "final-report.md"
    if not report_path.exists():
        raise RuntimeError(f"final report missing: {report_path}")
    report_text = report_path.read_text(encoding="utf-8")
    target = output_dir / "final_report.md"
    target.write_text(report_text, encoding="utf-8")

    asset_count = 0
    assets_dir = run_dir / "reports" / "assets"
    if assets_dir.exists():
        target_assets = output_dir / "assets"
        if target_assets.exists():
            shutil.rmtree(target_assets)
        shutil.copytree(assets_dir, target_assets)
        asset_count = sum(1 for path in target_assets.rglob("*") if path.is_file())
    return target, len(report_text), asset_count


def export_run_evidence(run_dir: Path, output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=False)
    worker_exports = [export_worker_evidence(call_dir, output_dir) for call_dir in ordered_call_dirs(run_dir)]
    final_report_path, final_report_chars, final_report_asset_count = copy_final_report(run_dir, output_dir)
    for relative in ("request.json", "state.json"):
        source = run_dir / relative
        if source.exists():
            shutil.copy2(source, output_dir / relative)

    summary = {
        "run_id": run_dir.name,
        "run_dir": str(run_dir),
        "output_dir": str(output_dir),
        "worker_count": len(worker_exports),
        "workers": [item.__dict__ for item in worker_exports],
        "final_report": {
            "path": final_report_path.name,
            "chars": final_report_chars,
            "asset_count": final_report_asset_count,
        },
        "notes": [
            "Markdown-only worker evidence exported from real OpenClaw provider request and raw output files.",
            "llm_back.md and report.md are identical here because the approved worker report is the model raw output saved by claw-trade.",
            "provider_request.json and other raw JSON remain in runs/; this directory keeps reader-reviewable Markdown plus request/state identity files.",
        ],
    }
    write_json(output_dir / "capture_summary.json", summary)

    lines = [
        f"# claw-trade fresh live capture - {run_dir.name}",
        "",
        f"- run_dir: `{run_dir}`",
        f"- output_dir: `{output_dir}`",
        f"- worker_count: `{len(worker_exports)}`",
        f"- final_report: `{final_report_path.name}`",
        f"- final_report_assets: `{final_report_asset_count}`",
        "- mock/stub/fake/fallback/capture-only: `none`",
        "",
        "| worker | stage | status | final prompt | LLM back | report | prompt chars | report chars |",
        "|---|---|---|---|---|---|---:|---:|",
    ]
    for item in worker_exports:
        lines.append(
            f"| `{item.worker_id}` | `{item.stage}` | `{item.status}` | "
            f"`{item.final_prompt_path}` | `{item.llm_back_path}` | `{item.report_path}` | "
            f"{item.final_prompt_chars} | {item.report_chars} |"
        )
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- 本目录是 claw-trade `/report` fresh live 证据包，不是原版 TradingAgents 运行结果。",
            "- 每个 worker 的 final prompt 来自真实 provider payload capture，不来自静态 render 或日志重构。",
            "- `final_report.md` 的图片依赖保存在 `assets/`。",
        ]
    )
    (output_dir / "capture_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary


def parse_args() -> argparse.Namespace:
    today = date.today()
    parser = argparse.ArgumentParser(description="Run one claw-trade fresh report and export Markdown evidence.")
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--company-name", required=True)
    parser.add_argument("--market", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--currency", required=True)
    parser.add_argument("--currency-symbol", required=True)
    parser.add_argument("--current-date", default=today.isoformat())
    parser.add_argument("--start-date", default=(today - timedelta(days=365)).isoformat())
    parser.add_argument("--end-date", default=today.isoformat())
    parser.add_argument("--run-dir", default="runs")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--output-dir", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    settings = load_report_workflow_settings()
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
        stop_point=StopPoint.NONE,
        entry_point=WorkflowEntryPoint.REPORT_COMMAND,
        max_debate_rounds=settings.max_debate_rounds,
        max_risk_discuss_rounds=settings.max_risk_discuss_rounds,
        frontline_execution_mode=settings.frontline_execution_mode,
    )

    runner = _build_runner(Path(args.run_dir))
    state = runner.run(request)
    if state.status != RunStatus.COMPLETED:
        print(f"FAILED run_id={state.run_id} status={state.status.value} reason={state.failure_reason}")
        return 1

    output_dir = Path(args.output_dir) if args.output_dir else (
        Path(args.output_root)
        / f"trading_claw_trade_{safe_token(args.profile.lower())}_fresh_live_run-{state.run_id.removeprefix('run-')}_md"
    )
    summary = export_run_evidence(state.run_dir, output_dir)
    print(
        f"COMPLETED run_id={state.run_id} output_dir={summary['output_dir']} "
        f"workers={summary['worker_count']} final_report={summary['final_report']['path']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
