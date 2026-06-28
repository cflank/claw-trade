from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


UI_CHAT_AGENT_ID = "ui_chat"
UI_WORKER_CHAT_AGENT_ID = "ui_worker_chat"

WORKER_IDS = (
    "market_analyst",
    "fundamental_analyst",
    "news_analyst",
    "social_analyst",
    "policy_analyst",
    "hot_money_tracker",
    "lockup_watcher",
    "bull_researcher",
    "bear_researcher",
    "research_manager",
    "trader",
    "risk_challenger",
    "risk_guardian",
    "risk_moderator",
    "portfolio_manager",
    "report_polisher",
    "selection_strategist",
    "selection_skeptic",
    "selection_manager",
    "selection_portfolio_manager",
    "price_alert_scan_worker",
    "scheduled_report_runner",
    "market_data_maintenance_worker",
)

CONTEXT_FREE_WORKER_IDS = {
    "price_alert_scan_worker",
    "scheduled_report_runner",
    "market_data_maintenance_worker",
}

PLUGIN_IDS = (
    "claw-trade-frontline-tools",
    "claw-trade-selection-tools",
    "claw-trade-scheduled-work-tools",
)


@dataclass(frozen=True)
class LlmConfig:
    provider_id: str
    model: str
    provider_model_id: str
    provider_name: str
    api_key: str
    base_url: str | None = None
    api: str = "openai-completions"
    context_window: int | None = None
    max_tokens: int | None = None


