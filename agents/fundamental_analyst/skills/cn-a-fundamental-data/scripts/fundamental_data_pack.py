from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import sys
from typing import Any, Mapping, Protocol, Sequence

from akshare_fetcher import AkShareFetcher
from boundary import assert_data_service_boundary
from cache import create_mongo_client, inspect_fundamental_cache, maybe_write_cache
from cache_schema import ensure_fundamental_cache_collection, ensure_fundamental_cache_indexes
from config import FundamentalDataConfig, load_fundamental_data_config
from data_pack_builder import DataPackBuilder
from derived_summary import DerivedSummaryBuilder
from diagnostics import FreshnessChecker, MissingFieldDiagnostics
from evidence_capabilities import EvidenceCapabilityClassifier
from field_source_mapper import FieldSourceMapper
from models import ApiCallSpec, DataPackRequest, NormalizedInput, ProviderAttempt, ProviderResult
from planner import BuildAkshareSupplementPlanFromTushareResult, DeterministicProviderPlanner
from policy import resolve_fundamental_visible_tools
from profile import NormalizeInput
from provider_specs import list_akshare_v1_api_names, list_tushare_v1_api_names
from tushare_fetcher import TushareFetcher


class NormalizeInputPort(Protocol):
    def __call__(self, request: DataPackRequest) -> NormalizedInput: ...


class MongoCacheInspectorPort(Protocol):
    def inspect(self, normalized: NormalizedInput) -> Any: ...


class ProviderPlannerPort(Protocol):
    def build_plan(self, normalized: NormalizedInput, cache_result: Any) -> Any: ...


class AkshareSupplementPlannerPort(Protocol):
    def build_plan(
        self,
        tushare_results: Sequence[ProviderResult],
        cache_result: Any,
        normalized: NormalizedInput,
    ) -> Any: ...


class ProviderExecutorPort(Protocol):
    def execute(self, calls: Sequence[ApiCallSpec], normalized: NormalizedInput) -> list[ProviderResult]: ...


class CacheWriterPort(Protocol):
    def write(self, normalized: NormalizedInput, provider_result: ProviderResult) -> Any: ...


class FieldSourceMapperPort(Protocol):
    def map(self, cache_result: Any, provider_results: list[ProviderResult]) -> Any: ...


class FreshnessCheckerPort(Protocol):
    def check(self, field_sources: Mapping[str, Any], current_date: str) -> Any: ...


class MissingFieldDiagnosticsPort(Protocol):
    def compute(self, mapped: Any, freshness: Any, provider_results: list[ProviderResult]) -> Any: ...


class EvidenceCapabilityClassifierPort(Protocol):
    def classify(self, mapped: Any, freshness: Any, diagnostics: Any) -> Any: ...


class DerivedSummaryBuilderPort(Protocol):
    def build(self, mapped: Any, diagnostics: Any) -> Any: ...


class DataPackBuilderPort(Protocol):
    def build(
        self,
        *,
        normalized_input: NormalizedInput,
        mapped: Any,
        freshness: Any,
        diagnostics: Any,
        evidence_capabilities: Any,
        derived_summary_result: Any,
        provider_results: list[ProviderResult],
        cache_result: Any,
    ) -> Any: ...


@dataclass(frozen=True)
class FundamentalPackDependencies:
    normalize_input: NormalizeInputPort
    cache_inspector: MongoCacheInspectorPort
    provider_planner: ProviderPlannerPort
    phase2_planner: AkshareSupplementPlannerPort
    provider_executor: ProviderExecutorPort
    cache_writer: CacheWriterPort
    field_source_mapper: FieldSourceMapperPort
    freshness_checker: FreshnessCheckerPort
    missing_field_diagnostics: MissingFieldDiagnosticsPort
    evidence_capability_classifier: EvidenceCapabilityClassifierPort
    derived_summary_builder: DerivedSummaryBuilderPort
    data_pack_builder: DataPackBuilderPort
    close: Any | None = None


class FundamentalOrchestratorError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


