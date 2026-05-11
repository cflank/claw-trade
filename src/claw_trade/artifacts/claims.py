from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from claw_trade.artifacts.refs import ApprovedMaterial, L1Claim, L2Index
from claw_trade.guards.common import GuardResult, guard_failed, guard_passed
from claw_trade.workflow.models import Stage, WorkerCall

CLAIM_SCHEMA_VERSION = "control.claims.v1"
MATERIAL_CLAIMS_SOURCE = "openclaw_openviking_write_material"
RUNTIME_MATERIAL_CLAIMS_SOURCE = "openclaw_runtime_material_save"
_ALLOWED_MATERIAL_CLAIMS_SOURCES = frozenset(
    {MATERIAL_CLAIMS_SOURCE, RUNTIME_MATERIAL_CLAIMS_SOURCE}
)
MATERIAL_CLAIMS_FILENAME = "material-claims.json"

_ALLOWED_CLAIM_KINDS = frozenset(
    {
        "news",
        "valuation",
        "target_price",
        "rating",
        "trade_action",
        "risk_condition",
        "sentiment",
        "chart",
        "tool_success",
        "source",
        "other",
    }
)
_HIGH_RISK_CLAIM_KINDS = (
    "valuation",
    "target_price",
    "rating",
    "trade_action",
    "risk_condition",
    "news",
    "sentiment",
    "chart",
    "tool_success",
    "source",
)


def high_risk_claim_kinds() -> tuple[str, ...]:
    return _HIGH_RISK_CLAIM_KINDS


@dataclass(frozen=True)
class ClaimBlockClaim:
    claim_id: str
    kind: str
    text: str
    value: str | None
    evidence_ids: tuple[str, ...]
    source_worker_id: str


@dataclass(frozen=True)
class ClaimBlock:
    schema_version: str
    run_id: str
    call_id: str
    worker_id: str
    stage: Stage
    material_id: str
    material_layer: str | None
    source_kind: str | None
    claims: tuple[ClaimBlockClaim, ...]


@dataclass(frozen=True)
class ClaimBlockResult:
    ok: bool
    block: ClaimBlock | None
    reason: str | None

    @classmethod
    def passed(cls, block: ClaimBlock) -> "ClaimBlockResult":
        return cls(ok=True, block=block, reason=None)

    @classmethod
    def failed(cls, reason: str) -> "ClaimBlockResult":
        return cls(ok=False, block=None, reason=reason)


@dataclass(frozen=True)
class MaterialClaimsEvidence:
    schema_version: str
    source: str
    run_id: str
    call_id: str
    worker_id: str
    stage: Stage
    material_id: str
    target_name: str
    l1_uri: str
    l1_sha256: str
    l1_size_bytes: int
    material_layer: str
    source_kind: str
    claims: tuple[L1Claim, ...]


@dataclass(frozen=True)
class MaterialClaimsEvidenceResult:
    ok: bool
    evidence: MaterialClaimsEvidence | None
    reason: str | None

    @classmethod
    def passed(cls, evidence: MaterialClaimsEvidence) -> "MaterialClaimsEvidenceResult":
        return cls(ok=True, evidence=evidence, reason=None)

    @classmethod
    def failed(cls, reason: str) -> "MaterialClaimsEvidenceResult":
        return cls(ok=False, evidence=None, reason=reason)


