from __future__ import annotations

import re
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
    "report_polisher",
)

APPROVED_PROMPT_PROFILES = ("US", "CN_A")
UNAPPROVED_PROMPT_PROFILES = ("HK", "CRYPTO")
REPORT_POLISHER = "report_polisher"
APPROVED_PROMPT_CASES = tuple(
    (worker_id, profile)
    for worker_id in REQUIRED_WORKERS
    for profile in APPROVED_PROMPT_PROFILES
)
UNAPPROVED_PROMPT_CASES = tuple(
    (worker_id, profile)
    for worker_id in REQUIRED_WORKERS
    for profile in UNAPPROVED_PROMPT_PROFILES
)

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

US_PROMPT_FORBIDDEN_CONTROL_TOKENS = (
    "OpenClaw",
    "OpenViking",
    "[ApprovedMaterials]",
    "[RuntimeTarget]",
    "[ReportSubmission]",
    "RuntimeTarget",
    "ReportSubmission",
    "viking://",
    "material_id",
    "l1_sha256",
    "l2_available",
    "receipt_path",
    "source_worker_id",
    "tool_choice",
    "Python",
    "artifact",
    "capability",
)

US_BASELINE_SNIPPETS: dict[str, tuple[str, ...]] = {
    "market_analyst": (
        "You are a trading assistant tasked with analyzing financial markets",
        "select the **most relevant indicators**",
        "The goal is to choose up to **8 indicators**",
        "Write a very detailed and nuanced report of the trends you observe",
        "append a Markdown table at the end of the report",
    ),
    "fundamental_analyst": (
        "You are a researcher tasked with analyzing fundamental information over the past week about a company",
        "financial documents, company profile, basic company financials, and company financial history",
        "Make sure to include as much detail as possible",
        "append a Markdown table at the end of the report",
    ),
    "news_analyst": (
        "You are a news researcher tasked with analyzing recent news and trends over the past week",
        "current state of the world that is relevant for trading and macroeconomics",
        "Provide specific, actionable insights with supporting evidence",
        "append a Markdown table at the end of the report",
    ),
    "social_analyst": (
        "You are a social media and company specific news researcher/analyst",
        "write a comprehensive long report detailing your analysis, insights, and implications",
        "Try to look at all sources possible from social media to sentiment to news",
        "append a Markdown table at the end of the report",
    ),
    "bull_researcher": (
        "You are a Bull Analyst advocating for investing in the stock",
        "Growth Potential:",
        "Bear Counterpoints:",
        "engaging directly with the bear analyst's points",
    ),
    "bear_researcher": (
        "You are a Bear Analyst making the case against investing in the stock",
        "Risks and Challenges:",
        "Bull Counterpoints:",
        "directly engaging with the bull analyst's points",
    ),
    "research_manager": (
        "As the portfolio manager and debate facilitator",
        "align with the bear analyst, the bull analyst, or choose Hold",
        "Your recommendation--Buy, Sell, or Hold--must be clear and actionable",
        "develop a detailed investment plan for the trader",
    ),
    "trader": (
        "You are a trading agent analyzing market data to make investment decisions",
        "provide a specific recommendation to buy, sell, or hold",
        "FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL**",
        "Proposed Investment Plan: {investment_plan}",
    ),
    "risk_challenger": (
        "As the Aggressive Risk Analyst",
        "champion high-reward, high-risk opportunities",
        "Here is the trader's decision:",
        "countering with data-driven rebuttals",
    ),
    "risk_guardian": (
        "As the Conservative Risk Analyst",
        "protect assets, minimize volatility",
        "Here is the trader's decision:",
        "low-risk strategy",
    ),
    "risk_moderator": (
        "As the Neutral Risk Analyst",
        "provide a balanced perspective",
        "Here is the trader's decision:",
        "Challenge each of their points",
    ),
    "portfolio_manager": (
        "As the Portfolio Manager, synthesize the risk analysts' debate",
        "**Rating Scale** (use exactly one):",
        "Research Manager's investment plan: **{research_plan}**",
        "Trader's transaction proposal: **{trader_decision}**",
        "Be decisive and ground every conclusion in specific evidence",
    ),
}

