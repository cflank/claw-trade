from __future__ import annotations

import csv
import hashlib
from pathlib import Path
from typing import Any

from claw_trade.data_gateway.models import ProviderStatus
from claw_trade.data_gateway.selection_seed_importer import import_a_share_seed_to_mongo
from claw_trade.data_gateway.store import (
    MongoAttemptStore,
    MongoNormalizedStore,
    MongoRawPayloadStore,
)
from claw_trade.data_gateway.store.mongo import (
    OPENBB_NORMALIZED,
    OPENBB_PROVIDER_ATTEMPTS,
    OPENBB_RAW_PAYLOADS,
)
from pymongo.errors import DuplicateKeyError


class _MemoryCollection:
    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}

    def update_one(self, query: dict[str, Any], update: dict[str, Any], upsert: bool = False) -> None:
        document_id = query.get("_id")
        assert isinstance(document_id, str)
        existing = self.docs.get(document_id)
        if existing is None:
            if not upsert:
                return
            existing = {"_id": document_id}
            existing.update(update.get("$setOnInsert", {}))
            self.docs[document_id] = existing
        if "$set" in update:
            existing.update(update["$set"])

    def insert_one(self, doc: dict[str, Any]) -> None:
        document_id = doc["_id"]
        if document_id in self.docs:
            raise DuplicateKeyError("duplicate")
        self.docs[document_id] = dict(doc)

    def find_one(self, query: dict[str, Any]) -> dict[str, Any] | None:
        document_id = query.get("_id")
        if isinstance(document_id, str):
            return self.docs.get(document_id)
        return None


def test_a_share_seed_import_writes_raw_normalized_attempt_refs_and_readback(tmp_path: Path) -> None:
    baostock_root, private_root = _sample_seed(tmp_path)
    stores = _stores()

    result = import_a_share_seed_to_mongo(
        baostock_root=baostock_root,
        private_placement_root=private_root,
        trade_date="2026-05-26",
        import_run_id="t9a-sample-ok",
        raw_store=stores["raw_store"],
        normalized_store=stores["normalized_store"],
        attempt_store=stores["attempt_store"],
    )

    assert result.blocker_gaps == ()
    assert result.manifest_hash and result.manifest_hash.startswith("sha256:")
    assert result.raw_refs
    assert all(ref.startswith(f"mongo://{OPENBB_RAW_PAYLOADS}/") for ref in result.raw_refs)
    assert result.normalized_refs
    assert all(ref.startswith(f"normalized://mongo/{OPENBB_NORMALIZED}/") for ref in result.normalized_refs)
    assert result.attempt_refs
    assert all(ref.startswith(f"attempt://mongo/{OPENBB_PROVIDER_ATTEMPTS}/") for ref in result.attempt_refs)
    assert result.readback_counts["raw"] == len(result.raw_refs)
    assert result.readback_counts["normalized_rows"] >= 3
    assert result.warehouse_check_ref

    attempt_docs = stores["attempt_collection"].docs.values()
    assert attempt_docs
    assert {doc["provider"] for doc in attempt_docs} == {"local-baostock", "akshare-private-placement"}
    assert all(doc["status"] != ProviderStatus.REMOTE_SUCCESS.value for doc in attempt_docs)
    assert all(doc["raw_ref"] and doc["normalized_ref"] for doc in attempt_docs)
    assert all(doc["source_metadata"]["local_seed_import_status"] == "imported" for doc in attempt_docs)


def test_manifest_accepts_repo_relative_paths_that_include_seed_root(tmp_path: Path) -> None:
    baostock_root, private_root = _sample_seed(tmp_path)
    qfq = baostock_root / "daily" / "qfq" / "sh.600519.csv"
    manifest = baostock_root / "manifest" / "baostock_files.sha256"
    lines: list[str] = []
    for path in sorted(path for path in baostock_root.rglob("*.csv") if "manifest" not in path.parts):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        manifest_path = path.as_posix() if path == qfq else path.relative_to(baostock_root).as_posix()
        lines.append(f"{digest}  {manifest_path}")
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    stores = _stores()

    result = import_a_share_seed_to_mongo(
        baostock_root=baostock_root,
        private_placement_root=private_root,
        trade_date="2026-05-26",
        import_run_id="t9a-root-prefixed-manifest",
        raw_store=stores["raw_store"],
        normalized_store=stores["normalized_store"],
        attempt_store=stores["attempt_store"],
    )

    assert result.blocker_gaps == ()
    assert result.normalized_refs
    attempt_docs = stores["attempt_collection"].docs.values()
    assert any(doc["source_metadata"]["relative_path"] == "daily/qfq/sh.600519.csv" for doc in attempt_docs)


