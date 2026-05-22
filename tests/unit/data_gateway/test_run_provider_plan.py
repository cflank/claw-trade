from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from claw_trade.artifacts.manifest import ManifestStore
from claw_trade.data_gateway.errors import DataGatewayError, DataGatewayErrorCode
from claw_trade.data_gateway.models import (
    AdmissionCheckStatus,
    CredentialStatus,
    Market,
    PackDomain,
    PrioritySource,
    ProviderCapability,
    ProviderKind,
    RunProviderPlan,
    SourceRole,
)
from claw_trade.data_gateway.providers.registry import ProviderRegistry
from claw_trade.data_gateway.providers.run_plan import (
    RunProviderPlanner,
    build_report_run_plan,
    load_plan_for_pack_runtime,
    report_run_plan_domains,
)
from claw_trade.data_gateway.providers.defaults import (
    build_default_provider_adapters,
    build_default_provider_registry,
    default_provider_config_version,
    load_default_system_capabilities,
)
from claw_trade.workflow.models import (
    Decision,
    DecisionKind,
    RunRequest,
    RunStatus,
    StopPoint,
    WorkflowEntryPoint,
)
from claw_trade.workflow.runner import ControlRunner
from claw_trade.workflow.store import WorkflowStore


@dataclass
class _Probe:
    ok: bool = True
    reason: str | None = None


class _OpenClaw:
    def probe(self) -> _Probe:
        return _Probe(ok=True)

    def run_worker(self, command: object):  # pragma: no cover - should not run in this suite
        raise AssertionError(f"unexpected run_worker call: {command}")


class _OpenViking:
    def __init__(self) -> None:
        self.ensure_namespace_calls: list[str] = []

    def probe_read_stat_receipt(self) -> _Probe:
        return _Probe(ok=True)

    def probe_namespace_stat(self) -> _Probe:
        return _Probe(ok=True)

    def ensure_namespace(self, namespace: str) -> None:
        self.ensure_namespace_calls.append(namespace)


class _ToolRegistryProbe:
    def probe(self):
        from claw_trade.guards.common import BootResult

        return BootResult.ok_result()


@dataclass
class _PlanStore:
    written: list[RunProviderPlan]

    def write(self, plan: RunProviderPlan) -> str:
        self.written.append(plan)
        return plan.run_id

    def load(self, run_id: str) -> RunProviderPlan:
        for plan in self.written:
            if plan.run_id == run_id:
                return plan
        raise DataGatewayError(DataGatewayErrorCode.RUN_PLAN_MISSING, f"missing run plan: {run_id}")


class _CredentialMissingAdapter:
    adapter_id = "project.tushare"
    provider_id = "tushare"

    def __init__(self) -> None:
        self.validate_calls = 0
        self.fetch_calls = 0

    def validate_credentials(self) -> CredentialStatus:
        self.validate_calls += 1
        return CredentialStatus(
            status=AdmissionCheckStatus.MISSING,
            provider=self.provider_id,
            adapter_id=self.adapter_id,
            missing_keys=("TUSHARE_TOKEN",),
        )

    def fetch(self, spec, request):  # pragma: no cover - contract guard
        self.fetch_calls += 1
        raise AssertionError(f"planner must not call fetch: {spec} {request}")


def _request(
    entry_point: WorkflowEntryPoint = WorkflowEntryPoint.REPORT_COMMAND,
    *,
    market: str = "CN_A",
    profile: str = "CN_A",
) -> RunRequest:
    return RunRequest(
        ticker="000001.SZ",
        company_name="平安银行",
        market=market,
        profile=profile,
        currency="CNY",
        currency_symbol="¥",
        current_date="2026-05-17",
        start_date="2026-04-17",
        end_date="2026-05-17",
        stop_point=StopPoint.NONE,
        entry_point=entry_point,
    )


def _registry() -> ProviderRegistry:
    capability = ProviderCapability(
        provider="tushare",
        adapter_id="project.tushare",
        provider_kind=ProviderKind.PROJECT_EXTENSION,
        market=Market.CN_A,
        domain=PackDomain.MARKET,
        endpoint="daily",
        source_role=SourceRole.MARKET_DATA,
        expected_schema_id="market.daily.v1",
        license_policy_id="personal_research",
        credential_requirements=("TUSHARE_TOKEN",),
        rate_limit_policy_id="tushare.basic",
        cache_ttl_seconds=300,
        required=True,
        attempt_required=True,
        coverage_group="core_market",
        coverage_quorum=1,
        priority=0,
        priority_source=PrioritySource.SYSTEM_DEFAULT,
    )
    return ProviderRegistry(capabilities=(capability,))


