from __future__ import annotations

import json
import logging
import os
import selectors
import subprocess
import tempfile
import threading
import uuid
from copy import deepcopy
from pathlib import Path
from time import perf_counter, sleep
from typing import Any, Mapping

_DEFAULT_GATEWAY_WS_URL = "ws://127.0.0.1:18789"
_PARAMS_ARG_BYTE_LIMIT = 60_000
_SUBPROCESS_TIMEOUT_GRACE_SECONDS = 5.0
_CLI_JSON_TIMEOUT_SECONDS = 15.0
_CLI_PROBE_TIMEOUT_SECONDS = 35.0
_UI_CHAT_AGENT_ID = "ui_chat"
_UI_CHAT_SESSION_VERSION = "v2"
_UI_CHAT_LANE = "ui-interactive"
_UI_CHAT_START_TIMEOUT_MS = 60_000
_UI_CHAT_WAIT_TIMEOUT_MS = 60_000
_UI_CHAT_HISTORY_TIMEOUT_MS = 15_000
_CHANNEL_TEXT_SEND_TIMEOUT_MS = 15_000
_CHANNEL_FILE_SEND_TIMEOUT_MS = 180_000
_CHANNEL_STATUS_LIGHT_CACHE_TTL_SECONDS = 30.0
_CHANNEL_STATUS_PROBE_CACHE_TTL_SECONDS = 15.0
_CHAT_GATEWAY_METHODS = frozenset({"sessions.create", "agent", "chat.send", "agent.wait", "chat.history"})
_BACKGROUND_GATEWAY_METHODS = frozenset({"channels.status"})
def _resolve_gateway_rpc_helper_script() -> Path:
    env_path = os.environ.get("OPENCLAW_GATEWAY_RPC_HELPER_SCRIPT", "").strip()
    if env_path:
        return Path(env_path)
    current = Path(__file__).resolve()
    for parent in current.parents:
        candidate = parent / "scripts" / "openclaw-gateway-rpc-helper.mjs"
        if candidate.exists():
            return candidate
    return current.parents[3] / "scripts" / "openclaw-gateway-rpc-helper.mjs"


_GATEWAY_RPC_HELPER_SCRIPT = _resolve_gateway_rpc_helper_script()
_LOGGER = logging.getLogger("uvicorn.error")


