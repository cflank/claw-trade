from __future__ import annotations

from claw_trade.data_gateway.models import HttpVisibility

from .models import ParserStatus, endpoint, no_batch, output_contract


def _public(
    *,
    provider_id: str,
    source_type: str,
    endpoint_id: str,
    official_path_or_api_name: str,
    required_params: tuple[str, ...] = (),
    optional_params: tuple[str, ...] = (),
    auth: str = "none",
    http_visibility: HttpVisibility | str = HttpVisibility.MANAGED_HTTP,
    doc: str,
    parser_status: ParserStatus | None = None,
    adapter_endpoint_id: str | None = None,
):
    outputs = _PUBLIC_OUTPUTS_BY_ENDPOINT_ID.get(endpoint_id, ())
    response_shape = {"outputs": outputs} if outputs else {}
    request_template = {"target": official_path_or_api_name}
    if adapter_endpoint_id:
        request_template["adapter_endpoint_id"] = adapter_endpoint_id
    return endpoint(
        provider_id=provider_id,
        source_type=source_type,
        endpoint_id=endpoint_id,
        official_path_or_api_name=official_path_or_api_name,
        method="GET",
        required_params=required_params,
        optional_params=optional_params,
        auth=auth,
        rate_limit_bucket=f"ratelimit:{source_type}",
        batch_policy=no_batch(),
        parser_status=parser_status,
        official_doc_ref=doc,
        http_visibility=http_visibility,
        request_template=request_template,
        response_shape=response_shape,
    )


AKSHARE_DOC = "https://akshare.akfamily.xyz"
EASTMONEY_DOC = "https://quote.eastmoney.com/center/api.html"
MOOTDX_DOC = "https://github.com/mootdx/mootdx"
BAOSTOCK_DOC = "http://baostock.com/baostock/index.php/Python_API%E6%96%87%E6%A1%A3"
YAHOO_DOC = "https://query1.finance.yahoo.com"
BINANCE_DOC = "https://developers.binance.com/docs/binance-spot-api-docs/rest-api"
BINANCE_FUTURES_DOC = "https://developers.binance.com/docs/derivatives/usds-margined-futures/market-data/rest-api"
BINANCE_OPTIONS_DOC = "https://developers.binance.com/docs/derivatives/options-trading/market-data"
COINGECKO_DOC = "https://docs.coingecko.com/reference"
GOOGLE_NEWS_DOC = "https://news.google.com/rss"
TUSHARE_SDK_DOC = "https://tushare.pro/document/2?doc_id=316"

