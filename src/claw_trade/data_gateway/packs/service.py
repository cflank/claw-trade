from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any, Mapping, Sequence

from claw_trade.data_gateway.analysis.crypto_lens import CryptoLensAnalysisEvidenceStore
from claw_trade.data_gateway.models import (
    AdmissionCheckStatus,
    DataGap,
    DataGapReason,
    DomainPack,
    DomainPackResult,
    FreshnessStatus,
    GapSeverity,
    Market,
    PackDomain,
    PackRequest,
    PrioritySource,
    ProviderAttempt,
    ProviderCallSpec,
    ProviderKind,
    ProviderResult,
    ProviderStatus,
    RunProviderPlan,
    SourceRole,
    utc_now_iso,
)
from claw_trade.data_gateway.packs.fundamental import FundamentalPackService
from claw_trade.data_gateway.packs.hot_money import HotMoneyPackBuilder
from claw_trade.data_gateway.packs.lockup import LockupPackBuilder
from claw_trade.data_gateway.packs.market import MarketPackBuilder
from claw_trade.data_gateway.packs.materializer import materialize_domain_pack_result
from claw_trade.data_gateway.packs.news import NewsPackBuilder
from claw_trade.data_gateway.packs.policy import PolicyPackBuilder
from claw_trade.data_gateway.packs.social import SocialPackBuilder
from claw_trade.data_gateway.providers.base import ProviderAdapter
from claw_trade.data_gateway.providers.execution import ProviderExecutionEvidenceHelper
from claw_trade.data_gateway.providers.gate import run_provider_call_gate
from claw_trade.data_gateway.store.attempts import MongoAttemptStore
from claw_trade.data_gateway.store.cache import MongoCacheStore
from claw_trade.data_gateway.store.http_evidence import MongoProviderHttpEvidenceStore
from claw_trade.data_gateway.store.mongo import (
    CRYPTO_LENS_ANALYSIS_EVIDENCE,
    OPENBB_CACHE_ENTRIES,
    OPENBB_NORMALIZED,
    OPENBB_PROVIDER_ATTEMPTS,
    OPENBB_PROVIDER_HTTP_EVIDENCE,
    OPENBB_RATE_LIMITS,
    OPENBB_RAW_PAYLOADS,
    OPENBB_SINGLE_FLIGHT_CALLS,
)
from claw_trade.data_gateway.store.normalized import MongoNormalizedStore
from claw_trade.data_gateway.store.rate_limits import MongoRateLimitStore
from claw_trade.data_gateway.store.raw_payloads import MongoRawPayloadStore
from claw_trade.data_gateway.store.single_flight import MongoSingleFlightCoordinator
from pymongo import MongoClient


