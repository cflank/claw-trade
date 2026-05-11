from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Any, Mapping

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_PYTHON_ROOT = REPO_ROOT / "openclaw_plugins" / "claw-trade-frontline-tools" / "python"
if str(SHARED_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(SHARED_PYTHON_ROOT))

from frontline_data_pack.config import load_frontline_provider_config  # noqa: E402
from frontline_data_pack.evidence import OpenVikingStatResult, OpenVikingWriteResult  # noqa: E402
from frontline_data_pack.errors import (  # noqa: E402
    TOOL_CONTEXT_INCOMPLETE,
    TOOL_WORKER_MISMATCH,
    FrontlineValidationError,
)
import frontline_data_pack.fundamentals_data_pack as fundamentals_data_pack_module  # noqa: E402
from frontline_data_pack.fundamentals_data_pack import BuildFundamentalDataPack  # noqa: E402
from frontline_data_pack.runtime_context import ToolRuntimeContext  # noqa: E402


def _runtime_context_payload(
    *,
    remove_fields: set[str] | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "run_id": "run-fnd-1",
        "stage": "frontline",
        "worker_id": "fundamental_analyst",
        "call_id": "call-fnd-1",
        "dispatch_id": "dispatch-fnd-1",
        "tool_name": "fundamental_fundamentals_data_pack",
        "evidence_root": "/tmp/evidence",
        "current_time": "2026-05-09T12:00:00Z",
    }
    if remove_fields is not None:
        for field_name in remove_fields:
            payload.pop(field_name, None)
    payload.update(overrides)
    return payload


def test_t_fnd_002_complete_when_company_valuation_and_financial_core_are_auditable() -> None:
    pack = _build_runner(
        call_registry={
            ("akshare", "company_info"): _company_info_provider(),
            ("akshare", "financial_abstract"): _financial_abstract_provider_with_revenue_net_profit(),
            ("akshare", "stock_zh_a_spot_em"): _valuation_provider(),
        }
    ).build(_tool_input(), _runtime_context())

    assert pack.quality.status == "complete"
    assert pack.domain == "fundamental"
    assert pack.domain_data["company_profile"]
    assert pack.domain_data["valuation_fields"].get("valuation.pe_ttm") is not None
    assert pack.domain_data["valuation_fields"].get("valuation.pb") is not None
    assert pack.domain_data["financial_fields"].get("financial_indicators.roe") is not None
    assert pack.domain_data["financial_fields"].get("financial_indicators.roa") is not None
    assert pack.domain_data["financial_fields"].get("financial_indicators.gross_margin") is not None
    assert pack.domain_data["financial_fields"].get("financial_indicators.netprofit_margin") is not None
    assert pack.domain_data["financial_fields"].get("financial_indicators.debt_to_assets") is not None
    assert pack.domain_data["financial_fields"].get("income_statement.revenue") is not None
    assert pack.domain_data["financial_fields"].get("income_statement.net_profit") is not None
    assert "cash_flow.operating_cash_flow" in pack.domain_data["missing_core_fields"]
    assert "本资料包未单列 TTM EPS 时，只能按 PE TTM 做相对估值讨论" in pack.reader_brief
    assert "如需给出价格区间测算结论，只能作为分析师情景假设，不得表述为数据源事实" in pack.reader_brief


def test_t_fnd_002_reader_brief_explains_eps_basis_when_eps_is_present() -> None:
    pack = _build_runner(
        call_registry={
            ("akshare", "company_info"): _company_info_provider(),
            ("akshare", "financial_abstract"): _financial_abstract_provider_without_core(),
            ("akshare", "stock_zh_a_spot_em"): _valuation_provider(),
        }
    ).build(_tool_input(), _runtime_context())

    assert pack.domain_data["financial_fields"].get("income_statement.eps") is not None
    assert "每股收益来自财报摘要对应报告期，不自动折算为 TTM EPS" in pack.reader_brief
    assert "不要把报告期 EPS 与 PE TTM 直接相乘" in pack.reader_brief
    assert "若使用全年 EPS、行业估值或历史估值进行测算，必须明确写出假设前提与推演口径" in pack.reader_brief


