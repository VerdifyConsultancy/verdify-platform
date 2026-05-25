from datetime import UTC, datetime, timedelta
from uuid import uuid4

from verdify_schemas.slack_ops import (
    CropTaskRow,
    SlackAIWorkItemRow,
    SlackAlertActionRow,
    SlackAlertRunbookRow,
    SlackCommandAuditRow,
    SlackCommandRequest,
    SlackCommandResponse,
    SlackConfirmationRequestRow,
    SlackNotificationEventRow,
    SlackParsedIntent,
    SlackUserRoleRow,
)


def test_slack_command_contracts_validate():
    req = SlackCommandRequest(text="status", slack_user_id="U1", channel_id="C0ANVVAPLD6")
    intent = SlackParsedIntent(normalized_intent="status.get")
    response = SlackCommandResponse(ok=True, text="ok", normalized_intent=intent.normalized_intent)

    assert req.text == "status"
    assert intent.required_role == "viewer"
    assert response.ok is True


def test_slack_db_row_contracts_validate():
    now = datetime.now(UTC)
    conf_id = uuid4()

    SlackUserRoleRow(slack_user_id="U1", role="operator")
    SlackCommandAuditRow(channel_id="C0ANVVAPLD6", slack_user_id="U1", command_text="status")
    SlackConfirmationRequestRow(
        id=conf_id,
        expires_at=now + timedelta(minutes=10),
        slack_user_id="U1",
        channel_id="C0ANVVAPLD6",
        normalized_intent="crop.clear",
        confirmation_text="confirm",
    )
    SlackAlertActionRow(alert_id=1, action="ack", slack_user_id="U1", channel_id="C0ANVVAPLD6")
    SlackNotificationEventRow(source="ingestor", event_type="operator_brief", channel_id="C0ANVVAPLD6")
    CropTaskRow(task_type="scout", due_at=now)
    SlackAlertRunbookRow(alert_type="temp_safety", title="Temperature safety", summary="Check equipment")
    SlackAIWorkItemRow(id=uuid4(), work_type="lesson_extraction")
