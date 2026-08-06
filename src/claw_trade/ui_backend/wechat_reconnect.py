from __future__ import annotations

import fcntl
import json
import os
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence


_ACTIVE_PHASES = frozenset({"preparing", "awaiting_scan", "committing", "restoring"})


class WechatReconnectError(RuntimeError):
    pass


class WechatReconnectBusy(WechatReconnectError):
    pass


class WechatReconnectController:
    def __init__(
        self,
        client: Any,
        *,
        state_path: Path,
        sleep: Any = time.sleep,
    ) -> None:
        self._client = client
        self._state_path = state_path
        self._lock_path = state_path.with_suffix(".lock")
        self._sleep = sleep
        self._delivery_guard_local = threading.local()

    def begin(self, *, request_id: str) -> dict[str, Any]:
        with self._locked():
            existing = self._load_state()
            if existing is not None and existing.get("requestId") == request_id:
                result = _public_state(existing)
                if existing.get("phase") == "awaiting_scan":
                    login = _mapping(
                        self._client.weixin_replacement_login_start(
                            operation_id=str(existing["operationId"])
                        )
                    )
                    if _optional_str(login.get("sessionKey")) == _optional_str(
                        existing.get("sessionKey")
                    ):
                        result["qrCodeImageDataUrl"] = _optional_qr_data_url(
                            login.get("qrCodeImageDataUrl") or login.get("qrDataUrl")
                        )
                return result
            if existing is not None and existing.get("phase") != "completed":
                inventory_conflict_without_plugin_operation = (
                    existing.get("lastErrorCode") == "WECHAT_ACCOUNT_INVENTORY_CONFLICT"
                    and not self._plugin_operation_exists(str(existing.get("operationId") or ""))
                )
                if not inventory_conflict_without_plugin_operation:
                    raise WechatReconnectBusy("wechat reconnect already in progress")
            inspection = _mapping(
                self._client.weixin_replacement_inspect(operation_id=None)
            )
            inventory = _inventory_from_status(
                self._client.channels_status(probe=True, fresh=True)
            )
            old_account_ids = _string_list(inspection.get("registeredAccountIds"))
            stored_account_ids = _string_list(inspection.get("storedAccountIds"))
            operation_id = str(uuid.uuid4())
            state = {
                "schemaVersion": 1,
                "operationId": operation_id,
                "requestId": request_id,
                "phase": "preparing",
                "oldAccountIds": old_account_ids,
                "channelWasDisabled": inventory.get("enabledAccountIds") == [],
                "sessionKey": None,
                "candidateAccountId": None,
                "loginReceiptId": None,
                "outcome": None,
                "lastErrorCode": None,
                "lastErrorMessage": None,
                "updatedAt": _now_iso(),
            }
            if (
                len(old_account_ids) != 1
                or stored_account_ids != old_account_ids
                or _nonnegative_int(inspection.get("activeLoginCount")) != 0
                or not _inventory_is_replaceable(inventory, old_account_ids[0])
            ):
                state.update(
                    phase="needs_attention",
                    lastErrorCode="WECHAT_ACCOUNT_INVENTORY_CONFLICT",
                    lastErrorMessage="微信账号状态异常，无法安全开始重新连接。",
                )
                self._save_state(state)
                return _public_state(state)
            self._save_state(state)
            try:
                self._client.weixin_replacement_begin(
                    operation_id=operation_id,
                    old_account_ids=old_account_ids,
                )
                login = self._client.weixin_replacement_login_start(operation_id=operation_id)
                session_key = _optional_str(_mapping(login).get("sessionKey"))
                if session_key is None:
                    raise WechatReconnectError("replacement login did not return sessionKey")
                state.update(phase="awaiting_scan", sessionKey=session_key, updatedAt=_now_iso())
                self._save_state(state)
                result = _public_state(state)
                result["qrCodeImageDataUrl"] = _optional_qr_data_url(
                    _mapping(login).get("qrCodeImageDataUrl") or _mapping(login).get("qrDataUrl")
                )
                return result
            except Exception:
                state.update(
                    phase="restoring",
                    lastErrorCode="WECHAT_RECONNECT_START_FAILED",
                    lastErrorMessage="微信重新连接启动失败，正在恢复旧账号。",
                    updatedAt=_now_iso(),
                )
                self._save_state(state)
                return self._restore_locked(state)

    def current_state(self, *, include_qr: bool = False) -> dict[str, Any] | None:
        with self._locked():
            state = self._load_state()
            if state is None:
                return None
            result = _public_state(state)
            if include_qr and state.get("phase") == "awaiting_scan":
                try:
                    login = _mapping(
                        self._client.weixin_replacement_login_start(
                            operation_id=str(state["operationId"])
                        )
                    )
                    if _optional_str(login.get("sessionKey")) == _optional_str(state.get("sessionKey")):
                        result["qrCodeImageDataUrl"] = _optional_qr_data_url(
                            login.get("qrCodeImageDataUrl") or login.get("qrDataUrl")
                        )
                except Exception:
                    pass
            return result

    def blocks_delivery(self) -> bool:
        with self._locked():
            try:
                state = self._load_state()
            except WechatReconnectError:
                return True
            return state is not None and state.get("phase") != "completed"

    @contextmanager
    def delivery_guard(self) -> Iterator[bool]:
        depth = int(getattr(self._delivery_guard_local, "depth", 0))
        if depth:
            self._delivery_guard_local.depth = depth + 1
            try:
                yield True
            finally:
                self._delivery_guard_local.depth = depth
            return
        lock = self._locked()
        try:
            lock.__enter__()
        except WechatReconnectBusy:
            yield False
            return
        try:
            try:
                state = self._load_state()
            except WechatReconnectError:
                yield False
                return
            allowed = state is None or state.get("phase") == "completed"
            self._delivery_guard_local.depth = 1
            try:
                yield allowed
            finally:
                self._delivery_guard_local.depth = 0
        finally:
            lock.__exit__(None, None, None)

    def record_recovery_failure(self) -> dict[str, Any]:
        with self._locked():
            try:
                state = self._load_state()
            except WechatReconnectError:
                return {
                    "operationId": None,
                    "phase": "needs_attention",
                    "outcome": None,
                    "lastErrorCode": "WECHAT_RECOVERY_STATE_INVALID",
                    "lastErrorMessage": "微信恢复状态损坏，发送已暂停。",
                    "updatedAt": _now_iso(),
                }
            if state is None:
                return {
                    "operationId": None,
                    "phase": "needs_attention",
                    "outcome": None,
                    "lastErrorCode": "WECHAT_RECOVERY_CHECK_FAILED",
                    "lastErrorMessage": "微信状态检查失败，发送已暂停。",
                    "updatedAt": _now_iso(),
                }
            state.update(
                phase="needs_attention",
                recoveryFromPhase=state.get("recoveryFromPhase") or state.get("phase"),
                lastErrorCode="WECHAT_RECOVERY_CHECK_FAILED",
                lastErrorMessage="微信状态检查失败，发送已暂停，请稍后继续恢复。",
                updatedAt=_now_iso(),
            )
            self._save_state(state)
            return _public_state(state)

    def poll(self, *, operation_id: str, timeout_ms: int = 1_500) -> dict[str, Any]:
        with self._locked():
            state = self._require_operation(operation_id)
            if state.get("phase") == "completed":
                return _public_state(state)
            if state.get("phase") != "awaiting_scan":
                return _public_state(state)
            raw = _mapping(
                self._client.weixin_replacement_login_wait(
                    operation_id=operation_id,
                    session_key=str(state.get("sessionKey") or ""),
                    timeout_ms=timeout_ms,
                )
            )
            if raw.get("connected") is not True:
                if _login_finished_unsuccessfully(raw):
                    state.update(phase="restoring", updatedAt=_now_iso())
                    self._save_state(state)
                    session_key = _optional_str(state.get("sessionKey"))
                    if session_key is not None:
                        self._client.weixin_login_cancel(
                            operation_id=operation_id,
                            session_key=session_key,
                        )
                    return self._restore_locked(state)
                result = _public_state(state)
                result["qrCodeImageDataUrl"] = _optional_qr_data_url(
                    raw.get("qrCodeImageDataUrl") or raw.get("qrDataUrl")
                )
                return result
            candidate_account_id = _optional_str(raw.get("candidateAccountId"))
            login_receipt_id = _optional_str(raw.get("loginReceiptId"))
            if candidate_account_id is None or login_receipt_id is None:
                raise WechatReconnectError("replacement login result is missing candidate receipt")
            state.update(
                phase="committing",
                candidateAccountId=candidate_account_id,
                loginReceiptId=login_receipt_id,
                updatedAt=_now_iso(),
            )
            self._save_state(state)
            return self._commit_locked(state)

    def cancel(self, *, operation_id: str) -> dict[str, Any]:
        with self._locked():
            state = self._require_operation(operation_id)
            if state.get("phase") == "completed":
                return _public_state(state)
            state.update(phase="restoring", updatedAt=_now_iso())
            self._save_state(state)
            session_key = _optional_str(state.get("sessionKey"))
            if session_key is not None:
                self._client.weixin_login_cancel(
                    operation_id=operation_id,
                    session_key=session_key,
                )
            return self._restore_locked(state)

    def recover(self, *, operation_id: str | None = None) -> dict[str, Any] | None:
        with self._locked():
            state = self._load_state()
            if state is None:
                return None
            if operation_id is not None and state.get("operationId") != operation_id:
                raise WechatReconnectError("wechat reconnect operation does not match")
            phase = str(state.get("phase") or "")
            if (
                phase == "needs_attention"
                and state.get("lastErrorCode") == "WECHAT_RECOVERY_CHECK_FAILED"
            ):
                resume_phase = str(state.get("recoveryFromPhase") or "")
                if resume_phase in _ACTIVE_PHASES:
                    state.update(
                        phase=resume_phase,
                        recoveryFromPhase=None,
                        lastErrorCode=None,
                        lastErrorMessage=None,
                        updatedAt=_now_iso(),
                    )
                    self._save_state(state)
                    phase = resume_phase
            if phase == "completed":
                return _public_state(state)
            current_operation_id = str(state.get("operationId") or "")
            if not current_operation_id:
                raise WechatReconnectError("wechat reconnect state is invalid")
            candidate_account_id = _optional_str(state.get("candidateAccountId"))
            if phase in {"committing", "needs_attention"} and candidate_account_id:
                if self._candidate_is_stable(current_operation_id, candidate_account_id):
                    if not self._plugin_operation_exists(current_operation_id):
                        return self._complete_switched(state)
                    state.update(phase="committing", updatedAt=_now_iso())
                    self._save_state(state)
                    return self._commit_locked(state)
                if phase == "needs_attention" and self._plugin_operation_phase(
                    current_operation_id
                ) in {"candidate_ready", "committing"}:
                    state.update(phase="committing", updatedAt=_now_iso())
                    self._save_state(state)
                    return self._commit_locked(state)
            if (
                phase == "needs_attention"
                and state.get("lastErrorCode") == "WECHAT_RESTORE_OPERATION_MISSING"
            ):
                state.update(phase="preparing", updatedAt=_now_iso())
                self._save_state(state)
                phase = "preparing"
            elif phase == "needs_attention" and str(state.get("lastErrorCode") or "").startswith(
                "WECHAT_RESTORE_"
            ):
                state.update(phase="restoring", updatedAt=_now_iso())
                self._save_state(state)
                return self._restore_locked(state)
            if phase == "restoring":
                return self._restore_locked(state)
            if phase == "awaiting_scan":
                inspection = _mapping(
                    self._client.weixin_replacement_inspect(operation_id=current_operation_id)
                )
                inspected_operation = _matching_inspection_operation(
                    inspection,
                    current_operation_id,
                )
                inspected_candidate = _optional_str(
                    inspected_operation.get("candidateAccountId")
                )
                inspected_receipt = _optional_str(
                    inspected_operation.get("loginReceiptId")
                )
                if inspected_candidate and inspected_receipt:
                    state.update(
                        phase="committing",
                        candidateAccountId=inspected_candidate,
                        loginReceiptId=inspected_receipt,
                        updatedAt=_now_iso(),
                    )
                    self._save_state(state)
                    return self._commit_locked(state)
                active_login_count = _nonnegative_int(inspection.get("activeLoginCount"))
                if active_login_count is None:
                    state.update(
                        phase="needs_attention",
                        lastErrorCode="WECHAT_INSPECTION_INCOMPLETE",
                        lastErrorMessage="微信登录状态证据不完整，发送已暂停。",
                        updatedAt=_now_iso(),
                    )
                    self._save_state(state)
                    return _public_state(state)
                if active_login_count > 0:
                    return _public_state(state)
                state.update(phase="restoring", updatedAt=_now_iso())
                self._save_state(state)
                return self._restore_locked(state)
            if phase == "committing":
                return self._commit_locked(state)
            if phase == "preparing":
                if not self._plugin_operation_exists(current_operation_id):
                    try:
                        self._ensure_wechat_enabled(state)
                    except Exception:
                        state.update(
                            phase="needs_attention",
                            lastErrorCode="WECHAT_RESTORE_FAILED",
                            lastErrorMessage="旧微信账号恢复失败，发送已暂停。",
                            updatedAt=_now_iso(),
                        )
                        self._save_state(state)
                        return _public_state(state)
                    old_account_ids = _string_list(state.get("oldAccountIds"))
                    if len(old_account_ids) == 1 and self._candidate_is_stable(
                        current_operation_id,
                        old_account_ids[0],
                    ):
                        return self._complete_restored(state)
                    state.update(
                        phase="needs_attention",
                        lastErrorCode="WECHAT_RESTORE_OPERATION_MISSING",
                        lastErrorMessage="微信重连事务未启动且旧账号状态异常，发送已暂停。",
                        updatedAt=_now_iso(),
                    )
                    self._save_state(state)
                    return _public_state(state)
                state.update(phase="restoring", updatedAt=_now_iso())
                self._save_state(state)
                return self._restore_locked(state)
            return _public_state(state)

    def _require_operation(self, operation_id: str) -> dict[str, Any]:
        state = self._load_state()
        if state is None or state.get("operationId") != operation_id:
            raise WechatReconnectError("wechat reconnect operation does not match")
        return state

    def _candidate_is_stable(self, operation_id: str, candidate_account_id: str) -> bool:
        first = self._combined_snapshot(operation_id)
        self._sleep(0.4)
        second = self._combined_snapshot(operation_id)
        return first == second and _combined_snapshot_is_healthy(second, candidate_account_id)

    def _combined_snapshot(self, operation_id: str) -> dict[str, Any]:
        replacement = _mapping(
            self._client.weixin_replacement_inspect(operation_id=operation_id)
        )
        runtime = _inventory_from_status(
            self._client.channels_status(probe=True, fresh=True)
        )
        return {
            "registeredAccountIds": _string_list(replacement.get("registeredAccountIds")),
            "storedAccountIds": _string_list(replacement.get("storedAccountIds")),
            "configuredAccountIds": runtime["configuredAccountIds"],
            "enabledAccountIds": runtime["enabledAccountIds"],
            "runningAccountIds": runtime["runningAccountIds"],
            "defaultAccountId": runtime["defaultAccountId"],
            "activeLoginCount": _nonnegative_int(replacement.get("activeLoginCount")),
        }

    def _restore_locked(self, state: dict[str, Any]) -> dict[str, Any]:
        operation_id = str(state["operationId"])
        old_account_ids = _string_list(state.get("oldAccountIds"))
        if len(old_account_ids) != 1:
            state.update(
                phase="needs_attention",
                lastErrorCode="WECHAT_RESTORE_AMBIGUOUS",
                lastErrorMessage="旧微信账号无法唯一确定，发送已暂停。",
                updatedAt=_now_iso(),
            )
            self._save_state(state)
            return _public_state(state)
        try:
            self._ensure_wechat_enabled(state)
            self._client.weixin_replacement_restore(operation_id=operation_id)
        except Exception:
            state.update(
                phase="needs_attention",
                lastErrorCode="WECHAT_RESTORE_FAILED",
                lastErrorMessage="旧微信账号恢复失败，发送已暂停。",
                updatedAt=_now_iso(),
            )
            self._save_state(state)
            return _public_state(state)
        if not self._candidate_is_stable(operation_id, old_account_ids[0]):
            state.update(
                phase="needs_attention",
                lastErrorCode="WECHAT_RESTORE_UNSTABLE",
                lastErrorMessage="旧微信账号恢复后状态未稳定，发送已暂停。",
                updatedAt=_now_iso(),
            )
            self._save_state(state)
            return _public_state(state)
        return self._complete_restored(state)

    def _commit_locked(self, state: dict[str, Any]) -> dict[str, Any]:
        operation_id = str(state["operationId"])
        candidate_account_id = _optional_str(state.get("candidateAccountId"))
        login_receipt_id = _optional_str(state.get("loginReceiptId"))
        if candidate_account_id is None or login_receipt_id is None:
            state.update(
                phase="needs_attention",
                lastErrorCode="WECHAT_COMMIT_EVIDENCE_MISSING",
                lastErrorMessage="新微信提交凭证不完整，发送已暂停。",
                updatedAt=_now_iso(),
            )
            self._save_state(state)
            return _public_state(state)
        try:
            self._ensure_wechat_enabled(state)
            self._client.weixin_replacement_commit(
                operation_id=operation_id,
                candidate_account_id=candidate_account_id,
                login_receipt_id=login_receipt_id,
            )
        except Exception:
            if self._candidate_is_stable(operation_id, candidate_account_id):
                if not self._plugin_operation_exists(operation_id):
                    return self._complete_switched(state)
                state.update(
                    phase="needs_attention",
                    lastErrorCode="WECHAT_COMMIT_INCOMPLETE",
                    lastErrorMessage="新微信提交未完成，发送已暂停，系统将在恢复时继续提交。",
                    updatedAt=_now_iso(),
                )
                self._save_state(state)
                return _public_state(state)
            plugin_phase = self._plugin_operation_phase(operation_id)
            if plugin_phase not in {"candidate_ready", "login_started", "suspended"}:
                state.update(
                    phase="needs_attention",
                    lastErrorCode="WECHAT_COMMIT_INCOMPLETE",
                    lastErrorMessage="新微信提交未完成，发送已暂停，系统将在恢复时继续提交。",
                    updatedAt=_now_iso(),
                )
                self._save_state(state)
                return _public_state(state)
            state.update(phase="restoring", updatedAt=_now_iso())
            self._save_state(state)
            return self._restore_locked(state)
        if self._candidate_is_stable(operation_id, candidate_account_id):
            return self._complete_switched(state)
        state.update(
            phase="needs_attention",
            lastErrorCode="WECHAT_RECONNECT_UNSTABLE",
            lastErrorMessage="新微信状态未稳定，发送已暂停。",
            updatedAt=_now_iso(),
        )
        self._save_state(state)
        return _public_state(state)

    def _ensure_wechat_enabled(self, state: dict[str, Any]) -> None:
        if state.get("channelWasDisabled") is not True:
            return
        self._client.config_patch(
            expected_settings_version=None,
            patch={"channels": {"openclaw-weixin": {"enabled": True}}},
        )
        state["channelWasDisabled"] = False
        state["updatedAt"] = _now_iso()
        self._save_state(state)

    def _plugin_operation_phase(self, operation_id: str) -> str | None:
        try:
            inspection = _mapping(
                self._client.weixin_replacement_inspect(operation_id=operation_id)
            )
        except Exception:
            return None
        operation = _matching_inspection_operation(inspection, operation_id)
        return _optional_str(operation.get("phase"))

    def _plugin_operation_exists(self, operation_id: str) -> bool:
        inspection = _mapping(
            self._client.weixin_replacement_inspect(operation_id=operation_id)
        )
        nested_operation_id = _optional_str(_mapping(inspection.get("operation")).get("operationId"))
        top_operation_id = _optional_str(inspection.get("operationId"))
        return operation_id in {nested_operation_id, top_operation_id}

    def _complete_switched(self, state: dict[str, Any]) -> dict[str, Any]:
        state.update(
            phase="completed",
            outcome="switched",
            loginReceiptId=None,
            lastErrorCode=None,
            lastErrorMessage=None,
            updatedAt=_now_iso(),
        )
        self._save_state(state)
        return _public_state(state)

    def _complete_restored(self, state: dict[str, Any]) -> dict[str, Any]:
        state.update(
            phase="completed",
            outcome="restored",
            loginReceiptId=None,
            lastErrorCode=None,
            lastErrorMessage=None,
            updatedAt=_now_iso(),
        )
        self._save_state(state)
        return _public_state(state)

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self._lock_path, os.O_RDONLY | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise WechatReconnectBusy("wechat reconnect state is locked") from exc
            yield
        finally:
            os.close(fd)

    def _load_state(self) -> dict[str, Any] | None:
        if not self._state_path.exists():
            return None
        try:
            raw = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise WechatReconnectError("wechat reconnect state is invalid") from exc
        if not isinstance(raw, dict) or raw.get("schemaVersion") != 1:
            raise WechatReconnectError("wechat reconnect state is invalid")
        return raw

    def _save_state(self, state: Mapping[str, Any]) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self._state_path.parent,
                delete=False,
            ) as handle:
                tmp_path = Path(handle.name)
                json.dump(dict(state), handle, ensure_ascii=False, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            tmp_path.replace(self._state_path)
            directory_fd = os.open(self._state_path.parent, os.O_RDONLY | os.O_CLOEXEC)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)


