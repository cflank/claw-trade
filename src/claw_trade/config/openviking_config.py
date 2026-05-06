from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol

from claw_trade.guards.common import BootResult


class OpenVikingClient(Protocol):
    def read(self, uri: str) -> bytes: ...

    def stat(self, uri: str) -> object: ...

    def read_receipt(self, receipt_path: Path) -> object: ...


@dataclass(frozen=True)
class OpenVikingConfig:
    endpoint: str
    workspace: str
    long_term_memory_capability: str | None


@dataclass(frozen=True)
class OpenVikingConfigResult:
    ok: bool
    config: OpenVikingConfig | None
    reason: str | None


def load_openviking_config() -> OpenVikingConfigResult:
    endpoint = os.environ.get("OPENVIKING_ENDPOINT", "").strip()
    workspace = os.environ.get("OPENVIKING_WORKSPACE", "").strip()
    capability = os.environ.get("OPENVIKING_LONG_TERM_MEMORY_CAPABILITY", "").strip() or None

    if not endpoint:
        return OpenVikingConfigResult(ok=False, config=None, reason="OPENVIKING_ENDPOINT is required")
    if not workspace:
        return OpenVikingConfigResult(ok=False, config=None, reason="OPENVIKING_WORKSPACE is required")

    return OpenVikingConfigResult(
        ok=True,
        config=OpenVikingConfig(
            endpoint=endpoint,
            workspace=workspace,
            long_term_memory_capability=capability,
        ),
        reason=None,
    )


def probe_openviking_contract(client: OpenVikingClient) -> BootResult:
    # 这里是 OpenViking 合同硬边界：必须真实探测 read/stat/receipt，任一失败都要阻断，不能靠 callable 假通过。
    unified_probe = getattr(client, "probe_read_stat_receipt", None)
    if callable(unified_probe):
        try:
            probe_result = unified_probe()
        except Exception as exc:
            return BootResult.blocked(
                category="openviking_contract",
                reason=f"probe_read_stat_receipt raised: {exc}",
            )
        probe_ok, probe_reason = _extract_probe_ok_and_reason(probe_result)
        if not probe_ok:
            return BootResult.blocked(
                category="openviking_contract",
                reason=probe_reason or "probe_read_stat_receipt returned failure",
            )
        return BootResult.ok_result()

    required_methods = ("read", "stat", "read_receipt")
    for method_name, probe_arg in (
        ("read", "viking://probe/read"),
        ("stat", "viking://probe/stat"),
        ("read_receipt", Path("runs/probe/openviking/receipt.json")),
    ):
        method = getattr(client, method_name, None)
        if method is None or not callable(method):
            return BootResult.blocked(
                category="openviking_contract",
                reason=f"missing required OpenViking method: {method_name}",
            )
        try:
            probe_result = method(probe_arg)
        except Exception as exc:
            return BootResult.blocked(
                category="openviking_contract",
                reason=f"OpenViking probe call failed: {method_name} ({exc})",
            )
        if probe_result is None:
            return BootResult.blocked(
                category="openviking_contract",
                reason=f"OpenViking probe call returned None: {method_name}",
            )

    return BootResult.ok_result()


def _extract_probe_ok_and_reason(result: object) -> tuple[bool, str | None]:
    # 统一 probe 的成功必须是结构化明确信号，禁止“返回了一个对象”就当作通过。
    try:
        if result is None:
            return False, "probe returned None"
        if isinstance(result, bool):
            return result, None if result else "probe returned False"
        if isinstance(result, Mapping):
            if "ok" not in result:
                return False, "probe mapping missing bool ok"
            ok_value = result.get("ok")
            if not isinstance(ok_value, bool):
                return False, "probe mapping ok is not bool"
            reason = result.get("reason")
            return ok_value, str(reason) if reason is not None else None
        ok_value = getattr(result, "ok", None)
        if ok_value is None:
            return False, "probe object missing bool ok"
        if not isinstance(ok_value, bool):
            return False, "probe object ok is not bool"
        reason = getattr(result, "reason", None)
        return ok_value, str(reason) if reason is not None else None
    except Exception as exc:
        return False, f"probe result inspect raised: {exc}"
