from __future__ import annotations

import json
from decimal import Decimal

from claw_trade.ui_backend.task_costs import (
    TaskCostSnapshot,
    TaskTokenCostSummary,
    collect_token_cost_summary_from_path,
    estimate_task_cost,
    render_task_cost_line,
)


def test_estimate_task_cost_renders_deepseek_balance_delta_in_cny() -> None:
    estimate = estimate_task_cost(
        TaskCostSnapshot.captured(provider="deepseek", balance=Decimal("12.00")),
        TaskCostSnapshot.captured(provider="deepseek", balance=Decimal("11.875")),
    )

    assert render_task_cost_line(estimate) == (
        "费用统计：Token 总数：未知；任务前余额：¥12.00；任务后余额：¥11.88；本次消费：¥0.13"
    )


def test_estimate_task_cost_does_not_fake_cost_when_balance_increases() -> None:
    estimate = estimate_task_cost(
        TaskCostSnapshot.captured(provider="deepseek", balance=Decimal("12.00")),
        TaskCostSnapshot.captured(provider="deepseek", balance=Decimal("13.00")),
    )

    assert estimate.state == "unavailable"
    assert estimate.reason == "balance_increased"
    assert render_task_cost_line(estimate) == (
        "费用统计：Token 总数：未知；任务前余额：¥12.00；任务后余额：¥13.00；本次消费：未知"
    )


def test_estimate_task_cost_renders_sub_cent_delta_without_zeroing() -> None:
    estimate = estimate_task_cost(
        TaskCostSnapshot.captured(provider="deepseek", balance=Decimal("12.000")),
        TaskCostSnapshot.captured(provider="deepseek", balance=Decimal("11.996")),
    )

    assert render_task_cost_line(estimate).endswith("本次消费：<¥0.01")


def test_render_task_cost_includes_token_total_and_balance_delta() -> None:
    estimate = estimate_task_cost(
        TaskCostSnapshot.captured(provider="deepseek", balance=Decimal("20.00")),
        TaskCostSnapshot.captured(provider="deepseek", balance=Decimal("17.78")),
        token_summary=TaskTokenCostSummary.estimated(
            model="deepseek-chat",
            cache_hit_input_tokens=1_000_000,
            cache_miss_input_tokens=2_000_000,
            output_tokens=100_000,
            source_call_count=3,
        ),
    )

    text = render_task_cost_line(estimate)

    assert text == (
        "费用统计：Token 总数：3,100,000；任务前余额：¥20.00；任务后余额：¥17.78；本次消费：¥2.22"
    )


def test_render_token_cost_marks_partial_usage_when_some_calls_are_missing() -> None:
    estimate = estimate_task_cost(
        None,
        None,
        token_summary=TaskTokenCostSummary.estimated(
            model="deepseek-chat",
            cache_hit_input_tokens=10,
            cache_miss_input_tokens=20,
            output_tokens=3,
            source_call_count=2,
            missing_usage_call_count=1,
        ),
    )

    text = render_task_cost_line(estimate)

    assert text == "费用统计：Token 总数（已统计）：33；任务前余额：未知；任务后余额：未知；本次消费：未知"


def test_render_token_cost_reports_missing_usage_file() -> None:
    estimate = estimate_task_cost(
        None,
        None,
        token_summary=TaskTokenCostSummary.unavailable(reason="usage_file_missing"),
    )

    assert render_task_cost_line(estimate) == (
        "费用统计：Token 总数：未知；任务前余额：未知；任务后余额：未知；本次消费：未知"
    )


