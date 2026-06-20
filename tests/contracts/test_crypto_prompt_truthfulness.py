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
        assert "数据缺口本身不是看涨或看跌事实" in prompt or "缺数据不能支持" in prompt, relative_path
        assert "直接删除" in prompt or "删除对应" in prompt, relative_path
        assert "不要编造" in prompt or "不得补写缺失事实" in prompt, relative_path


def test_crypto_prompts_preserve_units_sources_and_position_boundaries() -> None:
    fundamental_prompt = _read("agents/fundamental_analyst/prompts/CRYPTO.md")
    market_prompt = _read("agents/market_analyst/prompts/CRYPTO.md")
    news_prompt = _read("agents/news_analyst/prompts/CRYPTO.md")
    social_prompt = _read("agents/social_analyst/prompts/CRYPTO.md")
    trader_prompt = _read("agents/trader/prompts/CRYPTO.md")
    polisher_prompt = _read("agents/report_polisher/prompts/CRYPTO.md")

    assert "AHR999 是指数/无量纲读数" in fundamental_prompt
    assert "只有媒体聚合、新闻搜索摘要、行情站标题或搜索发现时，不算可引用项目新闻" in fundamental_prompt
    assert "不输出媒体聚合标题本身或来源性质说明" in fundamental_prompt
    assert "输出只允许三部分：标题、`## 行情概览`、`## 技术结构读数`" in fundamental_prompt
    assert "标题下面如果只能写取数状态或省略原因，该标题不得出现" in fundamental_prompt
    assert "AHR999 的数值是无量纲/指数读数" in market_prompt
    assert "不得附加币本位/U本位确认句或价格单位确认句" in market_prompt
    assert "非 BTC 标的不写 BTC 专用占位句" in market_prompt
    assert "最终正文只保留已经进入论证的数据、推导和样本边界" in market_prompt
    assert "空结果解释、请求状态句、论证外类别、专属性解释和工具过程句一律整段删除" in market_prompt
    assert "无可引用材料" not in market_prompt
    assert "未返回可引用材料" not in market_prompt
    assert "非社交平台原文样本" not in market_prompt
    assert "项目专属样本" not in market_prompt
    assert "Fear & Greed 指标只能写分数、区间、来源和日期" in market_prompt
    assert "不得给情绪读数添加过往统计、资金主体、抄底窗口或下跌中继叙事" in market_prompt
    assert "若只能写取数状态或专属性解释，本节整节省略" in market_prompt
    assert "才写统计胜算、历史频率或周期极值结论" in market_prompt
    assert "必须有具体数值和可引用材料才写" in market_prompt
    assert "只列本次已返回且进入分析的指标" in market_prompt
    assert "该主题连名称都不要进入正文、覆盖表、样本边界、关键价格、场景和结论" in market_prompt
    assert "这个小节不是候选清单" in market_prompt
    assert "非 BTC 写不适用" not in market_prompt
    assert "确属当前标的不适用" not in market_prompt
    assert "搜索线索里的机构名、法案名、产品名、政策名和标题实体不得进入正文" in news_prompt
    assert "搜索摘要、媒体聚合标题、取数状态和材料过滤理由只用于内部取舍" in news_prompt
    assert "宏观背景写完立即结束" in news_prompt
    assert "解释为什么没有写项目新闻" not in news_prompt
    assert "宏观段后不得追加任何文字" in news_prompt
    assert "默认输出结构只包含标题和 `## 市场级情绪读数` 一节" in social_prompt
    assert "`市场级情绪读数` 只写分数、刻度、情绪区间、来源和样本日期；该节之后直接结束" in social_prompt
    assert "全文必须以 `市场级情绪读数` 表格最后一行收尾" in social_prompt
    assert "默认结构下全文不提扩展章节和样本覆盖情况" in social_prompt
    assert "材料未支撑的主题直接不写" in social_prompt
    assert "本次材料只有市场级情绪分数" not in social_prompt
    assert "未提供可引用" not in social_prompt
    assert "不扩展其他章节" not in social_prompt
    assert "报告结束" not in social_prompt
    assert "未返回真实社交平台原文样本" not in social_prompt
    assert "非社交平台原文样本" not in social_prompt
    assert "非专属样本" not in social_prompt
    assert "不得假设用户已经持有资产或任何持仓状态" in trader_prompt
    assert "直接删除合约处理段，不写原因" in trader_prompt
    assert "未提供真实持仓与保证金参数，无法审计合约风险" not in trader_prompt
    assert "不得改写、压缩或换算上游数值和单位" in polisher_prompt
    assert "如果数据证据边界已经返回供应字段，不得再写未返回供给与发行材料" in polisher_prompt
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
    assert "每个列出的数据项在本 turn 最多请求一次" in market_prompt
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
    assert "随后请求 `项目资料`、`估值`、`交易所净流量`、`交易所余额`、`链上`、`AHR999`、`机构产品资金流` 和 `公司新闻`" in fundamental_prompt
    assert "每个列出的数据项在本 turn 最多请求一次" in fundamental_prompt
    assert "不要因为行情、项目资料、估值或项目新闻可用，就把交易所净流量、交易所余额、链上、AHR999、机构产品资金流或其它链上数据当成已经覆盖" in fundamental_prompt
    for item in ("项目资料", "估值", "交易所净流量", "交易所余额", "链上", "AHR999", "机构产品资金流", "公司新闻"):
        assert f"`{item}`" in fundamental_prompt
    assert "随后请求 `宏观` 和 `事件日历`" in news_prompt
    assert "每个列出的数据项在本 turn 最多请求一次" in news_prompt
    for item in ("公司新闻", "宏观", "事件日历"):
        assert f"`{item}`" in news_prompt
    for prompt in (market_prompt, fundamental_prompt, news_prompt):
        assert "api_id" not in prompt


