from __future__ import annotations

from pathlib import Path

from claw_trade.artifacts.openviking_client import OpenVikingClient
from claw_trade.artifacts.refs import MaterialReceipt, MaterialTarget
from claw_trade.guards.common import GuardResult, guard_failed, guard_passed
from claw_trade.workflow.models import WorkerCall

_EXPECTED_RECEIPT_SOURCE = "openviking_adapter_verified_receipt"
_EXPECTED_RECEIPT_LABEL = "verified_openviking_write_receipt"
_EXPECTED_RECEIPT_ORIGIN = "adapter_verified_non_native"
_EXPECTED_VERIFICATION_METHODS = frozenset(
    {
        "openviking_write_then_stat_then_readback_sha_size_identity_check",
        "openviking_write_then_stat_then_downloadback_sha_size_identity_check",
    }
)


def validate_openviking_receipt(
    call: WorkerCall,
    receipt: MaterialReceipt,
    client: OpenVikingClient,
) -> GuardResult:
    target = call.material_target
    adapter_verified_error = _adapter_verified_receipt_error(receipt)
    if adapter_verified_error is not None:
        return _fail(adapter_verified_error, call)

    identity_error = _receipt_identity_error(receipt=receipt, target=target)
    if identity_error is not None:
        return _fail(identity_error, call)

    # 这里必须走真实 OpenViking stat/read 复核，防止“只有本地 receipt 文件”的假成功路径。
    stat = client.stat_for_receipt_verification(receipt, target)
    if not stat.ok:
        reason = stat.error_message or "receipt stat 复核失败"
        return _fail(f"receipt stat 复核失败: {stat.error_category}:{reason}", call)

    read_result = client.read_for_receipt_verification(receipt, target)
    if not read_result.ok:
        reason = read_result.error_message or "receipt read 复核失败"
        return _fail(f"receipt read 复核失败: {read_result.error_category}:{reason}", call)

    return guard_passed(category="openviking_receipt")


def _receipt_identity_error(receipt: MaterialReceipt, target: MaterialTarget) -> str | None:
    checks = (
        ("uri", receipt.uri, target.l1_uri),
        ("run_id", receipt.run_id, target.run_id),
        ("call_id", receipt.call_id, target.call_id),
        ("worker_id", receipt.worker_id, target.worker_id),
        ("stage", receipt.stage.value, target.stage.value),
        ("target_name", receipt.target_name, target.target_name),
    )
    for name, actual, expected in checks:
        if actual != expected:
            return f"receipt.{name} 与 material_target 不一致: actual={actual!r} expected={expected!r}"
    return None


def _adapter_verified_receipt_error(receipt: MaterialReceipt) -> str | None:
    checks = (
        ("source", receipt.source, _EXPECTED_RECEIPT_SOURCE),
        ("receipt_label", receipt.receipt_label, _EXPECTED_RECEIPT_LABEL),
        ("receipt_origin", receipt.receipt_origin, _EXPECTED_RECEIPT_ORIGIN),
    )
    for name, actual, expected in checks:
        if actual != expected:
            return f"receipt.{name} 非法: actual={actual!r} expected={expected!r}"

    if receipt.is_openviking_native_receipt is not False:
        return (
            "receipt.is_openviking_native_receipt 必须为 False: "
            f"actual={receipt.is_openviking_native_receipt!r}"
        )

    if receipt.verification is None:
        return "receipt.verification 缺失"
    if receipt.verification.verified is not True:
        return (
            "receipt.verification.verified 必须为 True: "
            f"actual={receipt.verification.verified!r}"
        )
    if receipt.verification.method not in _EXPECTED_VERIFICATION_METHODS:
        return (
            "receipt.verification.method 非法: "
            f"actual={receipt.verification.method!r} expected_one_of={sorted(_EXPECTED_VERIFICATION_METHODS)!r}"
        )
    return None


def _fail(reason: str, call: WorkerCall) -> GuardResult:
    return guard_failed(
        category="openviking_receipt",
        reason=reason,
        paths=_receipt_paths(call),
    )


def _receipt_paths(call: WorkerCall) -> tuple[Path, ...]:
    return (call.evidence_dir / "openviking-receipt.json",)
