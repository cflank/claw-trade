from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

from claw_trade.data_gateway.a_share_prepackaged_importer import (
    audit_a_share_prepackaged_packages,
    import_a_share_prepackaged_to_repository,
)
from claw_trade.data_gateway.warehouse import DatasetRepository


def test_a_share_prepackaged_audit_compares_daily_and_factor_packages(tmp_path: Path) -> None:
    daily_root = tmp_path / "a_stock_daily"
    factor_root = tmp_path / "stk_factor_pro"
    local_root = tmp_path / "missing-local"
    _write_daily_csv(daily_root / "000001_SZ.csv")
    _write_daily_csv(daily_root / "000002_SZ.csv", symbol="000002.SZ")
    _write_factor_zip(factor_root / "stk_factor_pro2026.zip", symbols=("000001.SZ", "600000.SH"))
    (factor_root / "stk_factor_pro.zip.001").write_bytes(b"PK\x03\x04split")

    audit = audit_a_share_prepackaged_packages(
        daily_root=daily_root,
        factor_root=factor_root,
        local_roots=(local_root,),
    )

    assert audit.daily_symbol_count == 2
    assert audit.factor_symbol_count == 2
    assert audit.intersection_symbol_count == 1
    assert audit.daily_only_symbol_count == 1
    assert audit.factor_only_symbol_count == 1
    assert "factor_split_zip_parts_present_not_imported" in audit.warnings
    assert "local_package_roots_missing" in audit.warnings


def test_a_share_prepackaged_import_writes_unified_selection_records(tmp_path: Path) -> None:
    daily_root = tmp_path / "a_stock_daily"
    factor_root = tmp_path / "stk_factor_pro"
    _write_daily_csv(daily_root / "000001_SZ.csv")
    _write_factor_zip(factor_root / "stk_factor_pro2026.zip", symbols=("000001.SZ",))
    repo = DatasetRepository()
    repo.insert_normalized(_stale_prepackaged_record())

    result = import_a_share_prepackaged_to_repository(
        daily_root=daily_root,
        factor_root=factor_root,
        repository=repo,
        trade_date="2026-05-30",
        import_run_id="import-test",
        history_days=4,
        replace_existing=True,
    )

    assert result.imported_symbol_count == 1
    assert result.deleted_existing_count == 1
    assert result.warning_counts["company_name_missing"] == 1
    assert result.warning_counts["private_placement_missing"] == 1
    assert result.raw_refs
    assert result.attempt_refs
    assert result.manifest_ref
    records = repo.query_normalized(
        dataset="daily_bar",
        market="CN_A",
        symbol_id=None,
        universe_ref="all_a_shares",
    )
    assert len(records) == 1
    row = records[0].row
    assert row["ticker"] == "000001.SZ"
    assert row["date"] == "2026-05-27"
    assert row["trade_date"] == "2026-05-27"
    assert row["history"][-1]["date"] == "2026-05-27"
    assert records[0].period_end == "2026-05-27"
    assert row["amount"] == 905710900.0
    assert row["market_cap"] == 20880767.981
    assert "company_name" not in row
    assert records[0].source_roles == ("local_seed",)
    attempt = repo.get_provider_attempt(result.attempt_refs[0])
    assert attempt is not None
    assert attempt["remote_attempted"] is False
    assert attempt["remote_success"] is False
    assert attempt["status"] == "local_seed_imported"


def test_a_share_prepackaged_import_adds_identity_and_private_placement_coverage(tmp_path: Path) -> None:
    daily_root = tmp_path / "a_stock_daily"
    factor_root = tmp_path / "stk_factor_pro"
    baostock_root = tmp_path / "baostock"
    private_root = tmp_path / "akshare_stock_add_stock"
    _write_daily_csv(daily_root / "000001_SZ.csv")
    _write_factor_zip(factor_root / "stk_factor_pro2026.zip", symbols=("000001.SZ",))
    _write_identity_csvs(baostock_root)
    _write_empty_private_placement_csv(private_root / "000001.csv")
    repo = DatasetRepository()

    result = import_a_share_prepackaged_to_repository(
        daily_root=daily_root,
        factor_root=factor_root,
        repository=repo,
        trade_date="2026-05-30",
        import_run_id="import-identity-test",
        local_roots=(baostock_root, private_root),
        history_days=4,
    )

    assert result.imported_symbol_count == 1
    assert result.warning_counts["company_name_missing"] == 0
    assert result.warning_counts["private_placement_missing"] == 0
    records = repo.query_normalized(
        dataset="daily_bar",
        market="CN_A",
        symbol_id=None,
        universe_ref="all_a_shares",
    )
    row = records[0].row
    assert row["company_name"] == "平安银行"
    assert row["industry"] == "银行"
    assert row["list_date"] == "1991-04-03"
    assert row["private_placement_event_date"] == "none"
    assert row["private_placement_days_since"] == 9999.0
    assert row["data_quality_warnings"] == ()


