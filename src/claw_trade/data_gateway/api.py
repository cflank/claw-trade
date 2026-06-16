from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Protocol

from .models import DataGap, DataResult, DataResultStatus, Market
from .public_api import PublicDataRequest, validate_public_request


class DataServiceLike(Protocol):
    def request_data(self, requests: Sequence[PublicDataRequest]) -> list[DataResult]: ...

    def resolve_company_names(
        self,
        *,
        market: Market | str,
        symbol_ids: Sequence[str],
        dataset: str = "daily_bar",
    ) -> Mapping[str, str]: ...


class DataAPI:
    def __init__(self, data_service: DataServiceLike) -> None:
        self._data_service = data_service

    def request_data(self, requests: Sequence[PublicDataRequest | Mapping[str, Any]]) -> list[DataResult]:
        valid: list[PublicDataRequest] = []
        invalid_by_index: dict[int, DataResult] = {}
        for idx, item in enumerate(requests):
            normalized = validate_public_request(item)
            if not normalized.ok:
                invalid_by_index[idx] = normalized.result or self._invalid_request_result(item)
                continue
            if normalized.request is not None:
                valid.append(normalized.request)

        valid_results: list[DataResult] = []
        if valid:
            valid_results = self._data_service.request_data(valid)

        ordered: list[DataResult] = []
        valid_cursor = 0
        for idx in range(len(requests)):
            invalid = invalid_by_index.get(idx)
            if invalid is not None:
                ordered.append(invalid)
                continue
            ordered.append(valid_results[valid_cursor])
            valid_cursor += 1
        return ordered

    def resolve_company_names(
        self,
        *,
        market: Market | str,
        symbol_ids: Sequence[str],
        dataset: str = "daily_bar",
    ) -> Mapping[str, str]:
        resolver = getattr(self._data_service, "resolve_company_names", None)
        if not callable(resolver):
            return {}
        return resolver(market=market, symbol_ids=symbol_ids, dataset=dataset)

    def _invalid_request_result(self, raw: Any) -> DataResult:
        if isinstance(raw, Mapping):
            request_id = str(raw.get("request_id") or raw.get("requestId") or raw.get("need_id") or "unknown")
            market = self._extract_market(raw)
        else:
            request_id = "unknown"
            market = Market.CN_A
        as_of = datetime.now(tz=UTC)
        gap = DataGap.invalid_request(
            request_id=request_id,
            message="invalid_public_request",
            market=market,
            as_of=as_of,
        )
        return DataResult(request_id=request_id, status=DataResultStatus.ERROR, gaps=(gap,), as_of=as_of)

    @staticmethod
    def _extract_market(payload: Mapping[str, Any]) -> Market:
        raw_market = payload.get("market")
        if isinstance(raw_market, str):
            try:
                return Market(raw_market.strip().upper())
            except ValueError:
                return Market.CN_A
        if isinstance(raw_market, Market):
            return raw_market
        return Market.CN_A