def test_missing_manifest_is_blocker_and_does_not_write_normalized_rows(tmp_path: Path) -> None:
    baostock_root = tmp_path / "baostock"
    private_root = tmp_path / "akshare"
    (baostock_root / "daily" / "qfq").mkdir(parents=True)
    private_root.mkdir(parents=True)
    stores = _stores()

    result = import_a_share_seed_to_mongo(
        baostock_root=baostock_root,
        private_placement_root=private_root,
        trade_date="2026-05-26",
        import_run_id="t9a-missing-manifest",
        raw_store=stores["raw_store"],
        normalized_store=stores["normalized_store"],
        attempt_store=stores["attempt_store"],
    )

    assert any(gap.gap_code == "seed_manifest_missing" for gap in result.blocker_gaps)
    assert result.normalized_refs == ()
    assert result.warehouse_check_ref is None
    assert stores["normalized_collection"].docs == {}
    assert stores["attempt_collection"].docs


def test_empty_seed_file_is_blocker_gap(tmp_path: Path) -> None:
    baostock_root, private_root = _sample_seed(tmp_path)
    empty_file = baostock_root / "daily" / "qfq" / "sh.600519.csv"
    empty_file.write_text("", encoding="utf-8")
    _write_manifest(baostock_root, tuple(path for path in baostock_root.rglob("*.csv") if "manifest" not in path.parts))
    stores = _stores()

    result = import_a_share_seed_to_mongo(
        baostock_root=baostock_root,
        private_placement_root=private_root,
        trade_date="2026-05-26",
        import_run_id="t9a-empty-file",
        raw_store=stores["raw_store"],
        normalized_store=stores["normalized_store"],
        attempt_store=stores["attempt_store"],
    )

    assert any(gap.gap_code == "seed_file_empty" for gap in result.blocker_gaps)
    assert result.normalized_refs == ()
    assert result.warehouse_check_ref is None


def test_qfq_missing_required_fields_is_blocker_gap(tmp_path: Path) -> None:
    baostock_root, private_root = _sample_seed(tmp_path)
    qfq = baostock_root / "daily" / "qfq" / "sh.600519.csv"
    _write_csv(qfq, ("date", "code", "open", "high", "low", "volume", "adjustflag"), (("2026-05-26", "sh.600519", "1", "2", "1", "100", "2"),))
    _write_manifest(baostock_root, tuple(path for path in baostock_root.rglob("*.csv") if "manifest" not in path.parts))
    stores = _stores()

    result = import_a_share_seed_to_mongo(
        baostock_root=baostock_root,
        private_placement_root=private_root,
        trade_date="2026-05-26",
        import_run_id="t9a-missing-fields",
        raw_store=stores["raw_store"],
        normalized_store=stores["normalized_store"],
        attempt_store=stores["attempt_store"],
    )

    assert any(gap.gap_code == "seed_file_missing_fields" for gap in result.blocker_gaps)
    assert result.normalized_refs == ()


def test_qfq_duplicate_conflict_is_blocker_gap(tmp_path: Path) -> None:
    baostock_root, private_root = _sample_seed(tmp_path)
    qfq = baostock_root / "daily" / "qfq" / "sh.600519.csv"
    _write_csv(
        qfq,
        ("date", "code", "open", "high", "low", "close", "volume", "adjustflag"),
        (
            ("2026-05-26", "sh.600519", "1", "2", "1", "1.5", "100", "2"),
            ("2026-05-26", "sh.600519", "1", "2", "1", "1.6", "100", "2"),
        ),
    )
    _write_manifest(baostock_root, tuple(path for path in baostock_root.rglob("*.csv") if "manifest" not in path.parts))
    stores = _stores()

    result = import_a_share_seed_to_mongo(
        baostock_root=baostock_root,
        private_placement_root=private_root,
        trade_date="2026-05-26",
        import_run_id="t9a-duplicate",
        raw_store=stores["raw_store"],
        normalized_store=stores["normalized_store"],
        attempt_store=stores["attempt_store"],
    )

    assert any(gap.gap_code == "qfq_daily_duplicate_conflict" for gap in result.blocker_gaps)
    assert result.normalized_refs == ()


