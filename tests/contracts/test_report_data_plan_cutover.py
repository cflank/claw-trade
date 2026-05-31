from __future__ import annotations

import ast
import importlib.abc
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest
from claw_trade.artifacts.manifest import ManifestStore
from claw_trade.data_gateway.models import (
    Market,
    PackDomain,
    PrioritySource,
    ProviderCapability,
    ProviderKind,
    RunProviderPlan,
    SourceRole,
)
from claw_trade.data_gateway.providers.registry import ProviderRegistry
from claw_trade.data_gateway.providers.run_plan import RunProviderPlanner
from claw_trade.guards.common import BootResult
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

_PROVIDER_PREFIX = "claw_trade.data_gateway.providers"
_ALLOWED_PROVIDER_IMPORTS = {
    "claw_trade.data_gateway.providers.defaults",
    "claw_trade.data_gateway.providers.market_policy",
    "claw_trade.data_gateway.providers.registry",
    "claw_trade.data_gateway.providers.run_plan",
}
_FORBIDDEN_IMPORT_ROOTS = (
    "src/claw_trade/workflow",
    "src/claw_trade/ui_backend",
    "src/claw_trade/cli",
)
_LEGACY_PROVIDER_MODULES = (
    "frontline_data_pack",
    "frontline_data_pack.provider_executor",
    "crypto_market_data_pack",
    "claw_trade.providers",
)


class _Probe:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.reason = None


class _OpenClaw:
    def probe(self) -> _Probe:
        return _Probe(ok=True)

    def run_worker(self, command: object):  # pragma: no cover - should never run here
        raise AssertionError(f"unexpected run_worker: {command}")


class _OpenViking:
    def probe_read_stat_receipt(self) -> _Probe:
        return _Probe(ok=True)

    def probe_namespace_stat(self) -> _Probe:
        return _Probe(ok=True)

    def ensure_namespace(self, namespace: str) -> None:
        del namespace


class _ToolRegistryProbe:
    def probe(self) -> BootResult:
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
        raise KeyError(run_id)


def _request(*, data_gateway: str = "openbb") -> RunRequest:
    return RunRequest(
        ticker="600519.SH",
        company_name="贵州茅台",
        market="CN_A",
        profile="CN_A",
        currency="CNY",
        currency_symbol="¥",
        current_date="2026-05-29",
        start_date="2025-05-29",
        end_date="2026-05-29",
        data_gateway=data_gateway,
        stop_point=StopPoint.NONE,
        entry_point=WorkflowEntryPoint.REPORT_COMMAND,
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
        coverage_group="cn_a_market_kline",
        coverage_quorum=1,
        priority=0,
        priority_source=PrioritySource.SYSTEM_DEFAULT,
    )
    return ProviderRegistry(capabilities=(capability,))


def _extension_registry() -> ProviderRegistry:
    return ProviderRegistry(
        capabilities=(
            ProviderCapability(
                provider="cninfo_policy",
                adapter_id="policy.cninfo.official.cn_a",
                provider_kind=ProviderKind.PROJECT_EXTENSION,
                market=Market.CN_A,
                domain=PackDomain.POLICY,
                endpoint="announcements",
                source_role=SourceRole.OFFICIAL_ORIGINAL,
                expected_schema_id="cn_a.policy.official.v1",
                license_policy_id="personal_research",
                credential_requirements=(),
                rate_limit_policy_id="cninfo.policy",
                cache_ttl_seconds=900,
                required=True,
                attempt_required=True,
                coverage_group="cn_a_policy_official",
                coverage_quorum=1,
                priority=0,
                priority_source=PrioritySource.SYSTEM_DEFAULT,
            ),
            ProviderCapability(
                provider="eastmoney_hot_money",
                adapter_id="hot_money.eastmoney.dragon_tiger.cn_a",
                provider_kind=ProviderKind.PROJECT_EXTENSION,
                market=Market.CN_A,
                domain=PackDomain.HOT_MONEY,
                endpoint="dragon_tiger",
                source_role=SourceRole.MARKET_DATA,
                expected_schema_id="cn_a.hot_money.dragon_tiger.v1",
                license_policy_id="personal_research",
                credential_requirements=(),
                rate_limit_policy_id="eastmoney.dragon_tiger",
                cache_ttl_seconds=600,
                required=True,
                attempt_required=True,
                coverage_group="cn_a_hot_money_dragon_tiger",
                coverage_quorum=1,
                priority=10,
                priority_source=PrioritySource.SYSTEM_DEFAULT,
            ),
            ProviderCapability(
                provider="cninfo_lockup",
                adapter_id="lockup.cninfo.unlock.official.cn_a",
                provider_kind=ProviderKind.PROJECT_EXTENSION,
                market=Market.CN_A,
                domain=PackDomain.LOCKUP,
                endpoint="unlock_official",
                source_role=SourceRole.OFFICIAL_ORIGINAL,
                expected_schema_id="cn_a.lockup.unlock.official.v1",
                license_policy_id="personal_research",
                credential_requirements=(),
                rate_limit_policy_id="cninfo.unlock",
                cache_ttl_seconds=1800,
                required=True,
                attempt_required=True,
                coverage_group="cn_a_lockup_unlock",
                coverage_quorum=1,
                priority=0,
                priority_source=PrioritySource.SYSTEM_DEFAULT,
            ),
        )
    )