def test_t_fnd_002_complete_when_valuation_core_with_cashflow_and_missing_revenue_net_profit() -> None:
    pack = _build_runner(
        call_registry={
            ("akshare", "company_info"): _company_info_provider(),
            ("akshare", "financial_abstract"): _financial_abstract_provider_with_cashflow_only(),
            ("akshare", "stock_zh_a_spot_em"): _valuation_provider(),
        }
    ).build(_tool_input(), _runtime_context())

    assert pack.quality.status == "complete"
    assert pack.domain_data["financial_fields"].get("financial_indicators.roe") is not None
    assert pack.domain_data["cash_flow_fields"].get("cash_flow.operating_cash_flow") is not None
    assert "income_statement.revenue" in pack.domain_data["missing_core_fields"]
    assert "income_statement.net_profit" in pack.domain_data["missing_core_fields"]


def test_t_fnd_002_partial_when_company_exists_but_core_gaps_remain() -> None:
    pack = _build_runner(
        call_registry={
            ("akshare", "company_info"): _company_info_provider(),
            ("akshare", "financial_abstract"): _financial_abstract_provider_without_core(),
            ("akshare", "stock_zh_a_spot_em"): _valuation_provider_without_pe_pb(),
        }
    ).build(_tool_input(), _runtime_context())

    assert pack.quality.status == "partial"
    assert set(pack.domain_data["missing_core_fields"]) >= {
        "valuation.pe_ttm",
        "valuation.pb",
        "financial_indicators.roe",
        "income_statement.revenue",
        "income_statement.net_profit",
        "cash_flow.operating_cash_flow",
    }


def test_t_fnd_002_failed_when_core_providers_all_fail() -> None:
    pack = _build_runner(
        call_registry={
            ("akshare", "company_info"): _failing_provider(),
            ("akshare", "financial_abstract"): _failing_provider(),
            ("akshare", "stock_zh_a_spot_em"): _failing_provider(),
        }
    ).build(_tool_input(), _runtime_context())

    assert pack.quality.status == "failed"
    assert pack.domain_data["company_profile"] == {}


def test_t_fnd_002_partial_when_valuation_core_missing_fields_are_listed() -> None:
    pack = _build_runner(
        call_registry={
            ("akshare", "company_info"): _company_info_provider(),
            ("akshare", "financial_abstract"): _financial_abstract_provider_full(),
            ("akshare", "stock_zh_a_spot_em"): _valuation_provider_without_pe(),
        }
    ).build(_tool_input(), _runtime_context())

    assert pack.quality.status == "partial"
    assert "valuation.pe_ttm" in pack.domain_data["missing_core_fields"]


def test_t_fnd_002_partial_when_financial_and_cashflow_groups_both_missing() -> None:
    pack = _build_runner(
        call_registry={
            ("akshare", "company_info"): _company_info_provider(),
            ("akshare", "financial_abstract"): _financial_abstract_provider_with_roe_only(),
            ("akshare", "stock_zh_a_spot_em"): _valuation_provider(),
        }
    ).build(_tool_input(), _runtime_context())

    assert pack.quality.status == "partial"
    assert set(pack.domain_data["missing_core_fields"]) >= {
        "income_statement.revenue",
        "income_statement.net_profit",
        "cash_flow.operating_cash_flow",
    }


def test_t_fnd_002_default_window_uses_90_days_when_start_and_end_are_missing() -> None:
    pack = _build_runner(
        call_registry={
            ("akshare", "company_info"): _company_info_provider(),
            ("akshare", "financial_abstract"): _financial_abstract_provider_with_revenue_net_profit(),
            ("akshare", "stock_zh_a_spot_em"): _valuation_provider(),
        }
    ).build(
        _tool_input(start_date=None, end_date=None),
        _runtime_context(),
    )

    assert pack.input.end_date == "2026-05-09"
    assert pack.input.start_date == "2026-02-09"


