from __future__ import annotations

import sys
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_PYTHON_ROOT = REPO_ROOT / "openclaw_plugins" / "claw-trade-frontline-tools" / "python"
if str(SHARED_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(SHARED_PYTHON_ROOT))

from frontline_data_pack.config import load_frontline_provider_config  # noqa: E402
from frontline_data_pack.errors import TOOL_CONTEXT_INCOMPLETE, TOOL_WORKER_MISMATCH, FrontlineValidationError  # noqa: E402
from frontline_data_pack.evidence import OpenVikingStatResult, OpenVikingWriteResult  # noqa: E402
from frontline_data_pack.news_data_pack import BuildNewsDataPack  # noqa: E402
from frontline_data_pack.runtime_context import ToolRuntimeContext  # noqa: E402


def _runtime_context_payload(
    *,
    remove_fields: set[str] | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "run_id": "run-1",
        "stage": "frontline",
        "worker_id": "news_analyst",
        "call_id": "call-1",
        "dispatch_id": "dispatch-1",
        "tool_name": "news_news_data_pack",
        "evidence_root": "/tmp/evidence",
        "current_time": "2026-05-09T12:00:00Z",
    }
    if remove_fields is not None:
        for field_name in remove_fields:
            payload.pop(field_name, None)
    payload.update(overrides)
    return payload


def test_t_news_002_valid_context_with_company_hard_match_has_company_news_and_raw_ref() -> None:
    pack = _build_runner(
        call_registry={
            ("akshare", "stock_news_em"): _company_news_provider(2),
            ("akshare", "stock_info_global_cls"): _macro_news_provider(1),
        }
    ).build(_tool_input(), _runtime_context())

    assert pack.domain == "news"
    assert len(pack.domain_data["company_news"]) >= 1
    for item in pack.domain_data["company_news"]:
        assert item["raw_payload_ref"].startswith("viking://")


def test_t_news_002_only_background_without_company_match_caps_quality_at_partial() -> None:
    pack = _build_runner(
        call_registry={
            ("akshare", "stock_news_em"): _industry_only_news_provider(3),
            ("akshare", "stock_info_global_cls"): _macro_news_provider(3),
        }
    ).build(_tool_input(), _runtime_context())

    assert pack.quality.status in {"partial", "failed"}
    assert pack.quality.status != "complete"


@pytest.mark.parametrize(
    ("runtime_context", "expected_code"),
    [
        pytest.param(
            _runtime_context_payload(remove_fields={"worker_id"}),
            TOOL_CONTEXT_INCOMPLETE,
            id="worker_id_missing",
        ),
        pytest.param(
            _runtime_context_payload(worker_id=" "),
            TOOL_CONTEXT_INCOMPLETE,
            id="context_incomplete_equivalent_empty_worker_id",
        ),
        pytest.param(
            _runtime_context_payload(worker_id="market_analyst"),
            TOOL_WORKER_MISMATCH,
            id="worker_id_mismatch",
        ),
        pytest.param(
            _runtime_context_payload(tool_name="news.other_tool"),
            TOOL_WORKER_MISMATCH,
            id="tool_name_mismatch",
        ),
        pytest.param(
            _runtime_context_payload(stage="investment_debate"),
            TOOL_WORKER_MISMATCH,
            id="stage_mismatch",
        ),
        pytest.param(
            _runtime_context_payload(market="US"),
            TOOL_WORKER_MISMATCH,
            id="market_mismatch",
        ),
    ],
)
def test_t_news_002_context_errors_do_not_touch_provider_registry_or_l2_mongo(
    runtime_context: dict[str, Any],
    expected_code: str,
) -> None:
    with pytest.raises(FrontlineValidationError) as error:
        _build_fail_fast_runner().build(_tool_input(), runtime_context)

    assert error.value.code == expected_code


def test_t_news_002_bucket_cap_20_and_brief_uses_existing_max_8_summary_rule() -> None:
    pack = _build_runner(
        call_registry={
            ("akshare", "stock_news_em"): _company_news_provider(30),
            ("akshare", "stock_info_global_cls"): _macro_news_provider(30),
        }
    ).build(_tool_input(), _runtime_context())

    assert len(pack.domain_data["company_news"]) == 20
    assert len(pack.domain_data["industry_macro_news"]) == 20
    assert len(pack.domain_data["announcements"]) <= 20
    assert "其余" in pack.reader_brief


def test_t_news_002_cls_without_company_industry_macro_keyword_must_be_rejected() -> None:
    pack = _build_runner(
        call_registry={
            ("akshare", "stock_news_em"): _empty_news_provider(),
            ("akshare", "stock_info_global_cls"): _cls_unmatched_provider(2),
        }
    ).build(_tool_input(), _runtime_context())

    assert pack.domain_data["company_news"] == []
    assert pack.domain_data["industry_macro_news"] == []
    assert pack.domain_data["rejected_count"] >= 2


