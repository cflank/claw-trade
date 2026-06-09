from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from claw_trade.data_gateway.warehouse import DatasetRepository
from claw_trade.data_gateway.warehouse.normalized_columnar import NormalizedColumnarWarehouse


def _load_cli_module():
    script_path = Path(__file__).parents[3] / "scripts" / "selection" / "import_a_share_prepackaged_to_mongo.py"
    spec = importlib.util.spec_from_file_location("import_a_share_prepackaged_to_mongo", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _collections() -> dict[str, dict[str, object]]:
    return {name: {} for name in DatasetRepository.collection_names()}


def _daily_row() -> dict[str, object]:
    return {
        "dataset": "daily_bar",
        "market": "CN_A",
        "symbol_id": "600519.SH",
        "universe_ref": "all_a_shares",
        "granularity": "daily",
        "period_start": "2026-06-04",
        "period_end": "2026-06-04",
        "field_set": ("close", "volume"),
        "as_of": datetime(2026, 6, 4, tzinfo=UTC),
        "fresh_until": datetime(2026, 6, 5, tzinfo=UTC),
        "source_roles": ("local_seed",),
        "exchange": "SH",
        "currency": "CNY",
        "timezone": "Asia/Shanghai",
        "calendar": "CN_A_SSE_SZSE",
        "base_asset": None,
        "quote_asset": None,
        "provider_lineage": {
            "provider_id": "local_a_share_prepackaged",
            "endpoint_id": "a_share_prepackaged_selection_import",
            "raw_refs": ("raw:CN_A:local_a_share_prepackaged:test",),
            "remote_attempted": False,
        },
        "schema_id": "cn_a_selection_prepackaged.v1",
        "quality_flags": (),
        "row": {"close": 10.5, "volume": 1000},
    }


def test_a_share_import_cli_writes_normalized_rows_to_columnar_not_mongo_rows(tmp_path: Path, monkeypatch) -> None:
    cli = _load_cli_module()
    collections = _collections()
    output_json = tmp_path / "import.json"
    columnar_root = tmp_path / "columnar"

    class _Result:
        def as_dict(self) -> dict[str, object]:
            return {"dataset_refs": ["dataset:daily_bar:CN_A:600519.SH:2026-06-04"]}

    def fake_repository_from_mongo(mongo_uri: str, mongo_database: str, *, columnar_root):
        assert mongo_uri == "mongodb://127.0.0.1:27017/test_a_share"
        assert mongo_database == "test_a_share"
        return DatasetRepository(
            collections=collections,
            normalized_columnar=NormalizedColumnarWarehouse(columnar_root),
        )

    def fake_import_a_share_prepackaged_to_repository(**kwargs):
        repository = kwargs["repository"]
        repository.upsert_normalized_documents((_daily_row(),))
        return _Result()

    monkeypatch.setattr(cli, "_repository_from_mongo", fake_repository_from_mongo)
    monkeypatch.setattr(cli, "import_a_share_prepackaged_to_repository", fake_import_a_share_prepackaged_to_repository)

    exit_code = cli.main(
        (
            "--daily-root",
            str(tmp_path / "daily"),
            "--factor-root",
            str(tmp_path / "factor"),
            "--trade-date",
            "2026-06-07",
            "--import-run-id",
            "a-share-columnar-smoke",
            "--mongo-uri",
            "mongodb://127.0.0.1:27017/test_a_share",
            "--columnar-root",
            str(columnar_root),
            "--output-json",
            str(output_json),
        )
    )

    assert exit_code == 0
    payload = json.loads(output_json.read_text(encoding="utf-8"))
    assert payload["normalized_storage"] == "parquet"
    assert payload["catalog_storage"] == "mongo"
    assert collections["normalized_datasets"] == {}
    manifests = tuple(collections["dataset_manifests"].values())
    assert len(manifests) == 1
    assert manifests[0]["storage"] == "parquet"
    assert tuple(columnar_root.rglob("*.parquet"))
