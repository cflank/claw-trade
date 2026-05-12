from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


SOCIAL_CONFIG_ROOT = Path("agents/social_analyst/skills/cn-a-social-data/config")
SOCIAL_SCRIPTS_ROOT = Path("agents/social_analyst/skills/cn-a-social-data/scripts")
SOCIAL_RULES_PATH = SOCIAL_SCRIPTS_ROOT / "rules.py"
ALIAS_RULES_PATH = SOCIAL_CONFIG_ROOT / "alias_rules.yaml"
KEYWORD_CATEGORIES_PATH = SOCIAL_CONFIG_ROOT / "keyword_categories.yaml"


def test_alias_rules_uses_only_approved_traceable_materials_for_alias_source() -> None:
    alias_rules = RULES_MODULE.load_alias_rules(ALIAS_RULES_PATH)

    rules = alias_rules.get("rules")
    assert isinstance(rules, dict)
    assert rules.get("approved_aliases_source") == "approved_traceable_alias_materials_only"


def test_keyword_categories_contains_attention_topic_and_forbidden_target_match() -> None:
    keyword_categories = RULES_MODULE.load_keyword_categories(KEYWORD_CATEGORIES_PATH)

    categories = keyword_categories.get("categories")
    assert isinstance(categories, dict)
    assert {"attention", "topic", "forbidden_as_target_match"} <= set(categories.keys())

    forbidden_terms = categories["forbidden_as_target_match"]
    assert isinstance(forbidden_terms, list)
    assert {"白酒", "消费", "国企改革"} & {str(item) for item in forbidden_terms}


@pytest.mark.parametrize(
    ("loader_name", "payload", "error_pattern"),
    [
        (
            "load_alias_rules",
            """
profile: CN_A
rules:
  approved_aliases_source: approved_traceable_alias_materials_only
  secret: should_fail
""",
            "禁用字段",
        ),
        (
            "load_alias_rules",
            """
profile: CN_A
rules:
  approved_aliases_source: approved_traceable_alias_materials_only
  raw_payload: should_fail
""",
            "raw_payload",
        ),
        (
            "load_keyword_categories",
            """
profile: CN_A
categories:
  attention: [人气]
  topic: [政策]
  forbidden_as_target_match: [白酒]
provider_payload: should_fail
""",
            "provider_payload",
        ),
        (
            "load_keyword_categories",
            """
profile: CN_A
categories:
  attention: [人气]
  topic: [政策]
  forbidden_as_target_match: [白酒]
worker_report: should_fail
""",
            "worker_report",
        ),
        (
            "load_keyword_categories",
            """
profile: CN_A
categories:
  attention: [人气]
  topic: [政策]
  forbidden_as_target_match: [白酒]
note: provider payload leaked text
""",
            "禁用内容片段",
        ),
    ],
)
def test_social_config_loader_rejects_sensitive_or_out_of_scope_fields(
    tmp_path: Path,
    loader_name: str,
    payload: str,
    error_pattern: str,
) -> None:
    config_path = tmp_path / "bad_config.yaml"
    config_path.write_text(payload.lstrip(), encoding="utf-8")

    loader = getattr(RULES_MODULE, loader_name)
    with pytest.raises(RULES_MODULE.SocialRulesConfigError, match=error_pattern):
        loader(config_path)


@pytest.mark.parametrize("loader_name", ["load_alias_rules", "load_keyword_categories"])
def test_social_config_loader_blocks_missing_cn_a_profile(tmp_path: Path, loader_name: str) -> None:
    config_path = tmp_path / "missing_profile.yaml"
    if loader_name == "load_alias_rules":
        payload = """
rules:
  approved_aliases_source: approved_traceable_alias_materials_only
"""
    else:
        payload = """
categories:
  attention: [人气]
  topic: [政策]
  forbidden_as_target_match: [白酒]
"""
    config_path.write_text(payload.lstrip(), encoding="utf-8")
    loader = getattr(RULES_MODULE, loader_name)
    with pytest.raises(RULES_MODULE.SocialRulesConfigError, match="profile=CN_A"):
        loader(config_path)


def _load_rules_module():
    spec = importlib.util.spec_from_file_location("cn_a_social_rules", SOCIAL_RULES_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载规则模块: {SOCIAL_RULES_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RULES_MODULE = _load_rules_module()