def test_crypto_prompts_do_not_turn_missing_backtests_into_core_report_gaps() -> None:
    market_prompt = _read("agents/market_analyst/prompts/CRYPTO.md")
    polisher_prompt = _read("agents/report_polisher/prompts/CRYPTO.md")

    assert "才写统计胜算、历史频率或周期极值结论" in market_prompt
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

    assert "已返回核心读数但只有粗粒度、单点、短窗口" in market_prompt
    assert "样本边界" in market_prompt
    assert "可选补充验证" in market_prompt
    assert "表格行如果只能写空结果说明、请求状态或占位符，必须整行删除" in market_prompt
    assert "不要增加论证外数据类别列表" in market_prompt
    assert "若数据证据边界或前线报告已经写明某数据项可用" in portfolio_prompt
    assert "若数据证据边界或上游报告已经写明某数据项可用" in polisher_prompt
    assert "已返回 100 档盘口时，不得写订单簿深度缺失" in polisher_prompt
    assert "已返回小时级 CVD/资金费率时，不得写 CVD/资金费率缺失" in polisher_prompt
    assert "只写已返回簇读数" in polisher_prompt
    assert "事件行只在事件日历结果给出样本时进入表格" in market_prompt
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
    research_manager_prompt = _read("agents/research_manager/prompts/CRYPTO.md")
    risk_moderator_prompt = _read("agents/risk_moderator/prompts/CRYPTO.md")
    portfolio_prompt = _read("agents/portfolio_manager/prompts/CRYPTO.md")
    polisher_prompt = _read("agents/report_polisher/prompts/CRYPTO.md")

    assert "持续观察 CVD、机构产品资金流、交易所余额等数据是否形成一致趋势" not in trader_prompt
    assert "只能写持续观察已返回并进入论证的数据类别" in trader_prompt
    assert "只能写持续观察已返回并进入论证的数据类别" in portfolio_prompt
    assert "只能写持续观察已返回并进入论证的数据类别" in polisher_prompt
    assert "直接删除真实持仓调整、合约处理和账户动作段" in polisher_prompt
    assert "删除所有只有标题没有正文的空章节或空小节" in polisher_prompt
    assert "市场级情绪不得进入“核心依据”或“最终结论”作为买入、卖出或持有理由" in polisher_prompt
    assert "不得按已持有/空仓/合约交易者拆分真实账户动作" in portfolio_prompt
    assert "缺少真实持仓参数只限制真实持仓调整" in portfolio_prompt
    assert "123 突破参考位尚未确认、没有可审计入场触发、没有目标/止损/仓位参数，都不是研究性买入方向的否决条件" in research_manager_prompt
    assert "如果你准备写“持有/观望”" in research_manager_prompt
    assert "这不是独立持有理由" in research_manager_prompt
    assert "如果研究经理把“突破未确认”“没有可执行入场条件”“没有目标/止损/仓位参数”作为不买入理由" in trader_prompt
    assert "平衡不等于默认“不买入、不卖出”" in risk_moderator_prompt
    assert "执行入场条件、目标价、止损、仓位或真实持仓参数缺失，不是“为什么不是买入”的理由" in portfolio_prompt
    assert "不得作为唯一理由把中期多头结构压成持有/观望" in portfolio_prompt
    assert "最终投资决策方向权威" in polisher_prompt
    assert "不得把组合经理的买入/有条件买入改写成持有/观望" in polisher_prompt
    assert "如果组合经理最终方向是买入或研究性买入" in polisher_prompt
    assert "不得在任何章节写“组合经理执行上选择持有/观望”" in polisher_prompt
    assert "样本边界”只写已返回材料的时间、来源、粒度、样本行数或口径" in polisher_prompt
    assert "删除所有“动作约束”“执行约束”“删除所有数字化执行参数”“真实持仓参数”" in polisher_prompt
    assert "不得写“缺乏对项目收入、基本面改善、供给/解锁压力的可引用判断”" in polisher_prompt
    bull_prompt = _read("agents/bull_researcher/prompts/CRYPTO.md")
    challenger_prompt = _read("agents/risk_challenger/prompts/CRYPTO.md")
    for prompt in (bull_prompt, challenger_prompt):
        assert "不得给情绪读数添加过往底部、资金主体行为、抄底窗口或类似叙事" in prompt


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
        assert "可以作为研究性方向证据" in prompt, relative_path
        assert "不得改写成入场、目标、止损、失效、仓位百分比或未来买卖安排" in prompt, relative_path
        assert "不得自设天数、收盘确认、成交量倍数、指标阈值或价格突破/跌破条件" in prompt, relative_path
        assert "不得概括成核心资料不足" in prompt, relative_path
        assert "不得写“维持现有持仓不动”“维持现货不动”“保留现货仓位”" in prompt, relative_path
        assert "不得把单点读数单独外推成确定性交易、资金主体意图、心理标签、排他性安全说法或确定胜率故事" in prompt, relative_path

    research_manager_prompt = _read("agents/research_manager/prompts/CRYPTO.md")
    market_prompt = _read("agents/market_analyst/prompts/CRYPTO.md")
    trader_prompt = _read("agents/trader/prompts/CRYPTO.md")
    risk_guardian_prompt = _read("agents/risk_guardian/prompts/CRYPTO.md")
    risk_moderator_prompt = _read("agents/risk_moderator/prompts/CRYPTO.md")
    portfolio_prompt = _read("agents/portfolio_manager/prompts/CRYPTO.md")
    polisher_prompt = _read("agents/report_polisher/prompts/CRYPTO.md")

    assert "您必须提供具体的价格区间" not in research_manager_prompt
    assert "价格目标或价格区间的时间范围" not in research_manager_prompt
    assert "只有市场/技术报告明确返回的支撑位" not in research_manager_prompt
    assert "只有市场/技术报告明确返回的支撑位" not in trader_prompt
    assert "只有市场/技术报告明确返回的支撑位" not in portfolio_prompt
    assert "可以作为研究性方向证据或技术参考" in trader_prompt
    assert "审计交易员计划" in portfolio_prompt
    assert "不得把组合经理的买入/有条件买入改写成持有/观望" in polisher_prompt
    assert "终稿清理优先级高于保留信息密度或保留上游措辞" in polisher_prompt
    assert "真实头寸未给出时" in polisher_prompt
    assert "不得写保留某类资产头寸" in polisher_prompt
    assert "分歧表只允许使用“证据类别 / 组合经理处理 / 可保留的真实数据”" in polisher_prompt
    assert "不要用“看涨论点（被否决）”“看跌论点（未被采纳）”这类列名" in polisher_prompt
    assert "| 指标 | 数据 | 推导 | 交易作用 | 样本边界 |" in market_prompt
    assert "| 指标 | 数据 | 推导 | 交易作用 | 样本边界 |" in polisher_prompt
    assert "保留组合经理的关键入场触发" not in polisher_prompt
    assert "若 CVD、主动买量或主动卖量已经显示 USD/USDT 等单位" in polisher_prompt
    assert "分歧章节只写组合经理采纳或不采纳的证据权重" in polisher_prompt
    assert "终稿不得把单点读数单独外推成确定性交易、资金主体意图、心理标签、排他性安全说法或确定胜率故事" in polisher_prompt
    assert "所有“冲突、分歧、风险、采纳/不采纳”表格都必须使用固定三列" in polisher_prompt
    assert "禁止使用任何会复述多空原话、冲突原文或终稿采纳原文的列名" in polisher_prompt
    assert "`证据类别` 单元格只能写简短数据类别" in polisher_prompt
    assert "不得加括号、引号、上游角色、原话摘要、指标组合、故事名或行为标签" in polisher_prompt
    assert "如果任一上游报告或数据证据边界已经给出某指标读数" in polisher_prompt
    assert "不得再写该指标没有数值" in polisher_prompt
    assert "跟踪条件和重新评估条件只能列数据类别" in polisher_prompt
    assert "不得写自设数值阈值、连续天数、条件数量、情绪分数关口、成交量门槛、价格位或指标阈值" in polisher_prompt
    assert "不得把“缺历史分位、回测、执行机制或可靠来源验证”写成读者版理由" in polisher_prompt
    assert "重新评估段只能写数据类别" in polisher_prompt
    assert "读者版正文不得出现内部来源 id" in polisher_prompt
    assert "重新评估和后续跟踪只列已返回并进入论证的数据类别" in polisher_prompt
    assert "买入、卖出、持有三种结论地位相同" in research_manager_prompt
    assert "不要为了显得可操作而自设条件、价位或阈值" in research_manager_prompt
    assert "当前读数可以作为研究性多空推理线索" in research_manager_prompt
    assert "不能单独把顶层结论推成卖出" in research_manager_prompt
    assert "必须先判断它们是否都来自同一段价格/动能结构" in research_manager_prompt
    assert "突破未确认，只能降低买入置信度" in research_manager_prompt
    assert "不得用“当前价格距离上方参考位更近、距离下方支撑或 FVG 更远”" in research_manager_prompt
    assert "不是加密资产估值模型" in research_manager_prompt
    assert "没有看到新增买盘来源，都不是卖出证据" in research_manager_prompt
    assert "这不是有效卖出理由" in research_manager_prompt
    assert "不得写“风险收益比不清晰所以不买入”" in research_manager_prompt
    assert "不得输出“等待某个参考价位突破确认后再做决策”" in research_manager_prompt
    assert "不得用“当前价格距离上方参考位更近、距离下方支撑或 FVG 更远”" in trader_prompt
    assert "POC、价值区间、FVG 和成交量密集区只是技术参考" in trader_prompt
    assert "不得输出“等待某个参考价位突破确认后再做决策”" in trader_prompt
    assert "不得用“当前价格距离上方参考位更近、距离下方支撑或 FVG 更远”" in risk_moderator_prompt
    assert "不得输出“等待某个参考价位突破确认后再做决策”" in risk_moderator_prompt
    assert "不得用“当前价格距离上方参考位更近、距离下方支撑或 FVG 更远”" in portfolio_prompt
    assert "最终裁决不得采纳该卖出链条" in portfolio_prompt
    assert "不得输出“等待某个参考价位突破确认后再做决策”" in portfolio_prompt
    assert "不得保留“不进行买入或卖出操作，等待某个参考价位突破确认/方向确认”" in polisher_prompt
    assert "估值过高、系统性获利了结压力、风险收益比不利、缺乏新增买盘来源或研究性卖出" in polisher_prompt
    assert "不能单独把交易员顶层建议推成卖出" in trader_prompt
    assert "突破未确认不是趋势破坏" in trader_prompt
    assert "不是天然否决买入或自动支持卖出" in risk_guardian_prompt
    assert "不能写灾难路径" in risk_guardian_prompt
    assert "不能因为卖出听起来更安全" in risk_moderator_prompt
    assert "不能单独把最终组合动作推成卖出" in portfolio_prompt
    assert "必须重新合并权重" in portfolio_prompt
    assert "最终裁决不得用弱信号数量覆盖趋势未破这一事实" in portfolio_prompt
    assert "平衡方案不是折中造交易条件" in risk_moderator_prompt
    assert "而不是默认不交易" in risk_moderator_prompt
    assert "整句或整段删除" in polisher_prompt
    assert "删除后若标题下没有正文，连标题一起删除" in polisher_prompt


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

    assert "已返回盘口档位时，应直接分析档位读数" in market_prompt
    assert "如果工具结果显示“买盘档数/卖盘档数”，即使只有 1 个盘口快照，也代表已返回多档盘口" in market_prompt
    assert "不得输出单点读数外推成方向性交易、资金主体意图、心理标签、排他性安全说法或确定胜率故事" in market_prompt
    assert "若要验证 CVD 背离，只能写逐笔成交、订单流或更细粒度深度变化作为可选补充验证" in market_prompt
    assert "已返回盘口档位时，不得写订单簿数据如果可用" in polisher_prompt
    assert "已返回核心数据时，禁止把它概括成资料没有返回的总表或核心资料不足" in polisher_prompt