class _GatewayRpcHelperProcess:
    def __init__(
        self,
        *,
        gateway_ws_url: str,
        timeout_ms: int,
        token: str | None,
        password: str | None,
        helper_script: Path = _GATEWAY_RPC_HELPER_SCRIPT,
    ) -> None:
        self._gateway_ws_url = gateway_ws_url
        self._timeout_ms = timeout_ms
        self._token = token
        self._password = password
        self._helper_script = helper_script
        self._lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None

    def start(self) -> None:
        with self._lock:
            self._ensure_process_locked()

    def call(
        self,
        method: str,
        params: Mapping[str, Any] | None,
        *,
        expect_final: bool,
        timeout_ms: int | None,
    ) -> Any:
        effective_timeout_ms = timeout_ms or self._timeout_ms
        with self._lock:
            process = self._ensure_process_locked()
            request_id = f"claw-trade-ui-{uuid.uuid4()}"
            request = {
                "id": request_id,
                "method": method,
                "params": dict(params or {}),
                "url": self._gateway_ws_url,
                "token": self._token,
                "password": self._password,
                "expectFinal": expect_final,
                "timeoutMs": effective_timeout_ms,
            }
            try:
                assert process.stdin is not None
                process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
                process.stdin.flush()
                assert process.stdout is not None
                response_line = _readline_with_timeout(process.stdout, effective_timeout_ms)
            except (BrokenPipeError, OSError) as exc:
                self._close_locked()
                raise RuntimeError("gateway helper unavailable") from exc
            if not response_line:
                self._close_locked()
                raise RuntimeError("gateway helper unavailable")
            try:
                response = json.loads(response_line)
            except json.JSONDecodeError as exc:
                self._close_locked()
                raise RuntimeError(f"gateway helper returned invalid JSON: {exc}") from exc
            if not isinstance(response, Mapping) or response.get("id") != request_id:
                self._close_locked()
                raise RuntimeError("gateway helper returned invalid response")
            if response.get("ok") is True:
                return response.get("result")
            error = response.get("error")
            if isinstance(error, Mapping):
                message = error.get("message")
                if isinstance(message, str) and message.strip():
                    raise RuntimeError(message.strip())
            raise RuntimeError("assistant_unavailable")

    def _ensure_process_locked(self) -> subprocess.Popen[str]:
        if self._process is not None and self._process.poll() is None:
            return self._process
        self._close_locked()
        env = dict(os.environ)
        env["OPENCLAW_GATEWAY_URL"] = self._gateway_ws_url
        env["OPENCLAW_GATEWAY_TIMEOUT_MS"] = str(self._timeout_ms)
        if self._token:
            env["OPENCLAW_GATEWAY_TOKEN"] = self._token
        if self._password:
            env["OPENCLAW_GATEWAY_PASSWORD"] = self._password
        self._process = subprocess.Popen(
            ["node", str(self._helper_script)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            env=env,
        )
        return self._process

    def _close_locked(self) -> None:
        process = self._process
        self._process = None
        if process is None:
            return
        try:
            process.terminate()
        except OSError:
            pass


class OpenClawGatewayRpcClient:
    supports_channel_capabilities = False

    def __init__(
        self,
        *,
        gateway_call_bin: str,
        gateway_ws_url: str,
        timeout_ms: int,
        token: str | None,
        password: str | None,
    ) -> None:
        self._gateway_call_bin = gateway_call_bin
        self._gateway_ws_url = gateway_ws_url
        self._timeout_ms = timeout_ms
        self._token = token
        self._password = password
        self._session_key_by_context: dict[str, str] = {}
        self._channel_status_cache_lock = threading.Lock()
        self._channel_status_cache: dict[bool, tuple[float, Any]] = {}
        self._chat_helper = _GatewayRpcHelperProcess(
            gateway_ws_url=gateway_ws_url,
            timeout_ms=timeout_ms,
            token=token,
            password=password,
        )
        self._background_helper = _GatewayRpcHelperProcess(
            gateway_ws_url=gateway_ws_url,
            timeout_ms=timeout_ms,
            token=token,
            password=password,
        )

    def prewarm_ui_chat(self) -> None:
        try:
            self._ensure_session_key("normal-chat", timeout_ms=self._ui_chat_start_timeout_ms())
        except RuntimeError:
            self._chat_helper.start()

    def chat_send(self, *, context_id: str, text: str, request_id: str) -> dict[str, Any]:
        session_key = self._ensure_session_key(context_id, timeout_ms=self._ui_chat_start_timeout_ms())
        return {
            "text": self._send_agent_and_read_reply(
                agent_id=_UI_CHAT_AGENT_ID,
                session_key=session_key,
                text=text,
                request_id=request_id,
                start_timeout_ms=self._ui_chat_start_timeout_ms(),
                wait_timeout_ms=self._ui_chat_wait_timeout_ms(),
                history_timeout_ms=self._ui_chat_history_timeout_ms(),
            )
        }

    def report_qa_chat_send(
        self,
        *,
        request_id: str,
        context_id: str,
        session_id: str,
        text: str,
    ) -> dict[str, Any]:
        _ = context_id
        return {"text": self._send_chat_and_read_reply(session_key=session_id, text=text, request_id=request_id)}

    def worker_chat_send(
        self,
        *,
        session_key: str,
        message: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        if session_key.startswith("agent:ui_worker_chat:generic:"):
            return {
                "text": self._send_agent_and_read_reply(
                    agent_id="ui_worker_chat",
                    session_key=session_key,
                    text=message,
                    request_id=idempotency_key,
                    start_timeout_ms=self._ui_chat_start_timeout_ms(),
                    wait_timeout_ms=self._ui_chat_wait_timeout_ms(),
                    history_timeout_ms=self._ui_chat_history_timeout_ms(),
                )
            }
        params: dict[str, Any] = {
            "sessionKey": session_key,
            "message": message,
            "deliver": False,
            "idempotencyKey": idempotency_key,
        }
        return {"text": self._send_chat_params_and_read_reply(session_key=session_key, text=message, params=params)}

    def _send_chat_and_read_reply(
        self,
        *,
        session_key: str,
        text: str,
        request_id: str,
        start_timeout_ms: int | None = None,
        wait_timeout_ms: int | None = None,
        history_timeout_ms: int | None = None,
    ) -> str:
        return self._send_chat_params_and_read_reply(
            session_key=session_key,
            text=text,
            params={
                "sessionKey": session_key,
                "message": text,
                "deliver": False,
                "idempotencyKey": request_id,
                "lane": _UI_CHAT_LANE,
            },
            start_timeout_ms=start_timeout_ms,
            wait_timeout_ms=wait_timeout_ms,
            history_timeout_ms=history_timeout_ms,
        )

    def _send_chat_params_and_read_reply(
        self,
        *,
        session_key: str,
        text: str,
        params: Mapping[str, Any],
        start_timeout_ms: int | None = None,
        wait_timeout_ms: int | None = None,
        history_timeout_ms: int | None = None,
    ) -> str:
        total_started = perf_counter()
        chat_send_started = perf_counter()
        effective_wait_timeout_ms = wait_timeout_ms or self._timeout_ms
        payload = self._call(
            "chat.send",
            params,
            expect_final=True,
            timeout_ms=effective_wait_timeout_ms,
        )
        chat_send_ms = round((perf_counter() - chat_send_started) * 1000)
        try:
            reply = _extract_text(payload)
            _LOGGER.info(
                "openclaw ui chat timing session_key=%s run_id=%s chat_send_ms=%s wait_ms=0 history_ms=0 total_ms=%s",
                session_key,
                _payload_str(payload, "runId") or "-",
                chat_send_ms,
                round((perf_counter() - total_started) * 1000),
            )
            return reply
        except RuntimeError:
            pass
        run_id = _payload_str(payload, "runId")
        if not run_id:
            raise RuntimeError("assistant_unavailable")
        wait_started = perf_counter()
        wait_payload = self._call(
            "agent.wait",
            {"runId": run_id, "timeoutMs": effective_wait_timeout_ms},
            timeout_ms=effective_wait_timeout_ms,
        )
        wait_ms = round((perf_counter() - wait_started) * 1000)
        status = _payload_str(wait_payload, "status").lower()
        if status not in {"ok", "succeeded", "completed"}:
            raise RuntimeError("assistant_unavailable")
        history_started = perf_counter()
        history = self._call(
            "chat.history",
            {"sessionKey": session_key, "limit": 20},
            timeout_ms=history_timeout_ms or min(self._timeout_ms, 30_000),
        )
        history_ms = round((perf_counter() - history_started) * 1000)
        _LOGGER.info(
            "openclaw ui chat timing session_key=%s run_id=%s chat_send_ms=%s wait_ms=%s history_ms=%s total_ms=%s",
            session_key,
            run_id,
            chat_send_ms,
            wait_ms,
            history_ms,
            round((perf_counter() - total_started) * 1000),
        )
        return _extract_reply_after_user(history, text)

    def _send_agent_and_read_reply(
        self,
        *,
        agent_id: str,
        session_key: str,
        text: str,
        request_id: str,
        start_timeout_ms: int | None = None,
        wait_timeout_ms: int | None = None,
        history_timeout_ms: int | None = None,
    ) -> str:
        total_started = perf_counter()
        effective_wait_timeout_ms = wait_timeout_ms or self._timeout_ms
        params = {
            "sessionKey": session_key,
            "agentId": agent_id,
            "message": text,
            "deliver": False,
            "idempotencyKey": request_id,
            "lane": _UI_CHAT_LANE,
            "waitForCompletion": True,
        }
        done_event = threading.Event()
        holder: dict[str, Any] = {}

        def call_agent() -> None:
            agent_started = perf_counter()
            try:
                holder["payload"] = self._call(
                    "agent",
                    params,
                    expect_final=True,
                    timeout_ms=effective_wait_timeout_ms,
                )
            except Exception as exc:  # pragma: no cover - surfaced below unless state already won
                holder["error"] = exc
            finally:
                holder["agent_ms"] = round((perf_counter() - agent_started) * 1000)
                done_event.set()

        threading.Thread(target=call_agent, name="openclaw-ui-agent-chat", daemon=True).start()
        run_id = request_id
        state_started = perf_counter()
        state_reply = _read_reply_from_agent_state(
            agent_id=agent_id,
            run_id=run_id,
            user_text=text,
            timeout_ms=effective_wait_timeout_ms,
            done_event=done_event,
        )
        if state_reply:
            _LOGGER.info(
                "openclaw ui agent chat timing session_key=%s run_id=%s agent_ms=%s state_ms=%s total_ms=%s mode=state-first",
                session_key,
                run_id,
                holder.get("agent_ms", "in_flight"),
                round((perf_counter() - state_started) * 1000),
                round((perf_counter() - total_started) * 1000),
            )
            return state_reply
        if not done_event.wait(max(0.001, effective_wait_timeout_ms / 1000)):
            raise RuntimeError("assistant_unavailable")
        if isinstance(holder.get("error"), Exception):
            raise holder["error"]
        payload = holder.get("payload")
        run_id = _payload_str(payload, "runId") or request_id
        _LOGGER.info(
            "openclaw ui agent chat timing session_key=%s run_id=%s agent_ms=%s state_ms=%s total_ms=%s mode=fallback",
            session_key,
            run_id,
            holder.get("agent_ms", "-"),
            round((perf_counter() - state_started) * 1000),
            round((perf_counter() - total_started) * 1000),
        )
        return _extract_text(payload)

    def sessions_create(self, *, metadata: dict[str, object], timeout_ms: int | None = None) -> str:
        key = metadata.get("sessionKey")
        if not isinstance(key, str) or not key.strip():
            key = f"ui:{metadata.get('scope') or 'chat'}"
        key = key.strip()
        label = metadata.get("label")
        if not isinstance(label, str) or not label.strip():
            label = key
        try:
            params: dict[str, Any] = {
                "key": key,
                "label": label.strip(),
            }
            agent_id = metadata.get("agentId")
            if isinstance(agent_id, str) and agent_id.strip():
                params["agentId"] = agent_id.strip()
            payload = self._call(
                "sessions.create",
                params,
                timeout_ms=timeout_ms,
            )
        except RuntimeError as exc:
            if _session_already_exists(str(exc)):
                return key
            raise
        if isinstance(payload, Mapping):
            for candidate in ("key", "sessionKey"):
                value = payload.get(candidate)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        raise RuntimeError("assistant_unavailable")

    def config_schema_lookup(self, *, path: str) -> Any:
        return self._call("config.schema.lookup", {"path": path}, timeout_ms=8000)

    def config_get(self, *, paths: tuple[str, ...]) -> dict[str, Any]:
        _ = paths
        payload = self._call("config.get", {}, timeout_ms=8000)
        if not isinstance(payload, Mapping):
            raise RuntimeError("assistant_unavailable")
        if isinstance(payload.get("config"), Mapping):
            out = dict(payload["config"])
            if "revision" not in out:
                out["revision"] = payload.get("hash") or payload.get("revision")
            return out
        out = dict(payload)
        if "revision" not in out:
            out["revision"] = payload.get("hash")
        return out

    def models_list(self, *, provider: str | None = None) -> Any:
        params: dict[str, Any] = {"view": "configured"}
        if provider:
            params["provider"] = provider
        return self._call("models.list", params, timeout_ms=8000)

    def models_status(self, *, provider: str | None = None, as_json: bool = True) -> Any:
        params: dict[str, Any] = {"probe": False, "json": as_json}
        if provider:
            params["provider"] = provider
        return self._call("models.authStatus", params, timeout_ms=8000)

    def models_auth_status(
        self,
        *,
        provider: str,
        model: str | None = None,
        endpoint_url: str | None = None,
        probe: bool = True,
    ) -> Any:
        params: dict[str, Any] = {"provider": provider, "probe": probe}
        if model:
            params["model"] = model
        if endpoint_url:
            params["endpointUrl"] = endpoint_url
        return self._call("models.authStatus", params, timeout_ms=20_000)

    def models_probe_status(
        self,
        *,
        provider: str,
        model: str | None = None,
        endpoint_url: str | None = None,
    ) -> Any:
        _ = (model, endpoint_url)
        return self._run_cli_json(
            ["models", "status", "--json", "--probe", "--probe-provider", provider],
            timeout_seconds=_CLI_PROBE_TIMEOUT_SECONDS,
        )

    def config_patch(
        self,
        *,
        expected_settings_version: str | None = None,
        patch: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "raw": json.dumps(dict(patch or {}), ensure_ascii=False, indent=2),
        }
        if expected_settings_version and not str(expected_settings_version).startswith("v_"):
            params["baseHash"] = expected_settings_version
        else:
            base_hash = self._current_config_hash()
            if base_hash:
                params["baseHash"] = base_hash
        self._clear_channels_status_cache()
        payload = self._call("config.patch", params, timeout_ms=15_000)
        self._clear_channels_status_cache()
        if isinstance(payload, Mapping):
            return dict(payload)
        return {"newHash": None}

    def plugins_list(self) -> Any:
        payload = self._run_cli_json(["plugins", "list", "--json"])
        if isinstance(payload, Mapping):
            if isinstance(payload.get("plugins"), list):
                return payload["plugins"]
            if isinstance(payload.get("items"), list):
                return payload["items"]
        if isinstance(payload, list):
            return payload
        return payload

    def channels_status(self, *, probe: bool = False) -> Any:
        ttl_seconds = _CHANNEL_STATUS_PROBE_CACHE_TTL_SECONDS if probe else _CHANNEL_STATUS_LIGHT_CACHE_TTL_SECONDS
        cache_key = bool(probe)
        with self._channel_status_cache_lock:
            cached = self._channel_status_cache.get(cache_key)
            now = perf_counter()
            if cached is not None:
                cached_at, payload = cached
                if now - cached_at <= ttl_seconds:
                    return deepcopy(payload)
                self._channel_status_cache.pop(cache_key, None)
            payload = self._call(
                "channels.status",
                {"probe": probe, "timeoutMs": 8000 if probe else 3000},
                timeout_ms=10_000 if probe else 5000,
            )
            self._channel_status_cache[cache_key] = (perf_counter(), deepcopy(payload))
            return deepcopy(payload)

    def channels_capabilities(self, *, channel: str) -> Any:
        return self._call("channels.capabilities", {"channel": channel}, timeout_ms=8000)

    def web_login_start(self, *, force: bool = False, timeout_ms: int = 12_000) -> Any:
        self._clear_channels_status_cache()
        try:
            return self._call(
                "web.login.start",
                {
                    "force": force,
                    "timeoutMs": timeout_ms,
                },
                timeout_ms=timeout_ms + 3000,
            )
        finally:
            self._clear_channels_status_cache()

    def web_login_wait(
        self,
        *,
        timeout_ms: int = 1_500,
        current_qr_data_url: str | None = None,
        session_key: str | None = None,
    ) -> Any:
        params: dict[str, Any] = {"timeoutMs": timeout_ms}
        if current_qr_data_url:
            params["currentQrDataUrl"] = current_qr_data_url
        if session_key:
            params["sessionKey"] = session_key
        try:
            return self._call("web.login.wait", params, timeout_ms=max(timeout_ms + 3000, 45_000))
        finally:
            self._clear_channels_status_cache()

    def _clear_channels_status_cache(self) -> None:
        with self._channel_status_cache_lock:
            self._channel_status_cache.clear()

    def channels_send_text(
        self,
        *,
        channel: str,
        text: str,
        dedupe_key: str,
        to: str,
        account_id: str | None = None,
    ) -> Any:
        params: dict[str, Any] = {
            "channel": channel,
            "to": to,
            "message": text,
            "idempotencyKey": dedupe_key,
        }
        if account_id:
            params["accountId"] = account_id
        return self._call("send", params, timeout_ms=self._channel_text_send_timeout_ms())

    def channels_send_file(
        self,
        *,
        channel: str,
        file_name: str,
        dedupe_key: str,
        to: str,
        payload: bytes | None = None,
        file_path: Path | str | None = None,
        account_id: str | None = None,
    ) -> Any:
        temp_media_path: Path | None = None
        if file_path is not None:
            media_path = Path(file_path)
        elif payload is not None:
            suffix = Path(file_name).suffix or ".bin"
            with tempfile.NamedTemporaryFile("wb", delete=False, prefix="openclaw-ui-file-", suffix=suffix) as handle:
                handle.write(payload)
                media_path = Path(handle.name)
                temp_media_path = media_path
        else:
            raise RuntimeError("file payload unavailable")
        params: dict[str, Any] = {
            "channel": channel,
            "message": file_name,
            "mediaUrl": str(media_path),
            "idempotencyKey": dedupe_key,
            "to": to,
        }
        if account_id:
            params["accountId"] = account_id
        try:
            return self._call("send", params, timeout_ms=self._channel_file_send_timeout_ms())
        finally:
            if temp_media_path is not None:
                temp_media_path.unlink(missing_ok=True)

    def cron_add(self, params: Mapping[str, Any]) -> Any:
        return self._call("cron.add", params, timeout_ms=15_000)

    def cron_update(self, params: Mapping[str, Any]) -> Any:
        raw = dict(params)
        job_id = str(raw.pop("id", raw.pop("jobId", ""))).strip()
        patch = raw.pop("patch", raw)
        return self._call("cron.update", {"id": job_id, "patch": dict(patch)}, timeout_ms=15_000)

    def cron_remove(self, *, job_id: str) -> Any:
        return self._call("cron.remove", {"id": job_id}, timeout_ms=15_000)

    def cron_run(self, *, job_id: str, idempotency_key: str | None = None) -> Any:
        _ = idempotency_key
        params: dict[str, Any] = {"id": job_id, "mode": "force"}
        return self._call("cron.run", params, timeout_ms=30_000)

    def cron_list(self, params: Mapping[str, Any] | None = None) -> Any:
        return self._call("cron.list", params or {}, timeout_ms=15_000)

    def cron_status(self, *, job_id: str) -> Any:
        _ = job_id
        return self._call("cron.status", {}, timeout_ms=15_000)

    def cron_runs(self, *, job_id: str, limit: int | None = None) -> Any:
        params: dict[str, Any] = {"id": job_id}
        if limit is not None:
            params["limit"] = limit
        return self._call("cron.runs", params, timeout_ms=15_000)

    def _current_config_hash(self) -> str | None:
        payload = self._call("config.get", {}, timeout_ms=8000)
        if not isinstance(payload, Mapping):
            return None
        for key in ("hash", "revision"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    def _ensure_session_key(self, context_id: str, *, timeout_ms: int | None = None) -> str:
        existing = self._session_key_by_context.get(context_id)
        if existing:
            return existing
        session_key = f"agent:{_UI_CHAT_AGENT_ID}:{_UI_CHAT_SESSION_VERSION}:ui:{context_id}"
        created = self.sessions_create(
            metadata={
                "scope": "ui_chat",
                "agentId": _UI_CHAT_AGENT_ID,
                "sessionKey": session_key,
                "label": f"ui:{_UI_CHAT_SESSION_VERSION}:{context_id}",
            },
            timeout_ms=timeout_ms,
        )
        self._session_key_by_context[context_id] = created
        return created

    def _ui_chat_start_timeout_ms(self) -> int:
        return min(self._timeout_ms, _UI_CHAT_START_TIMEOUT_MS)

    def _ui_chat_wait_timeout_ms(self) -> int:
        return min(self._timeout_ms, _UI_CHAT_WAIT_TIMEOUT_MS)

    def _ui_chat_history_timeout_ms(self) -> int:
        return min(self._timeout_ms, _UI_CHAT_HISTORY_TIMEOUT_MS)

    def _channel_text_send_timeout_ms(self) -> int:
        return min(self._timeout_ms, _CHANNEL_TEXT_SEND_TIMEOUT_MS)

    def _channel_file_send_timeout_ms(self) -> int:
        return min(self._timeout_ms, _CHANNEL_FILE_SEND_TIMEOUT_MS)

    def _call(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
        *,
        expect_final: bool = False,
        timeout_ms: int | None = None,
    ) -> Any:
        effective_timeout_ms = timeout_ms or self._timeout_ms
        if method in _CHAT_GATEWAY_METHODS:
            return self._chat_helper.call(
                method,
                params,
                expect_final=expect_final,
                timeout_ms=effective_timeout_ms,
            )
        if method in _BACKGROUND_GATEWAY_METHODS:
            return self._background_helper.call(
                method,
                params,
                expect_final=expect_final,
                timeout_ms=effective_timeout_ms,
            )
        params_text = json.dumps(params or {}, ensure_ascii=False)
        params_file: str | None = None
        command: list[str] = [self._gateway_call_bin, "gateway", "call", method]
        if self._gateway_ws_url != _DEFAULT_GATEWAY_WS_URL:
            command.extend(["--url", self._gateway_ws_url])
        command.extend(["--timeout", str(effective_timeout_ms)])
        if expect_final:
            command.append("--expect-final")
        if len(params_text.encode("utf-8")) > _PARAMS_ARG_BYTE_LIMIT:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                delete=False,
                prefix="openclaw-ui-params-",
                suffix=".json",
            ) as handle:
                handle.write(params_text)
                params_file = handle.name
            command.extend(["--params-file", params_file])
        else:
            command.extend(["--params", params_text])
        command.append("--json")
        if self._token:
            command.extend(["--token", self._token])
        elif self._password:
            command.extend(["--password", self._password])

        child_env: dict[str, str] | None = None
        if self._gateway_ws_url == _DEFAULT_GATEWAY_WS_URL and not self._token and not self._password:
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
                    timeout=_subprocess_timeout_seconds(effective_timeout_ms),
                )
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(f"gateway cli timeout after {effective_timeout_ms}ms") from exc
            if completed.returncode != 0:
                detail = completed.stderr.strip() or completed.stdout.strip() or f"exit={completed.returncode}"
                raise RuntimeError(detail)
            output = completed.stdout.strip()
            if not output:
                raise RuntimeError("assistant_unavailable")
            try:
                parsed = json.loads(output)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"assistant_unavailable: {exc}") from exc
            return _unwrap_payload(parsed)
        finally:
            if params_file:
                Path(params_file).unlink(missing_ok=True)

    def _run_cli_json(self, args: list[str], *, timeout_seconds: float = _CLI_JSON_TIMEOUT_SECONDS) -> Any:
        command: list[str] = [self._gateway_call_bin, *args]
        child_env: dict[str, str] | None = None
        if self._gateway_ws_url == _DEFAULT_GATEWAY_WS_URL and not self._token and not self._password:
            child_env = dict(os.environ)
            child_env.pop("OPENCLAW_GATEWAY_URL", None)
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
                env=child_env,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("gateway cli timeout while reading OpenClaw JSON") from exc
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip() or f"exit={completed.returncode}"
            raise RuntimeError(detail)
        output = completed.stdout.strip()
        if not output:
            raise RuntimeError("assistant_unavailable")
        try:
            parsed = json.loads(output)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"assistant_unavailable: {exc}") from exc
        return _unwrap_payload(parsed)


