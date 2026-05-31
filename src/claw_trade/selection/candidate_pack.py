from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Callable, Mapping

from claw_trade.selection.artifacts import SelectionFileArtifactBackend
from claw_trade.selection.engine import FilteredUniverse, ScoringResult
from claw_trade.selection.features import SelectionNormalizedInputs
from claw_trade.selection.models import (
    DEFAULT_SELECTION_STRATEGY_CONFIG_VERSION,
    DEFAULT_SELECTION_WEIGHT_VERSION,
    CandidateFactRow,
    CandidatePackManifest,
    CandidatePackReadbackStatus,
    CandidatePackRef,
    CandidatePackSummary,
    DataGapRef,
    SelectionRunPlan,
)

_FORBIDDEN_OPINION_TERMS = (
    "值得买",
    "看好",
    "建议",
    "目标价",
    "止损",
    "交易建议",
    "买入",
    "卖出",
    "投资判断",
)

_FORBIDDEN_MACHINE_TERMS = (
    "manifest",
    "lineage",
    "sha256",
    "material_id",
    "viking://",
    "openviking",
    "provider-requests.jsonl",
    "normalized://",
    "attempt://",
    "feature://",
    "score://",
)

_READER_VISIBLE_FIELD_LABELS = {
    "amount": "成交额",
    "vol_ratio": "量比",
    "strategy_hit_count": "命中策略数",
    "data_gap_penalty_score": "数据缺口扣分",
    "risk_penalty_score": "风险扣分",
    "evidence_completeness_score": "证据完整度",
    "strategy_hit_coverage_score": "策略命中覆盖",
    "strategy_coverage_score": "策略命中覆盖",
    "strategy_inner_strength_score": "策略内强度",
    "strategy_strength_score": "策略内强度",
    "rps_trend_score": "RPS/趋势强度",
    "industry_theme_score": "行业/主题强度",
    "industry_theme_strength_score": "行业/主题强度",
    "intraday_return_pct": "日内涨跌幅",
    "close_open_ratio": "收盘/开盘比",
    "strategy_missing_field_count": "缺失策略字段数",
    "strategy_required_field_count": "策略必需字段数",
    "strategy_variant_count": "策略变体数",
    "score": "总分",
    "open": "开盘价",
    "close": "收盘价",
    "high": "最高价",
    "low": "最低价",
    "volume": "成交量",
    "range_pct": "振幅",
    "risk_penalty": "风险扣分",
    "data_gap_penalty": "数据缺口扣分",
    "liquidity_score": "流动性/可交易性",
    "liquidity_tradability_score": "流动性/可交易性",
    "tradability_score": "流动性/可交易性",
    "hit_volume_ratio": "命中量比",
    "hit_volume_breakout": "命中放量突破",
}


class CandidatePackError(ValueError):
    def __init__(self, code: str, reason: str) -> None:
        super().__init__(f"{code}: {reason}")
        self.code = code
        self.reason = reason


@dataclass(frozen=True)
class CandidatePackDraft:
    body_md: str
    body_json: str
    summary: CandidatePackSummary
    source_lineage_refs: tuple[str, ...]


@dataclass(frozen=True)
class ApprovedCandidatePack:
    candidate_pack_ref: CandidatePackRef
    manifest: CandidatePackManifest
    verification_log_refs: tuple[str, ...]


