from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from math import isfinite
from pathlib import Path
from typing import Any

_PER_MILLION = Decimal("1000000")
_DEEPSEEK_FLASH_MODEL_NAMES = {
    "deepseek-chat",
    "deepseek-reasoner",
    "deepseek-v4-flash",
}
_DEEPSEEK_PRO_MODEL_NAMES = {
    "deepseek-v4-pro",
}
_DEEPSEEK_FLASH_PRICING = (Decimal("0.02"), Decimal("1"), Decimal("2"))
_DEEPSEEK_PRO_PRICING = (Decimal("0.025"), Decimal("3"), Decimal("6"))
_OPENCLAW_STATE_AGENT_ROOT = Path(".runtime/dev-services/openclaw-state/agents")


@dataclass(frozen=True)
class TaskCostSnapshot:
    state: str
    provider: str | None = None
    currency: str = "CNY"
    balance: Decimal | None = None
    checked_at: str | None = None
    reason: str | None = None

    @classmethod
    def captured(
        cls,
        *,
        provider: str,
        balance: Decimal,
        currency: str = "CNY",
        checked_at: str | None = None,
    ) -> "TaskCostSnapshot":
        return cls(
            state="captured",
            provider=provider,
            currency=currency,
            balance=balance,
            checked_at=checked_at,
        )

    @classmethod
    def unavailable(cls, *, reason: str, provider: str | None = None) -> "TaskCostSnapshot":
        return cls(state="unavailable", provider=provider, reason=reason)


