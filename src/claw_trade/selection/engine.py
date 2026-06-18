from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Mapping

from claw_trade.selection.features import FeatureRow, FeatureSnapshot
from claw_trade.selection.models import DataGapRef, DataGapSeverity, SelectionRunPlan

ComparisonOperator = Literal[">=", ">", "<=", "<", "==", "!="]


class SelectionEngineError(ValueError):
    def __init__(self, code: str, reason: str, *, data_gaps: tuple[DataGapRef, ...] = ()) -> None:
        super().__init__(f"{code}: {reason}")
        self.code = code
        self.reason = reason
        self.data_gaps = data_gaps


@dataclass(frozen=True)
class HardFilterRule:
    name: str
    field: str
    operator: ComparisonOperator
    value: float


@dataclass(frozen=True)
class StrategyCondition:
    field: str
    operator: ComparisonOperator
    value: float


@dataclass(frozen=True)
class StrategyRule:
    name: str
    all_of: tuple[StrategyCondition, ...]
    source: str = ""
    required_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class StableSortField:
    field: str
    descending: bool


@dataclass(frozen=True)
class StableTop20Rule:
    score_field: str
    tie_break_fields: tuple[StableSortField, ...]
    missing_policy: Literal["fail"]


@dataclass(frozen=True)
class ApprovedSelectionStrategy:
    config_ref: str
    hard_filters: tuple[HardFilterRule, ...]
    strategy_set: tuple[StrategyRule, ...]
    weights: Mapping[str, float]
    stable_top20_rule: StableTop20Rule

    def __post_init__(self) -> None:
        if not self.config_ref.strip():
            raise ValueError("config_ref must be non-empty")
        if not self.hard_filters:
            raise ValueError("hard_filters must be non-empty")
        if not self.strategy_set:
            raise ValueError("strategy_set must be non-empty")
        if not self.weights:
            raise ValueError("weights must be non-empty")
        if not self.stable_top20_rule.score_field.strip():
            raise ValueError("stable_top20_rule.score_field must be non-empty")
        if not self.stable_top20_rule.tie_break_fields:
            raise ValueError("stable_top20_rule.tie_break_fields must be non-empty")
        if self.stable_top20_rule.missing_policy != "fail":
            raise ValueError("stable_top20_rule.missing_policy must be fail")


@dataclass(frozen=True)
class FilterDecision:
    ticker: str
    passed: bool
    failed_rules: tuple[str, ...]


@dataclass(frozen=True)
class FilteredUniverse:
    rows: tuple[FeatureRow, ...]
    decisions: tuple[FilterDecision, ...]


@dataclass(frozen=True)
class CandidateScoreRow:
    ticker: str
    company_name: str
    industry: str | None
    score: float
    strategy_hits: tuple[str, ...]
    feature_values: Mapping[str, float]
    source_ref: str = ""


@dataclass(frozen=True)
class ScoringResult:
    score_ref: str
    top20: tuple[CandidateScoreRow, ...]
    all_scores: tuple[CandidateScoreRow, ...] = ()


def run_hard_filters(
    *,
    plan: SelectionRunPlan,
    snapshot: FeatureSnapshot,
    strategy: ApprovedSelectionStrategy,
) -> FilteredUniverse:
    passed_rows: list[FeatureRow] = []
    for row in snapshot.rows:
        failed_rules: list[str] = []
        for rule in strategy.hard_filters:
            raw_value = row.feature_values.get(rule.field)
            if raw_value is None:
                failed_rules.append(rule.name)
                continue
            if not _compare(raw_value, rule.operator, rule.value):
                failed_rules.append(rule.name)
        is_passed = not failed_rules
        if is_passed:
            passed_rows.append(row)
    if not passed_rows:
        raise SelectionEngineError(
            "selection_inputs_insufficient",
            "硬过滤后无候选",
            data_gaps=(
                _blocker_gap(
                    gap_id=f"{plan.selection_run_id}-filtered-empty",
                    gap_code="filtered_universe_empty",
                    attempt_refs=("attempt://filters",),
                    reader_message="硬过滤后无可评分股票，任务失败。",
                ),
            ),
        )
    return FilteredUniverse(rows=tuple(passed_rows), decisions=())