def parse_material_claims_evidence(path: Path) -> MaterialClaimsEvidenceResult:
    if not path.exists() or not path.is_file():
        return MaterialClaimsEvidenceResult.failed(f"{MATERIAL_CLAIMS_FILENAME} 不存在或不是文件")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return MaterialClaimsEvidenceResult.failed(f"{MATERIAL_CLAIMS_FILENAME} JSON 非法: {exc}")
    except OSError as exc:
        return MaterialClaimsEvidenceResult.failed(f"{MATERIAL_CLAIMS_FILENAME} 读取失败: {exc}")
    if not isinstance(payload, dict):
        return MaterialClaimsEvidenceResult.failed(f"{MATERIAL_CLAIMS_FILENAME} 根对象必须是 object")

    required_fields = (
        "schema_version",
        "source",
        "run_id",
        "call_id",
        "worker_id",
        "stage",
        "material_id",
        "target_name",
        "l1_uri",
        "l1_sha256",
        "l1_size_bytes",
        "material_layer",
        "source_kind",
        "claims",
    )
    for field in required_fields:
        if field not in payload:
            return MaterialClaimsEvidenceResult.failed(f"{MATERIAL_CLAIMS_FILENAME} 缺少字段: {field}")
    if payload["schema_version"] != CLAIM_SCHEMA_VERSION:
        return MaterialClaimsEvidenceResult.failed(f"{MATERIAL_CLAIMS_FILENAME} schema_version 不匹配")
    if payload["source"] not in _ALLOWED_MATERIAL_CLAIMS_SOURCES:
        return MaterialClaimsEvidenceResult.failed(f"{MATERIAL_CLAIMS_FILENAME} source 不匹配")
    try:
        stage = Stage(str(payload["stage"]))
    except ValueError:
        return MaterialClaimsEvidenceResult.failed(f"{MATERIAL_CLAIMS_FILENAME} stage 非法: {payload['stage']!r}")

    l1_size_bytes = payload.get("l1_size_bytes")
    if not isinstance(l1_size_bytes, int) or l1_size_bytes <= 0:
        return MaterialClaimsEvidenceResult.failed(f"{MATERIAL_CLAIMS_FILENAME} l1_size_bytes 必须是正整数")
    claims_data = payload.get("claims")
    if not isinstance(claims_data, list):
        return MaterialClaimsEvidenceResult.failed(f"{MATERIAL_CLAIMS_FILENAME} claims 必须是数组")
    claims: list[L1Claim] = []
    for idx, item in enumerate(claims_data):
        if not isinstance(item, dict):
            return MaterialClaimsEvidenceResult.failed(f"{MATERIAL_CLAIMS_FILENAME} claim[{idx}] 必须是对象")
        claim_result = _parse_claim_item(idx, item)
        if not claim_result.ok or claim_result.block is None:
            return MaterialClaimsEvidenceResult.failed(
                claim_result.reason or f"{MATERIAL_CLAIMS_FILENAME} claim[{idx}] 解析失败"
            )
        claims.append(_to_l1_claim(claim_result.block.claims[0]))

    return MaterialClaimsEvidenceResult.passed(
        MaterialClaimsEvidence(
            schema_version=CLAIM_SCHEMA_VERSION,
            source=str(payload["source"]),
            run_id=str(payload["run_id"]),
            call_id=str(payload["call_id"]),
            worker_id=str(payload["worker_id"]),
            stage=stage,
            material_id=str(payload["material_id"]),
            target_name=str(payload["target_name"]),
            l1_uri=str(payload["l1_uri"]),
            l1_sha256=str(payload["l1_sha256"]),
            l1_size_bytes=l1_size_bytes,
            material_layer=str(payload["material_layer"]),
            source_kind=str(payload["source_kind"]),
            claims=tuple(claims),
        )
    )


def require_material_claims_evidence(call: WorkerCall) -> tuple[MaterialClaimsEvidence | None, GuardResult]:
    evidence_dir = getattr(call, "evidence_dir", None)
    if not isinstance(evidence_dir, Path):
        return None, _material_claims_failed(call, "call.evidence_dir 缺失")
    path = evidence_dir / MATERIAL_CLAIMS_FILENAME
    parsed = parse_material_claims_evidence(path)
    if not parsed.ok or parsed.evidence is None:
        return None, _material_claims_failed(call, parsed.reason or "material-claims 解析失败")
    evidence = parsed.evidence
    if evidence.run_id != call.run_id:
        return None, _material_claims_failed(call, "material-claims run_id 与 call 不一致")
    if evidence.call_id != call.call_id:
        return None, _material_claims_failed(call, "material-claims call_id 与 call 不一致")
    if evidence.worker_id != call.worker_id:
        return None, _material_claims_failed(call, "material-claims worker_id 与 call 不一致")
    if evidence.stage != call.stage:
        return None, _material_claims_failed(call, "material-claims stage 与 call 不一致")
    for claim in evidence.claims:
        if not claim.claim_id.strip():
            return None, _material_claims_failed(call, "material-claims claim_id 不能为空")
    return evidence, guard_passed(category="material_claims")


def l1_has_manual_claim_block(l1_text: str) -> bool:
    # L1 是读者报告；若正文出现机器 claim block，说明回退到 LLM 手拼路径，必须拒绝。
    for raw in _iter_fenced_blocks(l1_text):
        if CLAIM_SCHEMA_VERSION in raw:
            return True
    return CLAIM_SCHEMA_VERSION in l1_text and _decode_json_object(l1_text.strip()) is not None


def parse_l1_claim_block(l1_text: str, material: ApprovedMaterial | None = None) -> ClaimBlockResult:
    payloads = _claim_block_payloads(l1_text)
    if not payloads:
        return ClaimBlockResult.failed("L1 缺少结构化 claim block")
    if len(payloads) > 1:
        return ClaimBlockResult.failed("L1 出现多个结构化 claim block")
    block_result = _parse_claim_block_payload(payloads[0])
    if not block_result.ok:
        return block_result
    block = block_result.block
    assert block is not None
    if material is not None:
        if block.run_id != material.run_id:
            return ClaimBlockResult.failed("claim block run_id 与 material 不一致")
        if block.call_id != material.call_id:
            return ClaimBlockResult.failed("claim block call_id 与 material 不一致")
        if block.worker_id != material.worker_id:
            return ClaimBlockResult.failed("claim block worker_id 与 material 不一致")
        if block.stage != material.stage:
            return ClaimBlockResult.failed("claim block stage 与 material 不一致")
        if block.material_id != material.material_id:
            return ClaimBlockResult.failed("claim block material_id 与 material 不一致")
    return block_result


