from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_PYTHON_ROOT = REPO_ROOT / "openclaw_plugins" / "claw-trade-frontline-tools" / "python"
SRC_ROOT = REPO_ROOT / "src"
if str(SHARED_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(SHARED_PYTHON_ROOT))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from claw_trade.providers.tushare_client import TushareClientConfigError  # noqa: E402
from frontline_data_pack.models import ProviderQuery, ProviderSpec  # noqa: E402
from frontline_data_pack.provider_executor import execute_provider_attempt  # noqa: E402
from frontline_data_pack.providers_tushare_news import call_tushare_anns_d  # noqa: E402
from frontline_data_pack.runtime_context import ToolRuntimeContext  # noqa: E402


QUERY_FINGERPRINT = "sha256:" + ("e" * 64)


def test_tushare_anns_d_maps_announcement_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakePro:
        def anns_d(self, **kwargs: object) -> pd.DataFrame:
            _ = kwargs
            return pd.DataFrame(
                [
                    {
                        "ann_date": "20260508",
                        "title": "贵州茅台：董事会决议公告",
                        "name": "贵州茅台",
                        "url": "https://example.com/ann/1",
                        "content": "公告内容摘要",
                    }
                ]
            )

    monkeypatch.setattr(
        "frontline_data_pack.providers_tushare_news.create_tushare_pro",
        lambda: _FakePro(),
    )

    spec = _spec()
    result = execute_provider_attempt(
        spec,
        _query(),
        _context(),
        call_registry={(spec.provider, spec.endpoint): call_tushare_anns_d},
    )

    assert result.attempt.status == "success"
    assert result.attempt.accepted_count == 1
    assert result.normalized_rows[0]["title"] == "贵州茅台：董事会决议公告"
    assert result.normalized_rows[0]["publish_time"] == "2026-05-08"


def test_tushare_anns_d_missing_token_is_explicit_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "frontline_data_pack.providers_tushare_news.create_tushare_pro",
        lambda: (_ for _ in ()).throw(TushareClientConfigError("TUSHARE_TOKEN missing")),
    )

    spec = _spec()
    result = execute_provider_attempt(
        spec,
        _query(),
        _context(),
        call_registry={(spec.provider, spec.endpoint): call_tushare_anns_d},
    )
    assert result.attempt.status == "error"
    assert result.attempt.error_code == "PROVIDER_KEY_MISSING"


def _spec() -> ProviderSpec:
    return ProviderSpec(
        domain="news",
        priority="P2",
        provider="tushare",
        endpoint="anns_d",
        role="announcement",
        enabled=True,
        mode="remote",
        timeout_ms=1000,
        required_for_complete=False,
        query_parameters=[],
    )


def _query() -> ProviderQuery:
    return ProviderQuery(
        market="CN_A",
        ticker="600519.SH",
        company_name="贵州茅台",
        industry="白酒",
        start_date="2026-05-01",
        end_date="2026-05-09",
        adjust=None,
        query_fingerprint=QUERY_FINGERPRINT,
    )


def _context() -> ToolRuntimeContext:
    return ToolRuntimeContext(
        run_id="run-tushare-news",
        stage="frontline",
        worker_id="news_analyst",
        call_id="call-tushare-news",
        dispatch_id="dispatch-tushare-news",
        tool_name="news_news_data_pack",
        evidence_root="/tmp/evidence",
        current_time="2026-05-09T12:00:00Z",
        current_date="2026-05-09",
    )