@dataclass
class DomainPackService:
    settings: Any
    adapters: tuple[ProviderAdapter, ...]
    market_builder: MarketPackBuilder = field(default_factory=MarketPackBuilder)
    news_builder: NewsPackBuilder = field(default_factory=NewsPackBuilder)
    social_builder: SocialPackBuilder = field(default_factory=SocialPackBuilder)
    policy_builder: PolicyPackBuilder = field(default_factory=PolicyPackBuilder)
    hot_money_builder: HotMoneyPackBuilder = field(default_factory=HotMoneyPackBuilder)
    lockup_builder: LockupPackBuilder = field(default_factory=LockupPackBuilder)
    provider_execution_helper: ProviderExecutionEvidenceHelper | None = None

    def __post_init__(self) -> None:
        self._adapters_by_id = {adapter.adapter_id: adapter for adapter in self.adapters}
        self._mongo_client: MongoClient[Any] | None = None
        self._crypto_lens_evidence_store: CryptoLensAnalysisEvidenceStore | None = None
        if self.provider_execution_helper is None:
            self.provider_execution_helper = self._build_execution_helper()
        self._gate_executor = self._build_gate_executor()

    def get_pack(self, request: PackRequest, run_plan: RunProviderPlan) -> DomainPackResult:
        if request.domain == PackDomain.MARKET:
            return self._get_market_pack(request=request, run_plan=run_plan)
        if request.domain == PackDomain.FUNDAMENTAL:
            fundamental_specs = _domain_specs(run_plan=run_plan, request=request)
            warehouse_gaps = _warehouse_unavailable_gaps(request=request, specs=fundamental_specs, settings=self.settings)
            return FundamentalPackService(
                settings=self.settings,
                adapters=self.adapters,
                provider_execution_helper=self._gate_executor,
            ).get_pack(
                request,
                run_plan,
                warehouse_results=self._warehouse_results_for_domain(request=request, specs=fundamental_specs),
                warehouse_gaps=warehouse_gaps,
            )
        if request.domain == PackDomain.NEWS:
            news_specs = _domain_specs(run_plan=run_plan, request=request)
            return self.news_builder.build(
                request=request,
                run_plan=run_plan,
                adapters_by_id=self._adapters_by_id,
                provider_execution_helper=self._gate_executor,
                warehouse_results=self._warehouse_results_for_domain(request=request, specs=news_specs),
                extra_gaps=_warehouse_unavailable_gaps(request=request, specs=news_specs, settings=self.settings),
            )
        if request.domain == PackDomain.SOCIAL:
            social_specs = _domain_specs(run_plan=run_plan, request=request)
            return self.social_builder.build(
                request=request,
                run_plan=run_plan,
                adapters_by_id=self._adapters_by_id,
                provider_execution_helper=self._gate_executor,
                warehouse_results=self._warehouse_results_for_domain(request=request, specs=social_specs),
                extra_gaps=_warehouse_unavailable_gaps(request=request, specs=social_specs, settings=self.settings),
            )
        if request.domain == PackDomain.POLICY:
            return self.policy_builder.build(
                request=request,
                run_plan=run_plan,
                adapters_by_id=self._adapters_by_id,
                provider_execution_helper=self._gate_executor,
            )
        if request.domain == PackDomain.HOT_MONEY:
            return self.hot_money_builder.build(
                request=request,
                run_plan=run_plan,
                adapters_by_id=self._adapters_by_id,
                provider_execution_helper=self._gate_executor,
            )
        if request.domain == PackDomain.LOCKUP:
            return self.lockup_builder.build(
                request=request,
                run_plan=run_plan,
                adapters_by_id=self._adapters_by_id,
                provider_execution_helper=self._gate_executor,
            )
        raise ValueError(f"unsupported pack domain: {request.domain}")

    def materialize_for_worker(self, pack_result: DomainPackResult) -> DomainPack:
        return materialize_domain_pack_result(pack_result)

    def _get_market_pack(self, *, request: PackRequest, run_plan: RunProviderPlan) -> DomainPackResult:
        specs = tuple(
            spec
            for spec in run_plan.call_specs
            if spec.domain == PackDomain.MARKET and spec.market == request.market
        )
        if not specs:
            gap = DataGap(
                gap_id=f"{request.run_id}:{request.call_id}:market:source_not_configured",
                domain=PackDomain.MARKET,
                severity=GapSeverity.FAIL,
                reason=DataGapReason.SOURCE_NOT_CONFIGURED,
                field_path="market",
                provider_candidates=(),
                attempt_ids=(),
                root_cause="no market call specs in run plan",
                next_action="configure market providers",
            )
            return self.market_builder.build(
                request=request,
                run_plan=run_plan,
                results=(),
                data_gaps=(gap,),
            )

        results_list: list[ProviderResult] = []
        warehouse_result = self._warehouse_market_result(request=request, specs=specs)
        warehouse_coverage_group: str | None = None
        if warehouse_result is not None:
            warehouse_result = self._record_attempt_only(warehouse_result)
            results_list.append(warehouse_result)
            warehouse_coverage_group = warehouse_result.spec.coverage_group
        for spec in specs:
            if (
                warehouse_coverage_group
                and spec.coverage_group == warehouse_coverage_group
                and not spec.user_preferred
                and spec.priority_source != PrioritySource.USER_PREFERRED
            ):
                continue
            results_list.append(
                self._execute_market_spec(
                    request=request,
                    spec=spec,
                    optional_skip_reason=_optional_market_skip_reason(spec=spec, previous_results=results_list),
                )
            )
        results = tuple(results_list)
        return self.market_builder.build(
            request=request,
            run_plan=run_plan,
            results=results,
            data_gaps=tuple(gap for gap in run_plan.initial_gaps if gap.domain == PackDomain.MARKET)
            + _warehouse_unavailable_gaps(request=request, specs=specs, settings=self.settings),
            chart_object_store_uri=_chart_object_store_uri(self.settings),
            crypto_lens_evidence_store=self._build_crypto_lens_evidence_store()
            if request.market == Market.CRYPTO
            else None,
        )

    def _execute_market_spec(
        self,
        *,
        request: PackRequest,
        spec: ProviderCallSpec,
        optional_skip_reason: str | None = None,
    ) -> ProviderResult:
        started = utc_now_iso()
        t0 = time.perf_counter()
        adapter = self._adapters_by_id.get(spec.adapter_id)
        if adapter is None:
            result = self._market_result(
                request=request,
                spec=spec,
                started=started,
                status=ProviderStatus.SKIPPED_NOT_CONFIGURED,
                freshness=FreshnessStatus.NOT_FETCHED,
                rows=(),
                row_count=0,
                raw_ref=None,
                normalized_ref=None,
                error_code="adapter_not_configured",
                error_message=f"adapter {spec.adapter_id} not configured",
                latency_ms=_elapsed_ms(t0),
                adapter_kind=spec.provider_kind.value,
                provider_kind=spec.provider_kind,
            )
            return self._record_attempt_only(result)

        credential = adapter.validate_credentials()
        if credential.status == AdmissionCheckStatus.MISSING:
            result = self._market_result(
                request=request,
                spec=spec,
                started=started,
                status=ProviderStatus.CREDENTIAL_MISSING,
                freshness=FreshnessStatus.NOT_FETCHED,
                rows=(),
                row_count=0,
                raw_ref=None,
                normalized_ref=None,
                error_code="credential_missing",
                error_message=credential.root_cause or "credential missing",
                latency_ms=_elapsed_ms(t0),
                adapter_kind=adapter.adapter_kind,
                provider_kind=adapter.provider_kind,
            )
            return self._record_attempt_only(result)

        if optional_skip_reason:
            result = self._market_result(
                request=request,
                spec=spec,
                started=started,
                status=ProviderStatus.NOT_APPLICABLE,
                freshness=FreshnessStatus.NOT_FETCHED,
                rows=(),
                row_count=0,
                raw_ref=None,
                normalized_ref=None,
                error_code=ProviderStatus.NOT_APPLICABLE.value,
                error_message=optional_skip_reason,
                latency_ms=_elapsed_ms(t0),
                adapter_kind=adapter.adapter_kind,
                provider_kind=adapter.provider_kind,
            )
            return self._record_attempt_only(result)

        executor = self._gate_executor
        if executor is not None:
            return executor.execute(
                request=request,
                spec=spec,
                adapter=adapter,
                started_at=started,
            )

        result = self._market_result(
            request=request,
            spec=spec,
            started=started,
            status=ProviderStatus.EVIDENCE_WRITE_FAILED,
            freshness=FreshnessStatus.NOT_FETCHED,
            rows=(),
            row_count=0,
            raw_ref=None,
            normalized_ref=None,
            error_code="provider_call_gate_missing",
            error_message="provider call gate is not configured; pack runtime remote calls must use run_provider_call_gate",
            latency_ms=_elapsed_ms(t0),
            adapter_kind=adapter.adapter_kind,
            provider_kind=adapter.provider_kind,
        )
        return self._record_attempt_only(result)

    def _build_execution_helper(self) -> ProviderExecutionEvidenceHelper | None:
        mongo_uri = getattr(self.settings, "mongo_uri", None)
        if not isinstance(mongo_uri, str) or not mongo_uri.strip():
            return None
        inline_max = getattr(self.settings, "raw_payload_inline_max_bytes", 200_000)
        provider_settings = getattr(self.settings, "provider_settings", None)
        self._mongo_client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
        database = self._mongo_client.get_default_database("claw_trade_openbb")
        return ProviderExecutionEvidenceHelper(
            raw_store=MongoRawPayloadStore(database[OPENBB_RAW_PAYLOADS], inline_max_bytes=int(inline_max)),
            normalized_store=MongoNormalizedStore(database[OPENBB_NORMALIZED]),
            attempt_store=MongoAttemptStore(database[OPENBB_PROVIDER_ATTEMPTS]),
            http_evidence_store=MongoProviderHttpEvidenceStore(database[OPENBB_PROVIDER_HTTP_EVIDENCE]),
            provider_settings=provider_settings if isinstance(provider_settings, Mapping) else None,
        )

    def _build_gate_executor(self) -> "_GateControlledProviderExecutor | None":
        helper = self.provider_execution_helper
        if helper is None:
            return None
        mongo_uri = getattr(self.settings, "mongo_uri", None)
        if not isinstance(mongo_uri, str) or not mongo_uri.strip():
            return None
        if self._mongo_client is None:
            self._mongo_client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
        database = self._mongo_client.get_default_database("claw_trade_openbb")
        attempt_store = helper.attempt_store
        return _GateControlledProviderExecutor(
            helper=helper,
            cache_store=MongoCacheStore(database[OPENBB_CACHE_ENTRIES]),
            rate_limit_store=MongoRateLimitStore(database[OPENBB_RATE_LIMITS]),
            single_flight=MongoSingleFlightCoordinator(
                collection=database[OPENBB_SINGLE_FLIGHT_CALLS],
                attempt_store=attempt_store,
            ),
            attempt_store=attempt_store,
        )

    def _warehouse_market_result(
        self,
        *,
        request: PackRequest,
        specs: Sequence[ProviderCallSpec],
    ) -> ProviderResult | None:
        if request.market != Market.CN_A:
            return None
        if request.domain.value in request.freshness_policy.require_remote_for_domains:
            return None
        spec = _warehouse_market_spec(specs)
        if spec is None:
            return None
        mongo_uri = getattr(self.settings, "mongo_uri", None)
        if not isinstance(mongo_uri, str) or not mongo_uri.strip():
            return None
        if self._mongo_client is None:
            self._mongo_client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
        database = self._mongo_client.get_default_database("claw_trade_openbb")
        doc = _find_cn_a_qfq_daily_doc(database[OPENBB_NORMALIZED], request.ticker)
        if not doc:
            return None
        rows = _cn_a_qfq_rows_for_request(doc=doc, request=request)
        if (
            not rows
            or not _cn_a_qfq_rows_have_required_daily_fields(rows)
            or not _cn_a_warehouse_rows_are_fresh(rows=rows, request=request)
        ):
            return None
        return self._warehouse_market_result_from_rows(
            request=request,
            source_spec=spec,
            rows=rows,
            docs=(doc,),
            schema_id="cn_a.baostock.qfq_daily.v1",
            endpoint="cn_a_baostock_qfq_daily",
            data_type="cn_a.baostock.qfq_daily",
            license_policy_id="local_seed_audit",
        )

    def _warehouse_market_result_from_rows(
        self,
        *,
        request: PackRequest,
        source_spec: ProviderCallSpec,
        rows: tuple[Mapping[str, Any], ...],
        docs: Sequence[Mapping[str, Any]],
        schema_id: str,
        endpoint: str,
        data_type: str,
        license_policy_id: str,
    ) -> ProviderResult:
        normalized_refs = tuple(f"mongo://{OPENBB_NORMALIZED}/{doc.get('_id')}" for doc in docs if doc.get("_id"))
        raw_refs = tuple(str(doc.get("source_raw_ref") or "") for doc in docs if doc.get("source_raw_ref"))
        spec = _warehouse_market_spec(
            (source_spec,),
            schema_id=schema_id,
            endpoint=endpoint,
            data_type=data_type,
            license_policy_id=license_policy_id,
        )
        assert spec is not None
        started = utc_now_iso()
        result = self._market_result(
            request=request,
            spec=spec,
            started=started,
            status=ProviderStatus.WAREHOUSE_HIT,
            freshness=FreshnessStatus.FRESH_WAREHOUSE,
            rows=rows,
            row_count=len(rows),
            raw_ref=raw_refs[0] if raw_refs else None,
            normalized_ref=normalized_refs[0] if normalized_refs else None,
            error_code=None,
            error_message=None,
            latency_ms=0,
            adapter_kind=ProviderKind.PROJECT_EXTENSION.value,
            provider_kind=ProviderKind.PROJECT_EXTENSION,
        )
        first_doc = docs[0] if docs else {}
        stamp = str(first_doc.get("created_at") or started)
        attempt = replace(
            result.attempt,
            started_at=stamp,
            finished_at=stamp,
            source_metadata={
                "warehouse_collection": OPENBB_NORMALIZED,
                "warehouse_schema_id": schema_id,
                "warehouse_ref": normalized_refs[0] if normalized_refs else None,
                "warehouse_refs": normalized_refs,
            },
        )
        return replace(result, requested_at=stamp, attempt=attempt)

    def _build_crypto_lens_evidence_store(self) -> CryptoLensAnalysisEvidenceStore | None:
        if self._crypto_lens_evidence_store is not None:
            return self._crypto_lens_evidence_store
        mongo_uri = getattr(self.settings, "mongo_uri", None)
        if not isinstance(mongo_uri, str) or not mongo_uri.strip():
            return None
        if self._mongo_client is None:
            self._mongo_client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
        database = self._mongo_client.get_default_database("claw_trade_openbb")
        self._crypto_lens_evidence_store = CryptoLensAnalysisEvidenceStore(database[CRYPTO_LENS_ANALYSIS_EVIDENCE])
        return self._crypto_lens_evidence_store

    def _market_result(
        self,
        *,
        request: PackRequest,
        spec: ProviderCallSpec,
        started: str,
        status: ProviderStatus,
        freshness: FreshnessStatus,
        rows: tuple[Mapping[str, Any], ...],
        row_count: int,
        raw_ref: str | None,
        normalized_ref: str | None,
        error_code: str | None,
        error_message: str | None,
        latency_ms: int,
        adapter_kind: str,
        provider_kind: ProviderKind,
    ) -> ProviderResult:
        attempt_id = f"{request.run_id}:{request.call_id}:{spec.adapter_id}:{spec.endpoint}"
        attempt = ProviderAttempt(
            attempt_id=attempt_id,
            run_id=request.run_id,
            call_id=request.call_id,
            worker_id=request.worker_id,
            pack=PackDomain.MARKET.value,
            provider=spec.provider,
            adapter_id=spec.adapter_id,
            adapter_kind=adapter_kind,
            provider_kind=provider_kind,
            provider_config_version=spec.provider_config_version,
            endpoint=spec.endpoint,
            source_role=spec.source_role,
            started_at=started,
            finished_at=utc_now_iso(),
            status=status,
            required=spec.required,
            attempt_required=spec.attempt_required,
            coverage_group=spec.coverage_group,
            coverage_quorum=spec.coverage_quorum,
            priority_source=spec.priority_source,
            user_preferred=spec.user_preferred,
            from_cache=False,
            cache_status=None,
            single_flight_role="none",
            shared_from_attempt_id=None,
            latency_ms=max(0, latency_ms),
            row_count=row_count,
            raw_ref=raw_ref,
            normalized_ref=normalized_ref,
            error_code=error_code,
            error_message=error_message,
            schema_id=spec.expected_schema_id,
            license_note="approved",
        )
        return ProviderResult(
            spec=spec,
            status=status,
            request_id=None,
            requested_at=started,
            latency_ms=max(0, latency_ms),
            source_role=spec.source_role,
            freshness=freshness,
            license_note="approved",
            raw_ref=raw_ref,
            normalized_ref=normalized_ref,
            rows=rows,
            row_count=row_count,
            cache_receipt=None,
            attempt=attempt,
            error_code=error_code,
            error_message=error_message,
        )

    def _record_attempt_only(self, result: ProviderResult) -> ProviderResult:
        helper = self.provider_execution_helper
        if helper is None:
            return result
        return helper.record_attempt_only(result)

    def _warehouse_results_for_domain(
        self,
        *,
        request: PackRequest,
        specs: Sequence[ProviderCallSpec],
    ) -> tuple[ProviderResult, ...]:
        if request.domain == PackDomain.MARKET:
            return ()
        if request.domain.value in request.freshness_policy.require_remote_for_domains:
            return ()
        mongo_uri = getattr(self.settings, "mongo_uri", None)
        if not isinstance(mongo_uri, str) or not mongo_uri.strip():
            return ()
        if self._mongo_client is None:
            self._mongo_client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
        database = self._mongo_client.get_default_database("claw_trade_openbb")
        collection = database[OPENBB_NORMALIZED]
        results: list[ProviderResult] = []
        covered_groups: set[str] = set()
        for spec in specs:
            if spec.coverage_group and spec.coverage_group in covered_groups:
                continue
            doc = _find_domain_warehouse_doc(collection=collection, request=request, spec=spec)
            if doc is None:
                continue
            result = self._warehouse_result_from_doc(request=request, spec=spec, doc=doc)
            result = self._record_attempt_only(result)
            results.append(result)
            if spec.coverage_group:
                covered_groups.add(spec.coverage_group)
        return tuple(results)

    def _warehouse_result_from_doc(
        self,
        *,
        request: PackRequest,
        spec: ProviderCallSpec,
        doc: Mapping[str, Any],
    ) -> ProviderResult:
        normalized_ref = f"mongo://{OPENBB_NORMALIZED}/{doc.get('_id')}"
        raw_ref = str(doc.get("source_raw_ref") or "")
        rows = tuple(row for row in (doc.get("rows") or ()) if isinstance(row, Mapping))
        started = str(doc.get("created_at") or utc_now_iso())
        warehouse_spec = replace(
            spec,
            call_key=f"{request.domain.value}:mongo.openbb_normalized:{spec.endpoint}",
            provider="mongo_warehouse",
            adapter_id="mongo.openbb_normalized",
            provider_kind=ProviderKind.PROJECT_EXTENSION,
            endpoint=f"warehouse:{spec.endpoint}",
            params={
                **dict(spec.params),
                "warehouse_collection": OPENBB_NORMALIZED,
                "warehouse_schema_id": str(doc.get("schema_id") or spec.expected_schema_id),
            },
            cache_ttl_seconds=0,
            priority=spec.priority - 1000,
            priority_source=PrioritySource.SYSTEM_DEFAULT,
            user_preferred=False,
            raw_export_policy="metadata_only",
            data_type=spec.data_type or spec.expected_schema_id,
            managed_http_required=False,
        )
        attempt = ProviderAttempt(
            attempt_id=f"{request.run_id}:{request.call_id}:mongo.openbb_normalized:{spec.endpoint}",
            run_id=request.run_id,
            call_id=request.call_id,
            worker_id=request.worker_id,
            pack=request.domain.value,
            provider="mongo_warehouse",
            adapter_id="mongo.openbb_normalized",
            adapter_kind=ProviderKind.PROJECT_EXTENSION.value,
            provider_kind=ProviderKind.PROJECT_EXTENSION,
            provider_config_version=spec.provider_config_version,
            endpoint=warehouse_spec.endpoint,
            source_role=spec.source_role,
            started_at=started,
            finished_at=started,
            status=ProviderStatus.WAREHOUSE_HIT,
            required=spec.required,
            attempt_required=spec.attempt_required,
            coverage_group=spec.coverage_group,
            coverage_quorum=spec.coverage_quorum,
            priority_source=PrioritySource.SYSTEM_DEFAULT,
            user_preferred=False,
            from_cache=False,
            cache_status=None,
            single_flight_role="none",
            shared_from_attempt_id=None,
            latency_ms=0,
            row_count=len(rows),
            raw_ref=raw_ref or None,
            normalized_ref=normalized_ref,
            error_code=None,
            error_message=None,
            schema_id=str(doc.get("schema_id") or spec.expected_schema_id),
            license_note="warehouse_hit",
            source_metadata={
                "warehouse_collection": OPENBB_NORMALIZED,
                "warehouse_schema_id": str(doc.get("schema_id") or spec.expected_schema_id),
                "warehouse_ref": normalized_ref,
            },
        )
        return ProviderResult(
            spec=warehouse_spec,
            status=ProviderStatus.WAREHOUSE_HIT,
            request_id=None,
            requested_at=started,
            latency_ms=0,
            source_role=spec.source_role,
            freshness=FreshnessStatus.FRESH_WAREHOUSE,
            license_note="warehouse_hit",
            raw_ref=raw_ref or None,
            normalized_ref=normalized_ref,
            rows=rows,
            row_count=len(rows),
            cache_receipt=None,
            attempt=attempt,
            error_code=None,
            error_message=None,
        )


