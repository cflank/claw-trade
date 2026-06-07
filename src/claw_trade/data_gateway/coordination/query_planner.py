from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from claw_trade.data_gateway.models import (
    CoverageRequirement,
    DataRequest,
    Market,
    QueryPlan,
    WarehouseCheck,
)


class QueryPlanner:
    _MARKET_DEFAULTS: dict[Market, tuple[str, str]] = {
        Market.CN_A: ("Asia/Shanghai", "CN_A_SSE_SZSE"),
        Market.US: ("America/New_York", "US_NYSE_NASDAQ"),
        Market.HK: ("Asia/Hong_Kong", "HK_HKEX"),
        Market.CRYPTO: ("UTC", "CRYPTO_24_7"),
    }

    def validate_and_normalize(self, request: DataRequest) -> QueryPlan:
        normalized = self.normalize_symbol(request)
        checks = self.build_warehouse_checks((normalized,))
        required = self.build_required_coverage((normalized,))
        return QueryPlan(
            normalized_requests=(normalized,),
            warehouse_checks=checks,
            required_coverage=required,
            expected_outputs=(normalized.data_type,),
        )

    def validate_and_normalize_many(self, requests: Sequence[DataRequest]) -> QueryPlan:
        normalized = tuple(self.normalize_symbol(req) for req in requests)
        checks = self.build_warehouse_checks(normalized)
        required = self.build_required_coverage(normalized)
        expected_outputs = tuple(dict.fromkeys(req.data_type for req in normalized))
        return QueryPlan(
            normalized_requests=normalized,
            warehouse_checks=checks,
            required_coverage=required,
            expected_outputs=expected_outputs,
        )

    def normalize_symbol(self, request: DataRequest) -> DataRequest:
        timezone, calendar = self._MARKET_DEFAULTS[request.market]
        symbol_id = request.symbol_id.strip().upper() if request.symbol_id else None
        base_asset = request.base_asset.strip().upper() if request.base_asset else None
        quote_asset = request.quote_asset.strip().upper() if request.quote_asset else None
        fields = tuple(dict.fromkeys(field.strip() for field in request.fields if field.strip()))
        granularity = request.granularity.strip().lower()
        as_of = request.as_of if request.as_of.tzinfo else request.as_of.replace(tzinfo=UTC)
        if as_of.tzinfo != UTC:
            as_of = as_of.astimezone(UTC)
        payload = request.model_dump()
        payload.update(
            {
                "symbol_id": symbol_id,
                "base_asset": base_asset,
                "quote_asset": quote_asset,
                "fields": fields,
                "granularity": granularity,
                "timezone": request.timezone.strip() or timezone,
                "calendar": request.calendar.strip() or calendar,
                "as_of": as_of,
            }
        )
        return DataRequest.model_validate(payload)

    def build_warehouse_checks(self, requests: Sequence[DataRequest]) -> tuple[WarehouseCheck, ...]:
        return tuple(
            WarehouseCheck(
                request_id=req.request_id,
                market=req.market,
                symbol_id=req.symbol_id,
                universe_ref=req.universe_ref,
                data_type=req.data_type,
                granularity=req.granularity,
                fields=req.fields,
                date_range_start=req.date_range_start,
                date_range_end=req.date_range_end,
                freshness_policy=req.freshness_policy,
                timezone=req.timezone,
                calendar=req.calendar,
                source_role_required=req.source_role_required,
                as_of=req.as_of,
            )
            for req in requests
        )

    def build_required_coverage(self, requests: Sequence[DataRequest]) -> CoverageRequirement:
        request_ids = tuple(req.request_id for req in requests)
        required_fields = {req.request_id: req.fields for req in requests}
        expected_outputs = tuple(dict.fromkeys(req.data_type for req in requests))
        return CoverageRequirement(
            request_ids=request_ids,
            expected_outputs=expected_outputs,
            required_fields_by_request=required_fields,
        )
