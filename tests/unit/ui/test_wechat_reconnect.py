from __future__ import annotations

import json
import fcntl
import os
from pathlib import Path

import pytest

from claw_trade.ui_backend.channel_bridge import ChannelBridge
from claw_trade.ui_backend.wechat_reconnect import WechatReconnectBusy, WechatReconnectController


def _inventory(account_id: str) -> dict[str, object]:
    return {
        "registeredAccountIds": [account_id],
        "configuredAccountIds": [account_id],
        "enabledAccountIds": [account_id],
        "runningAccountIds": [account_id],
        "defaultAccountId": account_id,
        "activeLoginCount": 0,
    }


class _ReplacementClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.statuses = [_inventory("old-account")]

    def channels_status(self, *, probe=False, fresh=False):  # type: ignore[no-untyped-def]
        self.calls.append(("status", {"probe": probe, "fresh": fresh}))
        return self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]

    def weixin_replacement_begin(self, *, operation_id, old_account_ids):  # type: ignore[no-untyped-def]
        self.calls.append(
            ("begin", {"operationId": operation_id, "oldAccountIds": list(old_account_ids)})
        )
        return {"ok": True}

    def weixin_replacement_login_start(self, *, operation_id):  # type: ignore[no-untyped-def]
        self.calls.append(("login.start", {"operationId": operation_id}))
        return {
            "sessionKey": "session-1",
            "qrCodeImageDataUrl": "data:image/png;base64,qr-secret",
        }

    def weixin_replacement_login_wait(self, *, operation_id, session_key, timeout_ms):  # type: ignore[no-untyped-def]
        self.calls.append(
            (
                "login.wait",
                {"operationId": operation_id, "sessionKey": session_key, "timeoutMs": timeout_ms},
            )
        )
        return {
            "connected": True,
            "candidateAccountId": "old-account",
            "loginReceiptId": "receipt-generation-2",
        }

    def weixin_replacement_commit(
        self, *, operation_id, candidate_account_id, login_receipt_id
    ):  # type: ignore[no-untyped-def]
        self.calls.append(
            (
                "commit",
                {
                    "operationId": operation_id,
                    "candidateAccountId": candidate_account_id,
                    "loginReceiptId": login_receipt_id,
                },
            )
        )
        return {"ok": True}

    def weixin_replacement_inspect(self, *, operation_id):  # type: ignore[no-untyped-def]
        self.calls.append(("inspect", {"operationId": operation_id}))
        return {
            "registeredAccountIds": ["old-account"],
            "storedAccountIds": ["old-account"],
            "activeLoginCount": 0,
        }

    def weixin_login_cancel(self, *, operation_id, session_key):  # type: ignore[no-untyped-def]
        self.calls.append(
            ("login.cancel", {"operationId": operation_id, "sessionKey": session_key})
        )
        return {"cancelled": True}

    def weixin_replacement_restore(self, *, operation_id):  # type: ignore[no-untyped-def]
        self.calls.append(("restore", {"operationId": operation_id}))
        return {"restored": True}

    def config_patch(self, *, expected_settings_version, patch):  # type: ignore[no-untyped-def]
        self.calls.append(
            (
                "config.patch",
                {"expectedSettingsVersion": expected_settings_version, "patch": patch},
            )
        )
        return {"restartRequired": False}


def test_begin_replacement_persists_waiting_state_without_qr_payload(tmp_path: Path) -> None:
    state_path = tmp_path / ".ui-wechat-reconnect.json"
    client = _ReplacementClient()
    controller = WechatReconnectController(client, state_path=state_path)

    result = controller.begin(request_id="request-1")

    assert result["phase"] == "awaiting_scan"
    assert result["qrCodeImageDataUrl"] == "data:image/png;base64,qr-secret"
    stored = json.loads(state_path.read_text(encoding="utf-8"))
    assert stored["phase"] == "awaiting_scan"
    assert stored["oldAccountIds"] == ["old-account"]
    assert stored["sessionKey"] == "session-1"
    assert "qrCodeImageDataUrl" not in stored
    assert client.calls[0] == ("inspect", {"operationId": None})
    assert client.calls[1] == ("status", {"probe": True, "fresh": True})
    assert [name for name, _ in client.calls] == ["inspect", "status", "begin", "login.start"]


