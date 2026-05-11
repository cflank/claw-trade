from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


SCRIPTS_ROOT = Path("agents/fundamental_analyst/skills/cn-a-fundamental-data/scripts")


def test_boundary_guard_blocks_investment_and_control_plane_operations() -> None:
    err = None
    try:
        BOUNDARY_MODULE.assert_data_service_boundary("write_rating")
    except Exception as exc:  # noqa: BLE001
        err = exc
    assert err is not None
    assert getattr(err, "code", None) == BOUNDARY_MODULE.FND_BOUNDARY_INVESTMENT_CONCLUSION_FORBIDDEN

    err = None
    try:
        BOUNDARY_MODULE.assert_data_service_boundary("approve_artifact")
    except Exception as exc:  # noqa: BLE001
        err = exc
    assert err is not None
    assert getattr(err, "code", None) == BOUNDARY_MODULE.FND_BOUNDARY_CONTROL_GATE_FORBIDDEN

    err = None
    try:
        BOUNDARY_MODULE.assert_data_service_boundary("write_free_report_body_comment")
    except Exception as exc:  # noqa: BLE001
        err = exc
    assert err is not None
    assert getattr(err, "code", None) == BOUNDARY_MODULE.FND_BOUNDARY_INVESTMENT_CONCLUSION_FORBIDDEN

    err = None
    try:
        BOUNDARY_MODULE.assert_data_service_boundary("terminate_workflow_run")
    except Exception as exc:  # noqa: BLE001
        err = exc
    assert err is not None
    assert getattr(err, "code", None) == BOUNDARY_MODULE.FND_BOUNDARY_CONTROL_GATE_FORBIDDEN

    BOUNDARY_MODULE.assert_data_service_boundary("collect_fundamental_data")
    BOUNDARY_MODULE.assert_data_service_boundary("write_raw_payload")
    BOUNDARY_MODULE.assert_data_service_boundary("write_cache_record")

    err = None
    try:
        BOUNDARY_MODULE.assert_data_service_boundary("write_note")
    except Exception as exc:  # noqa: BLE001
        err = exc
    assert err is not None
    assert getattr(err, "code", None) == BOUNDARY_MODULE.FND_BOUNDARY_OPERATION_UNKNOWN


def test_load_fundamental_data_config_reads_env_and_validates_mongo_and_openviking() -> None:
    env = {
        "TUSHARE_TOKEN": "ts_token_abcdefghijklmnopqrstuvwxyz",
        "CN_A_MONGODB_URI": "mongodb://user:pass@localhost:27017/?authSource=admin",
        "CN_A_MONGODB_DATABASE": "claw_trade",
        "CN_A_MONGODB_CACHE_COLLECTION": "cn_a_fundamental_cache",
        "OPENVIKING_ENDPOINT": "https://openviking.example.internal",
        "OPENVIKING_API_KEY": "openviking_key_abcdefghijklmnopqrstuvwxyz",
        "OPENVIKING_WORKSPACE": "workspace-a",
        "CN_A_FUNDAMENTAL_TOOL_ENABLED": "true",
        "CN_A_FUNDAMENTAL_DISABLE_TUSHARE": "false",
        "CN_A_FUNDAMENTAL_DISABLE_AKSHARE": "true",
        "CN_A_FUNDAMENTAL_CACHE_READ_ONLY": "true",
    }
    config = CONFIG_MODULE.load_fundamental_data_config(env)
    assert config.tool_enabled is True
    assert config.disable_tushare is False
    assert config.disable_akshare is True
    assert config.cache_read_only is True

    local_env = dict(env)
    local_env["CN_A_MONGODB_URI"] = "mongodb://localhost:27017"
    local_config = CONFIG_MODULE.load_fundamental_data_config(local_env)
    assert local_config.mongodb_uri == "mongodb://localhost:27017"

    bad_env = dict(env)
    bad_env["CN_A_MONGODB_URI"] = "mongodb://user:pass@localhost:27017"
    try:
        CONFIG_MODULE.load_fundamental_data_config(bad_env)
    except Exception as exc:  # noqa: BLE001
        assert getattr(exc, "code", None) == CONFIG_MODULE.FND_CONFIG_MONGO_AUTHSOURCE_REQUIRED
    else:
        raise AssertionError("authSource 缺失时必须报错")


