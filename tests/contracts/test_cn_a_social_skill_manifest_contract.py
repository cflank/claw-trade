from __future__ import annotations

from pathlib import Path

import yaml

from claw_trade.config.stage_policy import load_stage_policy
from claw_trade.config.tool_names import load_tool_registry, resolve_tools


def test_cn_a_social_skill_metadata_contains_required_fields() -> None:
    skill_path = Path("agents/social_analyst/skills/cn-a-social-data/SKILL.md")
    text = skill_path.read_text(encoding="utf-8")

    assert "name: cn-a-social-data" in text
    assert "tool: claw_get_social_pack" in text
    assert "entrypoint: openclaw_plugins/claw-trade-frontline-tools/index.js" in text
    assert "schema_version: openbb_social_pack.v1" in text


def test_cn_a_social_analyst_visible_tools_are_exactly_approved_set() -> None:
    registry_result = load_tool_registry()
    assert registry_result.ok is True and registry_result.registry is not None
    registry = registry_result.registry

    policy_result = load_stage_policy(Path("agents"), "social_analyst", "CN_A")
    assert policy_result.ok is True and policy_result.policy is not None
    tools = resolve_tools(policy_result.policy, registry)

    assert set(tools) == {"claw_get_social_pack"}


def test_social_analyst_visible_tools_do_not_leak_provider_atomic_interfaces() -> None:
    registry_result = load_tool_registry()
    assert registry_result.ok is True and registry_result.registry is not None
    registry = registry_result.registry

    policy_result = load_stage_policy(Path("agents"), "social_analyst", "CN_A")
    assert policy_result.ok is True and policy_result.policy is not None
    tools = set(resolve_tools(policy_result.policy, registry))

    disallowed_provider_tools = {
        "stock_hot_rank_latest_em",
        "stock_hot_keyword_em",
        "stock_hot_rank_relate_em",
        "stock_hot_rank_em",
        "stock_hot_follow_xq",
        "stock_hot_tweet_xq",
        "stock_hot_deal_xq",
    }
    assert tools.isdisjoint(disallowed_provider_tools)


def test_cn_a_social_skill_only_binds_to_social_analyst_worker() -> None:
    bound_workers: list[tuple[str, tuple[str, ...]]] = []
    for manifest in sorted(Path("agents").glob("*/skills/manifest.yaml")):
        worker_id = manifest.parent.parent.name
        for entry in _load_skill_manifest_entries(manifest):
            if entry.get("path") != "cn-a-social-data/SKILL.md":
                continue
            workers = entry.get("workers")
            assert isinstance(workers, list)
            bound_workers.append((worker_id, tuple(workers)))

    assert bound_workers == [("social_analyst", ("social_analyst",))]


def _load_skill_manifest_entries(manifest_path: Path) -> list[dict[str, object]]:
    data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    skills = data.get("skills")
    assert isinstance(skills, list)
    assert all(isinstance(entry, dict) for entry in skills)
    return skills
