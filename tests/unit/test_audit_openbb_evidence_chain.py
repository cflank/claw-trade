from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from claw_trade.data_gateway.warehouse import ALLOWED_MONGO_COLLECTIONS


def _audit_module():
    script_path = Path(__file__).parents[2] / "scripts" / "audit_openbb_evidence_chain.py"
    spec = importlib.util.spec_from_file_location("audit_openbb_evidence_chain", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class _MongoLikeCollection:
    def __init__(self) -> None:
        self._docs: list[dict[str, object]] = []

    def find(self, criteria: dict[str, object]) -> list[dict[str, object]]:
        if not criteria:
            return [dict(item) for item in self._docs]
        return [dict(item) for item in self._docs if all(item.get(k) == v for k, v in criteria.items())]

    def find_one(self, criteria: dict[str, object]) -> dict[str, object] | None:
        rows = self.find(criteria)
        return rows[0] if rows else None

    def replace_one(self, criteria: dict[str, object], doc: dict[str, object], *, upsert: bool = False) -> None:
        for idx, row in enumerate(self._docs):
            if all(row.get(k) == v for k, v in criteria.items()):
                self._docs[idx] = dict(doc)
                return
        if upsert:
            self._docs.append(dict(doc))

    def create_index(self, fields: list[tuple[str, int]], **kwargs: object) -> str:
        del fields
        return str(kwargs.get("name") or "index")


class _MongoLikeDatabase:
    def __init__(self) -> None:
        self._collections = {name: _MongoLikeCollection() for name in ALLOWED_MONGO_COLLECTIONS}

    def __getitem__(self, name: str) -> _MongoLikeCollection:
        return self._collections[name]

    def insert(self, collection: str, doc: dict[str, object]) -> None:
        self._collections[collection]._docs.append(dict(doc))


def test_audit_uses_report_prefetch_refs_and_passes_when_documents_exist(tmp_path: Path) -> None:
    audit_script = _audit_module()
    runs_dir = tmp_path / "runs"
    _write_prefetch(
        runs_dir,
        run_id="run-1",
        attempt_refs=["attempt:p:e:1"],
        raw_refs=["raw:one"],
        dataset_refs=["dataset:one"],
    )
    db = _MongoLikeDatabase()
    db.insert(
        "provider_attempts",
        {
            "attempt_ref": "attempt:p:e:1",
            "status": "success",
            "raw_refs": ("raw:one",),
            "dataset_refs": ("dataset:one",),
        },
    )
    db.insert("raw_payloads", {"raw_ref": "raw:one"})
    db.insert("normalized_datasets", {"dataset_ref": "dataset:one"})

    audit = audit_script.audit_openbb_evidence_chain(db, run_id="run-1", runs_dir=runs_dir)

    assert audit.passed is True
    assert audit.scope == "report_prefetch"
    assert audit.as_dict()["missing_raw_refs"] == []


def test_audit_fails_when_attempt_raw_ref_document_is_missing(tmp_path: Path) -> None:
    audit_script = _audit_module()
    runs_dir = tmp_path / "runs"
    _write_prefetch(
        runs_dir,
        run_id="run-1",
        attempt_refs=["attempt:p:e:1"],
        raw_refs=["raw:missing"],
        dataset_refs=[],
    )
    db = _MongoLikeDatabase()
    db.insert(
        "provider_attempts",
        {
            "attempt_ref": "attempt:p:e:1",
            "status": "success",
            "raw_refs": ("raw:missing",),
            "dataset_refs": (),
        },
    )

    audit = audit_script.audit_openbb_evidence_chain(db, run_id="run-1", runs_dir=runs_dir)

    assert audit.passed is False
    assert audit.as_dict()["missing_raw_refs"] == ["raw:missing"]
    assert audit.as_dict()["link_errors"] == [
        {
            "attempt_ref": "attempt:p:e:1",
            "field": "raw_refs",
            "missing_ref": "raw:missing",
        }
    ]


def test_audit_fails_when_run_prefetch_is_missing_and_no_run_attempts(tmp_path: Path) -> None:
    audit_script = _audit_module()
    db = _MongoLikeDatabase()

    audit = audit_script.audit_openbb_evidence_chain(db, run_id="run-missing", runs_dir=tmp_path / "runs")

    assert audit.passed is False
    assert audit.as_dict()["scope_errors"] == ["report_prefetch_missing"]


def test_audit_fails_when_report_prefetch_does_not_match_run(tmp_path: Path) -> None:
    audit_script = _audit_module()
    runs_dir = tmp_path / "runs"
    _write_prefetch(
        runs_dir,
        run_id="run-1",
        payload_run_id="run-2",
        attempt_refs=["attempt:p:e:1"],
        raw_refs=["raw:one"],
        dataset_refs=["dataset:one"],
    )
    db = _MongoLikeDatabase()
    db.insert(
        "provider_attempts",
        {
            "attempt_ref": "attempt:p:e:1",
            "status": "success",
            "raw_refs": ("raw:one",),
            "dataset_refs": ("dataset:one",),
        },
    )
    db.insert("raw_payloads", {"raw_ref": "raw:one"})
    db.insert("normalized_datasets", {"dataset_ref": "dataset:one"})

    audit = audit_script.audit_openbb_evidence_chain(db, run_id="run-1", runs_dir=runs_dir)

    assert audit.passed is False
    assert audit.as_dict()["scope_errors"] == ["report_prefetch_run_id_mismatch"]


def test_audit_fails_when_report_prefetch_is_not_marked_ok(tmp_path: Path) -> None:
    audit_script = _audit_module()
    runs_dir = tmp_path / "runs"
    _write_prefetch(
        runs_dir,
        run_id="run-1",
        ok=False,
        attempt_refs=["attempt:p:e:1"],
        raw_refs=["raw:one"],
        dataset_refs=["dataset:one"],
    )
    db = _MongoLikeDatabase()

    audit = audit_script.audit_openbb_evidence_chain(db, run_id="run-1", runs_dir=runs_dir)

    assert audit.passed is False
    assert audit.as_dict()["scope_errors"] == ["report_prefetch_not_ok"]


def test_audit_accepts_wrapped_attempt_ref_and_columnar_manifest_dataset_ref(tmp_path: Path) -> None:
    audit_script = _audit_module()
    runs_dir = tmp_path / "runs"
    _write_prefetch(
        runs_dir,
        run_id="run-1",
        attempt_refs=["attempt://data-provider/attempt:p:e:1"],
        raw_refs=["raw:one"],
        dataset_refs=["dataset://normalized/CN_A/daily/dataset:one"],
    )
    db = _MongoLikeDatabase()
    db.insert(
        "provider_attempts",
        {
            "attempt_ref": "attempt:p:e:1",
            "status": "success",
            "raw_refs": ("raw:one",),
            "dataset_refs": ("dataset://normalized/CN_A/daily/dataset:one",),
        },
    )
    db.insert("raw_payloads", {"raw_ref": "raw:one"})
    db.insert(
        "dataset_manifests",
        {
            "manifest_ref": "manifest:normalized:one",
            "status": "active",
            "storage": "parquet",
            "dataset_refs": ("dataset:one",),
        },
    )

    audit = audit_script.audit_openbb_evidence_chain(db, run_id="run-1", runs_dir=runs_dir)

    assert audit.passed is True
    assert audit.as_dict()["missing_attempt_refs"] == []
    assert audit.as_dict()["missing_dataset_refs"] == []


def test_mongo_run_field_fallback_does_not_match_run_id_prefixes(tmp_path: Path) -> None:
    audit_script = _audit_module()
    db = _MongoLikeDatabase()
    db.insert(
        "provider_attempts",
        {
            "attempt_ref": "attempt:p:e:1",
            "request_id": "run-10:report-prefetch:market:1:daily_bar",
            "status": "success",
            "raw_refs": (),
            "dataset_refs": (),
        },
    )

    audit = audit_script.audit_openbb_evidence_chain(db, run_id="run-1", runs_dir=tmp_path / "runs")

    assert audit.passed is False
    assert audit.scope == "missing_run_evidence"


def _write_prefetch(
    runs_dir: Path,
    *,
    run_id: str,
    attempt_refs: list[str],
    raw_refs: list[str],
    dataset_refs: list[str],
    ok: bool = True,
    payload_run_id: str | None = None,
) -> None:
    path = runs_dir / run_id / "data-layer" / "report-prefetch.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "ok": ok,
                "schema_version": "report_data_prefetch.v1",
                "run_id": payload_run_id or run_id,
                "attempt_refs": attempt_refs,
                "raw_refs": raw_refs,
                "dataset_refs": dataset_refs,
            }
        ),
        encoding="utf-8",
    )