SUPPORTED_US_PROMPT_PLACEHOLDERS = {
    "ticker",
    "company_name",
    "market",
    "currency",
    "currency_symbol",
    "current_date",
    "start_date",
    "end_date",
    "market_research_report",
    "sentiment_report",
    "news_report",
    "fundamentals_report",
    "history",
    "current_response",
    "past_memory_str",
    "investment_plan",
    "trader_decision",
    "current_safe_response",
    "current_neutral_response",
    "current_risky_response",
    "research_plan",
    "portfolio_manager_report",
    "market_analyst_report",
    "fundamental_analyst_report",
    "news_analyst_report",
    "social_analyst_report",
    "trader_report",
    "supporting_worker_reports",
    "chart_assets_note",
}

US_RESEARCH_MANAGER_ALLOWED_PLACEHOLDERS = {
    "ticker",
    "past_memory_str",
    "history",
}

CN_A_RESEARCH_MANAGER_REQUIRED_PLACEHOLDERS = {
    "ticker",
    "past_memory_str",
    "market_research_report",
    "sentiment_report",
    "news_report",
    "fundamentals_report",
    "history",
}

FRONTLINE_WORKERS: tuple[str, ...] = (
    "market_analyst",
    "fundamental_analyst",
    "news_analyst",
    "social_analyst",
)

DOWNSTREAM_DECISION_WORKERS: tuple[str, ...] = (
    "bull_researcher",
    "bear_researcher",
    "research_manager",
    "trader",
    "risk_challenger",
    "risk_guardian",
    "risk_moderator",
    "portfolio_manager",
)

ACTION_SEMANTIC_WORKERS: tuple[str, ...] = (
    "research_manager",
    "trader",
    "portfolio_manager",
)

FRONTLINE_PROCESS_PROSE_RULE_SNIPPETS: tuple[str, ...] = (
    "最终报告正文必须直接从报告标题或正文第一句开始",
    "不要输出“我将调用工具”",
    "数据限制与风险提示",
)

FRONTLINE_MACHINE_PROTOCOL_KEYWORDS: tuple[str, ...] = (
    "artifact",
    "ref",
    "hash",
    "receipt",
    "capability",
    "ApprovedMaterials",
    "RuntimeTarget",
    "ReportSubmission",
)

US_FRONTLINE_SELF_IMPOSED_STYLE_BANS: tuple[str, ...] = (
    "Do not write a planning paragraph before the first tool call",
)

US_MARKET_OVERBROAD_DECISION_BANS: tuple[str, ...] = (
    "Do not present the final portfolio decision",
    "Do not use `FINAL TRANSACTION PROPOSAL`",
)

US_DOWNSTREAM_SELF_IMPOSED_MEMO_BANS: tuple[str, ...] = (
    "If important evidence is missing, state the limitation instead of filling it in",
    "write a limitation report",
    "avoid strong opinions",
    "avoid decisive language",
    "avoid final recommendations",
    "do not provide final recommendations",
    "do not provide a final transaction proposal",
)

US_DOWNSTREAM_ROLE_STYLE_SNIPPETS: dict[str, tuple[str, ...]] = {
    "bull_researcher": (
        "This truthfulness rule does not prohibit a strong bull stance",
        "direct rebuttals",
        "clear pro-investment view",
    ),
    "bear_researcher": (
        "This truthfulness rule does not prohibit a strong bear stance",
        "direct rebuttals",
        "clear anti-investment view",
    ),
    "research_manager": (
        "This truthfulness rule does not prohibit a decisive Buy/Sell/Hold recommendation",
        "clear commitment to the strongest side of the debate",
        "concrete strategic actions",
    ),
    "trader": (
        "This truthfulness rule does not prohibit a firm trading decision",
        "concrete execution plan",
        "final BUY/HOLD/SELL transaction proposal",
    ),
    "risk_challenger": (
        "This truthfulness rule does not prohibit an aggressive risk stance",
        "bold upside framing",
        "forceful challenge to overly cautious views",
    ),
    "risk_guardian": (
        "This truthfulness rule does not prohibit a forceful conservative risk stance",
        "direct rebuttals",
        "firm critique of excessive risk-taking",
    ),
    "risk_moderator": (
        "This truthfulness rule does not prohibit a critical neutral risk stance",
        "direct challenges to both sides",
        "firm risk-adjusted view",
    ),
    "portfolio_manager": (
        "This truthfulness rule does not prohibit a decisive rating",
        "clear final trading decision",
        "strong risk/reward judgment",
    ),
}

