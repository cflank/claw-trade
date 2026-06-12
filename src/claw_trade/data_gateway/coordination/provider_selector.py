from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Protocol, Sequence

from claw_trade.data_gateway.models import ProviderCandidate
from claw_trade.data_gateway.providers.registry import ProviderRegistry

from ..providers.base import ProviderCapabilityView

_SOURCE_ROLE_ORDER = {
    "official": 0,
    "paid_data": 1,
    "built_in_public": 2,
    "sentiment": 3,
    "discovery": 4,
    "event_expectation": 5,
}
@dataclass(frozen=True)
class SkippedProviderCandidate:
    request_id: str
    provider_id: str
    endpoint_id: str
    market: str
    data_type: str
    granularity: str
    source_role: str
    reason: str
    remote_attempted: bool
    credential_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class _CredentialSkip:
    reason: str
    credential_names: tuple[str, ...]


def _read_attr(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _as_tuple(value: Any) -> tuple[Any, ...]:
    if value is None:
        return ()
    if isinstance(value, tuple):
        return value
    if isinstance(value, list):
        return tuple(value)
    return (value,)


def _as_datetime_or_date(value: Any) -> date | datetime | None:
    if isinstance(value, (date, datetime)):
        return value
    return None


def _as_string(value: Any) -> str:
    enum_value = getattr(value, "value", None)
    if isinstance(enum_value, str):
        return enum_value
    return str(value)


class ProviderSelector:
    def __init__(self, registry: ProviderRegistry, *, credential_resolver: CredentialResolverLike | None = None) -> None:
        self.registry = registry
        self.credential_resolver = credential_resolver
        self._skipped_candidates: tuple[SkippedProviderCandidate, ...] = ()

    @property
    def skipped_candidates(self) -> tuple[SkippedProviderCandidate, ...]:
        return self._skipped_candidates

    def select_candidates(self, gaps: Sequence[Any], plan: Any) -> tuple[ProviderCandidate, ...]:
        selected: list[ProviderCandidate] = []
        skipped: list[SkippedProviderCandidate] = []
        for gap in gaps:
            request = plan.request_for_gap(gap)
            capabilities = self.registry.list_capabilities(
                market=_as_string(_read_attr(request, "market")),
                data_type=_as_string(_read_attr(request, "data_type")),
            )
            matched = self.filter_by_gap(gap, capabilities, request, skipped=skipped)
            selected.extend(self.order_candidates(matched, request))
        self._skipped_candidates = tuple(skipped)
        return tuple(selected)

    def read_capabilities(self, candidates: Sequence[ProviderCandidate]) -> Any:
        provider_ids = tuple(dict.fromkeys(candidate.provider_id for candidate in candidates))
        return self.registry.read_capabilities(provider_ids)

    def filter_by_gap(
        self,
        gap: Any,
        capabilities: Sequence[ProviderCapabilityView],
        request: Any,
        *,
        skipped: list[SkippedProviderCandidate] | None = None,
    ) -> tuple[ProviderCandidate, ...]:
        required_granularity = _as_string(_read_attr(request, "granularity"))
        required_fields = set(_as_tuple(_read_attr(request, "fields", ())))

        matches: list[ProviderCandidate] = []
        for cap in capabilities:
            if self._is_symbol_request_using_universe_endpoint(request=request, cap=cap):
                continue
            if required_granularity not in set(cap.supported_granularities):
                continue
            source_role = _as_string(cap.source_role)
            credential_skip = self._credential_skip(cap)
            if credential_skip is not None:
                if skipped is not None:
                    skipped.append(
                        SkippedProviderCandidate(
                            request_id=str(_read_attr(gap, "request_id")),
                            provider_id=cap.provider_id,
                            endpoint_id=cap.endpoint_id,
                            market=cap.market,
                            data_type=cap.data_type,
                            granularity=required_granularity,
                            source_role=source_role,
                            reason=credential_skip.reason,
                            remote_attempted=False,
                            credential_names=credential_skip.credential_names,
                        )
                    )
                continue
            configured_paid_data = self._configured_paid_data(cap=cap, source_role=source_role)
            matches.append(
                ProviderCandidate(
                    request_id=str(_read_attr(gap, "request_id")),
                    provider_id=cap.provider_id,
                    endpoint_id=cap.endpoint_id,
                    market=cap.market,
                    data_type=cap.data_type,
                    granularity=required_granularity,
                    source_role=cap.source_role,
                    priority_rank=cap.priority_rank,
                    symbol_id=_read_attr(request, "symbol_id", _read_attr(gap, "symbol_id")),
                    universe_ref=_read_attr(request, "universe_ref", None),
                    exchange=_read_attr(request, "exchange", None),
                    currency=_read_attr(request, "currency", None),
                    timezone=_read_attr(request, "timezone", None),
                    calendar=_read_attr(request, "calendar", None),
                    base_asset=_read_attr(request, "base_asset", None),
                    quote_asset=_read_attr(request, "quote_asset", None),
                    date_range_start=_as_datetime_or_date(_read_attr(request, "date_range_start", None)),
                    date_range_end=_as_datetime_or_date(_read_attr(request, "date_range_end", None)),
                    fields=tuple(sorted(required_fields)),
                    required_level=_as_string(_read_attr(gap, "required_level", "required")),
                    configured_paid_data=configured_paid_data,
                    deadline_at=_as_datetime_or_date(_read_attr(request, "deadline_at", None)),
                )
            )
        return tuple(matches)

    def order_candidates(self, candidates: Sequence[ProviderCandidate], request: Any) -> tuple[ProviderCandidate, ...]:
        if not candidates:
            return ()

        filtered = list(candidates)

        ordered = sorted(
            filtered,
            key=lambda item: (
                self._source_role_rank(item),
                int(item.priority_rank),
                item.provider_id,
                item.endpoint_id,
            ),
        )
        return tuple(ordered)

    def _credential_skip(self, cap: ProviderCapabilityView) -> _CredentialSkip | None:
        if not bool(_read_attr(cap, "credential_required", False)):
            return None
        if self.credential_resolver is None:
            return None
        names = tuple(str(name).strip() for name in _as_tuple(_read_attr(cap, "credential_names", ())) if str(name).strip())
        if not names:
            return _CredentialSkip(reason="credential_missing", credential_names=())
        status_reader = getattr(self.credential_resolver, "get_credential_status", None)
        if callable(status_reader):
            statuses = tuple(status_reader(name) for name in names)
            if any(bool(_read_attr(status, "available", False)) for status in statuses):
                return None
            if any(
                bool(_read_attr(status, "configured", False)) and bool(_read_attr(status, "enabled", False))
                for status in statuses
            ):
                return _CredentialSkip(reason="credential_missing", credential_names=names)
            if any(bool(_read_attr(status, "configured", False)) for status in statuses):
                return _CredentialSkip(reason="source_disabled", credential_names=names)
            return _CredentialSkip(reason="source_not_configured", credential_names=names)
        if any(bool(self.credential_resolver.get_credential(name)) for name in names):
            return None
        return _CredentialSkip(reason="credential_missing", credential_names=names)

    def _configured_paid_data(self, *, cap: ProviderCapabilityView, source_role: str) -> bool:
        return (
            self.credential_resolver is not None
            and source_role == "paid_data"
            and bool(_read_attr(cap, "credential_required", False))
        )

    @staticmethod
    def _source_role_rank(candidate: ProviderCandidate) -> int:
        source_role = _as_string(candidate.source_role)
        if bool(getattr(candidate, "configured_paid_data", False)) and source_role == "paid_data":
            return -1
        return _SOURCE_ROLE_ORDER.get(source_role, 99)

    @staticmethod
    def _is_symbol_request_using_universe_endpoint(*, request: Any, cap: ProviderCapabilityView) -> bool:
        symbol_id = str(_read_attr(request, "symbol_id", "") or "").strip()
        universe_ref = str(_read_attr(request, "universe_ref", "") or "").strip()
        endpoint_id = str(_read_attr(cap, "endpoint_id", "") or "")
        if not symbol_id or universe_ref:
            return False
        return endpoint_id == "daily_bar_by_trade_date"


class CredentialResolverLike(Protocol):
    def get_credential(self, name: str) -> str | None: ...
