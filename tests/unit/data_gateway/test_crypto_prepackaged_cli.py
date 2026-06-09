from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from zipfile import ZipFile

from claw_trade.data_gateway.warehouse import DatasetRepository
from claw_trade.data_gateway.warehouse.normalized_columnar import NormalizedColumnarWarehouse


def _load_cli_module():
    script_path = Path(__file__).parents[3] / "scripts" / "crypto" / "import_crypto_prepackaged_to_mongo.py"
    spec = importlib.util.spec_from_file_location("import_crypto_prepackaged_to_mongo", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_package(path: Path, payload: bytes = b"zip") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    path.with_name(f"{path.name}.CHECKSUM").write_text(f"{hashlib.sha256(payload).hexdigest()}  {path.name}\n", encoding="utf-8")


def _write_kline_package(path: Path, csv_body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    csv_name = path.name.replace(".zip", ".csv")
    with ZipFile(path, "w") as archive:
        archive.writestr(csv_name, csv_body)
    payload = path.read_bytes()
    path.with_name(f"{path.name}.CHECKSUM").write_text(
        f"{hashlib.sha256(payload).hexdigest()}  {path.name}\n",
        encoding="utf-8",
    )


def _collections() -> dict[str, dict[str, object]]:
    return {name: {} for name in DatasetRepository.collection_names()}


def test_build_import_jobs_discovers_spot_and_futures_packages(tmp_path: Path) -> None:
    cli = _load_cli_module()
    spot_root = tmp_path / "spot"
    futures_root = tmp_path / "futures"
    (spot_root / "data" / "spot" / "monthly" / "klines" / "BTCUSDT" / "1d").mkdir(parents=True)
    (futures_root / "data" / "futures" / "um" / "monthly" / "klines" / "BTCUSDT" / "1h").mkdir(parents=True)
    spot_zip = spot_root / "data" / "spot" / "monthly" / "klines" / "BTCUSDT" / "1d" / "BTCUSDT-1d-2026-05.zip"
    futures_zip = futures_root / "data" / "futures" / "um" / "monthly" / "klines" / "BTCUSDT" / "1h" / "BTCUSDT-1h-2026-05.zip"
    _write_package(spot_zip)
    _write_package(futures_zip)

    jobs = cli.build_import_jobs(
        spot_root=spot_root,
        futures_root=futures_root,
        assets=("BTC",),
        market_segments=("spot", "usdm_futures"),
        intervals=("1d", "1h"),
        start_date=cli.date.fromisoformat("2026-05-01"),
        end_date=cli.date.fromisoformat("2026-05-31"),
    )

    assert [(job.market_segment, job.interval, job.symbol_id) for job in jobs] == [
        ("spot", "1d", "BTCUSDT"),
        ("usdm_futures", "1h", "BTCUSDT"),
    ]


def test_build_import_jobs_uses_usdt_symbol_manifest_and_ignores_non_usdt_pairs(tmp_path: Path) -> None:
    cli = _load_cli_module()
    spot_root = tmp_path / "spot"
    package_dir = spot_root / "data" / "spot" / "monthly" / "klines" / "DOGEUSDT" / "1d"
    package_dir.mkdir(parents=True)
    _write_package(package_dir / "DOGEUSDT-1d-2026-05.zip")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "symbol_metadata": [
                    {
                        "symbol": "ETHBTC",
                        "base_asset": "ETH",
                        "quote_asset": "BTC",
                        "market_segment": "spot",
                    },
                    {
                        "symbol": "DOGEUSDT",
                        "base_asset": "DOGE",
                        "quote_asset": "USDT",
                        "market_segment": "spot",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    jobs = cli.build_import_jobs(
        spot_root=spot_root,
        futures_root=None,
        symbols=cli._load_symbol_specs((), None, (manifest,), None),
        market_segments=("spot",),
        intervals=("1d",),
        start_date=cli.date.fromisoformat("2026-05-01"),
        end_date=cli.date.fromisoformat("2026-05-31"),
    )

    assert [(job.symbol_id, job.base_asset, job.quote_asset) for job in jobs] == [("DOGEUSDT", "DOGE", "USDT")]


def test_build_import_jobs_indexes_each_root_once(tmp_path: Path, monkeypatch) -> None:
    cli = _load_cli_module()
    spot_root = tmp_path / "spot"
    calls: list[Path] = []

    def fake_index_packages(root: Path, **_kwargs):
        calls.append(root)
        return {
            ("BTCUSDT", "1d"): (root / "BTCUSDT-1d-2026-05.zip",),
            ("ETHUSDT", "1h"): (root / "ETHUSDT-1h-2026-05.zip",),
        }

    monkeypatch.setattr(cli, "_index_packages", fake_index_packages)

    jobs = cli.build_import_jobs(
        spot_root=spot_root,
        futures_root=None,
        symbols=(
            cli.CryptoSymbolSpec(asset="BTC", symbol_id="BTCUSDT", base_asset="BTC", quote_asset="USDT"),
            cli.CryptoSymbolSpec(asset="ETH", symbol_id="ETHUSDT", base_asset="ETH", quote_asset="USDT"),
        ),
        market_segments=("spot",),
        intervals=("1d", "1h"),
        start_date=cli.date.fromisoformat("2026-05-01"),
        end_date=cli.date.fromisoformat("2026-05-31"),
    )

    assert calls == [spot_root]
    assert [(job.symbol_id, job.interval, len(job.package_paths)) for job in jobs] == [
        ("BTCUSDT", "1d", 1),
        ("ETHUSDT", "1h", 1),
    ]


def test_build_import_jobs_filters_shared_root_by_market_segment(tmp_path: Path) -> None:
    cli = _load_cli_module()
    root = tmp_path / "binance"
    spot_zip = root / "data" / "spot" / "daily" / "klines" / "SOLUSDT" / "1d" / "SOLUSDT-1d-2026-06-01.zip"
    futures_zip = (
        root / "data" / "futures" / "um" / "daily" / "klines" / "SOLUSDT" / "1d" / "SOLUSDT-1d-2026-06-01.zip"
    )
    _write_package(spot_zip)
    _write_package(futures_zip)

    jobs = cli.build_import_jobs(
        spot_root=root,
        futures_root=root,
        assets=("SOL",),
        market_segments=("spot", "usdm_futures"),
        intervals=("1d",),
        start_date=cli.date.fromisoformat("2026-06-01"),
        end_date=cli.date.fromisoformat("2026-06-01"),
    )

    assert [(job.market_segment, job.package_paths) for job in jobs] == [
        ("spot", (spot_zip,)),
        ("usdm_futures", (futures_zip,)),
    ]


def test_build_import_jobs_maps_non_usdt_symbol_scope_to_usdt_pair(tmp_path: Path) -> None:
    cli = _load_cli_module()
    spot_root = tmp_path / "spot"
    package_dir = spot_root / "data" / "spot" / "monthly" / "klines" / "BTCUSDT" / "1d"
    package_zip = package_dir / "BTCUSDT-1d-2026-05.zip"
    _write_package(package_zip)

    jobs = cli.build_import_jobs(
        spot_root=spot_root,
        futures_root=None,
        assets=("BTCUSD",),
        market_segments=("spot",),
        intervals=("1d",),
        start_date=cli.date.fromisoformat("2026-05-01"),
        end_date=cli.date.fromisoformat("2026-05-31"),
    )

    assert [(job.symbol_id, job.base_asset, job.quote_asset, job.package_paths) for job in jobs] == [
        ("BTCUSDT", "BTC", "USDT", (package_zip,))
    ]


def test_import_cli_audit_only_writes_json_without_mongo(tmp_path: Path) -> None:
    cli = _load_cli_module()
    spot_root = tmp_path / "spot"
    package_dir = spot_root / "data" / "spot" / "monthly" / "klines" / "SOLUSDT" / "1d"
    package_dir.mkdir(parents=True)
    _write_package(package_dir / "SOLUSDT-1d-2026-06.zip")
    output_json = tmp_path / "audit.json"

    exit_code = cli.main(
        (
            "--spot-root",
            str(spot_root),
            "--trade-date",
            "2026-06-07",
            "--import-run-id",
            "import-1",
            "--start-date",
            "2026-06-01",
            "--end-date",
            "2026-06-30",
            "--symbol",
            "SOL",
            "--market-segment",
            "spot",
            "--interval",
            "1d",
            "--audit-only",
            "--output-json",
            str(output_json),
        )
    )

    assert exit_code == 0
    payload = json.loads(output_json.read_text(encoding="utf-8"))
    assert payload["audit_only"] is True
    assert payload["job_count"] == 1
    assert payload["jobs"][0]["symbol_id"] == "SOLUSDT"


def test_import_cli_audit_only_warns_for_packages_outside_symbol_scope(tmp_path: Path) -> None:
    cli = _load_cli_module()
    spot_root = tmp_path / "spot"
    sol_dir = spot_root / "data" / "spot" / "monthly" / "klines" / "SOLUSDT" / "1d"
    extra_dir = spot_root / "data" / "spot" / "monthly" / "klines" / "DELISTEDUSDT" / "1d"
    sol_dir.mkdir(parents=True)
    extra_dir.mkdir(parents=True)
    _write_package(sol_dir / "SOLUSDT-1d-2026-06.zip")
    _write_package(extra_dir / "DELISTEDUSDT-1d-2026-06.zip")
    output_json = tmp_path / "audit.json"

    exit_code = cli.main(
        (
            "--spot-root",
            str(spot_root),
            "--trade-date",
            "2026-06-07",
            "--import-run-id",
            "import-1",
            "--start-date",
            "2026-06-01",
            "--end-date",
            "2026-06-30",
            "--symbol",
            "SOL",
            "--market-segment",
            "spot",
            "--interval",
            "1d",
            "--audit-only",
            "--output-json",
            str(output_json),
        )
    )

    assert exit_code == 0
    payload = json.loads(output_json.read_text(encoding="utf-8"))
    assert payload["job_count"] == 1
    assert payload["warnings"] == [
        "delisted_symbol_skipped: spot:DELISTEDUSDT:1d package_count=1 not in selected discovery or symbol scope"
    ]


def test_import_cli_writes_normalized_rows_to_columnar_not_mongo_rows(tmp_path: Path, monkeypatch) -> None:
    cli = _load_cli_module()
    collections = _collections()
    spot_root = tmp_path / "spot"
    package_dir = spot_root / "data" / "spot" / "monthly" / "klines" / "SOLUSDT" / "1d"
    package_path = package_dir / "SOLUSDT-1d-2026-06.zip"
    _write_kline_package(
        package_path,
        "1780272000000000,150.10,155.20,149.00,153.40,1234.5,"
        "1780358399999999,188888.8,12345,500.0,76500.0,0\n",
    )
    output_json = tmp_path / "import.json"
    columnar_root = tmp_path / "columnar"

    def fake_repository_from_mongo(mongo_uri: str, mongo_database: str, *, columnar_root):
        assert mongo_uri == "mongodb://127.0.0.1:27017/test_crypto"
        assert mongo_database == ""
        return DatasetRepository(
            collections=collections,
            normalized_columnar=NormalizedColumnarWarehouse(columnar_root),
        )

    monkeypatch.setattr(cli, "_repository_from_mongo", fake_repository_from_mongo)

    exit_code = cli.main(
        (
            "--spot-root",
            str(spot_root),
            "--trade-date",
            "2026-06-07",
            "--import-run-id",
            "import-columnar",
            "--start-date",
            "2026-06-01",
            "--end-date",
            "2026-06-30",
            "--symbol",
            "SOL",
            "--market-segment",
            "spot",
            "--interval",
            "1d",
            "--mongo-uri",
            "mongodb://127.0.0.1:27017/test_crypto",
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
    assert any(manifest.get("storage") == "parquet" for manifest in manifests)
    assert any(manifest.get("job_type") == "seed_import" for manifest in manifests)
    assert tuple(columnar_root.rglob("*.parquet"))


def test_import_cli_rejects_package_without_checksum(tmp_path: Path) -> None:
    cli = _load_cli_module()
    spot_root = tmp_path / "spot"
    package_dir = spot_root / "data" / "spot" / "monthly" / "klines" / "SOLUSDT" / "1d"
    package_dir.mkdir(parents=True)
    (package_dir / "SOLUSDT-1d-2026-06.zip").write_bytes(b"zip")

    try:
        cli.build_import_jobs(
            spot_root=spot_root,
            futures_root=None,
            assets=("SOL",),
            market_segments=("spot",),
            intervals=("1d",),
            start_date=cli.date.fromisoformat("2026-06-01"),
            end_date=cli.date.fromisoformat("2026-06-30"),
        )
    except FileNotFoundError as exc:
        assert "checksum file missing" in str(exc)
    else:
        raise AssertionError("expected import CLI to reject unverified package")


def test_import_cli_rejects_package_with_bad_checksum(tmp_path: Path) -> None:
    cli = _load_cli_module()
    spot_root = tmp_path / "spot"
    package_dir = spot_root / "data" / "spot" / "monthly" / "klines" / "SOLUSDT" / "1d"
    package_dir.mkdir(parents=True)
    zip_path = package_dir / "SOLUSDT-1d-2026-06.zip"
    zip_path.write_bytes(b"zip")
    zip_path.with_name(f"{zip_path.name}.CHECKSUM").write_text("0  SOLUSDT-1d-2026-06.zip\n", encoding="utf-8")

    try:
        cli.build_import_jobs(
            spot_root=spot_root,
            futures_root=None,
            assets=("SOL",),
            market_segments=("spot",),
            intervals=("1d",),
            start_date=cli.date.fromisoformat("2026-06-01"),
            end_date=cli.date.fromisoformat("2026-06-30"),
        )
    except ValueError as exc:
        assert "checksum failed" in str(exc)
    else:
        raise AssertionError("expected import CLI to reject checksum mismatch")


def test_import_cli_replace_existing_requires_confirmation(tmp_path: Path) -> None:
    cli = _load_cli_module()

    try:
        cli.main(
            (
                "--spot-root",
                str(tmp_path),
                "--trade-date",
                "2026-06-07",
                "--import-run-id",
                "import-1",
                "--start-date",
                "2026-06-01",
                "--end-date",
                "2026-06-30",
                "--replace-existing",
                "--audit-only",
            )
        )
    except SystemExit as exc:
        assert "--confirm-replace-existing" in str(exc)
    else:
        raise AssertionError("expected replace-existing to require explicit confirmation")


def test_import_cli_requires_explicit_mongo_uri_when_not_audit_only(tmp_path: Path, monkeypatch) -> None:
    cli = _load_cli_module()
    monkeypatch.delenv("DATA_GATEWAY_MONGODB_URI", raising=False)
    monkeypatch.delenv("CRYPTO_MONGODB_URI", raising=False)

    try:
        cli.main(
            (
                "--spot-root",
                str(tmp_path),
                "--trade-date",
                "2026-06-07",
                "--import-run-id",
                "import-1",
                "--start-date",
                "2026-06-01",
                "--end-date",
                "2026-06-30",
                "--phase1-sample",
            )
        )
    except SystemExit as exc:
        assert "missing --mongo-uri" in str(exc)
    else:
        raise AssertionError("expected missing mongo uri to fail")


def test_import_cli_requires_explicit_symbol_scope(tmp_path: Path) -> None:
    cli = _load_cli_module()

    try:
        cli.main(
            (
                "--spot-root",
                str(tmp_path),
                "--trade-date",
                "2026-06-07",
                "--import-run-id",
                "import-1",
                "--start-date",
                "2026-06-01",
                "--end-date",
                "2026-06-30",
                "--audit-only",
            )
        )
    except ValueError as exc:
        assert "explicit symbol scope required" in str(exc)
    else:
        raise AssertionError("expected missing symbol scope to fail")
