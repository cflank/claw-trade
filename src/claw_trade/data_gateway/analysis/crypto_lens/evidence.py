from __future__ import annotations

from typing import Any

from .input_contract import CryptoLensInput
from .output_contract import CryptoLensAnalysisResult, stable_payload_hash, _normalize_for_json


CRYPTO_LENS_ANALYSIS_EVIDENCE = "crypto_lens_analysis_evidence"
CRYPTO_LENS_NO_NETWORK_ASSERTION = {
    "asserted": True,
    "scope": "crypto_lens_analysis_runtime",
    "network": "not_used",
    "provider_keys": "not_read",
    "mongo": "not_accessed_by_analysis",
}


class CryptoLensAnalysisEvidenceStore:
    def __init__(self, collection: Any) -> None:
        collection_name = getattr(collection, "name", CRYPTO_LENS_ANALYSIS_EVIDENCE)
        if collection_name != CRYPTO_LENS_ANALYSIS_EVIDENCE:
            raise ValueError("CryptoLens analysis evidence must use crypto_lens_analysis_evidence")
        self._collection = collection

    def write(
        self,
        *,
        input: CryptoLensInput,
        result: CryptoLensAnalysisResult,
        input_hash: str,
    ) -> str:
        document_id = f"{result.analysis_id}:evidence"
        result_ref = f"crypto_lens_analysis_result://{result.output_hash.removeprefix('sha256:')}"
        result_payload = result.as_dict()
        input_payload = _normalize_for_json(input.as_dict())
        document = {
            "_id": document_id,
            "run_id": input.run_id,
            "call_id": input.call_id,
            "worker_id": "market_analyst",
            "analysis_id": result.analysis_id,
            "engine_name": result.engine_name,
            "engine_version": result.engine_version,
            "engine_source_hash": result.engine_source_ref,
            "input_hash": input_hash,
            "referenced_normalized_refs": input.normalized_refs,
            "output_hash": result.output_hash,
            "no_network_assertion": CRYPTO_LENS_NO_NETWORK_ASSERTION,
            "data_gaps": result_payload["data_gaps"],
            "conflicts": result_payload["conflicts"],
            "analysis_result_ref": result_ref,
            "analysis_result": result_payload,
            "analysis_input": input_payload,
        }
        self._collection.insert_one(document)
        return f"mongo://{CRYPTO_LENS_ANALYSIS_EVIDENCE}/{document_id}"

    def write_failure(
        self,
        *,
        input: CryptoLensInput,
        input_hash: str,
        engine_source_ref: str,
        failure_reason: str,
    ) -> str:
        failure_payload = {
            "run_id": input.run_id,
            "call_id": input.call_id,
            "ticker": input.ticker,
            "status": "failure",
            "failure_reason": failure_reason,
            "referenced_normalized_refs": input.normalized_refs,
        }
        failure_hash = stable_payload_hash(failure_payload)
        document_id = f"crypto_lens:{input.run_id}:{input.call_id}:failure"
        input_payload = _normalize_for_json(input.as_dict())
        document = {
            "_id": document_id,
            "run_id": input.run_id,
            "call_id": input.call_id,
            "worker_id": "market_analyst",
            "analysis_id": f"crypto_lens:{input.run_id}:{input.call_id}",
            "engine_name": "claw-trade-crypto-lens",
            "engine_version": "2026.05.18",
            "engine_source_hash": engine_source_ref,
            "status": "failure",
            "failure_reason": failure_reason,
            "input_hash": input_hash,
            "referenced_normalized_refs": input.normalized_refs,
            "output_hash": None,
            "failure_result_hash": failure_hash,
            "no_network_assertion": CRYPTO_LENS_NO_NETWORK_ASSERTION,
            "data_gaps": _normalize_for_json(input.data_gaps),
            "conflicts": _normalize_for_json(input.conflicts),
            "analysis_result_ref": None,
            "failure_result_ref": f"crypto_lens_analysis_failure://{failure_hash.removeprefix('sha256:')}",
            "analysis_input": input_payload,
        }
        self._collection.insert_one(document)
        return f"mongo://{CRYPTO_LENS_ANALYSIS_EVIDENCE}/{document_id}"
