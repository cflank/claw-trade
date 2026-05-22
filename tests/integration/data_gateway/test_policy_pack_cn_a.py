from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from claw_trade.data_gateway.models import FreshnessPolicy, Market, PackDomain, PackRequest, RunProviderPlan
from claw_trade.data_gateway.packs.service import DomainPackService
from claw_trade.data_gateway.providers.policy import build_default_policy_adapters
from claw_trade.data_gateway.store.attempts import MongoAttemptStore
from claw_trade.data_gateway.store.http_evidence import MongoProviderHttpEvidenceStore
from claw_trade.data_gateway.store.normalized import MongoNormalizedStore
from claw_trade.data_gateway.store.raw_payloads import MongoRawPayloadStore
from claw_trade.data_gateway.providers.execution import ProviderExecutionEvidenceHelper


class _Collection:
    def __init__(self, name: str = "test_collection") -> None:
        self.name = name
        self.docs: dict[str, dict[str, Any]] = {}

    def update_one(self, query: dict[str, Any], update: dict[str, Any], upsert: bool = False) -> None:
        del upsert
        key = query["_id"]
        current = self.docs.get(key, {})
        current.update(update.get("$setOnInsert", {}))
        current.update(update.get("$set", {}))
        if "_id" not in current:
            current["_id"] = key
        self.docs[key] = current

    def insert_one(self, doc: dict[str, Any]) -> None:
        self.docs[doc["_id"]] = dict(doc)


def _request() -> PackRequest:
    return PackRequest(
        run_id="run-policy-cn-a",
        call_id="call-policy-cn-a",
        worker_id="policy_analyst",
        market=Market.CN_A,
        domain=PackDomain.POLICY,
        ticker="600519.SH",
        company_name="贵州茅台",
        start_date="2026-05-01",
        end_date="2026-05-22",
        current_date="2026-05-22",
        currency="CNY",
        profile="CN_A",
        freshness_policy=FreshnessPolicy(max_age_seconds=300),
    )


def _plan(request: PackRequest, adapters) -> RunProviderPlan:
    specs = []
    for adapter in adapters:
        specs.extend(adapter.build_call_specs(request))
    ordered = tuple(sorted(specs, key=lambda item: (item.priority, item.adapter_id)))
    return RunProviderPlan(
        run_id=request.run_id,
        provider_config_version="cfg-policy-cn-a",
        market=request.market,
        ticker=request.ticker,
        domains=(PackDomain.POLICY,),
        call_specs=ordered,
        shared_call_keys=tuple(item.call_key for item in ordered),
        cache_keys=tuple(f"cache:{item.adapter_id}" for item in ordered),
        rate_limit_plan=(),
        initial_gaps=(),
        generated_at="2026-05-22T00:00:00+00:00",
        remote_prefetch_allowed=False,
    )


def _helper() -> ProviderExecutionEvidenceHelper:
    return ProviderExecutionEvidenceHelper(
        raw_store=MongoRawPayloadStore(_Collection()),
        normalized_store=MongoNormalizedStore(_Collection()),
        attempt_store=MongoAttemptStore(_Collection()),
        http_evidence_store=MongoProviderHttpEvidenceStore(_Collection()),
    )


