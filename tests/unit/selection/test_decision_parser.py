from __future__ import annotations

import pytest
from claw_trade.selection.controller import (
    _extract_allowed_ticker_companies_from_summary,
    _parse_and_validate_selection_decision,
    _render_selection_reader_chat_message,
)
from claw_trade.selection.models import DecisionTicker, SelectionDecision


def test_parse_selection_decision_rejects_missing_sections_and_ambiguous_lines() -> None:
    missing_section = "\n".join(
        [
            "进入 /report:",
            "- 600519.SH | 贵州茅台 | 经营质量与现金流稳定。",
            "放弃:",
            "- 300750.SZ | 宁德时代 | 当前证据链分歧较大。",
        ]
    )
    missing_result = _parse_and_validate_selection_decision(
        pm_raw_text=missing_section,
        workflow_run_id="wf-1",
        allowed_tickers=frozenset({"600519.SH", "300750.SZ"}),
        approved_material_id="selection-pm-decision-wf-1",
    )
    assert missing_result.decision is None
    assert missing_result.invalid_reason == "missing_required_sections"

    ambiguous_watch = "\n".join(
        [
            "进入 /report:",
            "- 600519.SH | 贵州茅台 | 经营质量与现金流稳定。",
            "观察:",
            "- 000858.SZ 五粮液 继续跟踪",
            "放弃:",
            "- 300750.SZ | 宁德时代 | 当前证据链分歧较大。",
        ]
    )
    ambiguous_result = _parse_and_validate_selection_decision(
        pm_raw_text=ambiguous_watch,
        workflow_run_id="wf-1",
        allowed_tickers=frozenset({"600519.SH", "000858.SZ", "300750.SZ"}),
        approved_material_id="selection-pm-decision-wf-1",
    )
    assert ambiguous_result.decision is None
    assert ambiguous_result.blocked_reason == "ambiguous_watch_section_requires_human_review"


def test_parse_selection_decision_allows_trade_advice_like_language_without_expression_guard() -> None:
    with_trade_advice = "\n".join(
        [
            "进入 /report:",
            "- 600519.SH | 贵州茅台 | 建议买入并尽快推进报告。",
            "观察:",
            "- 000858.SZ | 五粮液 | 等待后续数据确认。",
            "放弃:",
            "- 300750.SZ | 宁德时代 | 当前证据链分歧较大。",
        ]
    )
    result = _parse_and_validate_selection_decision(
        pm_raw_text=with_trade_advice,
        workflow_run_id="wf-2",
        allowed_tickers=frozenset({"600519.SH", "000858.SZ", "300750.SZ"}),
        approved_material_id="selection-pm-decision-wf-2",
    )
    assert result.invalid_reason is None
    assert result.decision is not None
    assert [item.ticker for item in result.decision.enter_report] == ["600519.SH"]


def test_parse_selection_decision_rejects_ticker_company_mismatch() -> None:
    mismatched_company = "\n".join(
        [
            "进入 /report:",
            "- 600519.SH | 五粮液 | 经营质量与现金流稳定。",
            "观察:",
            "- 000858.SZ | 贵州茅台 | 等待后续数据确认。",
            "放弃:",
            "- 300750.SZ | 宁德时代 | 当前证据链分歧较大。",
        ]
    )
    result = _parse_and_validate_selection_decision(
        pm_raw_text=mismatched_company,
        workflow_run_id="wf-company-mismatch",
        allowed_tickers=frozenset({"600519.SH", "000858.SZ", "300750.SZ"}),
        allowed_ticker_companies={
            "600519.SH": "贵州茅台",
            "000858.SZ": "五粮液",
            "300750.SZ": "宁德时代",
        },
        approved_material_id="selection-pm-decision-wf-company-mismatch",
    )
    assert result.decision is None
    assert result.invalid_reason == "ticker_company_mismatch:600519.SH:expected=贵州茅台:actual=五粮液"


