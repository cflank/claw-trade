from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from claw_trade.artifacts.openviking_client import OpenVikingAccessError, OpenVikingClient
from claw_trade.artifacts.refs import ApprovedMaterial
from claw_trade.data_gateway.models import DomainPackResult
from claw_trade.data_gateway.store.mongo import OPENBB_PROVIDER_ATTEMPTS

from .models import (
    ContextIndexReceipt,
    EngineeringMemoryRecord,
    EvidenceBundleReceipt,
    FinalReportClaim,
    HealthStatus,
    OpenVikingFindResult,
    OpenVikingGlobResult,
    OpenVikingGrepResult,
    OpenVikingRelationsDump,
    OpenVikingRelation,
    OpenVikingRuntimeHealth,
    OpenVikingTree,
    RelationKind,
)


def _now_text() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _run_root_uri(run_id: str) -> str:
    return f"viking://resources/workflow/{run_id}/"


def _normalize_health(value: object, *, unavailable_default: bool = False) -> HealthStatus:
    if isinstance(value, str):
        raw = value.strip().lower()
        mapping = {
            "ok": "ok",
            "healthy": "ok",
            "degraded": "degraded",
            "warn": "degraded",
            "warning": "degraded",
            "blocked": "blocked",
            "error": "blocked",
            "failed": "blocked",
            "unavailable": "unavailable",
            "unknown": "unavailable",
        }
        if raw in mapping:
            return mapping[raw]  # type: ignore[return-value]
    if isinstance(value, bool):
        return "ok" if value else "blocked"
    return "unavailable" if unavailable_default else "blocked"


def _call_missing_text(name: str) -> str:
    return f"openviking wrapper unavailable: {name}"


