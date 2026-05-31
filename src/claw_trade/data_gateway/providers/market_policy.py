from __future__ import annotations

from dataclasses import dataclass

from claw_trade.data_gateway.models import (
    DataGap,
    DataGapReason,
    GapSeverity,
    Market,
    PackDomain,
    ProviderCapability,
    ProviderDisplayDecision,
    ProviderDisplayStatus,
    SelectSupportStatus,
    SourceRole,
    source_role_can_be_primary_fact,
    source_role_is_discovery_only,
)
from claw_trade.data_gateway.providers.defaults import load_default_system_capabilities
from claw_trade.data_gateway.providers.registry import ProviderRegistry


@dataclass(frozen=True)
class SelectMarketPolicy:
    market: Market
    support_status: SelectSupportStatus
    gaps: tuple[DataGap, ...]
    history_package_scope: str
    csv_role: str | None = None


@dataclass(frozen=True)
class ProviderPolicyDecision:
    market: Market
    domain: PackDomain
    data_type: str
    provider_candidates: tuple[ProviderCapability, ...]
    official_provider_ids: tuple[str, ...]
    user_config_provider_ids: tuple[str, ...]
    discovery_provider_ids: tuple[str, ...]
    gaps: tuple[DataGap, ...]


@dataclass(frozen=True)
class ProviderMainChainEvidence:
    called_by_report_or_select: bool = False
    writes_mongo_and_evidence: bool = False
    enters_domain_pack_or_feature: bool = False
    consumed_by_worker_or_strategy: bool = False
    live_fresh_evidence_ref: str | None = None
    probe_only: bool = False

    @property
    def complete(self) -> bool:
        return (
            self.called_by_report_or_select
            and self.writes_mongo_and_evidence
            and self.enters_domain_pack_or_feature
            and self.consumed_by_worker_or_strategy
            and bool(self.live_fresh_evidence_ref)
            and not self.probe_only
        )


def default_market_policy_registry() -> ProviderRegistry:
    return ProviderRegistry(capabilities=load_default_system_capabilities())


def report_market_supported(market: Market) -> bool:
    return market in {Market.CN_A, Market.CRYPTO, Market.HK, Market.US}


def resolve_select_market_policy(
    market: Market,
    *,
    warehouse_cutover_complete: bool = False,
    crypto_history_ingest_approved: bool = False,
    crypto_history_ingest_complete: bool = False,
) -> SelectMarketPolicy:
    if market == Market.CN_A:
        if warehouse_cutover_complete:
            return SelectMarketPolicy(
                market=market,
                support_status=SelectSupportStatus.SUPPORTED,
                gaps=(),
                history_package_scope="baostock_qfq_seed_imported_to_mongo",
                csv_role="seed_import_audit_only",
            )
        return SelectMarketPolicy(
            market=market,
            support_status=SelectSupportStatus.TARGET_DESIGN,
            gaps=(
                _gap(
                    market=market,
                    domain=PackDomain.SELECT_FEATURE,
                    gap_id="cn_a_select_warehouse_cutover_required",
                    reason=DataGapReason.MONGO_MISSING,
                    field_path="select.warehouse",
                    root_cause="CN_A /select requires Baostock/AkShare seed import into Mongo and a warehouse marker first.",
                    next_action="Complete T9A warehouse cutover before marking /select supported.",
                    data_type="cn_a_select_features",
                ),
            ),
            history_package_scope="baostock_qfq_csv_seed_import_only",
            csv_role="seed_import_audit_only",
        )

    if market == Market.CRYPTO:
        if crypto_history_ingest_complete:
            return SelectMarketPolicy(
                market=market,
                support_status=SelectSupportStatus.TARGET_DESIGN,
                gaps=(),
                history_package_scope="crypto_history_ingest_complete_with_warehouse_coverage",
            )
        root_cause = "Crypto history universe, source, range, interval, and license scope are not approved."
        next_action = "Wait for T9B approval, then ingest normalized rows into Mongo."
        history_package_scope = "missing_blocked_until_t9b_approval"
        if crypto_history_ingest_approved:
            root_cause = "Crypto history ingest is approved, but Mongo normalized rows and warehouse coverage are not complete."
            next_action = "Complete history ingest and warehouse coverage before removing this blocker gap."
            history_package_scope = "approved_but_ingest_not_complete"
        return SelectMarketPolicy(
            market=market,
            support_status=SelectSupportStatus.TARGET_DESIGN,
            gaps=(
                _gap(
                    market=market,
                    domain=PackDomain.SELECT_FEATURE,
                    gap_id="crypto_select_history_missing",
                    reason=DataGapReason.MONGO_MISSING,
                    field_path="select.crypto_history",
                    root_cause=root_cause,
                    next_action=next_action,
                    data_type="crypto_select_history",
                ),
            ),
            history_package_scope=history_package_scope,
        )

    if market == Market.HK:
        return _unsupported_select_policy(
            market=market,
            gap_id="hk_select_unsupported",
            root_cause="HK currently serves /report only; /select and HK history packages are not in scope.",
            history_package_scope="not_in_current_scope",
        )
    if market == Market.US:
        return _unsupported_select_policy(
            market=market,
            gap_id="us_select_unsupported",
            root_cause="US currently serves /report only; /select and US history packages are not in scope.",
            history_package_scope="not_in_current_scope",
        )
    raise ValueError(f"unsupported market: {market}")


