from __future__ import annotations

import hashlib
import json
import os
import secrets
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Any


class WechatDeliveryStore:
    def __init__(self, path: Path, *, now_provider: Callable[[], str] | None = None) -> None:
        self._path = path
        self._now_provider = now_provider or _now_iso
        self._lock = Lock()

    def create_waiting_report(self, intent_id: str) -> None:
        with self._lock:
            payload = self._load()
            payload["deliveries"].setdefault(
                intent_id,
                {
                    "intent_id": intent_id,
                    "report_id": None,
                    "state": "waiting_report",
                    "attempt_count": 0,
                    "last_error": None,
                    "result_unknown": False,
                    "text_footer": None,
                    "created_at": self._now_provider(),
                    "updated_at": self._now_provider(),
                },
            )
            self._save(payload)

    def mark_report_pending(self, intent_id: str, *, report_id: str, text_footer: str | None = None) -> None:
        with self._lock:
            payload = self._load()
            item = payload["deliveries"].get(intent_id)
            if not isinstance(item, dict):
                raise KeyError(intent_id)
            existing_report_id = str(item.get("report_id") or "").strip()
            if existing_report_id and existing_report_id != report_id:
                raise ValueError("delivery_intent_report_mismatch")
            if item.get("state") != "waiting_report":
                return
            item.update(
                {
                    "report_id": report_id,
                    "state": "pending",
                    "last_error": None,
                    "result_unknown": False,
                    "text_footer": (text_footer or "").strip() or None,
                    "updated_at": self._now_provider(),
                }
            )
            self._save(payload)

    def link_report(self, intent_id: str, *, report_id: str) -> None:
        with self._lock:
            payload = self._load()
            item = payload["deliveries"].get(intent_id)
            if not isinstance(item, dict):
                return
            existing_report_id = str(item.get("report_id") or "").strip()
            if existing_report_id and existing_report_id != report_id:
                raise ValueError("delivery_intent_report_mismatch")
            if existing_report_id == report_id:
                return
            item["report_id"] = report_id
            item["updated_at"] = self._now_provider()
            self._save(payload)

    def mark_report_failed(self, intent_id: str, *, error: str) -> None:
        with self._lock:
            payload = self._load()
            item = payload["deliveries"].get(intent_id)
            if not isinstance(item, dict) or item.get("state") in {"sent", "failed"}:
                return
            item.update(
                state="failed",
                last_error=error,
                result_unknown=False,
                updated_at=self._now_provider(),
            )
            self._save(payload)

    def get_delivery_status(self, intent_id: str) -> dict[str, object] | None:
        with self._lock:
            item = self._load()["deliveries"].get(intent_id)
        if not isinstance(item, dict):
            return None
        return {
            "intentId": str(item["intent_id"]),
            "reportId": item.get("report_id"),
            "state": str(item["state"]),
            "attemptCount": int(item.get("attempt_count", 0)),
            "lastError": item.get("last_error"),
            "resultUnknown": bool(item.get("result_unknown", False)),
        }

    def list_delivery_records(self, *, states: set[str]) -> list[dict[str, Any]]:
        with self._lock:
            deliveries = self._load()["deliveries"].values()
            return [dict(item) for item in deliveries if isinstance(item, dict) and item.get("state") in states]

    def begin_attempt(self, intent_id: str, *, allow_unknown: bool = False) -> dict[str, Any] | None:
        with self._lock:
            payload = self._load()
            item = payload["deliveries"].get(intent_id)
            allowed = {"pending", "unknown"} if allow_unknown else {"pending"}
            if not isinstance(item, dict) or item.get("state") not in allowed:
                return None
            item.update(
                {
                    "state": "unknown",
                    "attempt_count": int(item.get("attempt_count", 0)) + 1,
                    "last_error": "send_in_progress",
                    "result_unknown": True,
                    "updated_at": self._now_provider(),
                }
            )
            self._save(payload)
            return dict(item)

    def mark_sent(self, intent_id: str, *, message_id: str | None) -> None:
        self._update_delivery(
            intent_id,
            state="sent",
            last_error=None,
            result_unknown=False,
            message_id=(message_id or "").strip() or None,
        )

    def mark_known_failure(self, intent_id: str, *, error: str) -> None:
        self._update_delivery(
            intent_id,
            state="pending",
            last_error=error,
            result_unknown=False,
        )

    def mark_unknown(self, intent_id: str, *, error: str) -> None:
        self._update_delivery(
            intent_id,
            state="unknown",
            last_error=error,
            result_unknown=True,
        )

    def _update_delivery(self, intent_id: str, **changes: Any) -> None:
        with self._lock:
            payload = self._load()
            item = payload["deliveries"].get(intent_id)
            if not isinstance(item, dict):
                raise KeyError(intent_id)
            item.update(changes)
            item["updated_at"] = self._now_provider()
            self._save(payload)

    def save_binding_challenge(self, *, code: str, expires_at: str) -> None:
        with self._lock:
            payload = self._load()
            payload["binding_challenge"] = {
                "code_hash": _code_hash(code),
                "expires_at": expires_at,
            }
            self._save(payload)

    def get_or_create_binding_challenge(
        self,
        request_id: str,
        *,
        code: str,
        expires_at: str,
    ) -> dict[str, str]:
        request_id = request_id.strip()
        if not request_id or len(request_id) > 200:
            raise ValueError("invalid_binding_request_id")
        with self._lock:
            payload = self._load()
            requests = payload.setdefault("binding_code_requests", {})
            existing = requests.get(request_id)
            if isinstance(existing, dict):
                return {
                    "code": str(existing["code"]),
                    "expires_at": str(existing["expires_at"]),
                }
            result = {"code": code, "expires_at": expires_at}
            requests[request_id] = result
            payload["binding_challenge"] = {
                "code_hash": _code_hash(code),
                "expires_at": expires_at,
            }
            self._save(payload)
            return result

    def consume_binding_challenge(
        self,
        *,
        code: str,
        account_id: str,
        sender_id: str,
        now: str | None = None,
    ) -> bool:
        with self._lock:
            payload = self._load()
            challenge = payload.get("binding_challenge")
            requests = payload.get("binding_code_requests")
            candidates = [challenge] if isinstance(challenge, dict) else []
            if isinstance(requests, dict):
                candidates.extend(item for item in requests.values() if isinstance(item, dict))
            current_now = now or self._now_provider()
            if not any(
                _binding_challenge_matches(item, code=code, now=current_now)
                for item in candidates
            ):
                return False
            payload["binding"] = {
                "account_id": account_id,
                "sender_id": sender_id,
                "bound_at": now or self._now_provider(),
            }
            payload["binding_challenge"] = None
            payload["binding_code_requests"] = {}
            self._save(payload)
            return True

    def get_binding(self) -> dict[str, str] | None:
        with self._lock:
            binding = self._load().get("binding")
        if not isinstance(binding, dict):
            return None
        return {str(key): str(value) for key, value in binding.items()}

    def begin_manual_retry(self, request_id: str) -> dict[str, object]:
        request_id = request_id.strip()
        if not request_id or len(request_id) > 200:
            raise ValueError("invalid_manual_retry_request_id")
        with self._lock:
            payload = self._load()
            requests = payload.setdefault("manual_retry_requests", {})
            existing = requests.get(request_id)
            if isinstance(existing, dict):
                return {
                    "started": False,
                    "in_progress": existing.get("state") == "in_progress",
                    "attempted": int(existing.get("attempted", 0)),
                    "sent": int(existing.get("sent", 0)),
                }
            requests[request_id] = {
                "state": "in_progress",
                "attempted": 0,
                "sent": 0,
                "updated_at": self._now_provider(),
            }
            self._save(payload)
            return {"started": True, "in_progress": True, "attempted": 0, "sent": 0}

    def complete_manual_retry(self, request_id: str, *, attempted: int, sent: int) -> None:
        with self._lock:
            payload = self._load()
            requests = payload.setdefault("manual_retry_requests", {})
            item = requests.get(request_id)
            if not isinstance(item, dict):
                raise KeyError(request_id)
            item.update(
                state="completed",
                attempted=attempted,
                sent=sent,
                updated_at=self._now_provider(),
            )
            self._save(payload)

    def _load(self) -> dict[str, Any]:
        if not self._path.exists():
            return {
                "version": 1,
                "binding": None,
                "binding_challenge": None,
                "binding_code_requests": {},
                "deliveries": {},
                "manual_retry_requests": {},
            }
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or not isinstance(payload.get("deliveries"), dict):
                raise TypeError("invalid delivery store")
            payload.setdefault("manual_retry_requests", {})
            payload.setdefault("binding_code_requests", {})
            return payload
        except Exception as exc:
            raise RuntimeError("wechat_delivery_store_load_failed") from exc

    def _save(self, payload: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=self._path.parent, delete=False) as tmp:
                tmp_path = Path(tmp.name)
                json.dump(payload, tmp, ensure_ascii=False, sort_keys=True)
                tmp.write("\n")
                tmp.flush()
                os.fsync(tmp.fileno())
            tmp_path.chmod(0o600)
            os.replace(tmp_path, self._path)
            self._path.chmod(0o600)
            directory_fd = os.open(self._path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if tmp_path is not None and tmp_path.exists():
                tmp_path.unlink()


def _code_hash(code: str) -> str:
    return hashlib.sha256(code.strip().upper().encode("utf-8")).hexdigest()


def _constant_time_equal(left: str, right: str) -> bool:
    return secrets.compare_digest(left, right)


def _binding_challenge_matches(challenge: dict[str, Any], *, code: str, now: str) -> bool:
    try:
        if _as_datetime(now) >= _as_datetime(str(challenge.get("expires_at") or "")):
            return False
    except (TypeError, ValueError):
        return False
    expected_hash = str(challenge.get("code_hash") or "")
    if not expected_hash and challenge.get("code") is not None:
        expected_hash = _code_hash(str(challenge["code"]))
    return _constant_time_equal(_code_hash(code), expected_hash)


def _as_datetime(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text).astimezone(UTC)


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
