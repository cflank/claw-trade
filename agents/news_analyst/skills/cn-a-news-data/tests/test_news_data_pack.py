from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from errors import E_INVALID_INPUT, E_PROVIDER_LAYER_FAILED, NewsDataError  # noqa: E402
from models import NewsDataPackRequest, ProviderQuery  # noqa: E402
import news_data_pack as news_data_pack_module  # noqa: E402
from news_data_pack import NewsDataService  # noqa: E402
from providers import NewsProvider, build_raw_news_item  # noqa: E402


@dataclass(frozen=True)
class _ProviderPlan:
    mode: Literal["success", "error", "no_result"]
    rows: list[dict[str, str | None]]


class _ControlledProvider(NewsProvider):
    priority = "P0"

    def __init__(self, *, provider_name: str, endpoint: str, plan: _ProviderPlan) -> None:
        self.name = provider_name
        self.endpoint = endpoint
        self.plan = plan

    def _call_remote(self, query: ProviderQuery) -> object:
        if self.plan.mode == "error":
            raise RuntimeError(f"{self.endpoint} controlled error")
        if self.plan.mode == "no_result":
            return []
        return self.plan.rows

    def _normalize_rows(self, raw_table: object, query: ProviderQuery):
        rows = list(raw_table)
        fetch_time = self.default_source_fetch_time()
        normalized = []
        for row in rows:
            normalized.append(
                build_raw_news_item(
                    endpoint=query.endpoint,
                    raw_id=None,
                    title=str(row.get("title") or ""),
                    summary=row.get("summary"),
                    source=row.get("source"),
                    publish_time=row.get("publish_time"),
                    url=row.get("url"),
                    data_source=f"{self.name}.{self.endpoint}",
                    raw_payload_ref=None,
                    source_fetch_time=fetch_time,
                )
            )
        return normalized


