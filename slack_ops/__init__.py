"""Deterministic Slack operations for the Iris greenhouse channel."""

from .intents import parse_command, role_allows
from .policy import alert_post_mode, should_post_alert
from .service import handle_slack_command

__all__ = [
    "alert_post_mode",
    "handle_slack_command",
    "parse_command",
    "role_allows",
    "should_post_alert",
]