def test_poll_commits_same_account_id_generation_only_after_two_stable_snapshots(tmp_path: Path) -> None:
    client = _ReplacementClient()
    controller = WechatReconnectController(client, state_path=tmp_path / ".ui-wechat-reconnect.json", sleep=lambda _: None)
    started = controller.begin(request_id="request-1")

    result = controller.poll(operation_id=str(started["operationId"]))
    repeated = controller.poll(operation_id=str(started["operationId"]))

    assert result["phase"] == "completed"
    assert result["outcome"] == "switched"
    assert repeated == result
    assert [name for name, _ in client.calls].count("commit") == 1
    commit = next(payload for name, payload in client.calls if name == "commit")
    assert commit["candidateAccountId"] == "old-account"
    assert commit["loginReceiptId"] == "receipt-generation-2"
    assert len(
        [payload for name, payload in client.calls if name == "inspect" and payload["operationId"] is not None]
    ) == 2
    assert [payload for name, payload in client.calls if name == "status"] == [
        {"probe": True, "fresh": True},
        {"probe": True, "fresh": True},
        {"probe": True, "fresh": True},
    ]


def test_cancel_restores_old_account_once_and_is_idempotent(tmp_path: Path) -> None:
    client = _ReplacementClient()
    controller = WechatReconnectController(client, state_path=tmp_path / ".ui-wechat-reconnect.json", sleep=lambda _: None)
    started = controller.begin(request_id="request-1")

    first = controller.cancel(operation_id=str(started["operationId"]))
    second = controller.cancel(operation_id=str(started["operationId"]))

    assert first["phase"] == "completed"
    assert first["outcome"] == "restored"
    assert second == first
    assert [name for name, _ in client.calls].count("login.cancel") == 1
    assert [name for name, _ in client.calls].count("restore") == 1


def test_begin_failure_actually_restores_old_account(tmp_path: Path) -> None:
    class _StartFailureClient(_ReplacementClient):
        def weixin_replacement_login_start(self, *, operation_id):  # type: ignore[no-untyped-def]
            self.calls.append(("login.start", {"operationId": operation_id}))
            raise RuntimeError("login start failed")

    client = _StartFailureClient()
    controller = WechatReconnectController(client, state_path=tmp_path / ".ui-wechat-reconnect.json", sleep=lambda _: None)

    result = controller.begin(request_id="request-start-failure")

    assert result["phase"] == "completed"
    assert result["outcome"] == "restored"
    assert [name for name, _ in client.calls].count("restore") == 1


def test_begin_rejects_cross_process_lock_conflict_before_gateway_calls(tmp_path: Path) -> None:
    state_path = tmp_path / ".ui-wechat-reconnect.json"
    lock_path = state_path.with_suffix(".lock")
    lock_path.touch(mode=0o600)
    fd = os.open(lock_path, os.O_RDONLY)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    client = _ReplacementClient()
    controller = WechatReconnectController(client, state_path=state_path)
    try:
        with pytest.raises(WechatReconnectBusy):
            controller.begin(request_id="request-locked")
    finally:
        os.close(fd)

    assert client.calls == []


def test_recover_marks_lost_commit_response_complete_only_from_live_evidence(tmp_path: Path) -> None:
    state_path = tmp_path / ".ui-wechat-reconnect.json"
    client = _ReplacementClient()
    controller = WechatReconnectController(client, state_path=state_path, sleep=lambda _: None)
    started = controller.begin(request_id="request-crash")
    stored = json.loads(state_path.read_text(encoding="utf-8"))
    stored.update(
        phase="committing",
        candidateAccountId="old-account",
        loginReceiptId="receipt-generation-2",
    )
    state_path.write_text(json.dumps(stored), encoding="utf-8")
    client.calls.clear()

    recovered = WechatReconnectController(client, state_path=state_path, sleep=lambda _: None).recover()

    assert recovered is not None
    assert recovered["phase"] == "completed"
    assert recovered["outcome"] == "switched"
    assert [name for name, _ in client.calls] == ["inspect", "status", "inspect", "status", "inspect"]


