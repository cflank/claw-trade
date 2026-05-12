from __future__ import annotations

import hashlib
import sys
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
import frontline_data_pack.social_sentiment_pack as social_sentiment_pack_module  # noqa: E402
from frontline_data_pack.social_sentiment_pack import BuildSocialSentimentPack  # noqa: E402
from frontline_data_pack.runtime_context import ToolRuntimeContext  # noqa: E402


def _runtime_context_payload(
    *,
    remove_fields: set[str] | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "run_id": "run-social-1",
        "stage": "frontline",
        "worker_id": "social_analyst",
        "call_id": "call-social-1",
        "dispatch_id": "dispatch-social-1",
        "tool_name": "social_social_sentiment_pack",
        "evidence_root": "/tmp/evidence",
        "current_time": "2026-05-09T12:00:00Z",
    }
    if remove_fields is not None:
        for field_name in remove_fields:
            payload.pop(field_name, None)
    payload.update(overrides)
    return payload


def test_t_soc_002_target_signal_with_l2_yields_complete_or_partial_and_bucket_present() -> None:
    pack = _build_runner(
        call_registry={
            ("eastmoney_akshare", "hot_rank_latest"): _attention_provider(target_code="600519"),
            ("eastmoney_akshare", "hot_keyword"): _keyword_provider(target_code="600519"),
            ("eastmoney_akshare", "related_hot_rank"): _empty_provider(),
            ("eastmoney_direct", "full_hot_rank_board"): _empty_provider(),
        }
    ).build(_tool_input(), _runtime_context())

    assert pack.quality.status in {"complete", "partial"}
    assert len(pack.domain_data["attention_signals"]) + len(pack.domain_data["topic_keyword_signals"]) >= 1


def test_t_soc_002_target_accepted_signal_zero_must_failed() -> None:
    pack = _build_runner(
        call_registry={
            ("eastmoney_akshare", "hot_rank_latest"): _attention_provider(target_code="000001"),
            ("eastmoney_akshare", "hot_keyword"): _keyword_provider(target_code="000001", keyword="银行"),
            ("eastmoney_akshare", "related_hot_rank"): _empty_provider(),
            ("eastmoney_direct", "full_hot_rank_board"): _empty_provider(),
        }
    ).build(_tool_input(), _runtime_context())

    assert pack.quality.status == "failed"


def test_t_soc_002_target_heat_without_text_evidence_is_partial_and_judgment_disallowed() -> None:
    pack = _build_runner(
        call_registry={
            ("eastmoney_akshare", "hot_rank_latest"): _attention_provider(target_code="600519"),
            ("eastmoney_akshare", "hot_keyword"): _empty_provider(),
            ("eastmoney_akshare", "related_hot_rank"): _empty_provider(),
            ("eastmoney_direct", "full_hot_rank_board"): _empty_provider(),
        }
    ).build(_tool_input(), _runtime_context())

    assert pack.quality.status == "partial"
    assert pack.domain_data["social_judgment_allowed"] is False
    assert "social_text_evidence_missing" in pack.reader_brief


def test_t_soc_002_core_signal_l2_write_failed_must_failed() -> None:
    with pytest.raises(FrontlineValidationError):
        _build_runner(
            call_registry={
                ("eastmoney_akshare", "hot_rank_latest"): _attention_provider(target_code="600519"),
                ("eastmoney_akshare", "hot_keyword"): _empty_provider(),
                ("eastmoney_akshare", "related_hot_rank"): _empty_provider(),
                ("eastmoney_direct", "full_hot_rank_board"): _empty_provider(),
            },
            evidence_client=_FailRawWriteL2Client(),
        ).build(_tool_input(), _runtime_context())


def test_t_soc_002_provider_attempts_l2_write_failed_must_failed_and_no_fake_attempt_ref() -> None:
    with pytest.raises(FrontlineValidationError):
        _build_runner(
            call_registry={
                ("eastmoney_akshare", "hot_rank_latest"): _attention_provider(target_code="600519"),
                ("eastmoney_akshare", "hot_keyword"): _empty_provider(),
                ("eastmoney_akshare", "related_hot_rank"): _empty_provider(),
                ("eastmoney_direct", "full_hot_rank_board"): _empty_provider(),
            },
            evidence_client=_FailProviderAttemptsWriteL2Client(),
        ).build(_tool_input(), _runtime_context())