FND_TOOL_DISABLED = "FND_TOOL_DISABLED"
FND_TOOL_ENTRYPOINT_INVALID_INPUT = "FND_TOOL_ENTRYPOINT_INVALID_INPUT"
FND_PROVIDER_CALL_PROVIDER_UNSUPPORTED = "FND_PROVIDER_CALL_PROVIDER_UNSUPPORTED"
SKIP_REASON_PROVIDER_DISABLED = "provider_disabled"
SKIP_REASON_MISSING_TOKEN_OR_DISABLED = "missing_token_or_disabled"


@dataclass(frozen=True)
class DeterministicAkshareSupplementPlanner:
    disable_akshare: bool = False

    def build_plan(
        self,
        tushare_results: Sequence[ProviderResult],
        cache_result: Any,
        normalized: NormalizedInput,
    ) -> Any:
        return BuildAkshareSupplementPlanFromTushareResult(
            tushare_results,
            cache_result,
            normalized,
            disable_akshare=self.disable_akshare,
        )


@dataclass(frozen=True)
class DeterministicProviderExecutor:
    config: FundamentalDataConfig
    tushare_fetcher: TushareFetcher
    akshare_fetcher: AkShareFetcher

    def execute(self, calls: Sequence[ApiCallSpec], normalized: NormalizedInput) -> list[ProviderResult]:
        results: list[ProviderResult] = []
        for call in calls:
            if call.provider == "tushare":
                if self.config.disable_tushare:
                    results.append(
                        _build_skipped_provider_result(
                            call,
                            normalized,
                            reason=SKIP_REASON_MISSING_TOKEN_OR_DISABLED,
                        )
                    )
                    continue
                results.append(self.tushare_fetcher.Fetch(normalized, call))
                continue
            if call.provider == "akshare":
                if self.config.disable_akshare:
                    results.append(_build_skipped_provider_result(call, normalized, reason=SKIP_REASON_PROVIDER_DISABLED))
                    continue
                results.append(self.akshare_fetcher.Fetch(normalized, call))
                continue
            raise FundamentalOrchestratorError(
                FND_PROVIDER_CALL_PROVIDER_UNSUPPORTED,
                f"不支持的 provider: {call.provider}",
            )
        return results


@dataclass(frozen=True)
class CollectionCacheInspector:
    collection: Any
    approved_api_names: tuple[str, ...]

    def inspect(self, normalized: NormalizedInput) -> Any:
        return inspect_fundamental_cache(
            self.collection,
            normalized,
            approved_api_names=self.approved_api_names,
        )


@dataclass(frozen=True)
class CollectionCacheWriter:
    collection: Any
    config: FundamentalDataConfig
    metric_definition_version: str = "cn_a_fundamental_pack.metric.v1"

    def write(self, normalized: NormalizedInput, provider_result: ProviderResult) -> Any:
        return maybe_write_cache(
            self.collection,
            normalized,
            provider_result,
            metric_definition_version=self.metric_definition_version,
            cache_read_only=self.config.cache_read_only,
        )


@dataclass(frozen=True)
class _FreshnessCheckerAdapter:
    checker: FreshnessChecker

    def check(self, field_sources: Mapping[str, Any], current_date: str) -> Any:
        return self.checker.check(field_sources, current_date)


@dataclass(frozen=True)
class _MissingFieldDiagnosticsAdapter:
    diagnostics: MissingFieldDiagnostics

    def compute(self, mapped: Any, freshness: Any, provider_results: list[ProviderResult]) -> Any:
        return self.diagnostics.compute(mapped, freshness, provider_results)


@dataclass(frozen=True)
class _EvidenceCapabilityClassifierAdapter:
    classifier: EvidenceCapabilityClassifier

    def classify(self, mapped: Any, freshness: Any, diagnostics: Any) -> Any:
        return self.classifier.classify(mapped, freshness, diagnostics)


@dataclass(frozen=True)
class _DerivedSummaryBuilderAdapter:
    builder: DerivedSummaryBuilder

    def build(self, mapped: Any, diagnostics: Any) -> Any:
        return self.builder.build(mapped, diagnostics)


