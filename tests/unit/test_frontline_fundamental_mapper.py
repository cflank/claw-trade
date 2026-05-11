from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_PYTHON_ROOT = REPO_ROOT / "openclaw_plugins" / "claw-trade-frontline-tools" / "python"
if str(SHARED_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(SHARED_PYTHON_ROOT))

from frontline_data_pack.fundamental_mapper import (  # noqa: E402
    compute_missing_core_fields,
    map_fundamental_fields,
)
from frontline_data_pack.models import (  # noqa: E402
    ProviderAttempt,
    ProviderResult,
    ProviderSpec,
)


QUERY_FINGERPRINT = "sha256:" + ("f" * 64)
PAYLOAD_HASH_A = "sha256:" + ("a" * 64)
PAYLOAD_HASH_B = "sha256:" + ("b" * 64)


def test_t_fnd_001_maps_core_fundamental_fields_from_provider_rows() -> None:
    result = _provider_result(
        priority="P0",
        provider="akshare",
        endpoint="financial_abstract",
        payload_hash=PAYLOAD_HASH_A,
        raw_payload_ref="viking://raw/fundamental/a.json",
        rows=[
            {"field_name": "company_profile.industry", "value": "酿酒行业", "unit": "text"},
            {"field_name": "valuation.pe_ttm", "value": 25.2, "unit": "ratio"},
            {"field_name": "valuation.pb", "value": 8.8, "unit": "ratio"},
            {
                "report_period": "2025-12-31",
                "financial_indicators.roe": 31.2,
                "financial_indicators.roa": 18.6,
                "financial_indicators.gross_margin": 90.1,
                "financial_indicators.netprofit_margin": 52.2,
                "financial_indicators.debt_to_assets": 12.1,
                "income_statement.revenue": 321654987.0,
                "income_statement.net_profit": 123456789.0,
                "income_statement.eps": 8.88,
                "cash_flow.operating_cash_flow": 100000000.0,
            },
        ],
    )

    fields, conflict_diagnostics = map_fundamental_fields([result])

    assert conflict_diagnostics == []
    assert fields["valuation.pe_ttm"].value == 25.2
    assert fields["valuation.pb"].value == 8.8
    assert fields["financial_indicators.roe"].value == 31.2
    assert fields["financial_indicators.roa"].value == 18.6
    assert fields["financial_indicators.gross_margin"].value == 90.1
    assert fields["financial_indicators.netprofit_margin"].value == 52.2
    assert fields["financial_indicators.debt_to_assets"].value == 12.1
    assert fields["income_statement.revenue"].value == 321654987.0
    assert fields["income_statement.net_profit"].value == 123456789.0
    assert fields["cash_flow.operating_cash_flow"].value == 100000000.0

    missing = compute_missing_core_fields(fields)
    assert missing == []


def test_t_fnd_001_conflict_keeps_higher_priority_field_and_records_diagnostic() -> None:
    p1_result = _provider_result(
        priority="P1",
        provider="sina",
        endpoint="valuation_backup",
        payload_hash=PAYLOAD_HASH_B,
        raw_payload_ref="viking://raw/fundamental/p1.json",
        rows=[{"field_name": "valuation.pe_ttm", "value": 21.1, "unit": "ratio"}],
    )
    p0_result = _provider_result(
        priority="P0",
        provider="akshare",
        endpoint="valuation_primary",
        payload_hash=PAYLOAD_HASH_A,
        raw_payload_ref="viking://raw/fundamental/p0.json",
        rows=[{"field_name": "valuation.pe_ttm", "value": 25.2, "unit": "ratio"}],
    )

    fields, conflict_diagnostics = map_fundamental_fields([p1_result, p0_result])

    assert fields["valuation.pe_ttm"].value == 25.2
    assert len(conflict_diagnostics) == 1
    assert "field_conflict:valuation.pe_ttm" in conflict_diagnostics[0]
    assert "akshare/valuation_primary" in conflict_diagnostics[0]
    assert "sina/valuation_backup" in conflict_diagnostics[0]


def test_t_fnd_001_compute_missing_core_fields_contains_pe_pb_roe_when_missing() -> None:
    result = _provider_result(
        priority="P0",
        provider="akshare",
        endpoint="financial_abstract",
        payload_hash=PAYLOAD_HASH_A,
        raw_payload_ref="viking://raw/fundamental/missing.json",
        rows=[
            {
                "report_period": "2025-12-31",
                "income_statement.revenue": 321654987.0,
                "income_statement.net_profit": 123456789.0,
                "cash_flow.operating_cash_flow": 100000000.0,
            }
        ],
    )

    fields, _ = map_fundamental_fields([result])
    missing = compute_missing_core_fields(fields)

    assert "valuation.pe_ttm" in missing
    assert "valuation.pb" in missing
    assert "financial_indicators.roe" in missing


def test_t_fnd_001_row_without_raw_payload_ref_is_dropped() -> None:
    no_ref_result = _provider_result(
        priority="P0",
        provider="akshare",
        endpoint="valuation_primary",
        payload_hash=PAYLOAD_HASH_A,
        raw_payload_ref=None,
        rows=[{"field_name": "valuation.pe_ttm", "value": 25.2, "unit": "ratio"}],
    )

    fields, _ = map_fundamental_fields([no_ref_result])
    assert "valuation.pe_ttm" not in fields


def test_t_fnd_001_output_never_contains_target_price_or_rating_phrases() -> None:
    result = _provider_result(
        priority="P0",
        provider="akshare",
        endpoint="valuation_primary",
        payload_hash=PAYLOAD_HASH_A,
        raw_payload_ref="viking://raw/fundamental/safe.json",
        rows=[
            {"field_name": "valuation.pe_ttm", "value": 25.2, "unit": "ratio"},
            {"field_name": "company_profile.main_business", "value": "白酒生产与销售", "unit": "text"},
        ],
    )

    fields, conflict_diagnostics = map_fundamental_fields([result])
    forbidden_terms = ("目标价", "买入", "卖出", "评级")

    assert all("target" not in field_name.lower() for field_name in fields.keys())
    assert all("rating" not in field_name.lower() for field_name in fields.keys())

    rendered_values = " ".join(str(field.value) for field in fields.values())
    rendered_diagnostics = " ".join(conflict_diagnostics)
    for term in forbidden_terms:
        assert term not in rendered_values
        assert term not in rendered_diagnostics


def _provider_result(
    *,
    priority: str,
    provider: str,
    endpoint: str,
    payload_hash: str | None,
    raw_payload_ref: str | None,
    rows: list[dict[str, object]],
) -> ProviderResult:
    spec = ProviderSpec(
        domain="fundamental",
        priority=priority,
        provider=provider,
        endpoint=endpoint,
        role="fundamental",
        enabled=True,
        mode="remote",
        timeout_ms=1000,
        required_for_complete=True,
        query_parameters=[],
    )
    attempt = ProviderAttempt(
        provider=provider,
        endpoint=endpoint,
        role="fundamental",
        status="success",
        started_at="2026-05-08T12:00:00.000Z",
        finished_at="2026-05-08T12:00:00.500Z",
        elapsed_ms=500,
        timeout_ms=1000,
        query_fingerprint=QUERY_FINGERPRINT,
        raw_count=len(rows),
        accepted_count=len(rows),
        payload_hash=payload_hash,
        raw_payload_ref=raw_payload_ref,
        error_code=None,
        error_message_redacted=None,
    )
    return ProviderResult(
        spec=spec,
        attempt=attempt,
        raw_payload=None,
        normalized_rows=rows,
        field_sources={},
    )
