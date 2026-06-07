from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Protocol

from pydantic import ValidationError

from .models import DataGap, DataRequest, DataResult, DataResultStatus, Market


class DataServiceLike(Protocol):
    def get_data(self, request: DataRequest) -> DataResult: ...

    def get_data_batch(self, requests: Sequence[DataRequest]) -> list[DataResult]: ...

    def plan_batch(self, requests: Sequence[DataRequest]) -> Any: ...

    def execute_plan(self, plan: Any) -> list[DataResult]: ...

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

    def get_data(self, request: DataRequest | Mapping[str, Any]) -> DataResult:
        normalized = self._validate_request(request)
        if isinstance(normalized, DataResult):
            return normalized
        return self._data_service.get_data(normalized)

    def get_data_batch(self, requests: Sequence[DataRequest | Mapping[str, Any]]) -> list[DataResult]:
        valid: list[DataRequest] = []
        invalid_by_index: dict[int, DataResult] = {}
        for idx, item in enumerate(requests):
            normalized = self._validate_request(item)
            if isinstance(normalized, DataResult):
                invalid_by_index[idx] = normalized
                continue
            valid.append(normalized)

        valid_results: list[DataResult] = []
        if valid:
            plan = self._data_service.plan_batch(valid)
            valid_results = self._data_service.execute_plan(plan)

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

    def _validate_request(self, raw: DataRequest | Mapping[str, Any]) -> DataRequest | DataResult:
        payload: Mapping[str, Any] | None
        if isinstance(raw, DataRequest):
            return raw
        payload = raw
        request_id = str(payload.get("request_id") or payload.get("requestId") or "unknown")
        as_of = datetime.now(tz=UTC)
        market = self._extract_market(payload)
        try:
            return DataRequest.model_validate(payload)
        except ValidationError as exc:
            gap = DataGap.invalid_request(
                request_id=request_id,
                message=f"invalid_request: {exc.errors()[0]['msg']}",
                market=market,
                as_of=as_of,
            )
            return DataResult(
                request_id=request_id,
                status=DataResultStatus.ERROR,
                gaps=(gap,),
                as_of=as_of,
            )

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
