"""Read-only local Baostock seed reader.

T13 cutover note: local CSV files are seed/import/audit input only. This module
may be called by explicit importer/fetcher work, but `/select` availability
must come from a warehouse-marked run with `openbb_normalized` refs.
"""

from __future__ import annotations

import csv
import hashlib
import os
from collections import deque
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Mapping, Sequence

from claw_trade.selection.data_job import SelectionProviderBatchResult
from claw_trade.selection.models import (
    DataGapRef,
    DataGapSeverity,
    SelectionBatchScope,
    SelectionProviderBatchPlan,
    SelectionRunPlan,
)

LOCAL_BAOSTOCK_ROOT_ENV = "CLAW_TRADE_SELECTION_LOCAL_BAOSTOCK_ROOT"
LOCAL_PRIVATE_PLACEMENT_ROOT_ENV = "CLAW_TRADE_SELECTION_LOCAL_PRIVATE_PLACEMENT_ROOT"
LOCAL_BAOSTOCK_PROVIDER_ID = "local_baostock_qfq_seed"
LOCAL_BAOSTOCK_CANDIDATE_TYPE = "local_history_seed_partial_candidate"
_NO_PRIVATE_PLACEMENT_EVENT_DATE = "none"
_NO_PRIVATE_PLACEMENT_DAYS_SINCE = 9999.0


@dataclass(frozen=True)
class _UniverseEntry:
    raw_code: str
    ticker: str
    company_name: str


def resolve_local_baostock_root_from_env() -> Path | None:
    configured = os.environ.get(LOCAL_BAOSTOCK_ROOT_ENV, "").strip()
    return Path(configured).expanduser() if configured else None


def resolve_local_private_placement_root_from_env() -> Path | None:
    configured = os.environ.get(LOCAL_PRIVATE_PLACEMENT_ROOT_ENV, "").strip()
    return Path(configured).expanduser() if configured else None