def test_collect_token_cost_summary_from_openclaw_results(tmp_path) -> None:
    call_dir = tmp_path / "calls" / "call-1"
    call_dir.mkdir(parents=True)
    (call_dir / "openclaw-result.json").write_text(
        (
            '{"provider":"deepseek","model":"deepseek-v4-pro",'
            '"usage":{"cacheRead":1000000,"input":2000000,"output":100000}}\n'
        ),
        encoding="utf-8",
    )

    summary = collect_token_cost_summary_from_path(tmp_path)

    assert summary is not None
    assert summary.state == "estimated"
    assert summary.cache_hit_input_tokens == 1_000_000
    assert summary.cache_miss_input_tokens == 2_000_000
    assert summary.output_tokens == 100_000
    assert summary.total_tokens == 3_100_000
    assert summary.total_cost == Decimal("6.625")


def test_collect_token_cost_summary_rejects_non_deepseek_provider_with_deepseek_named_model(tmp_path) -> None:
    call_dir = tmp_path / "calls" / "call-1"
    call_dir.mkdir(parents=True)
    (call_dir / "openclaw-result.json").write_text(
        (
            '{"provider":"qwen","model":"deepseek-r1",'
            '"usage":{"cacheRead":1000000,"input":1000000,"output":1000000}}\n'
        ),
        encoding="utf-8",
    )

    summary = collect_token_cost_summary_from_path(tmp_path)

    assert summary is not None
    assert summary.state == "unavailable"
    assert summary.reason == "unsupported_provider"


def test_collect_token_cost_summary_accepts_known_deepseek_model_when_provider_is_missing(tmp_path) -> None:
    call_dir = tmp_path / "calls" / "call-1"
    call_dir.mkdir(parents=True)
    (call_dir / "openclaw-result.json").write_text(
        '{"model":"deepseek-chat","usage":{"cacheRead":1000,"input":2000,"output":300}}\n',
        encoding="utf-8",
    )

    summary = collect_token_cost_summary_from_path(tmp_path)

    assert summary is not None
    assert summary.state == "estimated"
    assert summary.cache_hit_input_tokens == 1_000
    assert summary.cache_miss_input_tokens == 2_000
    assert summary.output_tokens == 300


def test_collect_token_cost_summary_keeps_token_total_for_unknown_deepseek_pricing(tmp_path) -> None:
    call_dir = tmp_path / "calls" / "call-1"
    call_dir.mkdir(parents=True)
    (call_dir / "openclaw-result.json").write_text(
        (
            '{"provider":"deepseek","model":"deepseek-new-model",'
            '"usage":{"cacheRead":1000000,"input":1000000,"output":1000000}}\n'
        ),
        encoding="utf-8",
    )

    summary = collect_token_cost_summary_from_path(tmp_path)

    assert summary is not None
    assert summary.state == "estimated"
    assert summary.total_tokens == 3_000_000
    assert render_task_cost_line(estimate_task_cost(None, None, token_summary=summary)) == (
        "费用统计：Token 总数：3,000,000；任务前余额：未知；任务后余额：未知；本次消费：未知"
    )


def test_collect_token_cost_summary_keeps_token_total_for_mixed_deepseek_pricing_tiers(tmp_path) -> None:
    for index, model in enumerate(["deepseek-chat", "deepseek-v4-pro"], start=1):
        call_dir = tmp_path / "calls" / f"call-{index}"
        call_dir.mkdir(parents=True)
        (call_dir / "openclaw-result.json").write_text(
            json.dumps(
                {
                    "provider": "deepseek",
                    "model": model,
                    "usage": {"cacheRead": 1000, "input": 1000, "output": 1000},
                }
            ),
            encoding="utf-8",
        )

    summary = collect_token_cost_summary_from_path(tmp_path)

    assert summary is not None
    assert summary.state == "estimated"
    assert summary.total_tokens == 6_000
    assert render_task_cost_line(estimate_task_cost(None, None, token_summary=summary)) == (
        "费用统计：Token 总数：6,000；任务前余额：未知；任务后余额：未知；本次消费：未知"
    )


