"""Shared Slack configuration for Verdify greenhouse integrations."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_CONFIG_PATH = REPO_ROOT / "slack.yaml"


@dataclass(frozen=True)
class SlackSettings:
    config_path: Path
    api_base_url: str
    timeout_seconds: int
    bot_token_file: str
    app_token_file: str | None
    channel_key: str
    channel_id: str
    channel_name: str
    display_name: str
    icon_emoji: str | None
    customize_messages: bool
    unfurl_links: bool
    unfurl_media: bool
    archive_output_dir: Path
    timezone: str


def _load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text()) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Slack config must be a mapping: {path}")
    return data


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


@lru_cache(maxsize=8)
def load_slack_settings(config_path: str | None = None, channel_key: str = "greenhouse") -> SlackSettings:
    """Load Verdify's tracked Slack config with env overrides for deploys."""

    selected_path = Path(
        config_path or os.environ.get("VERDIFY_SLACK_CONFIG") or os.environ.get("SLACK_CONFIG") or DEFAULT_CONFIG_PATH
    )
    raw = _load_yaml(selected_path)

    channels = raw.get("channels") or {}
    if channel_key not in channels:
        raise KeyError(f"Slack channel {channel_key!r} is not configured in {selected_path}")
    channel = channels[channel_key] or {}
    identity = raw.get("identity") or {}
    credentials = raw.get("credentials") or {}
    api = raw.get("api") or {}
    archive = raw.get("archive") or {}

    bot_token_file = os.environ.get("SLACK_TOKEN_FILE") or os.environ.get("SLACK_BOT_TOKEN_FILE")
    if not bot_token_file:
        bot_token_file = str(credentials.get("bot_token_file") or "")
    if not bot_token_file:
        raise ValueError(f"Slack bot token file is not configured in {selected_path}")

    return SlackSettings(
        config_path=selected_path,
        api_base_url=str(api.get("base_url") or "https://slack.com/api").rstrip("/"),
        timeout_seconds=int(os.environ.get("SLACK_TIMEOUT_SECONDS") or api.get("timeout_seconds") or 10),
        bot_token_file=bot_token_file,
        app_token_file=os.environ.get("SLACK_APP_TOKEN_FILE") or credentials.get("app_token_file"),
        channel_key=channel_key,
        channel_id=os.environ.get("SLACK_CHANNEL") or str(channel.get("id") or ""),
        channel_name=str(channel.get("name") or channel_key),
        display_name=os.environ.get("SLACK_USERNAME") or str(identity.get("display_name") or "Iris"),
        icon_emoji=os.environ.get("SLACK_ICON_EMOJI") or identity.get("icon_emoji"),
        customize_messages=_as_bool(
            os.environ.get("SLACK_CUSTOMIZE_MESSAGES"),
            _as_bool(identity.get("customize_messages"), True),
        ),
        unfurl_links=_as_bool(api.get("unfurl_links"), False),
        unfurl_media=_as_bool(api.get("unfurl_media"), False),
        archive_output_dir=Path(os.environ.get("SLACK_ARCHIVE_OUTPUT_DIR") or archive.get("output_dir") or ""),
        timezone=str(os.environ.get("SLACK_TIMEZONE") or archive.get("timezone") or "America/Denver"),
    )


def read_slack_token(settings: SlackSettings | None = None) -> str:
    selected = settings or load_slack_settings()
    return Path(selected.bot_token_file).read_text().strip()


def build_slack_payload(settings: SlackSettings, text: str, thread_ts: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "channel": settings.channel_id,
        "text": text,
        "unfurl_links": settings.unfurl_links,
        "unfurl_media": settings.unfurl_media,
    }
    if thread_ts:
        payload["thread_ts"] = thread_ts
    if settings.customize_messages:
        payload["username"] = settings.display_name
        if settings.icon_emoji:
            payload["icon_emoji"] = settings.icon_emoji
    return payload
