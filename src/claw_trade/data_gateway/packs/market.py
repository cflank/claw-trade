from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime
import hashlib
import json
from typing import Any, Mapping, Sequence

from claw_trade.data_gateway.models import (
    ChartAsset,
    Conflict,
    DataGap,
    DataGapReason,
    DomainPackResult,
    FreshnessStatus,
    GapSeverity,
    Market,
    PackAuditPayload,
    PackDomain,
    PackRequest,
    PrioritySource,
    ProviderAttempt,
    ProviderCallSpec,
    ProviderResult,
    ProviderStatus,
    Readiness,
    ReadinessStatus,
    RunProviderPlan,
    SourceRole,
    utc_now_iso,
)
from claw_trade.data_gateway.packs.charts import (
    build_chart_assets,
    build_market_chart_inputs,
    render_market_chart_images,
)

_OHLCV_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "trade_date": ("trade_date", "date", "日期"),
    "open": ("open", "开盘"),
    "high": ("high", "最高"),
    "low": ("low", "最低"),
    "close": ("close", "收盘"),
    "volume": ("volume", "成交量"),
    "amount": ("amount", "turnover", "成交额"),
    "change": ("change", "涨跌额"),
    "pct_change": ("pct_change", "pct_chg", "涨跌幅"),
}
_BLOCKING_REASONS = frozenset(
    {
        DataGapReason.CREDENTIAL_MISSING,
        DataGapReason.LICENSE_BLOCKED,
        DataGapReason.RATE_LIMITED,
    }
)

_STATUS_TEXT: dict[ProviderStatus, str] = {
    ProviderStatus.REMOTE_SUCCESS: "远端获取成功",
    ProviderStatus.CACHE_HIT: "使用有效缓存",
    ProviderStatus.SHARED_RESULT: "复用同次运行结果",
    ProviderStatus.CREDENTIAL_MISSING: "缺少接口凭证",
    ProviderStatus.LICENSE_BLOCKED: "许可边界阻断",
    ProviderStatus.RATE_LIMITED: "接口限流",
    ProviderStatus.EMPTY: "来源返回为空",
    ProviderStatus.FIELD_MISSING: "字段缺失",
    ProviderStatus.SCHEMA_INVALID: "返回结构不符合预期",
    ProviderStatus.CACHE_ERROR: "缓存读取失败",
    ProviderStatus.CACHED_EMPTY: "缓存为空",
    ProviderStatus.CACHE_STALE: "缓存已过期",
    ProviderStatus.EVIDENCE_WRITE_FAILED: "证据写入失败",
    ProviderStatus.SKIPPED_NOT_CONFIGURED: "来源未配置",
    ProviderStatus.REMOTE_ERROR: "远端请求失败",
    ProviderStatus.CACHE_MISS: "缓存未命中",
}

_FRESHNESS_TEXT: dict[FreshnessStatus, str] = {
    FreshnessStatus.FRESH_REMOTE: "远端实时获取",
    FreshnessStatus.FRESH_CACHE: "有效缓存",
    FreshnessStatus.STALE_CACHE: "过期缓存",
    FreshnessStatus.CACHE_UNUSABLE: "缓存不可用",
    FreshnessStatus.NOT_FETCHED: "未获取",
}

_READINESS_TEXT: dict[ReadinessStatus, str] = {
    ReadinessStatus.READY: "就绪",
    ReadinessStatus.PARTIAL: "部分覆盖",
    ReadinessStatus.INSUFFICIENT: "资料不足",
    ReadinessStatus.BLOCKED: "阻断",
}

_GAP_REASON_TEXT: dict[DataGapReason, str] = {
    DataGapReason.CREDENTIAL_MISSING: "缺少接口凭证",
    DataGapReason.RATE_LIMITED: "接口限流",
    DataGapReason.EMPTY: "来源返回为空",
    DataGapReason.FIELD_MISSING: "字段缺失",
    DataGapReason.SCHEMA_INVALID: "返回结构不符合预期",
    DataGapReason.PROVIDER_UNAVAILABLE: "来源不可用",
    DataGapReason.SOURCE_NOT_CONFIGURED: "来源未配置",
    DataGapReason.CACHE_ERROR: "缓存读取失败",
    DataGapReason.CACHED_EMPTY: "缓存为空",
    DataGapReason.STALE_CACHE_UNUSABLE: "缓存已过期且不可用",
    DataGapReason.EVIDENCE_WRITE_FAILED: "证据写入失败",
    DataGapReason.LICENSE_BLOCKED: "许可边界阻断",
}