US_ACTION_SEMANTIC_SNIPPETS: dict[str, tuple[str, ...]] = {
    "research_manager": (
        "If you choose Hold, do not instruct selling existing long positions",
        "Hold means maintain the current position",
        "choose Sell rather than Hold",
    ),
    "trader": (
        "HOLD means maintain the current position",
        "must not mean selling existing long positions",
        "the final proposal must be SELL, not HOLD",
    ),
    "portfolio_manager": (
        "**Hold** means maintain the current position",
        "you must not say to sell existing long positions",
        "the rating must be Underweight or Sell, not Hold",
    ),
}

CN_A_ACTION_SEMANTIC_SNIPPETS: dict[str, tuple[str, ...]] = {
    "research_manager": (
        "如果选择“持有”，不得写成卖出现有多头",
        "“持有”表示维持当前仓位",
        "建议必须是“卖出”，不是“持有”",
    ),
    "trader": (
        "“持有”表示维持当前仓位",
        "不得把“持有”写成卖出现有多头",
        "最终交易建议必须是“卖出”，不是“持有”",
    ),
    "portfolio_manager": (
        "“持有”表示维持当前仓位",
        "不得在“持有”下写卖出现有多头",
        "最终建议必须是“卖出”，不是“持有”",
    ),
}

SOCIAL_TOOL_SILENT_PROCESS_RULE_SNIPPETS: tuple[str, ...] = (
    "需要调用工具时，直接发起工具调用；不要先输出任何自然语言说明。",
    "如果工具调用失败且需要重试，直接再次发起工具调用；不要输出“工具调用超时”“我来调用”“我重新尝试”等过程说明。",
    "最终回答第一行必须是 Markdown 标题，且必须以 `# ` 开头。",
)

AGENT_FACING_RELATIVE_PATHS = (
    "USER.md",
    "skills/claw-trade-stage/SKILL.md",
)


@pytest.mark.parametrize(("worker_id", "profile"), APPROVED_PROMPT_CASES)
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
    if worker_id == REPORT_POLISHER:
        assert "human-approved report-polisher" in review["us_alignment"]
        assert "alphaear-reporter" in review["cn_a_alignment"]
    else:
        assert review["us_alignment"] == "TradingAgents"
        assert review["cn_a_alignment"] == "TradingAgents-CN"


@pytest.mark.parametrize(("worker_id", "profile"), UNAPPROVED_PROMPT_CASES)
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


@pytest.mark.parametrize("worker_id", tuple(worker for worker in REQUIRED_WORKERS if worker != REPORT_POLISHER))
def test_us_worker_prompts_keep_original_tradingagents_baseline_language(worker_id: str) -> None:
    text = (Path("agents") / worker_id / "prompts" / "US.md").read_text(encoding="utf-8")

    for snippet in US_BASELINE_SNIPPETS[worker_id]:
        assert snippet in text, f"{worker_id} missing original TradingAgents snippet: {snippet!r}"


@pytest.mark.parametrize("worker_id", REQUIRED_WORKERS)
def test_us_worker_prompts_do_not_contain_runtime_protocol_or_control_plane_terms(worker_id: str) -> None:
    text = (Path("agents") / worker_id / "prompts" / "US.md").read_text(encoding="utf-8")

    for token in US_PROMPT_FORBIDDEN_CONTROL_TOKENS:
        assert token not in text, f"{worker_id} US prompt contains control-plane token {token!r}"


@pytest.mark.parametrize("worker_id", REQUIRED_WORKERS)
def test_us_worker_prompt_placeholders_are_supported_runtime_vars(worker_id: str) -> None:
    text = (Path("agents") / worker_id / "prompts" / "US.md").read_text(encoding="utf-8")
    placeholders = _prompt_placeholders(text)

    unsupported = placeholders - SUPPORTED_US_PROMPT_PLACEHOLDERS
    assert unsupported == set()


def test_research_manager_prompt_placeholders_follow_profile_material_boundaries() -> None:
    us_text = (Path("agents") / "research_manager" / "prompts" / "US.md").read_text(encoding="utf-8")
    cn_a_text = (Path("agents") / "research_manager" / "prompts" / "CN_A.md").read_text(encoding="utf-8")

    us_placeholders = _prompt_placeholders(us_text)
    cn_a_placeholders = _prompt_placeholders(cn_a_text)

    assert us_placeholders == US_RESEARCH_MANAGER_ALLOWED_PLACEHOLDERS
    assert cn_a_placeholders == CN_A_RESEARCH_MANAGER_REQUIRED_PLACEHOLDERS


