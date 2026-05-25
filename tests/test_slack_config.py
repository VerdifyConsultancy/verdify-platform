from __future__ import annotations

from pathlib import Path

from slack_config import build_slack_payload, load_slack_settings

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_root_slack_config_points_to_iris_identity():
    load_slack_settings.cache_clear()
    settings = load_slack_settings(str(REPO_ROOT / "slack.yaml"))

    assert settings.channel_id == "C0ANVVAPLD6"
    assert settings.channel_name == "#greenhouse"
    assert settings.display_name == "Iris"
    assert settings.icon_emoji == ":seedling:"
    assert settings.bot_token_file == "/etc/verdify/slack/iris_slack_bot_token.txt"
    assert settings.app_token_file == "/etc/verdify/slack/iris_slack_app_token.txt"

    payload = build_slack_payload(settings, "hello", thread_ts="123.456")
    assert payload["channel"] == "C0ANVVAPLD6"
    assert payload["username"] == "Iris"
    assert payload["icon_emoji"] == ":seedling:"
    assert payload["thread_ts"] == "123.456"


def test_slack_token_file_env_override(monkeypatch):
    token_path = REPO_ROOT / ".test-iris-token"
    monkeypatch.setenv("SLACK_TOKEN_FILE", str(token_path))
    load_slack_settings.cache_clear()

    settings = load_slack_settings(str(REPO_ROOT / "slack.yaml"))

    assert settings.bot_token_file == str(token_path)


def test_no_active_code_uses_orbit_slack_token_path():
    old_path = "/mnt/agents/shared/credentials/slack_bot_token.txt"
    old_iris_path = "/mnt/agents" + "/shared/credentials/iris_slack"
    code_paths = [
        REPO_ROOT / "SLACK.md",
        REPO_ROOT / "slack.yaml",
        REPO_ROOT / "ingestor" / "config.py",
        REPO_ROOT / "ingestor" / "tasks.py",
        REPO_ROOT / "scripts" / "alert-monitor.py",
        REPO_ROOT / "scripts" / "forecast-action-engine.py",
        REPO_ROOT / "scripts" / "checklist-to-slack.sh",
        REPO_ROOT / "scripts" / "slack-channel-archive.py",
        REPO_ROOT / "scripts" / "slack-post.py",
    ]

    for path in code_paths:
        text = path.read_text()
        assert old_path not in text
        assert old_iris_path not in text
