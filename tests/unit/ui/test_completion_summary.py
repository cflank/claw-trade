from __future__ import annotations

from claw_trade.ui_backend.report_repository import ReportRepository
from claw_trade.ui_backend.summary_builder import CompletionSummaryBuilder, render_completion_summary_text


def test_completion_summary_only_extracts_from_saved_report_and_pm_conclusion() -> None:
    repo = ReportRepository()
    repo.save_succeeded_report(
        report_id="r-sum-1",
        instrument_code="BTC",
        market="CRYPTO",
        title="BTC 报告",
        pm_final_conclusion="建议分批建仓",
        markdown=(
            "# 报告\n"
            "## 核心理由\n"
            "- 成交量回升\n"
            "- 资金费率回归\n"
            "## 主要风险\n"
            "- 波动仍高\n"
        ),
    )
    builder = CompletionSummaryBuilder(repo)
    summary = builder.build_completion_summary_from_saved_report("r-sum-1", pdf_available=True)
    assert summary["reportId"] == "r-sum-1"
    assert summary["finalConclusion"] == "建议分批建仓"
    assert summary["coreReasons"] == ["成交量回升", "资金费率回归"]
    assert summary["mainRisks"] == ["波动仍高"]
    assert summary["pdfAvailable"] is True


def test_completion_summary_uses_fallback_lines_when_sections_missing() -> None:
    repo = ReportRepository()
    repo.save_succeeded_report(
        report_id="r-sum-2",
        instrument_code="AAPL",
        market="US",
        title="AAPL 报告",
        markdown="# 报告\n正文",
    )
    builder = CompletionSummaryBuilder(repo)
    summary = builder.build_completion_summary_from_saved_report("r-sum-2")
    assert summary["coreReasons"] == ["完整理由请查看报告正文。"]
    assert summary["mainRisks"] == ["主要风险请查看报告正文。"]


def test_completion_summary_extracts_real_brief_from_numbered_final_report() -> None:
    repo = ReportRepository()
    repo.save_succeeded_report(
        report_id="r-sum-3",
        instrument_code="00700.HK",
        market="HK",
        title="腾讯报告",
        markdown=(
            "# 腾讯控股投资研究报告\n\n"
            "## 八、最终结论\n\n"
            "综合以上所有分析，组合经理在审慎评估多空双方核心论据后，做出以下最终决策："
            "**卖出腾讯控股（00700.HK）**。\n\n"
            "**执行条件与核心判断：**\n\n"
            "1.  **决定性因素：技术面空头趋势明确，且未见底。** 均线系统完全空头排列，"
            "MACD于零轴下方持续死叉运行。\n\n"
            "2.  **核心逻辑“回购支撑”已被证伪。** 公司回购价格区间明显高于当前股价，"
            "无法构成交易层面的安全垫。\n\n"
            "**主要风险**：超预期宏观利好可能触发空头回补式反弹；社交情绪数据完全缺失，"
            "市场情绪方向存在不可知的不确定性。\n"
        ),
    )
    builder = CompletionSummaryBuilder(repo)

    summary = builder.build_completion_summary_from_saved_report("r-sum-3")
    rendered = render_completion_summary_text(summary)

    assert "最终结论已写入完整报告" not in rendered
    assert "完整理由请查看报告正文" not in rendered
    assert "主要风险请查看报告正文" not in rendered
    assert summary["finalConclusion"].startswith("综合以上所有分析")
    assert any("技术面空头趋势明确" in item for item in summary["coreReasons"])
    assert any("回购支撑" in item for item in summary["coreReasons"])
    assert summary["mainRisks"] == [
        "超预期宏观利好可能触发空头回补式反弹；社交情绪数据完全缺失，市场情绪方向存在不可知的不确定性。"
    ]
