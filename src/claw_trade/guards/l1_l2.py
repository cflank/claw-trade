from __future__ import annotations

from pathlib import Path

from claw_trade.artifacts.claims import (
    l1_declares_compact_material,
    l1_has_manual_claim_block,
    require_material_claims_evidence,
    validate_claims,
)
from claw_trade.artifacts.openviking_client import OpenVikingClient
from claw_trade.artifacts.refs import L1Claim, L2Index, OpenVikingReadCapability, VikingUri
from claw_trade.guards.common import GuardResult, guard_failed, guard_passed
from claw_trade.workflow.models import WorkerCall


def validate_l1_l2_contract(
    call: WorkerCall,
    l1_text: str,
    raw_output: str,
    l2_index: L2Index,
) -> tuple[tuple[L1Claim, ...], GuardResult]:
    del raw_output
    if not l1_text.strip():
        return (), guard_failed(category="l1_l2", reason="L1 不能为空", paths=_l1_paths(call))
    if l1_declares_compact_material(l1_text):
        return (), guard_failed(category="l1_l2", reason="L1 不能是 compact 摘要", paths=_l1_paths(call))
    if l1_has_manual_claim_block(l1_text):
        # 越权边界：L1 正文只给读者，不接受 LLM 在正文手拼机器 claim block。
        return (), guard_failed(
            category="l1_l2",
            reason="L1 正文包含手拼 control.claims.v1 机器块，必须改为 material-claims.json",
            paths=_l1_paths(call),
        )
    if _has_schema_in_fenced_json(l1_text, "control.pm_decision.v1"):
        return (), guard_failed(
            category="l1_l2",
            reason="L1 正文包含手拼 control.pm_decision.v1 机器块，必须改为 pm-decision.json",
            paths=_l1_paths(call),
        )

    # 只信工具落盘后的结构化证据文件，禁止从 L1 正文抽取或猜测 claims。
    material_claims, claims_guard = require_material_claims_evidence(call=call)
    if not claims_guard.ok or material_claims is None:
        return (), claims_guard
    claim_guard = validate_claims(claims=material_claims.claims, l2_index=l2_index)
    if not claim_guard.ok:
        return material_claims.claims, claim_guard
    return material_claims.claims, guard_passed(category="l1_l2")


def validate_l2_entries(
    client: OpenVikingClient,
    l2_index: L2Index,
    allowed_prefix: VikingUri,
) -> GuardResult:
    index_paths = _l2_index_paths(l2_index)
    if not allowed_prefix.strip():
        return guard_failed(
            category="l1_l2",
            reason="L2 allowed_prefix 不能为空",
            paths=index_paths,
        )
    if l2_index.index_uri is None:
        return guard_failed(
            category="l1_l2",
            reason="L2 index_uri 不能为空",
            paths=index_paths,
        )
    if not l2_index.entries and not l2_index.empty_reason:
        return guard_failed(
            category="l1_l2",
            reason="L2 为空时必须提供 empty_reason",
            paths=index_paths,
        )
    if not l2_index.index_uri.startswith(allowed_prefix):
        return guard_failed(
            category="l1_l2",
            reason=f"L2 index_uri 超出允许前缀: {l2_index.index_uri}",
            paths=index_paths,
        )
    capability = _validation_capability(allowed_prefix)
    if isinstance(capability, GuardResult):
        return capability

    index_guard = _verify_uri_sha_size(
        client=client,
        capability=capability,
        uri=l2_index.index_uri,
        sha256=l2_index.index_sha256,
        size_bytes=l2_index.index_size_bytes,
        label="L2 index",
        paths=index_paths,
    )
    if not index_guard.ok:
        return index_guard

    seen: set[str] = set()
    for entry in l2_index.entries:
        if entry.evidence_id in seen:
            return guard_failed(
                category="l1_l2",
                reason=f"L2 evidence_id 重复: {entry.evidence_id}",
                paths=index_paths,
            )
        seen.add(entry.evidence_id)
        if not entry.uri.startswith(allowed_prefix):
            return guard_failed(
                category="l1_l2",
                reason=f"L2 entry URI 超出允许前缀: {entry.uri}",
                paths=index_paths,
            )
        verify_guard = _verify_uri_sha_size(
            client=client,
            capability=capability,
            uri=entry.uri,
            sha256=entry.sha256,
            size_bytes=entry.size_bytes,
            label=f"L2 entry {entry.evidence_id}",
            paths=index_paths,
        )
        if not verify_guard.ok:
            return verify_guard
    return guard_passed(category="l1_l2")


