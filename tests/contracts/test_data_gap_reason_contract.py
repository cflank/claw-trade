from __future__ import annotations

from claw_trade.data_gateway.models import GapReason


def test_required_data_need_gap_reasons_exist() -> None:
    required = {
        "permission_denied",
        "provider_empty",
        "parser_missing",
        "resolver_mapping_missing",
        "rate_limited_by_tool_budget",
    }

    assert required <= {item.value for item in GapReason}
