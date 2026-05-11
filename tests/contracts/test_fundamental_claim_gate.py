from __future__ import annotations

from claw_trade.guards.fundamental_claim_gate import (
    evaluate_fundamental_report_claims_v1,
    parse_report_claims_by_rules_v1,
)
from claw_trade.guards.fundamental_claim_rules import CLAIM_DICTIONARY_REVISION_ID


def test_parse_report_claims_covers_required_metric_dictionary() -> None:
    report = (
        "当前PE约20倍，PB约5倍，ROE为30%，ROA为18%，毛利率和净利率保持高位，"
        "资产负债率低，营收和净利润稳步增长，EPS持续提升，市值维持在高位。"
    )
    claims = parse_report_claims_by_rules_v1(report)
    metric_keys = {item.claim_key for item in claims if item.claim_type == "metric"}
    assert metric_keys == {
        "pe",
        "pb",
        "roe",
        "roa",
        "gross_margin",
        "netprofit_margin",
        "debt_to_assets",
        "revenue",
        "net_profit",
        "eps",
        "total_mv",
    }


def test_metric_claim_without_fact_or_field_source_is_unsupported() -> None:
    pack = _base_pack()
    pack["facts"]["valuation"]["pb"] = None
    pack["field_sources"].pop("valuation.pb")
    result = evaluate_fundamental_report_claims_v1("公司PB约5倍。", pack)
    assert result.guard.ok is False
    assert len(result.unsupported_claims) == 1
    assert result.unsupported_claims[0].claim.claim_key == "pb"
    assert set(result.unsupported_claims[0].reason_codes) == {"required_fact_missing", "field_source_missing"}


def test_trend_claim_requires_financial_trend_capability_available() -> None:
    pack = _base_pack()
    pack["evidence_capabilities"]["financial_trend"]["status"] = "blocked"
    result = evaluate_fundamental_report_claims_v1("公司ROE连续三年提升。", pack)
    assert result.guard.ok is False
    assert len(result.unsupported_claims) == 1
    assert result.unsupported_claims[0].claim.claim_key == "roe"
    assert "financial_trend_unavailable" in result.unsupported_claims[0].reason_codes


def test_target_price_rating_and_valuation_judgment_are_unsupported_when_blocked() -> None:
    pack = _base_pack()
    result = evaluate_fundamental_report_claims_v1(
        "我们给出目标价2200元，维持买入评级，并判断当前估值偏高。",
        pack,
    )
    unsupported_keys = {item.claim.claim_key for item in result.unsupported_claims}
    assert result.guard.ok is False
    assert {"target_price", "rating", "valuation_judgment"}.issubset(unsupported_keys)


def test_narrative_claim_is_not_hard_gated_when_business_segments_evidence_insufficient() -> None:
    pack = _base_pack()
    pack["facts"]["business_segments"] = []
    pack["field_sources"].pop("business_segments.items")
    result = evaluate_fundamental_report_claims_v1("公司具备护城河，且行业龙头地位稳固。", pack)
    narrative_keys = {item.claim_key for item in result.claims if item.claim_type == "narrative"}
    assert {"moat", "industry_leader"}.issubset(narrative_keys)
    assert result.unsupported_claims == ()
    assert result.guard.ok is True


def test_compliant_downgrade_sentences_are_not_counted_as_unsupported_investment_conclusions() -> None:
    pack = _base_pack()
    result = evaluate_fundamental_report_claims_v1(
        "证据不足，无法给出基本面评级。证据不足，无法给出目标价。",
        pack,
    )
    assert result.unsupported_claims == ()
    assert result.guard.ok is True


def test_claim_gate_evaluation_contains_dictionary_revision_id() -> None:
    pack = _base_pack()
    result = evaluate_fundamental_report_claims_v1("公司PB约5倍。", pack)
    assert result.dictionary_revision_id == CLAIM_DICTIONARY_REVISION_ID


