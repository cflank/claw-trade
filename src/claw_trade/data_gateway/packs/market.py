from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime
import hashlib
import json
from typing import Any, Mapping, Sequence

from claw_trade.data_gateway.analysis.crypto_lens import (
    CryptoLensAnalysisFailed,
    CryptoLensAnalysisEvidenceStore,
    CryptoLensAnalysisResult,
    analyze_openbb_crypto_lens_bundle,
)
from claw_trade.data_gateway.models import (
    ChartAsset,
    Conflict,
    CryptoDomainBundle,
    DataGap,
    DataGapReason,
    DomainReadiness,
    DomainPackResult,
    FreshnessStatus,
    GapSeverity,
    Market,
    NormalizedCryptoMarketBundle,
    PackAuditPayload,
    PackDomain,
    PackRequest,
    PrioritySource,
    ProviderAttempt,
    ProviderCallSpec,
    ProviderResult,
    ProviderSourceRef,
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
    ProviderStatus.WAREHOUSE_HIT: "使用主仓库数据",
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
    ProviderStatus.NOT_APPLICABLE: "本次不适用",
}

_FRESHNESS_TEXT: dict[FreshnessStatus, str] = {
    FreshnessStatus.FRESH_REMOTE: "远端实时获取",
    FreshnessStatus.FRESH_CACHE: "有效缓存",
    FreshnessStatus.FRESH_WAREHOUSE: "主仓库有效数据",
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
    DataGapReason.NOT_APPLICABLE: "本次不适用",
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
        crypto_lens_evidence_store: CryptoLensAnalysisEvidenceStore | None = None,
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
        crypto_lens_result: CryptoLensAnalysisResult | None = None
        analysis_evidence_refs: tuple[str, ...] = ()
        if request.market == Market.CRYPTO:
            bundle, bundle_gaps = _build_crypto_lens_bundle(
                request=request,
                results=market_results,
                ohlcv_rows=ohlcv_rows,
                indicator_rows=indicator_rows,
                compact_facts=compact_facts,
                gaps=combined_gaps,
                conflicts=conflicts,
            )
            combined_gaps = _merge_gaps(tuple(combined_gaps) + bundle_gaps)
            if bundle is not None:
                try:
                    crypto_lens_result = analyze_openbb_crypto_lens_bundle(
                        bundle,
                        evidence_store=crypto_lens_evidence_store,
                    )
                    if crypto_lens_result.analysis_evidence_ref:
                        analysis_evidence_refs = (crypto_lens_result.analysis_evidence_ref,)
                    combined_gaps = _merge_gaps(tuple(combined_gaps) + crypto_lens_result.data_gaps)
                except Exception as exc:  # noqa: BLE001
                    evidence_ref = exc.analysis_evidence_ref if isinstance(exc, CryptoLensAnalysisFailed) else None
                    if evidence_ref:
                        analysis_evidence_refs = (evidence_ref,)
                    combined_gaps = _merge_gaps(
                        tuple(combined_gaps)
                        + (
                            _crypto_lens_failure_gap(request=request, reason=str(exc)),
                        )
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
            crypto_lens_result=crypto_lens_result,
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
                analysis_evidence_refs=analysis_evidence_refs,
                generated_at=generated_at,
            ),
            generated_at=generated_at,
            analysis_evidence_refs=analysis_evidence_refs,
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
        if result.status not in {
            ProviderStatus.REMOTE_SUCCESS,
            ProviderStatus.CACHE_HIT,
            ProviderStatus.WAREHOUSE_HIT,
            ProviderStatus.SHARED_RESULT,
        }:
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
    success_count_by_group: dict[str, int] = {}
    for result in results:
        group = result.spec.coverage_group
        if group and _counts_as_coverage_success(result, request):
            success_count_by_group[group] = success_count_by_group.get(group, 0) + 1
    attempt_call_key = {result.attempt.attempt_id: result.spec.call_key for result in results}
    call_keys_by_provider: dict[str, set[str]] = {}
    for spec in call_specs:
        call_keys_by_provider.setdefault(spec.provider, set()).add(spec.call_key)
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
        ok_count = success_count_by_group.get(group, 0)
        coverage[f"group:{group}"] = f"{ok_count}/{quorum}"
        if ok_count < quorum:
            missing_items.append(f"group:{group}")
            blocking_specs.extend(spec.call_key for spec in specs)

    uncovered_call_keys = set(blocking_specs)
    fail_gap_items = tuple(gap for gap in gaps if gap.severity == GapSeverity.FAIL)
    non_blocking_reclassified_gap_ids = tuple(
        gap.gap_id
        for gap in fail_gap_items
        if _is_covered_blocking_gap(
            gap=gap,
            uncovered_call_keys=uncovered_call_keys,
            attempt_call_key=attempt_call_key,
            call_keys_by_provider=call_keys_by_provider,
        )
    )
    fail_gap_items = tuple(gap for gap in fail_gap_items if gap.gap_id not in set(non_blocking_reclassified_gap_ids))
    fail_gaps = tuple(gap.gap_id for gap in fail_gap_items)
    warn_gaps = tuple(
        dict.fromkeys(
            [gap.gap_id for gap in gaps if gap.severity != GapSeverity.FAIL] + list(non_blocking_reclassified_gap_ids)
        )
    )
    chart_ready = all(asset.status == ReadinessStatus.READY for asset in chart_assets)
    blocking_status = any(
        status_by_key.get(call_key) in {ProviderStatus.CREDENTIAL_MISSING, ProviderStatus.LICENSE_BLOCKED, ProviderStatus.RATE_LIMITED}
        for call_key in blocking_specs
    )
    if blocking_status and missing_items:
        status = ReadinessStatus.BLOCKED
    elif fail_gaps and any(gap.reason in _BLOCKING_REASONS for gap in fail_gap_items):
        status = ReadinessStatus.BLOCKED
    elif fail_gaps or missing_items:
        status = ReadinessStatus.INSUFFICIENT
    elif missing_items or not chart_ready or warn_gaps:
        status = ReadinessStatus.PARTIAL
    else:
        status = ReadinessStatus.READY

    root_cause: str | None = None
    if status == ReadinessStatus.BLOCKED:
        reason = next((gap.root_cause for gap in fail_gap_items if gap.reason in _BLOCKING_REASONS), None)
        root_cause = reason or "存在阻断型资料缺口，需补齐凭证或解除限流/许可限制。"
    elif status == ReadinessStatus.INSUFFICIENT:
        reason = next((gap.root_cause for gap in fail_gap_items), None)
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


def _is_covered_blocking_gap(
    *,
    gap: DataGap,
    uncovered_call_keys: set[str],
    attempt_call_key: Mapping[str, str],
    call_keys_by_provider: Mapping[str, set[str]],
) -> bool:
    if gap.reason not in _BLOCKING_REASONS:
        return False
    related_call_keys: set[str] = set()
    for attempt_id in gap.attempt_ids:
        call_key = attempt_call_key.get(attempt_id)
        if call_key:
            related_call_keys.add(call_key)
    for provider in gap.provider_candidates:
        related_call_keys.update(call_keys_by_provider.get(provider, set()))
    if not related_call_keys:
        return False
    return related_call_keys.isdisjoint(uncovered_call_keys)


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
        ProviderStatus.NOT_APPLICABLE: DataGapReason.NOT_APPLICABLE,
    }
    for result in results:
        reason = reason_map.get(result.status)
        if reason is None:
            continue
        severity = GapSeverity.WARN
        if reason in {DataGapReason.CREDENTIAL_MISSING, DataGapReason.LICENSE_BLOCKED, DataGapReason.EVIDENCE_WRITE_FAILED}:
            severity = GapSeverity.FAIL
        if reason == DataGapReason.NOT_APPLICABLE:
            severity = GapSeverity.INFO
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
    if result.status == ProviderStatus.WAREHOUSE_HIT:
        return bool(result.normalized_ref and result.row_count > 0)
    if result.status == ProviderStatus.SHARED_RESULT:
        return bool(result.normalized_ref)
    if result.status != ProviderStatus.CACHE_HIT:
        return False
    if request.domain.value in request.freshness_policy.require_remote_for_domains:
        return False
    receipt = result.cache_receipt
    if receipt is not None:
        return bool(receipt.normalized_ref and receipt.hit and not receipt.stale and not receipt.cached_empty)
    return bool(result.normalized_ref and result.row_count > 0)


