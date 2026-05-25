from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from verdify_schemas.slack_ops import (
    CropTaskRow,
    SlackCommandAuditRow,
    SlackCommandRequest,
    SlackConfirmationRequestRow,
    SlackParsedIntent,
)


def test_slack_command_request_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        SlackCommandRequest.model_validate({"command_text": "iris status", "surprise": True})


def test_slack_command_audit_row_accepts_known_statuses():
    row = SlackCommandAuditRow.model_validate(
        {
            "command_text": "iris status",
            "normalized_intent": "status.get",
            "status": "executed",
            "role": "operator",
            "raw_event": {"channel": "C0ANVVAPLD6"},
        }
    )

    assert row.normalized_intent == "status.get"
    assert row.status == "executed"
    assert row.model_routing == "deterministic"


def test_slack_parsed_intent_marks_unsafe_direct_control():
    parsed = SlackParsedIntent.model_validate(
        {
            "name": "unsafe.direct_relay_control",
            "unsafe_blocked": True,
            "reason": "direct relay control is not allowed",
        }
    )

    assert parsed.required_role == "viewer"
    assert parsed.unsafe_blocked is True


def test_slack_confirmation_request_requires_expiry():
    expires = datetime.now(UTC) + timedelta(minutes=5)
    row = SlackConfirmationRequestRow.model_validate(
        {
            "expires_at": expires,
            "slack_user_id": "U123",
            "normalized_intent": "crop.clear",
            "confirmation_text": "Clear crop 12?",
        }
    )

    assert row.status == "pending"
    assert row.payload == {}


def test_crop_task_row_models_due_scouting_task():
    due = datetime.now(UTC) + timedelta(hours=1)
    row = CropTaskRow.model_validate(
        {
            "task_type": "scouting",
            "priority": "normal",
            "crop_id": 42,
            "due_at": due,
        }
    )

    assert row.task_type == "scouting"
    assert row.status == "open"
