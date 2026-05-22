from __future__ import annotations

from tests.contracts.test_ui_api_contracts import (
    test_validate_ui_api_response_rejects_internal_field as _assert_reject_internal_field,
    test_validate_ui_api_response_rejects_internal_term_leak as _assert_reject_internal_term,
)
from tests.contracts.test_ui_chat_report_boundary import (
    test_confirm_creates_workflow_with_report_command_entry_point as _assert_confirm_entrypoint,
    test_normal_chat_never_starts_report_workflow as _assert_normal_chat_no_workflow,
    test_report_request_only_creates_confirmation_card_before_confirm as _assert_report_needs_confirm,
)
from tests.contracts.test_ui_user_dto_redaction import (
    test_chat_context_mapper_hides_locked_workflow_id_and_unapproved_fields as _assert_context_redaction,
)


def test_ui_boundary_normal_chat_and_report_intent_follow_contract() -> None:
    _assert_normal_chat_no_workflow()
    _assert_report_needs_confirm()


def test_ui_boundary_confirm_path_uses_report_command_entrypoint() -> None:
    _assert_confirm_entrypoint()


def test_ui_boundary_dto_and_forbidden_term_contract() -> None:
    _assert_context_redaction()
    _assert_reject_internal_field()
    _assert_reject_internal_term()