def test_a_share_prepackaged_import_keeps_private_placement_missing_without_event_file(tmp_path: Path) -> None:
    daily_root = tmp_path / "a_stock_daily"
    factor_root = tmp_path / "stk_factor_pro"
    baostock_root = tmp_path / "baostock"
    _write_daily_csv(daily_root / "000001_SZ.csv")
    _write_factor_zip(factor_root / "stk_factor_pro2026.zip", symbols=("000001.SZ",))
    _write_identity_csvs(baostock_root)
    repo = DatasetRepository()

    result = import_a_share_prepackaged_to_repository(
        daily_root=daily_root,
        factor_root=factor_root,
        repository=repo,
        trade_date="2026-05-30",
        import_run_id="import-private-missing-test",
        local_roots=(baostock_root,),
        history_days=4,
    )

    assert result.warning_counts["company_name_missing"] == 0
    assert result.warning_counts["private_placement_missing"] == 1
    records = repo.query_normalized(
        dataset="daily_bar",
        market="CN_A",
        symbol_id=None,
        universe_ref="all_a_shares",
    )
    row = records[0].row
    assert "private_placement_event_date" not in row
    assert "private_placement_days_since" not in row
    assert row["data_quality_warnings"] == ("private_placement_missing",)


def test_a_share_prepackaged_import_fetches_identity_from_baostock(
    tmp_path: Path,
    monkeypatch,
) -> None:
    daily_root = tmp_path / "a_stock_daily"
    factor_root = tmp_path / "stk_factor_pro"
    _write_daily_csv(daily_root / "000001_SZ.csv")
    _write_factor_zip(factor_root / "stk_factor_pro2026.zip", symbols=("000001.SZ",))
    _install_fake_baostock(monkeypatch, rows=(("sz.000001", "平安银行"),))
    repo = DatasetRepository()

    result = import_a_share_prepackaged_to_repository(
        daily_root=daily_root,
        factor_root=factor_root,
        repository=repo,
        trade_date="2026-05-30",
        import_run_id="import-baostock-identity-test",
        identity_source_order=("baostock", "akshare", "tushare"),
        history_days=4,
    )

    assert result.warning_counts["company_name_missing"] == 0
    assert result.identity_sources_attempted == ("baostock",)
    assert result.identity_sources_loaded == ("baostock",)
    assert result.identity_loaded_count == 1
    records = repo.query_normalized(
        dataset="daily_bar",
        market="CN_A",
        symbol_id=None,
        universe_ref="all_a_shares",
    )
    row = records[0].row
    assert row["company_name"] == "平安银行"
    assert row["identity_source_ref"] == "baostock://query_all_stock/2026-05-30"


def test_a_share_prepackaged_import_falls_back_to_akshare_when_baostock_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    daily_root = tmp_path / "a_stock_daily"
    factor_root = tmp_path / "stk_factor_pro"
    _write_daily_csv(daily_root / "000001_SZ.csv")
    _write_factor_zip(factor_root / "stk_factor_pro2026.zip", symbols=("000001.SZ",))
    _install_failing_baostock(monkeypatch)
    _install_fake_akshare(monkeypatch, rows=(("000001", "平安银行"),))
    repo = DatasetRepository()

    result = import_a_share_prepackaged_to_repository(
        daily_root=daily_root,
        factor_root=factor_root,
        repository=repo,
        trade_date="2026-05-30",
        import_run_id="import-akshare-fallback-identity-test",
        identity_source_order=("baostock", "akshare", "tushare"),
        history_days=4,
    )

    assert result.warning_counts["company_name_missing"] == 0
    assert result.identity_sources_attempted == ("baostock", "akshare")
    assert result.identity_sources_loaded == ("akshare",)
    assert result.identity_errors == ("baostock:RuntimeError:baostock_login_failed",)
    records = repo.query_normalized(
        dataset="daily_bar",
        market="CN_A",
        symbol_id=None,
        universe_ref="all_a_shares",
    )
    row = records[0].row
    assert row["company_name"] == "平安银行"
    assert row["identity_source_ref"] == "akshare://stock_info_a_code_name"


