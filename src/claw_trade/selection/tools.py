from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Callable, Mapping

from claw_trade.selection.models import (
    DEFAULT_SELECTION_STRATEGY_CONFIG_VERSION,
    DEFAULT_SELECTION_WEIGHT_VERSION,
    CandidatePackManifest,
    CandidatePackReadbackStatus,
    CandidatePackRef,
    SelectionMarket,
    SelectionProfile,
)

_SELECTION_TOOL_NAME = "claw_get_selection_candidate_pack"
_ALLOWED_WORKERS = frozenset({"selection_strategist", "selection_skeptic"})
_REQUIRED_STAGE = "selection_review"
_DEFAULT_ARTIFACT_ROOT = Path("runs/selection/artifacts")
_FORBIDDEN_MODEL_VISIBLE_TERMS = (
    "raw/debug",
    "debug envelope",
    "provider envelope",
    "mongo",
    "material_id",
    "sha256",
    "hash",
    "manifest",
    "lineage",
    "receipt",
    "refs",
    "viking://",
    "openviking",
    "openclaw",
    "provider-requests.jsonl",
    "runtime wrapper",
)
_REQUIRED_MODEL_VISIBLE_LABELS = (
    "总分",
    "分项得分",
    "策略来源",
    "策略变体",
    "命中字段",
    "实际指标值",
    "风险扣分",
    "数据缺口扣分",
    "tie-break",
    "权重版本",
    "策略配置版本",
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

_READER_VISIBLE_RAW_FIELD_NAMES = tuple(_READER_VISIBLE_FIELD_LABELS)


class SelectionToolError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True)
class SelectionPackReadResult:
    body_md: str
    pack_body_sha256: str
    candidate_count: int
    selection_run_id: str
    select_workflow_run_id: str
    market: str
    profile: str
    trade_date: str


