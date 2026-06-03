from __future__ import annotations

import pytest
from claw_trade.selection.models import (
    DATA_RUN_STATE_TRANSITIONS,
    WORKFLOW_FAILURE_STATUSES,
    WORKFLOW_STATE_TRANSITIONS,
    WORKFLOW_TERMINAL_STATUSES,
    SelectionDataRunStatus,
    SelectionWorkflowStatus,
    can_transition_data_run,
    can_transition_workflow,
    is_data_run_terminal,
    is_workflow_failure,
    is_workflow_terminal,
    transition_data_run_status,
    transition_workflow_status,
)


def test_data_run_state_machine_happy_path() -> None:
    flow = (
        SelectionDataRunStatus.PLANNED,
        SelectionDataRunStatus.LEASE_PENDING,
        SelectionDataRunStatus.RUNNING,
        SelectionDataRunStatus.FETCHING_DATA,
        SelectionDataRunStatus.NORMALIZING_INPUTS,
        SelectionDataRunStatus.BUILDING_FEATURES,
        SelectionDataRunStatus.FILTERING_AND_SCORING,
        SelectionDataRunStatus.BUILDING_CANDIDATE_PACK,
        SelectionDataRunStatus.APPROVING_CANDIDATE_PACK,
        SelectionDataRunStatus.COMPLETED,
    )
    current = flow[0]
    for expected in flow[1:]:
        current = transition_data_run_status(current, expected)
    assert current == SelectionDataRunStatus.COMPLETED
    assert is_data_run_terminal(current) is True


@pytest.mark.parametrize(
    "from_status",
    [
        SelectionDataRunStatus.FILTERING_AND_SCORING,
        SelectionDataRunStatus.BUILDING_CANDIDATE_PACK,
    ],
)
def test_data_run_state_machine_has_no_candidate_terminal_path(from_status: SelectionDataRunStatus) -> None:
    assert can_transition_data_run(from_status, SelectionDataRunStatus.NO_CANDIDATE) is True
    assert transition_data_run_status(from_status, SelectionDataRunStatus.NO_CANDIDATE) == SelectionDataRunStatus.NO_CANDIDATE
    assert is_data_run_terminal(SelectionDataRunStatus.NO_CANDIDATE) is True


@pytest.mark.parametrize(
    "status",
    [
        SelectionDataRunStatus.PLANNED,
        SelectionDataRunStatus.LEASE_PENDING,
        SelectionDataRunStatus.RUNNING,
        SelectionDataRunStatus.FETCHING_DATA,
        SelectionDataRunStatus.NORMALIZING_INPUTS,
        SelectionDataRunStatus.BUILDING_FEATURES,
        SelectionDataRunStatus.FILTERING_AND_SCORING,
        SelectionDataRunStatus.BUILDING_CANDIDATE_PACK,
        SelectionDataRunStatus.APPROVING_CANDIDATE_PACK,
    ],
)
def test_data_run_state_machine_has_explicit_failure_path(status: SelectionDataRunStatus) -> None:
    assert can_transition_data_run(status, SelectionDataRunStatus.FAILED) is True
    assert transition_data_run_status(status, SelectionDataRunStatus.FAILED) == SelectionDataRunStatus.FAILED


def test_data_run_terminal_status_rejects_extra_transition() -> None:
    with pytest.raises(ValueError, match="invalid selection data run transition"):
        transition_data_run_status(SelectionDataRunStatus.COMPLETED, SelectionDataRunStatus.FAILED)

    with pytest.raises(ValueError, match="invalid selection data run transition"):
        transition_data_run_status(SelectionDataRunStatus.NO_CANDIDATE, SelectionDataRunStatus.FAILED)

    with pytest.raises(ValueError, match="invalid selection data run transition"):
        transition_data_run_status(SelectionDataRunStatus.FAILED, SelectionDataRunStatus.PLANNED)


def test_workflow_state_machine_happy_path_and_handoff() -> None:
    flow = (
        SelectionWorkflowStatus.RECEIVED,
        SelectionWorkflowStatus.RESOLVING_REQUEST,
        SelectionWorkflowStatus.LOADING_COMPLETED_SELECTION_RUN,
        SelectionWorkflowStatus.VALIDATING_CANDIDATE_PACK,
        SelectionWorkflowStatus.SELECT_RUN_CREATED,
        SelectionWorkflowStatus.STRATEGIST_RUNNING,
        SelectionWorkflowStatus.STRATEGIST_APPROVED,
        SelectionWorkflowStatus.SKEPTIC_RUNNING,
        SelectionWorkflowStatus.SKEPTIC_APPROVED,
        SelectionWorkflowStatus.MANAGER_RUNNING,
        SelectionWorkflowStatus.MANAGER_APPROVED,
        SelectionWorkflowStatus.PORTFOLIO_MANAGER_RUNNING,
        SelectionWorkflowStatus.COMPLETED,
        SelectionWorkflowStatus.WAITING_REPORT_CONFIRMATION,
        SelectionWorkflowStatus.REPORT_HANDOFF_STARTED,
    )
    current = flow[0]
    for expected in flow[1:]:
        current = transition_workflow_status(current, expected)
    assert current == SelectionWorkflowStatus.REPORT_HANDOFF_STARTED
    assert is_workflow_terminal(current) is True