def test_t_news_002_default_window_is_7_days_when_start_end_missing() -> None:
    pack = _build_runner(
        call_registry={
            ("akshare", "stock_news_em"): _empty_news_provider(),
            ("akshare", "stock_info_global_cls"): _empty_news_provider(),
        }
    ).build(
        {
            "ticker": "600519.SH",
            "market": "CN_A",
            "company_name": "贵州茅台",
            "industry": "白酒",
            "start_date": None,
            "end_date": None,
            "approved_artifact_refs": [],
        },
        _runtime_context(),
    )

    assert pack.input.end_date == "2026-05-09"
    assert pack.input.start_date == "2026-05-03"


def test_t_news_002_enhancement_sources_are_attempted_without_blocking_accepted_company_news() -> None:
    pack = _build_runner(
        call_registry={
            ("akshare", "stock_news_em"): _company_news_provider(1),
            ("akshare", "stock_info_global_cls"): _empty_news_provider(),
        }
    ).build(_tool_input(), _runtime_context())

    enhancement_attempts = [
        attempt
        for attempt in pack.provider_attempts
        if attempt.provider in {"bocha", "tavily", "jina", "newsnow", "minimax", "tushare"}
    ]

    assert enhancement_attempts
    assert all(attempt.status != "config_blocked" for attempt in enhancement_attempts)
    assert len(pack.domain_data["company_news"]) >= 1
    assert all(item["provider"] == "akshare" for item in pack.domain_data["company_news"])


def test_t_news_002_provider_attempts_l2_write_failed_must_failed_and_no_fake_attempt_ref() -> None:
    with pytest.raises(FrontlineValidationError):
        _build_runner(
            call_registry={
                ("akshare", "stock_news_em"): _company_news_provider(1),
                ("akshare", "stock_info_global_cls"): _empty_news_provider(),
            },
            evidence_client=_FailProviderAttemptsWriteL2Client(),
        ).build(_tool_input(), _runtime_context())


def test_t_news_002_normalized_pack_l2_write_failed_must_failed_and_no_fake_pack_ref() -> None:
    with pytest.raises(FrontlineValidationError):
        _build_runner(
            call_registry={
                ("akshare", "stock_news_em"): _company_news_provider(1),
                ("akshare", "stock_info_global_cls"): _empty_news_provider(),
            },
            evidence_client=_FailNormalizedPackWriteL2Client(),
        ).build(_tool_input(), _runtime_context())


@dataclass
class _MongoCollection:
    find_doc: dict[str, Any] | None = None

    def find_one(self, _query: dict[str, Any]) -> dict[str, Any] | None:
        return self.find_doc

    def update_one(self, _flt: dict[str, Any], _update: dict[str, Any], *, upsert: bool) -> None:
        _ = upsert
        return None

    def insert_one(self, _doc: dict[str, Any]) -> None:
        return None


class _InMemoryL2Client:
    def __init__(self) -> None:
        self._content_by_uri: dict[str, bytes] = {}

    def write(
        self,
        *,
        uri: str,
        content_bytes: bytes,
        content_type: str,
        metadata: Mapping[str, str],
    ) -> OpenVikingWriteResult:
        _ = content_type, metadata
        self._content_by_uri[uri] = content_bytes
        return OpenVikingWriteResult(receipt_id="receipt-1")

    def stat(self, *, uri: str) -> OpenVikingStatResult:
        content = self._content_by_uri[uri]
        digest = hashlib.sha256(content).hexdigest()
        return OpenVikingStatResult(size_bytes=len(content), sha256=f"sha256:{digest}", exists=True)

    def read(self, *, uri: str) -> bytes:
        return self._content_by_uri[uri]


class _FailProviderAttemptsWriteL2Client(_InMemoryL2Client):
    def write(
        self,
        *,
        uri: str,
        content_bytes: bytes,
        content_type: str,
        metadata: Mapping[str, str],
    ) -> OpenVikingWriteResult:
        if uri.endswith("/provider_attempts.json"):
            raise RuntimeError("provider attempts l2 write failed")
        return super().write(uri=uri, content_bytes=content_bytes, content_type=content_type, metadata=metadata)


class _FailNormalizedPackWriteL2Client(_InMemoryL2Client):
    def write(
        self,
        *,
        uri: str,
        content_bytes: bytes,
        content_type: str,
        metadata: Mapping[str, str],
    ) -> OpenVikingWriteResult:
        if uri.endswith("/normalized_pack.json"):
            raise RuntimeError("normalized pack l2 write failed")
        return super().write(uri=uri, content_bytes=content_bytes, content_type=content_type, metadata=metadata)