def load_selection_candidate_pack_from_runtime_context(
    runtime_context: Mapping[str, object],
    *,
    now_fn: Callable[[], datetime] | None = None,
) -> SelectionPackReadResult:
    worker_id = _text(runtime_context, "worker_id")
    stage = _text(runtime_context, "stage")
    select_workflow_run_id = _text(runtime_context, "select_workflow_run_id")
    selection_run_id = _text(runtime_context, "selection_run_id")
    runtime_vars = _mapping(runtime_context, "runtime_vars")

    if worker_id is None or stage is None or select_workflow_run_id is None or selection_run_id is None:
        raise SelectionToolError(
            "selection_runtime_context_missing",
            "missing worker/stage/select_workflow_run_id/selection_run_id in runtime context",
        )
    if worker_id not in _ALLOWED_WORKERS or stage != _REQUIRED_STAGE:
        raise SelectionToolError(
            "selection_worker_not_allowed",
            f"worker={worker_id} stage={stage} is not allowed for {_SELECTION_TOOL_NAME}",
        )

    candidate_pack_ref = _load_candidate_pack_ref(runtime_context, runtime_vars, selection_run_id=selection_run_id)
    if candidate_pack_ref.selection_run_id != selection_run_id:
        raise SelectionToolError(
            "selection_run_missing",
            "selection_run_id does not match candidate_pack_ref.selection_run_id",
        )

    artifact_root = _artifact_root(runtime_context, runtime_vars)
    body_path = _resolve_ref_to_path(candidate_pack_ref.l1_uri, artifact_root=artifact_root)
    manifest_path = _resolve_ref_to_path(candidate_pack_ref.manifest_ref, artifact_root=artifact_root)

    body_text = _read_text(body_path)
    body_sha = sha256(body_text.encode("utf-8")).hexdigest()
    if body_sha != candidate_pack_ref.content_sha256:
        raise SelectionToolError("candidate_pack_integrity_failed", "candidate pack body sha256 mismatch")

    manifest_text = _read_text(manifest_path)
    manifest_sha = sha256(manifest_text.encode("utf-8")).hexdigest()
    manifest_payload = _load_json(manifest_text, code="candidate_pack_integrity_failed", target="manifest")
    manifest = _parse_manifest(manifest_payload)

    if manifest.selection_run_id != selection_run_id:
        raise SelectionToolError(
            "candidate_pack_integrity_failed",
            "manifest.selection_run_id does not match runtime context",
        )
    if manifest.pack_body_sha256 != candidate_pack_ref.content_sha256:
        raise SelectionToolError(
            "candidate_pack_integrity_failed",
            "manifest.pack_body_sha256 does not match candidate_pack_ref.content_sha256",
        )
    if manifest.readback_status != CandidatePackReadbackStatus.VERIFIED:
        raise SelectionToolError("candidate_pack_integrity_failed", "manifest.readback_status is not verified")
    if not manifest.source_lineage_refs:
        raise SelectionToolError("candidate_pack_lineage_incomplete", "manifest.source_lineage_refs is empty")
    if manifest.stage != "approving_candidate_pack" or manifest.target != "candidate_pack":
        raise SelectionToolError(
            "candidate_pack_integrity_failed",
            "manifest stage/target is not approving_candidate_pack/candidate_pack",
        )

    _validate_readback_verify_log(
        data_path=body_path,
        expected_sha256=candidate_pack_ref.content_sha256,
        code="candidate_pack_integrity_failed",
    )
    _validate_readback_verify_log(
        data_path=manifest_path,
        expected_sha256=manifest_sha,
        code="candidate_pack_integrity_failed",
    )

    now = (now_fn or _utc_now)()
    expires_at = _parse_iso_timestamp(candidate_pack_ref.expires_at)
    if expires_at <= now:
        raise SelectionToolError("candidate_pack_stale", "candidate pack has expired")

    runtime_market = _text(runtime_vars, "market")
    runtime_profile = _text(runtime_vars, "profile")
    runtime_trade_date = _text(runtime_vars, "trade_date")
    if runtime_market is not None and runtime_market != manifest.market.value:
        raise SelectionToolError("selection_run_missing", "runtime market does not match manifest")
    if runtime_profile is not None and runtime_profile != manifest.profile.value:
        raise SelectionToolError("selection_run_missing", "runtime profile does not match manifest")
    if runtime_trade_date is not None and runtime_trade_date != manifest.trade_date:
        raise SelectionToolError("selection_run_missing", "runtime trade_date does not match manifest")

    model_visible_body = _model_visible_candidate_pack_body(
        candidate_pack_ref=candidate_pack_ref,
        artifact_root=artifact_root,
        fallback_body=body_text,
        manifest=manifest,
    )
    _validate_model_visible_body(model_visible_body)
    return SelectionPackReadResult(
        body_md=model_visible_body,
        pack_body_sha256=body_sha,
        candidate_count=manifest.candidate_count,
        selection_run_id=selection_run_id,
        select_workflow_run_id=select_workflow_run_id,
        market=manifest.market.value,
        profile=manifest.profile.value,
        trade_date=manifest.trade_date,
    )


def execute_selection_candidate_pack_tool(payload: Mapping[str, object]) -> dict[str, object]:
    runtime_context = _mapping(payload, "runtime_context")
    params = payload.get("tool_input", {})
    if not isinstance(params, dict):
        raise SelectionToolError("selection_runtime_context_missing", "tool_input must be object")
    if params:
        raise SelectionToolError(
            "selection_runtime_context_missing",
            "claw_get_selection_candidate_pack does not accept business params",
        )
    result = load_selection_candidate_pack_from_runtime_context(runtime_context)
    return {
        "ok": True,
        "tool_name": _SELECTION_TOOL_NAME,
        "reader_brief_md": result.body_md,
        "pack_body_sha256": result.pack_body_sha256,
        "candidate_count": result.candidate_count,
        "selection_run_meta": {
            "selection_run_id": result.selection_run_id,
            "select_workflow_run_id": result.select_workflow_run_id,
            "market": result.market,
            "profile": result.profile,
            "trade_date": result.trade_date,
        },
    }