def build_candidate_pack(
    *,
    plan: SelectionRunPlan,
    inputs: SelectionNormalizedInputs,
    filtered: FilteredUniverse,
    scoring: ScoringResult,
    provider_attempt_refs: tuple[str, ...],
    data_gaps: tuple[DataGapRef, ...],
    feature_snapshot_ref: str,
    score_ref: str,
    stable_top20_rule: object | None = None,
    strategy_config_version: str = DEFAULT_SELECTION_STRATEGY_CONFIG_VERSION,
    weight_version: str = DEFAULT_SELECTION_WEIGHT_VERSION,
) -> CandidatePackDraft:
    _ = filtered
    candidate_count = len(scoring.top20)
    if candidate_count > 20:
        raise CandidatePackError(
            "candidate_pack_top_limit_exceeded",
            f"candidate_count={candidate_count} exceeds top20 contract",
        )
    if candidate_count < 1:
        raise CandidatePackError(
            "candidate_pack_top20_count_invalid",
            f"candidate_count must be between 1 and 20, actual={candidate_count}",
        )
    if not provider_attempt_refs:
        raise CandidatePackError(
            "candidate_pack_lineage_incomplete",
            "provider attempts lineage missing",
        )
    if not inputs.normalized_refs:
        raise CandidatePackError(
            "candidate_pack_lineage_incomplete",
            "normalized refs lineage missing",
        )
    if not feature_snapshot_ref.strip() or not score_ref.strip():
        raise CandidatePackError(
            "candidate_pack_lineage_incomplete",
            "feature_snapshot_ref/score_ref lineage missing",
        )
    blocker_gaps = tuple(gap for gap in data_gaps if gap.severity.value == "blocker")
    if blocker_gaps:
        codes = ",".join(sorted({gap.gap_code for gap in blocker_gaps}))
        raise CandidatePackError(
            "candidate_pack_data_gap_blocker",
            f"candidate pack has blocker data gaps: {codes}",
        )

    source_by_ticker: dict[str, str] = {row.ticker: row.source_ref for row in inputs.rows}
    tie_break_field_names = _stable_top20_tie_break_field_names(stable_top20_rule)
    candidates: list[CandidateFactRow] = []
    for rank, score_row in enumerate(scoring.top20, start=1):
        source_ref = source_by_ticker.get(score_row.ticker, "")
        if not source_ref:
            raise CandidatePackError(
                "candidate_pack_lineage_incomplete",
                f"ticker={score_row.ticker} missing normalized source_ref",
            )
        candidates.append(
            CandidateFactRow(
                rank=rank,
                ticker=score_row.ticker,
                company_name=score_row.company_name,
                industry=score_row.industry,
                feature_values=dict(score_row.feature_values),
                strategy_hits=score_row.strategy_hits,
                risk_flags=(),
                data_quality="完整",
                source_summary="标准化行情与财务快照",
                total_score=score_row.score,
                component_scores=_component_scores(score_row.feature_values),
                strategy_sources=_strategy_sources(score_row.strategy_hits),
                strategy_variants=_strategy_variants(score_row.strategy_hits),
                hit_fields=_hit_fields(score_row.feature_values),
                actual_metric_values=dict(score_row.feature_values),
                risk_penalty=_optional_float(
                    score_row.feature_values.get("risk_penalty", score_row.feature_values.get("risk_penalty_score"))
                ),
                data_gap_penalty=_optional_float(
                    score_row.feature_values.get("data_gap_penalty", score_row.feature_values.get("data_gap_penalty_score"))
                ),
                tie_break_fields=_tie_break_fields(score_row.feature_values, tie_break_field_names),
                strategy_config_version=strategy_config_version,
                weight_version=weight_version,
            )
        )

    data_quality_summary = _build_data_quality_summary(data_gaps=data_gaps)
    source_summary = (
        "来源摘要：交易日全市场标准化快照、特征快照与确定性评分结果。"
        f"本批次数据源调用 {len(provider_attempt_refs)} 次，标准化股票 {len(inputs.rows)} 行。"
    )
    summary_md = _render_summary_md(
        plan=plan,
        rows=tuple(candidates),
        data_quality_summary=data_quality_summary,
        source_summary=source_summary,
    )
    summary = CandidatePackSummary(
        summary_md=summary_md,
        candidates=tuple(candidates),
        data_quality_summary=data_quality_summary,
        source_summary=source_summary,
    )

    body_payload = _build_model_visible_json(plan=plan, summary=summary)
    source_lineage_refs = _dedup_refs(
        (
            plan.provider_batch_plan_ref,
            *provider_attempt_refs,
            *inputs.normalized_refs,
            feature_snapshot_ref,
            score_ref,
        )
    )
    draft = CandidatePackDraft(
        body_md=summary.summary_md,
        body_json=json.dumps(body_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        summary=summary,
        source_lineage_refs=source_lineage_refs,
    )
    validate_candidate_pack_contract(plan=plan, draft=draft)
    return draft


def validate_candidate_pack_contract(*, plan: SelectionRunPlan, draft: CandidatePackDraft) -> None:
    count = len(draft.summary.candidates)
    if count > 20:
        raise CandidatePackError("candidate_pack_top_limit_exceeded", f"candidate_count={count} exceeds 20")
    if count < 1:
        raise CandidatePackError(
            "candidate_pack_top20_count_invalid",
            f"candidate_count must be between 1 and 20, actual={count}",
        )
    _validate_candidate_strategy_field_completeness(draft.summary.candidates)
    _validate_no_forbidden_language(draft.body_md, field_name="candidate_pack.body_md")
    _validate_no_forbidden_language(draft.body_json, field_name="candidate_pack.body_json")
    _validate_no_forbidden_language(draft.summary.data_quality_summary, field_name="candidate_pack.data_quality_summary")
    _validate_no_forbidden_language(draft.summary.source_summary, field_name="candidate_pack.source_summary")
    for row in draft.summary.candidates:
        _validate_no_forbidden_language(row.source_summary, field_name=f"candidate[{row.ticker}].source_summary")
        _validate_no_forbidden_language(row.data_quality, field_name=f"candidate[{row.ticker}].data_quality")
    _validate_lineage_refs(
        plan=plan,
        source_lineage_refs=draft.source_lineage_refs,
    )


def validate_candidate_pack_payload_strategy_field_completeness(payload: Mapping[str, object]) -> None:
    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise CandidatePackError(
            "candidate_pack_strategy_fields_missing",
            "candidate pack payload must include non-empty candidates",
        )
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            raise CandidatePackError(
                "candidate_pack_strategy_fields_missing",
                "candidate pack candidate must be an object",
            )
        ticker = str(candidate.get("ticker") or "-").strip() or "-"
        values = _candidate_payload_feature_values(candidate)
        _validate_strategy_field_counts(ticker=ticker, feature_values=values)


def approve_candidate_pack(
    *,
    plan: SelectionRunPlan,
    draft: CandidatePackDraft,
    artifact_backend: SelectionFileArtifactBackend,
    now_fn: Callable[[], datetime] | None = None,
    candidate_scores_ref: str | None = None,
    stable_top20_rule: object | None = None,
) -> ApprovedCandidatePack:
    validate_candidate_pack_contract(plan=plan, draft=draft)
    now = (now_fn or _utc_now)()
    approved_at = _isoformat(now)
    expires_at = _isoformat(_to_utc(now) + timedelta(hours=24))
    base_uri = f"local://selection/{plan.selection_run_id}/candidate-pack/approved"
    body_uri = f"{base_uri}/candidate-pack.md"
    summary_uri = f"{base_uri}/candidate-pack-summary.md"
    json_uri = f"{base_uri}/candidate-pack.json"

    body_write = artifact_backend.write_text_verified(uri=body_uri, content=draft.body_md)
    summary_write = artifact_backend.write_text_verified(uri=summary_uri, content=draft.summary.summary_md)
    json_write = artifact_backend.write_text_verified(uri=json_uri, content=draft.body_json)

    manifest = CandidatePackManifest(
        schema_version="sel-04-candidate-pack-v1",
        selection_run_id=plan.selection_run_id,
        market=plan.market,
        profile=plan.profile,
        trade_date=plan.trade_date,
        candidate_count=len(draft.summary.candidates),
        source_lineage_refs=draft.source_lineage_refs,
        pack_body_sha256=body_write.content_sha256,
        strategy_config_ref=plan.approved_strategy_config_ref,
        readback_status=CandidatePackReadbackStatus.VERIFIED,
        strategy_config_version=_summary_strategy_config_version(draft.summary),
        weight_version=_summary_weight_version(draft.summary),
        candidate_scores_ref=candidate_scores_ref,
        stable_top20_rule=_stable_top20_rule_payload(stable_top20_rule),
        stage="approving_candidate_pack",
        target="candidate_pack",
    )
    manifest_uri = f"{base_uri}/candidate-pack-manifest.json"
    manifest_write = artifact_backend.write_json_verified(uri=manifest_uri, payload=_manifest_payload(manifest))

    candidate_pack_ref = CandidatePackRef(
        selection_run_id=plan.selection_run_id,
        material_id=f"selection-candidate-pack-{plan.selection_run_id}-{body_write.content_sha256[:12]}",
        l1_uri=body_uri,
        content_sha256=body_write.content_sha256,
        manifest_ref=manifest_uri,
        approved_at=approved_at,
        expires_at=expires_at,
        pack_summary_ref=summary_uri,
    )
    return ApprovedCandidatePack(
        candidate_pack_ref=candidate_pack_ref,
        manifest=manifest,
        verification_log_refs=(
            body_write.verification_log_ref,
            summary_write.verification_log_ref,
            json_write.verification_log_ref,
            manifest_write.verification_log_ref,
        ),
    )


def _build_data_quality_summary(*, data_gaps: tuple[DataGapRef, ...]) -> str:
    if not data_gaps:
        return "数据质量：本批次未发现阻断级或警告级缺口。"
    blocker = sum(1 for gap in data_gaps if gap.severity.value == "blocker")
    warn = sum(1 for gap in data_gaps if gap.severity.value == "warn")
    codes = sorted({gap.gap_code for gap in data_gaps})
    code_text = "、".join(codes)
    return f"数据质量：阻断 {blocker} 条，提示 {warn} 条。缺口编码：{code_text}。"


def _render_summary_md(
    *,
    plan: SelectionRunPlan,
    rows: tuple[CandidateFactRow, ...],
    data_quality_summary: str,
    source_summary: str,
) -> str:
    lines = [
        "# A股候选事实包",
        "",
        "## 本轮范围",
        f"- 交易日：{plan.trade_date}",
        f"- 市场：{plan.market.value}",
        f"- 候选数量：{len(rows)}",
        f"- 策略配置版本：{_rows_strategy_config_version(rows)}",
        f"- 权重版本：{_rows_weight_version(rows)}",
        "",
        "## 候选事实表",
        "| 排名 | 股票代码 | 股票名称 | 行业 | 总分 | 分项得分 | 策略来源 | 策略变体 | 命中字段 | 实际指标值 | 风险扣分 | 数据缺口扣分 | 排序 tie-break 字段 | 数据质量 | 读者化来源摘要 |",
        "| --- | --- | --- | --- | ---: | --- | --- | --- | --- | --- | ---: | ---: | --- | --- | --- |",
    ]
    for row in rows:
        score_text = _format_number(row.total_score)
        component_scores = _format_mapping(row.component_scores)
        strategy_sources = "、".join(row.strategy_sources) if row.strategy_sources else "-"
        strategy_variants = "、".join(row.strategy_variants) if row.strategy_variants else "-"
        hit_fields = _format_mapping(row.hit_fields)
        actual_metric_values = _format_mapping(row.actual_metric_values)
        risk_penalty = _format_number(row.risk_penalty)
        data_gap_penalty = _format_number(row.data_gap_penalty)
        tie_break_fields = _format_mapping(row.tie_break_fields)
        industry = row.industry if row.industry else "-"
        lines.append(
            f"| {row.rank} | {row.ticker} | {row.company_name} | {industry} | {score_text} | {component_scores} | "
            f"{strategy_sources} | {strategy_variants} | {hit_fields} | {actual_metric_values} | "
            f"{risk_penalty} | {data_gap_penalty} | {tie_break_fields} | {row.data_quality} | {row.source_summary} |"
        )
    lines.extend(
        (
            "",
            "## 策略命中明细",
            *_strategy_hit_detail_lines(rows),
            "",
            "## 排序与扣分说明",
            "- 总分：按 v1 透明权重对同一批次特征值进行确定性计算。",
            "- 分项得分：展示可复算的策略覆盖、策略强度、趋势、流动性、行业主题、证据完整度等子项。",
            "- 风险扣分与数据缺口扣分：仅展示确定性扣分值；缺字段时显示为空。",
            "- 排序 tie-break 字段：同分时使用的确定性排序字段值。",
            "",
            "## 字段说明",
            "- 策略来源与策略变体：来自已批准策略配置中的命中 id 解析结果。",
            "- 命中字段与实际指标值：展示本轮候选行已有的可复算字段值。",
            "- 数据质量：仅描述样本完整性，不包含研究结论。",
            "",
            "## 数据质量摘要",
            data_quality_summary,
            "",
            "## 来源摘要",
            source_summary,
        )
    )
    return "\n".join(lines).strip()


def _build_model_visible_json(
    *,
    plan: SelectionRunPlan,
    summary: CandidatePackSummary,
) -> dict[str, object]:
    return {
        "selection_run_id": plan.selection_run_id,
        "trade_date": plan.trade_date,
        "market": plan.market.value,
        "profile": plan.profile.value,
        "candidate_count": len(summary.candidates),
        "strategy_config_version": _summary_strategy_config_version(summary),
        "weight_version": _summary_weight_version(summary),
        "candidates": [
            {
                "rank": row.rank,
                "ticker": row.ticker,
                "company_name": row.company_name,
                "industry": row.industry,
                "total_score": row.total_score,
                "component_scores": dict(row.component_scores or {}),
                "strategy_sources": list(row.strategy_sources),
                "strategy_variants": list(row.strategy_variants),
                "hit_fields": dict(row.hit_fields or {}),
                "actual_metric_values": dict(row.actual_metric_values or {}),
                "risk_penalty": row.risk_penalty,
                "data_gap_penalty": row.data_gap_penalty,
                "tie_break_fields": dict(row.tie_break_fields or {}),
                "strategy_config_version": row.strategy_config_version,
                "weight_version": row.weight_version,
                "feature_values": dict(row.feature_values),
                "strategy_hits": list(row.strategy_hits),
                "risk_flags": list(row.risk_flags),
                "data_quality": row.data_quality,
                "source_summary": row.source_summary,
            }
            for row in summary.candidates
        ],
        "data_quality_summary": summary.data_quality_summary,
        "source_summary": summary.source_summary,
    }


def _manifest_payload(manifest: CandidatePackManifest) -> dict[str, object]:
    return {
        "schema_version": manifest.schema_version,
        "selection_run_id": manifest.selection_run_id,
        "stage": manifest.stage,
        "target": manifest.target,
        "market": manifest.market.value,
        "profile": manifest.profile.value,
        "trade_date": manifest.trade_date,
        "candidate_count": manifest.candidate_count,
        "source_lineage_refs": list(manifest.source_lineage_refs),
        "pack_body_sha256": manifest.pack_body_sha256,
        "strategy_config_ref": manifest.strategy_config_ref,
        "strategy_config_version": manifest.strategy_config_version,
        "weight_version": manifest.weight_version,
        "candidate_scores_ref": manifest.candidate_scores_ref,
        "stable_top20_rule": dict(manifest.stable_top20_rule or {}),
        "readback_status": manifest.readback_status.value,
    }


def _validate_no_forbidden_language(text: str, *, field_name: str) -> None:
    lowered = text.lower()
    for term in _FORBIDDEN_OPINION_TERMS:
        if term in text:
            raise CandidatePackError(
                "candidate_pack_subjective_language_forbidden",
                f"{field_name} contains forbidden term: {term}",
            )
    for term in _FORBIDDEN_MACHINE_TERMS:
        if term in lowered:
            raise CandidatePackError(
                "candidate_pack_model_boundary_forbidden",
                f"{field_name} contains machine-only term: {term}",
            )


def _validate_lineage_refs(*, plan: SelectionRunPlan, source_lineage_refs: tuple[str, ...]) -> None:
    if not source_lineage_refs:
        raise CandidatePackError("candidate_pack_lineage_incomplete", "source_lineage_refs missing")
    provider_plan_ok = plan.provider_batch_plan_ref in source_lineage_refs
    attempt_ok = any(ref.startswith("attempt://") for ref in source_lineage_refs)
    normalized_ok = any(_is_normalized_lineage_ref(ref) for ref in source_lineage_refs)
    feature_ok = any(ref.startswith("feature://") for ref in source_lineage_refs)
    score_or_top20_ok = any(ref.startswith("score://") or ref.startswith("top20://") for ref in source_lineage_refs)
    if not (provider_plan_ok and attempt_ok and normalized_ok and feature_ok and score_or_top20_ok):
        raise CandidatePackError(
            "candidate_pack_lineage_incomplete",
            "lineage must include provider plan/attempts/normalized/feature/score refs",
        )


def _is_normalized_lineage_ref(ref: str) -> bool:
    return ref.startswith("normalized://") or ref.startswith("mongo://openbb_normalized/")


def _validate_candidate_strategy_field_completeness(rows: tuple[CandidateFactRow, ...]) -> None:
    for row in rows:
        _validate_strategy_field_counts(ticker=row.ticker, feature_values=row.feature_values)


def _candidate_payload_feature_values(candidate: Mapping[str, object]) -> Mapping[str, object]:
    values = candidate.get("feature_values")
    if isinstance(values, Mapping):
        return values
    actual_values = candidate.get("actual_metric_values")
    if isinstance(actual_values, Mapping):
        return actual_values
    return {}


def _validate_strategy_field_counts(*, ticker: str, feature_values: Mapping[str, object]) -> None:
    required_count = _optional_float(feature_values.get("strategy_required_field_count"))
    missing_count = _optional_float(feature_values.get("strategy_missing_field_count"))
    if required_count is None or required_count <= 0:
        raise CandidatePackError(
            "candidate_pack_strategy_fields_missing",
            f"ticker={ticker} missing strategy_required_field_count",
        )
    if missing_count is None:
        raise CandidatePackError(
            "candidate_pack_strategy_fields_missing",
            f"ticker={ticker} missing strategy_missing_field_count",
        )
    if missing_count > 0:
        raise CandidatePackError(
            "candidate_pack_strategy_fields_missing",
            f"ticker={ticker} missing {missing_count:g}/{required_count:g} approved strategy fields",
        )


def _component_scores(values: Mapping[str, object]) -> dict[str, float | int | str | None]:
    component_keys = {
        "strategy_hit_coverage_score",
        "strategy_coverage_score",
        "strategy_strength_score",
        "strategy_inner_strength_score",
        "rps_trend_score",
        "liquidity_score",
        "liquidity_tradability_score",
        "tradability_score",
        "industry_theme_strength_score",
        "industry_theme_score",
        "evidence_completeness_score",
    }
    out: dict[str, float | int | str | None] = {}
    for key, value in values.items():
        if key in component_keys or key.endswith("_subscore"):
            out[key] = _model_value(value)
    return out


def _strategy_sources(strategy_hits: tuple[str, ...]) -> tuple[str, ...]:
    return _dedup_texts(tuple(source for source, _variant in (_split_strategy_hit(hit) for hit in strategy_hits) if source))


def _strategy_variants(strategy_hits: tuple[str, ...]) -> tuple[str, ...]:
    return _dedup_texts(tuple(variant for _source, variant in (_split_strategy_hit(hit) for hit in strategy_hits) if variant))


def _split_strategy_hit(value: str) -> tuple[str, str]:
    text = value.strip()
    for separator in ("::", ":"):
        if separator in text:
            source, variant = text.split(separator, 1)
            return source.strip(), variant.strip()
    return "", text


def _hit_fields(values: Mapping[str, object]) -> dict[str, float | int | str | None]:
    out: dict[str, float | int | str | None] = {}
    for key, value in values.items():
        if key.startswith("hit_") or key.startswith("strategy_hit_") or key.endswith("_hit"):
            out[key] = _model_value(value)
    return out


def _tie_break_fields(
    values: Mapping[str, object],
    field_names: tuple[str, ...],
) -> dict[str, float | int | str | None]:
    names = field_names or tuple(key for key in values if key.endswith("_tie_break"))
    return {name: _model_value(values.get(name)) for name in names if name in values}


def _stable_top20_tie_break_field_names(stable_top20_rule: object | None) -> tuple[str, ...]:
    fields = getattr(stable_top20_rule, "tie_break_fields", ()) if stable_top20_rule is not None else ()
    names: list[str] = []
    for field in fields:
        name = getattr(field, "field", "")
        if isinstance(name, str) and name.strip():
            names.append(name.strip())
    return tuple(names)


def _stable_top20_rule_payload(stable_top20_rule: object | None) -> dict[str, object] | None:
    if stable_top20_rule is None:
        return None
    score_field = getattr(stable_top20_rule, "score_field", None)
    tie_break_fields = getattr(stable_top20_rule, "tie_break_fields", ())
    if not isinstance(score_field, str) or not score_field.strip():
        return None
    tie_break: list[str] = []
    for field in tie_break_fields:
        field_name = getattr(field, "field", "")
        descending = bool(getattr(field, "descending", False))
        if isinstance(field_name, str) and field_name.strip():
            tie_break.append(f"{field_name.strip()}_{'desc' if descending else 'asc'}")
    return {
        "primary": f"{score_field.strip()}_desc",
        "tie_break": tie_break,
    }


def _summary_strategy_config_version(summary: CandidatePackSummary) -> str:
    return _rows_strategy_config_version(summary.candidates)


def _summary_weight_version(summary: CandidatePackSummary) -> str:
    return _rows_weight_version(summary.candidates)


def _rows_strategy_config_version(rows: tuple[CandidateFactRow, ...]) -> str:
    first = rows[0]
    return first.strategy_config_version


def _rows_weight_version(rows: tuple[CandidateFactRow, ...]) -> str:
    first = rows[0]
    return first.weight_version


def _strategy_hit_detail_lines(rows: tuple[CandidateFactRow, ...]) -> tuple[str, ...]:
    lines: list[str] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        for hit in row.strategy_hits:
            source, variant = _split_strategy_hit(hit)
            key = (source, variant)
            if key in seen:
                continue
            seen.add(key)
            source_text = source if source else "-"
            variant_text = variant if variant else hit
            lines.append(f"- 来源：{source_text}；策略变体：{variant_text}。")
    return tuple(lines) if lines else ("- 本轮候选未记录策略命中。",)


def _format_mapping(values: Mapping[str, object] | None) -> str:
    if not values:
        return "-"
    payload = {_reader_visible_label(key): _model_value(value) for key, value in sorted(values.items())}
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _reader_visible_label(key: str) -> str:
    return _READER_VISIBLE_FIELD_LABELS.get(key, key)


def _format_number(value: object) -> str:
    number = _optional_float(value)
    return f"{number:.6f}" if number is not None else "-"


def _optional_float(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    try:
        return float(str(value))
    except ValueError:
        return None


def _model_value(value: object) -> float | int | str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, int | float | str):
        return value
    return str(value)


def _dedup_texts(values: tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        text = value.strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return tuple(out)


def _dedup_refs(refs: tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for ref in refs:
        value = ref.strip()
        if not value or value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return tuple(ordered)


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _isoformat(value: datetime) -> str:
    return _to_utc(value).isoformat().replace("+00:00", "Z")
