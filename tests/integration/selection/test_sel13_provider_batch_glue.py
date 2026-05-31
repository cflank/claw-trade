from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import requests
from claw_trade.artifacts.openviking_client import OpenVikingStat
from claw_trade.data_gateway.models import (
    DeclarativeProviderManifest,
    Market,
    PackDomain,
    PrioritySource,
    ProviderAdmissionStatus,
    SourceRole,
)
from claw_trade.data_gateway.providers.execution import ProviderExecutionEvidenceHelper
from claw_trade.data_gateway.selection_batch import load_cn_a_selection_v1_strategy
from claw_trade.data_gateway.store import (
    MongoCacheStore,
    MongoRateLimitStore,
    MongoSingleFlightCoordinator,
)
from claw_trade.data_gateway.store.mongo import (
    OPENBB_CACHE_ENTRIES,
    OPENBB_NORMALIZED,
    OPENBB_PROVIDER_ATTEMPTS,
    OPENBB_PROVIDER_HTTP_EVIDENCE,
    OPENBB_RATE_LIMITS,
    OPENBB_RAW_PAYLOADS,
    OPENBB_SINGLE_FLIGHT_CALLS,
    mongo_ref,
)
from claw_trade.selection.data_job import SelectionDataJob, SelectionProviderBatchResult
from claw_trade.selection.models import (
    DataGapSeverity,
    SelectionDataRunStatus,
    SelectionMarket,
    SelectionProfile,
    SelectionRunPlan,
    SelectionTriggerSource,
)
from claw_trade.selection.provider_batch import (
    build_selection_provider_batch_plan,
    fetch_selection_batch_from_data_gateway,
)
from claw_trade.selection.store import SelectionRunStore
from pymongo.errors import DuplicateKeyError


class _FakeCollection:
    def __init__(self) -> None:
        self._docs: dict[str, dict[str, object]] = {}

    def upsert(self, document_id: str, payload: dict[str, object]) -> None:
        self._docs[document_id] = dict(payload)

    def find_one(self, query: dict[str, object]) -> dict[str, object] | None:
        for value in self._docs.values():
            if _matches_query(value, query):
                return dict(value)
        return None

    def insert_one(self, doc: dict[str, object]) -> None:
        document_id = str(doc["_id"])
        if document_id in self._docs:
            raise DuplicateKeyError("duplicate")
        self._docs[document_id] = dict(doc)

    def replace_one(self, query: dict[str, object], doc: dict[str, object], upsert: bool = False) -> None:
        existing = self.find_one(query)
        if existing is None and not upsert:
            return
        self._docs[str(doc["_id"])] = dict(doc)

    def update_one(self, query: dict[str, object], update: dict[str, object], upsert: bool = False) -> None:
        current = self.find_one(query)
        if current is None:
            if not upsert:
                return
            current = {"_id": query["_id"]}
        if "$setOnInsert" in update and str(current["_id"]) not in self._docs:
            current.update(update["$setOnInsert"])  # type: ignore[arg-type]
        if "$inc" in update:
            for field, delta in update["$inc"].items():  # type: ignore[union-attr]
                current[str(field)] = int(current.get(str(field), 0)) + int(delta)
        if "$set" in update:
            current.update(update["$set"])  # type: ignore[arg-type]
        self._docs[str(current["_id"])] = dict(current)

    def find_one_and_update(
        self,
        query: dict[str, object],
        update: dict[str, object],
        *,
        upsert: bool = False,
        return_document=None,
    ) -> dict[str, object] | None:
        del return_document
        current = self.find_one(query)
        if current is None and not upsert:
            return None
        self.update_one(query if current is not None else {"_id": query["_id"]}, update, upsert=upsert)
        return self.find_one({"_id": query["_id"]})


def _matches_query(doc: dict[str, object], query: dict[str, object]) -> bool:
    for key, expected in query.items():
        if key == "$or":
            branches = expected if isinstance(expected, list) else ()
            if not any(_matches_query(doc, branch) for branch in branches if isinstance(branch, dict)):
                return False
            continue
        actual = doc.get(key)
        if isinstance(expected, dict):
            pattern = expected.get("$regex")
            if isinstance(pattern, str):
                if actual is None or re.match(pattern, str(actual)) is None:
                    return False
                continue
            if "$lt" in expected and not (actual is not None and actual < expected["$lt"]):  # type: ignore[operator]
                return False
            if "$lte" in expected and not (actual is not None and actual <= expected["$lte"]):  # type: ignore[operator]
                return False
            if "$exists" in expected and ((actual is not None) != bool(expected["$exists"])):
                return False
            continue
        if actual != expected:
            return False
    return True


class _FakeRawStore:
    def __init__(self) -> None:
        self.refs: list[str] = []
        self.collection = _FakeCollection()

    def write_raw(
        self,
        *,
        request,
        spec,
        payload,
        content_type: str,
        source_url: str | None,
        raw_export_policy: str,
    ) -> str:
        ref = mongo_ref(
            OPENBB_RAW_PAYLOADS,
            f"{request.run_id}-{spec.adapter_id}-{spec.endpoint}-{len(self.refs) + 1}",
        )
        self.refs.append(ref)
        collection, document_id = ref.removeprefix("mongo://").split("/", 1)
        self.collection.upsert(
            document_id,
            {
                "_id": document_id,
                "collection": collection,
                "content_type": content_type,
                "provider": spec.provider,
                "endpoint": spec.endpoint,
                "params": dict(spec.params),
            },
        )
        return ref


class _FakeNormalizedStore:
    def __init__(self) -> None:
        self.refs: list[str] = []
        self._items: dict[str, object] = {}

    def write(self, *, request, spec, normalized) -> str:
        ref = mongo_ref(
            OPENBB_NORMALIZED,
            f"{request.run_id}-{spec.adapter_id}-{spec.endpoint}-{len(self.refs) + 1}",
        )
        self.refs.append(ref)
        self._items[ref] = normalized
        return ref

    def read(self, normalized_ref: str):
        item = self._items.get(normalized_ref)
        if item is None:
            raise ValueError(f"missing normalized ref: {normalized_ref}")
        return item


class _FakeAttemptStore:
    def __init__(self) -> None:
        self.attempt_ids: list[str] = []
        self._items: dict[str, object] = {}

    def write(self, attempt) -> str:
        self.attempt_ids.append(attempt.attempt_id)
        self._items[attempt.attempt_id] = attempt
        return attempt.attempt_id

    def get(self, attempt_id: str):
        return self._items.get(attempt_id)


class _FakeHttpEvidenceStore:
    def __init__(self) -> None:
        self.evidence_ids: list[str] = []
        self.collection = _FakeCollection()

    def write(self, evidence) -> str:
        self.evidence_ids.append(evidence.evidence_id)
        self.collection.upsert(
            evidence.evidence_id,
            {
                "_id": evidence.evidence_id,
                "status": evidence.status.value,
            },
        )
        return evidence.evidence_id


