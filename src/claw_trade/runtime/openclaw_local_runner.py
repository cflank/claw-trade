from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any
from urllib import error, parse, request

from claw_trade.runtime.openclaw_client import ProbeResult

_DEFAULT_GATEWAY_WS_URL = "ws://127.0.0.1:18789"
_DEFAULT_TIMEOUT_MS = 10_000
_METHOD_RUN_SINGLE_WORKER = "agent.runSingleWorker"
_GATEWAY_PARAMS_ARG_BYTE_LIMIT = 60_000
_PAIRING_REQUEST_ID_RE = re.compile(r"requestId:\s*(?P<request_id>[0-9a-fA-F-]{16,})")
_RUN_SINGLE_WORKER_SCOPES = ("operator.read", "operator.write")


def create_default_runner() -> OpenClawLocalRunner:
    raw_url = os.environ.get("OPENCLAW_GATEWAY_URL", _DEFAULT_GATEWAY_WS_URL).strip() or _DEFAULT_GATEWAY_WS_URL
    gateway_ws_url = _normalize_gateway_ws_url(raw_url)
    timeout_raw = os.environ.get("OPENCLAW_GATEWAY_TIMEOUT_MS", "").strip()
    timeout_ms = _DEFAULT_TIMEOUT_MS if not timeout_raw else int(timeout_raw)
    gateway_call_bin = os.environ.get("OPENCLAW_GATEWAY_CALL_BIN", "openclaw").strip() or "openclaw"
    token = os.environ.get("OPENCLAW_GATEWAY_TOKEN", "").strip() or None
    password = os.environ.get("OPENCLAW_GATEWAY_PASSWORD", "").strip() or None
    return OpenClawLocalRunner(
        gateway_ws_url=gateway_ws_url,
        timeout_ms=timeout_ms,
        token=token,
        password=password,
        gateway_call_bin=gateway_call_bin,
    )


