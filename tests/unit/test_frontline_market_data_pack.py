from __future__ import annotations

import hashlib
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

import pandas as pd
import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_PYTHON_ROOT = REPO_ROOT / "openclaw_plugins" / "claw-trade-frontline-tools" / "python"
SRC_ROOT = REPO_ROOT / "src"
if str(SHARED_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(SHARED_PYTHON_ROOT))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from frontline_data_pack.config import load_frontline_provider_config  # noqa: E402
from frontline_data_pack.errors import TOOL_CONTEXT_INCOMPLETE, TOOL_WORKER_MISMATCH, FrontlineValidationError  # noqa: E402
from frontline_data_pack.evidence import OpenVikingStatResult, OpenVikingWriteResult  # noqa: E402
from frontline_data_pack.market_data_pack import BuildMarketDataPack, clear_market_chart_references  # noqa: E402
from frontline_data_pack.models import (  # noqa: E402
    AtrIndicators,
    BollIndicators,
    ChartRef,
    EvidenceRef,
    KdjIndicators,
    MacdIndicators,
    MarketIndicators,
    MovingAverageIndicators,
    RsiIndicators,
    TechlabResult,
)
from frontline_data_pack.runtime_context import ToolRuntimeContext  # noqa: E402
from frontline_data_pack.techlab_adapter import compute_market_techlab_outputs  # noqa: E402


def _runtime_context_payload(
    *,
    stage: str = "frontline",
    worker_id: str = "market_analyst",
    tool_name: str = "market_market_data_pack",
    market: str | None = "CN_A",
    remove_fields: set[str] | None = None,
) -> dict[str, Any]:
    payload = {
        "run_id": "run-market-1",
        "stage": stage,
        "worker_id": worker_id,
        "call_id": "call-market-1",
        "dispatch_id": "dispatch-market-1",
        "tool_name": tool_name,
        "evidence_root": "/tmp/evidence",
        "current_time": "2026-05-09T12:00:00Z",
        "current_date": "2026-05-09",
    }
    if market is not None:
        payload["market"] = market
    if remove_fields:
        for key in remove_fields:
            payload.pop(key, None)
    return payload


def test_t_mkt_002_given_p0_success_and_l2_and_indicator_and_chart_quality_is_complete() -> None:
    pack = _build_runner(
        call_registry={
            ("akshare", "stock_zh_a_hist"): _market_rows_provider(20),
            ("eastmoney_direct", "push2his_kline"): _empty_rows_provider(),
            ("sina", "stock_zh_a_daily"): _empty_rows_provider(),
            ("tencent", "stock_zh_a_hist_tx"): _empty_rows_provider(),
        },
        techlab_compute=_techlab_complete,
    ).build(_tool_input(), _runtime_context())

    assert pack.quality.status == "complete"
    assert len(pack.domain_data["chart_refs"]) >= 1
    assert any(ref.kind == "provider_raw" for ref in pack.raw_payload_refs)
    assert any(attempt.provider == "baostock" and attempt.status == "error" for attempt in pack.provider_attempts)
    assert any(attempt.provider == "efinance" and attempt.status == "error" for attempt in pack.provider_attempts)
    assert any(attempt.provider == "tushare" and attempt.status == "error" for attempt in pack.provider_attempts)


def test_t_mkt_002_under_20_valid_ohlcv_cannot_be_complete_even_with_other_signals_ready() -> None:
    pack = _build_runner(
        call_registry={
            ("akshare", "stock_zh_a_hist"): _market_rows_provider(1),
            ("eastmoney_direct", "push2his_kline"): _empty_rows_provider(),
            ("sina", "stock_zh_a_daily"): _empty_rows_provider(),
            ("tencent", "stock_zh_a_hist_tx"): _empty_rows_provider(),
        },
        techlab_compute=_techlab_complete,
    ).build(_tool_input(), _runtime_context())

    assert pack.quality.status == "partial"
    assert any(item.startswith("market_ohlcv_window_insufficient:rows=1,min_required=20") for item in pack.quality.warnings)