def test_t_fnd_002_default_config_matrix_sources_are_enabled_and_unregistered_sources_fail_explicitly() -> None:
    pack = _build_runner(
        call_registry={
            ("akshare", "company_info"): _company_info_provider(),
            ("akshare", "financial_abstract"): _financial_abstract_provider_with_revenue_net_profit(),
            ("akshare", "stock_zh_a_spot_em"): _valuation_provider(),
        }
    ).build(_tool_input(), _runtime_context())

    matrix_attempts = [
        attempt
        for attempt in pack.provider_attempts
        if attempt.provider in {"eastmoney_direct", "baostock", "efinance", "tushare"}
    ]

    assert matrix_attempts
    assert not any(attempt.status == "config_blocked" for attempt in matrix_attempts)
    remote_matrix_attempts = [attempt for attempt in matrix_attempts if attempt.status != "cache_miss"]
    assert remote_matrix_attempts
    assert all(attempt.status == "error" for attempt in remote_matrix_attempts)
    assert all(attempt.error_message_redacted == "provider caller 未注册" for attempt in remote_matrix_attempts)
    accepted_field_providers = {
        field["provider"]
        for section in (
            pack.domain_data["company_profile"],
            pack.domain_data["valuation_fields"],
            pack.domain_data["financial_fields"],
            pack.domain_data["cash_flow_fields"],
        )
        for field in section.values()
    }
    assert not (accepted_field_providers & {"eastmoney_direct", "baostock", "efinance", "tushare"})
    assert pack.quality.status == "complete"