def test_policy_pack_cn_a_covers_all_groups_and_writes_evidence_refs(monkeypatch) -> None:
    request = _request()
    adapters = build_default_policy_adapters(provider_config_version="cfg-policy-cn-a")
    plan = _plan(request, adapters)
    groups = {spec.coverage_group for spec in plan.call_specs}
    assert groups == {
        "cn_a_policy_official",
        "cn_a_policy_news",
        "cn_a_policy_macro",
        "cn_a_policy_discovery",
    }

    monkeypatch.setattr(
        "claw_trade.data_gateway.providers.policy._fetch_cn_a_policy_official",
        lambda params: (
            (
                {
                    "title": "关于行业监管政策的公告",
                    "url": "https://cninfo.example/official-1",
                    "published_at": "2026-05-20",
                    "source": "cninfo",
                    "policy_level": "regulatory",
                },
            ),
            "https://cninfo.example/api",
        ),
    )
    monkeypatch.setattr(
        "claw_trade.data_gateway.providers.policy._fetch_cn_a_policy_news",
        lambda params: (
            (
                {
                    "title": "政策新闻线索",
                    "url": "https://eastmoney.example/news-1",
                    "published_at": "2026-05-19",
                    "source": "eastmoney_news",
                    "policy_level": "industry",
                },
            ),
            "https://eastmoney.example/news",
        ),
    )
    monkeypatch.setattr(
        "claw_trade.data_gateway.providers.policy._fetch_cn_a_policy_macro",
        lambda params: (
            (
                {
                    "title": "宏观政策动态",
                    "url": "https://eastmoney.example/macro-1",
                    "published_at": "2026-05-18",
                    "source": "eastmoney_macro",
                    "policy_level": "macro",
                },
            ),
            "https://eastmoney.example/macro",
        ),
    )
    monkeypatch.setattr(
        "claw_trade.data_gateway.providers.policy._fetch_cn_a_policy_discovery",
        lambda params: (
            (
                {
                    "title": "搜索发现线索",
                    "url": "https://google.example/discovery-1",
                    "published_at": "2026-05-17",
                    "source": "google_news",
                    "policy_level": "discovery",
                },
            ),
            "https://google.example/discovery",
        ),
    )

    pack = DomainPackService(settings=object(), adapters=adapters, provider_execution_helper=_helper()).get_pack(request, plan)

    assert pack.readiness.status.value in {"partial", "ready"}
    assert len(pack.attempts) == 4
    assert set(attempt.coverage_group for attempt in pack.attempts) == groups
    assert len(pack.raw_refs) == 4
    assert len(pack.normalized_refs) == 4
    assert all(ref.startswith("mongo://openbb_raw_payloads/") for ref in pack.raw_refs)
    assert all(ref.startswith("mongo://openbb_normalized/") for ref in pack.normalized_refs)
    assert "官方原文与宏观事实优先" in pack.reader_brief_md
    assert "搜索发现线索" in pack.reader_brief_md
    assert "无新增缺口" in pack.reader_brief_md


def test_policy_pack_cn_a_keeps_official_failure_root_cause_and_non_ready_chart(monkeypatch) -> None:
    request = _request()
    adapters = build_default_policy_adapters(provider_config_version="cfg-policy-cn-a")
    plan = _plan(request, adapters)

    def _official_fail(params):
        raise RuntimeError("cninfo official timeout")

    monkeypatch.setattr("claw_trade.data_gateway.providers.policy._fetch_cn_a_policy_official", _official_fail)
    monkeypatch.setattr(
        "claw_trade.data_gateway.providers.policy._fetch_cn_a_policy_news",
        lambda params: (
            (
                {
                    "title": "政策新闻线索 A",
                    "url": "https://eastmoney.example/news-a",
                    "published_at": "2026-05-19",
                    "source": "eastmoney_news",
                    "policy_level": "industry",
                },
                {
                    "title": "政策新闻线索 B",
                    "url": "https://eastmoney.example/news-b",
                    "published_at": "2026-05-18",
                    "source": "eastmoney_news",
                    "policy_level": "industry",
                },
            ),
            "https://eastmoney.example/news",
        ),
    )
    monkeypatch.setattr(
        "claw_trade.data_gateway.providers.policy._fetch_cn_a_policy_macro",
        lambda params: ((), "https://eastmoney.example/macro"),
    )
    monkeypatch.setattr(
        "claw_trade.data_gateway.providers.policy._fetch_cn_a_policy_discovery",
        lambda params: ((), "https://google.example/discovery"),
    )

    pack = DomainPackService(settings=object(), adapters=adapters, provider_execution_helper=_helper()).get_pack(request, plan)

    assert pack.readiness.status.value != "ready"
    assert any(gap.field_path == "cn_a_policy_official" for gap in pack.data_gaps)
    assert any("cninfo official timeout" in gap.root_cause for gap in pack.data_gaps)
    assert pack.chart_assets[0].status.value == "insufficient"
    assert "未就绪" in pack.reader_brief_md

