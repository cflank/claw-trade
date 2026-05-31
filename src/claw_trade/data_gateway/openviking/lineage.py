from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from datetime import UTC, datetime
from enum import Enum
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from claw_trade.artifacts.manifest import ApprovedManifest
from claw_trade.artifacts.openviking_client import OpenVikingClient
from claw_trade.artifacts.refs import ApprovedMaterial
from claw_trade.data_gateway.models import (
    ChartAsset,
    DataGapReason,
    DomainPackResult,
    FreshnessPolicy,
    Market,
    PackAuditPayload,
    PackDomain,
    PackRequest,
    PrioritySource,
    ProviderAttempt,
    ProviderKind,
    ProviderStatus,
    Readiness,
    ReadinessStatus,
    SourceRole,
)
from claw_trade.data_gateway.packs.materializer import materialize_domain_pack_result
from claw_trade.data_gateway.openviking.models import FinalReportClaim, OpenVikingRelation
from claw_trade.data_gateway.store.mongo import CRYPTO_LENS_ANALYSIS_EVIDENCE, OPENBB_PROVIDER_ATTEMPTS
from claw_trade.guards.export_claims import parse_export_claim_mapping
from claw_trade.workflow.models import ExportResult, Stage, WorkflowState

_PM_WORKER_ID = "portfolio_manager"
_FINAL_WORKER_ID = "report_polisher"
_FRONTLINE_PACK_BY_WORKER: dict[str, PackDomain] = {
    "market_analyst": PackDomain.MARKET,
    "fundamental_analyst": PackDomain.FUNDAMENTAL,
    "news_analyst": PackDomain.NEWS,
    "social_analyst": PackDomain.SOCIAL,
    "policy_analyst": PackDomain.POLICY,
    "hot_money_tracker": PackDomain.HOT_MONEY,
    "lockup_watcher": PackDomain.LOCKUP,
}


@dataclass(frozen=True)
class LineageWriteResult:
    ok: bool
    relation_count: int
    category: str | None
    reason: str | None
    paths: tuple[Path, ...]

    @classmethod
    def passed(cls, *, relation_count: int, paths: tuple[Path, ...]) -> LineageWriteResult:
        return cls(ok=True, relation_count=relation_count, category=None, reason=None, paths=paths)

    @classmethod
    def failed(cls, reason: str, *, paths: tuple[Path, ...]) -> LineageWriteResult:
        return cls(
            ok=False,
            relation_count=0,
            category="openviking_lineage",
            reason=reason,
            paths=paths,
        )