def test_planner_builds_plan_without_provider_fetch() -> None:
    registry = _registry()
    adapter = _CredentialMissingAdapter()
    planner = RunProviderPlanner(adapters_by_id={adapter.adapter_id: adapter}, now_text=lambda: "2026-05-17T00:00:00+00:00")

    plan = planner.build_run_plan(
        run_id="run-1",
        market=Market.CN_A,
        ticker="000001.SZ",
        company_name="平安银行",
        currency="CNY",
        profile="CN_A",
        current_date="2026-05-17",
        start_date="2026-04-17",
        end_date="2026-05-17",
        domains=(PackDomain.MARKET, PackDomain.NEWS),
        registry=registry,
        provider_config_version="cfg-v1",
    )

    assert plan.remote_prefetch_allowed is False
    assert len(plan.call_specs) == 1
    assert plan.call_specs[0].provider_config_version == "cfg-v1"
    assert plan.call_specs[0].adapter_id == "project.tushare"
    assert plan.call_specs[0].source_role == SourceRole.MARKET_DATA
    assert plan.call_specs[0].coverage_group == "core_market"
    assert plan.call_specs[0].coverage_quorum == 1
    assert plan.call_specs[0].license_policy_id == "personal_research"
    assert plan.call_specs[0].raw_export_policy == "metadata_only"
    assert plan.shared_call_keys == (plan.call_specs[0].call_key,)
    assert len(plan.cache_keys) == 1
    assert len(plan.rate_limit_plan) == 1
    assert any(gap.reason.value == "credential_missing" for gap in plan.initial_gaps)
    assert any(gap.reason.value == "source_not_configured" for gap in plan.initial_gaps)
    assert adapter.validate_calls == 1
    assert adapter.fetch_calls == 0


def test_build_report_run_plan_skips_generic_entry_point() -> None:
    plan = build_report_run_plan(
        request=_request(entry_point=WorkflowEntryPoint.GENERIC),
        run_id="run-1",
        provider_config_version="cfg-v1",
        planner=RunProviderPlanner(),
        registry=_registry(),
    )
    assert plan is None


def test_report_run_plan_domains_cn_a_has_seven_domains() -> None:
    domains = report_run_plan_domains(Market.CN_A)
    assert domains == (
        PackDomain.MARKET,
        PackDomain.FUNDAMENTAL,
        PackDomain.NEWS,
        PackDomain.SOCIAL,
        PackDomain.POLICY,
        PackDomain.HOT_MONEY,
        PackDomain.LOCKUP,
    )


def test_report_run_plan_domains_non_cn_a_keeps_approved_domains() -> None:
    assert report_run_plan_domains(Market.US) == (
        PackDomain.MARKET,
        PackDomain.FUNDAMENTAL,
        PackDomain.NEWS,
        PackDomain.SOCIAL,
    )


def test_build_report_run_plan_uses_cn_a_seven_domains() -> None:
    plan = build_report_run_plan(
        request=_request(),
        run_id="run-1",
        provider_config_version="cfg-v1",
        planner=RunProviderPlanner(),
        registry=_registry(),
    )
    assert plan is not None
    assert plan.domains == (
        PackDomain.MARKET,
        PackDomain.FUNDAMENTAL,
        PackDomain.NEWS,
        PackDomain.SOCIAL,
        PackDomain.POLICY,
        PackDomain.HOT_MONEY,
        PackDomain.LOCKUP,
    )


def test_cn_a_market_plan_includes_tushare_kline_fallback_and_missing_token_gap() -> None:
    env: dict[str, str] = {}
    registry = build_default_provider_registry()
    capabilities = load_default_system_capabilities()
    provider_config_version = default_provider_config_version(capabilities)
    adapters = build_default_provider_adapters(provider_config_version=provider_config_version, env=env)
    planner = RunProviderPlanner(adapters_by_id={item.adapter_id: item for item in adapters})

    plan = planner.build_run_plan(
        run_id="run-cn-a-plan",
        market=Market.CN_A,
        ticker="600519.SH",
        company_name="贵州茅台",
        currency="CNY",
        profile="CN_A",
        current_date="2026-05-17",
        start_date="2026-04-17",
        end_date="2026-05-17",
        domains=(PackDomain.MARKET,),
        registry=registry,
        provider_config_version=provider_config_version,
    )

    assert any(spec.provider == "tushare_kline_fallback" and spec.endpoint == "daily" for spec in plan.call_specs)
    assert any(
        gap.reason.value == "credential_missing"
        and "project.cn_a.market.tushare_fallback" in gap.root_cause
        and "TUSHARE_TOKEN" in gap.root_cause
        for gap in plan.initial_gaps
    )


def test_build_report_run_plan_non_cn_a_keeps_approved_domains() -> None:
    plan = build_report_run_plan(
        request=_request(market="US", profile="US"),
        run_id="run-1",
        provider_config_version="cfg-v1",
        planner=RunProviderPlanner(),
        registry=_registry(),
    )
    assert plan is not None
    assert plan.domains == (
        PackDomain.MARKET,
        PackDomain.FUNDAMENTAL,
        PackDomain.NEWS,
        PackDomain.SOCIAL,
    )


def test_load_plan_for_pack_runtime_checks_config_version() -> None:
    plan_store = _PlanStore(written=[])
    plan = build_report_run_plan(
        request=_request(),
        run_id="run-1",
        provider_config_version="cfg-v1",
        planner=RunProviderPlanner(),
        registry=_registry(),
    )
    assert plan is not None
    plan_store.write(plan)

    with pytest.raises(DataGatewayError) as excinfo:
        load_plan_for_pack_runtime(
            run_id="run-1",
            provider_config_version="cfg-v2",
            store=plan_store,
        )
    assert excinfo.value.code == DataGatewayErrorCode.CONFIG_VERSION_MISMATCH


