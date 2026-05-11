from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

SCRIPTS_ROOT = Path("agents/fundamental_analyst/skills/cn-a-fundamental-data/scripts")


def test_t_fnd_017_dispatch_rejects_non_whitelist_api() -> None:
    with pytest.raises(TUSHARE_FETCHER_MODULE.TushareFetcherError) as exc_info:
        TUSHARE_FETCHER_MODULE.invoke_tushare_api_by_dispatch(
            token="token-x",
            api_name="not_approved_api",
            parameters={},
            timeout_ms=1000,
        )
    assert exc_info.value.code == TUSHARE_FETCHER_MODULE.FND_TUSHARE_API_NOT_APPROVED


def test_t_fnd_017_dispatch_token_missing_must_fail_instead_of_empty_success() -> None:
    with pytest.raises(TUSHARE_FETCHER_MODULE.TushareFetcherError) as exc_info:
        TUSHARE_FETCHER_MODULE.invoke_tushare_api_by_dispatch(
            token="",
            api_name="daily_basic",
            parameters={},
            timeout_ms=1000,
        )
    assert exc_info.value.code == TUSHARE_FETCHER_MODULE.FND_TUSHARE_TOKEN_MISSING


def test_t_fnd_017_dispatch_calls_only_approved_method() -> None:
    fake_client = _FakeTushareClient()
    _inject_fake_tushare_module(fake_client)
    result = TUSHARE_FETCHER_MODULE.invoke_tushare_api_by_dispatch(
        token="token-x",
        api_name="daily_basic",
        parameters={"ts_code": "600519.SH", "trade_date": "20260507"},
        timeout_ms=1000,
    )
    assert isinstance(result, _FakeDataFrame)
    assert fake_client.called_api_names == ["daily_basic"]


def test_t_fnd_018_validate_columns_flags_schema_changed_when_columns_missing() -> None:
    validation = TUSHARE_FETCHER_MODULE.ValidateTushareColumns(
        "daily_basic",
        ["ts_code", "trade_date", "close", "pb", "total_mv"],
    )
    assert validation.is_schema_changed is True
    assert "float_mv" in validation.missing_columns
    assert "pe_ttm" in validation.missing_columns


def test_t_fnd_020_map_tushare_columns_uses_latest_end_date_and_skips_empty_values() -> None:
    dataframe = _FakeDataFrame(
        ["ts_code", "end_date", "bz_item", "bz_sales", "bz_profit"],
        [
            {
                "ts_code": "600519.SH",
                "end_date": "20241231",
                "bz_item": "旧业务",
                "bz_sales": "100.0",
                "bz_profit": "20.0",
            },
            {
                "ts_code": "600519.SH",
                "end_date": "20250331",
                "bz_item": "新业务",
                "bz_sales": "",
                "bz_profit": "30.0",
            },
        ],
    )
    extracted = TUSHARE_FETCHER_MODULE.map_tushare_columns_to_pack_fields_v1(
        "fina_mainbz",
        dataframe,
        "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        "viking://resources/workflow/run-1/frontline/fundamental_analyst/dispatch-1/provider_raw/tushare/fina_mainbz/1.json",
    )
    by_path = {field_path: payload for field_path, payload, _ in extracted}
    assert by_path["business_segments.items"]["value"] == "新业务"
    assert by_path["business_segments.items"]["unit"] == "text"
    assert by_path["business_segments.profit"]["value"] == "30.0"
    assert by_path["business_segments.profit"]["provider"] == "tushare"
    assert "business_segments.sales" not in by_path


