from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping
from urllib.parse import urlparse

from claw_trade.data_gateway.execution.managed_http import HttpRequestSpec
from claw_trade.data_gateway.models import FetchResult

from .common import BatchPolicy, CredentialPolicy, EndpointCapability, LicensePolicy

METADATA_ONLY_LICENSE = LicensePolicy(
    raw_storage_mode="metadata_only",
    normalized_storage_allowed=True,
    redistribution_allowed=False,
    retention_days=30,
)

NO_CREDENTIALS = CredentialPolicy(
    credential_required=False,
    credential_names=(),
    credential_scope=None,
    missing_behavior="credential_missing",
)


def endpoint_capability(
    *,
    endpoint_id: str,
    market: str,
    data_type: str,
    source_role: str,
    granularity: tuple[str, ...],
    fields: tuple[str, ...],
    priority_rank: int,
    supports_batch: bool = True,
    batch_by: str = "symbol",
    http_visibility: str = "managed_http",
    can_be_formal_fact_source: bool | None = None,
) -> EndpointCapability:
    return EndpointCapability(
        endpoint_id=endpoint_id,
        market=market,
        data_type=data_type,
        source_role=source_role,
        granularity=granularity,
        fields=fields,
        freshness_supported=("trading_day",),
        http_visibility=http_visibility,
        batch_policy=BatchPolicy(
            supports_batch=supports_batch,
            batch_by=batch_by,
            max_symbols_per_call=50 if supports_batch and batch_by != "none" else None,
            mergeable_fields=fields if supports_batch else (),
        ),
        priority_rank=priority_rank,
        rate_limit_policy={"window_seconds": 60, "max_calls": 30},
        license_policy=METADATA_ONLY_LICENSE,
        can_be_formal_fact_source=can_be_formal_fact_source,
    )


def credential_value(ctx: Any, name: str) -> str | None:
    resolver = getattr(ctx, "credential_resolver", None)
    if resolver is not None:
        getter = getattr(resolver, "get_credential", None)
        if callable(getter):
            return non_empty(getter(name))
    credentials = getattr(ctx, "credentials", None)
    if isinstance(credentials, Mapping):
        return non_empty(credentials.get(name))
    return None


def endpoint(ctx: Any, name: str, default: str) -> tuple[str, str]:
    resolver = getattr(ctx, "credential_resolver", None)
    getter = getattr(resolver, "get_endpoint_url", None)
    configured = non_empty(getter(name)) if callable(getter) else None
    parsed = urlparse(configured or default)
    if not parsed.scheme or not parsed.netloc:
        return str(configured or default).rstrip("/"), ""
    return f"{parsed.scheme}://{parsed.netloc}", parsed.path.rstrip("/")


def managed_http(ctx: Any) -> Any | None:
    value = getattr(ctx, "managed_http", None)
    if value is None or not callable(getattr(value, "send_capture", None)):
        return None
    return value


def send_json_request(task: Any, ctx: Any, request: HttpRequestSpec) -> tuple[Any, tuple[Any, ...], FetchResult | None]:
    http = managed_http(ctx)
    if http is None:
        return None, (), FetchResult.from_error(task, status="error", error=RuntimeError("managed_http_required"))
    capture = http.send_capture(request)
    observations = (capture.observation,)
    error_result = result_from_capture_error(task, capture, observations)
    if error_result is not None:
        return None, observations, error_result
    return capture.json_payload, observations, None


def result_from_capture_error(task: Any, capture: Any, observations: tuple[Any, ...]) -> FetchResult | None:
    observation = capture.observation
    if observation.error_code:
        return FetchResult.from_error(
            task,
            status="error",
            error=RuntimeError(observation.error_code),
            http_observations=observations,
        )
    if observation.quota_signal or observation.status_code == 429:
        return FetchResult.from_error(
            task,
            status="rate_limited",
            error=RuntimeError(observation.quota_signal or "http_429"),
            http_observations=observations,
        )
    if observation.status_code is None or observation.status_code >= 400:
        return FetchResult.from_error(
            task,
            status="error",
            error=RuntimeError(f"http_{observation.status_code}"),
            http_observations=observations,
        )
    return None


def first_symbol(task: Any) -> str | None:
    for item in getattr(task, "symbol_ids", ()) or ():
        text = non_empty(item)
        if text:
            return text.upper()
    return non_empty(getattr(task, "symbol_id", None))


def non_empty(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def decimal_float(value: Any) -> float | None:
    try:
        return float(Decimal(str(value)))
    except (TypeError, ValueError, InvalidOperation):
        return None


def parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.astimezone(UTC).date() if value.tzinfo else value.date()
    if isinstance(value, date):
        return value
    text = non_empty(value)
    if text is None:
        return None
    if text.isdigit():
        number = int(text)
        if number > 10_000_000_000:
            return datetime.fromtimestamp(number / 1000, tz=UTC).date()
        if len(text) >= 8:
            return date(int(text[:4]), int(text[4:6]), int(text[6:8]))
    return date.fromisoformat(text[:10])


def parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    text = non_empty(value)
    if text is None:
        return None
    if text.isdigit():
        number = int(text)
        return datetime.fromtimestamp(number / (1000 if number > 10_000_000_000 else 1), tz=UTC)
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def unwrap_raw(value: Any) -> Any:
    if not isinstance(value, Mapping):
        return value
    for key in ("data", "result", "items", "list"):
        if key in value:
            return value[key]
    return value
