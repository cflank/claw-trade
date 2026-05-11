from __future__ import annotations

from dataclasses import dataclass

FUNDAMENTAL_WORKER_ID = "fundamental_analyst"
FUNDAMENTAL_MARKET_PROFILE = "CN_A"
APPROVED_VISIBLE_TOOLS = frozenset(
    {
        "fundamental_fundamentals_data_pack",
    }
)
FORBIDDEN_PROVIDER_TOOLS = frozenset(
    {
        "tushare.*",
        "akshare.*",
        "baostock.*",
        "FinanceMCP.*",
    }
)

FND_BOUNDARY_INVESTMENT_CONCLUSION_FORBIDDEN = "FND_BOUNDARY_INVESTMENT_CONCLUSION_FORBIDDEN"
FND_BOUNDARY_CONTROL_GATE_FORBIDDEN = "FND_BOUNDARY_CONTROL_GATE_FORBIDDEN"
FND_BOUNDARY_OPERATION_UNKNOWN = "FND_BOUNDARY_OPERATION_UNKNOWN"

_INVESTMENT_SEMANTIC_TOKENS = frozenset(
    {
        "rating",
        "target_price",
        "buy_hold_sell",
        "investment_conclusion",
        "report_body",
        "pm_decision",
        "buy",
        "sell",
        "hold",
    }
)
_CONTROL_PLANE_SEMANTIC_TOKENS = frozenset(
    {
        "approve",
        "reject",
        "artifact",
        "workflow",
        "control_gate",
        "gate",
        "rerun",
        "terminate",
    }
)
_ALLOWED_OPERATION_PREFIXES = (
    "collect",
    "fetch",
    "inspect",
    "map",
    "build",
    "compute",
    "classify",
    "validate",
    "sanitize",
    "record",
    "write_raw_payload",
    "write_cache_record",
)
_ALLOWED_EXACT_OPERATIONS = frozenset(
    {
        "collect_fundamental_data",
        "write_raw_payload",
        "write_cache_record",
    }
)


@dataclass(frozen=True)
class BoundaryRuleError(ValueError):
    code: str
    message: str

    def __post_init__(self) -> None:
        ValueError.__init__(self, f"{self.code}: {self.message}")


def assert_data_service_boundary(operation: str) -> None:
    normalized = operation.strip().lower()
    if normalized == "":
        raise BoundaryRuleError(
            FND_BOUNDARY_OPERATION_UNKNOWN,
            "unknown operation: <empty>",
        )

    if any(token in normalized for token in _INVESTMENT_SEMANTIC_TOKENS):
        raise BoundaryRuleError(
            FND_BOUNDARY_INVESTMENT_CONCLUSION_FORBIDDEN,
            f"forbidden data service operation: {operation}",
        )
    if any(token in normalized for token in _CONTROL_PLANE_SEMANTIC_TOKENS):
        raise BoundaryRuleError(
            FND_BOUNDARY_CONTROL_GATE_FORBIDDEN,
            f"forbidden control-plane operation: {operation}",
        )
    if _is_allowed_operation(normalized):
        return

    raise BoundaryRuleError(
        FND_BOUNDARY_OPERATION_UNKNOWN,
        f"unknown operation: {operation}",
    )


def _is_allowed_operation(operation: str) -> bool:
    if operation in _ALLOWED_EXACT_OPERATIONS:
        return True
    for prefix in _ALLOWED_OPERATION_PREFIXES:
        if operation == prefix:
            return True
        if operation.startswith(prefix + "_"):
            return True
    return False


def is_forbidden_provider_tool_name(tool_name: str) -> bool:
    normalized = tool_name.strip()
    if normalized == "":
        return False
    return any(normalized.startswith(prefix[:-1]) for prefix in FORBIDDEN_PROVIDER_TOOLS)