@dataclass(frozen=True)
class _DataPackBuilderAdapter:
    builder: DataPackBuilder

    def build(
        self,
        *,
        normalized_input: NormalizedInput,
        mapped: Any,
        freshness: Any,
        diagnostics: Any,
        evidence_capabilities: Any,
        derived_summary_result: Any,
        provider_results: list[ProviderResult],
        cache_result: Any,
    ) -> Any:
        return self.builder.build(
            normalized_input=normalized_input,
            mapped=mapped,
            freshness=freshness,
            diagnostics=diagnostics,
            evidence_capabilities=evidence_capabilities,
            derived_summary_result=derived_summary_result,
            provider_results=provider_results,
            cache_result=cache_result,
        )


def BuildCnAFundamentalPack(
    request: DataPackRequest,
    dependencies: FundamentalPackDependencies | None = None,
) -> Any:
    assert_data_service_boundary("collect_fundamental_data")
    owns_default_dependencies = dependencies is None
    resolved_dependencies = dependencies if dependencies is not None else _build_default_dependencies()

    try:
        normalized = resolved_dependencies.normalize_input(request)
        cache_result = resolved_dependencies.cache_inspector.inspect(normalized)
        phase1_plan = resolved_dependencies.provider_planner.build_plan(normalized, cache_result)
        phase1_calls = _extract_phase1_calls(phase1_plan)
        phase1_results = resolved_dependencies.provider_executor.execute(phase1_calls, normalized)

        phase2_plan = resolved_dependencies.phase2_planner.build_plan(phase1_results, cache_result, normalized)
        phase2_calls = _extract_phase2_calls(phase2_plan)
        phase2_results = resolved_dependencies.provider_executor.execute(phase2_calls, normalized)

        provider_results = phase1_results + phase2_results
        for provider_result in provider_results:
            resolved_dependencies.cache_writer.write(normalized, provider_result)

        mapped = resolved_dependencies.field_source_mapper.map(cache_result, provider_results)
        freshness = resolved_dependencies.freshness_checker.check(mapped.field_sources, normalized.current_date)
        diagnostics = resolved_dependencies.missing_field_diagnostics.compute(mapped, freshness, provider_results)
        evidence_capabilities = resolved_dependencies.evidence_capability_classifier.classify(
            mapped,
            freshness,
            diagnostics,
        )
        derived_summary_result = resolved_dependencies.derived_summary_builder.build(mapped, diagnostics)
        return resolved_dependencies.data_pack_builder.build(
            normalized_input=normalized,
            mapped=mapped,
            freshness=freshness,
            diagnostics=diagnostics,
            evidence_capabilities=evidence_capabilities,
            derived_summary_result=derived_summary_result,
            provider_results=provider_results,
            cache_result=cache_result,
        )
    finally:
        if owns_default_dependencies and resolved_dependencies.close is not None:
            resolved_dependencies.close()


def tool_entrypoint(payload: Any, runtime_context: Any) -> dict[str, Any]:
    validation_error_type: type[Exception] = Exception
    try:
        run_fundamentals_data_pack, to_jsonable, validation_error_type = _load_frontline_pack_runtime()
        pack = run_fundamentals_data_pack(payload, _normalize_frontline_runtime_context(runtime_context))
        jsonable = to_jsonable(pack)
        if isinstance(jsonable, Mapping):
            return {str(key): jsonable[key] for key in jsonable.keys()}
        raise FundamentalOrchestratorError(
            FND_TOOL_ENTRYPOINT_INVALID_INPUT,
            "run_fundamentals_data_pack 返回结果不可序列化为 dict",
        )
    except Exception as exc:  # noqa: BLE001
        error_code = getattr(exc, "code", FND_TOOL_ENTRYPOINT_INVALID_INPUT)
        if isinstance(exc, validation_error_type):
            error_message = getattr(exc, "message", str(exc))
        else:
            error_message = str(exc)
        return {
            "ok": False,
            "error": {
                "code": str(error_code),
                "message": error_message,
            },
            "quality": {
                "status": "failed",
                "warnings": [
                    {
                        "code": str(error_code),
                        "message": error_message,
                    }
                ],
            },
            "reader_brief": f"基本面资料包执行失败：{error_code}。{error_message}",
        }


