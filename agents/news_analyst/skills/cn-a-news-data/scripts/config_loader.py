from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

import yaml

from models import KeywordRule
from security import sanitize_error

DEFAULT_ENABLED_PROVIDERS: tuple[str, ...] = (
    "akshare.stock_news_em",
    "akshare.stock_info_global_cls",
    "akshare.stock_info_global_em",
    "akshare.news_cctv",
    "tushare.anns_d",
)
DEFAULT_KEYWORD_RULES_PATH = Path("agents/news_analyst/skills/cn-a-news-data/config/keyword_categories.yaml")
DEFAULT_ALIAS_CONFLICT_BLACKLIST_PATH = Path("agents/news_analyst/skills/cn-a-news-data/config/alias_rules.yaml")
SCHEMA_ERROR_SUMMARY_LIMIT = 512
SCHEMA_CHANGED_EMPTY_REASON = "schema_changed"
FAILED_QUALITY_STATUS = "failed"
APPROVED_KEYWORD_CATEGORIES: tuple[str, ...] = (
    "财报",
    "公告",
    "分红",
    "经营",
    "渠道",
    "价格",
    "产能",
    "政策",
    "风险",
    "行业竞争",
    "宏观消费",
)


class ConfigLoadError(ValueError):
    """Raised when CN_A news environment config is invalid."""


@dataclass(frozen=True)
class CNAnewsConfig:
    enabled_providers: tuple[str, ...]
    tushare_token: str | None
    timeout_seconds: int
    total_timeout_seconds: int
    max_concurrency: int
    max_json_items: int
    max_brief_items: int
    title_similarity_threshold: float
    keyword_rules_path: Path
    alias_conflict_blacklist_path: Path


@dataclass(frozen=True)
class AliasRule:
    ticker: str
    company_name: str
    approved_aliases: list[str]
    approved_historical_names: list[str]


@dataclass(frozen=True)
class AliasConflict:
    alias: str
    blocked_terms: list[str]


@dataclass(frozen=True)
class AliasRulesConfig:
    version: str
    aliases: list[AliasRule]
    conflicts: list[AliasConflict]


def load_cn_a_news_config(env: Mapping[str, str] | None = None) -> CNAnewsConfig:
    source = os.environ if env is None else env

    return CNAnewsConfig(
        enabled_providers=_parse_enabled_providers(source),
        tushare_token=_parse_optional_secret(source, "CN_A_NEWS_TUSHARE_TOKEN")
        or _parse_optional_secret(source, "TUSHARE_TOKEN"),
        timeout_seconds=_parse_positive_int(source, "CN_A_NEWS_TIMEOUT_SECONDS", default=10),
        total_timeout_seconds=_parse_positive_int(source, "CN_A_NEWS_TOTAL_TIMEOUT_SECONDS", default=20),
        max_concurrency=_parse_positive_int(source, "CN_A_NEWS_MAX_CONCURRENCY", default=3),
        max_json_items=_parse_positive_int(source, "CN_A_NEWS_MAX_JSON_ITEMS", default=100),
        max_brief_items=_parse_positive_int(source, "CN_A_NEWS_MAX_BRIEF_ITEMS", default=20),
        title_similarity_threshold=_parse_float(source, "CN_A_NEWS_TITLE_SIMILARITY_THRESHOLD", default=0.92),
        keyword_rules_path=_parse_path(
            source,
            "CN_A_NEWS_KEYWORD_RULES_PATH",
            default=DEFAULT_KEYWORD_RULES_PATH,
        ),
        alias_conflict_blacklist_path=_parse_path(
            source,
            "CN_A_NEWS_ALIAS_CONFLICT_BLACKLIST_PATH",
            default=DEFAULT_ALIAS_CONFLICT_BLACKLIST_PATH,
        ),
    )


def summarize_cn_a_news_config(config: CNAnewsConfig) -> dict[str, object]:
    return {
        "enabled_providers": list(config.enabled_providers),
        "timeout_seconds": config.timeout_seconds,
        "total_timeout_seconds": config.total_timeout_seconds,
        "max_concurrency": config.max_concurrency,
        "max_json_items": config.max_json_items,
        "max_brief_items": config.max_brief_items,
        "title_similarity_threshold": config.title_similarity_threshold,
        "keyword_rules_path": str(config.keyword_rules_path),
        "alias_conflict_blacklist_path": str(config.alias_conflict_blacklist_path),
        "tushare_token_configured": bool(config.tushare_token),
    }


