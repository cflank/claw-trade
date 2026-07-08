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


def test_build_report_run_request_accepts_slash_crypto_pair() -> None:
    request = build_report_run_request(
        ticker="AR/USDT",
        settings=ReportWorkflowSettings(),
        current_date="2026-05-20",
    )

    assert request.ticker == "AR/USDT"
    assert request.company_name == "Arweave"
    assert request.market == "CRYPTO"
    assert request.profile == "CRYPTO"
    assert request.currency == "USDT"
    assert request.currency_symbol == "USDT"


def test_build_report_run_request_uses_crypto_base_when_pair_name_is_unknown() -> None:
    request = build_report_run_request(
        ticker="WIF/USDT",
        settings=ReportWorkflowSettings(),
        current_date="2026-05-20",
    )

    assert request.ticker == "WIF/USDT"
    assert request.company_name == "WIF"
    assert request.market == "CRYPTO"


def test_build_report_run_request_normalizes_market_specific_tickers() -> None:
    settings = ReportWorkflowSettings()

    cn_request = build_report_run_request(ticker="SH600519", settings=settings, current_date="2026-05-20")
    hk_request = build_report_run_request(ticker="HK00700", settings=settings, current_date="2026-05-20")
    us_request = build_report_run_request(ticker="AAPL.US", settings=settings, current_date="2026-05-20")

    assert cn_request.ticker == "600519.SH"
    assert cn_request.market == "CN_A"
    assert hk_request.ticker == "00700.HK"
    assert hk_request.market == "HK"
    assert us_request.ticker == "AAPL"
    assert us_request.market == "US"
