from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence
from urllib.parse import unquote, urlparse

from claw_trade.data_gateway.models import ChartAsset, ReadinessStatus


@dataclass(frozen=True)
class MarketChartInput:
    chart_id: str
    title: str
    kind: str
    status: ReadinessStatus
    root_cause: str | None
    series: Mapping[str, Any]


def build_market_chart_inputs(
    *,
    ticker: str,
    ohlcv_rows: Sequence[Mapping[str, Any]],
    indicator_rows: Sequence[Mapping[str, Any]],
    min_rows: int = 20,
) -> tuple[MarketChartInput, ...]:
    row_count = len(ohlcv_rows)
    if row_count < min_rows:
        cause = f"OHLCV 行数不足，无法生成技术图表：{row_count}/{min_rows}"
        return (
            MarketChartInput(
                chart_id=f"{ticker}:market_structure",
                title="价格与均线结构",
                kind="market_structure",
                status=ReadinessStatus.INSUFFICIENT,
                root_cause=cause,
                series={"ohlcv_rows": row_count},
            ),
            MarketChartInput(
                chart_id=f"{ticker}:indicator_panels",
                title="指标面板",
                kind="indicator_panels",
                status=ReadinessStatus.INSUFFICIENT,
                root_cause=cause,
                series={"indicator_rows": len(indicator_rows)},
            ),
        )

    return (
        MarketChartInput(
            chart_id=f"{ticker}:market_structure",
            title="价格与均线结构",
            kind="market_structure",
            status=ReadinessStatus.READY,
            root_cause=None,
            series={
                "trade_dates": [str(row.get("trade_date", "")) for row in ohlcv_rows],
                "close": [row.get("close") for row in ohlcv_rows],
                "ma5": [row.get("ma5") for row in indicator_rows],
                "ma10": [row.get("ma10") for row in indicator_rows],
                "ma20": [row.get("ma20") for row in indicator_rows],
            },
        ),
        MarketChartInput(
            chart_id=f"{ticker}:indicator_panels",
            title="指标面板",
            kind="indicator_panels",
            status=ReadinessStatus.READY,
            root_cause=None,
            series={
                "trade_dates": [str(row.get("trade_date", "")) for row in indicator_rows],
                "macd": [row.get("macd") for row in indicator_rows],
                "macd_signal": [row.get("macd_signal") for row in indicator_rows],
                "rsi14": [row.get("rsi14") for row in indicator_rows],
                "boll_mid": [row.get("boll_mid") for row in indicator_rows],
                "boll_upper": [row.get("boll_upper") for row in indicator_rows],
                "boll_lower": [row.get("boll_lower") for row in indicator_rows],
            },
        ),
    )