@dataclass
class MarketPackBuilder:
    openbb_runtime_marker: str = "openbb-v4.7.0"
    openbb_extension_version: str = "claw-trade.market-pack.v1"
    min_chart_rows: int = 20

    def build(
        self,
        *,
        request: PackRequest,
        run_plan: RunProviderPlan,
        results: Sequence[ProviderResult],
        data_gaps: Sequence[DataGap] = (),
        conflicts: Sequence[Conflict] = (),
        chart_image_refs: Mapping[str, str] | None = None,
        chart_object_store_uri: str | None = None,
    ) -> DomainPackResult:
        call_specs = tuple(
            spec
            for spec in run_plan.call_specs
            if spec.domain == PackDomain.MARKET and spec.market == request.market
        )
        market_results = tuple(
            sorted(
                (
                    result
                    for result in results
                    if result.spec.domain == PackDomain.MARKET and result.spec.market == request.market
                ),
                key=lambda item: (item.spec.priority, item.spec.provider, item.spec.endpoint),
            )
        )
        attempts = tuple(result.attempt for result in market_results)
        cache_receipts = tuple(result.cache_receipt for result in market_results if result.cache_receipt is not None)

        ohlcv_rows = _collect_ohlcv_rows(market_results)
        indicator_rows, compact_facts = _compute_indicators(ohlcv_rows)
        compact_facts["schema_id"] = f"{request.market.value.lower()}.market.ohlcv.v1"
        if not compact_facts.get("currency"):
            compact_facts["currency"] = _default_currency(request.market, request.currency)
        if not compact_facts.get("timezone"):
            compact_facts["timezone"] = _default_timezone(request.market)

        chart_inputs = build_market_chart_inputs(
            ticker=request.ticker,
            ohlcv_rows=ohlcv_rows,
            indicator_rows=indicator_rows,
            min_rows=self.min_chart_rows,
        )
        image_refs: dict[str, str] = dict(chart_image_refs or {})
        if chart_object_store_uri:
            image_refs.update(
                render_market_chart_images(
                    inputs=chart_inputs,
                    object_store_uri=chart_object_store_uri,
                    run_id=request.run_id,
                    call_id=request.call_id,
                    existing_image_refs=image_refs,
                )
            )
        chart_assets = build_chart_assets(inputs=chart_inputs, image_refs=image_refs)
        if request.market == Market.HK and not ohlcv_rows:
            chart_assets = _annotate_hk_chart_root_cause(chart_assets=chart_assets, results=market_results)

        combined_gaps = _merge_gaps(
            list(data_gaps)
            + _build_status_gaps(request=request, results=market_results)
            + _build_chart_gaps(request=request, chart_assets=chart_assets),
        )
        readiness = _compute_readiness(
            request=request,
            call_specs=call_specs,
            results=market_results,
            gaps=combined_gaps,
            chart_assets=chart_assets,
        )
        reader_brief = _render_reader_brief(
            request=request,
            readiness=readiness,
            ohlcv_rows=ohlcv_rows,
            compact_facts=compact_facts,
            results=market_results,
            gaps=combined_gaps,
            chart_assets=chart_assets,
        )

        raw_refs = tuple(result.raw_ref for result in market_results if result.raw_ref)
        normalized_refs = tuple(result.normalized_ref for result in market_results if result.normalized_ref)
        bundle_ref = _normalized_bundle_ref(request=request, normalized_refs=normalized_refs)
        generated_at = utc_now_iso()
        audit_payload = PackAuditPayload(
            request=request,
            openbb_runtime_marker=self.openbb_runtime_marker,
            openbb_extension_version=self.openbb_extension_version,
            run_provider_plan_id=run_plan.run_id,
            call_specs=call_specs,
            attempts=attempts,
            cache_receipts=cache_receipts,
            data_gaps=combined_gaps,
            conflicts=tuple(conflicts),
            readiness=readiness,
            chart_assets=chart_assets,
            raw_refs=raw_refs,
            normalized_refs=normalized_refs,
            normalized_bundle_ref=bundle_ref,
            payload_hash=_pack_hash(
                run_id=request.run_id,
                call_id=request.call_id,
                status=readiness.status.value,
                raw_refs=raw_refs,
                normalized_refs=normalized_refs,
                generated_at=generated_at,
            ),
            generated_at=generated_at,
        )
        return DomainPackResult(
            request=request,
            reader_brief_md=reader_brief,
            compact_facts=compact_facts,
            attempts=attempts,
            cache_receipts=cache_receipts,
            data_gaps=combined_gaps,
            conflicts=tuple(conflicts),
            readiness=readiness,
            chart_assets=chart_assets,
            raw_refs=raw_refs,
            normalized_refs=normalized_refs,
            normalized_bundle_ref=bundle_ref,
            audit_ref=f"audit://{request.run_id}/{request.call_id}/market",
            audit_payload_hash=audit_payload.payload_hash,
            audit_payload=audit_payload,
        )