def test_t_fnd_020_map_tushare_columns_picks_latest_trade_date_when_end_date_absent() -> None:
    dataframe = _FakeDataFrame(
        ["ts_code", "trade_date", "close", "pe_ttm", "pb", "total_mv", "float_mv"],
        [
            {"ts_code": "600519.SH", "trade_date": "20260506", "close": 1600.0, "pe_ttm": 20.0, "pb": 7.0, "total_mv": 1},
            {"ts_code": "600519.SH", "trade_date": "20260507", "close": 1700.0, "pe_ttm": 21.0, "pb": 8.0, "total_mv": 2},
        ],
    )
    extracted = TUSHARE_FETCHER_MODULE.map_tushare_columns_to_pack_fields_v1(
        "daily_basic",
        dataframe,
        "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        "viking://resources/workflow/run-1/frontline/fundamental_analyst/dispatch-1/provider_raw/tushare/daily_basic/1.json",
    )
    by_path = {field_path: payload for field_path, payload, _ in extracted}
    assert by_path["valuation.pe_ttm"]["value"] == 21.0
    assert by_path["valuation.pb"]["value"] == 8.0
    assert by_path["valuation.total_mv"]["value"] == 2
    assert by_path["valuation.total_mv"]["unit"] == "10k_cny"


def test_t_fnd_019_fetch_missing_token_returns_skipped_attempt() -> None:
    fetcher = TUSHARE_FETCHER_MODULE.TushareFetcher(
        _config(token=None),
        invoke_api=lambda *_args, **_kwargs: _FakeDataFrame(["ts_code"], []),
    )
    result = fetcher.Fetch(_normalized_input(), _daily_basic_spec())
    assert result.attempt.status == "skipped"
    assert result.attempt.reason == "missing_token"
    assert result.attempt.retry_count == 0
    assert result.attempt.response_row_count == 0
    assert result.attempt.request_params_redacted.get("api_name") == "daily_basic"


def test_t_fnd_019_fetch_timeout_retries_and_fills_attempt() -> None:
    call_count = {"n": 0}

    def raise_timeout(*_args, **_kwargs):
        call_count["n"] += 1
        raise TimeoutError("network timeout")

    fetcher = TUSHARE_FETCHER_MODULE.TushareFetcher(
        _config(token="token-x"),
        invoke_api=raise_timeout,
        sleep_fn=lambda _seconds: None,
    )
    spec = _daily_basic_spec(retry_limit=1)
    result = fetcher.Fetch(_normalized_input(), spec)
    assert call_count["n"] == 2
    assert result.attempt.status == "timeout"
    assert result.attempt.reason == "timeout"
    assert result.attempt.retry_count == 1
    assert result.attempt.error_type == "timeout"


def test_t_fnd_019_fetch_permission_or_quota_classification() -> None:
    def raise_quota(*_args, **_kwargs):
        raise RuntimeError("接口访问频次超限，积分不足")

    fetcher = TUSHARE_FETCHER_MODULE.TushareFetcher(
        _config(token="token-x"),
        invoke_api=raise_quota,
    )
    result = fetcher.Fetch(_normalized_input(), _daily_basic_spec())
    assert result.attempt.status == "error"
    assert result.attempt.reason == "permission_or_quota"
    assert result.attempt.error_type in {"quota_exceeded", "permission_denied"}


def test_t_fnd_019_fetch_provider_error_is_sanitized() -> None:
    def raise_provider_error(*_args, **_kwargs):
        raise RuntimeError("token=abcdefghijklmnopqrstuvwxyz012345 provider failed")

    fetcher = TUSHARE_FETCHER_MODULE.TushareFetcher(
        _config(token="token-x"),
        invoke_api=raise_provider_error,
    )
    result = fetcher.Fetch(_normalized_input(), _daily_basic_spec())
    assert result.attempt.status == "error"
    assert result.attempt.reason == "provider_error"
    assert result.attempt.error_message_redacted is not None
    assert "abcdefghijklmnopqrstuvwxyz012345" not in result.attempt.error_message_redacted