def _load_frontline_pack_runtime():
    shared_python_root = Path(__file__).resolve().parents[5] / "openclaw_plugins" / "claw-trade-frontline-tools" / "python"
    if str(shared_python_root) not in sys.path:
        sys.path.insert(0, str(shared_python_root))

    from frontline_data_pack.errors import FrontlineValidationError
    from frontline_data_pack.fundamentals_data_pack import run_fundamentals_data_pack
    from frontline_data_pack.models import to_jsonable

    return run_fundamentals_data_pack, to_jsonable, FrontlineValidationError


def _normalize_frontline_runtime_context(runtime_context: Any) -> dict[str, Any]:
    run_id = _read_required_text(runtime_context, "run_id", payload_name="runtime_context")
    worker_id = _read_required_text(runtime_context, "worker_id", payload_name="runtime_context")
    call_id = _read_optional_text(runtime_context, "call_id")
    dispatch_id = _read_optional_text(runtime_context, "dispatch_id")
    resolved_dispatch_id = dispatch_id or call_id or f"{run_id}-dispatch"
    resolved_call_id = call_id or resolved_dispatch_id
    current_date = _read_optional_text(runtime_context, "current_date")
    current_time = _read_optional_text(runtime_context, "current_time") or datetime.now(UTC).isoformat()
    evidence_root = _read_optional_text(runtime_context, "evidence_root") or "/tmp/claw-trade/frontline-evidence"
    stage = _read_optional_text(runtime_context, "stage") or "frontline"
    tool_name = _read_optional_text(runtime_context, "tool_name") or "fundamental_fundamentals_data_pack"

    return {
        "run_id": run_id,
        "stage": stage,
        "worker_id": worker_id,
        "call_id": resolved_call_id,
        "dispatch_id": resolved_dispatch_id,
        "tool_name": tool_name,
        "evidence_root": evidence_root,
        "current_time": current_time,
        "current_date": current_date,
    }


def build_cn_a_fundamental_pack(
    request: DataPackRequest,
    dependencies: FundamentalPackDependencies | None = None,
) -> Any:
    return BuildCnAFundamentalPack(request, dependencies)


def _build_default_dependencies() -> FundamentalPackDependencies:
    config = load_fundamental_data_config(os.environ)
    if not config.tool_enabled:
        raise FundamentalOrchestratorError(
            FND_TOOL_DISABLED,
            "CN_A_FUNDAMENTAL_TOOL_ENABLED=false，fundamental 数据服务被禁用",
        )

    mongo_client = create_mongo_client(config)
    database = mongo_client[config.mongodb_database]
    collection = ensure_fundamental_cache_collection(database)
    ensure_fundamental_cache_indexes(collection)

    approved_api_names = tuple(sorted(set(list_tushare_v1_api_names()) | set(list_akshare_v1_api_names())))
    # Keep provider plans intact so executor can emit explicit skipped attempts for disabled providers.
    planner = DeterministicProviderPlanner(disable_tushare=False)
    phase2_planner = DeterministicAkshareSupplementPlanner(disable_akshare=False)
    executor = DeterministicProviderExecutor(
        config=config,
        tushare_fetcher=TushareFetcher(config),
        akshare_fetcher=AkShareFetcher(),
    )

    return FundamentalPackDependencies(
        normalize_input=NormalizeInput,
        cache_inspector=CollectionCacheInspector(collection=collection, approved_api_names=approved_api_names),
        provider_planner=planner,
        phase2_planner=phase2_planner,
        provider_executor=executor,
        cache_writer=CollectionCacheWriter(collection=collection, config=config),
        field_source_mapper=FieldSourceMapper(),
        freshness_checker=_FreshnessCheckerAdapter(FreshnessChecker()),
        missing_field_diagnostics=_MissingFieldDiagnosticsAdapter(MissingFieldDiagnostics()),
        evidence_capability_classifier=_EvidenceCapabilityClassifierAdapter(EvidenceCapabilityClassifier()),
        derived_summary_builder=_DerivedSummaryBuilderAdapter(DerivedSummaryBuilder()),
        data_pack_builder=_DataPackBuilderAdapter(DataPackBuilder()),
        close=mongo_client.close,
    )


