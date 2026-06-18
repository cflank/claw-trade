from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from claw_trade.data_gateway.models import DataResultStatus, Market
from claw_trade.ui_contracts.enums import MarketProfile

_DEFAULT_MAX_QUOTE_AGE_SECONDS = 900


class PriceAlertDataAPI(Protocol):
    def request_data(self, requests: Sequence[Mapping[str, Any]]) -> Sequence[Any]: ...


class PriceAlertQuoteProvider:
    def __init__(
        self,
        *,
        data_api: PriceAlertDataAPI,
        now_provider: Callable[[], datetime] | None = None,
        max_quote_age_seconds: int = _DEFAULT_MAX_QUOTE_AGE_SECONDS,
    ) -> None:
        self._data_api = data_api
        self._now_provider = now_provider or (lambda: datetime.now(UTC))
        self._max_quote_age = timedelta(seconds=max_quote_age_seconds)

    def __call__(
        self,
        instrument_code: str,
        market_profile: MarketProfile,
        *,
        required_percent_window: str | None = None,
    ) -> dict[str, Any]:
        now = self._now()
        request = {
            "request_id": f"price-alert:{market_profile.value}:{instrument_code}:{now.strftime('%Y%m%d%H%M%S')}",
            "item": "realtime_quote",
            "market": market_profile.value,
            "instrument": instrument_code,
            "granularity": "realtime",
            "priority": "required",
            "requested_by_worker": "price_alert_quote_provider",
            "purpose": "price_alert_quote",
            "freshness_policy": "realtime",
            "deadline_at": (now + timedelta(seconds=10)).isoformat(),
            "consumer": "price_alert",
        }
        result = self._single_result(self._data_api.request_data([request]))
        if getattr(result, "status", None) != DataResultStatus.READY:
            raise RuntimeError("datasource_test_failed: price_alert_quote_unavailable")
        rows = tuple(getattr(result, "rows", ()) or ())
        if not rows:
            raise RuntimeError("datasource_test_failed: price_alert_quote_unavailable")
        row = dict(rows[0])
        quote_timestamp = _quote_timestamp(row)
        if now - quote_timestamp > self._max_quote_age:
            raise RuntimeError("datasource_test_failed: stale_quote_unavailable")
        payload = {
            "current_price": _required_float(row, ("current_price", "price", "last_price", "close")),
            "quote_timestamp": quote_timestamp.isoformat().replace("+00:00", "Z"),
            "evidence_ref": _evidence_ref(row=row, result=result),
            "source_market_session": row.get("source_market_session"),
        }
        _copy_optional_float(payload, "percent_change_24h", row, ("percent_change_24h", "change_pct_24h"))
        _copy_optional_float(payload, "percent_change_intraday", row, ("percent_change_intraday", "change_pct_intraday", "change_pct"))
        _copy_optional_float(payload, "percent_change", row, ("percent_change", "change_pct"))
        if required_percent_window == "24h" and "percent_change_24h" not in payload:
            raise RuntimeError("datasource_test_failed: invalid_quote_payload")
        if required_percent_window == "intraday" and "percent_change_intraday" not in payload:
            raise RuntimeError("datasource_test_failed: invalid_quote_payload")
        return payload

    def _now(self) -> datetime:
        value = self._now_provider()
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @staticmethod
    def _single_result(results: Sequence[Any]) -> Any:
        if len(results) != 1:
            raise RuntimeError("datasource_test_failed: price_alert_quote_unavailable")
        return results[0]


def _quote_timestamp(row: Mapping[str, Any]) -> datetime:
    raw = row.get("quote_timestamp") or row.get("timestamp")
    if raw is None:
        raise RuntimeError("datasource_test_failed: invalid_quote_payload")
    if isinstance(raw, datetime):
        value = raw
    else:
        try:
            value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError as exc:
            raise RuntimeError("datasource_test_failed: invalid_quote_payload") from exc
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _required_float(row: Mapping[str, Any], keys: tuple[str, ...]) -> float:
    for key in keys:
        if key not in row or row[key] is None:
            continue
        try:
            return float(row[key])
        except (TypeError, ValueError) as exc:
            raise RuntimeError("datasource_test_failed: invalid_quote_payload") from exc
    raise RuntimeError("datasource_test_failed: invalid_quote_payload")


def _copy_optional_float(payload: dict[str, Any], target_key: str, row: Mapping[str, Any], source_keys: tuple[str, ...]) -> None:
    for key in source_keys:
        if key not in row or row[key] is None:
            continue
        try:
            payload[target_key] = float(row[key])
            return
        except (TypeError, ValueError) as exc:
            raise RuntimeError("datasource_test_failed: invalid_quote_payload") from exc


def _evidence_ref(*, row: Mapping[str, Any], result: Any) -> str:
    raw = row.get("evidence_ref") or row.get("dataset_ref")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    dataset_refs = tuple(getattr(result, "dataset_refs", ()) or ())
    if dataset_refs:
        value = str(dataset_refs[0]).strip()
        if value:
            return value
    raise RuntimeError("datasource_test_failed: invalid_quote_payload")


def market_profile_to_data_market(market_profile: MarketProfile) -> Market:
    return Market(market_profile.value)
