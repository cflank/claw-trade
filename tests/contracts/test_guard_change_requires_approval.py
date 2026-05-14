from __future__ import annotations

import ast
import hashlib
from pathlib import Path

from claw_trade.guards.common import EARLY_STOP_CATEGORIES, should_early_stop
from claw_trade.workflow.models import FailureRecord, Stage


APPROVED_RUNTIME_GUARD_FILES = frozenset(
    {
        "__init__.py",
        "common.py",
        "export_claims.py",
        "fundamental_claim_dictionaries_v1.json",
        "fundamental_claim_gate.py",
        "fundamental_claim_rules.py",
        "fundamental_gate_inputs.py",
        "fundamental_gate_outcome.py",
        "l1_l2.py",
        "openviking_access.py",
        "openviking_receipt.py",
        "provider_request.py",
        "tool_calls.py",
        "visible_tools.py",
        "workspace_evidence.py",
    }
)

APPROVED_RUNTIME_GUARD_FILE_SHA256 = {
    "__init__.py": "094768affec41e45129e9e12472334f6dae1c1e2c51529a071f7baaf81a34b58",
    "common.py": "38491c28306cf1845dc245f7257d2a24d195d6b82a46fa6d2c0595d6bba4f803",
    "export_claims.py": "944ddf521f8f2cd5f8e0755fe6acb57d4a8a8ee73c0a6fd45096859f8e280e6d",
    "fundamental_claim_dictionaries_v1.json": "e1a9eff6f9c4d6ba97d2cfa2530101b90544f443bf15c652b1a279451e593a7b",
    "fundamental_claim_gate.py": "592f3c365c55fa5d7fd2767907181d308c182a8274a6f539a0f59de91ca77aa1",
    "fundamental_claim_rules.py": "ba1d62c0c8808e13921202ab2f11d9052d8546d88f6c005b0b56fdcb4077ca28",
    "fundamental_gate_inputs.py": "a76c2bfffc1c476fa6f8103299df981aac2261ccb4c59fa054f49dbc86b6a0f0",
    "fundamental_gate_outcome.py": "5b72c9d4f3f7464b380fa8256204f355b8c7e1fb8ec0235177e452ddb7dccd83",
    "l1_l2.py": "8c012fcc35325cc369184ce4fab4559b60f8700831ec3f7fd5d7f8deb2a1a8fc",
    "openviking_access.py": "70905f71358275d140abf8dcc8d71f63db8c6a2724793d429f716f6309b8d8f7",
    "openviking_receipt.py": "50cb4f10d40fd9a387073f44bcaf09d915937adb469f4636d00e0bcc3e9ec634",
    "provider_request.py": "3b71ee622d495c4c8a422521e52fb084e18ccacdaaa47eb446067b190b099e11",
    "tool_calls.py": "d56918e98e1bd9e4c50c029e808ec0f1d780d16ac2893310f6f4a53ec9fba333",
    "visible_tools.py": "0b18078feda9cb416e05ad4d6bbf8dacbc3118c895cf8947c84a5f7dc61695a5",
    "workspace_evidence.py": "332420a1ebac0af35fac0101a0c8d5814e57888a39dd84440e80f75f409319ab",
}

APPROVED_EARLY_STOP_CATEGORIES = frozenset(
    {
        "provider_evidence_untrusted",
        "provider_request",
        "workspace_evidence",
        "visible_tools",
        "tool_calls",
        "openviking_integrity",
        "openviking_runtime_reads",
        "openviking_receipt",
        "l1_l2",
        "artifact_flow_overreach",
        "python_overreach",
        "openclaw_overreach",
        "openviking_overreach",
        "architecture_boundary",
        "claim",
        "claims",
        "export_truthfulness",
        "human_decision_required",
        "fake_success_path",
        "security_or_data_loss",
    }
)

APPROVED_GUARD_LIKE_REJECTION_CALL_COUNTS = {
    "src/claw_trade/artifacts/approval.py": 3,
    "src/claw_trade/artifacts/claims.py": 5,
    "src/claw_trade/artifacts/refs.py": 12,
    "src/claw_trade/config/stage_policy.py": 2,
    "src/claw_trade/config/tool_names.py": 1,
    "src/claw_trade/runtime/openclaw_client.py": 5,
    "src/claw_trade/workflow/controller.py": 1,
    "src/claw_trade/workflow/runner.py": 4,
}

TRUTHFULNESS_REDLINE_CATEGORIES = (
    "provider_request",
    "workspace_evidence",
    "visible_tools",
    "tool_calls",
    "claim",
    "claims",
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


def test_runtime_guard_file_set_is_frozen_without_human_approval() -> None:
    guard_dir = Path("src/claw_trade/guards")
    actual = frozenset(path.name for path in guard_dir.iterdir() if path.is_file())

    assert actual == APPROVED_RUNTIME_GUARD_FILES


def test_runtime_guard_file_contents_are_frozen_without_human_approval() -> None:
    guard_dir = Path("src/claw_trade/guards")
    actual = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(guard_dir.iterdir())
        if path.is_file()
    }

    assert actual == APPROVED_RUNTIME_GUARD_FILE_SHA256


def test_guard_like_rejection_call_surface_is_frozen_without_human_approval() -> None:
    actual: dict[str, int] = {}
    for path in sorted(Path("src/claw_trade").rglob("*.py")):
        if "src/claw_trade/guards" in path.as_posix():
            continue
        count = _count_guard_like_rejection_calls(path)
        if count:
            actual[path.as_posix()] = count

    assert actual == APPROVED_GUARD_LIKE_REJECTION_CALL_COUNTS


def test_early_stop_category_set_is_frozen_without_human_approval() -> None:
    assert EARLY_STOP_CATEGORIES == APPROVED_EARLY_STOP_CATEGORIES


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
    assert "Runtime Guard Freeze" in agents_rules
    assert "Runtime guard/gate creation is frozen by default" in agents_rules
    assert "Guard source:" in agents_rules
    assert "Do not add or tighten runtime guards" in agents_rules
    for term in APPROVAL_SENSITIVE_REPORT_EXPRESSION_TERMS:
        assert term in agents_rules
    assert "guard 要防造假，不负责把报告写成模板" in detailed_design
    assert "runtime guard 清单默认冻结" in detailed_design
    assert "不能先写测试把新规则固化" in detailed_design
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


def _count_guard_like_rejection_calls(path: Path) -> int:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    count = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == "guard_failed":
            count += 1
        elif (
            isinstance(func, ast.Attribute)
            and func.attr == "failed"
            and isinstance(func.value, ast.Name)
            and func.value.id in {"GuardResult", "ApprovalResult"}
        ):
            count += 1
    return count
