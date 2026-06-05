from __future__ import annotations

import subprocess
from pathlib import Path


def _script_path() -> Path:
    return Path(__file__).resolve().parents[2] / "scripts" / "start-local-mongodb.sh"


def test_start_local_mongodb_script_is_bash_valid() -> None:
    completed = subprocess.run(
        ["bash", "-n", str(_script_path())],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


def test_start_local_mongodb_caps_wiredtiger_cache() -> None:
    text = _script_path().read_text(encoding="utf-8")

    assert 'CLAW_TRADE_MONGODB_CACHE_SIZE_GB="${CLAW_TRADE_MONGODB_CACHE_SIZE_GB:-1.0}"' in text
    assert "system_memory_quarter_gb()" in text
    assert "mongodb_cache_size_gb()" in text
    assert '--wiredTigerCacheSizeGB "${cache_size_gb}"' in text
    assert "MongoDB WiredTiger cache limit" in text
