from __future__ import annotations

from claw_trade.data_gateway.models import (
    Market,
    PackDomain,
    PrioritySource,
    ProviderCapability,
    ProviderDisplayStatus,
    ProviderKind,
    SelectSupportStatus,
    SourceRole,
)
from claw_trade.data_gateway.providers.market_policy import (
    ProviderMainChainEvidence,
    default_market_policy_registry,
    decide_provider_display,
    report_market_supported,
    resolve_provider_policy,
    resolve_select_market_policy,
)
from claw_trade.data_gateway.providers.registry import ProviderRegistry


def _capability(
    *,
    provider: str,
    adapter_id: str,
    source_role: SourceRole,
    priority: int,
    priority_source: PrioritySource = PrioritySource.SYSTEM_DEFAULT,
    user_config_key: str | None = None,
) -> ProviderCapability:
    return ProviderCapability(
        provider=provider,
        adapter_id=adapter_id,
        provider_kind=ProviderKind.PROJECT_EXTENSION,
        market=Market.CN_A,
        domain=PackDomain.NEWS,
        endpoint="announcements",
        source_role=source_role,
        expected_schema_id="cn_a.news.official.v1",
        license_policy_id="personal_research",
        credential_requirements=(user_config_key,) if user_config_key else (),
        rate_limit_policy_id="default",
        cache_ttl_seconds=900,
        required=True,
        attempt_required=True,
        coverage_group="cn_a_news_announcement",
        coverage_quorum=1,
        priority=priority,
        priority_source=priority_source,
        data_type="cn_a_news_announcement",
    )


def test_report_is_supported_for_all_four_markets() -> None:
    assert {market for market in Market if report_market_supported(market)} == {
        Market.CN_A,
        Market.CRYPTO,
        Market.HK,
        Market.US,
    }


def test_select_scope_is_market_specific_and_does_not_restore_hk_us_history() -> None:
    cn_a_pending = resolve_select_market_policy(Market.CN_A)
    assert cn_a_pending.support_status == SelectSupportStatus.TARGET_DESIGN
    assert cn_a_pending.csv_role == "seed_import_audit_only"
    assert cn_a_pending.gaps[0].gap_id == "cn_a_select_warehouse_cutover_required"

    cn_a_ready = resolve_select_market_policy(Market.CN_A, warehouse_cutover_complete=True)
    assert cn_a_ready.support_status == SelectSupportStatus.SUPPORTED
    assert cn_a_ready.csv_role == "seed_import_audit_only"

    crypto = resolve_select_market_policy(Market.CRYPTO)
    assert crypto.support_status == SelectSupportStatus.TARGET_DESIGN
    assert crypto.history_package_scope == "missing_blocked_until_t9b_approval"
    assert crypto.gaps[0].gap_id == "crypto_select_history_missing"

    crypto_approved_only = resolve_select_market_policy(Market.CRYPTO, crypto_history_ingest_approved=True)
    assert crypto_approved_only.support_status == SelectSupportStatus.TARGET_DESIGN
    assert crypto_approved_only.history_package_scope == "approved_but_ingest_not_complete"
    assert crypto_approved_only.gaps[0].gap_id == "crypto_select_history_missing"
    assert "not complete" in crypto_approved_only.gaps[0].root_cause

    crypto_complete = resolve_select_market_policy(Market.CRYPTO, crypto_history_ingest_complete=True)
    assert crypto_complete.support_status == SelectSupportStatus.TARGET_DESIGN
    assert crypto_complete.history_package_scope == "crypto_history_ingest_complete_with_warehouse_coverage"
    assert crypto_complete.gaps == ()

    hk = resolve_select_market_policy(Market.HK)
    us = resolve_select_market_policy(Market.US)
    assert hk.support_status == SelectSupportStatus.UNSUPPORTED
    assert us.support_status == SelectSupportStatus.UNSUPPORTED
    assert hk.gaps[0].gap_id == "hk_select_unsupported"
    assert us.gaps[0].gap_id == "us_select_unsupported"
    assert hk.history_package_scope == "not_in_current_scope"
    assert us.history_package_scope == "not_in_current_scope"