def score_candidates(
    *,
    plan: SelectionRunPlan,
    filtered: FilteredUniverse,
    strategy: ApprovedSelectionStrategy,
) -> ScoringResult:
    if not strategy.stable_top20_rule.tie_break_fields:
        raise SelectionEngineError(
            "strategy_config_unapproved",
            "stable_top20_rule.tie_break_fields 缺失",
            data_gaps=(
                _blocker_gap(
                    gap_id=f"{plan.selection_run_id}-stable-top20-tie-break-missing",
                    gap_code="stable_top20_rule_tie_break_missing",
                    attempt_refs=("attempt://strategy-config",),
                    reader_message="stable_top20_rule 缺少非空二级 tie-break，任务按 fail closed 失败。",
                ),
            ),
        )
    if not strategy.weights:
        raise SelectionEngineError(
            "strategy_config_unapproved",
            "weights 缺失",
        )
    _validate_v1_weights(plan=plan, strategy=strategy)
    required_strategy_fields = _strategy_required_fields(strategy.strategy_set)
    top_rows: list[CandidateScoreRow] = []
    scored_count = 0
    for item in filtered.rows:
        strategy_hits = _resolve_strategy_hits(item, strategy.strategy_set)
        feature_values = dict(item.feature_values)
        component_scores = _compute_transparent_v1_component_scores(
            feature_values=feature_values,
            strategy_hits=strategy_hits,
            strategy_count=len(strategy.strategy_set),
            required_strategy_fields=required_strategy_fields,
            weights=strategy.weights,
        )
        feature_values.update(component_scores)
        positive_score = (
            feature_values["strategy_hit_coverage_score"]
            + feature_values["strategy_inner_strength_score"]
            + feature_values["rps_trend_score"]
            + feature_values["liquidity_tradability_score"]
            + feature_values["industry_theme_score"]
            + feature_values["evidence_completeness_score"]
        )
        score = positive_score - feature_values["risk_penalty_score"] - feature_values["data_gap_penalty_score"]
        feature_values[strategy.stable_top20_rule.score_field] = score
        scored_count += 1
        candidate = CandidateScoreRow(
            ticker=item.ticker,
            company_name=item.company_name,
            industry=item.industry,
            score=score,
            strategy_hits=strategy_hits,
            feature_values=feature_values,
            source_ref=item.source_ref,
        )
        top_rows.append(candidate)
        if len(top_rows) > 20:
            top_rows.sort(
                key=lambda row: _stable_sort_key(
                    row,
                    score_field=strategy.stable_top20_rule.score_field,
                    tie_break_fields=strategy.stable_top20_rule.tie_break_fields,
                    missing_policy=strategy.stable_top20_rule.missing_policy,
                )
            )
            top_rows.pop()
    if scored_count == 0:
        raise SelectionEngineError(
            "selection_inputs_insufficient",
            "无可评分候选",
            data_gaps=(
                _blocker_gap(
                    gap_id=f"{plan.selection_run_id}-scoring-empty",
                    gap_code="scoring_rows_empty",
                    attempt_refs=("attempt://scoring",),
                    reader_message="评分结果为空，无法产出 top20。",
                ),
            ),
        )
    top20 = tuple(
        sorted(
            top_rows,
            key=lambda row: _stable_sort_key(
                row,
                score_field=strategy.stable_top20_rule.score_field,
                tie_break_fields=strategy.stable_top20_rule.tie_break_fields,
                missing_policy=strategy.stable_top20_rule.missing_policy,
            ),
        )
    )
    return ScoringResult(
        score_ref=f"score://{plan.selection_run_id}",
        top20=top20,
    )


