from __future__ import annotations

from dataclasses import asdict
from typing import Any, Mapping

from pymongo.errors import PyMongoError

from claw_trade.data_gateway.errors import DataGatewayError, DataGatewayErrorCode
from claw_trade.data_gateway.models import (
    DeclarativeProviderManifest,
    Market,
    PackDomain,
    PrioritySource,
    ProviderAdmissionStatus,
    SourceRole,
)

from .mongo import OPENBB_PROVIDER_MANIFESTS, utc_now_iso


class MongoProviderManifestStore:
    def __init__(self, collection: Any) -> None:
        self.collection = collection

    @property
    def collection_name(self) -> str:
        return OPENBB_PROVIDER_MANIFESTS

    def upsert(self, manifest: DeclarativeProviderManifest, *, updated_by: str) -> str:
        doc = asdict(manifest)
        doc["_id"] = manifest.adapter_id
        doc["markets"] = [market.value for market in manifest.markets]
        doc["domains"] = [domain.value for domain in manifest.domains]
        doc["market_domain_pairs"] = _market_domain_pairs(doc["markets"], doc["domains"])
        doc["source_role"] = manifest.source_role.value
        doc["admission_status"] = manifest.admission_status.value
        doc["priority_source"] = manifest.priority_source.value
        doc["updated_by"] = updated_by
        doc["updated_at"] = utc_now_iso()
        try:
            self.collection.replace_one({"_id": manifest.adapter_id}, doc, upsert=True)
        except PyMongoError as exc:
            raise DataGatewayError(
                DataGatewayErrorCode.EVIDENCE_WRITE_FAILED,
                f"provider manifest write failed: {exc}",
            ) from exc
        return manifest.adapter_id

    def enabled_candidates(
        self,
        *,
        market: Market | None = None,
        domain: PackDomain | None = None,
        endpoint: str | None = None,
    ) -> tuple[DeclarativeProviderManifest, ...]:
        query: dict[str, Any] = {
            "admission_status": ProviderAdmissionStatus.ENABLED_CANDIDATE.value,
            "enabled": True,
        }
        if market is not None and domain is not None:
            query["market_domain_pairs"] = f"{market.value}:{domain.value}"
        if endpoint is not None:
            query["endpoints"] = endpoint
        try:
            docs = tuple(self.collection.find(query))
        except PyMongoError as exc:
            raise DataGatewayError(
                DataGatewayErrorCode.EVIDENCE_READ_FAILED,
                f"provider manifest read failed: {exc}",
            ) from exc
        manifests: list[DeclarativeProviderManifest] = []
        for doc in docs:
            manifests.append(_manifest_from_doc(doc))
        manifests.sort(key=lambda item: (item.priority, item.adapter_id, item.config_version))
        return tuple(manifests)


def _market_domain_pairs(markets: list[str], domains: list[str]) -> list[str]:
    return [f"{market}:{domain}" for market in markets for domain in domains]


def _manifest_from_doc(doc: Mapping[str, Any]) -> DeclarativeProviderManifest:
    doc_id = str(doc.get("_id") or doc.get("adapter_id") or "unknown")
    try:
        markets = tuple(Market(item) for item in doc.get("markets", ()))
        domains = tuple(PackDomain(item) for item in doc.get("domains", ()))
        endpoints = tuple(str(item) for item in doc.get("endpoints", ()))
        if not markets or not domains or not endpoints:
            raise DataGatewayError(
                DataGatewayErrorCode.EVIDENCE_READ_FAILED,
                (
                    "provider manifest decode failed: "
                    f"_id={doc_id}, missing required markets/domains/endpoints"
                ),
            )
        return DeclarativeProviderManifest(
            provider_id=str(doc["provider_id"]),
            adapter_id=str(doc["adapter_id"]),
            display_name=str(doc["display_name"]),
            version=str(doc["version"]),
            config_version=str(doc["config_version"]),
            markets=markets,
            domains=domains,
            endpoints=endpoints,
            source_role=SourceRole(str(doc["source_role"])),
            expected_schema_id=str(doc["expected_schema_id"]),
            base_url=str(doc["base_url"]),
            request_template=dict(doc.get("request_template", {})),
            response_mapping=dict(doc.get("response_mapping", {})),
            credential_requirements=tuple(str(item) for item in doc.get("credential_requirements", ())),
            rate_limit_policy_id=str(doc["rate_limit_policy_id"]),
            cache_ttl_seconds=int(doc.get("cache_ttl_seconds", 0)),
            license_policy_id=str(doc["license_policy_id"]),
            raw_export_policy=str(doc.get("raw_export_policy", "metadata_only")),
            healthcheck=dict(doc.get("healthcheck", {})),
            enabled=bool(doc.get("enabled", False)),
            admission_status=ProviderAdmissionStatus(str(doc.get("admission_status", ProviderAdmissionStatus.DRAFT.value))),
            priority=int(doc.get("priority", 0)),
            priority_source=PrioritySource(str(doc.get("priority_source", PrioritySource.SYSTEM_DEFAULT.value))),
            coverage_group=str(doc["coverage_group"]) if doc.get("coverage_group") is not None else None,
            coverage_quorum=int(doc["coverage_quorum"]) if doc.get("coverage_quorum") is not None else None,
        )
    except DataGatewayError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise DataGatewayError(
            DataGatewayErrorCode.EVIDENCE_READ_FAILED,
            f"provider manifest decode failed: _id={doc_id}: {type(exc).__name__}: {exc}",
        ) from exc