def test_commit_does_not_complete_when_fresh_runtime_snapshots_change(tmp_path: Path) -> None:
    client = _ReplacementClient()
    unstable = _inventory("old-account")
    unstable["runningAccountIds"] = []
    client.statuses = [_inventory("old-account"), _inventory("old-account"), unstable]
    controller = WechatReconnectController(client, state_path=tmp_path / ".ui-wechat-reconnect.json", sleep=lambda _: None)
    started = controller.begin(request_id="request-unstable")

    result = controller.poll(operation_id=str(started["operationId"]))

    assert result["phase"] == "needs_attention"
    assert result["lastErrorCode"] == "WECHAT_RECONNECT_UNSTABLE"


def test_commit_failure_after_plugin_entered_committing_is_resumed_not_restored(tmp_path: Path) -> None:
    class _InterruptedCommitClient(_ReplacementClient):
        def __init__(self) -> None:
            super().__init__()
            self.commit_calls = 0
            self.statuses = [_inventory("old-account"), *[_inventory("new-account") for _ in range(6)]]

        def weixin_replacement_login_wait(self, *, operation_id, session_key, timeout_ms):  # type: ignore[no-untyped-def]
            self.calls.append(("login.wait", {"operationId": operation_id, "sessionKey": session_key, "timeoutMs": timeout_ms}))
            return {
                "connected": True,
                "candidateAccountId": "new-account",
                "loginReceiptId": "receipt-new",
            }

        def weixin_replacement_commit(self, *, operation_id, candidate_account_id, login_receipt_id):  # type: ignore[no-untyped-def]
            self.commit_calls += 1
            self.calls.append(("commit", {"operationId": operation_id}))
            if self.commit_calls == 1:
                raise RuntimeError("commit response lost after durable commit phase")
            return {"committed": True}

        def weixin_replacement_inspect(self, *, operation_id):  # type: ignore[no-untyped-def]
            self.calls.append(("inspect", {"operationId": operation_id}))
            if operation_id is None:
                return {
                    "registeredAccountIds": ["old-account"],
                    "storedAccountIds": ["old-account"],
                    "activeLoginCount": 0,
                }
            return {
                "registeredAccountIds": ["new-account"],
                "storedAccountIds": ["new-account"] if self.commit_calls > 1 else ["old-account", "new-account"],
                "activeLoginCount": 0,
                "operation": {"operationId": operation_id, "phase": "committing", "candidateAccountId": "new-account"},
            }

        def weixin_replacement_restore(self, *, operation_id):  # type: ignore[no-untyped-def]
            raise AssertionError("a durable committing generation must not be restored")

    client = _InterruptedCommitClient()
    state_path = tmp_path / ".ui-wechat-reconnect.json"
    controller = WechatReconnectController(client, state_path=state_path, sleep=lambda _: None)
    started = controller.begin(request_id="request-interrupted-commit")

    interrupted = controller.poll(operation_id=str(started["operationId"]))
    recovered = WechatReconnectController(client, state_path=state_path, sleep=lambda _: None).recover()

    assert interrupted["phase"] == "needs_attention"
    assert interrupted["lastErrorCode"] == "WECHAT_COMMIT_INCOMPLETE"
    assert recovered is not None
    assert recovered["phase"] == "completed"
    assert recovered["outcome"] == "switched"
    assert client.commit_calls == 2