def build_chart_assets(
    *,
    inputs: Sequence[MarketChartInput],
    image_refs: Mapping[str, str] | None = None,
) -> tuple[ChartAsset, ...]:
    refs = image_refs or {}
    assets: list[ChartAsset] = []
    for item in inputs:
        payload = json.dumps(item.series, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
        data_ref = f"chart-data://sha256:{hashlib.sha256(payload).hexdigest()}"
        image_ref = refs.get(item.chart_id)
        if item.status != ReadinessStatus.READY:
            assets.append(
                ChartAsset(
                    chart_id=item.chart_id,
                    title=item.title,
                    kind=item.kind,
                    image_ref=None,
                    data_ref=data_ref,
                    status=item.status,
                    root_cause=item.root_cause,
                )
            )
            continue
        if not image_ref:
            assets.append(
                ChartAsset(
                    chart_id=item.chart_id,
                    title=item.title,
                    kind=item.kind,
                    image_ref=None,
                    data_ref=data_ref,
                    status=ReadinessStatus.PARTIAL,
                    root_cause="图表渲染结果缺失：未生成图片资产。",
                )
            )
            continue
        assets.append(
            ChartAsset(
                chart_id=item.chart_id,
                title=item.title,
                kind=item.kind,
                image_ref=image_ref,
                data_ref=data_ref,
                status=ReadinessStatus.READY,
                root_cause=None,
            )
        )
    return tuple(assets)


def render_market_chart_images(
    *,
    inputs: Sequence[MarketChartInput],
    object_store_uri: str,
    run_id: str,
    call_id: str,
    existing_image_refs: Mapping[str, str] | None = None,
) -> dict[str, str]:
    store_root = _file_store_root(object_store_uri)
    if store_root is None:
        return {}

    refs = dict(existing_image_refs or {})
    generated: dict[str, str] = {}
    for item in inputs:
        if item.status != ReadinessStatus.READY or item.chart_id in refs:
            continue
        payload = json.dumps(item.series, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
        filename = f"{_safe_filename(item.kind)}-{hashlib.sha256(payload).hexdigest()[:16]}.png"
        output_path = store_root / "runs" / _safe_filename(run_id) / _safe_filename(call_id) / "charts" / filename
        try:
            _render_png(item, output_path)
        except Exception:
            continue
        generated[item.chart_id] = output_path.resolve().as_uri()
    return generated


def _file_store_root(object_store_uri: str) -> Path | None:
    parsed = urlparse(object_store_uri)
    if parsed.scheme != "file":
        return None
    if parsed.netloc and parsed.netloc not in {"localhost"}:
        return None
    path_text = unquote(parsed.path)
    if not path_text:
        return None
    return Path(path_text).expanduser()


def _render_png(item: MarketChartInput, output_path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg", force=True)
    from matplotlib import pyplot as plt

    output_path.parent.mkdir(parents=True, exist_ok=True)
    dates = [str(value) for value in item.series.get("trade_dates", ())]
    x_values = list(range(len(dates)))
    fig, ax = plt.subplots(figsize=(10, 4.8), dpi=120)
    if item.kind == "market_structure":
        _plot_line(ax, x_values, item.series.get("close", ()), label="close", color="#1f2937", width=1.8)
        _plot_line(ax, x_values, item.series.get("ma5", ()), label="ma5", color="#2563eb", width=1.1)
        _plot_line(ax, x_values, item.series.get("ma10", ()), label="ma10", color="#059669", width=1.1)
        _plot_line(ax, x_values, item.series.get("ma20", ()), label="ma20", color="#d97706", width=1.1)
        ax.set_ylabel("price")
    else:
        _plot_line(ax, x_values, item.series.get("macd", ()), label="macd", color="#2563eb", width=1.1)
        _plot_line(ax, x_values, item.series.get("macd_signal", ()), label="signal", color="#d97706", width=1.1)
        _plot_line(ax, x_values, item.series.get("rsi14", ()), label="rsi14", color="#7c3aed", width=1.1)
        _plot_line(ax, x_values, item.series.get("boll_mid", ()), label="boll_mid", color="#6b7280", width=0.9)
        _plot_line(ax, x_values, item.series.get("boll_upper", ()), label="boll_upper", color="#059669", width=0.9)
        _plot_line(ax, x_values, item.series.get("boll_lower", ()), label="boll_lower", color="#dc2626", width=0.9)
        ax.axhline(0, color="#9ca3af", linewidth=0.7)
        ax.set_ylabel("indicator")
    ax.set_title(f"{item.chart_id} {item.kind}")
    ax.grid(True, color="#e5e7eb", linewidth=0.8)
    _format_x_axis(ax, dates)
    ax.legend(loc="best", fontsize=8)
    fig.tight_layout()
    fig.savefig(output_path, format="png")
    plt.close(fig)


def _plot_line(ax: Any, x_values: Sequence[int], values: object, *, label: str, color: str, width: float) -> None:
    y_values = [_to_float_or_none(value) for value in values] if isinstance(values, Sequence) else []
    points = [(x, y) for x, y in zip(x_values, y_values, strict=False) if y is not None]
    if not points:
        return
    xs, ys = zip(*points, strict=False)
    ax.plot(xs, ys, label=label, color=color, linewidth=width)


def _format_x_axis(ax: Any, dates: Sequence[str]) -> None:
    if not dates:
        return
    step = max(1, len(dates) // 6)
    ticks = list(range(0, len(dates), step))
    if ticks[-1] != len(dates) - 1:
        ticks.append(len(dates) - 1)
    ax.set_xticks(ticks)
    ax.set_xticklabels([dates[index] for index in ticks], rotation=30, ha="right", fontsize=8)


def _to_float_or_none(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_filename(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return safe.strip("._") or "chart"
