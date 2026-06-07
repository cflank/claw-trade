from __future__ import annotations

import csv
import json
import os
import socket
from dataclasses import dataclass
from datetime import UTC, date, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from uuid import uuid4
from zipfile import BadZipFile, ZipFile

from claw_trade.data_gateway.maintenance.normalized_rows import discard_normalized_mongo_rows
from claw_trade.data_gateway.warehouse.repository import DatasetRepository

_CN_A_EXCHANGE_BY_SUFFIX = {
    "SH": "SSE",
    "SZ": "SZSE",
    "BJ": "BSE",
}

_DEFAULT_HISTORY_DAYS = 390


@dataclass(frozen=True)
class _IdentityFacts:
    company_name: str
    industry: str | None = None
    list_date: str | None = None
    source_ref: str | None = None


@dataclass(frozen=True)
class _IdentityLoadSummary:
    attempted_sources: tuple[str, ...]
    loaded_sources: tuple[str, ...]
    loaded_count: int
    errors: tuple[str, ...]


@dataclass(frozen=True)
class ASharePackageAudit:
    daily_root: Path
    factor_root: Path
    daily_file_count: int
    factor_zip_count: int
    factor_direct_zip_count: int
    factor_split_parts: tuple[str, ...]
    daily_symbol_count: int
    factor_symbol_count: int
    intersection_symbol_count: int
    daily_only_symbol_count: int
    factor_only_symbol_count: int
    missing_local_roots: tuple[str, ...]
    warnings: tuple[str, ...]
    daily_root_fingerprint: str
    factor_root_fingerprint: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "daily_root": str(self.daily_root),
            "factor_root": str(self.factor_root),
            "daily_file_count": self.daily_file_count,
            "factor_zip_count": self.factor_zip_count,
            "factor_direct_zip_count": self.factor_direct_zip_count,
            "factor_split_parts": list(self.factor_split_parts),
            "daily_symbol_count": self.daily_symbol_count,
            "factor_symbol_count": self.factor_symbol_count,
            "intersection_symbol_count": self.intersection_symbol_count,
            "daily_only_symbol_count": self.daily_only_symbol_count,
            "factor_only_symbol_count": self.factor_only_symbol_count,
            "missing_local_roots": list(self.missing_local_roots),
            "warnings": list(self.warnings),
            "daily_root_fingerprint": self.daily_root_fingerprint,
            "factor_root_fingerprint": self.factor_root_fingerprint,
        }


@dataclass(frozen=True)
class ASharePrepackagedImportResult:
    import_run_id: str
    trade_date: str
    audit: ASharePackageAudit
    deleted_existing_count: int
    dataset_refs: tuple[str, ...]
    raw_refs: tuple[str, ...]
    attempt_refs: tuple[str, ...]
    manifest_ref: str
    imported_symbol_count: int
    skipped_symbol_count: int
    identity_sources_attempted: tuple[str, ...]
    identity_sources_loaded: tuple[str, ...]
    identity_loaded_count: int
    identity_errors: tuple[str, ...]
    warning_counts: Mapping[str, int]
    sample_symbols: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "import_run_id": self.import_run_id,
            "trade_date": self.trade_date,
            "audit": self.audit.as_dict(),
            "deleted_existing_count": self.deleted_existing_count,
            "dataset_refs": list(self.dataset_refs),
            "raw_refs": list(self.raw_refs),
            "attempt_refs": list(self.attempt_refs),
            "manifest_ref": self.manifest_ref,
            "imported_symbol_count": self.imported_symbol_count,
            "skipped_symbol_count": self.skipped_symbol_count,
            "identity_sources_attempted": list(self.identity_sources_attempted),
            "identity_sources_loaded": list(self.identity_sources_loaded),
            "identity_loaded_count": self.identity_loaded_count,
            "identity_errors": list(self.identity_errors),
            "warning_counts": dict(self.warning_counts),
            "sample_symbols": list(self.sample_symbols),
        }