class OpenBBMongoLineageWriter:
    def __init__(
        self,
        *,
        openviking: OpenVikingClient,
        database: Any,
        now_text: Any | None = None,
    ) -> None:
        from claw_trade.data_gateway.openviking.plane import OpenVikingMaterialPlane

        self._plane = OpenVikingMaterialPlane(openviking)
        self._database = database
        self._now_text = now_text or _utc_now_iso_text

    def link_after_export(
        self,
        *,
        state: WorkflowState,
        manifest: ApprovedManifest,
        export_result: ExportResult,
    ) -> LineageWriteResult:
        if export_result.status != "passed" or export_result.final_report_path is None:
            return LineageWriteResult.passed(relation_count=0, paths=())

        materials = manifest.all_for_run(state.run_id)
        pm_material = _latest_material(materials, worker_id=_PM_WORKER_ID)
        final_material = _latest_material(materials, worker_id=_FINAL_WORKER_ID)
        if pm_material is None:
            return LineageWriteResult.failed(
                "缺少 portfolio_manager approved material，无法建立 final report -> PM 链路",
                paths=(state.run_dir / "openviking" / "approved-manifest.json",),
            )
        if final_material is None:
            return LineageWriteResult.failed(
                "缺少 report_polisher approved material，无法建立 final report 链路",
                paths=(state.run_dir / "openviking" / "approved-manifest.json",),
            )

        upstream_materials = tuple(
            material
            for material in materials
            if material.worker_id not in {_PM_WORKER_ID, _FINAL_WORKER_ID}
        )
        attempts = self._load_attempts(state.run_id)
        analysis_refs = self._load_crypto_lens_analysis_refs(state.run_id)
        pack_result = self._build_pack_results(
            state=state,
            upstream_materials=upstream_materials,
            attempts=attempts,
            analysis_refs_by_call=analysis_refs,
        )
        if not pack_result.ok:
            return LineageWriteResult.failed(
                pack_result.reason or "OpenBB pack provider refs 缺失",
                paths=(state.run_dir / "openviking" / "approved-manifest.json",),
            )

        claims_result = _load_final_claims(state=state, pm_material=pm_material)
        if isinstance(claims_result, str):
            return LineageWriteResult.failed(
                claims_result,
                paths=(state.run_dir / "reports" / "export-claims.json",),
            )

        try:
            relations = self._plane.link_final_report_chain(
                run_id=state.run_id,
                final_report_uri=final_material.l1_uri,
                final_claims=claims_result,
                pm_material=pm_material,
                upstream_materials=upstream_materials,
                pack_results=pack_result.pack_results,
            )
        except Exception as exc:  # noqa: BLE001
            return LineageWriteResult.failed(
                f"OpenViking relation 写入失败: {exc}",
                paths=(state.run_dir / "openviking" / "approved-manifest.json",),
            )

        audit_path = state.run_dir / "openviking" / "lineage-relations.json"
        try:
            _write_lineage_audit(
                path=audit_path,
                run_id=state.run_id,
                relations=relations,
                attempt_ids=tuple(attempt.attempt_id for attempt in attempts),
                pack_results=pack_result.pack_results,
            )
        except OSError as exc:
            return LineageWriteResult.failed(
                f"lineage audit 写入失败: {exc}",
                paths=(audit_path,),
            )
        return LineageWriteResult.passed(relation_count=len(relations), paths=(audit_path,))

    def _load_attempts(self, run_id: str) -> tuple[ProviderAttempt, ...]:
        rows = self._database[OPENBB_PROVIDER_ATTEMPTS].find({"run_id": run_id})
        return tuple(_attempt_from_doc(row) for row in rows)

    def _load_crypto_lens_analysis_refs(self, run_id: str) -> dict[str, tuple[str, ...]]:
        rows = self._database[CRYPTO_LENS_ANALYSIS_EVIDENCE].find({"run_id": run_id})
        refs_by_call: dict[str, list[str]] = {}
        for row in rows:
            call_id = _optional_str(row.get("call_id"))
            if not call_id:
                continue
            document_id = _optional_str(row.get("_id"))
            if document_id is None:
                continue
            refs_by_call.setdefault(call_id, []).append(f"mongo://{CRYPTO_LENS_ANALYSIS_EVIDENCE}/{document_id}")
        return {call_id: tuple(refs) for call_id, refs in refs_by_call.items()}

    def _build_pack_results(
        self,
        *,
        state: WorkflowState,
        upstream_materials: tuple[ApprovedMaterial, ...],
        attempts: tuple[ProviderAttempt, ...],
        analysis_refs_by_call: dict[str, tuple[str, ...]],
    ) -> "_PackResultsResult":
        attempts_by_call_pack: dict[tuple[str, str], list[ProviderAttempt]] = {}
        for attempt in attempts:
            attempts_by_call_pack.setdefault((attempt.call_id, attempt.pack), []).append(attempt)

        pack_results: list[DomainPackResult] = []
        evidence_failed: list[str] = []
        missing: list[str] = []
        for material in upstream_materials:
            domain = _FRONTLINE_PACK_BY_WORKER.get(material.worker_id)
            if domain is None:
                continue
            material_attempts = _attempts_for_material_call(
                attempts_by_call_pack=attempts_by_call_pack,
                material_call_id=material.call_id,
                pack=domain.value,
            )
            if not material_attempts:
                missing.append(f"{material.worker_id}:{material.call_id}:{domain.value}")
                continue
            analysis_refs = _analysis_refs_for_material_call(
                analysis_refs_by_call=analysis_refs_by_call,
                material_call_id=material.call_id,
            )
            pack_result = _pack_result_from_attempts(
                state=state,
                material=material,
                domain=domain,
                attempts=material_attempts,
                analysis_evidence_refs=analysis_refs,
                now_text=self._now_text(),
            )
            materialized_pack = materialize_domain_pack_result(pack_result)
            if _has_evidence_write_failure(materialized_pack.data_gaps, material_attempts):
                evidence_failed.append(
                    f"{material.worker_id}:{material.call_id}:{domain.value}:{materialized_pack.source_summary}"
                )
            pack_results.append(pack_result)

        if missing:
            return _PackResultsResult.failed(
                "缺少 OpenBB provider attempts，无法从 worker L1 追到 provider refs: " + ", ".join(missing)
            )
        if evidence_failed:
            return _PackResultsResult.failed(
                "provider evidence 写入失败，OpenViking 不能写 available lineage: " + " | ".join(evidence_failed)
            )
        if not pack_results:
            return _PackResultsResult.failed("没有可建立 lineage 的 OpenBB pack result")
        return _PackResultsResult.passed(tuple(pack_results))


