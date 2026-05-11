from __future__ import annotations

import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from config_loader import (  # noqa: E402
    DEFAULT_ALIAS_CONFLICT_BLACKLIST_PATH,
    DEFAULT_ENABLED_PROVIDERS,
    DEFAULT_KEYWORD_RULES_PATH,
    ConfigLoadError,
    build_schema_changed_failure_payload,
    load_alias_conflict_blacklist,
    load_alias_rules,
    load_cn_a_news_config,
    load_keyword_rules,
    summarize_cn_a_news_config,
)

_MISSING = object()
_CN_A_NEWS_ENV_KEYS: tuple[str, ...] = (
    "CN_A_NEWS_ENABLED_PROVIDERS",
    "CN_A_NEWS_TUSHARE_TOKEN",
    "CN_A_NEWS_TIMEOUT_SECONDS",
    "CN_A_NEWS_TOTAL_TIMEOUT_SECONDS",
    "CN_A_NEWS_MAX_CONCURRENCY",
    "CN_A_NEWS_MAX_JSON_ITEMS",
    "CN_A_NEWS_MAX_BRIEF_ITEMS",
    "CN_A_NEWS_TITLE_SIMILARITY_THRESHOLD",
    "CN_A_NEWS_KEYWORD_RULES_PATH",
    "CN_A_NEWS_ALIAS_CONFLICT_BLACKLIST_PATH",
)


@contextmanager
def _temporary_env(overrides: dict[str, str | None]) -> Iterator[None]:
    original_values: dict[str, object] = {key: os.environ.get(key, _MISSING) for key in overrides}
    try:
        for key, value in overrides.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        for key, value in original_values.items():
            if value is _MISSING:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value  # type: ignore[assignment]


def _clear_cn_a_news_env(overrides: dict[str, str | None] | None = None) -> dict[str, str | None]:
    merged = {key: None for key in _CN_A_NEWS_ENV_KEYS}
    if overrides:
        merged.update(overrides)
    return merged


def test_load_cn_a_news_config_uses_dld_defaults_when_env_missing() -> None:
    with _temporary_env(_clear_cn_a_news_env()):
        config = load_cn_a_news_config()

    assert config.enabled_providers == DEFAULT_ENABLED_PROVIDERS
    assert config.timeout_seconds == 10
    assert config.total_timeout_seconds == 20
    assert config.max_concurrency == 3


def test_load_cn_a_news_config_reads_max_json_items_from_env() -> None:
    with _temporary_env(_clear_cn_a_news_env({"CN_A_NEWS_MAX_JSON_ITEMS": "80"})):
        config = load_cn_a_news_config()

    assert config.max_json_items == 80


def test_load_cn_a_news_config_raises_when_similarity_threshold_is_not_float() -> None:
    with _temporary_env(_clear_cn_a_news_env({"CN_A_NEWS_TITLE_SIMILARITY_THRESHOLD": "abc"})):
        with pytest.raises(ConfigLoadError, match=r"CN_A_NEWS_TITLE_SIMILARITY_THRESHOLD.*需要浮点数"):
            load_cn_a_news_config()


def test_summarize_cn_a_news_config_never_leaks_tushare_token_plaintext() -> None:
    token = "ts-secret-token-123"
    with _temporary_env(_clear_cn_a_news_env({"CN_A_NEWS_TUSHARE_TOKEN": token})):
        config = load_cn_a_news_config()

    summary = summarize_cn_a_news_config(config)
    serialized = json.dumps(summary, ensure_ascii=False)

    assert config.tushare_token == token
    assert summary["tushare_token_configured"] is True
    assert token not in serialized
    assert "tushare_token" not in summary
    assert summary["keyword_rules_path"] == str(DEFAULT_KEYWORD_RULES_PATH)
    assert summary["alias_conflict_blacklist_path"] == str(DEFAULT_ALIAS_CONFLICT_BLACKLIST_PATH)


def test_load_keyword_rules_only_returns_enabled_cn_a_rules() -> None:
    rules = load_keyword_rules(DEFAULT_KEYWORD_RULES_PATH)

    assert rules
    assert all("CN_A" in rule.markets for rule in rules)
    assert [rule.term for rule in rules] == ["业绩快报", "分红方案"]


def test_load_keyword_rules_rejects_unapproved_category_with_bounded_error(tmp_path: Path) -> None:
    invalid = """
version: "1"
rules:
  - term: "测试"
    category: "未批准分类未批准分类未批准分类未批准分类未批准分类未批准分类未批准分类未批准分类未批准分类未批准分类"
    markets: ["CN_A"]
    enabled: true
"""
    invalid_path = tmp_path / "keyword_invalid.yaml"
    invalid_path.write_text(invalid, encoding="utf-8")

    with pytest.raises(ConfigLoadError) as exc_info:
        load_keyword_rules(invalid_path)

    message = str(exc_info.value)
    assert "不在批准集合内" in message
    assert len(message) <= 512


def test_load_alias_conflict_blacklist_keeps_yaml_order() -> None:
    alias_rules = load_alias_rules(DEFAULT_ALIAS_CONFLICT_BLACKLIST_PATH)
    conflicts = load_alias_conflict_blacklist(DEFAULT_ALIAS_CONFLICT_BLACKLIST_PATH)

    assert alias_rules.version == "1"
    assert [conflict.alias for conflict in conflicts] == ["平安", "中兴"]


def test_keyword_yaml_parse_error_can_be_converted_to_failed_schema_payload(tmp_path: Path) -> None:
    bad_yaml_path = tmp_path / "keyword_bad_yaml.yaml"
    bad_yaml_path.write_text("version: [", encoding="utf-8")

    with pytest.raises(ConfigLoadError) as exc_info:
        load_keyword_rules(bad_yaml_path)

    payload = build_schema_changed_failure_payload(exc_info.value)
    assert payload["ok"] is False
    assert payload["quality"]["status"] == "failed"
    assert payload["empty_reason"] == "schema_changed"
    assert len(payload["error"]["summary"]) <= 512