def _collect_ohlcv_rows(results: Sequence[ProviderResult]) -> list[dict[str, Any]]:
    normalized_rows: list[dict[str, Any]] = []
    for result in results:
        if result.status not in {ProviderStatus.REMOTE_SUCCESS, ProviderStatus.CACHE_HIT, ProviderStatus.SHARED_RESULT}:
            continue
        for row in result.rows:
            normalized = _normalize_ohlcv_row(row=row, result=result)
            if normalized is not None:
                normalized_rows.append(normalized)
    normalized_rows.sort(key=lambda item: item["trade_date"])

    deduped: dict[str, dict[str, Any]] = {}
    for row in normalized_rows:
        trade_date = row["trade_date"]
        if trade_date not in deduped:
            deduped[trade_date] = row
    return [deduped[key] for key in sorted(deduped.keys())]


def _normalize_ohlcv_row(*, row: Mapping[str, Any], result: ProviderResult) -> dict[str, Any] | None:
    mapped: dict[str, Any] = {}
    for field, aliases in _OHLCV_FIELD_ALIASES.items():
        value = _pick_value(row, aliases)
        if value is None:
            continue
        if field == "trade_date":
            trade_date = _normalize_date(value)
            if trade_date is None:
                return None
            mapped[field] = trade_date
            continue
        if field in {"open", "high", "low", "close", "volume", "amount", "change", "pct_change"}:
            number = _to_float(value)
            if number is None:
                continue
            mapped[field] = number
            continue
        mapped[field] = value

    required = ("trade_date", "open", "high", "low", "close", "volume")
    if any(key not in mapped for key in required):
        return None
    mapped["provider"] = result.spec.provider
    mapped["endpoint"] = result.spec.endpoint
    mapped["currency"] = result.spec.params.get("currency") or result.rows[0].get("currency") if result.rows else None
    mapped["timezone"] = result.rows[0].get("timezone") if result.rows else None
    return mapped


