"""Shared Slack configuration for Verdify runtime components."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "slack.yaml"


@dataclass(frozen=True)
class SlackSettings:
    config_path: Path
    api_base_url: str
    channel_id: str
    channel_name: str
    bot_token_file: str
    app_token_file: str | None
    signing_secret_file: str | None
    archive_dir: Path
    timezone: str
    greenhouse_id: str
    display_name: str
    username: str
    icon_emoji: str | None
    default_post_mode: str
    alert_policy: dict[str, Any] = field(default_factory=dict)
    briefs: dict[str, Any] = field(default_factory=dict)
    default_role: str = "viewer"
    role_order: tuple[str, ...] = ("viewer", "grower", "operator", "coordinator")


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Slack config {path} must contain a YAML mapping")
    return data


def load_slack_settings(path: str | os.PathLike[str] | None = None) -> SlackSettings:
    """Load the repo-root Slack config, with env vars only for deployment overrides."""

    config_path = Path(path or os.environ.get("VERDIFY_SLACK_CONFIG", DEFAULT_CONFIG_PATH)).expanduser()
    data = _read_yaml(config_path)
    slack = data.get("slack") or {}
    identity = data.get("identity") or {}
    notifications = data.get("notifications") or {}
    roles = data.get("roles") or {}

    return SlackSettings(
        config_path=config_path,
        api_base_url=os.environ.get("SLACK_API_BASE_URL", slack.get("api_base_url", "https://slack.com/api")),
        channel_id=os.environ.get("SLACK_CHANNEL", slack.get("channel_id", "")),
        channel_name=os.environ.get("SLACK_CHANNEL_NAME", slack.get("channel_name", "greenhouse")),
        bot_token_file=os.environ.get("SLACK_TOKEN_FILE", slack.get("bot_token_file", "")),
        app_token_file=os.environ.get("SLACK_APP_TOKEN_FILE", slack.get("app_token_file")),
        signing_secret_file=os.environ.get("SLACK_SIGNING_SECRET_FILE", slack.get("signing_secret_file")),
        archive_dir=Path(os.environ.get("SLACK_ARCHIVE_DIR", slack.get("archive_dir", "state/slack/archive"))),
        timezone=os.environ.get("SLACK_TIMEZONE", slack.get("timezone", "America/Denver")),
        greenhouse_id=os.environ.get("GREENHOUSE_ID", identity.get("greenhouse_id", "vallery")),
        display_name=identity.get("display_name", identity.get("agent_name", "Iris")),
        username=identity.get("username", identity.get("display_name", "Iris")),
        icon_emoji=identity.get("icon_emoji"),
        default_post_mode=notifications.get("default_post_mode", "thread"),
        alert_policy=notifications.get("alert_policy") or {},
        briefs=notifications.get("briefs") or {},
        default_role=roles.get("default_role", "viewer"),
        role_order=tuple(roles.get("role_order") or ("viewer", "grower", "operator", "coordinator")),
    )


def read_slack_token(token_file: str | os.PathLike[str] | None = None) -> str:
    path = Path(token_file or load_slack_settings().bot_token_file).expanduser()
    return path.read_text(encoding="utf-8").strip()


def build_slack_payload(
    settings: SlackSettings,
    text: str,
    *,
    channel: str | None = None,
    thread_ts: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "channel": channel or settings.channel_id,
        "text": text,
        "username": settings.username,
    }
    if settings.icon_emoji:
        payload["icon_emoji"] = settings.icon_emoji
    if thread_ts:
        payload["thread_ts"] = thread_ts
    if metadata:
        payload["metadata"] = metadata
    return payload
