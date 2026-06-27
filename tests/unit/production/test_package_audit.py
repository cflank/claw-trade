from __future__ import annotations

import tarfile
from pathlib import Path

from scripts.production.audit_production_package import audit_archive


def _write_archive(tmp_path: Path, names: list[str]) -> Path:
    archive = tmp_path / "package.tar"
    payload = tmp_path / "payload"
    for name in names:
        file_path = payload / name
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text("x\n", encoding="utf-8")
    with tarfile.open(archive, "w") as tar:
        tar.add(payload, arcname="claw-trade")
    return archive


def test_package_audit_accepts_clean_package(tmp_path: Path) -> None:
    archive = _write_archive(tmp_path, ["bin/claw-trade-ui"])

    result = audit_archive(archive)

    assert result.ok
    assert result.forbidden_hits == ()


def test_package_audit_rejects_forbidden_payload(tmp_path: Path) -> None:
    archive = tmp_path / "bad.tar"
    payload = tmp_path / "payload"
    forbidden_names = [
        ".git/HEAD",
        "tests/test_smoke.py",
        "docs/internal.md",
        ".env.local",
        "web/research-ui/src/main.tsx",
    ]
    for name in forbidden_names:
        file_path = payload / name
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text("x\n", encoding="utf-8")
    with tarfile.open(archive, "w") as tar:
        tar.add(payload, arcname="claw-trade")

    result = audit_archive(archive)

    assert not result.ok
    assert any(".git" in hit for hit in result.forbidden_hits)
    assert any("tests" in hit for hit in result.forbidden_hits)
    assert any("docs" in hit for hit in result.forbidden_hits)
    assert any(".env.local" in hit for hit in result.forbidden_hits)
    assert any("web/research-ui/src" in hit for hit in result.forbidden_hits)