def _compute_indicators(ohlcv_rows: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    closes = [float(item["close"]) for item in ohlcv_rows]
    volumes = [float(item["volume"]) for item in ohlcv_rows]
    dates = [str(item["trade_date"]) for item in ohlcv_rows]

    ma5 = _sma(closes, 5)
    ma10 = _sma(closes, 10)
    ma20 = _sma(closes, 20)
    vol_ma5 = _sma(volumes, 5)
    vol_ma20 = _sma(volumes, 20)
    macd, macd_signal, macd_hist = _macd(closes)
    rsi14 = _rsi(closes, 14)
    boll_mid, boll_upper, boll_lower = _bollinger(closes, 20, 2.0)

    indicator_rows: list[dict[str, Any]] = []
    for idx, trade_date in enumerate(dates):
        indicator_rows.append(
            {
                "trade_date": trade_date,
                "ma5": ma5[idx],
                "ma10": ma10[idx],
                "ma20": ma20[idx],
                "vol_ma5": vol_ma5[idx],
                "vol_ma20": vol_ma20[idx],
                "macd": macd[idx],
                "macd_signal": macd_signal[idx],
                "macd_hist": macd_hist[idx],
                "rsi14": rsi14[idx],
                "boll_mid": boll_mid[idx],
                "boll_upper": boll_upper[idx],
                "boll_lower": boll_lower[idx],
            }
        )

    compact_facts: dict[str, Any] = {
        "schema_id": None,
        "ohlcv_row_count": len(ohlcv_rows),
        "latest_close": closes[-1] if closes else None,
        "moving_average": {
            "ma5": ma5[-1] if ma5 else None,
            "ma10": ma10[-1] if ma10 else None,
            "ma20": ma20[-1] if ma20 else None,
        },
        "volume_indicator": {
            "latest_volume": volumes[-1] if volumes else None,
            "vol_ma5": vol_ma5[-1] if vol_ma5 else None,
            "vol_ma20": vol_ma20[-1] if vol_ma20 else None,
        },
        "macd": {
            "macd": macd[-1] if macd else None,
            "signal": macd_signal[-1] if macd_signal else None,
            "hist": macd_hist[-1] if macd_hist else None,
        },
        "rsi14": rsi14[-1] if rsi14 else None,
        "bollinger": {
            "mid": boll_mid[-1] if boll_mid else None,
            "upper": boll_upper[-1] if boll_upper else None,
            "lower": boll_lower[-1] if boll_lower else None,
        },
        "currency": _preferred_currency(ohlcv_rows),
        "timezone": _preferred_timezone(ohlcv_rows),
    }
    return indicator_rows, compact_facts


def _compute_readiness(
    *,
    request: PackRequest,
    call_specs: Sequence[ProviderCallSpec],
    results: Sequence[ProviderResult],
    gaps: Sequence[DataGap],
    chart_assets: Sequence[ChartAsset],
) -> Readiness:
    status_by_key = {result.spec.call_key: result.status for result in results}
    success_by_key = {result.spec.call_key: _counts_as_coverage_success(result, request) for result in results}
    coverage: dict[str, str] = {}
    missing_items: list[str] = []
    blocking_specs: list[str] = []
    grouped_specs: dict[str, list[ProviderCallSpec]] = {}
    for spec in call_specs:
        if spec.coverage_group:
            grouped_specs.setdefault(spec.coverage_group, []).append(spec)
            continue
        if not spec.required:
            continue
        key = f"{spec.provider}:{spec.endpoint}"
        ok = success_by_key.get(spec.call_key, False)
        coverage[key] = "ok" if ok else "missing"
        if not ok:
            missing_items.append(key)
            blocking_specs.append(spec.call_key)

    for group, specs in grouped_specs.items():
        quorum = max((spec.coverage_quorum or 1) for spec in specs)
        ok_count = sum(1 for spec in specs if success_by_key.get(spec.call_key, False))
        coverage[f"group:{group}"] = f"{ok_count}/{quorum}"
        if ok_count < quorum:
            missing_items.append(f"group:{group}")
            blocking_specs.extend(spec.call_key for spec in specs)

    fail_gaps = tuple(gap.gap_id for gap in gaps if gap.severity == GapSeverity.FAIL)
    warn_gaps = tuple(gap.gap_id for gap in gaps if gap.severity != GapSeverity.FAIL)
    chart_ready = all(asset.status == ReadinessStatus.READY for asset in chart_assets)
    blocking_status = any(
        status_by_key.get(call_key) in {ProviderStatus.CREDENTIAL_MISSING, ProviderStatus.LICENSE_BLOCKED, ProviderStatus.RATE_LIMITED}
        for call_key in blocking_specs
    )
    if blocking_status and missing_items:
        status = ReadinessStatus.BLOCKED
    elif fail_gaps and any(gap.reason in _BLOCKING_REASONS for gap in gaps if gap.severity == GapSeverity.FAIL):
        status = ReadinessStatus.BLOCKED
    elif fail_gaps or missing_items:
        status = ReadinessStatus.INSUFFICIENT
    elif missing_items or not chart_ready or warn_gaps:
        status = ReadinessStatus.PARTIAL
    else:
        status = ReadinessStatus.READY

    root_cause: str | None = None
    if status == ReadinessStatus.BLOCKED:
        reason = next((gap.root_cause for gap in gaps if gap.reason in _BLOCKING_REASONS), None)
        root_cause = reason or "存在阻断型资料缺口，需补齐凭证或解除限流/许可限制。"
    elif status == ReadinessStatus.INSUFFICIENT:
        reason = next((gap.root_cause for gap in gaps if gap.severity == GapSeverity.FAIL), None)
        root_cause = reason or "核心行情覆盖不足，当前资料包不可直接用于完整技术分析。"
    elif status == ReadinessStatus.PARTIAL:
        reason = next((asset.root_cause for asset in chart_assets if asset.root_cause), None)
        root_cause = reason or "存在非阻断缺口，建议结合 attempts 与 data gaps 一起解读。"

    missing_domains = ("market",) if status != ReadinessStatus.READY else ()
    return Readiness(
        status=status,
        coverage=coverage,
        required_domains=("market",),
        missing_domains=missing_domains,
        blocking_gap_ids=fail_gaps,
        non_blocking_gap_ids=warn_gaps,
        root_cause=root_cause,
    )


def _build_status_gaps(*, request: PackRequest, results: Sequence[ProviderResult]) -> list[DataGap]:
    gaps: list[DataGap] = []
    reason_map: dict[ProviderStatus, DataGapReason] = {
        ProviderStatus.CREDENTIAL_MISSING: DataGapReason.CREDENTIAL_MISSING,
        ProviderStatus.RATE_LIMITED: DataGapReason.RATE_LIMITED,
        ProviderStatus.EMPTY: DataGapReason.EMPTY,
        ProviderStatus.FIELD_MISSING: DataGapReason.FIELD_MISSING,
        ProviderStatus.SCHEMA_INVALID: DataGapReason.SCHEMA_INVALID,
        ProviderStatus.CACHE_ERROR: DataGapReason.CACHE_ERROR,
        ProviderStatus.CACHED_EMPTY: DataGapReason.CACHED_EMPTY,
        ProviderStatus.CACHE_STALE: DataGapReason.STALE_CACHE_UNUSABLE,
        ProviderStatus.EVIDENCE_WRITE_FAILED: DataGapReason.EVIDENCE_WRITE_FAILED,
        ProviderStatus.LICENSE_BLOCKED: DataGapReason.LICENSE_BLOCKED,
        ProviderStatus.SKIPPED_NOT_CONFIGURED: DataGapReason.SOURCE_NOT_CONFIGURED,
        ProviderStatus.REMOTE_ERROR: DataGapReason.PROVIDER_UNAVAILABLE,
    }
    for result in results:
        reason = reason_map.get(result.status)
        if reason is None:
            continue
        severity = GapSeverity.WARN
        if reason in {DataGapReason.CREDENTIAL_MISSING, DataGapReason.LICENSE_BLOCKED, DataGapReason.EVIDENCE_WRITE_FAILED}:
            severity = GapSeverity.FAIL
        if reason == DataGapReason.RATE_LIMITED:
            severity = GapSeverity.WARN
        message = result.error_message or result.attempt.error_message or result.status.value
        gaps.append(
            DataGap(
                gap_id=f"{request.run_id}:{request.call_id}:{result.spec.call_key}:{reason.value}",
                domain=request.domain,
                severity=severity,
                reason=reason,
                field_path=f"{request.domain.value}.{result.spec.endpoint}",
                provider_candidates=(result.spec.provider,),
                attempt_ids=(result.attempt.attempt_id,),
                root_cause=message,
                next_action=_next_action_for_reason(reason),
            )
        )
    return gaps


def _build_chart_gaps(*, request: PackRequest, chart_assets: Sequence[ChartAsset]) -> list[DataGap]:
    gaps: list[DataGap] = []
    for asset in chart_assets:
        if asset.status == ReadinessStatus.READY:
            continue
        severity = GapSeverity.FAIL if asset.status == ReadinessStatus.INSUFFICIENT else GapSeverity.WARN
        gaps.append(
            DataGap(
                gap_id=f"{request.run_id}:{request.call_id}:{asset.chart_id}:chart",
                domain=request.domain,
                severity=severity,
                reason=DataGapReason.FIELD_MISSING,
                field_path=f"charts.{asset.kind}",
                provider_candidates=(),
                attempt_ids=(),
                root_cause=asset.root_cause or "图表缺失，原因未明确。",
                next_action="检查 OHLCV 覆盖、指标输入与图表渲染输出。",
            )
        )
    return gaps


def _merge_gaps(gaps: Sequence[DataGap]) -> tuple[DataGap, ...]:
    dedup: dict[str, DataGap] = {}
    for gap in gaps:
        dedup[gap.gap_id] = gap
    return tuple(dedup.values())


def _annotate_hk_chart_root_cause(*, chart_assets: Sequence[ChartAsset], results: Sequence[ProviderResult]) -> tuple[ChartAsset, ...]:
    causes: list[str] = []
    for result in results:
        if result.status in {ProviderStatus.REMOTE_SUCCESS, ProviderStatus.CACHE_HIT, ProviderStatus.SHARED_RESULT}:
            continue
        text = result.error_message or result.status.value
        causes.append(f"{result.spec.provider}/{result.spec.endpoint}：{text}")
    if not causes:
        return tuple(chart_assets)
    tail = "；".join(causes)
    return tuple(
        replace(asset, root_cause=f"{asset.root_cause or '图表不可用'}（港股图表根因：{tail}）")
        if asset.status != ReadinessStatus.READY
        else asset
        for asset in chart_assets
    )


def _counts_as_coverage_success(result: ProviderResult, request: PackRequest) -> bool:
    if result.status == ProviderStatus.REMOTE_SUCCESS:
        return result.row_count > 0
    if result.status == ProviderStatus.SHARED_RESULT:
        return bool(result.normalized_ref)
    if result.status != ProviderStatus.CACHE_HIT:
        return False
    if request.domain.value in request.freshness_policy.require_remote_for_domains:
        return False
    receipt = result.cache_receipt
    return bool(receipt and receipt.normalized_ref and receipt.hit and not receipt.stale and not receipt.cached_empty)


def _render_reader_brief(
    *,
    request: PackRequest,
    readiness: Readiness,
    ohlcv_rows: Sequence[Mapping[str, Any]],
    compact_facts: Mapping[str, Any],
    results: Sequence[ProviderResult],
    gaps: Sequence[DataGap],
    chart_assets: Sequence[ChartAsset],
) -> str:
    lines: list[str] = [
        f"## 数据资料包：{request.ticker} / {request.market.value} / {request.domain.value}",
        "",
        "### 资料就绪度",
        f"- 当前状态：{_READINESS_TEXT.get(readiness.status, readiness.status.value)}",
    ]
    if readiness.root_cause:
        lines.append(f"- 根因：{readiness.root_cause}")
    if readiness.coverage:
        missing_count = sum(1 for value in readiness.coverage.values() if value == "missing" or value.startswith("0/"))
        lines.append(f"- 覆盖概览：完成 {len(readiness.coverage) - missing_count} 项检查，仍有 {missing_count} 项缺口。")
    if request.market == Market.CRYPTO:
        lines.append(
            "- 加密市场覆盖边界：本次资料就绪度只代表已列明来源的价格历史、"
            "本地技术指标和图表资产；如果来源列表没有 BB/CoinGlass、资金费率、"
            "OI、多空比、清算、链上、宏观或 AHR999 的成功记录，这些指标均视为未覆盖，"
            "不得写成已验证事实。"
        )

    lines.extend(
        [
            "",
            "### 核心事实材料",
            f"- OHLCV 行数：{len(ohlcv_rows)}（交易日升序）",
            f"- 最新收盘：{_fmt_num(compact_facts.get('latest_close'))}",
            (
                "- 均线：MA5="
                f"{_fmt_num(_nested(compact_facts, 'moving_average', 'ma5'))}，"
                f"MA10={_fmt_num(_nested(compact_facts, 'moving_average', 'ma10'))}，"
                f"MA20={_fmt_num(_nested(compact_facts, 'moving_average', 'ma20'))}"
            ),
            (
                "- 成交量：最新="
                f"{_fmt_num(_nested(compact_facts, 'volume_indicator', 'latest_volume'))}，"
                f"VOL_MA5={_fmt_num(_nested(compact_facts, 'volume_indicator', 'vol_ma5'))}，"
                f"VOL_MA20={_fmt_num(_nested(compact_facts, 'volume_indicator', 'vol_ma20'))}"
            ),
            (
                "- MACD："
                f"macd={_fmt_num(_nested(compact_facts, 'macd', 'macd'))}，"
                f"signal={_fmt_num(_nested(compact_facts, 'macd', 'signal'))}，"
                f"hist={_fmt_num(_nested(compact_facts, 'macd', 'hist'))}"
            ),
            f"- RSI(14)：{_fmt_num(compact_facts.get('rsi14'))}",
            (
                "- Bollinger："
                f"mid={_fmt_num(_nested(compact_facts, 'bollinger', 'mid'))}，"
                f"upper={_fmt_num(_nested(compact_facts, 'bollinger', 'upper'))}，"
                f"lower={_fmt_num(_nested(compact_facts, 'bollinger', 'lower'))}"
            ),
            f"- 币种/时区：{compact_facts.get('currency') or request.currency} / {compact_facts.get('timezone') or _default_timezone(request.market)}",
        ]
    )

    lines.extend(["", "### 图表和指标"])
    for asset in chart_assets:
        line = f"- {asset.title}：{_READINESS_TEXT.get(asset.status, asset.status.value)}"
        if asset.root_cause:
            line = f"{line}（{asset.root_cause}）"
        lines.append(line)

    lines.extend(["", "### 来源和缺口"])
    for result in results:
        cache_text = ""
        if result.cache_receipt is not None:
            cache_text = f"，缓存状态：{_STATUS_TEXT.get(result.cache_receipt.status, result.cache_receipt.status.value)}"
        lines.append(
            "- "
            f"{result.spec.provider}/{result.spec.endpoint}：{_STATUS_TEXT.get(result.status, result.status.value)}"
            f"（资料新鲜度：{_FRESHNESS_TEXT.get(result.freshness, result.freshness.value)}{cache_text}）"
        )
    if gaps:
        for gap in gaps:
            lines.append(f"- {_GAP_REASON_TEXT.get(gap.reason, gap.reason.value)}：{gap.root_cause}")
    else:
        lines.append("- 暂无缺口。")

    lines.extend(
        [
            "",
            "### 分析时必须注意",
            "- 缓存命中不等于远端实时成功，需结合来源尝试和资料就绪度解读。",
            "- 缺 key、限流、空返回、字段缺失已写入缺口；禁止把缺口信息改写成成功覆盖。",
            "- 本资料包只提供事实材料与技术指标，不直接生成投资结论。",
            "",
        ]
    )
    return "\n".join(lines)


def _pack_hash(
    *,
    run_id: str,
    call_id: str,
    status: str,
    raw_refs: Sequence[str],
    normalized_refs: Sequence[str],
    generated_at: str,
) -> str:
    payload = {
        "run_id": run_id,
        "call_id": call_id,
        "status": status,
        "raw_refs": list(raw_refs),
        "normalized_refs": list(normalized_refs),
        "generated_at": generated_at,
    }
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _normalized_bundle_ref(*, request: PackRequest, normalized_refs: Sequence[str]) -> str | None:
    if not normalized_refs:
        return None
    raw = "|".join((request.run_id, request.call_id, *sorted(normalized_refs)))
    return "bundle://sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _next_action_for_reason(reason: DataGapReason) -> str:
    if reason == DataGapReason.CREDENTIAL_MISSING:
        return "配置对应 provider 的 credential 后重试。"
    if reason == DataGapReason.RATE_LIMITED:
        return "降低并发并等待限流窗口恢复后重试。"
    if reason == DataGapReason.LICENSE_BLOCKED:
        return "确认许可证和导出策略，解除限制后重试。"
    if reason == DataGapReason.SCHEMA_INVALID:
        return "检查 provider 响应结构和字段映射。"
    if reason == DataGapReason.FIELD_MISSING:
        return "检查关键字段缺失并补充替代来源。"
    if reason == DataGapReason.CACHE_ERROR:
        return "排查缓存读写错误并触发远端重取。"
    if reason == DataGapReason.CACHED_EMPTY:
        return "缓存命中空数据，需要远端重取确认。"
    if reason == DataGapReason.STALE_CACHE_UNUSABLE:
        return "缓存已过期且不可用，需远端重取。"
    if reason == DataGapReason.EVIDENCE_WRITE_FAILED:
        return "修复 evidence 写入后重跑，避免无证据成功。"
    if reason == DataGapReason.SOURCE_NOT_CONFIGURED:
        return "补齐 provider 配置后重试。"
    if reason == DataGapReason.PROVIDER_UNAVAILABLE:
        return "检查 provider 可达性和上游稳定性。"
    return "补齐资料后重试。"


def _pick_value(row: Mapping[str, Any], aliases: Sequence[str]) -> Any:
    for alias in aliases:
        if alias in row:
            return row[alias]
    return None


def _normalize_date(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if not text:
        return None
    if "T" in text:
        text = text.split("T", 1)[0]
    if " " in text:
        text = text.split(" ", 1)[0]
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _to_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    if text.endswith("%"):
        text = text[:-1].strip()
    try:
        return float(text)
    except ValueError:
        return None


def _sma(values: Sequence[float], window: int) -> list[float | None]:
    out: list[float | None] = []
    for idx in range(len(values)):
        if idx + 1 < window:
            out.append(None)
            continue
        segment = values[idx + 1 - window : idx + 1]
        out.append(sum(segment) / float(window))
    return out


def _ema(values: Sequence[float], period: int) -> list[float]:
    if not values:
        return []
    k = 2.0 / (period + 1.0)
    out = [values[0]]
    for value in values[1:]:
        out.append((value * k) + (out[-1] * (1.0 - k)))
    return out


def _macd(values: Sequence[float]) -> tuple[list[float | None], list[float | None], list[float | None]]:
    if not values:
        return [], [], []
    fast = _ema(values, 12)
    slow = _ema(values, 26)
    macd = [fast[idx] - slow[idx] for idx in range(len(values))]
    signal = _ema(macd, 9)
    hist = [macd[idx] - signal[idx] for idx in range(len(values))]
    return _nullable(macd, 26), _nullable(signal, 26 + 9 - 1), _nullable(hist, 26 + 9 - 1)


def _rsi(values: Sequence[float], period: int) -> list[float | None]:
    if not values:
        return []
    gains: list[float] = [0.0]
    losses: list[float] = [0.0]
    for idx in range(1, len(values)):
        delta = values[idx] - values[idx - 1]
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))

    out: list[float | None] = []
    avg_gain = 0.0
    avg_loss = 0.0
    for idx in range(len(values)):
        if idx < period:
            out.append(None)
            avg_gain += gains[idx]
            avg_loss += losses[idx]
            continue
        if idx == period:
            avg_gain /= float(period)
            avg_loss /= float(period)
        else:
            avg_gain = ((avg_gain * (period - 1)) + gains[idx]) / float(period)
            avg_loss = ((avg_loss * (period - 1)) + losses[idx]) / float(period)
        if avg_loss == 0:
            out.append(100.0)
            continue
        rs = avg_gain / avg_loss
        out.append(100.0 - (100.0 / (1.0 + rs)))
    return out


