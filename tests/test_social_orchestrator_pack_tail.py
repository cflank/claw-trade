from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest


SOCIAL_SCRIPTS_ROOT = Path("agents/social_analyst/skills/cn-a-social-data/scripts")
SOCIAL_ORCHESTRATOR_PATH = SOCIAL_SCRIPTS_ROOT / "orchestrator.py"


def test_build_social_sentiment_pack_complete_when_heat_and_keyword_have_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    provider_plan = [
        _provider_query(endpoint="stock_hot_rank_latest_em", query={"symbol": "100.600519"}, fingerprint="q-latest"),
        _provider_query(endpoint="stock_hot_keyword_em", query={"symbol": "100.600519"}, fingerprint="q-keyword"),
    ]
    _patch_provider_execution(monkeypatch, provider_plan=provider_plan, execution_results=_complete_execution_results(provider_plan))
    _patch_pack_evidence_write(monkeypatch)

    pack = ORCH_MODULE.build_social_sentiment_pack(_tool_input(), _runtime_context(), _config())

    assert pack.ok is True
    assert pack.quality.status == "complete"
    assert "后续任务执行" not in pack.reader_brief


def test_build_social_sentiment_pack_failed_when_p0_all_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    provider_plan = [
        _provider_query(endpoint="stock_hot_rank_latest_em", query={"symbol": "100.600519"}, fingerprint="q-latest"),
        _provider_query(endpoint="stock_hot_keyword_em", query={"symbol": "100.600519"}, fingerprint="q-keyword"),
    ]
    _patch_provider_execution(monkeypatch, provider_plan=provider_plan, execution_results=_all_failed_execution_results(provider_plan))
    _patch_pack_evidence_write(monkeypatch)

    pack = ORCH_MODULE.build_social_sentiment_pack(_tool_input(), _runtime_context(), _config())

    assert pack.ok is False
    assert pack.quality.status == "failed"


def test_build_social_sentiment_pack_downgrades_when_accepted_signal_missing_evidence(monkeypatch: pytest.MonkeyPatch) -> None:
    provider_plan = [_provider_query(endpoint="stock_hot_rank_latest_em", query={"symbol": "100.600519"}, fingerprint="q-latest")]
    _patch_provider_execution(monkeypatch, provider_plan=provider_plan, execution_results=_complete_execution_results(provider_plan))
    _patch_pack_evidence_write(monkeypatch)
    monkeypatch.setattr(ORCH_MODULE, "normalize_provider_rows", lambda _results: [{"provider": "akshare"}])
    monkeypatch.setattr(
        ORCH_MODULE,
        "match_deduplicate_and_bucket",
        lambda _rows, _profile, _config: SimpleNamespace(
            attention_signals=[
                {
                    "provider": "akshare",
                    "platform": "东方财富",
                    "endpoint": "stock_hot_rank_latest_em",
                    "content_hash": "",
                    "raw_payload_ref": "",
                }
            ],
            topic_keyword_signals=[],
            related_symbol_signals=[],
            narrative_signals=[],
            rejected_signals=[],
        ),
    )
    monkeypatch.setattr(
        ORCH_MODULE,
        "evaluate_social_quality",
        lambda _input: SimpleNamespace(
            ok=True,
            status="complete",
            social_judgment_allowed=True,
            missing_fields=(),
            warnings=(),
            reason_codes=("COMPLETE_CONDITIONS_MET",),
        ),
    )
    monkeypatch.setattr(
        ORCH_MODULE,
        "build_reader_brief",
        lambda _input: SimpleNamespace(text="brief"),
    )

    pack = ORCH_MODULE.build_social_sentiment_pack(_tool_input(), _runtime_context(), _config())

    assert pack.ok is False
    assert pack.quality.status == "failed"
    assert any(warning.get("code") == ORCH_MODULE.SOCIAL_EVIDENCE_REF_MISSING for warning in pack.quality.warnings)


def test_build_social_sentiment_pack_evidence_hash_recomputable(monkeypatch: pytest.MonkeyPatch) -> None:
    provider_plan = [
        _provider_query(endpoint="stock_hot_rank_latest_em", query={"symbol": "100.600519"}, fingerprint="q-latest"),
        _provider_query(endpoint="stock_hot_keyword_em", query={"symbol": "100.600519"}, fingerprint="q-keyword"),
    ]
    _patch_provider_execution(monkeypatch, provider_plan=provider_plan, execution_results=_complete_execution_results(provider_plan))
    _patch_pack_evidence_write(monkeypatch)

    pack = ORCH_MODULE.build_social_sentiment_pack(_tool_input(), _runtime_context(), _config())

    expected_hash = ORCH_MODULE.canonical_json_sha256(
        {
            "pack_path": pack.evidence.pack_path,
            "provider_attempts_path": pack.evidence.provider_attempts_path,
            "cache_inspection_path": pack.evidence.cache_inspection_path,
            "raw_payload_refs": pack.evidence.raw_payload_refs,
        }
    )
    assert pack.evidence.content_hash == expected_hash