def _render_reader_brief(
    *,
    request: PackRequest,
    readiness: Readiness,
    ohlcv_rows: Sequence[Mapping[str, Any]],
    compact_facts: Mapping[str, Any],
    results: Sequence[ProviderResult],
    gaps: Sequence[DataGap],
    chart_assets: Sequence[ChartAsset],
    crypto_lens_result: CryptoLensAnalysisResult | None = None,
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
            "OpenBB 归一化资料、CryptoLens 指标分析和图表资产；如果来源列表没有资金费率、"
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

    if request.market == Market.CRYPTO:
        lines.extend(_render_crypto_lens_brief(crypto_lens_result))

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


def _render_crypto_lens_brief(result: CryptoLensAnalysisResult | None) -> list[str]:
    lines = ["", "### CryptoLens 指标分析"]
    if result is None:
        lines.extend(
            [
                "- 状态：未生成。",
                "- 说明：OpenBB 来源尝试和证据引用已保留，但本次没有可用的 CryptoLens 指标分析结果；不得补写资金费率、OI、清算簇、链上、宏观或 AHR999 结论。",
            ]
        )
        return lines

    lines.extend(
        [
            f"- 数据状态：{_READINESS_TEXT.get(result.readiness.status, result.readiness.status.value)}；分析状态：{result.status}。",
            f"- 来源摘要：CryptoLens 仅消费 OpenBB 归一化资料，本次引用 {len(result.input_normalized_refs)} 个 normalized ref。",
            "- 指标覆盖："
            + "；".join(
                f"{_crypto_lens_section_title(key)}={_analysis_status_text(value.get('status'))}"
                for key, value in result.indicator_coverage.items()
            ),
        ]
    )
    sections = (
        ("价格/多周期结构", result.market_structure),
        ("技术形态", result.technical_patterns),
        ("衍生品拥挤度", result.derivatives_context),
        ("清算压力", result.liquidation_context),
        ("链上指标", result.onchain_context),
        ("宏观约束", result.macro_context),
        ("AHR999", result.ahr999_context),
    )
    for title, section in sections:
        line = f"- {title}：{_analysis_status_text(section.status.value)}；{section.summary}"
        if section.notes:
            line += " 备注：" + "；".join(section.notes)
        if section.gap_ids:
            line += " 缺口：" + "、".join(section.gap_ids)
        lines.append(line)

    chart_text = _READINESS_TEXT.get(result.readiness.status, result.readiness.status.value)
    lines.append(f"- 图表 readiness：{chart_text}；图表结论须以资料包图表条目为准。")
    if result.conflicts:
        lines.append("- conflicts：" + "；".join(f"{item.field_path}:{item.resolution}" for item in result.conflicts))
    else:
        lines.append("- conflicts：暂无。")
    if result.data_gaps:
        lines.append("- data gaps：" + "；".join(f"{item.field_path}:{item.root_cause}" for item in result.data_gaps))
    else:
        lines.append("- data gaps：暂无。")
    invalidation = result.conditional_trade_framework.get("invalidation_gap_ids", ())
    invalidation_text = "、".join(str(item) for item in invalidation) if invalidation else "暂无关键失效缺口"
    lines.append(
        "- 条件化观察：上述结构、技术、衍生品与清算状态只用于 market analyst 形成市场解释；"
        f"若这些缺口未补齐，相关观察失效：{invalidation_text}。"
    )
    return lines


def _crypto_lens_section_title(key: str) -> str:
    return {
        "market_structure": "价格结构",
        "technical_patterns": "技术形态",
        "derivatives_context": "衍生品",
        "liquidation_context": "清算",
        "onchain_context": "链上",
        "macro_context": "宏观",
        "ahr999_context": "AHR999",
    }.get(key, key)


def _analysis_status_text(value: object) -> str:
    return {
        "ready": "就绪",
        "partial": "部分覆盖",
        "gap": "缺口",
        "not_applicable": "不适用",
        "insufficient": "资料不足",
    }.get(str(value), str(value))


def _build_crypto_lens_bundle(
    *,
    request: PackRequest,
    results: Sequence[ProviderResult],
    ohlcv_rows: Sequence[Mapping[str, Any]],
    indicator_rows: Sequence[Mapping[str, Any]],
    compact_facts: Mapping[str, Any],
    gaps: Sequence[DataGap],
    conflicts: Sequence[Conflict],
) -> tuple[NormalizedCryptoMarketBundle | None, tuple[DataGap, ...]]:
    if request.market != Market.CRYPTO:
        return None, ()

    latest = ohlcv_rows[-1] if ohlcv_rows else {}
    ohlcv_payload = _crypto_ohlcv_payload(ohlcv_rows=ohlcv_rows, indicator_rows=indicator_rows, compact_facts=compact_facts)
    domain_payloads: dict[str, Mapping[str, Any] | None] = {
        "market": _crypto_market_payload(latest),
        "ohlcv": ohlcv_payload,
        "derivatives": _crypto_endpoint_payload(results, "futures_oi_funding", _crypto_derivatives_payload),
        "liquidation_map": _crypto_endpoint_payload(results, "liquidation_heatmap", _crypto_liquidation_payload),
        "onchain": _crypto_endpoint_payload(results, "onchain_signals", _crypto_onchain_payload),
        "macro": _crypto_endpoint_payload(results, "macro_regime", _crypto_macro_payload),
        "events": _crypto_endpoint_payload(results, "catalyst_events", lambda row: dict(row)),
        "ahr999": _crypto_endpoint_payload(results, "ahr999_index", _crypto_ahr999_payload),
    }
    domain_status = {
        "market": _crypto_domain_status_from_payload(domain_payloads["market"], endpoint="crypto_price_historical", results=results),
        "ohlcv": _crypto_ohlcv_status(ohlcv_payload, results),
        "derivatives": _crypto_domain_status_from_payload(domain_payloads["derivatives"], endpoint="futures_oi_funding", results=results),
        "liquidation_map": _crypto_domain_status_from_payload(domain_payloads["liquidation_map"], endpoint="liquidation_heatmap", results=results),
        "onchain": _crypto_domain_status_from_payload(domain_payloads["onchain"], endpoint="onchain_signals", results=results),
        "macro": _crypto_domain_status_from_payload(domain_payloads["macro"], endpoint="macro_regime", results=results),
        "events": _crypto_domain_status_from_payload(domain_payloads["events"], endpoint="catalyst_events", results=results),
        "ahr999": _crypto_ahr999_status(request=request, payload=domain_payloads["ahr999"], results=results),
    }
    partial_gaps = _crypto_partial_domain_gaps(request=request, domain_payloads=domain_payloads)
    if any(gap.field_path.startswith("onchain.") for gap in partial_gaps) and domain_status["onchain"] == DomainReadiness.READY:
        domain_status["onchain"] = DomainReadiness.PARTIAL
    bundle_gaps = _merge_gaps(_crypto_domain_gaps(request=request, domain_status=domain_status) + partial_gaps)
    combined_gaps = _merge_gaps(tuple(gaps) + bundle_gaps)
    source_refs = tuple(
        ProviderSourceRef(
            ref_id=f"{result.spec.provider}:{result.spec.endpoint}:{result.attempt.attempt_id}",
            provider=result.spec.provider,
            adapter_id=result.spec.adapter_id,
            endpoint=result.spec.endpoint,
            source_role=result.spec.source_role,
            status=result.status,
            normalized_ref=result.normalized_ref,
        )
        for result in results
    )
    attempt_refs = tuple(result.attempt.attempt_id for result in results)
    raw_refs = tuple(result.raw_ref for result in results if result.raw_ref)
    normalized_refs = tuple(result.normalized_ref for result in results if result.normalized_ref)
    bundle = NormalizedCryptoMarketBundle(
        run_id=request.run_id,
        call_id=request.call_id,
        ticker=request.ticker,
        market=Market.CRYPTO,
        quote=request.currency or "USDT",
        as_of=request.current_date,
        start_date=request.start_date,
        end_date=request.end_date,
        freshness=_crypto_bundle_freshness(results),
        domains=CryptoDomainBundle(
            market=domain_payloads["market"],
            ohlcv=domain_payloads["ohlcv"],
            derivatives=domain_payloads["derivatives"],
            liquidation_map=domain_payloads["liquidation_map"],
            onchain=domain_payloads["onchain"],
            macro=domain_payloads["macro"],
            events=domain_payloads["events"],
            ahr999=domain_payloads["ahr999"],
        ),
        domain_status=domain_status,
        data_gaps=combined_gaps,
        conflicts=tuple(conflicts),
        source_refs=source_refs,
        attempt_refs=attempt_refs,
        raw_refs=raw_refs,
        normalized_refs=normalized_refs,
    )
    return bundle, bundle_gaps


def _crypto_market_payload(latest: Mapping[str, Any]) -> Mapping[str, Any] | None:
    price = _to_float(latest.get("close"))
    if price is None:
        return None
    return {
        "price": price,
        "volume_24h": _to_float(latest.get("volume")),
        "currency": latest.get("currency"),
        "timezone": latest.get("timezone"),
    }


def _crypto_ohlcv_payload(
    *,
    ohlcv_rows: Sequence[Mapping[str, Any]],
    indicator_rows: Sequence[Mapping[str, Any]],
    compact_facts: Mapping[str, Any],
) -> Mapping[str, Any] | None:
    if not ohlcv_rows:
        return None
    latest_indicators = indicator_rows[-1] if indicator_rows else {}
    return {
        "timeframe": "1d",
        "rows": len(ohlcv_rows),
        "candles": tuple(
            {
                "date": row.get("trade_date"),
                "open": row.get("open"),
                "high": row.get("high"),
                "low": row.get("low"),
                "close": row.get("close"),
                "volume": row.get("volume"),
            }
            for row in ohlcv_rows
        ),
        "indicators": {
            "rsi": compact_facts.get("rsi14"),
            "macd_hist": latest_indicators.get("macd_hist"),
        },
    }


def _crypto_endpoint_payload(
    results: Sequence[ProviderResult],
    endpoint: str,
    mapper: Any,
) -> Mapping[str, Any] | None:
    row = _first_success_row(results, endpoint=endpoint)
    if row is None:
        return None
    return mapper(row)


def _first_success_row(results: Sequence[ProviderResult], endpoint: str | None = None) -> Mapping[str, Any] | None:
    for result in results:
        if endpoint is not None and result.spec.endpoint != endpoint:
            continue
        if result.status not in {ProviderStatus.REMOTE_SUCCESS, ProviderStatus.CACHE_HIT, ProviderStatus.SHARED_RESULT}:
            continue
        if result.rows:
            return result.rows[-1]
    return None


def _crypto_derivatives_payload(row: Mapping[str, Any]) -> Mapping[str, Any]:
    return {
        "funding": _metric_value(row.get("funding") or row.get("funding_rate") or row.get("funding_rates")),
        "oi": _metric_value(row.get("oi") or row.get("open_interest")),
        "long_short_ratio": _metric_value(row.get("long_short_ratio")),
        "cvd_proxy": _metric_value(row.get("cvd_proxy")),
    }


def _crypto_liquidation_payload(row: Mapping[str, Any]) -> Mapping[str, Any]:
    largest = row.get("largest_cluster")
    if not isinstance(largest, Mapping):
        clusters = row.get("largest_clusters")
        if isinstance(clusters, Sequence) and not isinstance(clusters, (str, bytes, bytearray)):
            largest = next((item for item in clusters if isinstance(item, Mapping)), None)
    if not isinstance(largest, Mapping):
        return {}
    return {
        "largest_cluster": {
            "price": _metric_value(largest.get("price") or largest.get("price_level")),
            "size": _metric_value(largest.get("size") or largest.get("liquidation_value")),
        }
    }


def _crypto_onchain_payload(row: Mapping[str, Any]) -> Mapping[str, Any]:
    stablecoin_flows = row.get("stablecoin_flows") if isinstance(row.get("stablecoin_flows"), Mapping) else {}
    btc_indicators = row.get("btc_indicators") if isinstance(row.get("btc_indicators"), Mapping) else {}
    whale_activity = row.get("whale_activity") if isinstance(row.get("whale_activity"), Mapping) else {}
    return {
        "exchange_netflow": _metric_value(
            row.get("exchange_netflow")
            or row.get("exchange_net_position_change")
            or row.get("exchange_flows")
            or stablecoin_flows
        ),
        "active_addresses": _metric_value(row.get("active_addresses") or btc_indicators.get("active_addresses")),
        "mvrv": _metric_value(row.get("mvrv") or btc_indicators.get("mvrv")),
        "sth_sopr": _metric_value(row.get("sth_sopr") or btc_indicators.get("sth_sopr")),
        "lth_sopr": _metric_value(row.get("lth_sopr") or btc_indicators.get("lth_sopr")),
        "nupl": _metric_value(row.get("nupl") or btc_indicators.get("nupl")),
        "whale_large_tx_count": _metric_value(whale_activity.get("large_tx_count")),
        "whale_large_tx_volume": _metric_value(whale_activity.get("large_tx_volume")),
        "stablecoin_exchange_netflow": _metric_value(stablecoin_flows),
        "exchange_balance": _metric_value(row.get("exchange_balance_trend")),
    }


def _crypto_macro_payload(row: Mapping[str, Any]) -> Mapping[str, Any]:
    series = row.get("series") if isinstance(row.get("series"), Mapping) else {}
    return {
        "dxy": _metric_value(row.get("dxy")),
        "us10y": _metric_value(row.get("us10y") or (series or {}).get("DGS10")),
    }


def _crypto_ahr999_payload(row: Mapping[str, Any]) -> Mapping[str, Any]:
    return {
        "value": _metric_value(row.get("value") or row.get("ahr999")),
        "fitted_price": _metric_value(row.get("fitted_price")),
    }


def _metric_value(value: object) -> float | None:
    direct = _to_float(value)
    if direct is not None:
        return direct
    if isinstance(value, Mapping):
        for key in (
            "value",
            "latest",
            "close",
            "v",
            "funding",
            "funding_rate",
            "fundingRate",
            "open_interest",
            "openInterest",
            "open_interest_usd",
            "open_interest_quantity",
            "open_interest_by_stable_coin_margin",
            "long_short_ratio",
            "longShortRatio",
            "global_account_long_short_ratio",
            "cumulative_delta",
            "latest_delta",
            "aggregate_netflow",
            "exchange_netflow",
            "netflow",
            "net_flow",
            "stablecoin_margin_list",
            "token_margin_list",
            "count",
            "addresses",
            "active_address_count",
            "sopr",
            "sth_sopr",
            "lth_sopr",
            "nupl",
            "net_unpnl",
        ):
            nested = value.get(key)
            direct = _metric_value(nested)
            if direct is not None:
                return direct
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            direct = _metric_value(item)
            if direct is not None:
                return direct
    return None


def _crypto_domain_status_from_payload(
    payload: Mapping[str, Any] | None,
    *,
    endpoint: str,
    results: Sequence[ProviderResult],
) -> DomainReadiness:
    if payload is None:
        return _crypto_missing_status_from_endpoint(endpoint=endpoint, results=results)
    if _payload_has_value(payload):
        return DomainReadiness.READY
    return DomainReadiness.INSUFFICIENT


def _payload_has_value(payload: Mapping[str, Any]) -> bool:
    for value in payload.values():
        if isinstance(value, Mapping):
            if _payload_has_value(value):
                return True
            continue
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            if len(value) > 0:
                return True
            continue
        if value is not None:
            return True
    return False


def _crypto_ohlcv_status(payload: Mapping[str, Any] | None, results: Sequence[ProviderResult]) -> DomainReadiness:
    if payload is None:
        return _crypto_missing_status_from_endpoint(endpoint="crypto_price_historical", results=results)
    rows = payload.get("rows")
    if isinstance(rows, int) and rows < 30:
        return DomainReadiness.INSUFFICIENT
    return DomainReadiness.READY


def _crypto_ahr999_status(
    *,
    request: PackRequest,
    payload: Mapping[str, Any] | None,
    results: Sequence[ProviderResult],
) -> DomainReadiness:
    if not request.ticker.upper().startswith("BTC"):
        return DomainReadiness.MISSING
    return _crypto_domain_status_from_payload(payload, endpoint="ahr999_index", results=results)


def _crypto_missing_status_from_endpoint(*, endpoint: str, results: Sequence[ProviderResult]) -> DomainReadiness:
    matching = [result for result in results if result.spec.endpoint == endpoint]
    if not matching:
        return DomainReadiness.MISSING
    if any(result.status == ProviderStatus.LICENSE_BLOCKED for result in matching):
        return DomainReadiness.LICENSE_BLOCKED
    if any(result.status in {ProviderStatus.CREDENTIAL_MISSING, ProviderStatus.SKIPPED_NOT_CONFIGURED} for result in matching):
        return DomainReadiness.MISSING
    return DomainReadiness.ERROR


def _crypto_domain_gaps(
    *,
    request: PackRequest,
    domain_status: Mapping[str, DomainReadiness],
) -> tuple[DataGap, ...]:
    gaps: list[DataGap] = []
    gap_fields = {
        "derivatives": ("derivatives.funding", "derivatives.oi", "derivatives.long_short_ratio", "derivatives.cvd_proxy"),
        "liquidation_map": ("liquidation_map.largest_cluster",),
        "ohlcv": ("ohlcv.rows",),
        "onchain": ("onchain.exchange_netflow",),
        "macro": ("macro.us10y",),
        "ahr999": ("ahr999.value",),
    }
    for domain, fields in gap_fields.items():
        status = domain_status.get(domain)
        if status in {DomainReadiness.READY, DomainReadiness.STALE}:
            continue
        if domain == "ahr999" and not request.ticker.upper().startswith("BTC"):
            continue
        for field_path in fields:
            gaps.append(
                DataGap(
                    gap_id=f"{request.run_id}:{request.call_id}:crypto_lens:{field_path}",
                    domain=PackDomain.MARKET,
                    severity=GapSeverity.WARN,
                    reason=DataGapReason.FIELD_MISSING,
                    field_path=field_path,
                    provider_candidates=(),
                    attempt_ids=(),
                    root_cause=f"CryptoLens 输入缺少 {field_path}，该指标不能写成已覆盖。",
                    next_action="补齐 OpenBB 归一化数据后重新生成 CryptoLens 分析。",
                )
            )
    return tuple(gaps)


def _crypto_partial_domain_gaps(
    *,
    request: PackRequest,
    domain_payloads: Mapping[str, Mapping[str, Any] | None],
) -> tuple[DataGap, ...]:
    onchain = domain_payloads.get("onchain")
    if not isinstance(onchain, Mapping) or not _payload_has_value(onchain):
        return ()
    fields = (
        "onchain.exchange_netflow",
        "onchain.active_addresses",
        "onchain.sth_sopr",
        "onchain.lth_sopr",
        "onchain.nupl",
        "onchain.stablecoin_exchange_netflow",
    )
    gaps: list[DataGap] = []
    for field_path in fields:
        key = field_path.split(".", 1)[1]
        if onchain.get(key) is not None:
            continue
        gaps.append(
            DataGap(
                gap_id=f"{request.run_id}:{request.call_id}:crypto_lens:{field_path}",
                domain=PackDomain.MARKET,
                severity=GapSeverity.WARN,
                reason=DataGapReason.FIELD_MISSING,
                field_path=field_path,
                provider_candidates=(),
                attempt_ids=(),
                root_cause=f"CryptoLens 输入缺少 {field_path}，链上资料只能部分覆盖。",
                next_action="等待 Coinglass 配额恢复或补充批准的 OpenBB 链上来源后重新生成。",
            )
        )
    return tuple(gaps)


def _crypto_lens_failure_gap(*, request: PackRequest, reason: str) -> DataGap:
    return DataGap(
        gap_id=f"{request.run_id}:{request.call_id}:crypto_lens:analysis_failed",
        domain=PackDomain.MARKET,
        severity=GapSeverity.WARN,
        reason=DataGapReason.FIELD_MISSING,
        field_path="crypto_lens.analysis",
        provider_candidates=(),
        attempt_ids=(),
        root_cause=f"CryptoLens 分析失败：{reason}",
        next_action="修复 CryptoLens 本地分析后重跑；不能用 OpenBB 原始事实伪造指标分析。",
    )


def _crypto_bundle_freshness(results: Sequence[ProviderResult]) -> FreshnessStatus:
    statuses = {result.freshness for result in results}
    if FreshnessStatus.FRESH_REMOTE in statuses:
        return FreshnessStatus.FRESH_REMOTE
    if FreshnessStatus.FRESH_CACHE in statuses:
        return FreshnessStatus.FRESH_CACHE
    if FreshnessStatus.STALE_CACHE in statuses:
        return FreshnessStatus.STALE_CACHE
    if FreshnessStatus.CACHE_UNUSABLE in statuses:
        return FreshnessStatus.CACHE_UNUSABLE
    return FreshnessStatus.NOT_FETCHED


def _pack_hash(
    *,
    run_id: str,
    call_id: str,
    status: str,
    raw_refs: Sequence[str],
    normalized_refs: Sequence[str],
    analysis_evidence_refs: Sequence[str],
    generated_at: str,
) -> str:
    payload = {
        "run_id": run_id,
        "call_id": call_id,
        "status": status,
        "raw_refs": list(raw_refs),
        "normalized_refs": list(normalized_refs),
        "analysis_evidence_refs": list(analysis_evidence_refs),
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
    if reason == DataGapReason.NOT_APPLICABLE:
        return "覆盖组已满足，本次无需请求该可选来源。"
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
        return requested_currency or "USDT"
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