def _load_candidate_pack_ref(
    runtime_context: Mapping[str, object],
    runtime_vars: Mapping[str, object],
    *,
    selection_run_id: str,
) -> CandidatePackRef:
    raw = runtime_context.get("candidate_pack_ref")
    if not isinstance(raw, (dict, str)):
        raw = runtime_vars.get("candidate_pack_ref")
    payload = _candidate_pack_ref_payload(raw)
    if payload is None:
        raise SelectionToolError("candidate_pack_not_approved", "candidate_pack_ref is missing")

    payload.setdefault("selection_run_id", selection_run_id)
    try:
        return CandidatePackRef(
            selection_run_id=_required_text(payload, "selection_run_id", code="candidate_pack_not_approved"),
            material_id=_required_text(payload, "material_id", code="candidate_pack_not_approved"),
            l1_uri=_required_text(payload, "l1_uri", code="candidate_pack_not_approved"),
            content_sha256=_required_text(payload, "content_sha256", code="candidate_pack_not_approved"),
            manifest_ref=_required_text(payload, "manifest_ref", code="candidate_pack_not_approved"),
            approved_at=_required_text(payload, "approved_at", code="candidate_pack_not_approved"),
            expires_at=_required_text(payload, "expires_at", code="candidate_pack_not_approved"),
            pack_summary_ref=_required_text(payload, "pack_summary_ref", code="candidate_pack_not_approved"),
        )
    except ValueError as exc:
        raise SelectionToolError("candidate_pack_not_approved", str(exc)) from exc


def _candidate_pack_ref_payload(raw: object) -> dict[str, object] | None:
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SelectionToolError("candidate_pack_not_approved", "candidate_pack_ref is not valid JSON") from exc
        if not isinstance(decoded, dict):
            raise SelectionToolError("candidate_pack_not_approved", "candidate_pack_ref JSON must be object")
        return dict(decoded)
    return None


def _artifact_root(runtime_context: Mapping[str, object], runtime_vars: Mapping[str, object]) -> Path:
    value = _text(runtime_context, "selection_artifact_root") or _text(runtime_vars, "selection_artifact_root")
    if value is None:
        return _DEFAULT_ARTIFACT_ROOT
    return Path(value)


def _resolve_ref_to_path(ref: str, *, artifact_root: Path) -> Path:
    prefix = "local://selection/"
    if ref.startswith(prefix):
        relative = ref[len(prefix) :].strip("/")
        segments = [part for part in relative.split("/") if part]
        if not segments or ".." in segments:
            raise SelectionToolError("candidate_pack_integrity_failed", f"unsafe local selection uri: {ref}")
        return artifact_root / Path(*segments)
    return Path(ref)


def _validate_readback_verify_log(*, data_path: Path, expected_sha256: str, code: str) -> None:
    verify_path = _readback_verify_path(data_path)
    if not verify_path.exists():
        raise SelectionToolError(code, f"missing readback verify log: {verify_path}")
    payload = _load_json(_read_text(verify_path), code=code, target=f"readback log {verify_path.name}")
    status = _text(payload, "status")
    expected = _text(payload, "expected_sha256")
    readback = _text(payload, "readback_sha256")
    if status != "verified":
        raise SelectionToolError(code, f"readback verify status is not verified: {verify_path}")
    if expected is None or readback is None:
        raise SelectionToolError(code, f"readback verify hash fields missing: {verify_path}")
    if expected != readback or expected != expected_sha256:
        raise SelectionToolError(code, f"readback verify hash mismatch: {verify_path}")


def _readback_verify_path(path: Path) -> Path:
    suffix = path.suffix
    if not suffix:
        return path.with_name(f"{path.name}.readback-verify.json")
    return path.with_suffix(f"{suffix}.readback-verify.json")


