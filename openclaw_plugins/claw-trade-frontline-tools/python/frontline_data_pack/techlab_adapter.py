from __future__ import annotations

from functools import lru_cache
import importlib.util
from pathlib import Path
import sys
import time
from typing import Any, Callable, Iterable, Sequence

import pandas as pd

from .errors import (
    L2_TARGET_INVALID,
    TECHLAB_CHART_FAILED,
    TECHLAB_INDICATOR_FAILED,
    TECHLAB_INPUT_INVALID,
    FrontlineValidationError,
)
from .evidence import OpenVikingEvidenceClient
from .evidence_writer import L2WriteSessionState, write_chart_evidence
from .models import (
    AtrIndicators,
    BollIndicators,
    KdjIndicators,
    MacdIndicators,
    MarketIndicators,
    MarketPriceRow,
    MovingAverageIndicators,
    RsiIndicators,
    TechlabInputFrame,
    TechlabResult,
)
from .runtime_context import ToolRuntimeContext
from .observability import record_span, record_techlab_chart_total, record_techlab_indicator_total
from .security import summarize_provider_error


MAX_CHARTS = 3
MAX_CHART_BYTES = 2 * 1024 * 1024
MIN_TECHLAB_ROWS = 20

_INDICATOR_ENGINE_PATH = (
    Path(__file__).resolve().parents[4]
    / "agents"
    / "market_analyst"
    / "skills"
    / "alphaear-techlab"
    / "scripts"
    / "indicator_engine.py"
)
_CHART_ENGINE_PATH = (
    Path(__file__).resolve().parents[4]
    / "agents"
    / "market_analyst"
    / "skills"
    / "alphaear-techlab"
    / "scripts"
    / "chart_engine.py"
)

IndicatorComputeFn = Callable[[pd.DataFrame, str], Any]
ChartRenderFn = Callable[[pd.DataFrame, str, Path], Sequence[Path | str]]


def validate_techlab_input(rows: list[MarketPriceRow]) -> None:
    if len(rows) < MIN_TECHLAB_ROWS:
        raise FrontlineValidationError(
            TECHLAB_INPUT_INVALID,
            f"OHLCV 行数不足，至少需要 {MIN_TECHLAB_ROWS} 行",
        )

    previous_date: str | None = None
    for row in rows:
        if previous_date is not None and row.trade_date <= previous_date:
            raise FrontlineValidationError(
                TECHLAB_INPUT_INVALID,
                "OHLCV 必须按 trade_date 严格升序",
            )
        previous_date = row.trade_date
        if row.open <= 0 or row.high <= 0 or row.low <= 0 or row.close <= 0:
            raise FrontlineValidationError(
                TECHLAB_INPUT_INVALID,
                "OHLCV 价格字段必须为正数",
            )
        if row.volume < 0:
            raise FrontlineValidationError(
                TECHLAB_INPUT_INVALID,
                "OHLCV volume 不能为负数",
            )
        if row.amount is not None and row.amount < 0:
            raise FrontlineValidationError(
                TECHLAB_INPUT_INVALID,
                "OHLCV amount 不能为负数",
            )


