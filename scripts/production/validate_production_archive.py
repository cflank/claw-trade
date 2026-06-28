#!/usr/bin/env python3
from __future__ import annotations

import argparse
import posixpath
import re
import sys
import tarfile
from pathlib import Path


RELEASE_NAME_RE = re.compile(r"^claw-trade-production-\d+\.\d+\.\d+-\d{8}T\d{6}Z$")


def _fail(message: str) -> int:
    print(message, file=sys.stderr)
    return 1


def _expected_release_name(path: Path) -> str:
    name = path.name
    if not name.endswith(".tar.gz"):
        raise ValueError("archive must be named claw-trade-production-*.tar.gz")
    release_name = name[: -len(".tar.gz")]
    if not RELEASE_NAME_RE.fullmatch(release_name):
        raise ValueError("archive basename is not a formal production release name")
    return release_name


def _normalize_member_name(raw_name: str) -> str:
    if "\x00" in raw_name or "\\" in raw_name or raw_name.startswith("/"):
        raise ValueError(f"invalid archive member path: {raw_name!r}")
    stripped = raw_name.strip("/")
    normalized = posixpath.normpath(stripped)
    if normalized in {"", "."} or normalized != stripped:
        raise ValueError(f"invalid archive member path: {raw_name!r}")
    if normalized == ".." or normalized.startswith("../") or "/../" in f"/{normalized}/":
        raise ValueError(f"archive member escapes release directory: {raw_name!r}")
    return normalized


def _validate_member_type_and_mode(member: tarfile.TarInfo) -> None:
    if not (member.isdir() or member.isreg()):
        raise ValueError(f"unsupported archive member type: {member.name!r}")
    if member.mode & 0o6000:
        raise ValueError(f"archive member has setuid/setgid bits: {member.name!r}")


def validate(path: Path) -> str:
    expected = _expected_release_name(path)
    seen_member = False
    with tarfile.open(path) as archive:
        for member in archive:
            normalized = _normalize_member_name(member.name)
            _validate_member_type_and_mode(member)
            top_dir = normalized.split("/", 1)[0]
            if top_dir != expected:
                raise ValueError(
                    f"archive top-level directory {top_dir!r} does not match archive basename {expected!r}"
                )
            seen_member = True
    if not seen_member:
        raise ValueError("archive is empty")
    return expected


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a claw-trade production archive name and top-level dir.")
    parser.add_argument("archive", type=Path)
    args = parser.parse_args(argv)

    try:
        print(validate(args.archive))
    except (tarfile.TarError, OSError, ValueError) as exc:
        return _fail(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
