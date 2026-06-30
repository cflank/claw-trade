from __future__ import annotations

import tarfile
import shutil
from pathlib import Path

from scripts.production.audit_production_package import (
    REQUIRED_AGENT_ASSET_PATHS,
    REQUIRED_PLUGIN_ASSET_PATHS,
    REQUIRED_RELEASE_PATHS,
    audit_archive,
    main,
)


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


def _write_clean_archive(tmp_path: Path) -> Path:
    archive = tmp_path / "package.tar"
    payload = tmp_path / "payload"
    for name in sorted(REQUIRED_RELEASE_PATHS - {"runtime/assets/agents.tar", "runtime/assets/openclaw_plugins.tar"}):
        file_path = payload / name
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text("x\n", encoding="utf-8")
    _write_nested_tar(payload / "runtime" / "assets" / "agents.tar", sorted(REQUIRED_AGENT_ASSET_PATHS))
    _write_nested_tar(
        payload / "runtime" / "assets" / "openclaw_plugins.tar",
        sorted(REQUIRED_PLUGIN_ASSET_PATHS),
    )
    with tarfile.open(archive, "w") as tar:
        tar.add(payload, arcname="claw-trade")
    return archive


def _write_nested_tar(path: Path, names: list[str]) -> None:
    source = path.parent / f"{path.stem}-source"
    if source.exists():
        shutil.rmtree(source)
    for name in names:
        file_path = source / name
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text("x\n", encoding="utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w") as tar:
        for item in source.iterdir():
            tar.add(item, arcname=item.name)


def test_package_audit_accepts_clean_package(tmp_path: Path) -> None:
    archive = _write_clean_archive(tmp_path)

    result = audit_archive(archive)

    assert result.ok
    assert result.forbidden_hits == ()
    assert result.missing_required == ()
    assert result.invalid_assets == ()


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


def test_package_audit_rejects_missing_runtime_assets(tmp_path: Path) -> None:
    archive = _write_archive(tmp_path, ["bin/claw-trade-ui", "web/dist/index.html"])

    result = audit_archive(archive)

    assert not result.ok
    assert "runtime/assets/agents.tar" in result.missing_required
    assert "runtime/assets/openclaw_plugins.tar" in result.missing_required


def test_package_audit_rejects_missing_bundled_weixin_plugin(tmp_path: Path) -> None:
    archive = _write_clean_archive(tmp_path)
    payload = tmp_path / "payload"
    with tarfile.open(archive) as source:
        source.extractall(payload)
    plugins_tar = payload / "claw-trade" / "runtime" / "assets" / "openclaw_plugins.tar"
    _write_nested_tar(
        plugins_tar,
        sorted(REQUIRED_PLUGIN_ASSET_PATHS - {"openclaw_plugins/node_modules/@tencent-weixin/openclaw-weixin/openclaw.plugin.json"}),
    )
    with tarfile.open(archive, "w") as tar:
        tar.add(payload / "claw-trade", arcname="claw-trade")

    result = audit_archive(archive)

    assert not result.ok
    assert any("openclaw-weixin/openclaw.plugin.json" in item for item in result.invalid_assets)


def test_package_audit_cli_rejects_missing_bundled_weixin_plugin(tmp_path: Path) -> None:
    archive = _write_archive(tmp_path, sorted(REQUIRED_RELEASE_PATHS))

    assert main([str(archive)]) == 1


def test_package_audit_requires_bundled_python(tmp_path: Path) -> None:
    archive = _write_clean_archive(tmp_path)
    payload = tmp_path / "payload"
    with tarfile.open(archive) as source:
        source.extractall(payload)
    (payload / "claw-trade" / "runtime" / "python" / "bin" / "python").unlink()
    with tarfile.open(archive, "w") as tar:
        tar.add(payload / "claw-trade", arcname="claw-trade")

    result = audit_archive(archive)

    assert not result.ok
    assert "runtime/python/bin/python" in result.missing_required


def test_package_audit_rejects_top_level_plain_agent_assets(tmp_path: Path) -> None:
    archive = _write_archive(
        tmp_path,
        [
            "bin/claw-trade-ui",
            "web/dist/index.html",
            "agents/market_analyst/AGENTS.md",
            "openclaw_plugins/claw-trade-frontline-tools/index.js",
        ],
    )

    result = audit_archive(archive)

    assert not result.ok
    assert any("agents/market_analyst/AGENTS.md" in hit for hit in result.forbidden_hits)
    assert any("openclaw_plugins/claw-trade-frontline-tools/index.js" in hit for hit in result.forbidden_hits)


def test_package_audit_allows_openclaw_node_module_package_docs(tmp_path: Path) -> None:
    archive = _write_clean_archive(tmp_path)
    payload = tmp_path / "payload"
    with tarfile.open(archive) as source:
        source.extractall(payload)
    package_doc = payload / "claw-trade" / "runtime" / "openclaw" / "node_modules" / "example" / "docs" / "readme.md"
    package_doc.parent.mkdir(parents=True, exist_ok=True)
    package_doc.write_text("x\n", encoding="utf-8")
    with tarfile.open(archive, "w") as tar:
        tar.add(payload / "claw-trade", arcname="claw-trade")

    result = audit_archive(archive)

    assert result.ok