def _parse_manifest(payload: Mapping[str, object]) -> CandidatePackManifest:
    try:
        return CandidatePackManifest(
            schema_version=_required_text(payload, "schema_version", code="candidate_pack_integrity_failed"),
            selection_run_id=_required_text(payload, "selection_run_id", code="candidate_pack_integrity_failed"),
            market=SelectionMarket(_required_text(payload, "market", code="candidate_pack_integrity_failed")),
            profile=SelectionProfile(_required_text(payload, "profile", code="candidate_pack_integrity_failed")),
            trade_date=_required_text(payload, "trade_date", code="candidate_pack_integrity_failed"),
            candidate_count=int(payload.get("candidate_count")),
            source_lineage_refs=tuple(_string_tuple(payload.get("source_lineage_refs"))),
            pack_body_sha256=_required_text(payload, "pack_body_sha256", code="candidate_pack_integrity_failed"),
            strategy_config_ref=_required_text(payload, "strategy_config_ref", code="candidate_pack_integrity_failed"),
            readback_status=CandidatePackReadbackStatus(
                _required_text(payload, "readback_status", code="candidate_pack_integrity_failed")
            ),
            strategy_config_version=_text(payload, "strategy_config_version") or "cn_a.selection_strategy.v1",
            weight_version=_text(payload, "weight_version") or "cn_a.selection_weights.v1",
            candidate_scores_ref=_text(payload, "candidate_scores_ref"),
            stable_top20_rule=_optional_mapping(payload.get("stable_top20_rule")),
            stage=_required_text(payload, "stage", code="candidate_pack_integrity_failed"),
            target=_required_text(payload, "target", code="candidate_pack_integrity_failed"),
        )
    except (TypeError, ValueError) as exc:
        raise SelectionToolError("candidate_pack_integrity_failed", str(exc)) from exc


def _validate_model_visible_body(body_md: str) -> None:
    lowered = body_md.lower()
    for token in _FORBIDDEN_MODEL_VISIBLE_TERMS:
        if token.lower() in lowered:
            raise SelectionToolError(
                "candidate_pack_forbidden_material",
                f"candidate pack body contains forbidden protocol token: {token}",
            )
    raw_field = _reader_visible_raw_field_name(body_md)
    if raw_field is not None:
        raise SelectionToolError(
            "candidate_pack_forbidden_material",
            f"candidate pack body contains reader-visible raw field name: {raw_field}",
        )
    for label in _REQUIRED_MODEL_VISIBLE_LABELS:
        if label not in body_md:
            raise SelectionToolError(
                "candidate_pack_integrity_failed",
                f"candidate pack body missing model-visible label: {label}",
            )


def _model_visible_candidate_pack_body(
    *,
    candidate_pack_ref: CandidatePackRef,
    artifact_root: Path,
    fallback_body: str,
    manifest: CandidatePackManifest,
) -> str:
    if _has_required_model_visible_labels(fallback_body):
        return fallback_body
    summary_path = _resolve_ref_to_path(candidate_pack_ref.pack_summary_ref, artifact_root=artifact_root)
    if summary_path.exists():
        summary_text = _read_text(summary_path)
        if _has_required_model_visible_labels(summary_text):
            return summary_text
    rebuilt = _rebuild_candidate_pack_body_from_json(
        candidate_pack_ref=candidate_pack_ref,
        artifact_root=artifact_root,
        manifest=manifest,
    )
    return rebuilt if rebuilt is not None else fallback_body


def _has_required_model_visible_labels(body_md: str) -> bool:
    return all(label in body_md for label in _REQUIRED_MODEL_VISIBLE_LABELS) and _reader_visible_raw_field_name(body_md) is None


def _reader_visible_raw_field_name(body_md: str) -> str | None:
    for key in _READER_VISIBLE_RAW_FIELD_NAMES:
        for marker in (f'"{key}"', f"'{key}'", f"{key}=", f"{key}:"):
            if marker in body_md:
                return key
    return None