@dataclass
class OpenClawLocalRunner:
    gateway_ws_url: str
    timeout_ms: int = _DEFAULT_TIMEOUT_MS
    token: str | None = None
    password: str | None = None
    gateway_health_path: str = "/health"
    gateway_call_bin: str = "openclaw"

    def probe(self) -> ProbeResult:
        health_ok, health_reason = self._probe_health()
        if not health_ok:
            return ProbeResult.failed(health_reason or "gateway health 探测失败")

        method_ok, method_reason = self._probe_run_single_worker_method()
        if not method_ok:
            return ProbeResult.failed(method_reason or "agent.runSingleWorker 不可用")
        return ProbeResult.passed()

    def run_worker(self, payload: dict[str, object]) -> dict[str, object]:
        if not isinstance(payload, dict):
            raise TypeError("payload 必须是 dict")
        result = self._call_gateway(_METHOD_RUN_SINGLE_WORKER, {"command": payload})
        if isinstance(result, dict) and isinstance(result.get("payload"), dict):
            result = result["payload"]
        if not isinstance(result, dict):
            raise RuntimeError(f"gateway 返回值不是 object: {type(result).__name__}")
        return dict(result)

    def _probe_health(self) -> tuple[bool, str | None]:
        health_url = _health_url_from_ws(self.gateway_ws_url, self.gateway_health_path)
        try:
            req = request.Request(health_url, method="GET")
            with request.urlopen(req, timeout=max(self.timeout_ms / 1000.0, 0.1)) as resp:
                code = resp.getcode()
                if code >= 400:
                    return False, f"gateway health HTTP {code}"
            return True, None
        except error.URLError as exc:
            return False, f"gateway health 不可达: {exc}"
        except Exception as exc:
            return False, f"gateway health 探测失败: {exc}"

    def _probe_run_single_worker_method(self) -> tuple[bool, str | None]:
        probe_payload = {"command": {}}
        try:
            self._call_gateway(_METHOD_RUN_SINGLE_WORKER, probe_payload)
            return True, None
        except RuntimeError as exc:
            text = str(exc).lower()
            if _looks_like_method_missing_error(text):
                return False, "gateway 缺少 agent.runSingleWorker 方法"
            if _looks_like_auth_error(text):
                return False, f"gateway 鉴权失败: {exc}"
            if _looks_like_connection_error(text):
                return False, f"gateway 连接失败: {exc}"
            if _looks_like_cli_missing_error(text):
                return False, f"gateway CLI 不可用: {exc}"
            # 只把“runSingleWorker 参数校验失败”当作方法存在；其它错误都必须失败，防止假阳性。
            if _looks_like_run_single_worker_param_error(text):
                return True, None
            return False, f"gateway method 探测失败: {exc}"

    def _call_gateway(self, method: str, params: dict[str, object]) -> object:
        params_json = json.dumps(params, ensure_ascii=False)
        params_file: str | None = None
        command = [
            self.gateway_call_bin,
            "gateway",
            "call",
            method,
        ]
        if method == _METHOD_RUN_SINGLE_WORKER:
            command.extend(["--scope", "operator.read", "--scope", "operator.write"])
        # 默认本机地址且无显式凭证时，不传 --url，避免触发 OpenClaw 的 URL override 凭证门禁。
        if self.gateway_ws_url != _DEFAULT_GATEWAY_WS_URL or self.token or self.password:
            command.extend(["--url", self.gateway_ws_url])
        command.extend(["--timeout", str(self.timeout_ms)])
        if len(params_json.encode("utf-8")) > _GATEWAY_PARAMS_ARG_BYTE_LIMIT:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                prefix="openclaw-gateway-params-",
                suffix=".json",
                delete=False,
            ) as handle:
                handle.write(params_json)
                params_file = handle.name
            command.extend(["--params-file", params_file])
        else:
            command.extend(["--params", params_json])
        command.append("--json")
        if self.token:
            command.extend(["--token", self.token])
        elif self.password:
            command.extend(["--password", self.password])
        child_env: dict[str, str] | None = None
        if self.gateway_ws_url == _DEFAULT_GATEWAY_WS_URL and not self.token and not self.password:
            # 默认本机直连时移除子进程 URL 环境变量，避免 CLI 误判为 env override。
            child_env = dict(os.environ)
            child_env.pop("OPENCLAW_GATEWAY_URL", None)

        try:
            try:
                completed = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    check=False,
                    env=child_env,
                )
            except OSError as exc:
                raise RuntimeError(f"gateway CLI 执行失败: {exc}") from exc
            if completed.returncode != 0:
                detail = completed.stderr.strip() or completed.stdout.strip() or f"exit={completed.returncode}"
                if method == _METHOD_RUN_SINGLE_WORKER and _try_approve_local_scope_upgrade(
                    detail,
                    state_dir=_resolve_openclaw_state_dir(child_env),
                    requested_scopes=_RUN_SINGLE_WORKER_SCOPES,
                ):
                    completed = subprocess.run(
                        command,
                        capture_output=True,
                        text=True,
                        check=False,
                        env=child_env,
                    )
                    if completed.returncode == 0:
                        stdout = completed.stdout.strip()
                        if not stdout:
                            raise RuntimeError("gateway 返回空响应")
                        try:
                            return json.loads(stdout)
                        except json.JSONDecodeError as exc:
                            raise RuntimeError(f"gateway 返回非 JSON: {exc}") from exc
                    detail = completed.stderr.strip() or completed.stdout.strip() or f"exit={completed.returncode}"
                raise RuntimeError(detail)
            stdout = completed.stdout.strip()
            if not stdout:
                raise RuntimeError("gateway 返回空响应")
            try:
                return json.loads(stdout)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"gateway 返回非 JSON: {exc}") from exc
        finally:
            if params_file:
                Path(params_file).unlink(missing_ok=True)


def _normalize_gateway_ws_url(raw: str) -> str:
    parsed = parse.urlparse(raw)
    if parsed.scheme in {"ws", "wss"}:
        return raw
    if parsed.scheme in {"http", "https"}:
        ws_scheme = "wss" if parsed.scheme == "https" else "ws"
        host = parsed.netloc or parsed.path
        if not host:
            raise ValueError(f"OPENCLAW_GATEWAY_URL 非法: {raw}")
        return f"{ws_scheme}://{host}"
    if "://" not in raw:
        return f"ws://{raw}"
    raise ValueError(f"OPENCLAW_GATEWAY_URL 非法: {raw}")


def _health_url_from_ws(ws_url: str, health_path: str) -> str:
    parsed = parse.urlparse(ws_url)
    if parsed.scheme not in {"ws", "wss"}:
        raise ValueError(f"gateway URL 非法: {ws_url}")
    http_scheme = "https" if parsed.scheme == "wss" else "http"
    return parse.urlunparse((http_scheme, parsed.netloc, health_path, "", "", ""))