def compute_market_techlab_outputs(
    frame: TechlabInputFrame,
    context: ToolRuntimeContext,
    *,
    l2_client: OpenVikingEvidenceClient | None = None,
    write_state: L2WriteSessionState | None = None,
    indicator_compute: IndicatorComputeFn | None = None,
    chart_render: ChartRenderFn | None = None,
) -> TechlabResult:
    techlab_started = time.perf_counter()
    diagnostics: list[str] = []
    chart_paths: list[str] = []
    chart_refs = []
    chart_evidence_refs = []

    try:
        validate_techlab_input(frame.rows)
    except FrontlineValidationError as exc:
        return TechlabResult(
            indicators=None,
            chart_paths=[],
            chart_refs=[],
            diagnostics=[_diagnostic(exc.code, exc.message)],
            failed=True,
            chart_evidence_refs=[],
        )

    try:
        evidence_root = _resolve_evidence_root(context.evidence_root)
        output_dir = _resolve_output_dir(frame.output_dir, evidence_root=evidence_root)
    except FrontlineValidationError as exc:
        return TechlabResult(
            indicators=None,
            chart_paths=[],
            chart_refs=[],
            diagnostics=[_diagnostic(exc.code, exc.message)],
            failed=True,
            chart_evidence_refs=[],
        )

    market_frame = _rows_to_frame(frame.rows)

    indicators: MarketIndicators | None = None
    chart_frame: pd.DataFrame | None = None
    bundle: Any | None = None
    compute = indicator_compute or _default_indicator_compute()
    indicator_started = time.perf_counter()
    indicator_status = "success"
    try:
        bundle = compute(market_frame, frame.ticker)
        chart_frame = getattr(bundle, "chart_frame")
    except Exception as exc:
        indicator_status = "error"
        diagnostics.append(
            _diagnostic(TECHLAB_INDICATOR_FAILED, summarize_provider_error(exc))
        )
    if bundle is not None:
        try:
            indicators = _build_market_indicators(bundle, chart_frame=chart_frame)
        except Exception as exc:
            indicator_status = "error"
            diagnostics.append(
                _diagnostic(TECHLAB_INDICATOR_FAILED, summarize_provider_error(exc))
            )
    indicator_elapsed_ms = max(0, int((time.perf_counter() - indicator_started) * 1000))
    record_span(
        "market.techlab.indicators",
        status=indicator_status,
        elapsed_ms=indicator_elapsed_ms,
        fields={
            "run_id": context.run_id,
            "call_id": context.call_id,
            "worker_id": context.worker_id,
            "tool_name": context.tool_name,
            "domain": "market",
            "status": indicator_status,
            "elapsed_ms": indicator_elapsed_ms,
        },
    )
    record_techlab_indicator_total(status=indicator_status)

    chart_started = time.perf_counter()
    chart_status = "success"
    if isinstance(chart_frame, pd.DataFrame):
        render = chart_render or _default_chart_render()
        try:
            rendered_paths = list(render(chart_frame, frame.ticker, output_dir))
        except Exception as exc:
            chart_status = "error"
            diagnostics.append(
                _diagnostic(TECHLAB_CHART_FAILED, summarize_provider_error(exc))
            )
        else:
            state = write_state or L2WriteSessionState()
            for raw_path in rendered_paths[:MAX_CHARTS]:
                try:
                    chart_path = _resolve_chart_path(raw_path, output_dir=output_dir)
                    chart_paths.append(str(chart_path))
                    chart_bytes = chart_path.read_bytes()
                    if len(chart_bytes) > MAX_CHART_BYTES:
                        chart_status = "error"
                        diagnostics.append(
                            _diagnostic(
                                TECHLAB_CHART_FAILED,
                                f"{chart_path.name} 超过 2 MiB，实际 {len(chart_bytes)} bytes",
                            )
                        )
                        continue
                    chart_kind = _derive_chart_kind(chart_path)
                    write_result = write_chart_evidence(
                        context=context,
                        chart_kind=chart_kind,
                        chart_bytes=chart_bytes,
                        chart_local_ref=str(chart_path),
                        state=state,
                        client=l2_client,
                    )
                    if (
                        write_result.ok
                        and write_result.chart_ref is not None
                        and write_result.evidence_ref is not None
                    ):
                        chart_refs.append(write_result.chart_ref)
                        chart_evidence_refs.append(write_result.evidence_ref)
                        continue
                    if write_result.ok:
                        chart_status = "error"
                        diagnostics.append(
                            _diagnostic(TECHLAB_CHART_FAILED, "chart 写入结果缺少证据引用")
                        )
                        continue
                    if write_result.error is not None:
                        chart_status = "error"
                        diagnostics.append(write_result.error.diagnostic_flag)
                        continue
                    chart_status = "error"
                    diagnostics.append(_diagnostic(TECHLAB_CHART_FAILED, "chart 写入失败"))
                except FrontlineValidationError as exc:
                    chart_status = "error"
                    diagnostics.append(_diagnostic(exc.code, exc.message))
                except Exception as exc:
                    chart_status = "error"
                    diagnostics.append(
                        _diagnostic(TECHLAB_CHART_FAILED, summarize_provider_error(exc))
                    )
    chart_elapsed_ms = max(0, int((time.perf_counter() - chart_started) * 1000))
    record_span(
        "market.techlab.chart",
        status=chart_status if chart_refs else ("error" if diagnostics else chart_status),
        elapsed_ms=chart_elapsed_ms,
        fields={
            "run_id": context.run_id,
            "call_id": context.call_id,
            "worker_id": context.worker_id,
            "tool_name": context.tool_name,
            "domain": "market",
            "status": chart_status if chart_refs else ("error" if diagnostics else chart_status),
            "elapsed_ms": chart_elapsed_ms,
        },
    )
    record_techlab_chart_total(
        status=chart_status if chart_refs else ("error" if diagnostics else chart_status),
        count=max(1, len(chart_refs) if chart_refs else 1),
    )

    failed = indicators is None and not chart_refs
    techlab_elapsed_ms = max(0, int((time.perf_counter() - techlab_started) * 1000))
    record_span(
        "market.techlab",
        status="error" if failed else "success",
        elapsed_ms=techlab_elapsed_ms,
        fields={
            "run_id": context.run_id,
            "call_id": context.call_id,
            "worker_id": context.worker_id,
            "tool_name": context.tool_name,
            "domain": "market",
            "status": "error" if failed else "success",
            "elapsed_ms": techlab_elapsed_ms,
        },
    )
    return TechlabResult(
        indicators=indicators,
        chart_paths=chart_paths,
        chart_refs=chart_refs,
        diagnostics=diagnostics,
        failed=failed,
        chart_evidence_refs=chart_evidence_refs,
    )


