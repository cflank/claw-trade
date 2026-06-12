from __future__ import annotations

from claw_trade.data_gateway.models import HttpVisibility

from .models import ParserStatus, endpoint, no_batch


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
    parser_status: ParserStatus = "normalized",
):
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
        request_template={"target": official_path_or_api_name},
    )


AKSHARE_DOC = "https://akshare.akfamily.xyz"
EASTMONEY_DOC = "https://quote.eastmoney.com/center/api.html"
MOOTDX_DOC = "https://github.com/mootdx/mootdx"
BAOSTOCK_DOC = "http://baostock.com/baostock/index.php/Python_API%E6%96%87%E6%A1%A3"
YAHOO_DOC = "https://query1.finance.yahoo.com"
BINANCE_DOC = "https://developers.binance.com/docs/binance-spot-api-docs/rest-api"
GOOGLE_NEWS_DOC = "https://news.google.com/rss"


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
    _public(provider_id="hk_yahoo_finance", source_type="yahoo_finance", endpoint_id="yahoo_finance.hk_chart_daily", official_path_or_api_name="/v8/finance/chart/{symbol}", required_params=("symbol",), optional_params=("range", "interval"), doc=YAHOO_DOC),
    _public(provider_id="hk_yahoo_finance", source_type="yahoo_finance", endpoint_id="yahoo_finance.hk_fundamentals_timeseries", official_path_or_api_name="/ws/fundamentals-timeseries/v1/finance/timeseries/{symbol}", required_params=("symbol", "type"), doc=YAHOO_DOC),

    _public(provider_id="crypto_binance_spot_market", source_type="binance", endpoint_id="binance.ticker_24hr", official_path_or_api_name="/api/v3/ticker/24hr", optional_params=("symbol",), doc=BINANCE_DOC),
    _public(provider_id="crypto_binance_spot_market", source_type="binance", endpoint_id="binance.order_book_depth", official_path_or_api_name="/api/v3/depth", required_params=("symbol",), optional_params=("limit",), doc=BINANCE_DOC),
    _public(provider_id="crypto_binance_spot_market", source_type="binance", endpoint_id="binance.spot_intraday_bar", official_path_or_api_name="/api/v3/klines", required_params=("symbol", "interval"), optional_params=("startTime", "endTime", "limit"), doc=BINANCE_DOC),
    _public(provider_id="crypto_primary", source_type="binance", endpoint_id="binance.spot_daily_bar", official_path_or_api_name="/api/v3/klines", required_params=("symbol", "interval"), optional_params=("startTime", "endTime", "limit"), doc=BINANCE_DOC),

    _public(provider_id="us_sec_official", source_type="sec", endpoint_id="sec.submissions", official_path_or_api_name="/submissions/CIK{cik}.json", required_params=("cik",), doc="https://www.sec.gov/search-filings/edgar-application-programming-interfaces"),
    _public(provider_id="us_sec_official", source_type="sec", endpoint_id="sec.companyfacts", official_path_or_api_name="/api/xbrl/companyfacts/CIK{cik}.json", required_params=("cik",), doc="https://www.sec.gov/search-filings/edgar-application-programming-interfaces"),
    _public(provider_id="us_stocktwits_social", source_type="stocktwits", endpoint_id="stocktwits.symbol_stream", official_path_or_api_name="/api/2/streams/symbol/{symbol}.json", required_params=("symbol",), doc="https://api.stocktwits.com/developers/docs"),
    _public(provider_id="hk_sina_public", source_type="sina_hk", endpoint_id="sina_hk.stock_hk_daily", official_path_or_api_name="/stock/hkstock/{symbol}/klc2_kl.js", required_params=("symbol",), doc="https://finance.sina.com.cn"),
    _public(provider_id="hk_hkexnews_official", source_type="hkexnews", endpoint_id="hkexnews.regulatory_announcements", official_path_or_api_name="/search/titlesearch.xhtml", optional_params=("stockId", "category", "from", "to"), doc="https://www.hkexnews.hk"),
    _public(provider_id="hk_hkexnews_official", source_type="hkexnews", endpoint_id="hkexnews.regulatory_calendar", official_path_or_api_name="/search/titlesearch.xhtml", optional_params=("stockId", "category", "from", "to"), doc="https://www.hkexnews.hk"),
    _public(provider_id="crypto_defillama_defi", source_type="defillama", endpoint_id="defillama.protocol", official_path_or_api_name="/protocol/{slug}", required_params=("slug",), doc="https://defillama.com/docs/api"),
    _public(provider_id="crypto_alternative_me_sentiment", source_type="alternative_me", endpoint_id="alternative_me.fear_greed", official_path_or_api_name="/fng/", optional_params=("limit", "format"), doc="https://alternative.me/crypto/fear-and-greed-index/"),
    _public(provider_id="crypto_project_official_news", source_type="github", endpoint_id="github.releases", official_path_or_api_name="/repos/{repo}/releases", required_params=("repo",), doc="https://docs.github.com/en/rest/releases/releases"),
    _public(provider_id="cn_a_google_news", source_type="google_news", endpoint_id="google_news.cn_a_company_news", official_path_or_api_name="/rss/search", required_params=("q",), doc=GOOGLE_NEWS_DOC, parser_status="raw_only"),
    _public(provider_id="us_google_news", source_type="google_news", endpoint_id="google_news.us_company_news", official_path_or_api_name="/rss/search", required_params=("q",), doc=GOOGLE_NEWS_DOC, parser_status="raw_only"),
    _public(provider_id="hk_google_news", source_type="google_news", endpoint_id="google_news.hk_company_news", official_path_or_api_name="/rss/search", required_params=("q",), doc=GOOGLE_NEWS_DOC, parser_status="raw_only"),
    _public(provider_id="crypto_google_news", source_type="google_news", endpoint_id="google_news.crypto_company_news", official_path_or_api_name="/rss/search", required_params=("q",), doc=GOOGLE_NEWS_DOC, parser_status="raw_only"),
    _public(provider_id="cn_a_astock_signal_social", source_type="ths", endpoint_id="ths.hot_reason", official_path_or_api_name="/stock_hot_reason", optional_params=("symbol",), doc="http://zx.10jqka.com.cn"),
    _public(provider_id="cn_a_astock_signal_social", source_type="baidu", endpoint_id="baidu.concept_blocks", official_path_or_api_name="/api/finance/concept", optional_params=("symbol",), doc="https://finance.pae.baidu.com"),
)
