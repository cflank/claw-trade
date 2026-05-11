from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from models import ProviderQuery, QueryPlan, ResolvedProfile, ToolRuntimeContext  # noqa: E402
import news_data_pack as news_data_pack_module  # noqa: E402
from observability import record_event, reset_observability, snapshot_observability  # noqa: E402
import provider_scheduler as provider_scheduler_module  # noqa: E402
from provider_scheduler import run_bounded_parallel  # noqa: E402
from providers import NewsProvider, build_raw_news_item  # noqa: E402
from quality import QualityGate  # noqa: E402
from test_quality import _build_attempt, _build_news_item, _build_quality_input  # noqa: E402


@dataclass(frozen=True)
class _ProviderBehavior:
    mode: str


class _ControlledProvider(NewsProvider):
    priority = "P0"

    def __init__(self, *, provider_name: str, endpoint: str, behavior: _ProviderBehavior) -> None:
        self.name = provider_name
        self.endpoint = endpoint
        self._behavior = behavior

    def _call_remote(self, query: ProviderQuery) -> object:
        if self._behavior.mode == "error":
            raise RuntimeError("controlled_error")
        if self._behavior.mode == "empty":
            return []
        return [
            {
                "title": f"{query.ticker} 新闻",
                "summary": "公司披露经营情况",
                "source": "来源A",
                "publish_time": "2026-05-07 09:00:00",
                "url": "https://example.com/news-1",
            }
        ]

    def _normalize_rows(self, raw_table: object, query: ProviderQuery):
        rows = list(raw_table)
        fetch_time = self.default_source_fetch_time()
        return [
            build_raw_news_item(
                endpoint=query.endpoint,
                raw_id=None,
                title=row["title"],
                summary=row["summary"],
                source=row["source"],
                publish_time=row["publish_time"],
                url=row["url"],
                data_source=f"{self.name}.{self.endpoint}",
                raw_payload_ref=None,
                source_fetch_time=fetch_time,
            )
            for row in rows
        ]


def _build_query_plan() -> QueryPlan:
    return QueryPlan(
        ticker="600519",
        exchange_ticker="600519.SH",
        company_name="贵州茅台",
        industry="白酒",
        start_date="2026-05-01",
        end_date="2026-05-07",
        company_keywords=["600519", "600519.SH", "贵州茅台"],
        industry_keywords=["白酒"],
        macro_keywords=["消费复苏"],
    )


def _metric_total_value(snapshot: dict, name: str, labels: dict[str, str]) -> float | None:
    for item in snapshot["metric_totals"]:
        if item["name"] != name:
            continue
        if item["labels"] == labels:
            return float(item["value"])
    return None


def _write_keyword_rules(path: Path) -> str:
    payload = """
version: "1"
rules:
  - term: "消费复苏"
    category: "宏观消费"
    markets: ["CN_A"]
    enabled: true
"""
    path.write_text(payload.strip() + "\n", encoding="utf-8")
    return str(path)


def _write_alias_rules(path: Path) -> str:
    payload = """
version: "1"
aliases: []
conflicts: []
"""
    path.write_text(payload.strip() + "\n", encoding="utf-8")
    return str(path)


def test_provider_success_attempt_records_attempt_and_elapsed_metrics() -> None:
    reset_observability()
    provider = _ControlledProvider(
        provider_name="akshare",
        endpoint="stock_news_em",
        behavior=_ProviderBehavior(mode="success"),
    )
    provider_overrides = {"akshare.stock_news_em": provider}

    _ = run_bounded_parallel(
        query_plan=_build_query_plan(),
        enabled_providers=["akshare.stock_news_em"],
        max_concurrency=1,
        per_provider_timeout_seconds=5,
        total_timeout_seconds=5,
        provider_overrides=provider_overrides,
        observability_fields={
            "run_id": "run-1",
            "stage": "frontline",
            "worker_id": "news_analyst",
            "call_id": "call-1",
            "tool_name": "news_news_data_pack",
        },
    )
    snapshot = snapshot_observability()

    attempt_total = _metric_total_value(
        snapshot,
        "news_provider_attempt_total",
        {
            "provider": "akshare",
            "endpoint": "stock_news_em",
            "status": "success",
            "empty_reason": "none",
        },
    )
    elapsed_total = _metric_total_value(
        snapshot,
        "news_provider_elapsed_ms_bucket",
        {"provider": "akshare", "endpoint": "stock_news_em"},
    )
    assert attempt_total == 1.0
    assert elapsed_total is not None
    assert elapsed_total >= 0.0


