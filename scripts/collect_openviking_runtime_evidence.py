#!/usr/bin/env python3
from __future__ import annotations

from dataclasses import asdict, is_dataclass
import argparse
import json
from pathlib import Path
from typing import Any, Iterable

from claw_trade.artifacts.openviking_backend_http import create_default_backend
from claw_trade.artifacts.openviking_client import OpenVikingAccessError, OpenVikingClient
from claw_trade.data_gateway.openviking import OpenVikingMaterialPlane


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect OpenViking runtime evidence for one claw-trade run.")
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--grep-pattern", default="portfolio_manager")
    parser.add_argument("--glob-pattern", default="**/approved-manifest.json")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    client = OpenVikingClient(create_default_backend())
    plane = OpenVikingMaterialPlane(client)
    run_root_uri = f"viking://resources/workflow/{args.run_id}/"

    tree = plane.tree_run(args.run_id)
    grep = plane.grep_run(args.run_id, args.grep_pattern)
    glob = plane.glob_run(args.run_id, args.glob_pattern)
    context_index = plane.index_run_context(args.run_id)
    runtime_health = plane.runtime_health()
    relation_queries = _relation_query_uris(args.run_id, run_root_uri)
    relations_by_uri = {uri: _safe_relations(client, uri) for uri in relation_queries}
    relations = relations_by_uri.get(run_root_uri, {})
    relations_non_empty = any(_relation_count(item) > 0 for item in relations_by_uri.values())
    export_receipt = plane.export_run_pack(args.run_id, str(output_dir / "ovpack"))
    import_receipt = None
    if export_receipt.portability_status != "blocked" and Path(export_receipt.bundle_path).exists():
        import_receipt = plane.import_run_pack(
            bundle_path=export_receipt.bundle_path,
            target_run_id=f"import-check-{args.run_id}",
        )

    payload = {
        "run_id": args.run_id,
        "run_root_uri": run_root_uri,
        "tree": _jsonable(tree),
        "grep": _jsonable(grep),
        "glob": _jsonable(glob),
        "relations": _jsonable(relations),
        "relation_queries": [{"uri": uri, "relations": _jsonable(relations_by_uri[uri])} for uri in relation_queries],
        "relations_non_empty": relations_non_empty,
        "context_index": [_jsonable(item) for item in context_index],
        "ovpack_export": _jsonable(export_receipt),
        "ovpack_import": _jsonable(import_receipt),
        "runtime_health": _jsonable(runtime_health),
    }
    evidence_path = output_dir / f"openviking-runtime-evidence-{args.run_id}.json"
    evidence_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"evidence_path": str(evidence_path), **payload}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _safe_relations(client: OpenVikingClient, uri: str) -> object:
    try:
        return client.relations(uri=uri)
    except OpenVikingAccessError as exc:
        return {
            "status": "blocked",
            "root_cause": f"relations blocked: {exc.category}:{exc}",
        }


def _relation_query_uris(run_id: str, run_root_uri: str, *, runs_dir: Path = Path("runs")) -> list[str]:
    uris: list[str] = [run_root_uri]
    run_openviking_dir = runs_dir / run_id / "openviking"
    manifest = _load_json(run_openviking_dir / "approved-manifest.json")
    for material in _iter_materials(manifest):
        for key in ("l1_uri", "l2_index_uri"):
            value = material.get(key)
            if isinstance(value, str) and value.strip():
                uris.append(value.strip())
                uris.append(_parent_resource_uri(value.strip()))
        l2_index = material.get("l2_index")
        if isinstance(l2_index, dict):
            index_uri = l2_index.get("index_uri")
            if isinstance(index_uri, str) and index_uri.strip():
                uris.append(index_uri.strip())
                uris.append(_parent_resource_uri(index_uri.strip()))
            for entry in _iter_dicts(l2_index.get("entries")):
                uri = entry.get("uri")
                if isinstance(uri, str) and uri.strip():
                    uris.append(uri.strip())
                    uris.append(_parent_resource_uri(uri.strip()))

    lineage = _load_json(run_openviking_dir / "lineage-relations.json")
    for relation in _iter_dicts(lineage.get("relations") if isinstance(lineage, dict) else None):
        for key in ("from_uri", "to_uri"):
            value = relation.get(key)
            if isinstance(value, str) and value.strip():
                uris.append(value.strip())

    return _dedupe(uris)


def _parent_resource_uri(uri: str) -> str:
    text = uri.strip()
    if text.endswith("/"):
        return text
    head, separator, _tail = text.rpartition("/")
    if not separator:
        return text
    return head.rstrip("/") + "/"


def _load_json(path: Path) -> object:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _iter_materials(value: object) -> Iterable[dict[str, object]]:
    if not isinstance(value, dict):
        return ()
    return _iter_dicts(value.get("materials"))


def _iter_dicts(value: object) -> Iterable[dict[str, object]]:
    if not isinstance(value, list):
        return ()
    return (item for item in value if isinstance(item, dict))


def _dedupe(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def _relation_count(value: object) -> int:
    if isinstance(value, list):
        return len(value)
    if not isinstance(value, dict):
        return 0
    total = 0
    for key in ("relations", "items", "edges", "links", "rows", "results"):
        nested = value.get(key)
        if isinstance(nested, list):
            total += len(nested)
        elif isinstance(nested, dict):
            total += _relation_count(nested)
    if total:
        return total
    if {"from_uri", "to_uri"}.issubset(value):
        return 1
    return 0


def _jsonable(value: object) -> object:
    if value is None:
        return None
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


if __name__ == "__main__":
    raise SystemExit(main())
