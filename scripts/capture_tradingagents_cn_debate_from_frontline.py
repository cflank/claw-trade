#!/usr/bin/env python3
"""Capture TradingAgents-CN investment debate from existing frontline reports.

This script does not run frontline analysts. It reads already-produced
frontline report text, runs only the original TradingAgents-CN bull/bear
researcher nodes, and saves final prompts, LLM replies, reports, and state
deltas for prompt-alignment evidence.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable

from capture_tradingagents_cn_later8 import (
    CLAW_TRADE_ROOT,
    DEFAULT_INPUTS,
    TRADINGAGENTS_CN_ROOT,
    CaptureError,
    RecordingLLM,
    StaticMemory,
    build_initial_state,
    install_company_name_override,
    invoke_worker,
    jsonable,
    local_stamp,
    make_real_llm,
    read_text,
    utc_now,
    write_json,
)


WORKER_MAP = {
    "bull_researcher": "TradingAgents-CN Bull Researcher",
    "bear_researcher": "TradingAgents-CN Bear Researcher",
}

INPUT_KEYS = ("market_report", "fundamentals_report", "news_report", "sentiment_report")

FRONTLINE_PATTERNS = {
    "market_report": (
        "market_analyst_report.md",
        "market_analyst_llm_back.md",
        "*market_analyst*report*.md",
        "*market_analyst*llm_back.md",
        "*market*report*.md",
    ),
    "fundamentals_report": (
        "fundamental_analyst_report.md",
        "fundamental_analyst_llm_back.md",
        "*fundamental_analyst*report*.md",
        "*fundamental_analyst*llm_back.md",
        "*fundamentals*report*.md",
        "*fundamental*report*.md",
    ),
    "news_report": (
        "news_analyst_report.md",
        "news_analyst_llm_back.md",
        "*news_analyst*report*.md",
        "*news_analyst*llm_back.md",
        "*news*report*.md",
    ),
    "sentiment_report": (
        "social_analyst_report.md",
        "social_analyst_llm_back.md",
        "*social_analyst*report*.md",
        "*social_analyst*llm_back.md",
        "*social*report*.md",
        "*sentiment*report*.md",
    ),
}


def safe_ticker_text(ticker: str) -> str:
    raw = ticker.strip() or "unknown"
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", raw)


def latest_frontline_dir() -> Path | None:
    root = CLAW_TRADE_ROOT / "docs/evidence/frontline_prompt_feedback"
    candidates = sorted(root.glob("frontline_full_cn_prompt_direct_live_*"))
    return candidates[-1] if candidates else None


def first_existing_match(root: Path, patterns: tuple[str, ...]) -> Path | None:
    for pattern in patterns:
        exact = root / pattern
        if exact.exists() and exact.is_file():
            return exact
        matches = sorted(path for path in root.glob(pattern) if path.is_file())
        if matches:
            return matches[0]
    return None


def explicit_input_paths(args: argparse.Namespace) -> dict[str, Path]:
    out: dict[str, Path] = {}
    mapping = {
        "market_report": args.market_report,
        "fundamentals_report": args.fundamentals_report,
        "news_report": args.news_report,
        "sentiment_report": args.sentiment_report,
    }
    for key, raw_path in mapping.items():
        if raw_path:
            out[key] = Path(raw_path)
    return out


def resolve_input_paths(args: argparse.Namespace) -> dict[str, Path]:
    paths = dict(DEFAULT_INPUTS)
    detected_dir = Path(args.frontline_dir) if args.frontline_dir else latest_frontline_dir()
    if detected_dir is not None:
        if not detected_dir.exists() or not detected_dir.is_dir():
            raise CaptureError(f"frontline dir 不存在或不是目录: {detected_dir}")
        for key in INPUT_KEYS:
            match = first_existing_match(detected_dir, FRONTLINE_PATTERNS[key])
            if match is not None:
                paths[key] = match

    paths.update(explicit_input_paths(args))
    missing = [f"{key}={path}" for key, path in paths.items() if not path.exists()]
    if missing:
        raise CaptureError("frontline 输入文件缺失: " + "; ".join(missing))
    return paths


def load_frontline_inputs(args: argparse.Namespace) -> tuple[dict[str, Path], dict[str, str]]:
    paths = resolve_input_paths(args)
    texts = {key: read_text(path) for key, path in paths.items()}
    empty = [key for key, text in texts.items() if not text.strip()]
    if empty and not args.allow_empty_report:
        detail = ", ".join(f"{key}={paths[key]}" for key in empty)
        raise CaptureError(
            "frontline report 为空，默认拒绝用未完成材料跑 debate；"
            f"如只是验证脚本可加 --allow-empty-report。empty: {detail}"
        )
    return paths, texts


def write_summary(
    *,
    output_dir: Path,
    args: argparse.Namespace,
    capture_started_at: str,
    input_paths: dict[str, Path],
    input_texts: dict[str, str],
    captures: list[Any],
    llm: RecordingLLM,
    state: dict[str, Any],
) -> None:
    write_json(output_dir / "all_llm_calls_raw.json", llm.calls)
    write_json(output_dir / "final_state_raw.json", jsonable(state))

    input_summary = {
        key: {
            "path": str(input_paths[key]),
            "chars": len(input_texts[key]),
            "empty": len(input_texts[key].strip()) == 0,
        }
        for key in INPUT_KEYS
    }
    summary = {
        "project": "TradingAgents-CN",
        "source_root": str(TRADINGAGENTS_CN_ROOT),
        "capture_scope": "investment_debate_only_from_existing_frontline",
        "capture_started_at_utc": capture_started_at,
        "capture_finished_at_utc": utc_now(),
        "ticker": args.ticker,
        "market": "CN_A",
        "provider": args.provider,
        "model": args.model,
        "base_url": args.base_url,
        "llm_call_mode": "capture_only_no_external_llm" if args.capture_only else "live_external_llm",
        "company_name_override": args.company_name,
        "frontline_dir": args.frontline_dir,
        "allow_empty_report": args.allow_empty_report,
        "upstream_inputs": input_summary,
        "captures": [capture.__dict__ for capture in captures],
        "notes": [
            "This evidence run does not execute frontline analysts.",
            "It runs only TradingAgents-CN bull_researcher then bear_researcher.",
            "The debate state follows the original CN one-round order: bull first, bear sees bull response.",
        ],
    }
    write_json(output_dir / "capture_summary.json", jsonable(summary))

    lines = [
        f"# TradingAgents-CN debate-only CN_A capture - {args.ticker}",
        "",
        f"- project: `{TRADINGAGENTS_CN_ROOT}`",
        f"- captured_at_utc: `{summary['capture_finished_at_utc']}`",
        f"- provider/model: `{args.provider}` / `{args.model}`",
        f"- llm_call_mode: `{summary['llm_call_mode']}`",
        f"- output_dir: `{output_dir}`",
        f"- frontline_dir: `{args.frontline_dir or '<auto/default>'}`",
        "",
        "## Upstream Inputs",
        "",
        "| state field | source | chars | empty |",
        "|---|---:|---:|---:|",
    ]
    for key, item in input_summary.items():
        lines.append(f"| `{key}` | `{item['path']}` | {item['chars']} | {item['empty']} |")
    lines.extend(
        [
            "",
            "## Captures",
            "",
            "| worker | CN node | status | final prompt | LLM back | report | state delta |",
            "|---|---|---|---|---|---|---|",
        ]
    )
    for capture in captures:
        lines.append(
            "| `{worker}` | {node} | {status} | {prompt} | {llm_back} | {report} | {state} |".format(
                worker=capture.worker_id,
                node=WORKER_MAP.get(capture.worker_id, ""),
                status=capture.status,
                prompt=f"`{capture.prompt_path.name}`" if capture.prompt_path else "",
                llm_back=f"`{capture.llm_back_path.name}`" if capture.llm_back_path else "",
                report=f"`{capture.report_path.name}`" if capture.report_path else "",
                state=f"`{capture.state_path.name}`" if capture.state_path else "",
            )
        )
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- 本目录只用于 CN prompt 对齐取证，不是 claw-trade 产品运行结果。",
            "- 脚本不运行 market/fundamental/news/social，只读取上表已有报告正文。",
            "- bear 的 prompt/state 继承 bull 的发言，顺序对齐 TradingAgents-CN 默认一轮 debate。",
        ]
    )
    (output_dir / "capture_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_capture(args: argparse.Namespace) -> Path:
    sys.path.insert(0, str(TRADINGAGENTS_CN_ROOT))

    from tradingagents.agents.researchers.bear_researcher import create_bear_researcher
    from tradingagents.agents.researchers.bull_researcher import create_bull_researcher

    install_company_name_override(args.ticker, args.company_name)

    output_dir = Path(args.output_dir) if args.output_dir else (
        CLAW_TRADE_ROOT
        / "docs/evidence/invest_debate_prompt_feedback"
        / f"tradingagents_cn_debate_cn_a_{safe_ticker_text(args.ticker)}_live_{local_stamp()}"
    )
    output_dir.mkdir(parents=True, exist_ok=False)

    input_paths, input_texts = load_frontline_inputs(args)
    state = build_initial_state(input_texts, args.ticker)

    real_llm = make_real_llm(args)
    llm_mode = "capture_only_no_external_llm" if args.capture_only else "live_external_llm"
    llm = RecordingLLM(real_llm, llm_mode)
    capture_started_at = utc_now()

    bull = create_bull_researcher(llm, StaticMemory("看涨研究员"))
    bear = create_bear_researcher(llm, StaticMemory("看跌研究员"))

    sequence: list[tuple[str, Callable[..., dict[str, Any]], Callable[[dict[str, Any], dict[str, Any]], str]]] = [
        ("bull_researcher", bull, lambda delta, s: delta["investment_debate_state"]["current_response"]),
        ("bear_researcher", bear, lambda delta, s: delta["investment_debate_state"]["current_response"]),
    ]

    captures: list[Any] = []
    for worker_id, node, extractor in sequence:
        print(f"[capture] invoking {worker_id} -> {WORKER_MAP[worker_id]}", flush=True)
        capture = invoke_worker(worker_id, node, state, llm, output_dir, extractor)
        captures.append(capture)
        print(f"[capture] {worker_id}: {capture.status}", flush=True)
        if capture.status == "error" and args.stop_on_error:
            break

    write_summary(
        output_dir=output_dir,
        args=args,
        capture_started_at=capture_started_at,
        input_paths=input_paths,
        input_texts=input_texts,
        captures=captures,
        llm=llm,
        state=state,
    )
    return output_dir


def parse_args() -> argparse.Namespace:
    from capture_tradingagents_cn_later8 import load_tradingagents_settings

    settings = load_tradingagents_settings()
    default_provider = settings.get("llm_provider") or "deepseek"
    default_model = settings.get("quick_think_llm") or "deepseek-chat"
    default_base_url = settings.get("backend_url") or "https://api.deepseek.com"
    quick_config = settings.get("quick_model_config") or {}

    parser = argparse.ArgumentParser(
        description="Run only TradingAgents-CN bull/bear debate from existing frontline report files.",
    )
    parser.add_argument("--ticker", default="600519")
    parser.add_argument("--company-name", default="")
    parser.add_argument("--frontline-dir", default="")
    parser.add_argument("--market-report", default="")
    parser.add_argument("--fundamentals-report", default="")
    parser.add_argument("--news-report", default="")
    parser.add_argument("--sentiment-report", default="")
    parser.add_argument("--allow-empty-report", action="store_true")
    parser.add_argument("--provider", default=default_provider)
    parser.add_argument("--model", default=default_model)
    parser.add_argument("--base-url", default=default_base_url)
    parser.add_argument("--max-tokens", type=int, default=int(quick_config.get("max_tokens") or 4096))
    parser.add_argument("--temperature", type=float, default=float(quick_config.get("temperature") or 0.1))
    parser.add_argument("--timeout", type=int, default=int(quick_config.get("timeout") or 240))
    parser.add_argument("--max-retries", type=int, default=1)
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--stop-on-error", action="store_true")
    parser.add_argument("--capture-only", action="store_true")
    return parser.parse_args()


def main() -> int:
    try:
        output_dir = run_capture(parse_args())
    except Exception as exc:
        print(f"[capture] failed: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 1
    print(f"[capture] done: {output_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
