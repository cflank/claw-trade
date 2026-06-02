from __future__ import annotations

from claw_trade.data_gateway.models import DataGap, IngestResult

NON_REMOTE_ATTEMPT_STATUSES = frozenset(
    {"cache_hit", "shared_result", "rate_limited", "cached_empty", "cooldown_skipped", "sdk_http_unknown"}
)

__all__ = ["DataGap", "IngestResult", "NON_REMOTE_ATTEMPT_STATUSES"]