def _inventory_from_status(raw: Any) -> dict[str, Any]:
    payload = _mapping(raw)
    direct_registered = payload.get("registeredAccountIds")
    if isinstance(direct_registered, Sequence) and not isinstance(direct_registered, (str, bytes)):
        return {
            "registeredAccountIds": _string_list(direct_registered),
            "configuredAccountIds": _string_list(payload.get("configuredAccountIds")),
            "enabledAccountIds": _string_list(payload.get("enabledAccountIds")),
            "runningAccountIds": _string_list(payload.get("runningAccountIds")),
            "defaultAccountId": _optional_str(payload.get("defaultAccountId")),
            "activeLoginCount": _nonnegative_int(payload.get("activeLoginCount")),
        }
    accounts_by_channel = payload.get("channelAccounts")
    accounts = accounts_by_channel.get("openclaw-weixin") if isinstance(accounts_by_channel, Mapping) else None
    account_rows = accounts if isinstance(accounts, Sequence) and not isinstance(accounts, (str, bytes)) else []
    registered: list[str] = []
    configured: list[str] = []
    enabled: list[str] = []
    running: list[str] = []
    for row in account_rows:
        item = _mapping(row)
        account_id = _optional_str(item.get("accountId"))
        if account_id is None:
            continue
        registered.append(account_id)
        if item.get("configured") is True:
            configured.append(account_id)
        if item.get("enabled") is not False:
            enabled.append(account_id)
        if item.get("running") is True:
            running.append(account_id)
    defaults = payload.get("channelDefaultAccountId")
    return {
        "registeredAccountIds": registered,
        "configuredAccountIds": configured,
        "enabledAccountIds": enabled,
        "runningAccountIds": running,
        "defaultAccountId": _optional_str(defaults.get("openclaw-weixin")) if isinstance(defaults, Mapping) else None,
        "activeLoginCount": _nonnegative_int(payload.get("activeLoginCount")),
    }


