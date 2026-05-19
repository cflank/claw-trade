from __future__ import annotations

import pytest

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


def _full_domains(*, ohlcv_rows: int = 180) -> CryptoDomainBundle:
    return CryptoDomainBundle(
        market={"price": 68000.0, "volume_24h": 123456789.0},
        ohlcv={"timeframe": "4h", "rows": ohlcv_rows},
        derivatives={"funding": 0.0003, "oi": 54321.0},
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


def _bundle(
    *,
    domains: CryptoDomainBundle | None = None,
    domain_status: dict[str, DomainReadiness] | None = None,
    freshness: FreshnessStatus = FreshnessStatus.FRESH_REMOTE,
    conflicts: tuple[Conflict, ...] = (),
) -> NormalizedCryptoMarketBundle:
    return NormalizedCryptoMarketBundle(
        run_id="run-crypto-1",
        call_id="call-crypto-1",
        ticker="BTC-USD",
        market=Market.CRYPTO,
        quote="USD",
        as_of="2026-05-18T12:00:00+00:00",
        start_date="2026-05-01",
        end_date="2026-05-18",
        freshness=freshness,
        domains=domains or _full_domains(),
        domain_status=domain_status or _full_status(),
        data_gaps=(
            DataGap(
                gap_id="gap-demo",
                domain=PackDomain.MARKET,
                severity=GapSeverity.WARN,
                reason=DataGapReason.EMPTY,
                field_path="events.unlock",
                provider_candidates=("openbb_demo",),
                attempt_ids=("attempt-1",),
                root_cause="events source returned empty",
                next_action="wait next refresh",
            ),
        ),
        conflicts=conflicts,
        source_refs=(
            ProviderSourceRef(
                ref_id="source://openbb-demo",
                provider="openbb_demo",
                adapter_id="openbb.demo",
                endpoint="crypto_bundle",
                source_role=SourceRole.MARKET_DATA,
                status=ProviderStatus.REMOTE_SUCCESS,
                normalized_ref="norm://openbb-demo",
            ),
        ),
        attempt_refs=("attempt-1", "attempt-2"),
        raw_refs=("raw://openbb-demo",),
        normalized_refs=("norm://openbb-demo",),
    )


def test_crypto_bundle_contract_accepts_complete_bundle() -> None:
    bundle = _bundle()
    assert bundle.market == Market.CRYPTO
    assert bundle.domains.ohlcv is not None
    assert bundle.domain_status["ahr999"] == DomainReadiness.READY


def test_crypto_bundle_contract_allows_missing_derivatives() -> None:
    domain_status = _full_status()
    domain_status["derivatives"] = DomainReadiness.MISSING
    domains = _full_domains()
    domains = CryptoDomainBundle(**{**domains.__dict__, "derivatives": None})
    bundle = _bundle(domains=domains, domain_status=domain_status)
    assert bundle.domains.derivatives is None


def test_crypto_bundle_contract_allows_missing_liquidation_map() -> None:
    domain_status = _full_status()
    domain_status["liquidation_map"] = DomainReadiness.MISSING
    domains = _full_domains()
    domains = CryptoDomainBundle(**{**domains.__dict__, "liquidation_map": None})
    bundle = _bundle(domains=domains, domain_status=domain_status)
    assert bundle.domains.liquidation_map is None


def test_crypto_bundle_contract_supports_ohlcv_insufficient_status() -> None:
    domain_status = _full_status()
    domain_status["ohlcv"] = DomainReadiness.INSUFFICIENT
    bundle = _bundle(domains=_full_domains(ohlcv_rows=18), domain_status=domain_status)
    assert bundle.domain_status["ohlcv"] == DomainReadiness.INSUFFICIENT


def test_crypto_bundle_contract_supports_stale_freshness() -> None:
    domain_status = _full_status()
    domain_status["market"] = DomainReadiness.STALE
    bundle = _bundle(freshness=FreshnessStatus.STALE_CACHE, domain_status=domain_status)
    assert bundle.freshness == FreshnessStatus.STALE_CACHE


def test_crypto_bundle_contract_supports_provider_conflict() -> None:
    conflict = Conflict(
        conflict_id="conflict-price-1",
        field_path="market.price",
        values=("68000", "67500"),
        provider_refs=("source://a", "source://b"),
        resolution="prefer higher-liquidity source",
        confidence="medium",
    )
    bundle = _bundle(conflicts=(conflict,))
    assert bundle.conflicts[0].conflict_id == "conflict-price-1"


def test_crypto_bundle_worker_material_excludes_raw_refs() -> None:
    bundle = _bundle()
    worker_material = bundle.to_worker_material_contract()
    assert "raw_refs" not in worker_material
    assert worker_material["normalized_refs"] == ("norm://openbb-demo",)


@pytest.mark.parametrize(
    "field_name",
    (
        "provider_raw_payload",
        "raw_payload",
        "debug_envelope",
        "openclaw_provider_payload",
        "openviking_protocol",
        "prompt_material",
        "prompt_material_body",
    ),
)
def test_crypto_bundle_rejects_polluted_input_fields(field_name: str) -> None:
    polluted_market = {"price": 68000.0, field_name: {"x": 1}}
    domains = _full_domains()
    domains = CryptoDomainBundle(**{**domains.__dict__, "market": polluted_market})

    with pytest.raises(ValueError):
        _bundle(domains=domains)


def test_crypto_bundle_input_contract_cannot_express_extra_top_level_fields() -> None:
    with pytest.raises(TypeError):
        NormalizedCryptoMarketBundle(
            **{
                **_bundle().__dict__,
                "debug_envelope": {"unexpected": True},
            }
        )