def test_report_command_initialization_writes_report_data_plan_snapshot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = WorkflowStore(tmp_path / "runs")
    manifest_store = ManifestStore(tmp_path / "runs")
    plan_store = _PlanStore(written=[])
    runner = ControlRunner(
        store=store,
        manifest_store=manifest_store,
        openclaw=_OpenClaw(),
        openviking=_OpenViking(),
        tool_registry_probe=_ToolRegistryProbe(),
        run_provider_planner=RunProviderPlanner(now_text=lambda: "2026-05-29T00:00:00+00:00"),
        run_provider_plan_store=plan_store,
        run_provider_registry=_registry(),
        provider_config_version_resolver=lambda: "cfg-v1",
    )

    monkeypatch.setattr(
        "claw_trade.workflow.runner.decide_next",
        lambda _: Decision(kind=DecisionKind.WAIT, reason="contract stop"),
    )
    state = runner.run(_request())

    assert state.status == RunStatus.CREATED
    assert len(plan_store.written) == 1
    snapshot_path = state.run_dir / "data_gateway" / "report-data-plan.json"
    assert snapshot_path.exists()
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "report_data_plan.v1"
    assert payload["report_data_plan"]["report_run_id"] == state.run_id
    assert payload["requirement_batch"]["request_kind"] == "report"
    requirements = payload["requirement_batch"]["merged_requirements"]
    assert {"market_analyst", "fundamental_analyst", "news_analyst", "social_analyst"} <= {
        item["consumer_id"] for item in requirements
    }
    assert {
        "cn_a_fundamental_financials",
        "cn_a_news_announcement",
        "cn_a_social_concept",
        "cn_a_policy_official",
        "cn_a_hot_money_dragon_tiger",
        "cn_a_lockup_unlock",
    } <= {item["data_type"] for item in requirements}
    requirement = next(
        item
        for item in requirements
        if item["consumer_id"] == "market_analyst" and item["data_type"] == "cn_a_market_kline"
    )
    [provider_call_spec] = payload["provider_call_specs"]
    assert requirement["data_type"] == "cn_a_market_kline"
    assert requirement["granularity"] == "daily"
    assert requirement["lookback_window_days"] == 366
    assert requirement["source_role_required"] == "market_data"
    assert requirement["field_set"] == ["trade_date", "open", "high", "low", "close", "volume"]
    assert requirement["consumer_type"] == "report_worker"
    assert requirement["consumer_id"] == "market_analyst"
    assert requirement["requirement_id"] == provider_call_spec["requirement_id"]
    assert provider_call_spec["data_type"] == "cn_a_market_kline"
    assert provider_call_spec["params"]["required_fields"] == ["trade_date", "open", "high", "low", "close", "volume"]
    assert payload["run_provider_plan_ref"] == f"mongo://openbb_run_provider_plans/{state.run_id}"
    assert payload["entry_point_cutover"] == {
        "entry_point": "report_command",
        "data_gateway": "openbb",
        "legacy_provider_path_blocked": True,
    }
    assert payload["domain_pack_generation"]["mode"] == "deferred_to_pack_runtime"
    assert payload["domain_pack_generation"]["planned_pack_ids"] == payload["report_data_plan"]["domain_pack_ids"]