def fetch_selection_batch_from_local_baostock(
    plan: SelectionRunPlan,
    *,
    root: Path,
    private_placement_root: Path | None = None,
) -> SelectionProviderBatchResult:
    root = root.expanduser()
    private_placement_root = private_placement_root or resolve_local_private_placement_root_from_env()
    if private_placement_root is not None:
        private_placement_root = private_placement_root.expanduser()
    provider_plan = _provider_batch_plan(plan=plan)
    source_metadata = _source_metadata(plan=plan, root=root, private_placement_root=private_placement_root)
    attempt_refs = _attempt_refs(plan=plan, root=root)
    data_gaps: list[DataGapRef] = []
    normalized_ref = _normalized_ref(plan=plan, root=root)

    if not root.exists():
        return SelectionProviderBatchResult(
            provider_batch_plan=provider_plan,
            attempt_refs=attempt_refs,
            normalized_refs=(),
            rows=(),
            data_gaps=(
                _blocker_gap(
                    plan=plan,
                    suffix="root-missing",
                    gap_code="selection_local_baostock_root_missing",
                    attempt_refs=attempt_refs,
                    message=f"本地 Baostock 历史数据目录不存在：{root}。不能用空数据冒充选股完成。",
                    source_metadata=source_metadata,
                ),
            ),
        )

    universe_entries, universe_gap = _load_universe(plan=plan, root=root, attempt_refs=attempt_refs)
    if universe_gap is not None:
        return SelectionProviderBatchResult(
            provider_batch_plan=provider_plan,
            attempt_refs=attempt_refs,
            normalized_refs=(),
            rows=(),
            data_gaps=(universe_gap,),
        )

    required_history_days = max(250, int(plan.lookback_trading_days or 0))
    qfq_dir = root / "daily" / "qfq"
    rows: list[Mapping[str, object]] = []
    insufficient_history: list[str] = []
    stale_history: list[str] = []
    missing_files: list[str] = []
    missing_private_placement_coverage: list[str] = []
    for entry in universe_entries:
        history_file = qfq_dir / f"{entry.raw_code}.csv"
        if not history_file.exists():
            missing_files.append(entry.ticker)
            continue
        history = _read_history_tail(
            history_file,
            trade_date=plan.trade_date,
            required_history_days=required_history_days,
        )
        if len(history) < required_history_days:
            insufficient_history.append(entry.ticker)
            continue
        latest = history[-1]
        if str(latest.get("date") or "") < plan.trade_date:
            stale_history.append(entry.ticker)
            continue
        row: dict[str, object] = {
            "ticker": entry.ticker,
            "company_name": entry.company_name,
            "industry": None,
            "open": latest["open"],
            "high": latest["high"],
            "low": latest["low"],
            "close": latest["close"],
            "volume": latest["volume"],
            "amount": latest.get("amount"),
            "p_change_pct": latest.get("p_change_pct"),
            "turn": latest.get("turn"),
            "tradestatus": latest.get("tradestatus"),
            "is_st": latest.get("is_st"),
            "history": history,
            "source_ref": normalized_ref,
        }
        private_event = _load_private_placement_event(
            ticker=entry.ticker,
            trade_date=plan.trade_date,
            private_placement_root=private_placement_root,
        )
        if private_event is None:
            missing_private_placement_coverage.append(entry.ticker)
        else:
            row.update(private_event)
        rows.append(row)

    if insufficient_history:
        data_gaps.append(
            _gap(
                plan=plan,
                suffix="history-insufficient",
                gap_code="selection_local_baostock_history_insufficient",
                severity=DataGapSeverity.BLOCKER if not rows else DataGapSeverity.WARN,
                attempt_refs=attempt_refs,
                message=(
                    "本地 Baostock 种子中部分股票历史日线不足，已从本次特征计算剔除；"
                    f"required_days={required_history_days}, count={len(insufficient_history)}。"
                ),
                source_metadata={
                    **source_metadata,
                    "required_history_days": required_history_days,
                    "missing_tickers_sample": tuple(insufficient_history[:20]),
                },
            )
        )
    if missing_files:
        data_gaps.append(
            _gap(
                plan=plan,
                suffix="history-files-missing",
                gap_code="selection_local_baostock_history_files_missing",
                severity=DataGapSeverity.BLOCKER if not rows else DataGapSeverity.WARN,
                attempt_refs=attempt_refs,
                message=(
                    "本地 Baostock universe 中有股票缺少日线 CSV，已从本次特征计算剔除；"
                    f"count={len(missing_files)}。"
                ),
                source_metadata={
                    **source_metadata,
                    "missing_tickers_sample": tuple(missing_files[:20]),
                },
            )
        )
    if stale_history:
        data_gaps.append(
            _gap(
                plan=plan,
                suffix="history-stale",
                gap_code="selection_local_baostock_history_stale",
                severity=DataGapSeverity.BLOCKER if not rows else DataGapSeverity.WARN,
                attempt_refs=attempt_refs,
                message=(
                    "本地日线中部分股票最后一个交易日早于选股日，已从本次特征计算剔除；"
                    f"trade_date={plan.trade_date}, count={len(stale_history)}。"
                ),
                source_metadata={
                    **source_metadata,
                    "trade_date": plan.trade_date,
                    "stale_tickers_sample": tuple(stale_history[:20]),
                },
            )
        )
    if missing_private_placement_coverage:
        data_gaps.append(
            _blocker_gap(
                plan=plan,
                suffix="private-placement-source-missing",
                gap_code="selection_private_placement_source_missing",
                attempt_refs=attempt_refs,
                message=(
                    "本地数据没有覆盖 Sequoia-X 私募/定增事件字段，"
                    "不能生成 approved candidate pack。"
                ),
                source_metadata={
                    **source_metadata,
                    "missing_fields": ("private_placement_event_date", "private_placement_days_since"),
                    "missing_ticker_count": len(missing_private_placement_coverage),
                    "missing_tickers_sample": tuple(missing_private_placement_coverage[:20]),
                },
            )
        )

    if not rows:
        data_gaps.append(
            _blocker_gap(
                plan=plan,
                suffix="rows-empty",
                gap_code="selection_local_baostock_rows_empty",
                attempt_refs=attempt_refs,
                message="本地 Baostock 种子未产生任何满足历史长度的股票行，任务必须 fail closed。",
                source_metadata=source_metadata,
            )
        )
        return SelectionProviderBatchResult(
            provider_batch_plan=provider_plan,
            attempt_refs=attempt_refs,
            normalized_refs=(),
            rows=(),
            data_gaps=tuple(data_gaps),
        )

    return SelectionProviderBatchResult(
        provider_batch_plan=provider_plan,
        attempt_refs=attempt_refs,
        normalized_refs=(normalized_ref,),
        rows=tuple(rows),
        data_gaps=tuple(data_gaps),
    )


