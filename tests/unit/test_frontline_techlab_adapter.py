from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Mapping

import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_PYTHON_ROOT = REPO_ROOT / "openclaw_plugins" / "claw-trade-frontline-tools" / "python"
if str(SHARED_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(SHARED_PYTHON_ROOT))

from frontline_data_pack.errors import (  # noqa: E402
    L2_TARGET_INVALID,
    L2_WRITE_FAILED,
    TECHLAB_CHART_FAILED,
    TECHLAB_INDICATOR_FAILED,
    TECHLAB_INPUT_INVALID,
)
from frontline_data_pack.evidence import OpenVikingStatResult, OpenVikingWriteResult  # noqa: E402
from frontline_data_pack.evidence_writer import ChartEvidenceWriteResult, EvidenceWriteError  # noqa: E402
from frontline_data_pack.models import MarketPriceRow, TechlabInputFrame  # noqa: E402
from frontline_data_pack.runtime_context import ToolRuntimeContext  # noqa: E402
import frontline_data_pack.techlab_adapter as techlab_adapter  # noqa: E402
from frontline_data_pack.techlab_adapter import compute_market_techlab_outputs  # noqa: E402


def test_load_module_supports_dataclass_module_under_python_312(tmp_path: Path) -> None:
    module_path = tmp_path / "indicator_engine_like.py"
    module_path.write_text(
        "from dataclasses import dataclass\n"
        "\n"
        "@dataclass\n"
        "class IndicatorSnapshot:\n"
        "    close: float\n"
        "\n"
        "def build_snapshot() -> IndicatorSnapshot:\n"
        "    return IndicatorSnapshot(close=123.45)\n",
        encoding="utf-8",
    )
    module_name = "frontline_test_indicator_engine_dataclass"
    module = techlab_adapter._load_module(module_path, module_name=module_name)
    try:
        assert module.build_snapshot().close == 123.45
        assert sys.modules[module_name] is module
    finally:
        sys.modules.pop(module_name, None)


def test_t_tech_001_valid_ohlcv_returns_indicators_and_chart_ref(tmp_path: Path) -> None:
    context = _context(tmp_path / "call-root")
    frame = TechlabInputFrame(
        ticker="600519.SH",
        rows=_rows(30),
        output_dir="techlab/charts-local",
    )
    l2_client = _InMemoryL2Client()

    result = compute_market_techlab_outputs(
        frame=frame,
        context=context,
        l2_client=l2_client,
        indicator_compute=_fake_indicator_compute,
        chart_render=_small_png_chart_render,
    )

    assert result.indicators is not None
    assert len(result.chart_refs) >= 1
    assert len(result.chart_evidence_refs) == len(result.chart_refs)
    assert len(l2_client.written_uris) >= 1
    assert result.chart_refs[0].openviking_ref.endswith("/charts/market_structure.manifest.json")
    assert result.chart_evidence_refs[0].uri == result.chart_refs[0].openviking_ref
    assert result.chart_evidence_refs[0].kind == "chart_manifest"


def test_t_tech_001_insufficient_rows_returns_input_invalid_and_skips_chart_render(tmp_path: Path) -> None:
    context = _context(tmp_path / "call-root")
    frame = TechlabInputFrame(
        ticker="600519.SH",
        rows=_rows(10),
        output_dir="techlab/charts-local",
    )
    called = {"chart": 0}

    def _chart_not_expected(_: pd.DataFrame, __: str, ___: Path):
        called["chart"] += 1
        return ()

    result = compute_market_techlab_outputs(
        frame=frame,
        context=context,
        indicator_compute=_fake_indicator_compute,
        chart_render=_chart_not_expected,
    )

    assert result.failed is True
    assert any(item.startswith(f"{TECHLAB_INPUT_INVALID}:") for item in result.diagnostics)
    assert called["chart"] == 0


def test_t_tech_001_large_png_is_rejected_before_l2_write(tmp_path: Path) -> None:
    context = _context(tmp_path / "call-root")
    frame = TechlabInputFrame(
        ticker="600519.SH",
        rows=_rows(25),
        output_dir="techlab/charts-local",
    )
    l2_client = _InMemoryL2Client()

    result = compute_market_techlab_outputs(
        frame=frame,
        context=context,
        l2_client=l2_client,
        indicator_compute=_fake_indicator_compute,
        chart_render=_large_png_chart_render,
    )

    assert result.indicators is not None
    assert result.chart_refs == []
    assert result.chart_evidence_refs == []
    assert l2_client.written_uris == []
    assert any(item.startswith(f"{TECHLAB_CHART_FAILED}:") for item in result.diagnostics)


