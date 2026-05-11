from __future__ import annotations

from dataclasses import dataclass, field, fields, is_dataclass
from typing import Any, Literal

from models import PackStatus, ProviderAttempt

CN_A_FUNDAMENTAL_PACK_SCHEMA_VERSION = "cn_a_fundamental_pack.v1"
CN_A_FUNDAMENTAL_PACK_CURRENCY = "CNY"


def to_canonical_value(value: Any) -> Any:
    if is_dataclass(value):
        return {
            dataclass_field.name: to_canonical_value(getattr(value, dataclass_field.name))
            for dataclass_field in fields(value)
        }
    if isinstance(value, dict):
        return {
            str(key): to_canonical_value(value[key])
            for key in sorted(value.keys(), key=lambda item: str(item))
        }
    if isinstance(value, list):
        return [to_canonical_value(item) for item in value]
    if isinstance(value, tuple):
        return [to_canonical_value(item) for item in value]
    return value


@dataclass(frozen=True)
class ProfileInfo:
    ticker: str
    canonical_code: str
    market: Literal["CN_A"]
    company_name: str | None
    currency: Literal["CNY"] = field(init=False, default=CN_A_FUNDAMENTAL_PACK_CURRENCY)


@dataclass(frozen=True)
class QueryInfo:
    start_date: str | None
    end_date: str | None
    current_date: str


@dataclass(frozen=True)
class ValuationFacts:
    pe_ttm: float | None
    pb: float | None
    total_mv: float | None


@dataclass(frozen=True)
class FinancialIndicatorsFacts:
    roe: float | None
    roa: float | None
    gross_margin: float | None
    netprofit_margin: float | None
    debt_to_assets: float | None


@dataclass(frozen=True)
class IncomeStatementFacts:
    revenue: float | None
    net_profit: float | None
    eps: float | None


@dataclass(frozen=True)
class BalanceSheetFacts:
    total_assets: float | None
    total_liabilities: float | None
    total_equity: float | None


@dataclass(frozen=True)
class CashFlowFacts:
    operating_cash_flow: float | None


@dataclass(frozen=True)
class CompanyProfileFacts:
    name: str | None
    industry: str | None
    main_business: str | None


@dataclass(frozen=True)
class PriceContextFacts:
    close: float | None
    trade_date: str | None
    volume: float | None


@dataclass(frozen=True)
class Facts:
    company_profile: CompanyProfileFacts
    price_context: PriceContextFacts
    valuation: ValuationFacts
    financial_indicators: FinancialIndicatorsFacts
    income_statement: IncomeStatementFacts
    balance_sheet: BalanceSheetFacts
    cash_flow: CashFlowFacts
    business_segments: list[dict[str, str | float]]
    dividend: list[dict[str, str | float]]
    shareholders: dict[str, list[dict[str, str | float]]]


@dataclass(frozen=True)
class DataPackQuality:
    status: PackStatus
    is_partial: bool
    warnings: list[str]


@dataclass(frozen=True)
class DerivedSummaryItem:
    template_id: str
    text: str
    source_ref: str
    inputs: dict[str, str]


@dataclass(frozen=True)
class DataPackEvidence:
    raw_payload_refs: list[str]
    content_hash: str


@dataclass(frozen=True)
class DataPack:
    ok: bool
    profile: ProfileInfo
    query: QueryInfo
    facts: Facts
    field_sources: dict[str, dict[str, str | float | bool | None]]
    provider_attempts: list[ProviderAttempt]
    missing_fields: list[dict[str, str | bool | list[str]]]
    freshness: dict[str, dict[str, str | bool | None]]
    evidence_capabilities: dict[str, dict[str, str | list[str]]]
    diagnostic_flags: list[dict[str, str]]
    derived_summary: list[DerivedSummaryItem]
    quality: DataPackQuality
    evidence: DataPackEvidence
    schema_version: Literal["cn_a_fundamental_pack.v1"] = field(
        init=False,
        default=CN_A_FUNDAMENTAL_PACK_SCHEMA_VERSION,
    )

    def to_dict(self) -> dict[str, Any]:
        canonical = to_canonical_value(self)
        if not isinstance(canonical, dict):
            raise TypeError("DataPack canonical value must be dict")
        return canonical


__all__ = [
    "CN_A_FUNDAMENTAL_PACK_CURRENCY",
    "CN_A_FUNDAMENTAL_PACK_SCHEMA_VERSION",
    "BalanceSheetFacts",
    "CashFlowFacts",
    "CompanyProfileFacts",
    "DataPack",
    "DataPackEvidence",
    "DataPackQuality",
    "DerivedSummaryItem",
    "Facts",
    "PriceContextFacts",
    "FinancialIndicatorsFacts",
    "IncomeStatementFacts",
    "ProfileInfo",
    "QueryInfo",
    "ValuationFacts",
    "to_canonical_value",
]