def test_cn_a_frontline_prompts_enforce_no_process_opening_and_no_machine_protocol_keywords() -> None:
    for worker_id in FRONTLINE_WORKERS:
        text = (Path("agents") / worker_id / "prompts" / "CN_A.md").read_text(encoding="utf-8")
        for snippet in FRONTLINE_PROCESS_PROSE_RULE_SNIPPETS:
            assert snippet in text, f"{worker_id} missing required process-prose rule: {snippet!r}"
        for token in FRONTLINE_MACHINE_PROTOCOL_KEYWORDS:
            assert token not in text, f"{worker_id} prompt contains machine protocol keyword {token!r}"


def test_us_frontline_prompts_do_not_carry_self_imposed_tool_preamble_bans() -> None:
    for worker_id in FRONTLINE_WORKERS:
        text = (Path("agents") / worker_id / "prompts" / "US.md").read_text(encoding="utf-8")
        for snippet in US_FRONTLINE_SELF_IMPOSED_STYLE_BANS:
            assert snippet not in text, f"{worker_id} US prompt contains self-imposed style ban: {snippet!r}"


def test_us_market_prompt_allows_original_tradingagents_transaction_proposal() -> None:
    text = (Path("agents") / "market_analyst" / "prompts" / "US.md").read_text(encoding="utf-8")

    assert "FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL**" in text
    assert "actionable BUY/HOLD/SELL technical recommendation" in text
    assert "Include `vwma` when evaluating volume confirmation" in text
    for snippet in US_MARKET_OVERBROAD_DECISION_BANS:
        assert snippet not in text


def test_us_fundamental_prompt_requires_quarterly_and_annual_statement_history() -> None:
    text = (Path("agents") / "fundamental_analyst" / "prompts" / "US.md").read_text(encoding="utf-8")

    assert "Before writing the fundamental report, complete the full evidence collection sequence" in text
    assert '`get_balance_sheet` with `freq="quarterly"` and `freq="annual"`' in text
    assert '`get_cashflow` with `freq="quarterly"` and `freq="annual"`' in text
    assert '`get_income_statement` with `freq="quarterly"` and `freq="annual"`' in text
    assert "Use the quarterly statements for recent operating momentum" in text
    assert "annual statements for multi-year history" in text
    assert "continue the remaining statement calls" in text
    assert "Clearly label TTM, quarterly, and annual-history figures" in text
    assert 'Use `freq="quarterly"` for the statement tools unless' not in text
    assert "If no fundamental data result is already available in this turn" not in text


def test_report_polisher_prompts_require_chinese_long_form_output_without_summary_compression() -> None:
    us_text = (Path("agents") / "report_polisher" / "prompts" / "US.md").read_text(encoding="utf-8")
    cn_a_text = (Path("agents") / "report_polisher" / "prompts" / "CN_A.md").read_text(encoding="utf-8")

    assert "The final output must be written in Chinese" in us_text
    assert "This is long-form report polishing, not a short summary" in us_text
    assert "Do not collapse the analyst materials into brief abstracts" in us_text
    assert "professional sell-side / investment-bank final report" in us_text
    assert "evidence -> interpretation -> investment implication -> risk, trigger, or invalidation condition" in us_text
    assert "Do not move that analytical chain into an appendix" in us_text
    assert "# {company_name}（{ticker}）投资研究报告" in us_text
    assert "## 二、技术指标分析" in us_text
    assert "MACD/RSI 动量信号" in us_text
    assert "布林带/ATR 波动信号" in us_text
    assert "成交量/VWMA 确认" in us_text
    assert "失效条件与触发条件" in us_text
    assert "毛利率/营业利润率/净利率" in us_text
    assert "资产负债表、杠杆和流动性" in us_text
    assert "七、关键分歧与跟踪条件" in us_text
    assert "Key Debates and Monitoring Conditions" not in us_text
    assert "output only that final report" in us_text

    assert "最终输出必须使用中文" in cn_a_text
    assert "这是完整终稿润色，不是短摘要" in cn_a_text
    assert "不得把上游报告压缩成几个概述段" in cn_a_text
    assert "专业卖方/投行研究终稿" in cn_a_text
    assert "证据 -> 解读 -> 投资含义 -> 风险、触发或失效条件" in cn_a_text
    assert "不要把分析链挪到附录" in cn_a_text
    assert "正文主体必须覆盖：图表读法与价格结构" in cn_a_text
    assert "趋势与价格结构" in cn_a_text
    assert "均线系统" in cn_a_text
    assert "MACD/RSI 动量信号" in cn_a_text
    assert "布林带/ATR 波动信号" in cn_a_text
    assert "成交量/VWMA 确认" in cn_a_text
    assert "失效条件与触发条件" in cn_a_text
    assert "毛利率/营业利润率/净利率" in cn_a_text
    assert "资产负债表、杠杆和流动性" in cn_a_text
    assert "这里可以简洁，但前面各节不能压缩成摘要" in cn_a_text