def _rebuild_candidate_pack_body_from_json(
    *,
    candidate_pack_ref: CandidatePackRef,
    artifact_root: Path,
    manifest: CandidatePackManifest,
) -> str | None:
    json_path = _candidate_pack_json_path(candidate_pack_ref, artifact_root=artifact_root)
    if json_path is None:
        return None
    try:
        payload = _load_json(_read_text(json_path), code="candidate_pack_integrity_failed", target="candidate pack json")
    except SelectionToolError:
        return None
    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        return None
    strategy_config_version = _first_text(
        payload.get("strategy_config_version"),
        manifest.strategy_config_version,
        _first_candidate_value(candidates, "strategy_config_version"),
        DEFAULT_SELECTION_STRATEGY_CONFIG_VERSION,
    )
    weight_version = _first_text(
        payload.get("weight_version"),
        manifest.weight_version,
        _first_candidate_value(candidates, "weight_version"),
        DEFAULT_SELECTION_WEIGHT_VERSION,
    )
    trade_date = _first_text(payload.get("trade_date"), manifest.trade_date)
    market = _first_text(payload.get("market"), manifest.market.value)
    candidate_count = _first_text(payload.get("candidate_count"), manifest.candidate_count, len(candidates))
    lines = [
        "# A股候选事实包",
        "",
        "## 本轮范围",
        f"- 交易日：{trade_date}",
        f"- 市场：{market}",
        f"- 候选数量：{candidate_count}",
        f"- 策略配置版本：{strategy_config_version}",
        f"- 权重版本：{weight_version}",
        "",
        "## 候选事实表",
        "| 排名 | 股票代码 | 股票名称 | 行业 | 总分 | 分项得分 | 策略来源 | 策略变体 | 命中字段 | 实际指标值 | 风险扣分 | 数据缺口扣分 | 排序 tie-break 字段 | 数据质量 | 读者化来源摘要 |",
        "| --- | --- | --- | --- | ---: | --- | --- | --- | --- | --- | ---: | ---: | --- | --- | --- |",
    ]
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        row = _candidate_row_summary(candidate)
        lines.append(
            f"| {row['rank']} | {row['ticker']} | {row['company_name']} | {row['industry']} | {row['total_score']} | "
            f"{row['component_scores']} | {row['strategy_sources']} | {row['strategy_variants']} | {row['hit_fields']} | "
            f"{row['actual_metric_values']} | {row['risk_penalty']} | {row['data_gap_penalty']} | {row['tie_break_fields']} | "
            f"{row['data_quality']} | {row['source_summary']} |"
        )
    lines.extend(
        (
            "",
            "## 策略命中明细",
            *_strategy_hit_lines(candidates),
            "",
            "## 排序与扣分说明",
            "- 总分：按已批准权重对同一批次特征值进行确定性计算。",
            "- 分项得分：展示可复算的策略覆盖、趋势、流动性、主题、证据完整度等子项。",
            "- 风险扣分与数据缺口扣分：仅展示确定性扣分值；缺字段时显示为空。",
            "- 排序 tie-break 字段：同分时使用的确定性排序字段值。",
            "",
            "## 字段说明",
            "- 策略来源与策略变体：来自已批准策略配置中的命中 id 解析结果。",
            "- 命中字段与实际指标值：展示本轮候选行已有的可复算字段值。",
            "- 数据质量：仅描述样本完整性，不包含研究结论。",
            "",
            "## 数据质量摘要",
            _reader_friendly_summary_text(
                _first_text(payload.get("data_quality_summary"), "数据质量：候选包未提供汇总文本。")
            ),
            "",
            "## 来源摘要",
            _reader_friendly_summary_text(_first_text(payload.get("source_summary"), "来源摘要：候选包未提供汇总文本。")),
        )
    )
    return "\n".join(lines).strip()


def _candidate_pack_json_path(candidate_pack_ref: CandidatePackRef, *, artifact_root: Path) -> Path | None:
    paths = (
        _resolve_ref_to_path(candidate_pack_ref.pack_summary_ref, artifact_root=artifact_root).with_name("candidate-pack.json"),
        _resolve_ref_to_path(candidate_pack_ref.l1_uri, artifact_root=artifact_root).with_name("candidate-pack.json"),
    )
    for path in paths:
        if path.is_file():
            return path
    return None