def audit_a_share_prepackaged_packages(
    *,
    daily_root: Path,
    factor_root: Path,
    local_roots: Iterable[Path] = (),
) -> ASharePackageAudit:
    daily_files = _daily_files(daily_root)
    factor_zips = tuple(sorted(factor_root.glob("*.zip")))
    direct_factor_zips = tuple(path for path in factor_zips if _zip_can_list(path))
    split_parts = tuple(path.name for path in sorted(factor_root.glob("*.zip.*")))
    daily_symbols = {_ticker_from_path(path) for path in daily_files}
    daily_symbols.discard(None)
    factor_symbols = _factor_symbols(direct_factor_zips)
    missing_local_roots = tuple(str(path) for path in local_roots if not path.exists())
    warnings: list[str] = []
    if split_parts:
        warnings.append("factor_split_zip_parts_present_not_imported")
    if not direct_factor_zips:
        warnings.append("factor_direct_zip_missing")
    if missing_local_roots:
        warnings.append("local_package_roots_missing")
    return ASharePackageAudit(
        daily_root=daily_root,
        factor_root=factor_root,
        daily_file_count=len(daily_files),
        factor_zip_count=len(factor_zips),
        factor_direct_zip_count=len(direct_factor_zips),
        factor_split_parts=split_parts,
        daily_symbol_count=len(daily_symbols),
        factor_symbol_count=len(factor_symbols),
        intersection_symbol_count=len(daily_symbols & factor_symbols),
        daily_only_symbol_count=len(daily_symbols - factor_symbols),
        factor_only_symbol_count=len(factor_symbols - daily_symbols),
        missing_local_roots=missing_local_roots,
        warnings=tuple(warnings),
        daily_root_fingerprint=_root_fingerprint(daily_files),
        factor_root_fingerprint=_root_fingerprint(tuple(sorted(factor_root.glob("*")))),
    )


def import_a_share_prepackaged_to_repository(
    *,
    daily_root: Path,
    factor_root: Path,
    repository: DatasetRepository,
    trade_date: str,
    import_run_id: str,
    local_roots: Iterable[Path] = (),
    identity_source_order: Iterable[str] = (),
    identity_fetch_timeout_seconds: float = 12.0,
    tushare_token: str | None = None,
    tushare_endpoint_url: str | None = None,
    history_days: int = _DEFAULT_HISTORY_DAYS,
    max_symbols: int | None = None,
    replace_existing: bool = False,
) -> ASharePrepackagedImportResult:
    audit = audit_a_share_prepackaged_packages(
        daily_root=daily_root,
        factor_root=factor_root,
        local_roots=tuple(local_roots),
    )
    deleted_existing_count = _delete_existing_prepackaged_records(repository) if replace_existing else 0
    factor_zips = tuple(path for path in sorted(factor_root.glob("*.zip")) if _zip_can_list(path))
    factor_entries = _factor_entry_map(factor_zips)
    daily_by_symbol = {_ticker_from_path(path): path for path in _daily_files(daily_root)}
    symbols = sorted(symbol for symbol in daily_by_symbol if symbol)
    if max_symbols is not None:
        symbols = symbols[: max(0, max_symbols)]
    identity_facts, identity_summary = _identity_facts_by_symbol(
        tuple(local_roots),
        symbols=tuple(symbols),
        trade_date=trade_date,
        source_order=tuple(identity_source_order),
        fetch_timeout_seconds=identity_fetch_timeout_seconds,
        tushare_token=tushare_token,
        tushare_endpoint_url=tushare_endpoint_url,
    )
    private_placement_facts = _private_placement_facts_by_code(tuple(local_roots), trade_date=trade_date)
    raw_ref = _write_raw_package_record(
        repository=repository,
        import_run_id=import_run_id,
        audit=audit,
        history_days=history_days,
    )

    refs: list[str] = []
    imported_symbols: list[str] = []
    warning_counts: dict[str, int] = {
        "company_name_missing": 0,
        "private_placement_missing": 0,
        "factor_missing": 0,
        "daily_missing": 0,
        "history_empty": 0,
    }
    as_of = datetime.now(tz=UTC)
    archives: dict[Path, ZipFile] = {}
    try:
        for symbol in symbols:
            daily_path = daily_by_symbol.get(symbol)
            if daily_path is None:
                warning_counts["daily_missing"] += 1
                continue
            daily_history = _read_daily_history(daily_path)
            if not daily_history:
                warning_counts["history_empty"] += 1
                continue
            factor_history, factor_latest, factor_source = _read_factor_history(
                symbol=symbol,
                entries=factor_entries,
                archives=archives,
            )
            if not factor_history:
                warning_counts["factor_missing"] += 1
            history = _merge_history(daily_history, factor_history, history_days=history_days)
            if not history:
                warning_counts["history_empty"] += 1
                continue
            latest = history[-1]
            row = _selection_row(
                symbol=symbol,
                history=history,
                latest=latest,
                factor_latest=factor_latest,
                daily_path=daily_path,
                factor_source=factor_source,
                identity=identity_facts.get(symbol),
                private_placement=private_placement_facts.get(symbol.split(".", 1)[0]),
            )
            if "company_name" not in row:
                warning_counts["company_name_missing"] += 1
            if symbol.split(".", 1)[0] not in private_placement_facts:
                warning_counts["private_placement_missing"] += 1
            record = _normalized_selection_record(
                symbol=symbol,
                row=row,
                trade_date=trade_date,
                as_of=as_of,
                raw_ref=raw_ref,
            )
            refs.append(repository.upsert_normalized_document(record))
            imported_symbols.append(symbol)
    finally:
        for archive in archives.values():
            archive.close()

    attempt_ref = _write_attempt_record(
        repository=repository,
        import_run_id=import_run_id,
        dataset_refs=tuple(refs),
        raw_ref=raw_ref,
        warning_counts=warning_counts,
    )
    manifest_ref = _write_manifest(
        repository=repository,
        import_run_id=import_run_id,
        trade_date=trade_date,
        audit=audit,
        dataset_refs=tuple(refs),
        raw_refs=(raw_ref,),
        attempt_refs=(attempt_ref,),
        replace_existing=replace_existing,
        deleted_existing_count=deleted_existing_count,
        warning_counts=warning_counts,
    )
    return ASharePrepackagedImportResult(
        import_run_id=import_run_id,
        trade_date=trade_date,
        audit=audit,
        deleted_existing_count=deleted_existing_count,
        dataset_refs=tuple(refs),
        raw_refs=(raw_ref,),
        attempt_refs=(attempt_ref,),
        manifest_ref=manifest_ref,
        imported_symbol_count=len(imported_symbols),
        skipped_symbol_count=len(symbols) - len(imported_symbols),
        identity_sources_attempted=identity_summary.attempted_sources,
        identity_sources_loaded=identity_summary.loaded_sources,
        identity_loaded_count=identity_summary.loaded_count,
        identity_errors=identity_summary.errors,
        warning_counts=warning_counts,
        sample_symbols=tuple(imported_symbols[:20]),
    )


