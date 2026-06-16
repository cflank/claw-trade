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


def _without_negative_contract_lines(prompt: str) -> str:
    negative_markers = ("不得", "不要", "禁止", "不能", "改写为", "统一改写")
    return "\n".join(
        line for line in prompt.splitlines() if not any(marker in line for marker in negative_markers)
    )


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
        positive_prompt = _without_negative_contract_lines(prompt)
        for phrase in banned_phrases:
            assert phrase not in positive_prompt, relative_path


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
    assert "不要用括号、举例或“如……”列出具体交易所、监管机构、产品、项目、平台或机构名称" in news_prompt
    assert "搜索摘要里的机构名、法案名、产品名、政策名和标题实体不得进入正文" in social_prompt
    assert "不要用括号、举例或“如……”列出具体社交平台、社群、交易所、机构、项目或参与者名称" in social_prompt
    assert "不得假设用户已经持有资产或任何持仓状态" in trader_prompt
    assert "未提供真实持仓与保证金参数，无法审计合约风险" in trader_prompt
    assert "不得改写、压缩或换算上游数值和单位" in polisher_prompt
    assert "如果数据证据边界已经返回供应字段，不得再写“未返回供给与发行材料”" in polisher_prompt
    assert "当前快照显示二者相等；未返回供给与发行材料" not in polisher_prompt
    assert "成交量倍数、连续 K 线数量、连续天数" in polisher_prompt
    assert "1.5倍成交量" not in polisher_prompt
    assert "5个交易日" not in polisher_prompt
    assert "合约计价结构只写“币本位”和“U 本位”" in polisher_prompt
    assert "布特" not in polisher_prompt


def test_crypto_frontline_prompts_request_required_detail_needs() -> None:
    market_prompt = _read("agents/market_analyst/prompts/CRYPTO.md")
    fundamental_prompt = _read("agents/fundamental_analyst/prompts/CRYPTO.md")
    news_prompt = _read("agents/news_analyst/prompts/CRYPTO.md")

    assert "随后必须继续请求本报告需要的核心细项" in market_prompt
    assert "不要因为清算、交易所净流量或交易所余额有数据" in market_prompt
    assert "就把清算地图、CVD、AHR999 或其它链上数据当成已经覆盖" in market_prompt
    for item in (
        "盘口",
        "资金费率",
        "OI",
        "多空比",
        "清算",
        "清算地图",
        "CVD",
        "交易所净流量",
        "交易所余额",
        "链上",
        "AHR999",
        "宏观",
        "事件日历",
        "社交情绪",
    ):
        assert f"`{item}`" in market_prompt
    assert "随后请求 `项目资料`、`估值`、`交易所净流量`、`交易所余额`、`链上`、`AHR999`、`ETF资金流` 和 `公司新闻`" in fundamental_prompt
    assert "不要因为行情、项目资料、估值或项目新闻可用，就把交易所净流量、交易所余额、链上、AHR999、ETF 资金流或其它链上数据当成已经覆盖" in fundamental_prompt
    for item in ("项目资料", "估值", "交易所净流量", "交易所余额", "链上", "AHR999", "ETF资金流", "公司新闻"):
        assert f"`{item}`" in fundamental_prompt
    assert "随后请求 `宏观` 和 `事件日历`" in news_prompt
    for item in ("公司新闻", "宏观", "事件日历"):
        assert f"`{item}`" in news_prompt
    for prompt in (market_prompt, fundamental_prompt, news_prompt):
        assert "api_id" not in prompt


def test_crypto_prompts_do_not_turn_missing_backtests_into_core_report_gaps() -> None:
    market_prompt = _read("agents/market_analyst/prompts/CRYPTO.md")
    polisher_prompt = _read("agents/report_polisher/prompts/CRYPTO.md")

    assert "不写统计胜算、历史频率" in market_prompt
    assert "已返回的 K 线、技术指标、资金费率、OI、多空比、清算簇、AHR999" in market_prompt
    assert "可以作为当前市场结构证据" in market_prompt
    assert "不要把“没有回测/统计”写成每个指标的缺失项" in polisher_prompt
    assert "不代表当前读数不能用于市场结构分析" in polisher_prompt