def _candidate_row_summary(candidate: Mapping[str, object]) -> dict[str, str]:
    features = _loose_mapping(candidate.get("feature_values"))
    strategy_hits = _loose_string_tuple(candidate.get("strategy_hits"))
    component_scores = _loose_mapping(candidate.get("component_scores")) or _legacy_component_scores(features)
    actual_metric_values = _loose_mapping(candidate.get("actual_metric_values")) or features
    hit_fields = _loose_mapping(candidate.get("hit_fields")) or _legacy_hit_fields(features)
    tie_break_fields = _loose_mapping(candidate.get("tie_break_fields")) or _legacy_tie_break_fields(features)
    return {
        "rank": _first_text(candidate.get("rank"), "-"),
        "ticker": _first_text(candidate.get("ticker"), "-"),
        "company_name": _first_text(candidate.get("company_name"), "-"),
        "industry": _first_text(candidate.get("industry"), "-"),
        "total_score": _format_candidate_number(_first_value(candidate.get("total_score"), features.get("score"))),
        "component_scores": _format_visible_mapping(component_scores),
        "strategy_sources": _join_or_dash(_strategy_sources_from_hits(strategy_hits)),
        "strategy_variants": _join_or_dash(_strategy_variants_from_hits(strategy_hits)),
        "hit_fields": _format_visible_mapping(hit_fields),
        "actual_metric_values": _format_visible_mapping(actual_metric_values),
        "risk_penalty": _format_candidate_number(
            _first_value(candidate.get("risk_penalty"), features.get("risk_penalty_score"), features.get("risk_penalty"))
        ),
        "data_gap_penalty": _format_candidate_number(
            _first_value(
                candidate.get("data_gap_penalty"),
                features.get("data_gap_penalty_score"),
                features.get("data_gap_penalty"),
            )
        ),
        "tie_break_fields": _format_visible_mapping(tie_break_fields),
        "data_quality": _first_text(candidate.get("data_quality"), "-"),
        "source_summary": _first_text(candidate.get("source_summary"), "-"),
    }


def _legacy_component_scores(values: Mapping[str, object]) -> dict[str, object]:
    keys = {
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
    return {key: value for key, value in values.items() if key in keys or key.endswith("_subscore")}


def _legacy_hit_fields(values: Mapping[str, object]) -> dict[str, object]:
    return {
        key: value
        for key, value in values.items()
        if key.startswith("hit_") or key.startswith("strategy_hit_") or key.endswith("_hit")
    }


def _legacy_tie_break_fields(values: Mapping[str, object]) -> dict[str, object]:
    candidates = ("score", "amount", "volume", "vol_ratio", "data_gap_penalty_score", "risk_penalty_score")
    return {key: values[key] for key in candidates if key in values}


def _strategy_hit_lines(candidates: list[object]) -> tuple[str, ...]:
    seen: set[tuple[str, str]] = set()
    lines: list[str] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        for hit in _loose_string_tuple(candidate.get("strategy_hits")):
            source, variant = _split_strategy_hit_text(hit)
            key = (source, variant)
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"- 来源：{source or '-'}；策略变体：{variant or hit}。")
    return tuple(lines) if lines else ("- 本轮候选未记录策略命中。",)


def _strategy_sources_from_hits(hits: tuple[str, ...]) -> tuple[str, ...]:
    return _dedupe(tuple(source for source, _variant in (_split_strategy_hit_text(hit) for hit in hits) if source))


def _strategy_variants_from_hits(hits: tuple[str, ...]) -> tuple[str, ...]:
    return _dedupe(tuple(variant for _source, variant in (_split_strategy_hit_text(hit) for hit in hits) if variant))