def test_parse_selection_decision_canonicalizes_explicit_ticker_correction() -> None:
    corrected_output = "\n".join(
        [
            "进入 /report:",
            "- 无",
            "观察:",
            "- 600519.SH | 贵州茅台 | 继续观察。",
            "放弃:",
            "- 603645.SH | 金安国纪 | 注：股票代码应为002636.SZ。波动较大，暂不继续。",
        ]
    )
    result = _parse_and_validate_selection_decision(
        pm_raw_text=corrected_output,
        workflow_run_id="wf-explicit-correction",
        allowed_tickers=frozenset({"600519.SH", "002636.SZ"}),
        allowed_ticker_companies={
            "600519.SH": "贵州茅台",
            "002636.SZ": "金安国纪",
        },
        approved_material_id="selection-pm-decision-wf-explicit-correction",
    )

    assert result.invalid_reason is None
    assert result.decision is not None
    assert [item.ticker for item in result.decision.reject] == ["002636.SZ"]


def test_parse_selection_decision_still_rejects_uncorrected_out_of_set_ticker() -> None:
    uncorrected_output = "\n".join(
        [
            "进入 /report:",
            "- 无",
            "观察:",
            "- 600519.SH | 贵州茅台 | 继续观察。",
            "放弃:",
            "- 603645.SH | 金安国纪 | 波动较大，暂不继续。",
        ]
    )
    result = _parse_and_validate_selection_decision(
        pm_raw_text=uncorrected_output,
        workflow_run_id="wf-uncorrected-out-of-set",
        allowed_tickers=frozenset({"600519.SH", "002636.SZ"}),
        allowed_ticker_companies={
            "600519.SH": "贵州茅台",
            "002636.SZ": "金安国纪",
        },
        approved_material_id="selection-pm-decision-wf-uncorrected-out-of-set",
    )

    assert result.decision is None
    assert result.invalid_reason == "ticker_not_in_allowed_set:603645.SH"


def test_parse_selection_decision_supplements_missing_final_row_from_group_table() -> None:
    manager_output = "\n".join(
        [
            "### 分组与优先级",
            "",
            "| 分组语义 | 代码 | 名称 | 核心理由 |",
            "|:---|:---|:---|:---|",
            "| **优先进入组合评审** | 600519.SH | 贵州茅台 | 经营质量与现金流稳定。 |",
            "| **继续观察** | 000858.SZ | 五粮液 | 还需后续财报确认。 |",
            "| | 301458.SZ | 钧崴电子 | 需要观察其断板后的承接力度和量价行为。 |",
            "| **暂不继续** | 300750.SZ | 宁德时代 | 当前证据链分歧较大。 |",
            "",
            "进入 /report:",
            "- 600519.SH | 贵州茅台 | 经营质量与现金流稳定。",
            "观察:",
            "- 000858.SZ | 五粮液 | 还需后续财报确认。",
            "放弃:",
            "- 300750.SZ | 宁德时代 | 当前证据链分歧较大。",
        ]
    )
    result = _parse_and_validate_selection_decision(
        pm_raw_text=manager_output,
        workflow_run_id="wf-table-supplement",
        allowed_tickers=frozenset({"600519.SH", "000858.SZ", "301458.SZ", "300750.SZ"}),
        allowed_ticker_companies={
            "600519.SH": "贵州茅台",
            "000858.SZ": "五粮液",
            "301458.SZ": "钧崴电子",
            "300750.SZ": "宁德时代",
        },
        approved_material_id="selection-pm-decision-wf-table-supplement",
    )

    assert result.invalid_reason is None
    assert result.decision is not None
    assert [item.ticker for item in result.decision.watch] == ["000858.SZ", "301458.SZ"]


def test_parse_selection_decision_supplements_from_approved_manager_material() -> None:
    pm_final_output = "\n".join(
        [
            "进入 /report:",
            "- 600519.SH | 贵州茅台 | 经营质量与现金流稳定。",
            "观察:",
            "- 000858.SZ | 五粮液 | 还需后续财报确认。",
            "放弃:",
            "- 300750.SZ | 宁德时代 | 当前证据链分歧较大。",
        ]
    )
    approved_manager_material = "\n".join(
        [
            "| 分组语义 | 代码 | 名称 | 核心理由 |",
            "|:---|:---|:---|:---|",
            "| **继续观察** | 301458.SZ | 钧崴电子 | 需要观察其断板后的承接力度和量价行为。 |",
        ]
    )
    result = _parse_and_validate_selection_decision(
        pm_raw_text=pm_final_output,
        supplemental_decision_texts=(approved_manager_material,),
        workflow_run_id="wf-manager-supplement",
        allowed_tickers=frozenset({"600519.SH", "000858.SZ", "301458.SZ", "300750.SZ"}),
        allowed_ticker_companies={
            "600519.SH": "贵州茅台",
            "000858.SZ": "五粮液",
            "301458.SZ": "钧崴电子",
            "300750.SZ": "宁德时代",
        },
        approved_material_id="selection-pm-decision-wf-manager-supplement",
    )

    assert result.invalid_reason is None
    assert result.decision is not None
    assert [item.ticker for item in result.decision.watch] == ["000858.SZ", "301458.SZ"]


