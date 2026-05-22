from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError
from claw_trade.data_gateway.errors import DataGatewayError, DataGatewayErrorCode
from claw_trade.data_gateway.models import PackRequest, ProviderCallSpec

from .mongo import OPENBB_RATE_LIMITS, utc_now_iso


class MongoRateLimitStore:
    def __init__(
        self,
        collection: Any,
        *,
        default_window_seconds: int = 60,
        default_limit: int | None = None,
    ) -> None:
        if default_window_seconds <= 0:
            raise ValueError("default_window_seconds must be > 0")
        if default_limit is not None and default_limit <= 0:
            raise ValueError("default_limit must be > 0 when provided")
        self.collection = collection
        self.default_window_seconds = default_window_seconds
        self.default_limit = default_limit

    @property
    def collection_name(self) -> str:
        return OPENBB_RATE_LIMITS

    def reserve(
        self,
        spec: ProviderCallSpec,
        request: PackRequest,
        *,
        limit: int | None = None,
        window_seconds: int | None = None,
    ) -> bool:
        del request
        resolved_window = self.default_window_seconds if window_seconds is None else int(window_seconds)
        if resolved_window <= 0:
            raise ValueError("window_seconds must be > 0")
        resolved_limit = self.default_limit if limit is None else limit
        if resolved_limit is not None and resolved_limit <= 0:
            raise ValueError("limit must be > 0 when provided")

        now = datetime.now(tz=UTC).replace(microsecond=0)
        window_start = _window_start(now, resolved_window)
        window_start_iso = window_start.isoformat()
        doc_id = f"{spec.provider}:{spec.endpoint}:{window_start_iso}:{resolved_window}"
        base = {
            "_id": doc_id,
            "provider": spec.provider,
            "endpoint": spec.endpoint,
            "window_start": window_start_iso,
            "window_seconds": resolved_window,
            "limit": resolved_limit,
        }

        try:
            if resolved_limit is None:
                self.collection.update_one(
                    {"_id": doc_id},
                    {
                        "$setOnInsert": dict(base),
                        "$inc": {"used": 1},
                        "$set": {"updated_at": utc_now_iso(), "last_error": None},
                    },
                    upsert=True,
                )
                return True

            try:
                reserved = self.collection.find_one_and_update(
                    {
                        "_id": doc_id,
                        "$or": [
                            {"used": {"$lt": resolved_limit}},
                            {"used": {"$exists": False}},
                        ],
                    },
                    {
                        "$setOnInsert": dict(base),
                        "$inc": {"used": 1},
                        "$set": {"updated_at": utc_now_iso(), "last_error": None},
                    },
                    upsert=True,
                    return_document=ReturnDocument.AFTER,
                )
            except DuplicateKeyError:
                reserved = None
            if reserved is not None:
                return True

            self.collection.update_one(
                {"_id": doc_id},
                {
                    "$setOnInsert": {**base, "used": 0},
                    "$set": {"updated_at": utc_now_iso(), "last_error": "rate_limit_exhausted"},
                },
                upsert=True,
            )
            return False
        except Exception as exc:  # noqa: BLE001
            raise DataGatewayError(
                DataGatewayErrorCode.EVIDENCE_WRITE_FAILED,
                f"rate limit reserve failed: {exc}",
            ) from exc


def _window_start(now: datetime, window_seconds: int) -> datetime:
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    elapsed = int((now - epoch).total_seconds())
    aligned = elapsed - (elapsed % window_seconds)
    return epoch + timedelta(seconds=aligned)
