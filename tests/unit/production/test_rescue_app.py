from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from claw_trade.production import host_locks
from claw_trade.production.factory_reset import FACTORY_RESET_CONFIRMATION, FactoryResetService
from claw_trade.production.rescue_app import build_rescue_app


@pytest.fixture(autouse=True)
def installed_host_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    host_lock = tmp_path / "opt" / "claw-trade" / "host-operations.lock"
    host_lock.parent.mkdir(parents=True)
    host_lock.touch()
    host_lock.chmod(0o660)
    monkeypatch.setattr(host_locks, "_expected_identity", lambda: (os.getuid(), os.getgid()))


def test_rescue_app_serves_status_and_factory_reset(tmp_path: Path) -> None:
    install_root = tmp_path / "opt" / "claw-trade"
    shared = install_root / "shared"
    dirty = shared / "config" / "app.env"
    dirty.parent.mkdir(parents=True)
    dirty.write_text("secret", encoding="utf-8")
    app = build_rescue_app(factory_reset=FactoryResetService(install_root=install_root))
    client = TestClient(app)

    page = client.get("/")
    assert page.status_code == 200
    assert "claw-trade 救援页" in page.text
    assert "恢复出厂设置" in page.text

    status = client.get("/api/rescue/status")
    assert status.status_code == 200
    assert status.json()["mode"] == "rescue"

    reset = client.post(
        "/api/rescue/factory-reset",
        headers={"origin": "http://127.0.0.1:5175"},
        json={"requestId": "rescue-reset", "confirmation": FACTORY_RESET_CONFIRMATION},
    )
    assert reset.status_code == 200
    assert reset.json()["status"] == "completed"
    assert dirty.exists() is False


def test_rescue_app_rejects_external_origin_for_factory_reset(tmp_path: Path) -> None:
    app = build_rescue_app(factory_reset=FactoryResetService(install_root=tmp_path / "opt" / "claw-trade"))
    client = TestClient(app)

    reset = client.post(
        "/api/rescue/factory-reset",
        headers={"origin": "https://example.com"},
        json={"requestId": "rescue-reset", "confirmation": FACTORY_RESET_CONFIRMATION},
    )

    assert reset.status_code == 401


def test_rescue_app_rejects_wrong_factory_reset_confirmation(tmp_path: Path) -> None:
    app = build_rescue_app(factory_reset=FactoryResetService(install_root=tmp_path / "opt" / "claw-trade"))
    client = TestClient(app)

    reset = client.post(
        "/api/rescue/factory-reset",
        headers={"origin": "http://127.0.0.1:5175"},
        json={"requestId": "rescue-reset", "confirmation": "wrong"},
    )

    assert reset.status_code == 400