def prepare_openclaw_config(
    *,
    config_path: Path,
    agents_root: Path,
    plugins_root: Path,
    gateway_token: str = "",
    llm_idle_timeout_seconds: int = 600,
    env: Mapping[str, str] | None = None,
) -> None:
    env = os.environ if env is None else env
    _validate_positive_int("OPENCLAW_LLM_IDLE_TIMEOUT_SECONDS", llm_idle_timeout_seconds)
    _require_runtime_assets(agents_root=agents_root, plugins_root=plugins_root)

    existing = _read_json_object(config_path)
    llm = _resolve_runtime_report_model(env) or _resolve_project_llm(env)

    existing_models = _object(existing.get("models"))
    existing_providers = _object(existing_models.get("providers"))
    existing_agents = _object(existing.get("agents"))
    existing_defaults = _object(existing_agents.get("defaults"))
    existing_plugins = _object(existing.get("plugins"))
    existing_plugin_entries = _object(existing_plugins.get("entries"))
    existing_plugin_allow = _string_list(existing_plugins.get("allow"))

    has_saved_llm = bool(existing_providers) or bool(_object(existing_defaults.get("model")))
    if llm is None and not has_saved_llm:
        print(
            "[WARN] OpenClaw LLM 未配置：UI 可启动；报告执行会被报告模型 gate 阻断。",
            file=sys.stderr,
        )

    merged_providers = dict(existing_providers)
    if llm is not None:
        provider_entry: dict[str, Any] = {
            "apiKey": llm.api_key,
            "api": llm.api,
            "timeoutSeconds": llm_idle_timeout_seconds,
            "models": [
                {
                    "id": llm.provider_model_id,
                    "name": llm.provider_name,
                    "reasoning": False,
                    "input": ["text"],
                }
            ],
        }
        if llm.base_url:
            provider_entry["baseUrl"] = llm.base_url
        if llm.context_window:
            provider_entry["models"][0]["contextWindow"] = llm.context_window
        if llm.max_tokens:
            provider_entry["models"][0]["maxTokens"] = llm.max_tokens
        merged_providers[llm.provider_id] = provider_entry

    merged_models = {**existing_models, "providers": merged_providers}

    merged_defaults = {
        **existing_defaults,
        "workspace": str(agents_root),
        "skipBootstrap": True,
    }
    if llm is not None:
        merged_defaults["model"] = {"primary": llm.model}
        merged_defaults["models"] = {llm.model: {"alias": llm.provider_name}}

    merged_plugin_entries = dict(existing_plugin_entries)
    for plugin_id in PLUGIN_IDS:
        merged_plugin_entries[plugin_id] = {
            **_object(existing_plugin_entries.get(plugin_id)),
            "enabled": True,
        }
    existing_weixin = _object(existing_plugin_entries.get("openclaw-weixin"))
    merged_plugin_entries["openclaw-weixin"] = {**existing_weixin, "enabled": True}
    if llm is not None:
        merged_plugin_entries[llm.provider_id] = {
            **_object(existing_plugin_entries.get(llm.provider_id)),
            "enabled": True,
        }

    required_plugin_allow = [
        "acpx",
        "bonjour",
        "browser",
        "device-pair",
        "file-transfer",
        "memory-core",
        "phone-control",
        "talk-voice",
        *PLUGIN_IDS,
        "openclaw-weixin",
    ]
    if llm is not None:
        required_plugin_allow.append(llm.provider_id)

    merged_plugins = {
        "enabled": True,
        "allow": _unique([*existing_plugin_allow, *required_plugin_allow]),
        "load": {"paths": [str(plugins_root / plugin_id) for plugin_id in PLUGIN_IDS]},
        "entries": merged_plugin_entries,
    }

    existing_channels = _object(existing.get("channels"))
    existing_weixin_channel = _object(existing_channels.get("openclaw-weixin"))
    weixin_enabled = False if existing_weixin_channel.get("enabled") is False else True
    merged_channels = {
        **existing_channels,
        "openclaw-weixin": {
            **existing_weixin_channel,
            "enabled": weixin_enabled,
            "replyProgressMessages": True,
        },
    }

    existing_messages = _object(existing.get("messages"))
    existing_inbound = _object(existing_messages.get("inbound"))
    existing_by_channel = _object(existing_inbound.get("byChannel"))
    merged_messages = {
        **existing_messages,
        "inbound": {
            **existing_inbound,
            "byChannel": {**existing_by_channel, "webchat": 0},
        },
    }

    config: dict[str, Any] = {
        "gateway": {
            "mode": "local",
            "bind": "loopback",
        },
        "agents": {
            "defaults": merged_defaults,
            "list": [
                _ui_chat_agent(agents_root),
                _ui_worker_chat_agent(agents_root),
                *[_worker_agent(agents_root, worker_id) for worker_id in WORKER_IDS],
            ],
        },
        "models": merged_models,
        "plugins": merged_plugins,
        "channels": merged_channels,
        "messages": merged_messages,
        "mcp": {"servers": {}},
    }
    token = gateway_token.strip()
    if token:
        config["gateway"]["auth"] = {"token": token}
        config["gateway"]["remote"] = {"token": token}
    if isinstance(existing.get("meta"), Mapping):
        config["meta"] = dict(existing["meta"])

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _require_runtime_assets(*, agents_root: Path, plugins_root: Path) -> None:
    if not (agents_root / UI_CHAT_AGENT_ID / "AGENTS.md").is_file():
        raise RuntimeError(f"UI agent 资产缺失：{agents_root / UI_CHAT_AGENT_ID / 'AGENTS.md'}")
    if not (agents_root / UI_WORKER_CHAT_AGENT_ID / "AGENTS.md").is_file():
        raise RuntimeError(f"UI worker agent 资产缺失：{agents_root / UI_WORKER_CHAT_AGENT_ID / 'AGENTS.md'}")
    for worker_id in WORKER_IDS:
        manifest_path = agents_root / worker_id / "skills" / "manifest.yaml"
        if not manifest_path.is_file():
            raise RuntimeError(f"worker skill manifest 不存在：{manifest_path}")
    for plugin_id in PLUGIN_IDS:
        plugin_entry = plugins_root / plugin_id / "index.js"
        if not plugin_entry.is_file():
            raise RuntimeError(f"OpenClaw 插件入口不存在：{plugin_entry}")


def _ui_chat_agent(agents_root: Path) -> dict[str, Any]:
    return {
        "id": UI_CHAT_AGENT_ID,
        "default": True,
        "workspace": str(agents_root / UI_CHAT_AGENT_ID),
        "contextInjection": "never",
        "systemPromptOverride": "\n".join(
            [
                "你是 claw-trade UI 的普通聊天助手。",
                "用中文直接回答用户普通消息。",
                "回答要短；普通寒暄、身份说明、界面解释不超过两句话。",
                "只输出纯文本，不使用 Markdown、加粗星号、标题、表格或代码块。",
                "不要进入报告工作流，不要调度或模拟 worker。",
                "用户明确使用 /report、/select 或 @worker 时，由 UI 路由处理。",
            ]
        ),
        "skills": [],
        "tools": {"allow": []},
    }


