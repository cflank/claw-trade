from __future__ import annotations

from pathlib import Path

import yaml


def test_cn_a_news_skill_metadata_contains_required_fields() -> None:
    skill_path = Path("agents/news_analyst/skills/cn-a-news-data/SKILL.md")
    text = skill_path.read_text(encoding="utf-8")

    assert "name: cn-a-news-data" in text
    assert "tool: claw_get_news_pack" in text
    assert "entrypoint: openclaw_plugins/claw-trade-frontline-tools/index.js" in text
    assert "schema_version: claw_data_news_pack.v1" in text


def test_cn_a_news_skill_only_binds_to_news_analyst_worker() -> None:
    bound_workers: list[tuple[str, tuple[str, ...]]] = []
    for manifest in sorted(Path("agents").glob("*/skills/manifest.yaml")):
        worker_id = manifest.parent.parent.name
        for entry in _load_skill_manifest_entries(manifest):
            if entry.get("path") != "cn-a-news-data/SKILL.md":
                continue
            workers = entry.get("workers")
            assert isinstance(workers, list)
            bound_workers.append((worker_id, tuple(workers)))

    assert bound_workers == [("news_analyst", ("news_analyst",))]


def test_cn_a_news_skill_manifest_exports_only_news_data_pack() -> None:
    manifest = Path("agents/news_analyst/skills/manifest.yaml")
    entries = _load_skill_manifest_entries(manifest)

    target = [entry for entry in entries if entry.get("path") == "cn-a-news-data/SKILL.md"]
    assert len(target) == 1
    entry = target[0]
    assert entry.get("tool_exports") == ["claw_get_news_pack"]


def _load_skill_manifest_entries(manifest_path: Path) -> list[dict[str, object]]:
    data = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    skills = data.get("skills")
    assert isinstance(skills, list)
    assert all(isinstance(entry, dict) for entry in skills)
    return skills