def test_t_mkt_002_chart_evidence_ref_from_real_techlab_write_enters_openviking_l2_refs() -> None:
    l2_client = _InMemoryL2Client()
    pack = BuildMarketDataPack(
        config=load_frontline_provider_config(_base_env()),
        provider_call_registry={
            ("akshare", "stock_zh_a_hist"): _market_rows_provider(25),
            ("eastmoney_direct", "push2his_kline"): _empty_rows_provider(),
            ("sina", "stock_zh_a_daily"): _empty_rows_provider(),
            ("tencent", "stock_zh_a_hist_tx"): _empty_rows_provider(),
        },
        evidence_client=l2_client,
        provider_cache_collection=_MongoCollection(),
        provider_attempts_collection=_MongoCollection(),
        normalized_market_collection=_MongoCollection(),
        techlab_compute=_techlab_compute_with_real_adapter,
    ).build(_tool_input(), _runtime_context())

    assert len(pack.domain_data["chart_refs"]) >= 1
    chart_ref = pack.domain_data["chart_refs"][0]
    chart_l2_refs = [ref for ref in pack.openviking_l2_refs if ref.kind == "chart_manifest"]
    assert len(chart_l2_refs) >= 1
    assert any(ref.uri == chart_ref["openviking_ref"] for ref in chart_l2_refs)
    assert chart_ref["openviking_ref"] in l2_client.written_uris


def test_t_mkt_002_given_rows_but_chart_write_failed_quality_partial_and_brief_mentions_chart_gap() -> None:
    l2_client = _ChartWriteFailingL2Client()
    pack = BuildMarketDataPack(
        config=load_frontline_provider_config(_base_env()),
        provider_call_registry={
            ("akshare", "stock_zh_a_hist"): _market_rows_provider(25),
            ("eastmoney_direct", "push2his_kline"): _empty_rows_provider(),
            ("sina", "stock_zh_a_daily"): _empty_rows_provider(),
            ("tencent", "stock_zh_a_hist_tx"): _empty_rows_provider(),
        },
        evidence_client=l2_client,
        provider_cache_collection=_MongoCollection(),
        provider_attempts_collection=_MongoCollection(),
        normalized_market_collection=_MongoCollection(),
        techlab_compute=_techlab_compute_with_real_adapter,
    ).build(_tool_input(), _runtime_context())

    assert pack.quality.status == "partial"
    assert pack.domain_data["chart_refs"] == []
    assert all(ref.kind != "chart_manifest" for ref in pack.openviking_l2_refs)
    assert "market_chart_evidence_gap" in pack.reader_brief


def test_t_mkt_002_chart_l2_uri_error_not_leaked_into_pack_diagnostic_flags() -> None:
    l2_client = _ChartWriteUriNoiseL2Client()
    pack = BuildMarketDataPack(
        config=load_frontline_provider_config(_base_env()),
        provider_call_registry={
            ("akshare", "stock_zh_a_hist"): _market_rows_provider(25),
            ("eastmoney_direct", "push2his_kline"): _empty_rows_provider(),
            ("sina", "stock_zh_a_daily"): _empty_rows_provider(),
            ("tencent", "stock_zh_a_hist_tx"): _empty_rows_provider(),
        },
        evidence_client=l2_client,
        provider_cache_collection=_MongoCollection(),
        provider_attempts_collection=_MongoCollection(),
        normalized_market_collection=_MongoCollection(),
        techlab_compute=_techlab_compute_with_real_adapter,
    ).build(_tool_input(), _runtime_context())

    assert pack.quality.status == "partial"
    assert any(item.startswith("l2_write_failed:chart:") for item in pack.diagnostic_flags)
    assert all("viking://" not in item for item in pack.diagnostic_flags)


