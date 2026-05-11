from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd

SCRIPTS_ROOT = Path("agents/fundamental_analyst/skills/cn-a-fundamental-data/scripts")


def _load_script_module(module_basename: str):
    scripts_path = str(SCRIPTS_ROOT.resolve())
    if scripts_path not in sys.path:
        sys.path.insert(0, scripts_path)
    module_path = SCRIPTS_ROOT / f"{module_basename}.py"
    spec = importlib.util.spec_from_file_location(f"cn_a_fundamental_{module_basename}", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载模块: {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"cn_a_fundamental_{module_basename}"] = module
    spec.loader.exec_module(module)
    return module


MODELS_MODULE = _load_script_module("models")
RAW_WRITER_MODULE = _load_script_module("raw_payload_writer")
AKSHARE_FETCHER_MODULE = _load_script_module("akshare_fetcher")


def test_t_fnd_022_invoke_akshare_api_by_dispatch_allowlist_only() -> None:
    class _FakeAkModule:
        @staticmethod
        def stock_zh_a_hist(**kwargs):
            return {"kwargs": kwargs}

    original = AKSHARE_FETCHER_MODULE.ak
    AKSHARE_FETCHER_MODULE.ak = _FakeAkModule()
    try:
        result = AKSHARE_FETCHER_MODULE.invoke_akshare_api_by_dispatch(
            "stock_zh_a_hist",
            {"symbol": "600519"},
            1000,
        )
        assert result["kwargs"]["symbol"] == "600519"
    finally:
        AKSHARE_FETCHER_MODULE.ak = original

    try:
        AKSHARE_FETCHER_MODULE.invoke_akshare_api_by_dispatch(
            "unapproved_api",
            {},
            1000,
        )
    except AKSHARE_FETCHER_MODULE.AkShareFetcherError as exc:
        assert exc.code == AKSHARE_FETCHER_MODULE.FND_AKSHARE_API_NOT_APPROVED
    else:
        raise AssertionError("unapproved api must fail")


def test_t_fnd_023_validate_columns_split_schema_changed_vs_empty_response() -> None:
    bad_columns = ["代码", "最新价", "总市值", "市净率"]
    validation_bad = AKSHARE_FETCHER_MODULE.ValidateAkShareColumns(
        "stock_zh_a_spot_em",
        bad_columns,
        target_code="600519",
        dataframe=pd.DataFrame(columns=bad_columns),
    )
    assert validation_bad.ok is False
    assert validation_bad.status == "schema_invalid"
    assert validation_bad.reason == "schema_changed"
    assert "市盈率-动态" in validation_bad.missing_columns

    full_columns = ["代码", "最新价", "总市值", "市盈率-动态", "市净率"]
    df = pd.DataFrame(
        [
            {"代码": "000001", "最新价": 10.0, "总市值": 100, "市盈率-动态": 11, "市净率": 2},
        ]
    )
    validation_empty = AKSHARE_FETCHER_MODULE.ValidateAkShareColumns(
        "stock_zh_a_spot_em",
        full_columns,
        target_code="600519",
        dataframe=df,
    )
    assert validation_empty.ok is False
    assert validation_empty.status == "empty"
    assert validation_empty.reason == "empty_response"


def test_t_fnd_024_map_akshare_columns_total_mv_keeps_cny_value() -> None:
    dataframe = pd.DataFrame(
        [
            {
                "代码": "600519",
                "最新价": "123.45",
                "总市值": "1234567890",
                "市盈率-动态": "20.50",
                "市净率": "5.60",
            }
        ]
    )
    extracted = AKSHARE_FETCHER_MODULE.map_akshare_columns_to_pack_fields_v1(
        "stock_zh_a_spot_em",
        dataframe,
        "sha256:abc",
        "viking://resources/workflow/run/frontline/fundamental_analyst/dispatch/provider_raw/akshare/stock_zh_a_spot_em/1.json",
    )
    by_path = {field_path: payload for field_path, payload, _ in extracted}
    assert by_path["valuation.total_mv"]["value"] == 1234567890.0
    assert by_path["valuation.total_mv"]["unit"] == "cny"
    assert by_path["valuation.total_mv"]["scale"] == "x"
    assert by_path["valuation.pb"]["value"] == 5.6
    assert by_path["price_context.close"]["value"] == 123.45


def test_t_fnd_024_map_akshare_stock_individual_info_em_item_value_rows() -> None:
    dataframe = pd.DataFrame(
        [
            {"item": "总市值", "value": "1234567890"},
            {"item": "行业", "value": "酿酒行业"},
            {"item": "主营业务", "value": "白酒生产销售"},
        ]
    )
    extracted = AKSHARE_FETCHER_MODULE.map_akshare_columns_to_pack_fields_v1(
        "stock_individual_info_em",
        dataframe,
        "sha256:abc",
        "viking://resources/workflow/run/frontline/fundamental_analyst/dispatch/provider_raw/akshare/stock_individual_info_em/1.json",
    )
    by_path = {field_path: payload for field_path, payload, _ in extracted}
    assert by_path["company_profile.industry"]["value"] == "酿酒行业"
    assert by_path["company_profile.main_business"]["value"] == "白酒生产销售"


def test_t_fnd_024_map_akshare_stock_zh_a_hist_trade_date_and_volume() -> None:
    dataframe = pd.DataFrame(
        [
            {"日期": "2026-05-06", "收盘": 1600.0, "成交量": 1000},
            {"日期": "2026-05-07", "收盘": 1700.0, "成交量": 2000},
        ]
    )
    extracted = AKSHARE_FETCHER_MODULE.map_akshare_columns_to_pack_fields_v1(
        "stock_zh_a_hist",
        dataframe,
        "sha256:abc",
        "viking://resources/workflow/run/frontline/fundamental_analyst/dispatch/provider_raw/akshare/stock_zh_a_hist/1.json",
    )
    by_path = {field_path: payload for field_path, payload, _ in extracted}
    assert by_path["price_context.trade_date"]["value"] == "2026-05-07"
    assert by_path["price_context.volume"]["value"] == 2000.0


def test_t_fnd_025_fetch_classifies_and_blocks_raw_write_failure() -> None:
    normalized = MODELS_MODULE.NormalizedInput(
        raw_ticker="600519",
        canonical_code="600519.SH",
        tushare_code="600519.SH",
        akshare_symbol="600519",
        exchange="SH",
        market="CN_A",
        start_date="2025-05-07",
        end_date="2026-05-07",
        current_date="2026-05-07",
        run_id="run-a",
        dispatch_id="dispatch-a",
        latest_report_period="20260331",
    )
    spec = MODELS_MODULE.ApiCallSpec(
        provider="akshare",
        api_name="stock_zh_a_spot_em",
        role="supplement",
        required=False,
        field_family="valuation",
        parameters={},
        timeout_ms=1000,
        retry_limit=1,
    )

    timeout_calls = {"count": 0}

    def timeout_dispatcher(_api_name, _parameters, _timeout_ms):
        timeout_calls["count"] += 1
        raise TimeoutError("timeout")

    timeout_result = AKSHARE_FETCHER_MODULE.AkShareFetcher(
        api_dispatcher=timeout_dispatcher,
        raw_writer=lambda _request: None,  # type: ignore[arg-type]
        sleep_fn=lambda _seconds: None,
    ).Fetch(normalized, spec)
    assert timeout_calls["count"] == 2
    assert timeout_result.attempt.status == "timeout"
    assert timeout_result.attempt.reason == "timeout"
    assert timeout_result.attempt.retry_count == 1

    def permission_dispatcher(_api_name, _parameters, _timeout_ms):
        raise RuntimeError("HTTP 403 Forbidden")

    permission_result = AKSHARE_FETCHER_MODULE.AkShareFetcher(
        api_dispatcher=permission_dispatcher,
        raw_writer=lambda _request: None,  # type: ignore[arg-type]
    ).Fetch(normalized, spec)
    assert permission_result.attempt.status == "error"
    assert permission_result.attempt.reason == "permission_or_access"

    good_df = pd.DataFrame(
        [
            {"代码": "600519", "最新价": 100.0, "总市值": 2000000, "市盈率-动态": 10.0, "市净率": 2.0},
        ]
    )

    def fake_dispatcher(_api_name, _parameters, _timeout_ms):
        return good_df

    def failed_raw_writer(_request):
        return RAW_WRITER_MODULE.RawPayloadWriteResult(
            ok=False,
            uri=None,
            attempt_seq=None,
            content_hash=None,
            bytes_written=0,
            started_at="2026-05-07T00:00:00+00:00",
            ended_at="2026-05-07T00:00:01+00:00",
            duration_ms=1,
            retry_count=0,
            error_type="timeout",
            error_message_redacted="timeout",
        )

    raw_failed = AKSHARE_FETCHER_MODULE.AkShareFetcher(
        api_dispatcher=fake_dispatcher,
        raw_writer=failed_raw_writer,
    ).Fetch(normalized, spec)
    assert raw_failed.attempt.status == "error"
    assert raw_failed.attempt.reason == "raw_payload_write_failed"
    assert raw_failed.extracted_fields == []
    assert raw_failed.raw_payload_hash is None
    assert raw_failed.raw_payload_ref is None


def test_t_fnd_025_fetch_success_path_with_raw_and_mapping() -> None:
    normalized = MODELS_MODULE.NormalizedInput(
        raw_ticker="600519",
        canonical_code="600519.SH",
        tushare_code="600519.SH",
        akshare_symbol="600519",
        exchange="SH",
        market="CN_A",
        start_date="2025-05-07",
        end_date="2026-05-07",
        current_date="2026-05-07",
        run_id="run-b",
        dispatch_id="dispatch-b",
        latest_report_period="20260331",
    )
    spec = MODELS_MODULE.ApiCallSpec(
        provider="akshare",
        api_name="stock_financial_abstract_ths",
        role="supplement",
        required=False,
        field_family="financial_indicators,income_statement",
        parameters={"symbol": "600519", "indicator": "20260331"},
        timeout_ms=1000,
        retry_limit=1,
    )
    dataframe = pd.DataFrame(
        [
            {
                "报告期": "20260331",
                "净利润": "123456",
                "营业总收入": "456789",
                "每股收益": "8.88",
                "净资产收益率": "12.5",
            }
        ]
    )

    def fake_dispatcher(_api_name, _parameters, _timeout_ms):
        return dataframe

    raw_writer_inputs: dict[str, object] = {}

    def success_raw_writer(_request):
        raw_writer_inputs["request"] = _request
        return RAW_WRITER_MODULE.RawPayloadWriteResult(
            ok=True,
            uri="viking://resources/workflow/run-b/frontline/fundamental_analyst/dispatch-b/provider_raw/akshare/stock_financial_abstract_ths/1.json",
            attempt_seq=1,
            content_hash="sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
            bytes_written=100,
            started_at="2026-05-07T00:00:00+00:00",
            ended_at="2026-05-07T00:00:01+00:00",
            duration_ms=1,
            retry_count=0,
            error_type=None,
            error_message_redacted=None,
        )

    result = AKSHARE_FETCHER_MODULE.AkShareFetcher(
        api_dispatcher=fake_dispatcher,
        raw_writer=success_raw_writer,
    ).Fetch(normalized, spec)
    assert result.attempt.status == "success"
    assert result.attempt.reason is None
    assert result.raw_payload_hash == "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    assert result.raw_payload_ref is not None
    assert result.attempt.attempt_seq == 1
    extracted_paths = {field_path for field_path, _, _ in result.extracted_fields}
    assert "financial_indicators.roe" in extracted_paths
    assert "income_statement.revenue" in extracted_paths
    assert "income_statement.net_profit" in extracted_paths
    assert "income_statement.eps" in extracted_paths
    raw_request = raw_writer_inputs["request"]
    assert raw_request.timeout_ms == RAW_WRITER_MODULE.RAW_PAYLOAD_WRITER_TIMEOUT_MS_V1
    assert raw_request.retry_limit == RAW_WRITER_MODULE.RAW_PAYLOAD_WRITER_RETRY_LIMIT_V1


def test_t_fnd_025_fetch_fails_when_raw_success_receipt_invalid() -> None:
    normalized = MODELS_MODULE.NormalizedInput(
        raw_ticker="600519",
        canonical_code="600519.SH",
        tushare_code="600519.SH",
        akshare_symbol="600519",
        exchange="SH",
        market="CN_A",
        start_date="2025-05-07",
        end_date="2026-05-07",
        current_date="2026-05-07",
        run_id="run-c",
        dispatch_id="dispatch-c",
        latest_report_period="20260331",
    )
    spec = MODELS_MODULE.ApiCallSpec(
        provider="akshare",
        api_name="stock_zh_a_spot_em",
        role="supplement",
        required=False,
        field_family="valuation",
        parameters={},
        timeout_ms=1000,
        retry_limit=1,
    )
    dataframe = pd.DataFrame(
        [
            {"代码": "600519", "最新价": 100.0, "总市值": 2000000, "市盈率-动态": 10.0, "市净率": 2.0},
        ]
    )

    def fake_dispatcher(_api_name, _parameters, _timeout_ms):
        return dataframe

    invalid_receipts = [
        RAW_WRITER_MODULE.RawPayloadWriteResult(
            ok=True,
            uri=None,
            attempt_seq=1,
            content_hash="sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
            bytes_written=100,
            started_at="2026-05-07T00:00:00+00:00",
            ended_at="2026-05-07T00:00:01+00:00",
            duration_ms=1,
            retry_count=0,
            error_type=None,
            error_message_redacted=None,
        ),
        RAW_WRITER_MODULE.RawPayloadWriteResult(
            ok=True,
            uri="viking://resources/workflow/run-c/frontline/fundamental_analyst/dispatch-c/provider_raw/akshare/stock_zh_a_spot_em/1.json",
            attempt_seq=1,
            content_hash="sha256:bad",
            bytes_written=100,
            started_at="2026-05-07T00:00:00+00:00",
            ended_at="2026-05-07T00:00:01+00:00",
            duration_ms=1,
            retry_count=0,
            error_type=None,
            error_message_redacted=None,
        ),
        RAW_WRITER_MODULE.RawPayloadWriteResult(
            ok=True,
            uri="viking://resources/workflow/run-c/frontline/fundamental_analyst/dispatch-c/provider_raw/akshare/stock_zh_a_spot_em/2.json",
            attempt_seq=1,
            content_hash="sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
            bytes_written=100,
            started_at="2026-05-07T00:00:00+00:00",
            ended_at="2026-05-07T00:00:01+00:00",
            duration_ms=1,
            retry_count=0,
            error_type=None,
            error_message_redacted=None,
        ),
    ]

    for receipt in invalid_receipts:
        def invalid_writer(_request, rec=receipt):
            return rec

        result = AKSHARE_FETCHER_MODULE.AkShareFetcher(
            api_dispatcher=fake_dispatcher,
            raw_writer=invalid_writer,
        ).Fetch(normalized, spec)
        assert result.attempt.status == "error"
        assert result.attempt.reason == "raw_payload_write_invalid_receipt"
        assert result.extracted_fields == []
        assert result.raw_payload_hash is None
        assert result.raw_payload_ref is None
