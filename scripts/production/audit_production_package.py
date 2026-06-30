#!/usr/bin/env python3
from __future__ import annotations

import argparse
import io
import os
import sys
import tarfile
from dataclasses import dataclass
from pathlib import Path


REQUIRED_RELEASE_PATHS = {
    "bin/claw-trade-ui",
    "runtime/claw-trade-control-runtime",
    "runtime/python/bin/python",
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
    "openclaw_plugins/node_modules/@tencent-weixin/openclaw-weixin/openclaw.plugin.json",
    "openclaw_plugins/node_modules/@tencent-weixin/openclaw-weixin/dist/index.js",
    "openclaw_plugins/node_modules/qrcode-terminal/package.json",
    "openclaw_plugins/node_modules/zod/package.json",
}

FORBIDDEN_TOP_LEVEL = {"tests", "docs", "memory"}
FORBIDDEN_SUFFIXES = {
    ".key",
    ".p12",
    ".pfx",
}
FORBIDDEN_EXACT = {
    ".env.local",
    "AGENTS.md",
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
    with tarfile.open(path) as archive:
        for member in archive.getmembers():
            name = member.name.strip("/")
            release_name = _release_relative_name(name)
            if release_name:
                release_paths.add(release_name)
            if _release_forbidden(name, release_name):
                hits.append(name)
        missing.extend(sorted(REQUIRED_RELEASE_PATHS - release_paths))
        invalid_assets.extend(_audit_nested_asset(archive, "runtime/assets/agents.tar", REQUIRED_AGENT_ASSET_PATHS))
        invalid_assets.extend(
            _audit_nested_asset(archive, "runtime/assets/openclaw_plugins.tar", REQUIRED_PLUGIN_ASSET_PATHS)
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


def _audit_nested_asset(archive: tarfile.TarFile, release_name: str, required: set[str]) -> list[str]:
    outer_name = _find_release_member(archive, release_name)
    if outer_name is None:
        return []
    extracted = archive.extractfile(outer_name)
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


def _find_release_member(archive: tarfile.TarFile, release_name: str) -> str | None:
    suffix = f"/{release_name}"
    for member in archive.getmembers():
        if member.name.strip("/").endswith(suffix):
            return member.name
    return None


def _nested_asset_forbidden(name: str) -> bool:
    parts = set(name.strip("/").split("/"))
    if name == "openclaw_plugins/node_modules" or name.startswith("openclaw_plugins/node_modules/"):
        if ".git" in parts:
            return True
        if name.endswith(".env.local") or name.endswith(".pyc") or name.endswith("prompt-review.yaml"):
            return True
        return False
    if parts & {".git", "tests", "docs", "__pycache__"}:
        return True
    if name.endswith(".env.local") or name.endswith(".pyc") or name.endswith("prompt-review.yaml"):
        return True
    if "/node_modules/" in f"/{name}/":
        return True
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit claw-trade factory-test package contents.")
    parser.add_argument("path", type=Path)
    args = parser.parse_args(argv)

    path = args.path
    archive_result: AuditResult | None = None
    if path.is_dir():
        names = [p.relative_to(path).as_posix() for p in path.rglob("*")]
    elif tarfile.is_tarfile(path):
        archive_result = audit_archive(path)
        with tarfile.open(path) as archive:
            names = [_release_relative_name(name.strip("/")) for name in archive.getnames()]
    else:
        print(f"unsupported package path: {path}", file=sys.stderr)
        return 2

    failures: list[str] = []
    for raw_name in names:
        name = raw_name.strip("/")
        if not name:
            continue
        path_parts = Path(name).parts
        parts = set(path_parts)
        top_level = path_parts[0] if path_parts else ""
        suffix = Path(name).suffix.lower()
        if ".git" in parts or "__pycache__" in parts:
            failures.append(f"forbidden path: {name}")
        if top_level in FORBIDDEN_TOP_LEVEL:
            failures.append(f"forbidden top-level path: {name}")
        if name in FORBIDDEN_EXACT:
            failures.append(f"forbidden file: {name}")
        if suffix in FORBIDDEN_SUFFIXES:
            failures.append(f"forbidden secret-like file: {name}")
        if name.startswith("web/research-ui/src/"):
            failures.append(f"forbidden frontend source: {name}")
        if name.startswith("app/python/claw_trade/") and name.endswith(".py"):
            failures.append(f"forbidden claw_trade source: {name}")

    if archive_result is not None:
        failures.extend(f"forbidden archive path: {item}" for item in archive_result.forbidden_hits)
        failures.extend(f"missing required path: {item}" for item in archive_result.missing_required)
        failures.extend(f"invalid runtime asset: {item}" for item in archive_result.invalid_assets)

    if failures:
        for failure in failures[:100]:
            print(failure, file=sys.stderr)
        if len(failures) > 100:
            print(f"... {len(failures) - 100} more failures", file=sys.stderr)
        return 1

    print(f"OK: audited {len(names)} entries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
