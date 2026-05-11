from __future__ import annotations

import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_PYTHON_ROOT = REPO_ROOT / "openclaw_plugins" / "claw-trade-frontline-tools" / "python"
if str(SHARED_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(SHARED_PYTHON_ROOT))

from frontline_data_pack.health import (  # noqa: E402
    L2_STAT_VERIFICATION_FAILED,
    TOOL_REGISTRATION_ERROR,
    frontline_data_pack_health,
    frontline_l2_health,
    frontline_tool_health,
)
from frontline_data_pack.mongo_store import build_collection_index_models  # noqa: E402
from frontline_data_pack.evidence import OpenVikingStatResult, OpenVikingWriteResult  # noqa: E402


def test_t_ops_001_frontline_tool_health_returns_registration_error_when_tools_missing(tmp_path: Path) -> None:
    plugin_dir = tmp_path / "openclaw_plugins" / "claw-trade-frontline-tools"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "openclaw.plugin.json").write_text(
        json.dumps(
            {
                "id": "claw-trade-frontline-tools",
                "contracts": {
                    "tools": [
                        "market_market_data_pack",
                        "fundamental_fundamentals_data_pack",
                        "news_news_data_pack",
                    ]
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = frontline_tool_health(repo_root=tmp_path)

    assert result.ok is False
    assert result.code == TOOL_REGISTRATION_ERROR


def test_t_ops_001_frontline_data_pack_health_returns_healthy_for_fast_ping_and_existing_indexes(monkeypatch) -> None:
    class _FakeAdmin:
        def command(self, name: str) -> dict[str, int]:
            if name != "ping":
                raise AssertionError(name)
            return {"ok": 1}

    class _FakeCollection:
        def __init__(self, keys_list: list[list[tuple[str, int]]]) -> None:
            self._keys_list = keys_list

        def index_information(self) -> dict[str, dict[str, object]]:
            return {
                f"idx_{index}": {"key": keys}
                for index, keys in enumerate(self._keys_list)
            }

    class _FakeDatabase:
        def __init__(self) -> None:
            models = build_collection_index_models()
            self._collections: dict[str, _FakeCollection] = {}
            for name, index_models in models.items():
                keys_list = [list(model.document["key"].items()) for model in index_models]
                self._collections[name] = _FakeCollection(keys_list)

        def __getitem__(self, name: str) -> _FakeCollection:
            return self._collections[name]

    class _FakeStore:
        def __init__(self) -> None:
            self.client = type("FakeClient", (), {"admin": _FakeAdmin()})()
            self.database = _FakeDatabase()
            self.database_name = "claw_trade"

    monkeypatch.setattr("frontline_data_pack.health.create_mongo_store", lambda _uri: _FakeStore())

    env = {
        "CN_A_PROVIDER_DEFAULT_TIMEOUT_MS": "10000",
        "CN_A_PROVIDER_TOTAL_TIMEOUT_MS": "30000",
        "CN_A_PROVIDER_MAX_CONCURRENCY": "3",
        "CN_A_PROVIDER_CACHE_REQUIRED": "false",
        "CN_A_MONGODB_URI": "mongodb://localhost:27017/claw_trade",
        "CLAW_TRADE_OPENVIKING_BASE_URI": "http://127.0.0.1:1933",
        "CLAW_TRADE_OPENVIKING_AUTH_MODE": "none",
    }
    result = frontline_data_pack_health(env=env)

    assert result.ok is True
    assert result.details["latency_ms"] < 200


def test_t_ops_001_frontline_l2_health_stat_verification_failure_is_unhealthy_and_blocks_core_evidence() -> None:
    class _StatFailClient:
        def __init__(self) -> None:
            self._content: dict[str, bytes] = {}

        def write(self, *, uri: str, content_bytes: bytes, content_type: str, metadata: dict[str, str]) -> OpenVikingWriteResult:
            _ = content_type, metadata
            self._content[uri] = content_bytes
            return OpenVikingWriteResult(receipt_id="r-1")

        def stat(self, *, uri: str) -> OpenVikingStatResult:
            _ = uri
            return OpenVikingStatResult(size_bytes=None, sha256=None, exists=True)

        def read(self, *, uri: str) -> bytes:
            return self._content[uri]

    result = frontline_l2_health(client=_StatFailClient())

    assert result.ok is False
    assert result.code == L2_STAT_VERIFICATION_FAILED
    assert result.details["blocks_core_evidence"] is True
