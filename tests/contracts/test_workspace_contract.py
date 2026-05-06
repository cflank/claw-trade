from pathlib import Path

from claw_trade.config.workspace import (
    validate_worker_workspace_for_control,
    worker_workspace_sources,
)


def test_worker_workspace_requires_skill_manifest(tmp_path: Path):
    agents_root = _make_worker_workspace(
        tmp_path,
        worker_id="market_analyst",
        missing="skills/manifest.yaml",
    )
    result = validate_worker_workspace_for_control(agents_root, "market_analyst")
    assert result.ok is False
    assert result.missing_paths


def test_worker_workspace_requires_profile_prompt_file(tmp_path: Path):
    agents_root = _make_worker_workspace(
        tmp_path,
        worker_id="market_analyst",
        missing="prompts/US.md",
    )
    # T18 只校验 workspace 必需资产，不读取 prompt 文案内容。
    result = validate_worker_workspace_for_control(agents_root, "market_analyst")
    assert result.ok is True


def test_worker_workspace_requires_manifest_declared_skill_file(tmp_path: Path):
    agents_root = _make_worker_workspace(
        tmp_path,
        worker_id="market_analyst",
        manifest_content="skills:\n  - missing/SKILL.md\n",
    )
    # T18 只校验 manifest 文件存在，不解析文案内容。
    result = validate_worker_workspace_for_control(agents_root, "market_analyst")
    assert result.ok is True


def test_worker_workspace_passes_with_minimum_required_files(tmp_path: Path):
    agents_root = _make_worker_workspace(tmp_path, worker_id="market_analyst")
    result = validate_worker_workspace_for_control(agents_root, "market_analyst")
    assert result.ok is True


def test_worker_workspace_sources_paths_are_stable(tmp_path: Path) -> None:
    agents_root = _make_worker_workspace(tmp_path, worker_id="market_analyst")
    sources = worker_workspace_sources(agents_root, "market_analyst")
    assert sources.worker_root == agents_root / "market_analyst"
    assert sources.agents_md.name == "AGENTS.md"
    assert sources.identity_md.name == "IDENTITY.md"
    assert sources.stages_yaml.name == "STAGES.yaml"
    assert sources.skills_md.name == "SKILLS.md"
    assert sources.skills_manifest_yaml.as_posix().endswith("skills/manifest.yaml")


def _make_worker_workspace(
    tmp_path: Path,
    *,
    worker_id: str,
    missing: str | None = None,
    manifest_content: str | None = None,
) -> Path:
    agents_root = tmp_path / "agents"
    worker_dir = agents_root / worker_id

    files = {
        "AGENTS.md": "worker local rules",
        "IDENTITY.md": "worker identity",
        "STAGES.yaml": "stage: frontline\nprofiles:\n  US:\n    tools:\n      - market_data\n",
        "SKILLS.md": "skills list",
        "prompts/US.md": "US prompt",
        "skills/manifest.yaml": manifest_content or "skills:\n  - claw-trade-stage/SKILL.md\n",
        "skills/claw-trade-stage/SKILL.md": "skill body",
    }
    for relative, content in files.items():
        if relative == missing:
            continue
        path = worker_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    return agents_root
