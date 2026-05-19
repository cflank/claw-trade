from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Mapping

from claw_trade.reports.structure import required_section_numerals, section_heading_marker

_UNKNOWN_MODEL_MAX_OUTPUT_TOKENS = 6000
_UNKNOWN_MODEL_CONTEXT_TOKENS = 32000
_PROMPT_OVERHEAD_TOKENS = 2200
_TARGET_OUTPUT_RATIO = 0.45
_MIN_TURN_TARGET_TOKENS = 1200

_SECTION_BASE_OUTPUT_TOKENS: dict[int, int] = {
    1: 900,
    2: 1800,
    3: 1700,
    4: 1200,
    5: 1200,
    6: 1300,
    7: 1300,
    8: 600,
}
_SECTION_SOURCE_WORKERS: dict[int, tuple[str, ...]] = {
    1: ("portfolio_manager", "trader"),
    2: ("market_analyst",),
    3: ("fundamental_analyst",),
    4: ("news_analyst",),
    5: ("social_analyst",),
    6: ("trader", "risk_challenger", "risk_guardian", "risk_moderator"),
    7: (
        "bull_researcher",
        "bear_researcher",
        "research_manager",
        "risk_challenger",
        "risk_guardian",
        "risk_moderator",
        "portfolio_manager",
    ),
    8: ("portfolio_manager",),
}
_ISOLATED_SECTIONS = frozenset({3, 4, 5})


@dataclass(frozen=True)
class ModelTokenBudget:
    max_output_tokens: int
    context_window_tokens: int | None


@dataclass(frozen=True)
class FinalReportSectionPlan:
    section_numbers: tuple[int, ...]
    required_sections: tuple[str, ...]
    instruction: str
    allow_h1: bool
    estimated_output_tokens: int


@dataclass(frozen=True)
class FinalReportSegmentScope:
    required_sections: tuple[str, ...]
    allow_h1: bool


def build_final_report_section_plan(
    *,
    material_sizes_by_worker: Mapping[str, int],
    model_budget: ModelTokenBudget | None = None,
) -> tuple[FinalReportSectionPlan, ...]:
    budget = model_budget or resolve_openclaw_model_token_budget()
    input_tokens = _estimate_input_tokens(material_sizes_by_worker)
    available_output_tokens = budget.max_output_tokens
    if budget.context_window_tokens is not None:
        context_available = budget.context_window_tokens - input_tokens - _PROMPT_OVERHEAD_TOKENS
        if context_available > 0:
            available_output_tokens = min(available_output_tokens, context_available)
    target_tokens = max(_MIN_TURN_TARGET_TOKENS, int(available_output_tokens * _TARGET_OUTPUT_RATIO))

    plans: list[FinalReportSectionPlan] = []
    current_sections: list[int] = []
    current_estimate = 0

    for section_number in range(1, 9):
        section_estimate = _estimate_section_output_tokens(section_number, material_sizes_by_worker)
        if section_number in _ISOLATED_SECTIONS:
            _flush_plan(plans, current_sections, current_estimate)
            current_sections = []
            current_estimate = 0
            plans.append(_make_plan((section_number,), section_estimate))
            continue
        if current_sections and current_estimate + section_estimate > target_tokens:
            _flush_plan(plans, current_sections, current_estimate)
            current_sections = []
            current_estimate = 0
        current_sections.append(section_number)
        current_estimate += section_estimate

    _flush_plan(plans, current_sections, current_estimate)
    return tuple(plans)


