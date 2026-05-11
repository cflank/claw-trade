from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from config import SOCIAL_SCHEMA_VERSION, SocialDataConfig  # noqa: E402
from cache import _endpoint_required_fields_missing  # noqa: E402
from profile import DateWindow  # noqa: E402
from providers import (  # noqa: E402
    SOCIAL_RAW_HASH_MISMATCH,
    SOCIAL_ROW_EVIDENCE_INCOMPLETE,
    SOCIAL_PROVIDER_EMPTY,
    SOCIAL_PROVIDER_FORBIDDEN_SOURCE,
    SOCIAL_PROVIDER_TIMEOUT,
    NormalizedProviderRow,
    ProviderExecutionResult,
    RawProviderResult,
    fetch_provider_payload,
    load_rows_by_raw_payload_ref,
    normalize_provider_payload,
    normalize_provider_rows,
    ProviderSpec,
    ProviderQuery,
    SocialProviderNormalizationError,
    SocialProviderPlanError,
    build_provider_plan,
)


def test_build_provider_plan_for_600519_contains_four_p0_endpoints() -> None:
    plan = build_provider_plan(
        profile=_target_profile_600519(),
        date_window=_date_window(),
        config=_build_config(p1_hot_up_enabled=False),
    )

    assert [query.endpoint for query in plan] == [
        "stock_hot_rank_latest_em",
        "stock_hot_keyword_em",
        "stock_hot_rank_relate_em",
        "stock_hot_rank_em",
    ]


def test_build_provider_plan_includes_stock_hot_up_em_when_p1_enabled() -> None:
    plan = build_provider_plan(
        profile=_target_profile_600519(),
        date_window=_date_window(),
        config=_build_config(p1_hot_up_enabled=True),
    )

    assert [query.endpoint for query in plan] == [
        "stock_hot_rank_latest_em",
        "stock_hot_keyword_em",
        "stock_hot_rank_relate_em",
        "stock_hot_rank_em",
        "stock_hot_up_em",
    ]


def test_build_provider_plan_raises_when_forbidden_source_endpoint_present() -> None:
    forbidden_specs = (
        ProviderSpec(
            provider="akshare",
            endpoint="stock_hot_follow_xq",
            priority="P1",
            role="forbidden_source",
            enabled=True,
            required_for_complete=False,
            timeout_seconds=10,
            max_rows=100,
        ),
    )

    with pytest.raises(SocialProviderPlanError) as exc_info:
        build_provider_plan(
            profile=_target_profile_600519(),
            date_window=_date_window(),
            config=_build_config(p1_hot_up_enabled=True),
            provider_specs_override=forbidden_specs,
        )

    assert exc_info.value.code == SOCIAL_PROVIDER_FORBIDDEN_SOURCE


def test_query_fingerprint_is_stable_for_same_endpoint_and_params() -> None:
    first_plan = build_provider_plan(
        profile=_target_profile_600519(),
        date_window=_date_window(),
        config=_build_config(p1_hot_up_enabled=True),
    )
    second_plan = build_provider_plan(
        profile=_target_profile_600519(),
        date_window=_date_window(),
        config=_build_config(p1_hot_up_enabled=True),
    )

    first_map = {query.endpoint: query.query_fingerprint for query in first_plan}
    second_map = {query.endpoint: query.query_fingerprint for query in second_plan}
    assert first_map == second_map


def test_fetch_provider_payload_calls_target_endpoint_and_records_elapsed_ms(monkeypatch: pytest.MonkeyPatch) -> None:
    query = _provider_query(endpoint="stock_hot_rank_latest_em", timeout_seconds=8)

    class _FakeFrame:
        def to_dict(self, *, orient: str) -> list[dict[str, object]]:
            assert orient == "records"
            return [{"代码": "600519", "排名": 1}]

    monkeypatch.setattr("providers._call_akshare_endpoint", lambda endpoint, params: _FakeFrame())
    monkeypatch.setattr("providers._call_with_timeout", lambda timeout_seconds, call: call())

    result = fetch_provider_payload(query)

    assert result.status == "success"
    assert result.ok is True
    assert result.raw_count == 1
    assert result.elapsed_ms >= 0
    assert result.payload_hash is not None


