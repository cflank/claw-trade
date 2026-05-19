from __future__ import annotations

from typing import Any

import pytest

import claw_trade.data_gateway.analysis.crypto_lens as crypto_lens_package
from claw_trade.data_gateway.analysis.crypto_lens import adapter as adapter_module
from claw_trade.data_gateway.analysis.crypto_lens import (
    CRYPTO_LENS_ANALYSIS_EVIDENCE,
    CryptoLensAnalysisFailed,
    CryptoLensAnalysisEvidenceStore,
    analyze_openbb_crypto_lens_bundle,
)
from claw_trade.data_gateway.models import (
    Conflict,
    CryptoDomainBundle,
    DataGap,
    DataGapReason,
    DomainReadiness,
    FreshnessStatus,
    GapSeverity,
    Market,
    NormalizedCryptoMarketBundle,
    PackDomain,
    ProviderSourceRef,
    ProviderStatus,
    SourceRole,
)
from claw_trade.data_gateway.store.mongo import (
    CRYPTO_LENS_ANALYSIS_EVIDENCE as STORE_CRYPTO_LENS_ANALYSIS_EVIDENCE,
    openbb_collection_indexes,
)


class _Collection:
    name = CRYPTO_LENS_ANALYSIS_EVIDENCE

    def __init__(self) -> None:
        self.docs: list[dict[str, Any]] = []

    def insert_one(self, document: dict[str, Any]) -> object:
        self.docs.append(document)
        return object()


class _WrongCollection(_Collection):
    name = "openbb_provider_http_evidence"


def _full_domains() -> CryptoDomainBundle:
    candles = [{"close": 62000.0 + float(i) * 50.0} for i in range(64)]
    return CryptoDomainBundle(
        market={"price": 68000.0, "volume_24h": 123456789.0},
        ohlcv={"timeframe": "4h", "rows": 64, "candles": candles, "indicators": {"rsi": 58.0}},
        derivatives={"funding": 0.0003, "oi": 54321.0, "long_short_ratio": 1.08},
        liquidation_map={"largest_cluster": {"price": 67000.0, "size": 12000000.0}},
        onchain={"exchange_netflow": -2500.0},
        macro={"dxy": 103.2},
        events={"upcoming_unlock_days": 12},
        ahr999={"value": 1.12},
    )


def _full_status() -> dict[str, DomainReadiness]:
    return {
        "market": DomainReadiness.READY,
        "ohlcv": DomainReadiness.READY,
        "derivatives": DomainReadiness.READY,
        "liquidation_map": DomainReadiness.READY,
        "onchain": DomainReadiness.READY,
        "macro": DomainReadiness.READY,
        "events": DomainReadiness.READY,
        "ahr999": DomainReadiness.READY,
    }


def _bundle() -> NormalizedCryptoMarketBundle:
    return NormalizedCryptoMarketBundle(
        run_id="run-crypto-lens-adapter",
        call_id="call-crypto-lens-adapter",
        ticker="BTC-USD",
        market=Market.CRYPTO,
        quote="USD",
        as_of="2026-05-18T12:00:00+00:00",
        start_date="2026-05-01",
        end_date="2026-05-18",
        freshness=FreshnessStatus.FRESH_REMOTE,
        domains=_full_domains(),
        domain_status=_full_status(),
        data_gaps=(
            DataGap(
                gap_id="gap-events",
                domain=PackDomain.MARKET,
                severity=GapSeverity.WARN,
                reason=DataGapReason.EMPTY,
                field_path="events.unlock",
                provider_candidates=("openbb_demo",),
                attempt_ids=("attempt-events",),
                root_cause="events source returned empty",
                next_action="wait next refresh",
            ),
        ),
        conflicts=(
            Conflict(
                conflict_id="conflict-price",
                field_path="market.price",
                values=("68000", "67980"),
                provider_refs=("source://a", "source://b"),
                resolution="prefer source a",
                confidence="medium",
            ),
        ),
        source_refs=(
            ProviderSourceRef(
                ref_id="source://openbb-demo",
                provider="openbb_demo",
                adapter_id="openbb.demo",
                endpoint="crypto_bundle",
                source_role=SourceRole.MARKET_DATA,
                status=ProviderStatus.REMOTE_SUCCESS,
                normalized_ref="mongo://openbb_normalized/sha256:normalized",
            ),
        ),
        attempt_refs=("mongo://openbb_provider_attempts/attempt-events",),
        raw_refs=("mongo://openbb_raw_payloads/sha256:raw",),
        normalized_refs=("mongo://openbb_normalized/sha256:normalized",),
    )


