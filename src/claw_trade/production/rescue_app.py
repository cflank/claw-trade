from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from claw_trade.production.factory_reset import FACTORY_RESET_CONFIRMATION, FactoryResetService


class RescueFactoryResetRequest(BaseModel):
    requestId: str
    confirmation: str


def build_rescue_app(*, factory_reset: FactoryResetService | None = None) -> FastAPI:
    service = factory_reset or FactoryResetService()
    app = FastAPI(title="claw-trade rescue")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "mode": "rescue"}

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return _RESCUE_HTML

    @app.get("/api/rescue/status")
    def status() -> JSONResponse:
        return JSONResponse({"mode": "rescue", "factoryReset": service.status_for_user()})

    @app.post("/api/rescue/factory-reset")
    def factory_reset(payload: RescueFactoryResetRequest, request: Request) -> JSONResponse:
        _assert_local_request(request)
        try:
            result = service.run(request_id=payload.requestId, confirmation=payload.confirmation)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return JSONResponse(result.to_user_dict())

    return app


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Serve claw-trade rescue UI.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5175)
    parser.add_argument("--install-root", default="/opt/claw-trade")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    app = build_rescue_app(factory_reset=FactoryResetService(install_root=Path(args.install_root)))
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


def _assert_local_request(request: Request) -> None:
    host = getattr(request.client, "host", "") if request.client is not None else ""
    if host not in {"127.0.0.1", "::1", "testclient"}:
        raise HTTPException(status_code=401, detail="恢复出厂只能从本机界面执行。")
    origin = request.headers.get("origin")
    referer = request.headers.get("referer")
    if origin:
        if not _is_local_browser_url(origin):
            raise HTTPException(status_code=401, detail="恢复出厂只能从本机界面执行。")
        return
    if referer and _is_local_browser_url(referer):
        return
    raise HTTPException(status_code=401, detail="恢复出厂只能从本机界面执行。")


def _is_local_browser_url(value: str) -> bool:
    from urllib.parse import urlsplit

    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and parsed.hostname in {"127.0.0.1", "localhost", "::1"} and parsed.port in {
        None,
        5175,
    }


_RESCUE_HTML = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>claw-trade 救援页</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 32px; max-width: 760px; }}
    button {{ padding: 10px 14px; }}
    code {{ background: #eee; padding: 2px 4px; }}
    .danger {{ border: 1px solid #b91c1c; padding: 16px; }}
    #result {{ white-space: pre-wrap; }}
  </style>
</head>
<body>
  <h1>claw-trade 救援页</h1>
  <p>主界面不可用时，可以在这里查看救援状态并执行恢复出厂。</p>
  <div class="danger">
    <h2>恢复出厂</h2>
    <p>会清空应用配置、报告、缓存、队列、会话和临时文件；保留授权、更新包和当前版本。</p>
    <p>确认码：<code>{FACTORY_RESET_CONFIRMATION}</code></p>
    <button id="factory-reset" type="button">恢复出厂设置</button>
    <p id="result"></p>
  </div>
  <script>
    const result = document.getElementById('result');
    document.getElementById('factory-reset').addEventListener('click', async () => {{
      if (!window.confirm('恢复出厂设置会删除本机应用配置、历史报告、缓存、队列、会话和临时文件。确定继续吗？')) {{
        return;
      }}
      result.textContent = '正在执行...';
      const response = await fetch('/api/rescue/factory-reset', {{
        method: 'POST',
        headers: {{ 'content-type': 'application/json' }},
        body: JSON.stringify({{
          requestId: `rescue-reset-${{Date.now()}}`,
          confirmation: '{FACTORY_RESET_CONFIRMATION}'
        }})
      }});
      const payload = await response.json();
      result.textContent = response.ok ? payload.userMessage : (payload.detail || payload.message || '恢复出厂失败');
    }});
  </script>
</body>
</html>
"""


if __name__ == "__main__":
    raise SystemExit(main())