def _elapsed_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _domain_specs(*, run_plan: RunProviderPlan, request: PackRequest) -> tuple[ProviderCallSpec, ...]:
    return tuple(
        spec
        for spec in run_plan.call_specs
        if spec.domain == request.domain and spec.market == request.market
    )


def _warehouse_unavailable_gaps(
    *,
    request: PackRequest,
    specs: Sequence[ProviderCallSpec],
    settings: Any,
) -> tuple[DataGap, ...]:
    if not specs:
        return ()
    if _has_mongo_uri(settings):
        return ()
    return (
        DataGap(
            gap_id=f"{request.run_id}:{request.call_id}:{request.domain.value}:mongo_warehouse_unconfigured",
            domain=request.domain,
            severity=GapSeverity.FAIL,
            reason=DataGapReason.MONGO_MISSING,
            field_path=f"{request.domain.value}.warehouse",
            provider_candidates=("mongo.openbb_normalized",),
            attempt_ids=(),
            root_cause="Mongo 主仓库未配置，无法先查 openbb_normalized。",
            next_action="配置 mongo_uri，并确认所需 raw/normalized/feature evidence 已入库。",
        ),
    )


def _has_mongo_uri(settings: Any) -> bool:
    mongo_uri = getattr(settings, "mongo_uri", None)
    return isinstance(mongo_uri, str) and bool(mongo_uri.strip())