def test_t_mkt_002_chart_cleanup_removes_stale_chart_refs_and_keeps_core_evidence() -> None:
    pack = _build_runner(
        call_registry={
            ("akshare", "stock_zh_a_hist"): _market_rows_provider(25),
            ("eastmoney_direct", "push2his_kline"): _empty_rows_provider(),
            ("sina", "stock_zh_a_daily"): _empty_rows_provider(),
            ("tencent", "stock_zh_a_hist_tx"): _empty_rows_provider(),
        },
        techlab_compute=_techlab_complete,
    ).build(_tool_input(), _runtime_context())
    assert pack.domain_data["chart_refs"]
    assert any(ref.kind == "chart_manifest" for ref in pack.openviking_l2_refs)
    provider_raw_count = sum(1 for ref in pack.openviking_l2_refs if ref.kind == "provider_raw")
    provider_attempts_count = sum(1 for ref in pack.openviking_l2_refs if ref.kind == "provider_attempts")
    normalized_pack_count = sum(1 for ref in pack.openviking_l2_refs if ref.kind == "normalized_pack")
    cleanup_ref = EvidenceRef(
        uri=(
            "viking://resources/workflow/run-market-1/frontline/market_analyst/call-market-1/"
            "evidence/charts/market_structure.manifest.json"
        ),
        sha256="sha256:" + ("f" * 64),
        size_bytes=128,
        kind="chart_manifest_cleanup",
        readback_verified=True,
    )

    cleaned = clear_market_chart_references(pack, cleanup_evidence_ref=cleanup_ref)

    assert cleaned.domain_data["chart_refs"] == []
    assert all(ref.kind != "chart_manifest" for ref in cleaned.openviking_l2_refs)
    assert any(ref.kind == "chart_manifest_cleanup" for ref in cleaned.openviking_l2_refs)
    assert sum(1 for ref in cleaned.openviking_l2_refs if ref.kind == "provider_raw") == provider_raw_count
    assert sum(1 for ref in cleaned.openviking_l2_refs if ref.kind == "provider_attempts") == provider_attempts_count
    assert sum(1 for ref in cleaned.openviking_l2_refs if ref.kind == "normalized_pack") == normalized_pack_count
    assert "market_chart_refs_cleared" in cleaned.diagnostic_flags


def test_t_mkt_002_given_p0_p1_all_failed_quality_failed_and_price_history_not_supportive() -> None:
    pack = _build_runner(
        call_registry={
            ("akshare", "stock_zh_a_hist"): _raise_provider_error,
            ("eastmoney_direct", "push2his_kline"): _raise_provider_error,
            ("sina", "stock_zh_a_daily"): _raise_provider_error,
            ("tencent", "stock_zh_a_hist_tx"): _raise_provider_error,
        },
        techlab_compute=_techlab_should_not_run,
    ).build(_tool_input(), _runtime_context())

    assert pack.quality.status == "failed"
    assert pack.domain_data["price_history"]["row_count"] == 0
    assert pack.domain_data["price_history"]["recent_rows"] == []


def test_t_mkt_002_core_provider_raw_l2_write_failed_results_in_failed_quality() -> None:
    pack = BuildMarketDataPack(
        config=load_frontline_provider_config(_base_env()),
        provider_call_registry={
            ("akshare", "stock_zh_a_hist"): _market_rows_provider(25),
            ("eastmoney_direct", "push2his_kline"): _empty_rows_provider(),
            ("sina", "stock_zh_a_daily"): _empty_rows_provider(),
            ("tencent", "stock_zh_a_hist_tx"): _empty_rows_provider(),
        },
        evidence_client=_ProviderRawWriteFailingL2Client(),
        provider_cache_collection=_MongoCollection(),
        provider_attempts_collection=_MongoCollection(),
        normalized_market_collection=_MongoCollection(),
        techlab_compute=_techlab_complete,
    ).build(_tool_input(), _runtime_context())

    assert pack.quality.status == "failed"
    assert any(item == "core_l2_write_or_verify_failed" for item in pack.quality.warnings)


