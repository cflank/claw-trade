from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from claw_trade.data_gateway.errors import DataGatewayError, DataGatewayErrorCode
from claw_trade.data_gateway.models import (
    CacheDecision,
    CacheReceipt,
    PackRequest,
    ProviderCallSpec,
    ProviderStatus,
)

from .mongo import OPENBB_CACHE_ENTRIES, mongo_ref, sha256_text, stable_hash, utc_now_iso


def build_cache_key(spec: ProviderCallSpec, request: PackRequest) -> tuple[str, str]:
    params_hash = stable_hash({k: spec.params[k] for k in sorted(spec.params.keys())})
    key_text = "|".join(
        (
            spec.provider,
            spec.endpoint,
            spec.market.value,
            spec.domain.value,
            request.ticker,
            request.start_date,
            request.end_date,
            spec.provider_config_version,
            params_hash,
        )
    )
    return sha256_text(key_text), params_hash


class MongoCacheStore:
    def __init__(self, collection: Any) -> None:
        self.collection = collection

    @property
    def collection_name(self) -> str:
        return OPENBB_CACHE_ENTRIES

    def get(self, spec: ProviderCallSpec, request: PackRequest) -> CacheDecision:
        cache_key, _params_hash = build_cache_key(spec, request)
        try:
            doc = self.collection.find_one({"_id": cache_key})
        except Exception as exc:  # noqa: BLE001
            receipt = CacheReceipt(
                cache_key=cache_key,
                provider=spec.provider,
                endpoint=spec.endpoint,
                status=ProviderStatus.CACHE_ERROR,
                hit=False,
                stale=False,
                cached_empty=False,
                created_at=None,
                expires_at=None,
                ttl_seconds=spec.cache_ttl_seconds,
                evidence_hash=None,
                raw_ref=None,
                normalized_ref=None,
            )
            return CacheDecision(
                status=ProviderStatus.CACHE_ERROR,
                receipt=receipt,
                usable_raw_ref=None,
                usable_normalized_ref=None,
                reason=str(exc),
            )

        if doc is None:
            receipt = CacheReceipt(
                cache_key=cache_key,
                provider=spec.provider,
                endpoint=spec.endpoint,
                status=ProviderStatus.CACHE_MISS,
                hit=False,
                stale=False,
                cached_empty=False,
                created_at=None,
                expires_at=None,
                ttl_seconds=spec.cache_ttl_seconds,
                evidence_hash=None,
                raw_ref=None,
                normalized_ref=None,
            )
            return CacheDecision(
                status=ProviderStatus.CACHE_MISS,
                receipt=receipt,
                usable_raw_ref=None,
                usable_normalized_ref=None,
            )

        created_at = doc.get("created_at")
        expires_at = doc.get("expires_at")
        entry_status = str(doc.get("status") or "").strip().lower()
        cached_empty = bool(doc.get("cached_empty"))
        now = datetime.now(tz=UTC)
        expiry = _parse_iso_or_none(expires_at)
        is_stale = bool(expiry is not None and expiry <= now)
        raw_ref = _str_or_none(doc.get("raw_ref"))
        normalized_ref = _str_or_none(doc.get("normalized_ref"))
        evidence_hash = _str_or_none(doc.get("evidence_hash"))

        if cached_empty or entry_status == ProviderStatus.CACHED_EMPTY.value:
            receipt = CacheReceipt(
                cache_key=cache_key,
                provider=spec.provider,
                endpoint=spec.endpoint,
                status=ProviderStatus.CACHED_EMPTY,
                hit=True,
                stale=is_stale,
                cached_empty=True,
                created_at=created_at,
                expires_at=expires_at,
                ttl_seconds=int(doc.get("ttl_seconds") or spec.cache_ttl_seconds),
                evidence_hash=evidence_hash,
                raw_ref=None,
                normalized_ref=None,
            )
            if is_stale:
                stale_receipt = CacheReceipt(
                    cache_key=cache_key,
                    provider=spec.provider,
                    endpoint=spec.endpoint,
                    status=ProviderStatus.CACHE_STALE,
                    hit=True,
                    stale=True,
                    cached_empty=True,
                    created_at=created_at,
                    expires_at=expires_at,
                    ttl_seconds=int(doc.get("ttl_seconds") or spec.cache_ttl_seconds),
                    evidence_hash=evidence_hash,
                    raw_ref=None,
                    normalized_ref=None,
                )
                return CacheDecision(
                    status=ProviderStatus.CACHE_STALE,
                    receipt=stale_receipt,
                    usable_raw_ref=None,
                    usable_normalized_ref=None,
                    reason="cached_empty entry expired",
                )
            return CacheDecision(
                status=ProviderStatus.CACHED_EMPTY,
                receipt=receipt,
                usable_raw_ref=None,
                usable_normalized_ref=None,
            )

        if entry_status != ProviderStatus.REMOTE_SUCCESS.value:
            receipt = CacheReceipt(
                cache_key=cache_key,
                provider=spec.provider,
                endpoint=spec.endpoint,
                status=ProviderStatus.CACHE_ERROR,
                hit=False,
                stale=False,
                cached_empty=False,
                created_at=created_at,
                expires_at=expires_at,
                ttl_seconds=int(doc.get("ttl_seconds") or spec.cache_ttl_seconds),
                evidence_hash=evidence_hash,
                raw_ref=raw_ref,
                normalized_ref=normalized_ref,
            )
            return CacheDecision(
                status=ProviderStatus.CACHE_ERROR,
                receipt=receipt,
                usable_raw_ref=None,
                usable_normalized_ref=None,
                reason=f"invalid cache entry status: {entry_status or '<empty>'}",
            )

        if is_stale:
            receipt = CacheReceipt(
                cache_key=cache_key,
                provider=spec.provider,
                endpoint=spec.endpoint,
                status=ProviderStatus.CACHE_STALE,
                hit=True,
                stale=True,
                cached_empty=False,
                created_at=created_at,
                expires_at=expires_at,
                ttl_seconds=int(doc.get("ttl_seconds") or spec.cache_ttl_seconds),
                evidence_hash=evidence_hash,
                raw_ref=raw_ref,
                normalized_ref=normalized_ref,
            )
            return CacheDecision(
                status=ProviderStatus.CACHE_STALE,
                receipt=receipt,
                usable_raw_ref=None,
                usable_normalized_ref=None,
            )

        if not raw_ref or not normalized_ref:
            receipt = CacheReceipt(
                cache_key=cache_key,
                provider=spec.provider,
                endpoint=spec.endpoint,
                status=ProviderStatus.CACHE_ERROR,
                hit=False,
                stale=False,
                cached_empty=False,
                created_at=created_at,
                expires_at=expires_at,
                ttl_seconds=int(doc.get("ttl_seconds") or spec.cache_ttl_seconds),
                evidence_hash=evidence_hash,
                raw_ref=raw_ref,
                normalized_ref=normalized_ref,
            )
            return CacheDecision(
                status=ProviderStatus.CACHE_ERROR,
                receipt=receipt,
                usable_raw_ref=None,
                usable_normalized_ref=None,
                reason="cache entry missing raw_ref/normalized_ref",
            )

        receipt = CacheReceipt(
            cache_key=cache_key,
            provider=spec.provider,
            endpoint=spec.endpoint,
            status=ProviderStatus.CACHE_HIT,
            hit=True,
            stale=False,
            cached_empty=False,
            created_at=created_at,
            expires_at=expires_at,
            ttl_seconds=int(doc.get("ttl_seconds") or spec.cache_ttl_seconds),
            evidence_hash=evidence_hash,
            raw_ref=raw_ref,
            normalized_ref=normalized_ref,
        )
        return CacheDecision(
            status=ProviderStatus.CACHE_HIT,
            receipt=receipt,
            usable_raw_ref=raw_ref,
            usable_normalized_ref=normalized_ref,
        )

    def put_success(
        self,
        *,
        spec: ProviderCallSpec,
        request: PackRequest,
        raw_ref: str,
        normalized_ref: str,
        evidence_hash: str,
        ttl_seconds: int,
    ) -> CacheReceipt:
        if not raw_ref or not normalized_ref:
            raise DataGatewayError(
                DataGatewayErrorCode.EVIDENCE_WRITE_FAILED,
                "cache put_success requires raw_ref and normalized_ref",
            )
        cache_key, params_hash = build_cache_key(spec, request)
        created_at = utc_now_iso()
        expires_at = (
            datetime.fromisoformat(created_at).astimezone(UTC) + timedelta(seconds=ttl_seconds)
        ).replace(microsecond=0).isoformat()
        doc = {
            "_id": cache_key,
            "cache_key": cache_key,
            "provider": spec.provider,
            "endpoint": spec.endpoint,
            "market": spec.market.value,
            "domain": spec.domain.value,
            "params_hash": params_hash,
            "created_at": created_at,
            "expires_at": expires_at,
            "ttl_seconds": ttl_seconds,
            "status": ProviderStatus.REMOTE_SUCCESS.value,
            "cached_empty": False,
            "raw_ref": raw_ref,
            "normalized_ref": normalized_ref,
            "evidence_hash": evidence_hash,
            "updated_at": created_at,
        }
        try:
            self.collection.replace_one({"_id": cache_key}, doc, upsert=True)
        except Exception as exc:  # noqa: BLE001
            raise DataGatewayError(DataGatewayErrorCode.EVIDENCE_WRITE_FAILED, f"cache put_success failed: {exc}") from exc
        return CacheReceipt(
            cache_key=cache_key,
            provider=spec.provider,
            endpoint=spec.endpoint,
            status=ProviderStatus.CACHE_HIT,
            hit=True,
            stale=False,
            cached_empty=False,
            created_at=created_at,
            expires_at=expires_at,
            ttl_seconds=ttl_seconds,
            evidence_hash=evidence_hash,
            raw_ref=raw_ref,
            normalized_ref=normalized_ref,
        )

    def put_empty(
        self,
        *,
        spec: ProviderCallSpec,
        request: PackRequest,
        ttl_seconds: int,
        evidence_hash: str | None = None,
    ) -> CacheReceipt:
        cache_key, params_hash = build_cache_key(spec, request)
        created_at = utc_now_iso()
        expires_at = (
            datetime.fromisoformat(created_at).astimezone(UTC) + timedelta(seconds=ttl_seconds)
        ).replace(microsecond=0).isoformat()
        doc = {
            "_id": cache_key,
            "cache_key": cache_key,
            "provider": spec.provider,
            "endpoint": spec.endpoint,
            "market": spec.market.value,
            "domain": spec.domain.value,
            "params_hash": params_hash,
            "created_at": created_at,
            "expires_at": expires_at,
            "ttl_seconds": ttl_seconds,
            "status": ProviderStatus.CACHED_EMPTY.value,
            "cached_empty": True,
            "raw_ref": None,
            "normalized_ref": None,
            "evidence_hash": evidence_hash,
            "updated_at": created_at,
        }
        try:
            self.collection.replace_one({"_id": cache_key}, doc, upsert=True)
        except Exception as exc:  # noqa: BLE001
            raise DataGatewayError(DataGatewayErrorCode.EVIDENCE_WRITE_FAILED, f"cache put_empty failed: {exc}") from exc
        return CacheReceipt(
            cache_key=cache_key,
            provider=spec.provider,
            endpoint=spec.endpoint,
            status=ProviderStatus.CACHED_EMPTY,
            hit=True,
            stale=False,
            cached_empty=True,
            created_at=created_at,
            expires_at=expires_at,
            ttl_seconds=ttl_seconds,
            evidence_hash=evidence_hash,
            raw_ref=None,
            normalized_ref=None,
        )

    def cache_entry_ref(self, spec: ProviderCallSpec, request: PackRequest) -> str:
        cache_key, _ = build_cache_key(spec, request)
        return mongo_ref(self.collection_name, cache_key)


def _parse_iso_or_none(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC)
    if isinstance(value, str):
        return datetime.fromisoformat(value).astimezone(UTC)
    raise ValueError(f"invalid datetime value: {value!r}")


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
