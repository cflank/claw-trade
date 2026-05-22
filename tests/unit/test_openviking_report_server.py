from __future__ import annotations

import subprocess
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def test_openviking_report_server_keeps_embedding_enabled_by_default(monkeypatch) -> None:
    from claw_trade.runtime.openviking_report_server import _openviking_embedding_enabled

    monkeypatch.delenv("CLAW_TRADE_OPENVIKING_EMBEDDING_ENABLED", raising=False)
    assert _openviking_embedding_enabled() is True

    monkeypatch.setenv("CLAW_TRADE_OPENVIKING_EMBEDDING_ENABLED", "0")
    assert _openviking_embedding_enabled() is False


def test_openviking_report_server_can_install_explicit_no_vectorization_patches() -> None:
    code = """
from claw_trade.runtime.openviking_report_server import _install_report_runtime_patches

_install_report_runtime_patches()

from openviking_cli.utils.config.embedding_config import EmbeddingConfig, EmbeddingModelConfig

config = EmbeddingConfig(
    dense=EmbeddingModelConfig(provider="litellm", model="deepseek/deepseek-chat", dimension=2048)
)
embedder = config.get_embedder()
print(embedder.provider)
"""
    completed = subprocess.run(
        ["uv", "run", "--project", str(_repo_root()), "python", "-c", code],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert "disabled" in completed.stdout