def test_metric_gap_statements_are_not_treated_as_unsupported_metric_claims() -> None:
    pack = _base_pack()
    pack["facts"]["financial_indicators"]["gross_margin"] = None
    pack["facts"]["financial_indicators"]["netprofit_margin"] = None
    pack["facts"]["financial_indicators"]["debt_to_assets"] = None
    pack["field_sources"].pop("financial_indicators.gross_margin")
    pack["field_sources"].pop("financial_indicators.netprofit_margin")
    pack["field_sources"].pop("financial_indicators.debt_to_assets")
    result = evaluate_fundamental_report_claims_v1(
        "缺失毛利率、净利率、资产负债率等完整盈利与财务结构数据。",
        pack,
    )
    assert result.unsupported_claims == ()
    assert result.guard.ok is True


def test_assertive_metric_statements_still_fail_without_evidence() -> None:
    pack = _base_pack()
    pack["facts"]["financial_indicators"]["gross_margin"] = None
    pack["facts"]["financial_indicators"]["netprofit_margin"] = None
    pack["facts"]["financial_indicators"]["debt_to_assets"] = None
    pack["field_sources"].pop("financial_indicators.gross_margin")
    pack["field_sources"].pop("financial_indicators.netprofit_margin")
    pack["field_sources"].pop("financial_indicators.debt_to_assets")
    result = evaluate_fundamental_report_claims_v1(
        "毛利率保持高位，资产负债率低，净利率改善。",
        pack,
    )
    unsupported_keys = {item.claim.claim_key for item in result.unsupported_claims}
    assert result.guard.ok is False
    assert {"gross_margin", "netprofit_margin", "debt_to_assets"}.issubset(unsupported_keys)


def test_main_evidence_gap_sentence_does_not_trigger_metric_unsupported_claim() -> None:
    pack = _base_pack()
    pack["facts"]["financial_indicators"]["gross_margin"] = None
    pack["facts"]["financial_indicators"]["netprofit_margin"] = None
    pack["facts"]["financial_indicators"]["debt_to_assets"] = None
    pack["facts"]["financial_indicators"]["roa"] = None
    pack["field_sources"].pop("financial_indicators.gross_margin")
    pack["field_sources"].pop("financial_indicators.netprofit_margin")
    pack["field_sources"].pop("financial_indicators.debt_to_assets")
    pack["field_sources"].pop("financial_indicators.roa")
    result = evaluate_fundamental_report_claims_v1(
        "主要证据缺口：缺失毛利率、净利率、ROA 等完整盈利能力口径；缺失资产负债率、流动/速动比率等财务稳健性数据。",
        pack,
    )
    assert result.unsupported_claims == ()
    assert result.guard.ok is True


def _base_pack() -> dict[str, object]:
    return {
        "facts": {
            "valuation": {"pe_ttm": 21.2, "pb": 5.8, "total_mv": 2.4e12},
            "financial_indicators": {
                "roe": 0.31,
                "roa": 0.18,
                "gross_margin": 0.9,
                "netprofit_margin": 0.52,
                "debt_to_assets": 0.2,
            },
            "income_statement": {"revenue": 1500.0, "net_profit": 700.0, "eps": 30.2},
            "business_segments": [{"bz_item": "白酒", "bz_sales": 1200.0}],
        },
        "field_sources": {
            "valuation.pe_ttm": {"provider": "tushare"},
            "valuation.pb": {"provider": "tushare"},
            "valuation.total_mv": {"provider": "tushare"},
            "financial_indicators.roe": {"provider": "tushare"},
            "financial_indicators.roa": {"provider": "tushare"},
            "financial_indicators.gross_margin": {"provider": "tushare"},
            "financial_indicators.netprofit_margin": {"provider": "tushare"},
            "financial_indicators.debt_to_assets": {"provider": "tushare"},
            "income_statement.revenue": {"provider": "tushare"},
            "income_statement.net_profit": {"provider": "tushare"},
            "income_statement.eps": {"provider": "tushare"},
            "business_segments.items": {"provider": "tushare"},
        },
        "evidence_capabilities": {
            "financial_trend": {"status": "available"},
            "target_price": {"status": "blocked"},
            "rating": {"status": "blocked"},
            "valuation_judgment": {"status": "blocked"},
        },
    }