def test_collect_token_cost_summary_from_openclaw_trajectory(tmp_path) -> None:
    call_dir = tmp_path / "calls" / "call-1"
    call_dir.mkdir(parents=True)
    provider_request_path = call_dir / "provider-request.json"
    provider_request_path.write_text(
        json.dumps(
            {
                "provider_model": {
                    "provider": "deepseek",
                    "id": "deepseek-chat",
                },
            }
        ),
        encoding="utf-8",
    )
    openclaw_run_id = "run-usage-1"
    (call_dir / "openclaw-result.json").write_text(
        json.dumps(
            {
                "openclaw_run_id": openclaw_run_id,
                "provider_request_path": str(provider_request_path),
            }
        ),
        encoding="utf-8",
    )
    state_root = tmp_path / "openclaw-state" / "agents"
    trajectory_dir = state_root / "report_polisher" / "sessions"
    trajectory_dir.mkdir(parents=True)
    (trajectory_dir / f"single-worker-{openclaw_run_id}.trajectory.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"type": "trace.metadata", "runId": openclaw_run_id}),
                json.dumps(
                    {
                        "type": "model.completed",
                        "runId": openclaw_run_id,
                        "data": {
                            "usage": {
                                "cacheRead": 1_000,
                                "input": 2_000,
                                "output": 300,
                                "total": 3_300,
                            }
                        },
                    }
                ),
                json.dumps(
                    {
                        "type": "model.completed",
                        "runId": openclaw_run_id,
                        "data": {
                            "usage": {
                                "prompt_cache_hit_tokens": 10,
                                "prompt_cache_miss_tokens": 20,
                                "completion_tokens": 3,
                            }
                        },
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )

    summary = collect_token_cost_summary_from_path(
        tmp_path,
        openclaw_state_agent_root=state_root,
    )

    assert summary is not None
    assert summary.state == "estimated"
    assert summary.model == "deepseek-chat"
    assert summary.cache_hit_input_tokens == 1_010
    assert summary.cache_miss_input_tokens == 2_020
    assert summary.output_tokens == 303
    assert summary.total_tokens == 3_333
    assert summary.missing_usage_call_count == 0


def test_collect_token_cost_summary_counts_missing_usage_inside_trajectory(tmp_path) -> None:
    call_dir = tmp_path / "calls" / "call-1"
    call_dir.mkdir(parents=True)
    provider_request_path = call_dir / "provider-request.json"
    provider_request_path.write_text(
        json.dumps({"provider_model": {"provider": "deepseek", "id": "deepseek-chat"}}),
        encoding="utf-8",
    )
    openclaw_run_id = "run-partial-usage"
    (call_dir / "openclaw-result.json").write_text(
        json.dumps(
            {
                "openclaw_run_id": openclaw_run_id,
                "provider_request_path": str(provider_request_path),
            }
        ),
        encoding="utf-8",
    )
    state_root = tmp_path / "openclaw-state" / "agents"
    trajectory_dir = state_root / "report_polisher" / "sessions"
    trajectory_dir.mkdir(parents=True)
    (trajectory_dir / f"single-worker-{openclaw_run_id}.trajectory.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "type": "model.completed",
                        "runId": openclaw_run_id,
                        "data": {"usage": {"cacheRead": 10, "input": 20, "output": 3}},
                    }
                ),
                json.dumps(
                    {
                        "type": "model.completed",
                        "runId": openclaw_run_id,
                        "data": {"usage": None},
                    }
                ),
            ]
        ),
        encoding="utf-8",
    )

    summary = collect_token_cost_summary_from_path(
        tmp_path,
        openclaw_state_agent_root=state_root,
    )

    assert summary is not None
    assert summary.state == "estimated"
    assert summary.source_call_count == 1
    assert summary.missing_usage_call_count == 1
    assert render_task_cost_line(estimate_task_cost(None, None, token_summary=summary)) == (
        "费用统计：Token 总数（已统计）：33；任务前余额：未知；任务后余额：未知；本次消费：未知"
    )


