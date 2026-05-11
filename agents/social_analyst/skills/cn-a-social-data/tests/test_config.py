from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from config import (  # noqa: E402
    DEFAULT_CACHE_REQUIRED,
    DEFAULT_EVIDENCE_ROOT,
    DEFAULT_MAX_SIGNALS_PER_BUCKET,
    DEFAULT_MONGODB_CACHE_COLLECTION,
    DEFAULT_MONGODB_DATABASE,
    DEFAULT_PACK_TIMEOUT_SECONDS,
    DEFAULT_P1_HOT_UP_ENABLED,
    DEFAULT_P1_XUEQIU_ENABLED,
    DEFAULT_PROVIDER_MAX_CONCURRENCY,
    DEFAULT_PROVIDER_TIMEOUT_SECONDS,
    DEFAULT_TTL_HEAT_SECONDS,
    DEFAULT_TTL_KEYWORD_SECONDS,
    DEFAULT_TTL_RELATED_SECONDS,
    SOCIAL_CONFIG_INVALID,
    SOCIAL_CONFIG_SECRET_MISSING,
    SOCIAL_SCHEMA_VERSION,
    SocialConfigError,
    load_social_data_config,
)


def test_load_social_data_config_returns_dld_defaults_when_env_empty() -> None:
    config = load_social_data_config({})

    assert config.schema_version == SOCIAL_SCHEMA_VERSION
    assert config.provider_timeout_seconds == DEFAULT_PROVIDER_TIMEOUT_SECONDS
    assert config.pack_timeout_seconds == DEFAULT_PACK_TIMEOUT_SECONDS
    assert config.provider_max_concurrency == DEFAULT_PROVIDER_MAX_CONCURRENCY
    assert config.max_signals_per_bucket == DEFAULT_MAX_SIGNALS_PER_BUCKET
    assert config.mongodb_uri is None
    assert config.mongodb_database == DEFAULT_MONGODB_DATABASE
    assert config.mongodb_cache_collection == DEFAULT_MONGODB_CACHE_COLLECTION
    assert config.cache_required is DEFAULT_CACHE_REQUIRED
    assert config.cache_required is False
    assert config.p1_hot_up_enabled is DEFAULT_P1_HOT_UP_ENABLED
    assert config.p1_xueqiu_enabled is DEFAULT_P1_XUEQIU_ENABLED
    assert config.evidence_root == DEFAULT_EVIDENCE_ROOT
    assert config.openviking_l2_write_target_root is None
    assert config.ttl_by_endpoint == {
        "stock_hot_rank_latest_em": DEFAULT_TTL_HEAT_SECONDS,
        "stock_hot_rank_em": DEFAULT_TTL_HEAT_SECONDS,
        "stock_hot_up_em": DEFAULT_TTL_HEAT_SECONDS,
        "stock_hot_keyword_em": DEFAULT_TTL_KEYWORD_SECONDS,
        "stock_hot_rank_relate_em": DEFAULT_TTL_RELATED_SECONDS,
    }


def test_load_social_data_config_raises_when_provider_timeout_exceeds_range() -> None:
    with pytest.raises(SocialConfigError) as exc_info:
        load_social_data_config({"CN_A_SOCIAL_PROVIDER_TIMEOUT_SECONDS": "31"})

    assert exc_info.value.code == SOCIAL_CONFIG_INVALID
    assert "CN_A_SOCIAL_PROVIDER_TIMEOUT_SECONDS" in exc_info.value.message


def test_load_social_data_config_raises_when_cache_required_but_mongodb_uri_missing() -> None:
    with pytest.raises(SocialConfigError) as exc_info:
        load_social_data_config({"CN_A_SOCIAL_CACHE_REQUIRED": "true"})

    assert exc_info.value.code == SOCIAL_CONFIG_SECRET_MISSING
    assert "CN_A_SOCIAL_MONGODB_URI" in exc_info.value.message


def test_load_social_data_config_accepts_cache_required_when_mongodb_uri_present() -> None:
    config = load_social_data_config(
        {
            "CN_A_SOCIAL_CACHE_REQUIRED": "true",
            "CN_A_SOCIAL_MONGODB_URI": "mongodb://localhost:27017",
        }
    )

    assert config.cache_required is True
    assert config.mongodb_uri == "mongodb://localhost:27017"


def test_load_social_data_config_raises_when_p1_xueqiu_enabled_without_approved_mapping() -> None:
    with pytest.raises(SocialConfigError) as exc_info:
        load_social_data_config(
            {
                "CN_A_SOCIAL_MONGODB_URI": "mongodb://localhost:27017",
                "CN_A_SOCIAL_P1_XUEQIU_ENABLED": "true",
            }
        )

    assert exc_info.value.code == SOCIAL_CONFIG_INVALID
    assert "缺少批准 endpoint 与字段映射" in exc_info.value.message
