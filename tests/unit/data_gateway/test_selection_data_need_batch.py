from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import claw_trade.data_gateway._selection_batch as selection_batch
from claw_trade.data_gateway.execution.rate_limiter import RateLimitPolicy
from claw_trade.data_gateway.models import FetchResult, IngestResult, Market
from claw_trade.data_gateway.needs import DataNeed, NeedPlan, NeedPriority, ProviderCallSpec
from claw_trade.data_gateway.public_api import PublicDataRequest, PublicRequestPriority
from claw_trade.selection.models import SelectionMarket, SelectionProfile, SelectionRunPlan, SelectionTriggerSource


def test_selection_data_need_batch_fans_out_merged_call_result_to_all_needs(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    deadline = datetime.now(tz=UTC) + timedelta(seconds=30)
    needs = (
        _need("need-1", deadline=deadline),
        _need("need-2", deadline=deadline),
    )
    call = ProviderCallSpec(
        call_id="call-daily",
        method="POST",
        public_api_id="cn_a.daily_bar",
        implementation_id="cn_a.daily_bar:official_api_tushare:tushare.daily",
        provider_id="official_api_tushare",
        catalog_endpoint_id="tushare.daily",
        official_path_or_api_name="daily",
        params={"ts_code": "600519.SH"},
        auth_scope="tushare_body_token",
        rate_limit_bucket="ratelimit:tushare",
        http_visibility="managed_http",
        parser_status="normalized",
        batch_key="batch:tushare.daily:600519",
        official_doc_ref="https://tushare.pro/document/2?doc_id=27",
        deadline_at=deadline,
        need_ids=("need-1", "need-2"),
        priority=NeedPriority.NORMAL,
    )
    monkeypatch.setattr(
        selection_batch,
        "plan_public_data_requests",
        lambda _requests: NeedPlan(plan_id="plan", needs=needs, planned_calls=(call,), created_at=deadline),
    )
    runtime = SimpleNamespace(
        rate_limit_policy_resolver=SimpleNamespace(resolve=lambda **_kwargs: RateLimitPolicy(window_seconds=60, max_requests=None)),
        rate_limiter=None,
        data_service=SimpleNamespace(execution_gate=_OwnerGate()),
        fetch_engine=SimpleNamespace(fetch=lambda batch: FetchResult.from_success(batch, payload={"rows": [_daily_row()]})),
        ingest=SimpleNamespace(ingest=lambda result, batch: _ingest(batch.batch_id)),
    )

    results = selection_batch._execute_selection_data_need_batch(
        runtime=runtime,
        plan=_selection_plan(),
        needs=(
            _request("need-1", deadline=deadline),
            _request("need-2", deadline=deadline),
        ),
    )

    assert [result.request_id for result in results] == ["need-1", "need-2"]
    assert all(result.dataset_refs == ("dataset:daily",) for result in results)
    assert all(result.rows and result.rows[0]["close"] == 1505.0 for result in results)


class _OwnerGate:
    def enter(self, _batch):  # type: ignore[no-untyped-def]
        return SimpleNamespace(kind="owner", owner_token="owner-token")

    def publish_shared_result(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
        return True

    def wait_after_rate_limited_fetch(self, *_args):  # type: ignore[no-untyped-def]
        return False

    def mark_cooldown_after_fetch(self, *_args):  # type: ignore[no-untyped-def]
        return None


def _need(need_id: str, *, deadline: datetime) -> DataNeed:
    return DataNeed(
        need_id=need_id,
        api_id="cn_a.daily_bar",
        market=Market.CN_A,
        instrument="600519.SH",
        time_range_start=date(2026, 6, 1),
        time_range_end=date(2026, 6, 12),
        granularity="daily",
        priority=NeedPriority.NORMAL,
        requested_by_worker="selection",
        purpose="selection_refresh",
        deadline_at=deadline,
        consumer="select",
    )


def _request(request_id: str, *, deadline: datetime) -> PublicDataRequest:
    return PublicDataRequest(
        request_id=request_id,
        item="日线",
        market=Market.CN_A,
        instrument="600519.SH",
        time_range_start=date(2026, 6, 1),
        time_range_end=date(2026, 6, 12),
        granularity="daily",
        priority=PublicRequestPriority.NORMAL,
        requested_by_worker="selection",
        purpose="selection_refresh",
        deadline_at=deadline,
        consumer="select",
    )


def _selection_plan() -> SelectionRunPlan:
    return SelectionRunPlan(
        selection_run_id="sel-run",
        market=SelectionMarket.CN_A,
        profile=SelectionProfile.CN_A,
        trade_date="2026-06-12",
        lookback_trading_days=20,
        universe_scope="test",
        data_need_audit_ref="audit:test",
        approved_strategy_config_ref="strategy:test",
        trigger_source=SelectionTriggerSource.SELECT_COMMAND_REFRESH,
    )


def _daily_row() -> dict[str, object]:
    return {
        "dataset": "daily_bar",
        "market": "CN_A",
        "symbol_id": "600519.SH",
        "granularity": "daily",
        "period_start": date(2026, 6, 11),
        "period_end": date(2026, 6, 11),
        "date": date(2026, 6, 11),
        "open": 1500.0,
        "high": 1510.0,
        "low": 1490.0,
        "close": 1505.0,
        "volume": 1000.0,
    }


def _ingest(batch_id: str) -> IngestResult:
    return IngestResult.from_refs(
        batch_id=batch_id,
        dataset_refs=("dataset:daily",),
        raw_refs=("raw:daily",),
        attempt_refs=("attempt:daily",),
        gaps=(),
        remote_success=True,
    )
