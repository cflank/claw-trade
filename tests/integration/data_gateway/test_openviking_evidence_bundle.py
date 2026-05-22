from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from claw_trade.artifacts.openviking_client import OpenVikingClient, OpenVikingStat
from claw_trade.artifacts.refs import L2Index
from claw_trade.data_gateway.openviking import OpenVikingMaterialPlane


@dataclass
class _BackendBase:
    def ensure_namespace(self, namespace: str) -> None:
        return None

    def fetch_content_by_uri(self, uri: str) -> bytes:
        return b"ok"

    def fetch_l2_index_by_uri(self, uri: str | None) -> L2Index:
        return L2Index(entries=(), empty_reason=None, index_uri=uri, index_sha256=None, index_size_bytes=None)

    def fetch_stat_by_uri(self, uri: str) -> OpenVikingStat:
        return OpenVikingStat(uri=uri, ok=True, sha256="0" * 64, size_bytes=2, exists=True, is_dir=False)

    def fetch_receipt_by_path(self, receipt_path: Path):  # pragma: no cover - not used
        raise FileNotFoundError(receipt_path)


@dataclass
class _BackendBundleReady(_BackendBase):
    def export_run_pack(self, uri: str, output_dir: str) -> dict[str, object]:
        run_id = uri.rstrip("/").split("/")[-1]
        bundle_path = Path(output_dir) / f"{run_id}.ovpack"
        bundle_path.write_bytes(b"bundle")
        return {
            "run_id": run_id,
            "bundle_uri": f"{uri}evidence/bundles/{bundle_path.name}",
            "bundle_path": str(bundle_path),
            "sha256": "sha256:test-bundle",
            "size_bytes": bundle_path.stat().st_size,
            "portability_status": "metadata_verified",
            "raw_payload_policy": "external_store_required",
            "external_store_refs": ("mongo://openbb_provider_raw/raw-1",),
            "exported_at": "2026-05-17T00:00:00Z",
        }

    def import_run_pack(self, bundle_path: str, namespace: str, verify_hashes: bool) -> dict[str, object]:
        del verify_hashes
        return {
            "run_id": namespace.rsplit("/", 1)[-1],
            "bundle_uri": f"viking://resources/{namespace}/",
            "bundle_path": bundle_path,
            "sha256": "sha256:test-bundle",
            "size_bytes": Path(bundle_path).stat().st_size,
            "portability_status": "metadata_verified",
            "raw_payload_policy": "external_store_required",
            "external_store_refs": ("mongo://openbb_provider_raw/raw-1",),
            "exported_at": "2026-05-17T00:00:00Z",
            "import_status": "ok",
        }

    def tree(self, uri: str) -> dict[str, object]:
        return {
            "status": "ok",
            "nodes": [
                {"uri": uri, "kind": "dir"},
                {"uri": f"{uri}final_report/report.md", "kind": "file"},
            ],
        }

    def grep(self, uri: str, pattern: str) -> dict[str, object]:
        return {
            "status": "ok",
            "matches": [
                {"uri": f"{uri}final_report/report.md", "line": "主结论", "pattern": pattern},
            ],
        }

    def glob(self, uri: str, pattern: str) -> dict[str, object]:
        return {"status": "ok", "matches": [f"{uri}**/{pattern}"]}

    def relations(self, uri: str) -> dict[str, object]:
        return {
            "status": "ok",
            "relations": [
                {
                    "from_uri": uri,
                    "to_uri": "mongo://openbb_provider_attempts/attempt-1",
                    "kind": "pack_audit_to_provider_attempt",
                },
            ],
        }


def test_openviking_export_import_bundle_works_without_docs_evidence_dependency(tmp_path: Path) -> None:
    backend = _BackendBundleReady()
    plane = OpenVikingMaterialPlane(OpenVikingClient(backend=backend))

    export_receipt = plane.export_run_pack("run-pack-1", str(tmp_path))
    assert export_receipt.portability_status == "metadata_verified"
    assert export_receipt.bundle_path.startswith(str(tmp_path))
    assert "docs/evidence" not in export_receipt.bundle_path

    import_receipt = plane.import_run_pack(
        bundle_path=export_receipt.bundle_path,
        target_run_id="imported-run-1",
    )
    assert import_receipt.imported_run_id == "imported-run-1"
    assert import_receipt.import_status == "ok"
    assert import_receipt.portability_status == "metadata_verified"

    tree = plane.tree_run("imported-run-1")
    assert tree.status == "ok"
    assert tree.node_count >= 1

    grep = plane.grep_run("imported-run-1", "主结论")
    assert grep.status == "ok"
    assert grep.matches

    glob = plane.glob_run("imported-run-1", "report.md")
    assert glob.status == "ok"
    assert glob.matches

    relation_dump = plane.dump_relations(
        "viking://resources/workflow/imported/imported-run-1/final_report/report.md"
    )
    assert relation_dump.status == "ok"
    assert relation_dump.relations


def test_openviking_export_bundle_marks_blocked_when_upstream_api_unavailable(tmp_path: Path) -> None:
    plane = OpenVikingMaterialPlane(OpenVikingClient(backend=_BackendBase()))

    receipt = plane.export_run_pack("run-pack-2", str(tmp_path))

    assert receipt.portability_status == "blocked"
    assert receipt.raw_payload_policy == "blocked"
    assert receipt.import_status == "blocked"
    assert receipt.root_cause is not None
