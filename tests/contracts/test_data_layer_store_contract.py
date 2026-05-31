from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from claw_trade.data_gateway.analysis.crypto_lens import (
    CRYPTO_LENS_ANALYSIS_EVIDENCE as ANALYSIS_CRYPTO_LENS_ANALYSIS_EVIDENCE,
    CryptoLensAnalysisEvidenceStore,
)
from claw_trade.data_gateway.models import (
    PrioritySource,
    ProviderAttempt,
    ProviderKind,
    ProviderStatus,
    SourceRole,
)
from claw_trade.data_gateway.store import (
    MongoAttemptStore,
    MongoCacheStore,
    MongoNormalizedStore,
    MongoProviderHttpEvidenceStore,
    MongoProviderManifestStore,
    MongoRateLimitStore,
    MongoRawPayloadStore,
    MongoRunProviderPlanStore,
    MongoSingleFlightCoordinator,
    MongoValidationReceiptStore,
)
from claw_trade.data_gateway.store.mongo import (
    CRYPTO_LENS_ANALYSIS_EVIDENCE,
    OPENBB_CACHE_ENTRIES,
    OPENBB_NORMALIZED,
    OPENBB_PROVIDER_ATTEMPTS,
    OPENBB_PROVIDER_HTTP_EVIDENCE,
    OPENBB_PROVIDER_MANIFESTS,
    OPENBB_PROVIDER_VALIDATION_RECEIPTS,
    OPENBB_RATE_LIMITS,
    OPENBB_RAW_PAYLOADS,
    OPENBB_RUN_PROVIDER_PLANS,
    OPENBB_SINGLE_FLIGHT_CALLS,
    openbb_collection_indexes,
)
from claw_trade.selection.data_job import SelectionDataJob
from claw_trade.selection.store import restore_selection_run_store


EXPECTED_COLLECTIONS = (
    OPENBB_PROVIDER_MANIFESTS,
    OPENBB_PROVIDER_VALIDATION_RECEIPTS,
    OPENBB_PROVIDER_ATTEMPTS,
    OPENBB_PROVIDER_HTTP_EVIDENCE,
    OPENBB_RAW_PAYLOADS,
    OPENBB_CACHE_ENTRIES,
    OPENBB_NORMALIZED,
    OPENBB_RATE_LIMITS,
    OPENBB_RUN_PROVIDER_PLANS,
    OPENBB_SINGLE_FLIGHT_CALLS,
    CRYPTO_LENS_ANALYSIS_EVIDENCE,
)


class _Collection:
    def __init__(self, *, name: str = "collection") -> None:
        self.name = name


def test_store_collection_constants_are_the_existing_data_layer_contract() -> None:
    assert EXPECTED_COLLECTIONS == (
        "openbb_provider_manifests",
        "openbb_provider_validation_receipts",
        "openbb_provider_attempts",
        "openbb_provider_http_evidence",
        "openbb_raw_payloads",
        "openbb_cache_entries",
        "openbb_normalized",
        "openbb_rate_limits",
        "openbb_run_provider_plans",
        "openbb_single_flight_calls",
        "crypto_lens_analysis_evidence",
    )
    assert tuple(entry.collection for entry in openbb_collection_indexes()) == EXPECTED_COLLECTIONS
    assert ANALYSIS_CRYPTO_LENS_ANALYSIS_EVIDENCE == CRYPTO_LENS_ANALYSIS_EVIDENCE


def test_store_writers_resolve_to_existing_collections_only() -> None:
    collection = _Collection()
    attempt_store = MongoAttemptStore(collection)

    stores = {
        MongoProviderManifestStore(collection).collection_name,
        MongoValidationReceiptStore(collection).collection_name,
        MongoRunProviderPlanStore(collection).collection_name,
        attempt_store.collection_name,
        MongoProviderHttpEvidenceStore(collection).collection_name,
        MongoRawPayloadStore(collection).collection_name,
        MongoNormalizedStore(collection).collection_name,
        MongoCacheStore(collection).collection_name,
        MongoRateLimitStore(collection).collection_name,
        MongoSingleFlightCoordinator(collection=collection, attempt_store=attempt_store).collection_name,
        CRYPTO_LENS_ANALYSIS_EVIDENCE,
    }
    CryptoLensAnalysisEvidenceStore(_Collection(name=CRYPTO_LENS_ANALYSIS_EVIDENCE))

    assert stores == set(EXPECTED_COLLECTIONS)


