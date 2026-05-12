from __future__ import annotations

import subprocess
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def test_openviking_report_server_installs_no_vectorization_patches() -> None:
    code = """
from claw_trade.runtime.openviking_report_server import _install_report_runtime_patches

_install_report_runtime_patches()

from openviking_cli.utils.config.embedding_config import EmbeddingConfig, EmbeddingModelConfig

config = EmbeddingConfig(
    dense=EmbeddingModelConfig(provider="litellm", model="deepseek/deepseek-chat", dimension=2048)
)
embedder = config.get_embedder()
print(embedder.provider)
try:
    embedder.embed("should fail")
except RuntimeError as exc:
    print(str(exc))
else:
    raise SystemExit("disabled embedder did not fail")
"""
    completed = subprocess.run(
        ["uv", "run", "--project", str(_repo_root()), "python", "-c", code],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert "disabled" in completed.stdout
    assert "vectorization is disabled" in completed.stdout