def _verify_uri_sha_size(
    *,
    client: OpenVikingClient,
    capability: OpenVikingReadCapability,
    uri: VikingUri,
    sha256: str | None,
    size_bytes: int | None,
    label: str,
    paths: tuple[Path, ...],
) -> GuardResult:
    if not sha256 or size_bytes is None or size_bytes <= 0:
        return guard_failed(
            category="l1_l2",
            reason=f"{label} 缺少有效 sha/size",
            paths=paths,
        )
    stat = client.stat_with_capability(capability, uri)
    if not stat.ok:
        return guard_failed(
            category="l1_l2",
            reason=f"OpenViking stat 失败: {uri} ({stat.error_category}:{stat.error_message})",
            paths=paths,
        )
    read = client.read_with_capability(capability, uri, expected_sha256=sha256)
    if not read.ok:
        return guard_failed(
            category="l1_l2",
            reason=f"OpenViking read 失败: {uri} ({read.error_category}:{read.error_message})",
            paths=paths,
        )
    if stat.size_bytes is None or stat.size_bytes <= 0:
        return guard_failed(
            category="l1_l2",
            reason=f"{label} stat.size_bytes 必须大于 0: {uri}",
            paths=paths,
        )
    if read.size_bytes is None or read.size_bytes <= 0:
        return guard_failed(
            category="l1_l2",
            reason=f"{label} read.size_bytes 必须大于 0: {uri}",
            paths=paths,
        )
    return guard_passed(category="l1_l2")


def _validation_capability(allowed_prefix: VikingUri) -> OpenVikingReadCapability | GuardResult:
    l1_uri = _l1_uri_for_l2_prefix(allowed_prefix)
    if l1_uri is None:
        return guard_failed(
            category="l1_l2",
            reason=f"L2 allowed_prefix 不是本 call 的 evidence 前缀: {allowed_prefix}",
            paths=(Path("l2_index.json"),),
        )
    return OpenVikingReadCapability(
        capability_id="l2-validation",
        material_id="material-under-validation",
        allowed_l1_uri=l1_uri,
        allowed_l1_sha256="l2-validation",
        allowed_l2_prefix=allowed_prefix,
        manifest_entry_sha256="l2-validation",
    )


def _l1_uri_for_l2_prefix(allowed_prefix: VikingUri) -> VikingUri | None:
    root = "viking://resources/workflow/"
    suffix = "/evidence/"
    if not allowed_prefix.startswith(root) or not allowed_prefix.endswith(suffix):
        return None
    identity = allowed_prefix[len(root) : -len(suffix)]
    parts = identity.split("/")
    if len(parts) != 4 or any(not part.strip() for part in parts):
        return None
    return f"{allowed_prefix[: -len(suffix)]}/report.md"


def _l1_paths(call: WorkerCall) -> tuple[Path, ...]:
    evidence_dir = getattr(call, "evidence_dir", None)
    if isinstance(evidence_dir, Path):
        return (evidence_dir / "openviking_l1.md",)
    return (Path("openviking_l1.md"),)


def _l2_index_paths(l2_index: L2Index) -> tuple[Path, ...]:
    if l2_index.index_uri:
        return (Path(l2_index.index_uri),)
    return (Path("l2_index.json"),)


def _has_schema_in_fenced_json(text: str, schema_version: str) -> bool:
    in_fence = False
    buffer: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            if not in_fence:
                in_fence = True
                buffer = []
                continue
            block = "\n".join(buffer)
            if schema_version in block:
                return True
            in_fence = False
            buffer = []
            continue
        if in_fence:
            buffer.append(line)
    return False