def _provider_batch_plan(*, plan: SelectionRunPlan) -> SelectionProviderBatchPlan:
    return SelectionProviderBatchPlan(
        plan_id=plan.provider_batch_plan_ref,
        scope=SelectionBatchScope.SELECTION_BATCH,
        market=plan.market,
        profile=plan.profile,
        trade_date=plan.trade_date,
        lookback_trading_days=plan.lookback_trading_days,
        universe_scope=plan.universe_scope,
        coverage_groups=("local_baostock_qfq_daily", "local_baostock_index_daily"),
        provider_candidates=(LOCAL_BAOSTOCK_PROVIDER_ID,),
        ttl_policy_ref="ttl://selection/local-baostock/daily-history",
        lineage_root_ref=f"lineage://selection/local-baostock/{plan.trade_date}",
    )


def _load_universe(
    *,
    plan: SelectionRunPlan,
    root: Path,
    attempt_refs: tuple[str, ...],
) -> tuple[tuple[_UniverseEntry, ...], DataGapRef | None]:
    universe_file = _select_universe_file(root=root, trade_date=plan.trade_date)
    if universe_file is None:
        return (), _blocker_gap(
            plan=plan,
            suffix="universe-missing",
            gap_code="selection_local_baostock_universe_missing",
            attempt_refs=attempt_refs,
            message="本地 Baostock 种子缺少 universe/query_all_stock_*.csv，无法确认股票名称和全市场范围。",
            source_metadata=_source_metadata(plan=plan, root=root, private_placement_root=None),
        )
    entries: list[_UniverseEntry] = []
    seen: dict[str, str] = {}
    with universe_file.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            raw_code = str(row.get("code") or "").strip().lower()
            if not _is_baostock_a_share_code(raw_code):
                continue
            ticker = _ticker_from_baostock_code(raw_code)
            company_name = str(row.get("code_name") or row.get("name") or "").strip()
            if not ticker or not company_name:
                continue
            previous = seen.get(ticker)
            if previous is not None:
                gap_code = "ticker_company_mismatch" if previous != company_name else "duplicate_ticker"
                return (), _blocker_gap(
                    plan=plan,
                    suffix=f"{gap_code}-{ticker}",
                    gap_code=gap_code,
                    attempt_refs=attempt_refs,
                    message=(
                        f"本地 Baostock universe 中 {ticker} 重复或名称冲突："
                        f"first={previous}, current={company_name}。"
                    ),
                        source_metadata={
                            **_source_metadata(plan=plan, root=root, private_placement_root=None),
                            "universe_file": str(universe_file),
                        },
                )
            seen[ticker] = company_name
            entries.append(_UniverseEntry(raw_code=raw_code, ticker=ticker, company_name=company_name))
    if not entries:
        return (), _blocker_gap(
            plan=plan,
            suffix="universe-empty",
            gap_code="selection_local_baostock_universe_empty",
            attempt_refs=attempt_refs,
            message="本地 Baostock universe 没有可用 A 股股票行。",
            source_metadata={
                **_source_metadata(plan=plan, root=root, private_placement_root=None),
                "universe_file": str(universe_file),
            },
        )
    return tuple(entries), None


def _select_universe_file(*, root: Path, trade_date: str) -> Path | None:
    universe_dir = root / "universe"
    dated_files = sorted(universe_dir.glob("query_all_stock_*.csv"))
    eligible: list[Path] = []
    for path in dated_files:
        date_part = path.stem.removeprefix("query_all_stock_")
        if date_part <= trade_date:
            eligible.append(path)
    if eligible:
        return eligible[-1]
    fallback = universe_dir / "a_share_stock_universe.csv"
    return fallback if fallback.exists() else None