def resolve_openclaw_model_token_budget() -> ModelTokenBudget:
    config_path = os.environ.get("OPENCLAW_CONFIG_PATH", "").strip()
    if not config_path:
        return ModelTokenBudget(
            max_output_tokens=_UNKNOWN_MODEL_MAX_OUTPUT_TOKENS,
            context_window_tokens=_UNKNOWN_MODEL_CONTEXT_TOKENS,
        )
    try:
        payload = json.loads(Path(config_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ModelTokenBudget(
            max_output_tokens=_UNKNOWN_MODEL_MAX_OUTPUT_TOKENS,
            context_window_tokens=_UNKNOWN_MODEL_CONTEXT_TOKENS,
        )
    model_entry = _primary_model_entry(payload)
    if not model_entry:
        return ModelTokenBudget(
            max_output_tokens=_UNKNOWN_MODEL_MAX_OUTPUT_TOKENS,
            context_window_tokens=_UNKNOWN_MODEL_CONTEXT_TOKENS,
        )
    max_output = _positive_int(model_entry.get("maxTokens")) or _UNKNOWN_MODEL_MAX_OUTPUT_TOKENS
    context_window = _positive_int(model_entry.get("contextWindow"))
    return ModelTokenBudget(max_output_tokens=max_output, context_window_tokens=context_window)


def parse_final_report_section_instruction(instruction: str) -> FinalReportSegmentScope | None:
    if not instruction.strip():
        return None
    required_sections: list[str] = []
    for section in required_section_numerals(range(1, 9)):
        if section_heading_marker(section) in instruction:
            required_sections.append(section)
    if not required_sections:
        return None
    allow_h1 = "第一行必须是唯一 H1 标题" in instruction and "严禁生成 H1 标题" not in instruction
    return FinalReportSegmentScope(required_sections=tuple(required_sections), allow_h1=allow_h1)


def _flush_plan(plans: list[FinalReportSectionPlan], section_numbers: list[int], estimate: int) -> None:
    if not section_numbers:
        return
    plans.append(_make_plan(tuple(section_numbers), estimate))


def _make_plan(section_numbers: tuple[int, ...], estimate: int) -> FinalReportSectionPlan:
    required_sections = required_section_numerals(section_numbers)
    allow_h1 = section_numbers[0] == 1
    return FinalReportSectionPlan(
        section_numbers=section_numbers,
        required_sections=required_sections,
        instruction=_section_instruction(section_numbers=section_numbers, required_sections=required_sections, allow_h1=allow_h1),
        allow_h1=allow_h1,
        estimated_output_tokens=estimate,
    )


def _section_instruction(
    *,
    section_numbers: tuple[int, ...],
    required_sections: tuple[str, ...],
    allow_h1: bool,
) -> str:
    section_names = "、".join(f"第{section}节" for section in required_sections)
    heading_list = "、".join(f"`{section_heading_marker(section)}`" for section in required_sections)
    if allow_h1:
        return (
            f"本次只撰写终稿的 H1 标题、{section_names}；第一行必须是唯一 H1 标题；"
            f"本段必须完整包含以下二级标题：{heading_list}；不要生成指定范围以外的其他编号章节正文。"
        )
    first_heading = section_heading_marker(required_sections[0])
    return (
        f"本次只撰写终稿的{section_names}；第一行必须以 `{first_heading}` 开头；"
        f"本段必须完整包含以下二级标题：{heading_list}；严禁生成 H1 标题；"
        "不要生成指定范围以外的其他编号章节正文。"
    )


def _estimate_input_tokens(material_sizes_by_worker: Mapping[str, int]) -> int:
    return sum(max(0, int(size)) for size in material_sizes_by_worker.values()) // 2


def _estimate_section_output_tokens(section_number: int, material_sizes_by_worker: Mapping[str, int]) -> int:
    base = _SECTION_BASE_OUTPUT_TOKENS[section_number]
    source_chars = sum(max(0, int(material_sizes_by_worker.get(worker_id, 0))) for worker_id in _SECTION_SOURCE_WORKERS[section_number])
    source_tokens = source_chars // 2
    compressed = int(source_tokens * 0.28)
    return max(base, min(base * 2, compressed))


def _primary_model_entry(payload: object) -> dict[str, object] | None:
    if not isinstance(payload, dict):
        return None
    agents = payload.get("agents")
    if not isinstance(agents, dict):
        return None
    defaults = agents.get("defaults")
    if not isinstance(defaults, dict):
        return None
    model = defaults.get("model")
    if not isinstance(model, dict):
        return None
    primary = model.get("primary")
    if not isinstance(primary, str) or "/" not in primary:
        return None
    provider_id, model_id = primary.split("/", 1)
    models = payload.get("models")
    if not isinstance(models, dict):
        return None
    providers = models.get("providers")
    if not isinstance(providers, dict):
        return None
    provider = providers.get(provider_id)
    if not isinstance(provider, dict):
        return None
    provider_models = provider.get("models")
    if not isinstance(provider_models, list):
        return None
    for entry in provider_models:
        if isinstance(entry, dict) and entry.get("id") == model_id:
            return entry
    return None


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    return None
