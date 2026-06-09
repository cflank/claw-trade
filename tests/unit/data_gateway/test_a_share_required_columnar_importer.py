from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace


def _load_cli_module():
    script_path = Path(__file__).parents[3] / "scripts" / "selection" / "import_a_share_required_to_columnar.py"
    spec = importlib.util.spec_from_file_location("import_a_share_required_to_columnar", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_required_a_share_importer_writes_identity_into_daily_rows() -> None:
    cli = _load_cli_module()
    identity = SimpleNamespace(
        company_name="平安银行",
        industry="银行",
        list_date="19910403",
        source_ref="baostock://query_all_stock/2026-05-27",
    )

    record = cli._daily_bar_from_factor(
        symbol="000001.SZ",
        row_date="2026-05-27",
        raw={
            "open_qfq": "10",
            "high_qfq": "11",
            "low_qfq": "9",
            "close_qfq": "10.5",
            "vol": "1000",
        },
        raw_ref="raw:CN_A:local_a_share_required:test",
        as_of=datetime(2026, 6, 8, tzinfo=UTC),
        source_ref="zip://stk_factor_pro2026.zip/000001.SZ.csv",
        identity=identity,
    )

    assert record is not None
    assert record["row"]["company_name"] == "平安银行"
    assert record["row"]["industry"] == "银行"
    assert record["row"]["list_date"] == "19910403"
    assert record["row"]["identity_source_ref"] == "baostock://query_all_stock/2026-05-27"
    assert {"company_name", "industry", "list_date", "identity_source_ref"}.issubset(record["field_set"])


def test_required_a_share_importer_filters_daily_only_rows_by_date_window(tmp_path: Path) -> None:
    cli = _load_cli_module()
    identity = SimpleNamespace(
        company_name="平安银行",
        industry="银行",
        list_date="19910403",
        source_ref="file://local_identity",
    )
    daily_path = tmp_path / "000001_SZ.csv"
    daily_path.write_text(
        "\n".join(
            (
                "trade_date,open,high,low,close,vol,amount,pct_chg,adj_factor",
                "2025-03-02,9,10,8,9.5,100,1000,1,1",
                "2025-03-03,10,11,9,10.5,110,1100,2,1",
                "2026-05-27,11,12,10,11.5,120,1200,3,1",
                "2026-05-28,12,13,11,12.5,130,1300,4,1",
            )
        ),
        encoding="utf-8",
    )

    records = tuple(
        cli._daily_only_records(
            symbol="000001.SZ",
            path=daily_path,
            raw_ref="raw:CN_A:local_a_share_required:test",
            as_of=datetime(2026, 6, 8, tzinfo=UTC),
            start_date="2025-03-03",
            end_date="2026-05-27",
            identity=identity,
        )
    )

    assert [record["period_start"] for record in records] == ["2025-03-03", "2026-05-27"]
    for record in records:
        assert record["row"]["company_name"] == "平安银行"
        assert record["row"]["industry"] == "银行"
        assert record["row"]["list_date"] == "19910403"
        assert record["row"]["identity_source_ref"] == "file://local_identity"
        assert {"company_name", "industry", "list_date", "identity_source_ref"}.issubset(record["field_set"])


def test_required_a_share_importer_filters_symbols_without_required_identity() -> None:
    cli = _load_cli_module()

    full_symbols, factor_2026_symbols, daily_only_symbols, skipped = cli._filter_symbols_by_identity(
        full_symbols={"000001.SZ", "000002.SZ"},
        factor_2026_symbols={"000001.SZ", "000003.SZ"},
        daily_only_symbols={"002231.SZ"},
        identity_facts={
            "000001.SZ": object(),
            "000002.SZ": object(),
        },
    )

    assert full_symbols == {"000001.SZ", "000002.SZ"}
    assert factor_2026_symbols == {"000001.SZ"}
    assert daily_only_symbols == set()
    assert skipped == ("000003.SZ", "002231.SZ")
