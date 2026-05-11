from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
import os
import re
import sys
import time
from uuid import uuid4

from pymongo import MongoClient
import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[2] / "agents/social_analyst/skills/cn-a-social-data/scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from cache import CacheKey, ensure_social_provider_cache_collection  # noqa: E402
from config import SOCIAL_SCHEMA_VERSION, SocialDataConfig  # noqa: E402
import evidence as evidence_module  # noqa: E402
from evidence import OpenVikingWriteReceipt, OpenVikingWriteRequest  # noqa: E402
from orchestrator import SocialToolRuntimeContext, build_social_sentiment_pack  # noqa: E402
from profile import ResolveDateWindow, SocialToolInput, resolve_social_target_profile  # noqa: E402
import providers as providers_module  # noqa: E402
from providers import ProviderQuery, build_provider_plan  # noqa: E402
from claw_trade.artifacts.openviking_backend_http import create_default_backend  # noqa: E402

_SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


class ReceiptHashMismatchWriter:
    """真实 OpenViking 写入后篡改 receipt hash，用于验证 mismatch hard-fail。"""

    def __init__(self) -> None:
        backend = create_default_backend()
        self._delegate = evidence_module._OpenVikingBackendWriter(backend=backend)

    def write_json(self, req: OpenVikingWriteRequest) -> OpenVikingWriteReceipt:
        receipt = self._delegate.write_json(req)
        if req.uri.endswith("/evidence/pack/social_sentiment_pack.json"):
            mismatch_hash = "sha256:" + ("0" * 64)
            if receipt.persisted_sha256 == mismatch_hash:
                mismatch_hash = "sha256:" + ("f" * 64)
            return replace(receipt, persisted_sha256=mismatch_hash)
        return receipt


def _require_real_env() -> dict[str, str]:
    missing: list[str] = []
    mongodb_uri = _read_env("CN_A_SOCIAL_MONGODB_URI")
    if mongodb_uri is None:
        missing.append("CN_A_SOCIAL_MONGODB_URI (真实 MongoDB 连接串)")
    openviking_endpoint = _read_env("OPENVIKING_ENDPOINT")
    if openviking_endpoint is None:
        missing.append("OPENVIKING_ENDPOINT (真实 OpenViking HTTP 地址)")
    if missing:
        pytest.skip("缺少真实依赖环境变量: " + ", ".join(missing))
    assert mongodb_uri is not None
    assert openviking_endpoint is not None
    return {
        "mongodb_uri": mongodb_uri,
        "openviking_endpoint": openviking_endpoint,
        "mongodb_database": _read_env("CN_A_SOCIAL_MONGODB_DATABASE") or "claw_trade",
        "mongodb_collection": _read_env("CN_A_SOCIAL_MONGODB_CACHE_COLLECTION") or "social_provider_cache",
    }


def _read_env(name: str) -> str | None:
    value = os.environ.get(name)
    if value is None:
        return None
    text = value.strip()
    return text or None


def _build_config(
    *,
    mongodb_uri: str,
    mongodb_database: str,
    mongodb_collection: str,
    provider_timeout_seconds: int = 10,
    pack_timeout_seconds: int = 20,
) -> SocialDataConfig:
    return SocialDataConfig(
        schema_version=SOCIAL_SCHEMA_VERSION,
        provider_timeout_seconds=provider_timeout_seconds,
        pack_timeout_seconds=pack_timeout_seconds,
        provider_max_concurrency=3,
        max_signals_per_bucket=50,
        mongodb_uri=mongodb_uri,
        mongodb_database=mongodb_database,
        mongodb_cache_collection=mongodb_collection,
        cache_required=True,
        ttl_by_endpoint={
            "stock_hot_rank_latest_em": 1800,
            "stock_hot_rank_em": 1800,
            "stock_hot_up_em": 1800,
            "stock_hot_keyword_em": 3600,
            "stock_hot_rank_relate_em": 3600,
        },
        p1_hot_up_enabled=False,
        p1_xueqiu_enabled=False,
        evidence_root="runs",
        openviking_l2_write_target_root=None,
    )