def test_private_placement_duplicate_conflict_is_blocker_gap(tmp_path: Path) -> None:
    baostock_root, private_root = _sample_seed(tmp_path, include_private=False)
    private_file = private_root / "600519.csv"
    _write_csv(
        private_file,
        ("股票代码", "公告日期", "发行方式", "融资金额"),
        (
            ("600519", "2026-05-20", "非公开发行", "100"),
            ("600519", "2026-05-20", "非公开发行", "200"),
        ),
    )
    _write_private_manifest(private_root, stock_count=1, success_count=1, failure_count=0)
    stores = _stores()

    result = import_a_share_seed_to_mongo(
        baostock_root=baostock_root,
        private_placement_root=private_root,
        trade_date="2026-05-26",
        import_run_id="t9a-private-duplicate",
        raw_store=stores["raw_store"],
        normalized_store=stores["normalized_store"],
        attempt_store=stores["attempt_store"],
    )

    assert any(gap.gap_code == "private_placement_duplicate_conflict" for gap in result.blocker_gaps)
    assert result.warehouse_check_ref is None


def test_index_universe_missing_daily_file_is_blocker_gap(tmp_path: Path) -> None:
    baostock_root, private_root = _sample_seed(tmp_path)
    (baostock_root / "index_daily" / "sh.000001.csv").unlink()
    stores = _stores()

    result = import_a_share_seed_to_mongo(
        baostock_root=baostock_root,
        private_placement_root=private_root,
        trade_date="2026-05-26",
        import_run_id="t9a-missing-index-coverage",
        raw_store=stores["raw_store"],
        normalized_store=stores["normalized_store"],
        attempt_store=stores["attempt_store"],
    )

    assert any(gap.gap_code == "index_daily_coverage_missing_files" for gap in result.blocker_gaps)
    assert result.warehouse_check_ref is None


def test_approved_optional_index_daily_files_are_not_required(tmp_path: Path) -> None:
    baostock_root, private_root = _sample_seed(tmp_path)
    index_universe = baostock_root / "universe" / "index_universe.csv"
    _write_csv(
        index_universe,
        ("code", "index_name"),
        (("sh.000001", "上证指数"), ("sh.000816", "批准不下载指数")),
    )
    _write_manifest(baostock_root, tuple(path for path in baostock_root.rglob("*.csv") if "manifest" not in path.parts))
    stores = _stores()

    result = import_a_share_seed_to_mongo(
        baostock_root=baostock_root,
        private_placement_root=private_root,
        trade_date="2026-05-26",
        import_run_id="t9a-optional-index-not-required",
        raw_store=stores["raw_store"],
        normalized_store=stores["normalized_store"],
        attempt_store=stores["attempt_store"],
    )

    assert not any(gap.gap_code == "index_daily_coverage_missing_files" for gap in result.blocker_gaps)
    assert result.blocker_gaps == ()
    assert result.warehouse_check_ref


def test_missing_universe_is_blocker_and_does_not_import_seed(tmp_path: Path) -> None:
    baostock_root, private_root = _sample_seed(tmp_path)
    (baostock_root / "universe" / "a_share_stock_universe.csv").unlink()
    stores = _stores()

    result = import_a_share_seed_to_mongo(
        baostock_root=baostock_root,
        private_placement_root=private_root,
        trade_date="2026-05-26",
        import_run_id="t9a-missing-stock-universe",
        raw_store=stores["raw_store"],
        normalized_store=stores["normalized_store"],
        attempt_store=stores["attempt_store"],
    )

    assert any(gap.gap_code == "stock_universe_coverage_missing_files" for gap in result.blocker_gaps)
    assert result.normalized_refs == ()
    assert result.warehouse_check_ref is None


def test_missing_industry_code_is_warn_not_blocker(tmp_path: Path) -> None:
    baostock_root, private_root = _sample_seed(tmp_path)
    industry = baostock_root / "basic" / "stock_industry.csv"
    _write_csv(
        industry,
        ("updateDate", "code", "code_name", "industry", "industryClassification"),
        (("2026-05-26", "sh.600000", "浦发银行", "银行业", "证监会行业分类"),),
    )
    _write_manifest(baostock_root, tuple(path for path in baostock_root.rglob("*.csv") if "manifest" not in path.parts))
    stores = _stores()

    result = import_a_share_seed_to_mongo(
        baostock_root=baostock_root,
        private_placement_root=private_root,
        trade_date="2026-05-26",
        import_run_id="t9a-missing-industry",
        raw_store=stores["raw_store"],
        normalized_store=stores["normalized_store"],
        attempt_store=stores["attempt_store"],
    )

    assert not any(gap.gap_code == "industry_coverage_missing_codes" for gap in result.blocker_gaps)
    assert any(gap.gap_code == "industry_coverage_missing_codes" and gap.severity == "warn" for gap in result.data_gaps)
    assert result.warehouse_check_ref


