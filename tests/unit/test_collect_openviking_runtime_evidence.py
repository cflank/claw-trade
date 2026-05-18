from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _collector_module():
    script_path = Path(__file__).parents[2] / "scripts" / "collect_openviking_runtime_evidence.py"
    spec = importlib.util.spec_from_file_location("collect_openviking_runtime_evidence", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_relation_query_uris_include_manifest_and_lineage_sources(tmp_path: Path) -> None:
    collector = _collector_module()
    run_id = "run-openviking"
    openviking_dir = tmp_path / "runs" / run_id / "openviking"
    openviking_dir.mkdir(parents=True)
    (openviking_dir / "approved-manifest.json").write_text(
        json.dumps(
            {
                "materials": [
                    {
                        "l1_uri": "viking://resources/workflow/run-openviking/frontline/market/report.md",
                        "l2_index_uri": "viking://resources/workflow/run-openviking/frontline/market/evidence/index.json",
                        "l2_index": {
                            "index_uri": "viking://resources/workflow/run-openviking/frontline/market/evidence/index.json",
                            "entries": [
                                {
                                    "uri": "viking://resources/workflow/run-openviking/frontline/market/evidence/pack.json"
                                }
                            ],
                        },
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (openviking_dir / "lineage-relations.json").write_text(
        json.dumps(
            {
                "relations": [
                    {
                        "from_uri": "mongo://openbb_provider_attempts/attempt-1",
                        "to_uri": "mongo://openbb_raw_payloads/raw-1",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    uris = collector._relation_query_uris(
        run_id,
        "viking://resources/workflow/run-openviking/",
        runs_dir=tmp_path / "runs",
    )

    assert uris[0] == "viking://resources/workflow/run-openviking/"
    assert "viking://resources/workflow/run-openviking/frontline/market/report.md" in uris
    assert "viking://resources/workflow/run-openviking/frontline/market/" in uris
    assert "viking://resources/workflow/run-openviking/frontline/market/evidence/index.json" in uris
    assert "viking://resources/workflow/run-openviking/frontline/market/evidence/" in uris
    assert "mongo://openbb_provider_attempts/attempt-1" in uris
    assert "mongo://openbb_raw_payloads/raw-1" in uris
    assert len(uris) == len(set(uris))


def test_relation_count_detects_common_relation_shapes() -> None:
    collector = _collector_module()

    assert collector._relation_count({"relations": [{"to_uri": "a"}]}) == 1
    assert collector._relation_count({"items": [{"to_uri": "a"}, {"to_uri": "b"}]}) == 2
    assert collector._relation_count({"from_uri": "a", "to_uri": "b"}) == 1
    assert collector._relation_count({"status": "blocked"}) == 0
