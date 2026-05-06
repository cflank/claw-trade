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
        "mat-pending-",
        "no-L2 policy",
        "L2 evidence id",
    )
    for token in forbidden_tokens:
        assert token not in text


def test_fundamental_prompt_requires_tool_then_write_contract() -> None:
    us = _read("agents/fundamental_analyst/prompts/US.md")
    cn = _read("agents/fundamental_analyst/prompts/CN_A.md")
    assert "fundamentals_data" in us
    assert "openviking.write_material" in us
    assert "[OpenVikingWriteTarget]" in us
    assert "[RuntimeTarget]" not in us
    assert "{ticker}" not in us
    assert "{company_name}" not in us
    assert "analysis body only" in us
    assert "Runtime owns receipt validation and retry policy." in us
    _assert_no_manual_claim_fields(us)
    assert "fundamentals_data" in cn
    assert "openviking.write_material" in cn
    assert "[OpenVikingWriteTarget]" in cn
    assert "[RuntimeTarget]" not in cn
    assert "{ticker}" not in cn
    assert "{company_name}" not in cn
    assert "分析正文" in cn
    assert "运行层负责" in cn
    _assert_no_manual_claim_fields(cn)


def test_news_prompt_requires_company_and_macro_tools_then_write_contract() -> None:
    us = _read("agents/news_analyst/prompts/US.md")
    cn = _read("agents/news_analyst/prompts/CN_A.md")
    for text in (us, cn):
        assert "company_news" in text
        assert "macro_news" in text
        assert "openviking.write_material" in text
        assert "[OpenVikingWriteTarget]" in text
        assert "[RuntimeTarget]" not in text
        assert "{ticker}" not in text
        assert "{company_name}" not in text
        _assert_no_manual_claim_fields(text)
    assert "analysis body only" in us
    assert "Runtime owns receipt validation and retry policy." in us
    assert "分析正文" in cn
    assert "运行层负责" in cn


def test_social_prompt_requires_tool_then_write_contract() -> None:
    us = _read("agents/social_analyst/prompts/US.md")
    cn = _read("agents/social_analyst/prompts/CN_A.md")
    for text in (us, cn):
        assert "social_sentiment" in text
        assert "openviking.write_material" in text
        assert "[OpenVikingWriteTarget]" in text
        assert "[RuntimeTarget]" not in text
        assert "{ticker}" not in text
        assert "{company_name}" not in text
        _assert_no_manual_claim_fields(text)
    assert "analysis body only" in us
    assert "Runtime owns receipt validation and retry policy." in us
    assert "分析正文" in cn
    assert "运行层负责" in cn


def test_frontline_user_and_skills_require_write_target_and_claim_block() -> None:
    for worker in ("fundamental_analyst", "news_analyst", "social_analyst"):
        user_text = _read(f"agents/{worker}/USER.md")
        skill_text = _read(f"agents/{worker}/skills/claw-trade-stage/SKILL.md")
        assert "openviking.write_material" in user_text
        assert "[OpenVikingWriteTarget]" in user_text
        assert "运行层负责" in user_text
        _assert_no_manual_claim_fields(user_text)
        assert "openviking.write_material" in skill_text
        assert "[OpenVikingWriteTarget]" in skill_text
        assert "runtime owns receipt validation and retry policy." in skill_text.lower()
        _assert_no_manual_claim_fields(skill_text)
