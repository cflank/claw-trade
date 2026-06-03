from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from claw_trade.config.report_workflow_settings import ReportWorkflowSettings
from claw_trade.runtime.openclaw_client import OpenClawClient
from claw_trade.runtime.openclaw_local_runner import create_default_runner
from claw_trade.selection.confirmation import (
    SelectionConfirmationController,
    SelectionConfirmRequest,
)
from claw_trade.selection.controller import SelectCommandCode, SelectionController
from claw_trade.selection.models import (
    CandidatePackManifest,
    CandidatePackReadbackStatus,
    CandidatePackRef,
    SelectionDataRun,
    SelectionDataRunStatus,
    SelectionMarket,
    SelectionProfile,
    SelectionRunPlan,
    SelectionTriggerSource,
)
from claw_trade.selection.store import SelectionDataRunRecord, SelectionRunStore
from claw_trade.ui_backend.report_queue import ReportTaskQueue
from claw_trade.ui_backend.workflow_bridge import ReportWorkflowBridge
from claw_trade.web.state import _ControlWorkflowRunner

_FORBIDDEN_PROTOCOL_TERMS = (
    "raw/debug",
    "provider envelope",
    "mongo",
    "openviking",
    "viking://",
    "material_id",
    "manifest",
    "lineage",
    "sha256",
    "hash",
    "refs",
    "runtime wrapper",
)
_CANDIDATE_PACK_BODY_MARKERS = (
    "# 候选池事实包",
    "## 候选事实表",
    "| 排名 | 代码 | 公司",
)
_SKEPTIC_PROMPT_MATERIAL_MARKERS = ("【approved_strategist_l1】",)
_MANAGER_PROMPT_MATERIAL_MARKERS = (
    "【approved_strategist_l1】",
    "【approved_skeptic_l1】",
    "【candidate_pack_summary】",
)
_PORTFOLIO_MANAGER_PROMPT_MATERIAL_MARKERS = (
    "【approved_manager_l1】",
    "【approved_strategist_l1】",
    "【approved_skeptic_l1】",
    "【candidate_pack_summary】",
)
_WORKER_ORDER = (
    "selection_strategist",
    "selection_skeptic",
    "selection_manager",
    "selection_portfolio_manager",
)
_EXPECTED_TOOLS = {
    "selection_strategist": {"claw_get_selection_candidate_pack"},
    "selection_skeptic": {"claw_get_selection_candidate_pack"},
    "selection_manager": set(),
    "selection_portfolio_manager": set(),
}
_SELECTION_WORKERS = (
    "selection_strategist",
    "selection_skeptic",
    "selection_manager",
    "selection_portfolio_manager",
)
_READER_FORBIDDEN_CANDIDATE_FACT_TERMS = (
    "liquidity_tradability_score",
    "amount=",
    '"amount":',
    "close=",
    '"close":',
    "strategy_hit_count",
    "data_gap_penalty_score",
    "risk_penalty_score",
)
_READER_REQUIRED_CANDIDATE_FACT_TERMS = (
    "成交额",
    "收盘价",
    "数据缺口扣分",
    "流动性/可交易性",
)
_LIVE_SELECTION_ACCEPTANCE_ENV = "CLAW_TRADE_RUN_LIVE_SELECTION_ACCEPTANCE"
_LLM_AUTH_ENV_KEYS = (
    "OPENAI_API_KEY",
    "DEEPSEEK_API_KEY",
    "QWEN_API_KEY",
    "DASHSCOPE_API_KEY",
    "MODELSTUDIO_API_KEY",
    "CLAW_TRADE_RUNTIME_REPORT_MODEL_API_KEY",
)


def _assert_candidate_fact_body_is_reader_chinese(text: str) -> None:
    for term in _READER_FORBIDDEN_CANDIDATE_FACT_TERMS:
        assert term not in text
    for term in _READER_REQUIRED_CANDIDATE_FACT_TERMS:
        assert term in text


def _require_live_selection_acceptance_enabled() -> None:
    if os.environ.get(_LIVE_SELECTION_ACCEPTANCE_ENV) != "1":
        pytest.skip(f"set {_LIVE_SELECTION_ACCEPTANCE_ENV}=1 and run through scripts/start-control-runtime.sh for live proof")