def test_crypto_prompts_do_not_seed_data_recovery_language_for_available_core_data() -> None:
    banned_phrases = (
        "需要恢复的数据类别",
        "等待数据恢复",
        "数据恢复后",
        "待数据恢复",
        "数据层恢复",
        "恢复数据源",
        "需要恢复和观察",
        "数据类别恢复",
        "可靠数据恢复",
        "未恢复",
    )
    for relative_path in CRYPTO_PROMPTS:
        prompt = _read(relative_path)
        positive_prompt = _without_negative_contract_lines(prompt)
        for phrase in banned_phrases:
            assert phrase not in positive_prompt, relative_path

    market_prompt = _read("agents/market_analyst/prompts/CRYPTO.md")
    portfolio_prompt = _read("agents/portfolio_manager/prompts/CRYPTO.md")
    polisher_prompt = _read("agents/report_polisher/prompts/CRYPTO.md")

    assert "已返回核心读数但缺少更细粒度" in market_prompt
    assert "样本限制" in market_prompt
    assert "可选补充验证" in market_prompt
    assert "若数据证据边界或前线报告已经写明某数据项可用" in portfolio_prompt
    assert "若数据证据边界或上游报告已经写明某数据项可用" in polisher_prompt
    assert "已返回 100 档盘口时，不得写订单簿深度缺失" in polisher_prompt
    assert "已返回小时级 CVD/资金费率时，不得写 CVD/资金费率缺失" in polisher_prompt
    assert "本次样本未显示上方密集簇" in polisher_prompt
    assert "不得写事件日历缺失" in polisher_prompt
    assert "缺失项也要入表" not in polisher_prompt


def test_crypto_prompts_do_not_seed_self_invented_holding_or_window_conditions() -> None:
    banned_phrases = (
        "连续7日",
        "连续3日",
        "连续5日",
        "连续N日",
        "连续小时级净买量",
        "至少一周",
        "假设持币者",
        "假设空仓者",
        "持币者不做调整",
        "空仓者继续观望",
        "对于已持有现货",
        "对于空仓",
        "空仓者",
        "具体执行价位",
        "具体执行价位需等待",
        "至少1次日线收盘",
        "单周以上",
        "1-2周",
        "时间止损",
        "建立初始仓位",
        "放量突破并站稳",
    )
    for relative_path in CRYPTO_PROMPTS:
        prompt = _read(relative_path)
        for phrase in banned_phrases:
            assert phrase not in prompt, relative_path

    trader_prompt = _read("agents/trader/prompts/CRYPTO.md")
    portfolio_prompt = _read("agents/portfolio_manager/prompts/CRYPTO.md")
    polisher_prompt = _read("agents/report_polisher/prompts/CRYPTO.md")

    assert "持续观察 CVD、ETF 资金流、交易所余额等数据是否形成一致趋势" in trader_prompt
    assert "持续观察 CVD、ETF 资金流、交易所余额等数据是否形成一致趋势" in portfolio_prompt
    assert "持续观察相关数据是否形成一致趋势" in polisher_prompt
    assert "未提供真实持仓参数，无法给出真实持仓调整" in polisher_prompt
    assert "不得按已持有/空仓/合约交易者拆分动作" in portfolio_prompt
    assert "当前没有入场、目标、止损或执行价位" in portfolio_prompt
    assert "最终投资决策方向权威" in polisher_prompt
    assert "不得为了保留组合经理原文而保留未经数据支持的仓位" in polisher_prompt