def _split_strategy_hit_text(value: str) -> tuple[str, str]:
    text = value.strip()
    for separator in ("::", ":"):
        if separator in text:
            source, variant = text.split(separator, 1)
            return source.strip(), variant.strip()
    return "", text


def _loose_mapping(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, dict) else {}


def _loose_string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def _first_candidate_value(candidates: list[object], key: str) -> object | None:
    for candidate in candidates:
        if isinstance(candidate, dict) and candidate.get(key) is not None:
            return candidate[key]
    return None


def _first_value(*values: object) -> object | None:
    for value in values:
        if value is not None:
            return value
    return None


def _first_text(*values: object) -> str:
    value = _first_value(*values)
    if value is None:
        return "-"
    text = str(value).strip()
    return text if text else "-"


def _format_visible_mapping(values: Mapping[str, object]) -> str:
    if not values:
        return "-"
    payload = {_reader_visible_label(str(key)): _model_visible_value(value) for key, value in sorted(values.items())}
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _reader_visible_label(key: str) -> str:
    return _READER_VISIBLE_FIELD_LABELS.get(key, key)


def _reader_friendly_summary_text(text: str) -> str:
    out = text.replace("BLOCKER", "阻断").replace("WARN", "提示")
    out = out.replace(" provider ", " 数据源 ").replace("provider 调用", "数据源调用")
    return out


def _format_candidate_number(value: object) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "-"
    if isinstance(value, int | float):
        return f"{float(value):.6f}"
    try:
        return f"{float(str(value)):.6f}"
    except ValueError:
        return str(value)


def _model_visible_value(value: object) -> float | int | str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, int | float | str):
        return value
    return str(value)


def _join_or_dash(values: tuple[str, ...]) -> str:
    return "、".join(values) if values else "-"


def _dedupe(values: tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return tuple(out)


def _load_json(content: str, *, code: str, target: str) -> dict[str, object]:
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise SelectionToolError(code, f"invalid json in {target}") from exc
    if not isinstance(parsed, dict):
        raise SelectionToolError(code, f"{target} must be json object")
    return parsed


def _mapping(payload: Mapping[str, object], field_name: str) -> Mapping[str, object]:
    value = payload.get(field_name, {})
    if not isinstance(value, dict):
        raise SelectionToolError("selection_runtime_context_missing", f"{field_name} must be object")
    return value


def _optional_mapping(value: object) -> Mapping[str, object] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise SelectionToolError("candidate_pack_integrity_failed", "stable_top20_rule must be object")
    return value


def _text(payload: Mapping[str, object], field_name: str) -> str | None:
    value = payload.get(field_name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise SelectionToolError("selection_runtime_context_missing", f"{field_name} must be string")
    text = value.strip()
    return text or None


def _required_text(payload: Mapping[str, object], field_name: str, *, code: str) -> str:
    value = payload.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise SelectionToolError(code, f"{field_name} is required")
    return value.strip()


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise SelectionToolError("candidate_pack_lineage_incomplete", "source_lineage_refs must be list[str]")
    items: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise SelectionToolError("candidate_pack_lineage_incomplete", "source_lineage_refs must be list[str]")
        items.append(item.strip())
    return tuple(items)


def _read_text(path: Path) -> str:
    if not path.exists():
        raise SelectionToolError("candidate_pack_not_approved", f"missing candidate pack artifact: {path}")
    return path.read_text(encoding="utf-8")


def _parse_iso_timestamp(value: str) -> datetime:
    normalized = value.replace("Z", "+00:00")
    dt = datetime.fromisoformat(normalized)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            raise SelectionToolError("selection_runtime_context_missing", "stdin payload must be json object")
        result = execute_selection_candidate_pack_tool(payload)
    except SelectionToolError as exc:
        result = {
            "ok": False,
            "error": {
                "code": exc.code,
                "message": exc.message,
            },
        }
    except Exception as exc:  # pragma: no cover - defensive fallback
        result = {
            "ok": False,
            "error": {
                "code": "candidate_pack_integrity_failed",
                "message": str(exc),
            },
        }
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