@pytest.mark.parametrize(
    ("runtime_context", "expected_code"),
    [
        pytest.param(
            _runtime_context_payload(remove_fields={"worker_id"}),
            TOOL_CONTEXT_INCOMPLETE,
            id="worker_id_missing",
        ),
        pytest.param(
            _runtime_context_payload(worker_id=" "),
            TOOL_CONTEXT_INCOMPLETE,
            id="worker_id_empty",
        ),
        pytest.param(
            _runtime_context_payload(worker_id="news_analyst"),
            TOOL_WORKER_MISMATCH,
            id="worker_id_mismatch",
        ),
        pytest.param(
            _runtime_context_payload(tool_name="market.other_tool"),
            TOOL_WORKER_MISMATCH,
            id="tool_name_mismatch",
        ),
        pytest.param(
            _runtime_context_payload(stage="investment_debate"),
            TOOL_WORKER_MISMATCH,
            id="stage_mismatch",
        ),
        pytest.param(
            _runtime_context_payload(market="US"),
            TOOL_WORKER_MISMATCH,
            id="market_mismatch",
        ),
    ],
)
def test_t_mkt_002_context_errors_fail_fast_without_provider_attempts(
    runtime_context: dict[str, Any],
    expected_code: str,
) -> None:
    with pytest.raises(FrontlineValidationError) as error:
        _build_fail_fast_runner().build(_tool_input(), runtime_context)

    assert error.value.code == expected_code


def test_t_mkt_002_default_60_day_window_recent_rows_max_10_and_trade_date_ascending() -> None:
    pack = _build_runner(
        call_registry={
            ("akshare", "stock_zh_a_hist"): _market_rows_provider(80),
            ("eastmoney_direct", "push2his_kline"): _empty_rows_provider(),
            ("sina", "stock_zh_a_daily"): _empty_rows_provider(),
            ("tencent", "stock_zh_a_hist_tx"): _empty_rows_provider(),
        },
        techlab_compute=_techlab_complete,
    ).build(
        {
            "ticker": "600519.SH",
            "market": "CN_A",
            "company_name": "贵州茅台",
            "start_date": None,
            "end_date": None,
        },
        _runtime_context(),
    )

    assert pack.input.end_date == "2026-05-09"
    assert pack.input.start_date == "2026-03-10"
    recent_rows = pack.domain_data["price_history"]["recent_rows"]
    assert len(recent_rows) == 10
    assert recent_rows == sorted(recent_rows, key=lambda item: item["trade_date"])


def test_t_mkt_002_reader_brief_material_body_excludes_zero_accepted_sources() -> None:
    pack = _build_runner(
        call_registry={
            ("akshare", "stock_zh_a_hist"): _market_rows_provider(25),
            ("eastmoney_direct", "push2his_kline"): _empty_rows_provider(),
            ("sina", "stock_zh_a_daily"): _empty_rows_provider(),
            ("tencent", "stock_zh_a_hist_tx"): _empty_rows_provider(),
        },
        techlab_compute=_techlab_complete,
    ).build(_tool_input(), _runtime_context())

    assert "共 25 个交易日" in pack.reader_brief
    assert "提供 0 条可用行情记录" not in pack.reader_brief


def test_t_mkt_002_reader_brief_includes_latest_change_and_pct_without_placeholder() -> None:
    pack = _build_runner(
        call_registry={
            ("akshare", "stock_zh_a_hist"): _market_rows_provider(25),
            ("eastmoney_direct", "push2his_kline"): _empty_rows_provider(),
            ("sina", "stock_zh_a_daily"): _empty_rows_provider(),
            ("tencent", "stock_zh_a_hist_tx"): _empty_rows_provider(),
        },
        techlab_compute=_techlab_complete,
    ).build(_tool_input(), _runtime_context())

    assert "日涨跌说明：前收" in pack.reader_brief
    assert "日涨跌额" in pack.reader_brief
    assert "日涨跌幅" in pack.reader_brief
    assert "待计算" not in pack.reader_brief


