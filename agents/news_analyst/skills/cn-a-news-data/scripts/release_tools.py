from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from security import sanitize_error

EXPECTED_WORKERS = ("news_analyst",)
EXPECTED_TOOL_EXPORTS = ("news_news_data_pack",)
EXPECTED_VISIBLE_TOOLS = ("news_news_data_pack",)
EXPECTED_ENTRYPOINT = "scripts/news_data_pack.py"
EXPECTED_SCHEMA_VERSION = "cn_a_news_pack.v1"
EXPECTED_TOOL_NAME = "news_news_data_pack"
TARGET_SKILL_PATH = "cn-a-news-data/SKILL.md"
TARGET_SKILL_ID = "cn-a-news-data"
VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")


class ReleaseValidationError(ValueError):
    """Raised when release contract validation fails."""


@dataclass(frozen=True)
class SkillMetadata:
    name: str
    version: str
    tool_name: str
    entrypoint: str
    schema_version: str


@dataclass(frozen=True)
class ManifestSkillEntry:
    path: str
    skill_id: str
    skill_version: str
    workers: tuple[str, ...]
    tool_exports: tuple[str, ...]
    skill_version_line: int


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="CN_A news skill release helper")
    subparsers = parser.add_subparsers(dest="command", required=True)

    check_version = subparsers.add_parser("check-version", help="Check SKILL.md and manifest release contract")
    check_version.add_argument("--skill-file", default=None, help="Override SKILL.md path")
    check_version.add_argument("--manifest-file", default=None, help="Override manifest.yaml path")

    check_visible = subparsers.add_parser("check-visible-tools", help="Check visible tools evidence JSON")
    check_visible.add_argument("--evidence-file", required=True, help="Visible tools evidence JSON path")

    rollback = subparsers.add_parser("rollback", help="Rollback manifest skill_version and run load validation")
    rollback.add_argument("--target-version", required=True, help="Stable skill version to rollback to")
    rollback.add_argument("--skill-file", default=None, help="Override SKILL.md path")
    rollback.add_argument("--manifest-file", default=None, help="Override manifest.yaml path")
    return parser


def _default_skill_path() -> Path:
    return Path(__file__).resolve().parents[1] / "SKILL.md"


def _default_manifest_path() -> Path:
    return Path(__file__).resolve().parents[2] / "manifest.yaml"


def _resolve_skill_path(skill_file: str | None) -> Path:
    return Path(skill_file).expanduser().resolve() if skill_file else _default_skill_path()


def _resolve_manifest_path(manifest_file: str | None) -> Path:
    return Path(manifest_file).expanduser().resolve() if manifest_file else _default_manifest_path()


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ReleaseValidationError(f"{path}: read failed: {sanitize_error(exc)}") from exc


def _read_lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8").splitlines(keepends=True)
    except OSError as exc:
        raise ReleaseValidationError(f"{path}: read failed: {sanitize_error(exc)}") from exc


def _extract_front_matter(text: str) -> list[str]:
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ReleaseValidationError("SKILL.md front matter missing")
    closing_index = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            closing_index = index
            break
    if closing_index is None:
        raise ReleaseValidationError("SKILL.md front matter closing marker missing")
    return lines[1:closing_index]


def _parse_front_matter(lines: list[str]) -> dict[str, str]:
    fields: dict[str, str] = {}
    for raw_line in lines:
        line = raw_line.strip()
        if line == "" or line.startswith("#"):
            continue
        if ":" not in line:
            raise ReleaseValidationError(f"SKILL.md front matter line invalid: {line}")
        key, value = line.split(":", 1)
        cleaned_key = key.strip()
        cleaned_value = value.strip()
        if cleaned_key == "":
            raise ReleaseValidationError("SKILL.md front matter key cannot be empty")
        fields[cleaned_key] = cleaned_value
    return fields