def test_load_fundamental_data_config_allows_tool_disabled_with_missing_required_services() -> None:
    env = {
        "CN_A_FUNDAMENTAL_TOOL_ENABLED": "false",
        "CN_A_FUNDAMENTAL_DISABLE_TUSHARE": "true",
        "CN_A_FUNDAMENTAL_DISABLE_AKSHARE": "true",
        "CN_A_FUNDAMENTAL_CACHE_READ_ONLY": "true",
    }
    config = CONFIG_MODULE.load_fundamental_data_config(env)
    assert config.tool_enabled is False
    assert config.disable_tushare is True
    assert config.disable_akshare is True
    assert config.cache_read_only is True
    assert config.mongodb_uri is None
    assert config.mongodb_database is None
    assert config.mongodb_cache_collection is None
    assert config.openviking_workspace is None


def test_sanitize_error_masks_sensitive_values_and_long_tokens() -> None:
    text = (
        "token=abcdefghijklmnopqrstuvwxyz012345 "
        "authorization: Bearer abcdefghijklmnopqrstuvwxyz "
        "url=https://x.example/path?api_key=secret123&ok=1"
    )
    sanitized = SECURITY_MODULE.sanitize_error(text)
    assert "abcdefghijklmnopqrstuvwxyz012345" not in sanitized
    assert "authorization: ***" in sanitized
    assert "api_key=%2A%2A%2A" in sanitized or "api_key=***" in sanitized


def test_liveness_and_readiness_report_dependency_states() -> None:
    env = {
        "TUSHARE_TOKEN": "ts_token_abcdefghijklmnopqrstuvwxyz",
        "CN_A_MONGODB_URI": "mongodb://user:pass@localhost:27017/?authSource=admin",
        "CN_A_MONGODB_DATABASE": "claw_trade",
        "CN_A_MONGODB_CACHE_COLLECTION": "cn_a_fundamental_cache",
        "OPENVIKING_ENDPOINT": "https://openviking.example.internal",
        "OPENVIKING_API_KEY": "openviking_key_abcdefghijklmnopqrstuvwxyz",
        "OPENVIKING_WORKSPACE": "workspace-a",
    }
    config = CONFIG_MODULE.load_fundamental_data_config(env)
    live = HEALTH_MODULE.liveness(config)
    assert live.ok is True

    ready = HEALTH_MODULE.readiness(
        config,
        mongo_ping=lambda timeout_ms: timeout_ms == 500,
        openviking_health=lambda timeout_ms: timeout_ms == 1000,
    )
    assert ready.ok is True
    assert ready.token.ok is True
    assert ready.mongodb.ok is True
    assert ready.openviking.ok is True


