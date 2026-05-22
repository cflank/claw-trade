#!/usr/bin/env python3
"""Run one claw-trade fresh report and export Markdown evidence."""

from __future__ import annotations

import argparse
import json
import re
import shutil
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from claw_trade.cli.run_control import _build_runner, _data_gateway_mode_from_env
from claw_trade.config.report_workflow_settings import load_report_workflow_settings
from claw_trade.workflow.models import RunStatus
from claw_trade.workflow.report_request_factory import build_report_run_request

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
    turn_index: int
    status: str
    final_prompt_path: str
    provider_prompt_initial_path: str
    provider_prompt_final_path: str
    llm_back_path: str
    report_path: str
    final_prompt_chars: int
    llm_back_chars: int
    report_chars: int
    provider_request_id: str | None


def safe_token(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip() or "unknown")


def call_turn_index(payload: dict[str, Any]) -> int:
    try:
        return int(payload.get("turn_index") or 0)
    except (TypeError, ValueError):
        return 0


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if isinstance(payload, dict):
            entries.append(payload)
    return entries


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


def format_tool_calls(message: dict[str, Any]) -> str:
    tool_calls = message.get("tool_calls")
    if not isinstance(tool_calls, list) or not tool_calls:
        return ""

    lines = ["### tool_calls"]
    for index, tool_call in enumerate(tool_calls, 1):
        if not isinstance(tool_call, dict):
            lines.append(f"- call {index}: {tool_call}")
            continue
        function = tool_call.get("function")
        if isinstance(function, dict):
            name = function.get("name") or tool_call.get("name") or "unknown"
            arguments = function.get("arguments")
        else:
            name = tool_call.get("name") or "unknown"
            arguments = tool_call.get("arguments")
        lines.append(f"- call {index}: `{name}`")
        if arguments is not None:
            lines.append(f"  args: `{arguments}`")
    return "\n".join(lines)


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
        content_parts: list[str] = []
        content = response_text(message.get("content"))
        if content:
            content_parts.append(content)
        tool_call_text = format_tool_calls(message)
        if tool_call_text:
            content_parts.append(tool_call_text)
        if content_parts:
            parts.append(f"## message {index}: {role}\n\n" + "\n\n".join(content_parts))
    return "\n\n".join(parts).strip()