def load_keyword_rules(path: Path) -> list[KeywordRule]:
    document = _load_yaml_document(path)
    root = _require_mapping(document, "keyword_categories.yaml 顶层")
    _require_non_empty_string(root, "version", "keyword_categories.yaml.version")
    rules = _require_list(root, "rules", "keyword_categories.yaml.rules")

    parsed_rules: list[KeywordRule] = []
    for index, item in enumerate(rules):
        field_prefix = f"keyword_categories.yaml.rules[{index}]"
        rule_payload = _require_mapping(item, field_prefix)

        term = _require_non_empty_string(rule_payload, "term", f"{field_prefix}.term")
        category = _require_non_empty_string(rule_payload, "category", f"{field_prefix}.category")
        if category not in APPROVED_KEYWORD_CATEGORIES:
            approved = " / ".join(APPROVED_KEYWORD_CATEGORIES)
            raise _schema_error(f"{field_prefix}.category 不在批准集合内: {category}；允许值: {approved}")

        markets = _require_non_empty_string_list(rule_payload, "markets", f"{field_prefix}.markets")
        if "CN_A" not in markets:
            raise _schema_error(f"{field_prefix}.markets 必须包含 CN_A")

        enabled = rule_payload.get("enabled", True)
        if not isinstance(enabled, bool):
            raise _schema_error(f"{field_prefix}.enabled 必须是 bool")
        if not enabled:
            continue

        parsed_rules.append(KeywordRule(term=term, category=category, markets=markets))

    return parsed_rules


def load_alias_rules(path: Path) -> AliasRulesConfig:
    document = _load_yaml_document(path)
    root = _require_mapping(document, "alias_rules.yaml 顶层")
    version = _require_non_empty_string(root, "version", "alias_rules.yaml.version")
    alias_items = _require_list(root, "aliases", "alias_rules.yaml.aliases")
    conflict_items = _require_list(root, "conflicts", "alias_rules.yaml.conflicts")

    aliases: list[AliasRule] = []
    for index, item in enumerate(alias_items):
        field_prefix = f"alias_rules.yaml.aliases[{index}]"
        alias_payload = _require_mapping(item, field_prefix)
        aliases.append(
            AliasRule(
                ticker=_require_non_empty_string(alias_payload, "ticker", f"{field_prefix}.ticker"),
                company_name=_require_non_empty_string(
                    alias_payload, "company_name", f"{field_prefix}.company_name"
                ),
                approved_aliases=_require_non_empty_string_list(
                    alias_payload,
                    "approved_aliases",
                    f"{field_prefix}.approved_aliases",
                    allow_empty=True,
                ),
                approved_historical_names=_require_non_empty_string_list(
                    alias_payload,
                    "approved_historical_names",
                    f"{field_prefix}.approved_historical_names",
                    allow_empty=True,
                ),
            )
        )

    conflicts: list[AliasConflict] = []
    for index, item in enumerate(conflict_items):
        field_prefix = f"alias_rules.yaml.conflicts[{index}]"
        conflict_payload = _require_mapping(item, field_prefix)
        conflicts.append(
            AliasConflict(
                alias=_require_non_empty_string(conflict_payload, "alias", f"{field_prefix}.alias"),
                blocked_terms=_require_non_empty_string_list(
                    conflict_payload,
                    "blocked_terms",
                    f"{field_prefix}.blocked_terms",
                    allow_empty=True,
                ),
            )
        )

    return AliasRulesConfig(version=version, aliases=aliases, conflicts=conflicts)


def load_alias_conflict_blacklist(path: Path) -> list[AliasConflict]:
    return load_alias_rules(path).conflicts


def build_schema_changed_failure_payload(error: Exception) -> dict[str, object]:
    return {
        "ok": False,
        "quality": {"status": FAILED_QUALITY_STATUS},
        "empty_reason": SCHEMA_CHANGED_EMPTY_REASON,
        "error": {
            "summary": _truncate_error_summary(sanitize_error(error)),
        },
    }