def _patch_provider_execution(
    monkeypatch: pytest.MonkeyPatch,
    *,
    provider_plan: list[object],
    execution_results: list[object],
) -> None:
    monkeypatch.setattr(ORCH_MODULE, "build_provider_plan", lambda *_args, **_kwargs: provider_plan)
    queue = list(execution_results)

    def _run_query(**_kwargs):
        return queue.pop(0)

    monkeypatch.setattr(ORCH_MODULE, "_execute_provider_query", _run_query)


def _patch_pack_evidence_write(monkeypatch: pytest.MonkeyPatch) -> None:
    def _write_pack_evidence(**_kwargs):
        return SimpleNamespace(
            ok=True,
            evidence=SimpleNamespace(
                pack_path="viking://resources/workflow/run-1/frontline/social_analyst/call-1/evidence/pack/social_sentiment_pack.json",
                provider_attempts_path="viking://resources/workflow/run-1/frontline/social_analyst/call-1/evidence/pack/provider_attempts.json",
                cache_inspection_path="viking://resources/workflow/run-1/frontline/social_analyst/call-1/evidence/pack/cache_inspection.json",
                raw_payload_refs=[
                    "viking://resources/workflow/run-1/frontline/social_analyst/call-1/evidence/provider_raw/rank.json",
                    "viking://resources/workflow/run-1/frontline/social_analyst/call-1/evidence/provider_raw/keyword.json",
                ],
                content_hash="sha256:" + "f" * 64,
            ),
            error_code=None,
            cause_error_code=None,
            warnings=(),
        )

    monkeypatch.setattr(ORCH_MODULE, "write_pack_evidence", _write_pack_evidence)


def _complete_execution_results(provider_plan: list[object]) -> list[object]:
    results = [
        _execution_result(
            query=provider_plan[0],
            attempt=_attempt(
                endpoint="stock_hot_rank_latest_em",
                status="success",
                ok=True,
                raw_payload_ref="viking://resources/workflow/run-1/frontline/social_analyst/call-1/evidence/provider_raw/rank.json",
                payload_hash="sha256:" + "1" * 64,
            ),
            rows=[
                _normalized_row(
                    endpoint="stock_hot_rank_latest_em",
                    source_kind="heat_rank",
                    payload_hash="sha256:" + "1" * 64,
                    raw_payload_ref="viking://resources/workflow/run-1/frontline/social_analyst/call-1/evidence/provider_raw/rank.json",
                    fields={
                        "code": "600519",
                        "name": "贵州茅台",
                        "rank": 1,
                        "rank_change": 1,
                        "heat": 99.0,
                        "source_time": "2026-05-07",
                    },
                )
            ],
            raw_payload_ref="viking://resources/workflow/run-1/frontline/social_analyst/call-1/evidence/provider_raw/rank.json",
            payload_hash="sha256:" + "1" * 64,
        )
    ]
    if len(provider_plan) >= 2:
        results.append(
            _execution_result(
                query=provider_plan[1],
                attempt=_attempt(
                    endpoint="stock_hot_keyword_em",
                    status="success",
                    ok=True,
                    raw_payload_ref="viking://resources/workflow/run-1/frontline/social_analyst/call-1/evidence/provider_raw/keyword.json",
                    payload_hash="sha256:" + "2" * 64,
                ),
                rows=[
                    _normalized_row(
                        endpoint="stock_hot_keyword_em",
                        source_kind="heat_keyword",
                        payload_hash="sha256:" + "2" * 64,
                        raw_payload_ref="viking://resources/workflow/run-1/frontline/social_analyst/call-1/evidence/provider_raw/keyword.json",
                        fields={
                            "code": "600519",
                            "keyword": "茅台",
                            "title": "茅台热词",
                            "value": 88,
                            "source_time": "2026-05-07",
                        },
                    )
                ],
                raw_payload_ref="viking://resources/workflow/run-1/frontline/social_analyst/call-1/evidence/provider_raw/keyword.json",
                payload_hash="sha256:" + "2" * 64,
            )
        )
    return results


def _all_failed_execution_results(provider_plan: list[object]) -> list[object]:
    return [
        _execution_result(
            query=provider_plan[0],
            attempt=_attempt(
                endpoint="stock_hot_rank_latest_em",
                status="error",
                ok=False,
                empty_reason="SOCIAL_PROVIDER_EXCEPTION",
                error={"code": "SOCIAL_PROVIDER_EXCEPTION", "message": "provider 失败"},
            ),
            rows=[],
            raw_payload_ref=None,
            payload_hash=None,
        ),
        _execution_result(
            query=provider_plan[1],
            attempt=_attempt(
                endpoint="stock_hot_keyword_em",
                status="timeout",
                ok=False,
                empty_reason="SOCIAL_PROVIDER_TIMEOUT",
                error={"code": "SOCIAL_PROVIDER_TIMEOUT", "message": "provider 超时"},
            ),
            rows=[],
            raw_payload_ref=None,
            payload_hash=None,
        ),
    ]


