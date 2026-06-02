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

APPROVED_PROMPT_PROFILES = ("US", "CN_A", "HK")
UNAPPROVED_PROMPT_PROFILES: tuple[str, ...] = ()
REPORT_POLISHER = "report_polisher"
APPROVED_PROMPT_CASES = tuple(
    (worker_id, profile)
    for worker_id in REQUIRED_WORKERS
    for profile in APPROVED_PROMPT_PROFILES
)
HK_PROMPT_CASES = tuple((worker_id, "HK") for worker_id in REQUIRED_WORKERS)
APPROVED_CRYPTO_PROMPT_CASES = tuple(
    (worker_id, "CRYPTO")
    for worker_id in REQUIRED_WORKERS
)
UNAPPROVED_PROMPT_CASES = tuple(
    (worker_id, profile)
    for worker_id in REQUIRED_WORKERS
    for profile in UNAPPROVED_PROMPT_PROFILES
    if (worker_id, profile) not in APPROVED_CRYPTO_PROMPT_CASES
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
    "final_report_section_instruction",
}
SUPPORTED_HK_PROMPT_PLACEHOLDERS = SUPPORTED_US_PROMPT_PLACEHOLDERS
SUPPORTED_CRYPTO_PROMPT_PLACEHOLDERS = SUPPORTED_US_PROMPT_PLACEHOLDERS | {"trader_plan"}

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
HK_FRONTLINE_TOOLS = {
    "market_analyst": "claw_get_market_pack",
    "fundamental_analyst": "claw_get_fundamental_pack",
    "news_analyst": "claw_get_news_pack",
    "social_analyst": "claw_get_social_pack",
}
HK_SPECIFIC_TOOL_TOKENS = ("hk_market_data", "hk_fundamental_data", "hk_news_data", "hk_social_sentiment")

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


@pytest.mark.parametrize(("worker_id", "profile"), APPROVED_CRYPTO_PROMPT_CASES)
def test_approved_crypto_worker_prompts_are_real_prompts_not_fail_closed_placeholders(
    worker_id: str,
    profile: str,
) -> None:
    prompt_path = Path("agents") / worker_id / "prompts" / f"{profile}.md"
    metadata = _front_matter(prompt_path)
    text = prompt_path.read_text(encoding="utf-8")

    assert metadata["profile"] == "CRYPTO"
    assert metadata["profile_status"] == "approved"
    assert metadata["worker_id"] == worker_id
    assert metadata["stage"] == worker_by_id(worker_id).stage.value
    assert "not been approved" not in text
    assert "Fail explicitly" not in text
    assert "fallback" not in text.lower()
    assert "加密" in text or "CRYPTO" in text
    assert "报告" in text or "辩论" in text or "决策" in text


def test_approved_crypto_market_prompt_uses_compact_pack_boundary() -> None:
    text = (Path("agents") / "market_analyst" / "prompts" / "CRYPTO.md").read_text(encoding="utf-8")

    assert "可用工具：本阶段可见的市场资料包工具" in text
    assert "worker 不直接读取原始大 JSON" in text
    assert "资料就绪度只能说明资料覆盖和通道质量" in text
    assert "若上游材料含内部字段名、键值串、英文状态词" in text
    assert "任何工具名、审计计数、机器状态码、带下划线字段" in text
    assert "不得推断其正常、过热或极端" in text
    assert "如果资料包只列出价格历史和本地技术指标成功" in text
    assert "每个小节必须使用 Markdown 表格" in text
    assert "| 指标 | 数据 | 推导 | 交易作用 | 失效条件 |" in text
    assert "每个关键指标单独一行" in text