def _bollinger(values: Sequence[float], window: int, std_factor: float) -> tuple[list[float | None], list[float | None], list[float | None]]:
    mid = _sma(values, window)
    upper: list[float | None] = []
    lower: list[float | None] = []
    for idx in range(len(values)):
        if idx + 1 < window:
            upper.append(None)
            lower.append(None)
            continue
        segment = values[idx + 1 - window : idx + 1]
        avg = sum(segment) / float(window)
        variance = sum((item - avg) ** 2 for item in segment) / float(window)
        std = variance ** 0.5
        upper.append(avg + (std * std_factor))
        lower.append(avg - (std * std_factor))
    return mid, upper, lower


def _nullable(values: Sequence[float], warmup: int) -> list[float | None]:
    out: list[float | None] = []
    for idx, value in enumerate(values):
        out.append(value if idx + 1 >= warmup else None)
    return out


def _preferred_currency(rows: Sequence[Mapping[str, Any]]) -> str | None:
    for row in rows:
        value = row.get("currency")
        if value:
            return str(value)
    return None


def _preferred_timezone(rows: Sequence[Mapping[str, Any]]) -> str | None:
    for row in rows:
        value = row.get("timezone")
        if value:
            return str(value)
    return None


def _default_timezone(market: Market) -> str:
    if market == Market.HK:
        return "Asia/Hong_Kong"
    if market == Market.US:
        return "America/New_York"
    if market == Market.CRYPTO:
        return "UTC"
    return "Asia/Shanghai"


