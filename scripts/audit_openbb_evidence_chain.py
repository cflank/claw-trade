#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from claw_trade.data_gateway.maintenance.normalized_rows import normalized_mongo_row_exists
from claw_trade.data_gateway.warehouse import DatasetRepository


class OpenBBEvidenceChainAudit:
    def __init__(
        self,
        *,
        run_id: str,
        scope: str,
        attempts_checked: int,
        raw_refs_checked: int,
        dataset_refs_checked: int,
        missing_attempt_refs: Sequence[str] = (),
        missing_raw_refs: Sequence[str] = (),
        missing_dataset_refs: Sequence[str] = (),
        link_errors: Sequence[Mapping[str, str]] = (),
        scope_errors: Sequence[str] = (),
        prefetch_path: str | None = None,
    ) -> None:
        self.run_id = run_id
        self.scope = scope
        self.attempts_checked = attempts_checked
        self.raw_refs_checked = raw_refs_checked
        self.dataset_refs_checked = dataset_refs_checked
        self.missing_attempt_refs = tuple(missing_attempt_refs)
        self.missing_raw_refs = tuple(missing_raw_refs)
        self.missing_dataset_refs = tuple(missing_dataset_refs)
        self.link_errors = tuple(dict(item) for item in link_errors)
        self.scope_errors = tuple(scope_errors)
        self.prefetch_path = prefetch_path

    @property
    def passed(self) -> bool:
        return not (
            self.missing_attempt_refs
            or self.missing_raw_refs
            or self.missing_dataset_refs
            or self.link_errors
            or self.scope_errors
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "scope": self.scope,
            "passed": self.passed,
            "attempts_checked": self.attempts_checked,
            "raw_refs_checked": self.raw_refs_checked,
            "dataset_refs_checked": self.dataset_refs_checked,
            "missing_attempt_refs": list(self.missing_attempt_refs),
            "missing_raw_refs": list(self.missing_raw_refs),
            "missing_dataset_refs": list(self.missing_dataset_refs),
            "link_errors": [dict(item) for item in self.link_errors],
            "scope_errors": list(self.scope_errors),
            "prefetch_path": self.prefetch_path,
        }


