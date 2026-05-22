from __future__ import annotations

from pathlib import Path
import subprocess
import sys

from fastapi.testclient import TestClient

from claw_trade.web.app import build_research_ui_app
from claw_trade.web.settings import ResearchUiServerSettings
from claw_trade.web.state import build_ui_http_services


def _app(tmp_path: Path):
    dist = tmp_path / "dist"
    assets = dist / "assets"
    assets.mkdir(parents=True)
    (dist / "index.html").write_text("<html><body>research-ui</body></html>", encoding="utf-8")
    (assets / "main.js").write_text("console.log('ok');", encoding="utf-8")
    settings = ResearchUiServerSettings(frontend_dist=dist)
    return build_research_ui_app(settings=settings, services=build_ui_http_services(settings))


def test_spa_fallback_is_last_and_api_prefix_keeps_json(tmp_path: Path) -> None:
    client = TestClient(_app(tmp_path))

    spa = client.get("/some/page")
    assert spa.status_code == 200
    assert spa.headers["content-type"].startswith("text/html")

    api = client.get("/api/ui/list-saved-reports")
    assert api.status_code == 200
    assert api.headers["content-type"].startswith("application/json")
    assert "text/html" not in api.headers["content-type"]

    missing_api = client.get("/api/ui/not-exists")
    assert missing_api.status_code == 404
    assert missing_api.headers["content-type"].startswith("application/json")


def test_module_entrypoint_help_for_claw_trade_web_app() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "claw_trade.web.app", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0
    assert "--frontend-dist" in completed.stdout