def test_build_cn_a_fundamental_pack_orchestrates_without_llm_or_report_path() -> None:
    request = MODELS_MODULE.DataPackRequest(
        ticker="600519",
        start_date="2026-01-01",
        end_date="2026-05-01",
        current_date="2026-05-07",
        run_id="run-1",
        dispatch_id="dispatch-1",
        worker_id="fundamental_analyst",
        market="CN_A",
    )
    normalized = MODELS_MODULE.NormalizedInput(
        raw_ticker="600519",
        canonical_code="600519.SH",
        tushare_code="600519.SH",
        akshare_symbol="600519",
        exchange="SH",
        market="CN_A",
        start_date="2026-01-01",
        end_date="2026-05-01",
        current_date="2026-05-07",
        run_id="run-1",
        dispatch_id="dispatch-1",
    )
    attempt = MODELS_MODULE.ProviderAttempt(
        provider="tushare",
        role="primary",
        api_name="fina_indicator",
        attempt_seq=1,
        status="success",
        reason=None,
        started_at="2026-05-07T00:00:00+00:00",
        ended_at="2026-05-07T00:00:00+00:00",
        duration_ms=1,
        retry_count=0,
        request_params_redacted={},
        response_row_count=1,
        response_col_count=1,
        field_coverage=["facts.financial_indicators.roe"],
        report_period="20251231",
        announce_date="2026-03-31",
        as_of="2026-05-07",
        fetched_at="2026-05-07T00:00:00+00:00",
        raw_payload_hash="sha256:abc",
        raw_payload_ref="viking://resources/workflow/run/frontline/fundamental_analyst/dispatch/provider_raw/tushare/x/1.json",
        error_type=None,
        error_message_redacted=None,
    )
    provider_result = MODELS_MODULE.ProviderResult(
        attempt=attempt,
        raw_payload_hash="sha256:abc",
        raw_payload_ref="viking://resources/workflow/run/frontline/fundamental_analyst/dispatch/provider_raw/tushare/x/1.json",
        extracted_fields=[("facts.financial_indicators.roe", 0.1, "field_sources.roe")],
        schema_changed=False,
    )

    class _CacheInspector:
        def inspect(self, normalized_input):
            return {"cache": "miss", "normalized": normalized_input.canonical_code}

    class _Planner:
        def build_plan(self, normalized_input, cache_result):
            assert cache_result["cache"] == "miss"
            return {
                "phase1_calls": [
                    MODELS_MODULE.ApiCallSpec(
                        provider="tushare",
                        api_name="fina_indicator",
                        role="primary",
                        required=True,
                        field_family="financial_indicators",
                        parameters={"ts_code": "600519.SH"},
                        timeout_ms=15000,
                        retry_limit=1,
                    )
                ]
            }

    class _Phase2Planner:
        def build_plan(self, tushare_results, cache_result, normalized_input):
            assert len(tushare_results) == 1
            assert cache_result["cache"] == "miss"
            return {
                "phase2_calls": [
                    MODELS_MODULE.ApiCallSpec(
                        provider="akshare",
                        api_name="stock_financial_abstract_ths",
                        role="supplement",
                        required=False,
                        field_family="financial_indicators",
                        parameters={"symbol": "600519"},
                        timeout_ms=18000,
                        retry_limit=1,
                    )
                ]
            }

    class _Executor:
        def execute(self, calls, normalized_input):
            assert normalized_input.canonical_code == "600519.SH"
            if calls and calls[0].provider == "tushare":
                return [provider_result]
            if calls and calls[0].provider == "akshare":
                return [
                    MODELS_MODULE.ProviderResult(
                        attempt=MODELS_MODULE.ProviderAttempt(
                            provider="akshare",
                            role="supplement",
                            api_name="stock_financial_abstract_ths",
                            attempt_seq=2,
                            status="success",
                            reason=None,
                            started_at="2026-05-07T00:00:01+00:00",
                            ended_at="2026-05-07T00:00:01+00:00",
                            duration_ms=1,
                            retry_count=0,
                            request_params_redacted={},
                            response_row_count=1,
                            response_col_count=1,
                            field_coverage=["facts.income_statement.revenue"],
                            report_period="20251231",
                            announce_date="2026-03-31",
                            as_of="2026-05-07",
                            fetched_at="2026-05-07T00:00:01+00:00",
                            raw_payload_hash="sha256:def",
                            raw_payload_ref="viking://resources/workflow/run/frontline/fundamental_analyst/dispatch/provider_raw/tushare/y/1.json",
                            error_type=None,
                            error_message_redacted=None,
                        ),
                        raw_payload_hash="sha256:def",
                        raw_payload_ref="viking://resources/workflow/run/frontline/fundamental_analyst/dispatch/provider_raw/tushare/y/1.json",
                        extracted_fields=[("facts.income_statement.revenue", 100.0, "field_sources.revenue")],
                        schema_changed=False,
                    )
                ]
            return []

    writes: list[tuple[str, str | None]] = []

    class _CacheWriter:
        def write(self, normalized_input, result):
            writes.append((result.attempt.provider, result.attempt.api_name))
            return {"status": "ok"}

    class _Mapper:
        def map(self, cache_result, provider_results):
            return type(
                "Mapped",
                (),
                {
                    "cache_result": cache_result,
                    "provider_results": provider_results,
                    "field_sources": {"financial_indicators.roe": {"as_of": "2026-05-07"}},
                },
            )()

    class _Freshness:
        def check(self, field_sources, current_date):
            assert "financial_indicators.roe" in field_sources
            assert current_date == "2026-05-07"
            return {"financial_indicators.roe": {"is_stale": False, "as_of": "2026-05-07"}}

    class _Diagnostics:
        def compute(self, mapped, freshness, provider_results):
            assert "financial_indicators.roe" in freshness
            return {"diagnostic_flags": [], "provider_count": len(provider_results), "mapped": mapped}

    class _Capabilities:
        def classify(self, mapped, freshness, diagnostics):
            return {"financial_snapshot": {"status": "available"}}

    class _Summary:
        def build(self, mapped, diagnostics):
            return []

    class _PackBuilder:
        def build(self, **kwargs):
            assert "freshness" in kwargs
            assert "evidence_capabilities" in kwargs
            assert "derived_summary_result" in kwargs
            return {
                "ok": True,
                "quality": {"status": "partial"},
                "diagnostics": kwargs["diagnostics"],
                "provider_results": kwargs["provider_results"],
            }

    deps = FUNDAMENTAL_PACK_MODULE.FundamentalPackDependencies(
        normalize_input=lambda incoming: normalized,
        cache_inspector=_CacheInspector(),
        provider_planner=_Planner(),
        phase2_planner=_Phase2Planner(),
        provider_executor=_Executor(),
        cache_writer=_CacheWriter(),
        field_source_mapper=_Mapper(),
        freshness_checker=_Freshness(),
        missing_field_diagnostics=_Diagnostics(),
        evidence_capability_classifier=_Capabilities(),
        derived_summary_builder=_Summary(),
        data_pack_builder=_PackBuilder(),
    )
    pack = FUNDAMENTAL_PACK_MODULE.BuildCnAFundamentalPack(request, deps)
    assert pack["ok"] is True
    assert pack["quality"]["status"] == "partial"
    assert pack["diagnostics"]["provider_count"] == 2
    assert writes == [("tushare", "fina_indicator"), ("akshare", "stock_financial_abstract_ths")]


