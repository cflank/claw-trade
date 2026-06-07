from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
from claw_trade.data_gateway import agent_tools
from claw_trade.data_gateway.models import DataResult, DataResultStatus


class _FakeAPI:
    def __init__(self, results: list[DataResult]) -> None:
        self.results = results
        self.requests = []

    def get_data(self, request):  # type: ignore[no-untyped-def]
        self.requests.append(request)
        return self.results[0]

    def get_data_batch(self, requests):  # type: ignore[no-untyped-def]
        self.requests.extend(requests)
        return self.results


def _ready(request_id: str, rows: list[dict[str, object]]) -> DataResult:
    return DataResult(
        request_id=request_id,
        status=DataResultStatus.READY,
        rows=tuple(rows),
        dataset_refs=(f"dataset:{request_id}",),
        as_of=datetime(2026, 6, 4, tzinfo=UTC),
    )


def test_agent_price_rows_use_data_api_only(monkeypatch) -> None:
    api = _FakeAPI(
        [
            _ready(
                "agent:market:daily_bar:600519.SH:2026-06-01:2026-06-04",
                [{"period_start": "2026-06-04", "open": "10", "high": "11", "low": "9", "close": "10.5", "volume": "100"}],
            )
        ]
    )
    monkeypatch.setattr(agent_tools, "build_data_api_from_env", lambda: api)

    rows = agent_tools.load_price_rows(ticker="600519", start_date="2026-06-01", end_date="2026-06-04")

    assert rows == [{"date": "2026-06-04", "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5, "volume": 100.0, "change_pct": 0.0}]
    assert api.requests[0].symbol_id == "600519.SH"
    assert api.requests[0].data_type == "daily_bar"


def test_agent_news_pack_groups_rows_by_request_id(monkeypatch) -> None:
    api = _FakeAPI(
        [
            _ready("agent:news:company_news:600519.SH", [{"title": "company", "source": "s"}]),
            _ready("agent:news:macro_news:600519.SH", [{"title": "macro", "source": "s"}]),
        ]
    )
    monkeypatch.setattr(agent_tools, "build_data_api_from_env", lambda: api)

    pack = agent_tools.load_cn_a_news_pack(ticker="600519", start_date="2026-06-01", end_date="2026-06-04")

    assert pack["ok"] is True
    assert pack["data"]["company_news"] == [{"title": "company", "source": "s"}]
    assert pack["data"]["macro_news"] == [{"title": "macro", "source": "s"}]


def test_agent_price_frame_returns_dataframe(monkeypatch) -> None:
    monkeypatch.setattr(
        agent_tools,
        "load_price_rows",
        lambda **_: [{"date": "2026-06-04", "open": 1.0, "high": 1.2, "low": 0.9, "close": 1.1, "volume": 100.0, "change_pct": 0.0}],
    )

    assert isinstance(agent_tools.load_price_frame(ticker="600519", start_date="2026-06-01", end_date="2026-06-04"), pd.DataFrame)
