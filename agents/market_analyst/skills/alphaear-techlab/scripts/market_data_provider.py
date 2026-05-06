from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path

import pandas as pd

STOCK_SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "alphaear-stock" / "scripts"
STOCK_SKILL_ROOT = STOCK_SCRIPTS_DIR.parent
STOCK_RUNTIME_PACKAGE = "_alphaear_market_stock_runtime"


class NoMarketDataError(RuntimeError):
    """Raised when the upstream stock skill reports that no rows are available."""


def _ensure_stock_runtime_package() -> None:
    if STOCK_RUNTIME_PACKAGE in sys.modules:
        return
    spec = importlib.util.spec_from_file_location(
        STOCK_RUNTIME_PACKAGE,
        STOCK_SCRIPTS_DIR / "__init__.py",
        submodule_search_locations=[str(STOCK_SCRIPTS_DIR)],
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("stock skill runtime package could not be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[STOCK_RUNTIME_PACKAGE] = module
    spec.loader.exec_module(module)


def _stock_default_db_path() -> str:
    return str((STOCK_SKILL_ROOT / "data" / "signal_flux.db").resolve())


def _stock_entrypoint_deps():
    _ensure_stock_runtime_package()
    database_module = importlib.import_module(f"{STOCK_RUNTIME_PACKAGE}.database_manager")
    stock_tools_module = importlib.import_module(f"{STOCK_RUNTIME_PACKAGE}.stock_tools")

    def get_stock_tools(db_path: str, *, auto_update: bool = True):
        db = database_module.DatabaseManager(db_path=db_path)
        return stock_tools_module.StockTools(db=db, auto_update=auto_update)

    return _stock_default_db_path, get_stock_tools


def _normalize_price_frame(frame: pd.DataFrame) -> pd.DataFrame:
    normalized = frame.copy()
    if normalized.empty:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume", "change_pct"])

    normalized.columns = [str(column).lower() for column in normalized.columns]
    required = {"date", "open", "high", "low", "close", "volume"}
    missing = required - set(normalized.columns)
    if missing:
        raise RuntimeError(f"price frame missing required columns: {sorted(missing)}")

    normalized["date"] = pd.to_datetime(normalized["date"], errors="coerce")
    normalized = normalized.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
    for column in ("open", "high", "low", "close", "volume"):
        normalized[column] = pd.to_numeric(normalized[column], errors="coerce")
    if "change_pct" in normalized.columns:
        normalized["change_pct"] = pd.to_numeric(normalized["change_pct"], errors="coerce")
    else:
        normalized["change_pct"] = normalized["close"].pct_change().mul(100.0)
    normalized["change_pct"] = normalized["change_pct"].fillna(0.0)
    normalized["date"] = normalized["date"].dt.strftime("%Y-%m-%d")
    normalized = normalized.dropna(subset=["open", "high", "low", "close", "volume"]).reset_index(drop=True)
    return normalized[["date", "open", "high", "low", "close", "volume", "change_pct"]]


def load_price_frame(*, ticker: str, start_date: str, end_date: str) -> pd.DataFrame:
    _default_db_path, get_stock_tools = _stock_entrypoint_deps()
    db_path = _default_db_path()
    tools = get_stock_tools(db_path, auto_update=False)
    frame = tools.get_stock_price(ticker, start_date=start_date, end_date=end_date)
    if frame is None:
        raise NoMarketDataError(f"no price rows available for {ticker}")
    normalized = _normalize_price_frame(frame)
    if normalized.empty:
        return normalized
    return normalized