def test_build_cn_a_fundamental_pack_hard_fails_when_tool_disabled() -> None:
    original_loader = FUNDAMENTAL_PACK_MODULE.load_fundamental_data_config
    FUNDAMENTAL_PACK_MODULE.load_fundamental_data_config = lambda env: CONFIG_MODULE.FundamentalDataConfig(
        tushare_token=None,
        mongodb_uri=None,
        mongodb_database=None,
        mongodb_cache_collection=None,
        openviking_endpoint=None,
        openviking_api_key=None,
        openviking_workspace=None,
        tool_enabled=False,
        disable_tushare=True,
        disable_akshare=True,
        cache_read_only=True,
    )
    try:
        request = MODELS_MODULE.DataPackRequest(
            ticker="600519",
            start_date=None,
            end_date=None,
            current_date="2026-05-07",
            run_id="run-disabled",
            dispatch_id="dispatch-disabled",
            worker_id="fundamental_analyst",
            market="CN_A",
        )
        try:
            FUNDAMENTAL_PACK_MODULE.BuildCnAFundamentalPack(request)
        except Exception as exc:  # noqa: BLE001
            assert getattr(exc, "code", None) == FUNDAMENTAL_PACK_MODULE.FND_TOOL_DISABLED
        else:
            raise AssertionError("tool disabled must fail")
    finally:
        FUNDAMENTAL_PACK_MODULE.load_fundamental_data_config = original_loader


