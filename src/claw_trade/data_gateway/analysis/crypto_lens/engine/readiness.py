from __future__ import annotations

from claw_trade.data_gateway.models import DomainReadiness

from ..output_contract import AnalysisState


def state_from_domain_status(status: DomainReadiness) -> AnalysisState:
    if status == DomainReadiness.READY:
        return AnalysisState.READY
    if status in (DomainReadiness.PARTIAL, DomainReadiness.STALE):
        return AnalysisState.PARTIAL
    return AnalysisState.GAP
