from __future__ import annotations

from dataclasses import dataclass, field
from string import Formatter
from typing import Literal

from models import ApiCallSpec, NormalizedInput

FND_PROVIDER_SPEC_INVALID = "FND_PROVIDER_SPEC_INVALID"
FND_PROVIDER_API_NOT_APPROVED = "FND_PROVIDER_API_NOT_APPROVED"
FND_PROVIDER_DYNAMIC_API_FORBIDDEN = "FND_PROVIDER_DYNAMIC_API_FORBIDDEN"


class ProviderSpecError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class ProviderApiTemplate:
    provider: Literal["tushare", "akshare"]
    api_name: str
    role: Literal["primary", "supplement"]
    required: bool
    field_family: str
    parameters_template: dict[str, str]
    timeout_ms: int
    retry_limit: int


TUSHARE_V1_API_TEMPLATES: tuple[ProviderApiTemplate, ...] = (
    ProviderApiTemplate(
        provider="tushare",
        api_name="stock_company",
        role="primary",
        required=False,
        field_family="company_profile",
        parameters_template={
            "exchange": "{tushare_exchange}",
            "fields": "ts_code,name,province,city,introduction,main_business",
        },
        timeout_ms=12000,
        retry_limit=1,
    ),
    ProviderApiTemplate(
        provider="tushare",
        api_name="stock_basic",
        role="primary",
        required=True,
        field_family="company_profile",
        parameters_template={
            "ts_code": "{tushare_code}",
            "list_status": "L",
            "fields": "ts_code,name,industry,market,list_date",
        },
        timeout_ms=12000,
        retry_limit=1,
    ),
    ProviderApiTemplate(
        provider="tushare",
        api_name="daily_basic",
        role="primary",
        required=True,
        field_family="price_context,valuation",
        parameters_template={
            "ts_code": "{tushare_code}",
            "trade_date": "{current_date_yyyymmdd}",
            "fields": "ts_code,trade_date,close,pe_ttm,pb,total_mv,float_mv",
        },
        timeout_ms=15000,
        retry_limit=1,
    ),
    ProviderApiTemplate(
        provider="tushare",
        api_name="fina_indicator",
        role="primary",
        required=True,
        field_family="financial_indicators",
        parameters_template={
            "ts_code": "{tushare_code}",
            "period": "{latest_report_period}",
            "fields": "ts_code,end_date,ann_date,roe,roa,grossprofit_margin,netprofit_margin,debt_to_assets",
        },
        timeout_ms=15000,
        retry_limit=1,
    ),
    ProviderApiTemplate(
        provider="tushare",
        api_name="income",
        role="primary",
        required=True,
        field_family="income_statement",
        parameters_template={
            "ts_code": "{tushare_code}",
            "period": "{latest_report_period}",
            "fields": "ts_code,end_date,ann_date,revenue,n_income,basic_eps",
        },
        timeout_ms=15000,
        retry_limit=1,
    ),
    ProviderApiTemplate(
        provider="tushare",
        api_name="balancesheet",
        role="primary",
        required=True,
        field_family="balance_sheet",
        parameters_template={
            "ts_code": "{tushare_code}",
            "period": "{latest_report_period}",
            "fields": "ts_code,end_date,ann_date,total_assets,total_liab,total_hldr_eqy_exc_min_int",
        },
        timeout_ms=15000,
        retry_limit=1,
    ),
    ProviderApiTemplate(
        provider="tushare",
        api_name="cashflow",
        role="primary",
        required=True,
        field_family="cash_flow",
        parameters_template={
            "ts_code": "{tushare_code}",
            "period": "{latest_report_period}",
            "fields": "ts_code,end_date,ann_date,n_cashflow_act",
        },
        timeout_ms=15000,
        retry_limit=1,
    ),
    ProviderApiTemplate(
        provider="tushare",
        api_name="fina_mainbz",
        role="primary",
        required=False,
        field_family="business_segments",
        parameters_template={
            "ts_code": "{tushare_code}",
            "period": "{latest_report_period}",
            "type": "P",
            "fields": "ts_code,end_date,bz_item,bz_sales,bz_profit",
        },
        timeout_ms=15000,
        retry_limit=1,
    ),
    ProviderApiTemplate(
        provider="tushare",
        api_name="dividend",
        role="primary",
        required=False,
        field_family="dividend",
        parameters_template={
            "ts_code": "{tushare_code}",
            "fields": "ts_code,ann_date,end_date,record_date,ex_date,stk_div,cash_div_tax",
        },
        timeout_ms=12000,
        retry_limit=1,
    ),
    ProviderApiTemplate(
        provider="tushare",
        api_name="top10_holders",
        role="primary",
        required=False,
        field_family="shareholders",
        parameters_template={
            "ts_code": "{tushare_code}",
            "period": "{latest_report_period}",
            "fields": "ts_code,ann_date,end_date,holder_name,hold_amount,hold_ratio",
        },
        timeout_ms=12000,
        retry_limit=1,
    ),
    ProviderApiTemplate(
        provider="tushare",
        api_name="top10_floatholders",
        role="primary",
        required=False,
        field_family="shareholders",
        parameters_template={
            "ts_code": "{tushare_code}",
            "period": "{latest_report_period}",
            "fields": "ts_code,ann_date,end_date,holder_name,hold_amount,hold_ratio",
        },
        timeout_ms=12000,
        retry_limit=1,
    ),
)

