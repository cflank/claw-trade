from __future__ import annotations

from pathlib import Path
from typing import Sequence

from claw_trade.data_gateway.models import DataResult

from .engine import analyze_crypto_lens
from .input_contract import CryptoLensInput
from .output_contract import CryptoLensAnalysisResult, stable_payload_hash


class CryptoLensAnalysisFailed(RuntimeError):
    def __init__(self, reason: str, *, analysis_evidence_ref: str | None = None) -> None:
        super().__init__(reason)
        self.analysis_evidence_ref = analysis_evidence_ref


def analyze_crypto_lens_input(
    input: CryptoLensInput,
) -> CryptoLensAnalysisResult:
    engine_source_ref = _engine_source_hash()
    try:
        return analyze_crypto_lens(input, engine_source_ref=engine_source_ref)
    except Exception as exc:  # noqa: BLE001
        raise CryptoLensAnalysisFailed(str(exc)) from exc


def analyze_crypto_lens_data_results(
    results: Sequence[DataResult],
    *,
    ticker: str,
    quote: str,
    run_id: str,
    call_id: str,
    as_of: str,
    start_date: str = "",
    end_date: str = "",
) -> CryptoLensAnalysisResult:
    input = CryptoLensInput.from_data_results(
        results,
        ticker=ticker,
        quote=quote,
        run_id=run_id,
        call_id=call_id,
        as_of=as_of,
        start_date=start_date,
        end_date=end_date,
    )
    return analyze_crypto_lens_input(input)


def _engine_source_hash() -> str:
    root = Path(__file__).resolve().parent
    files = sorted(path for path in root.rglob("*.py"))
    payload = {path.relative_to(root).as_posix(): path.read_text(encoding="utf-8") for path in files}
    return "source_hash:" + stable_payload_hash(payload)