def test_t_soc_002_normalized_pack_l2_write_failed_must_failed_and_no_fake_pack_ref() -> None:
    with pytest.raises(FrontlineValidationError):
        _build_runner(
            call_registry={
                ("eastmoney_akshare", "hot_rank_latest"): _attention_provider(target_code="600519"),
                ("eastmoney_akshare", "hot_keyword"): _empty_provider(),
                ("eastmoney_akshare", "related_hot_rank"): _empty_provider(),
                ("eastmoney_direct", "full_hot_rank_board"): _empty_provider(),
            },
            evidence_client=_FailNormalizedPackWriteL2Client(),
        ).build(_tool_input(), _runtime_context())


def test_t_soc_002_approved_aliases_can_hard_match_into_accepted() -> None:
    pack = _build_runner(
        call_registry={
            ("eastmoney_akshare", "hot_rank_latest"): _empty_provider(),
            ("eastmoney_akshare", "hot_keyword"): _alias_keyword_provider(),
            ("eastmoney_akshare", "related_hot_rank"): _empty_provider(),
            ("eastmoney_direct", "full_hot_rank_board"): _empty_provider(),
        }
    ).build(
        _tool_input(
            aliases=[],
            approved_aliases=["茅台"],
        ),
        _runtime_context(),
    )

    assert len(pack.domain_data["topic_keyword_signals"]) >= 1
    assert any(signal["match_evidence_span"] == "茅台" for signal in pack.domain_data["topic_keyword_signals"])


