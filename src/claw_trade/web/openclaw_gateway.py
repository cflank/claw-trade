from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Mapping

_DEFAULT_GATEWAY_WS_URL = "ws://127.0.0.1:18789"
_PARAMS_ARG_BYTE_LIMIT = 60_000
_SUBPROCESS_TIMEOUT_GRACE_SECONDS = 5.0
_CLI_JSON_TIMEOUT_SECONDS = 15.0
_CLI_PROBE_TIMEOUT_SECONDS = 35.0


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

    def chat_send(self, *, context_id: str, text: str, request_id: str) -> dict[str, Any]:
        session_key = self._ensure_session_key(context_id)
        return {"text": self._send_chat_and_read_reply(session_key=session_key, text=text, request_id=request_id)}

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
        params: dict[str, Any] = {
            "sessionKey": session_key,
            "message": message,
            "deliver": False,
            "idempotencyKey": idempotency_key,
        }
        return {"text": self._send_chat_params_and_read_reply(session_key=session_key, text=message, params=params)}

    def _send_chat_and_read_reply(self, *, session_key: str, text: str, request_id: str) -> str:
        return self._send_chat_params_and_read_reply(
            session_key=session_key,
            text=text,
            params={
                "sessionKey": session_key,
                "message": text,
                "deliver": False,
                "idempotencyKey": request_id,
            },
        )

    def _send_chat_params_and_read_reply(self, *, session_key: str, text: str, params: Mapping[str, Any]) -> str:
        payload = self._call(
            "chat.send",
            params,
            expect_final=True,
        )
        try:
            return _extract_text(payload)
        except RuntimeError:
            pass
        run_id = _payload_str(payload, "runId")
        if not run_id:
            raise RuntimeError("assistant_unavailable")
        wait_payload = self._call(
            "agent.wait",
            {"runId": run_id, "timeoutMs": self._timeout_ms},
            timeout_ms=self._timeout_ms,
        )
        status = _payload_str(wait_payload, "status").lower()
        if status not in {"ok", "succeeded", "completed"}:
            raise RuntimeError("assistant_unavailable")
        history = self._call(
            "chat.history",
            {"sessionKey": session_key, "limit": 20},
            timeout_ms=min(self._timeout_ms, 30_000),
        )
        return _extract_reply_after_user(history, text)

    def sessions_create(self, *, metadata: dict[str, object]) -> str:
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
        base_hash = self._current_config_hash()
        if base_hash:
            params["baseHash"] = base_hash
        if expected_settings_version and not str(expected_settings_version).startswith("v_"):
            params["baseHash"] = expected_settings_version
        payload = self._call("config.patch", params, timeout_ms=15_000)
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
        return self._call(
            "channels.status",
            {"probe": probe, "timeoutMs": 8000 if probe else 3000},
            timeout_ms=10_000 if probe else 5000,
        )

    def channels_capabilities(self, *, channel: str) -> Any:
        return self._call("channels.capabilities", {"channel": channel}, timeout_ms=8000)

    def web_login_start(self, *, force: bool = False, timeout_ms: int = 12_000) -> Any:
        return self._call(
            "web.login.start",
            {
                "force": force,
                "timeoutMs": timeout_ms,
            },
            timeout_ms=timeout_ms + 3000,
        )

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
        return self._call("web.login.wait", params, timeout_ms=max(timeout_ms + 3000, 45_000))

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
        return self._call("send", params)

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
            "to": to,
            "message": file_name,
            "mediaUrl": str(media_path),
            "idempotencyKey": dedupe_key,
        }
        if account_id:
            params["accountId"] = account_id
        try:
            return self._call("send", params)
        finally:
            if temp_media_path is not None:
                temp_media_path.unlink(missing_ok=True)

    def _current_config_hash(self) -> str | None:
        payload = self._call("config.get", {}, timeout_ms=8000)
        if not isinstance(payload, Mapping):
            return None
        for key in ("hash", "revision"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    def _ensure_session_key(self, context_id: str) -> str:
        existing = self._session_key_by_context.get(context_id)
        if existing:
            return existing
        created = self.sessions_create(
            metadata={"scope": "ui_chat", "sessionKey": f"ui:{context_id}", "label": f"ui:{context_id}"}
        )
        self._session_key_by_context[context_id] = created
        return created

    def _call(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
        *,
        expect_final: bool = False,
        timeout_ms: int | None = None,
    ) -> Any:
        effective_timeout_ms = timeout_ms or self._timeout_ms
        params_text = json.dumps(params or {}, ensure_ascii=False)
        params_file: str | None = None
        command: list[str] = [self._gateway_call_bin, "gateway", "call", method]
        if self._gateway_ws_url != _DEFAULT_GATEWAY_WS_URL or self._token or self._password:
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


def _unwrap_payload(raw: Any) -> Any:
    if isinstance(raw, Mapping):
        if "result" in raw:
            return raw["result"]
        if "payload" in raw:
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
    return _extract_text(payload)


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