def test_recover_retries_commit_when_new_account_is_stable_but_plugin_operation_remains(tmp_path: Path) -> None:
    class _CrashBeforeTerminalClient(_ReplacementClient):
        def __init__(self) -> None:
            super().__init__()
            self.commit_calls = 0
            self.statuses = [_inventory("old-account"), *[_inventory("new-account") for _ in range(8)]]

        def weixin_replacement_login_wait(self, *, operation_id, session_key, timeout_ms):  # type: ignore[no-untyped-def]
            return {
                "connected": True,
                "candidateAccountId": "new-account",
                "loginReceiptId": "receipt-new",
            }

        def weixin_replacement_commit(self, **kwargs):  # type: ignore[no-untyped-def]
            self.commit_calls += 1
            if self.commit_calls == 1:
                raise RuntimeError("crash before terminal receipt")
            return {"committed": True}

        def weixin_replacement_inspect(self, *, operation_id):  # type: ignore[no-untyped-def]
            if operation_id is None:
                return {
                    "registeredAccountIds": ["old-account"],
                    "storedAccountIds": ["old-account"],
                    "activeLoginCount": 0,
                }
            result = {
                "registeredAccountIds": ["new-account"],
                "storedAccountIds": ["new-account"],
                "activeLoginCount": 0,
            }
            if self.commit_calls < 2:
                result["operation"] = {
                    "operationId": operation_id,
                    "phase": "committing",
                    "candidateAccountId": "new-account",
                }
            return result

    client = _CrashBeforeTerminalClient()
    state_path = tmp_path / ".ui-wechat-reconnect.json"
    controller = WechatReconnectController(client, state_path=state_path, sleep=lambda _: None)
    started = controller.begin(request_id="request-crash-before-terminal")

    interrupted = controller.poll(operation_id=str(started["operationId"]))
    recovered = WechatReconnectController(client, state_path=state_path, sleep=lambda _: None).recover()

    assert interrupted["phase"] == "needs_attention"
    assert interrupted["lastErrorCode"] == "WECHAT_COMMIT_INCOMPLETE"
    assert recovered is not None
    assert recovered["phase"] == "completed"
    assert recovered["outcome"] == "switched"
    assert client.commit_calls == 2


def test_begin_replaces_all_legacy_accounts_without_treating_empty_index_entry_as_restorable(
    tmp_path: Path,
) -> None:
    class _LegacyInventoryClient(_ReplacementClient):
        restored = False

        def weixin_replacement_inspect(self, *, operation_id):  # type: ignore[no-untyped-def]
            self.calls.append(("inspect", {"operationId": operation_id}))
            if self.restored:
                return {
                    "registeredAccountIds": ["old-a", "old-b"],
                    "storedAccountIds": ["old-a", "old-b"],
                    "activeLoginCount": 0,
                }
            return {
                "registeredAccountIds": ["stale-index", "old-a", "old-b"],
                "storedAccountIds": ["old-a", "old-b"],
                "activeLoginCount": 0,
            }

        def weixin_replacement_restore(self, *, operation_id):  # type: ignore[no-untyped-def]
            self.restored = True
            return super().weixin_replacement_restore(operation_id=operation_id)

        def weixin_replacement_login_start(self, *, operation_id):  # type: ignore[no-untyped-def]
            self.calls.append(("login.start", {"operationId": operation_id}))
            return {
                "sessionKey": "session-1",
                "qrDataUrl": "data:image/svg+xml;base64,fake-qr",
            }

    client = _LegacyInventoryClient()
    legacy_inventory = {
        "registeredAccountIds": ["stale-index", "old-a", "old-b"],
        "configuredAccountIds": ["old-a", "old-b"],
        "enabledAccountIds": ["stale-index", "old-a", "old-b"],
        "runningAccountIds": ["old-a", "old-b"],
        "defaultAccountId": "stale-index",
        "activeLoginCount": 0,
    }
    restored_inventory = {
        **legacy_inventory,
        "registeredAccountIds": ["old-a", "old-b"],
        "enabledAccountIds": ["old-a", "old-b"],
        "defaultAccountId": "old-b",
    }
    client.statuses = [legacy_inventory, restored_inventory, restored_inventory]
    controller = WechatReconnectController(
        client, state_path=tmp_path / ".ui-wechat-reconnect.json"
    )

    result = controller.begin(request_id="request-conflict")

    assert result["phase"] == "awaiting_scan"
    assert result["qrCodeImageDataUrl"] == "data:image/svg+xml;base64,fake-qr"
    state = json.loads((tmp_path / ".ui-wechat-reconnect.json").read_text(encoding="utf-8"))
    assert state["oldAccountIds"] == ["stale-index", "old-a", "old-b"]
    assert state["restorableAccountIds"] == ["old-a", "old-b"]
    assert client.calls[2][1]["oldAccountIds"] == ["stale-index", "old-a", "old-b"]
    cancelled = controller.cancel(operation_id=str(result["operationId"]))
    assert cancelled["phase"] == "completed"
    assert cancelled["outcome"] == "restored"