def _delete_existing_prepackaged_records(repository: DatasetRepository) -> int:
    result = discard_normalized_mongo_rows(
        repository,
        {
            "dataset": "daily_bar",
            "market": "CN_A",
            "universe_ref": "all_a_shares",
            "provider_lineage.provider_id": "local_a_share_prepackaged",
            "provider_lineage.endpoint_id": "a_share_prepackaged_selection_import",
        },
        confirmed=True,
    )
    return result.deleted_count


def _daily_files(root: Path) -> tuple[Path, ...]:
    if not root.exists():
        return ()
    return tuple(sorted(root.glob("*.csv")))


def _zip_can_list(path: Path) -> bool:
    try:
        with ZipFile(path) as archive:
            archive.infolist()
    except (BadZipFile, OSError):
        return False
    return True


def _factor_symbols(paths: Iterable[Path]) -> set[str]:
    symbols: set[str] = set()
    for path in paths:
        with ZipFile(path) as archive:
            for name in archive.namelist():
                ticker = _ticker_from_name(Path(name).name)
                if ticker is not None:
                    symbols.add(ticker)
    return symbols


def _factor_entry_map(paths: Iterable[Path]) -> dict[str, tuple[Path, str]]:
    entries: dict[str, tuple[Path, str]] = {}
    for path in paths:
        with ZipFile(path) as archive:
            for name in archive.namelist():
                ticker = _ticker_from_name(Path(name).name)
                if ticker is not None:
                    entries[ticker] = (path, name)
    return entries