def _stable_hash(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _uri_from_ref_or_hash(value: str, *, fallback_prefix: str) -> str:
    text = str(value).strip()
    if not text:
        return f"{fallback_prefix}/unknown"
    if text.startswith(("viking://", "mongo://", "object://", "file://", "http://", "https://")):
        return text
    if text.startswith("sha256:"):
        return f"{fallback_prefix}/{text.replace(':', '_')}"
    return text


def _extract_root_cause(raw: object) -> str | None:
    if isinstance(raw, Mapping):
        for key in ("root_cause", "reason", "error", "message"):
            value = raw.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


class OpenVikingMaterialPlane:
    def __init__(self, client: OpenVikingClient) -> None:
        self._client = client

    def link_provider_evidence(
        self,
        *,
        material: ApprovedMaterial,
        pack_result: DomainPackResult,
        push: bool = True,
    ) -> tuple[OpenVikingRelation, ...]:
        relations: list[OpenVikingRelation] = []
        run_id = material.run_id
        stage = material.stage.value
        worker_id = material.worker_id
        call_id = material.call_id
        created_at = _now_text()
        l1_uri = material.l1_uri
        l2_uri = material.l2_index_uri or f"{material.l1_uri.rsplit('/', 1)[0]}/evidence/index.json"
        audit_uri = _uri_from_ref_or_hash(pack_result.audit_ref, fallback_prefix=f"{l2_uri.rsplit('/', 1)[0]}/pack_audit")

        relations.append(
            OpenVikingRelation(
                from_uri=l1_uri,
                to_uri=l2_uri,
                kind="worker_l1_to_l2_evidence",
                run_id=run_id,
                stage=stage,
                worker_id=worker_id,
                call_id=call_id,
                created_at=created_at,
                evidence_hash=material.l2_index.index_sha256,
                note="worker report declares this L2 evidence index",
            )
        )
        relations.append(
            OpenVikingRelation(
                from_uri=l2_uri,
                to_uri=audit_uri,
                kind="l2_evidence_to_pack_audit",
                run_id=run_id,
                stage=stage,
                worker_id=worker_id,
                call_id=call_id,
                created_at=created_at,
                evidence_hash=pack_result.audit_payload_hash,
                note="L2 evidence index contains pack audit",
            )
        )
        for analysis_ref in pack_result.audit_payload.analysis_evidence_refs:
            relations.append(
                OpenVikingRelation(
                    from_uri=l1_uri,
                    to_uri=_uri_from_ref_or_hash(
                        analysis_ref,
                        fallback_prefix=f"{l2_uri.rsplit('/', 1)[0]}/analysis_evidence",
                    ),
                    kind="worker_l1_to_l2_evidence",
                    run_id=run_id,
                    stage=stage,
                    worker_id=worker_id,
                    call_id=call_id,
                    created_at=created_at,
                    evidence_hash=None,
                    note="worker L1 references CryptoLens analysis evidence as L2 evidence",
                )
            )

        attempt_raw_refs: set[str] = set()
        attempt_normalized_refs: set[str] = set()
        for attempt in pack_result.attempts:
            attempt_uri = f"mongo://{OPENBB_PROVIDER_ATTEMPTS}/{attempt.attempt_id}"
            relations.append(
                OpenVikingRelation(
                    from_uri=audit_uri,
                    to_uri=attempt_uri,
                    kind="pack_audit_to_provider_attempt",
                    run_id=run_id,
                    stage=stage,
                    worker_id=worker_id,
                    call_id=call_id,
                    created_at=created_at,
                    evidence_hash=_stable_hash(asdict(attempt)),
                    note=f"pack audit references provider attempt {attempt.attempt_id}",
                )
            )
            if attempt.raw_ref:
                attempt_raw_refs.add(attempt.raw_ref)
                relations.append(
                    OpenVikingRelation(
                        from_uri=attempt_uri,
                        to_uri=_uri_from_ref_or_hash(
                            attempt.raw_ref,
                            fallback_prefix=f"{l2_uri.rsplit('/', 1)[0]}/provider_raw_refs",
                        ),
                        kind="provider_attempt_to_raw_payload",
                        run_id=run_id,
                        stage=stage,
                        worker_id=worker_id,
                        call_id=call_id,
                        created_at=created_at,
                        evidence_hash=None,
                        note=f"provider attempt {attempt.attempt_id} references raw payload hash/ref",
                    )
                )
            if attempt.normalized_ref:
                attempt_normalized_refs.add(attempt.normalized_ref)
                relations.append(
                    OpenVikingRelation(
                        from_uri=attempt_uri,
                        to_uri=_uri_from_ref_or_hash(
                            attempt.normalized_ref,
                            fallback_prefix=f"{l2_uri.rsplit('/', 1)[0]}/normalized_refs",
                        ),
                        kind="provider_attempt_to_normalized_result",
                        run_id=run_id,
                        stage=stage,
                        worker_id=worker_id,
                        call_id=call_id,
                        created_at=created_at,
                        evidence_hash=None,
                        note=f"provider attempt {attempt.attempt_id} references normalized result hash/ref",
                    )
                )

        for raw_ref in pack_result.raw_refs:
            if raw_ref in attempt_raw_refs:
                continue
            relations.append(
                OpenVikingRelation(
                    from_uri=audit_uri,
                    to_uri=_uri_from_ref_or_hash(raw_ref, fallback_prefix=f"{l2_uri.rsplit('/', 1)[0]}/provider_raw_refs"),
                    kind="provider_attempt_to_raw_payload",
                    run_id=run_id,
                    stage=stage,
                    worker_id=worker_id,
                    call_id=call_id,
                    created_at=created_at,
                    evidence_hash=None,
                    note="provider attempt references raw payload hash/ref",
                )
            )

        for normalized_ref in pack_result.normalized_refs:
            if normalized_ref in attempt_normalized_refs:
                continue
            relations.append(
                OpenVikingRelation(
                    from_uri=audit_uri,
                    to_uri=_uri_from_ref_or_hash(
                        normalized_ref,
                        fallback_prefix=f"{l2_uri.rsplit('/', 1)[0]}/normalized_refs",
                    ),
                    kind="provider_attempt_to_normalized_result",
                    run_id=run_id,
                    stage=stage,
                    worker_id=worker_id,
                    call_id=call_id,
                    created_at=created_at,
                    evidence_hash=None,
                    note="provider attempt references normalized result hash/ref",
                )
            )

        for cache_receipt in pack_result.cache_receipts:
            cache_uri = f"{l2_uri.rsplit('/', 1)[0]}/cache_receipts/{cache_receipt.cache_key}.json"
            relations.append(
                OpenVikingRelation(
                    from_uri=audit_uri,
                    to_uri=cache_uri,
                    kind="provider_attempt_to_cache_receipt",
                    run_id=run_id,
                    stage=stage,
                    worker_id=worker_id,
                    call_id=call_id,
                    created_at=created_at,
                    evidence_hash=_stable_hash(asdict(cache_receipt)),
                    note=f"cache status: {cache_receipt.status.value}",
                )
            )

        for chart in pack_result.chart_assets:
            if chart.image_ref:
                relations.append(
                    OpenVikingRelation(
                        from_uri=l1_uri,
                        to_uri=chart.image_ref,
                        kind="worker_l1_to_chart_asset",
                        run_id=run_id,
                        stage=stage,
                        worker_id=worker_id,
                        call_id=call_id,
                        created_at=created_at,
                        evidence_hash=None,
                        note=f"report chart: {chart.title}",
                    )
                )

        for gap in pack_result.data_gaps:
            gap_uri = f"{l2_uri.rsplit('/', 1)[0]}/data_gaps/{gap.gap_id}.json"
            relations.append(
                OpenVikingRelation(
                    from_uri=l1_uri,
                    to_uri=gap_uri,
                    kind="worker_l1_to_data_gap",
                    run_id=run_id,
                    stage=stage,
                    worker_id=worker_id,
                    call_id=call_id,
                    created_at=created_at,
                    evidence_hash=_stable_hash(asdict(gap)),
                    note=f"data gap: {gap.field_path}",
                )
            )

        if push:
            self._push_relations(relations)
        return tuple(relations)

    def link_final_report_chain(
        self,
        *,
        run_id: str,
        final_report_uri: str,
        final_claims: tuple[FinalReportClaim, ...],
        pm_material: ApprovedMaterial,
        upstream_materials: tuple[ApprovedMaterial, ...],
        pack_results: tuple[DomainPackResult, ...],
    ) -> tuple[OpenVikingRelation, ...]:
        relations: list[OpenVikingRelation] = []
        created_at = _now_text()
        pack_by_worker: dict[str, list[DomainPackResult]] = {}
        for item in pack_results:
            pack_by_worker.setdefault(item.request.worker_id, []).append(item)

        if not final_claims:
            relations.append(
                OpenVikingRelation(
                    from_uri=final_report_uri,
                    to_uri=pm_material.l1_uri,
                    kind="final_report_claim_to_pm_l1",
                    run_id=run_id,
                    stage="final_report",
                    worker_id="report_polisher",
                    call_id=None,
                    created_at=created_at,
                    evidence_hash=pm_material.l1_sha256,
                    note="final report is anchored to PM L1; structured final claim map is empty",
                )
            )

        for claim in final_claims:
            claim_uri = (
                f"{final_report_uri.rsplit('/', 1)[0]}/claims/"
                f"{claim.claim_id}.json"
            )
            relations.append(
                OpenVikingRelation(
                    from_uri=claim_uri,
                    to_uri=pm_material.l1_uri,
                    kind="final_report_claim_to_pm_l1",
                    run_id=run_id,
                    stage="final_report",
                    worker_id="report_polisher",
                    call_id=None,
                    created_at=created_at,
                    evidence_hash=_stable_hash(asdict(claim)),
                    note=f"final report claim supported by PM L1: {claim.claim_id}",
                )
            )

        for material in upstream_materials:
            relations.append(
                OpenVikingRelation(
                    from_uri=pm_material.l1_uri,
                    to_uri=material.l1_uri,
                    kind="pm_l1_to_worker_l1",
                    run_id=run_id,
                    stage=pm_material.stage.value,
                    worker_id=pm_material.worker_id,
                    call_id=pm_material.call_id,
                    created_at=created_at,
                    evidence_hash=material.l1_sha256,
                    note=f"PM material depends on approved worker L1: {material.worker_id}",
                )
            )
            for pack_result in pack_by_worker.get(material.worker_id, []):
                relations.extend(self.link_provider_evidence(material=material, pack_result=pack_result, push=False))

        if relations:
            relation_manifest_uri = f"{final_report_uri.rsplit('/', 1)[0]}/relations/manifest.json"
            relations.append(
                OpenVikingRelation(
                    from_uri=final_report_uri,
                    to_uri=relation_manifest_uri,
                    kind="final_report_claim_to_pm_l1",
                    run_id=run_id,
                    stage="final_report",
                    worker_id="report_polisher",
                    call_id=None,
                    created_at=created_at,
                    evidence_hash=_stable_hash(tuple(asdict(item) for item in relations)),
                    note="final report relation manifest",
                )
            )
            self._push_relations(relations)
        return tuple(relations)

    def export_run_pack(self, run_id: str, output_dir: str) -> EvidenceBundleReceipt:
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)
        bundle_path = output / f"{run_id}.ovpack"
        try:
            raw = self._client.export_run_pack(run_id=run_id, output_dir=output_dir)
        except OpenVikingAccessError as exc:
            return EvidenceBundleReceipt(
                run_id=run_id,
                bundle_uri=f"{_run_root_uri(run_id)}evidence/bundles/{bundle_path.name}",
                bundle_path=str(bundle_path),
                sha256="",
                size_bytes=0,
                portability_status="blocked",
                raw_payload_policy="blocked",
                external_store_refs=(),
                exported_at=_now_text(),
                import_status="blocked",
                root_cause=f"export blocked: {exc.category}:{exc}",
            )

        record = raw if isinstance(raw, Mapping) else {}
        return EvidenceBundleReceipt(
            run_id=run_id,
            bundle_uri=str(record.get("bundle_uri") or f"{_run_root_uri(run_id)}evidence/bundles/{bundle_path.name}"),
            bundle_path=str(record.get("bundle_path") or bundle_path),
            sha256=str(record.get("sha256") or ""),
            size_bytes=int(record.get("size_bytes") or 0),
            portability_status=str(record.get("portability_status") or "metadata_verified"),  # type: ignore[arg-type]
            raw_payload_policy=str(record.get("raw_payload_policy") or "external_store_required"),  # type: ignore[arg-type]
            external_store_refs=tuple(str(item) for item in (record.get("external_store_refs") or ())),
            exported_at=str(record.get("exported_at") or _now_text()),
            import_status=None,
            root_cause=_extract_root_cause(record),
        )

    def import_run_pack(
        self,
        *,
        bundle_path: str,
        target_run_id: str,
        verify_hashes: bool = True,
    ) -> EvidenceBundleReceipt:
        try:
            raw = self._client.import_run_pack(
                bundle_path=bundle_path,
                target_run_id=target_run_id,
                verify_hashes=verify_hashes,
            )
        except OpenVikingAccessError as exc:
            return EvidenceBundleReceipt(
                run_id=target_run_id,
                bundle_uri=f"viking://resources/workflow/imported/{target_run_id}/",
                bundle_path=bundle_path,
                sha256="",
                size_bytes=0,
                portability_status="blocked",
                raw_payload_policy="blocked",
                external_store_refs=(),
                exported_at=_now_text(),
                imported_run_id=target_run_id,
                import_status="blocked",
                root_cause=f"import blocked: {exc.category}:{exc}",
            )

        record = raw if isinstance(raw, Mapping) else {}
        return EvidenceBundleReceipt(
            run_id=str(record.get("run_id") or target_run_id),
            bundle_uri=str(record.get("bundle_uri") or f"viking://resources/workflow/imported/{target_run_id}/"),
            bundle_path=str(record.get("bundle_path") or bundle_path),
            sha256=str(record.get("sha256") or ""),
            size_bytes=int(record.get("size_bytes") or 0),
            portability_status=str(record.get("portability_status") or "metadata_verified"),  # type: ignore[arg-type]
            raw_payload_policy=str(record.get("raw_payload_policy") or "external_store_required"),  # type: ignore[arg-type]
            external_store_refs=tuple(str(item) for item in (record.get("external_store_refs") or ())),
            exported_at=str(record.get("exported_at") or _now_text()),
            imported_run_id=target_run_id,
            import_status=_normalize_health(record.get("import_status"), unavailable_default=True),
            root_cause=_extract_root_cause(record),
        )

    def tree_run(self, run_id: str) -> OpenVikingTree:
        root_uri = _run_root_uri(run_id)
        try:
            raw = self._client.tree_run(run_id=run_id)
        except OpenVikingAccessError as exc:
            return OpenVikingTree(
                root_uri=root_uri,
                node_count=0,
                nodes=(),
                status="blocked",
                root_cause=f"tree blocked: {exc.category}:{exc}",
            )
        nodes = tuple(self._normalize_node(node) for node in self._extract_list(raw, "nodes"))
        return OpenVikingTree(root_uri=root_uri, node_count=len(nodes), nodes=nodes)

    def grep_run(self, run_id: str, pattern: str) -> OpenVikingGrepResult:
        root_uri = _run_root_uri(run_id)
        try:
            raw = self._client.grep_run(run_id=run_id, pattern=pattern)
        except OpenVikingAccessError as exc:
            return OpenVikingGrepResult(
                root_uri=root_uri,
                pattern=pattern,
                matches=(),
                status="blocked",
                root_cause=f"grep blocked: {exc.category}:{exc}",
            )
        matches = tuple(self._normalize_node(item) for item in self._extract_list(raw, "matches"))
        return OpenVikingGrepResult(root_uri=root_uri, pattern=pattern, matches=matches)

    def glob_run(self, run_id: str, pattern: str) -> OpenVikingGlobResult:
        root_uri = _run_root_uri(run_id)
        try:
            raw = self._client.glob_run(run_id=run_id, pattern=pattern)
        except OpenVikingAccessError as exc:
            return OpenVikingGlobResult(
                root_uri=root_uri,
                pattern=pattern,
                matches=(),
                status="blocked",
                root_cause=f"glob blocked: {exc.category}:{exc}",
            )
        matches = tuple(str(item) for item in self._extract_list(raw, "matches"))
        return OpenVikingGlobResult(root_uri=root_uri, pattern=pattern, matches=matches)

    def dump_relations(self, uri: str) -> OpenVikingRelationsDump:
        try:
            raw = self._client.relations(uri=uri)
        except OpenVikingAccessError as exc:
            return OpenVikingRelationsDump(
                uri=uri,
                relations=(),
                status="blocked",
                root_cause=f"relations blocked: {exc.category}:{exc}",
            )
        relations = tuple(self._normalize_node(item) for item in self._extract_list(raw, "relations"))
        if relations:
            return OpenVikingRelationsDump(uri=uri, relations=relations, status="ok", root_cause=_extract_root_cause(raw))
        return OpenVikingRelationsDump(
            uri=uri,
            relations=(),
            status=_normalize_health(self._extract_value(raw, "status"), unavailable_default=True),
            root_cause=_extract_root_cause(raw),
        )

    def find_approved_materials(self, run_id: str, query: str) -> OpenVikingFindResult:
        try:
            raw = self._client.find_approved_materials(run_id=run_id, query=query)
        except OpenVikingAccessError as exc:
            return OpenVikingFindResult(
                run_id=run_id,
                query=query,
                matches=(),
                usable_as_investment_fact=False,
                status="blocked",
                root_cause=f"find blocked: {exc.category}:{exc}",
            )
        matches = tuple(self._normalize_node(item) for item in self._extract_list(raw, "matches"))
        return OpenVikingFindResult(
            run_id=run_id,
            query=query,
            matches=matches,
            usable_as_investment_fact=False,
            status=_normalize_health(self._extract_value(raw, "status"), unavailable_default=True),
            root_cause=_extract_root_cause(raw),
        )

    def index_run_context(self, run_id: str) -> tuple[ContextIndexReceipt, ...]:
        root_uri = _run_root_uri(run_id)
        l0 = ContextIndexReceipt(
            uri=root_uri,
            index_level="L0",
            status="ok",
            vectorized=False,
            searchable_by_control_plane=True,
            visible_to_worker=False,
            reason="L0 URI tree index available via control-plane wrappers",
        )
        l1 = ContextIndexReceipt(
            uri=root_uri,
            index_level="L1",
            status="ok",
            vectorized=False,
            searchable_by_control_plane=True,
            visible_to_worker=False,
            reason="L1 approved material metadata index available via control-plane wrappers",
        )

        semantic_reason = "OpenViking semantic/vector queue status unavailable"
        semantic_status: HealthStatus = "unavailable"
        try:
            raw = self._client.semantic_index_status(run_id=run_id)
            semantic_status = _normalize_health(self._extract_value(raw, "status"), unavailable_default=True)
            semantic_reason = _extract_root_cause(raw) or semantic_reason
        except OpenVikingAccessError as exc:
            semantic_status = "unavailable"
            semantic_reason = f"{_call_missing_text('semantic_index_status')}: {exc.category}:{exc}"

        semantic = ContextIndexReceipt(
            uri=root_uri,
            index_level="semantic",
            status=semantic_status,
            vectorized=semantic_status == "ok",
            searchable_by_control_plane=semantic_status == "ok",
            visible_to_worker=False,
            reason=semantic_reason,
        )
        return (l0, l1, semantic)

    def write_engineering_memory(self, record: EngineeringMemoryRecord) -> str:
        safe = EngineeringMemoryRecord(
            run_id=record.run_id,
            event_id=record.event_id,
            category=record.category,
            text=record.text,
            created_at=record.created_at,
            visible_to_worker=False,
            usable_as_investment_fact=False,
        )
        try:
            raw = self._client.write_engineering_memory(asdict(safe))
        except OpenVikingAccessError as exc:
            raise RuntimeError(f"engineering memory write blocked: {exc.category}:{exc}") from exc
        if isinstance(raw, Mapping):
            uri = raw.get("uri")
            if isinstance(uri, str) and uri.strip():
                return uri
        return f"viking://memory/claw-trade/{safe.run_id}/{safe.event_id}.json"

    def read_engineering_memory(self, run_id: str, query: str) -> tuple[EngineeringMemoryRecord, ...]:
        try:
            raw = self._client.read_engineering_memory(run_id=run_id, query=query)
        except OpenVikingAccessError as exc:
            raise RuntimeError(f"engineering memory read blocked: {exc.category}:{exc}") from exc
        rows = self._extract_list(raw, "records")
        records: list[EngineeringMemoryRecord] = []
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            records.append(
                EngineeringMemoryRecord(
                    run_id=str(row.get("run_id") or run_id),
                    event_id=str(row.get("event_id") or ""),
                    category=str(row.get("category") or "runtime"),  # type: ignore[arg-type]
                    text=str(row.get("text") or ""),
                    created_at=str(row.get("created_at") or _now_text()),
                    visible_to_worker=False,
                    usable_as_investment_fact=False,
                )
            )
        return tuple(records)

    def runtime_health(self) -> OpenVikingRuntimeHealth:
        metrics_status, metrics_ref, metrics_reason = self._fetch_health_part(
            "runtime_metrics",
            unavailable_reason="metrics endpoint unavailable",
        )
        observer_status, observer_ref, observer_reason = self._fetch_health_part(
            "runtime_observer",
            unavailable_reason="observer endpoint unavailable",
        )
        lock_status, lock_ref, lock_reason = self._fetch_health_part(
            "runtime_locks",
            unavailable_reason="lock observer endpoint unavailable",
        )
        queue_status, queue_ref, queue_reason = self._fetch_health_part(
            "runtime_semantic_queue",
            unavailable_reason="semantic queue observer unavailable",
        )

        recovery_reason = "upstream public recovery API 未确认"
        try:
            recovery_raw = self._client.runtime_recovery()
            recovery_status = _normalize_health(self._extract_value(recovery_raw, "status"), unavailable_default=True)
            recovery_reason = _extract_root_cause(recovery_raw) or recovery_reason
            recovery_ref = self._extract_value(recovery_raw, "raw_ref")
        except OpenVikingAccessError:
            recovery_status = "unavailable"
            recovery_ref = None

        parts: tuple[HealthStatus, ...] = (
            metrics_status,
            observer_status,
            lock_status,
            queue_status,
            recovery_status,
        )
        if "blocked" in parts:
            overall: HealthStatus = "blocked"
        elif "degraded" in parts:
            overall = "degraded"
        elif "unavailable" in parts:
            overall = "unavailable"
        else:
            overall = "ok"

        reasons = [item for item in (metrics_reason, observer_reason, lock_reason, queue_reason, recovery_reason) if item]
        refs = tuple(
            str(item)
            for item in (metrics_ref, observer_ref, lock_ref, queue_ref, recovery_ref)
            if isinstance(item, str) and item.strip()
        )
        return OpenVikingRuntimeHealth(
            status=overall,
            metrics_status=metrics_status,
            observer_status=observer_status,
            lock_status=lock_status,
            recovery_status=recovery_status,
            queue_status=queue_status,
            checked_at=_now_text(),
            root_cause="; ".join(reasons) if reasons else None,
            raw_refs=refs,
        )

    def _push_relations(self, relations: list[OpenVikingRelation]) -> None:
        if not relations:
            return
        failures: list[str] = []
        for item in relations:
            try:
                payload = asdict(item)
                payload["from_uri"] = _openviking_relation_source_uri(item)
                payload["note"] = _openviking_relation_reason(item)
                self._client.link_relation(payload)
            except OpenVikingAccessError as exc:
                failures.append(f"{item.kind}:{exc.category}:{exc}")
        if failures:
            raise RuntimeError("openviking relation write failed: " + "; ".join(failures))

    def _fetch_health_part(
        self,
        method_name: str,
        *,
        unavailable_reason: str,
    ) -> tuple[HealthStatus, str | None, str | None]:
        try:
            raw = getattr(self._client, method_name)()
        except OpenVikingAccessError:
            return ("unavailable", None, unavailable_reason)
        status = _normalize_health(self._extract_value(raw, "status"), unavailable_default=True)
        return (status, self._extract_value(raw, "raw_ref"), _extract_root_cause(raw))

    @staticmethod
    def _extract_list(raw: object, key: str) -> list[Any]:
        if isinstance(raw, Mapping):
            value = raw.get(key)
            if isinstance(value, list):
                return value
        if isinstance(raw, list):
            return raw
        return []

    @staticmethod
    def _extract_value(raw: object, key: str) -> Any:
        if isinstance(raw, Mapping):
            return raw.get(key)
        return None

    @staticmethod
    def _normalize_node(node: object) -> Mapping[str, str]:
        if isinstance(node, Mapping):
            return {str(k): str(v) for k, v in node.items()}
        return {"value": str(node)}


def _openviking_relation_source_uri(item: OpenVikingRelation) -> str:
    if item.call_id:
        return f"{_run_root_uri(item.run_id)}{item.stage}/{item.worker_id}/{item.call_id}/"
    if item.from_uri.startswith("viking://resources/"):
        return _existing_resource_dir_for_relation(item.from_uri)
    return _run_root_uri(item.run_id)


def _existing_resource_dir_for_relation(uri: str) -> str:
    for marker in ("/claims/", "/relations/"):
        if marker in uri:
            return uri.split(marker, 1)[0].rstrip("/") + "/"
    return _parent_resource_dir(uri)


def _parent_resource_dir(uri: str) -> str:
    text = uri.strip()
    if text.endswith("/"):
        return text
    head, separator, _tail = text.rpartition("/")
    if not separator:
        return text
    return head.rstrip("/") + "/"


def _openviking_relation_reason(item: OpenVikingRelation) -> str:
    return json.dumps(
        {
            "kind": item.kind,
            "semantic_from_uri": item.from_uri,
            "semantic_to_uri": item.to_uri,
            "evidence_hash": item.evidence_hash,
            "note": item.note,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
