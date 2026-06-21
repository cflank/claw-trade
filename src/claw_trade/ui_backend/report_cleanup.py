from __future__ import annotations

import json
import re
import shutil
import threading
from collections.abc import Callable, Iterable, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from claw_trade.ui_backend.report_repository import ReportRepository

_RUN_ID_RE = re.compile(r"\brun-[A-Za-z0-9][A-Za-z0-9_.-]*\b")
_TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


@dataclass(frozen=True)
class ReportCleanupRunResult:
    runId: str
    status: str
    deletedBytesApprox: int = 0
    warnings: list[str] = field(default_factory=list)
    userMessage: str = ""


@dataclass(frozen=True)
class ReportCleanupResult:
    deletedRunIds: list[str] = field(default_factory=list)
    skippedRunIds: list[str] = field(default_factory=list)
    failedRunIds: list[str] = field(default_factory=list)
    deletedBytesApprox: int = 0
    warnings: list[str] = field(default_factory=list)
    userMessage: str = ""
    runs: list[ReportCleanupRunResult] = field(default_factory=list)


class ReportFileSendTracker:
    def __init__(self) -> None:
        self._active: set[str] = set()
        self._lock = threading.Lock()

    @contextmanager
    def track(self, report_id: str):  # type: ignore[no-untyped-def]
        clean_id = str(report_id or "").strip()
        if clean_id:
            with self._lock:
                self._active.add(clean_id)
        try:
            yield
        finally:
            if clean_id:
                with self._lock:
                    self._active.discard(clean_id)

    def active_report_ids(self) -> set[str]:
        with self._lock:
            return set(self._active)


