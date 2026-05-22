from __future__ import annotations

from datetime import date, timedelta

from claw_trade.config.report_workflow_settings import ReportWorkflowSettings
from claw_trade.workflow.report_request_factory import build_report_run_request


def test_build_report_run_request_uses_script_defaults_for_crypto_btc() -> None:
    request = build_report_run_request(
        ticker="BTC",
        settings=ReportWorkflowSettings(),
        current_date="2026-05-20",
    )

    assert request.ticker == "BTC"
    assert request.company_name == "Bitcoin"
    assert request.market == "CRYPTO"
    assert request.profile == "CRYPTO"
    assert request.currency == "USDT"
    assert request.currency_symbol == "USDT"
    assert request.current_date == "2026-05-20"
    assert request.end_date == "2026-05-20"
    assert date.fromisoformat(request.end_date) - date.fromisoformat(request.start_date) == timedelta(days=365)
