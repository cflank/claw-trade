from __future__ import annotations

import json
from pathlib import Path

import pytest
from claw_trade.artifacts.manifest import ApprovedManifest, ArtifactFlowError
from claw_trade.config.stage_policy import load_stage_policy
from claw_trade.config.tool_names import load_tool_registry, resolve_tools
from claw_trade.workflow.models import Stage
from claw_trade.workflow.workers import all_worker_ids

_CN_A_FRONTLINE_EXPECTED = {
    "market_analyst": ("claw_request_data",),
    "fundamental_analyst": ("claw_request_data",),
    "news_analyst": ("claw_request_data",),
    "social_analyst": ("claw_request_data",),
    "policy_analyst": ("claw_request_data",),
    "hot_money_tracker": ("claw_request_data",),
    "lockup_watcher": ("claw_request_data",),
}

_FORBIDDEN_US_ATOMICS = {
    "get_stock_data",
    "get_indicators",
    "get_fundamentals",
    "get_balance_sheet",
    "get_cashflow",
    "get_income_statement",
    "get_news",
    "get_global_news",
}

_FORBIDDEN_LEGACY_TOOL_PATTERNS = (
    "provider.",
    "admin.",
    "discovery.",
    "activate_tools",
    "execute_prompt",
    "list_providers",
    "cache",
    "raw",
    "debug",
    "openviking",
    "cn_a_policy_data",
    "cn_a_hot_money_data",
    "cn_a_lockup_data",
)

_FORBIDDEN_MODEL_MESSAGE_PHRASES = (
    "数据工具已返回",
    "数据就绪状态为",
    "这只证明工具调用完成",
    "不证明资料覆盖完成",
)


def test_cn_a_frontline_visible_tools_are_exactly_one_data_need_tool() -> None:
    registry_result = load_tool_registry()
    assert registry_result.ok is True and registry_result.registry is not None
    registry = registry_result.registry
    agents_root = Path("agents")

    for worker_id, expected_tools in _CN_A_FRONTLINE_EXPECTED.items():
        policy_result = load_stage_policy(agents_root, worker_id, "CN_A")
        assert policy_result.ok is True and policy_result.policy is not None
        tools = resolve_tools(policy_result.policy, registry)
        assert tools == expected_tools
        assert _scan_openclaw_llm_provider_payload(_payload(worker_id, tools)) == tools


def test_downstream_workers_have_no_legacy_provider_tools_in_cn_a() -> None:
    registry_result = load_tool_registry()
    assert registry_result.ok is True and registry_result.registry is not None
    registry = registry_result.registry
    agents_root = Path("agents")

    for worker_id in all_worker_ids():
        policy_result = load_stage_policy(agents_root, worker_id, "CN_A")
        if not policy_result.ok or policy_result.policy is None:
            continue
        if policy_result.policy.stage == Stage.FRONTLINE:
            continue
        assert resolve_tools(policy_result.policy, registry) == ()


def test_cn_a_runtime_stage_membership_includes_new_frontline_workers() -> None:
    manifest = ApprovedManifest.empty()
    for worker_id in ("policy_analyst", "hot_money_tracker", "lockup_watcher"):
        assert manifest.for_worker_call(
            stage=Stage.FRONTLINE,
            worker_id=worker_id,
            run_id="run-1",
            market="CN_A",
        ) == ()


def test_non_cn_a_runtime_stage_membership_excludes_new_frontline_workers() -> None:
    manifest = ApprovedManifest.empty()
    for market in ("US", "HK", "CRYPTO"):
        for worker_id in ("policy_analyst", "hot_money_tracker", "lockup_watcher"):
            with pytest.raises(ArtifactFlowError, match="worker 不属于阶段"):
                manifest.for_worker_call(
                    stage=Stage.FRONTLINE,
                    worker_id=worker_id,
                    run_id="run-1",
                    market=market,
                )


@pytest.mark.parametrize("forbidden", sorted(_FORBIDDEN_US_ATOMICS))
def test_payload_scan_rejects_atomic_tools(forbidden: str) -> None:
    payload = _payload("market_analyst", (forbidden,))
    with pytest.raises(ValueError, match="forbidden tool"):
        _scan_openclaw_llm_provider_payload(payload)


@pytest.mark.parametrize(
    "forbidden",
    (
        "provider.quote",
        "admin.keys",
        "discovery.list",
        "openviking_write_material",
        "cn_a_policy_data",
        "cache_status",
        "raw_query",
        "debug_trace",
    ),
)
def test_payload_scan_rejects_legacy_admin_discovery_cache_and_alias(forbidden: str) -> None:
    payload = _payload("policy_analyst", (forbidden,))
    with pytest.raises(ValueError, match="forbidden tool"):
        _scan_openclaw_llm_provider_payload(payload)


