from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FieldMappingRule:
    provider: str
    api_name: str
    source_column: str
    pack_field_path: str
    domain: str
    unit_scale: str
    source_ref_kind: str
    source_ref_path: str


APPROVED_PROVIDERS = frozenset({"tushare", "akshare"})
APPROVED_PACK_FIELD_PATHS = frozenset(
    {
        "valuation.pe_ttm",
        "valuation.pb",
        "valuation.total_mv",
        "financial_indicators.roe",
        "financial_indicators.roa",
        "financial_indicators.gross_margin",
        "financial_indicators.netprofit_margin",
        "financial_indicators.debt_to_assets",
        "income_statement.revenue",
        "income_statement.net_profit",
        "income_statement.eps",
        "balance_sheet.total_assets",
        "balance_sheet.total_liabilities",
        "balance_sheet.total_equity",
        "cash_flow.operating_cash_flow",
        "business_segments.items",
        "business_segments.sales",
        "business_segments.profit",
        "dividend.stock_dividend",
        "dividend.cash_dividend",
        "shareholders.top10_holders.names",
        "shareholders.top10_holders.hold_amount",
        "shareholders.top10_float_holders.names",
        "shareholders.top10_float_holders.hold_amount",
        "price_context.close",
        "price_context.trade_date",
        "price_context.volume",
        "company_profile.name",
        "company_profile.industry",
        "company_profile.main_business",
        "dividend.ex_date",
        "dividend.ann_date",
    }
)

