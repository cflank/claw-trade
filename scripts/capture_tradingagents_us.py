#!/usr/bin/env python3
"""Capture original TradingAgents US prompts, LLM replies, and reports.

This script is evidence-only. It imports the original TradingAgents project,
runs the original worker node functions with DeepSeek, records the final prompt
input passed to the LLM, and saves each worker's LLM response and report.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


TRADINGAGENTS_ROOT = Path("/home/frank/src/TradingAgents")
CLAW_TRADE_ROOT = Path("/home/frank/src/claw-trade")
OUTPUT_ROOT = CLAW_TRADE_ROOT / "docs/evidence/trading"


WORKER_LABELS = {
    "market_analyst": "Original TradingAgents Market Analyst",
    "social_analyst": "Original TradingAgents Social Analyst",
    "news_analyst": "Original TradingAgents News Analyst",
    "fundamental_analyst": "Original TradingAgents Fundamentals Analyst",
    "bull_researcher": "Original TradingAgents Bull Researcher",
    "bear_researcher": "Original TradingAgents Bear Researcher",
    "research_manager": "Original TradingAgents Research Manager",
    "trader": "Original TradingAgents Trader",
    "risk_challenger": "Original TradingAgents Aggressive Analyst",
    "risk_guardian": "Original TradingAgents Conservative Analyst",
    "risk_moderator": "Original TradingAgents Neutral Analyst",
    "portfolio_manager": "Original TradingAgents Portfolio Manager",
}


class CaptureError(RuntimeError):
    pass


@dataclass
class WorkerCapture:
    worker_id: str
    original_node: str
    status: str
    final_prompt_path: str | None
    llm_back_path: str | None
    report_path: str | None
    state_delta_path: str | None
    llm_call_count: int
    tool_call_count: int
    report_chars: int
    error: str | None = None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def safe_name(path: Path | None) -> str | None:
    return path.name if path else None


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
    if hasattr(value, "type") and hasattr(value, "content"):
        data: dict[str, Any] = {
            "type": getattr(value, "type", None),
            "content": jsonable(getattr(value, "content", None)),
        }
        for attr in (
            "id",
            "name",
            "tool_call_id",
            "tool_calls",
            "invalid_tool_calls",
            "additional_kwargs",
            "response_metadata",
            "usage_metadata",
        ):
            attr_value = getattr(value, attr, None)
            if attr_value:
                data[attr] = jsonable(attr_value)
        return data
    if hasattr(value, "to_messages"):
        try:
            return jsonable(value.to_messages())
        except Exception:
            pass
    if hasattr(value, "model_dump"):
        try:
            return jsonable(value.model_dump())
        except Exception:
            pass
    if hasattr(value, "dict"):
        try:
            return jsonable(value.dict())
        except Exception:
            pass
    return repr(value)


def message_role(message: Any) -> str:
    if isinstance(message, dict):
        return str(message.get("role") or message.get("type") or "unknown")
    return str(getattr(message, "type", None) or getattr(message, "role", None) or "unknown")


def message_content(message: Any) -> str:
    if isinstance(message, dict):
        return response_content(message.get("content"))
    return response_content(getattr(message, "content", ""))


def prompt_to_markdown(prompt_input: Any) -> str:
    if isinstance(prompt_input, str):
        return prompt_input
    if hasattr(prompt_input, "to_messages"):
        try:
            prompt_input = prompt_input.to_messages()
        except Exception:
            pass
    if isinstance(prompt_input, list):
        chunks: list[str] = []
        for index, message in enumerate(prompt_input, 1):
            chunks.append(f"## message {index}: {message_role(message)}\n\n{message_content(message)}")
            tool_calls = getattr(message, "tool_calls", None)
            if tool_calls:
                chunks.append(
                    "### tool_calls\n\n```json\n"
                    + json.dumps(jsonable(tool_calls), ensure_ascii=False, indent=2)
                    + "\n```"
                )
        return "\n\n".join(chunks)
    return json.dumps(jsonable(prompt_input), ensure_ascii=False, indent=2)


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(jsonable(data), ensure_ascii=False, indent=2), encoding="utf-8")


def worker_for_tools(tools: list[Any]) -> str:
    names = {getattr(tool, "name", "") for tool in tools}
    if names == {"get_stock_data", "get_indicators"}:
        return "market_analyst"
    if names == {"get_news"}:
        return "social_analyst"
    if names == {"get_news", "get_global_news"}:
        return "news_analyst"
    if names == {"get_fundamentals", "get_balance_sheet", "get_cashflow", "get_income_statement"}:
        return "fundamental_analyst"
    return "unknown_tool_worker"


def infer_worker_from_input(prompt_input: Any) -> str | None:
    text = prompt_to_markdown(prompt_input)
    if text.startswith("You are a Bull Analyst"):
        return "bull_researcher"
    if text.startswith("You are a Bear Analyst"):
        return "bear_researcher"
    if text.startswith("As the portfolio manager and debate facilitator"):
        return "research_manager"
    if "You are a trading agent analyzing market data" in text:
        return "trader"
    if text.startswith("As the Aggressive Risk Analyst"):
        return "risk_challenger"
    if text.startswith("As the Conservative Risk Analyst"):
        return "risk_guardian"
    if text.startswith("As the Neutral Risk Analyst"):
        return "risk_moderator"
    if text.startswith("As the Portfolio Manager, synthesize"):
        return "portfolio_manager"
    return None


class RecordingLLM:
    def __init__(self, real_llm: Any, mode: str) -> None:
        self.real_llm = real_llm
        self.mode = mode
        self.calls: list[dict[str, Any]] = []
        self.active_worker: str | None = None

    def bind_tools(self, tools: list[Any], *args: Any, **kwargs: Any) -> Any:
        from langchain_core.runnables import RunnableLambda

        real_bound = self.real_llm.bind_tools(tools, *args, **kwargs)
        worker_id = worker_for_tools(tools)
        tool_names = [getattr(tool, "name", repr(tool)) for tool in tools]

        def _invoke(prompt_input: Any, config: Any = None, **invoke_kwargs: Any) -> Any:
            if config is not None:
                invoke_kwargs.setdefault("config", config)
            return self.record_invoke(
                real_bound,
                prompt_input,
                worker_id=worker_id,
                bound_tools=tool_names,
                **invoke_kwargs,
            )

        return RunnableLambda(_invoke)

    def invoke(self, prompt_input: Any, *args: Any, **kwargs: Any) -> Any:
        return self.record_invoke(
            self.real_llm,
            prompt_input,
            worker_id=self.active_worker or infer_worker_from_input(prompt_input),
            bound_tools=[],
            *args,
            **kwargs,
        )

    def record_invoke(
        self,
        target: Any,
        prompt_input: Any,
        *args: Any,
        worker_id: str | None,
        bound_tools: list[str],
        **kwargs: Any,
    ) -> Any:
        started_at = utc_now()
        started_monotonic = time.monotonic()
        prompt_record: dict[str, Any] = {
            "call_index": len(self.calls) + 1,
            "worker_id": worker_id,
            "llm_call_mode": self.mode,
            "started_at_utc": started_at,
            "input_type": type(prompt_input).__name__,
            "bound_tools": bound_tools,
            "input": jsonable(prompt_input),
            "prompt_markdown": prompt_to_markdown(prompt_input),
        }
        try:
            response = target.invoke(prompt_input, *args, **kwargs)
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
        tool_calls = getattr(response, "tool_calls", None) or []
        prompt_record.update(
            {
                "status": "completed",
                "finished_at_utc": utc_now(),
                "elapsed_seconds": round(time.monotonic() - started_monotonic, 3),
                "response": {
                    "content": content,
                    "content_length": len(content),
                    "tool_calls": jsonable(tool_calls),
                    "tool_call_count": len(tool_calls),
                    "raw": jsonable(response),
                    "response_metadata": jsonable(getattr(response, "response_metadata", None)),
                    "usage_metadata": jsonable(getattr(response, "usage_metadata", None)),
                },
            }
        )
        self.calls.append(prompt_record)
        return response

    def __getattr__(self, name: str) -> Any:
        return getattr(self.real_llm, name)


def latest_call_for(llm: RecordingLLM, worker_id: str, start_index: int) -> dict[str, Any] | None:
    for call in reversed(llm.calls[start_index:]):
        if call.get("worker_id") == worker_id:
            return call
    return None


def apply_delta(state: dict[str, Any], delta: dict[str, Any]) -> None:
    for key, value in delta.items():
        if key == "messages":
            state.setdefault("messages", []).extend(value)
        else:
            state[key] = value


def reset_messages(state: dict[str, Any]) -> None:
    from langchain_core.messages import HumanMessage

    state["messages"] = [HumanMessage(content="Continue")]


def write_worker_artifacts(
    output_dir: Path,
    worker_id: str,
    original_node: str,
    call: dict[str, Any] | None,
    report: str,
    state_delta: Any,
    llm_call_count: int,
    tool_call_count: int,
) -> WorkerCapture:
    prompt_raw = output_dir / f"{worker_id}_final_prompt_raw.json"
    prompt_md = output_dir / f"{worker_id}_final_prompt.md"
    llm_raw = output_dir / f"{worker_id}_llm_back_raw.json"
    llm_md = output_dir / f"{worker_id}_llm_back.md"
    report_md = output_dir / f"{worker_id}_report.md"
    state_raw = output_dir / f"{worker_id}_state_delta_raw.json"

    if call is None:
        report_md.write_text(report, encoding="utf-8")
        write_json(state_raw, state_delta)
        return WorkerCapture(
            worker_id,
            original_node,
            "missing_llm_call",
            None,
            None,
            safe_name(report_md),
            safe_name(state_raw),
            llm_call_count,
            tool_call_count,
            len(report),
        )

    write_json(prompt_raw, {key: value for key, value in call.items() if key != "response"})
    prompt_md.write_text(call.get("prompt_markdown", ""), encoding="utf-8")
    write_json(llm_raw, call.get("response", {}))
    llm_md.write_text(call.get("response", {}).get("content", ""), encoding="utf-8")
    report_md.write_text(report, encoding="utf-8")
    write_json(state_raw, state_delta)

    return WorkerCapture(
        worker_id,
        original_node,
        call.get("status", "unknown"),
        safe_name(prompt_md),
        safe_name(llm_md),
        safe_name(report_md),
        safe_name(state_raw),
        llm_call_count,
        tool_call_count,
        len(report),
    )


def run_frontline_worker(
    worker_id: str,
    original_node: str,
    node: Callable[[dict[str, Any]], dict[str, Any]],
    tool_node: Any,
    report_key: str,
    state: dict[str, Any],
    llm: RecordingLLM,
    output_dir: Path,
    max_tool_rounds: int,
) -> WorkerCapture:
    start_index = len(llm.calls)
    tool_call_count = 0
    state_delta: dict[str, Any] = {}

    for round_index in range(max_tool_rounds + 1):
        llm.active_worker = worker_id
        try:
            delta = node(state)
        finally:
            llm.active_worker = None
        apply_delta(state, delta)
        state_delta.update(delta)

        last_message = state["messages"][-1]
        pending_tool_calls = getattr(last_message, "tool_calls", None) or []
        if not pending_tool_calls:
            break
        if round_index >= max_tool_rounds:
            raise CaptureError(f"{worker_id} exceeded max tool rounds: {max_tool_rounds}")
        tool_call_count += len(pending_tool_calls)
        tool_delta = tool_node.invoke(state)
        apply_delta(state, tool_delta)
        state_delta.setdefault("tool_messages", []).extend(tool_delta.get("messages", []))

    call = latest_call_for(llm, worker_id, start_index)
    capture = write_worker_artifacts(
        output_dir,
        worker_id,
        original_node,
        call,
        state.get(report_key, ""),
        state_delta,
        len(llm.calls) - start_index,
        tool_call_count,
    )
    reset_messages(state)
    return capture


def run_plain_worker(
    worker_id: str,
    original_node: str,
    node: Callable[[dict[str, Any]], dict[str, Any]],
    report_extractor: Callable[[dict[str, Any], dict[str, Any]], str],
    state: dict[str, Any],
    llm: RecordingLLM,
    output_dir: Path,
) -> WorkerCapture:
    start_index = len(llm.calls)
    llm.active_worker = worker_id
    try:
        delta = node(state)
    finally:
        llm.active_worker = None
    apply_delta(state, delta)
    call = latest_call_for(llm, worker_id, start_index)
    return write_worker_artifacts(
        output_dir,
        worker_id,
        original_node,
        call,
        report_extractor(delta, state),
        delta,
        len(llm.calls) - start_index,
        0,
    )


def compose_final_report(state: dict[str, Any]) -> str:
    sections = [
        ("Analyst Team Reports", ""),
        ("Market Analysis", state.get("market_report", "")),
        ("Social Sentiment", state.get("sentiment_report", "")),
        ("News Analysis", state.get("news_report", "")),
        ("Fundamentals Analysis", state.get("fundamentals_report", "")),
        ("Research Team Decision", state.get("investment_plan", "")),
        ("Trading Team Plan", state.get("trader_investment_plan", "")),
        ("Portfolio Management Decision", state.get("final_trade_decision", "")),
    ]
    chunks: list[str] = []
    for title, content in sections:
        if title == "Analyst Team Reports":
            chunks.append(f"## {title}")
            continue
        if content:
            chunks.append(f"### {title}\n{content}")
    return "\n\n".join(chunks) + "\n"


def make_llm(args: argparse.Namespace) -> RecordingLLM:
    sys.path.insert(0, str(TRADINGAGENTS_ROOT))
    load_env_file(CLAW_TRADE_ROOT / ".env.local")
    load_env_file(TRADINGAGENTS_ROOT / ".env")
    if not os.getenv("DEEPSEEK_API_KEY"):
        raise CaptureError("DEEPSEEK_API_KEY is not configured in environment or .env.local")

    from tradingagents.llm_clients import create_llm_client

    client = create_llm_client(
        provider="deepseek",
        model=args.model,
        base_url=args.base_url,
        timeout=args.timeout,
        max_retries=args.max_retries,
    )
    return RecordingLLM(client.get_llm(), "live_external_llm")


def build_state(ticker: str, trade_date: str) -> dict[str, Any]:
    from langchain_core.messages import HumanMessage

    return {
        "messages": [HumanMessage(content=ticker)],
        "company_of_interest": ticker,
        "trade_date": trade_date,
        "investment_debate_state": {
            "bull_history": "",
            "bear_history": "",
            "history": "",
            "current_response": "",
            "judge_decision": "",
            "count": 0,
        },
        "risk_debate_state": {
            "aggressive_history": "",
            "conservative_history": "",
            "neutral_history": "",
            "history": "",
            "latest_speaker": "",
            "current_aggressive_response": "",
            "current_conservative_response": "",
            "current_neutral_response": "",
            "judge_decision": "",
            "count": 0,
        },
        "market_report": "",
        "fundamentals_report": "",
        "sentiment_report": "",
        "news_report": "",
    }


def configure_original_project(args: argparse.Namespace) -> dict[str, Any]:
    sys.path.insert(0, str(TRADINGAGENTS_ROOT))
    from tradingagents.dataflows.config import set_config
    from tradingagents.default_config import DEFAULT_CONFIG

    config = DEFAULT_CONFIG.copy()
    config.update(
        {
            "results_dir": str(OUTPUT_ROOT / "_original_results"),
            "data_cache_dir": str(CLAW_TRADE_ROOT / ".runtime/tradingagents_us_cache"),
            "llm_provider": "deepseek",
            "deep_think_llm": args.model,
            "quick_think_llm": args.model,
            "backend_url": args.base_url,
            "output_language": "English",
            "max_debate_rounds": args.max_debate_rounds,
            "max_risk_discuss_rounds": args.max_risk_discuss_rounds,
            "max_recur_limit": 100,
            "data_vendors": {
                "core_stock_apis": "yfinance",
                "technical_indicators": "yfinance",
                "fundamental_data": "yfinance",
                "news_data": "yfinance",
            },
            "tool_vendors": {},
        }
    )
    Path(config["results_dir"]).mkdir(parents=True, exist_ok=True)
    Path(config["data_cache_dir"]).mkdir(parents=True, exist_ok=True)
    set_config(config)
    return config


def run_capture(args: argparse.Namespace) -> Path:
    if not TRADINGAGENTS_ROOT.exists():
        raise CaptureError(f"TradingAgents root not found: {TRADINGAGENTS_ROOT}")

    output_dir = Path(args.output_dir) if args.output_dir else OUTPUT_ROOT
    output_dir.mkdir(parents=True, exist_ok=True)

    sys.path.insert(0, str(TRADINGAGENTS_ROOT))
    config = configure_original_project(args)
    llm = make_llm(args)

    from langgraph.prebuilt import ToolNode
    from tradingagents.agents.analysts.fundamentals_analyst import create_fundamentals_analyst
    from tradingagents.agents.analysts.market_analyst import create_market_analyst
    from tradingagents.agents.analysts.news_analyst import create_news_analyst
    from tradingagents.agents.analysts.social_media_analyst import create_social_media_analyst
    from tradingagents.agents.managers.portfolio_manager import create_portfolio_manager
    from tradingagents.agents.managers.research_manager import create_research_manager
    from tradingagents.agents.researchers.bear_researcher import create_bear_researcher
    from tradingagents.agents.researchers.bull_researcher import create_bull_researcher
    from tradingagents.agents.risk_mgmt.aggressive_debator import create_aggressive_debator
    from tradingagents.agents.risk_mgmt.conservative_debator import create_conservative_debator
    from tradingagents.agents.risk_mgmt.neutral_debator import create_neutral_debator
    from tradingagents.agents.trader.trader import create_trader
    from tradingagents.agents.utils.agent_utils import (
        get_balance_sheet,
        get_cashflow,
        get_fundamentals,
        get_global_news,
        get_indicators,
        get_income_statement,
        get_news,
        get_stock_data,
    )
    from tradingagents.agents.utils.memory import FinancialSituationMemory

    state = build_state(args.ticker, args.trade_date)
    capture_started_at = utc_now()
    captures: list[WorkerCapture] = []

    tool_nodes = {
        "market_analyst": ToolNode([get_stock_data, get_indicators]),
        "social_analyst": ToolNode([get_news]),
        "news_analyst": ToolNode([get_news, get_global_news]),
        "fundamental_analyst": ToolNode(
            [get_fundamentals, get_balance_sheet, get_cashflow, get_income_statement]
        ),
    }

    frontline_sequence = [
        (
            "market_analyst",
            "Market Analyst",
            create_market_analyst(llm),
            tool_nodes["market_analyst"],
            "market_report",
        ),
        (
            "social_analyst",
            "Social Analyst",
            create_social_media_analyst(llm),
            tool_nodes["social_analyst"],
            "sentiment_report",
        ),
        (
            "news_analyst",
            "News Analyst",
            create_news_analyst(llm),
            tool_nodes["news_analyst"],
            "news_report",
        ),
        (
            "fundamental_analyst",
            "Fundamentals Analyst",
            create_fundamentals_analyst(llm),
            tool_nodes["fundamental_analyst"],
            "fundamentals_report",
        ),
    ]

    for worker_id, original_node, node, tool_node, report_key in frontline_sequence:
        print(f"[capture] invoking {worker_id} -> {original_node}", flush=True)
        capture = run_frontline_worker(
            worker_id,
            original_node,
            node,
            tool_node,
            report_key,
            state,
            llm,
            output_dir,
            args.max_tool_rounds,
        )
        captures.append(capture)
        print(f"[capture] {worker_id}: {capture.status}", flush=True)

    bull_memory = FinancialSituationMemory("bull_memory", config)
    bear_memory = FinancialSituationMemory("bear_memory", config)
    trader_memory = FinancialSituationMemory("trader_memory", config)
    invest_judge_memory = FinancialSituationMemory("invest_judge_memory", config)
    portfolio_manager_memory = FinancialSituationMemory("portfolio_manager_memory", config)

    plain_sequence: list[tuple[str, str, Callable[[dict[str, Any]], dict[str, Any]], Callable[[dict[str, Any], dict[str, Any]], str]]] = [
        (
            "bull_researcher",
            "Bull Researcher",
            create_bull_researcher(llm, bull_memory),
            lambda delta, s: delta["investment_debate_state"]["current_response"],
        ),
        (
            "bear_researcher",
            "Bear Researcher",
            create_bear_researcher(llm, bear_memory),
            lambda delta, s: delta["investment_debate_state"]["current_response"],
        ),
        (
            "research_manager",
            "Research Manager",
            create_research_manager(llm, invest_judge_memory),
            lambda delta, s: delta["investment_plan"],
        ),
        (
            "trader",
            "Trader",
            create_trader(llm, trader_memory),
            lambda delta, s: delta["trader_investment_plan"],
        ),
        (
            "risk_challenger",
            "Aggressive Analyst",
            create_aggressive_debator(llm),
            lambda delta, s: delta["risk_debate_state"]["current_aggressive_response"],
        ),
        (
            "risk_guardian",
            "Conservative Analyst",
            create_conservative_debator(llm),
            lambda delta, s: delta["risk_debate_state"]["current_conservative_response"],
        ),
        (
            "risk_moderator",
            "Neutral Analyst",
            create_neutral_debator(llm),
            lambda delta, s: delta["risk_debate_state"]["current_neutral_response"],
        ),
        (
            "portfolio_manager",
            "Portfolio Manager",
            create_portfolio_manager(llm, portfolio_manager_memory),
            lambda delta, s: delta["final_trade_decision"],
        ),
    ]

    for worker_id, original_node, node, extractor in plain_sequence:
        print(f"[capture] invoking {worker_id} -> {original_node}", flush=True)
        capture = run_plain_worker(worker_id, original_node, node, extractor, state, llm, output_dir)
        captures.append(capture)
        print(f"[capture] {worker_id}: {capture.status}", flush=True)

    final_report = compose_final_report(state)
    (output_dir / "final_report.md").write_text(final_report, encoding="utf-8")
    write_json(output_dir / "all_llm_calls_raw.json", llm.calls)
    write_json(output_dir / "final_state_raw.json", state)

    summary = {
        "project": "TradingAgents",
        "source_root": str(TRADINGAGENTS_ROOT),
        "market": "US",
        "ticker": args.ticker,
        "trade_date": args.trade_date,
        "provider": "deepseek",
        "model": args.model,
        "base_url": args.base_url,
        "llm_call_mode": "live_external_llm",
        "output_dir": str(output_dir),
        "capture_started_at_utc": capture_started_at,
        "capture_finished_at_utc": utc_now(),
        "selected_analysts_source_order": [
            "market",
            "social",
            "news",
            "fundamentals",
        ],
        "data_vendors": config["data_vendors"],
        "worker_mapping": WORKER_LABELS,
        "captures": [capture.__dict__ for capture in captures],
        "final_report_chars": len(final_report),
        "notes": [
            "This is an evidence-only run of original TradingAgents node functions.",
            "The original TradingAgents project is imported from /home/frank/src/TradingAgents.",
            "LLM calls are real DeepSeek provider calls; no capture-only, mock, stub, fake, or fallback LLM output is written.",
            "US data tools are configured to use original TradingAgents yfinance vendors, which do not require extra API keys.",
            "Ticker defaults to AAPL because the user specified US stocks but did not name a ticker.",
        ],
    }
    write_json(output_dir / "capture_summary.json", summary)

    lines = [
        f"# Original TradingAgents US capture - {args.ticker}",
        "",
        f"- project: `{TRADINGAGENTS_ROOT}`",
        f"- market: `US`",
        f"- ticker: `{args.ticker}`",
        f"- trade_date: `{args.trade_date}`",
        f"- provider/model: `deepseek` / `{args.model}`",
        f"- base_url: `{args.base_url}`",
        "- llm_call_mode: `live_external_llm`",
        "- mock/stub/fake/fallback/capture-only: `none`",
        "",
        "## Captures",
        "",
        "| worker | original node | status | LLM calls | tool calls | final prompt | LLM back | report | report chars |",
        "|---|---|---:|---:|---:|---|---|---|---:|",
    ]
    for capture in captures:
        lines.append(
            "| `{worker}` | {node} | {status} | {calls} | {tools} | `{prompt}` | `{llm}` | `{report}` | {chars} |".format(
                worker=capture.worker_id,
                node=capture.original_node,
                status=capture.status,
                calls=capture.llm_call_count,
                tools=capture.tool_call_count,
                prompt=capture.final_prompt_path or "",
                llm=capture.llm_back_path or "",
                report=capture.report_path or "",
                chars=capture.report_chars,
            )
        )
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- 本目录只用于美股 prompt 对齐取证，不是 claw-trade 产品运行结果。",
            "- 本轮按原版 TradingAgents 默认 analyst 顺序运行：market -> social -> news -> fundamentals。",
            "- 因用户未指定美股标的，本轮使用 `AAPL`；如需其它 ticker，可用同脚本重跑。",
            "- `all_llm_calls_raw.json` 保留每次 LLM 调用，包括 frontline 工具调用前的中间回合。",
        ]
    )
    (output_dir / "capture_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output_dir


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", default="AAPL")
    parser.add_argument("--trade-date", default="2026-05-12")
    parser.add_argument("--model", default="deepseek-chat")
    parser.add_argument("--base-url", default="https://api.deepseek.com")
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--max-retries", type=int, default=1)
    parser.add_argument("--max-debate-rounds", type=int, default=1)
    parser.add_argument("--max-risk-discuss-rounds", type=int, default=1)
    parser.add_argument("--max-tool-rounds", type=int, default=8)
    parser.add_argument("--output-dir", default=str(OUTPUT_ROOT))
    return parser.parse_args()


def main() -> int:
    try:
        output_dir = run_capture(parse_args())
    except Exception as exc:
        print(f"[capture] failed: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 1
    print(f"[capture] wrote {output_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