def test_fetch_provider_payload_returns_empty_status_when_provider_rows_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    query = _provider_query(endpoint="stock_hot_keyword_em", timeout_seconds=10)

    class _FakeEmptyFrame:
        def to_dict(self, *, orient: str) -> list[dict[str, object]]:
            assert orient == "records"
            return []

    monkeypatch.setattr("providers._call_akshare_endpoint", lambda endpoint, params: _FakeEmptyFrame())
    monkeypatch.setattr("providers._call_with_timeout", lambda timeout_seconds, call: call())

    result = fetch_provider_payload(query)

    assert result.status == "empty"
    assert result.ok is False
    assert result.raw_count == 0
    assert result.empty_reason == SOCIAL_PROVIDER_EMPTY


def test_fetch_provider_payload_redacts_sensitive_error_message(monkeypatch: pytest.MonkeyPatch) -> None:
    query = _provider_query(endpoint="stock_hot_rank_relate_em", timeout_seconds=10)

    def _raise_sensitive_error(endpoint: str, params: dict[str, object]) -> RawProviderResult:
        raise RuntimeError("cookie=abc; token=def; authorization=bearer xxx")

    monkeypatch.setattr("providers._call_akshare_endpoint", _raise_sensitive_error)
    monkeypatch.setattr("providers._call_with_timeout", lambda timeout_seconds, call: call())

    result = fetch_provider_payload(query)
    assert result.status == "error"
    assert result.ok is False
    assert result.error is not None
    message = result.error["message"].lower()
    assert "credential" not in message
    assert "cookie" not in message
    assert "token" not in message
    assert "secret" not in message
    assert "authorization" not in message


def test_fetch_provider_payload_marks_timeout_status(monkeypatch: pytest.MonkeyPatch) -> None:
    query = _provider_query(endpoint="stock_hot_rank_latest_em", timeout_seconds=10)
    monkeypatch.setattr(
        "providers._call_with_timeout",
        lambda timeout_seconds, call: (_ for _ in ()).throw(TimeoutError("timeout")),
    )

    result = fetch_provider_payload(query)
    assert result.status == "timeout"
    assert result.ok is False
    assert result.raw_count == 0
    assert result.error == {
        "code": SOCIAL_PROVIDER_TIMEOUT,
        "message": "endpoint 调用超时（10s）",
    }


def test_fetch_provider_payload_uses_default_timeout_when_query_timeout_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    query = _provider_query(endpoint="stock_hot_keyword_em", timeout_seconds=0)
    captured: dict[str, int] = {}

    def _capture_timeout(timeout_seconds: int, call: object) -> list[dict[str, str]]:
        captured["value"] = timeout_seconds
        return [{"代码": "600519"}]

    monkeypatch.setattr("providers._call_with_timeout", _capture_timeout)
    monkeypatch.setattr("providers._call_akshare_endpoint", lambda endpoint, params: [{"代码": "600519"}])

    result = fetch_provider_payload(query)
    assert result.status == "success"
    assert captured["value"] == 10


def test_fetch_provider_payload_rank_board_filters_target_by_ticker_plain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    query = _board_query(endpoint="stock_hot_rank_em")
    rows = [
        {"代码": "600520", "排名": 22, "时间": "2026-05-07 10:00:00"},
        {"代码": "SH600519", "排名": 3, "时间": "2026-05-07 10:00:00"},
    ]
    monkeypatch.setattr("providers._call_with_timeout", lambda timeout_seconds, call: rows)
    monkeypatch.setattr("providers._call_akshare_endpoint", lambda endpoint, params: rows)

    result = fetch_provider_payload(query, ticker_plain="600519")

    assert result.ok is True
    assert result.status == "success"
    assert result.fields["target_present"] is True
    assert result.fields["target_rank"] == 3
    assert result.fields["sample_size"] == 2
    assert result.fields["as_of_date"] == "2026-05-07"