@dataclass(frozen=True)
class TaskTokenCostSummary:
    state: str
    provider: str = "deepseek"
    model: str | None = None
    cache_hit_input_tokens: int = 0
    cache_miss_input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cache_hit_input_price_per_million: Decimal | None = None
    cache_miss_input_price_per_million: Decimal | None = None
    output_price_per_million: Decimal | None = None
    cache_hit_input_cost: Decimal | None = None
    cache_miss_input_cost: Decimal | None = None
    output_cost: Decimal | None = None
    total_cost: Decimal | None = None
    source_call_count: int = 0
    missing_usage_call_count: int = 0
    reason: str | None = None

    @classmethod
    def estimated(
        cls,
        *,
        model: str | None,
        cache_hit_input_tokens: int,
        cache_miss_input_tokens: int,
        output_tokens: int,
        source_call_count: int,
        missing_usage_call_count: int = 0,
    ) -> "TaskTokenCostSummary":
        prices = _deepseek_pricing_for_model(model)
        cache_hit_price, cache_miss_price, output_price = prices or (None, None, None)
        cache_hit_cost = _token_cost(cache_hit_input_tokens, cache_hit_price) if cache_hit_price is not None else None
        cache_miss_cost = _token_cost(cache_miss_input_tokens, cache_miss_price) if cache_miss_price is not None else None
        output_cost = _token_cost(output_tokens, output_price) if output_price is not None else None
        total_cost = (
            cache_hit_cost + cache_miss_cost + output_cost
            if cache_hit_cost is not None and cache_miss_cost is not None and output_cost is not None
            else None
        )
        return cls(
            state="estimated",
            model=model,
            cache_hit_input_tokens=cache_hit_input_tokens,
            cache_miss_input_tokens=cache_miss_input_tokens,
            output_tokens=output_tokens,
            total_tokens=cache_hit_input_tokens + cache_miss_input_tokens + output_tokens,
            cache_hit_input_price_per_million=cache_hit_price,
            cache_miss_input_price_per_million=cache_miss_price,
            output_price_per_million=output_price,
            cache_hit_input_cost=cache_hit_cost,
            cache_miss_input_cost=cache_miss_cost,
            output_cost=output_cost,
            total_cost=total_cost,
            source_call_count=source_call_count,
            missing_usage_call_count=missing_usage_call_count,
        )

    @classmethod
    def unavailable(
        cls,
        *,
        reason: str,
        model: str | None = None,
        source_call_count: int = 0,
        missing_usage_call_count: int = 0,
    ) -> "TaskTokenCostSummary":
        return cls(
            state="unavailable",
            model=model,
            reason=reason,
            source_call_count=source_call_count,
            missing_usage_call_count=missing_usage_call_count,
        )

    @classmethod
    def no_model_call(cls) -> "TaskTokenCostSummary":
        return cls(state="no_model_call", reason="no_model_call")

    def to_user_payload(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "provider": self.provider,
            "model": self.model,
            "cacheHitInputTokens": self.cache_hit_input_tokens,
            "cacheMissInputTokens": self.cache_miss_input_tokens,
            "outputTokens": self.output_tokens,
            "totalTokens": self.total_tokens,
            "cacheHitInputPricePerMillion": _decimal_text(self.cache_hit_input_price_per_million),
            "cacheMissInputPricePerMillion": _decimal_text(self.cache_miss_input_price_per_million),
            "outputPricePerMillion": _decimal_text(self.output_price_per_million),
            "cacheHitInputCost": _decimal_text(self.cache_hit_input_cost),
            "cacheMissInputCost": _decimal_text(self.cache_miss_input_cost),
            "outputCost": _decimal_text(self.output_cost),
            "totalCost": _decimal_text(self.total_cost),
            "sourceCallCount": self.source_call_count,
            "missingUsageCallCount": self.missing_usage_call_count,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class _UsageTokens:
    cache_hit_input_tokens: int = 0
    cache_miss_input_tokens: int = 0
    output_tokens: int = 0

    def has_tokens(self) -> bool:
        return (
            self.cache_hit_input_tokens > 0
            or self.cache_miss_input_tokens > 0
            or self.output_tokens > 0
        )


@dataclass(frozen=True)
class _UsageCollection:
    tokens: _UsageTokens
    source_call_count: int
    missing_usage_call_count: int


@dataclass(frozen=True)
class TaskCostEstimate:
    state: str
    currency: str = "CNY"
    estimated_cost: Decimal | None = None
    balance_before: Decimal | None = None
    balance_after: Decimal | None = None
    reason: str | None = None
    token_summary: TaskTokenCostSummary | None = None

    @classmethod
    def estimated(
        cls,
        *,
        estimated_cost: Decimal,
        balance_before: Decimal,
        balance_after: Decimal,
        token_summary: TaskTokenCostSummary | None = None,
    ) -> "TaskCostEstimate":
        return cls(
            state="estimated",
            estimated_cost=estimated_cost,
            balance_before=balance_before,
            balance_after=balance_after,
            token_summary=token_summary,
        )

    @classmethod
    def unavailable(
        cls,
        *,
        reason: str,
        token_summary: TaskTokenCostSummary | None = None,
    ) -> "TaskCostEstimate":
        return cls(state="unavailable", reason=reason, token_summary=token_summary)

    def to_user_payload(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "currency": self.currency,
            "estimatedCost": _money_text(self.estimated_cost) if self.estimated_cost is not None else None,
            "balanceBefore": _money_text(self.balance_before) if self.balance_before is not None else None,
            "balanceAfter": _money_text(self.balance_after) if self.balance_after is not None else None,
            "reason": self.reason,
            "tokenSummary": self.token_summary.to_user_payload() if self.token_summary is not None else None,
        }


def estimate_task_cost(
    start: TaskCostSnapshot | None,
    finish: TaskCostSnapshot | None,
    *,
    token_summary: TaskTokenCostSummary | None = None,
) -> TaskCostEstimate:
    if start is None:
        return TaskCostEstimate.unavailable(reason="start_balance_missing", token_summary=token_summary)
    if finish is None:
        return TaskCostEstimate.unavailable(reason="finish_balance_missing", token_summary=token_summary)
    if start.state != "captured" or start.balance is None:
        return TaskCostEstimate.unavailable(
            reason=start.reason or "start_balance_unavailable",
            token_summary=token_summary,
        )
    if finish.state != "captured" or finish.balance is None:
        return TaskCostEstimate.unavailable(
            reason=finish.reason or "finish_balance_unavailable",
            token_summary=token_summary,
        )
    if start.currency != "CNY" or finish.currency != "CNY":
        return TaskCostEstimate.unavailable(reason="cny_balance_missing", token_summary=token_summary)
    delta = start.balance - finish.balance
    if delta < Decimal("0"):
        return TaskCostEstimate(
            state="unavailable",
            reason="balance_increased",
            balance_before=start.balance,
            balance_after=finish.balance,
            token_summary=token_summary,
        )
    return TaskCostEstimate.estimated(
        estimated_cost=delta,
        balance_before=start.balance,
        balance_after=finish.balance,
        token_summary=token_summary,
    )


def collect_token_cost_summary_from_path(
    root: Path | None,
    *,
    openclaw_state_agent_root: Path | None = None,
) -> TaskTokenCostSummary | None:
    if root is None or not root.exists():
        return None
    resolved_openclaw_state_agent_root = openclaw_state_agent_root or _default_openclaw_state_agent_root(root)
    result_paths = sorted(root.glob("**/openclaw-result.json"))
    if not result_paths:
        return TaskTokenCostSummary.unavailable(reason="usage_file_missing")

    cache_hit = 0
    cache_miss = 0
    output = 0
    observed_deepseek_calls = 0
    covered_deepseek_calls = 0
    missing_usage = 0
    unsupported_calls = 0
    model: str | None = None
    for result_path in result_paths:
        payload = _read_json_object(result_path)
        if payload is None:
            continue
        provider, call_model = _provider_and_model_from_result(payload, result_path=result_path)
        if not _is_deepseek_call(provider=provider, model=call_model):
            unsupported_calls += 1
            continue
        model = model or call_model
        usage_collection = _usage_collection_from_result(
            payload,
            openclaw_state_agent_root=resolved_openclaw_state_agent_root,
        )
        if usage_collection is None:
            observed_deepseek_calls += 1
            missing_usage += 1
            continue
        covered_deepseek_calls += usage_collection.source_call_count
        observed_deepseek_calls += (
            usage_collection.source_call_count + usage_collection.missing_usage_call_count
        )
        missing_usage += usage_collection.missing_usage_call_count
        if usage_collection.tokens.has_tokens():
            cache_hit += usage_collection.tokens.cache_hit_input_tokens
            cache_miss += usage_collection.tokens.cache_miss_input_tokens
            output += usage_collection.tokens.output_tokens

    if observed_deepseek_calls == 0:
        if unsupported_calls > 0:
            return TaskTokenCostSummary.unavailable(
                reason="unsupported_provider",
                source_call_count=unsupported_calls,
            )
        return None
    if cache_hit == 0 and cache_miss == 0 and output == 0:
        return TaskTokenCostSummary.unavailable(
            reason="usage_missing",
            model=model,
            source_call_count=covered_deepseek_calls,
            missing_usage_call_count=missing_usage,
        )
    return TaskTokenCostSummary.estimated(
        model=model,
        cache_hit_input_tokens=cache_hit,
        cache_miss_input_tokens=cache_miss,
        output_tokens=output,
        source_call_count=covered_deepseek_calls,
        missing_usage_call_count=missing_usage,
    )


def append_task_cost_line(text: str, estimate: TaskCostEstimate | None) -> str:
    line = render_task_cost_line(estimate)
    if not line:
        return text
    clean = text.rstrip()
    return f"{clean}\n{line}" if clean else line


def render_task_cost_line(estimate: TaskCostEstimate | None) -> str:
    if estimate is None:
        return ""
    token_label, token_text = _token_total_text(estimate.token_summary)
    return (
        "费用统计："
        f"{token_label}：{token_text}；"
        f"任务前余额：{_balance_text(estimate.balance_before)}；"
        f"任务后余额：{_balance_text(estimate.balance_after)}；"
        f"本次消费：{_consumption_text(estimate)}"
    )


def _cost_money_text(value: Decimal) -> str:
    if Decimal("0") < value < Decimal("0.01"):
        return "<¥0.01"
    return f"¥{_money_text(value)}"


def _money_text(value: Decimal) -> str:
    rounded = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return format(rounded, "f")


def _decimal_text(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value.normalize(), "f")


def _token_total_text(summary: TaskTokenCostSummary | None) -> tuple[str, str]:
    if summary is not None and summary.reason == "no_model_call":
        return "Token 总数", "0"
    if summary is not None and summary.state == "estimated":
        label = "Token 总数（已统计）" if summary.missing_usage_call_count else "Token 总数"
        return label, _tokens_text(summary.total_tokens)
    return "Token 总数", "未知"


def _balance_text(value: Decimal | None) -> str:
    if value is None:
        return "未知"
    return f"¥{_money_text(value)}"


def _consumption_text(estimate: TaskCostEstimate) -> str:
    if estimate.token_summary is not None and estimate.token_summary.reason == "no_model_call":
        return "¥0"
    if estimate.state == "estimated" and estimate.estimated_cost is not None:
        return _cost_money_text(estimate.estimated_cost)
    return "未知"


def _tokens_text(value: int) -> str:
    return f"{value:,}"


def _token_cost(tokens: int, price_per_million: Decimal) -> Decimal:
    return (Decimal(max(0, tokens)) * price_per_million) / _PER_MILLION


def _deepseek_pricing_for_model(model: str | None) -> tuple[Decimal, Decimal, Decimal] | None:
    normalized = _normalize_model_name(model)
    if normalized in _DEEPSEEK_PRO_MODEL_NAMES:
        return _DEEPSEEK_PRO_PRICING
    if normalized in _DEEPSEEK_FLASH_MODEL_NAMES:
        return _DEEPSEEK_FLASH_PRICING
    return None


def _normalize_model_name(model: str | None) -> str:
    text = (model or "").strip().lower()
    if "/" in text:
        text = text.rsplit("/", 1)[-1]
    return text


def _is_deepseek_call(*, provider: str | None, model: str | None) -> bool:
    provider_text = (provider or "").strip().lower()
    model_text = (model or "").strip().lower()
    if provider_text:
        return provider_text == "deepseek"
    return model_text.startswith("deepseek/") or _normalize_model_name(model) in (
        _DEEPSEEK_FLASH_MODEL_NAMES | _DEEPSEEK_PRO_MODEL_NAMES
    )


def _provider_and_model_from_result(
    payload: dict[str, Any],
    *,
    result_path: Path,
) -> tuple[str | None, str | None]:
    provider = _string_or_none(payload.get("provider"))
    model = _string_or_none(payload.get("model"))
    request_payload = _provider_request_payload(payload, result_path=result_path)
    provider_model = request_payload.get("provider_model") if request_payload is not None else None
    if isinstance(provider_model, dict):
        provider = provider or _string_or_none(provider_model.get("provider"))
        model = (
            model
            or _string_or_none(provider_model.get("id"))
            or _string_or_none(provider_model.get("name"))
        )
    return provider, model


def _default_openclaw_state_agent_root(root: Path) -> Path:
    resolved_root = root.resolve()
    for candidate in (resolved_root, *resolved_root.parents):
        state_root = candidate / _OPENCLAW_STATE_AGENT_ROOT
        if state_root.exists():
            return state_root
    return _OPENCLAW_STATE_AGENT_ROOT


def _provider_request_payload(
    payload: dict[str, Any],
    *,
    result_path: Path,
) -> dict[str, Any] | None:
    provider_request_path = _string_or_none(payload.get("provider_request_path"))
    if provider_request_path is None:
        return None
    request_path = Path(provider_request_path)
    if not request_path.is_absolute():
        request_path = result_path.parent / request_path
    return _read_json_object(request_path)


def _usage_collection_from_result(
    payload: dict[str, Any],
    *,
    openclaw_state_agent_root: Path | None,
) -> _UsageCollection | None:
    direct_usage = _usage_tokens_from_payload(payload.get("usage"))
    if direct_usage is not None:
        return _UsageCollection(
            tokens=direct_usage,
            source_call_count=1,
            missing_usage_call_count=0,
        )
    if isinstance(payload.get("usage"), dict):
        return _UsageCollection(
            tokens=_UsageTokens(),
            source_call_count=1,
            missing_usage_call_count=1,
        )
    openclaw_run_id = _string_or_none(payload.get("openclaw_run_id"))
    if openclaw_run_id is None:
        return None
    return _usage_collection_from_trajectory(
        openclaw_run_id,
        openclaw_state_agent_root=openclaw_state_agent_root,
    )


def _usage_collection_from_trajectory(
    openclaw_run_id: str,
    *,
    openclaw_state_agent_root: Path | None,
) -> _UsageCollection | None:
    if openclaw_state_agent_root is None or not openclaw_state_agent_root.exists():
        return None
    trajectory_paths = sorted(
        openclaw_state_agent_root.glob(
            f"*/sessions/single-worker-{openclaw_run_id}.trajectory.jsonl"
        )
    )
    if not trajectory_paths:
        trajectory_paths = sorted(openclaw_state_agent_root.glob(f"*/sessions/*{openclaw_run_id}*.trajectory.jsonl"))
    total_tokens = _UsageTokens()
    source_call_count = 0
    missing_usage_call_count = 0
    for trajectory_path in trajectory_paths:
        trajectory_usage = _usage_collection_from_trajectory_path(trajectory_path, openclaw_run_id)
        if trajectory_usage is None:
            continue
        source_call_count += trajectory_usage.source_call_count
        missing_usage_call_count += trajectory_usage.missing_usage_call_count
        total_tokens = _UsageTokens(
            cache_hit_input_tokens=total_tokens.cache_hit_input_tokens
            + trajectory_usage.tokens.cache_hit_input_tokens,
            cache_miss_input_tokens=total_tokens.cache_miss_input_tokens
            + trajectory_usage.tokens.cache_miss_input_tokens,
            output_tokens=total_tokens.output_tokens + trajectory_usage.tokens.output_tokens,
        )
    if source_call_count == 0 and missing_usage_call_count == 0:
        return None
    return _UsageCollection(
        tokens=total_tokens,
        source_call_count=source_call_count,
        missing_usage_call_count=missing_usage_call_count,
    )


def _usage_collection_from_trajectory_path(
    trajectory_path: Path,
    openclaw_run_id: str,
) -> _UsageCollection | None:
    total_tokens = _UsageTokens()
    source_call_count = 0
    missing_usage_call_count = 0
    try:
        with trajectory_path.open(encoding="utf-8") as file:
            for line in file:
                if "model.completed" not in line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict):
                    continue
                event_type = _string_or_none(event.get("type")) or _string_or_none(event.get("event"))
                if event_type != "model.completed":
                    continue
                event_run_id = _string_or_none(event.get("runId"))
                if event_run_id is not None and event_run_id != openclaw_run_id:
                    continue
                data = event.get("data")
                usage_source = data.get("usage") if isinstance(data, dict) else event.get("usage")
                usage_tokens = _usage_tokens_from_payload(usage_source)
                if usage_tokens is None:
                    missing_usage_call_count += 1
                    continue
                source_call_count += 1
                total_tokens = _UsageTokens(
                    cache_hit_input_tokens=total_tokens.cache_hit_input_tokens
                    + usage_tokens.cache_hit_input_tokens,
                    cache_miss_input_tokens=total_tokens.cache_miss_input_tokens
                    + usage_tokens.cache_miss_input_tokens,
                    output_tokens=total_tokens.output_tokens + usage_tokens.output_tokens,
                )
    except (OSError, UnicodeDecodeError):
        return None
    if source_call_count == 0 and missing_usage_call_count == 0:
        return None
    return _UsageCollection(
        tokens=total_tokens,
        source_call_count=source_call_count,
        missing_usage_call_count=missing_usage_call_count,
    )


