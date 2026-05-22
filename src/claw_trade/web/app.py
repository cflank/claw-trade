from __future__ import annotations

import argparse
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from claw_trade.web.routes_ui import router as ui_router
from claw_trade.web.settings import ResearchUiServerSettings
from claw_trade.web.state import UiHttpServices, build_ui_http_services, default_frontend_dist


def build_research_ui_app(
    *,
    settings: ResearchUiServerSettings,
    services: UiHttpServices | None = None,
) -> FastAPI:
    app = FastAPI(title="claw-trade research ui")
    app.state.research_ui_settings = settings
    app.state.ui_services = services or build_ui_http_services(settings)

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
        return FileResponse(index_path)

    return app


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Serve claw-trade research UI HTTP backend.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5175)
    parser.add_argument("--frontend-dist", default=str(default_frontend_dist()))
    parser.add_argument("--gateway-call-bin", default=os.environ.get("OPENCLAW_GATEWAY_CALL_BIN", "openclaw"))
    parser.add_argument("--gateway-ws-url", default=os.environ.get("OPENCLAW_GATEWAY_URL", "ws://127.0.0.1:18789"))
    parser.add_argument("--gateway-timeout-ms", type=int, default=int(os.environ.get("OPENCLAW_GATEWAY_TIMEOUT_MS", "10000")))
    parser.add_argument("--gateway-token", default=os.environ.get("OPENCLAW_GATEWAY_TOKEN") or None)
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


if __name__ == "__main__":
    raise SystemExit(main())
