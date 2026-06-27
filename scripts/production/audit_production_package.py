#!/usr/bin/env python3
from __future__ import annotations

import argparse
import tarfile
from dataclasses import dataclass
from pathlib import Path


FORBIDDEN_PARTS = {
    ".git",
    "tests",
    "docs",
    ".env.local",
    "web/research-ui/src",
}


@dataclass(frozen=True)
class AuditResult:
    ok: bool
    forbidden_hits: tuple[str, ...]


def audit_archive(path: Path) -> AuditResult:
    hits: list[str] = []
    with tarfile.open(path) as tar:
        for member in tar.getmembers():
            name = member.name.strip("/")
            parts = set(name.split("/"))
            if parts & {".git", "tests", "docs"}:
                hits.append(name)
            if name.endswith(".env.local") or "web/research-ui/src/" in f"{name}/":
                hits.append(name)
    return AuditResult(ok=not hits, forbidden_hits=tuple(hits))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("archive")
    args = parser.parse_args(argv)
    result = audit_archive(Path(args.archive))
    for hit in result.forbidden_hits:
        print(f"forbidden: {hit}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
