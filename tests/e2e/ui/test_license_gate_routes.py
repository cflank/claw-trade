from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from claw_trade.licensing.status import LicenseSnapshot, LicenseStatus
from claw_trade.web.routes_ui import router


class _LicenseService:
    def current_snapshot(self) -> LicenseSnapshot:
        return LicenseSnapshot(status=LicenseStatus.REVOKED, features=frozenset(), license_suffix="1234")


def test_license_status_route_returns_redacted_status() -> None:
    app = FastAPI()
    app.include_router(router, prefix="/api/ui")
    app.state.ui_services = SimpleNamespace(license_service=_LicenseService())
    client = TestClient(app)

    response = client.get("/api/ui/get-license-status")

    assert response.status_code == 200
    assert response.json()["status"] == "revoked"
    assert response.json()["allowsReportGeneration"] is False
    assert response.json()["allowsDataRefresh"] is False
    assert response.json()["licenseSuffix"] == "1234"
    assert "licenseCode" not in response.text