_PUBLIC_OUTPUTS_BY_ENDPOINT_ID = {
    "akshare.stock_zh_a_hist": (
        output_contract(
            market="CN_A",
            data_type="daily_bar",
            public_api_ids=('market_price',),
            granularity="daily",
            fields=("date", "open", "high", "low", "close", "volume", "amount", "amount_unit", "adjustment"),
            priority_rank=20,
        ),
    ),
    "akshare.stock_zh_a_spot_em": (
        output_contract(
            market="CN_A",
            data_type="quote_snapshot",
            public_api_ids=('quote_snapshot', 'market_price'),
            granularity="realtime",
            fields=("price", "change", "change_pct", "volume", "amount", "amount_unit", "timestamp", "symbol_id", "name", "company_name"),
            priority_rank=18,
        ),
    ),
    "akshare.stock_individual_info_em": (
        output_contract(
            market="CN_A",
            data_type="valuation_metric",
            public_api_ids=('valuation_metric',),
            granularity="realtime",
            fields=("market_cap", "market_cap_unit", "price", "symbol_id"),
            priority_rank=24,
        ),
    ),
    "akshare.stock_news_em": (
        output_contract(
            market="CN_A",
            data_type="company_news",
            public_api_ids=('company_news',),
            granularity="event",
            fields=("title", "published_at", "source", "summary", "url"),
            priority_rank=20,
        ),
    ),
    "akshare.stock_hot_rank_latest_em": (
        output_contract(
            market="CN_A",
            data_type="social_signal",
            public_api_ids=('social_signal',),
            granularity="event",
            fields=("source", "timestamp", "metrics", "symbol_id"),
            priority_rank=30,
        ),
    ),
    "akshare.stock_hot_keyword_em": (
        output_contract(
            market="CN_A",
            data_type="social_signal",
            public_api_ids=('social_signal',),
            granularity="event",
            fields=("source", "timestamp", "keyword", "score", "symbol_id"),
            priority_rank=30,
        ),
    ),
    "akshare.stock_hot_rank_relate_em": (
        output_contract(
            market="CN_A",
            data_type="social_signal",
            public_api_ids=('social_signal',),
            granularity="event",
            fields=("source", "timestamp", "related_symbol", "change_pct", "symbol_id"),
            priority_rank=30,
        ),
    ),
    "akshare.stock_individual_fund_flow": (
        output_contract(
            market="CN_A",
            data_type="capital_flow",
            public_api_ids=('capital_flow', 'money_flow'),
            granularity="daily",
            fields=("date", "main_net", "super_net", "large_net", "mid_net", "small_net", "amount_unit", "symbol_id"),
            priority_rank=11,
        ),
    ),
    "akshare.stock_sector_fund_flow_rank": (
        output_contract(
            market="CN_A",
            data_type="sector_snapshot",
            public_api_ids=('sector_snapshot', 'sector_flow'),
            granularity="event",
            fields=("sector_name", "main_net", "super_net", "large_net", "mid_net", "small_net", "amount_unit", "timestamp"),
            priority_rank=11,
        ),
    ),
    "eastmoney.securities.financial_analysis_indicator": (
        output_contract(
            market="CN_A",
            data_type="financial_metric",
            public_api_ids=('financial_metric',),
            granularity="quarterly",
            fields=("roe", "roa", "gross_margin", "debt_ratio", "eps", "revenue", "net_income"),
            priority_rank=20,
        ),
    ),
    "eastmoney.hsf10.financial_statement": (
        output_contract(
            market="CN_A",
            data_type="financial_statement",
            public_api_ids=('financial_statement',),
            granularity="quarterly",
            fields=("period", "revenue", "net_income", "assets", "liabilities", "cash_flow", "amount_unit"),
            priority_rank=20,
        ),
    ),
    "akshare.stock_financial_hk_analysis_indicator_em": (
        output_contract(
            market="HK",
            data_type="financial_metric",
            public_api_ids=('financial_metric',),
            granularity="quarterly",
            fields=("roe", "gross_margin", "eps"),
            priority_rank=20,
        ),
    ),
    "akshare.stock_financial_hk_report_em_income": (
        output_contract(
            market="HK",
            data_type="financial_statement",
            public_api_ids=('financial_statement',),
            granularity=("quarterly", "annual"),
            fields=("period", "revenue", "net_income"),
            priority_rank=20,
        ),
    ),
    "eastmoney.push2his.daily_bar": (
        output_contract(
            market="CN_A",
            data_type="daily_bar",
            public_api_ids=('market_price',),
            granularity="daily",
            fields=("date", "open", "high", "low", "close", "volume", "amount", "amount_unit", "adjustment"),
            priority_rank=30,
        ),
    ),
    "eastmoney.push2.spot_quote_batch": (
        output_contract(
            market="CN_A",
            data_type="quote_snapshot",
            public_api_ids=('quote_snapshot', 'market_price'),
            granularity="realtime",
            fields=("price", "change", "change_pct", "volume", "amount", "amount_unit", "timestamp", "symbol_id", "name", "company_name"),
            priority_rank=12,
        ),
    ),
    "eastmoney.datacenter.stock_fund_flow_daily": (
        output_contract(
            market="CN_A",
            data_type="capital_flow",
            public_api_ids=('capital_flow', 'money_flow'),
            granularity="daily",
            fields=("date", "main_net", "small_net", "mid_net", "large_net", "super_net", "amount_unit", "symbol_id"),
            priority_rank=12,
        ),
    ),
    "eastmoney.datacenter.sector_fund_flow_rank": (
        output_contract(
            market="CN_A",
            data_type="sector_snapshot",
            public_api_ids=('sector_snapshot', 'sector_flow'),
            granularity="event",
            fields=("sector_code", "sector_name", "main_net", "super_net", "large_net", "mid_net", "small_net", "amount_unit", "timestamp"),
            priority_rank=12,
        ),
    ),
    "eastmoney.datacenter.block_trade": (
        output_contract(
            market="CN_A",
            data_type="capital_flow",
            public_api_ids=('capital_flow', 'money_flow'),
            granularity="event",
            fields=("trade_date", "buyer", "seller", "price", "volume", "amount", "symbol_id"),
            priority_rank=18,
        ),
    ),
    "eastmoney.datacenter.margin_trading_detail": (
        output_contract(
            market="CN_A",
            data_type="margin_trading",
            public_api_ids=('margin_trading',),
            granularity="daily",
            fields=("date", "financing_balance", "margin_balance", "security_lending_volume", "symbol_id"),
            priority_rank=22,
        ),
    ),
    "eastmoney.datacenter.dividend_event": (
        output_contract(
            market="CN_A",
            data_type="corporate_action",
            public_api_ids=('corporate_action',),
            granularity="event",
            fields=("event_type", "event_date", "title", "source", "dividend", "symbol_id"),
            priority_rank=20,
        ),
    ),
    "eastmoney.datacenter.shareholder_count": (
        output_contract(
            market="CN_A",
            data_type="corporate_action",
            public_api_ids=('corporate_action',),
            granularity="event",
            fields=("event_type", "event_date", "title", "source", "holder_count", "symbol_id"),
            priority_rank=18,
        ),
    ),
    "eastmoney.appdata.global_news_7x24": (
        output_contract(
            market="CN_A",
            data_type="macro_news",
            public_api_ids=('macro_news',),
            granularity="event",
            fields=("title", "published_at", "region", "summary", "url"),
            priority_rank=18,
        ),
    ),
    "eastmoney.search.stock_info": (
        output_contract(
            market="CN_A",
            data_type="valuation_metric",
            public_api_ids=('valuation_metric',),
            granularity="realtime",
            fields=("market_cap", "market_cap_unit", "price", "symbol_id"),
            priority_rank=24,
        ),
    ),
    "eastmoney.np_weblist.lockup_event": (
        output_contract(
            market="CN_A",
            data_type="lockup_event",
            public_api_ids=('lockup_event',),
            granularity="event",
            fields=("unlock_date", "shares", "market_value", "holder"),
            priority_rank=10,
        ),
    ),
    "eastmoney.datacenter.hot_money_event": (
        output_contract(
            market="CN_A",
            data_type="hot_money_event",
            public_api_ids=('hot_money_event',),
            granularity="event",
            fields=("trade_date", "seat", "buy_amount", "sell_amount", "symbol_id"),
            priority_rank=10,
        ),
    ),
    "mootdx.daily_bar": (
        output_contract(
            market="CN_A",
            data_type="daily_bar",
            public_api_ids=('market_price',),
            granularity="daily",
            fields=("date", "open", "high", "low", "close", "volume", "amount", "amount_unit", "adjustment"),
            priority_rank=32,
        ),
    ),
    "mootdx.intraday_bar": (
        output_contract(
            market="CN_A",
            data_type="intraday_bar",
            public_api_ids=('market_price',),
            granularity="intraday",
            fields=("timestamp", "open", "high", "low", "close", "volume"),
            priority_rank=28,
        ),
    ),
    "mootdx.quote_snapshot": (
        output_contract(
            market="CN_A",
            data_type="quote_snapshot",
            public_api_ids=('quote_snapshot', 'market_price'),
            granularity="realtime",
            fields=("price", "change", "change_pct", "volume", "amount", "amount_unit", "timestamp", "symbol_id", "name", "company_name"),
            priority_rank=15,
        ),
    ),
    "mootdx.order_book_snapshot": (
        output_contract(
            market="CN_A",
            data_type="order_book_snapshot",
            public_api_ids=('order_book_snapshot',),
            granularity="realtime",
            fields=("bid_price", "bid_size", "ask_price", "ask_size", "timestamp", "symbol_id"),
            priority_rank=15,
        ),
    ),
    "mootdx.corporate_action": (
        output_contract(
            market="CN_A",
            data_type="corporate_action",
            public_api_ids=('corporate_action',),
            granularity="event",
            fields=("event_type", "event_date", "title", "source", "adjustment", "symbol_id"),
            priority_rank=36,
        ),
    ),
    "mootdx.finance_snapshot": (
        output_contract(
            market="CN_A",
            data_type="financial_metric",
            public_api_ids=('financial_metric',),
            granularity="quarterly",
            fields=("roe", "eps", "symbol_id"),
            priority_rank=34,
        ),
    ),
    "tushare_sdk.realtime_quote": (
        output_contract(
            market="CN_A",
            data_type="quote_snapshot",
            public_api_ids=('quote_snapshot', 'market_price'),
            granularity="realtime",
            fields=("price", "open", "high", "low", "previous_close", "bid_price", "ask_price", "volume", "amount", "amount_unit", "timestamp", "symbol_id", "name", "company_name"),
            priority_rank=7,
        ),
    ),
    "tushare_sdk.realtime_order_book": (
        output_contract(
            market="CN_A",
            data_type="order_book_snapshot",
            public_api_ids=('order_book_snapshot',),
            granularity="realtime",
            fields=("bid_price", "bid_size", "ask_price", "ask_size", "timestamp", "symbol_id"),
            priority_rank=7,
        ),
    ),
    "baostock.daily_bar": (
        output_contract(
            market="CN_A",
            data_type="daily_bar",
            public_api_ids=('market_price',),
            granularity="daily",
            fields=("date", "open", "high", "low", "close", "volume", "amount", "amount_unit", "adjustment"),
            priority_rank=25,
        ),
    ),
    "baostock.intraday_bar": (
        output_contract(
            market="CN_A",
            data_type="intraday_bar",
            public_api_ids=('market_price',),
            granularity="intraday",
            fields=("timestamp", "open", "high", "low", "close", "volume"),
            priority_rank=32,
        ),
    ),
    "baostock.adjust_factor": (
        output_contract(
            market="CN_A",
            data_type="corporate_action",
            public_api_ids=('corporate_action',),
            granularity="event",
            fields=("event_type", "event_date", "title", "source", "adjust_factor", "symbol_id"),
            priority_rank=24,
        ),
    ),
    "baostock.dividend": (
        output_contract(
            market="CN_A",
            data_type="corporate_action",
            public_api_ids=('corporate_action',),
            granularity="event",
            fields=("event_type", "event_date", "title", "source", "dividend", "symbol_id"),
            priority_rank=26,
        ),
    ),
    "baostock.financial_metric": (
        output_contract(
            market="CN_A",
            data_type="financial_metric",
            public_api_ids=('financial_metric',),
            granularity="quarterly",
            fields=("roe", "roa", "gross_margin", "debt_ratio", "eps"),
            priority_rank=30,
        ),
    ),
    "baostock.financial_statement": (
        output_contract(
            market="CN_A",
            data_type="financial_statement",
            public_api_ids=('financial_statement',),
            granularity="quarterly",
            fields=("period", "revenue", "net_income", "assets", "liabilities", "cash_flow", "amount_unit"),
            priority_rank=30,
        ),
    ),
    "baostock.trade_calendar": (
        output_contract(
            market="CN_A",
            data_type="event_calendar",
            public_api_ids=('event_calendar',),
            granularity="event",
            fields=("event_type", "event_date", "title", "source"),
            priority_rank=32,
        ),
    ),
    "baostock.stock_industry": (
        output_contract(
            market="CN_A",
            data_type="sector_snapshot",
            public_api_ids=('sector_snapshot',),
            granularity="event",
            fields=("sector_name", "timestamp", "symbol_id"),
            priority_rank=34,
        ),
    ),
    "cninfo.official_filing": (
        output_contract(
            market="CN_A",
            data_type="official_filing",
            public_api_ids=('official_filing',),
            granularity="event",
            fields=("title", "published_at", "url", "source", "body_ref"),
            priority_rank=5,
        ),
    ),
    "cninfo.event_calendar": (
        output_contract(
            market="CN_A",
            data_type="event_calendar",
            public_api_ids=('event_calendar',),
            granularity="event",
            fields=("event_type", "event_date", "title", "source"),
            priority_rank=5,
        ),
    ),
    "yahoo_finance.chart_daily": (
        output_contract(
            market="US",
            data_type="daily_bar",
            public_api_ids=('market_price',),
            granularity="daily",
            fields=("open", "high", "low", "close", "volume"),
            priority_rank=25,
        ),
    ),
    "yahoo_finance.quote_snapshot": (
        output_contract(
            market="US",
            data_type="quote_snapshot",
            public_api_ids=('quote_snapshot', 'market_price'),
            granularity="realtime",
            fields=("price", "change", "change_pct", "volume", "timestamp", "symbol_id"),
            priority_rank=10,
        ),
    ),
    "yahoo_finance.fundamentals_timeseries": (
        output_contract(
            market="US",
            data_type="financial_metric",
            public_api_ids=('financial_metric',),
            granularity=("quarterly", "realtime"),
            fields=("gross_margin", "eps"),
            priority_rank=10,
        ),
        output_contract(
            market="US",
            data_type="valuation_metric",
            public_api_ids=('valuation_metric',),
            granularity="realtime",
            fields=("pe", "market_cap"),
            priority_rank=10,
        ),
    ),
    "yahoo_finance.hk_chart_daily": (
        output_contract(
            market="HK",
            data_type="daily_bar",
            public_api_ids=('market_price',),
            granularity="daily",
            fields=("open", "high", "low", "close", "volume"),
            priority_rank=30,
        ),
    ),
    "yahoo_finance.hk_quote_summary_financial": (
        output_contract(
            market="HK",
            data_type="financial_metric",
            public_api_ids=('financial_metric',),
            granularity=("quarterly", "realtime"),
            fields=("gross_margin", "eps"),
            priority_rank=5,
        ),
    ),
    "yahoo_finance.hk_quote_summary_valuation": (
        output_contract(
            market="HK",
            data_type="valuation_metric",
            public_api_ids=('valuation_metric',),
            granularity="realtime",
            fields=("pe", "market_cap"),
            priority_rank=5,
        ),
    ),
    "binance.ticker_24hr": (
        output_contract(
            market="CRYPTO",
            data_type="quote_snapshot",
            public_api_ids=('quote_snapshot', 'market_price'),
            granularity="realtime",
            fields=("price", "change", "change_pct", "volume", "amount", "timestamp", "symbol_id"),
            priority_rank=10,
        ),
    ),
    "binance.order_book_depth": (
        output_contract(
            market="CRYPTO",
            data_type="order_book_snapshot",
            public_api_ids=('order_book_snapshot',),
            granularity="realtime",
            fields=("bid_price", "bid_size", "ask_price", "ask_size", "bid_levels", "ask_levels", "timestamp", "symbol_id"),
            priority_rank=10,
        ),
    ),
    "binance.spot_intraday_bar": (
        output_contract(
            market="CRYPTO",
            data_type="intraday_bar",
            public_api_ids=('intraday_bar', 'market_price'),
            granularity=("1h", "1m"),
            fields=("open", "high", "low", "close", "volume", "volume_unit", "amount", "amount_unit"),
            priority_rank=15,
        ),
    ),
    "binance.spot_daily_bar": (
        output_contract(
            market="CRYPTO",
            data_type="daily_bar",
            public_api_ids=('daily_bar', 'market_price'),
            granularity="daily",
            fields=("open", "high", "low", "close", "volume", "volume_unit", "amount", "amount_unit"),
            priority_rank=20,
        ),
    ),
    "binance.futures_funding_rate": (
        output_contract(
            market="CRYPTO",
            data_type="crypto_derivative_metric",
            public_api_ids=('funding_rate',),
            granularity=("realtime", "1h", "daily"),
            fields=("funding_rate", "funding_rate_unit", "timestamp", "symbol_id"),
            priority_rank=28,
        ),
    ),
    "binance.futures_open_interest": (
        output_contract(
            market="CRYPTO",
            data_type="crypto_derivative_metric",
            public_api_ids=('open_interest',),
            granularity=("realtime",),
            fields=("open_interest", "open_interest_unit", "timestamp", "symbol_id"),
            priority_rank=28,
        ),
    ),
    "binance.futures_open_interest_hist": (
        output_contract(
            market="CRYPTO",
            data_type="crypto_derivative_metric",
            public_api_ids=('open_interest',),
            granularity=("realtime", "1h", "daily"),
            fields=("open_interest", "open_interest_unit", "timestamp", "symbol_id"),
            priority_rank=29,
        ),
    ),
    "binance.futures_long_short_ratio": (
        output_contract(
            market="CRYPTO",
            data_type="crypto_derivative_metric",
            public_api_ids=('long_short_ratio',),
            granularity=("realtime", "1h", "daily"),
            fields=("long_short_ratio", "metric", "timestamp", "symbol_id"),
            priority_rank=28,
        ),
    ),
    "binance.futures_top_long_short_account_ratio": (
        output_contract(
            market="CRYPTO",
            data_type="crypto_derivative_metric",
            public_api_ids=('long_short_ratio',),
            granularity=("realtime", "1h", "daily"),
            fields=("long_short_ratio", "metric", "timestamp", "symbol_id"),
            priority_rank=29,
        ),
    ),
    "binance.futures_top_long_short_position_ratio": (
        output_contract(
            market="CRYPTO",
            data_type="crypto_derivative_metric",
            public_api_ids=('long_short_ratio',),
            granularity=("realtime", "1h", "daily"),
            fields=("long_short_ratio", "metric", "timestamp", "symbol_id"),
            priority_rank=29,
        ),
    ),
    "binance.futures_taker_buy_sell": (
        output_contract(
            market="CRYPTO",
            data_type="crypto_derivative_metric",
            public_api_ids=('taker_buy_sell',),
            granularity=("realtime", "1h", "daily"),
            fields=("taker_buy_volume", "taker_sell_volume", "taker_volume_unit", "taker_buy_sell_ratio", "timestamp", "symbol_id"),
            priority_rank=28,
        ),
    ),
    "binance.options_open_interest": (
        output_contract(
            market="CRYPTO",
            data_type="crypto_derivative_metric",
            public_api_ids=('options_open_interest',),
            granularity=("realtime",),
            fields=("options_open_interest", "options_open_interest_unit", "timestamp", "symbol_id"),
            priority_rank=28,
        ),
    ),
    "binance.options_ticker": (
        output_contract(
            market="CRYPTO",
            data_type="crypto_derivative_metric",
            public_api_ids=('options_volume',),
            granularity=("realtime",),
            fields=("options_volume", "options_volume_unit", "timestamp", "symbol_id"),
            priority_rank=28,
        ),
    ),
    "coingecko.coins_markets": (
        output_contract(
            market="CRYPTO",
            data_type="valuation_metric",
            public_api_ids=('valuation_metric',),
            granularity="realtime",
            fields=("price", "price_unit", "market_cap", "market_cap_unit", "fdv", "fdv_unit", "circulating_supply", "total_supply", "max_supply", "supply_unit", "volume", "volume_unit", "timestamp", "symbol_id"),
            priority_rank=20,
        ),
    ),
    "coingecko.coins_id": (
        output_contract(
            market="CRYPTO",
            data_type="company_profile",
            public_api_ids=('company_profile',),
            granularity="event",
            fields=("name", "symbol", "description", "homepage", "market_cap_rank", "circulating_supply", "total_supply", "max_supply", "supply_unit", "symbol_id"),
            priority_rank=20,
        ),
    ),
    "sec.submissions": (
        output_contract(
            market="US",
            data_type="official_filing",
            public_api_ids=('official_filing',),
            granularity="event",
            fields=("title", "published_at", "source", "url", "symbol_id"),
            priority_rank=0,
        ),
    ),
    "sec.companyfacts": (
        output_contract(
            market="US",
            data_type="financial_statement",
            public_api_ids=('financial_statement',),
            granularity=("quarterly", "annual"),
            fields=("period", "revenue", "net_income", "assets", "liabilities", "cash_flow"),
            priority_rank=0,
        ),
    ),
    "stocktwits.symbol_stream": (
        output_contract(
            market="US",
            data_type="social_signal",
            public_api_ids=('social_signal',),
            granularity="event",
            fields=("source", "timestamp", "message", "sentiment", "symbol_id"),
            priority_rank=10,
        ),
    ),
    "sina_hk.stock_hk_daily": (
        output_contract(
            market="HK",
            data_type="daily_bar",
            public_api_ids=('market_price',),
            granularity="daily",
            fields=("open", "high", "low", "close", "volume"),
            priority_rank=25,
        ),
    ),
    "hkexnews.regulatory_announcements": (
        output_contract(
            market="HK",
            data_type="official_filing",
            public_api_ids=('official_filing',),
            granularity="event",
            fields=("title", "published_at", "source", "url", "symbol_id"),
            priority_rank=0,
        ),
    ),
    "hkexnews.regulatory_calendar": (
        output_contract(
            market="HK",
            data_type="event_calendar",
            public_api_ids=('event_calendar',),
            granularity="event",
            fields=("event_date", "event_type", "title", "source", "url", "symbol_id"),
            priority_rank=0,
        ),
    ),
    "defillama.protocol": (
        output_contract(
            market="CRYPTO",
            data_type="defi_metric",
            public_api_ids=('defi_metric',),
            granularity=("daily", "realtime"),
            fields=("tvl", "chains", "category", "symbol_id"),
            priority_rank=20,
        ),
    ),
    "alternative_me.fear_greed": (
        output_contract(
            market="CRYPTO",
            data_type="social_signal",
            public_api_ids=('social_signal',),
            granularity="event",
            fields=("source", "timestamp", "score", "sentiment", "symbol_id"),
            priority_rank=20,
        ),
    ),
    "github.releases": (
        output_contract(
            market="CRYPTO",
            data_type="company_news",
            public_api_ids=('company_news',),
            granularity="event",
            fields=("title", "published_at", "source", "summary", "url"),
            priority_rank=0,
        ),
        output_contract(
            market="CRYPTO",
            data_type="event_calendar",
            public_api_ids=('event_calendar',),
            granularity="event",
            fields=("title", "published_at", "source", "summary", "url"),
            priority_rank=35,
        ),
    ),
    "google_news.cn_a_company_news": (
        output_contract(
            market="CN_A",
            data_type="company_news",
            public_api_ids=('company_news',),
            granularity="event",
            fields=("title", "published_at", "source", "summary", "url"),
            priority_rank=50,
        ),
    ),
    "google_news.cn_a_macro_news": (
        output_contract(
            market="CN_A",
            data_type="macro_news",
            public_api_ids=('macro_news',),
            granularity="event",
            fields=("title", "published_at", "region", "summary", "url"),
            priority_rank=50,
        ),
    ),
    "google_news.us_company_news": (
        output_contract(
            market="US",
            data_type="company_news",
            public_api_ids=('company_news',),
            granularity="event",
            fields=("title", "published_at", "source", "summary", "url"),
            priority_rank=50,
        ),
    ),
    "google_news.us_macro_news": (
        output_contract(
            market="US",
            data_type="macro_news",
            public_api_ids=('macro_news',),
            granularity="event",
            fields=("title", "published_at", "source", "summary", "url", "region"),
            priority_rank=50,
        ),
    ),
    "google_news.hk_company_news": (
        output_contract(
            market="HK",
            data_type="company_news",
            public_api_ids=('company_news',),
            granularity="event",
            fields=("title", "published_at", "source", "summary", "url"),
            priority_rank=50,
        ),
    ),
    "google_news.hk_macro_news": (
        output_contract(
            market="HK",
            data_type="macro_news",
            public_api_ids=('macro_news',),
            granularity="event",
            fields=("title", "published_at", "source", "summary", "url", "region"),
            priority_rank=50,
        ),
    ),
    "google_news.hk_social_signal_news_heat": (
        output_contract(
            market="HK",
            data_type="social_signal",
            public_api_ids=('social_signal',),
            granularity="event",
            fields=("source", "timestamp", "title", "url", "symbol_id"),
            priority_rank=50,
        ),
    ),
    "google_news.crypto_company_news": (
        output_contract(
            market="CRYPTO",
            data_type="company_news",
            public_api_ids=('company_news',),
            granularity="event",
            fields=("title", "published_at", "source", "summary", "url"),
            priority_rank=50,
        ),
    ),
    "google_news.crypto_macro_news": (
        output_contract(
            market="CRYPTO",
            data_type="macro_news",
            public_api_ids=('macro_news',),
            granularity="event",
            fields=("title", "published_at", "source", "summary", "url", "region"),
            priority_rank=45,
        ),
    ),
    "google_news.crypto_event_calendar": (
        output_contract(
            market="CRYPTO",
            data_type="event_calendar",
            public_api_ids=('event_calendar',),
            granularity="event",
            fields=("title", "published_at", "source", "summary", "url"),
            priority_rank=55,
        ),
    ),
    "ths.hot_reason": (
        output_contract(
            market="CN_A",
            data_type="social_signal",
            public_api_ids=('social_signal',),
            granularity="event",
            fields=("source", "timestamp", "symbol_id", "name", "topic", "reason", "change_pct", "turnover_rate", "amount", "volume", "large_order_net"),
            priority_rank=25,
        ),
    ),
    "ths.northbound_flow": (
        output_contract(
            market="CN_A",
            data_type="northbound_flow",
            public_api_ids=('northbound_flow',),
            granularity="realtime",
            fields=("timestamp", "hgt_net", "sgt_net", "northbound_net", "amount_unit", "source"),
            priority_rank=12,
        ),
    ),
    "baidu.concept_blocks": (
        output_contract(
            market="CN_A",
            data_type="social_signal",
            public_api_ids=('social_signal',),
            granularity="event",
            fields=("source", "timestamp", "symbol_id", "topic", "concept", "industry", "region", "change_pct", "description"),
            priority_rank=26,
        ),
    ),
}