def test_fetch_provider_payload_rank_board_not_hit_keeps_target_present_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    query = _board_query(endpoint="stock_hot_rank_em")
    rows = [
        {"代码": "600520", "排名": 22, "时间": "2026-05-07 10:00:00"},
        {"代码": "600521", "排名": 9, "时间": "2026-05-07 10:00:00"},
    ]
    monkeypatch.setattr("providers._call_with_timeout", lambda timeout_seconds, call: rows)
    monkeypatch.setattr("providers._call_akshare_endpoint", lambda endpoint, params: rows)

    result = fetch_provider_payload(query, ticker_plain="600519")

    assert result.ok is True
    assert result.fields["target_present"] is False
    assert result.fields["target_rank"] is None
    assert result.fields["sample_size"] == 2


def test_fetch_provider_payload_hot_up_board_filters_target_rank_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    query = _board_query(endpoint="stock_hot_up_em")
    rows = [
        {"代码": "1600519", "排名变化": 999, "时间": "2026-05-07 10:00:00"},
        {"代码": "SZ000001", "排名变化": 11, "时间": "2026-05-07 10:00:00"},
        {"代码": "600519", "排名变化": "+35", "时间": "2026-05-07 10:00:00"},
    ]
    monkeypatch.setattr("providers._call_with_timeout", lambda timeout_seconds, call: rows)
    monkeypatch.setattr("providers._call_akshare_endpoint", lambda endpoint, params: rows)

    result = fetch_provider_payload(query, ticker_plain="600519")

    assert result.ok is True
    assert result.fields["target_present"] is True
    assert result.fields["target_rank_change"] == 35
    assert result.fields["sample_size"] == 3


def test_fetch_provider_payload_rank_board_missing_date_marks_required_fields_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    query = _board_query(endpoint="stock_hot_rank_em")
    rows = [{"代码": "600519", "排名": 7}]
    monkeypatch.setattr("providers._call_with_timeout", lambda timeout_seconds, call: rows)
    monkeypatch.setattr("providers._call_akshare_endpoint", lambda endpoint, params: rows)

    result = fetch_provider_payload(query, ticker_plain="600519")

    assert result.ok is True
    assert result.fields["as_of_date"] is None
    assert _endpoint_required_fields_missing("stock_hot_rank_em", result.fields) is True


def test_normalize_provider_payload_maps_target_heat_rows_to_heat_rank_with_evidence_fields() -> None:
    query = _provider_query(endpoint="stock_hot_rank_latest_em", timeout_seconds=10)
    raw_result = RawProviderResult(
        provider="akshare",
        endpoint=query.endpoint,
        query=dict(query.query),
        ok=True,
        status="success",
        raw_payload=[{"代码": "600519", "排名": 1, "热度": 9999}],
        raw_count=1,
        elapsed_ms=5,
        empty_reason=None,
        error=None,
        payload_hash=None,
        row_count=1,
        fields={},
        as_of_date="2026-05-07",
        fetched_at="2026-05-07T00:00:00+00:00",
    )

    rows = normalize_provider_payload(
        query=query,
        raw_result=raw_result,
        raw_payload_ref="viking://resources/workflow/run/frontline/social_analyst/call/evidence/provider_raw/a.json",
    )

    assert len(rows) == 1
    assert rows[0].source_kind == "heat_rank"
    assert rows[0].raw_index == 0
    assert rows[0].raw_payload_ref.startswith("viking://resources/workflow/")
    assert rows[0].payload_hash.startswith("sha256:")


def test_normalize_provider_payload_maps_keyword_rows_to_heat_keyword() -> None:
    query = _provider_query(endpoint="stock_hot_keyword_em", timeout_seconds=10)
    raw_result = RawProviderResult(
        provider="akshare",
        endpoint=query.endpoint,
        query=dict(query.query),
        ok=True,
        status="success",
        raw_payload=[{"关键词": "白酒", "热度": 88}],
        raw_count=1,
        elapsed_ms=5,
        empty_reason=None,
        error=None,
        payload_hash=None,
        row_count=1,
        fields={},
        as_of_date="2026-05-07",
        fetched_at="2026-05-07T00:00:00+00:00",
    )

    rows = normalize_provider_payload(
        query=query,
        raw_result=raw_result,
        raw_payload_ref="viking://resources/workflow/run/frontline/social_analyst/call/evidence/provider_raw/b.json",
    )
    assert rows[0].source_kind == "heat_keyword"