def test_crypto_prompts_preserve_cn_a_role_strength_with_crypto_semantics() -> None:
    expected_snippets = {
        "fundamental_analyst": ("加密资产基本面分析师", "代币经济分析", "FDV/TVL", "买入/持有/卖出"),
        "news_analyst": ("加密市场新闻与事件分析师", "监管", "机构资金", "Markdown 表格"),
        "social_analyst": ("加密社区与市场情绪分析师", "真实平台样本", "1-5 天市场反应"),
        "bull_researcher": ("看涨加密资产研究员", "反驳看跌观点", "清算挤压"),
        "bear_researcher": ("看跌加密资产研究员", "反驳看涨观点", "代币释放/解锁"),
        "research_manager": ("买入、卖出或持有", "避免仅仅因为双方都有有效观点就默认选择持有", "价格区间与交易条件分析"),
        "trader": ("最终交易建议: **买入/持有/卖出**", "入场条件", "止损/失效位"),
        "risk_challenger": ("激进风险分析师", "高回报、高风险", "清算空头挤压"),
        "risk_guardian": ("安全/保守风险分析师", "保护资产", "交易所风险"),
        "risk_moderator": ("中性风险分析师", "平衡的加密资产风险视角", "降低杠杆"),
        "portfolio_manager": ("买入、卖出或持有", "清晰和果断", "调整后的交易员计划"),
        "report_polisher": ("加密资产投资研究终稿编辑", "完整终稿润色，不是短摘要", "专业加密资产研究终稿"),
    }
    for worker_id, snippets in expected_snippets.items():
        text = (Path("agents") / worker_id / "prompts" / "CRYPTO.md").read_text(encoding="utf-8")
        for snippet in snippets:
            assert snippet in text, f"{worker_id} CRYPTO prompt missing role-style snippet: {snippet!r}"
        if worker_id in {
            "fundamental_analyst",
            "bull_researcher",
            "bear_researcher",
            "research_manager",
            "trader",
            "portfolio_manager",
        }:
            assert "PE/PB/ROE" in text


def test_crypto_frontline_prompts_force_missing_data_into_worker_l1_reports() -> None:
    expected_snippets = {
        "fundamental_analyst": (
            "基本面资料包工具",
            "资料包未可用 / 未调用成功 / 覆盖不足",
            "不得用模型常识、历史印象或上游未提供的证据补写缺失事实",
        ),
        "news_analyst": (
            "新闻资料包工具",
            "不得写真实新闻、真实公告、真实监管事件或真实市场反应结论",
            "搜索摘要和媒体聚合标题只能作为发现线索",
            "即使线索提到机构资金、监管、链上活动或其它市场主题，也不能写成已验证事实",
            "事件预期来源只能表达事件预期或盘口概率",
            "市场级情绪指标不是新闻源",
        ),
        "social_analyst": (
            "舆情资料包工具",
            "不得写真实社交平台观点、真实 KOL 立场、真实社区共识或真实情绪结论",
            "搜索摘要只能作为公开讨论线索",
            "即使搜索摘要提到机构资金、链上大户或交易所行为，也不能写成已验证事实",
            "事件预期来源只能表达事件预期或盘口概率",
            "市场级情绪指标只能表达市场级情绪",
        ),
    }
    for worker_id, snippets in expected_snippets.items():
        text = (Path("agents") / worker_id / "prompts" / "CRYPTO.md").read_text(encoding="utf-8")
        for snippet in snippets:
            assert snippet in text, f"{worker_id} CRYPTO prompt missing missing-data boundary: {snippet!r}"


def test_crypto_downstream_prompts_condition_on_upstream_data_gaps_without_filling_facts() -> None:
    for worker_id in DOWNSTREAM_DECISION_WORKERS:
        text = (Path("agents") / worker_id / "prompts" / "CRYPTO.md").read_text(encoding="utf-8")
        assert "资料包未可用、未调用成功、覆盖不足或内容为空" in text
        assert "不得补写缺失事实" in text
        assert "数据缺口本身不是看涨或看跌事实" in text
        assert "必须逐项写明缺少哪些数据" in text
        assert "搜索发现、模型记忆或历史印象不能填补机构资金、链上、衍生品、清算或社交共识缺口" in text


