from slack_config import build_slack_payload, load_slack_settings


def test_root_slack_config_uses_iris_identity_and_local_secret_path():
    settings = load_slack_settings("slack.yaml")

    assert settings.username == "Iris"
    assert settings.display_name == "Iris"
    assert settings.channel_id == "C0ANVVAPLD6"
    assert settings.bot_token_file == "/etc/verdify/slack/iris_slack_bot_token.txt"
    assert "/mnt/agents/shared/credentials" not in settings.bot_token_file


def test_build_slack_payload_includes_display_identity():
    settings = load_slack_settings("slack.yaml")

    payload = build_slack_payload(settings, "hello", thread_ts="123.456")

    assert payload["channel"] == settings.channel_id
    assert payload["username"] == "Iris"
    assert payload["icon_emoji"] == ":seedling:"
    assert payload["thread_ts"] == "123.456"
