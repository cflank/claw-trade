from __future__ import annotations

from pathlib import Path


CN_A_FRONTLINE_TOOLS = {
    "market_analyst": "market_market_data_pack",
    "fundamental_analyst": "fundamental_fundamentals_data_pack",
    "news_analyst": "news_news_data_pack",
    "social_analyst": "social_social_sentiment_pack",
}

US_FRONTLINE_TOOLS = {
    "market_analyst": ("get_stock_data", "get_indicators"),
    "fundamental_analyst": ("get_fundamentals", "get_balance_sheet", "get_cashflow", "get_income_statement"),
    "news_analyst": ("get_news", "get_global_news"),
    "social_analyst": ("get_news",),
}

FRONTLINE_EXECUTION_TONE_SNIPPETS = {
    "market_analyst": ("工作流程：", "接收到工具数据后，必须立即生成完整的技术分析报告"),
    "social_analyst": ("工作流程：", "收到工具数据后，生成完整中文分析报告"),
}

FORBIDDEN_PROTOCOL_TOKENS = (
    "[RuntimeTarget]",
    "[OpenVikingWriteTarget]",
    "control.claims.v1",
    "claim block",
    "claims: []",
    "claim_id",
    "evidence_ids",
    "source_worker_id",
    "receipt_path",
    "mat-pending-",
)


def _read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def _assert_no_handwritten_protocol(text: str) -> None:
    for token in FORBIDDEN_PROTOCOL_TOKENS:
        assert token not in text


def test_frontline_cn_prompts_keep_only_domain_pack_tools() -> None:
    for worker, data_tool in CN_A_FRONTLINE_TOOLS.items():
        text = _read(f"agents/{worker}/prompts/CN_A.md")
        assert data_tool in text
        for us_tool in US_FRONTLINE_TOOLS[worker]:
            assert us_tool not in text
        assert "openviking_write_material" not in text
        assert "tool_choice" not in text
        assert "报告" in text
        for snippet in FRONTLINE_EXECUTION_TONE_SNIPPETS.get(worker, ()):
            assert snippet not in text
        _assert_no_handwritten_protocol(text)


def test_frontline_cn_prompt_tool_parameters_match_frontline_schema() -> None:
    for worker in CN_A_FRONTLINE_TOOLS:
        text = _read(f"agents/{worker}/prompts/CN_A.md")
        if "工具调用参数：" not in text:
            continue
        section = text.split("工具调用参数：", 1)[1].split("写作边界", 1)[0]
        assert "current_date" not in section


def test_frontline_us_prompts_keep_only_us_profile_tools() -> None:
    for worker, data_tools in US_FRONTLINE_TOOLS.items():
        text = _read(f"agents/{worker}/prompts/US.md")
        for data_tool in data_tools:
            assert data_tool in text
        assert CN_A_FRONTLINE_TOOLS[worker] not in text
        if worker == "market_analyst":
            assert "us_market_data_pack" not in text
        if worker == "fundamental_analyst":
            assert "us_fundamentals_data_pack" not in text
        if worker == "news_analyst":
            assert "us_news_data_pack" not in text
        if worker == "social_analyst":
            assert "us_social_sentiment_pack" not in text
        assert "openviking_write_material" not in text
        assert "tool_choice" not in text
        assert "report" in text.lower()
        _assert_no_handwritten_protocol(text)


def test_frontline_user_and_stage_skill_do_not_request_report_submission_tool() -> None:
    for worker in CN_A_FRONTLINE_TOOLS:
        user_text = _read(f"agents/{worker}/USER.md")
        skill_text = _read(f"agents/{worker}/skills/claw-trade-stage/SKILL.md")
        for text in (user_text, skill_text):
            assert "openviking_write_material" not in text
            assert "tool_choice" not in text
            _assert_no_handwritten_protocol(text)