class _ExpectedRefs:
    def __init__(
        self,
        *,
        scope: str,
        attempt_refs: Sequence[str] = (),
        raw_refs: Sequence[str] = (),
        dataset_refs: Sequence[str] = (),
        prefetch_path: Path | None = None,
        scope_errors: Sequence[str] = (),
    ) -> None:
        self.scope = scope
        self.attempt_refs = tuple(_dedupe(attempt_refs))
        self.raw_refs = tuple(_dedupe(raw_refs))
        self.dataset_refs = tuple(_dedupe(dataset_refs))
        self.prefetch_path = prefetch_path
        self.scope_errors = tuple(_dedupe(scope_errors))


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit OpenBB provider evidence links for one run.")
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--mongo-uri",
        default=os.environ.get("DATA_GATEWAY_MONGODB_URI") or os.environ.get("CN_A_MONGODB_URI") or "",
    )
    parser.add_argument("--database", default="")
    parser.add_argument("--runs-dir", default="runs")
    args = parser.parse_args()

    if not args.mongo_uri.strip():
        raise SystemExit("missing --mongo-uri or DATA_GATEWAY_MONGODB_URI/CN_A_MONGODB_URI")

    from pymongo import MongoClient

    client: MongoClient[Any] = MongoClient(args.mongo_uri, serverSelectionTimeoutMS=5000)
    client.admin.command("ping")
    database = client[args.database] if args.database else client.get_default_database()
    audit = audit_openbb_evidence_chain(database, run_id=args.run_id, runs_dir=Path(args.runs_dir))
    print(json.dumps(audit.as_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if audit.passed else 1


def audit_openbb_evidence_chain(
    database: Any,
    *,
    run_id: str,
    runs_dir: Path = Path("runs"),
) -> OpenBBEvidenceChainAudit:
    repository = DatasetRepository.from_database(database, allow_normalized_mongo_read=True)
    expected = _expected_refs_for_run(database, run_id=run_id, runs_dir=runs_dir)
    attempt_docs, missing_attempt_refs = _load_attempt_docs(repository, expected.attempt_refs)

    raw_refs = _dedupe([*expected.raw_refs, *_linked_refs(attempt_docs, "raw_refs")])
    dataset_refs = _dedupe([*expected.dataset_refs, *_linked_refs(attempt_docs, "dataset_refs")])
    missing_raw_refs = tuple(ref for ref in raw_refs if not _raw_ref_exists(repository, ref))
    missing_dataset_refs = tuple(
        ref for ref in dataset_refs if not _dataset_ref_exists(database, repository, ref)
    )
    link_errors = tuple(
        _link_errors_for_attempt(
            attempt,
            missing_raw_refs=missing_raw_refs,
            missing_dataset_refs=missing_dataset_refs,
        )
        for attempt in attempt_docs
    )
    flat_link_errors = tuple(item for group in link_errors for item in group)
    return OpenBBEvidenceChainAudit(
        run_id=run_id,
        scope=expected.scope,
        attempts_checked=len(attempt_docs),
        raw_refs_checked=len(raw_refs),
        dataset_refs_checked=len(dataset_refs),
        missing_attempt_refs=missing_attempt_refs,
        missing_raw_refs=missing_raw_refs,
        missing_dataset_refs=missing_dataset_refs,
        link_errors=flat_link_errors,
        scope_errors=expected.scope_errors,
        prefetch_path=str(expected.prefetch_path) if expected.prefetch_path is not None else None,
    )


def _expected_refs_for_run(database: Any, *, run_id: str, runs_dir: Path) -> _ExpectedRefs:
    prefetch_path = runs_dir / run_id / "data-layer" / "report-prefetch.json"
    payload = _load_json(prefetch_path)
    if prefetch_path.exists() and not isinstance(payload, dict):
        return _ExpectedRefs(
            scope="report_prefetch",
            prefetch_path=prefetch_path,
            scope_errors=("report_prefetch_unreadable",),
        )
    if isinstance(payload, dict):
        scope_errors: list[str] = []
        if payload.get("ok") is not True:
            scope_errors.append("report_prefetch_not_ok")
        if payload.get("schema_version") != "report_data_prefetch.v1":
            scope_errors.append("report_prefetch_schema_mismatch")
        if payload.get("run_id") != run_id:
            scope_errors.append("report_prefetch_run_id_mismatch")
        if not (_refs(payload.get("attempt_refs")) or _refs(payload.get("raw_refs")) or _refs(payload.get("dataset_refs"))):
            scope_errors.append("report_prefetch_refs_missing")
        return _ExpectedRefs(
            scope="report_prefetch",
            attempt_refs=_refs(payload.get("attempt_refs")),
            raw_refs=_refs(payload.get("raw_refs")),
            dataset_refs=_refs(payload.get("dataset_refs")),
            prefetch_path=prefetch_path,
            scope_errors=scope_errors,
        )

    attempts = _mongo_attempts(database)
    run_attempts = tuple(item for item in attempts if _doc_mentions_run(item, run_id))
    if run_attempts:
        return _ExpectedRefs(
            scope="mongo_run_fields",
            attempt_refs=tuple(str(item.get("attempt_ref") or "") for item in run_attempts),
            raw_refs=_linked_refs(run_attempts, "raw_refs"),
            dataset_refs=_linked_refs(run_attempts, "dataset_refs"),
        )
    return _ExpectedRefs(
        scope="missing_run_evidence",
        scope_errors=("report_prefetch_missing",),
    )


def _load_attempt_docs(
    repository: DatasetRepository,
    attempt_refs: Sequence[str],
) -> tuple[tuple[dict[str, Any], ...], tuple[str, ...]]:
    docs: list[dict[str, Any]] = []
    missing: list[str] = []
    for ref in _dedupe(attempt_refs):
        if not ref:
            continue
        doc = _provider_attempt_doc(repository, ref)
        if doc is None:
            missing.append(ref)
            continue
        docs.append(doc)
    return tuple(docs), tuple(missing)


def _link_errors_for_attempt(
    attempt: Mapping[str, Any],
    *,
    missing_raw_refs: Sequence[str],
    missing_dataset_refs: Sequence[str],
) -> tuple[dict[str, str], ...]:
    attempt_ref = str(attempt.get("attempt_ref") or "")
    errors: list[dict[str, str]] = []
    missing_raw = set(missing_raw_refs)
    missing_dataset = set(missing_dataset_refs)
    for ref in _refs(attempt.get("raw_refs")):
        if ref in missing_raw:
            errors.append({"attempt_ref": attempt_ref, "field": "raw_refs", "missing_ref": ref})
    for ref in _refs(attempt.get("dataset_refs")):
        if ref in missing_dataset:
            errors.append({"attempt_ref": attempt_ref, "field": "dataset_refs", "missing_ref": ref})
    return tuple(errors)


def _mongo_attempts(database: Any) -> tuple[dict[str, Any], ...]:
    collection = database["provider_attempts"]
    finder = getattr(collection, "find", None)
    if not callable(finder):
        return ()
    return tuple(dict(item) for item in finder({}))


def _provider_attempt_doc(repository: DatasetRepository, ref: str) -> dict[str, Any] | None:
    for candidate in _provider_attempt_ref_candidates(ref):
        doc = repository.get_provider_attempt(candidate)
        if doc is not None:
            return doc
    return None


def _raw_ref_exists(repository: DatasetRepository, ref: str) -> bool:
    return any(repository.get_raw_payload(candidate) is not None for candidate in _raw_ref_candidates(ref))


def _dataset_ref_exists(database: Any, repository: DatasetRepository, ref: str) -> bool:
    candidates = _dataset_ref_candidates(ref)
    if normalized_mongo_row_exists(repository, candidates):
        return True
    return _dataset_manifest_has_ref(database, candidates)


def _dataset_manifest_has_ref(database: Any, refs: Sequence[str]) -> bool:
    collection = database["dataset_manifests"]
    finder = getattr(collection, "find", None)
    if not callable(finder):
        return False
    wanted = set(refs)
    try:
        rows = tuple(dict(item) for item in finder({"dataset_refs": {"$in": tuple(refs)}}))
    except Exception:
        rows = ()
    if not rows:
        rows = tuple(dict(item) for item in finder({}))
    return any(wanted.intersection(_refs(row.get("dataset_refs"))) for row in rows)


def _provider_attempt_ref_candidates(ref: str) -> tuple[str, ...]:
    text = ref.strip()
    candidates = [text]
    for prefix in ("attempt://data-provider/", "attempt://mongo/provider_attempts/"):
        if text.startswith(prefix):
            candidates.append(text[len(prefix) :])
    return _dedupe(candidates)


def _raw_ref_candidates(ref: str) -> tuple[str, ...]:
    text = ref.strip()
    candidates = [text]
    for prefix in ("mongo://raw_payloads/", "raw://data-provider/"):
        if text.startswith(prefix):
            candidates.append(text[len(prefix) :])
    return _dedupe(candidates)


def _dataset_ref_candidates(ref: str) -> tuple[str, ...]:
    text = ref.strip()
    candidates = [text]
    if text.startswith("normalized://mongo/normalized_datasets/"):
        candidates.append(text[len("normalized://mongo/normalized_datasets/") :])
    elif text.startswith("mongo://normalized_datasets/"):
        candidates.append(text[len("mongo://normalized_datasets/") :])
    elif text.startswith("dataset://normalized/"):
        tail = text[len("dataset://normalized/") :]
        parts = tail.split("/", 3)
        if len(parts) >= 3:
            candidates.append(parts[-1])
    return _dedupe(candidates)


def _doc_mentions_run(doc: Mapping[str, Any], run_id: str) -> bool:
    for key in ("run_id", "workflow_run_id", "report_run_id", "consumer_id", "request_id", "plan_id"):
        value = doc.get(key)
        if isinstance(value, str) and _text_mentions_run(value, run_id):
            return True
    for key in ("request_ids", "consumer_ids"):
        if any(_text_mentions_run(item, run_id) for item in _refs(doc.get(key))):
            return True
    return False


def _text_mentions_run(value: str, run_id: str) -> bool:
    text = value.strip()
    if text == run_id:
        return True
    for separator in (":", "/", "|", " ", "_"):
        if text.startswith(f"{run_id}{separator}"):
            return True
        if text.endswith(f"{separator}{run_id}"):
            return True
        if f"{separator}{run_id}{separator}" in text:
            return True
    return False


def _linked_refs(docs: Sequence[Mapping[str, Any]], field: str) -> tuple[str, ...]:
    return tuple(_dedupe(ref for doc in docs for ref in _refs(doc.get(field))))


def _refs(value: object) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def _dedupe(values: Sequence[str] | Any) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        ordered.append(text)
    return tuple(ordered)


def _load_json(path: Path) -> object:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


if __name__ == "__main__":
    raise SystemExit(main())