def _extract_phase1_calls(plan: Any) -> list[ApiCallSpec]:
    raw_calls = _read_sequence_field(plan, "phase1_calls")
    if len(raw_calls) == 0:
        raw_calls = _read_sequence_field(plan, "calls")
    calls = [_coerce_api_call_spec(item) for item in raw_calls]
    return [call for call in calls if call.provider == "tushare"]


def _extract_phase2_calls(plan: Any) -> list[ApiCallSpec]:
    raw_calls = _read_sequence_field(plan, "phase2_calls")
    if len(raw_calls) == 0:
        raw_calls = _read_sequence_field(plan, "calls")
    calls = [_coerce_api_call_spec(item) for item in raw_calls]
    return [call for call in calls if call.provider == "akshare"]


def _coerce_api_call_spec(value: Any) -> ApiCallSpec:
    if isinstance(value, ApiCallSpec):
        return value
    if all(hasattr(value, field) for field in ("provider", "api_name", "role", "required", "field_family")):
        parameters = getattr(value, "parameters", {}) or {}
        if not isinstance(parameters, Mapping):
            parameters = {}
        return ApiCallSpec(
            provider=str(getattr(value, "provider")),
            api_name=str(getattr(value, "api_name")),
            role=str(getattr(value, "role")),
            required=bool(getattr(value, "required")),
            field_family=str(getattr(value, "field_family")),
            parameters={str(k): str(v) for k, v in dict(parameters).items()},
            timeout_ms=int(getattr(value, "timeout_ms")),
            retry_limit=int(getattr(value, "retry_limit")),
        )
    if isinstance(value, Mapping):
        return ApiCallSpec(
            provider=str(value.get("provider")),
            api_name=str(value.get("api_name")),
            role=str(value.get("role")),
            required=bool(value.get("required")),
            field_family=str(value.get("field_family")),
            parameters={str(k): str(v) for k, v in dict(value.get("parameters", {})).items()},
            timeout_ms=int(value.get("timeout_ms")),
            retry_limit=int(value.get("retry_limit")),
        )
    raise FundamentalOrchestratorError(
        FND_TOOL_ENTRYPOINT_INVALID_INPUT,
        f"ApiCallSpec 格式非法: {type(value)}",
    )


def _read_sequence_field(source: Any, field_name: str) -> list[Any]:
    if isinstance(source, Mapping):
        value = source.get(field_name)
    else:
        value = getattr(source, field_name, None)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(value)
    return []


def _build_skipped_provider_result(spec: ApiCallSpec, normalized: NormalizedInput, *, reason: str) -> ProviderResult:
    now = datetime.now(UTC).isoformat()
    attempt = ProviderAttempt(
        provider=spec.provider,
        role=spec.role,
        api_name=spec.api_name,
        attempt_seq=None,
        status="skipped",
        reason=reason,
        started_at=now,
        ended_at=now,
        duration_ms=0,
        retry_count=0,
        request_params_redacted=dict(spec.parameters),
        response_row_count=0,
        response_col_count=0,
        field_coverage=[],
        report_period=normalized.latest_report_period,
        announce_date=None,
        as_of=normalized.current_date,
        fetched_at=now,
        raw_payload_hash=None,
        raw_payload_ref=None,
        error_type=reason,
        error_message_redacted=reason,
    )
    return ProviderResult(
        attempt=attempt,
        raw_payload_hash=None,
        raw_payload_ref=None,
        extracted_fields=[],
        schema_changed=False,
    )


def _read_optional_text(source: Any, key: str) -> str | None:
    raw = _read_field(source, key)
    if raw is None:
        return None
    if not isinstance(raw, str):
        text = str(raw).strip()
    else:
        text = raw.strip()
    if text == "":
        return None
    return text


def _read_required_text(source: Any, key: str, *, payload_name: str) -> str:
    value = _read_optional_text(source, key)
    if value is None:
        raise FundamentalOrchestratorError(
            FND_TOOL_ENTRYPOINT_INVALID_INPUT,
            f"{payload_name}.{key} 缺失或为空",
        )
    return value


def _read_field(source: Any, key: str) -> Any:
    if isinstance(source, Mapping):
        return source.get(key)
    return getattr(source, key, None)