def test_crypto_trader_and_polisher_forbid_unaudited_liquidation_price_calculation() -> None:
    trader_text = (Path("agents") / "trader" / "prompts" / "CRYPTO.md").read_text(encoding="utf-8")
    polisher_text = (Path("agents") / "report_polisher" / "prompts" / "CRYPTO.md").read_text(
        encoding="utf-8"
    )

    for text in (trader_text, polisher_text):
        assert "交易所、合约类型、保证金模式、维持保证金率和实际持仓参数" in text
        assert "不得" in text
        assert "具体清算价或强制平仓类价格" in text
        assert "清算价无法由现有材料审计计算" in text


def test_crypto_prompts_forbid_public_knowledge_gap_fill_facts() -> None:
    prompt_paths = (
        Path("agents") / "fundamental_analyst" / "prompts" / "CRYPTO.md",
        Path("agents") / "bull_researcher" / "prompts" / "CRYPTO.md",
        Path("agents") / "bear_researcher" / "prompts" / "CRYPTO.md",
        Path("agents") / "research_manager" / "prompts" / "CRYPTO.md",
        Path("agents") / "risk_challenger" / "prompts" / "CRYPTO.md",
        Path("agents") / "risk_guardian" / "prompts" / "CRYPTO.md",
        Path("agents") / "risk_moderator" / "prompts" / "CRYPTO.md",
        Path("agents") / "portfolio_manager" / "prompts" / "CRYPTO.md",
        Path("agents") / "report_polisher" / "prompts" / "CRYPTO.md",
    )

    for prompt_path in prompt_paths:
        text = prompt_path.read_text(encoding="utf-8")
        assert "输出前做读者版清理" in text
        assert "不要列举具体" in text or "具体事实名称" in text or "具体内容" in text
        assert (
            "上游数据" in text
            or "数据层证据" in text
            or "上游未验证" in text
            or "数据层恢复后再验证" in text
            or "未验证的" in text
            or "上游材料" in text
        )

    bull_text = (Path("agents") / "bull_researcher" / "prompts" / "CRYPTO.md").read_text(encoding="utf-8")
    assert "没有可用的基本面多头证据" in bull_text
    assert "不得来自模型记忆或信仰叙事" in bull_text

    challenger_text = (Path("agents") / "risk_challenger" / "prompts" / "CRYPTO.md").read_text(
        encoding="utf-8"
    )
    assert "只能列为待验证条件" in challenger_text


@pytest.mark.parametrize(("worker_id", "profile"), APPROVED_CRYPTO_PROMPT_CASES)
def test_crypto_worker_prompt_placeholders_are_supported_runtime_vars(
    worker_id: str,
    profile: str,
) -> None:
    text = (Path("agents") / worker_id / "prompts" / f"{profile}.md").read_text(encoding="utf-8")
    placeholders = _prompt_placeholders(text)

    unsupported = placeholders - SUPPORTED_CRYPTO_PROMPT_PLACEHOLDERS
    assert unsupported == set()


@pytest.mark.parametrize(("worker_id", "profile"), HK_PROMPT_CASES)
def test_hk_worker_prompts_are_real_prompts_not_fail_closed_placeholders(worker_id: str, profile: str) -> None:
    prompt_path = Path("agents") / worker_id / "prompts" / f"{profile}.md"
    metadata = _front_matter(prompt_path)
    text = prompt_path.read_text(encoding="utf-8")

    assert metadata["profile"] == "HK"
    assert metadata["profile_status"] == "approved"
    assert metadata["worker_id"] == worker_id
    assert metadata["stage"] == worker_by_id(worker_id).stage.value
    assert "not been approved" not in text
    assert "Fail explicitly" not in text
    assert "Do not fallback to US" not in text
    assert "Do not fallback to CN_A" not in text
    assert "港股" in text or "香港交易所" in text
    assert "报告" in text or "分析" in text or "辩论" in text


