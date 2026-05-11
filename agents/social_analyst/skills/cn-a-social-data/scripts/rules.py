from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

CONFIG_ROOT = Path(__file__).resolve().parents[1] / "config"
ALIAS_RULES_PATH = CONFIG_ROOT / "alias_rules.yaml"
KEYWORD_CATEGORIES_PATH = CONFIG_ROOT / "keyword_categories.yaml"

_REQUIRED_PROFILE = "CN_A"
_REQUIRED_ALIAS_SOURCE = "approved_artifact_refs_only"
_REQUIRED_KEYWORD_CATEGORIES = frozenset({"attention", "topic", "forbidden_as_target_match"})
_INDUSTRY_OR_TOPIC_CLASS_TERMS = frozenset({"白酒", "消费", "国企改革"})

_BANNED_FIELD_TOKENS = (
    "provider_payload",
    "raw_payload",
    "provider_request",
    "provider_requests_raw",
    "worker_report",
    "report_body",
    "secret",
    "token",
    "cookie",
    "password",
    "api_key",
    "authorization",
)
_BANNED_TEXT_TOKENS = (
    "provider payload",
    "raw payload",
    "worker report",
    "provider_requests_raw",
)


class SocialRulesConfigError(ValueError):
    """CN_A social alias/keyword 规则配置错误。"""


def load_alias_rules(path: Path | None = None) -> dict[str, Any]:
    config_path = path or ALIAS_RULES_PATH
    data = _load_yaml_mapping(config_path)
    _validate_cn_a_profile(data, config_path)
    _scan_forbidden_tokens(data, config_path)

    rules = data.get("rules")
    if not isinstance(rules, dict):
        raise SocialRulesConfigError(f"{config_path} 缺少 rules 映射")
    if rules.get("approved_aliases_source") != _REQUIRED_ALIAS_SOURCE:
        raise SocialRulesConfigError(
            f"{config_path} 必须声明 approved_aliases_source={_REQUIRED_ALIAS_SOURCE}",
        )
    return data


def load_keyword_categories(path: Path | None = None) -> dict[str, Any]:
    config_path = path or KEYWORD_CATEGORIES_PATH
    data = _load_yaml_mapping(config_path)
    _validate_cn_a_profile(data, config_path)
    _scan_forbidden_tokens(data, config_path)

    categories = data.get("categories")
    if not isinstance(categories, dict):
        raise SocialRulesConfigError(f"{config_path} 缺少 categories 映射")

    missing = sorted(_REQUIRED_KEYWORD_CATEGORIES - set(categories.keys()))
    if missing:
        raise SocialRulesConfigError(
            f"{config_path} 缺少必要分类: {', '.join(missing)}",
        )

    forbidden_terms = categories.get("forbidden_as_target_match")
    if not isinstance(forbidden_terms, list):
        raise SocialRulesConfigError(f"{config_path} 的 forbidden_as_target_match 必须是列表")
    normalized_forbidden = {str(item).strip() for item in forbidden_terms if str(item).strip()}
    if not normalized_forbidden:
        raise SocialRulesConfigError(f"{config_path} 的 forbidden_as_target_match 不能为空")
    if not (normalized_forbidden & _INDUSTRY_OR_TOPIC_CLASS_TERMS):
        raise SocialRulesConfigError(
            f"{config_path} 的 forbidden_as_target_match 必须包含行业或主题词分类",
        )
    return data


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SocialRulesConfigError(f"{path} 顶层必须是映射")
    return payload


def _validate_cn_a_profile(data: dict[str, Any], path: Path) -> None:
    if data.get("profile") != _REQUIRED_PROFILE:
        raise SocialRulesConfigError(f"{path} 必须声明 profile={_REQUIRED_PROFILE}")


def _scan_forbidden_tokens(data: dict[str, Any], path: Path) -> None:
    for key in _collect_keys(data):
        normalized_key = key.lower()
        for token in _BANNED_FIELD_TOKENS:
            if token in normalized_key:
                raise SocialRulesConfigError(f"{path} 包含禁用字段: {key}")

    for text in _collect_texts(data):
        normalized_text = text.lower()
        for token in _BANNED_TEXT_TOKENS:
            if token in normalized_text:
                raise SocialRulesConfigError(f"{path} 包含禁用内容片段: {token}")


def _collect_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        keys: set[str] = set()
        for key, nested in value.items():
            keys.add(str(key))
            keys.update(_collect_keys(nested))
        return keys
    if isinstance(value, list):
        keys: set[str] = set()
        for item in value:
            keys.update(_collect_keys(item))
        return keys
    return set()


def _collect_texts(value: object) -> set[str]:
    if isinstance(value, dict):
        texts: set[str] = set()
        for nested in value.values():
            texts.update(_collect_texts(nested))
        return texts
    if isinstance(value, list):
        texts: set[str] = set()
        for item in value:
            texts.update(_collect_texts(item))
        return texts
    if isinstance(value, str):
        return {value}
    return set()
