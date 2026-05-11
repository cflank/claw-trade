from __future__ import annotations

import re
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_PYTHON_ROOT = REPO_ROOT / "openclaw_plugins" / "claw-trade-frontline-tools" / "python"
if str(SHARED_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(SHARED_PYTHON_ROOT))

from frontline_data_pack.config import load_frontline_provider_config  # noqa: E402
from frontline_data_pack.provider_plan import (  # noqa: E402
    build_provider_plan,
    build_provider_query_from_tool_params,
    build_query_fingerprint,
    materialize_provider_query_parameters,
    ticker_to_eastmoney_symbol,
    ticker_to_eastmoney_secid,
)


def _base_env() -> dict[str, str]:
    return {
        "CN_A_MONGODB_URI": "mongodb://localhost:27017/claw_trade",
        "CLAW_TRADE_OPENVIKING_BASE_URI": "https://openviking.internal",
        "CLAW_TRADE_OPENVIKING_AUTH_MODE": "none",
    }


def test_t_pvd_002_query_fingerprint_is_stable_across_key_order() -> None:
    left = {
        "ticker": "600519.SH",
        "market": "CN_A",
        "start_date": "2026-01-01",
        "end_date": "2026-02-01",
        "company_name": "贵州茅台",
        "industry": "白酒",
        "adjust": "qfq",
    }
    right = {
        "adjust": "qfq",
        "industry": "白酒",
        "company_name": "贵州茅台",
        "end_date": "2026-02-01",
        "start_date": "2026-01-01",
        "market": "CN_A",
        "ticker": "600519.SH",
    }

    left_fingerprint = build_query_fingerprint(left)
    right_fingerprint = build_query_fingerprint(right)

    assert left_fingerprint == right_fingerprint
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", left_fingerprint) is not None


def test_t_pvd_002_eastmoney_secid_rule_for_sh() -> None:
    assert ticker_to_eastmoney_secid("600519.SH") == "1.600519"


def test_t_pvd_002_eastmoney_secid_rule_for_sz() -> None:
    assert ticker_to_eastmoney_secid("000001.SZ") == "0.000001"


def test_t_pvd_002_eastmoney_symbol_rule_for_sh() -> None:
    assert ticker_to_eastmoney_symbol("600519.SH") == "100.600519"


def test_t_pvd_002_tool_param_provider_noise_does_not_change_plan() -> None:
    config = load_frontline_provider_config(_base_env())
    base_params = {
        "ticker": "600519.SH",
        "market": "CN_A",
        "start_date": "2026-01-01",
        "end_date": "2026-02-01",
        "company_name": "贵州茅台",
        "industry": "白酒",
    }
    with_provider = dict(base_params)
    with_provider["provider"] = "akshare"

    query_a = build_provider_query_from_tool_params(base_params)
    query_b = build_provider_query_from_tool_params(with_provider)

    plan_a = build_provider_plan(domain="market", query=query_a, config=config, cache_inspection=[])
    plan_b = build_provider_plan(domain="market", query=query_b, config=config, cache_inspection=[])

    assert query_a == query_b
    assert plan_a == plan_b


def test_t_pvd_002_market_eastmoney_query_parameter_mapping_uses_secid() -> None:
    config = load_frontline_provider_config(_base_env())
    query = build_provider_query_from_tool_params(
        {
            "ticker": "600519.SH",
            "market": "CN_A",
            "start_date": "2026-01-01",
            "end_date": "2026-02-01",
        }
    )
    plan = build_provider_plan(domain="market", query=query, config=config, cache_inspection=[])
    eastmoney_spec = next(
        item for item in plan if item.provider == "eastmoney_direct" and item.endpoint == "push2his_kline"
    )

    by_name = {param.name: param.source for param in eastmoney_spec.query_parameters}
    resolved = materialize_provider_query_parameters(spec=eastmoney_spec, query=query, config=config)

    assert by_name["secid"] == "ticker_secid"
    assert resolved["secid"] == "1.600519"
    assert resolved["klt"] == 101
    assert resolved["fqt"] == 1
    assert resolved["beg"] == "20260101"
    assert resolved["end"] == "20260201"


def test_t_pvd_002_social_eastmoney_akshare_query_parameter_mapping_uses_eastmoney_symbol() -> None:
    config = load_frontline_provider_config(_base_env())
    query = build_provider_query_from_tool_params(
        {
            "ticker": "600519.SH",
            "market": "CN_A",
            "start_date": "2026-01-01",
            "end_date": "2026-02-01",
        }
    )
    plan = build_provider_plan(domain="social", query=query, config=config, cache_inspection=[])
    eastmoney_spec = next(
        item for item in plan if item.provider == "eastmoney_akshare" and item.endpoint == "hot_rank_latest"
    )

    by_name = {param.name: param.source for param in eastmoney_spec.query_parameters}
    resolved = materialize_provider_query_parameters(spec=eastmoney_spec, query=query, config=config)

    assert by_name["symbol"] == "eastmoney_symbol"
    assert resolved["symbol"] == "100.600519"
