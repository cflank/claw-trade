from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from claw_trade.guards.common import GuardResult, guard_failed, guard_passed
from claw_trade.workflow.models import Stage, WorkerSpec


_ALLOWED_OPENVIKING_ACCESS = {"none", "read", "write", "read_write"}


@dataclass(frozen=True)
class StagePolicy:
    worker_id: str
    stage: Stage
    profile: str
    tool_intents: tuple[str, ...]
    openviking_access: str
    source_path: Path


@dataclass(frozen=True)
class StagePolicyResult:
    ok: bool
    policy: StagePolicy | None
    reason: str | None
    source_path: Path


def load_stage_policy(agents_root: Path, worker_id: str, profile: str) -> StagePolicyResult:
    source_path = agents_root / worker_id / "STAGES.yaml"
    if not source_path.is_file():
        return StagePolicyResult(
            ok=False,
            policy=None,
            reason=f"missing stage policy file: {source_path}",
            source_path=source_path,
        )

    with source_path.open("r", encoding="utf-8") as fh:
        content = fh.read()
    parsed = _parse_stages_yaml(content)

    stage_text = parsed.get("stage")
    if not stage_text:
        return StagePolicyResult(False, None, "stage is required in STAGES.yaml", source_path)
    try:
        stage = Stage(stage_text)
    except ValueError:
        return StagePolicyResult(False, None, f"unknown stage: {stage_text}", source_path)

    profiles = parsed.get("profiles", {})
    profile_data = profiles.get(profile)
    if profile_data is None:
        return StagePolicyResult(False, None, f"profile block missing: {profile}", source_path)

    approved = profile_data.get("approved")
    if approved is not True:
        # 这里是 profile 边界：未批准配置必须失败，禁止 fallback 到其他市场策略。
        return StagePolicyResult(False, None, f"profile is not approved: {profile}", source_path)

    tools = tuple(profile_data.get("tools", ()))

    access = str(profile_data.get("openviking_access", "")).strip()
    if access not in _ALLOWED_OPENVIKING_ACCESS:
        return StagePolicyResult(
            False,
            None,
            f"invalid openviking_access for profile {profile}: {access or '<empty>'}",
            source_path,
        )

    policy = StagePolicy(
        worker_id=worker_id,
        stage=stage,
        profile=profile,
        tool_intents=tools,
        openviking_access=access,
        source_path=source_path,
    )
    return StagePolicyResult(ok=True, policy=policy, reason=None, source_path=source_path)


def validate_stage_policy_matches_worker(policy: StagePolicy, worker: WorkerSpec) -> GuardResult:
    if policy.worker_id != worker.id:
        return guard_failed(
            category="config_blocked",
            reason=f"stage policy worker mismatch: {policy.worker_id} != {worker.id}",
            paths=(policy.source_path,),
            early_stop=True,
        )
    if policy.stage != worker.stage:
        return guard_failed(
            category="config_blocked",
            reason=f"stage policy stage mismatch: {policy.stage.value} != {worker.stage.value}",
            paths=(policy.source_path,),
            early_stop=True,
        )
    return guard_passed("stage_policy_match")


def _parse_stages_yaml(content: str) -> dict[str, object]:
    result: dict[str, object] = {"profiles": {}}
    lines = content.splitlines()

    stage_value: str | None = None
    profiles: dict[str, dict[str, object]] = {}

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            i += 1
            continue

        if stripped.startswith("stage:"):
            stage_value = stripped.split(":", 1)[1].strip()
            i += 1
            continue

        if stripped == "profiles:":
            i += 1
            while i < len(lines):
                profile_line = lines[i]
                if not profile_line.startswith("  "):
                    break
                profile_name = _parse_profile_header(profile_line)
                if profile_name is None:
                    i += 1
                    continue

                profile_data: dict[str, object] = {}
                i += 1
                while i < len(lines):
                    field_line = lines[i]
                    if not field_line.startswith("    "):
                        break
                    field_stripped = field_line.strip()

                    if field_stripped.startswith("approved:"):
                        profile_data["approved"] = _parse_bool(field_stripped.split(":", 1)[1].strip())
                        i += 1
                        continue

                    if field_stripped.startswith("openviking_access:"):
                        profile_data["openviking_access"] = field_stripped.split(":", 1)[1].strip()
                        i += 1
                        continue

                    if field_stripped.startswith("tools:"):
                        inline = field_stripped.split(":", 1)[1].strip()
                        if inline == "[]":
                            profile_data["tools"] = []
                            i += 1
                            continue
                        tools: list[str] = []
                        i += 1
                        while i < len(lines):
                            item_line = lines[i]
                            if not item_line.startswith("      - "):
                                break
                            tool = item_line.split("-", 1)[1].strip()
                            if tool:
                                tools.append(tool)
                            i += 1
                        profile_data["tools"] = tools
                        continue

                    i += 1

                profiles[profile_name] = profile_data
                continue

            continue

        i += 1

    if stage_value is not None:
        result["stage"] = stage_value
    result["profiles"] = profiles
    return result


def _parse_profile_header(line: str) -> str | None:
    stripped = line.strip()
    if not stripped.endswith(":"):
        return None
    name = stripped[:-1].strip()
    if not name:
        return None
    return name


def _parse_bool(value: str) -> bool | None:
    if value == "true":
        return True
    if value == "false":
        return False
    return None