def test_report_command_binds_cn_a_extension_provider_specs_to_explicit_requirements(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = WorkflowStore(tmp_path / "runs")
    manifest_store = ManifestStore(tmp_path / "runs")
    plan_store = _PlanStore(written=[])
    runner = ControlRunner(
        store=store,
        manifest_store=manifest_store,
        openclaw=_OpenClaw(),
        openviking=_OpenViking(),
        tool_registry_probe=_ToolRegistryProbe(),
        run_provider_planner=RunProviderPlanner(now_text=lambda: "2026-05-29T00:00:00+00:00"),
        run_provider_plan_store=plan_store,
        run_provider_registry=_extension_registry(),
        provider_config_version_resolver=lambda: "cfg-v1",
    )
    monkeypatch.setattr(
        "claw_trade.workflow.runner.decide_next",
        lambda _: Decision(kind=DecisionKind.WAIT, reason="contract stop"),
    )

    state = runner.run(_request())

    assert state.status == RunStatus.CREATED
    payload = json.loads((state.run_dir / "data_gateway" / "report-data-plan.json").read_text(encoding="utf-8"))
    requirements_by_type = {
        requirement["data_type"]: requirement
        for requirement in payload["requirement_batch"]["merged_requirements"]
    }
    expected = {
        "cn_a_policy_official",
        "cn_a_hot_money_dragon_tiger",
        "cn_a_lockup_unlock",
    }
    assert expected <= set(requirements_by_type)
    assert {spec["data_type"] for spec in payload["provider_call_specs"]} == expected
    for spec in payload["provider_call_specs"]:
        requirement = requirements_by_type[spec["data_type"]]
        assert spec["requirement_id"] == requirement["requirement_id"]
        assert spec["params"]["requirement_id"] == requirement["requirement_id"]
        assert spec["params"]["granularity"] == requirement["granularity"]
        assert spec["params"]["required_fields"] == requirement["field_set"]

    written_specs = plan_store.written[0].call_specs
    assert {spec.data_type for spec in written_specs} == expected
    assert all(spec.requirement_id and spec.params.get("required_fields") for spec in written_specs)


def test_report_command_rejects_non_openbb_data_gateway(tmp_path: Path) -> None:
    store = WorkflowStore(tmp_path / "runs")
    runner = ControlRunner(
        store=store,
        manifest_store=ManifestStore(tmp_path / "runs"),
        openclaw=_OpenClaw(),
        openviking=_OpenViking(),
        tool_registry_probe=_ToolRegistryProbe(),
        run_provider_planner=RunProviderPlanner(),
        run_provider_plan_store=_PlanStore(written=[]),
        run_provider_registry=_registry(),
        provider_config_version_resolver=lambda: "cfg-v1",
    )

    state = runner.run(_request(data_gateway="legacy_gateway"))
    assert state.status == RunStatus.FAILED
    assert state.failure_reason is not None
    assert "data_gateway=openbb" in state.failure_reason


def test_report_path_import_boundary_blocks_direct_provider_adapters() -> None:
    repo = Path(__file__).resolve().parents[2]
    python_files = [
        path
        for root in _FORBIDDEN_IMPORT_ROOTS
        for path in (repo / root).rglob("*.py")
        if path.is_file()
    ]
    for source_path in sorted(python_files):
        source = source_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(source_path))
        imported_modules = _provider_import_modules_from_ast(tree)
        disallowed = sorted(
            module
            for module in imported_modules
            if module.startswith(_PROVIDER_PREFIX) and module not in _ALLOWED_PROVIDER_IMPORTS
        )
        assert not disallowed, f"{source_path} imported disallowed provider modules: {disallowed}"


def test_report_entry_still_passes_when_legacy_modules_are_blocked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class _BlockLegacyModules(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):  # type: ignore[no-untyped-def]
            del path, target
            if any(fullname == item or fullname.startswith(item + ".") for item in _LEGACY_PROVIDER_MODULES):
                raise ImportError(f"blocked old provider import: {fullname}")
            return None

    blocker = _BlockLegacyModules()
    sys.meta_path.insert(0, blocker)
    monkeypatch.setattr(
        "claw_trade.data_gateway.packs.service.DomainPackService.get_pack",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("legacy pack path must not run at /report entry")),
    )
    try:
        store = WorkflowStore(tmp_path / "runs")
        manifest_store = ManifestStore(tmp_path / "runs")
        plan_store = _PlanStore(written=[])
        runner = ControlRunner(
            store=store,
            manifest_store=manifest_store,
            openclaw=_OpenClaw(),
            openviking=_OpenViking(),
            tool_registry_probe=_ToolRegistryProbe(),
            run_provider_planner=RunProviderPlanner(now_text=lambda: "2026-05-29T00:00:00+00:00"),
            run_provider_plan_store=plan_store,
            run_provider_registry=_registry(),
            provider_config_version_resolver=lambda: "cfg-v1",
        )

        monkeypatch.setattr(
            "claw_trade.workflow.runner.decide_next",
            lambda _: Decision(kind=DecisionKind.WAIT, reason="contract stop"),
        )
        state = runner.run(_request())
    finally:
        if blocker in sys.meta_path:
            sys.meta_path.remove(blocker)

    assert state.status == RunStatus.CREATED
    assert len(plan_store.written) == 1
    assert (state.run_dir / "data_gateway" / "report-data-plan.json").exists()


def _provider_import_modules_from_ast(tree: ast.AST) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.name.strip()
                if name.startswith(_PROVIDER_PREFIX):
                    modules.add(name)
        elif isinstance(node, ast.ImportFrom):
            module = (node.module or "").strip()
            if not module:
                continue
            if module.startswith(_PROVIDER_PREFIX + "."):
                modules.add(module)
                continue
            if module == _PROVIDER_PREFIX:
                for alias in node.names:
                    child = alias.name.strip()
                    if child == "*":
                        modules.add(module + ".*")
                    elif child:
                        modules.add(f"{module}.{child}")
    return modules
