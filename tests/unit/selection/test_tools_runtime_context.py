from __future__ import annotations

import pytest
from claw_trade.selection.tools import SelectionToolError, execute_selection_candidate_cache_tool


def test_selection_tool_missing_runtime_context_fails() -> None:
    with pytest.raises(SelectionToolError, match="selection_runtime_context_missing"):
        execute_selection_candidate_cache_tool(
            {
                "runtime_context": {},
                "tool_input": {},
            }
        )


def test_selection_tool_worker_mismatch_fails() -> None:
    with pytest.raises(SelectionToolError, match="selection_worker_not_allowed"):
        execute_selection_candidate_cache_tool(
            {
                "runtime_context": {
                    "worker_id": "selection_manager",
                    "stage": "selection_decision",
                    "select_workflow_run_id": "wf-1",
                    "selection_run_id": "sel-run-1",
                    "runtime_vars": {},
                },
                "tool_input": {},
            }
        )