def test_load_plan_for_pack_runtime_fails_when_missing() -> None:
    with pytest.raises(DataGatewayError) as excinfo:
        load_plan_for_pack_runtime(
            run_id="missing-run",
            provider_config_version="cfg-v1",
            store=_PlanStore(written=[]),
        )
    assert excinfo.value.code == DataGatewayErrorCode.RUN_PLAN_MISSING


def test_control_runner_writes_report_run_plan_before_first_wake(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    store = WorkflowStore(tmp_path / "runs")
    manifest_store = ManifestStore(tmp_path / "runs")
    plan_store = _PlanStore(written=[])
    planner = RunProviderPlanner(now_text=lambda: "2026-05-17T00:00:00+00:00")
    registry = _registry()
    runner = ControlRunner(
        store=store,
        manifest_store=manifest_store,
        openclaw=_OpenClaw(),
        openviking=_OpenViking(),
        tool_registry_probe=_ToolRegistryProbe(),
        run_provider_planner=planner,
        run_provider_plan_store=plan_store,
        run_provider_registry=registry,
        provider_config_version_resolver=lambda: "cfg-v1",
    )

    def _wait(input) -> Decision:  # type: ignore[no-untyped-def]
        del input
        return Decision(kind=DecisionKind.WAIT, reason="stop for contract test")

    monkeypatch.setattr("claw_trade.workflow.runner.decide_next", _wait)
    state = runner.run(_request())

    assert state.status == RunStatus.CREATED
    assert len(plan_store.written) == 1
    assert plan_store.written[0].run_id == state.run_id
    assert plan_store.written[0].provider_config_version == "cfg-v1"
    assert plan_store.written[0].domains == (
        PackDomain.MARKET,
        PackDomain.FUNDAMENTAL,
        PackDomain.NEWS,
        PackDomain.SOCIAL,
        PackDomain.POLICY,
        PackDomain.HOT_MONEY,
        PackDomain.LOCKUP,
    )


def test_control_runner_does_not_create_plan_for_generic_entry(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    store = WorkflowStore(tmp_path / "runs")
    manifest_store = ManifestStore(tmp_path / "runs")
    plan_store = _PlanStore(written=[])
    planner = RunProviderPlanner(now_text=lambda: "2026-05-17T00:00:00+00:00")
    runner = ControlRunner(
        store=store,
        manifest_store=manifest_store,
        openclaw=_OpenClaw(),
        openviking=_OpenViking(),
        tool_registry_probe=_ToolRegistryProbe(),
        run_provider_planner=planner,
        run_provider_plan_store=plan_store,
        run_provider_registry=_registry(),
        provider_config_version_resolver=lambda: "cfg-v1",
    )

    def _wait(input) -> Decision:  # type: ignore[no-untyped-def]
        del input
        return Decision(kind=DecisionKind.WAIT, reason="stop for contract test")

    monkeypatch.setattr("claw_trade.workflow.runner.decide_next", _wait)
    state = runner.run(_request(entry_point=WorkflowEntryPoint.GENERIC))

    assert state.status == RunStatus.CREATED
    assert plan_store.written == []


def test_control_runner_snapshots_provider_config_version_per_report_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = WorkflowStore(tmp_path / "runs")
    manifest_store = ManifestStore(tmp_path / "runs")
    plan_store = _PlanStore(written=[])
    planner = RunProviderPlanner(now_text=lambda: "2026-05-17T00:00:00+00:00")
    versions = iter(("cfg-v1", "cfg-v2"))
    runner = ControlRunner(
        store=store,
        manifest_store=manifest_store,
        openclaw=_OpenClaw(),
        openviking=_OpenViking(),
        tool_registry_probe=_ToolRegistryProbe(),
        run_provider_planner=planner,
        run_provider_plan_store=plan_store,
        run_provider_registry=_registry(),
        provider_config_version_resolver=lambda: next(versions),
    )

    def _wait(input) -> Decision:  # type: ignore[no-untyped-def]
        del input
        return Decision(kind=DecisionKind.WAIT, reason="stop for contract test")

    monkeypatch.setattr("claw_trade.workflow.runner.decide_next", _wait)
    runner.run(_request())
    runner.run(_request())

    assert [plan.provider_config_version for plan in plan_store.written] == ["cfg-v1", "cfg-v2"]


def test_control_runner_blocks_report_when_plan_dependencies_missing(tmp_path: Path) -> None:
    store = WorkflowStore(tmp_path / "runs")
    manifest_store = ManifestStore(tmp_path / "runs")
    runner = ControlRunner(
        store=store,
        manifest_store=manifest_store,
        openclaw=_OpenClaw(),
        openviking=_OpenViking(),
        tool_registry_probe=_ToolRegistryProbe(),
    )

    state = runner.run(_request())

    assert state.status == RunStatus.FAILED
    assert state.failure_reason is not None
    assert "run_provider_plan" in state.failure_reason