def _attempts_for_material_call(
    *,
    attempts_by_call_pack: dict[tuple[str, str], list[ProviderAttempt]],
    material_call_id: str,
    pack: str,
) -> tuple[ProviderAttempt, ...]:
    attempts: list[ProviderAttempt] = []
    attempts.extend(attempts_by_call_pack.get((material_call_id, pack), ()))
    tool_call_prefix = f"{material_call_id}__tool-"
    for (call_id, pack_name), rows in attempts_by_call_pack.items():
        if pack_name == pack and call_id.startswith(tool_call_prefix):
            attempts.extend(rows)
    return tuple(attempts)


def _analysis_refs_for_material_call(
    *,
    analysis_refs_by_call: dict[str, tuple[str, ...]],
    material_call_id: str,
) -> tuple[str, ...]:
    refs: list[str] = []
    refs.extend(analysis_refs_by_call.get(material_call_id, ()))
    tool_call_prefix = f"{material_call_id}__tool-"
    for call_id, rows in analysis_refs_by_call.items():
        if call_id.startswith(tool_call_prefix):
            refs.extend(rows)
    return tuple(dict.fromkeys(refs))


def _has_evidence_write_failure(data_gaps: tuple[Any, ...], attempts: tuple[ProviderAttempt, ...]) -> bool:
    if any(attempt.status == ProviderStatus.EVIDENCE_WRITE_FAILED for attempt in attempts):
        return True
    return any(getattr(gap, "reason", None) == DataGapReason.EVIDENCE_WRITE_FAILED for gap in data_gaps)


@dataclass(frozen=True)
class _PackResultsResult:
    ok: bool
    pack_results: tuple[DomainPackResult, ...]
    reason: str | None

    @classmethod
    def passed(cls, pack_results: tuple[DomainPackResult, ...]) -> "_PackResultsResult":
        return cls(ok=True, pack_results=pack_results, reason=None)

    @classmethod
    def failed(cls, reason: str) -> "_PackResultsResult":
        return cls(ok=False, pack_results=(), reason=reason)


