from __future__ import annotations

from pathlib import Path

import pytest

from claw_trade.workflow.workers import worker_by_id


REQUIRED_WORKERS: tuple[str, ...] = (
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
)

APPROVED_PROMPT_PROFILES = ("US", "CN_A")
UNAPPROVED_PROMPT_PROFILES = ("HK", "CRYPTO")

FORBIDDEN_AGENT_FACING_PROTOCOL_TOKENS = (
    "[RuntimeTarget]",
    "[ReportSubmission]",
    "[OpenVikingWriteTarget]",
    "control.claims.v1",
    "claim block",
    "claim_id",
    "evidence_ids",
    "source_worker_id",
    "receipt_path",
    "mat-pending-",
    "tool_choice",
    "openviking_write_material",
    "viking://",
)

AGENT_FACING_RELATIVE_PATHS = (
    "USER.md",
    "skills/claw-trade-stage/SKILL.md",
)


@pytest.mark.parametrize("worker_id", REQUIRED_WORKERS)
@pytest.mark.parametrize("profile", APPROVED_PROMPT_PROFILES)
def test_approved_worker_prompts_keep_baseline_alignment_metadata(
    worker_id: str,
    profile: str,
) -> None:
    prompt_path = Path("agents") / worker_id / "prompts" / f"{profile}.md"
    metadata = _front_matter(prompt_path)

    assert metadata["profile"] == profile
    assert metadata["profile_status"] == "approved"
    assert metadata["worker_id"] == worker_id
    assert metadata["stage"] == worker_by_id(worker_id).stage.value

    review = _simple_yaml(Path("agents") / worker_id / "prompt-review.yaml")
    assert review["us_alignment"] == "TradingAgents"
    assert review["cn_a_alignment"] == "TradingAgents-CN"


@pytest.mark.parametrize("worker_id", REQUIRED_WORKERS)
@pytest.mark.parametrize("profile", UNAPPROVED_PROMPT_PROFILES)
def test_unapproved_profiles_fail_closed_without_fallback(worker_id: str, profile: str) -> None:
    prompt_path = Path("agents") / worker_id / "prompts" / f"{profile}.md"
    metadata = _front_matter(prompt_path)
    text = prompt_path.read_text(encoding="utf-8")

    assert metadata["profile"] == profile
    assert metadata["profile_status"] == "unapproved"
    assert "not been approved" in text
    assert "Fail explicitly" in text
    assert "Do not fallback to US" in text
    assert "Do not fallback to CN_A" in text


@pytest.mark.parametrize("worker_id", REQUIRED_WORKERS)
def test_agent_facing_text_does_not_contain_machine_protocol(worker_id: str) -> None:
    worker_dir = Path("agents") / worker_id
    paths = [
        worker_dir / "prompts" / "US.md",
        worker_dir / "prompts" / "CN_A.md",
        *(worker_dir / relative for relative in AGENT_FACING_RELATIVE_PATHS),
    ]

    for path in paths:
        text = path.read_text(encoding="utf-8")
        for token in FORBIDDEN_AGENT_FACING_PROTOCOL_TOKENS:
            assert token not in text, f"{path} contains machine protocol token {token!r}"


def test_prompt_alignment_policy_is_documented_as_runtime_evidence_not_static_render() -> None:
    agents_rules = Path("AGENTS.md").read_text(encoding="utf-8")
    playbook = Path("docs/prompt_alignment_playbook.md").read_text(encoding="utf-8")

    assert "Provider final prompt evidence must come from provider payload capture" in agents_rules
    assert "Prompt alignment is not proven by static tests alone" in agents_rules
    assert "claw_provider_final_prompt" in playbook
    assert "不得用静态渲染" in playbook


def _front_matter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    assert lines and lines[0] == "---", f"{path} missing front matter"
    end = lines.index("---", 1)
    return _parse_key_value_lines(lines[1:end])


def _simple_yaml(path: Path) -> dict[str, str]:
    return _parse_key_value_lines(path.read_text(encoding="utf-8").splitlines())


def _parse_key_value_lines(lines: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, value = line.split(":", 1)
        result[key.strip()] = value.strip()
    return result