def test_parse_selection_decision_supplements_missing_row_from_grouped_narrative() -> None:
    pm_output = "\n".join(
        [
            "## 综合判断",
            "### 继续观察",
            "**4. 长川科技 (300604.SZ)** - RPS趋势强度与流动性表现良好，短线爆发力偏弱，先继续观察。",
            "",
            "进入 /report:",
            "- 600519.SH | 贵州茅台 | 经营质量与现金流稳定。",
            "观察:",
            "- 000858.SZ | 五粮液 | 还需后续财报确认。",
            "放弃:",
            "- 300750.SZ | 宁德时代 | 当前证据链分歧较大。",
        ]
    )
    result = _parse_and_validate_selection_decision(
        pm_raw_text=pm_output,
        workflow_run_id="wf-narrative-supplement",
        allowed_tickers=frozenset({"600519.SH", "000858.SZ", "300604.SZ", "300750.SZ"}),
        allowed_ticker_companies={
            "600519.SH": "贵州茅台",
            "000858.SZ": "五粮液",
            "300604.SZ": "长川科技",
            "300750.SZ": "宁德时代",
        },
        approved_material_id="selection-pm-decision-wf-narrative-supplement",
    )

    assert result.invalid_reason is None
    assert result.decision is not None
    assert [item.ticker for item in result.decision.watch] == ["000858.SZ", "300604.SZ"]


def test_candidate_summary_allowed_tickers_include_bj_market() -> None:
    summary_md = "\n".join(
        [
            "| 排名 | 股票代码 | 股票名称 | 行业 | 总分 |",
            "| --- | --- | --- | --- | ---: |",
            "| 1 | 603045.SH | 福达合金 | - | 60.55 |",
            "| 2 | 920438.BJ | 戈碧迦 | - | 58.00 |",
        ]
    )

    allowed = _extract_allowed_ticker_companies_from_summary(summary_md)

    assert allowed["603045.SH"] == "福达合金"
    assert allowed["920438.BJ"] == "戈碧迦"


def test_parse_selection_decision_accepts_bj_candidate_from_allowed_set() -> None:
    decision_text = "\n".join(
        [
            "进入 /report:",
            "- 603045.SH | 福达合金 | 趋势强度较高。",
            "观察:",
            "- 920438.BJ | 戈碧迦 | 北交所标的，先观察流动性和数据覆盖。",
            "放弃:",
            "- 600367.SH | 红星发展 | 短期涨幅过大。",
        ]
    )
    result = _parse_and_validate_selection_decision(
        pm_raw_text=decision_text,
        workflow_run_id="wf-bj",
        allowed_tickers=frozenset({"603045.SH", "920438.BJ", "600367.SH"}),
        allowed_ticker_companies={
            "603045.SH": "福达合金",
            "920438.BJ": "戈碧迦",
            "600367.SH": "红星发展",
        },
        approved_material_id="selection-pm-decision-wf-bj",
    )

    assert result.invalid_reason is None
    assert result.decision is not None
    assert [item.ticker for item in result.decision.watch] == ["920438.BJ"]


def test_parse_selection_decision_allows_plain_language_hold_context_without_trade_advice_template() -> None:
    plain_language_hold_context = "\n".join(
        [
            "进入 /report:",
            "- 600519.SH | 贵州茅台 | 经营质量与现金流稳定，值得进入深度报告验证。",
            "观察:",
            "- 000858.SZ | 五粮液 | 在行业集中风险突出的前提下，无法证明同时持有两支的合理性，需待补充证据后重新评估。",
            "放弃:",
            "- 300750.SZ | 宁德时代 | 当前证据链分歧较大。",
        ]
    )
    result = _parse_and_validate_selection_decision(
        pm_raw_text=plain_language_hold_context,
        workflow_run_id="wf-hold-context",
        allowed_tickers=frozenset({"600519.SH", "000858.SZ", "300750.SZ"}),
        approved_material_id="selection-pm-decision-wf-hold-context",
    )
    assert result.invalid_reason is None
    assert result.decision is not None
    assert [item.ticker for item in result.decision.watch] == ["000858.SZ"]


