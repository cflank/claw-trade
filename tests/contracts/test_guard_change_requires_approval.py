from __future__ import annotations

from claw_trade.guards.common import EARLY_STOP_CATEGORIES, should_early_stop
from claw_trade.workflow.models import FailureRecord, Stage


TRUTHFULNESS_REDLINE_CATEGORIES = (
    "provider_request",
    "workspace_evidence",
    "visible_tools",
    "tool_calls",
    "claim",
    "claims",
    "pm_owner",
    "export_truthfulness",
    "fake_success_path",
    "architecture_boundary",
    "human_decision_required",
)

APPROVAL_SENSITIVE_REPORT_EXPRESSION_TERMS = (
    "ratings",
    "target-price framing",
    "trading suggestions",
    "risk wording",
    "sentiment judgment",
    "analyst tone",
    "report expression",
)


def test_truthfulness_redline_categories_still_force_early_stop() -> None:
    for category in TRUTHFULNESS_REDLINE_CATEGORIES:
        assert category in EARLY_STOP_CATEGORIES
        failure = _failure(category=category, human_action_required=None)
        assert should_early_stop(failure) is True


def test_report_expression_guard_changes_require_human_action() -> None:
    failure = _failure(
        category="config_blocked",
        human_action_required="需要人类批准：新增规则会影响评级、目标价或报告表达",
    )

    assert should_early_stop(failure) is True


def test_regular_config_blocked_can_still_collect_first() -> None:
    failure = _failure(category="config_blocked", human_action_required=None)

    assert should_early_stop(failure) is False


def test_guard_prompt_boundary_policy_is_documented() -> None:
    agents_rules = _read("AGENTS.md")
    detailed_design = _read("docs/详细设计.md")

    assert "Guard And Prompt Boundary" in agents_rules
    assert "Do not add or tighten runtime guards" in agents_rules
    for term in APPROVAL_SENSITIVE_REPORT_EXPRESSION_TERMS:
        assert term in agents_rules
    assert "guard 要防造假，不负责把报告写成模板" in detailed_design
    assert "不应该 hard fail" in detailed_design


def _failure(category: str, human_action_required: str | None) -> FailureRecord:
    return FailureRecord(
        run_id="run-1",
        call_id="call-1",
        worker_id="market_analyst",
        stage=Stage.FRONTLINE,
        category=category,
        reason=f"{category} failed",
        evidence_paths=(),
        early_stop=False,
        human_action_required=human_action_required,
    )


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()
