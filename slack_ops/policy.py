"""Config-driven Slack notification policy."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from slack_config import SlackSettings, load_slack_settings

DEFAULT_CRITICAL_TYPES = {
    "leak_detected",
    "temp_safety",
    "vpd_extreme",
    "safety_invalid",
    "heat_staging_inversion",
    "heap_pressure_critical",
    "planner_required_plan_missed",
}
DEFAULT_DIGEST_TYPES = {"sensor_offline", "esp32_reboot", "tunable_zero_variance"}


def _raw_config(settings: SlackSettings | None = None) -> dict[str, Any]:
    selected = settings or load_slack_settings()
    data = yaml.safe_load(Path(selected.config_path).read_text()) or {}
    return data if isinstance(data, dict) else {}


def alert_post_mode(alert_type: str, severity: str, settings: SlackSettings | None = None) -> str:
    """Return immediate, digest, thread, or suppressed for an alert."""

    raw = _raw_config(settings)
    notifications = raw.get("notifications") or {}
    alert_types = notifications.get("alert_types") or {}
    configured = alert_types.get(alert_type)
    if isinstance(configured, dict) and configured.get("mode"):
        return str(configured["mode"])
    if isinstance(configured, str):
        return configured

    quiet_types = set(notifications.get("quiet_types") or ())
    digest_types = set(notifications.get("digest_types") or DEFAULT_DIGEST_TYPES)
    critical_types = set(notifications.get("critical_types") or DEFAULT_CRITICAL_TYPES)
    defaults = notifications.get("alert_defaults") or {}

    if alert_type in quiet_types:
        return "suppressed"
    if severity == "critical" or alert_type in critical_types:
        return str(defaults.get("critical") or "immediate")
    if alert_type in digest_types:
        return "digest"
    if severity == "warning":
        return str(defaults.get("warning") or "immediate")
    return str(defaults.get("info") or defaults.get("digest") or "digest")


def should_post_alert(
    alert_type: str,
    severity: str,
    settings: SlackSettings | None = None,
    snoozed_until: datetime | None = None,
    now: datetime | None = None,
) -> bool:
    """True when a new Slack channel message should be posted now."""

    if snoozed_until and severity != "critical":
        selected_now = now or datetime.now(tz=snoozed_until.tzinfo)
        if snoozed_until > selected_now:
            return False
    return alert_post_mode(alert_type, severity, settings=settings) == "immediate"