def _read_daily_history(path: Path) -> tuple[dict[str, float | str], ...]:
    rows: list[dict[str, float | str]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for raw in reader:
            row = _history_row_from_daily(raw)
            if row is not None:
                rows.append(row)
    return tuple(sorted(rows, key=lambda item: str(item["date"])))


def _identity_facts_by_symbol(
    local_roots: tuple[Path, ...],
    *,
    symbols: tuple[str, ...] = (),
    trade_date: str | None = None,
    source_order: tuple[str, ...] = (),
    fetch_timeout_seconds: float = 12.0,
    tushare_token: str | None = None,
    tushare_endpoint_url: str | None = None,
) -> tuple[dict[str, _IdentityFacts], _IdentityLoadSummary]:
    basic_rows: dict[str, Mapping[str, str]] = {}
    industry_by_symbol: dict[str, str] = {}
    for root in local_roots:
        basic_path = _first_existing(root / "basic" / "stock_basic.csv", root / "stock_basic.csv")
        if basic_path is not None:
            for row in _read_csv_rows(basic_path):
                symbol = _ticker_from_baostock_code(row.get("code"))
                if symbol is not None:
                    basic_rows[symbol] = row
        industry_path = _first_existing(root / "basic" / "stock_industry.csv", root / "stock_industry.csv")
        if industry_path is not None:
            for row in _read_csv_rows(industry_path):
                symbol = _ticker_from_baostock_code(row.get("code"))
                industry = _first_text(row, ("industry", "industryClassification", "industry_classification", "行业"))
                if symbol is not None and industry:
                    industry_by_symbol[symbol] = industry

    facts: dict[str, _IdentityFacts] = {}
    for symbol, row in basic_rows.items():
        company_name = _first_text(row, ("code_name", "codeName", "name", "stock_name", "股票名称"))
        if not company_name:
            continue
        facts[symbol] = _IdentityFacts(
            company_name=company_name,
            industry=industry_by_symbol.get(symbol),
            list_date=_first_text(row, ("ipoDate", "ipo_date", "list_date", "listDate", "上市日期")),
            source_ref="file://local_identity",
        )

    target_symbols = set(symbols)
    attempted: list[str] = []
    loaded: list[str] = ["local_roots"] if facts else []
    errors: list[str] = []
    normalized_order = tuple(_normalize_identity_source(value) for value in source_order)
    for source in tuple(item for item in normalized_order if item):
        if target_symbols and target_symbols.issubset(facts):
            break
        attempted.append(source)
        try:
            if source == "baostock":
                provider_facts = _fetch_baostock_identity_facts(
                    trade_date=trade_date,
                    timeout_seconds=fetch_timeout_seconds,
                )
            elif source == "akshare":
                provider_facts = _fetch_akshare_identity_facts()
            elif source == "tushare":
                provider_facts = _fetch_tushare_identity_facts(
                    token=tushare_token,
                    endpoint_url=tushare_endpoint_url,
                )
            else:
                errors.append(f"{source}:unsupported_identity_source")
                continue
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{source}:{type(exc).__name__}:{exc}")
            continue
        merged = 0
        for symbol, fact in provider_facts.items():
            if target_symbols and symbol not in target_symbols:
                continue
            if symbol not in facts:
                facts[symbol] = fact
                merged += 1
        if merged:
            loaded.append(source)
    return facts, _IdentityLoadSummary(
        attempted_sources=tuple(attempted),
        loaded_sources=tuple(loaded),
        loaded_count=len(facts),
        errors=tuple(errors),
    )


def _normalize_identity_source(value: str) -> str:
    text = str(value or "").strip().lower().replace("_", "-")
    aliases = {
        "bao-stock": "baostock",
        "bao": "baostock",
        "ak": "akshare",
        "tu-share": "tushare",
    }
    return aliases.get(text, text)


def _fetch_baostock_identity_facts(
    *,
    trade_date: str | None,
    timeout_seconds: float,
) -> dict[str, _IdentityFacts]:
    previous_timeout = socket.getdefaulttimeout()
    socket.setdefaulttimeout(timeout_seconds)
    import baostock as bs  # type: ignore

    login = bs.login()
    error_code = str(getattr(login, "error_code", "0"))
    if error_code not in {"0", ""}:
        socket.setdefaulttimeout(previous_timeout)
        raise RuntimeError(str(getattr(login, "error_msg", "baostock_login_failed")))
    try:
        result = bs.query_all_stock(day=trade_date) if trade_date else bs.query_all_stock()
        error_code = str(getattr(result, "error_code", "0"))
        if error_code not in {"0", ""}:
            raise RuntimeError(str(getattr(result, "error_msg", "baostock_query_all_stock_failed")))
        facts: dict[str, _IdentityFacts] = {}
        fields = tuple(str(item) for item in getattr(result, "fields", ()))
        while result.next():
            values = tuple(str(item) for item in result.get_row_data())
            row = dict(zip(fields, values, strict=False))
            symbol = _ticker_from_baostock_code(row.get("code"))
            company_name = _first_text(row, ("code_name", "codeName", "name", "stock_name"))
            if symbol is not None and company_name:
                facts[symbol] = _IdentityFacts(
                    company_name=company_name,
                    list_date=_first_text(row, ("ipoDate", "ipo_date", "list_date", "listDate", "上市日期")),
                    source_ref=f"baostock://query_all_stock/{trade_date or 'latest'}",
                )
        return facts
    finally:
        try:
            bs.logout()
        finally:
            socket.setdefaulttimeout(previous_timeout)


def _fetch_akshare_identity_facts() -> dict[str, _IdentityFacts]:
    import akshare as ak  # type: ignore

    dataframe = ak.stock_info_a_code_name()
    rows = _dataframe_records(dataframe)
    facts = _identity_facts_from_code_name_rows(
        rows,
        code_keys=("code", "证券代码", "代码"),
        name_keys=("name", "证券简称", "名称"),
        source_ref="akshare://stock_info_a_code_name",
    )
    if facts:
        return facts
    dataframe = ak.stock_zh_a_spot_em()
    return _identity_facts_from_code_name_rows(
        _dataframe_records(dataframe),
        code_keys=("代码", "code", "证券代码"),
        name_keys=("名称", "name", "证券简称"),
        source_ref="akshare://stock_zh_a_spot_em",
    )


def _fetch_tushare_identity_facts(
    *,
    token: str | None,
    endpoint_url: str | None,
) -> dict[str, _IdentityFacts]:
    resolved_token = (token or os.environ.get("TUSHARE_TOKEN") or os.environ.get("CN_A_TUSHARE_TOKEN") or "").strip()
    if not resolved_token:
        raise RuntimeError("tushare_token_missing")
    import tushare as ts  # type: ignore

    api = ts.pro_api(resolved_token)
    if endpoint_url:
        setattr(api, "_DataApi__http_url", endpoint_url)
    dataframe = api.stock_basic(
        exchange="",
        list_status="L",
        fields="ts_code,symbol,name,area,industry,list_date",
    )
    facts: dict[str, _IdentityFacts] = {}
    for row in _dataframe_records(dataframe):
        symbol = _ticker_from_tushare_code(row.get("ts_code"))
        company_name = _first_text(row, ("name", "名称", "stock_name"))
        if symbol is None or not company_name:
            continue
        facts[symbol] = _IdentityFacts(
            company_name=company_name,
            industry=_first_text(row, ("industry", "行业")),
            list_date=_first_text(row, ("list_date", "上市日期")),
            source_ref="tushare://stock_basic?list_status=L",
        )
    return facts


def _dataframe_records(value: Any) -> list[dict[str, str]]:
    if hasattr(value, "to_dict"):
        return [
            {str(key): "" if item is None else str(item) for key, item in row.items()}
            for row in value.to_dict(orient="records")
        ]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        result: list[dict[str, str]] = []
        for row in value:
            if isinstance(row, Mapping):
                result.append({str(key): "" if item is None else str(item) for key, item in row.items()})
        return result
    return []


def _identity_facts_from_code_name_rows(
    rows: Iterable[Mapping[str, str]],
    *,
    code_keys: tuple[str, ...],
    name_keys: tuple[str, ...],
    source_ref: str,
) -> dict[str, _IdentityFacts]:
    facts: dict[str, _IdentityFacts] = {}
    for row in rows:
        symbol = _symbol_from_six_digit_code(_first_text(row, code_keys))
        company_name = _first_text(row, name_keys)
        if symbol is None or not company_name:
            continue
        facts[symbol] = _IdentityFacts(company_name=company_name, source_ref=source_ref)
    return facts


def _private_placement_facts_by_code(
    local_roots: tuple[Path, ...],
    *,
    trade_date: str,
) -> dict[str, dict[str, object]]:
    facts: dict[str, dict[str, object]] = {}
    trade_day = date.fromisoformat(trade_date)
    for root in local_roots:
        if not root.exists() or not root.is_dir():
            continue
        for path in sorted(root.glob("*.csv")):
            code = path.stem.strip()
            if len(code) != 6 or not code.isdigit():
                continue
            coverage = _read_private_placement_file(path=path, trade_day=trade_day)
            if coverage is not None:
                facts[code] = coverage
    return facts


def _read_private_placement_file(*, path: Path, trade_day: date) -> dict[str, object] | None:
    rows = _read_csv_rows(path)
    source_ref = f"file://{path}"
    if not rows:
        return {
            "private_placement_event_date": "none",
            "private_placement_days_since": 9999.0,
            "private_placement_source_ref": source_ref,
        }
    latest: date | None = None
    for row in rows:
        event_date = _parse_event_date(
            _first_text(row, ("公告日期", "发行公告日", "发行日期", "上市公告日", "上市日期", "event_date", "date"))
        )
        if event_date is None or event_date > trade_day:
            continue
        if latest is None or event_date > latest:
            latest = event_date
    if latest is None:
        return None
    return {
        "private_placement_event_date": latest.isoformat(),
        "private_placement_days_since": float((trade_day - latest).days),
        "private_placement_source_ref": source_ref,
    }


def _read_factor_history(
    *,
    symbol: str,
    entries: Mapping[str, tuple[Path, str]],
    archives: dict[Path, ZipFile] | None = None,
) -> tuple[tuple[dict[str, float | str], ...], Mapping[str, str] | None, str | None]:
    entry = entries.get(symbol)
    if entry is None:
        return (), None, None
    path, name = entry
    rows: list[dict[str, float | str]] = []
    latest_raw: Mapping[str, str] | None = None
    if archives is None:
        with ZipFile(path) as archive:
            rows, latest_raw = _read_factor_archive_entry(archive, name)
    else:
        archive = archives.get(path)
        if archive is None:
            archive = ZipFile(path)
            archives[path] = archive
        rows, latest_raw = _read_factor_archive_entry(archive, name)
    rows = sorted(rows, key=lambda item: str(item["date"]))
    return tuple(rows), latest_raw, f"zip://{path}/{name}"


def _read_factor_archive_entry(archive: ZipFile, name: str) -> tuple[list[dict[str, float | str]], Mapping[str, str] | None]:
    rows: list[dict[str, float | str]] = []
    latest_raw: Mapping[str, str] | None = None
    with archive.open(name) as raw_handle:
        text_handle = (line.decode("utf-8-sig") for line in raw_handle)
        reader = csv.DictReader(text_handle)
        for raw in reader:
            row = _history_row_from_factor(raw)
            if row is None:
                continue
            rows.append(row)
            if latest_raw is None or str(row["date"]) > _factor_date_text(latest_raw.get("trade_date")):
                latest_raw = dict(raw)
    return rows, latest_raw


def _history_row_from_daily(raw: Mapping[str, str]) -> dict[str, float | str] | None:
    row_date = _date_text(raw.get("trade_date") or raw.get("date"))
    if row_date is None:
        return None
    open_value = _float(raw.get("open"))
    high_value = _float(raw.get("high"))
    low_value = _float(raw.get("low"))
    close_value = _float(raw.get("close"))
    volume_value = _float(raw.get("vol"))
    if None in {open_value, high_value, low_value, close_value, volume_value}:
        return None
    row: dict[str, float | str] = {
        "date": row_date,
        "open": open_value,
        "high": high_value,
        "low": low_value,
        "close": close_value,
        "volume": volume_value,
    }
    amount = _amount_yuan(raw.get("amount"))
    if amount is not None:
        row["amount"] = amount
    pct = _float(raw.get("pct_chg"))
    if pct is not None:
        row["p_change_pct"] = pct
    return row


def _history_row_from_factor(raw: Mapping[str, str]) -> dict[str, float | str] | None:
    row_date = _factor_date_text(raw.get("trade_date"))
    if row_date is None:
        return None
    open_value = _float(raw.get("open_qfq") or raw.get("open"))
    high_value = _float(raw.get("high_qfq") or raw.get("high"))
    low_value = _float(raw.get("low_qfq") or raw.get("low"))
    close_value = _float(raw.get("close_qfq") or raw.get("close"))
    volume_value = _float(raw.get("vol"))
    if None in {open_value, high_value, low_value, close_value, volume_value}:
        return None
    row: dict[str, float | str] = {
        "date": row_date,
        "open": open_value,
        "high": high_value,
        "low": low_value,
        "close": close_value,
        "volume": volume_value,
    }
    amount = _amount_yuan(raw.get("amount"))
    if amount is not None:
        row["amount"] = amount
    pct = _float(raw.get("pct_chg"))
    if pct is not None:
        row["p_change_pct"] = pct
    return row


def _merge_history(
    daily_history: tuple[dict[str, float | str], ...],
    factor_history: tuple[dict[str, float | str], ...],
    *,
    history_days: int,
) -> tuple[dict[str, float | str], ...]:
    by_date: dict[str, dict[str, float | str]] = {str(row["date"]): dict(row) for row in daily_history}
    for row in factor_history:
        by_date[str(row["date"])] = dict(row)
    return tuple(sorted(by_date.values(), key=lambda item: str(item["date"]))[-history_days:])


def _selection_row(
    *,
    symbol: str,
    history: tuple[dict[str, float | str], ...],
    latest: Mapping[str, float | str],
    factor_latest: Mapping[str, str] | None,
    daily_path: Path,
    factor_source: str | None,
    identity: _IdentityFacts | None,
    private_placement: Mapping[str, object] | None,
) -> dict[str, Any]:
    warnings: list[str] = []
    if identity is None:
        warnings.append("company_name_missing")
    if private_placement is None:
        warnings.append("private_placement_missing")
    row: dict[str, Any] = {
        "ticker": symbol,
        "symbol_id": symbol,
        "date": latest.get("date"),
        "trade_date": latest.get("date"),
        "history": history,
        "source_ref": f"file://{daily_path}",
        "open": latest.get("open"),
        "high": latest.get("high"),
        "low": latest.get("low"),
        "close": latest.get("close"),
        "volume": latest.get("volume"),
        "amount": latest.get("amount"),
        "data_quality_warnings": tuple(warnings),
    }
    if identity is not None:
        row["company_name"] = identity.company_name
        if identity.industry:
            row["industry"] = identity.industry
        if identity.list_date:
            row["list_date"] = identity.list_date
        if identity.source_ref:
            row["identity_source_ref"] = identity.source_ref
    if private_placement is not None:
        row.update(private_placement)
    if factor_source is not None:
        row["factor_source_ref"] = factor_source
    if factor_latest:
        for target, source in {
            "vol_ratio": "volume_ratio",
            "turnover_rate": "turnover_rate",
            "pe": "pe",
            "pe_ttm": "pe_ttm",
            "pb": "pb",
            "ps": "ps",
            "ps_ttm": "ps_ttm",
            "market_cap": "total_mv",
            "float_market_cap": "circ_mv",
            "total_share": "total_share",
            "float_share": "float_share",
            "free_share": "free_share",
            "adj_factor": "adj_factor",
        }.items():
            value = _float(factor_latest.get(source))
            if value is not None:
                row[target] = value
    return row


def _read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists() or path.stat().st_size == 0:
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _first_existing(*paths: Path) -> Path | None:
    for path in paths:
        if path.exists():
            return path
    return None


def _first_text(row: Mapping[str, str], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return None


def _ticker_from_baostock_code(value: str | None) -> str | None:
    text = str(value or "").strip().lower()
    if "." not in text:
        return None
    market, code = text.split(".", 1)
    if len(code) != 6 or not code.isdigit() or market not in {"sh", "sz", "bj"}:
        return None
    return f"{code}.{market.upper()}"


def _ticker_from_tushare_code(value: str | None) -> str | None:
    text = str(value or "").strip().upper()
    if "." not in text:
        return _symbol_from_six_digit_code(text)
    code, market = text.split(".", 1)
    if len(code) != 6 or not code.isdigit() or market not in {"SH", "SZ", "BJ"}:
        return None
    return f"{code}.{market}"


def _symbol_from_six_digit_code(value: str | None) -> str | None:
    text = str(value or "").strip()
    if len(text) != 6 or not text.isdigit():
        return None
    if text.startswith(("60", "68", "90")):
        return f"{text}.SH"
    if text.startswith(("00", "20", "30")):
        return f"{text}.SZ"
    if text.startswith(("43", "82", "83", "87", "88", "92")):
        return f"{text}.BJ"
    return None


def _parse_event_date(value: str | None) -> date | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    for normalized in (text, text.replace("/", "-")):
        try:
            return date.fromisoformat(normalized[:10])
        except ValueError:
            continue
    if len(text) >= 8 and text[:8].isdigit():
        try:
            return date(int(text[:4]), int(text[4:6]), int(text[6:8]))
        except ValueError:
            return None
    return None


def _normalized_selection_record(
    *,
    symbol: str,
    row: Mapping[str, Any],
    trade_date: str,
    as_of: datetime,
    raw_ref: str,
) -> dict[str, Any]:
    exchange = _exchange_for_symbol(symbol)
    field_set = tuple(sorted(str(key) for key, value in row.items() if value is not None))
    period_start = row["history"][0]["date"]
    period_end = row["history"][-1]["date"]
    return {
        "dataset": "daily_bar",
        "market": "CN_A",
        "symbol_id": symbol,
        "universe_ref": "all_a_shares",
        "granularity": "daily",
        "period_start": period_start,
        "period_end": period_end,
        "field_set": field_set,
        "as_of": as_of,
        "fresh_until": as_of,
        "source_roles": ("local_seed",),
        "exchange": exchange,
        "currency": "CNY",
        "timezone": "Asia/Shanghai",
        "calendar": "CN_A_SSE_SZSE",
        "base_asset": None,
        "quote_asset": None,
        "provider_lineage": {
            "provider_id": "local_a_share_prepackaged",
            "endpoint_id": "a_share_prepackaged_selection_import",
            "raw_refs": (raw_ref,),
            "remote_attempted": False,
            "requested_trade_date": trade_date,
        },
        "schema_id": "cn_a_selection_prepackaged.v1",
        "quality_flags": tuple(row.get("data_quality_warnings", ())),
        "row": dict(row),
    }


def _write_raw_package_record(
    *,
    repository: DatasetRepository,
    import_run_id: str,
    audit: ASharePackageAudit,
    history_days: int,
) -> str:
    payload = {
        "import_run_id": import_run_id,
        "audit": audit.as_dict(),
        "history_days": history_days,
    }
    digest = sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    return repository.insert_raw_payload(
        {
            "raw_ref": f"raw:CN_A:local_a_share_prepackaged:{digest[:12]}",
            "metadata_ref": f"rawmeta:CN_A:local_a_share_prepackaged:{digest[:12]}",
            "provider": "local_a_share_prepackaged",
            "endpoint": "a_share_prepackaged_selection_import",
            "market": "CN_A",
            "payload_hash": f"sha256:{digest}",
            "object_uri": None,
            "storage_mode": "metadata_only",
            "payload_size_bytes": len(json.dumps(payload, ensure_ascii=False).encode("utf-8")),
            "license_policy": {"raw_storage_mode": "metadata_only", "normalized_storage_allowed": True},
            "redaction_meta": {"payload_body_stored": False, "mongo_body_stored": False},
            "created_at": datetime.now(tz=UTC),
            "payload_summary": payload,
        }
    )


def _write_attempt_record(
    *,
    repository: DatasetRepository,
    import_run_id: str,
    dataset_refs: tuple[str, ...],
    raw_ref: str,
    warning_counts: Mapping[str, int],
) -> str:
    return repository.insert_provider_attempt(
        {
            "attempt_ref": f"attempt:local_a_share_prepackaged:a_share_prepackaged_selection_import:{uuid4().hex[:12]}",
            "provider": "local_a_share_prepackaged",
            "endpoint": "a_share_prepackaged_selection_import",
            "status": "local_seed_imported",
            "remote_attempted": False,
            "remote_success": False,
            "dataset_refs": dataset_refs,
            "raw_refs": (raw_ref,),
            "gap_codes": tuple(key for key, value in warning_counts.items() if value),
            "source_metadata": {
                "import_run_id": import_run_id,
                "execution_scope": "local_a_share_prepackaged_seed_import",
                "warning_counts": dict(warning_counts),
            },
            "created_at": datetime.now(tz=UTC),
        }
    )


def _write_manifest(
    *,
    repository: DatasetRepository,
    import_run_id: str,
    trade_date: str,
    audit: ASharePackageAudit,
    dataset_refs: tuple[str, ...],
    raw_refs: tuple[str, ...],
    attempt_refs: tuple[str, ...],
    replace_existing: bool,
    deleted_existing_count: int,
    warning_counts: Mapping[str, int],
) -> str:
    manifest = {
        "manifest_ref": f"manifest:CN_A:a_share_prepackaged:{import_run_id}",
        "job_id": import_run_id,
        "job_type": "seed_import",
        "market": "CN_A",
        "dataset_scope": "daily_bar",
        "trade_date": trade_date,
        "source_summary": "local:a_share_prepackaged:daily+factor",
        "dataset_refs": dataset_refs,
        "raw_refs": raw_refs,
        "attempt_refs": attempt_refs,
        "output_refs": {
            "dataset_refs": dataset_refs,
            "raw_refs": raw_refs,
            "attempt_refs": attempt_refs,
        },
        "gap_summary": tuple(key for key, value in warning_counts.items() if value),
        "audit": audit.as_dict(),
        "cleanup": {
            "replace_existing_provider_scope": replace_existing,
            "deleted_existing_count": deleted_existing_count,
            "provider_id": "local_a_share_prepackaged",
            "dataset_scope": "daily_bar",
        },
        "warning_counts": dict(warning_counts),
        "created_at": datetime.now(tz=UTC),
    }
    return repository.write_dataset_manifest(manifest)


def _root_fingerprint(paths: tuple[Path, ...]) -> str:
    material = tuple((str(path), path.stat().st_size, int(path.stat().st_mtime)) for path in paths if path.exists())
    return f"sha256:{sha256(repr(material).encode('utf-8')).hexdigest()}"


def _ticker_from_path(path: Path) -> str | None:
    return _ticker_from_name(path.name)


def _ticker_from_name(name: str) -> str | None:
    stem = Path(name).stem.strip()
    if not stem:
        return None
    normalized = stem.upper().replace("_", ".")
    parts = normalized.split(".")
    if len(parts) != 2:
        return None
    code, suffix = parts
    if not code.isdigit() or suffix not in _CN_A_EXCHANGE_BY_SUFFIX:
        return None
    return f"{code}.{suffix}"


def _exchange_for_symbol(symbol: str) -> str:
    suffix = symbol.rsplit(".", 1)[-1].upper()
    return _CN_A_EXCHANGE_BY_SUFFIX.get(suffix, "CN_A")


def _date_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return _factor_date_text(text)


def _factor_date_text(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if len(text) != 8 or not text.isdigit():
        return None
    try:
        return date(int(text[:4]), int(text[4:6]), int(text[6:8])).isoformat()
    except ValueError:
        return None


def _amount_yuan(value: str | None) -> float | None:
    amount = _float(value)
    if amount is None:
        return None
    return amount * 1000.0


def _float(value: str | object | None) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None