def _subprocess_timeout_seconds(timeout_ms: int) -> float:
    return max(1.0, timeout_ms / 1000.0 + _SUBPROCESS_TIMEOUT_GRACE_SECONDS)


def _readline_with_timeout(stream: Any, timeout_ms: int) -> str:
    selector = selectors.DefaultSelector()
    try:
        selector.register(stream, selectors.EVENT_READ)
        events = selector.select(max(0.001, timeout_ms / 1000.0))
        if not events:
            raise RuntimeError(f"gateway helper timeout after {timeout_ms}ms")
        return stream.readline()
    finally:
        selector.close()


def _unwrap_payload(raw: Any) -> Any:
    if isinstance(raw, Mapping):
        if "result" in raw:
            return raw["result"]
        if "payload" in raw and ("ok" in raw or "error" in raw):
            return raw["payload"]
    return raw


def _payload_str(payload: Any, key: str) -> str:
    if isinstance(payload, Mapping):
        value = payload.get(key)
        if isinstance(value, str):
            return value.strip()
    return ""


def _session_already_exists(message: str) -> bool:
    lowered = message.lower()
    return "already in use" in lowered or "already exists" in lowered


def _extract_text(payload: Any) -> str:
    if isinstance(payload, str):
        text = payload.strip()
        if text:
            return text
    if isinstance(payload, Mapping):
        for key in ("text", "content", "reply", "message"):
            value = payload.get(key)
            text = _content_text(value)
            if text:
                return text
        messages = payload.get("messages")
        if isinstance(messages, list):
            for item in reversed(messages):
                if not isinstance(item, Mapping):
                    continue
                role = str(item.get("role") or item.get("actor") or "").strip().lower()
                if role and role not in {"assistant", "system"}:
                    continue
                text = _message_text(item)
                if text:
                    return text
    raise RuntimeError("assistant_unavailable")


