from __future__ import annotations

import os
from uuid import uuid4

from pymongo import MongoClient
import pytest

from claw_trade.data_gateway.errors import DataGatewayError
from claw_trade.data_gateway.models import (
    AdmissionCheckStatus,
    CredentialStatus,
    DataGap,
    DataGapReason,
    FreshnessPolicy,
    GapSeverity,
    LicenseCheckResult,
    Market,
    PackDomain,
    PackRequest,
    PrioritySource,
    ProviderAdmissionStatus,
    ProviderCallSpec,
    ProviderKind,
    ProviderValidationReceipt,
    RateLimitPlanItem,
    RunProviderPlan,
    SourceRole,
)
from claw_trade.data_gateway.providers.run_plan import load_plan_for_pack_runtime
from claw_trade.data_gateway.store import (
    MongoRunProviderPlanStore,
    MongoValidationReceiptStore,
    ensure_openbb_store_indexes,
)
from claw_trade.data_gateway.store.mongo import OPENBB_RUN_PROVIDER_PLANS, OPENBB_PROVIDER_VALIDATION_RECEIPTS


def _require_mongo_uri() -> str:
    uri = (os.environ.get("DATA_GATEWAY_MONGODB_URI") or os.environ.get("CN_A_SOCIAL_MONGODB_URI") or "").strip()
    if not uri:
        pytest.skip("缺少真实 Mongo 环境变量 DATA_GATEWAY_MONGODB_URI/CN_A_SOCIAL_MONGODB_URI，跳过集成验收")
    return uri


def _spec() -> ProviderCallSpec:
    return ProviderCallSpec(
        call_key="market:tushare:daily",
        provider="tushare",
        adapter_id="project.tushare",
        provider_kind=ProviderKind.PROJECT_EXTENSION,
        provider_config_version="cfg-v1",
        endpoint="daily",
        source_role=SourceRole.MARKET_DATA,
        market=Market.CN_A,
        domain=PackDomain.MARKET,
        required=True,
        attempt_required=True,
        coverage_group="core_market",
        coverage_quorum=1,
        params={"ticker": "000001.SZ"},
        cache_ttl_seconds=300,
        license_policy_id="personal_research",
        expected_schema_id="market.daily.v1",
        priority=0,
        priority_source=PrioritySource.SYSTEM_DEFAULT,
        user_preferred=False,
    )