@pytest.mark.parametrize(
    ("watch_line", "expected_ticker"),
    [
        ("- 000858.SZ | 五粮液 | 投资建议：买入。", "000858.SZ"),
        ("- 000858.SZ | 五粮液 | 最终交易建议：持有。", "000858.SZ"),
        ("- 000858.SZ | 五粮液 | 目标价：165 元。", "000858.SZ"),
        ("- 000858.SZ | 五粮液 | 止损价：138 元。", "000858.SZ"),
        ("- 000858.SZ | 五粮液 | 仓位：20%。", "000858.SZ"),
        ("- 000858.SZ | 五粮液 | 交易计划：回调 5% 分批建仓。", "000858.SZ"),
    ],
)
def test_parse_selection_decision_allows_trade_advice_templates_when_structure_is_valid(
    watch_line: str,
    expected_ticker: str,
) -> None:
    with_trade_template = "\n".join(
        [
            "进入 /report:",
            "- 600519.SH | 贵州茅台 | 经营质量与现金流稳定。",
            "观察:",
            watch_line,
            "放弃:",
            "- 300750.SZ | 宁德时代 | 当前证据链分歧较大。",
        ]
    )
    result = _parse_and_validate_selection_decision(
        pm_raw_text=with_trade_template,
        workflow_run_id="wf-trade-template",
        allowed_tickers=frozenset({"600519.SH", "000858.SZ", "300750.SZ"}),
        approved_material_id="selection-pm-decision-wf-trade-template",
    )
    assert result.invalid_reason is None
    assert result.decision is not None
    assert [item.ticker for item in result.decision.watch] == [expected_ticker]


def test_parse_selection_decision_keeps_full_ticker_when_row_has_no_bullet_prefix() -> None:
    plain_rows = "\n".join(
        [
            "进入 /report:",
            "600519.SH | 贵州茅台 | 经营质量与现金流稳定。",
            "观察:",
            "000858.SZ | 五粮液 | 等待后续数据确认。",
            "放弃:",
            "300750.SZ | 宁德时代 | 当前证据链分歧较大。",
        ]
    )
    result = _parse_and_validate_selection_decision(
        pm_raw_text=plain_rows,
        workflow_run_id="wf-plain",
        allowed_tickers=frozenset({"600519.SH", "000858.SZ", "300750.SZ"}),
        approved_material_id="selection-pm-decision-wf-plain",
    )
    assert result.invalid_reason is None
    assert result.decision is not None
    assert [item.ticker for item in result.decision.enter_report] == ["600519.SH"]
    assert [item.ticker for item in result.decision.watch] == ["000858.SZ"]
    assert [item.ticker for item in result.decision.reject] == ["300750.SZ"]


def test_selection_reader_message_only_contains_three_buckets() -> None:
    decision = SelectionDecision(
        select_workflow_run_id="wf-3",
        enter_report=(DecisionTicker(ticker="600519.SH", company_name="贵州茅台", rationale_excerpt="现金流稳定"),),
        watch=(DecisionTicker(ticker="000858.SZ", company_name="五粮液", rationale_excerpt="等待财报确认"),),
        reject=(DecisionTicker(ticker="300750.SZ", company_name="宁德时代", rationale_excerpt="证据链分歧"),),
        report_questions=None,
        source_summary=None,
        approved_material_id="selection-pm-decision-wf-3",
    )

    rendered = _render_selection_reader_chat_message(decision)

    assert "不会自动启动 `/report`" in rendered
    assert "`/select` 是候选研究池，不是买入建议" in rendered
    assert "完整 `/report` 的组合经理结论为准" in rendered
    assert "进入 `/report`：" in rendered
    assert "观察：" in rendered
    assert "放弃：" in rendered