def _ui_worker_chat_agent(agents_root: Path) -> dict[str, Any]:
    return {
        "id": UI_WORKER_CHAT_AGENT_ID,
        "default": False,
        "workspace": str(agents_root / UI_WORKER_CHAT_AGENT_ID),
        "contextInjection": "never",
        "systemPromptOverride": "\n".join(
            [
                "你是 claw-trade UI 的普通 worker 聊天助手。",
                "用中文直接回答，回答要短；普通寒暄不超过两句话。",
                "只输出纯文本，不使用 Markdown、加粗星号、标题、表格或代码块。",
                "只回答普通聊天，不进入 /report 投资报告工作流。",
                "不要生成正式报告，不输出 Run ID、Profile、Status、artifact 或报告执行摘要。",
                "不要调用数据、搜索、交易、消息或报告工具。",
                "用户消息会提供 worker_id 和 worker_display_name；按该 worker 的视角、职责边界和口吻回答。",
            ]
        ),
        "skills": [],
        "tools": {"allow": []},
    }


def _worker_agent(agents_root: Path, worker_id: str) -> dict[str, Any]:
    agent: dict[str, Any] = {
        "id": worker_id,
        "default": False,
        "workspace": str(agents_root / worker_id),
        "skills": _read_mounted_skills(agents_root / worker_id / "skills" / "manifest.yaml"),
    }
    if worker_id in CONTEXT_FREE_WORKER_IDS:
        agent["contextInjection"] = "never"
    return agent


_MANIFEST_PATH_RE = re.compile(r"^\s*-\s+path:\s+(.+?)\s*$")


def _read_mounted_skills(manifest_path: Path) -> list[str]:
    skills: list[str] = []
    seen: set[str] = set()
    for line in manifest_path.read_text(encoding="utf-8").splitlines():
        match = _MANIFEST_PATH_RE.match(line)
        if match is None:
            continue
        skill_path = match.group(1).strip().strip("\"'")
        if skill_path.startswith("/") or ".." in Path(skill_path).parts:
            raise RuntimeError(f"worker skill path 非法：{manifest_path} -> {skill_path}")
        skill_id = re.sub(r"/SKILL\.md$", "", skill_path, flags=re.IGNORECASE).strip()
        if skill_id and skill_id not in seen:
            seen.add(skill_id)
            skills.append(skill_id)
    if not skills:
        raise RuntimeError(f"worker skill manifest 没有可挂载 skill：{manifest_path}")
    return skills


def _resolve_runtime_report_model(env: Mapping[str, str]) -> LlmConfig | None:
    provider = _optional(env.get("CLAW_TRADE_RUNTIME_REPORT_MODEL_PROVIDER")).lower()
    model = _optional(env.get("CLAW_TRADE_RUNTIME_REPORT_MODEL_MODEL"))
    api_key = _optional(env.get("CLAW_TRADE_RUNTIME_REPORT_MODEL_API_KEY"))
    if not provider and not model and not api_key:
        return None
    if not provider or not model or not api_key:
        raise RuntimeError("Mongo 报告模型配置不完整：缺少 provider/model/apiKey。")
    normalized = _normalize_provider_model(provider, model)
    provider_model_id = _optional(env.get("CLAW_TRADE_RUNTIME_REPORT_MODEL_PROVIDER_MODEL_ID")) or _model_suffix(normalized)
    return LlmConfig(
        provider_id=provider,
        model=normalized,
        provider_model_id=provider_model_id,
        provider_name=_optional(env.get("CLAW_TRADE_RUNTIME_REPORT_MODEL_PROVIDER_NAME")) or provider_model_id,
        api_key=api_key,
        base_url=_optional(env.get("CLAW_TRADE_RUNTIME_REPORT_MODEL_BASE_URL")),
        api=_optional(env.get("CLAW_TRADE_RUNTIME_REPORT_MODEL_API")) or "openai-completions",
    )