AKSHARE_V1_API_TEMPLATES: tuple[ProviderApiTemplate, ...] = (
    ProviderApiTemplate(
        provider="akshare",
        api_name="stock_individual_info_em",
        role="supplement",
        required=False,
        field_family="company_profile,valuation",
        parameters_template={"symbol": "{akshare_symbol}"},
        timeout_ms=12000,
        retry_limit=1,
    ),
    ProviderApiTemplate(
        provider="akshare",
        api_name="stock_zh_a_spot_em",
        role="supplement",
        required=False,
        field_family="price_context,valuation",
        parameters_template={},
        timeout_ms=18000,
        retry_limit=1,
    ),
    ProviderApiTemplate(
        provider="akshare",
        api_name="stock_zh_a_hist",
        role="supplement",
        required=False,
        field_family="price_context",
        parameters_template={
            "symbol": "{akshare_symbol}",
            "period": "daily",
            "start_date": "{start_date_yyyymmdd}",
            "end_date": "{end_date_yyyymmdd}",
            "adjust": "qfq",
        },
        timeout_ms=18000,
        retry_limit=1,
    ),
    ProviderApiTemplate(
        provider="akshare",
        api_name="stock_financial_abstract_ths",
        role="supplement",
        required=False,
        field_family="financial_indicators,income_statement,balance_sheet,cash_flow",
        parameters_template={
            "symbol": "{akshare_symbol}",
            "indicator": "{latest_report_period}",
        },
        timeout_ms=18000,
        retry_limit=1,
    ),
    ProviderApiTemplate(
        provider="akshare",
        api_name="stock_history_dividend_detail",
        role="supplement",
        required=False,
        field_family="dividend",
        parameters_template={
            "symbol": "{akshare_symbol}",
            "indicator": "分红",
        },
        timeout_ms=12000,
        retry_limit=1,
    ),
)

_TUSHARE_BY_NAME = {item.api_name: item for item in TUSHARE_V1_API_TEMPLATES}
_AKSHARE_BY_NAME = {item.api_name: item for item in AKSHARE_V1_API_TEMPLATES}


def assert_provider_api_allowlist_v1() -> None:
    _assert_template_catalog(TUSHARE_V1_API_TEMPLATES, expected_provider="tushare", expected_count=11)
    _assert_template_catalog(AKSHARE_V1_API_TEMPLATES, expected_provider="akshare", expected_count=5)


def assert_no_dynamic_worker_api_name(worker_api_name: str | None) -> None:
    if worker_api_name is None:
        return
    normalized = worker_api_name.strip()
    if normalized == "":
        return
    raise ProviderSpecError(
        FND_PROVIDER_DYNAMIC_API_FORBIDDEN,
        f"worker dynamic api_name is forbidden: {worker_api_name}",
    )


def list_tushare_v1_api_names() -> tuple[str, ...]:
    return tuple(item.api_name for item in TUSHARE_V1_API_TEMPLATES)


def list_akshare_v1_api_names() -> tuple[str, ...]:
    return tuple(item.api_name for item in AKSHARE_V1_API_TEMPLATES)


def render_tushare_spec(api_name: str, normalized: NormalizedInput) -> ApiCallSpec:
    template = _TUSHARE_BY_NAME.get(api_name)
    if template is None:
        raise ProviderSpecError(FND_PROVIDER_API_NOT_APPROVED, f"tushare api not approved: {api_name}")
    return _render_spec(template, normalized)


def render_akshare_spec(api_name: str, normalized: NormalizedInput) -> ApiCallSpec:
    template = _AKSHARE_BY_NAME.get(api_name)
    if template is None:
        raise ProviderSpecError(FND_PROVIDER_API_NOT_APPROVED, f"akshare api not approved: {api_name}")
    return _render_spec(template, normalized)