def _build_tool_input() -> SocialToolInput:
    return SocialToolInput(
        ticker="600519",
        market="CN_A",
        company_name="贵州茅台",
        industry="白酒",
        start_date="2026-05-01",
        end_date="2026-05-07",
    )


def _build_context(*, evidence_root: Path) -> SocialToolRuntimeContext:
    run_id = f"it-social-{uuid4().hex[:10]}"
    call_id = f"it-call-{uuid4().hex[:10]}"
    return SocialToolRuntimeContext(
        run_id=run_id,
        stage="frontline",
        worker_id="social_analyst",
        call_id=call_id,
        tool_name="social_social_sentiment_pack",
        evidence_root=str(evidence_root),
        current_time=datetime.now(UTC).isoformat(),
    )


def _build_provider_plan_and_cache_keys(
    *,
    tool_input: SocialToolInput,
    config: SocialDataConfig,
) -> tuple[list[ProviderQuery], dict[str, CacheKey]]:
    profile = resolve_social_target_profile(tool_input)
    date_window = ResolveDateWindow(
        start_date=tool_input.start_date,
        end_date=tool_input.end_date,
        current_time=datetime.now(UTC).isoformat(),
    )
    plan = build_provider_plan(profile, date_window, config)
    keys: dict[str, CacheKey] = {}
    for query in plan:
        keys[query.endpoint] = CacheKey(
            market="CN_A",
            ticker=profile.ticker,
            provider=query.provider,
            endpoint=query.endpoint,
            query_fingerprint=query.query_fingerprint,
            date_window=f"{date_window.start_date}:{date_window.end_date}",
            schema_version=config.schema_version,
        )
    return plan, keys


def _mongo_collection(config: SocialDataConfig) -> tuple[MongoClient, object]:
    assert config.mongodb_uri is not None
    client = MongoClient(config.mongodb_uri, serverSelectionTimeoutMS=5000)
    collection = ensure_social_provider_cache_collection(
        client,
        database_name=config.mongodb_database,
        collection_name=config.mongodb_cache_collection,
    )
    return client, collection


@pytest.mark.integration
def test_cache_miss_live_provider_l2_upsert_and_pack_evidence(tmp_path: Path) -> None:
    env = _require_real_env()
    config = _build_config(
        mongodb_uri=env["mongodb_uri"],
        mongodb_database=env["mongodb_database"],
        mongodb_collection=env["mongodb_collection"],
    )
    tool_input = _build_tool_input()
    _, cache_keys = _build_provider_plan_and_cache_keys(tool_input=tool_input, config=config)
    client, collection = _mongo_collection(config)
    try:
        collection.delete_many({"cache_id": {"$in": [key.cache_id() for key in cache_keys.values()]}})
        pack = build_social_sentiment_pack(
            tool_input=tool_input,
            context=_build_context(evidence_root=tmp_path),
            config=config,
        )
        success_attempts = [attempt for attempt in pack.provider_attempts if attempt.status == "success" and attempt.ok]
        assert success_attempts, "真实 provider 未返回任何 success attempt，无法验证 L2/cache 链路"
        assert any(attempt.cache_status == "miss" for attempt in pack.provider_attempts)
        assert pack.evidence.pack_path.startswith("viking://resources/workflow/")
        assert pack.evidence.provider_attempts_path.startswith("viking://resources/workflow/")
        assert pack.evidence.cache_inspection_path.startswith("viking://resources/workflow/")
        assert pack.evidence.raw_payload_refs, "应存在 raw L2 ref"
        for attempt in success_attempts:
            assert attempt.raw_payload_ref is not None
            assert attempt.raw_payload_ref.startswith("viking://resources/workflow/")
            assert attempt.payload_hash is not None
            assert _SHA256_PATTERN.match(attempt.payload_hash)
            key = cache_keys[attempt.endpoint]
            record = collection.find_one({"cache_id": key.cache_id()})
            assert record is not None, f"endpoint={attempt.endpoint} 未找到 cache upsert 记录"
            assert record["raw_payload_ref"] == attempt.raw_payload_ref
            assert record["payload_hash"] == attempt.payload_hash
    finally:
        client.close()