def test_crypto_downstream_prompts_keep_execution_boundaries_near_top() -> None:
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
        assert "高优先级输出边界" in prompt, relative_path
        assert "只能写技术参考" in prompt, relative_path
        assert "不得改写成入场、目标、止损、失效、仓位百分比或未来买卖安排" in prompt, relative_path
        assert "不得自设天数、收盘确认、成交量倍数、指标阈值或价格突破/跌破条件" in prompt, relative_path
        assert "不得概括成核心资料不足" in prompt, relative_path
        assert "不得写“维持现有持仓不动”“维持现货不动”“保留现货仓位”" in prompt, relative_path
        assert "不得输出单点读数外推成方向性交易、资金主体意图、心理标签、排他性安全说法或确定胜率故事" in prompt, relative_path

    research_manager_prompt = _read("agents/research_manager/prompts/CRYPTO.md")
    market_prompt = _read("agents/market_analyst/prompts/CRYPTO.md")
    trader_prompt = _read("agents/trader/prompts/CRYPTO.md")
    portfolio_prompt = _read("agents/portfolio_manager/prompts/CRYPTO.md")
    polisher_prompt = _read("agents/report_polisher/prompts/CRYPTO.md")

    assert "您必须提供具体的价格区间" not in research_manager_prompt
    assert "价格目标或价格区间的时间范围" not in research_manager_prompt
    assert "只有市场/技术报告明确返回的支撑位" not in research_manager_prompt
    assert "只有市场/技术报告明确返回的支撑位" not in trader_prompt
    assert "只有市场/技术报告明确返回的支撑位" not in portfolio_prompt
    assert "市场/技术报告返回的支撑位、压力位、成交量密集区或清算密集区默认只是技术参考" in trader_prompt
    assert "审计交易员计划" in portfolio_prompt
    assert "不得为了保留组合经理原文而保留未经数据支持的仓位" in polisher_prompt
    assert "终稿清理优先级高于保留信息密度或保留上游措辞" in polisher_prompt
    assert "真实头寸未给出时" in polisher_prompt
    assert "不得写保留某类资产头寸" in polisher_prompt
    assert "分歧表只允许使用“被否决论点类型 / 不采纳理由 / 可保留的真实数据 / 需要继续验证的数据类别”" in polisher_prompt
    assert "不要用“看涨论点（被否决）”“看跌论点（未被采纳）”这类列名" in polisher_prompt
    assert "| 指标 | 数据 | 推导 | 交易作用 | 资料限制/重评依据 |" in market_prompt
    assert "| 指标 | 数据 | 推导 | 交易作用 | 资料限制/重评依据 |" in polisher_prompt
    assert "保留组合经理的关键入场触发" not in polisher_prompt
    assert "若 CVD、主动买量或主动卖量已经显示 USD/USDT 等单位" in polisher_prompt
    assert "分歧章节只写被否决论点的类型和不采纳理由" in polisher_prompt
    assert "终稿不得输出单点读数外推成方向性交易、资金主体意图、心理标签、排他性安全说法或确定胜率故事" in polisher_prompt
    assert "所有“冲突、分歧、风险、采纳/不采纳”表格都必须使用固定四列" in polisher_prompt
    assert "禁止使用任何会复述多空原话、冲突原文或终稿采纳原文的列名" in polisher_prompt
    assert "`被否决论点类型` 单元格只能逐字使用以下三类之一" in polisher_prompt
    assert "不得加括号、引号、上游角色、原话摘要、指标组合、故事名或行为标签" in polisher_prompt
    assert "如果任一上游报告或数据证据边界已经给出某指标读数" in polisher_prompt
    assert "不得再写该指标没有数值" in polisher_prompt
    assert "跟踪条件和重新评估条件只能列数据类别" in polisher_prompt
    assert "不得写自设数值阈值、连续天数、条件数量、情绪分数关口、成交量门槛、价格位或指标阈值" in polisher_prompt
    assert "不采纳理由只写“缺历史分位、回测、执行机制或可靠来源验证”" in polisher_prompt
    assert "重新评估段只能写数据类别" in polisher_prompt
    assert "读者版正文不得出现内部来源 id" in polisher_prompt
    assert "重新评估和后续跟踪只列数据类别" in polisher_prompt
    assert "持有/观望本身是明确裁决" in research_manager_prompt
    assert "不要为了显得可操作而自设条件、价位或阈值" in research_manager_prompt
    risk_moderator_prompt = _read("agents/risk_moderator/prompts/CRYPTO.md")
    assert "平衡方案不是折中造交易条件" in risk_moderator_prompt
    assert "等待数据类别补充验证后重新评估" in risk_moderator_prompt


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
        "多头陷阱",
        "空头挤压",
        "接盘",
        "大户派发",
    )
    for relative_path in CRYPTO_PROMPTS:
        prompt = _read(relative_path)
        positive_prompt = _without_negative_contract_lines(prompt)
        for phrase in leaky_phrases:
            assert phrase not in positive_prompt, relative_path


def test_crypto_prompts_do_not_turn_returned_depth_or_core_data_into_missing_data() -> None:
    banned_phrases = (
        "数据缺口补足",
        "关键数据缺口",
        "核心数据缺口",
        "数据缺口未补充",
        "数据缺口风险",
        "缺失数据",
        "订单簿数据：如果可用",
        "如果可用，用于验证",
    )
    for relative_path in CRYPTO_PROMPTS:
        prompt = _read(relative_path)
        for phrase in banned_phrases:
            assert phrase not in prompt, relative_path

    market_prompt = _read("agents/market_analyst/prompts/CRYPTO.md")
    polisher_prompt = _read("agents/report_polisher/prompts/CRYPTO.md")

    assert "已返回盘口档位时，不得写订单簿数据缺失或如果可用" in market_prompt
    assert "如果工具结果显示“买盘档数/卖盘档数”，即使只有 1 个盘口快照，也代表已返回多档盘口" in market_prompt
    assert "不得输出单点读数外推成方向性交易、资金主体意图、心理标签、排他性安全说法或确定胜率故事" in market_prompt
    assert "若要验证 CVD 背离，只能写逐笔成交、订单流或更细粒度深度变化作为可选补充验证" in market_prompt
    assert "已返回盘口档位时，不得写订单簿数据如果可用" in polisher_prompt
    assert "已返回核心数据时，禁止把它概括成资料没有返回的总表或核心资料不足" in polisher_prompt
