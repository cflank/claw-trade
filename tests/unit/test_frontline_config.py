from __future__ import annotations

import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_PYTHON_ROOT = REPO_ROOT / "openclaw_plugins" / "claw-trade-frontline-tools" / "python"
if str(SHARED_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(SHARED_PYTHON_ROOT))

from frontline_data_pack.config import (  # noqa: E402
    load_frontline_provider_config,
    summarize_frontline_provider_config,
)
from frontline_data_pack.errors import (  # noqa: E402
    CONFIG_INVALID,
    MONGO_CONFIG_INVALID,
    OPENVIKING_CONFIG_INVALID,
    FrontlineConfigError,
)


def _base_env() -> dict[str, str]:
    return {
        "CN_A_MONGODB_URI": "mongodb://localhost:27017/claw_trade",
        "CLAW_TRADE_OPENVIKING_BASE_URI": "https://openviking.internal",
        "CLAW_TRADE_OPENVIKING_AUTH_MODE": "none",
    }


def test_load_frontline_provider_config_reads_timeout_and_total_budget() -> None:
    env = _base_env()
    env["CN_A_PROVIDER_DEFAULT_TIMEOUT_MS"] = "10000"
    env["CN_A_PROVIDER_TOTAL_TIMEOUT_MS"] = "30000"

    config = load_frontline_provider_config(env)

    assert config.provider_runtime.default_timeout_ms == 10000
    assert config.provider_runtime.total_timeout_ms == 30000


def test_load_frontline_provider_config_rejects_out_of_range_concurrency() -> None:
    env = _base_env()
    env["CN_A_PROVIDER_MAX_CONCURRENCY"] = "7"

    with pytest.raises(FrontlineConfigError) as error:
        load_frontline_provider_config(env)

    assert error.value.code == CONFIG_INVALID
    assert "1 到 6" in error.value.message


def test_load_frontline_provider_config_requires_mongodb_database_in_uri_path() -> None:
    env = _base_env()
    env["CN_A_MONGODB_URI"] = "mongodb://host"

    with pytest.raises(FrontlineConfigError) as error:
        load_frontline_provider_config(env)

    assert error.value.code == MONGO_CONFIG_INVALID


def test_load_frontline_provider_config_rejects_bearer_without_token_without_secret_leak() -> None:
    env = _base_env()
    env["CLAW_TRADE_OPENVIKING_AUTH_MODE"] = "bearer"
    env.pop("CLAW_TRADE_OPENVIKING_TOKEN", None)

    with pytest.raises(FrontlineConfigError) as error:
        load_frontline_provider_config(env)

    assert error.value.code == OPENVIKING_CONFIG_INVALID
    assert "TOKEN" in error.value.message
    assert "secret_token_value" not in str(error.value)


def test_load_frontline_provider_config_allows_missing_news_search_keys_for_provider_attempts() -> None:
    env = _base_env()
    env["CN_A_NEWS_ENABLE_BOCHA"] = "true"
    env.pop("CN_A_NEWS_BOCHA_API_KEY", None)
    env["CN_A_NEWS_ENABLE_MINIMAX"] = "true"
    env.pop("CN_A_NEWS_MINIMAX_API_KEY", None)

    config = load_frontline_provider_config(env)
    summary = summarize_frontline_provider_config(config)

    assert config.provider_flags.news_enable_bocha is True
    assert config.provider_flags.news_bocha_api_key is None
    assert config.provider_flags.news_enable_minimax is True
    assert config.provider_flags.news_minimax_api_key is None
    assert config.provider_flags.news_minimax_base_url == "https://api.minimaxi.com/v1"
    assert config.provider_flags.news_minimax_model == "MiniMax-M2.7"
    provider_flags = summary["provider_flags"]
    assert isinstance(provider_flags, dict)
    assert provider_flags["news_bocha_api_key_configured"] is False
    assert provider_flags["news_minimax_api_key_configured"] is False