def _resolve_strategy_hits(row: CandidateScoreRow | FeatureRow, strategies: tuple[StrategyRule, ...]) -> tuple[str, ...]:
    hits: list[str] = []
    for strategy in strategies:
        passed = True
        for condition in strategy.all_of:
            raw_value = row.feature_values.get(condition.field)
            if raw_value is None or not _compare(raw_value, condition.operator, condition.value):
                passed = False
                break
        if passed:
            source = strategy.source.strip()
            hits.append(f"{source}:{strategy.name}" if source else strategy.name)
    return tuple(hits)


def _validate_v1_weights(*, plan: SelectionRunPlan, strategy: ApprovedSelectionStrategy) -> None:
    expected = {
        "strategy_hit_coverage_score": 30.0,
        "strategy_inner_strength_score": 25.0,
        "rps_trend_score": 20.0,
        "liquidity_tradability_score": 15.0,
        "industry_theme_score": 5.0,
        "evidence_completeness_score": 5.0,
        "risk_penalty_score": 20.0,
        "data_gap_penalty_score": 15.0,
    }
    if strategy.config_ref == "config://crypto-selection-v1":
        expected["industry_theme_score"] = 0.0
    actual = {key: float(value) for key, value in strategy.weights.items()}
    if actual != expected:
        raise SelectionEngineError(
            "strategy_config_unapproved",
            "transparent v1 weights 缺失或不匹配",
            data_gaps=(
                _blocker_gap(
                    gap_id=f"{plan.selection_run_id}-transparent-v1-weights-invalid",
                    gap_code="transparent_v1_weights_invalid",
                    attempt_refs=("attempt://strategy-config",),
                    reader_message="approved strategy config 未提供 v1 透明排序权重，任务按 fail closed 失败。",
                ),
            ),
        )


def _strategy_required_fields(strategies: tuple[StrategyRule, ...]) -> tuple[str, ...]:
    fields: set[str] = set()
    for strategy in strategies:
        fields.update(field for field in strategy.required_fields if field.strip())
        fields.update(condition.field for condition in strategy.all_of if condition.field.strip())
    return tuple(sorted(fields))


def _compute_transparent_v1_component_scores(
    *,
    feature_values: Mapping[str, float],
    strategy_hits: tuple[str, ...],
    strategy_count: int,
    required_strategy_fields: tuple[str, ...],
    weights: Mapping[str, float],
) -> dict[str, float]:
    missing_strategy_fields = tuple(field for field in required_strategy_fields if feature_values.get(field) is None)
    required_field_count = len(required_strategy_fields)
    missing_ratio = len(missing_strategy_fields) / required_field_count if required_field_count else 0.0
    hit_count = len(strategy_hits)
    hit_ratio = hit_count / strategy_count if strategy_count else 0.0

    result = {
        "strategy_hit_count": float(hit_count),
        "strategy_variant_count": float(strategy_count),
        "strategy_missing_field_count": float(len(missing_strategy_fields)),
        "strategy_required_field_count": float(required_field_count),
        "strategy_hit_coverage_score": _explicit_or_default_score(
            feature_values,
            "strategy_hit_coverage_score",
            max_score=weights["strategy_hit_coverage_score"],
            default=weights["strategy_hit_coverage_score"] * hit_ratio,
        ),
        "strategy_inner_strength_score": _explicit_or_default_score(
            feature_values,
            "strategy_inner_strength_score",
            max_score=weights["strategy_inner_strength_score"],
            default=weights["strategy_inner_strength_score"] * hit_ratio,
        ),
        "rps_trend_score": _explicit_or_default_score(
            feature_values,
            "rps_trend_score",
            max_score=weights["rps_trend_score"],
            default=_derive_rps_trend_score(feature_values, max_score=weights["rps_trend_score"]),
        ),
        "liquidity_tradability_score": _explicit_or_default_score(
            feature_values,
            "liquidity_tradability_score",
            max_score=weights["liquidity_tradability_score"],
            default=_derive_liquidity_score(feature_values, max_score=weights["liquidity_tradability_score"]),
        ),
        "industry_theme_score": _explicit_or_default_score(
            feature_values,
            "industry_theme_score",
            max_score=weights["industry_theme_score"],
            default=0.0,
        ),
        "evidence_completeness_score": _explicit_or_default_score(
            feature_values,
            "evidence_completeness_score",
            max_score=weights["evidence_completeness_score"],
            default=weights["evidence_completeness_score"] * (1.0 - missing_ratio),
        ),
        "risk_penalty_score": _explicit_or_default_score(
            feature_values,
            "risk_penalty_score",
            max_score=weights["risk_penalty_score"],
            default=0.0,
        ),
        "data_gap_penalty_score": _explicit_or_default_score(
            feature_values,
            "data_gap_penalty_score",
            max_score=weights["data_gap_penalty_score"],
            default=weights["data_gap_penalty_score"] * missing_ratio,
        ),
    }
    return result