def resolve_current_unique_account_id(client: Any) -> str | None:
    inspection = _mapping(client.weixin_replacement_inspect(operation_id=None))
    runtime = _inventory_from_status(client.channels_status(probe=True, fresh=True))
    registered = _string_list(inspection.get("registeredAccountIds"))
    stored = _string_list(inspection.get("storedAccountIds"))
    if (
        len(registered) != 1
        or stored != registered
        or _nonnegative_int(inspection.get("activeLoginCount")) != 0
    ):
        return None
    account_id = registered[0]
    return account_id if _inventory_is_healthy(runtime, account_id) else None


def _inventory_is_healthy(inventory: Mapping[str, Any], account_id: str) -> bool:
    expected = [account_id]
    return all(
        inventory.get(field) == expected
        for field in (
            "configuredAccountIds",
            "enabledAccountIds",
            "runningAccountIds",
        )
    ) and inventory.get("defaultAccountId") == account_id


def _inventory_is_replaceable(inventory: Mapping[str, Any], account_id: str) -> bool:
    expected = [account_id]
    active = (
        inventory.get("enabledAccountIds") == expected
        and inventory.get("runningAccountIds") == expected
    )
    disabled = (
        inventory.get("enabledAccountIds") == []
        and inventory.get("runningAccountIds") == []
    )
    return (
        inventory.get("configuredAccountIds") == expected
        and inventory.get("defaultAccountId") == account_id
        and (active or disabled)
    )