def test_t_soc_002_aliases_only_must_not_be_treated_as_approved_aliases() -> None:
    pack = _build_runner(
        call_registry={
            ("eastmoney_akshare", "hot_rank_latest"): _empty_provider(),
            ("eastmoney_akshare", "hot_keyword"): _alias_keyword_provider(),
            ("eastmoney_akshare", "related_hot_rank"): _empty_provider(),
            ("eastmoney_direct", "full_hot_rank_board"): _empty_provider(),
        }
    ).build(
        _tool_input(
            aliases=["茅台"],
            approved_aliases=[],
        ),
        _runtime_context(),
    )

    assert pack.quality.status == "failed"
    assert pack.domain_data["topic_keyword_signals"] == []


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
            id="worker_id_blank",
        ),
        pytest.param(
            _runtime_context_payload(worker_id="news_analyst"),
            TOOL_WORKER_MISMATCH,
            id="worker_id_mismatch",
        ),
        pytest.param(
            _runtime_context_payload(tool_name="social.other_tool"),
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
def test_t_soc_002_context_errors_do_not_touch_provider_registry_l2_or_mongo_or_generate_attempts(
    runtime_context: dict[str, Any],
    expected_code: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _fail_provider_attempt_ctor(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("context error path must not construct ProviderAttempt")

    monkeypatch.setattr(social_sentiment_pack_module, "ProviderAttempt", _fail_provider_attempt_ctor)

    with pytest.raises(FrontlineValidationError) as error:
        _build_fail_fast_runner().build(_tool_input(), runtime_context)

    assert error.value.code == expected_code


def test_t_soc_002_matrix_enhancement_sources_keep_explicit_failure_attempts_without_fake_success() -> None:
    pack = _build_runner(
        call_registry={
            ("eastmoney_akshare", "hot_rank_latest"): _attention_provider(target_code="600519"),
            ("eastmoney_akshare", "hot_keyword"): _empty_provider(),
            ("eastmoney_akshare", "related_hot_rank"): _empty_provider(),
            ("eastmoney_direct", "full_hot_rank_board"): _empty_provider(),
        }
    ).build(_tool_input(), _runtime_context())

    explicit_failures = [
        attempt
        for attempt in pack.provider_attempts
        if attempt.status == "error"
        and attempt.provider in {"bocha", "jina", "tavily", "alphaear_news_source_list", "xueqiu_guba"}
    ]

    assert explicit_failures
    by_provider = {attempt.provider: attempt.error_code for attempt in explicit_failures}
    assert by_provider["bocha"] == "PROVIDER_AUTH_MISSING"
    assert by_provider["jina"] == "PROVIDER_AUTH_MISSING"
    assert by_provider["tavily"] == "PROVIDER_AUTH_MISSING"
    assert by_provider["alphaear_news_source_list"] == "PROVIDER_CONTRACT_MISSING"
    assert by_provider["xueqiu_guba"] == "PROVIDER_AUTH_MISSING"

    assert not any(
        attempt.status == "config_blocked"
        and attempt.provider in {"bocha", "jina", "tavily", "alphaear_news_source_list", "xueqiu_guba"}
        for attempt in pack.provider_attempts
    )

    accepted_providers = {
        signal["provider"]
        for signal in (
            pack.domain_data["attention_signals"]
            + pack.domain_data["topic_keyword_signals"]
            + pack.domain_data["related_symbol_signals"]
            + pack.domain_data["narrative_signals"]
        )
    }
    assert not (accepted_providers & {"bocha", "jina", "tavily", "alphaear_news_source_list", "xueqiu_guba"})


class _MongoCollection:
    def find_one(self, _query: dict[str, Any]) -> dict[str, Any] | None:
        return None

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


class _FailRawWriteL2Client(_InMemoryL2Client):
    def write(
        self,
        *,
        uri: str,
        content_bytes: bytes,
        content_type: str,
        metadata: Mapping[str, str],
    ) -> OpenVikingWriteResult:
        if "/provider_raw/" in uri:
            raise RuntimeError("provider raw l2 write failed")
        return super().write(uri=uri, content_bytes=content_bytes, content_type=content_type, metadata=metadata)


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
) -> BuildSocialSentimentPack:
    return BuildSocialSentimentPack(
        config=load_frontline_provider_config(_base_env()),
        provider_call_registry=call_registry,
        evidence_client=_InMemoryL2Client() if evidence_client is None else evidence_client,
        provider_cache_collection=_MongoCollection(),
        provider_attempts_collection=_MongoCollection(),
        normalized_social_collection=_MongoCollection(),
    )


def _build_fail_fast_runner() -> BuildSocialSentimentPack:
    def _fail_fast_provider(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("context error path must not call provider registry")

    fail_fast_registry = {
        ("eastmoney_akshare", "hot_rank_latest"): _fail_fast_provider,
        ("eastmoney_akshare", "hot_keyword"): _fail_fast_provider,
        ("eastmoney_akshare", "related_hot_rank"): _fail_fast_provider,
        ("eastmoney_direct", "full_hot_rank_board"): _fail_fast_provider,
    }
    return BuildSocialSentimentPack(
        config=load_frontline_provider_config(_base_env()),
        provider_call_registry=fail_fast_registry,
        evidence_client=_FailFastL2Client(),
        provider_cache_collection=_FailFastCollection(),
        provider_attempts_collection=_FailFastCollection(),
        normalized_social_collection=_FailFastCollection(),
    )


def _base_env() -> dict[str, str]:
    return {
        "CN_A_MONGODB_URI": "mongodb://localhost:27017/claw_trade",
        "CLAW_TRADE_OPENVIKING_BASE_URI": "https://openviking.internal",
        "CLAW_TRADE_OPENVIKING_AUTH_MODE": "none",
    }


def _tool_input(
    *,
    aliases: list[str] | None = None,
    approved_aliases: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "ticker": "600519.SH",
        "market": "CN_A",
        "company_name": "贵州茅台",
        "industry": "白酒",
        "start_date": "2026-05-01",
        "end_date": "2026-05-08",
        "aliases": ["茅台"] if aliases is None else aliases,
        "approved_aliases": ["茅台"] if approved_aliases is None else approved_aliases,
    }


def _runtime_context() -> ToolRuntimeContext:
    return ToolRuntimeContext(
        run_id="run-social-1",
        stage="frontline",
        worker_id="social_analyst",
        call_id="call-social-1",
        dispatch_id="dispatch-social-1",
        tool_name="social_social_sentiment_pack",
        evidence_root="/tmp/evidence",
        current_time="2026-05-09T12:00:00Z",
    )


def _attention_provider(*, target_code: str):
    def _provider(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "rows": [
                {
                    "signal_type": "attention",
                    "symbol": target_code,
                    "rank": 1,
                    "heat_value": 998.0,
                    "observed_at": "2026-05-08T10:00:00+08:00",
                }
            ]
        }

    return _provider


def _keyword_provider(*, target_code: str, keyword: str = "茅台"):
    def _provider(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "rows": [
                {
                    "signal_type": "topic_keyword",
                    "title": f"{target_code} 热点关键词",
                    "keyword": keyword,
                    "observed_at": "2026-05-08T10:10:00+08:00",
                }
            ]
        }

    return _provider


def _alias_keyword_provider():
    def _provider(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "rows": [
                {
                    "signal_type": "topic_keyword",
                    "title": "茅台 热点关键词",
                    "keyword": "茅台",
                    "observed_at": "2026-05-08T10:10:00+08:00",
                }
            ]
        }

    return _provider


def _empty_provider():
    def _provider(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"rows": []}

    return _provider