def _find_domain_warehouse_doc(
    *,
    collection: Any,
    request: PackRequest,
    spec: ProviderCallSpec,
) -> Mapping[str, Any] | None:
    query = {
        "market": request.market.value,
        "domain": request.domain.value,
        "schema_id": spec.expected_schema_id,
    }
    try:
        cursor = collection.find(query).limit(20)
    except AttributeError:
        return None
    candidates = [
        doc
        for doc in cursor
        if isinstance(doc, Mapping)
        and _warehouse_doc_matches_ticker(doc=doc, ticker=request.ticker)
        and _warehouse_doc_is_fresh(doc=doc, request=request)
        and _warehouse_doc_has_required_fields(doc=doc, request=request, spec=spec)
    ]
    if not candidates:
        return None
    return sorted(candidates, key=lambda doc: str(doc.get("period_end") or doc.get("created_at") or ""), reverse=True)[0]


def _warehouse_doc_matches_ticker(*, doc: Mapping[str, Any], ticker: str) -> bool:
    normalized_ticker = ticker.strip().upper()
    if str(doc.get("ticker") or "").strip().upper() == normalized_ticker:
        return True
    for row in doc.get("rows") or ():
        if isinstance(row, Mapping) and str(row.get("ticker") or "").strip().upper() == normalized_ticker:
            return True
    return False