@pytest.mark.parametrize(
    ("runtime_context", "expected_code"),
    [
        pytest.param(
            _runtime_context_payload(remove_fields={"worker_id"}),
            TOOL_CONTEXT_INCOMPLETE,
            id="worker_id_missing",
        ),
        pytest.param(
            _runtime_context_payload(worker_id="news_analyst"),
            TOOL_WORKER_MISMATCH,
            id="worker_id_mismatch",
        ),
        pytest.param(
            _runtime_context_payload(tool_name="fundamental.other_tool"),
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
def test_t_fnd_002_context_errors_do_not_call_provider_or_generate_attempts(
    runtime_context: dict[str, Any],
    expected_code: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fail_provider_attempt_ctor(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("context error path must not construct ProviderAttempt")

    monkeypatch.setattr(fundamentals_data_pack_module, "ProviderAttempt", _fail_provider_attempt_ctor)
    with pytest.raises(FrontlineValidationError) as error:
        _build_fail_fast_runner().build(_tool_input(), runtime_context)

    assert error.value.code == expected_code


def test_t_fnd_002_raw_provider_l2_write_failed_must_not_fake_success() -> None:
    pack = _build_runner(
        call_registry={
            ("akshare", "company_info"): _company_info_provider(),
            ("akshare", "financial_abstract"): _financial_abstract_provider_with_revenue_net_profit(),
            ("akshare", "stock_zh_a_spot_em"): _valuation_provider(),
        },
        evidence_client=_FailPathL2Client(fail_path_markers={"/provider_raw/"}),
    ).build(_tool_input(), _runtime_context())

    assert pack.quality.status == "failed"
    assert pack.raw_payload_refs == []


def test_t_fnd_002_attempts_l2_write_failed_must_not_fake_success() -> None:
    pack = _build_runner(
        call_registry={
            ("akshare", "company_info"): _company_info_provider(),
            ("akshare", "financial_abstract"): _financial_abstract_provider_with_revenue_net_profit(),
            ("akshare", "stock_zh_a_spot_em"): _valuation_provider(),
        },
        evidence_client=_FailPathL2Client(fail_path_markers={"/provider_attempts.json"}),
    ).build(_tool_input(), _runtime_context())

    assert pack.quality.status == "failed"
    assert "l2_write_failed:provider_attempts" in pack.diagnostic_flags


def test_t_fnd_002_pack_l2_write_failed_must_not_fake_success() -> None:
    pack = _build_runner(
        call_registry={
            ("akshare", "company_info"): _company_info_provider(),
            ("akshare", "financial_abstract"): _financial_abstract_provider_with_revenue_net_profit(),
            ("akshare", "stock_zh_a_spot_em"): _valuation_provider(),
        },
        evidence_client=_FailPathL2Client(fail_path_markers={"/normalized_pack.json"}),
    ).build(_tool_input(), _runtime_context())

    assert pack.quality.status == "failed"
    assert "l2_write_failed:normalized_pack" in pack.diagnostic_flags


class _MongoCollection:
    def find_one(self, _query: dict[str, Any]) -> dict[str, Any] | None:
        return None

    def update_one(self, _flt: dict[str, Any], _update: dict[str, Any], *, upsert: bool) -> None:
        _ = upsert
        return None

    def insert_one(self, _doc: dict[str, Any]) -> None:
        return None


class _InMemoryL2Client:
    def __init__(self) -> None:
        self._content_by_uri: dict[str, bytes] = {}

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
        return OpenVikingWriteResult(receipt_id="receipt-fnd-1")

    def stat(self, *, uri: str) -> OpenVikingStatResult:
        content = self._content_by_uri[uri]
        digest = hashlib.sha256(content).hexdigest()
        return OpenVikingStatResult(size_bytes=len(content), sha256=f"sha256:{digest}", exists=True)

    def read(self, *, uri: str) -> bytes:
        return self._content_by_uri[uri]


class _FailPathL2Client(_InMemoryL2Client):
    def __init__(self, *, fail_path_markers: set[str]) -> None:
        super().__init__()
        self._fail_path_markers = fail_path_markers

    def write(
        self,
        *,
        uri: str,
        content_bytes: bytes,
        content_type: str,
        metadata: Mapping[str, str],
    ) -> OpenVikingWriteResult:
        if any(marker in uri for marker in self._fail_path_markers):
            raise RuntimeError("l2 write failed")
        return super().write(uri=uri, content_bytes=content_bytes, content_type=content_type, metadata=metadata)


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


def _build_runner(
    *,
    call_registry: Mapping[tuple[str, str], Any],
    evidence_client: _InMemoryL2Client | None = None,
) -> BuildFundamentalDataPack:
    return BuildFundamentalDataPack(
        config=load_frontline_provider_config(_base_env()),
        provider_call_registry=call_registry,
        evidence_client=_InMemoryL2Client() if evidence_client is None else evidence_client,
        provider_cache_collection=_MongoCollection(),
        provider_attempts_collection=_MongoCollection(),
        normalized_fundamental_collection=_MongoCollection(),
    )


def _build_fail_fast_runner() -> BuildFundamentalDataPack:
    def _fail_fast_provider(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("context error path must not call provider registry")

    fail_fast_registry = {
        ("akshare", "company_info"): _fail_fast_provider,
        ("akshare", "financial_abstract"): _fail_fast_provider,
        ("akshare", "stock_zh_a_spot_em"): _fail_fast_provider,
    }
    return BuildFundamentalDataPack(
        config=load_frontline_provider_config(_base_env()),
        provider_call_registry=fail_fast_registry,
        evidence_client=_FailFastL2Client(),
        provider_cache_collection=_FailFastCollection(),
        provider_attempts_collection=_FailFastCollection(),
        normalized_fundamental_collection=_FailFastCollection(),
    )


def _base_env() -> dict[str, str]:
    return {
        "CN_A_MONGODB_URI": "mongodb://localhost:27017/claw_trade",
        "CLAW_TRADE_OPENVIKING_BASE_URI": "https://openviking.internal",
        "CLAW_TRADE_OPENVIKING_AUTH_MODE": "none",
    }


def _tool_input(
    *,
    start_date: str | None = "2026-05-01",
    end_date: str | None = "2026-05-09",
) -> dict[str, Any]:
    return {
        "ticker": "600519.SH",
        "market": "CN_A",
        "company_name": "贵州茅台",
        "start_date": start_date,
        "end_date": end_date,
    }


def _runtime_context() -> ToolRuntimeContext:
    return ToolRuntimeContext(
        run_id="run-fnd-1",
        stage="frontline",
        worker_id="fundamental_analyst",
        call_id="call-fnd-1",
        dispatch_id="dispatch-fnd-1",
        tool_name="fundamental_fundamentals_data_pack",
        evidence_root="/tmp/evidence",
        current_time="2026-05-09T12:00:00Z",
    )


def _company_info_provider():
    def _provider(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "rows": [
                {"field_name": "company_profile.industry", "value": "酿酒", "unit": "text"},
                {"field_name": "company_profile.main_business", "value": "白酒生产与销售", "unit": "text"},
            ]
        }

    return _provider


def _valuation_provider():
    def _provider(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "rows": [
                {
                    "price_context.close": 1688.88,
                    "valuation.total_mv": 2123000000000.0,
                    "valuation.pe_ttm": 25.2,
                    "valuation.pb": 8.8,
                    "price_context.trade_date": "2026-05-09",
                }
            ]
        }

    return _provider


def _valuation_provider_without_pe_pb():
    def _provider(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "rows": [
                {
                    "price_context.close": 1688.88,
                    "valuation.total_mv": 2123000000000.0,
                    "price_context.trade_date": "2026-05-09",
                }
            ]
        }

    return _provider


def _valuation_provider_without_pe():
    def _provider(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "rows": [
                {
                    "price_context.close": 1688.88,
                    "valuation.total_mv": 2123000000000.0,
                    "valuation.pb": 8.8,
                    "price_context.trade_date": "2026-05-09",
                }
            ]
        }

    return _provider


def _financial_abstract_provider_full():
    def _provider(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "rows": [
                {
                    "report_period": "2025-12-31",
                    "financial_indicators.roe": 31.2,
                    "income_statement.revenue": 321654987.0,
                    "income_statement.net_profit": 123456789.0,
                    "cash_flow.operating_cash_flow": 100000000.0,
                }
            ]
        }

    return _provider


def _financial_abstract_provider_with_revenue_net_profit():
    def _provider(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "rows": [
                {
                    "report_period": "2025-12-31",
                    "financial_indicators.roe": 31.2,
                    "financial_indicators.roa": 18.6,
                    "financial_indicators.gross_margin": 90.1,
                    "financial_indicators.netprofit_margin": 52.2,
                    "financial_indicators.debt_to_assets": 12.1,
                    "income_statement.revenue": 321654987.0,
                    "income_statement.net_profit": 123456789.0,
                }
            ]
        }

    return _provider


def _financial_abstract_provider_with_cashflow_only():
    def _provider(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "rows": [
                {
                    "report_period": "2025-12-31",
                    "financial_indicators.roe": 31.2,
                    "cash_flow.operating_cash_flow": 100000000.0,
                }
            ]
        }

    return _provider


def _financial_abstract_provider_with_roe_only():
    def _provider(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "rows": [
                {
                    "report_period": "2025-12-31",
                    "financial_indicators.roe": 31.2,
                }
            ]
        }

    return _provider


def _financial_abstract_provider_without_core():
    def _provider(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "rows": [
                {
                    "report_period": "2025-12-31",
                    "income_statement.eps": 8.88,
                }
            ]
        }

    return _provider


def _failing_provider():
    def _provider(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("provider down")

    return _provider
