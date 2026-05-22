from __future__ import annotations

from claw_trade.ui_backend.chart_evidence import get_report_chart_evidence


def test_chart_evidence_uses_real_aggregated_inputs() -> None:
    result = get_report_chart_evidence(
        "r-chart-1",
        markdown="![K线图](k.png)\n![RSI](rsi.png)",
        chart_assets=[
            {"title": "K线图", "chartType": "kline", "status": "ready"},
            {"title": "RSI", "chartType": "rsi", "status": "failed", "reason": "指标计算失败"},
        ],
    )
    assert result["reportId"] == "r-chart-1"
    assert result["summary"] == "partial"
    by_title = {item["title"]: item for item in result["items"]}
    assert by_title["K线图"]["status"] == "ready"
    assert by_title["RSI"]["status"] == "failed"
    assert "失败" in by_title["RSI"]["userMessage"]


def test_chart_evidence_missing_reason_from_data_gap() -> None:
    result = get_report_chart_evidence(
        "r-chart-2",
        data_gaps=[{"title": "收益曲线", "reason": "数据源超时"}],
    )
    assert result["summary"] == "missing"
    assert result["items"][0]["title"] == "收益曲线"
    assert result["items"][0]["status"] in {"missing", "failed"}
    assert "数据源超时" in result["items"][0]["userMessage"]
