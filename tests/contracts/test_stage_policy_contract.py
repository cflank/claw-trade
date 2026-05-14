from pathlib import Path

from claw_trade.config.stage_policy import (
    StagePolicy,
    load_stage_policy,
    validate_stage_policy_matches_worker,
)
from claw_trade.workflow.models import Stage, WorkerSpec
from claw_trade.workflow.workers import worker_by_id


def test_load_stage_policy_reads_profile_tools_and_access(tmp_path: Path) -> None:
    agents_root = _write_stage_policy(tmp_path, stage="frontline")
    result = load_stage_policy(agents_root, "market_analyst", "US")

    assert result.ok is True
    assert result.policy is not None
    assert result.policy.worker_id == "market_analyst"
    assert result.policy.stage == Stage.FRONTLINE
    assert result.policy.tool_intents == ("us_market_data",)
    assert result.policy.openviking_access == "write"


def test_load_stage_policy_fails_when_profile_not_approved(tmp_path: Path) -> None:
    agents_root = _write_stage_policy(tmp_path, approved="false")
    result = load_stage_policy(agents_root, "market_analyst", "US")
    assert result.ok is False
    assert "not approved" in (result.reason or "")


def test_load_stage_policy_fails_when_tools_empty(tmp_path: Path) -> None:
    agents_root = _write_stage_policy(
        tmp_path,
        tools_block=(
            "    tools: []\n"
            "    openviking_access: write\n"
        ),
    )
    result = load_stage_policy(agents_root, "market_analyst", "US")
    assert result.ok is False
    assert "cannot be empty" in (result.reason or "")


def test_load_stage_policy_fails_when_openviking_access_missing(tmp_path: Path) -> None:
    agents_root = _write_stage_policy(
        tmp_path,
        tools_block=(
            "    tools:\n"
            "      - us_market_data\n"
        ),
    )
    result = load_stage_policy(agents_root, "market_analyst", "US")
    assert result.ok is False
    assert "openviking_access" in (result.reason or "")


def test_validate_stage_policy_matches_worker_passes_for_real_worker() -> None:
    result = load_stage_policy(Path("agents"), "market_analyst", "US")
    assert result.ok is True and result.policy is not None
    guard = validate_stage_policy_matches_worker(result.policy, worker_by_id("market_analyst"))
    assert guard.ok is True


def test_validate_stage_policy_matches_worker_blocks_mismatch() -> None:
    policy = StagePolicy(
        worker_id="market_analyst",
        stage=Stage.FRONTLINE,
        profile="US",
        tool_intents=("us_market_data",),
        openviking_access="write",
        source_path=Path("agents/market_analyst/STAGES.yaml"),
    )
    wrong_worker = WorkerSpec(id="market_analyst", stage=Stage.RISK_DEBATE)
    guard = validate_stage_policy_matches_worker(policy, wrong_worker)
    assert guard.ok is False
    assert guard.category == "config_blocked"


def _write_stage_policy(
    tmp_path: Path,
    *,
    stage: str = "frontline",
    approved: str = "true",
    tools_block: str | None = None,
) -> Path:
    agents_root = tmp_path / "agents"
    worker_dir = agents_root / "market_analyst"
    worker_dir.mkdir(parents=True, exist_ok=True)
    (worker_dir / "STAGES.yaml").write_text(
        _stage_policy_yaml(stage=stage, approved=approved, tools_block=tools_block),
        encoding="utf-8",
    )
    return agents_root


def _stage_policy_yaml(stage: str, approved: str, tools_block: str | None) -> str:
    return (
        f"stage: {stage}\n"
        "profiles:\n"
        "  US:\n"
        f"    approved: {approved}\n"
        + (
            tools_block
            if tools_block is not None
            else (
                "    tools:\n"
                "      - us_market_data\n"
                "    openviking_access: write\n"
            )
        )
    )
