from __future__ import annotations

from pathlib import Path

import pytest
from claw_trade.guards.common import (
    EARLY_STOP_CATEGORIES,
    combine_guard_results,
    guard_failed,
    guard_passed,
    should_early_stop,
)
from claw_trade.workflow.controller import (
    merge_stage_failures as merge_stage_failures_from_controller,
)
from claw_trade.workflow.models import FailureRecord, Stage
from claw_trade.workflow.runner import merge_stage_failures


def test_combine_guard_results_returns_ok_when_all_checks_pass() -> None:
    merged = combine_guard_results((guard_passed("provider_request"), guard_passed("visible_tools")))

    assert merged.ok
    assert merged.category == "ok"
    assert merged.reason is None
    assert merged.paths == ()
    assert merged.early_stop is False


def test_combine_guard_results_preserves_first_failure_and_merges_paths() -> None:
    p1 = Path("/tmp/a.json")
    p2 = Path("/tmp/b.json")
    merged = combine_guard_results(
        (
            guard_failed("provider_request", "缺少 provider request", (p1,), early_stop=False),
            guard_failed("tool_calls", "tool calls 非法", (p1, p2), early_stop=True),
        )
    )

    assert not merged.ok
    assert merged.category == "provider_request"
    assert merged.reason == "缺少 provider request"
    assert merged.paths == (p1, p2)
    assert merged.early_stop is True


def test_should_early_stop_true_for_flag_category_or_human_action() -> None:
    flagged = _failure(category="config_blocked", early_stop=True, human_action_required=None)
    category_hit = _failure(category="provider_request", early_stop=False, human_action_required=None)
    human_action = _failure(
        category="config_blocked",
        early_stop=False,
        human_action_required="需要人类确认 HK 提示词策略",
    )

    assert should_early_stop(flagged) is True
    assert should_early_stop(category_hit) is True
    assert should_early_stop(human_action) is True


def test_should_early_stop_false_for_collect_first_failure() -> None:
    regular = _failure(category="config_blocked", early_stop=False, human_action_required=None)

    assert should_early_stop(regular) is False


def test_merge_stage_failures_promotes_early_stop_for_early_stop_category() -> None:
    failure = _failure(category="provider_request", early_stop=False, human_action_required=None)

    merged = merge_stage_failures(run_id="run-1", stage=Stage.FRONTLINE, failures=(failure,))

    assert merged.category == "stage_batch"
    assert merged.early_stop is True
    assert "provider_request" in merged.reason


@pytest.mark.parametrize(
    "category",
    (
        "provider_evidence_untrusted",
        "openviking_integrity",
        "artifact_flow_overreach",
        "python_overreach",
        "openclaw_overreach",
        "openviking_overreach",
        "fake_success_path",
        "security_or_data_loss",
    ),
)
def test_should_early_stop_true_for_design_section_11_3_categories(category: str) -> None:
    assert category in EARLY_STOP_CATEGORIES
    assert should_early_stop(_failure(category=category, early_stop=False, human_action_required=None))


def test_controller_merge_stage_failures_uses_shared_early_stop_rule() -> None:
    failure = _failure(category="provider_evidence_untrusted", early_stop=False, human_action_required=None)

    merged = merge_stage_failures_from_controller(
        run_id="run-1",
        stage=Stage.FRONTLINE,
        failures=(failure,),
    )

    assert merged.category == "stage_batch"
    assert merged.early_stop is True
    assert "provider_evidence_untrusted" in merged.reason


def _failure(category: str, early_stop: bool, human_action_required: str | None) -> FailureRecord:
    return FailureRecord(
        run_id="run-1",
        call_id="call-1",
        worker_id="market_analyst",
        stage=Stage.FRONTLINE,
        category=category,
        reason=f"{category} failed",
        evidence_paths=(),
        early_stop=early_stop,
        human_action_required=human_action_required,
    )