def _extract_reply_after_user(payload: Any, user_text: str) -> str:
    reply = _try_extract_reply_after_user(payload, user_text)
    if reply:
        return reply
    return _extract_text(payload)


def _try_extract_reply_after_user(payload: Any, user_text: str) -> str | None:
    if isinstance(payload, Mapping):
        messages = payload.get("messages")
        if isinstance(messages, list):
            start_index = -1
            for index, item in enumerate(messages):
                if not isinstance(item, Mapping):
                    continue
                role = str(item.get("role") or item.get("actor") or "").strip().lower()
                if role == "user" and _message_text(item) == user_text:
                    start_index = index
            if start_index >= 0:
                reply = ""
                for item in messages[start_index + 1 :]:
                    if not isinstance(item, Mapping):
                        continue
                    role = str(item.get("role") or item.get("actor") or "").strip().lower()
                    if role != "assistant":
                        continue
                    text = _message_text(item)
                    if text:
                        reply = text
                if reply:
                    return reply
    return None


def _read_reply_from_agent_state(
    *,
    agent_id: str,
    run_id: str,
    user_text: str,
    timeout_ms: int,
    done_event: threading.Event | None = None,
) -> str | None:
    state_dir = _openclaw_state_dir()
    if state_dir is None:
        return None
    sessions_dir = state_dir / "agents" / agent_id / "sessions"
    if not sessions_dir.is_dir():
        return None
    trajectory_deadline = perf_counter() + timeout_ms / 1000
    session_log: Path | None = None
    while perf_counter() < trajectory_deadline:
        session_log = _find_agent_session_log(sessions_dir=sessions_dir, run_id=run_id)
        if session_log is not None:
            break
        if done_event is not None and done_event.is_set():
            break
        sleep(0.05)
    if session_log is None:
        return None
    deadline = perf_counter() + timeout_ms / 1000
    while perf_counter() < deadline:
        reply = _session_log_reply_after_user(session_log, user_text)
        if reply:
            return reply
        if done_event is not None and done_event.is_set():
            break
        sleep(0.1)
    return None