def _skip_if_openclaw_llm_auth_missing(reason: str | None) -> None:
    text = reason or ""
    if "No API key found for provider" in text:
        pytest.skip(f"OpenClaw LLM auth missing: {text}")


def _skip_if_openclaw_llm_auth_not_configured() -> None:
    if any(os.environ.get(key) for key in _LLM_AUTH_ENV_KEYS):
        return
    state_dir = Path(os.environ.get("OPENCLAW_STATE_DIR", ".runtime/dev-services/openclaw-state"))
    config_path = Path(os.environ.get("OPENCLAW_CONFIG_PATH", str(state_dir / "openclaw.json")))
    if _openclaw_config_has_model_provider_key(config_path):
        return
    if any(_json_file_has_auth_secret(path) for path in state_dir.glob("agents/*/agent/auth-profiles.json")):
        return
    pytest.skip("OpenClaw LLM auth missing: no LLM API key env var, provider apiKey, or agent auth profile")


def _openclaw_config_has_model_provider_key(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(payload, dict):
        return False
    models = payload.get("models")
    if not isinstance(models, dict):
        return False
    providers = models.get("providers")
    if not isinstance(providers, dict):
        return False
    return _json_value_has_auth_secret(providers)


def _json_file_has_auth_secret(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return _json_value_has_auth_secret(payload)


def _json_value_has_auth_secret(value: object) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).replace("_", "").replace("-", "").lower()
            if normalized in {"apikey", "token", "accesskey", "secretkey"} and str(item or "").strip():
                return True
            if _json_value_has_auth_secret(item):
                return True
    if isinstance(value, list):
        return any(_json_value_has_auth_secret(item) for item in value)
    return False


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _openclaw_cli_bin() -> str:
    configured = os.environ.get("OPENCLAW_GATEWAY_CALL_BIN", "").strip()
    if configured:
        return configured
    return str(_project_root() / "third_party" / "openclaw" / "openclaw.mjs")


def _list_openclaw_agent_ids() -> set[str]:
    env = _openclaw_runtime_env()
    completed = subprocess.run(
        [_openclaw_cli_bin(), "agents", "list", "--json"],
        cwd=_project_root(),
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    payload = json.loads(completed.stdout)
    if not isinstance(payload, list):
        raise AssertionError(f"BLOCKED_ASK_HUMAN: openclaw agents list returned invalid payload: {type(payload).__name__}")
    ids: set[str] = set()
    for item in payload:
        if isinstance(item, dict):
            value = item.get("id")
            if isinstance(value, str) and value.strip():
                ids.add(value.strip())
    return ids


def _openclaw_runtime_env() -> dict[str, str]:
    env = dict(os.environ)
    runtime_env_path = _project_root() / ".runtime" / "dev-services" / "runtime.env"
    if runtime_env_path.is_file():
        for raw_line in runtime_env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, raw_value = line.split("=", 1)
            key = key.strip()
            if key in {"OPENCLAW_STATE_DIR", "OPENCLAW_CONFIG_PATH"} and key not in env:
                env[key] = raw_value.strip().strip("'\"")
    return env


def _ensure_selection_agents_registered() -> None:
    existing_ids = _list_openclaw_agent_ids()
    missing = [worker_id for worker_id in _SELECTION_WORKERS if worker_id not in existing_ids]
    if missing:
        raise AssertionError(
            "BLOCKED_ASK_HUMAN: selection workers missing in runtime agent registry: "
            + ", ".join(missing)
        )


def _selection_artifact_root() -> Path:
    root = _project_root() / ".runtime" / "test-artifacts" / "selection-sel11"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _build_store(artifact_root: Path) -> tuple[SelectionRunStore, str]:
    store = SelectionRunStore()
    run_id = "sel-11-live-data-run"
    candidate_pack_body = "\n".join(
        [
            "# 候选池事实包",
            "",
            "## 本轮范围",
            "- 交易日：2026-05-26",
            "- 市场：CN_A",
            "- 候选数量：3",
            "- 策略配置版本：cn_a.selection_strategy.v1",
            "- 权重版本：cn_a.selection_weights.v1",
            "",
            "## 候选事实表",
            "| 排名 | 代码 | 公司 | 行业 | 总分 | 分项得分 | 策略来源 | 策略变体 | 命中字段 | 实际指标值 | 风险扣分 | 数据缺口扣分 | 排序 tie-break 字段 |",
            "| --- | --- | --- | --- | ---: | --- | --- | --- | --- | --- | ---: | ---: | --- |",
            "| 1 | 600519.SH | 贵州茅台 | 酿酒行业 | 91.000000 | 流动性/可交易性=15 | myhhub/stock | myhhub_volume_rise | 成交额=3000000000 | 成交额=3000000000, 收盘价=1612 | 0.000000 | 0.000000 | 成交额=3000000000 |",
            "| 2 | 000858.SZ | 五粮液 | 酿酒行业 | 83.000000 | 流动性/可交易性=12 | Sequoia-X | sequoia_ma_volume | 成交额=900000000 | 成交额=900000000, 收盘价=132 | 0.000000 | 1.000000 | 成交额=900000000 |",
            "| 3 | 300750.SZ | 宁德时代 | 电力设备 | 67.000000 | RPS/趋势强度=8 | - | - | - | 成交额=500000000, 收盘价=240 | 2.000000 | 3.000000 | 成交额=500000000 |",
            "",
            "## 数据质量摘要",
            "- 数据批次已批准",
            "- 仅含事实字段",
        ]
    ) + "\n"
    summary_path = artifact_root / "candidate-pack-summary.md"
    summary_path.write_text(candidate_pack_body, encoding="utf-8")
    pack_body_path = artifact_root / "candidate-pack-approved.md"
    pack_body_sha = _write_verified_text(pack_body_path, candidate_pack_body)
    _write_verified_text(
        artifact_root / "candidate-pack.json",
        json.dumps(
            {
                "schema_version": "sel-04-candidate-pack-v1",
                "selection_run_id": run_id,
                "market": SelectionMarket.CN_A.value,
                "trade_date": "2026-05-26",
                "candidate_count": 3,
                "strategy_config_version": "cn_a.selection_strategy.v1",
                "weight_version": "cn_a.selection_weights.v1",
                "data_quality_summary": "数据质量：本批次未发现阻断级或提示级缺口。",
                "source_summary": "来源摘要：交易日全市场标准化快照、特征快照与确定性评分结果。",
                "candidates": [
                    _candidate_pack_json_row(
                        rank=1,
                        ticker="600519.SH",
                        company_name="贵州茅台",
                        industry="酿酒行业",
                        score=91.0,
                        amount=3000000000,
                        close=1612,
                        strategy_hit="myhhub/stock::myhhub_volume_rise",
                    ),
                    _candidate_pack_json_row(
                        rank=2,
                        ticker="000858.SZ",
                        company_name="五粮液",
                        industry="酿酒行业",
                        score=83.0,
                        amount=900000000,
                        close=132,
                        strategy_hit="Sequoia-X::sequoia_ma_volume",
                    ),
                    _candidate_pack_json_row(
                        rank=3,
                        ticker="300750.SZ",
                        company_name="宁德时代",
                        industry="电力设备",
                        score=67.0,
                        amount=500000000,
                        close=240,
                        strategy_hit="",
                    ),
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )
    manifest_path = artifact_root / "candidate-pack-manifest.json"
    manifest_payload = {
        "schema_version": "sel-04-candidate-pack-v1",
        "selection_run_id": run_id,
        "market": SelectionMarket.CN_A.value,
        "profile": SelectionProfile.CN_A.value,
        "trade_date": "2026-05-26",
        "candidate_count": 3,
        "source_lineage_refs": ["lineage://sel-11-live"],
        "pack_body_sha256": pack_body_sha,
        "strategy_config_ref": "config://approved",
        "strategy_config_version": "cn_a.selection_strategy.v1",
        "weight_version": "cn_a.selection_weights.v1",
        "readback_status": CandidatePackReadbackStatus.VERIFIED.value,
        "stage": "approving_candidate_pack",
        "target": "candidate_pack",
    }
    _write_verified_text(
        manifest_path,
        json.dumps(manifest_payload, ensure_ascii=False, indent=2) + "\n",
    )
    now = datetime.now(tz=UTC)
    approved_at = now.replace(microsecond=0).isoformat()
    expires_at = datetime(2099, 1, 1, tzinfo=UTC).isoformat()
    candidate_pack_ref = CandidatePackRef(
        selection_run_id=run_id,
        material_id="selection-candidate-pack-sel-11-live",
        l1_uri=str(pack_body_path),
        content_sha256=pack_body_sha,
        manifest_ref=str(manifest_path),
        approved_at=approved_at,
        expires_at=expires_at,
        pack_summary_ref=str(summary_path),
    )
    store.save_data_run_record(
        SelectionDataRunRecord(
            run_plan=SelectionRunPlan(
                selection_run_id=run_id,
                market=SelectionMarket.CN_A,
                profile=SelectionProfile.CN_A,
                trade_date="2026-05-26",
                lookback_trading_days=260,
                universe_scope="all_a_shares",
                provider_batch_plan_ref="plan://sel-11-live",
                approved_strategy_config_ref="config://approved",
                trigger_source=SelectionTriggerSource.SCHEDULED,
            ),
            data_run=SelectionDataRun(
                selection_run_id=run_id,
                status=SelectionDataRunStatus.COMPLETED,
                normalized_refs=("normalized://mongo/normalized_datasets/sel-11-live",),
                provider_attempt_refs=("attempt://sel-11-live",),
                select_data_plan_ref=f"select-data-plan://selection/{run_id}/2026-05-26",
                warehouse_check_ref=f"warehouse-check://selection/{run_id}/2026-05-26/ok",
                candidate_pack_ref=candidate_pack_ref,
                completed_at=approved_at,
            ),
            manifest=CandidatePackManifest(
                schema_version="sel-04-candidate-pack-v1",
                selection_run_id=run_id,
                market=SelectionMarket.CN_A,
                profile=SelectionProfile.CN_A,
                trade_date="2026-05-26",
                candidate_count=3,
                source_lineage_refs=("lineage://sel-11-live",),
                pack_body_sha256=pack_body_sha,
                strategy_config_ref="config://approved",
                readback_status=CandidatePackReadbackStatus.VERIFIED,
                stage="approving_candidate_pack",
                target="candidate_pack",
            ),
        )
    )
    return store, run_id


def _candidate_pack_json_row(
    *,
    rank: int,
    ticker: str,
    company_name: str,
    industry: str,
    score: float,
    amount: int,
    close: float,
    strategy_hit: str,
) -> dict[str, Any]:
    strategy_hits = [strategy_hit] if strategy_hit else []
    feature_values = {
        "amount": amount,
        "close": close,
        "liquidity_tradability_score": 15.0,
        "rps_trend_score": 8.0,
        "risk_penalty_score": 0.0,
        "data_gap_penalty_score": 0.0,
        "strategy_required_field_count": 64,
        "strategy_missing_field_count": 0,
    }
    return {
        "rank": rank,
        "ticker": ticker,
        "company_name": company_name,
        "industry": industry,
        "total_score": score,
        "strategy_hits": strategy_hits,
        "feature_values": feature_values,
        "component_scores": {
            "liquidity_tradability_score": feature_values["liquidity_tradability_score"],
            "rps_trend_score": feature_values["rps_trend_score"],
        },
        "hit_fields": {"amount": amount} if strategy_hits else {},
        "actual_metric_values": {"amount": amount, "close": close},
        "risk_penalty": feature_values["risk_penalty_score"],
        "data_gap_penalty": feature_values["data_gap_penalty_score"],
        "tie_break_fields": {"amount": amount},
        "data_quality": "完整",
        "source_summary": "标准化行情与财务快照",
    }


def _write_verified_text(path: Path, content: str) -> str:
    path.write_text(content, encoding="utf-8")
    digest = sha256(content.encode("utf-8")).hexdigest()
    verify_path = path.with_suffix(f"{path.suffix}.readback-verify.json")
    verify_path.write_text(
        json.dumps(
            {
                "path": str(path),
                "status": "verified",
                "expected_sha256": digest,
                "readback_sha256": digest,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return digest


def _flatten_message_text(messages: list[dict[str, object]]) -> str:
    chunks: list[str] = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, str):
            chunks.append(content)
            continue
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str):
                        chunks.append(text)
                elif isinstance(item, str):
                    chunks.append(item)
    return "\n".join(chunks)


def _extract_tools(raw: object) -> set[str]:
    if raw is None:
        return set()
    if not isinstance(raw, list):
        raise AssertionError(f"tools payload type invalid: {type(raw).__name__}")
    names: set[str] = set()
    for item in raw:
        if isinstance(item, str):
            if item.strip():
                names.add(item.strip())
            continue
        if isinstance(item, dict):
            direct = item.get("name")
            if isinstance(direct, str) and direct.strip():
                names.add(direct.strip())
                continue
            function = item.get("function")
            if isinstance(function, dict):
                fn_name = function.get("name")
                if isinstance(fn_name, str) and fn_name.strip():
                    names.add(fn_name.strip())
                    continue
        raise AssertionError(f"tools item missing name: {item!r}")
    return names


def _assert_tools_and_boundary(*, worker_id: str, provider_payload: dict[str, object]) -> None:
    payload = provider_payload.get("payload")
    assert isinstance(payload, dict), "provider payload missing payload object"
    messages = payload.get("messages")
    assert isinstance(messages, list) and messages, "provider payload.messages missing"
    text = _flatten_message_text(messages)

    for forbidden in _FORBIDDEN_PROTOCOL_TERMS:
        if worker_id in {"selection_manager", "selection_portfolio_manager"}:
            assert forbidden.lower() not in text.lower(), f"{worker_id} payload leaked forbidden text: {forbidden}"

    if worker_id == "selection_strategist":
        for marker in _CANDIDATE_PACK_BODY_MARKERS:
            assert marker not in text, f"{worker_id} payload preloaded candidate-pack full body"
    if worker_id == "selection_skeptic":
        for marker in _SKEPTIC_PROMPT_MATERIAL_MARKERS:
            assert marker in text, f"skeptic payload missing marker: {marker}"
    elif worker_id == "selection_manager":
        for marker in _MANAGER_PROMPT_MATERIAL_MARKERS:
            assert marker in text, f"manager payload missing marker: {marker}"
    elif worker_id == "selection_portfolio_manager":
        for marker in _PORTFOLIO_MANAGER_PROMPT_MATERIAL_MARKERS:
            assert marker in text, f"portfolio manager payload missing marker: {marker}"


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def test_select_live_acceptance_provider_payload_and_handoff() -> None:
    _require_live_selection_acceptance_enabled()
    artifact_root = _selection_artifact_root()
    workflow_root = artifact_root / "selection-workflows"
    workflow_root.mkdir(parents=True, exist_ok=True)
    store, selection_run_id = _build_store(artifact_root)
    _ensure_selection_agents_registered()
    _skip_if_openclaw_llm_auth_not_configured()

    openclaw = OpenClawClient(create_default_runner())
    probe = openclaw.probe()
    assert probe.ok, f"BLOCKED_ASK_HUMAN: openclaw probe failed: {probe.reason}"

    controller = SelectionController(
        store=store,
        now_fn=lambda: datetime(2026, 5, 26, 12, 0, tzinfo=UTC),
        openclaw=openclaw,
        workflow_evidence_root=workflow_root,
    )
    live_request_id = f"sel-11-live-acceptance-{uuid4().hex[:8]}"
    result = controller.handle_select_command(raw_text="/select 2026-05-26", request_id=live_request_id)
    _skip_if_openclaw_llm_auth_missing(result.failure_reason)
    assert result.code == SelectCommandCode.COMPLETED, (
        "BLOCKED_ASK_HUMAN: /select live run not completed, "
        f"code={result.code.value}, reason={result.failure_reason}, evidence={result.evidence_path}"
    )
    assert result.evidence_path.is_file(), f"selection workflow evidence missing: {result.evidence_path}"

    workflow_payload = json.loads(result.evidence_path.read_text(encoding="utf-8"))
    assert workflow_payload.get("status") == "completed"
    assert workflow_payload.get("reason") == "waiting_report_confirmation"
    assert workflow_payload.get("selection_run_id") == selection_run_id
    dispatches = workflow_payload.get("dispatches")
    assert isinstance(dispatches, list) and len(dispatches) == 4, "dispatch evidence count must be 4"

    worker_ids = [str(item.get("worker_id")) for item in dispatches if isinstance(item, dict)]
    assert worker_ids == list(_WORKER_ORDER), f"dispatch order mismatch: {worker_ids}"

    dispatch_summary: list[dict[str, object]] = []
    for item in dispatches:
        assert isinstance(item, dict), "dispatch evidence item must be object"
        worker_id = str(item.get("worker_id") or "").strip()
        dispatch_id = str(item.get("dispatch_id") or "").strip()
        evidence_dir = Path(str(item.get("evidence_dir") or "")).resolve()
        command_snapshot_path = Path(str(item.get("command_snapshot_path") or "")).resolve()
        provider_request_path = (evidence_dir / "provider-request.json").resolve()
        visible_tools_path = (evidence_dir / "visible-tools.json").resolve()
        tool_calls_path = (evidence_dir / "tool-calls.json").resolve()
        raw_output_path = (evidence_dir / "raw-output.md").resolve()

        for path in (evidence_dir, command_snapshot_path, provider_request_path, visible_tools_path, tool_calls_path, raw_output_path):
            assert path.exists(), f"required live evidence path missing: {path}"

        command_snapshot = json.loads(command_snapshot_path.read_text(encoding="utf-8"))
        assert command_snapshot.get("run_id") == result.select_workflow_run_id
        assert command_snapshot.get("call_id") == dispatch_id
        assert command_snapshot.get("worker_id") == worker_id
        assert command_snapshot.get("system_context_policy") == "single_worker_minimal"

        provider_request = json.loads(provider_request_path.read_text(encoding="utf-8"))
        visible_tools = json.loads(visible_tools_path.read_text(encoding="utf-8"))
        tool_calls = json.loads(tool_calls_path.read_text(encoding="utf-8"))

        assert provider_request.get("source") == "provider_request_capture"
        assert visible_tools.get("source") == "provider_request"
        assert Path(str(visible_tools.get("provider_request_path") or "")).resolve() == provider_request_path

        runtime_markers = provider_request.get("runtime_markers")
        assert isinstance(runtime_markers, dict), "provider_request.runtime_markers missing"
        assert runtime_markers.get("run_id") == result.select_workflow_run_id
        assert runtime_markers.get("call_id") == dispatch_id
        assert runtime_markers.get("worker_id") == worker_id

        visible_markers = visible_tools.get("runtime_markers")
        assert isinstance(visible_markers, dict), "visible_tools.runtime_markers missing"
        assert visible_markers.get("run_id") == result.select_workflow_run_id
        assert visible_markers.get("call_id") == dispatch_id
        assert visible_markers.get("worker_id") == worker_id

        expected_tools = _EXPECTED_TOOLS[worker_id]
        provider_tool_names = _extract_tools((provider_request.get("payload") or {}).get("tools"))  # type: ignore[union-attr]
        visible_tool_names = _extract_tools(visible_tools.get("tools"))
        assert provider_tool_names == expected_tools, f"provider tools mismatch for {worker_id}: {provider_tool_names}"
        assert visible_tool_names == expected_tools, f"visible tools mismatch for {worker_id}: {visible_tool_names}"

        _assert_tools_and_boundary(worker_id=worker_id, provider_payload=provider_request)

        assert tool_calls.get("source") == "model_tool_events"
        calls = tool_calls.get("calls")
        assert isinstance(calls, list), "tool_calls.calls must be list"
        if worker_id in {"selection_strategist", "selection_skeptic"}:
            assert tool_calls.get("status") == "recorded"
            called_tools = {str(call.get("tool_name") or "").strip() for call in calls if isinstance(call, dict)}
            assert "claw_get_selection_candidate_pack" in called_tools, (
                f"{worker_id} did not invoke candidate-pack tool; calls={called_tools}"
            )
        else:
            assert tool_calls.get("status") in {"none", "recorded"}
            assert calls == [], f"{worker_id} should not invoke tools"

        dispatch_summary.append(
            {
                "worker_id": worker_id,
                "dispatch_id": dispatch_id,
                "evidence_dir": str(evidence_dir),
                "provider_request_path": str(provider_request_path),
                "visible_tools_path": str(visible_tools_path),
                "command_snapshot_path": str(command_snapshot_path),
                "tool_calls_path": str(tool_calls_path),
                "raw_output_path": str(raw_output_path),
            }
        )

    decision_payload = workflow_payload.get("decision")
    assert isinstance(decision_payload, dict), "decision payload missing"
    enter_report_raw = decision_payload.get("enter_report")
    assert isinstance(enter_report_raw, list), "enter_report missing"
    enter_report = [str(ticker).strip().upper() for ticker in enter_report_raw if str(ticker).strip()]
    zero_enter_report_branch = len(enter_report) == 0

    reader_artifact_path = artifact_root / f"{result.select_workflow_run_id}-select-reader-artifact.md"
    reader_text = result.chat_text
    _assert_candidate_fact_body_is_reader_chinese(reader_text)
    reader_artifact_path.write_text(reader_text.strip() + "\n", encoding="utf-8")
    assert reader_artifact_path.is_file()

    handoff_evidence_path: Path | None = None
    if zero_enter_report_branch:
        assert re.search(r"进入\s*`?/report`?：\s*\n-\s*(?:无|暂无|空)(?:\n|$)", reader_text), (
            "reader artifact must show empty enter-report section when enter_report is empty"
        )
    else:
        selected_ticker = enter_report[0]
        assert selected_ticker, "enter_report ticker invalid"

        report_workflow_root = artifact_root / "report-workflows"
        report_workflow_root.mkdir(parents=True, exist_ok=True)
        queue = ReportTaskQueue(ReportWorkflowBridge(_ControlWorkflowRunner(run_dir=report_workflow_root)))
        confirmation_controller = SelectionConfirmationController(
            store=store,
            queue=queue,
            settings=ReportWorkflowSettings(),
            workflow_evidence_root=workflow_root,
            now_fn=lambda: datetime(2026, 5, 26, 12, 1, tzinfo=UTC),
            today_fn=lambda: "2026-05-26",
        )
        confirmation_id = "sel-11-live-confirm-1"
        confirm_request = SelectionConfirmRequest(
            confirmation_id=confirmation_id,
            idempotency_key=f"{result.select_workflow_run_id}:{selected_ticker}:{confirmation_id}",
            select_workflow_run_id=result.select_workflow_run_id,
            ticker=selected_ticker,
        )
        confirm_result = confirmation_controller.confirm(confirm_request)
        assert confirm_result.code == "report_handoff_started"
        assert confirm_result.confirmation.status.value == "report_handoff_started"
        assert confirm_result.handoff_request["selectionStageMarker"] == "selection_report_handoff"
        assert confirm_result.handoff_request["reportRequest"]["entryPoint"] == "report_command"
        assert isinstance(confirm_result.report_run_id, str) and confirm_result.report_run_id.startswith("run-")
        report_run_dir = report_workflow_root / confirm_result.report_run_id
        report_run_state_path = report_run_dir / "state.json"
        report_run_request_path = report_run_dir / "request.json"
        assert report_run_state_path.is_file(), f"report run state missing: {report_run_state_path}"
        assert report_run_request_path.is_file(), f"report run request missing: {report_run_request_path}"

        handoff_evidence_path = artifact_root / f"{result.select_workflow_run_id}-confirmation-handoff-evidence.json"
        _write_json(
            handoff_evidence_path,
            {
                "select_workflow_run_id": result.select_workflow_run_id,
                "selection_run_id": selection_run_id,
                "confirmation_id": confirmation_id,
                "ticker": selected_ticker,
                "report_handoff_dedupe_key": confirm_result.report_handoff_dedupe_key,
                "report_task_id": confirm_result.report_task_id,
                "report_run_id": confirm_result.report_run_id,
                "handoff_request": confirm_result.handoff_request,
                "queue_payload": confirm_result.queue_payload,
                "deduped": confirm_result.deduped,
                "report_run_state_path": str(report_run_state_path.resolve()),
                "report_run_request_path": str(report_run_request_path.resolve()),
            },
        )
        assert handoff_evidence_path.is_file()

    summary_path = artifact_root / "sel-11-live-summary.json"
    _write_json(
        summary_path,
        {
            "select_workflow_run_id": result.select_workflow_run_id,
            "selection_run_id": selection_run_id,
            "selection_workflow_evidence_path": str(result.evidence_path.resolve()),
            "provider_payload_dispatches": dispatch_summary,
            "select_reader_artifact_path": str(reader_artifact_path.resolve()),
            "confirmation_handoff_evidence_path": (
                str(handoff_evidence_path.resolve()) if handoff_evidence_path is not None else None
            ),
            "zero_enter_report_branch": zero_enter_report_branch,
        },
    )
    assert summary_path.is_file()
