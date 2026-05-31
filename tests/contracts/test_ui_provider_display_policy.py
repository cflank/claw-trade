from __future__ import annotations

import pytest

from claw_trade.data_gateway.models import Market, ProviderDisplayStatus, SourceRole
from claw_trade.data_gateway.providers.market_policy import (
    ProviderMainChainEvidence,
    decide_provider_display,
    default_market_policy_registry,
    ui_display_decisions,
)


def _capability(provider: str, *, market: Market | None = None):
    registry = default_market_policy_registry()
    return next(
        item
        for item in registry.all_capabilities()
        if item.provider == provider and (market is None or item.market == market)
    )


def test_provider_name_or_key_is_not_enough_to_show_in_settings() -> None:
    coinglass = _capability("coinglass", market=Market.CRYPTO)

    decision = decide_provider_display(coinglass, ProviderMainChainEvidence())

    assert coinglass.user_config_key == "COINGLASS_API_KEY"
    assert decision.display_status == ProviderDisplayStatus.HIDE_UNTIL_INTEGRATED
    assert decision.changes_report_or_select_result is False
    assert decision.live_fresh_evidence_ref is None


def test_probe_only_evidence_is_not_main_chain_evidence() -> None:
    lunarcrush = _capability("lunarcrush", market=Market.CRYPTO)

    decision = decide_provider_display(
        lunarcrush,
        ProviderMainChainEvidence(
            called_by_report_or_select=True,
            writes_mongo_and_evidence=True,
            enters_domain_pack_or_feature=True,
            consumed_by_worker_or_strategy=True,
            live_fresh_evidence_ref="evidence://ui-probe/lunarcrush",
            probe_only=True,
        ),
    )

    assert decision.display_status == ProviderDisplayStatus.HIDE_UNTIL_INTEGRATED
    assert decision.probe_only is True


def test_complete_report_or_select_chain_evidence_allows_showing_user_config_provider() -> None:
    coinglass = _capability("coinglass", market=Market.CRYPTO)

    decision = decide_provider_display(
        coinglass,
        ProviderMainChainEvidence(
            called_by_report_or_select=True,
            writes_mongo_and_evidence=True,
            enters_domain_pack_or_feature=True,
            consumed_by_worker_or_strategy=True,
            live_fresh_evidence_ref="evidence://runs/report/coinglass-main-chain",
        ),
    )

    assert decision.display_status == ProviderDisplayStatus.SHOW
    assert decision.requires_user_credential is True


@pytest.mark.parametrize(
    "missing_field",
    (
        "called_by_report_or_select",
        "writes_mongo_and_evidence",
        "enters_domain_pack_or_feature",
        "consumed_by_worker_or_strategy",
        "live_fresh_evidence_ref",
    ),
)
def test_each_main_chain_evidence_check_is_required_to_show(missing_field: str) -> None:
    coinglass = _capability("coinglass", market=Market.CRYPTO)
    evidence = {
        "called_by_report_or_select": True,
        "writes_mongo_and_evidence": True,
        "enters_domain_pack_or_feature": True,
        "consumed_by_worker_or_strategy": True,
        "live_fresh_evidence_ref": "evidence://runs/report/coinglass-main-chain",
    }
    evidence[missing_field] = None if missing_field == "live_fresh_evidence_ref" else False

    decision = decide_provider_display(coinglass, ProviderMainChainEvidence(**evidence))

    assert decision.display_status == ProviderDisplayStatus.HIDE_UNTIL_INTEGRATED


def test_official_and_builtin_public_sources_remain_hidden_from_key_settings() -> None:
    cninfo = _capability("cninfo", market=Market.CN_A)
    sec = _capability("sec", market=Market.US)

    assert cninfo.source_role == SourceRole.OFFICIAL_ORIGINAL
    assert sec.source_role == SourceRole.OFFICIAL_ORIGINAL
    assert decide_provider_display(cninfo, ProviderMainChainEvidence()).display_status == ProviderDisplayStatus.HIDE_BUILTIN
    assert decide_provider_display(sec, ProviderMainChainEvidence()).display_status == ProviderDisplayStatus.HIDE_BUILTIN


def test_default_registry_does_not_show_sources_without_main_chain_evidence() -> None:
    decisions = ui_display_decisions(default_market_policy_registry(), evidence_by_provider={})

    assert decisions
    assert all(item.display_status != ProviderDisplayStatus.SHOW for item in decisions)