@lru_cache(maxsize=1)
def _default_indicator_compute() -> IndicatorComputeFn:
    module = _load_module(_INDICATOR_ENGINE_PATH, module_name="frontline_alphaear_indicator")
    analyze_market_frame = getattr(module, "analyze_market_frame", None)
    if not callable(analyze_market_frame):
        raise RuntimeError("alphaear-techlab indicator_engine.analyze_market_frame 不可调用")

    def _compute(frame: pd.DataFrame, ticker: str) -> Any:
        return analyze_market_frame(frame, ticker=ticker)

    return _compute


@lru_cache(maxsize=1)
def _default_chart_render() -> ChartRenderFn:
    module = _load_module(_CHART_ENGINE_PATH, module_name="frontline_alphaear_chart")
    render_market_charts = getattr(module, "render_market_charts", None)
    if not callable(render_market_charts):
        raise RuntimeError("alphaear-techlab chart_engine.render_market_charts 不可调用")

    def _render(chart_frame: pd.DataFrame, ticker: str, output_dir: Path) -> Sequence[Path | str]:
        return render_market_charts(chart_frame, ticker=ticker, output_dir=output_dir)

    return _render


def _load_module(path: Path, *, module_name: str) -> Any:
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载模块: {path}")
    module = importlib.util.module_from_spec(spec)
    previous = sys.modules.get(module_name)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        if previous is None:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = previous
        raise
    return module


def _rows_to_frame(rows: Iterable[MarketPriceRow]) -> pd.DataFrame:
    payload: list[dict[str, Any]] = []
    for row in rows:
        payload.append(
            {
                "date": row.trade_date,
                "open": row.open,
                "high": row.high,
                "low": row.low,
                "close": row.close,
                "volume": row.volume,
                "amount": row.amount,
            }
        )
    return pd.DataFrame(payload)


