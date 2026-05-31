from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SELECTION_TOOL_SOURCE = REPO_ROOT / "src" / "claw_trade" / "selection" / "tools.py"
SEL04_APPROVED_ARTIFACTS = (
    REPO_ROOT
    / "docs"
    / "evidence"
    / "sel-04-candidate-pack-artifacts-2026-05-26"
    / "sel04-approval-run"
    / "candidate-pack"
    / "approved"
)

_FORBIDDEN_SOURCE_TOKENS = (
    "claw_trade.data_gateway",
    "claw_trade.providers",
    "openbb",
    "pymongo",
    "MongoClient",
    "provider_executor",
    "frontline_data_pack",
)


def _prepare_runtime_context(tmp_path: Path) -> dict[str, object]:
    artifact_root = tmp_path / "selection-artifacts"
    target = artifact_root / "sel04-approval-run" / "candidate-pack" / "approved"
    shutil.copytree(SEL04_APPROVED_ARTIFACTS, target)

    manifest = json.loads((target / "candidate-pack-manifest.json").read_text(encoding="utf-8"))
    candidate_pack_ref = {
        "selection_run_id": manifest["selection_run_id"],
        "material_id": "selection-candidate-pack-import-block",
        "l1_uri": f"local://selection/{manifest['selection_run_id']}/candidate-pack/approved/candidate-pack.md",
        "content_sha256": manifest["pack_body_sha256"],
        "manifest_ref": f"local://selection/{manifest['selection_run_id']}/candidate-pack/approved/candidate-pack-manifest.json",
        "approved_at": "2026-05-26T09:00:00Z",
        "expires_at": "2026-06-26T09:00:00Z",
        "pack_summary_ref": f"local://selection/{manifest['selection_run_id']}/candidate-pack/approved/candidate-pack-summary.md",
    }
    return {
        "worker_id": "selection_strategist",
        "stage": "selection_review",
        "run_id": "sel-run-import-block",
        "call_id": "dispatch-import-block",
        "select_workflow_run_id": "wf-import-block",
        "selection_run_id": manifest["selection_run_id"],
        "selection_artifact_root": str(artifact_root),
        "candidate_pack_ref": candidate_pack_ref,
        "runtime_vars": {
            "market": "CN_A",
            "profile": "CN_A",
            "trade_date": manifest["trade_date"],
            "selection_run_id": manifest["selection_run_id"],
            "select_workflow_run_id": "wf-import-block",
            "selection_artifact_root": str(artifact_root),
            "candidate_pack_ref": candidate_pack_ref,
        },
    }


def test_selection_tool_backend_source_does_not_reference_provider_or_raw_modules() -> None:
    source = SELECTION_TOOL_SOURCE.read_text(encoding="utf-8")
    for token in _FORBIDDEN_SOURCE_TOKENS:
        assert token not in source


def test_selection_tool_backend_runs_with_provider_and_raw_imports_blocked(tmp_path: Path) -> None:
    runtime_context = _prepare_runtime_context(tmp_path)
    script = textwrap.dedent(
        """
        import importlib.abc
        import json
        import sys

        BLOCKED = (
            "claw_trade.data_gateway",
            "claw_trade.providers",
            "openbb",
            "pymongo",
            "frontline_data_pack",
            "provider_executor",
        )

        class BlockImports(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                del path, target
                for blocked in BLOCKED:
                    if fullname == blocked or fullname.startswith(blocked + "."):
                        raise ImportError(f"blocked import: {fullname}")
                return None

        sys.meta_path.insert(0, BlockImports())

        from claw_trade.selection.tools import load_selection_candidate_pack_from_runtime_context

        ctx = json.loads(sys.argv[1])
        result = load_selection_candidate_pack_from_runtime_context(ctx)
        print(json.dumps({
            "selection_run_id": result.selection_run_id,
            "pack_body_sha256": result.pack_body_sha256,
            "candidate_count": result.candidate_count,
        }, ensure_ascii=False))
        """
    ).strip()

    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO_ROOT / "src")
    completed = subprocess.run(
        [sys.executable, "-c", script, json.dumps(runtime_context)],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    assert payload["selection_run_id"] == "sel04-approval-run"
    assert payload["candidate_count"] == 20
    assert isinstance(payload["pack_body_sha256"], str) and len(payload["pack_body_sha256"]) == 64