def _read_history_tail(
    path: Path,
    *,
    trade_date: str,
    required_history_days: int,
) -> tuple[Mapping[str, float | str], ...]:
    rows: deque[Mapping[str, float | str]] = deque(maxlen=max(required_history_days, 1))
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for raw_row in reader:
            row_date = str(raw_row.get("date") or "").strip()
            if not row_date or row_date > trade_date:
                continue
            mapped = _map_history_row(raw_row)
            if mapped is not None:
                rows.append(mapped)
    return tuple(rows)


def _map_history_row(row: Mapping[str, str]) -> Mapping[str, float | str] | None:
    row_date = str(row.get("date") or "").strip()
    open_value = _float_value(row.get("open"))
    high_value = _float_value(row.get("high"))
    low_value = _float_value(row.get("low"))
    close_value = _float_value(row.get("close"))
    volume_value = _float_value(row.get("volume"))
    if not row_date or None in {open_value, high_value, low_value, close_value, volume_value}:
        return None
    mapped: dict[str, float | str] = {
        "date": row_date,
        "open": open_value,
        "high": high_value,
        "low": low_value,
        "close": close_value,
        "volume": volume_value,
    }
    optional_fields = {
        "amount": ("amount",),
        "p_change_pct": ("pctChg", "pct_chg", "p_change_pct"),
        "turn": ("turn",),
        "tradestatus": ("tradestatus", "tradeStatus"),
        "is_st": ("isST", "is_st"),
    }
    for target, candidates in optional_fields.items():
        value = _first_float(row, candidates)
        if value is not None:
            mapped[target] = value
    return mapped


def parse_baostock_daily_row(row: Mapping[str, str]) -> Mapping[str, float | str] | None:
    return _map_history_row(row)


def _is_baostock_a_share_code(raw_code: str) -> bool:
    if "." not in raw_code:
        return False
    market, code = raw_code.split(".", 1)
    if len(code) != 6 or not code.isdigit():
        return False
    if market == "sh":
        return code.startswith(("600", "601", "603", "605", "688", "689"))
    if market == "sz":
        return code.startswith(("000", "001", "002", "003", "300", "301"))
    if market == "bj":
        return code.startswith(("43", "83", "87", "88", "92"))
    return False


def normalize_baostock_a_share_code(raw_code: str) -> str | None:
    normalized = raw_code.strip().lower()
    if not _is_baostock_a_share_code(normalized):
        return None
    return _ticker_from_baostock_code(normalized)


def _ticker_from_baostock_code(raw_code: str) -> str:
    market, code = raw_code.split(".", 1)
    return f"{code}.{market.upper()}"


def _load_private_placement_event(
    *,
    ticker: str,
    trade_date: str,
    private_placement_root: Path | None,
) -> Mapping[str, object] | None:
    if private_placement_root is None:
        return None
    event_file = _private_placement_file(private_placement_root=private_placement_root, ticker=ticker)
    if event_file is None:
        return None
    trade_day = date.fromisoformat(trade_date)
    latest_event_date: date | None = None
    with event_file.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            event_date = _parse_event_date(row.get("公告日期"))
            if event_date is None or event_date > trade_day:
                continue
            method = str(row.get("发行方式") or "").strip()
            if not _is_private_placement_method(method):
                continue
            if latest_event_date is None or event_date > latest_event_date:
                latest_event_date = event_date
    if latest_event_date is None:
        event_date_text = _NO_PRIVATE_PLACEMENT_EVENT_DATE
        days_since = _NO_PRIVATE_PLACEMENT_DAYS_SINCE
    else:
        event_date_text = latest_event_date.isoformat()
        days_since = float((trade_day - latest_event_date).days)
    return {
        "private_placement_event_date": event_date_text,
        "private_placement_days_since": days_since,
        "private_placement_source_ref": event_file.resolve().as_uri(),
    }