class _FakeOpenVikingBackend:
    def __init__(self) -> None:
        self._content_by_uri: dict[str, bytes] = {}

    def _write_verified_content(
        self,
        *,
        uri: str,
        content: str,
        operations: list[dict[str, object]],
        operation_prefix: str,
    ) -> dict[str, object]:
        del operations, operation_prefix
        payload = content.encode("utf-8")
        digest = hashlib.sha256(payload).hexdigest()
        self._content_by_uri[uri] = payload
        return {
            "expected_write_sha256": digest,
            "expected_write_size_bytes": len(payload),
            "sha256": digest,
            "size_bytes": len(payload),
            "stat_size_bytes": len(payload),
            "stat_sha256": digest,
        }

    def fetch_stat_by_uri(self, uri: str) -> OpenVikingStat:
        payload = self._content_by_uri.get(uri)
        if payload is None:
            return OpenVikingStat(uri=uri, ok=True, exists=False, size_bytes=None, is_dir=False, sha256=None)
        return OpenVikingStat(
            uri=uri,
            ok=True,
            exists=True,
            size_bytes=len(payload),
            is_dir=False,
            sha256=hashlib.sha256(payload).hexdigest(),
        )

    def fetch_content_by_uri(self, uri: str) -> bytes:
        payload = self._content_by_uri.get(uri)
        if payload is None:
            raise ValueError(f"missing uri: {uri}")
        return payload


def _install_fake_evidence_runtime(monkeypatch: pytest.MonkeyPatch):
    from claw_trade.data_gateway import selection_batch as selection_batch_module

    raw_store = _FakeRawStore()
    normalized_store = _FakeNormalizedStore()
    attempt_store = _FakeAttemptStore()
    http_store = _FakeHttpEvidenceStore()
    cache_store = MongoCacheStore(_FakeCollection())
    rate_limit_store = MongoRateLimitStore(_FakeCollection())
    single_flight = MongoSingleFlightCoordinator(
        collection=_FakeCollection(),
        attempt_store=attempt_store,
        wait_timeout_seconds=0.1,
        poll_interval_seconds=0.001,
    )
    openviking_backend = _FakeOpenVikingBackend()
    helper = ProviderExecutionEvidenceHelper(
        raw_store=raw_store,
        normalized_store=normalized_store,
        attempt_store=attempt_store,
        http_evidence_store=http_store,
    )
    runtime = selection_batch_module._SelectionEvidenceRuntime(
        helper=helper,
        attempt_store=attempt_store,
        cache_store=cache_store,
        rate_limit_store=rate_limit_store,
        single_flight=single_flight,
        normalized_store=normalized_store,
        raw_store=raw_store,
        http_evidence_store=http_store,
        openviking_backend=openviking_backend,
        bootstrap_error=None,
    )
    monkeypatch.setattr(selection_batch_module, "_build_selection_evidence_runtime", lambda: runtime)
    raw_store.openviking_backend = openviking_backend  # type: ignore[attr-defined]  # noqa: SLF001
    return raw_store, normalized_store, attempt_store, http_store


def _plan(*, run_id: str, trade_date: str) -> SelectionRunPlan:
    provider_plan = build_selection_provider_batch_plan(
        market=SelectionMarket.CN_A,
        profile=SelectionProfile.CN_A,
        trade_date=trade_date,
    )
    return SelectionRunPlan(
        selection_run_id=run_id,
        market=SelectionMarket.CN_A,
        profile=SelectionProfile.CN_A,
        trade_date=trade_date,
        lookback_trading_days=provider_plan.lookback_trading_days,
        universe_scope=provider_plan.universe_scope,
        provider_batch_plan_ref=provider_plan.plan_id,
        approved_strategy_config_ref="config://cn-a-selection-v1",
        trigger_source=SelectionTriggerSource.SCHEDULED,
    )


def _spot_row(*, code: str, name: str, industry: str, open_price: float, close_price: float, amount: float, vol_ratio: float) -> dict[str, object]:
    return {
        "代码": code,
        "名称": name,
        "行业": industry,
        "今开": open_price,
        "最新价": close_price,
        "最高": close_price + 0.2,
        "最低": open_price - 0.2,
        "成交额": amount,
        "成交量": 1200000.0,
        "量比": vol_ratio,
        "history": _strategy_history_rows(open_price=open_price, close_price=close_price, amount=amount),
        "private_placement_event_date": "none",
        "private_placement_days_since": 9999.0,
    }


def _strategy_history_rows(*, open_price: float, close_price: float, amount: float) -> tuple[dict[str, object], ...]:
    rows: list[dict[str, object]] = []
    base_date = datetime(2025, 5, 27, tzinfo=UTC)
    start_close = max(1.0, close_price * 0.75)
    step = (close_price - start_close) / 259.0
    for idx in range(260):
        close = start_close + step * idx
        day_open = close * 0.99
        volume = 1000000.0 + idx * 1000.0
        if idx == 259:
            day_open = open_price
            volume = 1200000.0
        rows.append(
            {
                "date": (base_date + timedelta(days=idx)).date().isoformat(),
                "open": day_open,
                "high": max(close, day_open) * 1.01,
                "low": min(close, day_open) * 0.99,
                "close": close,
                "volume": volume,
                "amount": amount,
            }
        )
    return tuple(rows)


def _spot_row_without_vol_ratio(
    *,
    code: str,
    name: str,
    industry: str,
    open_price: float,
    close_price: float,
    amount: float,
) -> dict[str, object]:
    row = _spot_row(
        code=code,
        name=name,
        industry=industry,
        open_price=open_price,
        close_price=close_price,
        amount=amount,
        vol_ratio=1.0,
    )
    row.pop("量比", None)
    row.pop("vol_ratio", None)
    return row


def _spot_row_without_open(
    *,
    code: str,
    name: str,
    industry: str,
    close_price: float,
    amount: float,
    vol_ratio: float,
) -> dict[str, object]:
    row = _spot_row(
        code=code,
        name=name,
        industry=industry,
        open_price=close_price,
        close_price=close_price,
        amount=amount,
        vol_ratio=vol_ratio,
    )
    row.pop("今开", None)
    row.pop("open", None)
    return row


def _spot_row_without_amount(
    *,
    code: str,
    name: str,
    industry: str,
    open_price: float,
    close_price: float,
    vol_ratio: float,
) -> dict[str, object]:
    row = _spot_row(
        code=code,
        name=name,
        industry=industry,
        open_price=open_price,
        close_price=close_price,
        amount=1.0,
        vol_ratio=vol_ratio,
    )
    row.pop("成交额", None)
    row.pop("amount", None)
    row["成交量"] = 1200000.0
    return row


def _patch_tushare_selection_batch(
    monkeypatch: pytest.MonkeyPatch,
    *,
    rows: tuple[dict[str, object], ...],
) -> None:
    monkeypatch.setenv("TUSHARE_TOKEN", "sel13-test-token")
    monkeypatch.setattr(
        "claw_trade.data_gateway.providers.market_adapters._call_tushare_stock_zh_a_spot_batch",
        lambda *, token, trade_date, env=None: rows,
    )


def _patch_akshare_selection_batch(
    monkeypatch: pytest.MonkeyPatch,
    *,
    rows: tuple[dict[str, object], ...],
) -> None:
    monkeypatch.setattr(
        "claw_trade.data_gateway.providers.market_adapters._call_akshare_stock_zh_a_spot_batch",
        lambda: rows,
    )


def _enabled_selection_manifest(
    *,
    provider_id: str,
    adapter_id: str,
    priority: int,
    priority_source: PrioritySource,
    enabled: bool = True,
) -> DeclarativeProviderManifest:
    return DeclarativeProviderManifest(
        provider_id=provider_id,
        adapter_id=adapter_id,
        display_name=provider_id,
        version="1",
        config_version=f"cfg://{adapter_id}",
        markets=(Market.CN_A,),
        domains=(PackDomain.MARKET,),
        endpoints=("stock_zh_a_spot_em_batch",),
        source_role=SourceRole.MARKET_DATA,
        expected_schema_id="cn_a.selection.batch.v1",
        base_url="https://example.com",
        request_template={"path": "/selection"},
        response_mapping={"rows": "rows"},
        credential_requirements=(),
        rate_limit_policy_id=f"{adapter_id}.default",
        cache_ttl_seconds=900,
        license_policy_id="personal_research",
        raw_export_policy="metadata_only",
        healthcheck={"method": "GET", "path": "/health"},
        enabled=enabled,
        admission_status=ProviderAdmissionStatus.ENABLED_CANDIDATE,
        priority=priority,
        priority_source=priority_source,
        coverage_group="cn_a_selection_batch",
        coverage_quorum=1,
    )


