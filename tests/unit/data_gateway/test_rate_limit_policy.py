from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from claw_trade.data_gateway.coordination.scheduler import DataRunScheduleContext, DataRunScheduler
from claw_trade.data_gateway.execution.rate_limit_policy import (
    RateLimitPolicyResolver,
    provider_rate_limit_namespace,
)


class _Settings:
    def __init__(self, records: dict[str, dict[str, object]]) -> None:
        self._records = records

    def get_instance(self, name: str) -> dict[str, object] | None:
        return self._records.get(name)


def test_rate_limit_policy_resolver_uses_enabled_provider_settings() -> None:
    resolver = RateLimitPolicyResolver(
        data_source_settings=_Settings(
            {
                "data_source:coinglass": {
                    "rate_limit_max_calls": 9,
                    "rate_limit_window_seconds": 60,
                    "rate_limit_safety_margin": 1,
                    "rate_limit_overflow": "wait",
                    "rate_limit_wait_timeout_seconds": 75,
                }
            }
        )
    )

    policy = resolver.resolve(
        provider_id="crypto_coinglass_derivatives",
        default_policy=SimpleNamespace(window_seconds=60, max_requests=None),
    )

    assert policy.max_requests == 9
    assert policy.window_seconds == 60
    assert policy.safety_margin == 1
    assert policy.overflow == "wait"
    assert policy.wait_timeout_seconds == 75


def test_rate_limit_policy_resolver_uses_settings_for_any_mapped_source() -> None:
    resolver = RateLimitPolicyResolver(
        data_source_settings=_Settings(
            {
                "data_source:finnhub": {
                    "rate_limit_max_calls": 5,
                    "rate_limit_window_seconds": 30,
                }
            }
        )
    )

    policy = resolver.resolve(
        provider_id="us_finnhub_data",
        default_policy=SimpleNamespace(window_seconds=60, max_requests=None),
    )

    assert policy.max_requests == 5
    assert policy.window_seconds == 30


def test_rate_limit_policy_resolver_does_not_apply_provider_hard_limit_without_settings() -> None:
    resolver = RateLimitPolicyResolver(data_source_settings=_Settings({}))

    policy = resolver.resolve(
        provider_id="crypto_coinglass_derivatives",
        default_policy=SimpleNamespace(window_seconds=60, max_requests=30),
    )

    assert policy.max_requests is None
    assert policy.overflow == "fail_fast"


def test_rate_limit_policy_resolver_does_not_apply_provider_hard_limit_for_api_source_without_limit_settings() -> None:
    resolver = RateLimitPolicyResolver(
        data_source_settings=_Settings(
            {
                "data_source:finnhub": {
                    "credential_ref": "data_source:token",
                }
            }
        )
    )

    policy = resolver.resolve(
        provider_id="us_finnhub_data",
        default_policy=SimpleNamespace(window_seconds=60, max_requests=20),
    )

    assert policy.max_requests is None
    assert policy.overflow == "fail_fast"


def test_rate_limit_policy_resolver_ignores_provider_max_calls_without_user_settings() -> None:
    resolver = RateLimitPolicyResolver(data_source_settings=_Settings({}))

    policy = resolver.resolve(
        provider_id="cn_a_primary",
        default_policy=SimpleNamespace(window_seconds=60, max_calls=30),
    )

    assert policy.max_requests is None


def test_rate_limit_namespace_uses_final_data_source_not_endpoint() -> None:
    assert provider_rate_limit_namespace("crypto_coinglass_derivatives") == "coinglass"
    assert provider_rate_limit_namespace("official_api_coinglass") == "coinglass"
    assert provider_rate_limit_namespace("official_api_tushare") == "tushare"
    assert provider_rate_limit_namespace("us_finnhub_data") == "finnhub"
    assert provider_rate_limit_namespace("hk_finnhub_data") == "finnhub"


def test_data_run_scheduler_does_not_reanchor_external_source_limits() -> None:
    run_started_at = datetime(2026, 6, 2, 10, 15, 30, tzinfo=UTC)
    batch = SimpleNamespace(
        batch_id="batch-1",
        rate_limit_policy=SimpleNamespace(window_seconds=60, max_requests=9, window_anchor=None),
    )

    scheduled = DataRunScheduler().schedule(
        (batch,),
        DataRunScheduleContext.for_plan(run_id="plan-1", run_started_at=run_started_at),
    )

    assert scheduled[0].rate_limit_policy.window_anchor is None