def l1_declares_compact_material(l1_text: str) -> bool:
    parsed = parse_l1_claim_block(l1_text)
    if parsed.ok and parsed.block is not None:
        for marker in (parsed.block.material_layer, parsed.block.source_kind):
            if _is_compact_marker(marker):
                return True
    return _has_compact_summary_heading(l1_text)


def require_l1_claim_block(l1_text: str, call: WorkerCall) -> tuple[tuple[L1Claim, ...], GuardResult]:
    parsed = parse_l1_claim_block(l1_text)
    if not parsed.ok:
        return (), _claim_block_failed(call, parsed.reason or "claim block 解析失败")
    block = parsed.block
    assert block is not None
    identity_guard = validate_claim_block_identity(block, call)
    if not identity_guard.ok:
        return (), identity_guard
    claims = tuple(_to_l1_claim(item) for item in block.claims)
    return claims, guard_passed(category="claim_block")


def validate_claim_block_identity(block: ClaimBlock, call: WorkerCall) -> GuardResult:
    if block.run_id != call.run_id:
        return _claim_block_failed(call, "claim block run_id 与 call 不一致")
    if block.call_id != call.call_id:
        return _claim_block_failed(call, "claim block call_id 与 call 不一致")
    if block.worker_id != call.worker_id:
        return _claim_block_failed(call, "claim block worker_id 与 call 不一致")
    if block.stage != call.stage:
        return _claim_block_failed(call, "claim block stage 与 call 不一致")
    if not block.material_id.startswith("mat-"):
        return _claim_block_failed(call, "claim block material_id 非法")
    for claim in block.claims:
        if claim.source_worker_id != call.worker_id:
            return _claim_block_failed(call, f"claim {claim.claim_id} source_worker_id 与 call 不一致")
    return guard_passed(category="claim_block_identity")


def validate_claims(claims: tuple[L1Claim, ...], l2_index: L2Index) -> GuardResult:
    l2_evidence_ids = {entry.evidence_id for entry in l2_index.entries}
    for claim in claims:
        if claim.kind not in _ALLOWED_CLAIM_KINDS:
            return guard_failed(
                category="claim",
                reason=f"claim kind 不支持: {claim.kind}",
                paths=(Path("l1_text"),),
            )
        if claim.kind in high_risk_claim_kinds():
            if not claim.evidence_ids:
                return guard_failed(
                    category="claim",
                    reason=f"高风险声明缺少 evidence_ids: {claim.claim_id}",
                    paths=(Path("l1_text"),),
                )
            missing = tuple(evidence_id for evidence_id in claim.evidence_ids if evidence_id not in l2_evidence_ids)
            if missing:
                return guard_failed(
                    category="claim",
                    reason=f"高风险声明缺少 L2 映射: {claim.claim_id} -> {', '.join(missing)}",
                    paths=(Path("l1_text"),),
                )
    return guard_passed(category="claim")


def _claim_block_failed(call: WorkerCall, reason: str) -> GuardResult:
    return guard_failed(category="claim_block", reason=reason, paths=_call_paths(call))


def _material_claims_failed(call: WorkerCall, reason: str) -> GuardResult:
    return guard_failed(category="claim", reason=reason, paths=_material_claims_paths(call))


def _call_paths(call: WorkerCall) -> tuple[Path, ...]:
    evidence_dir = getattr(call, "evidence_dir", None)
    if isinstance(evidence_dir, Path):
        return (evidence_dir / "openviking_l1.md",)
    return (Path("l1_text"),)


def _material_claims_paths(call: WorkerCall) -> tuple[Path, ...]:
    evidence_dir = getattr(call, "evidence_dir", None)
    if isinstance(evidence_dir, Path):
        return (evidence_dir / MATERIAL_CLAIMS_FILENAME,)
    return (Path(MATERIAL_CLAIMS_FILENAME),)


def _to_l1_claim(claim: ClaimBlockClaim) -> L1Claim:
    required = ("l2_evidence",) if claim.kind in high_risk_claim_kinds() else ()
    return L1Claim(
        claim_id=claim.claim_id,
        kind=claim.kind,
        text=claim.text,
        value=claim.value,
        required_evidence_kinds=required,
        evidence_ids=claim.evidence_ids,
    )