def test_provider_executor_marks_disabled_provider_as_skipped_without_fake_success() -> None:
    class _NeverCalledTushare:
        def Fetch(self, normalized_input, spec):  # noqa: N802
            raise AssertionError("tushare fetcher should not be called when disabled")

    class _NeverCalledAkshare:
        def Fetch(self, normalized_input, spec):  # noqa: N802
            raise AssertionError("akshare fetcher should not be called when disabled")

    config = CONFIG_MODULE.FundamentalDataConfig(
        tushare_token=None,
        mongodb_uri=None,
        mongodb_database=None,
        mongodb_cache_collection=None,
        openviking_endpoint=None,
        openviking_api_key=None,
        openviking_workspace=None,
        tool_enabled=True,
        disable_tushare=True,
        disable_akshare=True,
        cache_read_only=True,
    )
    executor = FUNDAMENTAL_PACK_MODULE.DeterministicProviderExecutor(
        config=config,
        tushare_fetcher=_NeverCalledTushare(),
        akshare_fetcher=_NeverCalledAkshare(),
    )
    normalized = MODELS_MODULE.NormalizedInput(
        raw_ticker="600519",
        canonical_code="600519.SH",
        tushare_code="600519.SH",
        akshare_symbol="600519",
        exchange="SH",
        market="CN_A",
        start_date=None,
        end_date=None,
        current_date="2026-05-07",
        run_id="run-disabled-provider",
        dispatch_id="dispatch-disabled-provider",
    )
    results = executor.execute(
        [
            MODELS_MODULE.ApiCallSpec(
                provider="tushare",
                api_name="fina_indicator",
                role="primary",
                required=True,
                field_family="financial_indicators",
                parameters={"ts_code": "600519.SH"},
                timeout_ms=15000,
                retry_limit=1,
            ),
            MODELS_MODULE.ApiCallSpec(
                provider="akshare",
                api_name="stock_financial_abstract_ths",
                role="supplement",
                required=False,
                field_family="income_statement",
                parameters={"symbol": "600519"},
                timeout_ms=15000,
                retry_limit=1,
            ),
        ],
        normalized,
    )
    assert [item.attempt.status for item in results] == ["skipped", "skipped"]
    assert [item.attempt.reason for item in results] == ["missing_token_or_disabled", "provider_disabled"]


def test_build_default_dependencies_keeps_plans_and_relies_on_executor_for_disabled_attempts() -> None:
    class _FakeMongoClient:
        def __init__(self) -> None:
            self.closed = False

        def __getitem__(self, _name):
            return {}

        def close(self):
            self.closed = True

    fake_client = _FakeMongoClient()
    original_loader = FUNDAMENTAL_PACK_MODULE.load_fundamental_data_config
    original_create_mongo_client = FUNDAMENTAL_PACK_MODULE.create_mongo_client
    original_ensure_collection = FUNDAMENTAL_PACK_MODULE.ensure_fundamental_cache_collection
    original_ensure_indexes = FUNDAMENTAL_PACK_MODULE.ensure_fundamental_cache_indexes
    try:
        FUNDAMENTAL_PACK_MODULE.load_fundamental_data_config = lambda env: CONFIG_MODULE.FundamentalDataConfig(
            tushare_token=None,
            mongodb_uri="mongodb://user:pass@localhost:27017/?authSource=admin",
            mongodb_database="claw_trade",
            mongodb_cache_collection="cn_a_fundamental_cache",
            openviking_endpoint="https://openviking.example.internal",
            openviking_api_key="openviking-key",
            openviking_workspace="workspace-a",
            tool_enabled=True,
            disable_tushare=True,
            disable_akshare=True,
            cache_read_only=True,
        )
        FUNDAMENTAL_PACK_MODULE.create_mongo_client = lambda _config: fake_client
        FUNDAMENTAL_PACK_MODULE.ensure_fundamental_cache_collection = lambda _database: {}
        FUNDAMENTAL_PACK_MODULE.ensure_fundamental_cache_indexes = lambda _collection: None

        dependencies = FUNDAMENTAL_PACK_MODULE._build_default_dependencies()
        assert getattr(dependencies.provider_planner, "disable_tushare") is False
        assert getattr(dependencies.phase2_planner, "disable_akshare") is False
    finally:
        FUNDAMENTAL_PACK_MODULE.load_fundamental_data_config = original_loader
        FUNDAMENTAL_PACK_MODULE.create_mongo_client = original_create_mongo_client
        FUNDAMENTAL_PACK_MODULE.ensure_fundamental_cache_collection = original_ensure_collection
        FUNDAMENTAL_PACK_MODULE.ensure_fundamental_cache_indexes = original_ensure_indexes