def test_begin_rejects_orphan_stored_credential_before_replacement(tmp_path: Path) -> None:
    class _OrphanStoredClient(_ReplacementClient):
        def weixin_replacement_inspect(self, *, operation_id):  # type: ignore[no-untyped-def]
            self.calls.append(("inspect", {"operationId": operation_id}))
            return {
                "registeredAccountIds": ["old-account"],
                "storedAccountIds": ["old-account", "orphan-account"],
                "activeLoginCount": 0,
            }

    client = _OrphanStoredClient()
    controller = WechatReconnectController(client, state_path=tmp_path / ".ui-wechat-reconnect.json")

    result = controller.begin(request_id="request-orphan")

    assert result["phase"] == "needs_attention"
    assert result["lastErrorCode"] == "WECHAT_ACCOUNT_INVENTORY_CONFLICT"
    assert [name for name, _ in client.calls] == ["inspect", "status"]


def test_begin_rejects_inspection_without_active_login_count(tmp_path: Path) -> None:
    class _IncompleteInspectClient(_ReplacementClient):
        def weixin_replacement_inspect(self, *, operation_id):  # type: ignore[no-untyped-def]
            self.calls.append(("inspect", {"operationId": operation_id}))
            return {
                "registeredAccountIds": ["old-account"],
                "storedAccountIds": ["old-account"],
            }

    client = _IncompleteInspectClient()
    controller = WechatReconnectController(
        client,
        state_path=tmp_path / ".ui-wechat-reconnect.json",
    )

    result = controller.begin(request_id="request-incomplete-inspect")

    assert result["phase"] == "needs_attention"
    assert result["lastErrorCode"] == "WECHAT_ACCOUNT_INVENTORY_CONFLICT"
    assert [name for name, _ in client.calls] == ["inspect", "status"]


def test_channel_bridge_exposes_reconnect_control_without_provider_secrets(tmp_path: Path) -> None:
    client = _ReplacementClient()
    bridge = ChannelBridge(client, reconnect_state_path=tmp_path / ".ui-wechat-reconnect.json")

    started = bridge.start_wechat_reconnect(
        request_id="request-via-bridge",
        channel_kind="wechat_clawbot",
    )
    current = bridge.get_wechat_reconnect_state()

    assert started["phase"] == "awaiting_scan"
    assert current is not None
    assert current["operationId"] == started["operationId"]
    assert "sessionKey" not in started
    assert "candidateAccountId" not in started
    assert "loginReceiptId" not in started


def test_channel_bridge_resolves_current_account_only_from_fresh_complete_inventory(tmp_path: Path) -> None:
    client = _ReplacementClient()
    bridge = ChannelBridge(client, reconnect_state_path=tmp_path / ".ui-wechat-reconnect.json")

    assert bridge.current_unique_account_id() == "old-account"
    assert client.calls == [
        ("inspect", {"operationId": None}),
        ("status", {"probe": True, "fresh": True}),
    ]


def test_runtime_status_does_not_need_plugin_active_login_count(tmp_path: Path) -> None:
    client = _ReplacementClient()
    runtime = _inventory("old-account")
    runtime.pop("activeLoginCount")
    client.statuses = [runtime]
    bridge = ChannelBridge(
        client,
        reconnect_state_path=tmp_path / ".ui-wechat-reconnect.json",
    )

    assert bridge.current_unique_account_id() == "old-account"


def test_channel_bridge_does_not_resolve_account_while_reconnect_is_active(tmp_path: Path) -> None:
    client = _ReplacementClient()
    bridge = ChannelBridge(client, reconnect_state_path=tmp_path / ".ui-wechat-reconnect.json")
    bridge.start_wechat_reconnect(request_id="request-active", channel_kind="wechat_clawbot")
    client.calls.clear()

    assert bridge.current_unique_account_id() is None
    assert client.calls == []