def _parse_enabled_providers(env: Mapping[str, str]) -> tuple[str, ...]:
    raw_value = _get_raw_value(env, "CN_A_NEWS_ENABLED_PROVIDERS")
    if raw_value is None:
        return DEFAULT_ENABLED_PROVIDERS

    providers = tuple(item.strip() for item in raw_value.split(",") if item.strip())
    if not providers:
        raise ConfigLoadError("CN_A_NEWS_ENABLED_PROVIDERS 需要至少一个 provider endpoint")
    return providers


def _parse_optional_secret(env: Mapping[str, str], key: str) -> str | None:
    raw_value = _get_raw_value(env, key)
    if raw_value is None:
        return None
    if not raw_value.strip():
        return None
    return raw_value


def _parse_positive_int(env: Mapping[str, str], key: str, default: int) -> int:
    raw_value = _get_raw_value(env, key)
    if raw_value is None:
        return default

    try:
        parsed = int(raw_value)
    except ValueError as exc:
        raise ConfigLoadError(f"{key} 需要整数") from exc

    if parsed <= 0:
        raise ConfigLoadError(f"{key} 需要正整数")
    return parsed


def _parse_float(env: Mapping[str, str], key: str, default: float) -> float:
    raw_value = _get_raw_value(env, key)
    if raw_value is None:
        return default

    try:
        return float(raw_value)
    except ValueError as exc:
        raise ConfigLoadError(f"{key} 需要浮点数") from exc


def _parse_path(env: Mapping[str, str], key: str, default: Path) -> Path:
    raw_value = _get_raw_value(env, key)
    if raw_value is None:
        return default

    if not raw_value:
        raise ConfigLoadError(f"{key} 不能为空")
    return Path(raw_value)


def _get_raw_value(env: Mapping[str, str], key: str) -> str | None:
    value = env.get(key)
    if value is None:
        return None
    return value.strip()


def _load_yaml_document(path: Path) -> object:
    try:
        with path.open("r", encoding="utf-8") as handle:
            return yaml.safe_load(handle)
    except OSError as exc:
        raise _schema_error(f"{path}: 配置读取失败: {sanitize_error(exc)}") from exc
    except yaml.YAMLError as exc:
        raise _schema_error(f"{path}: YAML 语法错误: {sanitize_error(exc)}") from exc


def _require_mapping(value: object, field_name: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise _schema_error(f"{field_name} 必须是 mapping")
    return value


def _require_list(source: dict[str, object], key: str, field_name: str) -> list[object]:
    if key not in source:
        raise _schema_error(f"{field_name} 缺失")
    value = source[key]
    if not isinstance(value, list):
        raise _schema_error(f"{field_name} 必须是 list")
    return value


def _require_non_empty_string(source: dict[str, object], key: str, field_name: str) -> str:
    if key not in source:
        raise _schema_error(f"{field_name} 缺失")
    value = source[key]
    if not isinstance(value, str) or not value.strip():
        raise _schema_error(f"{field_name} 必须是非空字符串")
    return value.strip()


def _require_non_empty_string_list(
    source: dict[str, object],
    key: str,
    field_name: str,
    allow_empty: bool = False,
) -> list[str]:
    if key not in source:
        raise _schema_error(f"{field_name} 缺失")
    raw_values = source[key]
    if not isinstance(raw_values, list):
        raise _schema_error(f"{field_name} 必须是字符串列表")

    parsed: list[str] = []
    for index, raw_value in enumerate(raw_values):
        if not isinstance(raw_value, str) or not raw_value.strip():
            raise _schema_error(f"{field_name}[{index}] 必须是非空字符串")
        parsed.append(raw_value.strip())

    if not parsed and not allow_empty:
        raise _schema_error(f"{field_name} 不能为空")
    return parsed


def _schema_error(message: str) -> ConfigLoadError:
    return ConfigLoadError(_truncate_error_summary(message))


def _truncate_error_summary(message: str) -> str:
    if len(message) <= SCHEMA_ERROR_SUMMARY_LIMIT:
        return message
    return message[:SCHEMA_ERROR_SUMMARY_LIMIT]