def test_provider_requests_jsonl_sequence_2_scan_rejects_runtime_wrapper_message_text() -> None:
    jsonl = "\n".join(
        [
            json.dumps(_payload("policy_analyst", ("claw_request_data",)) | {"sequence": 1}),
            json.dumps(
                _payload("policy_analyst", ("claw_request_data",))
                | {
                    "sequence": 2,
                    "payload": {
                        "messages": [
                            {"role": "user", "content": "分析 600519"},
                            {
                                "role": "tool",
                                "content": (
                                    "数据工具已返回，但数据就绪状态为 partial；"
                                    "这只证明工具调用完成，不证明资料覆盖完成。\n"
                                    "## 政策数据结果\n资料缺口：官方原文暂缺。"
                                ),
                            },
                        ],
                        "tools": [
                            {"type": "function", "function": {"name": "claw_request_data"}},
                        ],
                    },
                },
                ensure_ascii=False,
            ),
        ]
    )

    with pytest.raises(ValueError, match="forbidden message text"):
        _scan_provider_requests_jsonl_sequence_2(jsonl)


def test_provider_requests_jsonl_sequence_2_scan_accepts_natural_model_visible_message_text() -> None:
    payload = _payload("policy_analyst", ("claw_request_data",)) | {
        "sequence": 2,
        "payload": {
            "messages": [
                {"role": "user", "content": "分析 600519"},
                {
                    "role": "tool",
                    "content": (
                        "## 政策数据\n"
                        "数据状态：部分覆盖。\n"
                        "核心事实：已取得政策新闻线索；官方原文暂缺。\n"
                        "资料缺口：缺少官方原文，不能把新闻线索写成已核验政策事实。"
                    ),
                },
            ],
            "tools": [{"type": "function", "function": {"name": "claw_request_data"}}],
        },
    }
    assert _scan_provider_requests_jsonl_sequence_2(json.dumps(payload, ensure_ascii=False)) == (
        "claw_request_data",
    )


def _payload(worker_id: str, tool_names: tuple[str, ...]) -> dict[str, object]:
    tools = [{"type": "function", "function": {"name": tool_name}} for tool_name in tool_names]
    return {
        "source": "provider_request_capture",
        "runtime_markers": {
            "run_id": "run-1",
            "call_id": "call-1",
            "worker_id": worker_id,
            "stage": "frontline",
            "profile": "CN_A",
            "openclaw_run_id": "oc-1",
        },
        "payload": {
            "messages": [{"role": "user", "content": "分析"}],
            "tools": tools,
        },
    }


def _scan_openclaw_llm_provider_payload(payload: dict[str, object]) -> tuple[str, ...]:
    source = payload.get("source")
    if source != "provider_request_capture":
        raise ValueError("openclaw_llm_provider_payload source must be provider_request_capture")

    body = payload.get("payload")
    if not isinstance(body, dict):
        raise ValueError("payload must be JSON object")
    _assert_no_forbidden_model_message_text(body.get("messages"))
    tools = body.get("tools")
    if not isinstance(tools, list):
        raise ValueError("payload.tools must be list")

    visible: list[str] = []
    forbidden_exact = _FORBIDDEN_US_ATOMICS
    for item in tools:
        if not isinstance(item, dict):
            raise ValueError("invalid tool item in payload.tools")
        function = item.get("function")
        if not isinstance(function, dict):
            raise ValueError("invalid tool item in payload.tools")
        name = function.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("invalid tool item in payload.tools")
        tool_name = name.strip()

        if tool_name in forbidden_exact:
            raise ValueError(f"forbidden tool in openclaw_llm_provider_payload: {tool_name}")
        if any(pattern in tool_name for pattern in _FORBIDDEN_LEGACY_TOOL_PATTERNS):
            raise ValueError(f"forbidden tool in openclaw_llm_provider_payload: {tool_name}")
        if tool_name != "claw_request_data":
            raise ValueError(f"forbidden tool in openclaw_llm_provider_payload: {tool_name}")

        visible.append(tool_name)
    return tuple(visible)


def _scan_provider_requests_jsonl_sequence_2(content: str) -> tuple[str, ...]:
    visible: list[str] = []
    for line in content.splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if not isinstance(record, dict):
            raise ValueError("provider-requests.jsonl line must be JSON object")
        if record.get("sequence") != 2:
            continue
        visible.extend(_scan_openclaw_llm_provider_payload(record))
    if not visible:
        raise ValueError("provider-requests.jsonl sequence=2 capture missing")
    return tuple(visible)


def _assert_no_forbidden_model_message_text(messages: object) -> None:
    if not isinstance(messages, list):
        return
    for text in _message_text_fragments(messages):
        for phrase in _FORBIDDEN_MODEL_MESSAGE_PHRASES:
            if phrase in text:
                raise ValueError(f"forbidden message text in openclaw_llm_provider_payload: {phrase}")


def _message_text_fragments(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list):
        fragments: list[str] = []
        for item in value:
            fragments.extend(_message_text_fragments(item))
        return tuple(fragments)
    if isinstance(value, dict):
        content = value.get("content")
        if isinstance(content, (str, list, dict)):
            return _message_text_fragments(content)
        text = value.get("text")
        if isinstance(text, str):
            return (text,)
    return ()