def test_t_tech_001_chart_only_reachable_when_indicator_mapping_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    context = _context(tmp_path / "call-root")
    frame = TechlabInputFrame(
        ticker="600519.SH",
        rows=_rows(25),
        output_dir="techlab/charts-local",
    )
    l2_client = _InMemoryL2Client()

    def _raise_indicator_mapping(*args, **kwargs):
        _ = args, kwargs
        raise RuntimeError("indicator mapping failure")

    monkeypatch.setattr(techlab_adapter, "_build_market_indicators", _raise_indicator_mapping)
    result = compute_market_techlab_outputs(
        frame=frame,
        context=context,
        l2_client=l2_client,
        indicator_compute=_fake_indicator_compute,
        chart_render=_small_png_chart_render,
    )

    assert result.indicators is None
    assert result.failed is False
    assert len(result.chart_refs) >= 1
    assert len(result.chart_evidence_refs) == len(result.chart_refs)
    assert any(item.startswith(f"{TECHLAB_INDICATOR_FAILED}:") for item in result.diagnostics)


def test_t_tech_001_l2_write_failure_does_not_emit_chart_ref(tmp_path: Path) -> None:
    context = _context(tmp_path / "call-root")
    frame = TechlabInputFrame(
        ticker="600519.SH",
        rows=_rows(25),
        output_dir="techlab/charts-local",
    )
    l2_client = _WriteFailingL2Client()

    result = compute_market_techlab_outputs(
        frame=frame,
        context=context,
        l2_client=l2_client,
        indicator_compute=_fake_indicator_compute,
        chart_render=_small_png_chart_render,
    )

    assert result.indicators is not None
    assert result.chart_refs == []
    assert result.chart_evidence_refs == []
    assert any(item.startswith("l2_write_failed:chart:") for item in result.diagnostics)
    assert result.failed is False


def test_t_tech_001_chart_l2_error_message_with_viking_uri_not_leaked_to_diagnostics(
    tmp_path: Path,
    monkeypatch,
) -> None:
    context = _context(tmp_path / "call-root")
    frame = TechlabInputFrame(
        ticker="600519.SH",
        rows=_rows(25),
        output_dir="techlab/charts-local",
    )

    def _write_chart_evidence_with_uri_error(*, chart_kind: str, **_kwargs):
        return ChartEvidenceWriteResult(
            ok=False,
            chart_ref=None,
            receipt=None,
            evidence_ref=None,
            error=EvidenceWriteError(
                code=L2_WRITE_FAILED,
                message=(
                    "OpenViking 写入失败: File not found: "
                    "viking://resources/workflow/run-techlab-1/frontline/market_analyst/"
                    "call-techlab-1/evidence/charts/market_structure.manifest.json"
                ),
                diagnostic_flag=f"l2_write_failed:chart:{chart_kind}",
            ),
        )

    monkeypatch.setattr(techlab_adapter, "write_chart_evidence", _write_chart_evidence_with_uri_error)
    result = compute_market_techlab_outputs(
        frame=frame,
        context=context,
        indicator_compute=_fake_indicator_compute,
        chart_render=_small_png_chart_render,
    )

    assert result.chart_refs == []
    assert result.chart_evidence_refs == []
    assert any(item.startswith("l2_write_failed:chart:") for item in result.diagnostics)
    assert all("viking://" not in item for item in result.diagnostics)


def test_t_tech_001_output_dir_outside_call_root_returns_path_error(tmp_path: Path) -> None:
    context = _context(tmp_path / "call-root")
    outside = tmp_path / "outside"
    frame = TechlabInputFrame(
        ticker="600519.SH",
        rows=_rows(25),
        output_dir=str(outside),
    )

    result = compute_market_techlab_outputs(
        frame=frame,
        context=context,
        indicator_compute=_fake_indicator_compute,
        chart_render=_small_png_chart_render,
    )

    assert result.failed is True
    assert any(item.startswith(f"{L2_TARGET_INVALID}:") for item in result.diagnostics)