def _pack_result_from_attempts(
    *,
    state: WorkflowState,
    material: ApprovedMaterial,
    domain: PackDomain,
    attempts: tuple[ProviderAttempt, ...],
    analysis_evidence_refs: tuple[str, ...],
    now_text: str,
) -> DomainPackResult:
    request = PackRequest(
        run_id=state.run_id,
        call_id=material.call_id,
        worker_id=material.worker_id,
        market=Market(state.request.market),
        domain=domain,
        ticker=state.request.ticker,
        company_name=state.request.company_name,
        start_date=state.request.start_date,
        end_date=state.request.end_date,
        current_date=state.request.current_date,
        currency=state.request.currency,
        profile=state.request.profile,
        freshness_policy=FreshnessPolicy(max_age_seconds=300),
    )
    raw_refs = tuple(attempt.raw_ref for attempt in attempts if attempt.raw_ref)
    normalized_refs = tuple(attempt.normalized_ref for attempt in attempts if attempt.normalized_ref)
    chart_assets = _chart_assets_for_material(state=state, material=material)
    readiness_status = (
        ReadinessStatus.READY
        if any(attempt.status in {ProviderStatus.REMOTE_SUCCESS, ProviderStatus.SHARED_RESULT} for attempt in attempts)
        else ReadinessStatus.PARTIAL
    )
    readiness = Readiness(
        status=readiness_status,
        coverage={domain.value: readiness_status.value},
        required_domains=(domain.value,),
        missing_domains=(),
        blocking_gap_ids=(),
        non_blocking_gap_ids=(),
        root_cause=None if readiness_status == ReadinessStatus.READY else "no successful provider attempt",
    )
    payload_hash = _hash_json(
        {
            "run_id": state.run_id,
            "call_id": material.call_id,
            "domain": domain.value,
            "attempt_ids": [attempt.attempt_id for attempt in attempts],
            "raw_refs": raw_refs,
            "normalized_refs": normalized_refs,
            "analysis_evidence_refs": analysis_evidence_refs,
        }
    )
    audit = PackAuditPayload(
        request=request,
        openbb_runtime_marker="openbb-runtime-via-mongo-lineage",
        openbb_extension_version="claw.v1",
        run_provider_plan_id=state.run_id,
        call_specs=(),
        attempts=attempts,
        cache_receipts=(),
        data_gaps=(),
        conflicts=(),
        readiness=readiness,
        chart_assets=chart_assets,
        raw_refs=raw_refs,
        normalized_refs=normalized_refs,
        normalized_bundle_ref=None,
        payload_hash=payload_hash,
        generated_at=now_text,
        analysis_evidence_refs=analysis_evidence_refs,
    )
    return DomainPackResult(
        request=request,
        reader_brief_md="OpenBB pack lineage reconstructed from persisted Mongo provider evidence.",
        compact_facts={},
        attempts=attempts,
        cache_receipts=(),
        data_gaps=(),
        conflicts=(),
        readiness=readiness,
        chart_assets=chart_assets,
        raw_refs=raw_refs,
        normalized_refs=normalized_refs,
        normalized_bundle_ref=None,
        audit_ref=f"audit://{state.run_id}/{material.call_id}/{domain.value}",
        audit_payload_hash=payload_hash,
        audit_payload=audit,
    )


def _chart_assets_for_material(*, state: WorkflowState, material: ApprovedMaterial) -> tuple[ChartAsset, ...]:
    if material.worker_id != "market_analyst":
        return ()
    assets_dir = state.run_dir / "reports" / "assets"
    if not assets_dir.exists():
        return ()
    charts: list[ChartAsset] = []
    for index, path in enumerate(sorted(assets_dir.glob("market-*")), start=1):
        if not path.is_file() or path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            continue
        charts.append(
            ChartAsset(
                chart_id=f"market-chart-{index}",
                title=path.stem,
                kind="report_chart",
                image_ref=path.resolve().as_uri(),
                data_ref=None,
                status=ReadinessStatus.READY,
                root_cause=None,
            )
        )
    return tuple(charts)


def _load_final_claims(*, state: WorkflowState, pm_material: ApprovedMaterial) -> tuple[FinalReportClaim, ...] | str:
    mapping_path = state.run_dir / "reports" / "export-claims.json"
    result = parse_export_claim_mapping(mapping_path)
    if not result.ok or result.mapping is None:
        return result.reason or "export claim mapping 读取失败"
    claims: list[FinalReportClaim] = []
    for claim in result.mapping.claims:
        claims.append(
            FinalReportClaim(
                claim_id=claim.export_claim_id,
                text=claim.text,
                section=claim.kind,
                pm_material_id=pm_material.material_id,
                worker_material_ids=claim.source_material_ids,
            )
        )
    return tuple(claims)


