from __future__ import annotations

from ..input_contract import CryptoLensDomainStatus
from ..output_contract import AnalysisState


def state_from_domain_status(status: CryptoLensDomainStatus) -> AnalysisState:
    if status == CryptoLensDomainStatus.READY:
        return AnalysisState.READY
    if status == CryptoLensDomainStatus.PARTIAL:
        return AnalysisState.PARTIAL
    if status == CryptoLensDomainStatus.NOT_APPLICABLE:
        return AnalysisState.NOT_APPLICABLE
    return AnalysisState.GAP