def test_t_mkt_002_reader_brief_marks_change_fields_missing_without_placeholder() -> None:
    pack = _build_runner(
        call_registry={
            ("akshare", "stock_zh_a_hist"): _market_rows_provider(1),
            ("eastmoney_direct", "push2his_kline"): _empty_rows_provider(),
            ("sina", "stock_zh_a_daily"): _empty_rows_provider(),
            ("tencent", "stock_zh_a_hist_tx"): _empty_rows_provider(),
        },
        techlab_compute=_techlab_complete,
    ).build(_tool_input(), _runtime_context())

    assert "日涨跌说明：涨跌幅字段未提供" in pack.reader_brief
    assert "禁止使用占位描述" in pack.reader_brief
    assert "待计算" not in pack.reader_brief


def test_t_mkt_002_reader_brief_prefers_source_pct_chg_when_available() -> None:
    def _provider(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "rows": [
                {
                    "trade_date": "2026-05-08",
                    "open": 100.0,
                    "high": 101.0,
                    "low": 99.0,
                    "close": 100.0,
                    "volume": 10_000.0,
                    "amount": 1_000_000.0,
                    "adjust": "qfq",
                },
                {
                    "trade_date": "2026-05-09",
                    "open": 102.0,
                    "high": 111.0,
                    "low": 101.0,
                    "close": 110.0,
                    "volume": 11_000.0,
                    "amount": 1_100_000.0,
                    "pre_close": 100.0,
                    "change": 3.0,
                    "pct_chg": 3.0,
                    "adjust": "qfq",
                },
            ]
        }

    pack = _build_runner(
        call_registry={
            ("akshare", "stock_zh_a_hist"): _provider,
            ("eastmoney_direct", "push2his_kline"): _empty_rows_provider(),
            ("sina", "stock_zh_a_daily"): _empty_rows_provider(),
            ("tencent", "stock_zh_a_hist_tx"): _empty_rows_provider(),
        },
        techlab_compute=_techlab_complete,
    ).build(_tool_input(), _runtime_context())

    assert "日涨跌额 3" in pack.reader_brief
    assert "日涨跌幅 3%" in pack.reader_brief


@dataclass
class _MongoCollection:
    find_doc: dict[str, Any] | None = None

    def find_one(self, _query: dict[str, Any]) -> dict[str, Any] | None:
        return self.find_doc

    def update_one(self, _flt: dict[str, Any], _update: dict[str, Any], *, upsert: bool) -> None:
        _ = upsert
        return None

    def insert_one(self, _doc: dict[str, Any]) -> None:
        return None


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
        return OpenVikingWriteResult(receipt_id="receipt-1")

    def stat(self, *, uri: str) -> OpenVikingStatResult:
        content = self._content_by_uri[uri]
        digest = hashlib.sha256(content).hexdigest()
        return OpenVikingStatResult(size_bytes=len(content), sha256=f"sha256:{digest}", exists=True)

    def read(self, *, uri: str) -> bytes:
        return self._content_by_uri[uri]


class _FailFastCollection:
    def find_one(self, _query: dict[str, Any]) -> dict[str, Any] | None:
        raise AssertionError("context error path must not read MongoDB")

    def update_one(self, _flt: dict[str, Any], _update: dict[str, Any], *, upsert: bool) -> None:
        _ = upsert
        raise AssertionError("context error path must not write MongoDB")

    def insert_one(self, _doc: dict[str, Any]) -> None:
        raise AssertionError("context error path must not insert MongoDB rows")