def _warehouse_doc_is_fresh(*, doc: Mapping[str, Any], request: PackRequest) -> bool:
    end = _parse_date(request.end_date)
    latest = _parse_date(str(doc.get("period_end") or doc.get("latest_data_time") or doc.get("created_at") or ""))
    if end is None or latest is None:
        return False
    if latest > end:
        return False
    return (end - latest).days <= 7


def _warehouse_doc_has_required_fields(
    *,
    doc: Mapping[str, Any],
    request: PackRequest,
    spec: ProviderCallSpec,
) -> bool:
    rows = tuple(row for row in (doc.get("rows") or ()) if isinstance(row, Mapping))
    if not rows:
        return False
    required_groups = _warehouse_required_field_groups(request=request, spec=spec)
    if not required_groups:
        return True
    return all(any(_row_has_any_field(row, fields) for row in rows) for fields in required_groups)


def _warehouse_required_field_groups(*, request: PackRequest, spec: ProviderCallSpec) -> tuple[tuple[str, ...], ...]:
    params_groups = spec.params.get("required_field_groups")
    if isinstance(params_groups, (list, tuple)):
        groups: list[tuple[str, ...]] = []
        for group in params_groups:
            if isinstance(group, (list, tuple)):
                fields = tuple(str(field) for field in group if str(field).strip())
            else:
                fields = (str(group),) if str(group).strip() else ()
            if fields:
                groups.append(fields)
        return tuple(groups)
    params_fields = spec.params.get("required_fields")
    if isinstance(params_fields, (list, tuple)):
        return tuple((str(field),) for field in params_fields if str(field).strip())
    text = "|".join(
        (
            request.domain.value,
            spec.endpoint,
            spec.data_type or "",
            spec.expected_schema_id,
        )
    ).lower()
    if request.domain == PackDomain.FUNDAMENTAL:
        if request.market == Market.CRYPTO:
            return (("valuation.market_cap_usd",), ("supply.circulating",), ("supply.total",))
        return (("valuation.pe",), ("valuation.pb",), ("financial_indicators.roe",))
    if request.domain in {PackDomain.NEWS, PackDomain.SOCIAL}:
        return (("title", "headline", "summary"),)
    if "policy" in text:
        return (("summary",),)
    return ()