def _claim_block_payloads(l1_text: str) -> tuple[dict[str, Any], ...]:
    payloads: list[dict[str, Any]] = []
    for raw in _iter_fenced_blocks(l1_text):
        parsed = _decode_json_object(raw)
        if parsed and parsed.get("schema_version") == CLAIM_SCHEMA_VERSION:
            payloads.append(parsed)
    parsed_full = _decode_json_object(l1_text.strip())
    if parsed_full and parsed_full.get("schema_version") == CLAIM_SCHEMA_VERSION:
        payloads.append(parsed_full)
    return tuple(payloads)


def _iter_fenced_blocks(text: str) -> tuple[str, ...]:
    lines = text.splitlines()
    blocks: list[str] = []
    in_fence = False
    current: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            if not in_fence:
                in_fence = True
                current = []
                continue
            blocks.append("\n".join(current).strip())
            in_fence = False
            current = []
            continue
        if in_fence:
            current.append(line)
    return tuple(blocks)


def _decode_json_object(raw: str) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    return data


def _parse_claim_block_payload(payload: dict[str, Any]) -> ClaimBlockResult:
    required_fields = ("schema_version", "run_id", "call_id", "worker_id", "stage", "material_id", "claims")
    for field in required_fields:
        if field not in payload:
            return ClaimBlockResult.failed(f"claim block 缺少字段: {field}")
    if payload["schema_version"] != CLAIM_SCHEMA_VERSION:
        return ClaimBlockResult.failed("claim block schema_version 不匹配")
    try:
        stage = Stage(str(payload["stage"]))
    except ValueError:
        return ClaimBlockResult.failed(f"claim block stage 非法: {payload['stage']!r}")
    claims_data = payload.get("claims")
    if not isinstance(claims_data, list):
        return ClaimBlockResult.failed("claim block claims 字段必须是数组")
    claims: list[ClaimBlockClaim] = []
    for idx, item in enumerate(claims_data):
        if not isinstance(item, dict):
            return ClaimBlockResult.failed(f"claim[{idx}] 必须是对象")
        claim_result = _parse_claim_item(idx, item)
        if not claim_result.ok:
            return claim_result
        assert claim_result.block is not None
        claims.append(claim_result.block.claims[0])
    return ClaimBlockResult.passed(
        ClaimBlock(
            schema_version=CLAIM_SCHEMA_VERSION,
            run_id=str(payload["run_id"]),
            call_id=str(payload["call_id"]),
            worker_id=str(payload["worker_id"]),
            stage=stage,
            material_id=str(payload["material_id"]),
            material_layer=_optional_text(payload.get("material_layer")),
            source_kind=_optional_text(payload.get("source_kind")),
            claims=tuple(claims),
        )
    )


def _parse_claim_item(idx: int, item: dict[str, Any]) -> ClaimBlockResult:
    fields = ("claim_id", "kind", "text", "evidence_ids", "source_worker_id")
    for field in fields:
        if field not in item:
            return ClaimBlockResult.failed(f"claim[{idx}] 缺少字段: {field}")
    kind = str(item["kind"])
    if kind not in _ALLOWED_CLAIM_KINDS:
        return ClaimBlockResult.failed(f"claim[{idx}] kind 不支持: {kind}")
    evidence_raw = item["evidence_ids"]
    if not isinstance(evidence_raw, list):
        return ClaimBlockResult.failed(f"claim[{idx}] evidence_ids 必须是数组")
    evidence_ids = tuple(str(evidence_id) for evidence_id in evidence_raw)
    if any(not evidence_id.strip() for evidence_id in evidence_ids):
        return ClaimBlockResult.failed(f"claim[{idx}] evidence_ids 存在空值")
    value = item.get("value")
    value_text: str | None
    if value is None:
        value_text = None
    elif isinstance(value, str):
        value_text = value
    else:
        value_text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    claim = ClaimBlockClaim(
        claim_id=str(item["claim_id"]),
        kind=kind,
        text=str(item["text"]),
        value=value_text,
        evidence_ids=evidence_ids,
        source_worker_id=str(item["source_worker_id"]),
    )
    # 复用 ClaimBlockResult，避免新增一套中间结果类型。
    return ClaimBlockResult.passed(
        ClaimBlock(
            schema_version=CLAIM_SCHEMA_VERSION,
            run_id="",
            call_id="",
            worker_id="",
            stage=Stage.FRONTLINE,
            material_id="",
            material_layer=None,
            source_kind=None,
            claims=(claim,),
        )
    )


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _is_compact_marker(value: str | None) -> bool:
    if value is None:
        return False
    normalized = value.strip().lower().replace("-", "_")
    return normalized in {"compact", "compact_summary"}


def _has_compact_summary_heading(l1_text: str) -> bool:
    for line in l1_text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith("#"):
            return False
        heading = stripped.lstrip("#").strip().lower().replace("-", "_")
        return heading in {"compact", "compact_summary"}
    return False