def _stale_prepackaged_record() -> dict[str, object]:
    as_of = datetime(2026, 5, 27, tzinfo=UTC)
    return {
        "dataset": "daily_bar",
        "market": "CN_A",
        "symbol_id": "000001.SZ",
        "universe_ref": "all_a_shares",
        "granularity": "daily",
        "period_start": "2026-03-30",
        "period_end": "2026-05-30",
        "field_set": ("date", "open", "high", "low", "close", "volume", "amount"),
        "as_of": as_of,
        "fresh_until": as_of,
        "source_roles": ("local_seed",),
        "exchange": "SZSE",
        "currency": "CNY",
        "timezone": "Asia/Shanghai",
        "calendar": "CN_A_SSE_SZSE",
        "base_asset": None,
        "quote_asset": None,
        "provider_lineage": {
            "provider_id": "local_a_share_prepackaged",
            "endpoint_id": "a_share_prepackaged_selection_import",
            "raw_refs": ("raw:stale",),
            "remote_attempted": False,
        },
        "schema_id": "cn_a_selection_prepackaged.v1",
        "quality_flags": ("company_name_missing",),
        "row": {"ticker": "000001.SZ", "date": "2026-05-30", "close": 1.0},
    }


def _write_daily_csv(path: Path, *, symbol: str = "000001.SZ") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\ufeffdate,stock_code,adj_factor,amount,change,close,high,low,open,pct_chg,pre_close,trade_date,vol\n"
        f"2026-03-30,{symbol},1,696208.267,-0.03,10.99,11.05,10.94,10.98,-0.2722,11.02,2026-03-30,632522.1\n"
        f"2026-03-31,{symbol},1,1294675.716,0.09,11.08,11.18,10.99,11.0,0.8189,10.99,2026-03-31,1164565.34\n"
        f"2026-04-01,{symbol},1,1025538.833,0.07,11.15,11.23,11.08,11.09,0.6318,11.08,2026-04-01,918925.39\n",
        encoding="utf-8",
    )


def _write_factor_zip(path: Path, *, symbols: tuple[str, ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(path, "w") as archive:
        for symbol in symbols:
            archive.writestr(
                f"stk_factor_pro2026/{symbol}.csv",
                "ts_code,trade_date,open,open_qfq,high,high_qfq,low,low_qfq,close,close_qfq,"
                "pre_close,change,pct_chg,vol,amount,turnover_rate,volume_ratio,pe,pe_ttm,pb,ps,ps_ttm,"
                "total_mv,circ_mv,adj_factor\n"
                f"{symbol},20260526,10.71,10.71,10.83,10.83,10.66,10.66,10.79,10.79,"
                "10.68,0.11,1.03,837346.9,900879.54,0.4315,0.98,4.9115,4.8627,0.4512,1.593,1.5742,"
                "20938985.7356,20938643.1046,134.5794\n"
                f"{symbol},20260527,10.76,10.76,10.85,10.85,10.72,10.72,10.76,10.76,"
                "10.79,-0.03,-0.278,839727.18,905710.9,0.4327,0.97,4.8978,4.8492,0.4499,1.5886,1.5699,"
                "20880767.981,20880426.3026,134.5794\n",
            )


def _write_identity_csvs(root: Path) -> None:
    basic = root / "basic"
    basic.mkdir(parents=True, exist_ok=True)
    (basic / "stock_basic.csv").write_text(
        "code,code_name,ipoDate,outDate,type,status\n"
        "sz.000001,平安银行,1991-04-03,,1,1\n",
        encoding="utf-8",
    )
    (basic / "stock_industry.csv").write_text(
        "date,code,code_name,industry,industryClassification\n"
        "2026-05-30,sz.000001,平安银行,银行,申万一级\n",
        encoding="utf-8",
    )


def _write_empty_private_placement_csv(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("公告日期,发行方式\n", encoding="utf-8")


def _install_fake_baostock(monkeypatch, *, rows: tuple[tuple[str, str], ...]) -> None:
    class _Result:
        fields = ("code", "code_name")

        def __init__(self) -> None:
            self.error_code = "0"
            self.error_msg = ""
            self._index = -1

        def next(self) -> bool:
            self._index += 1
            return self._index < len(rows)

        def get_row_data(self) -> list[str]:
            code, name = rows[self._index]
            return [code, name]

    module = SimpleNamespace(
        login=lambda: SimpleNamespace(error_code="0", error_msg=""),
        logout=lambda: None,
        query_all_stock=lambda day=None: _Result(),
    )
    monkeypatch.setitem(sys.modules, "baostock", module)


def _install_failing_baostock(monkeypatch) -> None:
    module = SimpleNamespace(
        login=lambda: SimpleNamespace(error_code="1", error_msg="baostock_login_failed"),
        logout=lambda: None,
    )
    monkeypatch.setitem(sys.modules, "baostock", module)


def _install_fake_akshare(monkeypatch, *, rows: tuple[tuple[str, str], ...]) -> None:
    class _Frame:
        def to_dict(self, orient: str) -> list[dict[str, str]]:
            assert orient == "records"
            return [{"code": code, "name": name} for code, name in rows]

    module = SimpleNamespace(stock_info_a_code_name=lambda: _Frame())
    monkeypatch.setitem(sys.modules, "akshare", module)