def test_collect_token_cost_summary_reports_when_all_trajectory_calls_miss_usage(tmp_path) -> None:
    call_dir = tmp_path / "calls" / "call-1"
    call_dir.mkdir(parents=True)
    provider_request_path = call_dir / "provider-request.json"
    provider_request_path.write_text(
        json.dumps({"provider_model": {"provider": "deepseek", "id": "deepseek-chat"}}),
        encoding="utf-8",
    )
    openclaw_run_id = "run-all-missing-usage"
    (call_dir / "openclaw-result.json").write_text(
        json.dumps(
            {
                "openclaw_run_id": openclaw_run_id,
                "provider_request_path": str(provider_request_path),
            }
        ),
        encoding="utf-8",
    )
    state_root = tmp_path / "openclaw-state" / "agents"
    trajectory_dir = state_root / "report_polisher" / "sessions"
    trajectory_dir.mkdir(parents=True)
    (trajectory_dir / f"single-worker-{openclaw_run_id}.trajectory.jsonl").write_text(
        json.dumps(
            {
                "type": "model.completed",
                "runId": openclaw_run_id,
                "data": {"usage": None},
            }
        ),
        encoding="utf-8",
    )

    summary = collect_token_cost_summary_from_path(
        tmp_path,
        openclaw_state_agent_root=state_root,
    )

    assert summary is not None
    assert summary.state == "unavailable"
    assert summary.reason == "usage_missing"
    assert summary.source_call_count == 0
    assert summary.missing_usage_call_count == 1


def test_collect_token_cost_summary_finds_openclaw_state_from_run_tree(tmp_path) -> None:
    project_root = tmp_path / "project"
    run_dir = project_root / "runs" / "run-1"
    call_dir = run_dir / "calls" / "call-1"
    call_dir.mkdir(parents=True)
    openclaw_run_id = "run-tree-1"
    (call_dir / "provider-request.json").write_text(
        json.dumps({"provider_model": {"provider": "deepseek", "id": "deepseek-chat"}}),
        encoding="utf-8",
    )
    (call_dir / "openclaw-result.json").write_text(
        json.dumps(
            {
                "openclaw_run_id": openclaw_run_id,
                "provider_request_path": str(call_dir / "provider-request.json"),
            }
        ),
        encoding="utf-8",
    )
    trajectory_dir = (
        project_root
        / ".runtime"
        / "dev-services"
        / "openclaw-state"
        / "agents"
        / "report_polisher"
        / "sessions"
    )
    trajectory_dir.mkdir(parents=True)
    (trajectory_dir / f"single-worker-{openclaw_run_id}.trajectory.jsonl").write_text(
        json.dumps(
            {
                "type": "model.completed",
                "runId": openclaw_run_id,
                "data": {"usage": {"cacheRead": 7, "input": 11, "output": 13}},
            }
        ),
        encoding="utf-8",
    )

    summary = collect_token_cost_summary_from_path(run_dir)

    assert summary is not None
    assert summary.state == "estimated"
    assert summary.cache_hit_input_tokens == 7
    assert summary.cache_miss_input_tokens == 11
    assert summary.output_tokens == 13


def test_collect_token_cost_summary_reports_missing_usage(tmp_path) -> None:
    call_dir = tmp_path / "calls" / "call-1"
    call_dir.mkdir(parents=True)
    (call_dir / "openclaw-result.json").write_text(
        '{"provider":"deepseek","model":"deepseek-chat"}\n',
        encoding="utf-8",
    )

    summary = collect_token_cost_summary_from_path(tmp_path)

    assert summary is not None
    assert summary.state == "unavailable"
    assert summary.reason == "usage_missing"
    assert render_task_cost_line(estimate_task_cost(None, None, token_summary=summary)) == (
        "费用统计：Token 总数：未知；任务前余额：未知；任务后余额：未知；本次消费：未知"
    )
