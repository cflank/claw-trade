from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WorkspaceSources:
    worker_id: str
    worker_root: Path
    agents_md: Path
    identity_md: Path
    stages_yaml: Path
    skills_md: Path
    skills_manifest_yaml: Path


@dataclass(frozen=True)
class WorkspaceResult:
    ok: bool
    worker_id: str
    sources: WorkspaceSources
    reason: str | None
    missing_paths: tuple[Path, ...]


def worker_workspace_sources(agents_root: Path, worker_id: str) -> WorkspaceSources:
    worker_root = agents_root / worker_id
    return WorkspaceSources(
        worker_id=worker_id,
        worker_root=worker_root,
        agents_md=worker_root / "AGENTS.md",
        identity_md=worker_root / "IDENTITY.md",
        stages_yaml=worker_root / "STAGES.yaml",
        skills_md=worker_root / "SKILLS.md",
        skills_manifest_yaml=worker_root / "skills" / "manifest.yaml",
    )


def validate_worker_workspace_for_control(agents_root: Path, worker_id: str) -> WorkspaceResult:
    # 这里只校验人工资产存在性，避免 Python 读取/改写 worker 文案正文并越权做业务决策。
    sources = worker_workspace_sources(agents_root, worker_id)
    required_paths = (
        sources.agents_md,
        sources.identity_md,
        sources.stages_yaml,
        sources.skills_md,
        sources.skills_manifest_yaml,
    )
    missing_paths = tuple(path for path in required_paths if not path.is_file())
    if missing_paths:
        return WorkspaceResult(
            ok=False,
            worker_id=worker_id,
            sources=sources,
            reason="worker workspace required files missing",
            missing_paths=missing_paths,
        )
    return WorkspaceResult(
        ok=True,
        worker_id=worker_id,
        sources=sources,
        reason=None,
        missing_paths=(),
    )
