from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from typing import Any

from claw_trade.guards.common import GuardResult, guard_failed, guard_passed
from claw_trade.runtime.evidence_reader import ProviderEvidence
from claw_trade.workflow.models import WorkerCall


def validate_workspace_evidence(
    call: WorkerCall,
    evidence: ProviderEvidence,
    agents_root: Path,
) -> GuardResult:
    path = evidence.workspace_evidence_path
    payload_result = _read_json_object(path)
    if isinstance(payload_result, GuardResult):
        return payload_result
    payload = payload_result

    source = payload.get("source")
    # workspace 证据必须由 OpenClaw workspace loader 产出，不能用 Python 自写摘要冒充。
    if source != "openclaw_workspace_loader":
        return guard_failed(
            category="workspace_evidence",
            reason=f"workspace evidence source 非法: {source!r}",
            paths=(path,),
            early_stop=True,
        )

    identity_guard = _require_match_fields(call, evidence, payload, path)
    if not identity_guard.ok:
        return identity_guard

    expected_worker_root = (agents_root / call.worker_id).resolve()
    block_guard = _require_sha_block(
        payload=payload,
        block_name="identity",
        expected_worker_root=expected_worker_root,
        worker_id=call.worker_id,
        expected_relative_path=("IDENTITY.md",),
        file_path=path,
    )
    if not block_guard.ok:
        return block_guard
    block_guard = _require_sha_block(
        payload=payload,
        block_name="skills_manifest",
        expected_worker_root=expected_worker_root,
        worker_id=call.worker_id,
        expected_relative_path=("skills", "manifest.yaml"),
        file_path=path,
    )
    if not block_guard.ok:
        return block_guard
    block_guard = _require_sha_block(
        payload=payload,
        block_name="stage_policy",
        expected_worker_root=expected_worker_root,
        worker_id=call.worker_id,
        expected_relative_path=("STAGES.yaml",),
        file_path=path,
    )
    if not block_guard.ok:
        return block_guard

    stage_policy = payload["stage_policy"]
    if not isinstance(stage_policy, dict):
        return guard_failed(
            category="workspace_evidence",
            reason="stage_policy 必须是 object",
            paths=(path,),
            early_stop=True,
        )
    selected_stage = stage_policy.get("selected_stage")
    if not isinstance(selected_stage, str) or selected_stage.strip() == "":
        return guard_failed(
            category="workspace_evidence",
            reason="stage_policy.selected_stage 缺失或为空",
            paths=(path,),
            early_stop=True,
        )
    if selected_stage != call.stage.value:
        return guard_failed(
            category="workspace_evidence",
            reason=f"stage_policy.selected_stage 不匹配: {selected_stage} != {call.stage.value}",
            paths=(path,),
            early_stop=True,
        )

    assets = payload.get("text_assets")
    if not isinstance(assets, list) or not assets:
        return guard_failed(
            category="workspace_evidence",
            reason="text_assets 缺失或为空",
            paths=(path,),
            early_stop=True,
        )
    for index, asset in enumerate(assets):
        if not isinstance(asset, dict):
            return guard_failed(
                category="workspace_evidence",
                reason=f"text_assets[{index}] 必须是 object",
                paths=(path,),
                early_stop=True,
            )
        path_value = asset.get("path")
        sha_value = asset.get("sha256")
        if not isinstance(path_value, str) or path_value.strip() == "":
            return guard_failed(
                category="workspace_evidence",
                reason=f"text_assets[{index}].path 缺失或为空",
                paths=(path,),
                early_stop=True,
            )
        if _resolve_worker_path(path_value, expected_worker_root, call.worker_id) is None:
            return guard_failed(
                category="workspace_evidence",
                reason=f"text_assets[{index}].path 不属于当前 worker: {path_value}",
                paths=(path,),
                early_stop=True,
            )
        if not isinstance(sha_value, str) or sha_value.strip() == "":
            return guard_failed(
                category="workspace_evidence",
                reason=f"text_assets[{index}].sha256 缺失或为空",
                paths=(path,),
                early_stop=True,
            )

    # 这里只验证 OpenClaw 已落盘的 workspace evidence，不读取/扫描 worker 文案内容。
    return guard_passed(category="workspace_evidence")