def _find_agent_session_log(*, sessions_dir: Path, run_id: str) -> Path | None:
    paths = sorted(sessions_dir.glob("*.trajectory.jsonl"), key=_path_mtime, reverse=True)
    needle = f'"runId":"{run_id}"'
    for path in paths:
        try:
            if needle not in path.read_text(encoding="utf-8", errors="ignore"):
                continue
        except OSError:
            continue
        return path.with_name(path.name[: -len(".trajectory.jsonl")] + ".jsonl")
    return None


def _session_log_reply_after_user(path: Path, user_text: str) -> str | None:
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return None
    start_seen = False
    reply = ""
    for raw in lines:
        try:
            record = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, Mapping) or record.get("type") != "message":
            continue
        message = record.get("message")
        if not isinstance(message, Mapping):
            continue
        role = str(message.get("role") or "").strip().lower()
        text = _message_text(message)
        if role == "user" and _user_text_matches(text, user_text):
            start_seen = True
            reply = ""
            continue
        if start_seen and role == "assistant" and text:
            reply = text
    return reply or None


def _user_text_matches(actual: str, expected: str) -> bool:
    return actual == expected or actual.endswith(expected)


def _openclaw_state_dir() -> Path | None:
    raw = os.environ.get("OPENCLAW_STATE_DIR", "").strip() or _runtime_env_value("OPENCLAW_STATE_DIR")
    if raw:
        return Path(raw)
    default = Path(".runtime/dev-services/openclaw-state")
    return default if default.is_dir() else None


def _runtime_env_value(key: str) -> str:
    path = Path(".runtime/dev-services/runtime.env")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    prefix = f"{key}="
    for raw in lines:
        if raw.startswith(prefix):
            return raw[len(prefix) :].strip().strip("'\"")
    return ""


def _path_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _message_text(message: Mapping[str, Any]) -> str:
    for key in ("text", "content", "reply", "message"):
        text = _content_text(message.get(key))
        if text:
            return text
    return ""


def _content_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts = [_content_text(item) for item in value]
        return "\n".join(part for part in parts if part).strip()
    if isinstance(value, Mapping):
        for key in ("text", "content", "reply"):
            text = _content_text(value.get(key))
            if text:
                return text
        return ""
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str) and item.strip():
                parts.append(item.strip())
            elif isinstance(item, Mapping):
                for key in ("text", "content"):
                    text = _content_text(item.get(key))
                    if text:
                        parts.append(text)
                        break
        return "\n".join(parts).strip()
    return ""
