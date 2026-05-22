from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

from pymongo import MongoClient
import pytest

from claw_trade.artifacts.manifest import ManifestStore
from claw_trade.data_gateway.models import (
    AdmissionCheckStatus,
    CredentialStatus,
    Market,
    PackDomain,
    PrioritySource,
    ProviderCapability,
    ProviderKind,
    SourceRole,
)
from claw_trade.data_gateway.providers.registry import ProviderRegistry
from claw_trade.data_gateway.providers.run_plan import RunProviderPlanner
from claw_trade.data_gateway.store import MongoRunProviderPlanStore, ensure_openbb_store_indexes
from claw_trade.data_gateway.store.mongo import OPENBB_PROVIDER_ATTEMPTS, OPENBB_RUN_PROVIDER_PLANS
from claw_trade.guards.common import BootResult
from claw_trade.workflow.models import Decision, DecisionKind, RunRequest, RunStatus, StopPoint, WorkflowEntryPoint
from claw_trade.workflow.runner import ControlRunner
from claw_trade.workflow.store import WorkflowStore


def _require_mongo_uri() -> str:
    uri = (os.environ.get("DATA_GATEWAY_MONGODB_URI") or os.environ.get("CN_A_SOCIAL_MONGODB_URI") or "").strip()
    if not uri:
        pytest.skip("缺少真实 Mongo 环境变量 DATA_GATEWAY_MONGODB_URI/CN_A_SOCIAL_MONGODB_URI，跳过集成验收")
    return uri


class _Probe:
    def __init__(self, *, ok: bool = True) -> None:
        self.ok = ok
        self.reason = None


class _OpenClaw:
    def probe(self) -> _Probe:
        return _Probe(ok=True)

    def run_worker(self, command: object):  # pragma: no cover - should not run in this suite
        raise AssertionError(f"unexpected run_worker call: {command}")


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


class _CredentialReadyAdapter:
    adapter_id = "project.tushare"
    provider_id = "tushare"

    def __init__(self) -> None:
        self.validate_calls = 0
        self.fetch_calls = 0

    def validate_credentials(self) -> CredentialStatus:
        self.validate_calls += 1
        return CredentialStatus(
            status=AdmissionCheckStatus.PASS,
            provider=self.provider_id,
            adapter_id=self.adapter_id,
        )

    def fetch(self, spec, request):  # pragma: no cover - contract guard
        self.fetch_calls += 1
        raise AssertionError(f"run plan initialization must not call fetch: {spec} {request}")


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


def _request() -> RunRequest:
    return RunRequest(
        ticker="000001.SZ",
        company_name="平安银行",
        market="CN_A",
        profile="CN_A",
        currency="CNY",
        currency_symbol="¥",
        current_date="2026-05-17",
        start_date="2026-04-17",
        end_date="2026-05-17",
        stop_point=StopPoint.NONE,
        entry_point=WorkflowEntryPoint.REPORT_COMMAND,
    )


@pytest.mark.integration
def test_report_run_initialization_no_prefetch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    uri = _require_mongo_uri()
    db_name = f"claw_trade_it_{uuid4().hex[:8]}"
    client = MongoClient(uri, serverSelectionTimeoutMS=5000)
    db = client[db_name]
    ensure_openbb_store_indexes(db)

    adapter = _CredentialReadyAdapter()
    runner = ControlRunner(
        store=WorkflowStore(tmp_path / "runs"),
        manifest_store=ManifestStore(tmp_path / "runs"),
        openclaw=_OpenClaw(),
        openviking=_OpenViking(),
        tool_registry_probe=_ToolRegistryProbe(),
        run_provider_planner=RunProviderPlanner(
            adapters_by_id={adapter.adapter_id: adapter},
            now_text=lambda: "2026-05-17T12:00:00+00:00",
        ),
        run_provider_plan_store=MongoRunProviderPlanStore(db[OPENBB_RUN_PROVIDER_PLANS]),
        run_provider_registry=_registry(),
        provider_config_version_resolver=lambda: "cfg-v1",
    )

    def _wait(input) -> Decision:  # type: ignore[no-untyped-def]
        del input
        return Decision(kind=DecisionKind.WAIT, reason="stop for no-prefetch contract test")

    monkeypatch.setattr("claw_trade.workflow.runner.decide_next", _wait)
    state = runner.run(_request())

    assert state.status == RunStatus.CREATED
    assert adapter.validate_calls == 1
    assert adapter.fetch_calls == 0

    plan_doc = db[OPENBB_RUN_PROVIDER_PLANS].find_one({"_id": state.run_id})
    assert plan_doc is not None
    assert plan_doc["provider_config_version"] == "cfg-v1"
    assert plan_doc["remote_prefetch_allowed"] is False
    assert db[OPENBB_PROVIDER_ATTEMPTS].count_documents({"run_id": state.run_id}) == 0

    client.drop_database(db_name)
    client.close()