def _private_placement_file(*, private_placement_root: Path, ticker: str) -> Path | None:
    code = ticker.split(".", 1)[0]
    candidates = (
        private_placement_root / f"{code}.csv",
        private_placement_root / f"{ticker}.csv",
        private_placement_root / f"{ticker.replace('.', '_')}.csv",
    )
    for path in candidates:
        if path.exists():
            return path
    return None


def parse_private_placement_event_date(value: object) -> date | None:
    return _parse_event_date(value)


def _parse_event_date(value: object) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    if len(text) == 8 and text.isdigit():
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def is_private_placement_method(method: str) -> bool:
    return _is_private_placement_method(method)


def _is_private_placement_method(method: str) -> bool:
    return any(token in method for token in ("定向", "非公开", "私募"))


def _attempt_refs(*, plan: SelectionRunPlan, root: Path) -> tuple[str, ...]:
    digest = _short_digest(f"{plan.selection_run_id}|{plan.trade_date}|{root}")
    attempt_ref = f"attempt://local-baostock/{plan.selection_run_id}/{plan.trade_date}/{digest}"
    manifest = root / "manifest" / "baostock_files.sha256"
    if manifest.exists():
        return (attempt_ref, manifest.resolve().as_uri())
    return (attempt_ref,)


def _normalized_ref(*, plan: SelectionRunPlan, root: Path) -> str:
    digest = _short_digest(f"{plan.selection_run_id}|{plan.trade_date}|{root.resolve()}")
    return f"normalized://local-baostock/qfq/{plan.trade_date}/{digest}"


def _source_metadata(
    *,
    plan: SelectionRunPlan,
    root: Path,
    private_placement_root: Path | None,
) -> Mapping[str, object]:
    metadata: dict[str, object] = {
        "provider": LOCAL_BAOSTOCK_PROVIDER_ID,
        "selection_candidate_type": LOCAL_BAOSTOCK_CANDIDATE_TYPE,
        "root": str(root),
        "trade_date": plan.trade_date,
        "qfq_daily_dir": str(root / "daily" / "qfq"),
        "index_daily_dir": str(root / "index_daily"),
        "universe_dir": str(root / "universe"),
        "adjustment": "qfq",
        "amount_unit": "CNY",
    }
    daily_source = _load_json(root / "manifest" / "daily_source.json")
    if daily_source:
        metadata["daily_source"] = daily_source
    if private_placement_root is not None:
        metadata["private_placement_dir"] = str(private_placement_root)
        metadata["private_placement_source"] = "akshare.stock_add_stock:sina"
    return metadata


def _load_json(path: Path) -> Mapping[str, object] | None:
    if not path.exists() or path.stat().st_size == 0:
        return None
    try:
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    return data if isinstance(data, dict) else None


def _gap(
    *,
    plan: SelectionRunPlan,
    suffix: str,
    gap_code: str,
    severity: DataGapSeverity,
    attempt_refs: tuple[str, ...],
    message: str,
    source_metadata: Mapping[str, object] | None = None,
) -> DataGapRef:
    return DataGapRef(
        gap_id=f"{plan.selection_run_id}-{suffix}",
        domain="selection",
        gap_code=gap_code,
        severity=severity,
        attempt_refs=attempt_refs,
        reader_message=message,
        source_metadata=source_metadata,
    )


def _blocker_gap(
    *,
    plan: SelectionRunPlan,
    suffix: str,
    gap_code: str,
    attempt_refs: tuple[str, ...],
    message: str,
    source_metadata: Mapping[str, object] | None = None,
) -> DataGapRef:
    return _gap(
        plan=plan,
        suffix=suffix,
        gap_code=gap_code,
        severity=DataGapSeverity.BLOCKER,
        attempt_refs=attempt_refs,
        message=message,
        source_metadata=source_metadata,
    )


def _first_float(row: Mapping[str, str], candidates: Sequence[str]) -> float | None:
    for key in candidates:
        value = _float_value(row.get(key))
        if value is not None:
            return value
    return None


def _float_value(value: object) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _short_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
