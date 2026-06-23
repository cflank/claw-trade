from __future__ import annotations

import argparse
import os
import shlex
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from claw_trade.web.routes_ui import router as ui_router
from claw_trade.web.settings import ResearchUiServerSettings
from claw_trade.web.state import UiHttpServices, build_ui_http_services, default_frontend_dist


def build_research_ui_app(
    *,
    settings: ResearchUiServerSettings,
    services: UiHttpServices | None = None,
) -> FastAPI:
    owns_services = services is None
    selection_auto_refresh_enabled = owns_services and _selection_auto_refresh_enabled()
    report_cleanup_scheduler_enabled = owns_services
    price_alert_scan_scheduler_enabled = owns_services

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        selection_started = False
        cleanup_started = False
        price_alert_scan_started = False
        try:
            if app.state.selection_auto_refresh_enabled:
                app.state.ui_services.selection_refresh_service.start_automatic_refresh_scheduler()
                selection_started = True
            if app.state.report_cleanup_scheduler_enabled:
                app.state.ui_services.report_cleanup_scheduler.start()
                cleanup_started = True
            price_alert_scan_scheduler = getattr(app.state.ui_services, "price_alert_scan_scheduler", None)
            if app.state.price_alert_scan_scheduler_enabled and price_alert_scan_scheduler is not None:
                price_alert_scan_scheduler.start()
                price_alert_scan_started = True
            yield
        finally:
            if price_alert_scan_started:
                app.state.ui_services.price_alert_scan_scheduler.stop()
            if cleanup_started:
                app.state.ui_services.report_cleanup_scheduler.stop()
            if selection_started:
                app.state.ui_services.selection_refresh_service.stop_automatic_refresh_scheduler()

    app = FastAPI(title="claw-trade research ui", lifespan=lifespan)
    app.state.research_ui_settings = settings
    app.state.ui_services = services or build_ui_http_services(settings)
    app.state.owns_ui_services = owns_services
    app.state.selection_auto_refresh_enabled = selection_auto_refresh_enabled
    app.state.report_cleanup_scheduler_enabled = report_cleanup_scheduler_enabled
    app.state.price_alert_scan_scheduler_enabled = price_alert_scan_scheduler_enabled

    @app.exception_handler(RequestValidationError)
    async def ui_validation_error_handler(request: Request, exc: RequestValidationError):
        if request.url.path.startswith("/api/ui"):
            payload = {
                "code": "INVALID_INPUT",
                "message": "请求参数不完整或格式不正确。",
                "severity": "error",
            }
            return JSONResponse(status_code=400, content=payload)
        return await request_validation_exception_handler(request, exc)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    frontend_dist = settings.frontend_dist
    app.include_router(ui_router, prefix="/api/ui")

    no_cache_headers = {"Cache-Control": "no-store, max-age=0"}

    @app.api_route(
        "/api/ui/{missing_path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    )
    def missing_ui_api_route(missing_path: str) -> JSONResponse:
        _ = missing_path
        return JSONResponse(
            status_code=404,
            content={"code": "INVALID_INPUT", "message": "接口不存在。"},
        )

    app.mount(
        "/assets",
        StaticFiles(directory=frontend_dist / "assets", check_dir=False),
        name="research-ui-assets",
    )

    @app.get("/{full_path:path}")
    def research_ui_spa(full_path: str = "") -> FileResponse:
        candidate = frontend_dist / full_path
        if full_path and candidate.exists() and candidate.is_file():
            return FileResponse(candidate)
        index_path = frontend_dist / "index.html"
        if not index_path.exists():
            raise HTTPException(status_code=404, detail="missing_research_ui_index")
        return FileResponse(index_path, headers=no_cache_headers)

    return app


def _selection_auto_refresh_enabled() -> bool:
    value = os.environ.get("CLAW_TRADE_SELECTION_AUTO_REFRESH")
    if value is None:
        return False
    return value.strip().lower() not in {"0", "false", "no", "off"}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Serve claw-trade research UI HTTP backend.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5175)
    parser.add_argument("--frontend-dist", default=str(default_frontend_dist()))
    parser.add_argument("--gateway-call-bin", default=os.environ.get("OPENCLAW_GATEWAY_CALL_BIN", "openclaw"))
    parser.add_argument("--gateway-ws-url", default=os.environ.get("OPENCLAW_GATEWAY_URL", "ws://127.0.0.1:18789"))
    parser.add_argument("--gateway-timeout-ms", type=int, default=int(os.environ.get("OPENCLAW_GATEWAY_TIMEOUT_MS", "10000")))
    parser.add_argument(
        "--gateway-token",
        default=os.environ.get("OPENCLAW_GATEWAY_TOKEN") or _runtime_env_value("OPENCLAW_GATEWAY_TOKEN"),
    )
    parser.add_argument("--gateway-password", default=os.environ.get("OPENCLAW_GATEWAY_PASSWORD") or None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = ResearchUiServerSettings(
        frontend_dist=Path(args.frontend_dist).resolve(),
        host=args.host,
        port=args.port,
        gateway_call_bin=args.gateway_call_bin,
        gateway_ws_url=args.gateway_ws_url,
        gateway_timeout_ms=args.gateway_timeout_ms,
        gateway_token=args.gateway_token,
        gateway_password=args.gateway_password,
    )
    app = build_research_ui_app(settings=settings)
    uvicorn.run(app, host=settings.host, port=settings.port)
    return 0


def _runtime_env_value(key: str) -> str | None:
    path = Path(".runtime/dev-services/runtime.env")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    prefix = f"{key}="
    for line in lines:
        if not line.startswith(prefix):
            continue
        raw = line[len(prefix):].strip()
        try:
            parts = shlex.split(raw)
        except ValueError:
            return raw or None
        return parts[0] if parts else None
    return None


if __name__ == "__main__":
    raise SystemExit(main())
