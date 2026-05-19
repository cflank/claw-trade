from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from claw_trade.data_gateway.models import NormalizedCryptoMarketBundle

from .engine import analyze_crypto_lens
from .evidence import CryptoLensAnalysisEvidenceStore
from .input_contract import CryptoLensInput
from .output_contract import CryptoLensAnalysisResult, stable_payload_hash


class CryptoLensAnalysisFailed(RuntimeError):
    def __init__(self, reason: str, *, analysis_evidence_ref: str | None = None) -> None:
        super().__init__(reason)
        self.analysis_evidence_ref = analysis_evidence_ref


def analyze_openbb_crypto_lens_bundle(
    input: NormalizedCryptoMarketBundle | CryptoLensInput,
    *,
    evidence_store: CryptoLensAnalysisEvidenceStore | None = None,
) -> CryptoLensAnalysisResult:
    crypto_lens_input = _coerce_input(input)
    input_hash = stable_payload_hash(crypto_lens_input.as_dict())
    engine_source_ref = _engine_source_hash()
    try:
        result = analyze_crypto_lens(crypto_lens_input, engine_source_ref=engine_source_ref)
    except Exception as exc:  # noqa: BLE001
        evidence_ref = None
        if evidence_store is not None:
            evidence_ref = evidence_store.write_failure(
                input=crypto_lens_input,
                input_hash=input_hash,
                engine_source_ref=engine_source_ref,
                failure_reason=str(exc),
            )
        raise CryptoLensAnalysisFailed(str(exc), analysis_evidence_ref=evidence_ref) from exc
    if evidence_store is not None:
        evidence_ref = evidence_store.write(input=crypto_lens_input, result=result, input_hash=input_hash)
        result = replace(result, analysis_evidence_ref=evidence_ref)
    return result


def _coerce_input(input: NormalizedCryptoMarketBundle | CryptoLensInput) -> CryptoLensInput:
    if isinstance(input, CryptoLensInput):
        return input
    if isinstance(input, NormalizedCryptoMarketBundle):
        return CryptoLensInput.from_normalized_bundle(input)
    raise TypeError("analyze_openbb_crypto_lens_bundle input must be a normalized crypto bundle")


def _engine_source_hash() -> str:
    root = Path(__file__).resolve().parent
    files = sorted(path for path in root.rglob("*.py") if path.name != "evidence.py")
    payload = {
        path.relative_to(root).as_posix(): path.read_text(encoding="utf-8")
        for path in files
    }
    return "source_hash:" + stable_payload_hash(payload)