def _execution_result(
    *,
    query: object,
    attempt: object,
    rows: list[dict[str, object]],
    raw_payload_ref: str | None,
    payload_hash: str | None,
) -> object:
    return ORCH_MODULE.ProviderExecutionResult(
        query=ORCH_MODULE._provider_query_to_dict(query),
        attempt=attempt,
        raw_rows=rows,
        raw_payload_ref=raw_payload_ref,
        payload_hash=payload_hash,
        cache_inspection={"status": "miss", "cache_key": "cache-key", "record": None, "reason": None, "payload_hash": payload_hash, "raw_payload_ref": raw_payload_ref},
    )


def _attempt(
    *,
    endpoint: str,
    status: str,
    ok: bool,
    raw_payload_ref: str | None = None,
    payload_hash: str | None = None,
    empty_reason: str | None = None,
    error: dict[str, str] | None = None,
) -> object:
    return ORCH_MODULE.ProviderAttempt(
        provider="akshare",
        endpoint=endpoint,
        priority="P0",
        query=json.dumps({"symbol": "100.600519"}, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        ok=ok,
        status=status,
        elapsed_ms=50,
        raw_count=1 if ok else 0,
        accepted_count=0,
        cache_status="miss",
        cache_key="cache-key",
        payload_hash=payload_hash,
        raw_payload_ref=raw_payload_ref,
        empty_reason=empty_reason,
        error=error,
        cancelled=False,
    )


def _normalized_row(
    *,
    endpoint: str,
    source_kind: str,
    payload_hash: str,
    raw_payload_ref: str,
    fields: dict[str, object],
) -> dict[str, object]:
    return {
        "provider": "akshare",
        "endpoint": endpoint,
        "platform": "东方财富",
        "source_kind": source_kind,
        "raw_index": 0,
        "fields": fields,
        "payload_hash": payload_hash,
        "raw_payload_ref": raw_payload_ref,
        "query": {"symbol": "100.600519"},
        "source_fetch_time": "2026-05-07T09:00:00+08:00",
    }


def _provider_query(*, endpoint: str, query: dict[str, object], fingerprint: str) -> object:
    return ORCH_MODULE.ProviderQuery(
        provider="akshare",
        endpoint=endpoint,
        priority="P0",
        query=query,
        query_fingerprint=fingerprint,
        date_window=ORCH_MODULE.ResolveDateWindow(
            start_date="2026-05-01",
            end_date="2026-05-07",
            current_time="2026-05-07T10:00:00+08:00",
        ),
        timeout_seconds=10,
    )


def _tool_input() -> object:
    return ORCH_MODULE.SocialToolInput(
        ticker="600519",
        market="CN_A",
        company_name="贵州茅台",
        industry="白酒",
        start_date="2026-05-01",
        end_date="2026-05-07",
    )


def _runtime_context() -> object:
    return ORCH_MODULE.SocialToolRuntimeContext(
        run_id="run-1",
        stage="frontline",
        worker_id="social_analyst",
        call_id="call-1",
        tool_name="social_social_sentiment_pack",
        evidence_root="runs",
        current_time="2026-05-07T10:00:00+08:00",
    )


def _config() -> object:
    return ORCH_MODULE.SocialDataConfig(
        schema_version="cn_a_social_pack.v1",
        provider_timeout_seconds=10,
        pack_timeout_seconds=20,
        provider_max_concurrency=3,
        max_signals_per_bucket=50,
        mongodb_uri=None,
        mongodb_database="claw_trade",
        mongodb_cache_collection="social_provider_cache",
        cache_required=False,
        ttl_by_endpoint={
            "stock_hot_rank_latest_em": 1800,
            "stock_hot_keyword_em": 3600,
            "stock_hot_rank_relate_em": 3600,
            "stock_hot_rank_em": 1800,
            "stock_hot_up_em": 1800,
        },
        p1_hot_up_enabled=False,
        p1_xueqiu_enabled=False,
        evidence_root="runs",
        openviking_l2_write_target_root=None,
    )


def _load_orchestrator_module():
    scripts_root = str(SOCIAL_SCRIPTS_ROOT.resolve())
    if scripts_root not in sys.path:
        sys.path.insert(0, scripts_root)
    module_name = "cn_a_social_orchestrator"
    spec = importlib.util.spec_from_file_location(module_name, SOCIAL_ORCHESTRATOR_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载 orchestrator 模块: {SOCIAL_ORCHESTRATOR_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


ORCH_MODULE = _load_orchestrator_module()