def test_load_rows_by_raw_payload_ref_returns_hash_mismatch_when_expected_hash_differs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = [{"symbol": "600519", "rank": 1}]
    monkeypatch.setattr(
        "providers._read_openviking_content_by_uri",
        lambda ref: json.dumps(payload, ensure_ascii=False).encode("utf-8"),
    )

    with pytest.raises(SocialProviderNormalizationError) as exc_info:
        load_rows_by_raw_payload_ref(
            ref="viking://resources/workflow/run/frontline/social_analyst/call/evidence/provider_raw/c.json",
            expected_hash="sha256:" + ("0" * 64),
        )

    assert exc_info.value.code == SOCIAL_RAW_HASH_MISMATCH


def test_normalize_provider_rows_raises_when_row_missing_raw_payload_ref() -> None:
    query = _provider_query(endpoint="stock_hot_rank_latest_em", timeout_seconds=10)
    result = ProviderExecutionResult(
        query=query,
        attempt={},
        raw_rows=[
            NormalizedProviderRow(
                provider="akshare",
                endpoint="stock_hot_rank_latest_em",
                platform="东方财富",
                source_kind="heat_rank",
                raw_index=0,
                fields={"symbol": "600519"},
                payload_hash="sha256:" + ("1" * 64),
                raw_payload_ref="",
            )
        ],
        raw_payload_ref=None,
        payload_hash=None,
        cache_inspection={},
    )

    with pytest.raises(SocialProviderNormalizationError) as exc_info:
        normalize_provider_rows([result])

    assert exc_info.value.code == SOCIAL_ROW_EVIDENCE_INCOMPLETE


def _target_profile_600519() -> dict[str, str]:
    return {
        "ticker": "600519.SH",
        "ticker_plain": "600519",
        "eastmoney_symbol": "100.600519",
    }


def _date_window() -> DateWindow:
    return DateWindow(
        start_date="2026-05-01",
        end_date="2026-05-07",
        as_of_date="2026-05-07",
    )


def _build_config(*, p1_hot_up_enabled: bool) -> SocialDataConfig:
    return SocialDataConfig(
        schema_version=SOCIAL_SCHEMA_VERSION,
        provider_timeout_seconds=10,
        pack_timeout_seconds=20,
        provider_max_concurrency=3,
        max_signals_per_bucket=50,
        mongodb_uri=None,
        mongodb_database="claw_trade",
        mongodb_cache_collection="social_provider_cache",
        cache_required=False,
        ttl_by_endpoint={
            "stock_hot_rank_latest_em": 1800,
            "stock_hot_rank_em": 1800,
            "stock_hot_up_em": 1800,
            "stock_hot_keyword_em": 3600,
            "stock_hot_rank_relate_em": 3600,
        },
        p1_hot_up_enabled=p1_hot_up_enabled,
        p1_xueqiu_enabled=False,
        evidence_root="runs",
        openviking_l2_write_target_root=None,
    )


def _provider_query(*, endpoint: str, timeout_seconds: int) -> ProviderQuery:
    return ProviderQuery(
        provider="akshare",
        endpoint=endpoint,
        priority="P0",
        query={"symbol": "100.600519"},
        query_fingerprint="sha256:test",
        date_window=_date_window(),
        timeout_seconds=timeout_seconds,
    )


def _board_query(*, endpoint: str) -> ProviderQuery:
    return ProviderQuery(
        provider="akshare",
        endpoint=endpoint,
        priority="P0",
        query={},
        query_fingerprint="sha256:test",
        date_window=_date_window(),
        timeout_seconds=10,
    )