def _render_spec(template: ProviderApiTemplate, normalized: NormalizedInput) -> ApiCallSpec:
    rendered_parameters: dict[str, str] = {}
    resolver = _LazyTemplateVariableResolver(normalized)
    formatter = Formatter()
    for key, value in template.parameters_template.items():
        template_var_names = {name for _, name, _, _ in formatter.parse(value) if name}
        for template_var_name in template_var_names:
            resolver.resolve(template_var_name)
        try:
            rendered_parameters[key] = value.format_map(resolver.values)
        except KeyError as exc:
            raise ProviderSpecError(
                FND_PROVIDER_SPEC_INVALID,
                f"unknown template variable in template {template.api_name}.{key}: {exc}",
            ) from exc

    return ApiCallSpec(
        provider=template.provider,
        api_name=template.api_name,
        role=template.role,
        required=template.required,
        field_family=template.field_family,
        parameters=rendered_parameters,
        timeout_ms=template.timeout_ms,
        retry_limit=template.retry_limit,
    )


@dataclass
class _LazyTemplateVariableResolver:
    normalized: NormalizedInput
    values: dict[str, str] = field(default_factory=dict)

    def resolve(self, template_var_name: str) -> str:
        if template_var_name in self.values:
            return self.values[template_var_name]
        value = _resolve_template_variable_value(template_var_name, self.normalized)
        self.values[template_var_name] = value
        return value


def _resolve_template_variable_value(template_var_name: str, normalized: NormalizedInput) -> str:
    if template_var_name == "tushare_code":
        return normalized.tushare_code
    if template_var_name == "akshare_symbol":
        return normalized.akshare_symbol
    if template_var_name == "latest_report_period":
        return _required_value("latest_report_period", normalized.latest_report_period)
    if template_var_name == "current_date_yyyymmdd":
        return _date_to_yyyymmdd("current_date", normalized.current_date)
    if template_var_name == "start_date_yyyymmdd":
        return _date_to_yyyymmdd("start_date", normalized.start_date)
    if template_var_name == "end_date_yyyymmdd":
        return _date_to_yyyymmdd("end_date", normalized.end_date)
    if template_var_name == "tushare_exchange":
        return "SSE" if normalized.exchange == "SH" else "SZSE"
    raise ProviderSpecError(FND_PROVIDER_SPEC_INVALID, f"unknown template variable: {template_var_name}")


def _assert_template_catalog(
    templates: tuple[ProviderApiTemplate, ...],
    *,
    expected_provider: Literal["tushare", "akshare"],
    expected_count: int,
) -> None:
    if len(templates) != expected_count:
        raise ProviderSpecError(
            FND_PROVIDER_SPEC_INVALID,
            f"{expected_provider} allowlist count mismatch: {len(templates)}",
        )
    seen_names: set[str] = set()
    for template in templates:
        if template.provider != expected_provider:
            raise ProviderSpecError(
                FND_PROVIDER_SPEC_INVALID,
                f"provider mismatch for api {template.api_name}: {template.provider}",
            )
        if template.api_name in seen_names:
            raise ProviderSpecError(FND_PROVIDER_SPEC_INVALID, f"duplicate api_name: {template.api_name}")
        seen_names.add(template.api_name)
        lowered = template.api_name.lower()
        if "baostock" in lowered or "financemcp" in lowered:
            raise ProviderSpecError(
                FND_PROVIDER_SPEC_INVALID,
                f"forbidden provider api included: {template.api_name}",
            )
        if template.timeout_ms <= 0:
            raise ProviderSpecError(FND_PROVIDER_SPEC_INVALID, f"timeout must be positive: {template.api_name}")
        if template.retry_limit < 0:
            raise ProviderSpecError(FND_PROVIDER_SPEC_INVALID, f"retry_limit must be >= 0: {template.api_name}")
        if template.field_family.strip() == "":
            raise ProviderSpecError(FND_PROVIDER_SPEC_INVALID, f"field_family missing: {template.api_name}")


def _required_value(field_name: str, value: str | None) -> str:
    if value is None or value.strip() == "":
        raise ProviderSpecError(FND_PROVIDER_SPEC_INVALID, f"{field_name} is required for spec rendering")
    return value


def _date_to_yyyymmdd(field_name: str, value: str | None) -> str:
    if value is None:
        raise ProviderSpecError(FND_PROVIDER_SPEC_INVALID, f"{field_name} is required for spec rendering")
    text = value.strip()
    if len(text) != 10 or text[4] != "-" or text[7] != "-":
        raise ProviderSpecError(FND_PROVIDER_SPEC_INVALID, f"{field_name} must be YYYY-MM-DD: {value}")
    return text.replace("-", "")


assert_provider_api_allowlist_v1()