def test_no_parallel_data_layer_collection_names_in_t2_sources() -> None:
    source_paths = (
        Path("src/claw_trade/data_gateway/store"),
        Path("src/claw_trade/selection/store.py"),
        Path("src/claw_trade/selection/data_job.py"),
    )
    text = "\n".join(
        path.read_text(encoding="utf-8")
        for root in source_paths
        for path in (root.rglob("*.py") if root.is_dir() else (root,))
    )

    for forbidden in (
        "market_bars",
        "select_data_plans",
        "provider_cooldowns",
        "http_cache",
        "provider_cache",
    ):
        assert forbidden not in text


def test_non_remote_attempt_states_cannot_be_store_remote_success() -> None:
    attempts = (
        _attempt(status=ProviderStatus.CACHE_HIT, from_cache=True, cache_status=ProviderStatus.CACHE_HIT),
        _attempt(status=ProviderStatus.RATE_LIMITED, raw_ref=None, normalized_ref=None),
        _attempt(status=ProviderStatus.CACHED_EMPTY, raw_ref=None, normalized_ref=None),
        _attempt(status=ProviderStatus.COOLDOWN_SKIPPED, raw_ref=None, normalized_ref=None),
        _attempt(
            status=ProviderStatus.SHARED_RESULT,
            single_flight_role="consumer",
            shared_from_attempt_id="attempt-owner",
        ),
    )

    assert {attempt.status for attempt in attempts} == {
        ProviderStatus.CACHE_HIT,
        ProviderStatus.RATE_LIMITED,
        ProviderStatus.CACHED_EMPTY,
        ProviderStatus.COOLDOWN_SKIPPED,
        ProviderStatus.SHARED_RESULT,
    }
    assert all(not attempt.remote_success for attempt in attempts)


def test_remote_success_attempt_requires_raw_and_normalized_refs() -> None:
    with pytest.raises(ValueError, match="remote_success requires raw_ref and normalized_ref"):
        _attempt(
            status=ProviderStatus.REMOTE_SUCCESS,
            raw_ref="mongo://openbb_raw_payloads/raw-1",
            normalized_ref=None,
        )


def test_selection_evidence_boundary_is_existing_files_not_mongo_plan_collection() -> None:
    store_source = Path("src/claw_trade/selection/store.py").read_text(encoding="utf-8")
    data_job_source = Path("src/claw_trade/selection/data_job.py").read_text(encoding="utf-8")

    assert restore_selection_run_store.__name__ in store_source
    assert '"runs/selection"' in store_source
    assert '"store" / "data-runs"' in store_source
    assert SelectionDataJob.__name__ in data_job_source
    assert "selection_runs_root" in store_source
    assert "select_data_plans" not in store_source + data_job_source


def _attempt(
    *,
    status: ProviderStatus,
    raw_ref: str | None = "mongo://openbb_raw_payloads/raw-1",
    normalized_ref: str | None = "mongo://openbb_normalized/norm-1",
    from_cache: bool = False,
    cache_status: ProviderStatus | None = None,
    single_flight_role: str = "none",
    shared_from_attempt_id: str | None = None,
) -> ProviderAttempt:
    return ProviderAttempt(
        attempt_id=f"attempt-{status.value}",
        run_id="run-store-contract",
        call_id="call-store-contract",
        worker_id="market_analyst",
        pack="market",
        provider="tushare",
        adapter_id="project.tushare",
        adapter_kind=ProviderKind.PROJECT_EXTENSION.value,
        provider_kind=ProviderKind.PROJECT_EXTENSION,
        provider_config_version="cfg-v1",
        endpoint="daily",
        source_role=SourceRole.MARKET_DATA,
        started_at="2026-05-29T10:00:00+00:00",
        finished_at="2026-05-29T10:00:00+00:00",
        status=status,
        required=True,
        attempt_required=True,
        coverage_group=None,
        coverage_quorum=None,
        priority_source=PrioritySource.SYSTEM_DEFAULT,
        user_preferred=False,
        from_cache=from_cache,
        cache_status=cache_status,
        single_flight_role=single_flight_role,  # type: ignore[arg-type]
        shared_from_attempt_id=shared_from_attempt_id,
        latency_ms=0,
        row_count=1 if raw_ref or normalized_ref else None,
        raw_ref=raw_ref,
        normalized_ref=normalized_ref,
        error_code=None if raw_ref or normalized_ref else status.value,
        error_message=None,
        schema_id="market.daily.v1",
        license_note="ok",
    )