def _default_currency(market: Market, requested_currency: str) -> str:
    if market == Market.HK:
        return "HKD"
    if market == Market.US:
        return "USD"
    if market == Market.CRYPTO:
        return "USD"
    return requested_currency or "CNY"


def _fmt_num(value: Any) -> str:
    number = _to_float(value)
    if number is None:
        return "缺失"
    if abs(number) >= 1000:
        return f"{number:,.2f}"
    return f"{number:.4f}"


def _nested(source: Mapping[str, Any], key: str, sub_key: str) -> Any:
    value = source.get(key)
    if isinstance(value, Mapping):
        return value.get(sub_key)
    return None


def make_provider_attempt(
    *,
    request: PackRequest,
    spec: ProviderCallSpec,
    status: ProviderStatus,
    row_count: int | None,
    raw_ref: str | None,
    normalized_ref: str | None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> ProviderAttempt:
    now = utc_now_iso()
    from_cache = status == ProviderStatus.CACHE_HIT
    cache_status = status if status in {
        ProviderStatus.CACHE_HIT,
        ProviderStatus.CACHE_MISS,
        ProviderStatus.CACHE_STALE,
        ProviderStatus.CACHE_ERROR,
        ProviderStatus.CACHED_EMPTY,
    } else None
    return ProviderAttempt(
        attempt_id=f"{request.run_id}:{request.call_id}:{spec.call_key}:{status.value}",
        run_id=request.run_id,
        call_id=request.call_id,
        worker_id=request.worker_id,
        pack=request.domain.value,
        provider=spec.provider,
        adapter_id=spec.adapter_id,
        adapter_kind="project_extension",
        provider_kind=spec.provider_kind,
        provider_config_version=spec.provider_config_version,
        endpoint=spec.endpoint,
        source_role=spec.source_role,
        started_at=now,
        finished_at=now,
        status=status,
        required=spec.required,
        attempt_required=spec.attempt_required,
        coverage_group=spec.coverage_group,
        coverage_quorum=spec.coverage_quorum,
        priority_source=PrioritySource.SYSTEM_DEFAULT,
        user_preferred=False,
        from_cache=from_cache,
        cache_status=cache_status,
        single_flight_role="none",
        shared_from_attempt_id=None,
        latency_ms=0,
        row_count=row_count,
        raw_ref=raw_ref,
        normalized_ref=normalized_ref,
        error_code=error_code,
        error_message=error_message,
        schema_id=spec.expected_schema_id,
        license_note="approved",
    )


def make_provider_result(
    *,
    request: PackRequest,
    spec: ProviderCallSpec,
    status: ProviderStatus,
    rows: Sequence[Mapping[str, Any]],
    freshness: FreshnessStatus,
    raw_ref: str | None = None,
    normalized_ref: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> ProviderResult:
    attempt = make_provider_attempt(
        request=request,
        spec=spec,
        status=status,
        row_count=len(rows),
        raw_ref=raw_ref,
        normalized_ref=normalized_ref,
        error_code=error_code,
        error_message=error_message,
    )
    return ProviderResult(
        spec=spec,
        status=status,
        request_id=None,
        requested_at=utc_now_iso(),
        latency_ms=0,
        source_role=spec.source_role,
        freshness=freshness,
        license_note="approved",
        raw_ref=raw_ref,
        normalized_ref=normalized_ref,
        rows=tuple(dict(item) for item in rows),
        row_count=len(rows),
        cache_receipt=None,
        attempt=attempt,
        missing_fields=(),
        error_code=error_code,
        error_message=error_message,
    )