ENDPOINTS = (
    _public(provider_id="cn_a_akshare_social_news", source_type="akshare", endpoint_id="akshare.stock_zh_a_hist", official_path_or_api_name="stock_zh_a_hist", required_params=("symbol",), optional_params=("period", "start_date", "end_date", "adjust"), doc=AKSHARE_DOC),
    _public(provider_id="cn_a_akshare_social_news", source_type="akshare", endpoint_id="akshare.stock_zh_a_spot_em", official_path_or_api_name="stock_zh_a_spot_em", doc=AKSHARE_DOC),
    _public(provider_id="cn_a_akshare_social_news", source_type="akshare", endpoint_id="akshare.stock_individual_info_em", official_path_or_api_name="stock_individual_info_em", required_params=("symbol",), doc=AKSHARE_DOC),
    _public(provider_id="cn_a_akshare_social_news", source_type="akshare", endpoint_id="akshare.stock_news_em", official_path_or_api_name="stock_news_em", required_params=("symbol",), doc=AKSHARE_DOC),
    _public(provider_id="cn_a_akshare_social_news", source_type="akshare", endpoint_id="akshare.stock_hot_rank_latest_em", official_path_or_api_name="stock_hot_rank_latest_em", doc=AKSHARE_DOC),
    _public(provider_id="cn_a_akshare_social_news", source_type="akshare", endpoint_id="akshare.stock_hot_keyword_em", official_path_or_api_name="stock_hot_keyword_em", required_params=("symbol",), doc=AKSHARE_DOC),
    _public(provider_id="cn_a_akshare_social_news", source_type="akshare", endpoint_id="akshare.stock_hot_rank_relate_em", official_path_or_api_name="stock_hot_rank_relate_em", required_params=("symbol",), doc=AKSHARE_DOC),
    _public(provider_id="cn_a_akshare_social_news", source_type="akshare", endpoint_id="akshare.stock_individual_fund_flow", official_path_or_api_name="stock_individual_fund_flow", required_params=("stock",), http_visibility=HttpVisibility.SDK_INTERNAL_UNKNOWN, doc=AKSHARE_DOC),
    _public(provider_id="cn_a_akshare_social_news", source_type="akshare", endpoint_id="akshare.stock_sector_fund_flow_rank", official_path_or_api_name="stock_sector_fund_flow_rank", optional_params=("indicator", "sector_type"), http_visibility=HttpVisibility.SDK_INTERNAL_UNKNOWN, doc=AKSHARE_DOC),
    _public(provider_id="cn_a_eastmoney_market_data", source_type="eastmoney", endpoint_id="eastmoney.securities.financial_analysis_indicator", official_path_or_api_name="/securities/api/data/get", required_params=("symbol",), optional_params=("indicator",), adapter_endpoint_id="financial_analysis_indicator", doc=EASTMONEY_DOC),
    _public(provider_id="cn_a_eastmoney_market_data", source_type="eastmoney", endpoint_id="eastmoney.hsf10.financial_statement", official_path_or_api_name="/PC_HSF10/NewFinanceAnalysis", required_params=("symbol",), adapter_endpoint_id="financial_statement", doc=EASTMONEY_DOC),
    _public(provider_id="hk_akshare_fundamental", source_type="akshare", endpoint_id="akshare.stock_financial_hk_analysis_indicator_em", official_path_or_api_name="stock_financial_hk_analysis_indicator_em", required_params=("symbol",), doc=AKSHARE_DOC),
    _public(provider_id="hk_akshare_fundamental", source_type="akshare", endpoint_id="akshare.stock_financial_hk_report_em_income", official_path_or_api_name="stock_financial_hk_report_em_income", required_params=("symbol",), doc=AKSHARE_DOC),

    _public(provider_id="cn_a_eastmoney_market_data", source_type="eastmoney", endpoint_id="eastmoney.push2his.daily_bar", official_path_or_api_name="/api/qt/stock/kline/get", required_params=("secid",), optional_params=("klt", "fqt", "beg", "end"), doc=EASTMONEY_DOC),
    _public(provider_id="cn_a_eastmoney_market_data", source_type="eastmoney", endpoint_id="eastmoney.push2.spot_quote_batch", official_path_or_api_name="/api/qt/clist/get", optional_params=("fs", "fields"), doc=EASTMONEY_DOC),
    _public(provider_id="cn_a_eastmoney_market_data", source_type="eastmoney", endpoint_id="eastmoney.datacenter.stock_fund_flow_daily", official_path_or_api_name="/securities/api/data/v1/get", optional_params=("reportName", "filter", "columns"), doc=EASTMONEY_DOC),
    _public(provider_id="cn_a_eastmoney_market_data", source_type="eastmoney", endpoint_id="eastmoney.datacenter.sector_fund_flow_rank", official_path_or_api_name="/securities/api/data/v1/get", optional_params=("reportName", "filter", "columns"), doc=EASTMONEY_DOC),
    _public(provider_id="cn_a_eastmoney_market_data", source_type="eastmoney", endpoint_id="eastmoney.datacenter.block_trade", official_path_or_api_name="/securities/api/data/v1/get", optional_params=("reportName", "filter", "columns"), doc=EASTMONEY_DOC),
    _public(provider_id="cn_a_eastmoney_market_data", source_type="eastmoney", endpoint_id="eastmoney.datacenter.margin_trading_detail", official_path_or_api_name="/securities/api/data/v1/get", optional_params=("reportName", "filter", "columns"), doc=EASTMONEY_DOC),
    _public(provider_id="cn_a_eastmoney_market_data", source_type="eastmoney", endpoint_id="eastmoney.datacenter.dividend_event", official_path_or_api_name="/securities/api/data/v1/get", optional_params=("reportName", "filter", "columns"), doc=EASTMONEY_DOC),
    _public(provider_id="cn_a_eastmoney_market_data", source_type="eastmoney", endpoint_id="eastmoney.datacenter.shareholder_count", official_path_or_api_name="/securities/api/data/v1/get", optional_params=("reportName", "filter", "columns"), doc=EASTMONEY_DOC),
    _public(provider_id="cn_a_eastmoney_market_data", source_type="eastmoney", endpoint_id="eastmoney.appdata.global_news_7x24", official_path_or_api_name="/data/v1/get", optional_params=("reportName", "columns"), doc=EASTMONEY_DOC),
    _public(provider_id="cn_a_eastmoney_market_data", source_type="eastmoney", endpoint_id="eastmoney.search.stock_info", official_path_or_api_name="/api/suggest/get", required_params=("input",), doc=EASTMONEY_DOC),
    _public(provider_id="cn_a_eastmoney_events", source_type="eastmoney", endpoint_id="eastmoney.np_weblist.lockup_event", official_path_or_api_name="/comm/web/getFastReports", optional_params=("pageSize", "pageNo"), doc=EASTMONEY_DOC),
    _public(provider_id="cn_a_eastmoney_events", source_type="eastmoney", endpoint_id="eastmoney.datacenter.hot_money_event", official_path_or_api_name="/securities/api/data/v1/get", optional_params=("reportName", "filter", "columns"), doc=EASTMONEY_DOC),

    _public(provider_id="cn_a_mootdx_market", source_type="mootdx", endpoint_id="mootdx.daily_bar", official_path_or_api_name="client.bars", required_params=("symbol",), http_visibility=HttpVisibility.SDK_INTERNAL_UNKNOWN, doc=MOOTDX_DOC),
    _public(provider_id="cn_a_mootdx_market", source_type="mootdx", endpoint_id="mootdx.intraday_bar", official_path_or_api_name="client.minutes", required_params=("symbol",), http_visibility=HttpVisibility.SDK_INTERNAL_UNKNOWN, doc=MOOTDX_DOC),
    _public(provider_id="cn_a_mootdx_market", source_type="mootdx", endpoint_id="mootdx.quote_snapshot", official_path_or_api_name="client.quotes", required_params=("symbol",), http_visibility=HttpVisibility.SDK_INTERNAL_UNKNOWN, doc=MOOTDX_DOC),
    _public(provider_id="cn_a_mootdx_market", source_type="mootdx", endpoint_id="mootdx.order_book_snapshot", official_path_or_api_name="client.transaction", required_params=("symbol",), http_visibility=HttpVisibility.SDK_INTERNAL_UNKNOWN, doc=MOOTDX_DOC),
    _public(provider_id="cn_a_mootdx_market", source_type="mootdx", endpoint_id="mootdx.corporate_action", official_path_or_api_name="reader.fq_factor", required_params=("symbol",), http_visibility=HttpVisibility.SDK_INTERNAL_UNKNOWN, doc=MOOTDX_DOC),
    _public(provider_id="cn_a_mootdx_market", source_type="mootdx", endpoint_id="mootdx.finance_snapshot", official_path_or_api_name="reader.financial", required_params=("symbol",), http_visibility=HttpVisibility.SDK_INTERNAL_UNKNOWN, doc=MOOTDX_DOC),

    _public(provider_id="cn_a_tushare_realtime", source_type="tushare", endpoint_id="tushare_sdk.realtime_quote", official_path_or_api_name="tushare.realtime_quote", required_params=("ts_code",), optional_params=("src",), auth="tushare_sdk_token", http_visibility=HttpVisibility.SDK_INTERNAL_UNKNOWN, doc=TUSHARE_SDK_DOC),
    _public(provider_id="cn_a_tushare_realtime", source_type="tushare", endpoint_id="tushare_sdk.realtime_order_book", official_path_or_api_name="tushare.realtime_quote", required_params=("ts_code",), optional_params=("src",), auth="tushare_sdk_token", http_visibility=HttpVisibility.SDK_INTERNAL_UNKNOWN, doc=TUSHARE_SDK_DOC),

    _public(provider_id="cn_a_baostock_market", source_type="baostock", endpoint_id="baostock.daily_bar", official_path_or_api_name="query_history_k_data_plus", required_params=("code",), optional_params=("start_date", "end_date", "frequency", "adjustflag"), http_visibility=HttpVisibility.SDK_INTERNAL_UNKNOWN, doc=BAOSTOCK_DOC),
    _public(provider_id="cn_a_baostock_market", source_type="baostock", endpoint_id="baostock.intraday_bar", official_path_or_api_name="query_history_k_data_plus", required_params=("code",), optional_params=("frequency",), http_visibility=HttpVisibility.SDK_INTERNAL_UNKNOWN, doc=BAOSTOCK_DOC),
    _public(provider_id="cn_a_baostock_market", source_type="baostock", endpoint_id="baostock.adjust_factor", official_path_or_api_name="query_adjust_factor", required_params=("code",), http_visibility=HttpVisibility.SDK_INTERNAL_UNKNOWN, doc=BAOSTOCK_DOC),
    _public(provider_id="cn_a_baostock_market", source_type="baostock", endpoint_id="baostock.dividend", official_path_or_api_name="query_dividend_data", required_params=("code",), http_visibility=HttpVisibility.SDK_INTERNAL_UNKNOWN, doc=BAOSTOCK_DOC),
    _public(provider_id="cn_a_baostock_market", source_type="baostock", endpoint_id="baostock.financial_metric", official_path_or_api_name="query_profit_data", required_params=("code", "year", "quarter"), http_visibility=HttpVisibility.SDK_INTERNAL_UNKNOWN, doc=BAOSTOCK_DOC),
    _public(provider_id="cn_a_baostock_market", source_type="baostock", endpoint_id="baostock.financial_statement", official_path_or_api_name="query_balance_data", required_params=("code", "year", "quarter"), http_visibility=HttpVisibility.SDK_INTERNAL_UNKNOWN, doc=BAOSTOCK_DOC),
    _public(provider_id="cn_a_baostock_market", source_type="baostock", endpoint_id="baostock.trade_calendar", official_path_or_api_name="query_trade_dates", optional_params=("start_date", "end_date"), http_visibility=HttpVisibility.SDK_INTERNAL_UNKNOWN, doc=BAOSTOCK_DOC),
    _public(provider_id="cn_a_baostock_market", source_type="baostock", endpoint_id="baostock.stock_industry", official_path_or_api_name="query_stock_industry", required_params=("code",), http_visibility=HttpVisibility.SDK_INTERNAL_UNKNOWN, doc=BAOSTOCK_DOC),

    _public(provider_id="cn_a_cninfo_events", source_type="cninfo", endpoint_id="cninfo.official_filing", official_path_or_api_name="/new/hisAnnouncement/query", required_params=("stock",), optional_params=("pageNum", "pageSize", "column", "plate", "category"), doc="https://www.cninfo.com.cn/new/index"),
    _public(provider_id="cn_a_cninfo_events", source_type="cninfo", endpoint_id="cninfo.event_calendar", official_path_or_api_name="/new/disclosure", optional_params=("stock", "date"), doc="https://www.cninfo.com.cn/new/index"),

    _public(provider_id="us_yahoo_finance", source_type="yahoo_finance", endpoint_id="yahoo_finance.chart_daily", official_path_or_api_name="/v8/finance/chart/{symbol}", required_params=("symbol",), optional_params=("range", "interval", "period1", "period2", "events"), doc=YAHOO_DOC),
    _public(provider_id="us_yahoo_finance", source_type="yahoo_finance", endpoint_id="yahoo_finance.quote_snapshot", official_path_or_api_name="/v8/finance/chart/{symbol}", required_params=("symbol",), optional_params=("range", "interval"), doc=YAHOO_DOC),
    _public(provider_id="us_yahoo_finance", source_type="yahoo_finance", endpoint_id="yahoo_finance.fundamentals_timeseries", official_path_or_api_name="/ws/fundamentals-timeseries/v1/finance/timeseries/{symbol}", required_params=("symbol", "type"), doc=YAHOO_DOC),
    _public(provider_id="hk_yahoo_finance", source_type="yahoo_finance", endpoint_id="yahoo_finance.hk_chart_daily", official_path_or_api_name="/v8/finance/chart/{symbol}", required_params=("symbol",), optional_params=("range", "interval"), doc=YAHOO_DOC, adapter_endpoint_id="chart_daily"),
    _public(provider_id="hk_yahoo_finance", source_type="yahoo_finance", endpoint_id="yahoo_finance.hk_quote_summary_financial", official_path_or_api_name="/ws/fundamentals-timeseries/v1/finance/timeseries/{symbol}", required_params=("symbol", "type"), doc=YAHOO_DOC, adapter_endpoint_id="quote_summary_financial"),
    _public(provider_id="hk_yahoo_finance", source_type="yahoo_finance", endpoint_id="yahoo_finance.hk_quote_summary_valuation", official_path_or_api_name="/ws/fundamentals-timeseries/v1/finance/timeseries/{symbol}", required_params=("symbol", "type"), doc=YAHOO_DOC, adapter_endpoint_id="quote_summary_valuation"),

    _public(provider_id="crypto_binance_spot_market", source_type="binance", endpoint_id="binance.ticker_24hr", official_path_or_api_name="/api/v3/ticker/24hr", optional_params=("symbol",), doc=BINANCE_DOC),
    _public(provider_id="crypto_binance_spot_market", source_type="binance", endpoint_id="binance.order_book_depth", official_path_or_api_name="/api/v3/depth", required_params=("symbol",), optional_params=("limit",), doc=BINANCE_DOC),
    _public(provider_id="crypto_binance_spot_market", source_type="binance", endpoint_id="binance.spot_intraday_bar", official_path_or_api_name="/api/v3/klines", required_params=("symbol", "interval"), optional_params=("startTime", "endTime", "limit"), doc=BINANCE_DOC),
    _public(provider_id="crypto_primary", source_type="binance", endpoint_id="binance.spot_daily_bar", official_path_or_api_name="/api/v3/klines", required_params=("symbol", "interval"), optional_params=("startTime", "endTime", "limit"), doc=BINANCE_DOC),
    _public(provider_id="crypto_binance_spot_market", source_type="binance", endpoint_id="binance.futures_funding_rate", official_path_or_api_name="/fapi/v1/fundingRate", required_params=("symbol",), optional_params=("startTime", "endTime", "limit"), doc=BINANCE_FUTURES_DOC, adapter_endpoint_id="futures_funding_rate"),
    _public(provider_id="crypto_binance_spot_market", source_type="binance", endpoint_id="binance.futures_open_interest", official_path_or_api_name="/fapi/v1/openInterest", required_params=("symbol",), doc=BINANCE_FUTURES_DOC, adapter_endpoint_id="futures_open_interest"),
    _public(provider_id="crypto_binance_spot_market", source_type="binance", endpoint_id="binance.futures_open_interest_hist", official_path_or_api_name="/futures/data/openInterestHist", required_params=("symbol",), optional_params=("period", "startTime", "endTime", "limit"), doc=BINANCE_FUTURES_DOC, adapter_endpoint_id="futures_open_interest_hist"),
    _public(provider_id="crypto_binance_spot_market", source_type="binance", endpoint_id="binance.futures_long_short_ratio", official_path_or_api_name="/futures/data/globalLongShortAccountRatio", required_params=("symbol",), optional_params=("period", "startTime", "endTime", "limit"), doc=BINANCE_FUTURES_DOC, adapter_endpoint_id="futures_long_short_ratio"),
    _public(provider_id="crypto_binance_spot_market", source_type="binance", endpoint_id="binance.futures_top_long_short_account_ratio", official_path_or_api_name="/futures/data/topLongShortAccountRatio", required_params=("symbol",), optional_params=("period", "startTime", "endTime", "limit"), doc=BINANCE_FUTURES_DOC, adapter_endpoint_id="futures_top_long_short_account_ratio"),
    _public(provider_id="crypto_binance_spot_market", source_type="binance", endpoint_id="binance.futures_top_long_short_position_ratio", official_path_or_api_name="/futures/data/topLongShortPositionRatio", required_params=("symbol",), optional_params=("period", "startTime", "endTime", "limit"), doc=BINANCE_FUTURES_DOC, adapter_endpoint_id="futures_top_long_short_position_ratio"),
    _public(provider_id="crypto_binance_spot_market", source_type="binance", endpoint_id="binance.futures_taker_buy_sell", official_path_or_api_name="/futures/data/takerlongshortRatio", required_params=("symbol",), optional_params=("period", "startTime", "endTime", "limit"), doc=BINANCE_FUTURES_DOC, adapter_endpoint_id="futures_taker_buy_sell"),
    _public(provider_id="crypto_binance_spot_market", source_type="binance", endpoint_id="binance.options_open_interest", official_path_or_api_name="/eapi/v1/openInterest", optional_params=("underlyingAsset", "expiration"), doc=BINANCE_OPTIONS_DOC, adapter_endpoint_id="options_open_interest"),
    _public(provider_id="crypto_binance_spot_market", source_type="binance", endpoint_id="binance.options_ticker", official_path_or_api_name="/eapi/v1/ticker", optional_params=("symbol",), doc=BINANCE_OPTIONS_DOC, adapter_endpoint_id="options_ticker"),
    _public(provider_id="crypto_coingecko_market", source_type="coingecko", endpoint_id="coingecko.coins_markets", official_path_or_api_name="/coins/markets", optional_params=("ids", "vs_currency"), doc=COINGECKO_DOC, adapter_endpoint_id="coins_markets"),
    _public(provider_id="crypto_coingecko_market", source_type="coingecko", endpoint_id="coingecko.coins_id", official_path_or_api_name="/coins/{id}", optional_params=("id",), doc=COINGECKO_DOC, adapter_endpoint_id="coins_id"),

    _public(provider_id="us_sec_official", source_type="sec", endpoint_id="sec.submissions", official_path_or_api_name="/submissions/CIK{cik}.json", required_params=("cik",), doc="https://www.sec.gov/search-filings/edgar-application-programming-interfaces"),
    _public(provider_id="us_sec_official", source_type="sec", endpoint_id="sec.companyfacts", official_path_or_api_name="/api/xbrl/companyfacts/CIK{cik}.json", required_params=("cik",), doc="https://www.sec.gov/search-filings/edgar-application-programming-interfaces"),
    _public(provider_id="us_stocktwits_social", source_type="stocktwits", endpoint_id="stocktwits.symbol_stream", official_path_or_api_name="/api/2/streams/symbol/{symbol}.json", required_params=("symbol",), doc="https://api.stocktwits.com/developers/docs", adapter_endpoint_id="symbol_stream"),
    _public(provider_id="hk_sina_public", source_type="sina_hk", endpoint_id="sina_hk.stock_hk_daily", official_path_or_api_name="/stock/hkstock/{symbol}/klc2_kl.js", required_params=("symbol",), doc="https://finance.sina.com.cn", adapter_endpoint_id="stock_hk_daily"),
    _public(provider_id="hk_hkexnews_official", source_type="hkexnews", endpoint_id="hkexnews.regulatory_announcements", official_path_or_api_name="/Services/RSS-Feeds/regulatory-announcements", doc="https://www.hkexnews.hk", adapter_endpoint_id="regulatory_announcements"),
    _public(provider_id="hk_hkexnews_official", source_type="hkexnews", endpoint_id="hkexnews.regulatory_calendar", official_path_or_api_name="/Services/RSS-Feeds/regulatory-announcements", doc="https://www.hkexnews.hk", adapter_endpoint_id="regulatory_calendar"),
    _public(provider_id="crypto_defillama_defi", source_type="defillama", endpoint_id="defillama.protocol", official_path_or_api_name="/protocol/{slug}", required_params=("slug",), doc="https://defillama.com/docs/api"),
    _public(provider_id="crypto_alternative_me_sentiment", source_type="alternative_me", endpoint_id="alternative_me.fear_greed", official_path_or_api_name="/fng/", optional_params=("limit", "format"), doc="https://alternative.me/crypto/fear-and-greed-index/"),
    _public(provider_id="crypto_project_official_news", source_type="github", endpoint_id="github.releases", official_path_or_api_name="/repos/{repo}/releases", required_params=("repo",), doc="https://docs.github.com/en/rest/releases/releases"),
    _public(provider_id="cn_a_google_news", source_type="google_news", endpoint_id="google_news.cn_a_company_news", official_path_or_api_name="/rss/search", required_params=("q",), doc=GOOGLE_NEWS_DOC, parser_status="raw_only", adapter_endpoint_id="company_news"),
    _public(provider_id="cn_a_google_news", source_type="google_news", endpoint_id="google_news.cn_a_macro_news", official_path_or_api_name="/rss/search", required_params=("q",), doc=GOOGLE_NEWS_DOC, parser_status="raw_only", adapter_endpoint_id="macro_news"),
    _public(provider_id="us_google_news", source_type="google_news", endpoint_id="google_news.us_company_news", official_path_or_api_name="/rss/search", required_params=("q",), doc=GOOGLE_NEWS_DOC, parser_status="raw_only", adapter_endpoint_id="company_news"),
    _public(provider_id="us_google_news", source_type="google_news", endpoint_id="google_news.us_macro_news", official_path_or_api_name="/rss/search", required_params=("q",), doc=GOOGLE_NEWS_DOC, parser_status="raw_only", adapter_endpoint_id="macro_news"),
    _public(provider_id="hk_google_news", source_type="google_news", endpoint_id="google_news.hk_company_news", official_path_or_api_name="/rss/search", required_params=("q",), doc=GOOGLE_NEWS_DOC, parser_status="raw_only", adapter_endpoint_id="company_news"),
    _public(provider_id="hk_google_news", source_type="google_news", endpoint_id="google_news.hk_macro_news", official_path_or_api_name="/rss/search", required_params=("q",), doc=GOOGLE_NEWS_DOC, parser_status="raw_only", adapter_endpoint_id="macro_news"),
    _public(provider_id="hk_google_news", source_type="google_news", endpoint_id="google_news.hk_social_signal_news_heat", official_path_or_api_name="/rss/search", required_params=("q",), doc=GOOGLE_NEWS_DOC, parser_status="raw_only", adapter_endpoint_id="social_signal_news_heat"),
    _public(provider_id="crypto_google_news", source_type="google_news", endpoint_id="google_news.crypto_company_news", official_path_or_api_name="/rss/search", required_params=("q",), doc=GOOGLE_NEWS_DOC, parser_status="raw_only", adapter_endpoint_id="company_news"),
    _public(provider_id="crypto_google_news", source_type="google_news", endpoint_id="google_news.crypto_macro_news", official_path_or_api_name="/rss/search", required_params=("q",), doc=GOOGLE_NEWS_DOC, parser_status="raw_only", adapter_endpoint_id="macro_news"),
    _public(provider_id="crypto_google_news", source_type="google_news", endpoint_id="google_news.crypto_event_calendar", official_path_or_api_name="/rss/search", required_params=("q",), doc=GOOGLE_NEWS_DOC, parser_status="raw_only", adapter_endpoint_id="event_calendar"),
    _public(provider_id="cn_a_astock_signal_social", source_type="ths", endpoint_id="ths.hot_reason", official_path_or_api_name="/stock_hot_reason", optional_params=("symbol",), doc="http://zx.10jqka.com.cn", adapter_endpoint_id="ths_hot_reason"),
    _public(provider_id="cn_a_astock_signal_social", source_type="ths", endpoint_id="ths.northbound_flow", official_path_or_api_name="/market/hsgtApi/method/dayChart/", doc="https://data.hexin.cn", adapter_endpoint_id="northbound_flow"),
    _public(provider_id="cn_a_astock_signal_social", source_type="baidu", endpoint_id="baidu.concept_blocks", official_path_or_api_name="/api/finance/concept", optional_params=("symbol",), doc="https://finance.pae.baidu.com", adapter_endpoint_id="baidu_concept_blocks"),
)