def test_expired_login_cancels_session_and_restores_old_account(tmp_path: Path) -> None:
    class _ExpiredLoginClient(_ReplacementClient):
        def weixin_replacement_login_wait(self, *, operation_id, session_key, timeout_ms):  # type: ignore[no-untyped-def]
            self.calls.append(
                (
                    "login.wait",
                    {"operationId": operation_id, "sessionKey": session_key, "timeoutMs": timeout_ms},
                )
            )
            return {"connected": False, "expired": True}

    client = _ExpiredLoginClient()
    controller = WechatReconnectController(client, state_path=tmp_path / ".ui-wechat-reconnect.json", sleep=lambda _: None)
    started = controller.begin(request_id="request-expired")

    result = controller.poll(operation_id=str(started["operationId"]))

    assert result["phase"] == "completed"
    assert result["outcome"] == "restored"
    assert [name for name, _ in client.calls].count("login.cancel") == 1
    assert [name for name, _ in client.calls].count("restore") == 1


def test_pending_login_poll_keeps_same_session_until_scan_succeeds(tmp_path: Path) -> None:
    class _SlowLoginClient(_ReplacementClient):
        def __init__(self) -> None:
            super().__init__()
            self.wait_count = 0

        def weixin_replacement_login_wait(self, *, operation_id, session_key, timeout_ms):  # type: ignore[no-untyped-def]
            self.calls.append(
                (
                    "login.wait",
                    {"operationId": operation_id, "sessionKey": session_key, "timeoutMs": timeout_ms},
                )
            )
            self.wait_count += 1
            if self.wait_count == 1:
                return {"state": "pending", "connected": False}
            return {
                "state": "connected",
                "connected": True,
                "candidateAccountId": "old-account",
                "loginReceiptId": "receipt-generation-2",
            }

    client = _SlowLoginClient()
    controller = WechatReconnectController(
        client,
        state_path=tmp_path / ".ui-wechat-reconnect.json",
        sleep=lambda _: None,
    )
    started = controller.begin(request_id="request-slow-scan")

    pending = controller.poll(operation_id=str(started["operationId"]))
    connected = controller.poll(operation_id=str(started["operationId"]))

    assert pending["phase"] == "awaiting_scan"
    assert connected["phase"] == "completed"
    assert connected["outcome"] == "switched"
    assert [payload["sessionKey"] for name, payload in client.calls if name == "login.wait"] == [
        "session-1",
        "session-1",
    ]
    assert [name for name, _ in client.calls].count("login.cancel") == 0


def test_begin_accepts_single_disabled_account_with_preserved_credentials(tmp_path: Path) -> None:
    client = _ReplacementClient()
    client.statuses = [
        {
            "registeredAccountIds": ["old-account"],
            "configuredAccountIds": ["old-account"],
            "enabledAccountIds": [],
            "runningAccountIds": [],
            "defaultAccountId": "old-account",
            "activeLoginCount": 0,
        }
    ]

    result = WechatReconnectController(
        client,
        state_path=tmp_path / ".ui-wechat-reconnect.json",
    ).begin(request_id="request-disabled-account")

    assert result["phase"] == "awaiting_scan"
    assert [name for name, _ in client.calls].count("begin") == 1


def test_disabled_channel_is_enabled_inside_transaction_before_candidate_commit(tmp_path: Path) -> None:
    client = _ReplacementClient()
    client.statuses = [
        {
            "registeredAccountIds": ["old-account"],
            "configuredAccountIds": ["old-account"],
            "enabledAccountIds": [],
            "runningAccountIds": [],
            "defaultAccountId": "old-account",
            "activeLoginCount": 0,
        },
        _inventory("old-account"),
    ]
    controller = WechatReconnectController(
        client,
        state_path=tmp_path / ".ui-wechat-reconnect.json",
        sleep=lambda _: None,
    )
    started = controller.begin(request_id="request-disabled-commit")

    result = controller.poll(operation_id=str(started["operationId"]))

    names = [name for name, _ in client.calls]
    assert result["phase"] == "completed"
    assert names.index("config.patch") < names.index("commit")
    config_call = next(payload for name, payload in client.calls if name == "config.patch")
    assert config_call["patch"] == {"channels": {"openclaw-weixin": {"enabled": True}}}