def resolve_provider_policy(
    registry: ProviderRegistry,
    *,
    market: Market,
    domain: PackDomain,
    data_type: str,
    require_formal_fact_source: bool = True,
    official_source_required: bool = False,
) -> ProviderPolicyDecision:
    candidates = registry.capabilities_for_data_type(market=market, domain=domain, data_type=data_type)
    official = tuple(item for item in candidates if item.source_role == SourceRole.OFFICIAL_ORIGINAL)
    discovery = tuple(item for item in candidates if source_role_is_discovery_only(item.source_role))
    user_config = tuple(item for item in candidates if item.user_config_key)
    usable = tuple(item for item in candidates if _capability_can_satisfy(item, require_formal_fact_source=require_formal_fact_source))

    gaps: list[DataGap] = []
    if official_source_required and not official:
        usable = ()
        gaps.append(
            _gap(
                market=market,
                domain=domain,
                gap_id=f"{market.value.lower()}_{data_type}_official_source_missing",
                reason=DataGapReason.PROVIDER_UNAVAILABLE,
                field_path=data_type,
                provider_candidates=tuple(item.provider for item in candidates),
                root_cause="This data type requires an official source, but no official provider is available.",
                next_action="Add or repair the official source; do not substitute a vendor or search source.",
                data_type=data_type,
                official_source_required=True,
            )
        )
    if require_formal_fact_source and candidates and not usable and discovery:
        gaps.append(
            _gap(
                market=market,
                domain=domain,
                gap_id=f"{market.value.lower()}_{data_type}_discovery_only",
                reason=DataGapReason.PROVIDER_UNAVAILABLE,
                field_path=data_type,
                provider_candidates=tuple(item.provider for item in candidates),
                root_cause="Current candidates are discovery or event-expectation sources only.",
                next_action="Add a formal fact source, or treat the material as unverified discovery.",
                data_type=data_type,
                search_discovery_only=True,
            )
        )
    if require_formal_fact_source and not usable and not gaps:
        gaps.append(
            _gap(
                market=market,
                domain=domain,
                gap_id=f"{market.value.lower()}_{data_type}_provider_unavailable",
                reason=DataGapReason.PROVIDER_UNAVAILABLE,
                field_path=data_type,
                provider_candidates=tuple(item.provider for item in candidates),
                root_cause="No formal fact source is available for this data type.",
                next_action="Add an auditable provider according to design, or return a data gap.",
                data_type=data_type,
            )
        )

    return ProviderPolicyDecision(
        market=market,
        domain=domain,
        data_type=data_type,
        provider_candidates=usable,
        official_provider_ids=tuple(item.provider for item in official),
        user_config_provider_ids=tuple(item.provider for item in user_config),
        discovery_provider_ids=tuple(item.provider for item in discovery),
        gaps=tuple(gaps),
    )


