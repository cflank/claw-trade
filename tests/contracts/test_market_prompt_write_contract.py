from __future__ import annotations

from pathlib import Path


def _read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def _assert_no_manual_claim_fields(text: str) -> None:
    forbidden_tokens = (
        "control.claims.v1",
        "claim block",
        "claims: []",
        "claim_id",
        "evidence_ids",
        "source_worker_id",
        "receipt_path",
    )
    for token in forbidden_tokens:
        assert token not in text


def test_market_us_prompt_requires_write_material_even_on_empty_or_failures() -> None:
    text = _read("agents/market_analyst/prompts/US.md")
    assert "openviking.write_material" in text
    assert "[OpenVikingWriteTarget]" in text
    assert "zero rows" in text
    assert "must be a limitation report" in text
    assert "analysis body only" in text
    assert "Runtime owns receipt validation and retry policy." in text
    _assert_no_manual_claim_fields(text)


def test_market_cn_prompt_requires_write_material_even_on_empty_or_failures() -> None:
    text = _read("agents/market_analyst/prompts/CN_A.md")
    assert "openviking.write_material" in text
    assert "[OpenVikingWriteTarget]" in text
    assert "zero rows" in text
    assert "限制说明报告" in text
    assert "分析正文" in text
    assert "运行层负责" in text
    _assert_no_manual_claim_fields(text)


def test_market_user_and_skill_require_write_target_usage() -> None:
    user_text = _read("agents/market_analyst/USER.md")
    skill_text = _read("agents/market_analyst/skills/claw-trade-stage/SKILL.md")
    assert "openviking.write_material" in user_text
    assert "[OpenVikingWriteTarget]" in user_text
    assert "分析正文" in user_text
    assert "运行层负责" in user_text
    _assert_no_manual_claim_fields(user_text)
    assert "openviking.write_material" in skill_text
    assert "[OpenVikingWriteTarget]" in skill_text
    assert "analysis body only" in skill_text
    assert "runtime owns receipt validation and retry policy." in skill_text.lower()
    _assert_no_manual_claim_fields(skill_text)
