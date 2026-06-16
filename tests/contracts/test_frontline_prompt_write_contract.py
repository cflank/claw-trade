from __future__ import annotations

from pathlib import Path

CN_A_FRONTLINE_TOOLS = {
    "market_analyst": "claw_request_data",
    "fundamental_analyst": "claw_request_data",
    "news_analyst": "claw_request_data",
    "social_analyst": "claw_request_data",
}

US_FRONTLINE_TOOLS = {
    "market_analyst": ("claw_request_data",),
    "fundamental_analyst": ("claw_request_data",),
    "news_analyst": ("claw_request_data",),
    "social_analyst": ("claw_request_data",),
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


def test_frontline_cn_prompts_use_current_data_tool() -> None:
    for worker, data_tool in CN_A_FRONTLINE_TOOLS.items():
        text = _read(f"agents/{worker}/prompts/CN_A.md")
        assert data_tool in text
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


def test_cn_a_market_prompt_forbids_filling_values_without_data_refs() -> None:
    text = _read("agents/market_analyst/prompts/CN_A.md")

    assert "没有结构化/原始引用" in text
    assert "当前价格、涨跌幅、成交量、均线、MACD、RSI、布林带、支撑位、压力位、评级、目标价和止损位" in text
    assert "不得用近期公开行情、经验、估算或模型记忆补数值" in text


def test_cn_a_market_prompt_forbids_reader_facing_tool_name_and_process_prose() -> None:
    text = _read("agents/market_analyst/prompts/CN_A.md")

    assert "最终报告正文不得出现内部工具名" in text
    assert "标题之前禁止输出任何文字" in text
    assert "最终报告第一行必须是 `## 📊 股票基本信息`" in text
    assert "我获取到了" in text
    assert "禁止再次调用同一数据需求" in text
    assert "需要说明来源状态时，写成“数据层返回结果”或“数据结果”" in text


def test_frontline_us_prompts_keep_only_us_profile_tools() -> None:
    for worker, data_tools in US_FRONTLINE_TOOLS.items():
        text = _read(f"agents/{worker}/prompts/US.md")
        for data_tool in data_tools:
            assert data_tool in text
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