def _fake_indicator_compute(frame: pd.DataFrame, ticker: str) -> SimpleNamespace:
    _ = ticker
    chart_frame = frame.copy()
    chart_frame["date"] = pd.to_datetime(chart_frame["date"])
    chart_frame["ma5"] = chart_frame["close"].rolling(5).mean()
    chart_frame["ma10"] = chart_frame["close"].rolling(10).mean()
    chart_frame["ma20"] = chart_frame["close"].rolling(20).mean()
    chart_frame["macd"] = 1.0
    chart_frame["signal"] = 0.8
    chart_frame["hist"] = 0.2
    chart_frame["rsi14"] = 55.0
    chart_frame["boll_upper"] = chart_frame["close"] * 1.02
    chart_frame["boll_mid"] = chart_frame["close"]
    chart_frame["boll_lower"] = chart_frame["close"] * 0.98
    chart_frame["atr14"] = 10.0
    chart_frame["k"] = 60.0
    chart_frame["d"] = 55.0
    chart_frame["j"] = 70.0
    indicators = {
        "ma": {"ma5": 102.0, "ma10": 101.0, "ma20": 100.0},
        "macd": {"macd": 1.0, "signal": 0.8, "hist": 0.2},
        "rsi": {},
        "boll": {"upper": 110.0, "mid": 100.0, "lower": 90.0},
        "kdj": {"k": 60.0, "d": 55.0, "j": 70.0},
        "atr": {"atr14": 10.0},
    }
    return SimpleNamespace(indicators=indicators, chart_frame=chart_frame)


def _small_png_chart_render(_: pd.DataFrame, ticker: str, output_dir: Path):
    output_dir.mkdir(parents=True, exist_ok=True)
    market = output_dir / f"{ticker}_market_structure.png"
    indicator = output_dir / f"{ticker}_indicator_panels.png"
    market.write_bytes(b"\x89PNG\r\n\x1a\nsmall-market")
    indicator.write_bytes(b"\x89PNG\r\n\x1a\nsmall-indicator")
    return (market, indicator)


def _large_png_chart_render(_: pd.DataFrame, ticker: str, output_dir: Path):
    output_dir.mkdir(parents=True, exist_ok=True)
    big = output_dir / f"{ticker}_market_structure.png"
    big.write_bytes(b"\x89PNG\r\n\x1a\n" + (b"0" * (2 * 1024 * 1024 + 1)))
    return (big,)


def _rows(count: int) -> list[MarketPriceRow]:
    rows: list[MarketPriceRow] = []
    for index in range(count):
        day = index + 1
        rows.append(
            MarketPriceRow(
                trade_date=f"2026-01-{day:02d}",
                open=100.0 + day,
                high=101.0 + day,
                low=99.0 + day,
                close=100.5 + day,
                volume=1000.0 + day,
                amount=2000.0 + day,
                adjust="qfq",
                source_ref=f"source-{day}",
            )
        )
    return rows


def _context(evidence_root: Path) -> ToolRuntimeContext:
    return ToolRuntimeContext(
        run_id="run-techlab-1",
        stage="frontline",
        worker_id="market_analyst",
        call_id="call-techlab-1",
        dispatch_id="call-techlab-1",
        tool_name="market_market_data_pack",
        evidence_root=str(evidence_root),
        current_time="2026-05-09T10:00:00Z",
        current_date="2026-05-09",
    )


class _InMemoryL2Client:
    def __init__(self) -> None:
        self._content_by_uri: dict[str, bytes] = {}
        self.written_uris: list[str] = []

    def write(
        self,
        *,
        uri: str,
        content_bytes: bytes,
        content_type: str,
        metadata: Mapping[str, str],
    ) -> OpenVikingWriteResult:
        _ = content_type, metadata
        self._content_by_uri[uri] = content_bytes
        self.written_uris.append(uri)
        return OpenVikingWriteResult(receipt_id="receipt-techlab-1")

    def stat(self, *, uri: str) -> OpenVikingStatResult:
        content = self._content_by_uri[uri]
        return OpenVikingStatResult(
            size_bytes=len(content),
            sha256="sha256:" + hashlib.sha256(content).hexdigest(),
            exists=True,
        )

    def read(self, *, uri: str) -> bytes:
        return self._content_by_uri[uri]


class _WriteFailingL2Client:
    def write(
        self,
        *,
        uri: str,
        content_bytes: bytes,
        content_type: str,
        metadata: Mapping[str, str],
    ) -> OpenVikingWriteResult:
        _ = uri, content_bytes, content_type, metadata
        raise RuntimeError("l2 write failure")

    def stat(self, *, uri: str) -> OpenVikingStatResult:
        _ = uri
        raise AssertionError("write 失败后不应执行 stat")

    def read(self, *, uri: str) -> bytes:
        _ = uri
        raise AssertionError("write 失败后不应执行 read")