def test_observability_labels_alerts_and_graceful_shutdown() -> None:
    labels = OBS_MODULE.sanitize_metric_labels(
        {
            "worker_id": "fundamental_analyst",
            "market": "CN_A",
            "provider": "tushare",
            "api_name": "fina_indicator",
            "status": "success",
            "quality_status": "ok",
            "ticker": "600519",
            "token": "should_not_be_label",
        }
    )
    assert "ticker" not in labels
    assert "token" not in labels
    assert labels["worker_id"] == "fundamental_analyst"

    events = OBS_MODULE.evaluate_alerts(
        OBS_MODULE.FundamentalAlertSnapshot(
            failed_pack_total_5m=2,
            pack_total_5m=10,
            missing_token_15m=1,
            raw_write_error_5m=1,
            unsupported_claim_10m=8,
            unsupported_claim_prev_10m=6,
        ),
        run_id="run-2",
        dispatch_id="dispatch-2",
    )
    reasons = {event.reason for event in events}
    assert "failed_ratio_5m" in reasons
    assert "missing_token_15m" in reasons
    assert "raw_write_error_5m" in reasons
    assert "unsupported_claim_10m_increase" in reasons

    unfinished_captured: list[tuple[str, ...]] = []
    closed = {"mongo": False, "flushed": False}
    hook = OBS_MODULE.FundamentalShutdownHook(max_wait_seconds=0)
    assert hook.begin_request("req-1") is True
    result = hook.graceful_shutdown(
        write_unfinished_failed_diagnostics=lambda request_ids: unfinished_captured.append(request_ids),
        close_mongo_pool=lambda: closed.__setitem__("mongo", True),
        flush_metrics_and_audit_logs=lambda: closed.__setitem__("flushed", True),
    )
    assert result.unfinished_request_ids == ("req-1",)
    assert unfinished_captured == [("req-1",)]
    assert closed["mongo"] is True
    assert closed["flushed"] is True


def _load_script_module(module_basename: str):
    scripts_path = str(SCRIPTS_ROOT.resolve())
    if scripts_path not in sys.path:
        sys.path.insert(0, scripts_path)
    module_path = SCRIPTS_ROOT / f"{module_basename}.py"
    spec = importlib.util.spec_from_file_location(f"cn_a_fundamental_{module_basename}", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载模块: {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"cn_a_fundamental_{module_basename}"] = module
    spec.loader.exec_module(module)
    return module


SECURITY_MODULE = _load_script_module("security")
BOUNDARY_MODULE = _load_script_module("boundary")
CONFIG_MODULE = _load_script_module("config")
MODELS_MODULE = _load_script_module("models")
HEALTH_MODULE = _load_script_module("health")
FUNDAMENTAL_PACK_MODULE = _load_script_module("fundamental_data_pack")
OBS_MODULE = _load_script_module("observability")