def _latest_material(materials: tuple[ApprovedMaterial, ...], *, worker_id: str) -> ApprovedMaterial | None:
    selected = [material for material in materials if material.worker_id == worker_id]
    if not selected:
        return None
    return sorted(selected, key=lambda item: (item.turn_index, item.round_index, item.role_turn_index, item.call_id))[-1]


def _attempt_from_doc(doc: Mapping[str, Any]) -> ProviderAttempt:
    return ProviderAttempt(
        attempt_id=str(doc.get("_id") or doc["attempt_id"]),
        run_id=str(doc["run_id"]),
        call_id=str(doc["call_id"]),
        worker_id=str(doc["worker_id"]),
        pack=str(doc["pack"]),
        provider=str(doc["provider"]),
        adapter_id=str(doc["adapter_id"]),
        adapter_kind=str(doc["adapter_kind"]),
        provider_kind=ProviderKind(str(doc["provider_kind"])),
        provider_config_version=str(doc["provider_config_version"]),
        endpoint=str(doc["endpoint"]),
        source_role=SourceRole(str(doc["source_role"])),
        started_at=str(doc["started_at"]),
        finished_at=str(doc["finished_at"]),
        status=ProviderStatus(str(doc["status"])),
        required=bool(doc["required"]),
        attempt_required=bool(doc["attempt_required"]),
        coverage_group=_optional_str(doc.get("coverage_group")),
        coverage_quorum=_optional_int(doc.get("coverage_quorum")),
        priority_source=PrioritySource(str(doc["priority_source"])),
        user_preferred=bool(doc["user_preferred"]),
        from_cache=bool(doc["from_cache"]),
        cache_status=ProviderStatus(str(doc["cache_status"])) if doc.get("cache_status") else None,
        single_flight_role=str(doc["single_flight_role"]),  # type: ignore[arg-type]
        shared_from_attempt_id=_optional_str(doc.get("shared_from_attempt_id")),
        latency_ms=int(doc["latency_ms"]),
        row_count=_optional_int(doc.get("row_count")),
        raw_ref=_optional_str(doc.get("raw_ref")),
        normalized_ref=_optional_str(doc.get("normalized_ref")),
        error_code=_optional_str(doc.get("error_code")),
        error_message=_optional_str(doc.get("error_message")),
        schema_id=str(doc["schema_id"]),
        license_note=str(doc["license_note"]),
    )


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    return int(value)


def _write_lineage_audit(
    *,
    path: Path,
    run_id: str,
    relations: tuple[OpenVikingRelation, ...],
    attempt_ids: tuple[str, ...],
    pack_results: tuple[DomainPackResult, ...],
) -> None:
    payload = {
        "source": "openviking_lineage_writer",
        "run_id": run_id,
        "relation_count": len(relations),
        "attempt_ids": list(attempt_ids),
        "pack_statuses": _pack_status_payloads(pack_results),
        "relations": [_jsonable(asdict(item)) for item in relations],
        "written_at": _utc_now_iso_text(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _pack_status_payloads(pack_results: tuple[DomainPackResult, ...]) -> list[dict[str, object]]:
    payloads: list[dict[str, object]] = []
    for pack_result in pack_results:
        materialized = materialize_domain_pack_result(pack_result)
        payloads.append(
            {
                "worker_id": pack_result.request.worker_id,
                "call_id": pack_result.request.call_id,
                "domain": pack_result.request.domain.value,
                "approval_status": materialized.approval_status.value,
                "source_summary": materialized.source_summary,
                "attempt_statuses": [
                    {
                        "attempt_id": attempt.attempt_id,
                        "provider": attempt.provider,
                        "status": attempt.status.value,
                        "raw_ref": attempt.raw_ref,
                        "normalized_ref": attempt.normalized_ref,
                        "error_code": attempt.error_code,
                        "error_message": attempt.error_message,
                    }
                    for attempt in pack_result.attempts
                ],
                "data_gaps": [_jsonable(asdict(gap)) for gap in materialized.data_gaps],
            }
        )
    return payloads


def _hash_json(payload: Mapping[str, object]) -> str:
    raw = json.dumps(_jsonable(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _jsonable(value: object) -> object:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value


def _utc_now_iso_text() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