def test_t_fnd_019_fetch_distinguishes_empty_and_schema_invalid() -> None:
    fetcher_empty = TUSHARE_FETCHER_MODULE.TushareFetcher(
        _config(token="token-x"),
        invoke_api=lambda *_args, **_kwargs: _FakeDataFrame(
            ["ts_code", "trade_date", "close", "pe_ttm", "pb", "total_mv", "float_mv"],
            [],
        ),
    )
    empty_result = fetcher_empty.Fetch(_normalized_input(), _daily_basic_spec())
    assert empty_result.attempt.status == "empty"
    assert empty_result.attempt.reason == "empty_response"

    fetcher_schema = TUSHARE_FETCHER_MODULE.TushareFetcher(
        _config(token="token-x"),
        invoke_api=lambda *_args, **_kwargs: _FakeDataFrame(
            ["ts_code", "trade_date", "close", "pe_ttm", "pb", "total_mv"],
            [{"ts_code": "600519.SH"}],
        ),
    )
    schema_result = fetcher_schema.Fetch(_normalized_input(), _daily_basic_spec())
    assert schema_result.attempt.status == "schema_invalid"
    assert schema_result.attempt.reason == "schema_changed"
    assert schema_result.schema_changed is True


def test_t_fnd_021_fetch_blocks_raw_write_failure() -> None:
    raw_write_input: dict[str, Any] = {}

    def failed_raw_writer(request_input):
        raw_write_input["request"] = request_input
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

    fetcher = TUSHARE_FETCHER_MODULE.TushareFetcher(
        _config(token="token-x"),
        invoke_api=lambda *_args, **_kwargs: _FakeDataFrame(
            ["ts_code", "trade_date", "close", "pe_ttm", "pb", "total_mv", "float_mv"],
            [{"ts_code": "600519.SH", "trade_date": "20260507", "close": 1700.0, "pe_ttm": 21.0, "pb": 8.0, "total_mv": 2}],
        ),
        raw_writer=failed_raw_writer,
    )
    result = fetcher.Fetch(_normalized_input(), _daily_basic_spec())
    assert result.attempt.status == "error"
    assert result.attempt.reason == "raw_payload_write_failed"
    assert result.raw_payload_hash is None
    assert result.raw_payload_ref is None
    assert result.extracted_fields == []
    assert isinstance(result.attempt.attempt_seq, int)
    assert result.attempt.attempt_seq >= 1
    raw_request = raw_write_input["request"]
    assert raw_request.api_name == "daily_basic"
    assert raw_request.provider == "tushare"
    assert raw_request.timeout_ms == RAW_WRITER_MODULE.RAW_PAYLOAD_WRITER_TIMEOUT_MS_V1
    assert raw_request.retry_limit == RAW_WRITER_MODULE.RAW_PAYLOAD_WRITER_RETRY_LIMIT_V1


def test_t_fnd_021_fetch_blocks_invalid_raw_write_receipt() -> None:
    fetcher = TUSHARE_FETCHER_MODULE.TushareFetcher(
        _config(token="token-x"),
        invoke_api=lambda *_args, **_kwargs: _FakeDataFrame(
            ["ts_code", "trade_date", "close", "pe_ttm", "pb", "total_mv", "float_mv"],
            [{"ts_code": "600519.SH", "trade_date": "20260507", "close": 1700.0, "pe_ttm": 21.0, "pb": 8.0, "total_mv": 2}],
        ),
        raw_writer=lambda _request: RAW_WRITER_MODULE.RawPayloadWriteResult(
            ok=True,
            uri="viking://resources/workflow/run-1/frontline/fundamental_analyst/dispatch-1/provider_raw/tushare/daily_basic/2.json",
            attempt_seq=2,
            content_hash="sha256:bad",
            bytes_written=100,
            started_at="2026-05-07T00:00:00+00:00",
            ended_at="2026-05-07T00:00:01+00:00",
            duration_ms=1,
            retry_count=0,
            error_type=None,
            error_message_redacted=None,
        ),
    )
    result = fetcher.Fetch(_normalized_input(), _daily_basic_spec())
    assert result.attempt.status == "error"
    assert result.attempt.reason == "raw_payload_write_invalid_receipt"
    assert result.extracted_fields == []
    assert result.raw_payload_hash is None
    assert result.raw_payload_ref is None