def test_cancel_from_disabled_channel_reenables_before_restoring_old_account(tmp_path: Path) -> None:
    client = _ReplacementClient()
    client.statuses = [
        {
            "registeredAccountIds": ["old-account"],
            "configuredAccountIds": ["old-account"],
            "enabledAccountIds": [],
            "runningAccountIds": [],
            "defaultAccountId": "old-account",
            "activeLoginCount": 0,
        },
        _inventory("old-account"),
    ]
    controller = WechatReconnectController(
        client,
        state_path=tmp_path / ".ui-wechat-reconnect.json",
        sleep=lambda _: None,
    )
    started = controller.begin(request_id="request-disabled-cancel")

    result = controller.cancel(operation_id=str(started["operationId"]))

    names = [name for name, _ in client.calls]
    assert result["phase"] == "completed"
    assert result["outcome"] == "restored"
    assert names.index("config.patch") < names.index("restore")


def test_recover_reads_candidate_receipt_from_matching_inspect_operation(tmp_path: Path) -> None:
    class _NestedCandidateClient(_ReplacementClient):
        def __init__(self) -> None:
            super().__init__()
            self.operation_inspects = 0

        def weixin_replacement_inspect(self, *, operation_id):  # type: ignore[no-untyped-def]
            self.calls.append(("inspect", {"operationId": operation_id}))
            if operation_id is None:
                return {
                    "registeredAccountIds": ["old-account"],
                    "storedAccountIds": ["old-account"],
                    "activeLoginCount": 0,
                }
            self.operation_inspects += 1
            if self.operation_inspects == 1:
                return {
                    "registeredAccountIds": [],
                    "storedAccountIds": ["old-account"],
                    "activeLoginCount": 0,
                    "operation": {
                        "operationId": operation_id,
                        "candidateAccountId": "old-account",
                        "loginReceiptId": "receipt-after-crash",
                    },
                }
            return {
                "registeredAccountIds": ["old-account"],
                "storedAccountIds": ["old-account"],
                "activeLoginCount": 0,
            }

    client = _NestedCandidateClient()
    state_path = tmp_path / ".ui-wechat-reconnect.json"
    started = WechatReconnectController(client, state_path=state_path).begin(request_id="request-crash-candidate")
    client.calls.clear()

    result = WechatReconnectController(client, state_path=state_path, sleep=lambda _: None).recover()

    assert result is not None
    assert result["phase"] == "completed"
    assert result["outcome"] == "switched"
    commit = next(payload for name, payload in client.calls if name == "commit")
    assert commit["loginReceiptId"] == "receipt-after-crash"
    assert commit["operationId"] == started["operationId"]


def test_begin_idempotency_reloads_active_qr_for_same_request(tmp_path: Path) -> None:
    client = _ReplacementClient()
    controller = WechatReconnectController(client, state_path=tmp_path / ".ui-wechat-reconnect.json")

    first = controller.begin(request_id="request-retry")
    second = controller.begin(request_id="request-retry")

    assert second["operationId"] == first["operationId"]
    assert second["qrCodeImageDataUrl"] == "data:image/png;base64,qr-secret"
    assert [name for name, _ in client.calls].count("begin") == 1
    assert [name for name, _ in client.calls].count("login.start") == 2


def test_current_state_reloads_qr_after_ui_restart(tmp_path: Path) -> None:
    client = _ReplacementClient()
    state_path = tmp_path / ".ui-wechat-reconnect.json"
    WechatReconnectController(client, state_path=state_path).begin(request_id="request-restart")

    resumed = WechatReconnectController(client, state_path=state_path).current_state(include_qr=True)

    assert resumed is not None
    assert resumed["phase"] == "awaiting_scan"
    assert resumed["qrCodeImageDataUrl"] == "data:image/png;base64,qr-secret"
    assert [name for name, _ in client.calls].count("login.start") == 2