@pytest.mark.parametrize(
    ("from_status", "to_status"),
    [
        (SelectionWorkflowStatus.RECEIVED, SelectionWorkflowStatus.MARKET_STRATEGY_UNAPPROVED),
        (SelectionWorkflowStatus.RESOLVING_REQUEST, SelectionWorkflowStatus.MARKET_STRATEGY_UNAPPROVED),
        (SelectionWorkflowStatus.LOADING_COMPLETED_SELECTION_RUN, SelectionWorkflowStatus.NO_COMPLETED_SELECTION_RUN),
        (SelectionWorkflowStatus.LOADING_COMPLETED_SELECTION_RUN, SelectionWorkflowStatus.NO_CANDIDATE_SELECTION_RUN),
        (SelectionWorkflowStatus.LOADING_COMPLETED_SELECTION_RUN, SelectionWorkflowStatus.STALE_SELECTION_RUN),
        (SelectionWorkflowStatus.VALIDATING_CANDIDATE_PACK, SelectionWorkflowStatus.CANDIDATE_PACK_NOT_APPROVED),
        (SelectionWorkflowStatus.VALIDATING_CANDIDATE_PACK, SelectionWorkflowStatus.CANDIDATE_PACK_HASH_MISMATCH),
        (SelectionWorkflowStatus.VALIDATING_CANDIDATE_PACK, SelectionWorkflowStatus.CANDIDATE_PACK_INTEGRITY_FAILED),
        (SelectionWorkflowStatus.VALIDATING_CANDIDATE_PACK, SelectionWorkflowStatus.CANDIDATE_PACK_LINEAGE_INCOMPLETE),
        (SelectionWorkflowStatus.SELECT_RUN_CREATED, SelectionWorkflowStatus.TOOL_SCHEMA_VIOLATION),
        (SelectionWorkflowStatus.STRATEGIST_RUNNING, SelectionWorkflowStatus.WORKER_RUNTIME_FAILED),
        (SelectionWorkflowStatus.STRATEGIST_RUNNING, SelectionWorkflowStatus.ARTIFACT_APPROVAL_FAILED),
        (SelectionWorkflowStatus.PORTFOLIO_MANAGER_RUNNING, SelectionWorkflowStatus.SELECTION_RESULT_INVALID),
        (SelectionWorkflowStatus.WAITING_REPORT_CONFIRMATION, SelectionWorkflowStatus.FAILED),
    ],
)
def test_workflow_state_machine_has_explicit_failure_paths(
    from_status: SelectionWorkflowStatus,
    to_status: SelectionWorkflowStatus,
) -> None:
    assert can_transition_workflow(from_status, to_status) is True
    assert transition_workflow_status(from_status, to_status) == to_status
    assert is_workflow_failure(to_status) is True


def test_workflow_failure_states_are_terminal_and_enumerated() -> None:
    expected_failure_states = {
        SelectionWorkflowStatus.MARKET_STRATEGY_UNAPPROVED,
        SelectionWorkflowStatus.NO_COMPLETED_SELECTION_RUN,
        SelectionWorkflowStatus.NO_CANDIDATE_SELECTION_RUN,
        SelectionWorkflowStatus.STALE_SELECTION_RUN,
        SelectionWorkflowStatus.CANDIDATE_PACK_NOT_APPROVED,
        SelectionWorkflowStatus.CANDIDATE_PACK_HASH_MISMATCH,
        SelectionWorkflowStatus.CANDIDATE_PACK_INTEGRITY_FAILED,
        SelectionWorkflowStatus.CANDIDATE_PACK_LINEAGE_INCOMPLETE,
        SelectionWorkflowStatus.SELECTION_WAREHOUSE_CHECK_MISSING,
        SelectionWorkflowStatus.SELECT_MARKET_UNSUPPORTED,
        SelectionWorkflowStatus.CRYPTO_SELECT_HISTORY_MISSING,
        SelectionWorkflowStatus.TOOL_SCHEMA_VIOLATION,
        SelectionWorkflowStatus.WORKER_RUNTIME_FAILED,
        SelectionWorkflowStatus.ARTIFACT_APPROVAL_FAILED,
        SelectionWorkflowStatus.SELECTION_RESULT_INVALID,
        SelectionWorkflowStatus.FAILED,
    }
    assert WORKFLOW_FAILURE_STATUSES == expected_failure_states

    for failure_state in WORKFLOW_FAILURE_STATUSES:
        assert WORKFLOW_STATE_TRANSITIONS[failure_state] == frozenset()
        assert is_workflow_terminal(failure_state) is True
        assert failure_state in WORKFLOW_TERMINAL_STATUSES


def test_state_machine_maps_cover_every_enum_value() -> None:
    assert set(DATA_RUN_STATE_TRANSITIONS.keys()) == set(SelectionDataRunStatus)
    assert set(WORKFLOW_STATE_TRANSITIONS.keys()) == set(SelectionWorkflowStatus)


def test_invalid_workflow_transition_is_rejected() -> None:
    with pytest.raises(ValueError, match="invalid selection workflow transition"):
        transition_workflow_status(
            SelectionWorkflowStatus.RECEIVED,
            SelectionWorkflowStatus.STRATEGIST_RUNNING,
        )