class _FailFastCollection:
    def find_one(self, _query: dict[str, Any]) -> dict[str, Any] | None:
        raise AssertionError("context error path must not read MongoDB")

    def update_one(self, _flt: dict[str, Any], _update: dict[str, Any], *, upsert: bool) -> None:
        _ = upsert
        raise AssertionError("context error path must not write MongoDB")

    def insert_one(self, _doc: dict[str, Any]) -> None:
        raise AssertionError("context error path must not insert MongoDB rows")


class _FailFastL2Client:
    def write(
        self,
        *,
        uri: str,
        content_bytes: bytes,
        content_type: str,
        metadata: Mapping[str, str],
    ) -> OpenVikingWriteResult:
        _ = uri, content_bytes, content_type, metadata
        raise AssertionError("context error path must not write L2")

    def stat(self, *, uri: str) -> OpenVikingStatResult:
        _ = uri
        raise AssertionError("context error path must not stat L2")

    def read(self, *, uri: str) -> bytes:
        _ = uri
        raise AssertionError("context error path must not read L2")


def _build_runner(
    *,
    call_registry: Mapping[tuple[str, str], Any],
    evidence_client: _InMemoryL2Client | None = None,
) -> BuildNewsDataPack:
    return BuildNewsDataPack(
        config=load_frontline_provider_config(_base_env()),
        provider_call_registry=call_registry,
        evidence_client=_InMemoryL2Client() if evidence_client is None else evidence_client,
        provider_cache_collection=_MongoCollection(),
        provider_attempts_collection=_MongoCollection(),
        normalized_news_collection=_MongoCollection(),
    )


def _build_fail_fast_runner() -> BuildNewsDataPack:
    def _fail_fast_provider(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("context error path must not call provider registry")

    fail_fast_registry = {
        ("akshare", "stock_news_em"): _fail_fast_provider,
        ("akshare", "stock_info_global_cls"): _fail_fast_provider,
    }
    return BuildNewsDataPack(
        config=load_frontline_provider_config(_base_env()),
        provider_call_registry=fail_fast_registry,
        evidence_client=_FailFastL2Client(),
        provider_cache_collection=_FailFastCollection(),
        provider_attempts_collection=_FailFastCollection(),
        normalized_news_collection=_FailFastCollection(),
    )


def _base_env() -> dict[str, str]:
    return {
        "CN_A_MONGODB_URI": "mongodb://localhost:27017/claw_trade",
        "CLAW_TRADE_OPENVIKING_BASE_URI": "https://openviking.internal",
        "CLAW_TRADE_OPENVIKING_AUTH_MODE": "none",
    }


def _tool_input() -> dict[str, Any]:
    return {
        "ticker": "600519.SH",
        "market": "CN_A",
        "company_name": "贵州茅台",
        "industry": "白酒",
        "start_date": "2026-05-01",
        "end_date": "2026-05-08",
        "approved_artifact_refs": [],
    }


def _runtime_context() -> ToolRuntimeContext:
    return ToolRuntimeContext(
        run_id="run-1",
        stage="frontline",
        worker_id="news_analyst",
        call_id="call-1",
        dispatch_id="dispatch-1",
        tool_name="news_news_data_pack",
        evidence_root="/tmp/evidence",
        current_time="2026-05-09T12:00:00Z",
    )


def _company_news_provider(count: int):
    def _provider(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        rows = []
        for index in range(count):
            rows.append(
                {
                    "title": f"600519 事件 {index}",
                    "summary": "公司新闻",
                    "source": "财联社",
                    "publish_time": f"2026-05-08T10:{index % 60:02d}:00",
                    "url": f"https://example.com/company/{index}",
                }
            )
        return {"rows": rows}

    return _provider


def _industry_only_news_provider(count: int):
    def _provider(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        rows = []
        for index in range(count):
            rows.append(
                {
                    "title": f"白酒行业快讯 {index}",
                    "summary": "行业资讯",
                    "source": "财联社",
                    "publish_time": f"2026-05-08T09:{index % 60:02d}:00",
                    "url": f"https://example.com/industry/{index}",
                }
            )
        return {"rows": rows}

    return _provider


def _macro_news_provider(count: int):
    def _provider(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        rows = []
        for index in range(count):
            rows.append(
                {
                    "title": f"白酒与宏观观察 {index}",
                    "summary": "背景信息",
                    "source": "财联社",
                    "publish_time": f"2026-05-08T08:{index % 60:02d}:00",
                    "url": f"https://example.com/macro/{index}",
                }
            )
        return {"rows": rows}

    return _provider


def _empty_news_provider():
    def _provider(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"rows": []}

    return _provider


def _cls_unmatched_provider(count: int):
    def _provider(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        rows = []
        for index in range(count):
            rows.append(
                {
                    "title": f"短讯 {index}",
                    "summary": "普通播报，无目标词",
                    "source": "财联社",
                    "publish_time": f"2026-05-08T08:{index % 60:02d}:00",
                    "url": f"https://example.com/macro/reject-{index}",
                }
            )
        return {"rows": rows}

    return _provider