class _FailFastL2Client:
    def write(
        self,
        *,
        uri: str,
        content_bytes: bytes,
        content_type: str,
        metadata: Mapping[str, str],
    ) -> OpenVikingWriteResult:
        _ = uri, content_bytes, content_type, metadata
        raise AssertionError("context error path must not write L2")

    def stat(self, *, uri: str) -> OpenVikingStatResult:
        _ = uri
        raise AssertionError("context error path must not stat L2")

    def read(self, *, uri: str) -> bytes:
        _ = uri
        raise AssertionError("context error path must not read L2")


class _ChartWriteFailingL2Client(_InMemoryL2Client):
    def write(
        self,
        *,
        uri: str,
        content_bytes: bytes,
        content_type: str,
        metadata: Mapping[str, str],
    ) -> OpenVikingWriteResult:
        if "/charts/" in uri:
            raise RuntimeError("chart write failed")
        return super().write(
            uri=uri,
            content_bytes=content_bytes,
            content_type=content_type,
            metadata=metadata,
        )


class _ChartWriteUriNoiseL2Client(_InMemoryL2Client):
    def write(
        self,
        *,
        uri: str,
        content_bytes: bytes,
        content_type: str,
        metadata: Mapping[str, str],
    ) -> OpenVikingWriteResult:
        if "/charts/" in uri:
            raise RuntimeError(f"OpenViking 写入失败: File not found: {uri}")
        return super().write(
            uri=uri,
            content_bytes=content_bytes,
            content_type=content_type,
            metadata=metadata,
        )


class _ProviderRawWriteFailingL2Client(_InMemoryL2Client):
    def write(
        self,
        *,
        uri: str,
        content_bytes: bytes,
        content_type: str,
        metadata: Mapping[str, str],
    ) -> OpenVikingWriteResult:
        if "/provider_raw/" in uri:
            raise RuntimeError("provider raw write failed")
        return super().write(
            uri=uri,
            content_bytes=content_bytes,
            content_type=content_type,
            metadata=metadata,
        )


def _build_runner(
    *,
    call_registry: Mapping[tuple[str, str], Any],
    techlab_compute: Any,
) -> BuildMarketDataPack:
    return BuildMarketDataPack(
        config=load_frontline_provider_config(_base_env()),
        provider_call_registry=call_registry,
        evidence_client=_InMemoryL2Client(),
        provider_cache_collection=_MongoCollection(),
        provider_attempts_collection=_MongoCollection(),
        normalized_market_collection=_MongoCollection(),
        techlab_compute=techlab_compute,
    )


