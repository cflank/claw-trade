from __future__ import annotations

import hashlib
import importlib.util
import sys
import urllib.error
from datetime import date
from pathlib import Path


def _load_downloader_module():
    script_path = Path(__file__).parents[3] / "scripts" / "crypto" / "download_binance_public_data.py"
    spec = importlib.util.spec_from_file_location("download_binance_public_data", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_build_spot_mixed_monthly_and_daily_kline_download_plan() -> None:
    downloader = _load_downloader_module()

    manifest = downloader.build_download_plan(
        output_root=Path("/tmp/binance"),
        market_segment="spot",
        interval="1d",
        symbols=("solusdt",),
        start_date=date(2026, 5, 1),
        end_date=date(2026, 6, 7),
        checksum_required=True,
    )

    items = manifest["items"]
    assert manifest["item_count"] == 8
    assert items[0]["symbol"] == "SOLUSDT"
    assert items[0]["frequency"] == "monthly"
    assert items[0]["url"] == (
        "https://data.binance.vision/data/spot/monthly/klines/"
        "SOLUSDT/1d/SOLUSDT-1d-2026-05.zip"
    )
    assert items[0]["checksum_url"] == items[0]["url"] + ".CHECKSUM"
    assert items[1]["frequency"] == "daily"
    assert items[1]["url"].endswith("/daily/klines/SOLUSDT/1d/SOLUSDT-1d-2026-06-01.zip")
    assert items[-1]["url"].endswith("/daily/klines/SOLUSDT/1d/SOLUSDT-1d-2026-06-07.zip")


def test_build_usdm_futures_monthly_kline_download_plan() -> None:
    downloader = _load_downloader_module()

    manifest = downloader.build_download_plan(
        output_root=Path("/tmp/binance"),
        market_segment="usdm_futures",
        interval="1h",
        symbols=("BTCUSDT",),
        start_date=date(2026, 6, 1),
        end_date=date(2026, 6, 30),
        checksum_required=False,
    )

    item = manifest["items"][0]
    assert item["url"] == (
        "https://data.binance.vision/data/futures/um/monthly/klines/"
        "BTCUSDT/1h/BTCUSDT-1h-2026-06.zip"
    )
    assert item["checksum_required"] is False


def test_build_download_plan_percent_encodes_non_ascii_symbols() -> None:
    downloader = _load_downloader_module()

    manifest = downloader.build_download_plan(
        output_root=Path("/tmp/binance"),
        market_segment="spot",
        interval="1d",
        symbols=("币安人生USDT",),
        start_date=date(2026, 6, 1),
        end_date=date(2026, 6, 1),
        checksum_required=True,
    )

    item = manifest["items"][0]
    assert item["symbol"] == "币安人生USDT"
    assert "%E5%B8%81%E5%AE%89%E4%BA%BA%E7%94%9FUSDT" in item["url"]
    assert item["checksum_url"] == item["url"] + ".CHECKSUM"


def test_symbol_file_and_max_symbols_are_applied(tmp_path: Path) -> None:
    downloader = _load_downloader_module()
    symbol_file = tmp_path / "symbols.txt"
    symbol_file.write_text("BTCUSDT\nETHUSDT\nSOLUSDT\n", encoding="utf-8")

    symbols = downloader._load_symbols(("BTCUSDT",), symbol_file, 2)

    assert symbols == ("BTCUSDT", "ETHUSDT")


def test_all_symbols_from_spot_exchange_info_are_normalized() -> None:
    downloader = _load_downloader_module()

    symbols = downloader._spot_symbols(
        {
            "symbols": (
                {
                    "symbol": "ETHBTC",
                    "status": "TRADING",
                    "baseAsset": "ETH",
                    "quoteAsset": "BTC",
                    "isSpotTradingAllowed": True,
                },
                {
                    "symbol": "ETHUSDT",
                    "status": "TRADING",
                    "baseAsset": "ETH",
                    "quoteAsset": "USDT",
                    "isSpotTradingAllowed": True,
                },
                {
                    "symbol": "OLDUSDT",
                    "status": "BREAK",
                    "baseAsset": "OLD",
                    "quoteAsset": "USDT",
                    "isSpotTradingAllowed": True,
                },
            )
        }
    )

    assert symbols == (
        {
            "symbol": "ETHUSDT",
            "base_asset": "ETH",
            "quote_asset": "USDT",
            "status": "TRADING",
            "market_segment": "spot",
            "source": "binance_spot_exchange_info",
        },
    )


def test_all_symbols_from_usdm_exchange_info_keep_perpetuals() -> None:
    downloader = _load_downloader_module()

    symbols = downloader._usdm_symbols(
        {
            "symbols": (
                {
                    "symbol": "BTCUSDT",
                    "status": "TRADING",
                    "contractType": "PERPETUAL",
                    "baseAsset": "BTC",
                    "quoteAsset": "USDT",
                    "underlyingType": "COIN",
                },
                {
                    "symbol": "BTCUSDT_260626",
                    "status": "TRADING",
                    "contractType": "CURRENT_QUARTER",
                    "baseAsset": "BTC",
                    "quoteAsset": "USDT",
                },
            )
        }
    )

    assert symbols[0]["symbol"] == "BTCUSDT"
    assert symbols[0]["market_segment"] == "usdm_futures"
    assert symbols[0]["contract_type"] == "PERPETUAL"


def test_dry_run_cli_writes_manifest_without_downloading(tmp_path: Path) -> None:
    downloader = _load_downloader_module()
    output_json = tmp_path / "manifest.json"
    output_root = tmp_path / "downloads"

    exit_code = downloader.main(
        (
            "--output-root",
            str(output_root),
            "--market-segment",
            "spot",
            "--interval",
            "1d",
            "--start-date",
            "2026-06-01",
            "--end-date",
            "2026-06-01",
            "--symbol",
            "SOLUSDT",
            "--dry-run",
            "--checksum-required",
            "--output-json",
            str(output_json),
        )
    )

    assert exit_code == 0
    assert output_json.exists()
    assert not output_root.exists()
    assert "SOLUSDT-1d-2026-06-01.zip" in output_json.read_text(encoding="utf-8")


def test_load_symbols_accepts_discovered_symbols() -> None:
    downloader = _load_downloader_module()

    assert downloader._load_symbols((), None, 2, discovered_symbols=("ETHBTC", "BTCUSDT", "ETHBTC")) == (
        "ETHUSDT",
        "BTCUSDT",
    )


def test_load_symbols_maps_bare_and_non_usdt_inputs_to_usdt() -> None:
    downloader = _load_downloader_module()

    assert downloader._load_symbols(("BTCUSD", "SOL", "ETH/BTC"), None, None) == (
        "BTCUSDT",
        "SOLUSDT",
        "ETHUSDT",
    )


def test_sha256_checksum_verification(tmp_path: Path) -> None:
    downloader = _load_downloader_module()
    zip_path = tmp_path / "BTCUSDT-1d-2026-06.zip"
    zip_path.write_bytes(b"sample")
    checksum = hashlib.sha256(b"sample").hexdigest()
    checksum_path = tmp_path / "BTCUSDT-1d-2026-06.zip.CHECKSUM"
    checksum_path.write_text(f"{checksum}  BTCUSDT-1d-2026-06.zip\n", encoding="utf-8")

    assert downloader.verify_sha256(zip_path, checksum_path) is True


def test_download_items_skips_existing_verified_file(tmp_path: Path) -> None:
    downloader = _load_downloader_module()
    relative_path = "data/spot/monthly/klines/BTCUSDT/1d/BTCUSDT-1d-2026-06.zip"
    zip_path = tmp_path / relative_path
    zip_path.parent.mkdir(parents=True)
    zip_path.write_bytes(b"sample")
    checksum_path = tmp_path / f"{relative_path}.CHECKSUM"
    checksum_path.write_text(f"{hashlib.sha256(b'sample').hexdigest()}  BTCUSDT-1d-2026-06.zip\n", encoding="utf-8")
    manifest = {
        "items": (
            {
                "relative_path": relative_path,
                "checksum_relative_path": f"{relative_path}.CHECKSUM",
                "url": "https://example.invalid/file.zip",
                "checksum_url": "https://example.invalid/file.zip.CHECKSUM",
                "checksum_required": True,
            },
        )
    }

    result = downloader.download_items(manifest, output_root=tmp_path)

    assert result["items"][0]["status"] == "skipped_existing"
    assert result["items"][0]["checksum_status"] == "verified"


def test_download_items_fails_existing_file_without_required_checksum(tmp_path: Path) -> None:
    downloader = _load_downloader_module()
    relative_path = "data/spot/monthly/klines/BTCUSDT/1d/BTCUSDT-1d-2026-06.zip"
    zip_path = tmp_path / relative_path
    zip_path.parent.mkdir(parents=True)
    zip_path.write_bytes(b"sample")
    manifest = {
        "items": (
            {
                "relative_path": relative_path,
                "checksum_relative_path": f"{relative_path}.CHECKSUM",
                "url": "https://example.invalid/file.zip",
                "checksum_url": "https://example.invalid/file.zip.CHECKSUM",
                "checksum_required": True,
            },
        )
    }

    try:
        downloader.download_items(manifest, output_root=tmp_path)
    except FileNotFoundError as exc:
        assert "checksum file missing" in str(exc)
    else:
        raise AssertionError("expected existing unverified package to fail closed")


def test_download_items_removes_zip_when_downloaded_checksum_is_missing(tmp_path: Path) -> None:
    downloader = _load_downloader_module()
    relative_path = "data/spot/monthly/klines/BTCUSDT/1d/BTCUSDT-1d-2026-06.zip"
    manifest = {
        "items": (
            {
                "relative_path": relative_path,
                "checksum_relative_path": f"{relative_path}.CHECKSUM",
                "url": "https://example.invalid/file.zip",
                "checksum_url": "https://example.invalid/file.zip.CHECKSUM",
                "checksum_required": True,
            },
        )
    }

    def retriever(url: str, destination: Path) -> None:
        if url.endswith(".CHECKSUM"):
            raise urllib.error.HTTPError(url, 404, "missing", {}, None)
        destination.write_bytes(b"sample")

    result = downloader.download_items(manifest, output_root=tmp_path, retriever=retriever, ignore_missing=True)

    assert result["items"][0]["status"] == "missing"
    assert result["items"][0]["checksum_status"] == "missing"
    assert not (tmp_path / relative_path).exists()


def test_checksum_required_fails_when_checksum_file_is_missing(tmp_path: Path) -> None:
    downloader = _load_downloader_module()
    zip_path = tmp_path / "BTCUSDT-1d-2026-06.zip"
    zip_path.write_bytes(b"sample")

    try:
        downloader.verify_sha256(zip_path, tmp_path / "missing.CHECKSUM")
    except FileNotFoundError as exc:
        assert "checksum file missing" in str(exc)
    else:
        raise AssertionError("expected missing checksum to fail closed")