@pytest.mark.integration
def test_second_run_reads_cache_hit_with_raw_ref_and_hash(tmp_path: Path) -> None:
    env = _require_real_env()
    config = _build_config(
        mongodb_uri=env["mongodb_uri"],
        mongodb_database=env["mongodb_database"],
        mongodb_collection=env["mongodb_collection"],
    )
    tool_input = _build_tool_input()
    _, cache_keys = _build_provider_plan_and_cache_keys(tool_input=tool_input, config=config)
    client, collection = _mongo_collection(config)
    try:
        collection.delete_many({"cache_id": {"$in": [key.cache_id() for key in cache_keys.values()]}})
        first_pack = build_social_sentiment_pack(
            tool_input=tool_input,
            context=_build_context(evidence_root=tmp_path / "first"),
            config=config,
        )
        first_success = {a.endpoint: a for a in first_pack.provider_attempts if a.status == "success" and a.ok}
        assert first_success, "首次运行未形成可缓存 success attempt"
        second_pack = build_social_sentiment_pack(
            tool_input=tool_input,
            context=_build_context(evidence_root=tmp_path / "second"),
            config=config,
        )
        second_by_endpoint = {a.endpoint: a for a in second_pack.provider_attempts}
        hit_count = 0
        for endpoint, first_attempt in first_success.items():
            second_attempt = second_by_endpoint[endpoint]
            assert second_attempt.cache_status == "hit", f"endpoint={endpoint} 第二次未命中 cache"
            assert second_attempt.raw_payload_ref == first_attempt.raw_payload_ref
            assert second_attempt.payload_hash == first_attempt.payload_hash
            hit_count += 1
        assert hit_count > 0
    finally:
        client.close()


@pytest.mark.integration
def test_openviking_receipt_hash_mismatch_marks_pack_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = _require_real_env()
    config = _build_config(
        mongodb_uri=env["mongodb_uri"],
        mongodb_database=env["mongodb_database"],
        mongodb_collection=env["mongodb_collection"],
    )
    tool_input = _build_tool_input()
    monkeypatch.setenv(
        "CN_A_SOCIAL_OPENVIKING_EVIDENCE_WRITER",
        "tests.integration.test_cn_a_social_pack_integration:ReceiptHashMismatchWriter",
    )
    pack = build_social_sentiment_pack(
        tool_input=tool_input,
        context=_build_context(evidence_root=tmp_path),
        config=config,
    )
    assert pack.ok is False
    assert pack.quality.status == "failed"
    warning_codes = {warning.get("code") for warning in pack.quality.warnings if isinstance(warning, dict)}
    assert "SOCIAL_OPENVIKING_RECEIPT_HASH_MISMATCH" in warning_codes


@pytest.mark.integration
def test_single_provider_timeout_still_collects_other_endpoint_attempts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = _require_real_env()
    config = _build_config(
        mongodb_uri=env["mongodb_uri"],
        mongodb_database=env["mongodb_database"],
        mongodb_collection=env["mongodb_collection"],
        provider_timeout_seconds=1,
        pack_timeout_seconds=20,
    )
    tool_input = _build_tool_input()
    original_call = providers_module._call_akshare_endpoint
    timeout_endpoint = "stock_hot_rank_latest_em"

    def _call_with_one_timeout(endpoint: str, query: dict[str, str]) -> object:
        if endpoint == timeout_endpoint:
            time.sleep(2.5)
            return []
        return original_call(endpoint, query)

    monkeypatch.setattr(providers_module, "_call_akshare_endpoint", _call_with_one_timeout)
    pack = build_social_sentiment_pack(
        tool_input=tool_input,
        context=_build_context(evidence_root=tmp_path),
        config=config,
    )
    attempts_by_endpoint = {attempt.endpoint: attempt for attempt in pack.provider_attempts}
    assert timeout_endpoint in attempts_by_endpoint
    assert attempts_by_endpoint[timeout_endpoint].status == "timeout"
    other_endpoints = [endpoint for endpoint in attempts_by_endpoint if endpoint != timeout_endpoint]
    assert other_endpoints, "provider plan 必须包含 timeout endpoint 之外的 endpoint"
    assert all(attempts_by_endpoint[endpoint].status != "cancelled" for endpoint in other_endpoints)