def _build_market_indicators(bundle: Any, *, chart_frame: pd.DataFrame) -> MarketIndicators:
    indicators = getattr(bundle, "indicators", {})
    ma_payload = indicators.get("ma", {}) if isinstance(indicators, dict) else {}
    macd_payload = indicators.get("macd", {}) if isinstance(indicators, dict) else {}
    rsi_payload = indicators.get("rsi", {}) if isinstance(indicators, dict) else {}
    boll_payload = indicators.get("boll", {}) if isinstance(indicators, dict) else {}
    kdj_payload = indicators.get("kdj", {}) if isinstance(indicators, dict) else {}
    atr_payload = indicators.get("atr", {}) if isinstance(indicators, dict) else {}

    close_series = pd.to_numeric(chart_frame.get("close"), errors="coerce")
    ma60_value = _latest_float(close_series.rolling(60).mean())
    rsi6_value = _latest_float(_compute_rsi(close_series, 6))
    rsi12_value = _latest_float(_compute_rsi(close_series, 12))
    rsi24_value = _latest_float(_compute_rsi(close_series, 24))

    return MarketIndicators(
        ma=MovingAverageIndicators(
            ma5=_optional_float(ma_payload.get("ma5")),
            ma10=_optional_float(ma_payload.get("ma10")),
            ma20=_optional_float(ma_payload.get("ma20")),
            ma60=ma60_value,
        ),
        macd=MacdIndicators(
            dif=_optional_float(macd_payload.get("dif", macd_payload.get("macd"))),
            dea=_optional_float(macd_payload.get("dea", macd_payload.get("signal"))),
            macd=_optional_float(macd_payload.get("hist")),
        ),
        rsi=RsiIndicators(
            rsi6=_optional_float(rsi_payload.get("rsi6", rsi6_value)),
            rsi12=_optional_float(rsi_payload.get("rsi12", rsi12_value)),
            rsi24=_optional_float(rsi_payload.get("rsi24", rsi24_value)),
        ),
        boll=BollIndicators(
            mid=_optional_float(boll_payload.get("mid")),
            upper=_optional_float(boll_payload.get("upper")),
            lower=_optional_float(boll_payload.get("lower")),
        ),
        kdj=KdjIndicators(
            k=_optional_float(kdj_payload.get("k")),
            d=_optional_float(kdj_payload.get("d")),
            j=_optional_float(kdj_payload.get("j")),
        ),
        atr=AtrIndicators(
            atr14=_optional_float(atr_payload.get("atr14")),
        ),
    )


def _compute_rsi(series: pd.Series, window: int) -> pd.Series:
    delta = series.diff()
    gains = delta.clip(lower=0)
    losses = -delta.clip(upper=0)
    avg_gain = gains.ewm(alpha=1 / window, min_periods=window, adjust=False).mean()
    avg_loss = losses.ewm(alpha=1 / window, min_periods=window, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, pd.NA)
    return 100 - (100 / (1 + rs))


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    if pd.isna(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _latest_float(series: pd.Series) -> float | None:
    if series.empty:
        return None
    value = series.iloc[-1]
    if pd.isna(value):
        return None
    return float(value)


def _resolve_evidence_root(evidence_root: str) -> Path:
    if "://" in evidence_root:
        raise FrontlineValidationError(
            L2_TARGET_INVALID,
            "runtime_context.evidence_root 必须是当前 call 本地目录",
        )
    root = Path(evidence_root).expanduser().resolve()
    return root


def _resolve_output_dir(output_dir: str, *, evidence_root: Path) -> Path:
    candidate = Path(output_dir).expanduser()
    if candidate.is_absolute():
        normalized = candidate.resolve()
    else:
        normalized = (evidence_root / candidate).resolve()
    if normalized != evidence_root and evidence_root not in normalized.parents:
        raise FrontlineValidationError(
            L2_TARGET_INVALID,
            "output_dir 超出当前 call 本地审计目录",
        )
    return normalized


def _resolve_chart_path(raw_path: Path | str, *, output_dir: Path) -> Path:
    path = Path(raw_path).expanduser()
    normalized = path.resolve() if path.is_absolute() else (output_dir / path).resolve()
    if normalized != output_dir and output_dir not in normalized.parents:
        raise FrontlineValidationError(
            L2_TARGET_INVALID,
            f"chart 文件路径越界: {raw_path}",
        )
    return normalized


def _derive_chart_kind(path: Path) -> str:
    stem = path.stem.lower()
    if "market_structure" in stem:
        return "market_structure"
    if "volume" in stem:
        return "volume"
    return "indicator"


def _diagnostic(code: str, message: str) -> str:
    return f"{code}:{message}"