def test_t_fnd_021_fetch_success_writes_raw_and_maps_fields() -> None:
    fetcher = TUSHARE_FETCHER_MODULE.TushareFetcher(
        _config(token="token-x"),
        invoke_api=lambda *_args, **_kwargs: _FakeDataFrame(
            ["ts_code", "trade_date", "close", "pe_ttm", "pb", "total_mv", "float_mv"],
            [{"ts_code": "600519.SH", "trade_date": "20260507", "close": 1700.0, "pe_ttm": 21.0, "pb": 8.0, "total_mv": 2}],
        ),
        raw_writer=lambda _request: RAW_WRITER_MODULE.RawPayloadWriteResult(
            ok=True,
            uri="viking://resources/workflow/run-1/frontline/fundamental_analyst/dispatch-1/provider_raw/tushare/daily_basic/9.json",
            attempt_seq=9,
            content_hash="sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
            bytes_written=100,
            started_at="2026-05-07T00:00:00+00:00",
            ended_at="2026-05-07T00:00:01+00:00",
            duration_ms=1,
            retry_count=0,
            error_type=None,
            error_message_redacted=None,
        ),
    )
    result = fetcher.Fetch(_normalized_input(), _daily_basic_spec())
    assert result.attempt.status == "success"
    assert result.attempt.reason is None
    assert result.raw_payload_hash == "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    assert result.raw_payload_ref is not None
    assert result.attempt.attempt_seq == 9
    assert result.attempt.raw_payload_hash == result.raw_payload_hash
    assert result.attempt.raw_payload_ref == result.raw_payload_ref
    assert result.attempt.field_coverage == [
        "price_context.close",
        "price_context.trade_date",
        "valuation.pb",
        "valuation.pe_ttm",
        "valuation.total_mv",
    ]
    assert result.attempt.as_of == "20260507"
    assert result.attempt.fetched_at is not None
    by_path = {field_path: payload for field_path, payload, _ in result.extracted_fields}
    assert by_path["valuation.pe_ttm"]["value"] == 21.0
    assert by_path["valuation.pe_ttm"]["provider"] == "tushare"
    assert by_path["valuation.pe_ttm"]["api_name"] == "daily_basic"


def _daily_basic_spec(*, retry_limit: int = 1):
    return MODELS_MODULE.ApiCallSpec(
        provider="tushare",
        api_name="daily_basic",
        role="primary",
        required=True,
        field_family="price_context,valuation",
        parameters={
            "ts_code": "600519.SH",
            "trade_date": "20260507",
            "fields": "ts_code,trade_date,close,pe_ttm,pb,total_mv,float_mv",
        },
        timeout_ms=1000,
        retry_limit=retry_limit,
    )


def _normalized_input():
    return MODELS_MODULE.NormalizedInput(
        raw_ticker="600519",
        canonical_code="600519.SH",
        tushare_code="600519.SH",
        akshare_symbol="600519",
        exchange="SH",
        market="CN_A",
        start_date="2026-01-01",
        end_date="2026-05-07",
        current_date="2026-05-07",
        run_id="run-1",
        dispatch_id="dispatch-1",
        latest_report_period="20260331",
    )


def _config(*, token: str | None):
    return CONFIG_MODULE.FundamentalDataConfig(
        tushare_token=token,
        mongodb_uri="mongodb://user:pass@localhost:27017/?authSource=admin",
        mongodb_database="claw_trade",
        mongodb_cache_collection="cn_a_fundamental_cache",
        openviking_endpoint="https://openviking.example.internal",
        openviking_api_key="openviking-key",
        openviking_workspace="workspace-a",
        tool_enabled=True,
        disable_tushare=False,
        disable_akshare=False,
        cache_read_only=False,
    )


class _FakeColumns(list[str]):
    def tolist(self) -> list[str]:
        return list(self)


