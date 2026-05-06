from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from claw_trade.guards.common import GuardResult
from claw_trade.workflow.models import Stage

VikingUri = str


@dataclass(frozen=True)
class MaterialTarget:
    run_id: str
    call_id: str
    worker_id: str
    stage: Stage
    target_name: str
    l1_uri: VikingUri
    l2_prefix: VikingUri


@dataclass(frozen=True)
class MaterialReadRef:
    material_id: str
    capability_id: str
    worker_id: str
    stage: Stage
    l1_uri: VikingUri
    l1_sha256: str
    l2_index_uri: VikingUri | None
    l2_allowed_prefix: VikingUri | None
    call_id: str


@dataclass(frozen=True)
class OpenVikingReadCapability:
    capability_id: str
    material_id: str
    allowed_l1_uri: VikingUri
    allowed_l1_sha256: str
    allowed_l2_prefix: VikingUri | None
    manifest_entry_sha256: str
    allowed_l2_index_sha256: str | None = None


@dataclass(frozen=True)
class L2Entry:
    evidence_id: str
    uri: VikingUri
    kind: str
    source: str
    sha256: str
    size_bytes: int


@dataclass(frozen=True)
class L2Index:
    entries: tuple[L2Entry, ...]
    empty_reason: str | None
    index_uri: VikingUri | None
    index_sha256: str | None
    index_size_bytes: int | None


@dataclass(frozen=True)
class L1Claim:
    claim_id: str
    kind: str
    text: str
    value: str | None
    required_evidence_kinds: tuple[str, ...]
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class MaterialReceiptVerification:
    verified: bool | None
    method: str | None


@dataclass(frozen=True)
class MaterialReceipt:
    uri: VikingUri
    run_id: str
    call_id: str
    worker_id: str
    stage: Stage
    target_name: str
    sha256: str
    size_bytes: int
    written_at: str
    receipt_id: str | None
    source: str | None = None
    receipt_label: str | None = None
    receipt_origin: str | None = None
    is_openviking_native_receipt: bool | None = None
    verification: MaterialReceiptVerification | None = None


@dataclass(frozen=True)
class ApprovedMaterial:
    material_id: str
    run_id: str
    call_id: str
    worker_id: str
    stage: Stage
    target_name: str
    l1_uri: VikingUri
    l1_sha256: str
    l1_size_bytes: int
    l2_index_uri: VikingUri | None
    l2_index: L2Index
    l1_claims: tuple[L1Claim, ...]
    approved_at: str
    hard_gate_result_path: Path


def make_material_target(
    run_id: str,
    stage: Stage,
    worker_id: str,
    call_id: str,
    target_name: str = "report",
) -> MaterialTarget:
    if not run_id.strip() or not worker_id.strip() or not call_id.strip():
        raise ValueError("run_id/worker_id/call_id 不能为空")
    if not target_name.strip():
        raise ValueError("target_name 不能为空")
    if "/" in target_name or target_name in {".", ".."}:
        raise ValueError("target_name 不能包含路径分隔符")
    stage_text = stage.value
    base = f"viking://resources/workflow/{run_id}/{stage_text}/{worker_id}/{call_id}"
    return MaterialTarget(
        run_id=run_id,
        call_id=call_id,
        worker_id=worker_id,
        stage=stage,
        target_name=target_name,
        l1_uri=f"{base}/{target_name}.md",
        l2_prefix=f"{base}/evidence/",
    )


def validate_viking_uri_shape(
    uri: VikingUri,
    run_id: str,
    stage: Stage,
    worker_id: str,
    call_id: str,
) -> GuardResult:
    # 这里是 OpenViking 正式路径边界：必须精确绑定 run/stage/worker/call，防止 latest/list/目录扫描越权。
    raw = uri.strip()
    if not raw:
        return GuardResult.failed("invalid_uri", "URI 不能为空", paths=())
    if not raw.startswith("viking://resources/workflow/"):
        return GuardResult.failed("invalid_uri", f"URI 前缀非法: {uri}", paths=())
    for forbidden in ("/latest", "/list", "/compact", "/scan", "/../", "/./"):
        if forbidden in raw:
            return GuardResult.failed("invalid_uri", f"URI 含禁止片段: {forbidden}", paths=())

    tail = raw[len("viking://resources/workflow/") :]
    parts = tail.split("/")
    if len(parts) < 5:
        return GuardResult.failed("invalid_uri", f"URI 层级不足: {uri}", paths=())
    if parts[0] != run_id:
        return GuardResult.failed("invalid_uri", f"URI run_id 不匹配: {parts[0]}", paths=())
    if parts[1] != stage.value:
        return GuardResult.failed("invalid_uri", f"URI stage 不匹配: {parts[1]}", paths=())
    if parts[2] != worker_id:
        return GuardResult.failed("invalid_uri", f"URI worker_id 不匹配: {parts[2]}", paths=())
    if parts[3] != call_id:
        return GuardResult.failed("invalid_uri", f"URI call_id 不匹配: {parts[3]}", paths=())

    artifact_parts = parts[4:]
    if artifact_parts[0] == "evidence":
        if len(artifact_parts) < 2 or not artifact_parts[1]:
            return GuardResult.failed("invalid_uri", f"URI evidence 路径缺少对象: {uri}", paths=())
    else:
        if len(artifact_parts) != 1:
            return GuardResult.failed("invalid_uri", f"URI 正式材料路径非法: {uri}", paths=())
        leaf = artifact_parts[0]
        if not leaf.endswith(".md") or leaf == ".md":
            return GuardResult.failed("invalid_uri", f"URI L1 文件名非法: {leaf}", paths=())
    if raw.endswith("/"):
        return GuardResult.failed("invalid_uri", f"URI 不能是目录路径: {uri}", paths=())
    return GuardResult.passed("ok")
