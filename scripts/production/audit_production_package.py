#!/usr/bin/env python3
from __future__ import annotations

import argparse
import io
import tarfile
from dataclasses import dataclass
from pathlib import Path


REQUIRED_RELEASE_PATHS = {
    "bin/claw-trade-ui",
    "runtime/bin/claw-trade-control-runtime",
    "runtime/openclaw/openclaw.mjs",
    "runtime/openclaw/node_modules/dotenv",
    "runtime/openclaw/node_modules/global-agent",
    "runtime/openclaw/node_modules/json5",
    "runtime/openclaw/node_modules/supports-color",
    "runtime/assets/agents.tar",
    "runtime/assets/openclaw_plugins.tar",
    "web/dist/index.html",
}

REQUIRED_AGENT_ASSET_PATHS = {
    "agents/market_analyst/AGENTS.md",
    "agents/market_analyst/STAGES.yaml",
    "agents/market_analyst/skills/manifest.yaml",
    "agents/portfolio_manager/prompts/CN_A.md",
}

REQUIRED_PLUGIN_ASSET_PATHS = {
    "openclaw_plugins/claw-trade-frontline-tools/index.js",
    "openclaw_plugins/claw-trade-selection-tools/index.js",
    "openclaw_plugins/claw-trade-scheduled-work-tools/index.js",
}


@dataclass(frozen=True)
class AuditResult:
    ok: bool
    forbidden_hits: tuple[str, ...]
    missing_required: tuple[str, ...]
    invalid_assets: tuple[str, ...]


def audit_archive(path: Path) -> AuditResult:
    hits: list[str] = []
    missing: list[str] = []
    invalid_assets: list[str] = []
    release_paths: set[str] = set()
    with tarfile.open(path) as tar:
        for member in tar.getmembers():
            name = member.name.strip("/")
            release_name = _release_relative_name(name)
            if release_name:
                release_paths.add(release_name)
            if _release_forbidden(name, release_name):
                hits.append(name)
        missing.extend(sorted(REQUIRED_RELEASE_PATHS - release_paths))
        invalid_assets.extend(_audit_nested_asset(tar, "runtime/assets/agents.tar", REQUIRED_AGENT_ASSET_PATHS))
        invalid_assets.extend(
            _audit_nested_asset(tar, "runtime/assets/openclaw_plugins.tar", REQUIRED_PLUGIN_ASSET_PATHS)
        )
    return AuditResult(
        ok=not hits and not missing and not invalid_assets,
        forbidden_hits=tuple(hits),
        missing_required=tuple(missing),
        invalid_assets=tuple(invalid_assets),
    )


def _release_relative_name(member_name: str) -> str:
    parts = member_name.split("/", 1)
    if len(parts) != 2:
        return ""
    return parts[1]


def _release_forbidden(member_name: str, release_name: str) -> bool:
    parts = set(member_name.split("/"))
    if ".git" in parts:
        return True
    if member_name.endswith(".env.local") or "web/research-ui/src/" in f"{member_name}/":
        return True
    if release_name.startswith("runtime/openclaw/node_modules/"):
        return False
    if parts & {"tests", "docs"}:
        return True
    if release_name.startswith("agents/") or release_name.startswith("openclaw_plugins/"):
        return True
    return False


def _audit_nested_asset(tar: tarfile.TarFile, release_name: str, required: set[str]) -> list[str]:
    outer_name = _find_release_member(tar, release_name)
    if outer_name is None:
        return []
    extracted = tar.extractfile(outer_name)
    if extracted is None:
        return [f"{release_name}: not a readable tar file"]
    data = extracted.read()
    try:
        with tarfile.open(fileobj=io.BytesIO(data)) as nested:
            names = {member.name.strip("/") for member in nested.getmembers()}
    except tarfile.TarError as exc:
        return [f"{release_name}: invalid tar file: {exc}"]
    errors: list[str] = []
    missing = sorted(required - names)
    errors.extend(f"{release_name}: missing {item}" for item in missing)
    forbidden = sorted(name for name in names if _nested_asset_forbidden(name))
    errors.extend(f"{release_name}: forbidden {item}" for item in forbidden)
    return errors


def _find_release_member(tar: tarfile.TarFile, release_name: str) -> str | None:
    suffix = f"/{release_name}"
    for member in tar.getmembers():
        if member.name.strip("/").endswith(suffix):
            return member.name
    return None


def _nested_asset_forbidden(name: str) -> bool:
    parts = set(name.strip("/").split("/"))
    if parts & {".git", "tests", "docs", "__pycache__"}:
        return True
    if name.endswith(".env.local") or name.endswith(".pyc") or name.endswith("prompt-review.yaml"):
        return True
    if "/node_modules/" in f"/{name}/":
        return True
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("archive")
    args = parser.parse_args(argv)
    result = audit_archive(Path(args.archive))
    for hit in result.forbidden_hits:
        print(f"forbidden: {hit}")
    for item in result.missing_required:
        print(f"missing: {item}")
    for item in result.invalid_assets:
        print(f"invalid_asset: {item}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