def test_user_configured_vendor_cannot_replace_cninfo_official_disclosure() -> None:
    registry = ProviderRegistry(
        capabilities=(
            _capability(
                provider="cninfo",
                adapter_id="news.cninfo.cn_a",
                source_role=SourceRole.OFFICIAL_ORIGINAL,
                priority=50,
            ),
            _capability(
                provider="paid_vendor",
                adapter_id="news.paid.cn_a",
                source_role=SourceRole.PAID_DATA,
                priority=0,
                priority_source=PrioritySource.USER_PREFERRED,
                user_config_key="PAID_VENDOR_TOKEN",
            ),
        )
    )

    decision = resolve_provider_policy(
        registry,
        market=Market.CN_A,
        domain=PackDomain.NEWS,
        data_type="cn_a_news_announcement",
        official_source_required=True,
    )

    assert [item.provider for item in decision.provider_candidates] == ["cninfo", "paid_vendor"]
    assert decision.official_provider_ids == ("cninfo",)
    assert decision.user_config_provider_ids == ("paid_vendor",)
    assert decision.gaps == ()


def test_paid_vendor_alone_cannot_satisfy_official_disclosure_requirement() -> None:
    registry = ProviderRegistry(
        capabilities=(
            _capability(
                provider="paid_vendor",
                adapter_id="news.paid.cn_a",
                source_role=SourceRole.PAID_DATA,
                priority=0,
                priority_source=PrioritySource.USER_PREFERRED,
                user_config_key="PAID_VENDOR_TOKEN",
            ),
        )
    )

    decision = resolve_provider_policy(
        registry,
        market=Market.CN_A,
        domain=PackDomain.NEWS,
        data_type="cn_a_news_announcement",
        official_source_required=True,
    )

    assert decision.provider_candidates == ()
    assert decision.gaps[0].official_source_required is True


def test_search_and_polymarket_are_not_formal_fact_sources() -> None:
    registry = default_market_policy_registry()

    discovery = resolve_provider_policy(
        registry,
        market=Market.CRYPTO,
        domain=PackDomain.NEWS,
        data_type="crypto.news.discovery",
    )
    assert discovery.provider_candidates == ()
    assert discovery.discovery_provider_ids == ("google_news",)
    assert discovery.gaps[0].search_discovery_only is True

    event_expectation = resolve_provider_policy(
        registry,
        market=Market.CRYPTO,
        domain=PackDomain.NEWS,
        data_type="crypto.news.event_expectation",
    )
    assert event_expectation.provider_candidates == ()
    assert event_expectation.discovery_provider_ids == ("polymarket",)
    assert event_expectation.gaps[0].search_discovery_only is True


def test_hkexnews_and_sec_official_sources_are_locked_first() -> None:
    registry = default_market_policy_registry()

    hk = resolve_provider_policy(
        registry,
        market=Market.HK,
        domain=PackDomain.NEWS,
        data_type="hk_news_fact",
        official_source_required=True,
    )
    assert hk.provider_candidates[0].provider == "hkexnews"
    assert "hkexnews" in hk.official_provider_ids

    us = resolve_provider_policy(
        registry,
        market=Market.US,
        domain=PackDomain.NEWS,
        data_type="us_news_fact",
        official_source_required=True,
    )
    assert us.provider_candidates[0].provider == "sec"
    assert "sec" in us.official_provider_ids


def test_key_or_probe_does_not_make_provider_visible() -> None:
    registry = default_market_policy_registry()
    coinglass = next(item for item in registry.all_capabilities() if item.provider == "coinglass")

    key_only = decide_provider_display(coinglass, ProviderMainChainEvidence())
    assert key_only.display_status == ProviderDisplayStatus.HIDE_UNTIL_INTEGRATED

    probe_only = decide_provider_display(
        coinglass,
        ProviderMainChainEvidence(
            called_by_report_or_select=True,
            writes_mongo_and_evidence=True,
            enters_domain_pack_or_feature=True,
            consumed_by_worker_or_strategy=True,
            live_fresh_evidence_ref="evidence://probe/coinglass",
            probe_only=True,
        ),
    )
    assert probe_only.display_status == ProviderDisplayStatus.HIDE_UNTIL_INTEGRATED