def _require_match_fields(
    call: WorkerCall,
    evidence: ProviderEvidence,
    payload: dict[str, object],
    path: Path,
) -> GuardResult:
    # 绑定 run/call/worker/stage/profile，可阻断“同 worker 不同阶段”或“同阶段不同 call”的证据串用。
    checks: tuple[tuple[str, str], ...] = (
        ("run_id", call.run_id),
        ("call_id", call.call_id),
        ("worker_id", call.worker_id),
        ("stage", call.stage.value),
        ("profile", call.profile),
        ("openclaw_run_id", evidence.openclaw_run_id),
    )
    for field, expected in checks:
        value = payload.get(field)
        if not isinstance(value, str) or value.strip() == "":
            return guard_failed(
                category="workspace_evidence",
                reason=f"{field} 缺失或为空",
                paths=(path,),
                early_stop=True,
            )
        if value != expected:
            return guard_failed(
                category="workspace_evidence",
                reason=f"{field} 不匹配: {value} != {expected}",
                paths=(path,),
                early_stop=True,
            )
    return guard_passed(category="workspace_evidence")


def _require_sha_block(
    *,
    payload: dict[str, object],
    block_name: str,
    expected_worker_root: Path,
    worker_id: str,
    expected_relative_path: tuple[str, ...],
    file_path: Path,
) -> GuardResult:
    # path 负责定位到当前 worker 的固定文件，sha256 负责证明内容未被替换，二者缺一不可。
    block = payload.get(block_name)
    if not isinstance(block, dict):
        return guard_failed(
            category="workspace_evidence",
            reason=f"{block_name} 缺失或不是 object",
            paths=(file_path,),
            early_stop=True,
        )
    path_value = block.get("path")
    sha_value = block.get("sha256")
    if not isinstance(path_value, str) or path_value.strip() == "":
        return guard_failed(
            category="workspace_evidence",
            reason=f"{block_name}.path 缺失或为空",
            paths=(file_path,),
            early_stop=True,
        )
    resolved_path = _resolve_worker_path(path_value, expected_worker_root, worker_id)
    if resolved_path is None:
        return guard_failed(
            category="workspace_evidence",
            reason=f"{block_name}.path 不属于当前 worker: {path_value}",
            paths=(file_path,),
            early_stop=True,
        )
    expected_path = (expected_worker_root / Path(*expected_relative_path)).resolve()
    if resolved_path != expected_path:
        expected_tail = "/".join(expected_relative_path)
        return guard_failed(
            category="workspace_evidence",
            reason=f"{block_name}.path 必须指向当前 worker 的 {expected_tail}: {path_value}",
            paths=(file_path,),
            early_stop=True,
        )
    if not isinstance(sha_value, str) or sha_value.strip() == "":
        return guard_failed(
            category="workspace_evidence",
            reason=f"{block_name}.sha256 缺失或为空",
            paths=(file_path,),
            early_stop=True,
        )
    return guard_passed(category="workspace_evidence")


def _read_json_object(path: Path) -> dict[str, object] | GuardResult:
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        return guard_failed(
            category="workspace_evidence",
            reason=f"workspace evidence 读取失败: {exc}",
            paths=(path,),
            early_stop=True,
        )
    except json.JSONDecodeError as exc:
        return guard_failed(
            category="workspace_evidence",
            reason=f"workspace evidence JSON 解析失败: {exc}",
            paths=(path,),
            early_stop=True,
        )
    if not isinstance(payload, dict):
        return guard_failed(
            category="workspace_evidence",
            reason="workspace evidence 必须是 JSON object",
            paths=(path,),
            early_stop=True,
        )
    return payload


def _resolve_worker_path(path_value: str, expected_worker_root: Path, worker_id: str) -> Path | None:
    normalized = path_value.replace("\\", "/").strip()
    if normalized == "":
        return None
    if _looks_like_windows_drive(normalized):
        return None

    candidate = PurePosixPath(normalized)
    candidate_parts = candidate.parts
    if not candidate_parts:
        return None
    if any(part in {"", ".", ".."} for part in candidate_parts):
        return None

    if candidate.is_absolute():
        absolute_path = Path(str(candidate)).resolve()
        return absolute_path if _is_within(absolute_path, expected_worker_root) else None

    relative_parts: tuple[str, ...] | None = None
    if _has_prefix(candidate_parts, ("agents", worker_id)):
        relative_parts = candidate_parts[2:]
    elif _has_prefix(candidate_parts, (worker_id,)):
        relative_parts = candidate_parts[1:]
    if not relative_parts:
        return None
    return (expected_worker_root / Path(*relative_parts)).resolve()


def _looks_like_windows_drive(path_value: str) -> bool:
    return len(path_value) >= 2 and path_value[1] == ":" and path_value[0].isalpha()


def _is_within(candidate: Path, expected_worker_root: Path) -> bool:
    try:
        candidate.relative_to(expected_worker_root)
    except ValueError:
        return False
    return True


def _has_prefix(parts: tuple[str, ...], prefix: tuple[str, ...]) -> bool:
    if not prefix:
        return False
    if len(parts) < len(prefix):
        return False
    return parts[: len(prefix)] == prefix