CN_A_FUNDAMENTAL_FIELD_MAPPING_V1: tuple[FieldMappingRule, ...] = (
    FieldMappingRule("tushare", "stock_company", "name", "company_profile.name", "company_profile", "text", "raw", "field_sources.company_profile.name"),
    FieldMappingRule("tushare", "stock_company", "main_business", "company_profile.main_business", "company_profile", "text", "raw", "field_sources.company_profile.main_business"),
    FieldMappingRule("tushare", "stock_basic", "industry", "company_profile.industry", "company_profile", "text", "raw", "field_sources.company_profile.industry"),
    FieldMappingRule("tushare", "daily_basic", "trade_date", "price_context.trade_date", "price_context", "date", "raw", "field_sources.price_context.trade_date"),
    FieldMappingRule("tushare", "daily_basic", "close", "price_context.close", "price_context", "cny_per_share", "raw", "field_sources.price_context.close"),
    FieldMappingRule("tushare", "daily_basic", "pe_ttm", "valuation.pe_ttm", "valuation", "x", "raw", "field_sources.valuation.pe_ttm"),
    FieldMappingRule("tushare", "daily_basic", "pb", "valuation.pb", "valuation", "x", "raw", "field_sources.valuation.pb"),
    FieldMappingRule("tushare", "daily_basic", "total_mv", "valuation.total_mv", "valuation", "10k_cny", "raw", "field_sources.valuation.total_mv"),
    FieldMappingRule("tushare", "fina_indicator", "roe", "financial_indicators.roe", "financial_indicators", "%", "raw", "field_sources.financial_indicators.roe"),
    FieldMappingRule("tushare", "fina_indicator", "roa", "financial_indicators.roa", "financial_indicators", "%", "raw", "field_sources.financial_indicators.roa"),
    FieldMappingRule("tushare", "fina_indicator", "grossprofit_margin", "financial_indicators.gross_margin", "financial_indicators", "%", "raw", "field_sources.financial_indicators.gross_margin"),
    FieldMappingRule("tushare", "fina_indicator", "netprofit_margin", "financial_indicators.netprofit_margin", "financial_indicators", "%", "raw", "field_sources.financial_indicators.netprofit_margin"),
    FieldMappingRule("tushare", "fina_indicator", "debt_to_assets", "financial_indicators.debt_to_assets", "financial_indicators", "%", "raw", "field_sources.financial_indicators.debt_to_assets"),
    FieldMappingRule("tushare", "income", "revenue", "income_statement.revenue", "income_statement", "cny", "raw", "field_sources.income_statement.revenue"),
    FieldMappingRule("tushare", "income", "n_income", "income_statement.net_profit", "income_statement", "cny", "raw", "field_sources.income_statement.net_profit"),
    FieldMappingRule("tushare", "income", "basic_eps", "income_statement.eps", "income_statement", "cny_per_share", "raw", "field_sources.income_statement.eps"),
    FieldMappingRule("tushare", "balancesheet", "total_assets", "balance_sheet.total_assets", "balance_sheet", "cny", "raw", "field_sources.balance_sheet.total_assets"),
    FieldMappingRule("tushare", "balancesheet", "total_liab", "balance_sheet.total_liabilities", "balance_sheet", "cny", "raw", "field_sources.balance_sheet.total_liabilities"),
    FieldMappingRule("tushare", "balancesheet", "total_hldr_eqy_exc_min_int", "balance_sheet.total_equity", "balance_sheet", "cny", "raw", "field_sources.balance_sheet.total_equity"),
    FieldMappingRule("tushare", "cashflow", "n_cashflow_act", "cash_flow.operating_cash_flow", "cash_flow", "cny", "raw", "field_sources.cash_flow.operating_cash_flow"),
    FieldMappingRule("tushare", "fina_mainbz", "bz_item", "business_segments.items", "business_segments", "text", "raw", "field_sources.business_segments.items"),
    FieldMappingRule("tushare", "fina_mainbz", "bz_sales", "business_segments.sales", "business_segments", "cny", "raw", "field_sources.business_segments.sales"),
    FieldMappingRule("tushare", "fina_mainbz", "bz_profit", "business_segments.profit", "business_segments", "cny", "raw", "field_sources.business_segments.profit"),
    FieldMappingRule("tushare", "dividend", "stk_div", "dividend.stock_dividend", "dividend", "share_per_share", "raw", "field_sources.dividend.stock_dividend"),
    FieldMappingRule("tushare", "dividend", "cash_div_tax", "dividend.cash_dividend", "dividend", "cny_per_share", "raw", "field_sources.dividend.cash_dividend"),
    FieldMappingRule("tushare", "top10_holders", "holder_name", "shareholders.top10_holders.names", "shareholders", "text", "raw", "field_sources.shareholders.top10_holders.names"),
    FieldMappingRule("tushare", "top10_holders", "hold_amount", "shareholders.top10_holders.hold_amount", "shareholders", "share", "raw", "field_sources.shareholders.top10_holders.hold_amount"),
    FieldMappingRule("tushare", "top10_floatholders", "holder_name", "shareholders.top10_float_holders.names", "shareholders", "text", "raw", "field_sources.shareholders.top10_float_holders.names"),
    FieldMappingRule("tushare", "top10_floatholders", "hold_amount", "shareholders.top10_float_holders.hold_amount", "shareholders", "share", "raw", "field_sources.shareholders.top10_float_holders.hold_amount"),
    FieldMappingRule("akshare", "stock_individual_info_em", "行业", "company_profile.industry", "company_profile", "text", "raw", "field_sources.company_profile.industry"),
    FieldMappingRule("akshare", "stock_individual_info_em", "主营业务", "company_profile.main_business", "company_profile", "text", "raw", "field_sources.company_profile.main_business"),
    FieldMappingRule("akshare", "stock_zh_a_spot_em", "市盈率-动态", "valuation.pe_ttm", "valuation", "ratio", "raw", "field_sources.valuation.pe_ttm"),
    FieldMappingRule("akshare", "stock_zh_a_spot_em", "市净率", "valuation.pb", "valuation", "ratio", "raw", "field_sources.valuation.pb"),
    FieldMappingRule("akshare", "stock_zh_a_spot_em", "总市值", "valuation.total_mv", "valuation", "cny", "raw", "field_sources.valuation.total_mv"),
    FieldMappingRule("akshare", "stock_zh_a_spot_em", "最新价", "price_context.close", "price_context", "cny_per_share", "raw", "field_sources.price_context.close"),
    FieldMappingRule("akshare", "stock_zh_a_hist", "日期", "price_context.trade_date", "price_context", "date", "raw", "field_sources.price_context.trade_date"),
    FieldMappingRule("akshare", "stock_zh_a_hist", "成交量", "price_context.volume", "price_context", "share", "raw", "field_sources.price_context.volume"),
    FieldMappingRule("akshare", "stock_financial_abstract_ths", "净资产收益率", "financial_indicators.roe", "financial_indicators", "%", "raw", "field_sources.financial_indicators.roe"),
    FieldMappingRule("akshare", "stock_financial_abstract_ths", "营业总收入", "income_statement.revenue", "income_statement", "cny", "raw", "field_sources.income_statement.revenue"),
    FieldMappingRule("akshare", "stock_financial_abstract_ths", "净利润", "income_statement.net_profit", "income_statement", "cny", "raw", "field_sources.income_statement.net_profit"),
    FieldMappingRule("akshare", "stock_financial_abstract_ths", "每股收益", "income_statement.eps", "income_statement", "cny_per_share", "raw", "field_sources.income_statement.eps"),
    FieldMappingRule("akshare", "stock_history_dividend_detail", "每股分红", "dividend.cash_dividend", "dividend", "cny_per_share", "raw", "field_sources.dividend.cash_dividend"),
    FieldMappingRule("akshare", "stock_history_dividend_detail", "除权除息日", "dividend.ex_date", "dividend", "date", "raw", "field_sources.dividend.ex_date"),
    FieldMappingRule("akshare", "stock_history_dividend_detail", "公告日期", "dividend.ann_date", "dividend", "date", "raw", "field_sources.dividend.ann_date"),
)


def validate_field_mapping_rules(rules: tuple[FieldMappingRule, ...]) -> None:
    for index, rule in enumerate(rules, start=1):
        for field_name in FieldMappingRule.__dataclass_fields__:
            value = getattr(rule, field_name)
            if not isinstance(value, str) or value.strip() == "":
                raise ValueError(f"invalid mapping rule at {index}: {field_name} is empty")

        if rule.provider not in APPROVED_PROVIDERS:
            raise ValueError(f"invalid mapping rule at {index}: provider={rule.provider}")
        if rule.source_ref_kind != "raw":
            raise ValueError(f"invalid mapping rule at {index}: source_ref_kind={rule.source_ref_kind}")
        if not rule.source_ref_path.startswith("field_sources."):
            raise ValueError(f"invalid mapping rule at {index}: source_ref_path={rule.source_ref_path}")
        if rule.pack_field_path not in APPROVED_PACK_FIELD_PATHS:
            raise ValueError(f"invalid mapping rule at {index}: pack_field_path={rule.pack_field_path}")


validate_field_mapping_rules(CN_A_FUNDAMENTAL_FIELD_MAPPING_V1)
