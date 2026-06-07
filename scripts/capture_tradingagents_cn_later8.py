#!/usr/bin/env python3
"""Capture TradingAgents-CN later-8 prompts, LLM replies, and node reports.

This script is evidence-only. It runs the original TradingAgents-CN later-stage
node functions with a real LangChain LLM client, records the exact prompt/input
passed to the LLM, and saves the corresponding response and state material.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

TRADINGAGENTS_CN_ROOT = Path("/home/frank/src/TradingAgents-CN")
CLAW_TRADE_ROOT = Path("/home/frank/src/claw-trade")


WORKER_MAP = {
    "bull_researcher": "TradingAgents-CN Bull Researcher",
    "bear_researcher": "TradingAgents-CN Bear Researcher",
    "research_manager": "TradingAgents-CN Research Manager",
    "trader": "TradingAgents-CN Trader",
    "risk_challenger": "TradingAgents-CN Risky Analyst",
    "risk_guardian": "TradingAgents-CN Safe Analyst",
    "risk_moderator": "TradingAgents-CN Neutral Analyst",
    "portfolio_manager": "TradingAgents-CN Risk Judge",
}


DEFAULT_INPUTS = {
    "market_report": CLAW_TRADE_ROOT
    / "docs/evidence/market_prompt_feedback/tradingagents_cn_market_after_tuple_fix_600519_20260506_report.md",
    "fundamentals_report": CLAW_TRADE_ROOT
    / "docs/evidence/frontline_prompt_feedback/tradingagents_cn_fundamentals_600519_20260506_report.md",
    "news_report": CLAW_TRADE_ROOT
    / "docs/evidence/frontline_prompt_feedback/tradingagents_cn_news_600519_20260506_report.md",
    "sentiment_report": CLAW_TRADE_ROOT
    / "docs/evidence/frontline_prompt_feedback/tradingagents_cn_social_600519_20260506_report.md",
}


class CaptureError(RuntimeError):
    pass


class StaticMemory:
    def __init__(self, label: str) -> None:
        self.label = label

    def get_memories(self, curr_situation: str, n_matches: int = 2) -> list[dict[str, str]]:
        return [
            {
                "recommendation": (
                    f"{self.label}历史反思：在资料不完整或观点分歧较大时，应明确区分"
                    "已验证证据、推断和需要继续观察的触发条件。"
                )
            }
        ][:n_matches]


class CaptureOnlyModel:
    def invoke(self, prompt_input: Any, *args: Any, **kwargs: Any) -> Any:
        class CaptureOnlyResponse:
            content = "【CAPTURE_ONLY_NO_EXTERNAL_LLM：外部 LLM 调用被环境策略阻止，本段不是模型真实返回。】"
            response_metadata = {"mode": "capture_only_no_external_llm"}
            usage_metadata = None

        return CaptureOnlyResponse()


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


def load_tradingagents_settings() -> dict[str, Any]:
    settings_path = TRADINGAGENTS_CN_ROOT / "config/settings.json"
    if not settings_path.exists():
        return {}
    try:
        return json.loads(settings_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def install_company_name_override(ticker: str, company_name: str) -> None:
    if not company_name:
        return
    import tradingagents.dataflows.interface as interface

    def _get_china_stock_info_unified(ticker_code: str, *args: Any, **kwargs: Any) -> str:
        if ticker_code == ticker:
            return (
                f"股票代码: {ticker_code}\n"
                f"股票名称: {company_name}\n"
                "所属市场: 中国A股\n"
                "数据来源: capture_company_name_override\n"
            )
        return (
            f"股票代码: {ticker_code}\n"
            f"股票名称: {company_name}\n"
            "所属市场: 中国A股\n"
            "数据来源: capture_company_name_override\n"
        )

    interface.get_china_stock_info_unified = _get_china_stock_info_unified


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def local_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def read_text(path: Path) -> str:
    if not path.exists():
        raise CaptureError(f"missing input file: {path}")
    return path.read_text(encoding="utf-8")


def jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, list):
        return [jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if hasattr(value, "dict"):
        try:
            return jsonable(value.dict())
        except Exception:
            pass
    if hasattr(value, "model_dump"):
        try:
            return jsonable(value.model_dump())
        except Exception:
            pass
    if hasattr(value, "__dict__"):
        data = {
            key: item
            for key, item in vars(value).items()
            if not key.startswith("_")
        }
        if data:
            return jsonable(data)
    return repr(value)


def response_content(response: Any) -> str:
    content = getattr(response, "content", response)
    if isinstance(content, list):
        texts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                texts.append(str(item.get("text", "")))
            elif isinstance(item, str):
                texts.append(item)
        return "\n".join(part for part in texts if part)
    return "" if content is None else str(content)


def prompt_to_markdown(prompt_input: Any) -> str:
    if isinstance(prompt_input, str):
        return prompt_input
    if isinstance(prompt_input, list):
        chunks: list[str] = []
        for index, message in enumerate(prompt_input, 1):
            role = None
            content = None
            if isinstance(message, dict):
                role = message.get("role")
                content = message.get("content")
            else:
                role = getattr(message, "type", None) or getattr(message, "role", None)
                content = getattr(message, "content", None)
            chunks.append(f"## message {index}: {role or 'unknown'}\n\n{response_content(content)}")
        return "\n\n".join(chunks)
    return json.dumps(jsonable(prompt_input), ensure_ascii=False, indent=2)


class RecordingLLM:
    def __init__(self, real_llm: Any, mode: str) -> None:
        self.real_llm = real_llm
        self.mode = mode
        self.calls: list[dict[str, Any]] = []
        self.active_worker: str | None = None

    def invoke(self, prompt_input: Any, *args: Any, **kwargs: Any) -> Any:
        started_at = utc_now()
        started_monotonic = time.monotonic()
        prompt_record = {
            "worker_id": self.active_worker,
            "llm_call_mode": self.mode,
            "started_at_utc": started_at,
            "input_type": type(prompt_input).__name__,
            "input": jsonable(prompt_input),
            "prompt_markdown": prompt_to_markdown(prompt_input),
        }
        try:
            response = self.real_llm.invoke(prompt_input, *args, **kwargs)
        except Exception as exc:
            prompt_record.update(
                {
                    "status": "error",
                    "finished_at_utc": utc_now(),
                    "elapsed_seconds": round(time.monotonic() - started_monotonic, 3),
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )
            self.calls.append(prompt_record)
            raise
        content = response_content(response)
        prompt_record.update(
            {
                "status": "completed",
                "finished_at_utc": utc_now(),
                "elapsed_seconds": round(time.monotonic() - started_monotonic, 3),
                "response": {
                    "content": content,
                    "content_length": len(content),
                    "raw": jsonable(response),
                    "response_metadata": jsonable(getattr(response, "response_metadata", None)),
                    "usage_metadata": jsonable(getattr(response, "usage_metadata", None)),
                },
            }
        )
        self.calls.append(prompt_record)
        return response


@dataclass
class WorkerCapture:
    worker_id: str
    status: str
    prompt_path: Path | None
    llm_back_path: Path | None
    report_path: Path | None
    state_path: Path | None
    error: str | None = None


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def write_worker_artifacts(
    out_dir: Path,
    worker_id: str,
    call: dict[str, Any] | None,
    report: str,
    state_delta: Any,
) -> WorkerCapture:
    prefix = out_dir / worker_id
    prompt_raw = Path(f"{prefix}_final_prompt_raw.json")
    prompt_md = Path(f"{prefix}_final_prompt.md")
    llm_raw = Path(f"{prefix}_llm_back_raw.json")
    llm_md = Path(f"{prefix}_llm_back.md")
    report_md = Path(f"{prefix}_report.md")
    state_raw = Path(f"{prefix}_state_delta_raw.json")

    if call is None:
        report_md.write_text(report, encoding="utf-8")
        write_json(state_raw, jsonable(state_delta))
        return WorkerCapture(worker_id, "missing_llm_call", None, None, report_md, state_raw)

    write_json(prompt_raw, {key: value for key, value in call.items() if key != "response"})
    prompt_md.write_text(call.get("prompt_markdown", ""), encoding="utf-8")
    write_json(llm_raw, call.get("response", {}))
    llm_md.write_text(call.get("response", {}).get("content", ""), encoding="utf-8")
    report_md.write_text(report, encoding="utf-8")
    write_json(state_raw, jsonable(state_delta))

    return WorkerCapture(worker_id, call.get("status", "unknown"), prompt_md, llm_raw, report_md, state_raw)


def latest_call_for(llm: RecordingLLM, worker_id: str, start_index: int) -> dict[str, Any] | None:
    for call in reversed(llm.calls[start_index:]):
        if call.get("worker_id") == worker_id:
            return call
    return None


def invoke_worker(
    worker_id: str,
    node: Callable[..., dict[str, Any]],
    state: dict[str, Any],
    llm: RecordingLLM,
    out_dir: Path,
    report_extractor: Callable[[dict[str, Any], dict[str, Any]], str],
) -> WorkerCapture:
    before = len(llm.calls)
    llm.active_worker = worker_id
    try:
        delta = node(state)
    except Exception as exc:
        call = latest_call_for(llm, worker_id, before)
        if call is not None:
            call["node_error"] = {"type": type(exc).__name__, "message": str(exc)}
        err_path = out_dir / f"{worker_id}_error.json"
        write_json(
            err_path,
            {
                "worker_id": worker_id,
                "status": "error",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "captured_at_utc": utc_now(),
                "call": call,
            },
        )
        return WorkerCapture(worker_id, "error", None, None, None, err_path, str(exc))
    finally:
        llm.active_worker = None

    state.update(delta)
    call = latest_call_for(llm, worker_id, before)
    report = report_extractor(delta, state)
    return write_worker_artifacts(out_dir, worker_id, call, report, delta)


def build_initial_state(inputs: dict[str, str], ticker: str) -> dict[str, Any]:
    return {
        "company_of_interest": ticker,
        "trade_date": "2026-05-11",
        "market_report": inputs["market_report"],
        "sentiment_report": inputs["sentiment_report"],
        "news_report": inputs["news_report"],
        "fundamentals_report": inputs["fundamentals_report"],
        "investment_debate_state": {
            "history": "",
            "bull_history": "",
            "bear_history": "",
            "current_response": "",
            "judge_decision": "",
            "count": 0,
        },
        "risk_debate_state": {
            "history": "",
            "risky_history": "",
            "safe_history": "",
            "neutral_history": "",
            "latest_speaker": "",
            "current_risky_response": "",
            "current_safe_response": "",
            "current_neutral_response": "",
            "judge_decision": "",
            "count": 0,
        },
    }


def make_real_llm(args: argparse.Namespace) -> Any:
    sys.path.insert(0, str(TRADINGAGENTS_CN_ROOT))
    if args.capture_only:
        return CaptureOnlyModel()

    load_env_file(CLAW_TRADE_ROOT / ".env.local")
    load_env_file(TRADINGAGENTS_CN_ROOT / ".env")

    from tradingagents.llm_clients import create_llm_client
    from tradingagents.llm_clients.provider_keys import env_key_for_provider

    provider = args.provider
    api_key = None
    env_key = env_key_for_provider(provider)
    if env_key:
        api_key = os.getenv(env_key)

    client = create_llm_client(
        provider=provider,
        model=args.model,
        base_url=args.base_url or None,
        api_key=api_key,
        temperature=args.temperature,
        max_tokens=args.max_tokens,
        timeout=args.timeout,
        max_retries=args.max_retries,
    )
    return client.get_llm()


def run_capture(args: argparse.Namespace) -> Path:
    sys.path.insert(0, str(TRADINGAGENTS_CN_ROOT))

    from tradingagents.agents.managers.research_manager import create_research_manager
    from tradingagents.agents.managers.risk_manager import create_risk_manager
    from tradingagents.agents.researchers.bear_researcher import create_bear_researcher
    from tradingagents.agents.researchers.bull_researcher import create_bull_researcher
    from tradingagents.agents.risk_mgmt.aggresive_debator import create_risky_debator
    from tradingagents.agents.risk_mgmt.conservative_debator import create_safe_debator
    from tradingagents.agents.risk_mgmt.neutral_debator import create_neutral_debator
    from tradingagents.agents.trader.trader import create_trader

    install_company_name_override(args.ticker, args.company_name)

    output_dir = Path(args.output_dir) if args.output_dir else (
        CLAW_TRADE_ROOT
        / "docs/evidence/later8_prompt_feedback"
        / f"tradingagents_cn_later8_cn_a_600519_live_{local_stamp()}"
    )
    output_dir.mkdir(parents=True, exist_ok=False)

    input_texts = {key: read_text(path) for key, path in DEFAULT_INPUTS.items()}
    state = build_initial_state(input_texts, args.ticker)

    real_llm = make_real_llm(args)
    llm_mode = "capture_only_no_external_llm" if args.capture_only else "live_external_llm"
    llm = RecordingLLM(real_llm, llm_mode)

    captures: list[WorkerCapture] = []
    capture_started_at = utc_now()

    bull = create_bull_researcher(llm, StaticMemory("看涨研究员"))
    bear = create_bear_researcher(llm, StaticMemory("看跌研究员"))
    research_manager = create_research_manager(llm, StaticMemory("研究经理"))
    trader = create_trader(llm, StaticMemory("交易员"))
    risk_challenger = create_risky_debator(llm)
    risk_guardian = create_safe_debator(llm)
    risk_moderator = create_neutral_debator(llm)
    portfolio_manager = create_risk_manager(llm, StaticMemory("组合经理"))

    sequence: list[tuple[str, Callable[..., dict[str, Any]], Callable[[dict[str, Any], dict[str, Any]], str]]] = [
        ("bull_researcher", bull, lambda delta, s: delta["investment_debate_state"]["current_response"]),
        ("bear_researcher", bear, lambda delta, s: delta["investment_debate_state"]["current_response"]),
        ("research_manager", research_manager, lambda delta, s: delta["investment_plan"]),
        ("trader", trader, lambda delta, s: delta["trader_investment_plan"]),
        ("risk_challenger", risk_challenger, lambda delta, s: delta["risk_debate_state"]["current_risky_response"]),
        ("risk_guardian", risk_guardian, lambda delta, s: delta["risk_debate_state"]["current_safe_response"]),
        ("risk_moderator", risk_moderator, lambda delta, s: delta["risk_debate_state"]["current_neutral_response"]),
        ("portfolio_manager", portfolio_manager, lambda delta, s: delta["final_trade_decision"]),
    ]

    for worker_id, node, extractor in sequence:
        print(f"[capture] invoking {worker_id} -> {WORKER_MAP[worker_id]}", flush=True)
        capture = invoke_worker(worker_id, node, state, llm, output_dir, extractor)
        captures.append(capture)
        print(f"[capture] {worker_id}: {capture.status}", flush=True)
        if capture.status == "error" and args.stop_on_error:
            break

    write_json(output_dir / "all_llm_calls_raw.json", llm.calls)
    write_json(output_dir / "final_state_raw.json", jsonable(state))

    input_summary = {
        key: {
            "path": str(DEFAULT_INPUTS[key]),
            "chars": len(text),
            "empty": len(text.strip()) == 0,
        }
        for key, text in input_texts.items()
    }
    summary = {
        "project": "TradingAgents-CN",
        "source_root": str(TRADINGAGENTS_CN_ROOT),
        "capture_started_at_utc": capture_started_at,
        "capture_finished_at_utc": utc_now(),
        "ticker": args.ticker,
        "market": "CN_A",
        "provider": args.provider,
        "model": args.model,
        "base_url": args.base_url,
        "llm_call_mode": llm_mode,
        "company_name_override": args.company_name,
        "max_tokens": args.max_tokens,
        "temperature": args.temperature,
        "worker_mapping": WORKER_MAP,
        "upstream_inputs": input_summary,
        "captures": [capture.__dict__ for capture in captures],
        "notes": [
            "This is an evidence-only run of original TradingAgents-CN later-stage node functions.",
            "The LLM calls are real provider calls unless llm_call_mode is capture_only_no_external_llm.",
            "Later-stage nodes consume existing TradingAgents-CN frontline evidence files listed in upstream_inputs.",
            "portfolio_manager maps to TradingAgents-CN Risk Judge because claw-trade makes PM the final portfolio decision owner.",
        ],
    }
    write_json(output_dir / "capture_summary.json", jsonable(summary))

    lines = [
        f"# TradingAgents-CN later-8 CN_A capture - {args.ticker}",
        "",
        f"- project: `{TRADINGAGENTS_CN_ROOT}`",
        f"- captured_at_utc: `{summary['capture_finished_at_utc']}`",
        f"- provider/model: `{args.provider}` / `{args.model}`",
        f"- llm_call_mode: `{llm_mode}`",
        f"- output_dir: `{output_dir}`",
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
            "- 本目录只用于 prompt 对齐取证，不是 claw-trade 产品运行结果。",
            "- 当 `llm_call_mode=live_external_llm` 时，LLM 调用为真实 provider 调用；当为 `capture_only_no_external_llm` 时，只保存 prompt，LLM back/report 是阻断占位。",
            "- `portfolio_manager` 对应 TradingAgents-CN 的 `Risk Judge`，因为当前 claw-trade 设计由 PM 拥有最终组合决策。",
        ]
    )
    (output_dir / "capture_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output_dir


def parse_args() -> argparse.Namespace:
    settings = load_tradingagents_settings()
    default_provider = settings.get("llm_provider") or "deepseek"
    default_model = settings.get("quick_think_llm") or "deepseek-chat"
    default_base_url = settings.get("backend_url") or "https://api.deepseek.com"
    quick_config = settings.get("quick_model_config") or {}
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", default="600519")
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
    parser.add_argument("--company-name", default="")
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