def _write_keyword_rules(path: Path) -> str:
    payload = """
version: "1"
rules:
  - term: "稳增长"
    category: "政策"
    markets: ["CN_A"]
    enabled: true
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


def _build_request(*, start_date: str = "2026-05-01", end_date: str = "2026-05-07") -> NewsDataPackRequest:
    return NewsDataPackRequest(
        ticker="600519",
        exchange_ticker="600519.SH",
        market="CN_A",
        company_name="贵州茅台",
        industry="白酒",
        start_date=start_date,
        end_date=end_date,
        approved_aliases=["茅台"],
        approved_historical_names=["贵州茅台酒股份"],
        profile_missing_fields=[],
    )


def _configure_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, enabled_providers: list[str]) -> None:
    keyword_rules_path = _write_keyword_rules(tmp_path / "keyword_categories.yaml")
    alias_rules_path = _write_alias_rules(tmp_path / "alias_rules.yaml")
    monkeypatch.setenv("CN_A_NEWS_KEYWORD_RULES_PATH", keyword_rules_path)
    monkeypatch.setenv("CN_A_NEWS_ALIAS_CONFLICT_BLACKLIST_PATH", alias_rules_path)
    monkeypatch.setenv("CN_A_NEWS_ENABLED_PROVIDERS", ",".join(enabled_providers))
    monkeypatch.setenv("CN_A_NEWS_TIMEOUT_SECONDS", "2")
    monkeypatch.setenv("CN_A_NEWS_TOTAL_TIMEOUT_SECONDS", "5")
    monkeypatch.setenv("CN_A_NEWS_MAX_CONCURRENCY", "3")
    monkeypatch.setenv("CN_A_NEWS_MAX_JSON_ITEMS", "100")
    monkeypatch.setenv("CN_A_NEWS_MAX_BRIEF_ITEMS", "20")
    monkeypatch.setenv("CN_A_NEWS_TITLE_SIMILARITY_THRESHOLD", "0.92")


def _provider_map(plans: dict[str, _ProviderPlan]) -> dict[str, NewsProvider]:
    mapping: dict[str, NewsProvider] = {}
    for provider_id, plan in plans.items():
        provider_name, endpoint = provider_id.split(".", maxsplit=1)
        mapping[provider_id] = _ControlledProvider(provider_name=provider_name, endpoint=endpoint, plan=plan)
    return mapping


def test_build_pack_complete_when_p0_has_success_and_company_direct_news_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    enabled_providers = [
        "akshare.stock_news_em",
        "akshare.stock_info_global_cls",
        "akshare.stock_info_global_em",
        "akshare.news_cctv",
        "tushare.anns_d",
    ]
    _configure_env(tmp_path, monkeypatch, enabled_providers)
    providers = _provider_map(
        {
            "akshare.stock_news_em": _ProviderPlan(
                mode="success",
                rows=[
                    {
                        "title": "600519 披露经营简报",
                        "summary": "公司披露阶段经营情况",
                        "source": "来源A",
                        "publish_time": "2026-05-06 10:00:00",
                        "url": "https://example.com/company-1",
                    }
                ],
            ),
            "akshare.stock_info_global_cls": _ProviderPlan(mode="error", rows=[]),
            "akshare.stock_info_global_em": _ProviderPlan(mode="no_result", rows=[]),
            "akshare.news_cctv": _ProviderPlan(mode="no_result", rows=[]),
            "tushare.anns_d": _ProviderPlan(mode="no_result", rows=[]),
        }
    )

    pack = NewsDataService(provider_overrides=providers).build_pack(_build_request())

    assert pack.ok is True
    assert pack.quality.status == "complete"
    assert len(pack.provider_attempts) == len(enabled_providers)
    assert [f"{attempt.provider}.{attempt.endpoint}" for attempt in pack.provider_attempts] == enabled_providers
    assert pack.profile["company_name"] == "贵州茅台"
    assert pack.profile["industry"] == "白酒"
    assert set(pack.profile.keys()) == {"company_name", "industry"}
    assert all(
        isinstance(value, str) or value is None for value in pack.profile.values()
    )
    assert "company_news" in pack.data
    assert "industry_news" in pack.data
    assert "policy_macro_news" in pack.data
    assert "announcements" in pack.data


def test_build_pack_partial_when_only_industry_or_macro_background_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    enabled_providers = [
        "akshare.stock_news_em",
        "akshare.stock_info_global_cls",
        "akshare.stock_info_global_em",
    ]
    _configure_env(tmp_path, monkeypatch, enabled_providers)
    providers = _provider_map(
        {
            "akshare.stock_news_em": _ProviderPlan(
                mode="success",
                rows=[
                    {
                        "title": "板块跟踪",
                        "summary": "白酒渠道库存变化",
                        "source": "来源B",
                        "publish_time": "2026-05-06 11:00:00",
                        "url": "https://example.com/industry-1",
                    }
                ],
            ),
            "akshare.stock_info_global_cls": _ProviderPlan(mode="error", rows=[]),
            "akshare.stock_info_global_em": _ProviderPlan(mode="no_result", rows=[]),
        }
    )

    pack = NewsDataService(provider_overrides=providers).build_pack(_build_request())

    assert pack.ok is True
    assert pack.quality.status == "partial"
    assert pack.quality.company_direct_news_count == 0
    assert pack.quality.industry_background_count >= 1


def test_build_pack_failed_when_both_p0_fail_and_p1_p2_attempts_are_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    enabled_providers = [
        "akshare.stock_news_em",
        "akshare.stock_info_global_cls",
        "akshare.stock_info_global_em",
        "akshare.news_cctv",
        "tushare.anns_d",
    ]
    _configure_env(tmp_path, monkeypatch, enabled_providers)
    providers = _provider_map(
        {
            "akshare.stock_news_em": _ProviderPlan(mode="error", rows=[]),
            "akshare.stock_info_global_cls": _ProviderPlan(mode="error", rows=[]),
            "akshare.stock_info_global_em": _ProviderPlan(
                mode="success",
                rows=[
                    {
                        "title": "稳增长政策继续推进",
                        "summary": "多部门发布配套措施",
                        "source": "来源C",
                        "publish_time": "2026-05-06 09:00:00",
                        "url": "https://example.com/macro-1",
                    }
                ],
            ),
            "akshare.news_cctv": _ProviderPlan(
                mode="success",
                rows=[
                    {
                        "title": "消费复苏政策观察",
                        "summary": "新闻联播播报消费政策",
                        "source": "新闻联播",
                        "publish_time": "2026-05-06",
                        "url": None,
                    }
                ],
            ),
            "tushare.anns_d": _ProviderPlan(
                mode="success",
                rows=[
                    {
                        "title": "公司公告摘要",
                        "summary": "600519.SH 发布公告",
                        "source": "tushare.anns_d",
                        "publish_time": "2026-05-06",
                        "url": "https://example.com/ann-1",
                    }
                ],
            ),
        }
    )

    pack = NewsDataService(provider_overrides=providers).build_pack(_build_request())

    assert pack.ok is False
    assert pack.quality.status == "failed"
    assert len(pack.provider_attempts) == len(enabled_providers)
    attempts_by_id = {f"{attempt.provider}.{attempt.endpoint}": attempt for attempt in pack.provider_attempts}
    assert attempts_by_id["akshare.stock_info_global_em"].ok is True
    assert attempts_by_id["akshare.news_cctv"].ok is True
    assert attempts_by_id["tushare.anns_d"].ok is True


def test_build_pack_keeps_failed_when_only_p1_p2_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    enabled_providers = [
        "akshare.stock_news_em",
        "akshare.stock_info_global_cls",
        "akshare.stock_info_global_em",
        "akshare.news_cctv",
    ]
    _configure_env(tmp_path, monkeypatch, enabled_providers)
    providers = _provider_map(
        {
            "akshare.stock_news_em": _ProviderPlan(mode="error", rows=[]),
            "akshare.stock_info_global_cls": _ProviderPlan(mode="error", rows=[]),
            "akshare.stock_info_global_em": _ProviderPlan(
                mode="success",
                rows=[
                    {
                        "title": "稳增长政策继续推进",
                        "summary": "宏观政策观察",
                        "source": "来源D",
                        "publish_time": "2026-05-06 09:00:00",
                        "url": "https://example.com/macro-2",
                    }
                ],
            ),
            "akshare.news_cctv": _ProviderPlan(
                mode="success",
                rows=[
                    {
                        "title": "消费复苏政策观察",
                        "summary": "政策支持消费复苏",
                        "source": "新闻联播",
                        "publish_time": "2026-05-06",
                        "url": None,
                    }
                ],
            ),
        }
    )

    pack = NewsDataService(provider_overrides=providers).build_pack(_build_request())

    assert pack.ok is False
    assert pack.quality.status == "failed"


def test_build_pack_rejects_invalid_date_window_with_e_invalid_input() -> None:
    service = NewsDataService(provider_overrides={})
    request = _build_request(start_date="2026-05-08", end_date="2026-05-07")

    with pytest.raises(NewsDataError) as raised:
        service.build_pack(request)

    assert raised.value.code == E_INVALID_INPUT


def test_build_pack_rejects_non_yyyy_mm_dd_date_format() -> None:
    service = NewsDataService(provider_overrides={})
    request = _build_request(start_date="2026/05/01", end_date="2026-05-07")

    with pytest.raises(NewsDataError) as raised:
        service.build_pack(request)

    assert raised.value.code == E_INVALID_INPUT


def test_rejected_items_do_not_enter_data_and_counts_remain_traceable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    enabled_providers = [
        "akshare.stock_news_em",
        "akshare.stock_info_global_cls",
    ]
    _configure_env(tmp_path, monkeypatch, enabled_providers)
    providers = _provider_map(
        {
            "akshare.stock_news_em": _ProviderPlan(
                mode="success",
                rows=[
                    {
                        "title": "600519 披露经营简报",
                        "summary": "公司披露阶段经营情况",
                        "source": "来源A",
                        "publish_time": "2026-05-06 10:00:00",
                        "url": "https://example.com/company-2",
                    },
                    {
                        "title": "港口物流动态",
                        "summary": "沿海运价波动",
                        "source": "来源E",
                        "publish_time": "2026-05-06 12:00:00",
                        "url": "https://example.com/rejected-1",
                    },
                ],
            ),
            "akshare.stock_info_global_cls": _ProviderPlan(mode="error", rows=[]),
        }
    )

    pack = NewsDataService(provider_overrides=providers).build_pack(_build_request())

    all_titles = [item.title for bucket_items in pack.data.values() for item in bucket_items]
    assert "港口物流动态" not in all_titles
    assert pack.quality.total_raw_count == 2
    assert pack.quality.accepted_count == 1
    assert "共获取 2 条原始新闻，去重后 1 条，接受 1 条。" in pack.reader_brief


def test_pack_profile_values_are_only_str_or_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    enabled_providers = ["akshare.stock_news_em", "akshare.stock_info_global_cls"]
    _configure_env(tmp_path, monkeypatch, enabled_providers)
    providers = _provider_map(
        {
            "akshare.stock_news_em": _ProviderPlan(
                mode="success",
                rows=[
                    {
                        "title": "600519 披露经营简报",
                        "summary": "公司披露阶段经营情况",
                        "source": "来源A",
                        "publish_time": "2026-05-06 10:00:00",
                        "url": "https://example.com/company-3",
                    }
                ],
            ),
            "akshare.stock_info_global_cls": _ProviderPlan(mode="no_result", rows=[]),
        }
    )

    pack = NewsDataService(provider_overrides=providers).build_pack(_build_request())

    assert all(isinstance(value, str) or value is None for value in pack.profile.values())
    assert not any(isinstance(value, list) for value in pack.profile.values())


def test_build_pack_unexpected_error_details_are_sanitized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise_sensitive_error() -> object:
        raise RuntimeError(
            "request failed token=abc123 api_key=secret456 cookie=session789"
        )

    monkeypatch.setattr(news_data_pack_module, "load_cn_a_news_config", _raise_sensitive_error)
    service = NewsDataService(provider_overrides={})

    with pytest.raises(NewsDataError) as raised:
        service.build_pack(_build_request())

    assert raised.value.code == E_PROVIDER_LAYER_FAILED
    details = raised.value.details or {}
    sanitized_error = str(details.get("error", ""))
    assert "abc123" not in sanitized_error
    assert "secret456" not in sanitized_error
    assert "session789" not in sanitized_error
    assert "***" in sanitized_error