def _build_fail_fast_runner() -> BuildMarketDataPack:
    def _fail_fast_provider(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("context error path must not call provider registry")

    fail_fast_registry = {
        ("akshare", "stock_zh_a_hist"): _fail_fast_provider,
        ("eastmoney_direct", "push2his_kline"): _fail_fast_provider,
        ("sina", "stock_zh_a_daily"): _fail_fast_provider,
        ("tencent", "stock_zh_a_hist_tx"): _fail_fast_provider,
    }
    return BuildMarketDataPack(
        config=load_frontline_provider_config(_base_env()),
        provider_call_registry=fail_fast_registry,
        evidence_client=_FailFastL2Client(),
        provider_cache_collection=_FailFastCollection(),
        provider_attempts_collection=_FailFastCollection(),
        normalized_market_collection=_FailFastCollection(),
        techlab_compute=_techlab_should_not_run,
    )


def _base_env() -> dict[str, str]:
    return {
        "CN_A_MONGODB_URI": "mongodb://localhost:27017/claw_trade",
        "CLAW_TRADE_OPENVIKING_BASE_URI": "https://openviking.internal",
        "CLAW_TRADE_OPENVIKING_AUTH_MODE": "none",
    }


def _runtime_context() -> ToolRuntimeContext:
    return ToolRuntimeContext(
        run_id="run-market-1",
        stage="frontline",
        worker_id="market_analyst",
        call_id="call-market-1",
        dispatch_id="dispatch-market-1",
        tool_name="market_market_data_pack",
        evidence_root="/tmp/evidence",
        current_time="2026-05-09T12:00:00Z",
        current_date="2026-05-09",
    )


def _tool_input() -> dict[str, Any]:
    return {
        "ticker": "600519.SH",
        "market": "CN_A",
        "company_name": "贵州茅台",
        "start_date": "2026-03-01",
        "end_date": "2026-05-08",
    }


def _market_rows_provider(count: int):
    def _provider(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        end = date(2026, 5, 9)
        start = end - timedelta(days=count - 1)
        rows = []
        for index in range(count):
            day = start + timedelta(days=index)
            base = 1500.0 + index
            rows.append(
                {
                    "trade_date": day.isoformat(),
                    "open": base,
                    "high": base + 20.0,
                    "low": base - 20.0,
                    "close": base + 5.0,
                    "volume": 1000000.0 + index,
                    "amount": 3000000.0 + index,
                    "adjust": "qfq",
                }
            )
        return {"rows": rows}

    return _provider


def _empty_rows_provider():
    def _provider(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"rows": []}

    return _provider


def _raise_provider_error(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
    raise RuntimeError("provider failure")


def _techlab_complete(*_args: Any, **_kwargs: Any) -> TechlabResult:
    return TechlabResult(
        indicators=_sample_indicators(),
        chart_paths=["/tmp/evidence/charts/market_structure.png"],
        chart_refs=[
            ChartRef(
                kind="market_structure",
                path="/tmp/evidence/charts/market_structure.png",
                openviking_ref=(
                    "viking://resources/workflow/run-market-1/frontline/market_analyst/call-market-1/"
                    "evidence/charts/market_structure.manifest.json"
                ),
                sha256="sha256:" + ("a" * 64),
            )
        ],
        diagnostics=[],
        failed=False,
        chart_evidence_refs=[
            EvidenceRef(
                uri=(
                    "viking://resources/workflow/run-market-1/frontline/market_analyst/call-market-1/"
                    "evidence/charts/market_structure.manifest.json"
                ),
                sha256="sha256:" + ("a" * 64),
                size_bytes=16,
                kind="chart_manifest",
                readback_verified=True,
            )
        ],
    )


def _techlab_compute_with_real_adapter(*, frame: Any, context: Any, l2_client: Any, write_state: Any) -> TechlabResult:
    return compute_market_techlab_outputs(
        frame=frame,
        context=context,
        l2_client=l2_client,
        write_state=write_state,
        indicator_compute=_fake_indicator_compute,
        chart_render=_small_png_chart_render,
    )


def _techlab_chart_failed(*_args: Any, **_kwargs: Any) -> TechlabResult:
    return TechlabResult(
        indicators=_sample_indicators(),
        chart_paths=[],
        chart_refs=[],
        diagnostics=["TECHLAB_CHART_FAILED:chart write failed"],
        failed=False,
    )


def _techlab_should_not_run(*_args: Any, **_kwargs: Any) -> TechlabResult:
    raise AssertionError("rows 为空时不应调用 techlab")


def _sample_indicators() -> MarketIndicators:
    return MarketIndicators(
        ma=MovingAverageIndicators(ma5=1.0, ma10=1.0, ma20=1.0, ma60=1.0),
        macd=MacdIndicators(dif=1.0, dea=1.0, macd=1.0),
        rsi=RsiIndicators(rsi6=50.0, rsi12=50.0, rsi24=50.0),
        boll=BollIndicators(mid=1.0, upper=1.0, lower=1.0),
        kdj=KdjIndicators(k=50.0, d=50.0, j=50.0),
        atr=AtrIndicators(atr14=1.0),
    )


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
    market.write_bytes(b"\x89PNG\r\n\x1a\nsmall-market")
    return (market,)