def _load_skill_metadata(path: Path) -> SkillMetadata:
    front_matter = _extract_front_matter(_read_text(path))
    payload = _parse_front_matter(front_matter)

    name = payload.get("name", "").strip()
    version = payload.get("version", "").strip()
    entrypoint = payload.get("entrypoint", "").strip()
    schema_version = payload.get("schema_version", "").strip()
    tool_name = payload.get("tool_name", "").strip() or payload.get("tool", "").strip()

    missing = []
    if name == "":
        missing.append("name")
    if version == "":
        missing.append("version")
    if tool_name == "":
        missing.append("tool_name")
    if entrypoint == "":
        missing.append("entrypoint")
    if schema_version == "":
        missing.append("schema_version")
    if missing:
        raise ReleaseValidationError(f"SKILL.md required fields missing: {', '.join(missing)}")

    return SkillMetadata(
        name=name,
        version=version,
        tool_name=tool_name,
        entrypoint=entrypoint,
        schema_version=schema_version,
    )


def _leading_spaces(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _strip_quotes(value: str) -> str:
    stripped = value.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in {"'", '"'}:
        return stripped[1:-1]
    return stripped


def _find_manifest_skill_block(lines: list[str]) -> tuple[int, int]:
    target_start: int | None = None
    target_indent = 0
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith("- path:"):
            continue
        _, value = stripped.split(":", 1)
        if _strip_quotes(value) != TARGET_SKILL_PATH:
            continue
        target_start = index
        target_indent = _leading_spaces(line)
        break
    if target_start is None:
        raise ReleaseValidationError(f"manifest entry not found for path={TARGET_SKILL_PATH}")

    end_index = len(lines)
    for index in range(target_start + 1, len(lines)):
        line = lines[index]
        if _leading_spaces(line) != target_indent:
            continue
        if line.strip().startswith("- path:"):
            end_index = index
            break
    return target_start, end_index


def _collect_nested_list(lines: list[str], start: int, parent_indent: int) -> tuple[tuple[str, ...], int]:
    values: list[str] = []
    index = start
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if stripped == "":
            index += 1
            continue
        indent = _leading_spaces(line)
        if indent <= parent_indent:
            break
        if not stripped.startswith("- "):
            raise ReleaseValidationError(f"manifest list item invalid: {stripped}")
        values.append(_strip_quotes(stripped[2:].strip()))
        index += 1
    return tuple(values), index


def _parse_manifest_skill_entry(path: Path) -> tuple[ManifestSkillEntry, list[str]]:
    lines = _read_lines(path)
    start, end = _find_manifest_skill_block(lines)

    skill_id = ""
    skill_version = ""
    workers: tuple[str, ...] = ()
    tool_exports: tuple[str, ...] = ()
    skill_version_line = -1
    index = start
    while index < end:
        line = lines[index]
        stripped = line.strip()
        if stripped == "":
            index += 1
            continue
        if stripped.startswith("- path:"):
            index += 1
            continue
        if ":" not in stripped:
            raise ReleaseValidationError(f"manifest line invalid: {stripped}")

        key, value = stripped.split(":", 1)
        clean_key = key.strip()
        clean_value = value.strip()
        line_indent = _leading_spaces(line)
        if clean_key == "workers":
            workers, index = _collect_nested_list(lines, index + 1, line_indent)
            continue
        if clean_key == "tool_exports":
            tool_exports, index = _collect_nested_list(lines, index + 1, line_indent)
            continue
        if clean_key == "skill_id":
            skill_id = _strip_quotes(clean_value)
        elif clean_key == "skill_version":
            skill_version = _strip_quotes(clean_value)
            skill_version_line = index
        index += 1

    if skill_version_line < 0:
        raise ReleaseValidationError("manifest skill_version missing")
    entry = ManifestSkillEntry(
        path=TARGET_SKILL_PATH,
        skill_id=skill_id,
        skill_version=skill_version,
        workers=workers,
        tool_exports=tool_exports,
        skill_version_line=skill_version_line,
    )
    return entry, lines


def _validate_version_format(label: str, value: str) -> None:
    if value.strip() == "":
        raise ReleaseValidationError(f"{label} cannot be empty")
    if VERSION_PATTERN.match(value.strip()) is None:
        raise ReleaseValidationError(f"{label} invalid format: {value}")


def _validate_release_contract(
    *,
    skill: SkillMetadata,
    manifest: ManifestSkillEntry,
    check_version_sync: bool,
) -> list[str]:
    mismatches: list[str] = []

    if skill.name != TARGET_SKILL_ID:
        mismatches.append(f"name mismatch: SKILL.md name={skill.name}, expected={TARGET_SKILL_ID}")
    if skill.tool_name != EXPECTED_TOOL_NAME:
        mismatches.append(f"tool_name mismatch: SKILL.md tool_name={skill.tool_name}, expected={EXPECTED_TOOL_NAME}")
    if skill.entrypoint != EXPECTED_ENTRYPOINT:
        mismatches.append(f"entrypoint mismatch: SKILL.md entrypoint={skill.entrypoint}, expected={EXPECTED_ENTRYPOINT}")
    if skill.schema_version != EXPECTED_SCHEMA_VERSION:
        mismatches.append(
            "schema_version mismatch: "
            f"SKILL.md schema_version={skill.schema_version}, expected={EXPECTED_SCHEMA_VERSION}"
        )

    if manifest.skill_id != TARGET_SKILL_ID:
        mismatches.append(f"skill_id mismatch: manifest skill_id={manifest.skill_id}, expected={TARGET_SKILL_ID}")
    if manifest.workers != EXPECTED_WORKERS:
        mismatches.append(
            f"workers mismatch: manifest workers={list(manifest.workers)}, expected={list(EXPECTED_WORKERS)}"
        )
    if manifest.tool_exports != EXPECTED_TOOL_EXPORTS:
        mismatches.append(
            "tool_exports mismatch: "
            f"manifest tool_exports={list(manifest.tool_exports)}, expected={list(EXPECTED_TOOL_EXPORTS)}"
        )

    if check_version_sync:
        if skill.version != manifest.skill_version:
            mismatches.append(
                "version mismatch: "
                f"SKILL.md version={skill.version}, manifest skill_version={manifest.skill_version}"
            )
        else:
            _validate_version_format("version", skill.version)
    else:
        _validate_version_format("manifest skill_version", manifest.skill_version)
    return mismatches


def run_check_version(skill_file: str | None, manifest_file: str | None) -> int:
    skill_path = _resolve_skill_path(skill_file)
    manifest_path = _resolve_manifest_path(manifest_file)
    skill = _load_skill_metadata(skill_path)
    manifest, _ = _parse_manifest_skill_entry(manifest_path)
    mismatches = _validate_release_contract(skill=skill, manifest=manifest, check_version_sync=True)
    if mismatches:
        for item in mismatches:
            sys.stderr.write(f"release contract check failed: {item}\n")
        return 1
    sys.stdout.write("release contract check passed\n")
    return 0


def _normalize_tool_names(raw_tools: list[object]) -> tuple[str, ...]:
    normalized: list[str] = []
    for item in raw_tools:
        if isinstance(item, str):
            candidate = item.strip()
            if candidate:
                normalized.append(candidate)
            continue
        if isinstance(item, dict):
            raw_name = item.get("name")
            if isinstance(raw_name, str) and raw_name.strip():
                normalized.append(raw_name.strip())
            continue
    return tuple(normalized)


def _collect_visible_tools_candidates(obj: object, path: str, results: list[tuple[str, tuple[str, ...]]]) -> None:
    if isinstance(obj, dict):
        visible_tools = obj.get("visible_tools")
        if isinstance(visible_tools, list):
            tools = _normalize_tool_names(visible_tools)
            results.append((path + ".visible_tools", tools))

        worker_id = obj.get("worker_id")
        if worker_id == "news_analyst" and isinstance(visible_tools, list):
            tools = _normalize_tool_names(visible_tools)
            results.append((path + ".worker_id(news_analyst)", tools))

        news_analyst_obj = obj.get("news_analyst")
        if isinstance(news_analyst_obj, dict):
            nested = news_analyst_obj.get("visible_tools")
            if isinstance(nested, list):
                tools = _normalize_tool_names(nested)
                results.append((path + ".news_analyst.visible_tools", tools))

        tools = obj.get("tools")
        if isinstance(tools, list):
            normalized_tools = _normalize_tool_names(tools)
            if normalized_tools:
                results.append((path + ".tools", normalized_tools))

        for key, value in obj.items():
            next_path = f"{path}.{key}"
            _collect_visible_tools_candidates(value, next_path, results)
    elif isinstance(obj, list):
        for index, item in enumerate(obj):
            _collect_visible_tools_candidates(item, f"{path}[{index}]", results)


def _extract_visible_tools(payload: object) -> tuple[str, tuple[str, ...]]:
    candidates: list[tuple[str, tuple[str, ...]]] = []
    _collect_visible_tools_candidates(payload, "$", candidates)
    deduped: list[tuple[str, tuple[str, ...]]] = []
    seen: set[tuple[str, ...]] = set()
    for path, tools in candidates:
        if tools in seen:
            continue
        seen.add(tools)
        deduped.append((path, tools))

    if not deduped:
        raise ReleaseValidationError("visible_tools not found in evidence")

    expected_set = set(EXPECTED_VISIBLE_TOOLS)
    for path, tools in deduped:
        tool_set = set(tools)
        if tool_set == expected_set and len(tools) == len(expected_set):
            return path, tools

    return deduped[0]


def run_check_visible_tools(evidence_file: str) -> int:
    evidence_path = Path(evidence_file).expanduser().resolve()
    try:
        payload = json.loads(_read_text(evidence_path))
    except json.JSONDecodeError as exc:
        sys.stderr.write(f"visible tools check failed: invalid JSON: {sanitize_error(exc)}\n")
        return 1

    source_path, actual_tools = _extract_visible_tools(payload)
    expected = set(EXPECTED_VISIBLE_TOOLS)
    actual = set(actual_tools)
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    duplicates = sorted({tool for tool in actual_tools if actual_tools.count(tool) > 1})
    if missing or extra or duplicates or len(actual_tools) != len(expected):
        sys.stderr.write(f"visible tools check failed: source={source_path}\n")
        if missing:
            sys.stderr.write(f"missing tools: {missing}\n")
        if extra:
            sys.stderr.write(f"extra tools: {extra}\n")
        if duplicates:
            sys.stderr.write(f"duplicate tools: {duplicates}\n")
        return 1

    sys.stdout.write("visible tools check passed\n")
    return 0


def _replace_skill_version_line(line: str, target_version: str) -> str:
    if "skill_version:" not in line:
        raise ReleaseValidationError("manifest skill_version line invalid")
    prefix = line.split("skill_version:", 1)[0]
    return f"{prefix}skill_version: {target_version}\n"


def _write_manifest_lines(path: Path, lines: list[str]) -> None:
    try:
        path.write_text("".join(lines), encoding="utf-8")
    except OSError as exc:
        raise ReleaseValidationError(f"{path}: write failed: {sanitize_error(exc)}") from exc


def run_rollback(target_version: str, skill_file: str | None, manifest_file: str | None) -> int:
    cleaned_version = target_version.strip()
    _validate_version_format("target_version", cleaned_version)

    skill_path = _resolve_skill_path(skill_file)
    manifest_path = _resolve_manifest_path(manifest_file)
    skill = _load_skill_metadata(skill_path)
    manifest_entry, manifest_lines = _parse_manifest_skill_entry(manifest_path)
    manifest_lines[manifest_entry.skill_version_line] = _replace_skill_version_line(
        manifest_lines[manifest_entry.skill_version_line],
        cleaned_version,
    )
    _write_manifest_lines(manifest_path, manifest_lines)

    reloaded_manifest, _ = _parse_manifest_skill_entry(manifest_path)
    mismatches = _validate_release_contract(
        skill=skill,
        manifest=reloaded_manifest,
        check_version_sync=False,
    )
    if mismatches:
        for item in mismatches:
            sys.stderr.write(f"rollback load check failed: {item}\n")
        return 1
    sys.stdout.write(f"rollback succeeded: manifest skill_version={cleaned_version}\n")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "check-version":
            return run_check_version(args.skill_file, args.manifest_file)
        if args.command == "check-visible-tools":
            return run_check_visible_tools(args.evidence_file)
        if args.command == "rollback":
            return run_rollback(args.target_version, args.skill_file, args.manifest_file)
        parser.error(f"unknown command: {args.command}")
        return 2
    except ReleaseValidationError as exc:
        sys.stderr.write(f"release_tools failed: {sanitize_error(exc)}\n")
        return 1
    except Exception as exc:  # pragma: no cover
        sys.stderr.write(f"release_tools failed: {sanitize_error(exc)}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