def _combined_snapshot_is_healthy(snapshot: Mapping[str, Any], account_id: str) -> bool:
    expected = [account_id]
    return all(
        snapshot.get(field) == expected
        for field in (
            "registeredAccountIds",
            "storedAccountIds",
            "configuredAccountIds",
            "enabledAccountIds",
            "runningAccountIds",
        )
    ) and snapshot.get("defaultAccountId") == account_id and snapshot.get("activeLoginCount") == 0


def _public_state(state: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "operationId": state.get("operationId"),
        "phase": state.get("phase"),
        "outcome": state.get("outcome"),
        "lastErrorCode": state.get("lastErrorCode"),
        "lastErrorMessage": state.get("lastErrorMessage"),
        "updatedAt": state.get("updatedAt"),
    }


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _matching_inspection_operation(
    inspection: Mapping[str, Any],
    operation_id: str,
) -> Mapping[str, Any]:
    nested = _mapping(inspection.get("operation"))
    nested_operation_id = _optional_str(nested.get("operationId"))
    if nested_operation_id == operation_id:
        return nested
    top_operation_id = _optional_str(inspection.get("operationId"))
    if top_operation_id in {None, operation_id}:
        return inspection
    return {}


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return []
    return [text for item in value if (text := _optional_str(item)) is not None]


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_qr_data_url(value: Any) -> str | None:
    text = _optional_str(value)
    if text is None or not text.startswith("data:image/png;base64,") or len(text) > 16_384:
        return None
    return text


def _login_finished_unsuccessfully(raw: Mapping[str, Any]) -> bool:
    if any(raw.get(field) is True for field in ("expired", "cancelled", "failed", "sessionClosed")):
        return True
    terminal_state = raw.get("state") or raw.get("status")
    return str(terminal_state or "").strip().lower() in {
        "expired",
        "cancelled",
        "failed",
        "closed",
    }


def _nonnegative_int(value: Any) -> int | None:
    return value if isinstance(value, int) and value >= 0 else None


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