def test_qfq_start_date_gap_is_not_blocker(tmp_path: Path) -> None:
    baostock_root, private_root = _sample_seed(tmp_path)
    basic = baostock_root / "basic" / "stock_basic.csv"
    _write_csv(basic, ("code", "code_name", "ipoDate"), (("sh.600519", "贵州茅台", "2001-08-27"),))
    _write_manifest(baostock_root, tuple(path for path in baostock_root.rglob("*.csv") if "manifest" not in path.parts))
    stores = _stores()

    result = import_a_share_seed_to_mongo(
        baostock_root=baostock_root,
        private_placement_root=private_root,
        trade_date="2026-05-26",
        import_run_id="t9a-start-date-gap",
        raw_store=stores["raw_store"],
        normalized_store=stores["normalized_store"],
        attempt_store=stores["attempt_store"],
    )

    assert not any(gap.gap_code == "qfq_daily_coverage_start_incomplete" for gap in result.blocker_gaps)
    assert result.blocker_gaps == ()
    assert result.warehouse_check_ref


def test_qfq_last_date_before_trade_date_is_not_blocker(tmp_path: Path) -> None:
    baostock_root, private_root = _sample_seed(tmp_path)
    qfq = baostock_root / "daily" / "qfq" / "sh.600519.csv"
    _write_csv(
        qfq,
        ("date", "code", "open", "high", "low", "close", "volume", "amount", "adjustflag", "pctChg", "turn", "tradestatus", "isST"),
        (("2026-04-27", "sh.600519", "1600", "1610", "1590", "1605", "1000", "1605000", "2", "1.1", "0.5", "1", "0"),),
    )
    _write_manifest(baostock_root, tuple(path for path in baostock_root.rglob("*.csv") if "manifest" not in path.parts))
    stores = _stores()

    result = import_a_share_seed_to_mongo(
        baostock_root=baostock_root,
        private_placement_root=private_root,
        trade_date="2026-05-26",
        import_run_id="t9a-last-date-gap",
        raw_store=stores["raw_store"],
        normalized_store=stores["normalized_store"],
        attempt_store=stores["attempt_store"],
    )

    assert not any(gap.gap_code == "qfq_daily_coverage_stale_files" for gap in result.blocker_gaps)
    assert result.blocker_gaps == ()
    assert result.warehouse_check_ref


def test_qfq_adjustflag_mismatch_is_blocker(tmp_path: Path) -> None:
    baostock_root, private_root = _sample_seed(tmp_path)
    qfq = baostock_root / "daily" / "qfq" / "sh.600519.csv"
    _write_csv(
        qfq,
        ("date", "code", "open", "high", "low", "close", "volume", "amount", "adjustflag", "pctChg", "turn", "tradestatus", "isST"),
        (
            ("2026-05-25", "sh.600519", "1600", "1610", "1590", "1605", "1000", "1605000", "3", "1.1", "0.5", "1", "0"),
            ("2026-05-26", "sh.600519", "1606", "1620", "1600", "1612", "2000", "3224000", "3", "0.4", "0.6", "1", "0"),
        ),
    )
    _write_manifest(baostock_root, tuple(path for path in baostock_root.rglob("*.csv") if "manifest" not in path.parts))
    stores = _stores()

    result = import_a_share_seed_to_mongo(
        baostock_root=baostock_root,
        private_placement_root=private_root,
        trade_date="2026-05-26",
        import_run_id="t9a-adjustflag-mismatch",
        raw_store=stores["raw_store"],
        normalized_store=stores["normalized_store"],
        attempt_store=stores["attempt_store"],
    )

    assert any(gap.gap_code == "qfq_daily_adjustflag_mismatch" for gap in result.blocker_gaps)
    assert result.warehouse_check_ref is None


def test_private_placement_missing_stock_file_is_blocker(tmp_path: Path) -> None:
    baostock_root, private_root = _sample_seed(tmp_path)
    (private_root / "600519.csv").unlink()
    stores = _stores()

    result = import_a_share_seed_to_mongo(
        baostock_root=baostock_root,
        private_placement_root=private_root,
        trade_date="2026-05-26",
        import_run_id="t9a-private-coverage-missing",
        raw_store=stores["raw_store"],
        normalized_store=stores["normalized_store"],
        attempt_store=stores["attempt_store"],
    )

    assert any(gap.gap_code == "private_placement_coverage_missing_files" for gap in result.blocker_gaps)
    assert result.warehouse_check_ref is None


def test_cli_output_does_not_claim_real_full_seed_import() -> None:
    source = Path("scripts/selection/import_a_share_seed_to_mongo.py").read_text(encoding="utf-8")

    assert "real_full_seed_import" not in source
    assert '"execution_scope": "operator_supplied_paths"' in source
    assert '"full_seed_completion_claimed": False' in source