def _usage_tokens_from_payload(value: object) -> _UsageTokens | None:
    if not isinstance(value, dict):
        return None

    normalized = _usage_tokens_from_openclaw_usage(value)
    if normalized is not None:
        return normalized

    deepseek = _usage_tokens_from_deepseek_usage(value)
    if deepseek is not None:
        return deepseek

    return _usage_tokens_from_openai_usage(value)


def _usage_tokens_from_openclaw_usage(usage: dict[str, Any]) -> _UsageTokens | None:
    hit_value = _nonnegative_int(usage.get("cacheRead"))
    miss_value = _nonnegative_int(usage.get("input"))
    output_value = _nonnegative_int(usage.get("output"))
    if hit_value is None and miss_value is None and output_value is None:
        return None
    tokens = _UsageTokens(
        cache_hit_input_tokens=hit_value or 0,
        cache_miss_input_tokens=miss_value or 0,
        output_tokens=output_value or 0,
    )
    return tokens if tokens.has_tokens() else None


def _usage_tokens_from_deepseek_usage(usage: dict[str, Any]) -> _UsageTokens | None:
    hit_value = _nonnegative_int(usage.get("prompt_cache_hit_tokens"))
    miss_value = _nonnegative_int(usage.get("prompt_cache_miss_tokens"))
    output_value = _nonnegative_int(usage.get("completion_tokens"))
    if hit_value is None and miss_value is None and output_value is None:
        return None
    tokens = _UsageTokens(
        cache_hit_input_tokens=hit_value or 0,
        cache_miss_input_tokens=miss_value or 0,
        output_tokens=output_value or 0,
    )
    return tokens if tokens.has_tokens() else None


def _usage_tokens_from_openai_usage(usage: dict[str, Any]) -> _UsageTokens | None:
    prompt_tokens = _nonnegative_int(usage.get("prompt_tokens"))
    output_tokens = _nonnegative_int(usage.get("completion_tokens"))
    if prompt_tokens is None and output_tokens is None:
        return None
    prompt_details = usage.get("prompt_tokens_details")
    cached_tokens = (
        _nonnegative_int(prompt_details.get("cached_tokens"))
        if isinstance(prompt_details, dict)
        else 0
    )
    prompt_value = prompt_tokens or 0
    cache_hit = min(cached_tokens or 0, prompt_value)
    tokens = _UsageTokens(
        cache_hit_input_tokens=cache_hit,
        cache_miss_input_tokens=max(0, prompt_value - cache_hit),
        output_tokens=output_tokens or 0,
    )
    return tokens if tokens.has_tokens() else None


def _read_json_object(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _string_or_none(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _nonnegative_int(value: object) -> int | None:
    if not isinstance(value, int | float) or not isfinite(value):
        return None
    return max(0, int(value))