def _row_has_any_field(row: Mapping[str, Any], fields: tuple[str, ...]) -> bool:
    return any(_row_has_field(row, field) for field in fields)


def _row_has_field(row: Mapping[str, Any], field: str) -> bool:
    if field in row and row[field] not in (None, ""):
        return True
    current: Any = row
    for part in field.split("."):
        if not isinstance(current, Mapping):
            return False
        current = current.get(part)
    return current not in (None, "")


def _optional_market_skip_reason(*, spec: ProviderCallSpec, previous_results: Sequence[ProviderResult]) -> str | None:
    if spec.required or not spec.coverage_group:
        return None
    if spec.user_preferred or spec.priority_source == PrioritySource.USER_PREFERRED:
        return None
    quorum = spec.coverage_quorum or 1
    covered = sum(
        1
        for result in previous_results
        if result.spec.coverage_group == spec.coverage_group and _result_covers_provider_group(result)
    )
    if covered < quorum:
        return None
    return (
        f"coverage quorum already satisfied for {spec.coverage_group}; "
        f"optional provider {spec.provider}/{spec.endpoint} was not requested"
    )


def _result_covers_provider_group(result: ProviderResult) -> bool:
    if result.status not in {
        ProviderStatus.REMOTE_SUCCESS,
        ProviderStatus.CACHE_HIT,
        ProviderStatus.WAREHOUSE_HIT,
        ProviderStatus.SHARED_RESULT,
    }:
        return False
    return bool(result.normalized_ref and result.row_count > 0)