@pytest.mark.integration
def test_run_provider_plan_snapshot_and_validation_receipt_roundtrip() -> None:
    uri = _require_mongo_uri()
    db_name = f"claw_trade_it_{uuid4().hex[:8]}"
    run_id = f"run-{uuid4().hex[:8]}"
    client = MongoClient(uri, serverSelectionTimeoutMS=5000)
    db = client[db_name]
    ensure_openbb_store_indexes(db)

    plan_store = MongoRunProviderPlanStore(db[OPENBB_RUN_PROVIDER_PLANS])
    receipt_store = MongoValidationReceiptStore(db[OPENBB_PROVIDER_VALIDATION_RECEIPTS])

    plan = RunProviderPlan(
        run_id=run_id,
        provider_config_version="cfg-v1",
        market=Market.CN_A,
        ticker="000001.SZ",
        domains=(PackDomain.MARKET, PackDomain.NEWS),
        call_specs=(_spec(),),
        shared_call_keys=("market:tushare:daily",),
        cache_keys=("cache-1",),
        rate_limit_plan=(
            RateLimitPlanItem(
                provider="tushare",
                endpoint="daily",
                call_key="market:tushare:daily",
                window_seconds=60,
                estimated_cost=1,
                hard_reserved=False,
            ),
        ),
        initial_gaps=(
            DataGap(
                gap_id="gap-1",
                domain=PackDomain.NEWS,
                severity=GapSeverity.WARN,
                reason=DataGapReason.SOURCE_NOT_CONFIGURED,
                field_path="news",
                provider_candidates=("benzinga",),
                attempt_ids=(),
                root_cause="news provider not configured",
                next_action="configure provider",
            ),
        ),
        generated_at="2026-05-17T12:00:00+00:00",
        remote_prefetch_allowed=False,
    )
    plan_store.write(plan)
    loaded = plan_store.load(run_id)
    assert loaded.run_id == run_id
    assert loaded.provider_config_version == "cfg-v1"
    assert loaded.remote_prefetch_allowed is False
    assert loaded.call_specs[0].adapter_id == "project.tushare"
    assert loaded.initial_gaps[0].reason == DataGapReason.SOURCE_NOT_CONFIGURED

    receipt = ProviderValidationReceipt(
        provider_id="custom_rss_001",
        adapter_id="user.custom_rss_001",
        config_version="sha256:manifest-v1",
        status=ProviderAdmissionStatus.ENABLED_CANDIDATE,
        credential_status=AdmissionCheckStatus.PASS,
        healthcheck_status=AdmissionCheckStatus.PASS,
        schema_status=AdmissionCheckStatus.PASS,
        license_status=AdmissionCheckStatus.PASS,
        secret_status=AdmissionCheckStatus.PASS,
        credential_detail=CredentialStatus(
            status=AdmissionCheckStatus.PASS,
            provider="custom_rss_001",
            adapter_id="user.custom_rss_001",
        ),
        license_detail=LicenseCheckResult(
            status=AdmissionCheckStatus.PASS,
            license_policy_id="custom_rss_001.default",
            cost_tier="free",
            raw_export_policy="redacted",
            commercial_use_allowed=True,
            note="approved",
        ),
        sample_raw_ref="mongo://openbb_raw_payloads/raw-sample",
        sample_normalized_ref="mongo://openbb_normalized/norm-sample",
        transition_actor="user:tester",
        previous_status=ProviderAdmissionStatus.VALIDATED,
        transition_reason="manual enable",
        errors=(),
        validated_at="2026-05-17T12:05:00+00:00",
    )
    receipt_id = receipt_store.write(receipt)
    loaded_receipt = receipt_store.latest(provider_id="custom_rss_001", adapter_id="user.custom_rss_001")
    assert loaded_receipt is not None
    assert loaded_receipt.status == ProviderAdmissionStatus.ENABLED_CANDIDATE
    assert loaded_receipt.credential_status == AdmissionCheckStatus.PASS
    assert loaded_receipt.sample_raw_ref == "mongo://openbb_raw_payloads/raw-sample"
    assert loaded_receipt.sample_normalized_ref == "mongo://openbb_normalized/norm-sample"
    assert receipt_id.startswith("custom_rss_001:sha256:manifest-v1:")

    client.drop_database(db_name)
    client.close()


@pytest.mark.integration
def test_run_provider_plan_runtime_load_rejects_config_mismatch() -> None:
    uri = _require_mongo_uri()
    db_name = f"claw_trade_it_{uuid4().hex[:8]}"
    run_id = f"run-{uuid4().hex[:8]}"
    client = MongoClient(uri, serverSelectionTimeoutMS=5000)
    db = client[db_name]
    ensure_openbb_store_indexes(db)
    plan_store = MongoRunProviderPlanStore(db[OPENBB_RUN_PROVIDER_PLANS])

    plan = RunProviderPlan(
        run_id=run_id,
        provider_config_version="cfg-v1",
        market=Market.CN_A,
        ticker="000001.SZ",
        domains=(PackDomain.MARKET,),
        call_specs=(_spec(),),
        shared_call_keys=("market:tushare:daily",),
        cache_keys=("cache-1",),
        rate_limit_plan=(
            RateLimitPlanItem(
                provider="tushare",
                endpoint="daily",
                call_key="market:tushare:daily",
                window_seconds=60,
                estimated_cost=1,
            ),
        ),
        initial_gaps=(),
        generated_at="2026-05-17T12:00:00+00:00",
        remote_prefetch_allowed=False,
    )
    plan_store.write(plan)

    with pytest.raises(DataGatewayError) as excinfo:
        load_plan_for_pack_runtime(
            run_id=run_id,
            provider_config_version="cfg-v2",
            store=plan_store,
        )
    assert "config_version_mismatch" in str(excinfo.value)

    client.drop_database(db_name)
    client.close()