def test_recover_preparing_without_plugin_operation_accepts_stable_old_account(tmp_path: Path) -> None:
    client = _ReplacementClient()
    state_path = tmp_path / ".ui-wechat-reconnect.json"
    started = WechatReconnectController(client, state_path=state_path).begin(
        request_id="request-preparing-crash"
    )
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["phase"] = "preparing"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    client.calls.clear()

    result = WechatReconnectController(
        client,
        state_path=state_path,
        sleep=lambda _: None,
    ).recover(operation_id=str(started["operationId"]))

    assert result is not None
    assert result["phase"] == "completed"
    assert result["outcome"] == "restored"
    assert [name for name, _ in client.calls].count("restore") == 0


def test_disabled_preparing_recovery_rechecks_old_account_after_config_restart(tmp_path: Path) -> None:
    disabled = {
        "registeredAccountIds": ["old-account"],
        "configuredAccountIds": ["old-account"],
        "enabledAccountIds": [],
        "runningAccountIds": [],
        "defaultAccountId": "old-account",
        "activeLoginCount": 0,
    }
    client = _ReplacementClient()
    client.statuses = [disabled, disabled, disabled, _inventory("old-account"), _inventory("old-account")]
    state_path = tmp_path / ".ui-wechat-reconnect.json"
    started = WechatReconnectController(client, state_path=state_path).begin(
        request_id="request-disabled-preparing"
    )
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["phase"] = "preparing"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    controller = WechatReconnectController(client, state_path=state_path, sleep=lambda _: None)

    first = controller.recover(operation_id=str(started["operationId"]))
    second = controller.recover(operation_id=str(started["operationId"]))

    assert first is not None
    assert first["lastErrorCode"] == "WECHAT_RESTORE_OPERATION_MISSING"
    assert second is not None
    assert second["phase"] == "completed"
    assert second["outcome"] == "restored"
    assert [name for name, _ in client.calls].count("restore") == 0


def test_recovery_check_failure_preserves_awaiting_scan_for_manual_resume(tmp_path: Path) -> None:
    class _TransientInspectClient(_ReplacementClient):
        def __init__(self) -> None:
            super().__init__()
            self.fail_operation_inspect = False

        def weixin_replacement_inspect(self, *, operation_id):  # type: ignore[no-untyped-def]
            if operation_id is not None and self.fail_operation_inspect:
                self.fail_operation_inspect = False
                raise OSError("gateway restarting")
            if operation_id is not None:
                return {
                    "registeredAccountIds": [],
                    "storedAccountIds": ["old-account"],
                    "activeLoginCount": 1,
                    "operation": {"operationId": operation_id, "phase": "login_started"},
                }
            return super().weixin_replacement_inspect(operation_id=operation_id)

    client = _TransientInspectClient()
    state_path = tmp_path / ".ui-wechat-reconnect.json"
    started = WechatReconnectController(client, state_path=state_path).begin(
        request_id="request-recovery-awaiting"
    )
    controller = WechatReconnectController(client, state_path=state_path)
    client.fail_operation_inspect = True

    with pytest.raises(OSError, match="gateway restarting"):
        controller.recover()
    failed = controller.record_recovery_failure()
    resumed = controller.recover(operation_id=str(started["operationId"]))

    assert failed["phase"] == "needs_attention"
    assert resumed is not None
    assert resumed["phase"] == "awaiting_scan"


def test_unfinished_needs_attention_cannot_be_overwritten_by_new_request(tmp_path: Path) -> None:
    client = _ReplacementClient()
    state_path = tmp_path / ".ui-wechat-reconnect.json"
    started = WechatReconnectController(client, state_path=state_path).begin(
        request_id="request-original"
    )
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state.update(
        phase="needs_attention",
        candidateAccountId="candidate-new",
        loginReceiptId="receipt-new",
        lastErrorCode="WECHAT_COMMIT_INCOMPLETE",
    )
    state_path.write_text(json.dumps(state), encoding="utf-8")

    with pytest.raises(WechatReconnectBusy):
        WechatReconnectController(client, state_path=state_path).begin(
            request_id="request-new"
        )

    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    assert persisted["operationId"] == started["operationId"]
