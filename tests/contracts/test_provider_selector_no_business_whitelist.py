from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

from claw_trade.data_gateway.coordination.provider_selector import ProviderSelector
from claw_trade.data_gateway.providers.registry import ProviderRegistry


@dataclass(frozen=True)
class _BatchPolicy:
    supports_batch: bool = False
    batch_by: str = "none"
    max_symbols_per_call: int | None = None
    max_days_per_call: int | None = None
    mergeable_fields: tuple[str, ...] = ()
    pagination_policy: object = "none"
    split_policy: object = "strict"


@dataclass(frozen=True)
class _CredentialPolicy:
    credential_required: bool = False
    credential_names: tuple[str, ...] = ()
    credential_scope: str | None = None
    missing_behavior: str = "credential_missing"


@dataclass(frozen=True)
class _LicensePolicy:
    raw_storage_mode: str = "metadata_only"
    normalized_storage_allowed: bool = True
    redistribution_allowed: bool = False
    retention_days: int | None = 30


@dataclass(frozen=True)
class _EndpointCapability:
    endpoint_id: str
    market: str
    data_type: str
    source_role: str
    supported_granularities: tuple[str, ...]
    coverage_fields: tuple[str, ...]
    http_visibility: str = "managed_http"
    batch_policy: _BatchPolicy = _BatchPolicy()
    priority_rank: int | None = None
    rate_limit_policy: object | None = None
    license_policy: _LicensePolicy | None = None
    can_be_formal_fact_source: bool | None = None


@dataclass(frozen=True)
class _ProviderCapabilities:
    provider_id: str
    plugin_version: str
    endpoints: tuple[_EndpointCapability, ...]
    credentials: _CredentialPolicy = _CredentialPolicy()
    license_policy: _LicensePolicy = _LicensePolicy()
    default_rate_limit_policy: object = None
    default_priority_rank: int = 100


class _FakePlugin:
    def __init__(self, capabilities: _ProviderCapabilities) -> None:
        self._capabilities = capabilities
        self.plugin_id = capabilities.provider_id
        self.version = capabilities.plugin_version

    def capabilities(self) -> _ProviderCapabilities:
        return self._capabilities

    def build_fetch_tasks(self, batch: object) -> tuple[object, ...]:
        del batch
        return ()

    def fetch(self, task: object, ctx: object) -> object:
        del task
        del ctx
        raise NotImplementedError


class _FakePlan:
    def __init__(self, request: object) -> None:
        self._request = request

    def request_for_gap(self, gap: object) -> object:
        del gap
        return self._request


def test_same_data_need_has_same_provider_candidates_across_consumers() -> None:
    consumers = ("report", "select", "ui_probe", "maintenance")
    selected = [
        _provider_ids(_select_candidates(_request(consumer=consumer, fields=())))
        for consumer in consumers
    ]

    assert all(item == selected[0] for item in selected), dict(zip(consumers, selected, strict=True))


def test_fields_and_coverage_fields_do_not_deny_provider_candidates() -> None:
    candidates_without_fields = _provider_ids(_select_candidates(_request(fields=())))
    candidates_with_unknown_field = _provider_ids(_select_candidates(_request(fields=("not_declared_by_catalog",))))

    assert candidates_with_unknown_field == candidates_without_fields


def test_worker_domain_and_report_section_do_not_change_provider_candidates() -> None:
    base = _provider_ids(_select_candidates(_request(fields=())))
    tagged = _provider_ids(
        _select_candidates(
            _request(
                fields=(),
                worker_id="market_analyst",
                domain="market",
                report_section="capital-flow-section",
            )
        )
    )

    assert tagged == base


def test_source_role_required_does_not_deny_provider_candidates() -> None:
    candidates_without_source_role = _provider_ids(_select_candidates(_request(fields=())))
    candidates_with_source_role = _provider_ids(
        _select_candidates(_request(fields=(), source_role_required="official"))
    )

    assert candidates_with_source_role == candidates_without_source_role


def _select_candidates(request: SimpleNamespace) -> tuple[object, ...]:
    selector = ProviderSelector(_registry())
    gap = SimpleNamespace(request_id="need:capital_flow", symbol_id="600519.SH", required_level="required")
    return selector.select_candidates((gap,), _FakePlan(request))


def _provider_ids(candidates: tuple[object, ...]) -> tuple[str, ...]:
    return tuple(candidate.provider_id for candidate in candidates)


def _request(
    *,
    consumer: str = "report",
    fields: tuple[str, ...],
    worker_id: str | None = None,
    domain: str | None = None,
    report_section: str | None = None,
    source_role_required: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        market="CN_A",
        data_type="capital_flow",
        granularity="daily",
        fields=fields,
        source_role_required=source_role_required,
        symbol_id="600519.SH",
        universe_ref=None,
        consumer=consumer,
        worker_id=worker_id,
        domain=domain,
        report_section=report_section,
    )


def _registry() -> ProviderRegistry:
    registry = ProviderRegistry()
    registry.register(
        _FakePlugin(
            _ProviderCapabilities(
                provider_id="official_tushare",
                plugin_version="1.0.0",
                endpoints=(
                    _EndpointCapability(
                        endpoint_id="tushare.moneyflow",
                        market="CN_A",
                        data_type="capital_flow",
                        source_role="official",
                        supported_granularities=("daily",),
                        coverage_fields=("net_mf_amount",),
                        priority_rank=10,
                    ),
                ),
            )
        )
    )
    registry.register(
        _FakePlugin(
            _ProviderCapabilities(
                provider_id="public_eastmoney",
                plugin_version="1.0.0",
                endpoints=(
                    _EndpointCapability(
                        endpoint_id="eastmoney.fund_flow",
                        market="CN_A",
                        data_type="capital_flow",
                        source_role="built_in_public",
                        supported_granularities=("daily",),
                        coverage_fields=("main_net_inflow",),
                        priority_rank=20,
                    ),
                ),
            )
        )
    )
    return registry
