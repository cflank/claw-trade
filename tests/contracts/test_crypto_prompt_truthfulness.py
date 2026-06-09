from pathlib import Path


CRYPTO_PROMPTS = (
    "agents/market_analyst/prompts/CRYPTO.md",
    "agents/fundamental_analyst/prompts/CRYPTO.md",
    "agents/news_analyst/prompts/CRYPTO.md",
    "agents/social_analyst/prompts/CRYPTO.md",
    "agents/bull_researcher/prompts/CRYPTO.md",
    "agents/bear_researcher/prompts/CRYPTO.md",
    "agents/research_manager/prompts/CRYPTO.md",
    "agents/trader/prompts/CRYPTO.md",
    "agents/risk_challenger/prompts/CRYPTO.md",
    "agents/risk_guardian/prompts/CRYPTO.md",
    "agents/risk_moderator/prompts/CRYPTO.md",
    "agents/portfolio_manager/prompts/CRYPTO.md",
    "agents/report_polisher/prompts/CRYPTO.md",
)


def _read(relative_path: str) -> str:
    return (Path(__file__).resolve().parents[2] / relative_path).read_text(encoding="utf-8")


def test_crypto_prompts_do_not_restore_gap_mode_or_stats_missing_language() -> None:
    banned_phrases = (
        "缺口模式优先级高于",
        "缺失结果优先级高于",
        "只要任一上游材料包含",
        "就按 CRYPTO 缺口场景处理",
        "最终报告只能使用下面固定文本结构",
        "四段组织",
        "完整叙事要求失效",
        "自由辩论要求失效",
        "自由调解要求失效",
        "缺少统计样本",
        "统计阈值",
        "胜率阈值",
        "概率判断标准",
        "有效样本",
        "最低样本阈值",
        "已有现货",
        "持有现货",
        "现有仓位",
        "现货仓位",
        "减半周期",
        "区块奖励",
        "释放时间表",
        "未来解锁",
        "积累/派发",
        "轧空",
        "空头挤压",
        "强制退出",
        "强制平仓",
    )
    for relative_path in CRYPTO_PROMPTS:
        prompt = _read(relative_path)
        for phrase in banned_phrases:
            assert phrase not in prompt, relative_path


def test_crypto_prompts_keep_truthfulness_without_auto_failing_on_partial_data() -> None:
    downstream_prompts = (
        "agents/bull_researcher/prompts/CRYPTO.md",
        "agents/bear_researcher/prompts/CRYPTO.md",
        "agents/research_manager/prompts/CRYPTO.md",
        "agents/trader/prompts/CRYPTO.md",
        "agents/risk_challenger/prompts/CRYPTO.md",
        "agents/risk_guardian/prompts/CRYPTO.md",
        "agents/risk_moderator/prompts/CRYPTO.md",
        "agents/portfolio_manager/prompts/CRYPTO.md",
    )
    for relative_path in downstream_prompts:
        prompt = _read(relative_path)
        assert "不要因为" in prompt, relative_path
        assert "压缩成缺口报告" in prompt or "自动停止" in prompt, relative_path
        assert "缺口不能被写成利多或利空事实" in prompt or "缺数据不能支持" in prompt, relative_path
        assert "不要编造" in prompt or "不得补写缺失事实" in prompt, relative_path


def test_crypto_prompts_preserve_units_sources_and_position_boundaries() -> None:
    fundamental_prompt = _read("agents/fundamental_analyst/prompts/CRYPTO.md")
    market_prompt = _read("agents/market_analyst/prompts/CRYPTO.md")
    news_prompt = _read("agents/news_analyst/prompts/CRYPTO.md")
    social_prompt = _read("agents/social_analyst/prompts/CRYPTO.md")
    trader_prompt = _read("agents/trader/prompts/CRYPTO.md")
    polisher_prompt = _read("agents/report_polisher/prompts/CRYPTO.md")

    assert "AHR999 是指数/无量纲读数" in fundamental_prompt
    assert "AHR999 的数值是无量纲/指数读数" in market_prompt
    assert "不得再写“单位未确认”“需确认币本位/U本位”" in market_prompt
    assert "搜索线索里的机构名、法案名、产品名、政策名和标题实体不得进入正文" in news_prompt
    assert "搜索摘要里的机构名、法案名、产品名、政策名和标题实体不得进入正文" in social_prompt
    assert "不得假设用户已经持有资产或任何持仓状态" in trader_prompt
    assert "未提供真实持仓与保证金参数，无法审计合约风险" in trader_prompt
    assert "不得改写、压缩或换算上游数值和单位" in polisher_prompt


def test_crypto_prompts_do_not_turn_missing_backtests_into_core_report_gaps() -> None:
    market_prompt = _read("agents/market_analyst/prompts/CRYPTO.md")
    polisher_prompt = _read("agents/report_polisher/prompts/CRYPTO.md")

    assert "不写统计胜算、历史频率" in market_prompt
    assert "已返回的 K 线、技术指标、资金费率、OI、多空比、清算簇、AHR999" in market_prompt
    assert "可以作为当前市场结构证据" in market_prompt
    assert "不要把“没有回测/统计”写成每个指标的缺失项" in polisher_prompt
    assert "不代表当前读数不能用于市场结构分析" in polisher_prompt


def test_crypto_prompt_low_quality_phrases_are_not_seeded() -> None:
    leaky_phrases = (
        "高赔率",
        "高回报",
        "高胜率",
        "价格引力",
        "空头回补",
        "踩踏",
        "强制等待期",
        "被迫状态",
        "安全边际",
        "已被定价",
        "无需额外数据支持",
        "不对称收益",
        "1.5以下",
        "0.001%以下",
        "20亿",
        "MicroStrategy",
        "S2F",
        "数字黄金",
        "Short Squeeze",
    )
    for relative_path in CRYPTO_PROMPTS:
        prompt = _read(relative_path)
        for phrase in leaky_phrases:
            assert phrase not in prompt, relative_path