def _warehouse_market_spec(
    specs: Sequence[ProviderCallSpec],
    *,
    schema_id: str = "cn_a.baostock.qfq_daily.v1",
    endpoint: str = "cn_a_baostock_qfq_daily",
    data_type: str = "cn_a.baostock.qfq_daily",
    license_policy_id: str = "local_seed_audit",
) -> ProviderCallSpec | None:
    source = next((spec for spec in specs if spec.coverage_group == "cn_a_market_kline"), None)
    if source is None:
        return None
    return replace(
        source,
        call_key="market:mongo.openbb_normalized:cn_a_baostock_qfq_daily",
        provider="mongo_warehouse",
        adapter_id="mongo.openbb_normalized",
        provider_kind=ProviderKind.PROJECT_EXTENSION,
        endpoint=endpoint,
        source_role=SourceRole.MARKET_DATA,
        required=True,
        attempt_required=True,
        coverage_group=source.coverage_group or "cn_a_market_kline",
        coverage_quorum=1,
        params={
            **dict(source.params),
            "warehouse_collection": OPENBB_NORMALIZED,
            "warehouse_schema_id": schema_id,
        },
        cache_ttl_seconds=0,
        license_policy_id=license_policy_id,
        expected_schema_id=schema_id,
        priority=-100,
        priority_source=PrioritySource.SYSTEM_DEFAULT,
        user_preferred=False,
        raw_export_policy="metadata_only",
        data_type=data_type,
        managed_http_required=False,
    )


def _find_cn_a_qfq_daily_doc(collection: Any, ticker: str) -> Mapping[str, Any] | None:
    normalized_ticker = ticker.strip().upper()
    if not normalized_ticker:
        return None
    query = {
        "schema_id": "cn_a.baostock.qfq_daily.v1",
        "market": Market.CN_A.value,
        "rows.ticker": normalized_ticker,
    }
    try:
        cursor = collection.find(query).limit(2)
    except AttributeError:
        return None
    docs = tuple(cursor)
    if len(docs) != 1:
        return None
    doc = docs[0]
    if not isinstance(doc, Mapping):
        return None
    if str(doc.get("domain") or "").strip() not in {PackDomain.MARKET.value, PackDomain.SELECT_FEATURE.value}:
        return None
    return doc


def _cn_a_qfq_rows_for_request(*, doc: Mapping[str, Any], request: PackRequest) -> tuple[Mapping[str, Any], ...]:
    start = _parse_date(request.start_date)
    end = _parse_date(request.end_date)
    ticker = request.ticker.strip().upper()
    rows: list[Mapping[str, Any]] = []
    for row in doc.get("rows") or ():
        if not isinstance(row, Mapping):
            continue
        if str(row.get("ticker") or "").strip().upper() != ticker:
            continue
        row_date = _parse_date(str(row.get("trade_date") or row.get("date") or ""))
        if row_date is None:
            continue
        if start is not None and row_date < start:
            continue
        if end is not None and row_date > end:
            continue
        rows.append(row)
    return tuple(sorted(rows, key=lambda item: str(item.get("trade_date") or item.get("date") or "")))


def _cn_a_warehouse_rows_are_fresh(*, rows: Sequence[Mapping[str, Any]], request: PackRequest) -> bool:
    if not rows:
        return False
    latest = _parse_date(str(rows[-1].get("trade_date") or rows[-1].get("date") or ""))
    end = _parse_date(request.end_date)
    if latest is None or end is None:
        return False
    if latest > end:
        return False
    return (end - latest).days <= 7


def _cn_a_qfq_rows_have_required_daily_fields(rows: Sequence[Mapping[str, Any]]) -> bool:
    required = ("open", "high", "low", "close", "volume")
    for row in rows:
        if not str(row.get("trade_date") or row.get("date") or "").strip():
            return False
        for field in required:
            value = row.get(field)
            if value is None or str(value).strip() == "":
                return False
    return True


def _parse_date(value: str) -> date | None:
    text = value.strip()
    if not text:
        return None
    if len(text) == 8 and text.isdigit():
        text = f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _chart_object_store_uri(settings: Any) -> str | None:
    value = getattr(settings, "object_store_uri", None)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