def provider_request_payloads_for_export(call_dir: Path, provider_request_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    requests_jsonl = call_dir / "provider-requests.jsonl"
    if requests_jsonl.exists():
        entries = read_jsonl(requests_jsonl)
        if entries:
            return entries[0], entries[-1]
    payload = read_json(provider_request_path)
    return payload, payload


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
        turn = call_turn_index(payload)
        return (order.get(worker, 900), turn, call_dir.name)

    return sorted(call_dirs, key=sort_key)


def export_worker_evidence(call_dir: Path, output_dir: Path, *, file_prefix: str | None = None) -> WorkerExport:
    call_payload = read_json(call_dir / "call.json")
    result_payload = read_json(call_dir / "openclaw-result.json")
    worker_id = str(call_payload.get("worker_id") or "unknown_worker")
    stage = str(call_payload.get("stage") or "unknown_stage")
    turn_index = call_turn_index(call_payload)
    status = str(result_payload.get("status") or "unknown")

    provider_request_path = resolve_evidence_path(result_payload.get("provider_request_path"), call_dir)
    raw_output_path = resolve_evidence_path(result_payload.get("raw_output_path"), call_dir)
    if provider_request_path is None or not provider_request_path.exists():
        raise RuntimeError(f"{worker_id}: provider request missing: {provider_request_path}")
    if raw_output_path is None or not raw_output_path.exists():
        raise RuntimeError(f"{worker_id}: raw output missing: {raw_output_path}")

    initial_provider_request, final_provider_request = provider_request_payloads_for_export(
        call_dir,
        provider_request_path,
    )
    initial_prompt = extract_final_prompt_text(initial_provider_request)
    final_prompt = extract_final_prompt_text(final_provider_request)
    raw_output = raw_output_path.read_text(encoding="utf-8")

    prefix = safe_token(file_prefix or worker_id)
    final_prompt_out = output_dir / f"{prefix}_final_prompt.md"
    provider_prompt_initial_out = output_dir / f"{prefix}_provider_prompt_initial.md"
    provider_prompt_final_out = output_dir / f"{prefix}_provider_prompt_final.md"
    llm_back_out = output_dir / f"{prefix}_llm_back.md"
    report_out = output_dir / f"{prefix}_report.md"

    final_prompt_out.write_text(final_prompt + "\n", encoding="utf-8")
    provider_prompt_initial_out.write_text(initial_prompt + "\n", encoding="utf-8")
    provider_prompt_final_out.write_text(final_prompt + "\n", encoding="utf-8")
    llm_back_out.write_text(raw_output, encoding="utf-8")
    report_out.write_text(raw_output, encoding="utf-8")

    return WorkerExport(
        worker_id=worker_id,
        stage=stage,
        turn_index=turn_index,
        status=status,
        final_prompt_path=final_prompt_out.name,
        provider_prompt_initial_path=provider_prompt_initial_out.name,
        provider_prompt_final_path=provider_prompt_final_out.name,
        llm_back_path=llm_back_out.name,
        report_path=report_out.name,
        final_prompt_chars=len(final_prompt),
        llm_back_chars=len(raw_output),
        report_chars=len(raw_output),
        provider_request_id=result_payload.get("provider_request_id"),
    )


def copy_final_report(run_dir: Path, output_dir: Path) -> tuple[Path, int, int, int]:
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

    appendix_count = 0
    appendix_dir = run_dir / "reports" / "worker-appendix"
    if appendix_dir.exists():
        target_appendix = output_dir / "worker-appendix"
        if target_appendix.exists():
            shutil.rmtree(target_appendix)
        shutil.copytree(appendix_dir, target_appendix)
        appendix_count = sum(1 for path in target_appendix.rglob("*") if path.is_file())
    return target, len(report_text), asset_count, appendix_count


def export_run_evidence(run_dir: Path, output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=False)
    call_dirs = ordered_call_dirs(run_dir)
    worker_ids = [
        str(read_json(call_dir / "call.json").get("worker_id") or "unknown_worker")
        for call_dir in call_dirs
    ]
    duplicate_workers = {worker_id for worker_id, count in Counter(worker_ids).items() if count > 1}
    used_prefixes: set[str] = set()
    worker_exports: list[WorkerExport] = []
    for call_dir in call_dirs:
        call_payload = read_json(call_dir / "call.json")
        worker_id = str(call_payload.get("worker_id") or "unknown_worker")
        file_prefix = worker_id
        if worker_id in duplicate_workers:
            turn_index = call_turn_index(call_payload)
            file_prefix = f"{worker_id}_t{turn_index:02d}"
            if safe_token(file_prefix) in used_prefixes:
                file_prefix = f"{file_prefix}_{safe_token(call_dir.name)}"
        used_prefixes.add(safe_token(file_prefix))
        worker_exports.append(export_worker_evidence(call_dir, output_dir, file_prefix=file_prefix))
    final_report_path, final_report_chars, final_report_asset_count, worker_appendix_count = copy_final_report(
        run_dir,
        output_dir,
    )
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
            "worker_appendix_count": worker_appendix_count,
        },
        "notes": [
            "Markdown-only worker evidence exported from real OpenClaw provider request and raw output files.",
            "final_prompt.md is the last captured provider request for that worker; provider_prompt_initial.md and provider_prompt_final.md preserve the turn boundary.",
            "llm_back.md and report.md are identical here because the approved worker report is the model raw output saved by claw-trade.",
            "provider_request.json and other raw JSON remain in runs/; this directory keeps reader-reviewable Markdown plus request/state identity files.",
            "worker-appendix/ mirrors the final user package appendix for approved worker raw reports.",
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
        f"- worker_appendix_files: `{worker_appendix_count}`",
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
            "- 每个 worker 的 final prompt 来自最后一条真实 provider payload capture，不来自静态 render 或日志重构。",
            "- 每个 worker 额外保留 `provider_prompt_initial.md` 和 `provider_prompt_final.md`，用于审计多轮工具调用边界。",
            "- `final_report.md` 的图片依赖保存在 `assets/`。",
            "- `worker-appendix/` 保存最终用户包中的 worker 原文附录。",
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
    request = build_report_run_request(
        ticker=args.ticker,
        settings=settings,
        company_name=args.company_name,
        market=args.market,
        profile=args.profile,
        currency=args.currency,
        currency_symbol=args.currency_symbol,
        current_date=args.current_date,
        start_date=args.start_date,
        end_date=args.end_date,
        data_gateway=_data_gateway_mode_from_env(),
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