def test_us_downstream_prompts_keep_truthfulness_redlines_from_becoming_memo_style_bans() -> None:
    for worker_id in DOWNSTREAM_DECISION_WORKERS:
        text = (Path("agents") / worker_id / "prompts" / "US.md").read_text(encoding="utf-8")

        for snippet in US_DOWNSTREAM_SELF_IMPOSED_MEMO_BANS:
            assert snippet not in text, f"{worker_id} US prompt contains self-imposed memo/style ban: {snippet!r}"
        for snippet in US_DOWNSTREAM_ROLE_STYLE_SNIPPETS[worker_id]:
            assert snippet in text, f"{worker_id} US prompt does not preserve original strong-role style: {snippet!r}"


def test_decision_prompts_do_not_add_self_imposed_hold_action_guards() -> None:
    for worker_id in ACTION_SEMANTIC_WORKERS:
        us_text = (Path("agents") / worker_id / "prompts" / "US.md").read_text(encoding="utf-8")
        cn_a_text = (Path("agents") / worker_id / "prompts" / "CN_A.md").read_text(encoding="utf-8")

        for snippet in US_ACTION_SEMANTIC_SNIPPETS[worker_id]:
            assert snippet not in us_text, f"{worker_id} US prompt contains self-imposed Hold action guard: {snippet!r}"
        for snippet in CN_A_ACTION_SEMANTIC_SNIPPETS[worker_id]:
            assert snippet not in cn_a_text, f"{worker_id} CN_A prompt contains self-imposed Hold action guard: {snippet!r}"


def test_cn_a_market_prompt_keeps_tradingagents_cn_visual_headings_without_emoji_ban() -> None:
    text = (Path("agents") / "market_analyst" / "prompts" / "CN_A.md").read_text(encoding="utf-8")

    assert "## 📊 股票基本信息" in text
    assert "## 📈 技术指标分析" in text
    assert "## 📉 价格趋势分析" in text
    assert "## 💭 投资建议" in text
    assert "不要使用emoji" not in text
    assert "纯文本标题" not in text
    assert "报告标题必须是" not in text


def test_social_prompt_requires_silent_tool_call_and_hash_title_first_line() -> None:
    text = (Path("agents") / "social_analyst" / "prompts" / "CN_A.md").read_text(encoding="utf-8")
    for snippet in SOCIAL_TOOL_SILENT_PROCESS_RULE_SNIPPETS:
        assert snippet in text, f"social_analyst missing required silent-process rule: {snippet!r}"


def test_prompt_alignment_policy_is_documented_as_runtime_evidence_not_static_render() -> None:
    agents_rules = Path("AGENTS.md").read_text(encoding="utf-8")
    playbook = Path("docs/prompt_alignment_playbook.md").read_text(encoding="utf-8")

    assert "Provider final prompt evidence must come from provider payload capture" in agents_rules
    assert "Prompt alignment is not proven by static tests alone" in agents_rules
    assert "model-visible messages must not contain runtime wrapper prose" in agents_rules
    assert "Prompt front matter is agent configuration only" in agents_rules
    assert "must expose no OpenViking read/write tools to the model" in agents_rules
    assert "claw_provider_final_prompt" in playbook
    assert "不得用静态渲染" in playbook
    assert "真实 provider prompt 必须从角色正文直接开始" in playbook
    assert "准备让 CN/原版纯 prompt worker 暴露 OpenViking read/write 工具" in playbook


def _front_matter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    assert lines and lines[0] == "---", f"{path} missing front matter"
    end = lines.index("---", 1)
    return _parse_key_value_lines(lines[1:end])


def _simple_yaml(path: Path) -> dict[str, str]:
    return _parse_key_value_lines(path.read_text(encoding="utf-8").splitlines())


def _prompt_placeholders(text: str) -> set[str]:
    return set(re.findall(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", text))


def _parse_key_value_lines(lines: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, value = line.split(":", 1)
        result[key.strip()] = value.strip()
    return result
