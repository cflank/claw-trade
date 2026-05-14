#!/usr/bin/env python3
"""Capture original TradingAgents-CN prompts, LLM replies, and reports."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from langchain_core.messages import HumanMessage, ToolMessage

from capture_tradingagents_cn_later8 import (
    CLAW_TRADE_ROOT,
    TRADINGAGENTS_CN_ROOT,
    RecordingLLM,
    StaticMemory,
    install_company_name_override,
    jsonable,
    load_env_file,
    load_tradingagents_settings,
    make_real_llm,
    response_content,
    write_json,
)


OUTPUT_ROOT = CLAW_TRADE_ROOT / "docs" / "evidence"

WORKER_LABELS = {
    "market_analyst": "TradingAgents-CN Market Analyst",
    "social_analyst": "TradingAgents-CN Social Media Analyst",
    "news_analyst": "TradingAgents-CN News Analyst",
    "fundamental_analyst": "TradingAgents-CN Fundamentals Analyst",
    "bull_researcher": "TradingAgents-CN Bull Researcher",
    "bear_researcher": "TradingAgents-CN Bear Researcher",
    "research_manager": "TradingAgents-CN Research Manager",
    "trader": "TradingAgents-CN Trader",
    "risk_challenger": "TradingAgents-CN Risky Analyst",
    "risk_guardian": "TradingAgents-CN Safe Analyst",
    "risk_moderator": "TradingAgents-CN Neutral Analyst",
    "portfolio_manager": "TradingAgents-CN Risk Judge",
}


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


def local_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def safe_name(path: Path | None) -> str | None:
    return path.name if path else None


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
        return "\n\n".join(chunks)
    return json.dumps(jsonable(prompt_input), ensure_ascii=False, indent=2)


class FullRecordingLLM(RecordingLLM):
    def record_invoke(
        self,
        target: Any,
        prompt_input: Any,
        *args: Any,
        worker_id: str | None,
        bound_tools: list[str] | None = None,
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
            "bound_tools": bound_tools or [],
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

    def invoke(self, prompt_input: Any, *args: Any, **kwargs: Any) -> Any:
        return self.record_invoke(
            self.real_llm,
            prompt_input,
            worker_id=self.active_worker,
            bound_tools=[],
            *args,
            **kwargs,
        )

    def bind_tools(self, tools: list[Any], *args: Any, **kwargs: Any) -> Any:
        from langchain_core.runnables import RunnableLambda

        real_bound = self.real_llm.bind_tools(tools, *args, **kwargs)
        tool_names = [getattr(tool, "name", getattr(tool, "__name__", repr(tool))) for tool in tools]

        def _invoke(prompt_input: Any, config: Any = None, **invoke_kwargs: Any) -> Any:
            if config is not None:
                invoke_kwargs.setdefault("config", config)
            return self.record_invoke(
                real_bound,
                prompt_input,
                worker_id=self.active_worker,
                bound_tools=tool_names,
                **invoke_kwargs,
            )

        return RunnableLambda(_invoke)


def latest_call_for(llm: FullRecordingLLM, worker_id: str, start_index: int) -> dict[str, Any] | None:
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
    state["messages"] = [HumanMessage(content="Continue")]


def tool_name(tool: Any) -> str:
    return str(getattr(tool, "name", getattr(tool, "__name__", repr(tool))))


def execute_tool_calls(tool_calls: list[Any], tools: list[Any]) -> dict[str, Any]:
    tools_by_name = {tool_name(tool): tool for tool in tools}
    available = ", ".join(tools_by_name)
    messages: list[ToolMessage] = []
    for index, tool_call in enumerate(tool_calls, 1):
        name = str(tool_call.get("name") or "")
        args = tool_call.get("args") or {}
        tool_call_id = str(tool_call.get("id") or f"capture_tool_call_{index}")
        tool = tools_by_name.get(name)
        if tool is None:
            content = f"Error: {name} is not a valid tool, try one of [{available}]."
        else:
            try:
                content = str(tool.invoke(args))
            except Exception as exc:
                content = (
                    f"Error invoking tool '{name}' with kwargs {args} with error:\n"
                    f" {exc}\n Please fix the error and try again."
                )
        messages.append(ToolMessage(content=content, tool_call_id=tool_call_id))
    return {"messages": messages}


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

    report_md.write_text(report, encoding="utf-8")
    write_json(state_raw, jsonable(state_delta))
    if call is None:
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


def run_worker(
    worker_id: str,
    original_node: str,
    node: Callable[[dict[str, Any]], dict[str, Any]],
    report_key: str,
    state: dict[str, Any],
    llm: FullRecordingLLM,
    output_dir: Path,
    tools: list[Any] | None = None,
    max_rounds: int = 4,
) -> WorkerCapture:
    start_index = len(llm.calls)
    tool_call_count = 0
    state_delta: dict[str, Any] = {}
    last_error: Exception | None = None

    for round_index in range(max_rounds + 1):
        llm.active_worker = worker_id
        try:
            delta = node(state)
        except Exception as exc:
            last_error = exc
            break
        finally:
            llm.active_worker = None
        apply_delta(state, delta)
        state_delta.update(delta)

        report = str(state.get(report_key) or "")
        last_message = state.get("messages", [])[-1] if state.get("messages") else None
        pending_tool_calls = getattr(last_message, "tool_calls", None) or []
        if report.strip() and not pending_tool_calls:
            break
        if pending_tool_calls and tools is not None and round_index < max_rounds:
            tool_call_count += len(pending_tool_calls)
            tool_delta = execute_tool_calls(pending_tool_calls, tools)
            apply_delta(state, tool_delta)
            state_delta.setdefault("tool_messages", []).extend(tool_delta.get("messages", []))
            continue
        break

    call = latest_call_for(llm, worker_id, start_index)
    report = str(state.get(report_key) or "")
    capture = write_worker_artifacts(
        output_dir,
        worker_id,
        original_node,
        call,
        report,
        state_delta,
        len(llm.calls) - start_index,
        tool_call_count,
    )
    if last_error is not None:
        capture.status = "error"
        capture.error = f"{type(last_error).__name__}: {last_error}"
        error_raw = output_dir / f"{worker_id}_error_raw.json"
        write_json(
            error_raw,
            {
                "error_type": type(last_error).__name__,
                "error": str(last_error),
                "traceback": "".join(traceback.format_exception(last_error)),
            },
        )
    reset_messages(state)
    return capture


def build_state(ticker: str, trade_date: str) -> dict[str, Any]:
    return {
        "messages": [HumanMessage(content=ticker)],
        "company_of_interest": ticker,
        "trade_date": trade_date,
        "session_id": f"capture-{ticker}-{trade_date}",
        "market_report": "",
        "sentiment_report": "",
        "news_report": "",
        "fundamentals_report": "",
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


def configure_project(args: argparse.Namespace) -> dict[str, Any]:
    sys.path.insert(0, str(TRADINGAGENTS_CN_ROOT))
    load_env_file(CLAW_TRADE_ROOT / ".env.local")
    load_env_file(TRADINGAGENTS_CN_ROOT / ".env")

    from tradingagents.dataflows.interface import set_config
    from tradingagents.default_config import DEFAULT_CONFIG

    config = DEFAULT_CONFIG.copy()
    config.update(
        {
            "results_dir": str(OUTPUT_ROOT / "_tradingagents_cn_results"),
            "data_cache_dir": str(CLAW_TRADE_ROOT / ".runtime/tradingagents_cn_cache"),
            "llm_provider": args.provider,
            "deep_think_llm": args.model,
            "quick_think_llm": args.model,
            "backend_url": args.base_url,
            "online_tools": True,
            "online_news": True,
            "memory_enabled": False,
            "max_debate_rounds": args.max_debate_rounds,
            "max_risk_discuss_rounds": args.max_risk_discuss_rounds,
            "quick_model_config": {
                "max_tokens": args.max_tokens,
                "temperature": args.temperature,
                "timeout": args.timeout,
            },
            "deep_model_config": {
                "max_tokens": args.max_tokens,
                "temperature": args.temperature,
                "timeout": args.timeout,
            },
        }
    )
    Path(config["results_dir"]).mkdir(parents=True, exist_ok=True)
    Path(config["data_cache_dir"]).mkdir(parents=True, exist_ok=True)
    set_config(config)
    return config


def make_recording_llm(args: argparse.Namespace) -> FullRecordingLLM:
    real_llm = make_real_llm(args)
    mode = "capture_only_no_external_llm" if args.capture_only else "live_external_llm"
    return FullRecordingLLM(real_llm, mode)


def compose_final_report(state: dict[str, Any]) -> str:
    sections = [
        ("分析师团队报告", ""),
        ("市场技术分析", state.get("market_report", "")),
        ("社交媒体情绪分析", state.get("sentiment_report", "")),
        ("新闻事件分析", state.get("news_report", "")),
        ("基本面分析", state.get("fundamentals_report", "")),
        ("研究团队投资决策", state.get("investment_plan", "")),
        ("交易员执行计划", state.get("trader_investment_plan", "")),
        ("组合风险最终裁决", state.get("final_trade_decision", "")),
    ]
    chunks: list[str] = []
    for title, content in sections:
        if title == "分析师团队报告":
            chunks.append(f"## {title}")
            continue
        if content:
            chunks.append(f"### {title}\n{content}")
    return "\n\n".join(chunks) + "\n"


def run_capture(args: argparse.Namespace) -> Path:
    if not TRADINGAGENTS_CN_ROOT.exists():
        raise RuntimeError(f"TradingAgents-CN root not found: {TRADINGAGENTS_CN_ROOT}")

    sys.path.insert(0, str(TRADINGAGENTS_CN_ROOT))
    config = configure_project(args)
    install_company_name_override(args.ticker, args.company_name)

    from tradingagents.agents.analysts.fundamentals_analyst import create_fundamentals_analyst
    from tradingagents.agents.analysts.market_analyst import create_market_analyst
    from tradingagents.agents.analysts.news_analyst import create_news_analyst
    from tradingagents.agents.analysts.social_media_analyst import create_social_media_analyst
    from tradingagents.agents.managers.research_manager import create_research_manager
    from tradingagents.agents.managers.risk_manager import create_risk_manager
    from tradingagents.agents.researchers.bear_researcher import create_bear_researcher
    from tradingagents.agents.researchers.bull_researcher import create_bull_researcher
    from tradingagents.agents.risk_mgmt.aggresive_debator import create_risky_debator
    from tradingagents.agents.risk_mgmt.conservative_debator import create_safe_debator
    from tradingagents.agents.risk_mgmt.neutral_debator import create_neutral_debator
    from tradingagents.agents.trader.trader import create_trader
    from tradingagents.agents.utils.agent_utils import Toolkit

    toolkit = Toolkit(config=config)
    frontline_tools = {
        "market_analyst": [toolkit.get_stock_market_data_unified],
        "social_analyst": [toolkit.get_stock_sentiment_unified],
        "news_analyst": [toolkit.get_stock_news_unified],
        "fundamental_analyst": [toolkit.get_stock_fundamentals_unified],
    }

    output_dir = Path(args.output_dir) if args.output_dir else (
        OUTPUT_ROOT / f"tradingagents_cn_full_{args.ticker}_{local_stamp()}"
    )
    output_dir.mkdir(parents=True, exist_ok=False)

    llm = make_recording_llm(args)
    state = build_state(args.ticker, args.trade_date)
    capture_started_at = utc_now()
    captures: list[WorkerCapture] = []

    frontline = [
        (
            "market_analyst",
            "Market Analyst",
            create_market_analyst(llm, toolkit),
            "market_report",
            frontline_tools["market_analyst"],
        ),
        (
            "social_analyst",
            "Social Media Analyst",
            create_social_media_analyst(llm, toolkit),
            "sentiment_report",
            frontline_tools["social_analyst"],
        ),
        (
            "news_analyst",
            "News Analyst",
            create_news_analyst(llm, toolkit),
            "news_report",
            frontline_tools["news_analyst"],
        ),
        (
            "fundamental_analyst",
            "Fundamentals Analyst",
            create_fundamentals_analyst(llm, toolkit),
            "fundamentals_report",
            frontline_tools["fundamental_analyst"],
        ),
    ]
    for worker_id, label, node, report_key, tools in frontline:
        print(f"[capture] invoking {worker_id} -> {label}", flush=True)
        capture = run_worker(worker_id, label, node, report_key, state, llm, output_dir, tools=tools)
        captures.append(capture)
        print(f"[capture] {worker_id}: {capture.status} report_chars={capture.report_chars}", flush=True)
        if capture.status == "error" and args.stop_on_error:
            break

    later = [
        (
            "bull_researcher",
            "Bull Researcher",
            create_bull_researcher(llm, StaticMemory("看涨研究员")),
            lambda s: s["investment_debate_state"]["current_response"],
        ),
        (
            "bear_researcher",
            "Bear Researcher",
            create_bear_researcher(llm, StaticMemory("看跌研究员")),
            lambda s: s["investment_debate_state"]["current_response"],
        ),
        (
            "research_manager",
            "Research Manager",
            create_research_manager(llm, StaticMemory("研究经理")),
            lambda s: s["investment_plan"],
        ),
        (
            "trader",
            "Trader",
            create_trader(llm, StaticMemory("交易员")),
            lambda s: s["trader_investment_plan"],
        ),
        (
            "risk_challenger",
            "Risky Analyst",
            create_risky_debator(llm),
            lambda s: s["risk_debate_state"]["current_risky_response"],
        ),
        (
            "risk_guardian",
            "Safe Analyst",
            create_safe_debator(llm),
            lambda s: s["risk_debate_state"]["current_safe_response"],
        ),
        (
            "risk_moderator",
            "Neutral Analyst",
            create_neutral_debator(llm),
            lambda s: s["risk_debate_state"]["current_neutral_response"],
        ),
        (
            "portfolio_manager",
            "Risk Judge",
            create_risk_manager(llm, StaticMemory("组合经理")),
            lambda s: s["final_trade_decision"],
        ),
    ]
    for worker_id, label, node, extractor in later:
        print(f"[capture] invoking {worker_id} -> {label}", flush=True)
        before = len(llm.calls)
        llm.active_worker = worker_id
        try:
            delta = node(state)
            apply_delta(state, delta)
            report = extractor(state)
            call = latest_call_for(llm, worker_id, before)
            capture = write_worker_artifacts(
                output_dir,
                worker_id,
                label,
                call,
                str(report or ""),
                delta,
                len(llm.calls) - before,
                0,
            )
        except Exception as exc:
            call = latest_call_for(llm, worker_id, before)
            capture = write_worker_artifacts(
                output_dir,
                worker_id,
                label,
                call,
                "",
                {
                    "error": f"{type(exc).__name__}: {exc}",
                    "traceback": "".join(traceback.format_exception(exc)),
                },
                len(llm.calls) - before,
                0,
            )
            capture.status = "error"
            capture.error = f"{type(exc).__name__}: {exc}"
        finally:
            llm.active_worker = None
        captures.append(capture)
        reset_messages(state)
        print(f"[capture] {worker_id}: {capture.status} report_chars={capture.report_chars}", flush=True)
        if capture.status == "error" and args.stop_on_error:
            break

    final_report = compose_final_report(state)
    (output_dir / "final_report.md").write_text(final_report, encoding="utf-8")
    write_json(output_dir / "all_llm_calls_raw.json", llm.calls)
    write_json(output_dir / "final_state_raw.json", jsonable(state))

    summary = {
        "project": "TradingAgents-CN",
        "source_root": str(TRADINGAGENTS_CN_ROOT),
        "market": "CN_A",
        "ticker": args.ticker,
        "company_name": args.company_name,
        "trade_date": args.trade_date,
        "provider": args.provider,
        "model": args.model,
        "base_url": args.base_url,
        "llm_call_mode": llm.mode,
        "output_dir": str(output_dir),
        "capture_started_at_utc": capture_started_at,
        "capture_finished_at_utc": utc_now(),
        "worker_mapping": WORKER_LABELS,
        "captures": [capture.__dict__ for capture in captures],
        "final_report_chars": len(final_report),
        "notes": [
            "This is an evidence-only run of original TradingAgents-CN node functions.",
            "The original TradingAgents-CN project is imported from /home/frank/src/TradingAgents-CN.",
            "LLM calls are real provider calls unless llm_call_mode is capture_only_no_external_llm.",
            "The composed final_report.md is a capture artifact assembled from original node state fields, not a claw-trade exporter result.",
        ],
    }
    write_json(output_dir / "capture_summary.json", summary)

    lines = [
        f"# TradingAgents-CN full CN_A capture - {args.ticker}",
        "",
        f"- project: `{TRADINGAGENTS_CN_ROOT}`",
        f"- market: `CN_A`",
        f"- ticker/company: `{args.ticker}` / `{args.company_name}`",
        f"- trade_date: `{args.trade_date}`",
        f"- provider/model: `{args.provider}` / `{args.model}`",
        f"- llm_call_mode: `{llm.mode}`",
        "- mock/stub/fake/fallback/capture-only: `none` when llm_call_mode is live_external_llm",
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
            "- 本目录只用于 CN prompt 对齐取证，不是 claw-trade 产品运行结果。",
            "- `portfolio_manager` 对应 TradingAgents-CN 的 `Risk Judge`。",
            "- `all_llm_calls_raw.json` 保留每次 LLM 调用，包括前台工具调用和补救/强制分析回合。",
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
    parser.add_argument("--company-name", default="贵州茅台")
    parser.add_argument("--trade-date", default="2026-05-14")
    parser.add_argument("--provider", default=default_provider)
    parser.add_argument("--model", default=default_model)
    parser.add_argument("--base-url", default=default_base_url)
    parser.add_argument("--max-tokens", type=int, default=int(quick_config.get("max_tokens") or 4096))
    parser.add_argument("--temperature", type=float, default=float(quick_config.get("temperature") or 0.1))
    parser.add_argument("--timeout", type=int, default=int(quick_config.get("timeout") or 240))
    parser.add_argument("--max-retries", type=int, default=1)
    parser.add_argument("--max-debate-rounds", type=int, default=1)
    parser.add_argument("--max-risk-discuss-rounds", type=int, default=1)
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--stop-on-error", action="store_true")
    parser.add_argument("--capture-only", action="store_true")
    return parser.parse_args()


def main() -> int:
    try:
        output_dir = run_capture(parse_args())
    except Exception as exc:
        print(f"[capture] failed: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        print("".join(traceback.format_exception(exc)), file=sys.stderr, flush=True)
        return 1
    print(f"[capture] wrote {output_dir}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