def decide_provider_display(
    capability: ProviderCapability,
    evidence: ProviderMainChainEvidence,
) -> ProviderDisplayDecision:
    if capability.source_role == SourceRole.OFFICIAL_ORIGINAL or not capability.user_config_key:
        return ProviderDisplayDecision(
            provider_id=capability.provider,
            market=capability.market,
            display_status=ProviderDisplayStatus.HIDE_BUILTIN,
            reason="provider is built in or does not require user credentials",
            requires_user_credential=bool(capability.user_config_key),
            changes_report_or_select_result=evidence.called_by_report_or_select,
            writes_mongo_and_evidence=evidence.writes_mongo_and_evidence,
            enters_domain_pack=evidence.enters_domain_pack_or_feature,
            consumed_by_worker_or_strategy=evidence.consumed_by_worker_or_strategy,
            live_fresh_evidence_ref=evidence.live_fresh_evidence_ref,
            probe_only=evidence.probe_only,
        )

    if evidence.complete:
        return ProviderDisplayDecision(
            provider_id=capability.provider,
            market=capability.market,
            display_status=ProviderDisplayStatus.SHOW,
            reason="provider has complete report/select main-chain evidence",
            requires_user_credential=True,
            changes_report_or_select_result=True,
            writes_mongo_and_evidence=True,
            enters_domain_pack=True,
            consumed_by_worker_or_strategy=True,
            live_fresh_evidence_ref=evidence.live_fresh_evidence_ref,
            probe_only=False,
        )

    return ProviderDisplayDecision(
        provider_id=capability.provider,
        market=capability.market,
        display_status=ProviderDisplayStatus.HIDE_UNTIL_INTEGRATED,
        reason="provider lacks complete report/select main-chain evidence",
        requires_user_credential=True,
        changes_report_or_select_result=evidence.called_by_report_or_select,
        writes_mongo_and_evidence=evidence.writes_mongo_and_evidence,
        enters_domain_pack=evidence.enters_domain_pack_or_feature,
        consumed_by_worker_or_strategy=evidence.consumed_by_worker_or_strategy,
        live_fresh_evidence_ref=evidence.live_fresh_evidence_ref,
        probe_only=evidence.probe_only,
    )


def ui_display_decisions(
    registry: ProviderRegistry,
    evidence_by_provider: dict[tuple[Market, str], ProviderMainChainEvidence],
) -> tuple[ProviderDisplayDecision, ...]:
    decisions: list[ProviderDisplayDecision] = []
    seen: set[tuple[Market, str]] = set()
    for capability in registry.all_capabilities():
        key = (capability.market, capability.provider)
        if key in seen:
            continue
        seen.add(key)
        decisions.append(decide_provider_display(capability, evidence_by_provider.get(key, ProviderMainChainEvidence())))
    return tuple(decisions)


def _capability_can_satisfy(capability: ProviderCapability, *, require_formal_fact_source: bool) -> bool:
    if not require_formal_fact_source:
        return True
    return bool(capability.can_be_formal_fact_source) and source_role_can_be_primary_fact(capability.source_role)


def _unsupported_select_policy(
    *,
    market: Market,
    gap_id: str,
    root_cause: str,
    history_package_scope: str,
) -> SelectMarketPolicy:
    return SelectMarketPolicy(
        market=market,
        support_status=SelectSupportStatus.UNSUPPORTED,
        gaps=(
            _gap(
                market=market,
                domain=PackDomain.SELECT_FEATURE,
                gap_id=gap_id,
                reason=DataGapReason.NOT_APPLICABLE,
                field_path="select.market",
                root_cause=root_cause,
                next_action="If this market /select is approved later, define universe, history scope, cleaning rules, and ingest tasks first.",
                data_type=f"{market.value.lower()}_select",
            ),
        ),
        history_package_scope=history_package_scope,
    )


def _gap(
    *,
    market: Market,
    domain: PackDomain,
    gap_id: str,
    reason: DataGapReason,
    field_path: str,
    root_cause: str,
    next_action: str,
    provider_candidates: tuple[str, ...] = (),
    data_type: str | None = None,
    official_source_required: bool = False,
    search_discovery_only: bool = False,
) -> DataGap:
    return DataGap(
        gap_id=gap_id,
        domain=domain,
        severity=GapSeverity.BLOCKER,
        reason=reason,
        field_path=field_path,
        provider_candidates=provider_candidates,
        attempt_ids=(),
        root_cause=root_cause,
        next_action=next_action,
        market=market,
        data_type=data_type,
        official_source_required=official_source_required,
        search_discovery_only=search_discovery_only,
        human_readable=root_cause,
    )
