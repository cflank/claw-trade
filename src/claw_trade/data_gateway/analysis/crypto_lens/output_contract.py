from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass
from enum import StrEnum
import hashlib
import json
from typing import Any, Literal, Mapping

from claw_trade.data_gateway.models import Conflict, DataGap, Readiness


ENGINE_NAME = "claw-trade-crypto-lens"
ENGINE_VERSION = "2026.05.18"
AnalysisStatus = Literal["ready", "partial", "insufficient"]


class AnalysisState(StrEnum):
    READY = "ready"
    PARTIAL = "partial"
    GAP = "gap"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True)
class AnalysisSection:
    status: AnalysisState
    summary: str
    evidence: Mapping[str, Any]
    gap_ids: tuple[str, ...]
    notes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "summary": self.summary,
            "evidence": _normalize_for_json(self.evidence),
            "gap_ids": self.gap_ids,
            "notes": self.notes,
        }


@dataclass(frozen=True)
class CryptoLensAnalysisResult:
    analysis_id: str
    run_id: str
    call_id: str
    ticker: str
    as_of: str
    engine_name: str
    engine_version: str
    engine_source_ref: str
    input_normalized_refs: tuple[str, ...]
    status: AnalysisStatus
    readiness: Readiness
    indicator_coverage: Mapping[str, Any]
    market_structure: AnalysisSection
    technical_patterns: AnalysisSection
    derivatives_context: AnalysisSection
    liquidation_context: AnalysisSection
    onchain_context: AnalysisSection
    macro_context: AnalysisSection
    ahr999_context: AnalysisSection
    conditional_trade_framework: Mapping[str, Any]
    data_gaps: tuple[DataGap, ...]
    conflicts: tuple[Conflict, ...]
    output_hash: str = ""
    analysis_evidence_ref: str | None = None

    def __post_init__(self) -> None:
        if self.status not in ("ready", "partial", "insufficient"):
            raise ValueError("CryptoLensAnalysisResult.status must be ready, partial, or insufficient")
        if not self.output_hash:
            object.__setattr__(self, "output_hash", stable_payload_hash(self.as_dict(include_output_hash=False)))

    @property
    def normalized_refs(self) -> tuple[str, ...]:
        return self.input_normalized_refs

    @property
    def input_data_gap_ids(self) -> tuple[str, ...]:
        return tuple(gap.gap_id for gap in self.data_gaps)

    @property
    def input_conflict_ids(self) -> tuple[str, ...]:
        return tuple(conflict.conflict_id for conflict in self.conflicts)

    def as_dict(self, *, include_output_hash: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "analysis_id": self.analysis_id,
            "run_id": self.run_id,
            "call_id": self.call_id,
            "ticker": self.ticker,
            "as_of": self.as_of,
            "engine_name": self.engine_name,
            "engine_version": self.engine_version,
            "engine_source_ref": self.engine_source_ref,
            "input_normalized_refs": self.input_normalized_refs,
            "status": self.status,
            "readiness": _normalize_for_json(self.readiness),
            "indicator_coverage": _normalize_for_json(self.indicator_coverage),
            "market_structure": self.market_structure.as_dict(),
            "technical_patterns": self.technical_patterns.as_dict(),
            "derivatives_context": self.derivatives_context.as_dict(),
            "liquidation_context": self.liquidation_context.as_dict(),
            "onchain_context": self.onchain_context.as_dict(),
            "macro_context": self.macro_context.as_dict(),
            "ahr999_context": self.ahr999_context.as_dict(),
            "conditional_trade_framework": _normalize_for_json(self.conditional_trade_framework),
            "data_gaps": _normalize_for_json(self.data_gaps),
            "conflicts": _normalize_for_json(self.conflicts),
        }
        if include_output_hash:
            payload["output_hash"] = self.output_hash
        return payload


def stable_payload_hash(payload: Mapping[str, Any]) -> str:
    rendered = json.dumps(
        _normalize_for_json(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def _normalize_for_json(value: object) -> object:
    if hasattr(value, "value") and isinstance(getattr(value, "value"), str):
        return getattr(value, "value")
    if dataclass_is_instance(value):
        return _normalize_for_json(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _normalize_for_json(nested) for key, nested in value.items()}
    if isinstance(value, tuple | list):
        return [_normalize_for_json(nested) for nested in value]
    return value


def dataclass_is_instance(value: object) -> bool:
    return hasattr(value, "__dataclass_fields__") and not isinstance(value, type)