def _looks_like_method_missing_error(text: str) -> bool:
    return "method not found" in text or "unknown method" in text or "-32601" in text


def _looks_like_auth_error(text: str) -> bool:
    markers = (
        "unauthorized",
        "authentication",
        "invalid token",
        "forbidden",
        "permission denied",
        "401",
        "403",
        "access denied",
    )
    return any(marker in text for marker in markers)


def _looks_like_connection_error(text: str) -> bool:
    markers = (
        "connection refused",
        "failed to connect",
        "unable to connect",
        "network is unreachable",
        "name or service not known",
        "timed out",
        "timeout",
        "econnrefused",
        "enotfound",
    )
    return any(marker in text for marker in markers)


def _looks_like_cli_missing_error(text: str) -> bool:
    markers = (
        "no such file or directory",
        "executable file not found",
        "gateway cli",
        "is not recognized as an internal or external command",
    )
    return any(marker in text for marker in markers)


def _looks_like_run_single_worker_param_error(text: str) -> bool:
    if not any(marker in text for marker in ("invalid params", "invalid param", "-32602", "validation", "required property")):
        return False
    if "runsingleworker" in text:
        return True
    return "required property" in text and "command" in text


def _looks_like_pairing_required_error(text: str) -> bool:
    lowered = text.lower()
    return "pairing required" in lowered or "scope upgrade pending approval" in lowered


def _extract_pairing_request_id(text: str) -> str | None:
    match = _PAIRING_REQUEST_ID_RE.search(text)
    if match is None:
        return None
    return match.group("request_id").strip() or None


def _resolve_openclaw_state_dir(env: dict[str, str] | None) -> Path:
    raw = (env or os.environ).get("OPENCLAW_STATE_DIR", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return (Path.home() / ".openclaw").resolve()


def _openclaw_device_pairing_dist_module_path() -> Path:
    root = Path(__file__).resolve().parents[3]
    matches = sorted((root / "third_party" / "openclaw" / "dist").glob("device-pairing-*.js"))
    if not matches:
        raise RuntimeError("OpenClaw device-pairing dist module 不存在，请先构建 OpenClaw。")
    return matches[0]


def _try_approve_local_scope_upgrade(
    detail: str,
    *,
    state_dir: Path,
    requested_scopes: tuple[str, ...],
) -> bool:
    if not _looks_like_pairing_required_error(detail):
        return False
    request_id = _extract_pairing_request_id(detail)
    if not request_id:
        return False
    _approve_local_scope_upgrade_request(
        request_id=request_id,
        requested_scopes=requested_scopes,
        state_dir=state_dir,
    )
    return True


def _approve_local_scope_upgrade_request(
    *,
    request_id: str,
    requested_scopes: tuple[str, ...],
    state_dir: Path,
) -> None:
    module_path = _openclaw_device_pairing_dist_module_path()
    script = """
const mod = await import(process.argv[1]);
const approveDevicePairing = mod.approveDevicePairing ?? mod.n ?? mod.t;
if (typeof approveDevicePairing !== "function") {
  throw new Error("approveDevicePairing export not found");
}
const requestId = process.argv[2];
const stateDir = process.argv[3];
const callerScopes = JSON.parse(process.argv[4]);
const result = await approveDevicePairing(requestId, { callerScopes }, stateDir);
process.stdout.write(JSON.stringify(result ?? null));
"""
    completed = subprocess.run(
        [
            "node",
            "--input-type=module",
            "--eval",
            script,
            str(module_path),
            request_id,
            str(state_dir),
            json.dumps(list(requested_scopes), ensure_ascii=True),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or f"exit={completed.returncode}"
        raise RuntimeError(f"OpenClaw scope 自动批准失败: {detail}")
    try:
        payload = json.loads(completed.stdout.strip() or "null")
    except json.JSONDecodeError as exc:
        raise RuntimeError("OpenClaw scope 自动批准返回非 JSON") from exc
    if payload is None:
        return
    if not isinstance(payload, dict) or payload.get("status") != "approved":
        raise RuntimeError(f"OpenClaw scope 自动批准被拒绝: {payload}")