class _FakeDataFrame:
    def __init__(self, columns: list[str], rows: list[dict[str, Any]]) -> None:
        self.columns = _FakeColumns(columns)
        self._rows = rows

    @property
    def empty(self) -> bool:
        return len(self._rows) == 0

    @property
    def shape(self) -> tuple[int, int]:
        return (len(self._rows), len(self.columns))


class _FakeTushareClient:
    def __init__(self) -> None:
        self.called_api_names: list[str] = []

    def daily_basic(self, **_kwargs):
        self.called_api_names.append("daily_basic")
        return _FakeDataFrame(
            ["ts_code", "trade_date", "close", "pe_ttm", "pb", "total_mv", "float_mv"],
            [{"ts_code": "600519.SH", "trade_date": "20260507"}],
        )

    def stock_company(self, **_kwargs):  # pragma: no cover - dispatch table completeness
        self.called_api_names.append("stock_company")
        return _FakeDataFrame(["ts_code", "name", "province", "city", "introduction", "main_business"], [])

    def stock_basic(self, **_kwargs):  # pragma: no cover - dispatch table completeness
        self.called_api_names.append("stock_basic")
        return _FakeDataFrame(["ts_code", "name", "industry", "market", "list_date"], [])

    def fina_indicator(self, **_kwargs):  # pragma: no cover - dispatch table completeness
        self.called_api_names.append("fina_indicator")
        return _FakeDataFrame(
            ["ts_code", "end_date", "ann_date", "roe", "roa", "grossprofit_margin", "netprofit_margin", "debt_to_assets"],
            [],
        )

    def income(self, **_kwargs):  # pragma: no cover - dispatch table completeness
        self.called_api_names.append("income")
        return _FakeDataFrame(["ts_code", "end_date", "ann_date", "revenue", "n_income", "basic_eps"], [])

    def balancesheet(self, **_kwargs):  # pragma: no cover - dispatch table completeness
        self.called_api_names.append("balancesheet")
        return _FakeDataFrame(
            ["ts_code", "end_date", "ann_date", "total_assets", "total_liab", "total_hldr_eqy_exc_min_int"],
            [],
        )

    def cashflow(self, **_kwargs):  # pragma: no cover - dispatch table completeness
        self.called_api_names.append("cashflow")
        return _FakeDataFrame(["ts_code", "end_date", "ann_date", "n_cashflow_act"], [])

    def fina_mainbz(self, **_kwargs):  # pragma: no cover - dispatch table completeness
        self.called_api_names.append("fina_mainbz")
        return _FakeDataFrame(["ts_code", "end_date", "bz_item", "bz_sales", "bz_profit"], [])

    def dividend(self, **_kwargs):  # pragma: no cover - dispatch table completeness
        self.called_api_names.append("dividend")
        return _FakeDataFrame(["ts_code", "ann_date", "end_date", "record_date", "ex_date", "stk_div", "cash_div_tax"], [])

    def top10_holders(self, **_kwargs):  # pragma: no cover - dispatch table completeness
        self.called_api_names.append("top10_holders")
        return _FakeDataFrame(["ts_code", "ann_date", "end_date", "holder_name", "hold_amount", "hold_ratio"], [])

    def top10_floatholders(self, **_kwargs):  # pragma: no cover - dispatch table completeness
        self.called_api_names.append("top10_floatholders")
        return _FakeDataFrame(["ts_code", "ann_date", "end_date", "holder_name", "hold_amount", "hold_ratio"], [])


def _inject_fake_tushare_module(client: _FakeTushareClient) -> None:
    class _FakeTushareModule:
        @staticmethod
        def pro_api(_token: str):
            return client

    sys.modules["tushare"] = _FakeTushareModule()  # type: ignore[assignment]


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


CONFIG_MODULE = _load_script_module("config")
MODELS_MODULE = _load_script_module("models")
RAW_WRITER_MODULE = _load_script_module("raw_payload_writer")
TUSHARE_FETCHER_MODULE = _load_script_module("tushare_fetcher")