def _resolve_project_llm(env: Mapping[str, str]) -> LlmConfig | None:
    provider = _optional(env.get("CLAW_TRADE_LLM_PROVIDER")).lower()
    configured_model = _optional(env.get("CLAW_TRADE_LLM_MODEL")) or _optional(env.get("DEEPSEEK_MODEL"))
    deepseek_key = _optional(env.get("DEEPSEEK_API_KEY"))
    qwen_key = _first_non_empty(env.get("QWEN_API_KEY"), env.get("MODELSTUDIO_API_KEY"), env.get("DASHSCOPE_API_KEY"))
    qwen_model = _first_non_empty(env.get("QWEN_MODEL"), env.get("MODELSTUDIO_MODEL"), env.get("DASHSCOPE_MODEL"))

    selected_model = _select_project_model(
        configured_model=configured_model,
        provider=provider,
        deepseek_key=deepseek_key,
        qwen_key=qwen_key,
        qwen_model=qwen_model,
    )
    if not selected_model:
        return None

    if selected_model.startswith("deepseek/"):
        if not deepseek_key:
            raise RuntimeError("缺少 DEEPSEEK_API_KEY，无法生成 OpenClaw runtime LLM 配置。")
        provider_model_id = _model_suffix(selected_model)
        return LlmConfig(
            provider_id="deepseek",
            model=selected_model,
            provider_model_id=provider_model_id,
            provider_name="DeepSeek Chat" if provider_model_id == "deepseek-chat" else provider_model_id,
            api_key=deepseek_key,
            base_url=_optional(env.get("DEEPSEEK_BASE_URL")) or "https://api.deepseek.com",
            context_window=131072,
            max_tokens=8192,
        )

    if selected_model.startswith(("qwen/", "dashscope/", "modelstudio/", "qwencloud/")):
        if not qwen_key:
            raise RuntimeError("缺少 QWEN_API_KEY / MODELSTUDIO_API_KEY / DASHSCOPE_API_KEY，无法生成 OpenClaw runtime LLM 配置。")
        provider_model_id = _model_suffix(selected_model)
        return LlmConfig(
            provider_id="qwen",
            model=f"qwen/{provider_model_id}",
            provider_model_id=provider_model_id,
            provider_name=provider_model_id,
            api_key=qwen_key,
            base_url=_optional(env.get("QWEN_BASE_URL"))
            or _optional(env.get("DASHSCOPE_BASE_URL"))
            or "https://dashscope.aliyuncs.com/compatible-mode/v1",
            context_window=1000000,
            max_tokens=65536,
        )

    raise RuntimeError(f"当前 LLM provider 暂未接入 OpenClaw runtime 配置生成：{selected_model}")


def _select_project_model(
    *,
    configured_model: str,
    provider: str,
    deepseek_key: str,
    qwen_key: str,
    qwen_model: str,
) -> str:
    if configured_model:
        if "/" in configured_model:
            return configured_model
        if provider:
            return _normalize_provider_model(provider, configured_model)
        if deepseek_key and not qwen_key:
            return _normalize_provider_model("deepseek", configured_model)
        if qwen_key and not deepseek_key:
            return _normalize_provider_model("qwen", configured_model)
        raise RuntimeError("CLAW_TRADE_LLM_MODEL 没有 provider 前缀；请写成 deepseek/... 或 qwen/...")
    if qwen_model:
        return _normalize_provider_model("qwen", qwen_model)
    if deepseek_key:
        return "deepseek/deepseek-chat"
    if qwen_key:
        return "qwen/qwen3.5-plus"
    return ""


def _normalize_provider_model(provider: str, model: str) -> str:
    provider = provider.strip().lower()
    model = model.strip()
    if not model:
        return ""
    if "/" in model:
        return model
    if provider in {"dashscope", "modelstudio", "qwencloud"}:
        return f"qwen/{model}"
    return f"{provider}/{model}"


def _model_suffix(model: str) -> str:
    suffix = "/".join(model.split("/")[1:]).strip()
    if not suffix:
        raise RuntimeError(f"model 配置非法：{model}")
    return suffix


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return dict(value) if isinstance(value, Mapping) else {}


def _object(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _unique(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out


def _optional(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _first_non_empty(*values: object) -> str:
    for value in values:
        text = _optional(value)
        if text:
            return text
    return ""


def _validate_positive_int(name: str, value: int) -> None:
    if value <= 0:
        raise RuntimeError(f"{name} 必须是正整数，当前值：{value}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config-path", required=True)
    parser.add_argument("--agents-root", required=True)
    parser.add_argument("--plugins-root", required=True)
    parser.add_argument("--gateway-token", default=os.environ.get("OPENCLAW_GATEWAY_TOKEN", ""))
    parser.add_argument(
        "--llm-idle-timeout-seconds",
        type=int,
        default=int(os.environ.get("OPENCLAW_LLM_IDLE_TIMEOUT_SECONDS", "600")),
    )
    args = parser.parse_args(argv)
    prepare_openclaw_config(
        config_path=Path(args.config_path),
        agents_root=Path(args.agents_root),
        plugins_root=Path(args.plugins_root),
        gateway_token=args.gateway_token,
        llm_idle_timeout_seconds=args.llm_idle_timeout_seconds,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