def test_quality_failed_pack_records_failed_status_missing_field_and_company_direct_metrics() -> None:
    reset_observability()
    quality_input = _build_quality_input(
        provider_attempts=[
            _build_attempt(provider="akshare", endpoint="stock_news_em", ok=False, raw_count=0, empty_reason="timeout"),
            _build_attempt(
                provider="akshare",
                endpoint="stock_info_global_cls",
                ok=False,
                raw_count=0,
                empty_reason="provider_error",
            ),
        ],
        items=[_build_news_item("industry-1", bucket="industry_news")],
    )
    _ = QualityGate().evaluate(quality_input)
    snapshot = snapshot_observability()

    assert _metric_total_value(
        snapshot,
        "news_pack_status_total",
        {"status": "failed"},
    ) == 1.0
    assert _metric_total_value(
        snapshot,
        "news_pack_company_direct_count",
        {},
    ) == 0.0
    assert _metric_total_value(
        snapshot,
        "news_pack_missing_field_total",
        {"field": "company_direct_news"},
    ) == 1.0


def test_observability_structured_event_does_not_keep_plaintext_or_sensitive_fields() -> None:
    reset_observability()
    record_event(
        "news_log_event",
        fields={
            "run_id": "run-1",
            "status": "ok",
            "path": "/tmp/evidence/news_data_pack.json",
            "summary": "这里是正文，不允许进入普通日志",
            "title": "标题",
            "api_key": "RAWSECRET",
        },
    )
    snapshot = snapshot_observability()
    assert len(snapshot["events"]) == 1
    fields = snapshot["events"][0]["fields"]
    assert fields["run_id"] == "run-1"
    assert fields["status"] == "ok"
    assert fields["path"] == "/tmp/evidence/news_data_pack.json"
    assert "summary" not in fields
    assert "title" not in fields
    assert "api_key" not in fields


def test_tool_call_trace_contains_tool_adapter_service_and_key_path_spans(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_observability()
    keyword_rules_path = _write_keyword_rules(tmp_path / "keyword_categories.yaml")
    alias_rules_path = _write_alias_rules(tmp_path / "alias_rules.yaml")
    monkeypatch.setenv("CN_A_NEWS_KEYWORD_RULES_PATH", keyword_rules_path)
    monkeypatch.setenv("CN_A_NEWS_ALIAS_CONFLICT_BLACKLIST_PATH", alias_rules_path)
    monkeypatch.setenv("CN_A_NEWS_ENABLED_PROVIDERS", "akshare.stock_news_em")
    monkeypatch.setenv("CN_A_NEWS_TIMEOUT_SECONDS", "3")
    monkeypatch.setenv("CN_A_NEWS_TOTAL_TIMEOUT_SECONDS", "5")
    monkeypatch.setenv("CN_A_NEWS_MAX_CONCURRENCY", "1")
    monkeypatch.setenv("CN_A_NEWS_MAX_JSON_ITEMS", "50")
    monkeypatch.setenv("CN_A_NEWS_MAX_BRIEF_ITEMS", "10")
    monkeypatch.setenv("CN_A_NEWS_TITLE_SIMILARITY_THRESHOLD", "0.92")

    provider = _ControlledProvider(
        provider_name="akshare",
        endpoint="stock_news_em",
        behavior=_ProviderBehavior(mode="success"),
    )
    monkeypatch.setitem(provider_scheduler_module._DEFAULT_PROVIDER_FACTORIES, "akshare.stock_news_em", lambda: provider)
    monkeypatch.setattr(
        news_data_pack_module,
        "ApprovedProfileResolver",
        lambda: type(
            "_Resolver",
            (),
            {
                "resolve": staticmethod(
                    lambda **_: ResolvedProfile(
                        company_name="贵州茅台",
                        industry="白酒",
                        approved_aliases=["茅台"],
                        approved_historical_names=[],
                        missing_fields=[],
                    )
                )
            },
        )(),
    )

    response = news_data_pack_module.run_news_data_pack(
        tool_input={"ticker": "600519", "market": "CN_A"},
        context=ToolRuntimeContext(
            run_id="run-obs-1",
            stage="frontline",
            worker_id="news_analyst",
            call_id="call-obs-1",
            tool_name="news_news_data_pack",
            evidence_root=str(tmp_path / "evidence"),
        ),
    )
    assert response["ok"] is True

    span_names = [span["name"] for span in snapshot_observability()["spans"]]
    assert "news_data_pack.tool_adapter" in span_names
    assert "news_data_pack.service" in span_names
    assert "news_data_pack.match" in span_names
    assert "news_data_pack.quality" in span_names
    assert "news_data_pack.evidence" in span_names
    assert "news_data_pack.provider.stock_news_em" in span_names