class ReportCleanupService:
    def __init__(
        self,
        *,
        run_root: Path,
        openviking_workflow_root: Path,
        openclaw_state_root: Path,
        repository: ReportRepository,
        protected_run_ids_provider: Callable[[], set[str]],
        in_flight_report_ids_provider: Callable[[], set[str]],
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self._run_root = Path(run_root).resolve()
        self._openviking_workflow_root = Path(openviking_workflow_root).resolve()
        self._openclaw_state_root = Path(openclaw_state_root).resolve()
        self._repository = repository
        self._protected_run_ids_provider = protected_run_ids_provider
        self._in_flight_report_ids_provider = in_flight_report_ids_provider
        self._now_provider = now_provider or (lambda: datetime.now(UTC))
        self._lock = threading.Lock()

    def cleanup_expired_reports(self, retention_days: int) -> ReportCleanupResult:
        cutoff = _as_utc(self._now_provider()) - timedelta(days=retention_days)
        candidates: list[str] = []
        if self._run_root.is_dir():
            for run_dir in sorted(self._run_root.glob("run-*")):
                if not _is_valid_run_id(run_dir.name):
                    continue
                safe_run_dir = _safe_run_dir(self._run_root, run_dir.name)
                if safe_run_dir is None:
                    continue
                age = self._run_age(safe_run_dir)
                if age is not None and age < cutoff:
                    candidates.append(run_dir.name)
        return self._delete_report_runs(candidates, cutoff=cutoff)

    def delete_report_runs(self, run_ids: Iterable[str]) -> ReportCleanupResult:
        return self._delete_report_runs(run_ids, cutoff=None)

    def _delete_report_runs(self, run_ids: Iterable[str], *, cutoff: datetime | None) -> ReportCleanupResult:
        with self._lock:
            protected = set(self._protected_run_ids_provider())
            protected.update(self._in_flight_report_ids_provider())
            deleted: list[str] = []
            skipped: list[str] = []
            failed: list[str] = []
            warnings: list[str] = []
            run_results: list[ReportCleanupRunResult] = []
            deleted_bytes = 0
            for raw_run_id in run_ids:
                run_id = str(raw_run_id or "").strip()
                if not _is_valid_run_id(run_id):
                    failed.append(run_id or "<empty>")
                    message = f"已拒绝无效运行 ID：{run_id or '<empty>'}"
                    warnings.append(message)
                    run_results.append(ReportCleanupRunResult(runId=run_id, status="failed", warnings=[message]))
                    continue
                run_result = self._delete_one_run(run_id, protected=protected, cutoff=cutoff)
                run_results.append(run_result)
                warnings.extend(run_result.warnings)
                if run_result.status == "deleted":
                    deleted.append(run_id)
                    deleted_bytes += run_result.deletedBytesApprox
                elif run_result.status == "skipped":
                    skipped.append(run_id)
                else:
                    failed.append(run_id)
            return ReportCleanupResult(
                deletedRunIds=deleted,
                skippedRunIds=skipped,
                failedRunIds=failed,
                deletedBytesApprox=deleted_bytes,
                warnings=warnings,
                userMessage=_summary_message(deleted, skipped, failed),
                runs=run_results,
            )

    def _delete_one_run(
        self,
        run_id: str,
        *,
        protected: set[str],
        cutoff: datetime | None,
    ) -> ReportCleanupRunResult:
        run_dir = _safe_run_dir(self._run_root, run_id)
        if run_dir is None:
            return ReportCleanupRunResult(runId=run_id, status="failed", warnings=[f"运行目录越界：{run_id}"])
        if run_id in protected:
            return ReportCleanupRunResult(runId=run_id, status="skipped", userMessage="运行仍受保护。")
        state = _read_json_object(run_dir / "state.json")
        status = str((state or {}).get("status") or "").strip().lower()
        if status not in _TERMINAL_STATUSES:
            return ReportCleanupRunResult(runId=run_id, status="skipped", userMessage="运行尚未进入终态。")
        if cutoff is not None:
            age = self._run_age(run_dir, state=state)
            if age is None or age >= cutoff:
                return ReportCleanupRunResult(runId=run_id, status="skipped", userMessage="运行仍在保留期内。")

        warnings: list[str] = []
        deleted_bytes = 0
        try:
            deleted_bytes += _delete_openclaw_session_files(self._openclaw_state_root, run_id)
            queue_bytes, queue_warnings = _clean_delivery_queue(self._openclaw_state_root, run_id)
            deleted_bytes += queue_bytes
            warnings.extend(queue_warnings)
            deleted_bytes += _delete_openviking_workflow_dir(self._openviking_workflow_root, run_id)
            if run_dir.exists():
                deleted_bytes += _path_size(run_dir)
                shutil.rmtree(run_dir)
        except OSError as exc:
            return ReportCleanupRunResult(
                runId=run_id,
                status="failed",
                deletedBytesApprox=deleted_bytes,
                warnings=[*warnings, f"删除 {run_id} 失败：{exc}"],
            )

        try:
            self._repository.remove_report_state(run_id)
            self._repository.discard_deleted_report_id(run_id)
        except Exception as exc:
            return ReportCleanupRunResult(
                runId=run_id,
                status="failed",
                deletedBytesApprox=deleted_bytes,
                warnings=[*warnings, f"删除 {run_id} 后清理索引失败：{exc}"],
            )
        return ReportCleanupRunResult(
            runId=run_id,
            status="deleted",
            deletedBytesApprox=deleted_bytes,
            warnings=warnings,
            userMessage="报告已硬删除。",
        )

    def _run_age(self, run_dir: Path, *, state: Mapping[str, Any] | None = None) -> datetime | None:
        payload = state if state is not None else _read_json_object(run_dir / "state.json")
        if payload:
            for key in ("updated_at", "created_at"):
                parsed = _parse_datetime(payload.get(key))
                if parsed is not None:
                    return parsed
        try:
            return datetime.fromtimestamp(run_dir.stat().st_mtime, UTC)
        except OSError:
            return None


def _delete_openclaw_session_files(openclaw_state_root: Path, run_id: str) -> int:
    sessions_parent = openclaw_state_root / "agents"
    if sessions_parent.is_symlink() or not sessions_parent.is_dir():
        return 0
    deleted_bytes = 0
    for agent_dir in sorted(sessions_parent.iterdir()):
        if agent_dir.is_symlink() or not agent_dir.is_dir():
            continue
        sessions_dir = agent_dir / "sessions"
        if sessions_dir.is_symlink() or not sessions_dir.is_dir():
            continue
        for path in _iter_files_no_symlink_dirs(sessions_dir):
            session_stem = _single_worker_session_stem(path)
            if path.is_symlink() or session_stem is None:
                continue
            text = _read_text(path)
            if text is None or not _text_uniquely_matches_run(text, run_id):
                continue
            deleted_bytes += _file_size(path)
            path.unlink()
            deleted_bytes += _delete_openclaw_session_sidecars(path.parent, session_stem)
    return deleted_bytes


def _clean_delivery_queue(openclaw_state_root: Path, run_id: str) -> tuple[int, list[str]]:
    queue_dir = openclaw_state_root / "delivery-queue"
    if queue_dir.is_symlink() or not queue_dir.is_dir():
        return 0, []
    deleted_bytes = 0
    warnings: list[str] = []
    for path in sorted(queue_dir.glob("*.json")):
        if path.is_symlink():
            continue
        text = _read_text(path)
        if text is None or run_id not in text:
            continue
        run_ids = set(_RUN_ID_RE.findall(text))
        if run_ids and run_ids != {run_id}:
            warnings.append(f"跳过共享发送队列文件：{path.name}")
            continue
        payload = _loads_json(text)
        if isinstance(payload, list):
            kept: list[Any] = []
            changed = False
            for item in payload:
                item_text = json.dumps(item, ensure_ascii=False, sort_keys=True)
                if run_id in item_text and _text_uniquely_matches_run(item_text, run_id):
                    changed = True
                    continue
                kept.append(item)
            if not changed:
                warnings.append(f"跳过无法精确清理的发送队列文件：{path.name}")
                continue
            old_size = _file_size(path)
            if kept:
                new_text = json.dumps(kept, ensure_ascii=False, indent=2)
                path.write_text(new_text, encoding="utf-8")
                deleted_bytes += max(old_size - len(new_text.encode("utf-8")), 0)
            else:
                deleted_bytes += old_size
                path.unlink()
            continue
        if _text_uniquely_matches_run(text, run_id):
            deleted_bytes += _file_size(path)
            path.unlink()
        else:
            warnings.append(f"跳过无法精确清理的发送队列文件：{path.name}")
    return deleted_bytes, warnings


def _delete_openviking_workflow_dir(workflow_root: Path, run_id: str) -> int:
    entry = workflow_root / run_id
    if entry.is_symlink():
        deleted_bytes = _link_size(entry)
        entry.unlink()
        return deleted_bytes
    workflow_dir = _safe_child_dir(workflow_root, run_id)
    if workflow_dir is None or not workflow_dir.is_dir():
        return 0
    deleted_bytes = _path_size(workflow_dir)
    shutil.rmtree(workflow_dir)
    return deleted_bytes


def _iter_files_no_symlink_dirs(root: Path) -> list[Path]:
    files: list[Path] = []
    pending = [root]
    while pending:
        current = pending.pop()
        for item in current.iterdir():
            if item.is_symlink():
                continue
            if item.is_dir():
                pending.append(item)
            elif item.is_file():
                files.append(item)
    return sorted(files)


def _single_worker_session_stem(path: Path) -> str | None:
    name = path.name
    if not name.startswith("single-worker-"):
        return None
    for suffix in (".trajectory.jsonl", ".jsonl"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return None


def _delete_openclaw_session_sidecars(directory: Path, session_stem: str) -> int:
    deleted_bytes = 0
    for name in (f"{session_stem}.trajectory-path.json",):
        path = directory / name
        if path.is_symlink() or not path.is_file():
            continue
        deleted_bytes += _file_size(path)
        path.unlink()
    return deleted_bytes


def _is_valid_run_id(run_id: str) -> bool:
    return bool(re.fullmatch(r"run-[A-Za-z0-9][A-Za-z0-9_.-]*", run_id or ""))


def _safe_child_dir(root: Path, child_name: str) -> Path | None:
    try:
        resolved_root = root.resolve()
        candidate = (resolved_root / child_name).resolve()
        candidate.relative_to(resolved_root)
    except (OSError, ValueError):
        return None
    if candidate == resolved_root:
        return None
    return candidate


def _safe_run_dir(root: Path, run_id: str) -> Path | None:
    entry = root / run_id
    if entry.is_symlink():
        return None
    candidate = _safe_child_dir(root, run_id)
    if candidate is None or candidate.name != run_id or not candidate.is_dir():
        return None
    return candidate


def _read_json_object(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _loads_json(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _text_uniquely_matches_run(text: str, run_id: str) -> bool:
    return set(_RUN_ID_RE.findall(text)) == {run_id}


def _parse_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        return _as_utc(datetime.fromisoformat(text))
    except ValueError:
        return None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _path_size(path: Path) -> int:
    if path.is_file() or path.is_symlink():
        return _file_size(path)
    total = 0
    for item in path.rglob("*"):
        if item.is_file() or item.is_symlink():
            total += _file_size(item)
    return total


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _link_size(path: Path) -> int:
    try:
        return path.lstat().st_size
    except OSError:
        return 0


def _summary_message(deleted: list[str], skipped: list[str], failed: list[str]) -> str:
    if failed:
        return f"已删除 {len(deleted)} 份报告，{len(skipped)} 份跳过，{len(failed)} 份失败。"
    if deleted:
        return f"已删除 {len(deleted)} 份报告，{len(skipped)} 份跳过。"
    return f"没有可删除的报告，{len(skipped)} 份跳过。"