def test_openbb_crypto_lens_entry_writes_isolated_analysis_evidence() -> None:
    collection = _Collection()
    result = analyze_openbb_crypto_lens_bundle(
        _bundle(),
        evidence_store=CryptoLensAnalysisEvidenceStore(collection),
    )

    assert result.analysis_id == "crypto_lens:run-crypto-lens-adapter:call-crypto-lens-adapter"
    assert result.engine_name == "claw-trade-crypto-lens"
    assert result.engine_version
    assert result.engine_source_ref.startswith("source_hash:sha256:")
    assert result.output_hash.startswith("sha256:")
    assert result.input_normalized_refs == ("mongo://openbb_normalized/sha256:normalized",)
    assert result.data_gaps[0].gap_id == "gap-events"
    assert result.conflicts[0].conflict_id == "conflict-price"

    [document] = collection.docs
    assert document["run_id"] == "run-crypto-lens-adapter"
    assert document["call_id"] == "call-crypto-lens-adapter"
    assert document["worker_id"] == "market_analyst"
    assert document["analysis_id"] == result.analysis_id
    assert document["engine_name"] == result.engine_name
    assert document["engine_version"] == result.engine_version
    assert document["engine_source_hash"] == result.engine_source_ref
    assert document["input_hash"].startswith("sha256:")
    assert document["referenced_normalized_refs"] == ("mongo://openbb_normalized/sha256:normalized",)
    assert document["output_hash"] == result.output_hash
    assert document["no_network_assertion"]["asserted"] is True
    assert document["no_network_assertion"]["network"] == "not_used"
    assert document["data_gaps"][0]["gap_id"] == "gap-events"
    assert document["conflicts"][0]["conflict_id"] == "conflict-price"
    assert document["analysis_result_ref"].startswith("crypto_lens_analysis_result://")
    assert "source_url" not in document
    assert "response_headers_summary" not in document
    assert "raw_ref" not in document


def test_openbb_crypto_lens_entry_writes_failure_evidence_without_success_result(monkeypatch) -> None:
    collection = _Collection()

    def _raise(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise RuntimeError("engine failed")

    monkeypatch.setattr(adapter_module, "analyze_crypto_lens", _raise)
    with pytest.raises(CryptoLensAnalysisFailed) as exc_info:
        analyze_openbb_crypto_lens_bundle(
            _bundle(),
            evidence_store=CryptoLensAnalysisEvidenceStore(collection),
        )

    assert exc_info.value.analysis_evidence_ref == (
        "mongo://crypto_lens_analysis_evidence/"
        "crypto_lens:run-crypto-lens-adapter:call-crypto-lens-adapter:failure"
    )
    [document] = collection.docs
    assert document["status"] == "failure"
    assert document["failure_reason"] == "engine failed"
    assert document["input_hash"].startswith("sha256:")
    assert document["referenced_normalized_refs"] == ("mongo://openbb_normalized/sha256:normalized",)
    assert document["output_hash"] is None
    assert document["analysis_result_ref"] is None
    assert document["failure_result_ref"].startswith("crypto_lens_analysis_failure://")
    assert document["no_network_assertion"]["asserted"] is True
    assert document["data_gaps"][0]["gap_id"] == "gap-events"
    assert document["conflicts"][0]["conflict_id"] == "conflict-price"
    assert "analysis_result" not in document


def test_crypto_lens_evidence_store_rejects_openbb_provider_collection_name() -> None:
    with pytest.raises(ValueError):
        CryptoLensAnalysisEvidenceStore(_WrongCollection())


def test_crypto_lens_analysis_collection_is_registered_separately_from_openbb_provider_evidence() -> None:
    collection_names = {entry.collection for entry in openbb_collection_indexes()}

    assert STORE_CRYPTO_LENS_ANALYSIS_EVIDENCE == "crypto_lens_analysis_evidence"
    assert STORE_CRYPTO_LENS_ANALYSIS_EVIDENCE in collection_names
    assert STORE_CRYPTO_LENS_ANALYSIS_EVIDENCE != "openbb_provider_http_evidence"
    assert STORE_CRYPTO_LENS_ANALYSIS_EVIDENCE != "openbb_raw_payloads"


def test_crypto_lens_result_has_no_final_investment_decision_fields_or_terms() -> None:
    result = analyze_openbb_crypto_lens_bundle(_bundle())
    payload = result.as_dict()
    rendered = str(payload).upper()

    forbidden_fields = {
        "rating",
        "pm_rating",
        "final_decision",
        "position_size",
        "execution_order",
    }
    assert forbidden_fields.isdisjoint(payload)
    assert "BUY" not in rendered
    assert "HOLD" not in rendered
    assert "SELL" not in rendered


def test_crypto_lens_package_exports_only_adapter_entry_not_engine_shortcut() -> None:
    assert "analyze_openbb_crypto_lens_bundle" in crypto_lens_package.__all__
    assert "analyze_crypto_lens" not in crypto_lens_package.__all__
    assert not hasattr(crypto_lens_package, "analyze_crypto_lens")