@pytest.mark.integration
def test_sel13_strategy_loader_from_selection_batch_does_not_require_vol_ratio() -> None:
    strategy = load_cn_a_selection_v1_strategy("config://cn-a-selection-v1")
    assert strategy is not None
    assert all(rule.field != "vol_ratio" for rule in strategy.hard_filters)
    assert "vol_ratio" not in strategy.weights
    assert all(field.field != "vol_ratio" for field in strategy.stable_top20_rule.tie_break_fields)


@pytest.mark.integration
def test_sel13_market_adapter_batch_retries_remote_disconnect_and_maps_industry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from claw_trade.data_gateway.providers import market_adapters

    class _FakeResponse:
        def __init__(self, payload: dict[str, object]) -> None:
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return self._payload

    page1_calls = 0
    page2_calls = 0
    page1_hosts: list[str] = []

    def _fake_get(url: str, *, params: dict[str, str], headers: dict[str, str], timeout: int):
        nonlocal page1_calls, page2_calls, page1_hosts
        del headers, timeout
        page = int(params["pn"])
        if page == 1:
            page1_calls += 1
            page1_hosts.append(url.split("/")[2])
            if page1_calls <= 2:
                raise requests.exceptions.ConnectionError("Remote end closed connection without response")
            return _FakeResponse(
                {
                    "data": {
                        "total": 6001,
                        "diff": [
                            {
                                "f12": "600519",
                                "f14": "贵州茅台",
                                "f100": "酿酒行业",
                                "f17": 1600.0,
                                "f2": 1612.0,
                                "f15": 1620.0,
                                "f16": 1598.0,
                                "f6": 3000000000.0,
                                "f10": 1.8,
                            }
                        ],
                    }
                }
            )
        page2_calls += 1
        return _FakeResponse(
            {
                "data": {
                    "total": 6001,
                    "diff": [
                        {
                            "f12": "000858",
                            "f14": "五粮液",
                            "f100": "酿酒行业",
                            "f17": 130.0,
                            "f2": 132.0,
                            "f15": 133.0,
                            "f16": 129.0,
                            "f6": 900000000.0,
                            "f10": 2.1,
                        }
                    ],
                }
            }
        )

    monkeypatch.setattr(market_adapters.requests, "get", _fake_get)
    monkeypatch.setattr(market_adapters.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(market_adapters.random, "uniform", lambda _low, _high: 0.0)

    rows = market_adapters._call_eastmoney_stock_zh_a_spot_batch()

    assert len(rows) == 2
    assert rows[0]["代码"] == "600519"
    assert rows[0]["行业"] == "酿酒行业"
    assert rows[1]["代码"] == "000858"
    assert page1_calls == 3
    assert page2_calls == 1
    assert len(set(page1_hosts[:2])) == 2


@pytest.mark.integration
def test_sel13_market_adapter_batch_fails_after_retry_budget_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from claw_trade.data_gateway.providers import market_adapters

    def _always_fail(url: str, *, params: dict[str, str], headers: dict[str, str], timeout: int):
        del url, params, headers, timeout
        raise requests.exceptions.ConnectionError("Remote end closed connection without response")

    monkeypatch.setattr(market_adapters.requests, "get", _always_fail)
    monkeypatch.setattr(market_adapters.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(market_adapters.random, "uniform", lambda _low, _high: 0.0)

    with pytest.raises(requests.exceptions.ConnectionError) as excinfo:
        market_adapters._eastmoney_stock_zh_a_spot_page(page=1)
    assert "page=1" in str(excinfo.value)
    assert "host=" in str(excinfo.value)


@pytest.mark.integration
def test_sel13_fetch_selection_batch_from_data_gateway_maps_cn_a_rows(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    plan = _plan(run_id="sel13-fetch-ok", trade_date="2026-05-26")
    raw_store, normalized_store, attempt_store, http_store = _install_fake_evidence_runtime(monkeypatch)

    _patch_tushare_selection_batch(
        monkeypatch,
        rows=(
            _spot_row(
                code="600519",
                name="贵州茅台",
                industry="酿酒行业",
                open_price=1600.0,
                close_price=1612.0,
                amount=3000000000.0,
                vol_ratio=1.8,
            ),
            _spot_row(
                code="000858",
                name="五粮液",
                industry="酿酒行业",
                open_price=130.0,
                close_price=132.0,
                amount=900000000.0,
                vol_ratio=2.1,
            ),
        ),
    )
    monkeypatch.setattr(
        "claw_trade.data_gateway.providers.market_adapters._call_eastmoney_stock_zh_a_spot_batch",
        lambda: (_ for _ in ()).throw(AssertionError("eastmoney must not be called when tushare candidate succeeded")),
    )

    result = fetch_selection_batch_from_data_gateway(plan, evidence_root=tmp_path / "provider-batch")

    assert result.provider_batch_plan.plan_id == plan.provider_batch_plan_ref
    assert result.rows[0]["ticker"] == "000858.SZ"
    assert result.rows[1]["ticker"] == "600519.SH"
    assert result.rows[0]["source_ref"].startswith(f"normalized://mongo/{OPENBB_NORMALIZED}/")
    assert result.attempt_refs and result.attempt_refs[0].startswith(f"attempt://mongo/{OPENBB_PROVIDER_ATTEMPTS}/")
    assert any(ref.startswith("viking://resources/workflow/sel13-fetch-ok/") for ref in result.attempt_refs)
    assert result.normalized_refs and result.normalized_refs[0].startswith(f"normalized://mongo/{OPENBB_NORMALIZED}/")
    assert raw_store.refs and raw_store.refs[0].startswith(f"mongo://{OPENBB_RAW_PAYLOADS}/")
    assert normalized_store.refs and normalized_store.refs[0].startswith(f"mongo://{OPENBB_NORMALIZED}/")
    assert attempt_store.attempt_ids
    assert not http_store.evidence_ids
    first_attempt = attempt_store.get(attempt_store.attempt_ids[0])
    assert first_attempt is not None
    assert not (
        first_attempt.source_metadata
        and first_attempt.source_metadata.get("http_evidence_expected") is True
    )
    assert result.data_gaps == ()
    assert not any(
        "project.cn_a.mootdx_selection_batch:stock_zh_a_spot_mootdx_batch" in ref
        or "project.cn_a.tencent_selection_batch:stock_zh_a_spot_tencent_batch" in ref
        or "project.cn_a.sina_selection_batch:stock_zh_a_spot_sina_batch" in ref
        for ref in result.attempt_refs
        if ref.startswith("attempt://mongo/")
    )


@pytest.mark.integration
def test_sel13_fetch_selection_batch_uses_akshare_batch_when_eastmoney_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from claw_trade.data_gateway import selection_batch as selection_batch_module

    plan = _plan(run_id="sel13-fetch-akshare-legacy-path", trade_date="2026-05-26")
    _install_fake_evidence_runtime(monkeypatch)
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    monkeypatch.setattr(
        selection_batch_module,
        "_load_enabled_selection_user_manifests",
        lambda: selection_batch_module._SelectionManifestLoadState(manifests=()),
    )
    monkeypatch.setattr(
        "claw_trade.data_gateway.providers.market_adapters._call_eastmoney_stock_zh_a_spot_batch",
        lambda: (_ for _ in ()).throw(RuntimeError("eastmoney temporary unavailable")),
    )
    _patch_akshare_selection_batch(
        monkeypatch,
        rows=(
            _spot_row(
                code="600519",
                name="贵州茅台",
                industry="酿酒行业",
                open_price=1600.0,
                close_price=1612.0,
                amount=3000000000.0,
                vol_ratio=1.8,
            ),
            _spot_row(
                code="000858",
                name="五粮液",
                industry="酿酒行业",
                open_price=130.0,
                close_price=132.0,
                amount=900000000.0,
                vol_ratio=2.1,
            ),
        ),
    )

    result = fetch_selection_batch_from_data_gateway(plan, evidence_root=tmp_path / "provider-batch")

    assert len(result.rows) == 2
    assert result.rows[0]["ticker"] == "000858.SZ"
    assert result.rows[1]["ticker"] == "600519.SH"
    assert any("selection_batch_remote_error" == gap.gap_code for gap in result.data_gaps)
    assert any(ref.startswith("attempt://mongo/") for ref in result.attempt_refs)


@pytest.mark.integration
def test_sel13_selection_layer_only_delegates_to_data_gateway_facade(monkeypatch: pytest.MonkeyPatch) -> None:
    from claw_trade.selection import provider_batch as selection_provider_batch

    plan = _plan(run_id="sel13-selection-delegate", trade_date="2026-05-26")
    expected = SelectionProviderBatchResult(
        provider_batch_plan=build_selection_provider_batch_plan(
            market=SelectionMarket.CN_A,
            profile=SelectionProfile.CN_A,
            trade_date="2026-05-26",
            plan_id=plan.provider_batch_plan_ref,
        ),
        attempt_refs=("attempt://mongo/openbb_provider_attempts/delegate",),
        normalized_refs=("normalized://mongo/openbb_normalized/delegate",),
        rows=(
            {
                "ticker": "600519.SH",
                "company_name": "贵州茅台",
                "industry": "酿酒行业",
                "open": 1600.0,
                "close": 1612.0,
                "high": 1620.0,
                "low": 1598.0,
                "amount": 3000000000.0,
                "vol_ratio": 1.8,
                "source_ref": "normalized://mongo/openbb_normalized/delegate",
            },
        ),
        data_gaps=(),
    )

    called = {"count": 0}

    def _fake_gateway_fetch(run_plan: SelectionRunPlan, *, evidence_root: Path | None = None) -> SelectionProviderBatchResult:
        del evidence_root
        called["count"] += 1
        assert run_plan.selection_run_id == plan.selection_run_id
        return expected

    monkeypatch.setattr(selection_provider_batch, "_fetch_selection_batch_from_data_gateway", _fake_gateway_fetch)
    actual = fetch_selection_batch_from_data_gateway(plan)
    assert called["count"] == 1
    assert actual is expected


@pytest.mark.integration
def test_sel13_fetch_selection_batch_from_data_gateway_converts_tushare_amount_unit_to_cny(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plan = _plan(run_id="sel13-fetch-tushare-amount-unit", trade_date="2026-05-26")
    _install_fake_evidence_runtime(monkeypatch)
    _patch_tushare_selection_batch(
        monkeypatch,
        rows=(
            _spot_row(
                code="600519",
                name="贵州茅台",
                industry="酿酒行业",
                open_price=1600.0,
                close_price=1612.0,
                amount=250000.0,
                vol_ratio=1.8,
            ),
        ),
    )

    result = fetch_selection_batch_from_data_gateway(plan, evidence_root=tmp_path / "provider-batch")

    assert len(result.rows) == 1
    assert result.rows[0]["ticker"] == "600519.SH"
    assert result.rows[0]["amount"] == pytest.approx(250000000.0)


@pytest.mark.integration
def test_sel13_fetch_selection_batch_from_data_gateway_keeps_eastmoney_amount_in_cny_without_double_convert(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plan = _plan(run_id="sel13-fetch-eastmoney-amount-unit", trade_date="2026-05-26")
    _install_fake_evidence_runtime(monkeypatch)
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    monkeypatch.setattr(
        "claw_trade.data_gateway.providers.market_adapters._call_eastmoney_stock_zh_a_spot_batch",
        lambda: (
            _spot_row(
                code="000858",
                name="五粮液",
                industry="酿酒行业",
                open_price=130.0,
                close_price=132.0,
                amount=250000000.0,
                vol_ratio=2.1,
            ),
        ),
    )

    result = fetch_selection_batch_from_data_gateway(plan, evidence_root=tmp_path / "provider-batch")

    assert len(result.rows) == 1
    assert result.rows[0]["ticker"] == "000858.SZ"
    assert result.rows[0]["amount"] == pytest.approx(250000000.0)


@pytest.mark.integration
def test_sel13_fetch_selection_batch_from_data_gateway_records_gap_for_missing_fields_and_allows_later_source_rows(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plan = _plan(run_id="sel13-fetch-missing-fields", trade_date="2026-05-26")
    _, _, attempt_store, _ = _install_fake_evidence_runtime(monkeypatch)

    _patch_tushare_selection_batch(
        monkeypatch,
        rows=(
            _spot_row_without_open(
                code="600519",
                name="贵州茅台",
                industry="",
                close_price=1612.0,
                amount=3000000000.0,
                vol_ratio=1.8,
            ),
        ),
    )
    monkeypatch.setattr(
        "claw_trade.data_gateway.providers.market_adapters._call_eastmoney_stock_zh_a_spot_batch",
        lambda: (
            _spot_row(
                code="000858",
                name="五粮液",
                industry="酿酒行业",
                open_price=130.0,
                close_price=132.0,
                amount=250000000.0,
                vol_ratio=2.1,
            ),
        ),
    )

    result = fetch_selection_batch_from_data_gateway(plan, evidence_root=tmp_path / "provider-batch")

    assert len(result.rows) == 1
    assert result.rows[0]["ticker"] == "000858.SZ"
    assert result.normalized_refs and result.normalized_refs[0].startswith(f"normalized://mongo/{OPENBB_NORMALIZED}/")
    assert result.data_gaps
    assert any(gap.gap_code == "selection_batch_rows_dropped" for gap in result.data_gaps)
    assert result.attempt_refs and result.attempt_refs[0].startswith(f"attempt://mongo/{OPENBB_PROVIDER_ATTEMPTS}/")
    assert any(ref.startswith("viking://resources/workflow/sel13-fetch-missing-fields/") for ref in result.attempt_refs)
    assert len(attempt_store.attempt_ids) >= 2


@pytest.mark.integration
def test_sel13_fetch_selection_batch_from_data_gateway_keeps_valid_rows_when_partial_fields_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plan = _plan(run_id="sel13-fetch-partial-fields", trade_date="2026-05-26")
    _install_fake_evidence_runtime(monkeypatch)
    _patch_tushare_selection_batch(
        monkeypatch,
        rows=(
            _spot_row(
                code="600519",
                name="贵州茅台",
                industry="酿酒行业",
                open_price=1600.0,
                close_price=1612.0,
                amount=3000000000.0,
                vol_ratio=1.8,
            ),
            _spot_row_without_open(
                code="000858",
                name="五粮液",
                industry="",
                close_price=132.0,
                amount=900000000.0,
                vol_ratio=2.1,
            ),
        ),
    )

    result = fetch_selection_batch_from_data_gateway(plan, evidence_root=tmp_path / "provider-batch")

    assert len(result.rows) == 1
    assert result.rows[0]["ticker"] == "600519.SH"
    assert any(gap.gap_code == "selection_batch_rows_dropped" for gap in result.data_gaps)


@pytest.mark.integration
def test_sel13_fetch_selection_batch_remote_success_but_evidence_chain_failed_is_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plan = _plan(run_id="sel13-fetch-readback-failed", trade_date="2026-05-26")
    _, normalized_store, attempt_store, _ = _install_fake_evidence_runtime(monkeypatch)
    monkeypatch.setattr(normalized_store, "read", lambda _normalized_ref: (_ for _ in ()).throw(ValueError("normalized missing")))

    _patch_tushare_selection_batch(
        monkeypatch,
        rows=(
            _spot_row(
                code="600519",
                name="贵州茅台",
                industry="酿酒行业",
                open_price=1600.0,
                close_price=1612.0,
                amount=3000000000.0,
                vol_ratio=1.8,
            ),
        ),
    )
    monkeypatch.setattr(
        "claw_trade.data_gateway.providers.market_adapters._call_eastmoney_stock_zh_a_spot_batch",
        lambda: (_ for _ in ()).throw(RuntimeError("eastmoney intentionally disabled in test")),
    )
    monkeypatch.setattr(
        "claw_trade.data_gateway.providers.market_adapters._call_akshare_stock_zh_a_spot_batch",
        lambda: (_ for _ in ()).throw(RuntimeError("akshare intentionally disabled in test")),
    )
    monkeypatch.setattr(
        "claw_trade.data_gateway.providers.market_adapters._call_mootdx_stock_zh_a_spot_batch",
        lambda *, trade_date: (_ for _ in ()).throw(RuntimeError("mootdx intentionally disabled in test")),
    )
    monkeypatch.setattr(
        "claw_trade.data_gateway.providers.market_adapters._call_tencent_stock_zh_a_spot_batch",
        lambda *, trade_date: (_ for _ in ()).throw(RuntimeError("tencent intentionally disabled in test")),
    )
    monkeypatch.setattr(
        "claw_trade.data_gateway.providers.market_adapters._call_sina_stock_zh_a_spot_batch_with_chunk_metadata",
        lambda *, trade_date: (_ for _ in ()).throw(RuntimeError("sina intentionally disabled in test")),
    )
    monkeypatch.setattr(
        "claw_trade.data_gateway.providers.market_adapters._call_baostock_stock_zh_a_daily_batch",
        lambda *, trade_date: (_ for _ in ()).throw(RuntimeError("baostock intentionally disabled in test")),
    )

    result = fetch_selection_batch_from_data_gateway(plan, evidence_root=tmp_path / "provider-batch")

    # Contract test only: fake store/backend proves fail-closed branch; not runtime/live evidence.
    assert result.rows == ()
    assert any(
        gap.gap_code == "selection_batch_evidence_readback_failed"
        and gap.severity == DataGapSeverity.BLOCKER
        for gap in result.data_gaps
    )
    assert result.attempt_refs
    assert any(ref.startswith("attempt://mongo/") for ref in result.attempt_refs)
    assert any(ref.startswith("viking://resources/workflow/sel13-fetch-readback-failed/") for ref in result.attempt_refs)
    assert attempt_store.attempt_ids


@pytest.mark.integration
def test_sel13_fetch_selection_batch_without_configured_source_returns_gap_not_fake_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from claw_trade.data_gateway import selection_batch as selection_batch_module

    plan = _plan(run_id="sel13-fetch-no-configured-source", trade_date="2026-05-26")
    _install_fake_evidence_runtime(monkeypatch)
    monkeypatch.setattr(selection_batch_module, "_selection_batch_capability_candidates", lambda *, registry: ())

    result = fetch_selection_batch_from_data_gateway(plan, evidence_root=tmp_path / "provider-batch")

    assert result.rows == ()
    assert result.attempt_refs
    assert any(gap.gap_code == "selection_batch_source_not_configured" for gap in result.data_gaps)
    assert any("未配置" in gap.reader_message or "配置" in gap.reader_message for gap in result.data_gaps)


@pytest.mark.integration
def test_sel13_partial_cn_a_batch_sources_execute_only_after_complete_sources_fail(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plan = _plan(run_id="sel13-fetch-partial-sources-after-complete-fail", trade_date="2026-05-26")
    _raw_store, _normalized_store, _attempt_store, _http_store = _install_fake_evidence_runtime(monkeypatch)
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    monkeypatch.setattr(
        "claw_trade.data_gateway.providers.market_adapters._call_mootdx_stock_zh_a_spot_batch",
        lambda *, trade_date: (
            {
                "代码": "600519",
                "名称": "贵州茅台",
                "今开": 1600.0,
                "最新价": 1612.0,
                "最高": 1620.0,
                "最低": 1598.0,
                "成交额": 3000000000.0,
                "成交量": 1200000.0,
                "trade_date": trade_date,
            },
        ),
    )
    monkeypatch.setattr(
        "claw_trade.data_gateway.providers.market_adapters._call_eastmoney_stock_zh_a_spot_batch",
        lambda: (_ for _ in ()).throw(RuntimeError("eastmoney temporary unavailable")),
    )
    monkeypatch.setattr(
        "claw_trade.data_gateway.providers.market_adapters._call_akshare_stock_zh_a_spot_batch",
        lambda: (_ for _ in ()).throw(RuntimeError("akshare temporary unavailable")),
    )

    result = fetch_selection_batch_from_data_gateway(plan, evidence_root=tmp_path / "provider-batch")

    assert result.rows == ()
    assert result.attempt_refs
    assert any(
        "project.cn_a.eastmoney_selection_batch:stock_zh_a_spot_em_batch" in ref
        or "project.cn_a.akshare_selection_batch:stock_zh_a_spot_em_batch" in ref
        for ref in result.attempt_refs
        if ref.startswith("attempt://mongo/")
    )
    assert any(
        "project.cn_a.mootdx_selection_batch:stock_zh_a_spot_mootdx_batch" in ref
        for ref in result.attempt_refs
        if ref.startswith("attempt://mongo/")
    )
    mongo_attempt_refs = [ref for ref in result.attempt_refs if ref.startswith("attempt://mongo/")]
    first_partial_index = min(
        index
        for index, ref in enumerate(mongo_attempt_refs)
        if "project.cn_a.mootdx_selection_batch:stock_zh_a_spot_mootdx_batch" in ref
    )
    last_complete_index = max(
        index
        for index, ref in enumerate(mongo_attempt_refs)
        if "project.cn_a.eastmoney_selection_batch:stock_zh_a_spot_em_batch" in ref
        or "project.cn_a.akshare_selection_batch:stock_zh_a_spot_em_batch" in ref
    )
    assert first_partial_index > last_complete_index
    remote_error_gaps = [gap for gap in result.data_gaps if gap.gap_code == "selection_batch_remote_error"]
    assert remote_error_gaps
    assert any(gap.severity == DataGapSeverity.BLOCKER for gap in remote_error_gaps)
    assert any(
        gap.gap_code == "selection_batch_strategy_fields_missing"
        for gap in result.data_gaps
    )


@pytest.mark.integration
def test_sel13_remote_success_missing_strategy_variant_fields_skips_provider_rows(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plan = _plan(run_id="sel13-fetch-missing-strategy-variant-fields-skips-provider", trade_date="2026-05-26")
    _install_fake_evidence_runtime(monkeypatch)
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    monkeypatch.setattr(
        "claw_trade.data_gateway.providers.market_adapters._call_eastmoney_stock_zh_a_spot_batch",
        lambda: (_ for _ in ()).throw(RuntimeError("eastmoney temporary unavailable")),
    )
    monkeypatch.setattr(
        "claw_trade.data_gateway.providers.market_adapters._call_akshare_stock_zh_a_spot_batch",
        lambda: (_ for _ in ()).throw(RuntimeError("akshare temporary unavailable")),
    )
    def _mootdx_current_only(*, trade_date):
        row = _spot_row_without_vol_ratio(
                code="600519",
                name="贵州茅台",
                industry="酿酒行业",
                open_price=1600.0,
                close_price=1612.0,
                amount=360000000.0,
            )
        row.pop("history", None)
        row.pop("private_placement_event_date", None)
        row.pop("private_placement_days_since", None)
        return (row | {"trade_date": trade_date},)

    monkeypatch.setattr(
        "claw_trade.data_gateway.providers.market_adapters._call_mootdx_stock_zh_a_spot_batch",
        _mootdx_current_only,
    )
    monkeypatch.setattr(
        "claw_trade.data_gateway.providers.market_adapters._call_tencent_stock_zh_a_spot_batch",
        lambda *, trade_date: (_ for _ in ()).throw(RuntimeError("tencent intentionally unavailable")),
    )
    monkeypatch.setattr(
        "claw_trade.data_gateway.providers.market_adapters._call_sina_stock_zh_a_spot_batch",
        lambda *, trade_date: (_ for _ in ()).throw(RuntimeError("sina intentionally unavailable")),
    )
    monkeypatch.setattr(
        "claw_trade.data_gateway.providers.market_adapters._call_baostock_stock_zh_a_daily_batch",
        lambda *, trade_date: (_ for _ in ()).throw(RuntimeError("baostock intentionally unavailable")),
    )

    result = fetch_selection_batch_from_data_gateway(plan, evidence_root=tmp_path / "provider-batch")

    assert result.rows == ()
    strategy_gap = next(gap for gap in result.data_gaps if gap.gap_code == "selection_batch_strategy_fields_missing")
    assert strategy_gap.severity == DataGapSeverity.WARN
    assert "history" in strategy_gap.reader_message


@pytest.mark.integration
def test_sel13_remote_success_missing_strategy_fields_warns_and_continues_to_later_source(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plan = _plan(run_id="sel13-fetch-missing-strategy-fields-continue", trade_date="2026-05-26")
    _install_fake_evidence_runtime(monkeypatch)
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    monkeypatch.setattr(
        "claw_trade.data_gateway.providers.market_adapters._call_eastmoney_stock_zh_a_spot_batch",
        lambda: (_ for _ in ()).throw(RuntimeError("eastmoney temporary unavailable")),
    )
    monkeypatch.setattr(
        "claw_trade.data_gateway.providers.market_adapters._call_akshare_stock_zh_a_spot_batch",
        lambda: (_ for _ in ()).throw(RuntimeError("akshare temporary unavailable")),
    )
    monkeypatch.setattr(
        "claw_trade.data_gateway.providers.market_adapters._call_mootdx_stock_zh_a_spot_batch",
        lambda *, trade_date: (
            _spot_row_without_amount(
                code="600519",
                name="贵州茅台",
                industry="酿酒行业",
                open_price=1600.0,
                close_price=1612.0,
                vol_ratio=1.8,
            )
            | {"trade_date": trade_date},
        ),
    )
    monkeypatch.setattr(
        "claw_trade.data_gateway.providers.market_adapters._call_tencent_stock_zh_a_spot_batch",
        lambda *, trade_date: (
            _spot_row(
                code="000858",
                name="五粮液",
                industry="酿酒行业",
                open_price=130.0,
                close_price=132.0,
                amount=250000000.0,
                vol_ratio=2.1,
            )
            | {"trade_date": trade_date},
        ),
    )

    result = fetch_selection_batch_from_data_gateway(plan, evidence_root=tmp_path / "provider-batch")

    assert len(result.rows) == 1
    assert result.rows[0]["ticker"] == "000858.SZ"
    assert result.rows[0]["amount"] == pytest.approx(250000000.0)
    assert result.attempt_refs and result.normalized_refs
    strategy_gap = next(gap for gap in result.data_gaps if gap.gap_code == "selection_batch_strategy_fields_missing")
    assert strategy_gap.severity == DataGapSeverity.WARN
    assert "amount" in strategy_gap.reader_message
    mongo_attempt_refs = [ref for ref in result.attempt_refs if ref.startswith("attempt://mongo/")]
    mootdx_attempt_index = next(
        index
        for index, ref in enumerate(mongo_attempt_refs)
        if "project.cn_a.mootdx_selection_batch:stock_zh_a_spot_mootdx_batch" in ref
    )
    tencent_attempt_index = next(
        index
        for index, ref in enumerate(mongo_attempt_refs)
        if "project.cn_a.tencent_selection_batch:stock_zh_a_spot_tencent_batch" in ref
    )
    assert tencent_attempt_index > mootdx_attempt_index


@pytest.mark.integration
def test_sel13_eastmoney_batch_fetch_uses_full_universe_page_size(monkeypatch: pytest.MonkeyPatch) -> None:
    from claw_trade.data_gateway.providers import market_adapters as market_adapters_module

    calls: list[dict[str, object]] = []

    class _Response:
        def __init__(self, *, page: int) -> None:
            self._page = page

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {
                "data": {
                    "total": 6001,
                    "diff": [
                        {
                            "f12": f"600{self._page:03d}",
                            "f14": f"样本{self._page}",
                            "f100": "样本行业",
                            "f17": 10.0,
                            "f2": 10.5,
                            "f15": 10.8,
                            "f16": 9.8,
                            "f6": 300000000.0,
                            "f5": 1000000.0,
                            "f10": 1.2,
                        }
                    ],
                }
            }

    def _get(url: str, *, params, headers, timeout):
        del url, headers, timeout
        captured = dict(params)
        calls.append(captured)
        return _Response(page=int(captured["pn"]))

    monkeypatch.setattr(market_adapters_module.requests, "get", _get)
    monkeypatch.setattr(market_adapters_module, "_eastmoney_spot_hosts_for_retry", lambda: ("eastmoney.test",))
    monkeypatch.setattr(market_adapters_module, "_wait_eastmoney_page_interval", lambda _last_request_monotonic: None)

    rows = market_adapters_module._call_eastmoney_stock_zh_a_spot_batch()

    assert len(rows) == 2
    assert [call["pn"] for call in calls] == ["1", "2"]
    assert {call["pz"] for call in calls} == {"5000"}


@pytest.mark.integration
def test_sel13_sina_partial_chunk_error_metadata_is_structured_in_attempt_gap_and_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from claw_trade.data_gateway.providers import market_adapters as market_adapters_module

    plan = _plan(run_id="sel13-sina-partial-chunk-metadata", trade_date="2026-05-26")
    raw_store, _normalized_store, attempt_store, _http_store = _install_fake_evidence_runtime(monkeypatch)
    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    monkeypatch.setattr(
        "claw_trade.data_gateway.providers.market_adapters._call_eastmoney_stock_zh_a_spot_batch",
        lambda: (_ for _ in ()).throw(RuntimeError("eastmoney temporary unavailable")),
    )
    monkeypatch.setattr(
        "claw_trade.data_gateway.providers.market_adapters._call_akshare_stock_zh_a_spot_batch",
        lambda: (_ for _ in ()).throw(RuntimeError("akshare temporary unavailable")),
    )
    monkeypatch.setattr(
        "claw_trade.data_gateway.providers.market_adapters._call_mootdx_stock_zh_a_spot_batch",
        lambda *, trade_date: (_ for _ in ()).throw(RuntimeError("mootdx temporary unavailable")),
    )
    monkeypatch.setattr(
        "claw_trade.data_gateway.providers.market_adapters._call_tencent_stock_zh_a_spot_batch",
        lambda *, trade_date: (_ for _ in ()).throw(RuntimeError("tencent temporary unavailable")),
    )

    def _fake_sina_outcome(*, trade_date: str):  # noqa: ARG001
        return market_adapters_module._ChunkedBatchOutcome(
            rows=(
                {
                    "代码": "600519",
                    "名称": "贵州茅台",
                    "今开": 1600.0,
                    "最新价": 1612.0,
                    "最高": 1620.0,
                    "最低": 1598.0,
                    "成交额": 3000000000.0,
                    "trade_date": "2026-05-26",
                },
            ),
            partial_chunk_error_count=1,
            partial_chunk_errors=("sina quote parse failed for symbols=sz019525,sz019528",),
            failed_chunk_symbols_sample=("sz019525,sz019528",),
        )

    monkeypatch.setattr(
        "claw_trade.data_gateway.providers.market_adapters._call_sina_stock_zh_a_spot_batch_with_chunk_metadata",
        _fake_sina_outcome,
    )
    monkeypatch.setattr(
        "claw_trade.data_gateway.providers.market_adapters._call_baostock_stock_zh_a_daily_batch",
        lambda *, trade_date: (_ for _ in ()).throw(RuntimeError("baostock temporary unavailable")),
    )

    result = fetch_selection_batch_from_data_gateway(plan, evidence_root=tmp_path / "provider-batch")

    assert result.rows == ()
    assert any(
        gap.gap_code == "selection_batch_strategy_fields_missing"
        for gap in result.data_gaps
    )
    sina_attempt = next(item for item in attempt_store._items.values() if item.provider == "sina_selection_batch")  # noqa: SLF001
    assert sina_attempt.source_metadata is not None
    assert sina_attempt.source_metadata["partial_chunk_error_count"] == 1
    assert sina_attempt.source_metadata["partial_chunk_errors"]
    assert sina_attempt.source_metadata["failed_chunk_symbols_sample"]
    openviking_ref = next(ref for ref in result.attempt_refs if ref.startswith("viking://resources/workflow/"))
    backend = raw_store.openviking_backend  # type: ignore[attr-defined]  # noqa: SLF001
    evidence_payload = backend.fetch_content_by_uri(openviking_ref).decode("utf-8")
    evidence_data = json.loads(evidence_payload)
    sina_entry = evidence_data["provider_source_matrix"]["sina_selection_batch:stock_zh_a_spot_sina_batch"]
    assert sina_entry["partial_chunk_error_count"] == 1
    assert sina_entry["partial_chunk_errors"]
    assert sina_entry["failed_chunk_symbols_sample"]


@pytest.mark.integration
def test_sel13_selection_batch_plan_without_tushare_token_does_not_prioritize_tushare(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from claw_trade.data_gateway import selection_batch as selection_batch_module

    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    monkeypatch.setattr(
        selection_batch_module,
        "_load_enabled_selection_user_manifests",
        lambda: selection_batch_module._SelectionManifestLoadState(manifests=()),
    )

    plan = build_selection_provider_batch_plan(
        market=SelectionMarket.CN_A,
        profile=SelectionProfile.CN_A,
        trade_date="2026-05-26",
    )

    assert plan.provider_candidates
    assert plan.provider_candidates[0] != "tushare_selection_batch"


@pytest.mark.integration
def test_sel13_selection_batch_plan_provider_candidates_match_runtime_execution_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from claw_trade.data_gateway import selection_batch as selection_batch_module

    monkeypatch.delenv("TUSHARE_TOKEN", raising=False)
    monkeypatch.setattr(
        selection_batch_module,
        "_load_enabled_selection_user_manifests",
        lambda: selection_batch_module._SelectionManifestLoadState(manifests=()),
    )
    plan = build_selection_provider_batch_plan(
        market=SelectionMarket.CN_A,
        profile=SelectionProfile.CN_A,
        trade_date="2026-05-26",
    )
    registry, _, _ = selection_batch_module._build_selection_provider_registry_context()
    ordered = selection_batch_module._selection_batch_ordered_candidates(
        selection_batch_module._selection_batch_capability_candidates(registry=registry)
    )
    expected_candidates = tuple(
        dict.fromkeys(item.capability.provider for item in ordered if item.candidate_type != "unsupported")
    )
    assert plan.provider_candidates == expected_candidates
    assert ordered[0].candidate_type == "complete_batch_candidate"
    partial_providers = {
        item.capability.provider
        for item in ordered
        if item.candidate_type == "partial_batch_candidate"
    }
    assert plan.provider_candidates[0] not in partial_providers


@pytest.mark.integration
def test_sel13_selection_batch_plan_with_tushare_token_uses_registry_user_preferred(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from claw_trade.data_gateway import selection_batch as selection_batch_module

    monkeypatch.setenv("TUSHARE_TOKEN", "sel13-test-token")
    monkeypatch.setattr(
        selection_batch_module,
        "_load_enabled_selection_user_manifests",
        lambda: selection_batch_module._SelectionManifestLoadState(manifests=()),
    )

    registry, _, _ = selection_batch_module._build_selection_provider_registry_context()
    ordered = selection_batch_module._selection_batch_capabilities_catalog(registry=registry)
    tushare_capability = next(item for item in ordered if item.provider == "tushare_selection_batch")

    assert ordered[0].provider == "tushare_selection_batch"
    assert tushare_capability.priority_source == PrioritySource.USER_PREFERRED


@pytest.mark.integration
def test_sel13_selection_batch_plan_with_tushare_token_keeps_complete_group_ahead_of_partial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from claw_trade.data_gateway import selection_batch as selection_batch_module

    monkeypatch.setenv("TUSHARE_TOKEN", "sel13-test-token")
    monkeypatch.setattr(
        selection_batch_module,
        "_load_enabled_selection_user_manifests",
        lambda: selection_batch_module._SelectionManifestLoadState(manifests=()),
    )
    plan = build_selection_provider_batch_plan(
        market=SelectionMarket.CN_A,
        profile=SelectionProfile.CN_A,
        trade_date="2026-05-26",
    )
    registry, _, _ = selection_batch_module._build_selection_provider_registry_context()
    ordered = selection_batch_module._selection_batch_ordered_candidates(
        selection_batch_module._selection_batch_capability_candidates(registry=registry)
    )
    complete_providers = [
        item.capability.provider
        for item in ordered
        if item.candidate_type == "complete_batch_candidate"
    ]
    partial_providers = [
        item.capability.provider
        for item in ordered
        if item.candidate_type == "partial_batch_candidate"
    ]
    complete_indexes = [plan.provider_candidates.index(provider) for provider in dict.fromkeys(complete_providers)]
    partial_indexes = [plan.provider_candidates.index(provider) for provider in dict.fromkeys(partial_providers)]
    assert plan.provider_candidates[0] == "tushare_selection_batch"
    assert max(complete_indexes) < min(partial_indexes)


@pytest.mark.integration
def test_sel13_selection_batch_plan_uses_registry_order_with_enabled_user_manifest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from claw_trade.data_gateway import selection_batch as selection_batch_module

    monkeypatch.setenv("TUSHARE_TOKEN", "sel13-test-token")
    manifest = _enabled_selection_manifest(
        provider_id="user_batch_provider",
        adapter_id="user.cn_a.selection_batch",
        priority=5,
        priority_source=PrioritySource.USER_PREFERRED,
    )
    monkeypatch.setattr(
        selection_batch_module,
        "_load_enabled_selection_user_manifests",
        lambda: selection_batch_module._SelectionManifestLoadState(manifests=(manifest,)),
    )

    plan = build_selection_provider_batch_plan(
        market=SelectionMarket.CN_A,
        profile=SelectionProfile.CN_A,
        trade_date="2026-05-26",
    )
    registry, _, _ = selection_batch_module._build_selection_provider_registry_context()
    ordered = selection_batch_module._selection_batch_ordered_candidates(
        selection_batch_module._selection_batch_capability_candidates(registry=registry)
    )
    expected_candidates = tuple(
        dict.fromkeys(
            item.capability.provider
            for item in ordered
            if item.candidate_type != "unsupported"
        )
    )

    assert plan.provider_candidates == expected_candidates
    assert "user_batch_provider" in plan.provider_candidates
    assert plan.provider_candidates.index("user_batch_provider") < plan.provider_candidates.index("eastmoney_selection_batch")


@pytest.mark.integration
def test_sel13_build_selection_batch_plan_manifest_load_error_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from claw_trade.data_gateway import selection_batch as selection_batch_module

    monkeypatch.setattr(
        selection_batch_module,
        "_load_enabled_selection_user_manifests",
        lambda: selection_batch_module._SelectionManifestLoadState(
            manifests=(),
            error="selection manifest load failed: DataGatewayError: provider manifest decode failed: _id=user.bad.manifest",
        ),
    )

    with pytest.raises(RuntimeError, match="selection batch manifest load failed"):
        build_selection_provider_batch_plan(
            market=SelectionMarket.CN_A,
            profile=SelectionProfile.CN_A,
            trade_date="2026-05-26",
        )


@pytest.mark.integration
def test_sel13_enabled_user_manifest_without_adapter_records_gap_and_attempt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from claw_trade.data_gateway import selection_batch as selection_batch_module

    plan = _plan(run_id="sel13-fetch-user-manifest-adapter-missing", trade_date="2026-05-26")
    _install_fake_evidence_runtime(monkeypatch)
    manifest = _enabled_selection_manifest(
        provider_id="user_batch_provider",
        adapter_id="user.cn_a.selection_batch_missing_adapter",
        priority=1,
        priority_source=PrioritySource.USER_PREFERRED,
    )
    monkeypatch.setattr(selection_batch_module, "cn_a_selection_batch_capabilities", lambda: ())
    monkeypatch.setattr(
        selection_batch_module,
        "_load_enabled_selection_user_manifests",
        lambda: selection_batch_module._SelectionManifestLoadState(manifests=(manifest,)),
    )

    result = fetch_selection_batch_from_data_gateway(plan, evidence_root=tmp_path / "provider-batch")

    assert result.rows == ()
    assert result.attempt_refs
    assert any(ref.startswith(f"attempt://mongo/{OPENBB_PROVIDER_ATTEMPTS}/") for ref in result.attempt_refs)
    assert any(gap.gap_code == "selection_batch_source_not_configured" for gap in result.data_gaps)


@pytest.mark.integration
def test_sel13_manifest_load_failure_is_fail_closed_and_visible_gap(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from claw_trade.data_gateway import selection_batch as selection_batch_module

    plan = _plan(run_id="sel13-fetch-manifest-load-failed", trade_date="2026-05-26")
    _install_fake_evidence_runtime(monkeypatch)
    monkeypatch.setattr(
        selection_batch_module,
        "_load_enabled_selection_user_manifests",
        lambda: selection_batch_module._SelectionManifestLoadState(
            manifests=(),
            error=(
                "selection manifest load failed: DataGatewayError: "
                "provider manifest decode failed: _id=user.bad.manifest"
            ),
        ),
    )
    monkeypatch.setattr(
        "claw_trade.data_gateway.providers.market_adapters._call_tushare_stock_zh_a_spot_batch",
        lambda *, token, trade_date, env=None: (_ for _ in ()).throw(
            AssertionError("manifest load failure should fail closed before remote provider call")
        ),
    )

    result = fetch_selection_batch_from_data_gateway(plan, evidence_root=tmp_path / "provider-batch")

    assert result.rows == ()
    assert result.attempt_refs
    assert any(ref.startswith(f"attempt://mongo/{OPENBB_PROVIDER_ATTEMPTS}/") for ref in result.attempt_refs)
    assert any(ref.startswith("viking://resources/workflow/sel13-fetch-manifest-load-failed/") for ref in result.attempt_refs)
    assert any(gap.gap_code == "selection_batch_manifest_load_failed" for gap in result.data_gaps)


@pytest.mark.integration
def test_sel13_data_job_with_data_gateway_batch_can_persist_completed_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plan = _plan(run_id="sel13-data-job-success", trade_date="2026-05-26")
    _install_fake_evidence_runtime(monkeypatch)
    rows = tuple(
        _spot_row(
            code=f"{600000 + idx}",
            name=f"样本股票{idx + 1}",
            industry="样本行业",
            open_price=10.0 + idx * 0.1,
            close_price=10.2 + idx * 0.1,
            amount=250000000.0 + idx * 10000000.0,
            vol_ratio=1.6 + idx * 0.05,
        )
        for idx in range(20)
    )
    _patch_tushare_selection_batch(monkeypatch, rows=rows)

    store = SelectionRunStore(persisted_runs_dir=tmp_path / "store" / "data-runs")
    job = SelectionDataJob(
        store=store,
        provider_fetch_batch=lambda run_plan: fetch_selection_batch_from_data_gateway(
            run_plan,
            evidence_root=tmp_path / "provider-batch",
        ),
        strategy_config_loader=load_cn_a_selection_v1_strategy,
        now_fn=lambda: datetime(2026, 5, 26, 10, 0, tzinfo=UTC),
        evidence_root=tmp_path / "selection-runs",
    )

    result = job.run(plan)

    assert result.record.data_run.status == SelectionDataRunStatus.COMPLETED
    assert len(result.top20_tickers) == 20
    assert (tmp_path / "store" / "data-runs" / f"{plan.selection_run_id}.json").exists()


@pytest.mark.integration
def test_sel13_data_job_with_rows_missing_vol_ratio_can_still_complete_when_amount_present(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    plan = _plan(run_id="sel13-data-job-no-vol-ratio", trade_date="2026-05-26")
    _install_fake_evidence_runtime(monkeypatch)
    rows = tuple(
        _spot_row_without_vol_ratio(
            code=f"{600300 + idx}",
            name=f"无量比样本{idx + 1}",
            industry="样本行业",
            open_price=20.0 + idx * 0.1,
            close_price=20.4 + idx * 0.1,
            amount=250000000.0 + idx * 8000000.0,
        )
        for idx in range(20)
    )
    _patch_tushare_selection_batch(monkeypatch, rows=rows)

    store = SelectionRunStore(persisted_runs_dir=tmp_path / "store" / "data-runs")
    job = SelectionDataJob(
        store=store,
        provider_fetch_batch=lambda run_plan: fetch_selection_batch_from_data_gateway(
            run_plan,
            evidence_root=tmp_path / "provider-batch",
        ),
        strategy_config_loader=load_cn_a_selection_v1_strategy,
        now_fn=lambda: datetime(2026, 5, 26, 10, 0, tzinfo=UTC),
        evidence_root=tmp_path / "selection-runs",
    )

    result = job.run(plan)

    assert result.record.data_run.status == SelectionDataRunStatus.COMPLETED
    assert len(result.top20_tickers) == 20
