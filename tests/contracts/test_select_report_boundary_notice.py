from __future__ import annotations

from pathlib import Path


def test_cn_a_report_polisher_has_conditional_select_boundary_notice() -> None:
    text = (Path("agents") / "report_polisher" / "prompts" / "CN_A.md").read_text(encoding="utf-8")

    assert "如果输入材料或运行上下文明确说明本报告由 select 候选股票触发生成" in text
    assert "select 仅表示该股票具备进一步研究价值，不代表买入建议" in text
    assert "没有明确 select 触发证据时，不要写这段声明" in text
