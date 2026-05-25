"""Config-driven Slack notification policy."""

from __future__ import annotations

from slack_config import SlackSettings, load_slack_settings


def _policy(settings: SlackSettings | None = None) -> dict:
    return (settings or load_slack_settings()).alert_policy or {}


def alert_post_mode(alert_type: str, severity: str, settings: SlackSettings | None = None) -> str:
    """Return immediate, delayed, digest, or silent for an alert envelope."""

    policy = _policy(settings)
    severity = (severity or "").lower()
    alert_type = (alert_type or "").lower()

    if alert_type in set(policy.get("silent") or ()):
        return "silent"

    immediate = policy.get("immediate") or {}
    if severity in set(immediate.get("severity") or ()):
        return "immediate"
    if alert_type in set(immediate.get("alert_type") or ()):
        return "immediate"

    if alert_type in (policy.get("delayed") or {}):
        return "delayed"
    if severity in set(policy.get("digest") or ()):
        return "digest"
    return "immediate"


def should_post_alert(
    alert_type: str,
    severity: str,
    *,
    settings: SlackSettings | None = None,
    escalated: bool = False,
) -> bool:
    mode = alert_post_mode(alert_type, severity, settings)
    return mode == "immediate" or (mode == "delayed" and escalated)