def _explicit_or_default_score(
    feature_values: Mapping[str, float],
    field: str,
    *,
    max_score: float,
    default: float,
) -> float:
    value = feature_values.get(field)
    if value is None:
        return _clamp(default, lower=0.0, upper=max_score)
    return _clamp(float(value), lower=0.0, upper=max_score)


def _derive_rps_trend_score(feature_values: Mapping[str, float], *, max_score: float) -> float:
    for field in ("rps120", "rps_120", "rps60", "rps_60"):
        value = feature_values.get(field)
        if value is not None:
            return max_score * _clamp(float(value) / 100.0, lower=0.0, upper=1.0)
    return 0.0


def _derive_liquidity_score(feature_values: Mapping[str, float], *, max_score: float) -> float:
    amount = feature_values.get("amount")
    if amount is None:
        return 0.0
    return max_score * _clamp(float(amount) / 1000000000.0, lower=0.0, upper=1.0)


def _clamp(value: float, *, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def _stable_sort_key(
    row: CandidateScoreRow,
    *,
    score_field: str,
    tie_break_fields: tuple[StableSortField, ...],
    missing_policy: Literal["fail"],
) -> tuple[float | str, ...]:
    score = row.feature_values.get(score_field)
    if score is None and missing_policy == "fail":
        raise SelectionEngineError("strategy_config_unapproved", f"stable_top20_rule 缺少 score 字段: {score_field}")
    key: list[float | str] = [(-1.0 * float(score)) if score is not None else 0.0]
    for field in tie_break_fields:
        value = row.feature_values.get(field.field)
        if value is None and missing_policy == "fail":
            raise SelectionEngineError(
                "strategy_config_unapproved",
                f"stable_top20_rule 缺少 tie-break 字段: {field.field}",
            )
        numeric_value = float(value) if value is not None else 0.0
        key.append(-numeric_value if field.descending else numeric_value)
    key.append(row.ticker)
    return tuple(key)


def _compare(left: float, operator: ComparisonOperator, right: float) -> bool:
    if operator == ">=":
        return left >= right
    if operator == ">":
        return left > right
    if operator == "<=":
        return left <= right
    if operator == "<":
        return left < right
    if operator == "==":
        return left == right
    if operator == "!=":
        return left != right
    raise ValueError(f"unsupported operator: {operator}")


def _blocker_gap(
    *,
    gap_id: str,
    gap_code: str,
    attempt_refs: tuple[str, ...],
    reader_message: str,
) -> DataGapRef:
    return DataGapRef(
        gap_id=gap_id,
        domain="selection",
        gap_code=gap_code,
        severity=DataGapSeverity.BLOCKER,
        attempt_refs=attempt_refs if attempt_refs else ("attempt://unknown",),
        reader_message=reader_message,
    )
