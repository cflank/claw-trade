from __future__ import annotations

import json
from pathlib import Path
import sys

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from release_tools import main  # noqa: E402


def _write_skill_file(path: Path, *, version: str = "0.1.0") -> Path:
    payload = f"""---
name: cn-a-news-data
version: {version}
description: test
tool: news_news_data_pack
tool_name: news_news_data_pack
entrypoint: scripts/news_data_pack.py
schema_version: cn_a_news_pack.v1
---
"""
    path.write_text(payload, encoding="utf-8")
    return path


def _write_manifest_file(path: Path, *, skill_version: str = "0.1.0") -> Path:
    payload = f"""skills:
  - path: claw-trade-stage/SKILL.md
  - path: cn-a-news-data/SKILL.md
    skill_id: cn-a-news-data
    skill_version: {skill_version}
    workers:
      - news_analyst
    tool_exports:
      - news_news_data_pack
"""
    path.write_text(payload, encoding="utf-8")
    return path


def test_check_version_returns_0_when_skill_and_manifest_version_match(
    tmp_path: Path, capsys
) -> None:
    skill_file = _write_skill_file(tmp_path / "SKILL.md", version="0.1.0")
    manifest_file = _write_manifest_file(tmp_path / "manifest.yaml", skill_version="0.1.0")

    exit_code = main(
        [
            "check-version",
            "--skill-file",
            str(skill_file),
            "--manifest-file",
            str(manifest_file),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "release contract check passed" in captured.out
    assert captured.err == ""


def test_check_version_returns_nonzero_and_reports_mismatch_field(
    tmp_path: Path, capsys
) -> None:
    skill_file = _write_skill_file(tmp_path / "SKILL.md", version="0.1.0")
    manifest_file = _write_manifest_file(tmp_path / "manifest.yaml", skill_version="0.1.1")

    exit_code = main(
        [
            "check-version",
            "--skill-file",
            str(skill_file),
            "--manifest-file",
            str(manifest_file),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code != 0
    assert "version mismatch" in captured.err


def test_check_visible_tools_fails_and_lists_extra_tool(tmp_path: Path, capsys) -> None:
    evidence_file = tmp_path / "visible_tools.json"
    evidence_file.write_text(
        json.dumps(
            {
                "worker_id": "news_analyst",
                "visible_tools": [
                    "news_news_data_pack",
                    "openviking.read_material",
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    exit_code = main(
        [
            "check-visible-tools",
            "--evidence-file",
            str(evidence_file),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code != 0
    assert "extra tools" in captured.err
    assert "openviking.read_material" in captured.err


def test_rollback_updates_manifest_skill_version_and_runs_load_check(
    tmp_path: Path, capsys
) -> None:
    skill_file = _write_skill_file(tmp_path / "SKILL.md", version="0.2.0")
    manifest_file = _write_manifest_file(tmp_path / "manifest.yaml", skill_version="0.2.0")

    exit_code = main(
        [
            "rollback",
            "--target-version",
            "0.1.0",
            "--skill-file",
            str(skill_file),
            "--manifest-file",
            str(manifest_file),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "rollback succeeded" in captured.out
    assert "skill_version: 0.1.0" in manifest_file.read_text(encoding="utf-8")