@pytest.mark.parametrize("worker_id", REQUIRED_WORKERS)
def test_agent_facing_text_does_not_contain_machine_protocol(worker_id: str) -> None:
    worker_dir = Path("agents") / worker_id
    paths = [
        worker_dir / "prompts" / "US.md",
        worker_dir / "prompts" / "CN_A.md",
        worker_dir / "prompts" / "HK.md",
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


def test_hk_frontline_prompts_reuse_existing_domain_pack_tools() -> None:
    us_tool_tokens = (
        "`get_stock_data`",
        "`get_indicators`",
        "`get_fundamentals`",
        "`get_balance_sheet`",
        "`get_cashflow`",
        "`get_income_statement`",
        "`get_news`",
        "`get_global_news`",
    )
    for worker_id in FRONTLINE_WORKERS:
        text = (Path("agents") / worker_id / "prompts" / "HK.md").read_text(encoding="utf-8")
        assert HK_FRONTLINE_TOOLS[worker_id] in text
        for hk_specific_tool in HK_SPECIFIC_TOOL_TOKENS:
            assert hk_specific_tool not in text
        assert "`HK`" in text or "HK（香港交易所）" in text
        for us_tool in us_tool_tokens:
            assert us_tool not in text
        for snippet in FRONTLINE_PROCESS_PROSE_RULE_SNIPPETS:
            assert snippet in text, f"{worker_id} HK prompt missing required process-prose rule: {snippet!r}"
        for token in FRONTLINE_MACHINE_PROTOCOL_KEYWORDS:
            assert token not in text, f"{worker_id} HK prompt contains machine protocol keyword {token!r}"


@pytest.mark.parametrize("worker_id", REQUIRED_WORKERS)
def test_hk_worker_prompt_placeholders_are_supported_runtime_vars(worker_id: str) -> None:
    text = (Path("agents") / worker_id / "prompts" / "HK.md").read_text(encoding="utf-8")
    placeholders = _prompt_placeholders(text)

    unsupported = placeholders - SUPPORTED_HK_PROMPT_PLACEHOLDERS
    assert unsupported == set()


def test_hk_prompts_preserve_tradingagents_cn_voice_without_memo_style_bans() -> None:
    expected_snippets = {
        "market_analyst": ("## 📊 股票基本信息", "## 📈 技术指标分析", "## 💭 投资建议"),
        "fundamental_analyst": ("港股基本面分析师", "投资建议（买入/持有/卖出）", "不得编造公司信息"),
        "news_analyst": ("港股财经新闻分析师", "关键新闻与事件梳理", "港交所披露"),
        "social_analyst": ("港股市场社交情绪分析师", "社交讨论热度", "数据限制与风险提示"),
        "bull_researcher": ("看涨分析师", "反驳看跌观点", "强有力的看涨立场"),
        "bear_researcher": ("看跌分析师", "反驳看涨观点", "强有力的看跌立场"),
        "research_manager": ("买入、卖出或持有", "做出承诺", "目标价格分析"),
        "trader": ("最终交易建议: **买入/持有/卖出**", "具体交易决策"),
        "risk_challenger": ("激进风险分析师", "高回报、高风险", "数据驱动的反驳"),
        "risk_guardian": ("安全/保守风险分析师", "保护资产", "直接回应他们的观点"),
        "risk_moderator": ("中性风险分析师", "平衡的港股风险视角", "挑战激进和安全分析师"),
        "portfolio_manager": ("买入、卖出或持有", "清晰和果断", "最终交易决策"),
        "report_polisher": ("完整终稿润色，不是短摘要", "专业卖方/投行研究终稿"),
    }
    assert set(expected_snippets) == set(REQUIRED_WORKERS)
    banned_style_snippets = (
        "避免强观点",
        "避免明确建议",
        "不要给买卖结论",
        "不要提供最终建议",
        "默认保守",
        "写成限制报告",
    )

    for worker_id, snippets in expected_snippets.items():
        text = (Path("agents") / worker_id / "prompts" / "HK.md").read_text(encoding="utf-8")
        for snippet in snippets:
            assert snippet in text, f"{worker_id} HK prompt missing role-style snippet: {snippet!r}"
        for snippet in banned_style_snippets:
            assert snippet not in text, f"{worker_id} HK prompt contains self-imposed style ban: {snippet!r}"


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

    assert "Use the available tool: `claw_get_fundamental_pack`" in text
    assert "quarterly/annual financial statement coverage" in text
    assert "valuation metrics" in text
    assert "source notes" in text
    assert "data gaps" in text
    assert "Clearly label TTM, quarterly, and annual-history figures" in text
    assert 'Use `freq="quarterly"` for the statement tools unless' not in text
    assert "If no fundamental data result is already available in this turn" not in text


def test_report_polisher_prompts_require_chinese_long_form_output_without_summary_compression() -> None:
    us_text = (Path("agents") / "report_polisher" / "prompts" / "US.md").read_text(encoding="utf-8")
    cn_a_text = (Path("agents") / "report_polisher" / "prompts" / "CN_A.md").read_text(encoding="utf-8")
    hk_text = (Path("agents") / "report_polisher" / "prompts" / "HK.md").read_text(encoding="utf-8")
    crypto_text = (Path("agents") / "report_polisher" / "prompts" / "CRYPTO.md").read_text(encoding="utf-8")
    user_text = (Path("agents") / "report_polisher" / "USER.md").read_text(encoding="utf-8")

    assert "The final output must be written in Chinese" in us_text
    assert "The first line of the final answer must be the Markdown H1 report title" in us_text
    assert "This is long-form report polishing, not a short summary" in us_text
    assert "Do not collapse the analyst materials into brief abstracts" in us_text
    assert "must not be more outline-like than `market_analyst_report`" in us_text
    assert "professional sell-side / investment-bank final report" in us_text
    assert "evidence -> interpretation -> investment implication -> risk, trigger, or invalidation condition" in us_text
    assert "Do not move that analytical chain into an appendix" in us_text
    assert "指标覆盖" in us_text
    assert "数据 -> 推导 -> 交易作用 -> 失效" in us_text
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
    assert "最终回答第一行必须是 Markdown H1 标题" in cn_a_text
    assert "这是完整终稿润色，不是短摘要" in cn_a_text
    assert "不得把上游报告压缩成几个概述段" in cn_a_text
    assert "终稿中的技术指标章节不得比 `market_analyst_report` 更提纲化" in cn_a_text
    assert "专业卖方/投行研究终稿" in cn_a_text
    assert "证据 -> 解读 -> 投资含义 -> 风险、触发或失效条件" in cn_a_text
    assert "不要把分析链挪到附录" in cn_a_text
    assert "指标覆盖" in cn_a_text
    assert "数据 -> 推导 -> 交易作用 -> 失效" in cn_a_text
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

    assert "最终输出必须使用中文" in hk_text
    assert "最终回答第一行必须是 Markdown H1 标题" in hk_text
    assert "这是完整终稿润色，不是短摘要" in hk_text
    assert "不得把上游报告压缩成几个概述段" in hk_text
    assert "终稿中的技术指标与交易结构章节不得比 `market_analyst_report` 更提纲化" in hk_text
    assert "专业卖方/投行研究终稿" in hk_text
    assert "证据 -> 解读 -> 投资含义 -> 风险、触发或失效条件" in hk_text
    assert "不要把分析链挪到附录" in hk_text
    assert "指标覆盖" in hk_text
    assert "数据 -> 推导 -> 交易作用 -> 失效" in hk_text
    assert "## 二、技术指标与交易结构分析" in hk_text

    assert "最终输出必须使用中文" in crypto_text
    assert "最终回答第一行必须是 Markdown H1 标题" in crypto_text
    assert "这是完整终稿润色，不是短摘要" in crypto_text
    assert "不得把上游报告压缩成几个概述段" in crypto_text
    assert "终稿中的市场结构与技术指标章节不得比 `market_analyst_report` 更提纲化" in crypto_text
    assert "专业加密资产研究终稿" in crypto_text
    assert "证据 -> 解读 -> 投资含义 -> 风险、触发或失效条件" in crypto_text
    assert "不要把分析链挪到附录" in crypto_text
    assert "# {company_name}（{ticker}）加密资产投资研究报告" in crypto_text
    assert "## 二、市场结构与技术指标分析" in crypto_text
    assert "数据状态" in crypto_text
    assert "指标覆盖" in crypto_text
    assert "OB/订单块" in crypto_text
    assert "FVG" in crypto_text
    assert "AHR999" in crypto_text
    assert "数据 -> 推导 -> 交易作用 -> 失效" in crypto_text
    assert "必须用 Markdown 表格排版" in crypto_text
    assert "| 指标 | 数据 | 推导 | 交易作用 | 失效条件 |" in crypto_text
    assert "不要把 Vegas、布林带、RSI、MACD、KD 挤在同一段" in crypto_text
    assert "不得原样粘贴 `market_analyst_report` 的整段“市场分析师完整指标材料”" in crypto_text
    assert "不得把缺数据写成市场没有多头/中性氛围" in crypto_text
    assert "资金费率、OI、多空比、清算地图" in crypto_text
    assert "项目与代币基本面分析" in crypto_text
    assert "FDV、市值、TVL、协议收入" in crypto_text
    assert "不得把搜索摘要写成事实" in crypto_text
    assert "不得把原始内部标识直接放进正文" in crypto_text
    assert "带下划线字段" in crypto_text
    assert "审计计数写成" in crypto_text
    assert "时间覆盖缺口写成" in crypto_text
    assert "终稿输出前最后自检" in crypto_text
    assert "不要用反引号保留内部标识" in crypto_text
    assert "未形成可引用的数据集、原始来源或来源尝试记录" in crypto_text
    assert "不要为了说明某条论据不可引用而写出未验证事实本身" in crypto_text
    assert "未验证的供给、网络采用、机构资金、宏观或链上线索" in crypto_text
    assert "任何工具名、审计计数、机器状态码、带下划线字段" in crypto_text
    assert "未验证的具体机构产品" in crypto_text
    assert "不要写“某工具标记为就绪”这类内部过程句" in crypto_text
    assert "终稿必须完整写到 `## 八、最终结论`" in crypto_text
    assert "不得停在任一中间章节、半句或列表项" in crypto_text
    assert "如果材料过长，优先压缩各节内部重复内容" in crypto_text
    assert "最后一段必须是完整自然段" in crypto_text
    assert "不得以“在……背景下”" in crypto_text
    assert "这里可以简洁，但前面各节不能压缩成摘要" in crypto_text
    assert "第一行必须是正式报告的 Markdown H1 标题" in user_text
    assert "不要以“好的”“收到”“我将”等过程性回应开头" in user_text
    assert "带下划线字段和资料包引用计数" in user_text
    assert "不能把它们压成几个提纲式结论" in user_text


def test_crypto_model_visible_prompts_do_not_seed_internal_or_unverified_literal_lists() -> None:
    prompt_paths = (
        *(Path("agents") / worker_id / "prompts" / "CRYPTO.md" for worker_id in REQUIRED_WORKERS),
        Path("agents") / "report_polisher" / "USER.md",
        Path("agents") / "market_analyst" / "skills" / "crypto-trading-analysis" / "SKILL.md",
        Path("agents") / "market_analyst" / "skills" / "crypto-trading-analysis" / "agents" / "openai.yaml",
        *(Path("agents") / "market_analyst" / "skills" / "crypto-trading-analysis" / "references" / name for name in (
            "amd-model.md",
            "indicators.md",
            "liquidity-and-derivatives.md",
            "macro-and-onchain.md",
            "output-templates.md",
            "quick-ref.md",
            "source-boundaries.md",
        )),
    )
    banned = (
        "dataset_refs",
        "raw_refs",
        "attempt_refs",
        "date_range_missing",
        "warehouse_missing",
        "readiness",
        "data_gaps",
        "provider_attempts",
        "reader" + "_brief",
        "CryptoLens",
        "Alternative.me",
        "LunarCrush",
        "Polymarket",
        "一切皆有可能",
        "没有证据证明不存在",
        "黄金坑",
        "洗盘",
        "历史上",
        "Short Squeeze",
        "空头回补",
        "清算单",
        "聪明钱",
        "ETF",
        "减半",
        "闪电网络",
        "工作量证明",
        "MicroStrategy",
        "S2F",
        "数字黄金",
        "强平价",
        "爆仓价",
        "输出前逐字搜索",
    )
    for prompt_path in prompt_paths:
        text = prompt_path.read_text(encoding="utf-8")
        for token in banned:
            assert token not in text, f"{prompt_path} seeds literal token {token!r}"


def test_report_polisher_prompts_support_sectioned_generation_without_protocol_leakage() -> None:
    prompt_paths = (
        Path("agents") / "report_polisher" / "prompts" / "US.md",
        Path("agents") / "report_polisher" / "prompts" / "CN_A.md",
        Path("agents") / "report_polisher" / "prompts" / "HK.md",
        Path("agents") / "report_polisher" / "prompts" / "CRYPTO.md",
    )

    for prompt_path in prompt_paths:
        text = prompt_path.read_text(encoding="utf-8")
        assert "{final_report_section_instruction}" in text
        assert "不是报告正文内容" in text or "not report body content" in text
        assert "为空时" in text or "is empty" in text
        assert "完整终稿" in text or "complete final report" in text
        assert "本次只写" in text or "write only the section or sections it specifies" in text
        assert "最终交付会按章节自然拼接" in text or "naturally joined section by section" in text
        assert "不得输出范围外章节" in text or "Do not output sections outside the requested scope" in text
        assert "严禁输出任何以 `# ` 开头的 H1 标题" in text or "must not output any line beginning with `# `" in text

    crypto_text = prompt_paths[-1].read_text(encoding="utf-8")
    assert "完整八节结构和第八节自然结尾要求" in crypto_text
    assert "不要把每个分段都写成完整八节" in crypto_text
    assert "所有编号章节必须使用 `##` 二级标题" in crypto_text
    assert "最后一段仍必须是完整自然段" in crypto_text
    assert "事件预期" in crypto_text
    assert "数据缺口本身不是看涨或看跌事实" in crypto_text
    assert "| 指标 | 数据 | 推导 | 交易作用 | 失效条件 |" in crypto_text
    assert "如果保留来源名有助于读者理解" in crypto_text
    assert "可用性状态写成“资料可用性/资料就绪”" in crypto_text

    user_text = (Path("agents") / "report_polisher" / "USER.md").read_text(encoding="utf-8")
    assert "final_report_section_instruction" in user_text
    assert "只是写作范围，不是报告正文内容" in user_text
    assert "当它为空时写完整终稿" in user_text
    assert "当它给定时只写指定章节" in user_text


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
        hk_text = (Path("agents") / worker_id / "prompts" / "HK.md").read_text(encoding="utf-8")

        for snippet in US_ACTION_SEMANTIC_SNIPPETS[worker_id]:
            assert snippet not in us_text, f"{worker_id} US prompt contains self-imposed Hold action guard: {snippet!r}"
        for snippet in CN_A_ACTION_SEMANTIC_SNIPPETS[worker_id]:
            assert snippet not in cn_a_text, f"{worker_id} CN_A prompt contains self-imposed Hold action guard: {snippet!r}"
            assert snippet not in hk_text, f"{worker_id} HK prompt contains self-imposed Hold action guard: {snippet!r}"


def test_hk_portfolio_manager_prompt_uses_research_plan_and_trader_decision_variables() -> None:
    text = (Path("agents") / "portfolio_manager" / "prompts" / "HK.md").read_text(encoding="utf-8")

    assert "{research_plan}" in text
    assert "{trader_decision}" in text
    assert "{trader_plan}" not in text
    assert "研究经理投资计划" in text
    assert "交易员交易计划" in text


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