def _stores() -> dict[str, Any]:
    raw_collection = _MemoryCollection()
    normalized_collection = _MemoryCollection()
    attempt_collection = _MemoryCollection()
    return {
        "raw_collection": raw_collection,
        "normalized_collection": normalized_collection,
        "attempt_collection": attempt_collection,
        "raw_store": MongoRawPayloadStore(raw_collection),
        "normalized_store": MongoNormalizedStore(normalized_collection),
        "attempt_store": MongoAttemptStore(attempt_collection),
    }


def _sample_seed(
    tmp_path: Path,
    *,
    include_private: bool = True,
    include_coverage: bool = True,
) -> tuple[Path, Path]:
    baostock_root = tmp_path / "baostock"
    private_root = tmp_path / "akshare"
    qfq = baostock_root / "daily" / "qfq" / "sh.600519.csv"
    basic = baostock_root / "basic" / "stock_basic.csv"
    manifest_paths = [qfq, basic]
    _write_csv(
        qfq,
        ("date", "code", "open", "high", "low", "close", "volume", "amount", "adjustflag", "pctChg", "turn", "tradestatus", "isST"),
        (
            ("2026-05-25", "sh.600519", "1600", "1610", "1590", "1605", "1000", "1605000", "2", "1.1", "0.5", "1", "0"),
            ("2026-05-26", "sh.600519", "1606", "1620", "1600", "1612", "2000", "3224000", "2", "0.4", "0.6", "1", "0"),
        ),
    )
    _write_csv(basic, ("code", "code_name", "ipoDate"), (("sh.600519", "贵州茅台", "2026-05-12"),))
    if include_coverage:
        stock_universe = baostock_root / "universe" / "a_share_stock_universe.csv"
        index_universe = baostock_root / "universe" / "index_universe.csv"
        factor = baostock_root / "adjust_factor" / "sh.600519_factor.csv"
        index_daily = baostock_root / "index_daily" / "sh.000001.csv"
        industry = baostock_root / "basic" / "stock_industry.csv"
        _write_csv(stock_universe, ("code", "code_name"), (("sh.600519", "贵州茅台"),))
        _write_csv(index_universe, ("code", "index_name"), (("sh.000001", "上证指数"),))
        _write_csv(
            industry,
            ("updateDate", "code", "code_name", "industry", "industryClassification"),
            (("2026-05-26", "sh.600519", "贵州茅台", "酒、饮料和精制茶制造业", "证监会行业分类"),),
        )
        _write_csv(
            factor,
            ("dividOperateDate", "code", "foreAdjustFactor"),
            (("2026-05-26", "sh.600519", "1.0"),),
        )
        _write_csv(
            index_daily,
            ("date", "code", "open", "high", "low", "close", "volume"),
            (
                ("2015-01-01", "sh.000001", "3000", "3010", "2990", "3005", "100000"),
                ("2026-05-26", "sh.000001", "3000", "3010", "2990", "3005", "100000"),
            ),
        )
        manifest_paths.extend((stock_universe, index_universe, factor, index_daily, industry))
    _write_manifest(baostock_root, tuple(manifest_paths))
    private_root.mkdir(parents=True)
    if include_private:
        _write_csv(
            private_root / "600519.csv",
            ("股票代码", "公告日期", "发行方式", "融资金额"),
            (("600519", "2026-05-20", "非公开发行", "100"),),
        )
    _write_private_manifest(
        private_root,
        stock_count=1,
        success_count=1 if include_private else 0,
        failure_count=0 if include_private else 1,
    )
    return baostock_root, private_root


def _write_manifest(root: Path, paths: tuple[Path, ...]) -> None:
    manifest = root / "manifest" / "baostock_files.sha256"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for path in paths:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {path.relative_to(root).as_posix()}")
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_private_manifest(root: Path, *, stock_count: int, success_count: int, failure_count: int) -> None:
    manifest = root / "manifest" / "akshare-stock-add-stock-test.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        (
            "{\n"
            '  "status": "completed",\n'
            f'  "stock_count": {stock_count},\n'
            f'  "success_count": {success_count},\n'
            f'  "failure_count": {failure_count},\n'
            '  "failures": [],\n'
            '  "source": "akshare.stock_add_stock:sina"\n'
            "}\n"
        ),
        encoding="utf-8",
    )


def _write_csv(path: Path, fieldnames: tuple[str, ...], rows: tuple[tuple[str, ...], ...]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(fieldnames)
        writer.writerows(rows)