@dataclass
class _GateControlledProviderExecutor:
    helper: ProviderExecutionEvidenceHelper
    cache_store: MongoCacheStore
    rate_limit_store: MongoRateLimitStore
    single_flight: MongoSingleFlightCoordinator
    attempt_store: MongoAttemptStore
    gate_controlled: bool = True

    def execute(
        self,
        *,
        request: PackRequest,
        spec: ProviderCallSpec,
        adapter: ProviderAdapter,
        started_at: str,
    ) -> ProviderResult:
        owner_result: dict[str, ProviderResult] = {}

        def owner_call() -> ProviderResult:
            result = self.helper.execute(
                request=request,
                spec=spec,
                adapter=adapter,
                started_at=started_at,
            )
            owner_result["value"] = result
            return result

        gate_result = run_provider_call_gate(
            request=request,
            spec=spec,
            cache_store=self.cache_store,
            rate_limit_store=self.rate_limit_store,
            single_flight=self.single_flight,
            attempt_store=self.attempt_store,
            owner_call=owner_call,
        )
        if gate_result.status == ProviderStatus.REMOTE_SUCCESS and "value" in owner_result:
            return owner_result["value"]
        if gate_result.status not in {
            ProviderStatus.CACHE_HIT,
            ProviderStatus.CACHED_EMPTY,
            ProviderStatus.RATE_LIMITED,
            ProviderStatus.SHARED_RESULT,
        } and "value" in owner_result:
            return owner_result["value"]
        return _provider_result_from_gate_result(
            request=request,
            spec=spec,
            gate_result=gate_result,
            attempt_store=self.attempt_store,
            normalized_store=self.helper.normalized_store,
        )

    def record_attempt_only(self, result: ProviderResult) -> ProviderResult:
        return self.helper.record_attempt_only(result)


def _provider_result_from_gate_result(
    *,
    request: PackRequest,
    spec: ProviderCallSpec,
    gate_result: Any,
    attempt_store: MongoAttemptStore,
    normalized_store: Any | None = None,
) -> ProviderResult:
    attempt = None
    if gate_result.attempt_ref:
        try:
            attempt = attempt_store.get(gate_result.attempt_ref)
        except Exception:  # noqa: BLE001
            attempt = None
    if attempt is None:
        attempt = ProviderAttempt(
            attempt_id=gate_result.attempt_ref or f"{request.run_id}:{request.call_id}:{spec.adapter_id}:{spec.endpoint}:gate",
            run_id=request.run_id,
            call_id=request.call_id,
            worker_id=request.worker_id,
            pack=request.domain.value,
            provider=spec.provider,
            adapter_id=spec.adapter_id,
            adapter_kind=spec.provider_kind.value,
            provider_kind=spec.provider_kind,
            provider_config_version=spec.provider_config_version,
            endpoint=spec.endpoint,
            source_role=spec.source_role,
            started_at=utc_now_iso(),
            finished_at=utc_now_iso(),
            status=gate_result.status,
            required=spec.required,
            attempt_required=spec.attempt_required,
            coverage_group=spec.coverage_group,
            coverage_quorum=spec.coverage_quorum,
            priority_source=spec.priority_source,
            user_preferred=spec.user_preferred,
            from_cache=gate_result.status in {ProviderStatus.CACHE_HIT, ProviderStatus.CACHED_EMPTY},
            cache_status=gate_result.status if gate_result.status in {ProviderStatus.CACHE_HIT, ProviderStatus.CACHED_EMPTY} else None,
            single_flight_role="consumer" if gate_result.status == ProviderStatus.SHARED_RESULT else "none",
            shared_from_attempt_id=gate_result.shared_owner_attempt_ref,
            latency_ms=0,
            row_count=len(gate_result.rows),
            raw_ref=gate_result.raw_payload_ref,
            normalized_ref=gate_result.normalized_ref,
            error_code=gate_result.status.value if gate_result.status != ProviderStatus.CACHE_HIT else None,
            error_message=gate_result.data_gaps[0].root_cause if gate_result.data_gaps else None,
            schema_id=spec.expected_schema_id,
            license_note=gate_result.status.value,
        )
    freshness = (
        FreshnessStatus.FRESH_CACHE
        if gate_result.status in {ProviderStatus.CACHE_HIT, ProviderStatus.SHARED_RESULT}
        else FreshnessStatus.NOT_FETCHED
    )
    rows = tuple(gate_result.rows)
    row_count = len(rows) if rows else int(attempt.row_count or 0)
    if gate_result.status in {ProviderStatus.CACHE_HIT, ProviderStatus.SHARED_RESULT} and gate_result.normalized_ref:
        normalized_rows = _read_normalized_rows(normalized_store=normalized_store, normalized_ref=gate_result.normalized_ref)
        if normalized_rows:
            rows = normalized_rows
            row_count = len(rows)
    return ProviderResult(
        spec=spec,
        status=gate_result.status,
        request_id=None,
        requested_at=attempt.started_at,
        latency_ms=attempt.latency_ms,
        source_role=spec.source_role,
        freshness=freshness,
        license_note=attempt.license_note,
        raw_ref=gate_result.raw_payload_ref,
        normalized_ref=gate_result.normalized_ref,
        rows=rows,
        row_count=row_count,
        cache_receipt=None,
        attempt=attempt,
        error_code=attempt.error_code,
        error_message=attempt.error_message,
    )


def _read_normalized_rows(*, normalized_store: Any | None, normalized_ref: str) -> tuple[Mapping[str, Any], ...]:
    read = getattr(normalized_store, "read", None)
    if not callable(read):
        return ()
    try:
        normalized = read(normalized_ref)
    except Exception:  # noqa: BLE001
        return ()
    rows = getattr(normalized, "rows", ())
    if not rows:
        return ()
    return tuple(row for row in rows if isinstance(row, Mapping))
